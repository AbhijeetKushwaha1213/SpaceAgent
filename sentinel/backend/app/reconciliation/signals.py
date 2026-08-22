"""
SENTINEL — Reconciliation Signals (app/reconciliation/signals.py)

Phase 24.  Deterministic reconciliation signal evaluators.

This module implements the 8 independent signal families (9 signal types)
evaluated for every pair of observation events:
  1. TEMPORAL_PROXIMITY
  2. SUBSYSTEM_RELATIONSHIP
  3. CHANNEL_RELATIONSHIP
  4. SIGNAL_PATTERN_SIMILARITY
  5. PHYSICAL_RELATIONSHIP
  6. HYPOTHESIS_COMPATIBILITY
  7. DUPLICATE_SIGNATURE
  8. CONTRADICTION_INDICATOR
  9. DATA_QUALITY

Invariants:
  - 100% deterministic Python.
  - Zero LLM invocation, zero embeddings, zero model text/confidence inspection.
  - Reuses existing repository physical propagation graph (app.diagnosis.propagation)
    and channel dictionary (app.ingest.channel_dict).
  - Handles missing/unparseable timestamps by recording defects (never coercing to 0.0).
  - Contradictions are explicitly flagged as CONTRADICTS rather than discarding evidence.
"""

from __future__ import annotations

from typing import Optional

from app.diagnosis.propagation import (
    get_edge,
    is_plausible_propagation,
)
from app.reconciliation.config import (
    DEFAULT_CONFIG,
    ReconciliationConfig,
)
from app.reconciliation.contract import (
    ObservationEvent,
    ReconciliationInput,
    ReconciliationSignal,
    SignalOutcome,
    SignalVerdict,
)
from app.reconciliation.events import (
    DIRECTION_UNKNOWN,
    OPPOSED_DIRECTIONS,
)


def evaluate_temporal_proximity(
    event_a: ObservationEvent,
    event_b: ObservationEvent,
    config: ReconciliationConfig = DEFAULT_CONFIG,
) -> SignalOutcome:
    """Evaluate temporal onset proximity between two observations."""
    if event_a.first_seen_s is None or event_b.first_seen_s is None:
        return SignalOutcome(
            signal=ReconciliationSignal.TEMPORAL_PROXIMITY,
            verdict=SignalVerdict.NOT_EVALUABLE,
            explanation="Missing or unparseable timestamp offset on one or both events.",
            threshold_name="temporal_same_case_window_s",
            threshold_used=config.temporal_same_case_window_s,
        )

    delta_s = abs(event_a.first_seen_s - event_b.first_seen_s)

    if delta_s <= config.temporal_same_case_window_s:
        return SignalOutcome(
            signal=ReconciliationSignal.TEMPORAL_PROXIMITY,
            verdict=SignalVerdict.SUPPORTS_IDENTITY,
            value=delta_s,
            threshold_name="temporal_same_case_window_s",
            threshold_used=config.temporal_same_case_window_s,
            explanation=(
                f"Onset delta {delta_s:.1f}s <= same-case window "
                f"{config.temporal_same_case_window_s:.1f}s."
            ),
        )
    elif delta_s <= config.temporal_related_window_s:
        return SignalOutcome(
            signal=ReconciliationSignal.TEMPORAL_PROXIMITY,
            verdict=SignalVerdict.SUPPORTS_RELATION,
            value=delta_s,
            threshold_name="temporal_related_window_s",
            threshold_used=config.temporal_related_window_s,
            explanation=(
                f"Onset delta {delta_s:.1f}s within related-case window "
                f"{config.temporal_related_window_s:.1f}s."
            ),
        )
    else:
        return SignalOutcome(
            signal=ReconciliationSignal.TEMPORAL_PROXIMITY,
            verdict=SignalVerdict.OPPOSES,
            value=delta_s,
            threshold_name="temporal_related_window_s",
            threshold_used=config.temporal_related_window_s,
            explanation=(
                f"Onset delta {delta_s:.1f}s exceeds related-case window "
                f"{config.temporal_related_window_s:.1f}s."
            ),
        )


def evaluate_subsystem_relationship(
    event_a: ObservationEvent,
    event_b: ObservationEvent,
    config: ReconciliationConfig = DEFAULT_CONFIG,
) -> SignalOutcome:
    """Evaluate whether subsystems coincide or connect via propagation."""
    if not event_a.has_known_subsystem or not event_b.has_known_subsystem:
        return SignalOutcome(
            signal=ReconciliationSignal.SUBSYSTEM_RELATIONSHIP,
            verdict=SignalVerdict.NOT_EVALUABLE,
            explanation="Subsystem unknown for one or both events.",
        )

    if event_a.subsystem == event_b.subsystem:
        return SignalOutcome(
            signal=ReconciliationSignal.SUBSYSTEM_RELATIONSHIP,
            verdict=SignalVerdict.SUPPORTS_IDENTITY,
            explanation=f"Both events belong to subsystem '{event_a.subsystem}'.",
        )

    if is_plausible_propagation(event_a.subsystem, event_b.subsystem) or is_plausible_propagation(
        event_b.subsystem, event_a.subsystem
    ):
        return SignalOutcome(
            signal=ReconciliationSignal.SUBSYSTEM_RELATIONSHIP,
            verdict=SignalVerdict.SUPPORTS_RELATION,
            explanation=(
                f"Subsystems '{event_a.subsystem}' and '{event_b.subsystem}' "
                f"are connected in the propagation graph."
            ),
        )

    return SignalOutcome(
        signal=ReconciliationSignal.SUBSYSTEM_RELATIONSHIP,
        verdict=SignalVerdict.NEUTRAL,
        explanation=(
            f"Subsystems '{event_a.subsystem}' and '{event_b.subsystem}' "
            f"are distinct without direct propagation edge."
        ),
    )


def evaluate_channel_relationship(
    event_a: ObservationEvent,
    event_b: ObservationEvent,
    config: ReconciliationConfig = DEFAULT_CONFIG,
) -> SignalOutcome:
    """Evaluate channel overlap and Jaccard index between observation events."""
    channels_a = {event_a.channel}
    channels_b = {event_b.channel}
    intersection = channels_a & channels_b
    union = channels_a | channels_b
    jaccard = len(intersection) / len(union) if union else 0.0

    if len(intersection) >= config.channel_shared_min_count:
        if jaccard >= config.channel_overlap_min_jaccard:
            return SignalOutcome(
                signal=ReconciliationSignal.CHANNEL_RELATIONSHIP,
                verdict=SignalVerdict.SUPPORTS_IDENTITY,
                value=jaccard,
                threshold_name="channel_overlap_min_jaccard",
                threshold_used=config.channel_overlap_min_jaccard,
                explanation=(
                    f"Shared channel '{event_a.channel}' (Jaccard: {jaccard:.2f} "
                    f">= {config.channel_overlap_min_jaccard:.2f})."
                ),
            )
        return SignalOutcome(
            signal=ReconciliationSignal.CHANNEL_RELATIONSHIP,
            verdict=SignalVerdict.SUPPORTS_RELATION,
            value=jaccard,
            threshold_name="channel_overlap_min_jaccard",
            threshold_used=config.channel_overlap_min_jaccard,
            explanation=f"Shared channel '{event_a.channel}' (Jaccard: {jaccard:.2f}).",
        )

    return SignalOutcome(
        signal=ReconciliationSignal.CHANNEL_RELATIONSHIP,
        verdict=SignalVerdict.NEUTRAL,
        value=jaccard,
        threshold_name="channel_overlap_min_jaccard",
        threshold_used=config.channel_overlap_min_jaccard,
        explanation=f"Distinct channels ('{event_a.channel}' vs '{event_b.channel}').",
    )


def evaluate_signal_pattern_similarity(
    event_a: ObservationEvent,
    event_b: ObservationEvent,
    config: ReconciliationConfig = DEFAULT_CONFIG,
) -> SignalOutcome:
    """Evaluate pattern similarity across detector sets, severity ranks, and directions."""
    if event_a.defects or event_b.defects or not event_a.detectors or not event_b.detectors:
        return SignalOutcome(
            signal=ReconciliationSignal.SIGNAL_PATTERN_SIMILARITY,
            verdict=SignalVerdict.NOT_EVALUABLE,
            threshold_name="pattern_similarity_min",
            threshold_used=config.pattern_similarity_min,
            explanation="Defective or missing detector metadata; pattern similarity not evaluable.",
        )

    set_det_a = set(event_a.detectors)
    set_det_b = set(event_b.detectors)
    union_det = set_det_a | set_det_b
    det_sim = len(set_det_a & set_det_b) / max(len(union_det), 1) if union_det else 0.0

    sev_diff = abs(event_a.severity_rank - event_b.severity_rank)
    sev_sim = max(0.0, 1.0 - (sev_diff / 4.0))

    set_dir_a = set(event_a.directions) - {DIRECTION_UNKNOWN}
    set_dir_b = set(event_b.directions) - {DIRECTION_UNKNOWN}
    if set_dir_a or set_dir_b:
        union_dir = set_dir_a | set_dir_b
        dir_sim = len(set_dir_a & set_dir_b) / max(len(union_dir), 1)
    else:
        dir_sim = 1.0

    pattern_similarity = 0.40 * det_sim + 0.30 * sev_sim + 0.30 * dir_sim

    if pattern_similarity >= config.pattern_similarity_min:
        return SignalOutcome(
            signal=ReconciliationSignal.SIGNAL_PATTERN_SIMILARITY,
            verdict=SignalVerdict.SUPPORTS_IDENTITY,
            value=pattern_similarity,
            threshold_name="pattern_similarity_min",
            threshold_used=config.pattern_similarity_min,
            explanation=(
                f"Signal pattern similarity {pattern_similarity:.2f} >= "
                f"{config.pattern_similarity_min:.2f}."
            ),
        )
    elif pattern_similarity >= 0.50:
        return SignalOutcome(
            signal=ReconciliationSignal.SIGNAL_PATTERN_SIMILARITY,
            verdict=SignalVerdict.NEUTRAL,
            value=pattern_similarity,
            threshold_name="pattern_similarity_min",
            threshold_used=config.pattern_similarity_min,
            explanation=f"Moderate signal pattern similarity {pattern_similarity:.2f}.",
        )
    else:
        return SignalOutcome(
            signal=ReconciliationSignal.SIGNAL_PATTERN_SIMILARITY,
            verdict=SignalVerdict.OPPOSES,
            value=pattern_similarity,
            threshold_name="pattern_similarity_min",
            threshold_used=config.pattern_similarity_min,
            explanation=(
                f"Low signal pattern similarity {pattern_similarity:.2f} < "
                f"{config.pattern_similarity_min:.2f}."
            ),
        )


def evaluate_physical_relationship(
    event_a: ObservationEvent,
    event_b: ObservationEvent,
    config: ReconciliationConfig = DEFAULT_CONFIG,
) -> SignalOutcome:
    """Evaluate deterministic physical propagation path between observation events."""
    if not event_a.has_known_subsystem or not event_b.has_known_subsystem:
        return SignalOutcome(
            signal=ReconciliationSignal.PHYSICAL_RELATIONSHIP,
            verdict=SignalVerdict.NOT_EVALUABLE,
            explanation="Subsystem unknown for physical propagation check.",
        )

    if event_a.subsystem == event_b.subsystem:
        return SignalOutcome(
            signal=ReconciliationSignal.PHYSICAL_RELATIONSHIP,
            verdict=SignalVerdict.SUPPORTS_IDENTITY,
            value=1.0,
            threshold_name="propagation_min_strength",
            threshold_used=config.propagation_min_strength,
            explanation=f"Intra-subsystem physical relationship within '{event_a.subsystem}'.",
        )

    edge_ab = get_edge(event_a.subsystem, event_b.subsystem)
    edge_ba = get_edge(event_b.subsystem, event_a.subsystem)
    active_edge = edge_ab if edge_ab is not None else edge_ba

    if active_edge is not None:
        strength = active_edge.strength
        if strength >= config.propagation_min_strength:
            # Check temporal ordering against propagation direction if timing exists
            if event_a.first_seen_s is not None and event_b.first_seen_s is not None:
                if edge_ab is not None and event_a.first_seen_s > event_b.first_seen_s + 60.0:
                    return SignalOutcome(
                        signal=ReconciliationSignal.PHYSICAL_RELATIONSHIP,
                        verdict=SignalVerdict.OPPOSES,
                        value=strength,
                        threshold_name="propagation_min_strength",
                        threshold_used=config.propagation_min_strength,
                        explanation=(
                            f"Propagation path {event_a.subsystem} -> {event_b.subsystem} "
                            f"opposed by temporal order (source at {event_a.first_seen_s:.1f}s "
                            f"after symptom at {event_b.first_seen_s:.1f}s)."
                        ),
                    )
                elif edge_ba is not None and event_b.first_seen_s > event_a.first_seen_s + 60.0:
                    return SignalOutcome(
                        signal=ReconciliationSignal.PHYSICAL_RELATIONSHIP,
                        verdict=SignalVerdict.OPPOSES,
                        value=strength,
                        threshold_name="propagation_min_strength",
                        threshold_used=config.propagation_min_strength,
                        explanation=(
                            f"Propagation path {event_b.subsystem} -> {event_a.subsystem} "
                            f"opposed by temporal order (source at {event_b.first_seen_s:.1f}s "
                            f"after symptom at {event_a.first_seen_s:.1f}s)."
                        ),
                    )

            return SignalOutcome(
                signal=ReconciliationSignal.PHYSICAL_RELATIONSHIP,
                verdict=SignalVerdict.SUPPORTS_RELATION,
                value=strength,
                threshold_name="propagation_min_strength",
                threshold_used=config.propagation_min_strength,
                explanation=(
                    f"Physical propagation declared: {active_edge.mechanism} "
                    f"(strength: {strength:.2f}, typical delay: {active_edge.typical_delay})."
                ),
            )
        else:
            return SignalOutcome(
                signal=ReconciliationSignal.PHYSICAL_RELATIONSHIP,
                verdict=SignalVerdict.NEUTRAL,
                value=strength,
                threshold_name="propagation_min_strength",
                threshold_used=config.propagation_min_strength,
                explanation=(
                    f"Weak propagation edge ({strength:.2f} < "
                    f"{config.propagation_min_strength:.2f})."
                ),
            )

    return SignalOutcome(
        signal=ReconciliationSignal.PHYSICAL_RELATIONSHIP,
        verdict=SignalVerdict.NEUTRAL,
        value=0.0,
        threshold_name="propagation_min_strength",
        threshold_used=config.propagation_min_strength,
        explanation=(
            f"No physical propagation edge declared between '{event_a.subsystem}' "
            f"and '{event_b.subsystem}'."
        ),
    )


def evaluate_hypothesis_compatibility(
    event_a: ObservationEvent,
    event_b: ObservationEvent,
    input_ctx: Optional[ReconciliationInput] = None,
    config: ReconciliationConfig = DEFAULT_CONFIG,
) -> SignalOutcome:
    """Evaluate candidate fault overlap and mutual exclusivity."""
    faults_a = set(event_a.candidate_fault_ids)
    faults_b = set(event_b.candidate_fault_ids)

    if input_ctx and input_ctx.mutually_exclusive_faults:
        for f_1, f_2 in input_ctx.mutually_exclusive_faults:
            if (f_1 in faults_a and f_2 in faults_b) or (f_2 in faults_a and f_1 in faults_b):
                return SignalOutcome(
                    signal=ReconciliationSignal.HYPOTHESIS_COMPATIBILITY,
                    verdict=SignalVerdict.CONTRADICTS,
                    explanation=(
                        f"Candidate faults '{f_1}' and '{f_2}' are declared mutually exclusive."
                    ),
                )

    if not faults_a or not faults_b:
        return SignalOutcome(
            signal=ReconciliationSignal.HYPOTHESIS_COMPATIBILITY,
            verdict=SignalVerdict.NEUTRAL,
            explanation="Candidate fault sets are unpopulated for one or both events.",
        )

    common = faults_a & faults_b
    if common:
        return SignalOutcome(
            signal=ReconciliationSignal.HYPOTHESIS_COMPATIBILITY,
            verdict=SignalVerdict.SUPPORTS_IDENTITY,
            explanation=f"Shared candidate fault hypothesis: {', '.join(sorted(common))}.",
        )

    return SignalOutcome(
        signal=ReconciliationSignal.HYPOTHESIS_COMPATIBILITY,
        verdict=SignalVerdict.NEUTRAL,
        explanation=(
            f"Distinct candidate fault sets ({', '.join(sorted(faults_a))} vs "
            f"{', '.join(sorted(faults_b))})."
        ),
    )


def evaluate_duplicate_signature(
    event_a: ObservationEvent,
    event_b: ObservationEvent,
    config: ReconciliationConfig = DEFAULT_CONFIG,
) -> SignalOutcome:
    """Evaluate exact equality of full deterministic signature."""
    sig_a = event_a.signature()
    sig_b = event_b.signature()

    if sig_a == sig_b:
        return SignalOutcome(
            signal=ReconciliationSignal.DUPLICATE_SIGNATURE,
            verdict=SignalVerdict.SUPPORTS_IDENTITY,
            value=1.0,
            explanation=(
                f"Exact match on deterministic signature: channel '{event_a.channel}', "
                f"detectors {event_a.detectors}, severity '{event_a.severity}'."
            ),
        )

    return SignalOutcome(
        signal=ReconciliationSignal.DUPLICATE_SIGNATURE,
        verdict=SignalVerdict.NEUTRAL,
        value=0.0,
        explanation="Deterministic signatures differ.",
    )


def evaluate_contradiction_indicator(
    event_a: ObservationEvent,
    event_b: ObservationEvent,
    input_ctx: Optional[ReconciliationInput] = None,
    config: ReconciliationConfig = DEFAULT_CONFIG,
) -> SignalOutcome:
    """Evaluate contradictory telemetry directions or opposing physics validation verdicts."""
    # Check 1: Shared channel with opposed directions
    if event_a.channel == event_b.channel:
        for dir_a in event_a.directions:
            for dir_b in event_b.directions:
                if frozenset({dir_a, dir_b}) in OPPOSED_DIRECTIONS:
                    return SignalOutcome(
                        signal=ReconciliationSignal.CONTRADICTION_INDICATOR,
                        verdict=SignalVerdict.CONTRADICTS,
                        explanation=(
                            f"Opposed directions on shared channel '{event_a.channel}': "
                            f"{dir_a} vs {dir_b}."
                        ),
                    )

    # Check 2: Physics validation conflict on mutually exclusive or identical candidate faults
    if input_ctx and input_ctx.physics_statuses:
        for f_a in event_a.candidate_fault_ids:
            for f_b in event_b.candidate_fault_ids:
                if f_a == f_b or (
                    input_ctx.mutually_exclusive_faults
                    and (
                        (f_a, f_b) in input_ctx.mutually_exclusive_faults
                        or (f_b, f_a) in input_ctx.mutually_exclusive_faults
                    )
                ):
                    st_a = input_ctx.physics_status_for(f_a)
                    st_b = input_ctx.physics_status_for(f_b)
                    if (st_a == "VALID" and st_b == "INVALID") or (
                        st_a == "INVALID" and st_b == "VALID"
                    ):
                        return SignalOutcome(
                            signal=ReconciliationSignal.CONTRADICTION_INDICATOR,
                            verdict=SignalVerdict.CONTRADICTS,
                            explanation=(
                                f"Physics validation contradiction: {f_a}={st_a} vs {f_b}={st_b}."
                            ),
                        )

    return SignalOutcome(
        signal=ReconciliationSignal.CONTRADICTION_INDICATOR,
        verdict=SignalVerdict.NEUTRAL,
        explanation="No contradictory telemetry directions or physics verdicts detected.",
    )


def evaluate_data_quality(
    event_a: ObservationEvent,
    event_b: ObservationEvent,
    config: ReconciliationConfig = DEFAULT_CONFIG,
) -> SignalOutcome:
    """Evaluate input data quality and report defects (never opposes)."""
    all_defects = tuple(event_a.defects) + tuple(event_b.defects)
    if all_defects:
        return SignalOutcome(
            signal=ReconciliationSignal.DATA_QUALITY,
            verdict=SignalVerdict.NOT_EVALUABLE,
            explanation=f"Input defects present: {'; '.join(all_defects)}.",
        )

    return SignalOutcome(
        signal=ReconciliationSignal.DATA_QUALITY,
        verdict=SignalVerdict.NEUTRAL,
        explanation=(
            "Input observations are well-formed with full timing, subsystem, and detector metadata."
        ),
    )


def evaluate_all_signals(
    event_a: ObservationEvent,
    event_b: ObservationEvent,
    input_ctx: Optional[ReconciliationInput] = None,
    config: ReconciliationConfig = DEFAULT_CONFIG,
) -> tuple[SignalOutcome, ...]:
    """Evaluate all 9 reconciliation signals for an observation pair in deterministic order."""
    return (
        evaluate_temporal_proximity(event_a, event_b, config),
        evaluate_subsystem_relationship(event_a, event_b, config),
        evaluate_channel_relationship(event_a, event_b, config),
        evaluate_signal_pattern_similarity(event_a, event_b, config),
        evaluate_physical_relationship(event_a, event_b, config),
        evaluate_hypothesis_compatibility(event_a, event_b, input_ctx, config),
        evaluate_duplicate_signature(event_a, event_b, config),
        evaluate_contradiction_indicator(event_a, event_b, input_ctx, config),
        evaluate_data_quality(event_a, event_b, config),
    )
