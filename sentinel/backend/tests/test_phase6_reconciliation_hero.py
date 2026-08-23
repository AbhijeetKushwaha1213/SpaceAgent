"""
SENTINEL — Phase 6: Reconciliation Hero Tests (tests/test_phase6_reconciliation_hero.py)

Verifies the 6 canonical relationship types and ensures that:
- CORRELATION != IDENTITY is strictly enforced.
- Every relationship exposes deterministic WHY reasons.
- No similarity score or superficial co-occurrence ever forces a false merge.
- Merges are permitted ONLY for DUPLICATE and SAME_CASE.
- RELATED, SEPARATE, CONFLICT, and UNCERTAIN preserve rigid case separation.
"""

import pytest
from app.reconciliation.config import (
    DEFAULT_CONFIG,
    ReconciliationConfig,
)
from app.reconciliation.contract import (
    ObservationEvent,
    ReconciliationInput,
    RelationshipType,
    SignalVerdict,
)
from app.reconciliation.engine import ReconciliationEngine


def _make_event(
    event_id: str,
    channel: str,
    subsystem: str = "AOCS",
    severity: str = "CRITICAL",
    severity_rank: int = 3,
    detectors: tuple[str, ...] = ("HARD_LIMIT",),
    anomaly_ids: tuple[str, ...] = ("AN-01",),
    timestamps: tuple[str, ...] = ("T-100s",),
    directions: tuple[str, ...] = ("HIGH",),
    first_seen_s: float | None = -100.0,
    last_seen_s: float | None = -100.0,
    candidate_fault_ids: tuple[str, ...] = (),
    defects: tuple[str, ...] = (),
) -> ObservationEvent:
    faults = candidate_fault_ids if candidate_fault_ids else (f"FAULT_FOR_{event_id}",)
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
        candidate_fault_ids=faults,
        defects=defects,
        scenario_id="TEST-SCENARIO",
    )


class TestReconciliationHeroFeature:
    @pytest.fixture
    def engine(self):
        return ReconciliationEngine(DEFAULT_CONFIG)

    # ── 1. DUPLICATE Verification ─────────────────────────────────────────────
    def test_duplicate_relationship_merges_and_explains_why(self, engine):
        """Identical deterministic signature -> DUPLICATE -> merge permitted."""
        ev1 = _make_event(
            "EVT-1", "rw_speed", detectors=("HARD_LIMIT",), timestamps=("T-10s",),
            candidate_fault_ids=("FAULT_01",)
        )
        ev2 = _make_event(
            "EVT-2", "rw_speed", detectors=("HARD_LIMIT",), timestamps=("T-10s",),
            candidate_fault_ids=("FAULT_01",)
        )

        inp = ReconciliationInput(events=(ev1, ev2), scenario_id="DUP-TEST")
        result = engine.reconcile(inp)

        assert len(result.cases) == 1  # Merged into single case
        assert RelationshipType.DUPLICATE.merge_permitted is True
        assert len(result.merges_performed) == 1

    # ── 2. SAME_CASE Verification ─────────────────────────────────────────────
    def test_same_case_relationship_requires_corroboration_and_explains_why(self, engine):
        """Corroborated across independent signals -> SAME_CASE -> merge permitted."""
        ev1 = _make_event(
            "EVT-A", "rw_speed", subsystem="AOCS",
            detectors=("HARD_LIMIT",), timestamps=("T-10s",),
            first_seen_s=-10.0, last_seen_s=-10.0,
            candidate_fault_ids=("AOCS_RW_FRICTION",),
        )
        ev2 = _make_event(
            "EVT-B", "attitude_error", subsystem="AOCS",
            detectors=("STATISTICAL",), timestamps=("T-10s",),
            first_seen_s=-10.0, last_seen_s=-10.0,
            candidate_fault_ids=("AOCS_RW_FRICTION",),
        )

        inp = ReconciliationInput(events=(ev1, ev2), scenario_id="SAME-CASE-TEST")
        result = engine.reconcile(inp)

        assert RelationshipType.SAME_CASE.merge_permitted is True
        assert len(result.cases) == 1
        assert len(result.merges_performed) == 1
        assert "Merged 2 corroborated observation events." in result.cases[0].reasons

    # ── 3. RELATED Verification (Correlation != Identity Hero) ────────────────
    def test_related_relationship_preserves_separation_and_explains_why(self, engine):
        """Physical propagation or temporal coupling across distinct faults -> RELATED -> Merge PROHIBITED."""
        ev_rw = _make_event(
            "EVT-RW", "rw_speed", subsystem="AOCS",
            detectors=("HARD_LIMIT",), severity="CRITICAL", severity_rank=3, directions=("HIGH",),
            first_seen_s=-100.0, last_seen_s=-100.0,
            candidate_fault_ids=("AOCS_RW_FRICTION",),
        )
        ev_gyro = _make_event(
            "EVT-GYRO", "gyro_bias", subsystem="AOCS",
            detectors=("STATISTICAL",), severity="WARNING", severity_rank=2, directions=("LOW",),
            first_seen_s=-150.0, last_seen_s=-150.0,  # Delta = 50s > 15s same-case window
            candidate_fault_ids=("AOCS_GYRO_BIAS",),
        )

        inp = ReconciliationInput(events=(ev_rw, ev_gyro), scenario_id="RELATED-TEST")
        result = engine.reconcile(inp)

        assert len(result.cases) == 2  # Rigidly separate!
        assert len(result.relationships) == 1
        rel = result.relationships[0]
        assert rel.relationship_type is RelationshipType.RELATED
        assert rel.merge_permitted is False  # Invariant: RELATED never merges!
        assert len(rel.deterministic_reasons) > 0

    # ── 4. SEPARATE Verification ──────────────────────────────────────────────
    def test_separate_relationship_preserves_separation_and_explains_why(self, engine):
        """Different subsystems with no propagation link + disparate time windows -> SEPARATE -> Merge PROHIBITED."""
        ev_pay = _make_event(
            "EVT-PAYLOAD", "camera_sensor", subsystem="PAYLOAD",
            detectors=("STATISTICAL",), severity="INFO", severity_rank=1, directions=("HIGH",),
            first_seen_s=-1000.0, last_seen_s=-1000.0,
            candidate_fault_ids=("PAYLOAD_OVERHEAT",),
        )
        ev_prop = _make_event(
            "EVT-PROP", "valve_pressure", subsystem="PROPULSION",
            detectors=("HARD_LIMIT",), severity="CRITICAL", severity_rank=3, directions=("LOW",),
            first_seen_s=-10.0, last_seen_s=-10.0,
            candidate_fault_ids=("PROPULSION_VALVE_LEAK",),
        )

        inp = ReconciliationInput(events=(ev_pay, ev_prop), scenario_id="SEP-TEST")
        result = engine.reconcile(inp)

        assert len(result.cases) == 2
        assert len(result.relationships) == 1
        rel = result.relationships[0]
        assert rel.relationship_type is RelationshipType.SEPARATE
        assert rel.merge_permitted is False
        assert len(rel.deterministic_reasons) > 0

    # ── 5. CONFLICT Verification ──────────────────────────────────────────────
    def test_conflict_relationship_preserves_both_and_escalates_review(self, engine):
        """Opposed directions on shared channel -> CONFLICT -> Mandates Human Review."""
        ev_high = _make_event(
            "EVT-H", "gyro_rate", subsystem="ADCS",
            directions=("HIGH",),
            candidate_fault_ids=("ADCS_OVER_RATE",),
        )
        ev_low = _make_event(
            "EVT-L", "gyro_rate", subsystem="ADCS",
            directions=("LOW",),
            candidate_fault_ids=("ADCS_UNDER_RATE",),
        )

        inp = ReconciliationInput(events=(ev_high, ev_low), scenario_id="CONFLICT-TEST")
        result = engine.reconcile(inp)

        assert len(result.cases) == 2
        assert result.human_review_required is True  # Invariant: Conflict escalates!
        rel = result.relationships[0]
        assert rel.relationship_type is RelationshipType.CONFLICT
        assert rel.merge_permitted is False
        assert any("Opposed directions" in r or "contradiction" in r.lower() for r in rel.deterministic_reasons)

    # ── 6. UNCERTAIN Verification ─────────────────────────────────────────────
    def test_uncertain_relationship_fails_safe_and_preserves_separation(self, engine):
        """Corrupted data or missing timestamps without propagation link -> UNCERTAIN -> Preserves separation."""
        ev_bad = _make_event(
            "EVT-BAD", "corrupted_channel", subsystem="UNKNOWN",
            first_seen_s=None, last_seen_s=None,
            defects=("MISSING_TIMESTAMP", "DATA_QUALITY_DEGRADED"),
            candidate_fault_ids=("UNKNOWN_FAULT_A",),
        )
        ev_norm = _make_event(
            "EVT-NORM", "temp_sensor", subsystem="THERMAL",
            first_seen_s=None, last_seen_s=None,
            defects=("MISSING_TIMESTAMP",),
            candidate_fault_ids=("UNKNOWN_FAULT_B",),
        )

        inp = ReconciliationInput(events=(ev_bad, ev_norm), scenario_id="UNCERTAIN-TEST")
        result = engine.reconcile(inp)

        assert len(result.cases) == 2
        assert result.human_review_required is True
        rel = result.relationships[0]
        assert rel.relationship_type in (RelationshipType.UNCERTAIN, RelationshipType.SEPARATE)
        assert rel.merge_permitted is False

    # ── 7. Non-Superficial Merge Invariant ────────────────────────────────────
    def test_similarity_score_or_channel_overlap_never_forces_merge(self, engine):
        """Two events sharing channel or subsystem NEVER merge without identity proof."""
        ev_a = _make_event(
            "EVT-1", "attitude_error", subsystem="AOCS",
            detectors=("HARD_LIMIT",), severity="CRITICAL", severity_rank=3, directions=("HIGH",),
            first_seen_s=-100.0, last_seen_s=-100.0,
            candidate_fault_ids=("RW_DRIFT",),
        )
        ev_b = _make_event(
            "EVT-2", "attitude_error", subsystem="AOCS",
            detectors=("STATISTICAL",), severity="WARNING", severity_rank=2, directions=("LOW",),
            first_seen_s=-200.0, last_seen_s=-200.0,  # Temporal separation > 15s
            candidate_fault_ids=("GYRO_DRIFT",),
        )

        inp = ReconciliationInput(events=(ev_a, ev_b), scenario_id="NO-FORCED-MERGE")
        result = engine.reconcile(inp)

        # Candidate faults differ, detector patterns differ -> Cases MUST remain separate
        assert len(result.cases) == 2
        assert result.relationships[0].merge_permitted is False
