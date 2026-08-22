"""
Tests for Phase 24 Reconciliation Engine (tests/test_phase24_reconciliation_engine.py).

Verifies:
  - Deterministic Case construction and ID stability.
  - Priority ladder classification: DUPLICATE, CONFLICT, SAME_CASE, RELATED, SEPARATE, UNCERTAIN.
  - Case clustering and merge rules (merge_permitted vs preserve separation).
  - Conflict preservation (conflicting evidence is never discarded).
  - Inter-case relationship generation and ordering independence.
  - Monotone human review requirement.
"""

import pytest

from app.reconciliation.config import DEFAULT_CONFIG
from app.reconciliation.contract import (
    ObservationEvent,
    ReconciliationInput,
    RelationshipType,
    make_case_id,
    make_relationship_id,
)
from app.reconciliation.engine import ReconciliationEngine


def _make_evt(
    event_id: str,
    channel: str = "I_sa",
    subsystem: str = "EPS",
    severity: str = "CRITICAL",
    severity_rank: int = 3,
    detectors: tuple[str, ...] = ("HARD_LIMIT",),
    anomaly_ids: tuple[str, ...] = ("AN-1",),
    timestamps: tuple[str, ...] = ("T-120s",),
    directions: tuple[str, ...] = ("LOW",),
    first_seen_s: float | None = -120.0,
    last_seen_s: float | None = -120.0,
    candidate_fault_ids: tuple[str, ...] = ("EPS_SOLAR_UNDERVOLT",),
    scenario_id: str = "SC-1",
    defects: tuple[str, ...] = (),
) -> ObservationEvent:
    return ObservationEvent(
        event_id=event_id,
        channel=channel,
        subsystem=subsystem,
        severity=severity,
        severity_rank=severity_rank,
        detectors=detectors,
        anomaly_ids=anomaly_ids,
        timestamps=timestamps,
        directions=directions,
        first_seen_s=first_seen_s,
        last_seen_s=last_seen_s,
        candidate_fault_ids=candidate_fault_ids,
        corroborated=True,
        scenario_id=scenario_id,
        source_ref="test",
        defects=defects,
    )


class TestReconciliationEngine:
    def test_empty_input_returns_empty_result(self):
        engine = ReconciliationEngine()
        inp = ReconciliationInput(events=())
        res = engine.reconcile(inp)
        assert res.case_count == 0
        assert len(res.cases) == 0
        assert len(res.relationships) == 0
        assert res.human_review_required is False

    def test_single_event_creates_single_case(self):
        engine = ReconciliationEngine()
        e1 = _make_evt("EVT-1")
        inp = ReconciliationInput(events=(e1,), scenario_id="SC-1")
        res = engine.reconcile(inp)
        assert res.case_count == 1
        case = res.cases[0]
        assert case.event_ids == ("EVT-1",)
        assert case.primary_subsystem == "EPS"
        assert len(res.relationships) == 0

    def test_duplicate_events_merged_into_single_case(self):
        engine = ReconciliationEngine()
        e1 = _make_evt("EVT-1", channel="V_bat", timestamps=("T-0s",), directions=("LOW",), severity="CRITICAL")
        e2 = _make_evt("EVT-2", channel="V_bat", timestamps=("T-0s",), directions=("LOW",), severity="CRITICAL")
        inp = ReconciliationInput(events=(e1, e2), scenario_id="SC-1")
        res = engine.reconcile(inp)
        assert res.case_count == 1
        assert len(res.merges_performed) == 1
        assert res.cases[0].event_ids == ("EVT-1", "EVT-2")

    def test_same_case_multi_signal_corroboration_merged(self):
        engine = ReconciliationEngine()
        # Same subsystem, near onset (delta 5s), matching candidate fault, compatible pattern
        e1 = _make_evt("EVT-1", channel="I_sa", subsystem="EPS", first_seen_s=-100.0, candidate_fault_ids=("EPS_SOLAR_UNDERVOLT",))
        e2 = _make_evt("EVT-2", channel="V_bat", subsystem="EPS", first_seen_s=-105.0, candidate_fault_ids=("EPS_SOLAR_UNDERVOLT",))
        inp = ReconciliationInput(events=(e1, e2), scenario_id="SC-1")
        res = engine.reconcile(inp)
        assert res.case_count == 1
        assert set(res.cases[0].event_ids) == {"EVT-1", "EVT-2"}
        assert set(res.cases[0].channels) == {"I_sa", "V_bat"}

    def test_related_cases_preserved_separately_with_relationship(self):
        engine = ReconciliationEngine()
        # AOCS fault propagating to EPS current drop
        e1 = _make_evt("EVT-AOCS", channel="gyro_rate", subsystem="AOCS", first_seen_s=-120.0, candidate_fault_ids=("ADCS_GYRO_SEU",))
        e2 = _make_evt("EVT-EPS", channel="I_sa", subsystem="EPS", first_seen_s=-100.0, candidate_fault_ids=("EPS_SOLAR_UNDERVOLT",))
        inp = ReconciliationInput(events=(e1, e2), scenario_id="SC-1")
        res = engine.reconcile(inp)

        # Correlation != Identity: Must stay SEPARATE cases
        assert res.case_count == 2
        assert len(res.relationships) == 1
        rel = res.relationships[0]
        assert rel.relationship_type == RelationshipType.RELATED
        assert rel.propagation_source_case_id == res.case_for_event("EVT-AOCS")

    def test_completely_separate_cases_preserved(self):
        engine = ReconciliationEngine()
        e1 = _make_evt("EVT-COMMS", channel="snr_db", subsystem="COMMS", first_seen_s=-10.0)
        e2 = _make_evt("EVT-PYLD", channel="optics_temp", subsystem="PYLD", first_seen_s=-800.0)  # distant time, unrelated sub
        inp = ReconciliationInput(events=(e1, e2), scenario_id="SC-1")
        res = engine.reconcile(inp)

        assert res.case_count == 2
        assert len(res.relationships) == 1
        assert res.relationships[0].relationship_type == RelationshipType.SEPARATE

    def test_conflicting_observations_preserved_and_flag_human_review(self):
        engine = ReconciliationEngine()
        # Opposed directions on shared channel V_bat
        e1 = _make_evt("EVT-HIGH", channel="V_bat", directions=("HIGH",), first_seen_s=-50.0)
        e2 = _make_evt("EVT-LOW", channel="V_bat", directions=("LOW",), first_seen_s=-50.0)
        inp = ReconciliationInput(events=(e1, e2), scenario_id="SC-1")
        res = engine.reconcile(inp)

        # Contradictions are NEVER discarded: Both cases exist and are linked as CONFLICT
        assert res.case_count == 2
        assert len(res.relationships) == 1
        assert res.relationships[0].relationship_type == RelationshipType.CONFLICT
        assert res.human_review_required is True
        assert any("Unresolved relationship" in w for w in res.warnings)

    def test_deterministic_ids_are_order_independent(self):
        id_1 = make_relationship_id("CASE-A", "CASE-B", RelationshipType.RELATED)
        id_2 = make_relationship_id("CASE-B", "CASE-A", RelationshipType.RELATED)
        assert id_1 == id_2
        assert id_1.startswith("REL-")

        c_id_1 = make_case_id(("EVT-1", "EVT-2"), "SC-1")
        c_id_2 = make_case_id(("EVT-2", "EVT-1"), "SC-1")
        assert c_id_1 == c_id_2
        assert c_id_1.startswith("CASE-")
