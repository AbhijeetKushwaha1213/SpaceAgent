"""
SENTINEL — Hybrid Router Deterministic Merge Resolver (app/llm/merge_resolver.py)

Phase 23 Step 4.  The pure deterministic answer to:

    "Given an arbitration result, the participating branch outputs, and the
    deterministic pipeline context, what information may SAFELY survive into
    the authoritative LLMRankingOutput?"

Separation of concerns:
    - Arbitrator:    decides WHO WINS / WHETHER REVIEW IS REQUIRED.
    - MergeResolver: decides WHAT INFORMATION MAY SAFELY SURVIVE.

Merge Semantics (Phase 22 §9, Phase 23 Step 4):

    - HYPOTHESES:         WINNER-SOURCED; DETERMINISTIC FALLBACK on conflict.
    - CAUSAL CHAINS:      WINNER-SOURCED; never mixed across models.
    - REASONING SUMMARY:  WINNER-SOURCED; DETERMINISTIC TEMPLATE on conflict.
    - UNCERTAINTY:        WINNER-SOURCED + deterministic append on conflict/limits.
    - SUPPORTING EVIDENCE:VALIDATED INTERSECTION (both branches) / VALIDATED (single).
    - CONTRADICTING EVID: VALIDATED UNION (conservative retention of contradiction).
    - PROCEDURES:         ALLOWLISTED INTERSECTION (both branches) / ALLOWLISTED (single).
                          Empty intersection -> selected_procedure_ids = ().
                          NEVER union procedures.
    - CONFIDENCE:         NEVER averaged ((local+cloud)/2).
                          NEVER max(local, cloud).
                          Winner confidence in agreement/single-winner;
                          deterministic model-free in conflict/fallback;
                          0.0 in INSUFFICIENT evidence.
    - HUMAN REVIEW:       MONOTONE OR (combine_human_review); never True -> False.
    - PHYSICS/SAFETY:     NOT merged; re-validated downstream by authoritative engines.

The resolver is dormant in production while ROUTER_ENABLED=false.
"""

from __future__ import annotations

from typing import Any, Optional

from app.llm.arbitrator import ArbitrationResult
from app.llm.models import (
    EvidenceStatus,
    HypothesisContext,
    LLMRankingInput,
    LLMRankingOutput,
    RankedHypothesis,
)
from app.llm.router_contract import (
    Branch,
    BranchResult,
    combine_human_review,
)


class MergeResolver:
    """Pure deterministic merge resolver for cross-branch results.

    Assembles the final LLMRankingOutput by applying strict field-level merge
    rules. Never averages confidence, never unions procedures, never accepts
    unallowlisted evidence or procedures, and never inspects raw_text_head.
    """

    def resolve(
        self,
        arbitration: ArbitrationResult,
        local: Optional[BranchResult],
        cloud: Optional[BranchResult],
        ranking_input: LLMRankingInput,
        physics_report: Any = None,
    ) -> LLMRankingOutput:
        """Resolve and merge branch outputs into the authoritative LLMRankingOutput.

        Parameters:
            arbitration:    ArbitrationResult from the Arbitrator.
            local:          Local branch result (or None).
            cloud:          Cloud branch result (or None).
            ranking_input:  Deterministic context bundle.
            physics_report: Optional deterministic PhysicsValidationReport.
        """
        # 1. Deterministic valid universes
        valid_faults = frozenset(ranking_input.valid_fault_ids)
        valid_procs = frozenset(ranking_input.valid_procedure_ids)
        valid_evidence = self._collect_valid_evidence(ranking_input)
        invalidated_faults = self._collect_invalidated_faults(ranking_input, physics_report)

        insufficient = (
            getattr(ranking_input, "evidence_status", "")
            == EvidenceStatus.INSUFFICIENT.value
        )

        # Monotone human review baseline
        forced_review = combine_human_review(
            arbitration.human_review_required,
            local.human_review_required if local is not None else False,
            cloud.human_review_required if cloud is not None else False,
            insufficient,
            arbitration.disagreement,
        )

        # ── PATH A: INSUFFICIENT EVIDENCE (Rule P1) ───────────────────────────
        if insufficient:
            return LLMRankingOutput(
                ranked_hypotheses=(),
                reasoning_summary=(
                    "Telemetry evidence is INSUFFICIENT to establish a confident "
                    "fault diagnosis. No hypothesis or procedure is authorized. "
                    "Mandatory human operator review required."
                ),
                supporting_evidence_ids=(),
                contradicting_evidence_ids=(),
                selected_procedure_ids=(),
                uncertainty=(
                    "Evidence is insufficient: telemetry window lacks required "
                    "channels or fresh samples for state estimation and physics."
                ),
                requires_human_review=True,
            )

        # ── DETERMINE WINNING BRANCH AND PARTICIPATING OUTPUTS ────────────────
        winner_result: Optional[BranchResult] = None
        if arbitration.winning_branch is Branch.LOCAL and local is not None:
            winner_result = local
        elif arbitration.winning_branch is Branch.CLOUD and cloud is not None:
            winner_result = cloud

        local_output = local.validated_output if (local and local.is_usable) else None
        cloud_output = cloud.validated_output if (cloud and cloud.is_usable) else None
        both_participated = (local_output is not None) and (cloud_output is not None)

        # ── 2. EVIDENCE & PROCEDURE MERGE ────────────────────────────────────
        supporting_ids: tuple[str, ...]
        contradicting_ids: tuple[str, ...]
        selected_proc_ids: tuple[str, ...]

        # Contradicting evidence: VALIDATED UNION across any participating outputs
        contra_set: set[str] = set()
        if local_output is not None:
            contra_set.update(local_output.contradicting_evidence_ids)
        if cloud_output is not None:
            contra_set.update(cloud_output.contradicting_evidence_ids)
        contra_union = contra_set & valid_evidence
        seen_contra = set()
        ordered_contra = []
        for eid in (
            (list(local_output.contradicting_evidence_ids) if local_output else [])
            + (list(cloud_output.contradicting_evidence_ids) if cloud_output else [])
        ):
            if eid in contra_union and eid not in seen_contra:
                ordered_contra.append(eid)
                seen_contra.add(eid)
        contradicting_ids = tuple(ordered_contra)

        if winner_result is not None:
            if both_participated:
                assert local_output is not None and cloud_output is not None
                # Supporting Evidence: VALIDATED INTERSECTION
                local_supp = set(local_output.supporting_evidence_ids)
                cloud_supp = set(cloud_output.supporting_evidence_ids)
                supp_intersection = (local_supp & cloud_supp) & valid_evidence
                seen_supp = set()
                ordered_supp = []
                for eid in local_output.supporting_evidence_ids:
                    if eid in supp_intersection and eid not in seen_supp:
                        ordered_supp.append(eid)
                        seen_supp.add(eid)
                supporting_ids = tuple(ordered_supp)

                # Procedures: ALLOWLISTED INTERSECTION
                local_p = set(local_output.selected_procedure_ids)
                cloud_p = set(cloud_output.selected_procedure_ids)
                proc_intersection = (local_p & cloud_p) & valid_procs
                seen_proc = set()
                ordered_proc = []
                for pid in local_output.selected_procedure_ids:
                    if pid in proc_intersection and pid not in seen_proc:
                        ordered_proc.append(pid)
                        seen_proc.add(pid)
                selected_proc_ids = tuple(ordered_proc)

            else:
                assert winner_result.validated_output is not None
                w_out = winner_result.validated_output
                # Single-winner supporting: winner ∩ valid_evidence
                supporting_ids = tuple(
                    dict.fromkeys(
                        eid for eid in w_out.supporting_evidence_ids
                        if eid in valid_evidence
                    )
                )
                # Single-winner procedures: winner ∩ valid_procs
                selected_proc_ids = tuple(
                    dict.fromkeys(
                        pid for pid in w_out.selected_procedure_ids
                        if pid in valid_procs
                    )
                )
        else:
            # Conflict / both invalid / A5 / A6 / A10 -> empty procedures and supporting evidence
            supporting_ids = ()
            selected_proc_ids = ()

        # ── 4. HYPOTHESIS & CONFIDENCE MERGE ─────────────────────────────────
        ranked_hyps: tuple[RankedHypothesis, ...]
        summary: str
        uncertainty: str

        if winner_result is not None and winner_result.validated_output is not None:
            w_out = winner_result.validated_output
            # Filter and sanitize winner's hypotheses
            cleaned_hyps: list[RankedHypothesis] = []
            for h in w_out.ranked_hypotheses:
                if h.fault_id not in valid_faults:
                    continue
                is_invalid = h.fault_id in invalidated_faults
                conf = min(h.confidence, 0.3) if is_invalid else h.confidence
                justification = (
                    h.justification + " [DEMOTED: physics validation INVALID]"
                    if is_invalid else h.justification
                )
                cleaned_hyps.append(
                    RankedHypothesis(
                        fault_id=h.fault_id,
                        rank=h.rank,
                        confidence=conf,
                        justification=justification,
                        affected_component=h.affected_component,
                        causal_chain=tuple(h.causal_chain),
                    )
                )

            # Re-rank if physics-invalidated candidates are ranked ahead of non-invalid
            invalid_list = [h for h in cleaned_hyps if h.fault_id in invalidated_faults]
            non_invalid_list = [h for h in cleaned_hyps if h.fault_id not in invalidated_faults]
            if invalid_list and non_invalid_list:
                first_inv = min(invalid_list, key=lambda h: h.rank)
                first_non = min(non_invalid_list, key=lambda h: h.rank)
                if first_inv.rank <= first_non.rank:
                    reordered = sorted(non_invalid_list, key=lambda h: h.rank) + sorted(invalid_list, key=lambda h: h.rank)
                    cleaned_hyps = [
                        RankedHypothesis(
                            fault_id=h.fault_id,
                            rank=idx,
                            confidence=h.confidence,
                            justification=h.justification,
                            affected_component=h.affected_component,
                            causal_chain=h.causal_chain,
                        )
                        for idx, h in enumerate(reordered, start=1)
                    ]

            ranked_hyps = tuple(cleaned_hyps)
            summary = w_out.reasoning_summary

            # Append deterministic context note to uncertainty if branches disagreed
            uncertainty_text = w_out.uncertainty
            if arbitration.disagreement:
                disagree_note = (
                    f" [ROUTER: Local and Cloud branches disagreed; resolved "
                    f"deterministically via rule {arbitration.rule_applied} in "
                    f"favor of {arbitration.winning_branch.value if arbitration.winning_branch else 'none'}]"
                )
                uncertainty_text = (uncertainty_text + disagree_note).strip()
            uncertainty = uncertainty_text

            forced_review = combine_human_review(
                forced_review,
                w_out.requires_human_review,
            )

        else:
            # ── PATH B: CONFLICT / DETERMINISTIC FALLBACK (A5/A6/A10) ─────────
            # Build deterministic ranking from ranking_input hypotheses (non-invalidated only)
            valid_hyps = [
                h for h in ranking_input.hypotheses
                if h.fault_id in valid_faults and h.fault_id not in invalidated_faults
            ]
            # Order by deterministic score descending, then rank ascending
            valid_hyps.sort(key=lambda h: (-h.deterministic_score, h.deterministic_rank))

            fallback_ranked: list[RankedHypothesis] = []
            for rank_idx, h in enumerate(valid_hyps, start=1):
                # Deterministic model-free confidence: capped conservative value
                det_conf = max(0.0, min(0.40, round(h.deterministic_score, 2)))
                fallback_ranked.append(
                    RankedHypothesis(
                        fault_id=h.fault_id,
                        rank=rank_idx,
                        confidence=det_conf,
                        justification=(
                            f"Deterministic fallback diagnosis (score={h.deterministic_score:.2f}). "
                            f"Adopted following router cross-branch resolution."
                        ),
                        affected_component=h.subsystem,
                        causal_chain=tuple(h.causal_chain),
                    )
                )

            ranked_hyps = tuple(fallback_ranked)
            summary = (
                f"Cross-branch arbitration resulted in conflict/unresolved ambiguity "
                f"(rule={arbitration.rule_applied}, reasons={[r.value for r in arbitration.reasons]}). "
                f"Reverted to deterministic hypothesis ranking without model authority."
            )
            uncertainty = (
                f"Branches produced unresolvable disagreement or both failed. "
                f"Diagnostic claims are derived solely from deterministic pipeline stages."
            )
            forced_review = True

        return LLMRankingOutput(
            ranked_hypotheses=ranked_hyps,
            reasoning_summary=summary,
            supporting_evidence_ids=supporting_ids,
            contradicting_evidence_ids=contradicting_ids,
            selected_procedure_ids=selected_proc_ids,
            uncertainty=uncertainty,
            requires_human_review=forced_review,
        )

    # ----------------------------------------------------------------------
    # Helper methods (pure, deterministic extraction)
    # ----------------------------------------------------------------------

    @staticmethod
    def _collect_valid_evidence(ranking_input: LLMRankingInput) -> frozenset[str]:
        """Collect all evidence IDs present in the input hypotheses."""
        evidence_set: set[str] = set()
        for h in ranking_input.hypotheses:
            evidence_set.update(h.supporting_evidence)
            evidence_set.update(h.contradicting_evidence)
            evidence_set.update(h.undetermined_evidence)
        return frozenset(evidence_set)

    @staticmethod
    def _collect_invalidated_faults(
        ranking_input: LLMRankingInput, physics_report: Any = None
    ) -> frozenset[str]:
        """Collect all physics-invalidated fault IDs."""
        invalidated: set[str] = set(ranking_input.physics.invalidated)
        if physics_report is not None:
            invalidated.update(getattr(physics_report, "invalidated", []))
        return frozenset(invalidated)
