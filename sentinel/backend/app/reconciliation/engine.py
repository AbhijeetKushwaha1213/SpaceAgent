"""
SENTINEL — Reconciliation Engine (app/reconciliation/engine.py)

Phase 24.  Deterministic Reconciliation & Separation Engine.

Evaluates observation pairs against the 8 signal families, classifies relationships
using the priority decision ladder, clusters events into isolated Cases, and builds
inter-case relationship records.

Invariants:
  - 100% deterministic Python.
  - Zero LLM invocation, zero randomness, zero network I/O.
  - Prioritizes case separation under uncertainty: CORRELATION != IDENTITY.
  - Contradictions (CONFLICT) are preserved and never silently discarded.
"""

from __future__ import annotations

from typing import Optional

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
    make_relationship_id,
)
from app.reconciliation.signals import evaluate_all_signals


class ReconciliationEngine:
    """Deterministic separation engine producing isolated Cases and CaseRelationships."""

    def __init__(self, config: ReconciliationConfig = DEFAULT_CONFIG) -> None:
        self.config = config

    def reconcile(
        self,
        input_data: ReconciliationInput,
        config: Optional[ReconciliationConfig] = None,
    ) -> ReconciliationResult:
        """Execute deterministic reconciliation over input observation events."""
        cfg = config or self.config
        events = list(input_data.events)

        if not events:
            return ReconciliationResult(
                cases=(),
                relationships=(),
                event_assignments=(),
                config_version=RECONCILIATION_CONFIG_VERSION,
                engine_version=RECONCILIATION_ENGINE_VERSION,
                human_review_required=False,
                reasons=("No observation events to reconcile.",),
                warnings=(),
                merges_performed=(),
            )

        # ── Step 1: Pairwise signal evaluation and relationship classification ──
        pairwise_outcomes: dict[
            tuple[str, str], tuple[RelationshipType, tuple[SignalOutcome, ...], tuple[str, ...]]
        ] = {}

        n = len(events)
        for i in range(n):
            for j in range(i + 1, n):
                ev_a = events[i]
                ev_b = events[j]
                pair_key = (ev_a.event_id, ev_b.event_id)

                signals = evaluate_all_signals(ev_a, ev_b, input_data, cfg)
                rel_type, reasons = self._classify_pair(ev_a, ev_b, signals, cfg)
                pairwise_outcomes[pair_key] = (rel_type, signals, reasons)

        # ── Step 2: Cluster events into Cases (Union-Find with Conflict Guard) ──
        parent: dict[str, str] = {e.event_id: e.event_id for e in events}

        def find(x: str) -> str:
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]

        def union(x: str, y: str) -> bool:
            rx, ry = find(x), find(y)
            if rx == ry:
                return False
            parent[rx] = ry
            return True

        merges_performed: list[tuple[str, str]] = []

        # Attempt to merge merge_permitted pairs
        for i in range(n):
            for j in range(i + 1, n):
                ev_a = events[i]
                ev_b = events[j]
                pair_key = (ev_a.event_id, ev_b.event_id)
                rel_type, _, _ = pairwise_outcomes[pair_key]

                if rel_type.merge_permitted:
                    root_a = find(ev_a.event_id)
                    root_b = find(ev_b.event_id)
                    if root_a != root_b:
                        # Check that no pair across clusters has a CONFLICT
                        members_a = [e.event_id for e in events if find(e.event_id) == root_a]
                        members_b = [e.event_id for e in events if find(e.event_id) == root_b]

                        has_conflict = False
                        for m_a in members_a:
                            for m_b in members_b:
                                key = tuple(sorted([m_a, m_b]))
                                if key in pairwise_outcomes and pairwise_outcomes[key][0] is RelationshipType.CONFLICT:
                                    has_conflict = True
                                    break
                            if has_conflict:
                                break

                        if not has_conflict:
                            union(ev_a.event_id, ev_b.event_id)
                            merges_performed.append((ev_a.event_id, ev_b.event_id))

        # Group events by cluster root
        clusters: dict[str, list[ObservationEvent]] = {}
        for e in events:
            root = find(e.event_id)
            clusters.setdefault(root, []).append(e)

        # ── Step 3: Build Case objects ──
        built_cases: list[Case] = []
        for cluster_events in clusters.values():
            reasons = []
            if len(cluster_events) > 1:
                reasons.append(
                    f"Merged {len(cluster_events)} corroborated observation events."
                )
            else:
                reasons.append("Single observation event case.")

            built_case = build_case_from_events(
                events=cluster_events,
                scenario_id=input_data.scenario_id,
                config_version=RECONCILIATION_CONFIG_VERSION,
                engine_version=RECONCILIATION_ENGINE_VERSION,
                reasons=tuple(reasons),
            )
            built_cases.append(built_case)

        # Sort cases deterministically: larger event count first, then case_id
        built_cases.sort(key=lambda c: (-len(c.event_ids), c.case_id))
        cases_tuple = tuple(built_cases)

        # Map event_id -> case_id
        event_to_case_id: dict[str, str] = {}
        for case in cases_tuple:
            for ev_id in case.event_ids:
                event_to_case_id[ev_id] = case.case_id

        event_assignments = tuple(
            sorted((e.event_id, event_to_case_id[e.event_id]) for e in events)
        )

        # ── Step 4: Build inter-case CaseRelationship records ──
        relationships: list[CaseRelationship] = []
        num_cases = len(cases_tuple)

        for i in range(num_cases):
            for j in range(i + 1, num_cases):
                case_a = cases_tuple[i]
                case_b = cases_tuple[j]

                # Aggregate signals and pair relationships across member events
                all_case_signals: list[SignalOutcome] = []
                case_rel_types: list[RelationshipType] = []
                all_reasons: list[str] = []
                evidence_refs: list[str] = []
                prop_source: Optional[str] = None

                for ev_a_id in case_a.event_ids:
                    for ev_b_id in case_b.event_ids:
                        low_id, high_id = sorted([ev_a_id, ev_b_id])
                        pair_k = (low_id, high_id)
                        if pair_k in pairwise_outcomes:
                            r_type, sigs, r_reasons = pairwise_outcomes[pair_k]
                            case_rel_types.append(r_type)
                            all_case_signals.extend(sigs)
                            all_reasons.extend(r_reasons)

                # Determine aggregated case-level relationship type
                if RelationshipType.CONFLICT in case_rel_types:
                    aggregated_type = RelationshipType.CONFLICT
                elif RelationshipType.RELATED in case_rel_types:
                    aggregated_type = RelationshipType.RELATED
                elif all(rt is RelationshipType.SEPARATE for rt in case_rel_types):
                    aggregated_type = RelationshipType.SEPARATE
                else:
                    aggregated_type = RelationshipType.UNCERTAIN

                # Check propagation direction
                from app.diagnosis.propagation import get_edge

                edge_ab = None
                edge_ba = None
                for s_a in case_a.subsystems:
                    for s_b in case_b.subsystems:
                        e1 = get_edge(s_a, s_b)
                        if e1 and not edge_ab:
                            edge_ab = e1
                        e2 = get_edge(s_b, s_a)
                        if e2 and not edge_ba:
                            edge_ba = e2

                if edge_ab and edge_ba:
                    # Both directions possible; resolve by onset timing
                    if (
                        case_a.window_start_s is not None
                        and case_b.window_start_s is not None
                    ):
                        if case_a.window_start_s <= case_b.window_start_s:
                            prop_source = case_a.case_id
                        else:
                            prop_source = case_b.case_id
                    else:
                        prop_source = case_a.case_id if edge_ab.strength >= edge_ba.strength else case_b.case_id
                elif edge_ab:
                    prop_source = case_a.case_id
                elif edge_ba:
                    prop_source = case_b.case_id

                # Deduplicate signals and reasons
                unique_signals = tuple(all_case_signals[:9]) if all_case_signals else ()
                evaluable_count = sum(1 for s in unique_signals if s.verdict is not SignalVerdict.NOT_EVALUABLE)
                supporting_count = sum(1 for s in unique_signals if s.supports_relation)
                confidence = round(supporting_count / evaluable_count, 3) if evaluable_count > 0 else 0.0

                # Window bounds
                start_w = min((c.window_start_s for c in [case_a, case_b] if c.window_start_s is not None), default=None)
                end_w = max((c.window_end_s for c in [case_a, case_b] if c.window_end_s is not None), default=start_w)

                low_c, high_c = sorted([case_a.case_id, case_b.case_id])
                rel_id = make_relationship_id(low_c, high_c, aggregated_type)

                # Collect physics support references
                physics_refs = tuple(
                    f"{fid}:{input_data.physics_status_for(fid)}"
                    for fid, _ in input_data.physics_statuses
                )

                relationships.append(
                    CaseRelationship(
                        relationship_id=rel_id,
                        source_case_id=low_c,
                        target_case_id=high_c,
                        relationship_type=aggregated_type,
                        deterministic_reasons=tuple(sorted(set(all_reasons))),
                        signals=unique_signals,
                        config_version=RECONCILIATION_CONFIG_VERSION,
                        engine_version=RECONCILIATION_ENGINE_VERSION,
                        event_window_s=(start_w, end_w),
                        physics_support=physics_refs,
                        evidence_references=(),
                        source_event_ids=case_a.event_ids if case_a.case_id == low_c else case_b.event_ids,
                        target_event_ids=case_b.event_ids if case_a.case_id == low_c else case_a.event_ids,
                        propagation_source_case_id=prop_source,
                        confidence=confidence,
                    )
                )

        # ── Step 5: Evaluate Human Review Monotonicity ──
        human_review_required = False
        warnings: list[str] = []

        # Check for any conflict, defect, or unresolved relationship
        for r in relationships:
            if r.relationship_type in (RelationshipType.CONFLICT, RelationshipType.UNCERTAIN):
                human_review_required = True
                warnings.append(
                    f"Unresolved relationship {r.relationship_id} between {r.source_case_id} "
                    f"and {r.target_case_id} ({r.relationship_type.value})."
                )

        for c in cases_tuple:
            if c.defects:
                human_review_required = True
                warnings.append(f"Case {c.case_id} contains input defects: {'; '.join(c.defects)}")

        return ReconciliationResult(
            cases=cases_tuple,
            relationships=tuple(relationships),
            event_assignments=event_assignments,
            config_version=RECONCILIATION_CONFIG_VERSION,
            engine_version=RECONCILIATION_ENGINE_VERSION,
            human_review_required=human_review_required,
            reasons=(f"Reconciled {len(events)} observation events into {len(cases_tuple)} case(s).",),
            warnings=tuple(warnings),
            merges_performed=tuple(merges_performed),
        )

    def _classify_pair(
        self,
        event_a: ObservationEvent,
        event_b: ObservationEvent,
        signals: tuple[SignalOutcome, ...],
        config: ReconciliationConfig,
    ) -> tuple[RelationshipType, tuple[str, ...]]:
        """Classify pair relationship using the priority decision ladder."""
        sig_map = {s.signal: s for s in signals}
        reasons: list[str] = []

        # Rule 1: Duplicate detection (exact signature match)
        dup_sig = sig_map.get(ReconciliationSignal.DUPLICATE_SIGNATURE)
        if dup_sig and dup_sig.supports_identity:
            reasons.append(dup_sig.explanation)
            return (RelationshipType.DUPLICATE, tuple(reasons))

        # Rule 2: Contradiction detection (opposing channels/directions or physics conflict)
        contra_sig = sig_map.get(ReconciliationSignal.CONTRADICTION_INDICATOR)
        if contra_sig and contra_sig.contradicts:
            reasons.append(contra_sig.explanation)
            return (RelationshipType.CONFLICT, tuple(reasons))

        hypo_sig = sig_map.get(ReconciliationSignal.HYPOTHESIS_COMPATIBILITY)
        if hypo_sig and hypo_sig.contradicts:
            reasons.append(hypo_sig.explanation)
            return (RelationshipType.CONFLICT, tuple(reasons))

        # Rule 3: Same Case (corroboration across independent signal families)
        supporting_identity_signals = [
            s for s in signals if s.supports_identity
        ]
        supporting_count = len(supporting_identity_signals)

        phys_sig = sig_map.get(ReconciliationSignal.PHYSICAL_RELATIONSHIP)
        phys_opposes = phys_sig.opposes if phys_sig else False

        if supporting_count >= config.identity_min_supporting_signals and not (
            config.require_physics_non_opposition and phys_opposes
        ):
            sig_names = ", ".join(s.signal.value for s in supporting_identity_signals)
            reasons.append(
                f"Corroborated across {supporting_count} independent signals ({sig_names})."
            )
            return (RelationshipType.SAME_CASE, tuple(reasons))

        # Rule 4: Related Case (physical propagation or subsystem connectivity)
        if phys_sig and phys_sig.supports_relation:
            reasons.append(phys_sig.explanation)
            return (RelationshipType.RELATED, tuple(reasons))

        subsys_sig = sig_map.get(ReconciliationSignal.SUBSYSTEM_RELATIONSHIP)
        if subsys_sig and subsys_sig.supports_relation:
            reasons.append(subsys_sig.explanation)
            return (RelationshipType.RELATED, tuple(reasons))

        temp_sig = sig_map.get(ReconciliationSignal.TEMPORAL_PROXIMITY)
        if temp_sig and temp_sig.supports_relation and not phys_opposes:
            reasons.append(temp_sig.explanation)
            return (RelationshipType.RELATED, tuple(reasons))

        # Rule 5: Separate Case (temporal separation or opposing pattern with no physical link)
        data_sig = sig_map.get(ReconciliationSignal.DATA_QUALITY)
        if data_sig and data_sig.verdict is SignalVerdict.NOT_EVALUABLE:
            reasons.append(data_sig.explanation)
            return (RelationshipType.UNCERTAIN, tuple(reasons))

        if temp_sig and temp_sig.opposes:
            reasons.append(temp_sig.explanation)
            return (RelationshipType.SEPARATE, tuple(reasons))

        pat_sig = sig_map.get(ReconciliationSignal.SIGNAL_PATTERN_SIMILARITY)
        if pat_sig and pat_sig.opposes and (not phys_sig or not phys_sig.supports_relation):
            reasons.append(pat_sig.explanation)
            return (RelationshipType.SEPARATE, tuple(reasons))

        # Rule 6: Uncertain (Default fallback — preserves case separation)
        reasons.append("Insufficient evidence to establish identity or physical propagation.")
        return (RelationshipType.UNCERTAIN, tuple(reasons))
