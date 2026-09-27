"""
SENTINEL — Phase 25 End-to-End Demonstration CLI Runner
(demo/run_e2e.py)

Single-command judge-facing demonstration executing the complete 14-stage
diagnostic pipeline across all 4 canonical scenarios.

Usage:
    python -m demo.run_e2e
    python -m demo.run_e2e --scenario A
    python -m demo.run_e2e --scenario B
    python -m demo.run_e2e --scenario C
    python -m demo.run_e2e --scenario D
    python -m demo.run_e2e --scenario ALL --json
    python -m demo.run_e2e --scenario B --reconciliation
    python -m demo.run_e2e --mode cloud  (requires GEMINI_API_KEY)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Ensure backend root is on sys.path
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Load .env if present
try:
    from dotenv import load_dotenv
    for env_path in [_BACKEND_DIR / ".env", _BACKEND_DIR.parent / ".env"]:
        if env_path.is_file():
            load_dotenv(env_path, override=False)
            break
except ImportError:
    pass

from demo.e2e_demo import (
    EndToEndDemoEngine,
    build_scenario_a_single_fault,
    build_scenario_b_two_separate_faults,
    build_scenario_c_conflicting_evidence,
    build_scenario_d_insufficient_data,
)
from app.reconciliation import RelationshipType

# Colors & ANSI styles
C_CYAN = "\033[36m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RESET = "\033[0m"


def p_banner(title: str) -> None:
    print(f"\n{C_BOLD}{C_CYAN}{'═' * 80}{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}  {title}{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}{'═' * 80}{C_RESET}")


def p_section(title: str) -> None:
    print(f"\n{C_BOLD}{C_CYAN}▶ {title}{C_RESET}")


def p_field(key: str, val: Any, color: str = "") -> None:
    c = color if color else C_RESET
    print(f"    {C_DIM}{key:<26}{C_RESET} {c}{val}{C_RESET}")


def p_reconciliation_focus(d: dict[str, Any]) -> None:
    """Explicit reconciliation spotlight for ``--reconciliation`` runs.

    Prints the deterministic case-separation result with the CORRELATION !=
    IDENTITY framing (§10, §11). Reads only the values the engine already
    produced in ``d`` — nothing here re-derives or invents reconciliation
    state. RELATED is narrated as a deterministic relationship with physics
    validation still pending, never as physical proof.
    """
    cases = d["cases"]
    relationships = d["relationships"]

    p_section("RECONCILIATION — DETERMINISTIC CASE SEPARATION (RECONCILIATION_ENABLED=true)")
    print(f"    {C_DIM}Principle: CORRELATION != IDENTITY — cases stay separate unless"
          f" deterministically proven identical.{C_RESET}")

    # Stable CASE 00N labels so relationships can refer back to them.
    labels = {c["case_id"]: f"CASE {i + 1:03d}" for i, c in enumerate(cases)}

    p_field("Total Cases:", len(cases), C_GREEN if cases else C_YELLOW)
    for c in cases:
        p_field(
            f"  • {labels[c['case_id']]}  {c['case_id']}",
            f"subsystems={c['subsystems']} channels={c['channels']}",
            C_CYAN,
        )

    p_field("Relationships:", len(relationships))
    for r in relationships:
        src = labels.get(r["source_case_id"], r["source_case_id"])
        tgt = labels.get(r["target_case_id"], r["target_case_id"])
        # Normalize to the authoritative enum for display: the demo serializer
        # leaves the str-enum member in place, so its repr can read
        # "RelationshipType.RELATED". Reconstructing the enum lets us reuse its
        # own .value / .merge_permitted rather than re-deriving either here.
        try:
            rt = RelationshipType(str(r["relationship_type"]).split(".")[-1])
            rtype = rt.value
            merge_permitted = rt.merge_permitted
        except ValueError:
            rtype = str(r["relationship_type"])
            merge_permitted = r.get("merge_permitted")
        rcolor = C_RED if rtype in ("CONFLICT", "UNCERTAIN") else C_YELLOW
        p_field(
            f"  • {src} <-> {tgt}",
            f"{rtype}  (merge_permitted={merge_permitted})",
            rcolor,
        )
        reasons = r.get("deterministic_reasons") or []
        if reasons:
            p_field("      deterministic:", "; ".join(reasons), C_DIM)
        if rtype == "RELATED":
            p_field(
                "      authority note:",
                "deterministic relationship (possible propagation) — physics "
                "validation PENDING; RELATED != physically proven.",
                C_DIM,
            )
        if rtype == "CONFLICT":
            p_field(
                "      authority note:",
                "observations contradict — cases kept separate, human review "
                "raised. Reconciliation does not resolve the conflict.",
                C_DIM,
            )

    rev_color = C_RED if d["human_review_required"] else C_GREEN
    p_field("Human Review Required:", str(d["human_review_required"]).upper(), rev_color)


def run_and_print_scenario(
    engine: EndToEndDemoEngine,
    scenario_data: dict[str, Any],
    json_only: bool = False,
    reconciliation_focus: bool = False,
) -> dict[str, Any]:
    res = engine.run_scenario(scenario_data)
    d = res.to_dict()

    if json_only:
        return d

    scenario_id = d["scenario_id"]
    p_banner(f"SCENARIO: {scenario_id}")

    if reconciliation_focus:
        p_reconciliation_focus(d)

    p_section("1. TELEMETRY & OBSERVATION RECONCILIATION")
    p_field("Observations Extracted:", len(d["observations"]))
    p_field("Cases Formed:", len(d["cases"]), C_GREEN if len(d["cases"]) > 0 else C_RED)
    for c in d["cases"]:
        p_field(f"  • Case ID: {c['case_id']}", f"Subsystems: {c['subsystems']} | Channels: {c['channels']}")
    p_field("Inter-case Links:", len(d["relationships"]))
    for r in d["relationships"]:
        p_field(f"  • Relationship:", f"{r['source_case_id']} ↔ {r['target_case_id']} [{r['relationship_type']}]", C_YELLOW)

    p_section("2. ISOLATION & RAG RETRIEVAL")
    p_field("Case-Scoped Evidence:", f"{len(d['evidence'])} evidence items bound strictly to case")
    p_field("Retrieved Documents:", f"{len(d['rag_context'])} engineering procedure(s)")
    for doc in d["rag_context"]:
        p_field("  • Doc Citation:", f"{doc.get('doc_id')}: {doc.get('title')}")

    p_section("3. DETERMINISTIC PHYSICS & HYPOTHESIS ARBITRATION")
    p_field("Physics Authority Status:", d["physics"]["status"], C_GREEN if d["physics"]["status"] == "VALIDATED" else C_YELLOW)
    p_field("Validated Faults:", d["physics"]["validated"])
    p_field("Arbitration Decision:", d["arbitration"]["decision"], C_CYAN)
    p_field("Winning Reasoning Branch:", d["arbitration"]["winning_branch"] or "None (Deterministic Tie / Review)", C_BOLD)
    p_field("Arbitration Rule Applied:", d["arbitration"]["rule_applied"])

    p_section("4. SAFETY VALIDATION & RECOVERY GATING")
    p_field("Safety Precondition Status:", d["safety"]["safety_status"], C_GREEN if d["safety"]["is_safe"] else C_RED)
    p_field("Blocked Hazardous Commands:", len(d["safety"]["blocked_steps"]))
    for b in d["safety"]["blocked_steps"]:
        p_field("  ✗ BLOCKED COMMAND:", f"{b.get('original_step', {}).get('command')} — Reason: {b.get('reason')}", C_RED)

    p_section("5. FINAL OPERATOR VERDICT & REVIEW GATE")
    rev_color = C_RED if d["human_review_required"] else C_GREEN
    p_field("HUMAN REVIEW REQUIRED:", str(d["human_review_required"]).upper(), rev_color)
    p_field("Audit Reference:", d["audit_reference"], C_DIM)

    return d


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentinel End-to-End Demonstration")
    parser.add_argument("--scenario", choices=["A", "B", "C", "D", "ALL"], default="ALL", help="Scenario to run")
    parser.add_argument("--mode", choices=["stub", "local", "cloud"], default="stub", help="LLM reasoning branch mode")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    parser.add_argument(
        "--reconciliation",
        action="store_true",
        help=(
            "Explicitly activate reconciliation for the demo (sets "
            "RECONCILIATION_ENABLED=true for this process) and print a focused "
            "deterministic case-separation section. Off by default."
        ),
    )
    args = parser.parse_args()

    # §3 explicit demo activation: turn the flag on for THIS process only when
    # the operator asks. The production default (reconciliation_enabled()==False)
    # is never changed; this is the documented opt-in activation path.
    if args.reconciliation:
        os.environ["RECONCILIATION_ENABLED"] = "true"

    # Credential Verification for Cloud Mode
    if args.mode == "cloud":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print(f"{C_RED}ERROR: Cloud mode requested but GEMINI_API_KEY environment variable is missing.{C_RESET}", file=sys.stderr)
            print(f"{C_YELLOW}Sentinel fails closed rather than fabricating unauthenticated cloud calls.{C_RESET}", file=sys.stderr)
            sys.exit(1)

    engine = EndToEndDemoEngine(mode=args.mode)

    scenarios_to_run = []
    if args.scenario in ("A", "ALL"):
        scenarios_to_run.append(build_scenario_a_single_fault())
    if args.scenario in ("B", "ALL"):
        scenarios_to_run.append(build_scenario_b_two_separate_faults())
    if args.scenario in ("C", "ALL"):
        scenarios_to_run.append(build_scenario_c_conflicting_evidence())
    if args.scenario in ("D", "ALL"):
        scenarios_to_run.append(build_scenario_d_insufficient_data())

    results = []
    if not args.json:
        p_banner("SENTINEL SPACECRAFT DIAGNOSTIC COPILOT — END-TO-END DEMO")
        print(f"  {C_DIM}Physics = Authority · Safety = Authority · LLM = Assistive · Reconciliation = Deterministic{C_RESET}")
        if args.reconciliation:
            print(f"  {C_BOLD}{C_GREEN}RECONCILIATION EXPLICITLY ACTIVATED "
                  f"(RECONCILIATION_ENABLED=true for this run){C_RESET}")

    for sc in scenarios_to_run:
        r = run_and_print_scenario(
            engine, sc, json_only=args.json, reconciliation_focus=args.reconciliation
        )
        results.append(r)

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2))


if __name__ == "__main__":
    main()
