"""
SENTINEL — Hybrid Router Deterministic Arbitrator (app/llm/arbitrator.py)

Phase 23 Step 4.  The pure deterministic answer to:

    "Given two reasoning branch results (local and cloud) and the deterministic
    pipeline context, which branch (if any) wins, what routing decision is
    reached, and is human review mandatory?"

Authority boundaries (Phase 22 rules 1-13, Phase 23 Step 4):

    1. Pure deterministic logic: no LLM calls, no network, no database, no
       filesystem mutation, no randomness, no timestamps.
    2. Trust boundary: raw model text (``raw_text_head``) is UNTRUSTED and
       strictly ignored.  Arbitration inspects ONLY ``validated_output`` and
       deterministic context.
    3. Strict precedence:
           PHYSICS VERDICTS
           > EVIDENCE CONTRACT
           > GUARDRAIL VALIDITY
           > DETERMINISTIC DISCRIMINATORS
           > AGREEMENT TIE-BREAK
           > UNRESOLVABLE DISAGREEMENT -> HUMAN REVIEW
    4. Confidence is NEVER an authority signal.  Model confidence cannot
       override physics, evidence, guardrails, or deterministic discriminators.
    5. Monotone human review: ``human_review_required`` can only be OR-accumulated
       via ``combine_human_review()``; no model opinion may clear it.

The arbitrator is dormant in production while ROUTER_ENABLED=false.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.llm.models import (
    EvidenceStatus,
    LLMRankingInput,
    RankedHypothesis,
)
from app.llm.router_contract import (
    Branch,
    BranchOutcome,
    BranchResult,
    RoutingDecision,
    RoutingReason,
    combine_human_review,
)


@dataclass(frozen=True)
class ArbitrationResult:
    """Immutable result of cross-branch deterministic arbitration.

    Fields:
        decision:              Final routing decision (RoutingDecision).
        reasons:               Tuple of deterministic reason codes (RoutingReason).
        winning_branch:        Branch.LOCAL, Branch.CLOUD, or None when neither wins.
        human_review_required: Monotone OR-combined human review requirement.
        disagreement:          True if both branches ran validly and differed in top hypothesis.
        rule_applied:          Rule identifier (e.g. A1, A2_PHYSICS, A2_SCORE, A3, A4, A5, A6, A10, P1).
    """
    decision: RoutingDecision
    reasons: tuple[RoutingReason, ...]
    winning_branch: Optional[Branch] = None
    human_review_required: bool = False
    disagreement: bool = False
    rule_applied: str = ""


class Arbitrator:
    """Pure deterministic cross-branch arbitrator (Phase 22 §8, Phase 23 Step 4).

    Evaluates two BranchResult objects against deterministic physics, evidence,
    guardrails, and deterministic discriminators. Never inspects model confidence
    as an authority metric, never inspects raw_text_head, and never mutates
    deterministic verdicts.
    """

    def arbitrate(
        self,
        local: Optional[BranchResult],
        cloud: Optional[BranchResult],
        ranking_input: LLMRankingInput,
        physics_report: Any = None,
        review_already_required: bool = False,
    ) -> ArbitrationResult:
        """Arbitrate between local and cloud branch results deterministically.

        Parameters:
            local:                   Result of local branch execution (or None).
            cloud:                   Result of cloud branch execution (or None).
            ranking_input:           Deterministic context bundle (hypotheses, physics, evidence).
            physics_report:          Optional deterministic PhysicsValidationReport.
            review_already_required: Upstream review flag (monotonically preserved).
        """
        # Baseline human-review requirement (monotone OR across all inputs)
        base_review = combine_human_review(
            review_already_required,
            local.human_review_required if local is not None else False,
            cloud.human_review_required if cloud is not None else False,
        )

        # ── GATE 0: PROVIDER AVAILABILITY / EXECUTION STATUS ──────────────────
        local_ran = local is not None and local.outcome != BranchOutcome.NOT_RUN
        cloud_ran = cloud is not None and cloud.outcome != BranchOutcome.NOT_RUN

        if not local_ran and not cloud_ran:
            return ArbitrationResult(
                decision=RoutingDecision.NO_INFERENCE,
                reasons=(RoutingReason.BOTH_UNAVAILABLE,),
                winning_branch=None,
                human_review_required=True,
                disagreement=False,
                rule_applied="A0_NONE_RUN",
            )

        # ── PRECEDENCE 2 (Rule P1): EVIDENCE CONTRACT ─────────────────────────
        # If deterministic evidence is INSUFFICIENT, no model may claim a
        # confident diagnosis. Enforce terminal HUMAN_REVIEW.
        evidence_status = getattr(
            ranking_input, "evidence_status", EvidenceStatus.INSUFFICIENT.value
        )
        if evidence_status == EvidenceStatus.INSUFFICIENT.value:
            return ArbitrationResult(
                decision=RoutingDecision.HUMAN_REVIEW,
                reasons=(RoutingReason.INSUFFICIENT_EVIDENCE,),
                winning_branch=None,
                human_review_required=True,
                disagreement=False,
                rule_applied="P1_INSUFFICIENT_EVIDENCE",
            )

        # Usability check: outcome must be ACCEPT with non-None validated_output
        local_valid = (
            local is not None
            and local.is_usable
            and local.validated_output is not None
        )
        cloud_valid = (
            cloud is not None
            and cloud.is_usable
            and cloud.validated_output is not None
        )

        # ── PRECEDENCE 3 (Rules P2, A5): GUARDRAIL & EXECUTION VALIDITY ───────
        if not local_valid and not cloud_valid:
            # Both failed, timed out, were unavailable, or violated guardrails
            both_unavailable = (
                local is not None and local.outcome == BranchOutcome.FAILURE
                and cloud is not None and cloud.outcome == BranchOutcome.FAILURE
                and any(
                    r in (RoutingReason.LOCAL_UNAVAILABLE, RoutingReason.LOCAL_TIMEOUT)
                    for r in local.reason_codes
                )
                and any(
                    r in (RoutingReason.CLOUD_UNAVAILABLE, RoutingReason.CLOUD_TIMEOUT)
                    for r in cloud.reason_codes
                )
            )
            reason = (
                RoutingReason.BOTH_UNAVAILABLE
                if both_unavailable
                else RoutingReason.BOTH_INVALID
            )
            return ArbitrationResult(
                decision=RoutingDecision.HUMAN_REVIEW,
                reasons=(reason,),
                winning_branch=None,
                human_review_required=True,
                disagreement=False,
                rule_applied="A5_BOTH_INVALID",
            )

        # Case A3: Local valid, Cloud invalid / failed / not-run
        if local_valid and not cloud_valid:
            assert local is not None and local.validated_output is not None
            # Check if local winner is physics-invalidated
            local_top = self._get_top_fault_id(local.validated_output)
            invalidated = self._get_invalidated_faults(ranking_input, physics_report)
            if local_top in invalidated:
                return ArbitrationResult(
                    decision=RoutingDecision.HUMAN_REVIEW,
                    reasons=(RoutingReason.PHYSICS_CONFLICT,),
                    winning_branch=None,
                    human_review_required=True,
                    disagreement=False,
                    rule_applied="A6_LOCAL_INVALID_PHYSICS",
                )
            forced_review = combine_human_review(
                base_review,
                local.validated_output.requires_human_review,
            )
            decision = (
                RoutingDecision.HUMAN_REVIEW
                if forced_review
                else RoutingDecision.LOCAL_ACCEPT
            )
            return ArbitrationResult(
                decision=decision,
                reasons=(RoutingReason.VALID_LOCAL_RESULT,),
                winning_branch=Branch.LOCAL,
                human_review_required=forced_review,
                disagreement=False,
                rule_applied="A3_LOCAL_VALID_CLOUD_INVALID",
            )

        # Case A4: Cloud valid, Local invalid / failed / not-run
        if not local_valid and cloud_valid:
            assert cloud is not None and cloud.validated_output is not None
            # Check if cloud winner is physics-invalidated
            cloud_top = self._get_top_fault_id(cloud.validated_output)
            invalidated = self._get_invalidated_faults(ranking_input, physics_report)
            if cloud_top in invalidated:
                return ArbitrationResult(
                    decision=RoutingDecision.HUMAN_REVIEW,
                    reasons=(RoutingReason.PHYSICS_CONFLICT,),
                    winning_branch=None,
                    human_review_required=True,
                    disagreement=False,
                    rule_applied="A6_CLOUD_INVALID_PHYSICS",
                )
            forced_review = combine_human_review(
                base_review,
                cloud.validated_output.requires_human_review,
            )
            decision = (
                RoutingDecision.HUMAN_REVIEW
                if forced_review
                else RoutingDecision.CLOUD_ACCEPT
            )
            reasons = (
                (RoutingReason.LOCAL_ESCALATION, RoutingReason.VALID_CLOUD_RESULT)
                if local is not None and local.outcome != BranchOutcome.NOT_RUN
                else (RoutingReason.VALID_CLOUD_RESULT,)
            )
            return ArbitrationResult(
                decision=decision,
                reasons=reasons,
                winning_branch=Branch.CLOUD,
                human_review_required=forced_review,
                disagreement=False,
                rule_applied="A4_LOCAL_INVALID_CLOUD_VALID",
            )

        # ── BOTH BRANCHES ARE VALID ───────────────────────────────────────────
        assert local is not None and local.validated_output is not None
        assert cloud is not None and cloud.validated_output is not None

        local_top = self._get_top_fault_id(local.validated_output)
        cloud_top = self._get_top_fault_id(cloud.validated_output)
        invalidated = self._get_invalidated_faults(ranking_input, physics_report)

        # ── PRECEDENCE 1 (Rule A6): PHYSICS VERDICTS ON TOP HYPOTHESES ────────
        local_top_invalid = local_top in invalidated
        cloud_top_invalid = cloud_top in invalidated

        if local_top_invalid and cloud_top_invalid:
            # Both models ranked an INVALIDATED hypothesis #1 -> discard both top claims
            return ArbitrationResult(
                decision=RoutingDecision.HUMAN_REVIEW,
                reasons=(RoutingReason.PHYSICS_CONFLICT,),
                winning_branch=None,
                human_review_required=True,
                disagreement=True,
                rule_applied="A6_BOTH_PHYSICS_INVALID",
            )

        if local_top_invalid and not cloud_top_invalid:
            # Local invalidated by physics; cloud is not -> Cloud wins by physics authority
            return ArbitrationResult(
                decision=RoutingDecision.CLOUD_ACCEPT,
                reasons=(RoutingReason.PHYSICS_CONFLICT, RoutingReason.VALID_CLOUD_RESULT),
                winning_branch=Branch.CLOUD,
                human_review_required=True,
                disagreement=True,
                rule_applied="A6_PHYSICS_FAVORS_CLOUD",
            )

        if not local_top_invalid and cloud_top_invalid:
            # Cloud invalidated by physics; local is not -> Local wins by physics authority
            return ArbitrationResult(
                decision=RoutingDecision.LOCAL_ACCEPT,
                reasons=(RoutingReason.PHYSICS_CONFLICT, RoutingReason.VALID_LOCAL_RESULT),
                winning_branch=Branch.LOCAL,
                human_review_required=True,
                disagreement=True,
                rule_applied="A6_PHYSICS_FAVORS_LOCAL",
            )

        # ── PRECEDENCE 5 (Rule A1): AGREEMENT TIE-BREAK ───────────────────────
        if local_top == cloud_top:
            # Both models independently agree on the top hypothesis
            forced_review = combine_human_review(
                base_review,
                local.validated_output.requires_human_review,
                cloud.validated_output.requires_human_review,
            )
            decision = (
                RoutingDecision.HUMAN_REVIEW
                if forced_review
                else RoutingDecision.LOCAL_ACCEPT
            )
            return ArbitrationResult(
                decision=decision,
                reasons=(RoutingReason.BRANCH_AGREEMENT,),
                winning_branch=Branch.LOCAL,
                human_review_required=forced_review,
                disagreement=False,
                rule_applied="A1_AGREEMENT",
            )

        # ── PRECEDENCE 4 & 6 (Rules A2, A10): DISAGREEMENT & DISCRIMINATORS ────
        # Top hypotheses differ and neither is physics-invalidated.
        # Disagreement always forces human review (monotone).
        forced_review = True

        hyp_map = {h.fault_id: h for h in ranking_input.hypotheses}
        local_hyp = hyp_map.get(local_top)
        cloud_hyp = hyp_map.get(cloud_top)

        validated_set = self._get_validated_faults(ranking_input, physics_report)

        # Discriminator 1: Physics VALIDATED status
        local_validated = local_top in validated_set
        cloud_validated = cloud_top in validated_set
        if local_validated and not cloud_validated:
            return ArbitrationResult(
                decision=RoutingDecision.LOCAL_ACCEPT,
                reasons=(RoutingReason.MODEL_DISAGREEMENT, RoutingReason.VALID_LOCAL_RESULT),
                winning_branch=Branch.LOCAL,
                human_review_required=forced_review,
                disagreement=True,
                rule_applied="A2_DISCRIMINATOR_PHYSICS",
            )
        if cloud_validated and not local_validated:
            return ArbitrationResult(
                decision=RoutingDecision.CLOUD_ACCEPT,
                reasons=(RoutingReason.MODEL_DISAGREEMENT, RoutingReason.VALID_CLOUD_RESULT),
                winning_branch=Branch.CLOUD,
                human_review_required=forced_review,
                disagreement=True,
                rule_applied="A2_DISCRIMINATOR_PHYSICS",
            )

        # Discriminator 2: Deterministic hypothesis score
        local_score = local_hyp.deterministic_score if local_hyp else 0.0
        cloud_score = cloud_hyp.deterministic_score if cloud_hyp else 0.0
        if local_score > cloud_score:
            return ArbitrationResult(
                decision=RoutingDecision.LOCAL_ACCEPT,
                reasons=(RoutingReason.MODEL_DISAGREEMENT, RoutingReason.VALID_LOCAL_RESULT),
                winning_branch=Branch.LOCAL,
                human_review_required=forced_review,
                disagreement=True,
                rule_applied="A2_DISCRIMINATOR_SCORE",
            )
        if cloud_score > local_score:
            return ArbitrationResult(
                decision=RoutingDecision.CLOUD_ACCEPT,
                reasons=(RoutingReason.MODEL_DISAGREEMENT, RoutingReason.VALID_CLOUD_RESULT),
                winning_branch=Branch.CLOUD,
                human_review_required=forced_review,
                disagreement=True,
                rule_applied="A2_DISCRIMINATOR_SCORE",
            )

        # Discriminator 3: Deterministic supporting evidence count
        local_supp = len(local_hyp.supporting_evidence) if local_hyp else 0
        cloud_supp = len(cloud_hyp.supporting_evidence) if cloud_hyp else 0
        if local_supp > cloud_supp:
            return ArbitrationResult(
                decision=RoutingDecision.LOCAL_ACCEPT,
                reasons=(RoutingReason.MODEL_DISAGREEMENT, RoutingReason.VALID_LOCAL_RESULT),
                winning_branch=Branch.LOCAL,
                human_review_required=forced_review,
                disagreement=True,
                rule_applied="A2_DISCRIMINATOR_EVIDENCE",
            )
        if cloud_supp > local_supp:
            return ArbitrationResult(
                decision=RoutingDecision.CLOUD_ACCEPT,
                reasons=(RoutingReason.MODEL_DISAGREEMENT, RoutingReason.VALID_CLOUD_RESULT),
                winning_branch=Branch.CLOUD,
                human_review_required=forced_review,
                disagreement=True,
                rule_applied="A2_DISCRIMINATOR_EVIDENCE",
            )

        # Rule A10: Unresolvable Disagreement (all deterministic discriminators tied)
        return ArbitrationResult(
            decision=RoutingDecision.HUMAN_REVIEW,
            reasons=(RoutingReason.MODEL_DISAGREEMENT, RoutingReason.UNRESOLVED_AMBIGUITY),
            winning_branch=None,
            human_review_required=True,
            disagreement=True,
            rule_applied="A10_UNRESOLVED_DISAGREEMENT",
        )

    # ----------------------------------------------------------------------
    # Helper methods (pure, deterministic extraction)
    # ----------------------------------------------------------------------

    @staticmethod
    def _get_top_fault_id(output: Optional[Any]) -> str:
        """Extract the top ranked fault_id safely."""
        if output is None or not output.ranked_hypotheses:
            return ""
        # Ranks might not be ordered 0..n, sort by rank ascending
        sorted_hyps = sorted(output.ranked_hypotheses, key=lambda h: getattr(h, "rank", 999))
        return sorted_hyps[0].fault_id if sorted_hyps else ""

    @staticmethod
    def _get_invalidated_faults(ranking_input: LLMRankingInput, physics_report: Any = None) -> set[str]:
        """Collect the deterministic invalidated fault set."""
        invalidated = set(ranking_input.physics.invalidated)
        if physics_report is not None:
            invalidated.update(getattr(physics_report, "invalidated", []))
        return invalidated

    @staticmethod
    def _get_validated_faults(ranking_input: LLMRankingInput, physics_report: Any = None) -> set[str]:
        """Collect the deterministic physics-validated fault set."""
        validated = set(ranking_input.physics.validated)
        if physics_report is not None:
            validated.update(getattr(physics_report, "validated", []))
        return validated
