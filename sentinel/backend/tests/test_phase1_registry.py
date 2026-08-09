"""
Phase 1 regression tests — command registry and consistency checker.

Covers:
  * the registry is well-formed and is genuinely the single source of truth
  * the consistency checker detects each class of conflict it claims to
  * every one of the 22 conflicts found during the Phase 1 audit is resolved
  * the checker passes on the repository as it stands (this is the CI gate)

Run:
    cd sentinel/backend && python3 -m unittest tests.test_phase1_registry -v
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.api.models import RiskLevel, SubsystemID  # noqa: E402
from app.agent.safety import (  # noqa: E402
    COMMAND_WHITELIST,
    infer_subsystem,
    is_command_whitelisted,
)
from app.validation.command_registry import (  # noqa: E402
    COMMAND_REGISTRY,
    CONDITION_NEGATION,
    HAZARD_CONDITIONS,
    POSITIVE_CONDITIONS,
    CommandSource,
    CommandSpec,
    Condition,
    all_command_ids,
    enabled_command_ids,
    get_command,
    is_enabled,
    is_registered,
    registry_by_subsystem,
    registry_status,
    registry_subsystem,
)
from app.validation.conflicts import (  # noqa: E402
    PLACEHOLDER_TOKENS,
    Severity,
    check_registry_metadata,
    check_whitelist_derived,
    ConflictReport,
    run_all_checks,
)


# ═══════════════════════════════════════════════════════════════════════════
# THE 22 CONFLICTS FOUND IN THE PHASE 1 AUDIT
# ═══════════════════════════════════════════════════════════════════════════
#
# Every command below was referenced by a procedure, a training plan or the demo
# cache while being absent from the safety whitelist, so any plan containing it
# was rejected. Each is mapped to how it was resolved. See
# docs/phase1_command_conflicts.md for the reasoning behind each decision.

#: Resolution A — the whitelist was incomplete. The command is a real, needed
#: capability and is now a registry entry under this exact name.
CONFLICTS_RESOLVED_BY_REGISTERING: dict[str, str] = {
    "CMD_VERIFY_SUN_ANGLE": "SYSTEM",
    "CMD_VERIFY_MEMORY_STATE": "SYSTEM",
    "CMD_VERIFY_SIGNAL_ACQUISITION": "SYSTEM",
    "CMD_VERIFY_THERMAL_MARGIN": "SYSTEM",
    "CMD_DISABLE_HEATER_ZONE": "TCS",
    "CMD_MONITOR_TEMPERATURE": "TCS",
    "CMD_SWITCH_BACKUP_TRANSPONDER": "COMMS",
    "CMD_CONFIRM_GROUND_CONTACT": "COMMS",
    "CMD_CONFIRM_COMMS_LOCK": "COMMS",
    "CMD_SOLAR_ARRAY_A_RESET": "EPS",
    "CMD_SWITCH_SOLAR_ARRAY": "EPS",
}

#: Resolution B — the procedure/demo data was wrong. An equivalent capability
#: already existed under a canonical name, so the citing source was corrected.
#: The invented name must NOT be in the registry.
CONFLICTS_RESOLVED_BY_RENAMING: dict[str, str] = {
    "CMD_BATTERY_CONSERVATION": "CMD_POWER_SHED_NONESSENTIAL",
    "CMD_SWITCH_TO_GYRO_B": "CMD_GYRO_SWITCH_TO_BACKUP",
    "CMD_ATTITUDE_RECOVERY_SUN_POINT": "CMD_SUN_ACQUISITION",
    "CMD_ATTITUDE_SUN_POINT_SAFE": "CMD_SUN_ACQUISITION",
    "CMD_EPS_STATUS_REPORT": "CMD_POWER_CHECK",
    "CMD_SHED_NON_ESSENTIAL_LOADS": "CMD_POWER_SHED_NONESSENTIAL",
    "CMD_SOLAR_ARRAY_RELAY_CYCLE": "CMD_SOLAR_ARRAY_A_RESET",
    "CMD_OBC_MEMORY_DUMP": "CMD_MEMORY_DUMP",
    "CMD_WATCHDOG_COUNTER_RESET": "CMD_WATCHDOG_CLEAR",
    "CMD_RESTART_ATTITUDE_CONTROL_THREAD": "CMD_VERIFY_MEMORY_STATE",
    "CMD_OBC_HEALTH_MONITOR_ENABLE": "CMD_HEALTH_CHECK",
}

ALL_AUDIT_CONFLICTS = (
    set(CONFLICTS_RESOLVED_BY_REGISTERING) | set(CONFLICTS_RESOLVED_BY_RENAMING)
)


class TestAuditConflictCount(unittest.TestCase):
    def test_all_22_conflicts_are_accounted_for(self):
        self.assertEqual(
            len(ALL_AUDIT_CONFLICTS), 22,
            msg="the Phase 1 audit found exactly 22 distinct command conflicts",
        )


class TestConflictsResolvedByRegistering(unittest.TestCase):
    """Resolution A: these commands now exist in the registry."""

    def test_each_is_registered_and_enabled(self):
        for cid in sorted(CONFLICTS_RESOLVED_BY_REGISTERING):
            with self.subTest(command=cid):
                self.assertTrue(is_registered(cid), msg=f"{cid} must be registered")
                self.assertTrue(is_enabled(cid), msg=f"{cid} must be enabled")

    def test_each_is_accepted_by_the_safety_validator(self):
        for cid in sorted(CONFLICTS_RESOLVED_BY_REGISTERING):
            with self.subTest(command=cid):
                self.assertTrue(
                    is_command_whitelisted(cid),
                    msg=f"{cid} was blocked before Phase 1 and must now pass",
                )

    def test_each_lands_in_the_expected_subsystem(self):
        for cid, expected in sorted(CONFLICTS_RESOLVED_BY_REGISTERING.items()):
            with self.subTest(command=cid):
                self.assertEqual(registry_subsystem(cid), expected)
                self.assertEqual(infer_subsystem(cid), expected)

    def test_declared_as_procedure_kb_sourced(self):
        # These entered the system through the procedure KB, so their provenance
        # must say so rather than claiming the baseline whitelist defined them.
        for cid in sorted(CONFLICTS_RESOLVED_BY_REGISTERING):
            with self.subTest(command=cid):
                self.assertEqual(
                    get_command(cid).source, CommandSource.PROCEDURE_KB,
                )

    def test_observation_only_commands_have_no_constraints(self):
        # All four CMD_VERIFY_* additions, plus the two TCS observations, cannot
        # change spacecraft state, so nothing may block them.
        for cid in (
            "CMD_VERIFY_SUN_ANGLE", "CMD_VERIFY_MEMORY_STATE",
            "CMD_VERIFY_SIGNAL_ACQUISITION", "CMD_VERIFY_THERMAL_MARGIN",
            "CMD_MONITOR_TEMPERATURE", "CMD_CONFIRM_COMMS_LOCK",
            "CMD_CONFIRM_GROUND_CONTACT",
        ):
            with self.subTest(command=cid):
                self.assertTrue(get_command(cid).is_observation_only)

    def test_thermal_remedy_is_never_thermally_blocked(self):
        """The most dangerous conflict Phase 1 fixed.

        The thermal-runaway procedure says to disable the stuck heater
        immediately. Before Phase 1 that command was blocked outright, and the
        thermal-survival rule would additionally have blocked it for being a
        non-remedy command. It must now carry no thermal prohibition.
        """
        spec = get_command("CMD_DISABLE_HEATER_ZONE")
        self.assertNotIn(Condition.THERMAL_ABOVE_SURVIVAL, spec.prohibited_conditions)
        self.assertNotIn(
            Condition.THERMAL_WITHIN_SURVIVAL, spec.required_preconditions,
        )
        self.assertTrue(spec.is_observation_only)


class TestConflictsResolvedByRenaming(unittest.TestCase):
    """Resolution B: the invented name is gone; the canonical one is used."""

    def test_invented_names_are_not_registered(self):
        for invented in sorted(CONFLICTS_RESOLVED_BY_RENAMING):
            with self.subTest(command=invented):
                self.assertFalse(
                    is_registered(invented),
                    msg=(
                        f"{invented} was an invented command name and must NOT "
                        f"be legitimised by adding it to the registry"
                    ),
                )

    def test_invented_names_are_still_rejected(self):
        for invented in sorted(CONFLICTS_RESOLVED_BY_RENAMING):
            with self.subTest(command=invented):
                self.assertFalse(is_command_whitelisted(invented))

    def test_canonical_replacements_exist_and_are_enabled(self):
        for invented, canonical in sorted(CONFLICTS_RESOLVED_BY_RENAMING.items()):
            with self.subTest(command=invented, canonical=canonical):
                self.assertTrue(
                    is_enabled(canonical),
                    msg=f"replacement {canonical} for {invented} must be usable",
                )

    def test_no_source_file_still_cites_an_invented_name(self):
        """Grep the citing sources directly, not just the parsed structures."""
        targets = [
            os.path.join(_BACKEND_ROOT, "app", "agent", "rag.py"),
            os.path.join(_BACKEND_ROOT, "app", "agent", "prompts.py"),
            os.path.join(_BACKEND_ROOT, "app", "analytics", "generate_demo_cache.py"),
            os.path.join(_BACKEND_ROOT, "simulation", "dataset_generator.py"),
        ]
        for path in targets:
            if not os.path.isfile(path):
                continue
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            # Strip comments: Phase 1 records the old name in a comment next to
            # each correction, and those notes must not trip this test.
            body = re.sub(r"^\s*#.*$", "", text, flags=re.MULTILINE)
            for invented in sorted(CONFLICTS_RESOLVED_BY_RENAMING):
                with self.subTest(file=os.path.basename(path), command=invented):
                    self.assertNotIn(
                        invented, body,
                        msg=f"{os.path.basename(path)} still cites {invented}",
                    )


# ═══════════════════════════════════════════════════════════════════════════
# REGISTRY INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════

class TestRegistryIntegrity(unittest.TestCase):

    def test_registry_is_not_empty(self):
        self.assertGreaterEqual(len(COMMAND_REGISTRY), 70)

    def test_every_entry_is_fully_specified(self):
        valid = {s.value for s in SubsystemID}
        for cid, spec in sorted(COMMAND_REGISTRY.items()):
            with self.subTest(command=cid):
                self.assertIsInstance(spec, CommandSpec)
                self.assertEqual(spec.command_id, cid)
                self.assertTrue(cid.startswith("CMD_"))
                self.assertIn(spec.subsystem.value, valid)
                self.assertIsInstance(spec.risk_level, RiskLevel)
                self.assertTrue(spec.description.strip())
                self.assertTrue(spec.expected_effect.strip())
                self.assertTrue(spec.source_reference.strip())
                self.assertIsInstance(spec.source, CommandSource)
                self.assertIsInstance(spec.enabled, bool)

    def test_keys_match_command_ids(self):
        for cid, spec in COMMAND_REGISTRY.items():
            self.assertEqual(cid, spec.command_id)

    def test_no_duplicate_command_ids(self):
        self.assertEqual(len(all_command_ids()), len(set(all_command_ids())))

    def test_condition_polarity_is_correct_everywhere(self):
        for cid, spec in sorted(COMMAND_REGISTRY.items()):
            with self.subTest(command=cid):
                for cond in spec.required_preconditions:
                    self.assertIn(cond, POSITIVE_CONDITIONS, msg=cond.value)
                for cond in spec.prohibited_conditions:
                    self.assertIn(cond, HAZARD_CONDITIONS, msg=cond.value)

    def test_no_command_is_unsatisfiable(self):
        for cid, spec in sorted(COMMAND_REGISTRY.items()):
            with self.subTest(command=cid):
                for cond in spec.required_preconditions:
                    negation = CONDITION_NEGATION.get(cond)
                    self.assertNotIn(
                        negation, spec.prohibited_conditions,
                        msg=f"{cid} requires {cond.value} and prohibits its negation",
                    )

    def test_every_condition_has_a_negation(self):
        for positive in POSITIVE_CONDITIONS:
            self.assertIn(positive, CONDITION_NEGATION)
        self.assertEqual(
            set(CONDITION_NEGATION.values()), set(HAZARD_CONDITIONS),
        )

    def test_positive_and_hazard_sets_are_disjoint(self):
        self.assertEqual(POSITIVE_CONDITIONS & HAZARD_CONDITIONS, frozenset())

    def test_all_conditions_are_classified(self):
        self.assertEqual(
            set(Condition), set(POSITIVE_CONDITIONS) | set(HAZARD_CONDITIONS),
        )

    def test_disabled_commands_carry_a_reason(self):
        for cid, spec in COMMAND_REGISTRY.items():
            if not spec.enabled:
                with self.subTest(command=cid):
                    self.assertTrue(spec.disabled_reason)

    def test_registry_status_is_coherent(self):
        st = registry_status()
        self.assertEqual(st["total_commands"], len(COMMAND_REGISTRY))
        self.assertEqual(st["enabled_commands"], len(enabled_command_ids()))
        self.assertEqual(
            sum(st["counts_per_subsystem"].values()), len(COMMAND_REGISTRY),
        )


class TestRegistryIsSingleSourceOfTruth(unittest.TestCase):

    def test_safety_whitelist_equals_enabled_registry(self):
        flat: set[str] = set()
        for cmds in COMMAND_WHITELIST.values():
            flat.update(cmds)
        self.assertEqual(flat, set(enabled_command_ids()))

    def test_whitelist_grouping_matches_declared_subsystems(self):
        self.assertEqual(COMMAND_WHITELIST, registry_by_subsystem(enabled_only=True))

    def test_whitelist_has_no_cross_subsystem_duplicates(self):
        seen: dict[str, str] = {}
        for sub, cmds in COMMAND_WHITELIST.items():
            for cid in cmds:
                self.assertNotIn(
                    cid, seen,
                    msg=f"{cid} filed under both {seen.get(cid)} and {sub}",
                )
                seen[cid] = sub

    def test_safety_module_declares_no_command_literals(self):
        """safety.py must not reintroduce a hand-maintained command list."""
        path = os.path.join(_BACKEND_ROOT, "app", "agent", "safety.py")
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        body = re.sub(r"^\s*#.*$", "", text, flags=re.MULTILINE)
        body = re.sub(r'"""[\s\S]*?"""', "", body)
        # Tokens ending in "_" are the subsystem PREFIXES used by
        # infer_subsystem's fallback heuristic, not commands. They are allowed.
        quoted = {
            t for t in re.findall(r'"(CMD_[A-Z0-9_]+)"', body)
            if not t.endswith("_")
        }
        self.assertEqual(
            quoted, set(),
            msg=(
                "safety.py contains hardcoded command literals "
                f"{sorted(quoted)}; commands belong in the registry"
            ),
        )

    def test_prompt_lists_the_registry_commands(self):
        from app.agent import prompts

        for cid in enabled_command_ids():
            with self.subTest(command=cid):
                self.assertIn(cid, prompts.SYSTEM_PROMPT)

    def test_prompt_no_longer_uses_the_naming_placeholder_as_a_command(self):
        from app.agent import prompts

        # The placeholder used to be the ONLY command token in the prompt, so
        # the model had to guess the vocabulary.
        self.assertNotIn("CMD_UPPER_SNAKE_CASE", prompts.SYSTEM_PROMPT)


# ═══════════════════════════════════════════════════════════════════════════
# THE CONSISTENCY CHECKER
# ═══════════════════════════════════════════════════════════════════════════

class TestConflictsCheckerOnRepository(unittest.TestCase):
    """This is the CI gate."""

    @classmethod
    def setUpClass(cls):
        cls.report = run_all_checks()

    def test_no_errors(self):
        self.assertEqual(
            self.report.errors, [],
            msg="\n" + self.report.format(),
        )

    def test_checker_actually_inspected_each_source(self):
        for key in (
            "registry entries",
            "whitelist commands",
            "procedure KB command references",
            "prompt command references",
            "dataset generator commands",
            "demo cache commands",
        ):
            with self.subTest(source=key):
                self.assertGreater(self.report.checked.get(key, 0), 0)

    def test_cli_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, "-m", "app.validation.conflicts", "--quiet"],
            cwd=_BACKEND_ROOT, capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)


class TestConflictsCheckerDetections(unittest.TestCase):
    """Each detection must actually fire when its condition is present.

    A checker that cannot fail is worthless as a gate, so these tests inject
    synthetic defects rather than trusting the clean run above.
    """

    @staticmethod
    def _codes(report: ConflictReport, severity: Severity | None = None) -> set[str]:
        return {
            f.code for f in report.findings
            if severity is None or f.severity is severity
        }

    def _report_with_registry(self, patched: dict[str, CommandSpec]) -> ConflictReport:
        import app.validation.command_registry as reg

        original = dict(reg.COMMAND_REGISTRY)
        try:
            reg.COMMAND_REGISTRY.clear()
            reg.COMMAND_REGISTRY.update(patched)
            report = ConflictReport()
            check_registry_metadata(report)
            return report
        finally:
            reg.COMMAND_REGISTRY.clear()
            reg.COMMAND_REGISTRY.update(original)

    def _base_spec(self, **overrides) -> CommandSpec:
        defaults = dict(
            command_id="CMD_TEST_ONLY",
            subsystem=SubsystemID.OBC,
            description="Synthetic command used only by the test suite.",
            risk_level=RiskLevel.LOW,
            expected_effect="Nothing; this command is never issued.",
            source=CommandSource.BASELINE_WHITELIST,
            source_reference="test fixture",
        )
        defaults.update(overrides)
        return CommandSpec(**defaults)

    def test_detects_missing_metadata(self):
        spec = self._base_spec(description="   ")
        report = self._report_with_registry({spec.command_id: spec})
        self.assertIn("MISSING_COMMAND_METADATA", self._codes(report))

    def test_detects_missing_expected_effect(self):
        spec = self._base_spec(expected_effect="")
        report = self._report_with_registry({spec.command_id: spec})
        self.assertIn("MISSING_COMMAND_METADATA", self._codes(report))

    def test_detects_disabled_without_reason(self):
        spec = self._base_spec(enabled=False, disabled_reason=None)
        report = self._report_with_registry({spec.command_id: spec})
        self.assertIn("MISSING_COMMAND_METADATA", self._codes(report))

    def test_detects_invalid_subsystem(self):
        spec = self._base_spec(subsystem="PROPULSION")  # not a SubsystemID
        report = self._report_with_registry({spec.command_id: spec})
        self.assertIn("INVALID_SUBSYSTEM", self._codes(report))

    def test_detects_bad_command_id_prefix(self):
        spec = self._base_spec(command_id="RESET_EVERYTHING")
        report = self._report_with_registry({spec.command_id: spec})
        self.assertIn("MISSING_COMMAND_METADATA", self._codes(report))

    def test_detects_incompatible_preconditions(self):
        spec = self._base_spec(
            required_preconditions=(Condition.GYRO_DATA_VALID,),
            prohibited_conditions=(Condition.GYRO_DATA_INVALID,),
        )
        report = self._report_with_registry({spec.command_id: spec})
        self.assertIn("INCOMPATIBLE_PRECONDITIONS", self._codes(report))

    def test_detects_wrong_condition_polarity(self):
        spec = self._base_spec(
            required_preconditions=(Condition.BATTERY_BELOW_FLOOR,),
            prohibited_conditions=(Condition.THERMAL_WITHIN_SURVIVAL,),
        )
        report = self._report_with_registry({spec.command_id: spec})
        self.assertIn("CONDITION_WRONG_POLARITY", self._codes(report))

    def test_detects_whitelist_drift(self):
        import app.agent.safety as safety

        original = dict(safety.COMMAND_WHITELIST)
        try:
            safety.COMMAND_WHITELIST["OBC"] = set(
                original.get("OBC", set())
            ) | {"CMD_NOT_IN_THE_REGISTRY"}
            report = ConflictReport()
            check_whitelist_derived(report)
            self.assertIn("WHITELIST_DRIFT", self._codes(report))
        finally:
            safety.COMMAND_WHITELIST.clear()
            safety.COMMAND_WHITELIST.update(original)

    def test_detects_procedure_command_not_in_registry(self):
        from app.validation.conflicts import _check_source

        report = ConflictReport()
        _check_source(report, "test-source", ["CMD_TOTALLY_MADE_UP"], set())
        self.assertIn("PROCEDURE_COMMAND_NOT_IN_REGISTRY", self._codes(report))

    def test_detects_procedure_reference_to_disabled_command(self):
        import app.validation.command_registry as reg
        from app.validation.conflicts import _check_source

        spec = self._base_spec(
            command_id="CMD_TEST_DISABLED",
            enabled=False,
            disabled_reason="withdrawn by the test suite",
        )
        reg.COMMAND_REGISTRY[spec.command_id] = spec
        try:
            report = ConflictReport()
            _check_source(report, "test-source", [spec.command_id], set())
            self.assertIn("PROCEDURE_REFERENCES_DISABLED", self._codes(report))
        finally:
            reg.COMMAND_REGISTRY.pop(spec.command_id, None)

    def test_placeholder_tokens_are_not_treated_as_commands(self):
        from app.validation.conflicts import _check_source, _referenced_commands

        found = _referenced_commands(
            "Use CMD_UPPER_SNAKE_CASE naming for CMD_HEALTH_CHECK."
        )
        self.assertNotIn("CMD_UPPER_SNAKE_CASE", found)
        self.assertIn("CMD_HEALTH_CHECK", found)
        self.assertIn("CMD_UPPER_SNAKE_CASE", PLACEHOLDER_TOKENS)

        report = ConflictReport()
        _check_source(report, "test-source", found, set())
        self.assertEqual(report.errors, [])

    def test_report_separates_errors_from_warnings(self):
        report = ConflictReport()
        report.error("E", "s", "detail here", "src")
        report.warn("W", "s", "detail here", "src")
        self.assertEqual([f.code for f in report.errors], ["E"])
        self.assertEqual([f.code for f in report.warnings], ["W"])
        self.assertIn("1 error(s), 1 warning(s)", report.format())


if __name__ == "__main__":
    unittest.main(verbosity=2)
