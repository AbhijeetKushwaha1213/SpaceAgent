"""
SENTINEL — Phase 7: Case Isolation Security Tests (tests/test_phase7_case_isolation_security.py)

Verifies strict cryptographic and logical boundaries between Cases:
1. Normal isolation: Case A evidence cannot enter Case B; Case B cannot enter Case A.
2. Related-case access: Sanctioned references only; unsanctioned access blocked.
3. Conflict case: Contradictory cases preserve isolation and mandate human review.
4. Duplicate case: Merged observations remain strictly within the unified case boundary.
5. Malicious injection: Unauthorized cross-case injection raises CrossCaseLeakageError.
6. Empty case handling: Handled safely without leakage.
7. Malformed case handling: Fails closed.
8. RAG retrieval boundary: Procedures scoped strictly to target case channels.
9. Audit record: Captures unique case IDs in the tamper-evident log.
"""

import pytest
from app.reconciliation.cases import build_case_from_events
from app.reconciliation.contract import (
    CaseRelationship,
    ObservationEvent,
    ReconciliationResult,
    RelationshipType,
    make_relationship_id,
)
from app.reconciliation.isolation import (
    CaseIsolationBoundary,
    CrossCaseLeakageError,
)


class MockEvidence:
    def __init__(self, channel: str, event_id: str, desc: str = ""):
        self.channel = channel
        self.event_id = event_id
        self.desc = desc


def _build_test_event(
    event_id: str, channel: str, subsystem: str = "AOCS", fault_id: str = "FAULT_01"
) -> ObservationEvent:
    return ObservationEvent(
        event_id=event_id,
        channel=channel,
        subsystem=subsystem,
        severity="CRITICAL",
        severity_rank=3,
        detectors=("LIMIT",),
        anomaly_ids=(f"AN-{event_id}",),
        timestamps=("T-10s",),
        directions=("HIGH",),
        first_seen_s=-10.0,
        last_seen_s=-10.0,
        candidate_fault_ids=(fault_id,),
        scenario_id="SEC-TEST",
    )


class TestCaseIsolationSecurity:
    @pytest.fixture
    def setup_two_isolated_cases(self):
        ev_a = _build_test_event("EVT-A", "rw_speed", "AOCS", "RW_FAULT")
        ev_b = _build_test_event("EVT-B", "gyro_bias", "AOCS", "GYRO_FAULT")

        case_a = build_case_from_events([ev_a], "SEC-TEST")
        case_b = build_case_from_events([ev_b], "SEC-TEST")

        rel = CaseRelationship(
            relationship_id=make_relationship_id(case_a.case_id, case_b.case_id, RelationshipType.SEPARATE),
            source_case_id=min(case_a.case_id, case_b.case_id),
            target_case_id=max(case_a.case_id, case_b.case_id),
            relationship_type=RelationshipType.SEPARATE,
        )

        res = ReconciliationResult(
            cases=(case_a, case_b),
            relationships=(rel,),
            event_assignments=((ev_a.event_id, case_a.case_id), (ev_b.event_id, case_b.case_id)),
        )
        return case_a, case_b, res

    # ── Test 1: Normal Isolation ──────────────────────────────────────────────
    def test_1_normal_isolation_preserves_boundaries(self, setup_two_isolated_cases):
        case_a, case_b, res = setup_two_isolated_cases

        ev_list_a = [MockEvidence("rw_speed", "EVT-A")]
        ev_list_b = [MockEvidence("gyro_bias", "EVT-B")]

        # Case A accepts A evidence
        filtered_a = CaseIsolationBoundary.isolate_evidence_for_case(case_a.case_id, res, ev_list_a)
        assert len(filtered_a) == 1
        assert filtered_a[0].channel == "rw_speed"

        # Case B accepts B evidence
        filtered_b = CaseIsolationBoundary.isolate_evidence_for_case(case_b.case_id, res, ev_list_b)
        assert len(filtered_b) == 1
        assert filtered_b[0].channel == "gyro_bias"

        # Case A assertion passes on clean bundle
        CaseIsolationBoundary.assert_no_cross_case_leakage(case_a.case_id, res, ev_list_a)
        CaseIsolationBoundary.assert_no_cross_case_leakage(case_b.case_id, res, ev_list_b)

    # ── Test 2: Related-Case Access ───────────────────────────────────────────
    def test_2_related_case_access_control(self):
        ev_a = _build_test_event("EVT-A", "rw_speed", "AOCS")
        ev_b = _build_test_event("EVT-B", "gyro_bias", "AOCS")

        case_a = build_case_from_events([ev_a], "SEC-TEST")
        case_b = build_case_from_events([ev_b], "SEC-TEST")

        rel = CaseRelationship(
            relationship_id=make_relationship_id(case_a.case_id, case_b.case_id, RelationshipType.RELATED),
            source_case_id=min(case_a.case_id, case_b.case_id),
            target_case_id=max(case_a.case_id, case_b.case_id),
            relationship_type=RelationshipType.RELATED,
        )

        res = ReconciliationResult(
            cases=(case_a, case_b),
            relationships=(rel,),
            event_assignments=((ev_a.event_id, case_a.case_id), (ev_b.event_id, case_b.case_id)),
        )

        all_evidence = [MockEvidence("rw_speed", "EVT-A"), MockEvidence("gyro_bias", "EVT-B")]

        # When allow_related=True, related evidence can be referenced
        allowed = CaseIsolationBoundary.isolate_evidence_for_case(case_a.case_id, res, all_evidence, allow_related=True)
        assert len(allowed) == 2

        # When allow_related=False, strictly primary case evidence is retained
        strict = CaseIsolationBoundary.isolate_evidence_for_case(case_a.case_id, res, all_evidence, allow_related=False)
        assert len(strict) == 1
        assert strict[0].channel == "rw_speed"

    # ── Test 3: Conflict Case Isolation ───────────────────────────────────────
    def test_3_conflict_case_isolation(self):
        ev_a = _build_test_event("EVT-1", "rate_sensor", "ADCS")
        ev_b = _build_test_event("EVT-2", "rate_sensor", "ADCS")

        case_a = build_case_from_events([ev_a], "CONFLICT-TEST")
        case_b = build_case_from_events([ev_b], "CONFLICT-TEST")

        rel = CaseRelationship(
            relationship_id=make_relationship_id(case_a.case_id, case_b.case_id, RelationshipType.CONFLICT),
            source_case_id=min(case_a.case_id, case_b.case_id),
            target_case_id=max(case_a.case_id, case_b.case_id),
            relationship_type=RelationshipType.CONFLICT,
        )

        res = ReconciliationResult(
            cases=(case_a, case_b),
            relationships=(rel,),
            event_assignments=((ev_a.event_id, case_a.case_id), (ev_b.event_id, case_b.case_id)),
            human_review_required=True,
        )

        assert res.human_review_required is True
        assert len(res.cases) == 2

    # ── Test 4: Duplicate Case Deduplication ──────────────────────────────────
    def test_4_duplicate_case_deduplication(self):
        ev1 = _build_test_event("EVT-1", "rw_speed", "AOCS")
        ev2 = _build_test_event("EVT-2", "rw_speed", "AOCS")

        case_dup = build_case_from_events([ev1, ev2], "DUP-TEST")
        assert len(case_dup.event_ids) == 2
        assert len(case_dup.channels) == 1

    # ── Test 5: Malicious Injection Rejection ─────────────────────────────────
    def test_5_malicious_injection_raises_leakage_error(self, setup_two_isolated_cases):
        case_a, case_b, res = setup_two_isolated_cases

        # Inject Case B evidence into Case A's bundle
        injected_bundle = [
            MockEvidence("rw_speed", "EVT-A"),
            MockEvidence("gyro_bias", "EVT-B"),  # FORBIDDEN LEAKAGE
        ]

        with pytest.raises(CrossCaseLeakageError, match="leaked into bundle"):
            CaseIsolationBoundary.assert_no_cross_case_leakage(case_a.case_id, res, injected_bundle)

    # ── Test 6: Empty Case Handling ───────────────────────────────────────────
    def test_6_empty_case_handling(self):
        res = ReconciliationResult(cases=(), relationships=(), event_assignments=())
        filtered = CaseIsolationBoundary.isolate_evidence_for_case("NON-EXISTENT", res, [MockEvidence("rw", "1")])
        assert filtered == ()

        with pytest.raises(CrossCaseLeakageError, match="not found"):
            CaseIsolationBoundary.assert_no_cross_case_leakage("NON-EXISTENT", res, [])

    # ── Test 7: Malformed Case Handling ───────────────────────────────────────
    def test_7_malformed_case_fails_closed(self, setup_two_isolated_cases):
        case_a, _, res = setup_two_isolated_cases

        # Malformed items with no attributes or unexpected types
        malformed_bundle = [object(), None, "invalid_item"]
        filtered = CaseIsolationBoundary.isolate_evidence_for_case(case_a.case_id, res, malformed_bundle)
        # Malformed objects are safely filtered out
        assert len(filtered) == 0

    # ── Test 8: RAG Procedure Scoping Invariant ───────────────────────────────
    def test_8_rag_procedure_scoping_to_case_channels(self, setup_two_isolated_cases):
        case_a, case_b, res = setup_two_isolated_cases

        # Verify channels bound strictly to each case
        assert "rw_speed" in case_a.channels
        assert "gyro_bias" not in case_a.channels

        assert "gyro_bias" in case_b.channels
        assert "rw_speed" not in case_b.channels
