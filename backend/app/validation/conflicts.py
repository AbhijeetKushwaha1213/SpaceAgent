"""
SENTINEL — Registry / Procedure Consistency Checker (conflicts.py)

Phase 1. Proves that every consumer of the command registry agrees with it.

Run it directly (exits non-zero on any ERROR, so it works as a CI gate):

    cd sentinel/backend && python3 -m app.validation.conflicts
    cd sentinel/backend && python3 -m app.validation.conflicts --strict   # warnings fail too

Or from a test:

    from app.validation.conflicts import run_all_checks
    report = run_all_checks()
    assert not report.errors, report.format()

Why this exists
---------------
Before Phase 1, the procedure knowledge base recommended 12 commands the safety
whitelist then blocked, and the demo cache shipped 10 more. Nothing detected it,
because the procedure text and the whitelist were independent hand-maintained
lists. A model that followed the retrieved procedure exactly had its whole plan
rejected. This checker makes that class of drift a build failure.

Detections (per the Phase 1 specification)
------------------------------------------
ERROR   PROCEDURE_COMMAND_NOT_IN_REGISTRY   procedure cites an unknown command
ERROR   PROCEDURE_REFERENCES_DISABLED       procedure cites a withdrawn command
ERROR   MISSING_COMMAND_METADATA            registry entry has an empty field
ERROR   INVALID_SUBSYSTEM                   subsystem outside SubsystemID
ERROR   INCOMPATIBLE_PRECONDITIONS          command can never run
ERROR   CONDITION_WRONG_POLARITY            positive predicate used as a hazard
ERROR   WHITELIST_DRIFT                     safety.py whitelist != registry
WARNING SUBSYSTEM_PREFIX_MISMATCH           declared subsystem != name prefix
WARNING UNREFERENCED_COMMAND                registry entry no procedure cites
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable

from app.api.models import SubsystemID
from app.validation.command_registry import (
    COMMAND_REGISTRY,
    CONDITION_NEGATION,
    HAZARD_CONDITIONS,
    POSITIVE_CONDITIONS,
    Condition,
    enabled_command_ids,
    get_command,
)

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

#: Matches a command token anywhere in free text.
COMMAND_PATTERN = re.compile(r"CMD_[A-Z0-9_]+")

#: Tokens that look like commands but are documentation placeholders, not
#: commands. Kept explicit so a real command can never be silently excused.
PLACEHOLDER_TOKENS: frozenset[str] = frozenset({
    "CMD_UPPER_SNAKE_CASE",   # naming-convention example in prompts.py
    "CMD_",                   # bare prefix
})


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass(frozen=True)
class Finding:
    """One consistency problem."""
    severity: Severity
    code: str
    subject: str
    detail: str
    source: str

    def format(self) -> str:
        return (
            f"[{self.severity.value}] {self.code}: {self.subject}\n"
            f"    {self.detail}\n"
            f"    source: {self.source}"
        )


@dataclass
class ConflictReport:
    findings: list[Finding] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=dict)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARNING]

    def add(self, severity: Severity, code: str, subject: str,
            detail: str, source: str) -> None:
        self.findings.append(Finding(severity, code, subject, detail, source))

    def error(self, code: str, subject: str, detail: str, source: str) -> None:
        self.add(Severity.ERROR, code, subject, detail, source)

    def warn(self, code: str, subject: str, detail: str, source: str) -> None:
        self.add(Severity.WARNING, code, subject, detail, source)

    def format(self) -> str:
        lines: list[str] = []
        lines.append("SENTINEL command registry consistency report")
        lines.append("=" * 60)
        for name, count in sorted(self.checked.items()):
            lines.append(f"  checked {name}: {count}")
        lines.append("")
        if not self.findings:
            lines.append("No conflicts found.")
        else:
            for f in self.errors:
                lines.append(f.format())
            for f in self.warnings:
                lines.append(f.format())
        lines.append("")
        lines.append(f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# CHECK 1 — REGISTRY INTERNAL INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════

_REQUIRED_TEXT_FIELDS = ("description", "expected_effect", "source_reference")


def check_registry_metadata(report: ConflictReport) -> None:
    """Every registry entry must be fully specified and internally consistent."""
    valid_subsystems = {s.value for s in SubsystemID}

    for cid, spec in sorted(COMMAND_REGISTRY.items()):
        src = f"app/validation/command_registry.py::{cid}"

        # --- missing metadata ---
        if not cid.startswith("CMD_"):
            report.error(
                "MISSING_COMMAND_METADATA", cid,
                "command_id must start with 'CMD_'", src,
            )
        for fname in _REQUIRED_TEXT_FIELDS:
            value = getattr(spec, fname, None)
            if not value or not str(value).strip():
                report.error(
                    "MISSING_COMMAND_METADATA", cid,
                    f"field '{fname}' is empty", src,
                )
        if spec.risk_level is None:
            report.error(
                "MISSING_COMMAND_METADATA", cid, "risk_level is missing", src,
            )
        if not spec.enabled and not spec.disabled_reason:
            report.error(
                "MISSING_COMMAND_METADATA", cid,
                "command is disabled but no disabled_reason is recorded", src,
            )

        # --- invalid subsystem ---
        sub = getattr(spec.subsystem, "value", spec.subsystem)
        if sub not in valid_subsystems:
            report.error(
                "INVALID_SUBSYSTEM", cid,
                f"subsystem '{sub}' is not a SubsystemID "
                f"(valid: {sorted(valid_subsystems)})",
                src,
            )

        # --- condition polarity ---
        for cond in spec.required_preconditions:
            if cond not in POSITIVE_CONDITIONS:
                report.error(
                    "CONDITION_WRONG_POLARITY", cid,
                    f"'{cond.value}' is a hazard predicate and cannot be a "
                    f"required precondition; use its positive counterpart",
                    src,
                )
        for cond in spec.prohibited_conditions:
            if cond not in HAZARD_CONDITIONS:
                report.error(
                    "CONDITION_WRONG_POLARITY", cid,
                    f"'{cond.value}' is a positive predicate and cannot be a "
                    f"prohibited condition; use its hazard counterpart",
                    src,
                )

        # --- incompatible preconditions (command could never run) ---
        for cond in spec.required_preconditions:
            negation = CONDITION_NEGATION.get(cond)
            if negation is not None and negation in spec.prohibited_conditions:
                report.error(
                    "INCOMPATIBLE_PRECONDITIONS", cid,
                    f"requires '{cond.value}' while also prohibiting "
                    f"'{negation.value}'. Both cannot be evaluated favourably, "
                    f"so this command can never be authorised.",
                    src,
                )
        dupes = {c for c in spec.required_preconditions
                 if list(spec.required_preconditions).count(c) > 1}
        if dupes:
            report.error(
                "INCOMPATIBLE_PRECONDITIONS", cid,
                f"duplicate required_preconditions: "
                f"{sorted(c.value for c in dupes)}",
                src,
            )

    report.checked["registry entries"] = len(COMMAND_REGISTRY)


# ═══════════════════════════════════════════════════════════════════════════
# CHECK 2 — SAFETY WHITELIST IS ACTUALLY DERIVED
# ═══════════════════════════════════════════════════════════════════════════

def check_whitelist_derived(report: ConflictReport) -> None:
    """safety.py's whitelist must equal the registry's enabled commands."""
    from app.agent.safety import COMMAND_WHITELIST

    flat: set[str] = set()
    for cmds in COMMAND_WHITELIST.values():
        flat.update(cmds)

    expected = set(enabled_command_ids())
    src = "app/agent/safety.py::COMMAND_WHITELIST"

    for cid in sorted(flat - expected):
        report.error(
            "WHITELIST_DRIFT", cid,
            "present in safety.py's whitelist but not enabled in the registry",
            src,
        )
    for cid in sorted(expected - flat):
        report.error(
            "WHITELIST_DRIFT", cid,
            "enabled in the registry but missing from safety.py's whitelist",
            src,
        )

    report.checked["whitelist commands"] = len(flat)


# ═══════════════════════════════════════════════════════════════════════════
# CHECK 3 — PROCEDURE SOURCES CITE ONLY REGISTRY COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

def _referenced_commands(text: str) -> set[str]:
    return {t for t in COMMAND_PATTERN.findall(text)
            if t not in PLACEHOLDER_TOKENS}


def _check_source(
    report: ConflictReport,
    source_label: str,
    commands: Iterable[str],
    referenced: set[str],
) -> None:
    for cid in sorted(set(commands)):
        spec = get_command(cid)
        if spec is None:
            report.error(
                "PROCEDURE_COMMAND_NOT_IN_REGISTRY", cid,
                "cited by a procedure/plan source but not defined in the "
                "command registry. Either add it to the registry or correct "
                "the procedure to cite an existing command_id.",
                source_label,
            )
        elif not spec.enabled:
            report.error(
                "PROCEDURE_REFERENCES_DISABLED", cid,
                f"cited by a procedure/plan source but disabled in the "
                f"registry ({spec.disabled_reason or 'no reason recorded'}).",
                source_label,
            )
        referenced.add(cid)


def check_procedure_kb(report: ConflictReport, referenced: set[str]) -> None:
    """Every command named in the RAG fallback knowledge base must be registered."""
    from app.agent.rag import FALLBACK_KB

    total = 0
    for entry in FALLBACK_KB:
        cmds = _referenced_commands(entry.content)
        total += len(cmds)
        _check_source(
            report,
            f"app/agent/rag.py::FALLBACK_KB[{entry.fault_class}]",
            cmds,
            referenced,
        )
    report.checked["procedure KB command references"] = total


def check_prompts(report: ConflictReport) -> None:
    """Commands named in the LLM system prompt must be registered.

    Deliberately does NOT contribute to the ``referenced`` set. The prompt's
    APPROVED COMMANDS section is generated from the registry, so counting it
    would make every entry look referenced and render the unreferenced-command
    warning meaningless. What this check catches is a command name hand-written
    elsewhere in the prompt text that the registry does not define.
    """
    from app.agent import prompts

    text = getattr(prompts, "SYSTEM_PROMPT", "")
    cmds = _referenced_commands(text)
    _check_source(
        report, "app/agent/prompts.py::SYSTEM_PROMPT", cmds, referenced=set(),
    )
    report.checked["prompt command references"] = len(cmds)


def check_dataset_generator(report: ConflictReport, referenced: set[str]) -> None:
    """Training-data recovery commands must be registered."""
    try:
        from simulation.dataset_generator import _RECOVERY_COMMANDS
    except Exception:
        return  # simulation package not importable in this context

    cmds: set[str] = set()
    for plan in _RECOVERY_COMMANDS.values():
        cmds.update(plan)
    _check_source(
        report,
        "simulation/dataset_generator.py::_RECOVERY_COMMANDS",
        cmds,
        referenced,
    )
    report.checked["dataset generator commands"] = len(cmds)


def check_demo_cache(report: ConflictReport, referenced: set[str]) -> None:
    """Cached demo recovery plans must only contain registered commands.

    The demo cache is replayed to operators, so a command that live safety would
    reject must never appear there.
    """
    cache_dir = _BACKEND_ROOT / "data" / "demo_cache"
    if not cache_dir.is_dir():
        return

    total = 0
    for path in sorted(cache_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            report.error(
                "MISSING_COMMAND_METADATA", path.name,
                f"demo cache file is not readable JSON: {exc}",
                f"data/demo_cache/{path.name}",
            )
            continue
        plan = payload.get("sentinel_output", {}).get("recovery_plan", [])
        cmds = [s.get("command", "") for s in plan if isinstance(s, dict)]
        total += len(cmds)
        _check_source(
            report, f"data/demo_cache/{path.name}", cmds, referenced,
        )
    report.checked["demo cache commands"] = total


# ═══════════════════════════════════════════════════════════════════════════
# CHECK 4 — PREFIX / SUBSYSTEM AGREEMENT (warning only)
# ═══════════════════════════════════════════════════════════════════════════

def check_prefix_agreement(report: ConflictReport) -> None:
    """Warn when a command's name prefix disagrees with its declared subsystem.

    A warning, not an error: the registry's declared subsystem is authoritative,
    and several legitimate commands (CMD_DISABLE_HEATER_ZONE,
    CMD_SWITCH_BACKUP_TRANSPONDER) do not follow a subsystem prefix at all. The
    warning exists so a genuine mis-filing is still visible.
    """
    from app.agent.safety import _PREFIX_MAP

    for cid, spec in sorted(COMMAND_REGISTRY.items()):
        prefix_subsystem = None
        for prefix, sub in _PREFIX_MAP:
            if cid.startswith(prefix):
                prefix_subsystem = sub
                break
        if prefix_subsystem is None:
            continue
        declared = spec.subsystem.value
        if prefix_subsystem != declared:
            report.warn(
                "SUBSYSTEM_PREFIX_MISMATCH", cid,
                f"declared subsystem is '{declared}' but the name prefix "
                f"suggests '{prefix_subsystem}'. The declaration wins; confirm "
                f"it is correct.",
                f"app/validation/command_registry.py::{cid}",
            )


def check_unreferenced(report: ConflictReport, referenced: set[str]) -> None:
    """Warn about registry entries that no procedure or plan source cites.

    Not an error: the registry deliberately holds capabilities that no current
    procedure happens to use. The warning keeps the list honest about which
    entries are actually exercised.
    """
    unused = sorted(set(enabled_command_ids()) - referenced)
    if unused:
        report.warn(
            "UNREFERENCED_COMMAND", f"{len(unused)} command(s)",
            "registered and enabled but not cited by any procedure, prompt, "
            "training plan or demo cache: " + ", ".join(unused),
            "app/validation/command_registry.py",
        )


# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def run_all_checks() -> ConflictReport:
    """Run every consistency check and return the combined report."""
    report = ConflictReport()
    referenced: set[str] = set()

    check_registry_metadata(report)
    check_whitelist_derived(report)
    check_procedure_kb(report, referenced)
    check_prompts(report)
    check_dataset_generator(report, referenced)
    check_demo_cache(report, referenced)
    check_prefix_agreement(report)
    check_unreferenced(report, referenced)

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check SENTINEL command registry consistency.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Treat warnings as failures.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Only print the summary line.",
    )
    args = parser.parse_args(argv)

    report = run_all_checks()

    if args.quiet:
        print(f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    else:
        print(report.format())

    if report.errors:
        return 1
    if args.strict and report.warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
