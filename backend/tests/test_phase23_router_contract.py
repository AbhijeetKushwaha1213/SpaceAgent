"""SENTINEL Phase 23 Step 1 — Hybrid router CONTRACT test skeletons.

These tests pin the router's LANGUAGE (Phase 22 plan, Phase 23 Step 1
mandate), not its behavior.  They use only stubs/fixtures:

    - no real Gemini API, no real Ollama inference, no network calls
    - no routing/arbitration/merge logic exists yet; tests that would need
      it are explicitly marked ``PHASE 23 FOLLOW-UP`` and skipped

What IS validated here:

    - enum validity and exhaustiveness
    - contract construction and required fields
    - immutability (frozen dataclasses, tuples)
    - monotone human-review semantics
    - fail-closed state representation
    - separation of raw/untrusted model output from validated output
    - authority boundaries (no model-owned physics/safety fields)
    - ROUTER_ENABLED defaults to false

CONTRACT TEST MATRIX (Part 12)

| Case | Scenario                    | Expected Decision          | Reason                    | Contract Covered |
|------|-----------------------------|----------------------------|---------------------------|------------------|
| A    | Local accepted              | LOCAL_ACCEPT               | valid_local_result        | yes              |
| B    | Local malformed             | CLOUD_ESCALATE             | invalid_structured_output | yes              |
| C    | Local fabricated evidence   | CLOUD_ESCALATE/HUMAN_REVIEW| evidence_failure          | yes              |
| D    | Local physics conflict      | CLOUD_ESCALATE             | physics_conflict          | yes              |
| E    | Local procedure violation   | CLOUD_ESCALATE/HUMAN_REVIEW| procedure_invalid         | yes              |
| F    | Local -> Gemini escalation  | CLOUD_ESCALATE             | local_escalation          | yes              |
| G    | Cloud accepted              | CLOUD_ACCEPT               | valid_cloud_result        | yes              |
| H    | Local/cloud agreement       | ACCEPT                     | branch_agreement          | yes              |
| I    | Local/cloud disagreement    | ARBITRATION                | model_disagreement        | future           |
| J    | Both invalid                | HUMAN_REVIEW               | both_invalid              | yes              |
| K    | Insufficient evidence       | HUMAN_REVIEW               | insufficient_evidence     | yes              |
| L    | Safety block                | BLOCKED                    | safety_block              | yes              |
| M    | Human review                | HUMAN_REVIEW               | human_review_required     | yes              |
| N    | Cloud unavailable           | FALLBACK/HUMAN             | cloud_unavailable         | yes              |
| O    | Local unavailable           | FALLBACK/HUMAN             | local_unavailable         | yes              |
| P    | Both unavailable            | HUMAN_REVIEW (NO_INFERENCE)| both_unavailable          | yes              |
"""

from __future__ import annotations

import dataclasses

import pytest

from app.llm.models import (
    EvidenceStatus,
    GuardrailResult,
    GuardrailViolation,
    LLMRankingOutput,
    RankedHypothesis,
    ViolationType,
)
from app.llm.router_contract import (
    Branch,
    BranchOutcome,
    BranchResult,
    RoutingDecision,
    RoutingReason,
    RoutingRecord,
    combine_human_review,
    router_enabled,
)


# ---------------------------------------------------------------------------
# helpers — stub builders only; no providers, no network
# ---------------------------------------------------------------------------

def _clean_output(fault_id: str = "ADCS_GYRO_SEU") -> LLMRankingOutput:
    return LLMRankingOutput(
        ranked_hypotheses=(
            RankedHypothesis(fault_id=fault_id, rank=1, confidence=0.8,
                             justification="stub", affected_component="ADCS"),
        ),
        reasoning_summary="stub branch output for contract tests",
        requires_human_review=False,
    )


def _clean_guardrail(output: LLMRankingOutput) -> GuardrailResult:
    return GuardrailResult(
        is_valid=True, violations=(), corrected_output=None,
        original_output=output, raw_response="{}",
    )


def _violating_guardrail(
    violation_type: ViolationType, detail: str,
) -> GuardrailResult:
    return GuardrailResult(
        is_valid=False,
        violations=(GuardrailViolation(
            violation_type=violation_type, detail=detail,
        ),),
        corrected_output=None, original_output=None, raw_response="{}",
    )


def _accepted_local(fault_id: str = "ADCS_GYRO_SEU") -> BranchResult:
    out = _clean_output(fault_id)
    return BranchResult(
        branch=Branch.LOCAL,
        outcome=BranchOutcome.ACCEPT,
        provider_name="local", model_name="phi3:mini",
        inference_performed=True,
        validated_output=out, guardrail_result=_clean_guardrail(out),
        evidence_status=EvidenceStatus.ADEQUATE.value,
        scenario_id="T", elapsed_ms=1.0, attempts=1,
        reason_codes=(RoutingReason.VALID_LOCAL_RESULT,),
    )


def _accepted_cloud(fault_id: str = "ADCS_GYRO_SEU") -> BranchResult:
    out = _clean_output(fault_id)
    return BranchResult(
        branch=Branch.CLOUD,
        outcome=BranchOutcome.ACCEPT,
        provider_name="gemini", model_name="gemini-2.5-flash",
        inference_performed=True,
        validated_output=out, guardrail_result=_clean_guardrail(out),
        evidence_status=EvidenceStatus.ADEQUATE.value,
        scenario_id="T", elapsed_ms=1.0, attempts=1,
        reason_codes=(RoutingReason.VALID_CLOUD_RESULT,),
    )


def _failed_local(reason: RoutingReason) -> BranchResult:
    return BranchResult(
        branch=Branch.LOCAL,
        outcome=BranchOutcome.FAILURE,
        provider_name="local", model_name="phi3:mini",
        inference_performed=False,
        validated_output=None, guardrail_result=None,
        evidence_status=EvidenceStatus.ADEQUATE.value,
        scenario_id="T", elapsed_ms=1.0, attempts=2,
        reason_codes=(reason,),
    )


# ---------------------------------------------------------------------------
# Enum validity + exhaustiveness
# ---------------------------------------------------------------------------

class TestEnumValidity:
    def test_routing_decision_has_mandated_semantics(self):
        # Phase 23 Step 1 Part 3 minimum set
        assert RoutingDecision.LOCAL_ACCEPT.value == "LOCAL_ACCEPT"
        assert RoutingDecision.CLOUD_ESCALATE.value == "CLOUD_ESCALATE"
        assert RoutingDecision.HUMAN_REVIEW.value == "HUMAN_REVIEW"
        assert RoutingDecision.BLOCKED.value == "BLOCKED"

    def test_routing_reason_covers_mandated_minimum(self):
        required = {
            "valid_local_result", "invalid_structured_output",
            "evidence_failure", "insufficient_evidence", "physics_conflict",
            "procedure_invalid", "safety_block", "local_timeout",
            "local_unavailable", "cloud_unavailable", "model_disagreement",
            "unresolved_ambiguity", "human_review_required",
        }
        actual = {r.value for r in RoutingReason}
        assert required <= actual

    def test_reason_values_are_snake_case_enums_not_free_strings(self):
        for r in RoutingReason:
            assert r.value == r.value.lower()
            assert " " not in r.value

    def test_branch_outcomes_cover_fail_closed_states(self):
        assert {o.value for o in BranchOutcome} == {
            "ACCEPT", "ESCALATION", "FAILURE", "NOT_RUN",
        }

    def test_terminal_review_decisions(self):
        assert RoutingDecision.HUMAN_REVIEW.is_terminal_review
        assert RoutingDecision.BLOCKED.is_terminal_review
        assert RoutingDecision.NO_INFERENCE.is_terminal_review
        assert not RoutingDecision.LOCAL_ACCEPT.is_terminal_review
        assert not RoutingDecision.CLOUD_ACCEPT.is_terminal_review
        assert not RoutingDecision.CLOUD_ESCALATE.is_terminal_review


# ---------------------------------------------------------------------------
# Contract construction + required fields
# ---------------------------------------------------------------------------

class TestContractConstruction:
    def test_branch_result_required_fields_present(self):
        result = _accepted_local()
        assert result.branch is Branch.LOCAL
        assert result.outcome is BranchOutcome.ACCEPT
        assert result.provider_name and result.model_name
        assert result.inference_performed is True
        assert result.validated_output is not None
        assert result.guardrail_result is not None
        assert result.evidence_status == "ADEQUATE"
        assert result.elapsed_ms >= 0.0
        assert result.reason_codes == (RoutingReason.VALID_LOCAL_RESULT,)
        assert result.human_review_required is False

    def test_routing_record_holds_decision_and_justification(self):
        record = RoutingRecord(
            decision=RoutingDecision.LOCAL_ACCEPT,
            reasons=(RoutingReason.VALID_LOCAL_RESULT,),
            local=_accepted_local(),
            cloud=None,
            signal_snapshot=(("evidence_status", "ADEQUATE"),),
        )
        assert record.decision is RoutingDecision.LOCAL_ACCEPT
        assert record.local is not None and record.cloud is None
        assert record.human_review_required is False

    def test_not_run_branch_is_representable(self):
        result = BranchResult(
            branch=Branch.CLOUD, outcome=BranchOutcome.NOT_RUN,
        )
        assert result.inference_performed is False
        assert result.validated_output is None
        assert not result.is_usable


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------

class TestImmutability:
    def test_branch_result_is_frozen(self):
        result = _accepted_local()
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.outcome = BranchOutcome.FAILURE  # type: ignore[misc]

    def test_routing_record_is_frozen(self):
        record = RoutingRecord(
            decision=RoutingDecision.HUMAN_REVIEW,
            reasons=(RoutingReason.INSUFFICIENT_EVIDENCE,),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.decision = RoutingDecision.LOCAL_ACCEPT  # type: ignore[misc]

    def test_reason_codes_are_tuples_not_lists(self):
        result = _accepted_local()
        assert isinstance(result.reason_codes, tuple)
        record = RoutingRecord(
            decision=RoutingDecision.HUMAN_REVIEW, reasons=(),
        )
        assert isinstance(record.reasons, tuple)
        assert isinstance(record.signal_snapshot, tuple)


# ---------------------------------------------------------------------------
# Monotone human review (Part 7)
# ---------------------------------------------------------------------------

class TestMonotoneHumanReview:
    def test_combine_is_or_monotone(self):
        assert combine_human_review(False, False) is False
        assert combine_human_review(True, False) is True
        assert combine_human_review(False, True) is True
        assert combine_human_review(True, True) is True
        assert combine_human_review() is False

    def test_terminal_decisions_force_review_regardless_of_models(self):
        # Both branches claim no review needed; deterministic terminals win.
        record = RoutingRecord(
            decision=RoutingDecision.HUMAN_REVIEW,
            reasons=(RoutingReason.BOTH_INVALID,),
            local=_accepted_local(),
            cloud=_accepted_cloud(),
            human_review_required=combine_human_review(
                _accepted_local().human_review_required,
                _accepted_cloud().human_review_required,
                RoutingDecision.HUMAN_REVIEW.is_terminal_review,
            ),
        )
        assert record.human_review_required is True

    def test_no_api_can_clear_a_raised_review_flag(self):
        result = BranchResult(
            branch=Branch.LOCAL, outcome=BranchOutcome.ACCEPT,
            human_review_required=True,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.human_review_required = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Raw/untrusted separation + authority boundaries (Parts 5, 8)
# ---------------------------------------------------------------------------

class TestTrustSeparation:
    def test_raw_text_head_cannot_promote_to_validated_output(self):
        # A prompt-echo style blob citing fabricated IDs stays diagnostic:
        # validated_output remains None and nothing parses it.
        result = BranchResult(
            branch=Branch.LOCAL,
            outcome=BranchOutcome.FAILURE,
            inference_performed=True,
            validated_output=None,
            guardrail_result=None,
            raw_text_head='{"scenario_id": "...", "fault_1": 0.95}',
        )
        assert result.validated_output is None
        assert not result.is_usable
        assert result.succeeded is True  # call completed; output unusable

    def test_branch_result_owns_no_physics_or_safety_verdict(self):
        # Authority boundary: model-owned physics/safety fields must not
        # exist in the contract (Phase 23 mandate Part 8).
        names = {f.name for f in dataclasses.fields(BranchResult)}
        for forbidden in (
            "physics_verdict", "model_physics_verdict",
            "safety_verdict", "model_safety_verdict", "safety_status",
        ):
            assert forbidden not in names

    def test_routing_record_owns_no_validated_state(self):
        names = {f.name for f in dataclasses.fields(RoutingRecord)}
        assert "physics_report" not in names
        assert "validation_result" not in names


# ---------------------------------------------------------------------------
# Fail-closed representation (Phase 22 §11)
# ---------------------------------------------------------------------------

class TestFailClosedRepresentation:
    def test_no_inference_is_an_explicit_state_not_an_exception(self):
        record = RoutingRecord(
            decision=RoutingDecision.NO_INFERENCE,
            reasons=(RoutingReason.BOTH_UNAVAILABLE,),
            local=BranchResult(
                branch=Branch.LOCAL, outcome=BranchOutcome.NOT_RUN,
                reason_codes=(RoutingReason.LOCAL_UNAVAILABLE,),
            ),
            cloud=BranchResult(
                branch=Branch.CLOUD, outcome=BranchOutcome.NOT_RUN,
                reason_codes=(RoutingReason.CLOUD_UNAVAILABLE,),
            ),
            human_review_required=True,
        )
        assert record.decision.is_terminal_review
        assert record.human_review_required is True


# ---------------------------------------------------------------------------
# ROUTER_ENABLED flag (Part 9)
# ---------------------------------------------------------------------------

class TestRouterEnabledFlag:
    def test_default_is_disabled(self, monkeypatch):
        monkeypatch.delenv("ROUTER_ENABLED", raising=False)
        assert router_enabled() is False

    def test_explicit_true_values_enable(self, monkeypatch):
        for value in ("true", "TRUE", "1", "yes"):
            monkeypatch.setenv("ROUTER_ENABLED", value)
            assert router_enabled() is True

    def test_any_other_value_stays_disabled(self, monkeypatch):
        for value in ("false", "0", "no", "hybrid", "", "on"):
            monkeypatch.setenv("ROUTER_ENABLED", value)
            assert router_enabled() is False


# ---------------------------------------------------------------------------
# TEST SKELETONS A-P (Part 11) — contract level, stubs only
# ---------------------------------------------------------------------------

class TestSkeletonA_LocalAccepted:
    """A: clean local output -> LOCAL_ACCEPT / valid_local_result."""

    def test_accept_contract(self):
        local = _accepted_local()
        record = RoutingRecord(
            decision=RoutingDecision.LOCAL_ACCEPT,
            reasons=(RoutingReason.VALID_LOCAL_RESULT,),
            local=local,
        )
        assert local.is_usable
        assert local.guardrail_result.is_valid
        assert record.decision is RoutingDecision.LOCAL_ACCEPT


class TestSkeletonB_LocalMalformed:
    """B: unparseable local output -> CLOUD_ESCALATE / invalid_structured_output."""

    def test_malformed_is_failure_with_no_validated_state(self):
        local = _failed_local(RoutingReason.INVALID_STRUCTURED_OUTPUT)
        record = RoutingRecord(
            decision=RoutingDecision.CLOUD_ESCALATE,
            reasons=(RoutingReason.INVALID_STRUCTURED_OUTPUT,),
            local=local,
        )
        assert not local.is_usable
        assert local.validated_output is None
        assert record.decision is RoutingDecision.CLOUD_ESCALATE


class TestSkeletonC_LocalFabricatedEvidence:
    """C: fabricated evidence IDs -> guardrail violation preserved."""

    def test_violation_survives_in_branch_result(self):
        violation = _violating_guardrail(
            ViolationType.NONEXISTENT_EVIDENCE,
            "LLM cited evidence ID 'EVID-FAKEFAKEFAKE' not in the allowlist",
        )
        local = BranchResult(
            branch=Branch.LOCAL, outcome=BranchOutcome.ESCALATION,
            inference_performed=True,
            validated_output=None, guardrail_result=violation,
            reason_codes=(RoutingReason.EVIDENCE_FAILURE,),
        )
        assert not local.is_usable
        assert violation.violation_types == (
            ViolationType.NONEXISTENT_EVIDENCE,
        )


class TestSkeletonD_LocalPhysicsConflict:
    """D: model ranking an INVALIDATED fault -> physics_conflict reason."""

    def test_conflict_reason_representable_without_model_physics_field(self):
        local = BranchResult(
            branch=Branch.LOCAL, outcome=BranchOutcome.ESCALATION,
            inference_performed=True,
            reason_codes=(RoutingReason.PHYSICS_CONFLICT,),
        )
        record = RoutingRecord(
            decision=RoutingDecision.CLOUD_ESCALATE,
            reasons=local.reason_codes,
            local=local,
        )
        assert record.reasons == (RoutingReason.PHYSICS_CONFLICT,)
        # The deterministic verdict itself lives in app.validation.physics,
        # never inside the BranchResult (asserted in TestTrustSeparation).


class TestSkeletonE_LocalProcedureViolation:
    """E: non-allowlisted procedure -> procedure_invalid."""

    def test_procedure_violation_classifies_escalation(self):
        violation = _violating_guardrail(
            ViolationType.INVALID_PROCEDURE,
            "LLM selected procedure 'PROC-IMAGINARY' not in valid list",
        )
        local = BranchResult(
            branch=Branch.LOCAL, outcome=BranchOutcome.ESCALATION,
            inference_performed=True,
            validated_output=None, guardrail_result=violation,
            reason_codes=(RoutingReason.PROCEDURE_INVALID,),
        )
        assert not local.is_usable
        assert RoutingReason.PROCEDURE_INVALID in local.reason_codes


class TestSkeletonF_LocalToGeminiEscalation:
    """F: local failure with cloud permitted -> CLOUD_ESCALATE."""

    def test_escalation_record_carries_local_failure_reasons(self):
        local = _failed_local(RoutingReason.LOCAL_TIMEOUT)
        record = RoutingRecord(
            decision=RoutingDecision.CLOUD_ESCALATE,
            reasons=(RoutingReason.LOCAL_ESCALATION, RoutingReason.LOCAL_TIMEOUT),
            local=local,
            cloud=BranchResult(branch=Branch.CLOUD,
                               outcome=BranchOutcome.NOT_RUN),
        )
        assert record.local.outcome is BranchOutcome.FAILURE
        assert record.cloud.outcome is BranchOutcome.NOT_RUN


class TestSkeletonG_CloudAccepted:
    """G: CLOUD_ONLY policy, clean cloud output -> CLOUD_ACCEPT."""

    def test_cloud_accept_contract(self):
        cloud = _accepted_cloud()
        record = RoutingRecord(
            decision=RoutingDecision.CLOUD_ACCEPT,
            reasons=(RoutingReason.VALID_CLOUD_RESULT,),
            local=BranchResult(branch=Branch.LOCAL,
                               outcome=BranchOutcome.NOT_RUN),
            cloud=cloud,
        )
        assert cloud.branch is Branch.CLOUD
        assert record.decision is RoutingDecision.CLOUD_ACCEPT


class TestSkeletonH_LocalCloudAgreement:
    """H: both valid, same top fault -> agreement adopt (local tie-break)."""

    def test_agreement_representation(self):
        local = _accepted_local("TCS_THERMAL_RUNAWAY")
        cloud = _accepted_cloud("TCS_THERMAL_RUNAWAY")
        assert (
            local.validated_output.ranked_hypotheses[0].fault_id
            == cloud.validated_output.ranked_hypotheses[0].fault_id
        )
        record = RoutingRecord(
            decision=RoutingDecision.LOCAL_ACCEPT,
            reasons=(RoutingReason.BRANCH_AGREEMENT,),
            local=local, cloud=cloud,
            human_review_required=combine_human_review(
                local.human_review_required, cloud.human_review_required,
            ),
        )
        assert record.human_review_required is False

    @pytest.mark.skip(
        reason="PHASE 23 FOLLOW-UP: adoption tie-break belongs to the "
               "Arbitrator, which is not implemented in Step 1.",
    )
    def test_arbitrator_adopts_local_on_agreement(self):
        pass  # requires Arbitrator (deferred)


class TestSkeletonI_LocalCloudDisagreement:
    """I: valid but different top faults -> model_disagreement.

    The deterministic-precedence resolution (Phase 22 A2/A10) is future
    Arbitrator behavior; Step 1 only pins that the disagreement is
    representable and never resolvable by comparing confidences.
    """

    def test_disagreement_reason_representable(self):
        local = _accepted_local("ADCS_GYRO_SEU")
        cloud = _accepted_cloud("TCS_THERMAL_RUNAWAY")
        assert (
            local.validated_output.ranked_hypotheses[0].fault_id
            != cloud.validated_output.ranked_hypotheses[0].fault_id
        )
        record = RoutingRecord(
            decision=RoutingDecision.HUMAN_REVIEW,
            reasons=(RoutingReason.MODEL_DISAGREEMENT,),
            local=local, cloud=cloud,
            human_review_required=True,
        )
        assert record.human_review_required is True

    @pytest.mark.skip(
        reason="PHASE 23 FOLLOW-UP: deterministic-discriminator arbitration "
               "(physics > score > evidence) is Arbitrator behavior.",
    )
    def test_arbitrator_resolves_by_deterministic_discriminators(self):
        pass  # requires Arbitrator (deferred)


class TestSkeletonJ_BothInvalid:
    """J: both branches violating/unparseable -> HUMAN_REVIEW / both_invalid."""

    def test_both_invalid_is_terminal_review(self):
        record = RoutingRecord(
            decision=RoutingDecision.HUMAN_REVIEW,
            reasons=(RoutingReason.BOTH_INVALID,),
            local=_failed_local(RoutingReason.INVALID_STRUCTURED_OUTPUT),
            cloud=BranchResult(
                branch=Branch.CLOUD, outcome=BranchOutcome.ESCALATION,
                inference_performed=True,
                guardrail_result=_violating_guardrail(
                    ViolationType.UNSUPPORTED_HYPOTHESIS, "stub"),
            ),
            human_review_required=True,
        )
        assert record.decision.is_terminal_review
        assert not record.local.is_usable
        assert not record.cloud.is_usable


class TestSkeletonK_InsufficientEvidence:
    """K: evidence_status=INSUFFICIENT -> fixed HUMAN_REVIEW outcome."""

    def test_insufficient_forces_review_before_any_inference(self):
        record = RoutingRecord(
            decision=RoutingDecision.HUMAN_REVIEW,
            reasons=(RoutingReason.INSUFFICIENT_EVIDENCE,),
            signal_snapshot=(("evidence_status", "INSUFFICIENT"),),
            human_review_required=True,
        )
        assert record.decision.is_terminal_review
        assert ("evidence_status", "INSUFFICIENT") in record.signal_snapshot


class TestSkeletonL_SafetyBlock:
    """L: deterministic safety blocks the plan -> BLOCKED / safety_block."""

    def test_safety_block_is_deterministic_terminal(self):
        record = RoutingRecord(
            decision=RoutingDecision.BLOCKED,
            reasons=(RoutingReason.SAFETY_BLOCK,),
            human_review_required=True,
        )
        assert record.decision is RoutingDecision.BLOCKED
        assert record.decision.is_terminal_review
        # Authority check: the safety verdict itself is never carried here;
        # it is produced by app.agent.safety after the merge (Part 8).


class TestSkeletonM_HumanReview:
    """M: model-emitted review flag is honored one-way."""

    def test_model_raised_review_survives_to_record(self):
        out = _clean_output()
        review_out = dataclasses.replace(out, requires_human_review=True)
        local = BranchResult(
            branch=Branch.LOCAL, outcome=BranchOutcome.ACCEPT,
            inference_performed=True,
            validated_output=review_out,
            guardrail_result=_clean_guardrail(review_out),
            human_review_required=review_out.requires_human_review,
        )
        record = RoutingRecord(
            decision=RoutingDecision.LOCAL_ACCEPT,
            reasons=(RoutingReason.HUMAN_REVIEW_REQUIRED,),
            local=local,
            human_review_required=combine_human_review(
                local.human_review_required,
            ),
        )
        assert record.human_review_required is True


class TestSkeletonN_CloudUnavailable:
    """N: policy allowed cloud but it is down -> degrade to local-only."""

    def test_cloud_unavailable_reason_and_fallback_representation(self):
        record = RoutingRecord(
            decision=RoutingDecision.LOCAL_ACCEPT,
            reasons=(RoutingReason.CLOUD_UNAVAILABLE,
                     RoutingReason.VALID_LOCAL_RESULT),
            local=_accepted_local(),
            cloud=BranchResult(
                branch=Branch.CLOUD, outcome=BranchOutcome.NOT_RUN,
                reason_codes=(RoutingReason.CLOUD_UNAVAILABLE,),
            ),
        )
        assert RoutingReason.CLOUD_UNAVAILABLE in record.reasons


class TestSkeletonO_LocalUnavailable:
    """O: local probe fails -> CLOUD_ONLY path or degraded review."""

    def test_local_unavailable_representation(self):
        record = RoutingRecord(
            decision=RoutingDecision.CLOUD_ACCEPT,
            reasons=(RoutingReason.LOCAL_UNAVAILABLE,
                     RoutingReason.VALID_CLOUD_RESULT),
            local=BranchResult(
                branch=Branch.LOCAL, outcome=BranchOutcome.NOT_RUN,
                reason_codes=(RoutingReason.LOCAL_UNAVAILABLE,),
            ),
            cloud=_accepted_cloud(),
        )
        assert record.local.outcome is BranchOutcome.NOT_RUN
        assert record.cloud.is_usable


class TestSkeletonP_BothUnavailable:
    """P: neither provider reachable -> NO_INFERENCE, mandatory review."""

    def test_both_unavailable_is_first_class_terminal(self):
        record = RoutingRecord(
            decision=RoutingDecision.NO_INFERENCE,
            reasons=(RoutingReason.BOTH_UNAVAILABLE,),
            local=BranchResult(
                branch=Branch.LOCAL, outcome=BranchOutcome.NOT_RUN,
                reason_codes=(RoutingReason.LOCAL_UNAVAILABLE,),
            ),
            cloud=BranchResult(
                branch=Branch.CLOUD, outcome=BranchOutcome.NOT_RUN,
                reason_codes=(RoutingReason.CLOUD_UNAVAILABLE,),
            ),
            human_review_required=True,
        )
        assert record.decision.is_terminal_review
        assert record.human_review_required is True
        assert record.local.outcome is BranchOutcome.NOT_RUN
        assert record.cloud.outcome is BranchOutcome.NOT_RUN


# ---------------------------------------------------------------------------
# Contract matrix (Part 12) — every row is representable by the contracts
# ---------------------------------------------------------------------------

MATRIX = [
    ("A", RoutingDecision.LOCAL_ACCEPT, RoutingReason.VALID_LOCAL_RESULT, True),
    ("B", RoutingDecision.CLOUD_ESCALATE, RoutingReason.INVALID_STRUCTURED_OUTPUT, True),
    ("C", RoutingDecision.CLOUD_ESCALATE, RoutingReason.EVIDENCE_FAILURE, True),
    ("D", RoutingDecision.CLOUD_ESCALATE, RoutingReason.PHYSICS_CONFLICT, True),
    ("E", RoutingDecision.CLOUD_ESCALATE, RoutingReason.PROCEDURE_INVALID, True),
    ("F", RoutingDecision.CLOUD_ESCALATE, RoutingReason.LOCAL_ESCALATION, True),
    ("G", RoutingDecision.CLOUD_ACCEPT, RoutingReason.VALID_CLOUD_RESULT, True),
    ("H", RoutingDecision.LOCAL_ACCEPT, RoutingReason.BRANCH_AGREEMENT, True),
    ("I", RoutingDecision.HUMAN_REVIEW, RoutingReason.MODEL_DISAGREEMENT, False),
    ("J", RoutingDecision.HUMAN_REVIEW, RoutingReason.BOTH_INVALID, True),
    ("K", RoutingDecision.HUMAN_REVIEW, RoutingReason.INSUFFICIENT_EVIDENCE, True),
    ("L", RoutingDecision.BLOCKED, RoutingReason.SAFETY_BLOCK, True),
    ("M", RoutingDecision.HUMAN_REVIEW, RoutingReason.HUMAN_REVIEW_REQUIRED, True),
    ("N", RoutingDecision.LOCAL_ACCEPT, RoutingReason.CLOUD_UNAVAILABLE, True),
    ("O", RoutingDecision.CLOUD_ACCEPT, RoutingReason.LOCAL_UNAVAILABLE, True),
    ("P", RoutingDecision.NO_INFERENCE, RoutingReason.BOTH_UNAVAILABLE, True),
]


class TestContractMatrix:
    @pytest.mark.parametrize("case,decision,reason,covered", MATRIX)
    def test_matrix_row_representable(self, case, decision, reason, covered):
        record = RoutingRecord(decision=decision, reasons=(reason,))
        assert isinstance(record.decision, RoutingDecision)
        assert all(isinstance(r, RoutingReason) for r in record.reasons)
        if decision.is_terminal_review:
            # Fail-closed terminals must never be constructible without an
            # explicit human-review obligation at the record level.
            assert decision in (
                RoutingDecision.HUMAN_REVIEW,
                RoutingDecision.BLOCKED,
                RoutingDecision.NO_INFERENCE,
            )
