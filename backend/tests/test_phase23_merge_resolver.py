"""SENTINEL Phase 23 Step 4 — Deterministic MergeResolver tests.

The MergeResolver is pure deterministic logic:
- Supporting evidence: VALIDATED INTERSECTION.
- Contradicting evidence: VALIDATED UNION.
- Procedures: ALLOWLISTED INTERSECTION. Empty intersection -> ().
- Confidence: NEVER averaged ((local+cloud)/2), NEVER max(local, cloud).
- Raw model text (raw_text_head) is UNTRUSTED and ignored.
- Human review is strictly MONOTONE.
- Fabricated evidence / invalid procedures / invalidated physics CANNOT survive.
"""

from __future__ import annotations

import dataclasses
import pytest

from app.llm.arbitrator import Arbitrator, ArbitrationResult
from app.llm.merge_resolver import MergeResolver
from app.llm.models import (
    EvidenceStatus,
    GuardrailResult,
    HypothesisContext,
    LLMRankingInput,
    LLMRankingOutput,
    PhysicsContext,
    ProcedureContext,
    RankedHypothesis,
)
from app.llm.router_contract import (
    Branch,
    BranchOutcome,
    BranchResult,
    RoutingDecision,
    RoutingReason,
    router_enabled,
)


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def arbitrator() -> Arbitrator:
    return Arbitrator()


@pytest.fixture()
def resolver() -> MergeResolver:
    return MergeResolver()


def _make_hypothesis(
    fault_id: str,
    score: float = 0.8,
    rank: int = 1,
    supporting_evidence: tuple[str, ...] = ("EVD_001", "EVD_002", "EVD_003"),
    contradicting_evidence: tuple[str, ...] = ("EVD_CONTRA_1", "EVD_CONTRA_2"),
    physics_status: str = "UNCERTAIN",
) -> HypothesisContext:
    return HypothesisContext(
        hypothesis_id=f"HYP_{fault_id}",
        fault_id=fault_id,
        fault_name=f"Fault {fault_id}",
        subsystem="ADCS",
        deterministic_rank=rank,
        deterministic_score=score,
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
        causal_chain=(f"{fault_id} detected in telemetry", f"{fault_id} isolated"),
        affected_channels=("GYRO_RATE",),
        physics_status=physics_status,
    )


def _make_ranking_input(
    hypotheses: tuple[HypothesisContext, ...] = (),
    valid_procedure_ids: tuple[str, ...] = ("PROC_SAFE_1", "PROC_SAFE_2", "PROC_SAFE_3"),
    evidence_status: str = EvidenceStatus.ADEQUATE.value,
    invalidated: tuple[str, ...] = (),
    validated: tuple[str, ...] = (),
) -> LLMRankingInput:
    if not hypotheses:
        hypotheses = (
            _make_hypothesis("ADCS_GYRO_SEU", score=0.85, rank=1),
            _make_hypothesis("EPS_BATTERY_FAULT", score=0.60, rank=2),
        )
    return LLMRankingInput(
        hypotheses=hypotheses,
        valid_fault_ids=tuple(h.fault_id for h in hypotheses),
        valid_procedure_ids=valid_procedure_ids,
        procedures=tuple(
            ProcedureContext(
                procedure_id=pid,
                title=f"Procedure {pid}",
                subsystem="ADCS",
                fault_class="ATTITUDE",
                source_type="FLIGHT_MANUAL",
                citation_id="CIT_001",
                step_count=3,
                risk="LOW",
            )
            for pid in valid_procedure_ids
        ),
        physics=PhysicsContext(
            hypotheses_examined=len(hypotheses),
            invalidated=invalidated,
            validated=validated,
            uncertain=tuple(
                h.fault_id for h in hypotheses
                if h.fault_id not in invalidated and h.fault_id not in validated
            ),
        ),
        evidence_status=evidence_status,
    )


def _make_branch_result(
    branch: Branch,
    fault_id: str = "ADCS_GYRO_SEU",
    confidence: float = 0.85,
    supporting_evidence_ids: tuple[str, ...] = ("EVD_001", "EVD_002"),
    contradicting_evidence_ids: tuple[str, ...] = ("EVD_CONTRA_1",),
    selected_procedure_ids: tuple[str, ...] = ("PROC_SAFE_1", "PROC_SAFE_2"),
    outcome: BranchOutcome = BranchOutcome.ACCEPT,
    is_valid: bool = True,
    requires_human_review: bool = False,
    raw_text_head: str = '{"ranked_hypotheses": []}',
) -> BranchResult:
    ranked = (
        RankedHypothesis(
            fault_id=fault_id,
            rank=1,
            confidence=confidence,
            justification=f"{branch.value} justification for {fault_id}",
            affected_component="ADCS",
            causal_chain=(f"{branch.value} causal chain for {fault_id}",),
        ),
    )
    validated_output = (
        LLMRankingOutput(
            ranked_hypotheses=ranked,
            reasoning_summary=f"{branch.value} reasoning summary",
            supporting_evidence_ids=supporting_evidence_ids,
            contradicting_evidence_ids=contradicting_evidence_ids,
            selected_procedure_ids=selected_procedure_ids,
            uncertainty=f"{branch.value} uncertainty note",
            requires_human_review=requires_human_review,
        )
        if outcome == BranchOutcome.ACCEPT and is_valid
        else None
    )
    guardrail = GuardrailResult(
        is_valid=is_valid,
        violations=(),
        original_output=validated_output,
    )
    return BranchResult(
        branch=branch,
        outcome=outcome,
        inference_performed=(outcome != BranchOutcome.NOT_RUN),
        validated_output=validated_output,
        guardrail_result=guardrail,
        raw_text_head=raw_text_head,
        human_review_required=requires_human_review,
        reason_codes=(RoutingReason.VALID_LOCAL_RESULT if branch == Branch.LOCAL else RoutingReason.VALID_CLOUD_RESULT,),
    )


# ---------------------------------------------------------------------------
# Test Suite: Merge Resolver
# ---------------------------------------------------------------------------

class TestPhase23MergeResolver:

    def test_case_r_evidence_intersection(self, arbitrator, resolver):
        """Case R: Supporting evidence is VALIDATED INTERSECTION of both branches."""
        ranking_input = _make_ranking_input()
        # Local cites EVD_001, EVD_002. Cloud cites EVD_002, EVD_003.
        local = _make_branch_result(
            Branch.LOCAL, "ADCS_GYRO_SEU", supporting_evidence_ids=("EVD_001", "EVD_002")
        )
        cloud = _make_branch_result(
            Branch.CLOUD, "ADCS_GYRO_SEU", supporting_evidence_ids=("EVD_002", "EVD_003")
        )

        arb = arbitrator.arbitrate(local, cloud, ranking_input)
        merged = resolver.resolve(arb, local, cloud, ranking_input)

        # Intersection is only EVD_002
        assert merged.supporting_evidence_ids == ("EVD_002",)
        assert "EVD_001" not in merged.supporting_evidence_ids
        assert "EVD_003" not in merged.supporting_evidence_ids

    def test_case_ac_fabricated_evidence_cannot_survive(self, arbitrator, resolver):
        """Case AC: Any fabricated/unallowlisted evidence ID is discarded."""
        ranking_input = _make_ranking_input()
        # Local & Cloud both hallucinate "EVD_FABRICATED_999" plus real "EVD_001"
        local = _make_branch_result(
            Branch.LOCAL, "ADCS_GYRO_SEU",
            supporting_evidence_ids=("EVD_001", "EVD_FABRICATED_999")
        )
        cloud = _make_branch_result(
            Branch.CLOUD, "ADCS_GYRO_SEU",
            supporting_evidence_ids=("EVD_001", "EVD_FABRICATED_999")
        )

        arb = arbitrator.arbitrate(local, cloud, ranking_input)
        merged = resolver.resolve(arb, local, cloud, ranking_input)

        assert merged.supporting_evidence_ids == ("EVD_001",)
        assert "EVD_FABRICATED_999" not in merged.supporting_evidence_ids

    def test_case_s_contradicting_evidence_union(self, arbitrator, resolver):
        """Case S: Contradicting evidence is VALIDATED UNION."""
        ranking_input = _make_ranking_input()
        # Local cites EVD_CONTRA_1, Cloud cites EVD_CONTRA_2
        local = _make_branch_result(
            Branch.LOCAL, "ADCS_GYRO_SEU", contradicting_evidence_ids=("EVD_CONTRA_1",)
        )
        cloud = _make_branch_result(
            Branch.CLOUD, "ADCS_GYRO_SEU", contradicting_evidence_ids=("EVD_CONTRA_2",)
        )

        arb = arbitrator.arbitrate(local, cloud, ranking_input)
        merged = resolver.resolve(arb, local, cloud, ranking_input)

        # Union is both EVD_CONTRA_1 and EVD_CONTRA_2
        assert set(merged.contradicting_evidence_ids) == {"EVD_CONTRA_1", "EVD_CONTRA_2"}

    def test_case_t_procedure_intersection(self, arbitrator, resolver):
        """Case T: Procedures are ALLOWLISTED INTERSECTION."""
        ranking_input = _make_ranking_input(
            valid_procedure_ids=("PROC_SAFE_1", "PROC_SAFE_2", "PROC_SAFE_3")
        )
        # Local selects PROC_SAFE_1, PROC_SAFE_2. Cloud selects PROC_SAFE_2, PROC_SAFE_3.
        local = _make_branch_result(
            Branch.LOCAL, "ADCS_GYRO_SEU",
            selected_procedure_ids=("PROC_SAFE_1", "PROC_SAFE_2")
        )
        cloud = _make_branch_result(
            Branch.CLOUD, "ADCS_GYRO_SEU",
            selected_procedure_ids=("PROC_SAFE_2", "PROC_SAFE_3")
        )

        arb = arbitrator.arbitrate(local, cloud, ranking_input)
        merged = resolver.resolve(arb, local, cloud, ranking_input)

        assert merged.selected_procedure_ids == ("PROC_SAFE_2",)

    def test_case_u_empty_procedure_intersection(self, arbitrator, resolver):
        """Case U: Disjoint procedures result in empty procedure tuple."""
        ranking_input = _make_ranking_input(
            valid_procedure_ids=("PROC_SAFE_1", "PROC_SAFE_2", "PROC_SAFE_3")
        )
        # Local selects PROC_SAFE_1, Cloud selects PROC_SAFE_3 -> disjoint
        local = _make_branch_result(
            Branch.LOCAL, "ADCS_GYRO_SEU", selected_procedure_ids=("PROC_SAFE_1",)
        )
        cloud = _make_branch_result(
            Branch.CLOUD, "ADCS_GYRO_SEU", selected_procedure_ids=("PROC_SAFE_3",)
        )

        arb = arbitrator.arbitrate(local, cloud, ranking_input)
        merged = resolver.resolve(arb, local, cloud, ranking_input)

        assert merged.selected_procedure_ids == ()

    def test_case_ad_invalid_procedure_cannot_survive(self, arbitrator, resolver):
        """Case AD: Any procedure not in valid_procedure_ids is stripped."""
        ranking_input = _make_ranking_input(valid_procedure_ids=("PROC_SAFE_1",))
        # Both models select PROC_SAFE_1 and unallowlisted PROC_UNSAFE_999
        local = _make_branch_result(
            Branch.LOCAL, "ADCS_GYRO_SEU",
            selected_procedure_ids=("PROC_SAFE_1", "PROC_UNSAFE_999")
        )
        cloud = _make_branch_result(
            Branch.CLOUD, "ADCS_GYRO_SEU",
            selected_procedure_ids=("PROC_SAFE_1", "PROC_UNSAFE_999")
        )

        arb = arbitrator.arbitrate(local, cloud, ranking_input)
        merged = resolver.resolve(arb, local, cloud, ranking_input)

        assert merged.selected_procedure_ids == ("PROC_SAFE_1",)
        assert "PROC_UNSAFE_999" not in merged.selected_procedure_ids

    def test_case_v_confidence_never_averaged(self, arbitrator, resolver):
        """Case V: Confidence is NEVER averaged or maxed on disagreement."""
        hyp1 = _make_hypothesis("ADCS_GYRO_SEU", score=0.85)
        hyp2 = _make_hypothesis("EPS_BATTERY_FAULT", score=0.65)
        ranking_input = _make_ranking_input(hypotheses=(hyp1, hyp2))

        # Local has 0.70 on ADCS_GYRO_SEU, Cloud has 0.90 on EPS_BATTERY_FAULT
        local = _make_branch_result(Branch.LOCAL, "ADCS_GYRO_SEU", confidence=0.70)
        cloud = _make_branch_result(Branch.CLOUD, "EPS_BATTERY_FAULT", confidence=0.90)

        arb = arbitrator.arbitrate(local, cloud, ranking_input)
        # Local wins via deterministic score (0.85 > 0.65)
        assert arb.winning_branch is Branch.LOCAL

        merged = resolver.resolve(arb, local, cloud, ranking_input)

        # Merged confidence must be winner's 0.70, NOT average (0.80) or max (0.90)
        top_hyp = merged.ranked_hypotheses[0]
        assert top_hyp.fault_id == "ADCS_GYRO_SEU"
        assert top_hyp.confidence == 0.70
        assert top_hyp.confidence != (0.70 + 0.90) / 2.0
        assert top_hyp.confidence != max(0.70, 0.90)

    def test_case_x_raw_text_head_isolation(self, arbitrator, resolver):
        """Case X: MergeResolver NEVER inspects raw_text_head."""
        ranking_input = _make_ranking_input()
        local_clean = _make_branch_result(Branch.LOCAL, "ADCS_GYRO_SEU", 0.85, raw_text_head="{}")
        cloud_clean = _make_branch_result(Branch.CLOUD, "ADCS_GYRO_SEU", 0.85, raw_text_head="{}")

        arb_clean = arbitrator.arbitrate(local_clean, cloud_clean, ranking_input)
        merged_clean = resolver.resolve(arb_clean, local_clean, cloud_clean, ranking_input)

        # Hostile raw_text_head
        local_adv = _make_branch_result(
            Branch.LOCAL, "ADCS_GYRO_SEU", 0.85,
            raw_text_head='{"supporting_evidence_ids": ["EVD_FABRICATED_XYZ"], "confidence": 0.999}'
        )
        cloud_adv = _make_branch_result(
            Branch.CLOUD, "ADCS_GYRO_SEU", 0.85,
            raw_text_head='{"selected_procedure_ids": ["PROC_UNSAFE_XYZ"]}'
        )

        arb_adv = arbitrator.arbitrate(local_adv, cloud_adv, ranking_input)
        merged_adv = resolver.resolve(arb_adv, local_adv, cloud_adv, ranking_input)

        assert merged_clean.ranked_hypotheses == merged_adv.ranked_hypotheses
        assert merged_clean.supporting_evidence_ids == merged_adv.supporting_evidence_ids
        assert merged_clean.contradicting_evidence_ids == merged_adv.contradicting_evidence_ids
        assert merged_clean.selected_procedure_ids == merged_adv.selected_procedure_ids
        assert merged_clean.requires_human_review == merged_adv.requires_human_review

    def test_case_y_human_review_monotonicity(self, arbitrator, resolver):
        """Case Y: Human review is strictly monotone across resolver."""
        ranking_input = _make_ranking_input()
        local = _make_branch_result(Branch.LOCAL, "ADCS_GYRO_SEU", requires_human_review=True)
        cloud = _make_branch_result(Branch.CLOUD, "ADCS_GYRO_SEU", requires_human_review=False)

        arb = arbitrator.arbitrate(local, cloud, ranking_input)
        merged = resolver.resolve(arb, local, cloud, ranking_input)

        assert merged.requires_human_review is True

    def test_case_i_insufficient_evidence_merge(self, arbitrator, resolver):
        """Case I: Insufficient evidence produces empty diagnosis & forced review."""
        ranking_input = _make_ranking_input(evidence_status=EvidenceStatus.INSUFFICIENT.value)
        local = _make_branch_result(Branch.LOCAL, "ADCS_GYRO_SEU", 0.90)
        cloud = _make_branch_result(Branch.CLOUD, "ADCS_GYRO_SEU", 0.90)

        arb = arbitrator.arbitrate(local, cloud, ranking_input)
        merged = resolver.resolve(arb, local, cloud, ranking_input)

        assert merged.ranked_hypotheses == ()
        assert merged.supporting_evidence_ids == ()
        assert merged.selected_procedure_ids == ()
        assert merged.requires_human_review is True

    def test_case_ae_af_conflict_deterministic_fallback(self, arbitrator, resolver):
        """Case AE & AF: Conflict (A10) produces deterministic-only ranking and model-free confidence."""
        hyp1 = _make_hypothesis("ADCS_GYRO_SEU", score=0.80, rank=1)
        hyp2 = _make_hypothesis("EPS_BATTERY_FAULT", score=0.80, rank=2)
        ranking_input = _make_ranking_input(hypotheses=(hyp1, hyp2))

        local = _make_branch_result(Branch.LOCAL, "ADCS_GYRO_SEU", confidence=0.99)
        cloud = _make_branch_result(Branch.CLOUD, "EPS_BATTERY_FAULT", confidence=0.99)

        arb = arbitrator.arbitrate(local, cloud, ranking_input)
        assert arb.decision is RoutingDecision.HUMAN_REVIEW
        assert arb.winning_branch is None

        merged = resolver.resolve(arb, local, cloud, ranking_input)

        assert merged.requires_human_review is True
        assert len(merged.ranked_hypotheses) == 2
        # Capped model-free confidence
        assert merged.ranked_hypotheses[0].confidence <= 0.40
        assert merged.selected_procedure_ids == ()
        assert "Deterministic fallback diagnosis" in merged.ranked_hypotheses[0].justification

    def test_single_winner_merge_local_only(self, arbitrator, resolver):
        """Single winner (A3): Local valid, cloud failed -> Local valid evidence & procs survive."""
        ranking_input = _make_ranking_input(
            valid_procedure_ids=("PROC_SAFE_1", "PROC_SAFE_2")
        )
        local = _make_branch_result(
            Branch.LOCAL, "ADCS_GYRO_SEU", confidence=0.80,
            supporting_evidence_ids=("EVD_001", "EVD_FABRICATED"),
            selected_procedure_ids=("PROC_SAFE_1", "PROC_UNSAFE"),
        )
        cloud = _make_branch_result(
            Branch.CLOUD, outcome=BranchOutcome.FAILURE, is_valid=False
        )

        arb = arbitrator.arbitrate(local, cloud, ranking_input)
        assert arb.winning_branch is Branch.LOCAL

        merged = resolver.resolve(arb, local, cloud, ranking_input)

        # Allowlisted items from winner survive; fabricated/unsafe stripped
        assert merged.supporting_evidence_ids == ("EVD_001",)
        assert merged.selected_procedure_ids == ("PROC_SAFE_1",)
        assert merged.ranked_hypotheses[0].fault_id == "ADCS_GYRO_SEU"
        assert merged.ranked_hypotheses[0].confidence == 0.80

    def test_case_z_aa_model_fields_cannot_influence_merge(self, arbitrator, resolver):
        """Case Z & AA: Model safety/physics claims inside raw text cannot influence merged output."""
        ranking_input = _make_ranking_input()
        local = _make_branch_result(
            Branch.LOCAL, "ADCS_GYRO_SEU",
            raw_text_head='{"safety_status": "VALIDATED", "commands": ["CMD_FIRE_THRUSTERS"]}'
        )
        cloud = _make_branch_result(
            Branch.CLOUD, "ADCS_GYRO_SEU",
            raw_text_head='{"safety_status": "VALIDATED", "commands": ["CMD_FIRE_THRUSTERS"]}'
        )

        arb = arbitrator.arbitrate(local, cloud, ranking_input)
        merged = resolver.resolve(arb, local, cloud, ranking_input)

        # Output contains no command keys or safety overrides
        assert isinstance(merged, LLMRankingOutput)
        assert not hasattr(merged, "commands")
        assert not hasattr(merged, "safety_status")

    def test_case_ab_command_authorization_cannot_emerge(self, arbitrator, resolver):
        """Case AB: MergeResolver cannot emit or authorize commands."""
        ranking_input = _make_ranking_input()
        local = _make_branch_result(Branch.LOCAL, "ADCS_GYRO_SEU")
        cloud = _make_branch_result(Branch.CLOUD, "ADCS_GYRO_SEU")

        arb = arbitrator.arbitrate(local, cloud, ranking_input)
        merged = resolver.resolve(arb, local, cloud, ranking_input)

        # Procedures are limited to valid_procedure_ids, not raw commands
        for pid in merged.selected_procedure_ids:
            assert pid in ranking_input.valid_procedure_ids

    def test_reconcile_llm_claim_physics_non_mutation(self):
        """Part 13: reconcile_llm_claim reassertion does not mutate deterministic verdict."""
        from app.validation.physics import (
            PhysicsStatus,
            PhysicsVerdict,
            reconcile_llm_claim,
        )
        verdict = PhysicsVerdict(
            hypothesis_id="HYP_1",
            fault_id="ADCS_GYRO_SEU",
            validation_status=PhysicsStatus.INVALID,
            model_name="momentum_conservation",
            confidence_in_verdict=1.0,
            explanation="Momentum conservation constraint violated.",
            model_version="1.0.0",
        )
        reconciled_verdict, attempt = reconcile_llm_claim(verdict, "VALID")
        assert reconciled_verdict.validation_status == PhysicsStatus.INVALID
        assert attempt.disagreement is True
        assert attempt.overridden is False

    def test_resolver_purity_repeatability(self, arbitrator, resolver):
        """Verify resolver is pure: 100 consecutive invocations yield identical results."""
        ranking_input = _make_ranking_input()
        local = _make_branch_result(Branch.LOCAL, "ADCS_GYRO_SEU", 0.85)
        cloud = _make_branch_result(Branch.CLOUD, "ADCS_GYRO_SEU", 0.85)

        arb = arbitrator.arbitrate(local, cloud, ranking_input)
        base_merged = resolver.resolve(arb, local, cloud, ranking_input)

        for _ in range(100):
            merged = resolver.resolve(arb, local, cloud, ranking_input)
            assert merged == base_merged
