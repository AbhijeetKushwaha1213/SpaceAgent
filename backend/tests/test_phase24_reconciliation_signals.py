"""
Tests for Phase 24 Reconciliation Signals (tests/test_phase24_reconciliation_signals.py).

Verifies the 8 deterministic signal families:
  1. TEMPORAL_PROXIMITY (near, distant, equal, malformed, missing)
  2. SUBSYSTEM_RELATIONSHIP (same, propagation-connected, unrelated, unknown)
  3. CHANNEL_RELATIONSHIP (same, shared, distinct, Jaccard thresholds)
  4. SIGNAL_PATTERN_SIMILARITY (matching detectors/severity/directions, divergent)
  5. PHYSICAL_RELATIONSHIP (intra-subsystem, propagation edge, temporal opposition, no edge)
  6. HYPOTHESIS_COMPATIBILITY (shared candidate, mutual exclusion CONFLICT, distinct)
  7. DUPLICATE_SIGNATURE (exact match DUPLICATE, differing)
  8. CONTRADICTION_INDICATOR (opposed directions CONFLICT, physics verdict conflict)
  9. DATA_QUALITY (defects NOT_EVALUABLE, clean NEUTRAL)
"""

import pytest

from app.reconciliation.config import DEFAULT_CONFIG, ReconciliationConfig
from app.reconciliation.contract import (
    ObservationEvent,
    ReconciliationInput,
    ReconciliationSignal,
    SignalVerdict,
)
from app.reconciliation.signals import (
    evaluate_all_signals,
    evaluate_channel_relationship,
    evaluate_contradiction_indicator,
    evaluate_data_quality,
    evaluate_duplicate_signature,
    evaluate_hypothesis_compatibility,
    evaluate_physical_relationship,
    evaluate_signal_pattern_similarity,
    evaluate_subsystem_relationship,
    evaluate_temporal_proximity,
)


def _make_event(
    event_id: str = "EVT-1",
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
    corroborated: bool = True,
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
        corroborated=corroborated,
        scenario_id=scenario_id,
        source_ref="test",
        defects=defects,
    )


class TestTemporalProximitySignal:
    def test_near_events_support_identity(self):
        e1 = _make_event(first_seen_s=-100.0)
        e2 = _make_event(first_seen_s=-115.0)  # delta = 15s <= 30s
        outcome = evaluate_temporal_proximity(e1, e2)
        assert outcome.verdict == SignalVerdict.SUPPORTS_IDENTITY
        assert outcome.supports_identity is True
        assert outcome.value == 15.0

    def test_equal_timestamps_support_identity(self):
        e1 = _make_event(first_seen_s=-100.0)
        e2 = _make_event(first_seen_s=-100.0)
        outcome = evaluate_temporal_proximity(e1, e2)
        assert outcome.verdict == SignalVerdict.SUPPORTS_IDENTITY
        assert outcome.value == 0.0

    def test_related_window_supports_relation(self):
        e1 = _make_event(first_seen_s=-100.0)
        e2 = _make_event(first_seen_s=-200.0)  # delta = 100s (>30s, <=300s)
        outcome = evaluate_temporal_proximity(e1, e2)
        assert outcome.verdict == SignalVerdict.SUPPORTS_RELATION
        assert outcome.supports_relation is True
        assert outcome.value == 100.0

    def test_distant_events_oppose(self):
        e1 = _make_event(first_seen_s=-100.0)
        e2 = _make_event(first_seen_s=-500.0)  # delta = 400s > 300s
        outcome = evaluate_temporal_proximity(e1, e2)
        assert outcome.verdict == SignalVerdict.OPPOSES
        assert outcome.opposes is True

    def test_missing_timestamp_is_not_evaluable(self):
        e1 = _make_event(first_seen_s=None)
        e2 = _make_event(first_seen_s=-100.0)
        outcome = evaluate_temporal_proximity(e1, e2)
        assert outcome.verdict == SignalVerdict.NOT_EVALUABLE


class TestSubsystemRelationshipSignal:
    def test_same_subsystem_supports_identity(self):
        e1 = _make_event(subsystem="EPS")
        e2 = _make_event(subsystem="EPS")
        outcome = evaluate_subsystem_relationship(e1, e2)
        assert outcome.verdict == SignalVerdict.SUPPORTS_IDENTITY

    def test_propagation_connected_subsystems_support_relation(self):
        e1 = _make_event(subsystem="AOCS")
        e2 = _make_event(subsystem="EPS")  # AOCS -> EPS edge exists
        outcome = evaluate_subsystem_relationship(e1, e2)
        assert outcome.verdict == SignalVerdict.SUPPORTS_RELATION

    def test_unrelated_subsystems_neutral(self):
        e1 = _make_event(subsystem="COMMS")
        e2 = _make_event(subsystem="PYLD")
        outcome = evaluate_subsystem_relationship(e1, e2)
        assert outcome.verdict == SignalVerdict.NEUTRAL

    def test_unknown_subsystem_not_evaluable(self):
        e1 = _make_event(subsystem="UNKNOWN")
        e2 = _make_event(subsystem="EPS")
        outcome = evaluate_subsystem_relationship(e1, e2)
        assert outcome.verdict == SignalVerdict.NOT_EVALUABLE


class TestChannelRelationshipSignal:
    def test_same_channel_supports_identity(self):
        e1 = _make_event(channel="I_sa")
        e2 = _make_event(channel="I_sa")
        outcome = evaluate_channel_relationship(e1, e2)
        assert outcome.verdict == SignalVerdict.SUPPORTS_IDENTITY
        assert outcome.value == 1.0

    def test_distinct_channels_neutral(self):
        e1 = _make_event(channel="I_sa")
        e2 = _make_event(channel="V_bat")
        outcome = evaluate_channel_relationship(e1, e2)
        assert outcome.verdict == SignalVerdict.NEUTRAL
        assert outcome.value == 0.0


class TestSignalPatternSimilarity:
    def test_identical_patterns_support_identity(self):
        e1 = _make_event(
            detectors=("HARD_LIMIT", "STATISTICAL"),
            severity_rank=3,
            directions=("LOW",),
        )
        e2 = _make_event(
            detectors=("HARD_LIMIT", "STATISTICAL"),
            severity_rank=3,
            directions=("LOW",),
        )
        outcome = evaluate_signal_pattern_similarity(e1, e2)
        assert outcome.verdict == SignalVerdict.SUPPORTS_IDENTITY
        assert outcome.value == 1.0

    def test_divergent_patterns_oppose(self):
        e1 = _make_event(
            detectors=("HARD_LIMIT",),
            severity_rank=4,
            directions=("HIGH",),
        )
        e2 = _make_event(
            detectors=("CUSUM",),
            severity_rank=0,
            directions=("LOW",),
        )
        outcome = evaluate_signal_pattern_similarity(e1, e2)
        assert outcome.verdict == SignalVerdict.OPPOSES


class TestPhysicalRelationshipSignal:
    def test_intra_subsystem_supports_identity(self):
        e1 = _make_event(subsystem="EPS")
        e2 = _make_event(subsystem="EPS")
        outcome = evaluate_physical_relationship(e1, e2)
        assert outcome.verdict == SignalVerdict.SUPPORTS_IDENTITY

    def test_propagation_path_supports_relation(self):
        e1 = _make_event(subsystem="AOCS", first_seen_s=-120.0)
        e2 = _make_event(subsystem="EPS", first_seen_s=-100.0)  # AOCS precedes EPS
        outcome = evaluate_physical_relationship(e1, e2)
        assert outcome.verdict == SignalVerdict.SUPPORTS_RELATION
        assert "off-points the solar arrays" in outcome.explanation

    def test_temporal_inversion_opposes_propagation(self):
        e1 = _make_event(subsystem="AOCS", first_seen_s=-20.0)
        e2 = _make_event(subsystem="EPS", first_seen_s=-120.0)  # EPS happens 100s before AOCS
        outcome = evaluate_physical_relationship(e1, e2)
        assert outcome.verdict == SignalVerdict.OPPOSES


class TestHypothesisCompatibilitySignal:
    def test_shared_candidate_fault_supports_identity(self):
        e1 = _make_event(candidate_fault_ids=("EPS_SOLAR_UNDERVOLT", "MULTI_CASCADE"))
        e2 = _make_event(candidate_fault_ids=("EPS_SOLAR_UNDERVOLT", "EPS_BATTERY_DEGRADATION"))
        outcome = evaluate_hypothesis_compatibility(e1, e2)
        assert outcome.verdict == SignalVerdict.SUPPORTS_IDENTITY

    def test_mutually_exclusive_faults_contradict(self):
        e1 = _make_event(candidate_fault_ids=("FAULT_A",))
        e2 = _make_event(candidate_fault_ids=("FAULT_B",))
        inp = ReconciliationInput(
            events=(e1, e2),
            mutually_exclusive_faults=(("FAULT_A", "FAULT_B"),),
        )
        outcome = evaluate_hypothesis_compatibility(e1, e2, input_ctx=inp)
        assert outcome.verdict == SignalVerdict.CONTRADICTS
        assert outcome.contradicts is True


class TestDuplicateSignatureSignal:
    def test_exact_signature_supports_identity(self):
        e1 = _make_event(channel="V_bat", detectors=("HARD_LIMIT",), timestamps=("T-0s",), directions=("LOW",), severity="CRITICAL")
        e2 = _make_event(channel="V_bat", detectors=("HARD_LIMIT",), timestamps=("T-0s",), directions=("LOW",), severity="CRITICAL")
        outcome = evaluate_duplicate_signature(e1, e2)
        assert outcome.verdict == SignalVerdict.SUPPORTS_IDENTITY
        assert outcome.value == 1.0

    def test_differing_signature_neutral(self):
        e1 = _make_event(channel="V_bat", severity="CRITICAL")
        e2 = _make_event(channel="V_bat", severity="LOW")
        outcome = evaluate_duplicate_signature(e1, e2)
        assert outcome.verdict == SignalVerdict.NEUTRAL


class TestContradictionIndicatorSignal:
    def test_opposing_directions_on_same_channel_contradict(self):
        e1 = _make_event(channel="V_bat", directions=("HIGH",))
        e2 = _make_event(channel="V_bat", directions=("LOW",))
        outcome = evaluate_contradiction_indicator(e1, e2)
        assert outcome.verdict == SignalVerdict.CONTRADICTS
        assert outcome.contradicts is True

    def test_physics_validation_conflict_contradicts(self):
        e1 = _make_event(candidate_fault_ids=("EPS_SOLAR_UNDERVOLT",))
        e2 = _make_event(candidate_fault_ids=("EPS_SOLAR_UNDERVOLT",))
        inp = ReconciliationInput(
            events=(e1, e2),
            physics_statuses=(
                ("EPS_SOLAR_UNDERVOLT", "VALID"),
            ),
        )
        # Identical status is NOT a conflict
        outcome = evaluate_contradiction_indicator(e1, e2, input_ctx=inp)
        assert outcome.verdict == SignalVerdict.NEUTRAL

        # Different physics status across mutually exclusive faults
        e_alt = _make_event(candidate_fault_ids=("EPS_BATTERY_DEGRADATION",))
        inp_conflict = ReconciliationInput(
            events=(e1, e_alt),
            physics_statuses=(
                ("EPS_SOLAR_UNDERVOLT", "VALID"),
                ("EPS_BATTERY_DEGRADATION", "INVALID"),
            ),
            mutually_exclusive_faults=(
                ("EPS_SOLAR_UNDERVOLT", "EPS_BATTERY_DEGRADATION"),
            ),
        )
        outcome_conflict = evaluate_contradiction_indicator(e1, e_alt, input_ctx=inp_conflict)
        assert outcome_conflict.verdict == SignalVerdict.CONTRADICTS


class TestDataQualitySignal:
    def test_clean_input_neutral(self):
        e1 = _make_event(defects=())
        e2 = _make_event(defects=())
        outcome = evaluate_data_quality(e1, e2)
        assert outcome.verdict == SignalVerdict.NEUTRAL

    def test_defects_present_not_evaluable(self):
        e1 = _make_event(defects=("unparseable timestamp offset",))
        e2 = _make_event(defects=())
        outcome = evaluate_data_quality(e1, e2)
        assert outcome.verdict == SignalVerdict.NOT_EVALUABLE


class TestEvaluateAllSignals:
    def test_returns_all_nine_signals_in_deterministic_order(self):
        e1 = _make_event()
        e2 = _make_event()
        outcomes = evaluate_all_signals(e1, e2)
        assert len(outcomes) == 9
        expected_signals = (
            ReconciliationSignal.TEMPORAL_PROXIMITY,
            ReconciliationSignal.SUBSYSTEM_RELATIONSHIP,
            ReconciliationSignal.CHANNEL_RELATIONSHIP,
            ReconciliationSignal.SIGNAL_PATTERN_SIMILARITY,
            ReconciliationSignal.PHYSICAL_RELATIONSHIP,
            ReconciliationSignal.HYPOTHESIS_COMPATIBILITY,
            ReconciliationSignal.DUPLICATE_SIGNATURE,
            ReconciliationSignal.CONTRADICTION_INDICATOR,
            ReconciliationSignal.DATA_QUALITY,
        )
        assert tuple(o.signal for o in outcomes) == expected_signals
        for outcome in outcomes:
            d = outcome.as_dict()
            assert "signal" in d
            assert "verdict" in d
            assert "explanation" in d
