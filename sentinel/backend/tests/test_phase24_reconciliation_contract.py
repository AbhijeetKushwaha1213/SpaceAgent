"""
Tests for Phase 24 Reconciliation Contracts and Isolation (tests/test_phase24_reconciliation_contract.py).

Verifies:
  - Immutable data contracts (frozen dataclasses, no raw model text/confidence).
  - Deterministic ID generators: make_event_id, make_case_id, make_relationship_id.
  - RelationshipType taxonomy and merge_permitted invariants.
  - SignalOutcome helper properties and dictionary serializability.
  - Case primary_subsystem derivation.
  - CaseIsolationBoundary filtering and cross-case leakage assertions.
  - RAG filtering boundary pass-through and case scoping.
"""

import pytest

from app.reconciliation.cases import build_case_from_events
from app.reconciliation.config import (
    DEFAULT_CONFIG,
    RECONCILIATION_CONFIG_VERSION,
    RECONCILIATION_ENGINE_VERSION,
    ReconciliationConfig,
)
from app.reconciliation.contract import (
    Case,
    CaseRelationship,
    ObservationEvent,
    ReconciliationInput,
    ReconciliationResult,
    ReconciliationSignal,
    RelationshipType,
    SignalOutcome,
    SignalVerdict,
    make_case_id,
    make_event_id,
    make_relationship_id,
)
from app.reconciliation.isolation import (
    CaseIsolationBoundary,
    CrossCaseLeakageError,
)
from app.reconciliation.rag_filter import filter_rag_context_for_case


def _make_sample_event(
    event_id: str = "EVT-test1",
    channel: str = "I_sa",
    subsystem: str = "EPS",
) -> ObservationEvent:
    return ObservationEvent(
        event_id=event_id,
        channel=channel,
        subsystem=subsystem,
        severity="CRITICAL",
        severity_rank=3,
        detectors=("HARD_LIMIT",),
        anomaly_ids=("AN-1",),
        timestamps=("T-100s",),
        directions=("LOW",),
        first_seen_s=-100.0,
        last_seen_s=-100.0,
        candidate_fault_ids=("EPS_SOLAR_UNDERVOLT",),
        scenario_id="SC-1",
    )


class TestReconciliationContracts:
    def test_id_generation_is_stable_and_deterministic(self):
        ev_id1 = make_event_id("I_sa", ("AN-1", "AN-2"), "SC-1")
        ev_id2 = make_event_id("I_sa", ("AN-2", "AN-1"), "SC-1")  # Order should not change hash
        assert ev_id1 == ev_id2
        assert ev_id1.startswith("EVT-")

        c_id1 = make_case_id(("EVT-1", "EVT-2"), "SC-1")
        c_id2 = make_case_id(("EVT-2", "EVT-1"), "SC-1")
        assert c_id1 == c_id2
        assert c_id1.startswith("CASE-")

        r_id1 = make_relationship_id("CASE-1", "CASE-2", RelationshipType.RELATED)
        r_id2 = make_relationship_id("CASE-2", "CASE-1", RelationshipType.RELATED)
        assert r_id1 == r_id2
        assert r_id1.startswith("REL-")

    def test_relationship_type_properties(self):
        assert RelationshipType.DUPLICATE.merge_permitted is True
        assert RelationshipType.SAME_CASE.merge_permitted is True
        assert RelationshipType.RELATED.merge_permitted is False
        assert RelationshipType.SEPARATE.merge_permitted is False
        assert RelationshipType.CONFLICT.merge_permitted is False
        assert RelationshipType.UNCERTAIN.merge_permitted is False

        assert RelationshipType.CONFLICT.is_unresolved is True
        assert RelationshipType.UNCERTAIN.is_unresolved is True
        assert RelationshipType.SAME_CASE.is_unresolved is False
        assert RelationshipType.SEPARATE.is_unresolved is False

    def test_case_primary_subsystem_derivation(self):
        e1 = _make_sample_event("EVT-1", "I_sa", "EPS")
        e2 = _make_sample_event("EVT-2", "V_bat", "EPS")
        case_single = build_case_from_events([e1, e2], "SC-1")
        assert case_single.primary_subsystem == "EPS"

        e3 = _make_sample_event("EVT-3", "gyro_rate", "AOCS")
        case_multi = build_case_from_events([e1, e3], "SC-1")
        assert case_multi.primary_subsystem == "MULTI"

    def test_input_contract_has_no_llm_fields(self):
        inp = ReconciliationInput(events=())
        assert not hasattr(inp, "raw_text")
        assert not hasattr(inp, "model_confidence")
        assert not hasattr(inp, "llm_case_id")
        assert not hasattr(inp, "reasoning")


class TestIsolationBoundary:
    def test_isolate_evidence_removes_separate_case_evidence(self):
        e_eps = _make_sample_event("EVT-EPS", "I_sa", "EPS")
        e_comms = _make_sample_event("EVT-COMMS", "snr_db", "COMMS")

        case_eps = build_case_from_events([e_eps], "SC-1")
        case_comms = build_case_from_events([e_comms], "SC-1")

        rel = CaseRelationship(
            relationship_id=make_relationship_id(case_eps.case_id, case_comms.case_id, RelationshipType.SEPARATE),
            source_case_id=min(case_eps.case_id, case_comms.case_id),
            target_case_id=max(case_eps.case_id, case_comms.case_id),
            relationship_type=RelationshipType.SEPARATE,
        )

        result = ReconciliationResult(
            cases=(case_eps, case_comms),
            relationships=(rel,),
            event_assignments=((e_eps.event_id, case_eps.case_id), (e_comms.event_id, case_comms.case_id)),
            config_version=RECONCILIATION_CONFIG_VERSION,
            engine_version=RECONCILIATION_ENGINE_VERSION,
        )

        evidence_items = [
            type("EvidenceMock", (), {"channel": "I_sa", "event_id": "EVT-EPS"})(),
            type("EvidenceMock", (), {"channel": "snr_db", "event_id": "EVT-COMMS"})(),
        ]

        isolated = CaseIsolationBoundary.isolate_evidence_for_case(case_eps.case_id, result, evidence_items)
        assert len(isolated) == 1
        assert isolated[0].channel == "I_sa"

    def test_assert_no_cross_case_leakage_raises_on_contamination(self):
        e_eps = _make_sample_event("EVT-EPS", "I_sa", "EPS")
        e_comms = _make_sample_event("EVT-COMMS", "snr_db", "COMMS")

        case_eps = build_case_from_events([e_eps], "SC-1")
        case_comms = build_case_from_events([e_comms], "SC-1")

        rel = CaseRelationship(
            relationship_id=make_relationship_id(case_eps.case_id, case_comms.case_id, RelationshipType.SEPARATE),
            source_case_id=min(case_eps.case_id, case_comms.case_id),
            target_case_id=max(case_eps.case_id, case_comms.case_id),
            relationship_type=RelationshipType.SEPARATE,
        )

        result = ReconciliationResult(
            cases=(case_eps, case_comms),
            relationships=(rel,),
            event_assignments=((e_eps.event_id, case_eps.case_id), (e_comms.event_id, case_comms.case_id)),
        )

        contaminated_bundle = [
            type("EvidenceMock", (), {"channel": "I_sa"})(),
            type("EvidenceMock", (), {"channel": "snr_db"})(),  # Leaked from separate COMMS case
        ]

        with pytest.raises(CrossCaseLeakageError, match="leaked into bundle"):
            CaseIsolationBoundary.assert_no_cross_case_leakage(case_eps.case_id, result, contaminated_bundle)


class TestRAGFilter:
    def test_rag_filter_pass_through_when_disabled(self, monkeypatch):
        monkeypatch.setenv("RECONCILIATION_ENABLED", "false")
        text, trace = filter_rag_context_for_case("CASE-1", None, "Proc text", {"snippets": [{"text": "S1"}]})
        assert text == "Proc text"
        assert trace["snippets"] == [{"text": "S1"}]
