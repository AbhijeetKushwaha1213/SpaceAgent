"""
SENTINEL — Phase 24 Reconciliation 6-Scenario Demo (demo/reconciliation_demo.py)

Demonstrates the 6 canonical reconciliation scenarios:
  1. DUPLICATE observations: Exact signature equality across detectors -> Merged into 1 Case.
  2. SAME_CASE multi-channel events: Corroborated across >=3 independent signals -> Merged into 1 Case.
  3. RELATED cases: Physical propagation path (AOCS -> EPS) -> 2 separate Cases, RELATED link.
  4. SEPARATE cases: Independent distant subsystems (COMMS vs PYLD) -> 2 separate Cases, SEPARATE link.
  5. CONFLICT observations: Contradictory telemetry / physics verdicts -> 2 Cases, CONFLICT link, Human Review.
  6. UNCERTAIN observations: Ambiguous / defective data -> Separate Cases, UNCERTAIN link, Human Review.

Core Principle: CORRELATION != IDENTITY.
"""

from __future__ import annotations

from typing import Any

from app.reconciliation.config import DEFAULT_CONFIG
from app.reconciliation.contract import (
    ObservationEvent,
    ReconciliationInput,
    ReconciliationResult,
    RelationshipType,
)
from app.reconciliation.engine import ReconciliationEngine


def scenario_duplicate_observations() -> ReconciliationResult:
    """Scenario 1: Exact duplicate findings on V_bat from redundant detector runs."""
    e1 = ObservationEvent(
        event_id="EVT-DUP-1",
        channel="V_bat",
        subsystem="EPS",
        severity="CRITICAL",
        severity_rank=3,
        detectors=("HARD_LIMIT",),
        anomaly_ids=("AN-1",),
        timestamps=("T-120s",),
        directions=("LOW",),
        first_seen_s=-120.0,
        last_seen_s=-120.0,
        candidate_fault_ids=("EPS_BATTERY_DEGRADATION",),
        corroborated=True,
        scenario_id="DEMO-DUP",
    )
    e2 = ObservationEvent(
        event_id="EVT-DUP-2",
        channel="V_bat",
        subsystem="EPS",
        severity="CRITICAL",
        severity_rank=3,
        detectors=("HARD_LIMIT",),
        anomaly_ids=("AN-2",),
        timestamps=("T-120s",),
        directions=("LOW",),
        first_seen_s=-120.0,
        last_seen_s=-120.0,
        candidate_fault_ids=("EPS_BATTERY_DEGRADATION",),
        corroborated=True,
        scenario_id="DEMO-DUP",
    )
    inp = ReconciliationInput(events=(e1, e2), scenario_id="DEMO-DUP")
    return ReconciliationEngine().reconcile(inp)


def scenario_same_case_corroboration() -> ReconciliationResult:
    """Scenario 2: Multi-channel findings within EPS supporting single root cause."""
    e1 = ObservationEvent(
        event_id="EVT-EPS-ISA",
        channel="I_sa",
        subsystem="EPS",
        severity="CRITICAL",
        severity_rank=3,
        detectors=("HARD_LIMIT", "STATISTICAL"),
        anomaly_ids=("AN-10",),
        timestamps=("T-100s",),
        directions=("LOW",),
        first_seen_s=-100.0,
        last_seen_s=-100.0,
        candidate_fault_ids=("EPS_SOLAR_UNDERVOLT",),
        corroborated=True,
        scenario_id="DEMO-SAME",
    )
    e2 = ObservationEvent(
        event_id="EVT-EPS-VBAT",
        channel="V_bat",
        subsystem="EPS",
        severity="CRITICAL",
        severity_rank=3,
        detectors=("HARD_LIMIT", "STATISTICAL"),
        anomaly_ids=("AN-11",),
        timestamps=("T-105s",),
        directions=("LOW",),
        first_seen_s=-105.0,
        last_seen_s=-105.0,
        candidate_fault_ids=("EPS_SOLAR_UNDERVOLT",),
        corroborated=True,
        scenario_id="DEMO-SAME",
    )
    inp = ReconciliationInput(events=(e1, e2), scenario_id="DEMO-SAME")
    return ReconciliationEngine().reconcile(inp)


def scenario_related_propagation_cases() -> ReconciliationResult:
    """Scenario 3: Causal propagation across subsystems (AOCS reaction wheel -> EPS array current)."""
    e_aocs = ObservationEvent(
        event_id="EVT-AOCS-RW",
        channel="gyro_rate",
        subsystem="AOCS",
        severity="WARNING",
        severity_rank=2,
        detectors=("CUSUM",),
        anomaly_ids=("AN-20",),
        timestamps=("T-150s",),
        directions=("HIGH",),
        first_seen_s=-150.0,
        last_seen_s=-150.0,
        candidate_fault_ids=("ADCS_GYRO_SEU",),
        corroborated=True,
        scenario_id="DEMO-PROP",
    )
    e_eps = ObservationEvent(
        event_id="EVT-EPS-ISA",
        channel="I_sa",
        subsystem="EPS",
        severity="CRITICAL",
        severity_rank=3,
        detectors=("HARD_LIMIT",),
        anomaly_ids=("AN-21",),
        timestamps=("T-120s",),
        directions=("LOW",),
        first_seen_s=-120.0,
        last_seen_s=-120.0,
        candidate_fault_ids=("EPS_SOLAR_UNDERVOLT",),
        corroborated=True,
        scenario_id="DEMO-PROP",
    )
    inp = ReconciliationInput(events=(e_aocs, e_eps), scenario_id="DEMO-PROP")
    return ReconciliationEngine().reconcile(inp)


def scenario_separate_independent_cases() -> ReconciliationResult:
    """Scenario 4: Completely distinct independent observations across subsystems."""
    e_comms = ObservationEvent(
        event_id="EVT-COMMS-SNR",
        channel="snr_db",
        subsystem="COMMS",
        severity="LOW",
        severity_rank=1,
        detectors=("STATISTICAL",),
        anomaly_ids=("AN-30",),
        timestamps=("T-10s",),
        directions=("LOW",),
        first_seen_s=-10.0,
        last_seen_s=-10.0,
        candidate_fault_ids=("COMMS_RX_DEGRADED",),
        corroborated=False,
        scenario_id="DEMO-SEP",
    )
    e_pyld = ObservationEvent(
        event_id="EVT-PYLD-TEMP",
        channel="optics_temp",
        subsystem="PYLD",
        severity="WARNING",
        severity_rank=2,
        detectors=("HARD_LIMIT",),
        anomaly_ids=("AN-31",),
        timestamps=("T-900s",),
        directions=("HIGH",),
        first_seen_s=-900.0,
        last_seen_s=-900.0,
        candidate_fault_ids=("PYLD_OPTICS_OVERTEMP",),
        corroborated=False,
        scenario_id="DEMO-SEP",
    )
    inp = ReconciliationInput(events=(e_comms, e_pyld), scenario_id="DEMO-SEP")
    return ReconciliationEngine().reconcile(inp)


def scenario_conflicting_observations() -> ReconciliationResult:
    """Scenario 5: Contradictory directions on shared channel requiring human review."""
    e_high = ObservationEvent(
        event_id="EVT-VBAT-HIGH",
        channel="V_bat",
        subsystem="EPS",
        severity="CRITICAL",
        severity_rank=3,
        detectors=("HARD_LIMIT",),
        anomaly_ids=("AN-40",),
        timestamps=("T-50s",),
        directions=("HIGH",),
        first_seen_s=-50.0,
        last_seen_s=-50.0,
        candidate_fault_ids=("EPS_OVERVOLT",),
        corroborated=False,
        scenario_id="DEMO-CONFLICT",
    )
    e_low = ObservationEvent(
        event_id="EVT-VBAT-LOW",
        channel="V_bat",
        subsystem="EPS",
        severity="CRITICAL",
        severity_rank=3,
        detectors=("HARD_LIMIT",),
        anomaly_ids=("AN-41",),
        timestamps=("T-50s",),
        directions=("LOW",),
        first_seen_s=-50.0,
        last_seen_s=-50.0,
        candidate_fault_ids=("EPS_UNDERVOLT",),
        corroborated=False,
        scenario_id="DEMO-CONFLICT",
    )
    inp = ReconciliationInput(events=(e_high, e_low), scenario_id="DEMO-CONFLICT")
    return ReconciliationEngine().reconcile(inp)


def scenario_uncertain_ambiguous_evidence() -> ReconciliationResult:
    """Scenario 6: Malformed / unparseable offsets requiring human review."""
    e_clean = ObservationEvent(
        event_id="EVT-CLEAN",
        channel="I_sa",
        subsystem="EPS",
        severity="LOW",
        severity_rank=1,
        detectors=("STATISTICAL",),
        anomaly_ids=("AN-50",),
        timestamps=("T-20s",),
        directions=("LOW",),
        first_seen_s=-20.0,
        last_seen_s=-20.0,
        scenario_id="DEMO-UNCERTAIN",
    )
    e_defective = ObservationEvent(
        event_id="EVT-DEFECTIVE",
        channel="V_unknown",
        subsystem="UNKNOWN",
        severity="UNKNOWN",
        severity_rank=0,
        detectors=(),
        anomaly_ids=("AN-51",),
        timestamps=("CORRUPTED_TIMESTAMP",),
        directions=("UNKNOWN",),
        first_seen_s=None,
        last_seen_s=None,
        defects=("Unparseable timestamp 'CORRUPTED_TIMESTAMP'", "Subsystem UNKNOWN"),
        scenario_id="DEMO-UNCERTAIN",
    )
    inp = ReconciliationInput(events=(e_clean, e_defective), scenario_id="DEMO-UNCERTAIN")
    return ReconciliationEngine().reconcile(inp)


def run_all_scenarios() -> list[dict[str, Any]]:
    """Execute all 6 reconciliation demo scenarios and format summaries."""
    scenarios = [
        ("Scenario 1: DUPLICATE Observations", scenario_duplicate_observations),
        ("Scenario 2: SAME_CASE Multi-Channel Corroboration", scenario_same_case_corroboration),
        ("Scenario 3: RELATED Causal Propagation", scenario_related_propagation_cases),
        ("Scenario 4: SEPARATE Independent Events", scenario_separate_independent_cases),
        ("Scenario 5: CONFLICT Contradictory Findings", scenario_conflicting_observations),
        ("Scenario 6: UNCERTAIN Ambiguous Data", scenario_uncertain_ambiguous_evidence),
    ]

    results = []
    for title, fn in scenarios:
        res = fn()
        rel_summary = (
            ", ".join(f"{r.relationship_type.value} ({r.source_case_id}<->{r.target_case_id})" for r in res.relationships)
            if res.relationships
            else "None (Single Merged Case)"
        )
        results.append(
            {
                "title": title,
                "case_count": res.case_count,
                "relationship_count": len(res.relationships),
                "relationship_summary": rel_summary,
                "human_review_required": res.human_review_required,
                "warnings": list(res.warnings),
            }
        )

    return results


if __name__ == "__main__":
    print("=" * 70)
    print(" SENTINEL — Phase 24 Deterministic Reconciliation 6-Scenario Demo")
    print("=" * 70)
    for res in run_all_scenarios():
        print(f"\n▶ {res['title']}")
        print(f"  • Cases Formed: {res['case_count']}")
        print(f"  • Relationships: {res['relationship_summary']}")
        print(f"  • Human Review Required: {res['human_review_required']}")
        if res["warnings"]:
            print(f"  • Warnings: {res['warnings']}")
    print("\n" + "=" * 70)
