"""SENTINEL Phase 23 Step 3 — CloudBranchRunner adapter tests (A-Y).

All tests use a FakeGeminiProvider that CAPTURES the exact messages the
runner transmits (mandate Part 12: provider payload interception).  No
GEMINI_API_KEY, no internet, no Gemini API, no Ollama.

Matrix coverage:

    A  successful cloud inference          N  deterministic fields intact
    B  valid Gemini JSON                   O  physics authority non-injectable
    C  malformed JSON                      P  safety authority non-injectable
    D  invalid evidence ID                 Q  no command authorization
    E  invalid procedure ID                R  raw output stays untrusted
    F  physics conflict                    S  validated output only post-guardrail
    G  cloud timeout                       T  human review monotone
    H  cloud unavailable                   U  provider/model identity
    I  redaction success                   V  latency recorded
    J  redaction failure                   W  bounded retry semantics
    K  provider NOT called on gate fail    X  no network in unit tests
    L  raw sensitive value never reaches   Y  ROUTER_ENABLED=false ⇒ unchanged
    M  redacted representation reaches
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import socket
import urllib.request

import pytest

from app.llm.cloud_branch import CloudBranchRunner
from app.llm.models import (
    EvidenceStatus,
    HypothesisContext,
    LLMRankingInput,
    PhysicsContext,
    ViolationType,
)
from app.llm.provider import LLMProvider, ProviderError
from app.llm.router_contract import (
    Branch,
    BranchOutcome,
    BranchResult,
    RoutingReason,
    router_enabled,
)

REAL_EVIDENCE_ID = "EVID-AAAAAAAAAAAA"
REAL_PROCEDURE_ID = "PROC-ADCS-GYRO-RECOVERY"
FAULT_ID = "ADCS_GYRO_SEU"
FAULT_ID_2 = "EPS_BATTERY_DEGRADATION"

RAW_SECRET = "RAW_OPERATOR_SECRET_9f8e7d"


# ---------------------------------------------------------------------------
# fakes + fixtures
# ---------------------------------------------------------------------------

class FakeGeminiProvider(LLMProvider):
    """Scripted cloud provider.  CAPTURES every message list it receives so
    tests can assert exactly what crossed the (simulated) network boundary.
    """

    def __init__(self, outcomes, model_name: str = "gemini-2.5-flash-test"):
        self._outcomes = list(outcomes)
        self._model_name = model_name
        self.calls = 0
        self.captured_messages: list[list[dict[str, str]]] = []

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model_name

    def call(self, messages):
        self.calls += 1
        self.captured_messages.append(list(messages))
        if not self._outcomes:
            raise ProviderError("FakeGeminiProvider script exhausted")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def payload_text(self) -> str:
        """Everything the runner would have transmitted, as one string."""
        return " ".join(
            m.get("content", "")
            for msgs in self.captured_messages
            for m in msgs
        )


def _ranking_input(**overrides) -> LLMRankingInput:
    hyp = HypothesisContext(
        hypothesis_id=f"HYP-{FAULT_ID}",
        fault_id=FAULT_ID,
        fault_name=FAULT_ID,
        subsystem="ADCS",
        deterministic_rank=1,
        deterministic_score=0.9,
        supporting_evidence=(REAL_EVIDENCE_ID,),
    )
    defaults = dict(
        anomaly_summary="gyro rate excursion preceding safe mode",
        hypotheses=(hyp,),
        valid_fault_ids=(FAULT_ID,),
        valid_procedure_ids=(REAL_PROCEDURE_ID,),
        evidence_status=EvidenceStatus.ADEQUATE.value,
        scenario_id="T-CLOUD-RUNNER",
    )
    defaults.update(overrides)
    return LLMRankingInput(**defaults)


def _valid_response(
    requires_review: bool = False,
    evidence: tuple[str, ...] = (REAL_EVIDENCE_ID,),
    procedures: tuple[str, ...] = (REAL_PROCEDURE_ID,),
    fault: str = FAULT_ID,
    ranked: tuple[tuple[str, int], ...] = (),
) -> str:
    hypotheses = ranked or ((fault, 1),)
    return json.dumps({
        "ranked_hypotheses": [{
            "fault_id": f,
            "rank": r,
            "confidence": max(0.1, 0.9 - 0.1 * r),
            "justification": "consistent with detected anomalies",
            "affected_component": "ADCS gyro assembly",
            "causal_chain": ["SEU", "rate bias", "safe mode"],
        } for f, r in hypotheses],
        "reasoning_summary": "stub cloud response for contract tests",
        "supporting_evidence_ids": list(evidence),
        "contradicting_evidence_ids": [],
        "selected_procedure_ids": list(procedures),
        "uncertainty": "",
        "requires_human_review": requires_review,
    })


def _runner(provider: FakeGeminiProvider) -> CloudBranchRunner:
    return CloudBranchRunner(provider=provider, max_retries=1)


# ---------------------------------------------------------------------------
# A-B: success and valid JSON
# ---------------------------------------------------------------------------

class TestSuccessPath:
    def test_A_successful_cloud_inference(self):
        provider = FakeGeminiProvider([_valid_response()])
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.ACCEPT
        assert result.is_usable
        assert result.validated_output is not None
        assert (
            result.validated_output.ranked_hypotheses[0].fault_id == FAULT_ID
        )
        assert RoutingReason.VALID_CLOUD_RESULT in result.reason_codes

    def test_B_valid_gemini_json_reaches_guardrails(self):
        provider = FakeGeminiProvider([_valid_response()])
        result = _runner(provider).run(_ranking_input())
        assert result.guardrail_result is not None
        assert result.guardrail_result.is_valid is True
        assert result.guardrail_result.violations == ()


# ---------------------------------------------------------------------------
# C: malformed JSON — bounded repair retry
# ---------------------------------------------------------------------------

class TestStructuralFailures:
    def test_C_malformed_json_after_bounded_repair_retry(self):
        provider = FakeGeminiProvider(["not json at all", "still not json"])
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.FAILURE
        assert RoutingReason.INVALID_STRUCTURED_OUTPUT in result.reason_codes
        assert result.attempts == 2
        assert provider.calls == 2
        assert result.validated_output is None

    def test_echo_shaped_completion_not_retried(self):
        echo = '{"scenario_id": "1", "satellite_id": "SENTINEL-2", ' * 3
        provider = FakeGeminiProvider([echo])
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.FAILURE
        assert RoutingReason.PROMPT_ECHO_TRUNCATION in result.reason_codes
        assert result.attempts == 1
        assert provider.calls == 1


# ---------------------------------------------------------------------------
# D-F: guardrail violations (evidence / procedure / physics)
# ---------------------------------------------------------------------------

class TestGuardrailViolations:
    def test_D_invalid_evidence_id(self):
        provider = FakeGeminiProvider(
            [_valid_response(evidence=("EVID-FAKEFAKEFAKE",))],
        )
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.FAILURE
        assert RoutingReason.EVIDENCE_FAILURE in result.reason_codes
        assert ViolationType.NONEXISTENT_EVIDENCE in (
            result.guardrail_result.violation_types
        )
        assert result.validated_output is None  # never promoted to success

    def test_E_invalid_procedure_id(self):
        provider = FakeGeminiProvider(
            [_valid_response(procedures=("PROC-IMAGINARY",))],
        )
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.FAILURE
        assert RoutingReason.PROCEDURE_INVALID in result.reason_codes
        assert result.validated_output is None

    def test_F_physics_conflict(self):
        """Model ranks a physics-INVALIDATED fault above a valid candidate:
        deterministic physics wins on the cloud branch too."""
        hyp2 = HypothesisContext(
            hypothesis_id=f"HYP-{FAULT_ID_2}",
            fault_id=FAULT_ID_2,
            fault_name=FAULT_ID_2,
            subsystem="EPS",
            deterministic_rank=2,
            deterministic_score=0.4,
        )
        bundle = _ranking_input(
            hypotheses=(
                HypothesisContext(
                    hypothesis_id=f"HYP-{FAULT_ID}",
                    fault_id=FAULT_ID,
                    fault_name=FAULT_ID,
                    subsystem="ADCS",
                    deterministic_rank=1,
                    deterministic_score=0.9,
                ),
                hyp2,
            ),
            valid_fault_ids=(FAULT_ID, FAULT_ID_2),
            physics=PhysicsContext(
                hypotheses_examined=2, invalidated=(FAULT_ID,),
            ),
        )
        provider = FakeGeminiProvider([_valid_response(
            ranked=((FAULT_ID, 1), (FAULT_ID_2, 2)),
        )])
        result = _runner(provider).run(bundle)
        assert result.outcome is BranchOutcome.FAILURE
        assert RoutingReason.PHYSICS_CONFLICT in result.reason_codes
        assert ViolationType.PHYSICS_OVERRIDE in (
            result.guardrail_result.violation_types
        )
        assert result.human_review_required is True  # review-forcing


# ---------------------------------------------------------------------------
# G-H: cloud availability failures
# ---------------------------------------------------------------------------

class TestCloudAvailability:
    def test_G_cloud_timeout(self):
        provider = FakeGeminiProvider(
            [ProviderError("google-genai request timed out")],
        )
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.FAILURE
        assert RoutingReason.CLOUD_TIMEOUT in result.reason_codes
        assert result.inference_performed is False

    def test_H_cloud_unavailable(self):
        provider = FakeGeminiProvider(
            [ProviderError("connection refused: service unavailable")],
        )
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.FAILURE
        assert RoutingReason.CLOUD_UNAVAILABLE in result.reason_codes
        assert result.inference_performed is False


# ---------------------------------------------------------------------------
# I-M: redaction gate integration + provider payload interception (Part 12)
# ---------------------------------------------------------------------------

class TestRedactionIntegration:
    def test_I_redaction_success_records_report(self):
        provider = FakeGeminiProvider([_valid_response()])
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.ACCEPT
        assert result.redaction_report is not None
        assert result.redaction_report["gate"] == (
            "phase23_step3_cloud_redaction"
        )
        assert result.redaction_report["gate_findings"] == []

    def test_J_redaction_failure_is_honest(self):
        bundle = _ranking_input(
            anomaly_summary="operator api_key visible in dump header",
        )
        provider = FakeGeminiProvider([_valid_response()])
        result = _runner(provider).run(bundle)
        assert result.outcome is BranchOutcome.FAILURE
        assert RoutingReason.REDACTION_GATE_FAILURE in result.reason_codes
        assert result.inference_performed is False
        assert result.human_review_required is True  # security escalation
        assert result.redaction_report is None  # gate refused transmission

    def test_K_gemini_not_called_when_redaction_fails(self):
        bundle = _ranking_input(
            anomaly_summary="operator api_key visible in dump header",
        )
        provider = FakeGeminiProvider([_valid_response()])
        _runner(provider).run(bundle)
        assert provider.calls == 0
        assert provider.captured_messages == []

    def test_L_raw_sensitive_value_cannot_reach_provider(self, monkeypatch):
        """Adversarial integration test: a raw CONFIDENTIAL value exists in
        the deterministic input; the captured provider payload must NOT
        contain it (full input → gate → prompt → provider path)."""
        base = _ranking_input().as_prompt_dict()

        def extended(self):
            return {**base, "operator_api_key": RAW_SECRET}

        monkeypatch.setattr(LLMRankingInput, "as_prompt_dict", extended)
        provider = FakeGeminiProvider([_valid_response()])
        result = _runner(provider).run(_ranking_input())

        assert result.outcome is BranchOutcome.ACCEPT
        assert provider.calls == 1
        assert RAW_SECRET not in provider.payload_text
        # nor in the structured capture
        for msgs in provider.captured_messages:
            for m in msgs:
                assert RAW_SECRET not in m["content"]

    def test_M_redacted_representation_reaches_provider(self, monkeypatch):
        base = _ranking_input().as_prompt_dict()

        def extended(self):
            return {**base, "operator_api_key": RAW_SECRET}

        monkeypatch.setattr(LLMRankingInput, "as_prompt_dict", extended)
        provider = FakeGeminiProvider([_valid_response()])
        _runner(provider).run(_ranking_input())
        assert "[REDACTED]" in provider.payload_text


# ---------------------------------------------------------------------------
# N: deterministic metadata intact
# ---------------------------------------------------------------------------

class TestDeterministicMetadata:
    def test_N_deterministic_fields_intact_in_result(self):
        provider = FakeGeminiProvider([_valid_response()])
        bundle = _ranking_input()
        result = _runner(provider).run(bundle)
        assert result.branch is Branch.CLOUD
        assert result.scenario_id == bundle.scenario_id
        assert result.evidence_status == bundle.evidence_status
        assert result.redaction_report is not None

    def test_N_transmitted_context_preserves_ranking_material(self):
        provider = FakeGeminiProvider([_valid_response()])
        _runner(provider).run(_ranking_input())
        payload = provider.payload_text
        assert FAULT_ID in payload
        assert REAL_EVIDENCE_ID in payload
        assert REAL_PROCEDURE_ID in payload
        assert EvidenceStatus.ADEQUATE.value in payload


# ---------------------------------------------------------------------------
# O-Q: authority boundaries
# ---------------------------------------------------------------------------

class TestAuthorityBoundaries:
    def test_O_physics_fields_cannot_be_injected(self):
        fields = {f.name for f in dataclasses.fields(BranchResult)}
        assert not any("physics" in f for f in fields)
        with pytest.raises(TypeError):
            BranchResult(
                branch=Branch.CLOUD,
                outcome=BranchOutcome.ACCEPT,
                physics_verdict="VALIDATED",  # not a contract field
            )

    def test_P_safety_fields_cannot_be_injected(self):
        fields = {f.name for f in dataclasses.fields(BranchResult)}
        assert not any("safety" in f for f in fields)
        with pytest.raises(TypeError):
            BranchResult(
                branch=Branch.CLOUD,
                outcome=BranchOutcome.ACCEPT,
                safety_status="SAFE",  # not a contract field
            )

    def test_Q_command_authorization_cannot_be_produced(self):
        fields = {f.name for f in dataclasses.fields(BranchResult)}
        assert not any("command" in f or "authoriz" in f for f in fields)
        provider = FakeGeminiProvider([_valid_response()])
        result = _runner(provider).run(_ranking_input())
        # a validated cloud result still carries no authorization power:
        assert type(result.validated_output).__name__ == "LLMRankingOutput"


# ---------------------------------------------------------------------------
# R-S: trust boundary between raw and validated output
# ---------------------------------------------------------------------------

class TestTrustBoundary:
    def test_R_raw_output_remains_untrusted_diagnostics(self):
        provider = FakeGeminiProvider(
            [_valid_response(evidence=("EVID-FAKEFAKEFAKE",))],
        )
        result = _runner(provider).run(_ranking_input())
        assert result.raw_text_head  # diagnostics captured
        assert len(result.raw_text_head) <= 500
        assert result.validated_output is None  # raw never promoted

    def test_S_validated_output_only_after_guardrails(self):
        # violation path: guardrail result exists, validated output absent
        provider = FakeGeminiProvider(
            [_valid_response(procedures=("PROC-IMAGINARY",))],
        )
        failing = _runner(provider).run(_ranking_input())
        assert failing.validated_output is None
        assert failing.guardrail_result is not None
        # clean path: both present, zero violations
        provider = FakeGeminiProvider([_valid_response()])
        passing = _runner(provider).run(_ranking_input())
        assert passing.validated_output is not None
        assert passing.guardrail_result.violations == ()


# ---------------------------------------------------------------------------
# T: human review monotonicity
# ---------------------------------------------------------------------------

class TestHumanReview:
    def test_T_pre_existing_review_is_never_downgraded(self):
        provider = FakeGeminiProvider([_valid_response()])
        result = _runner(provider).run(
            _ranking_input(), review_already_required=True,
        )
        assert result.outcome is BranchOutcome.ACCEPT
        assert result.human_review_required is True

    def test_T_model_review_flag_propagates(self):
        provider = FakeGeminiProvider([_valid_response(requires_review=True)])
        result = _runner(provider).run(_ranking_input())
        assert result.human_review_required is True


# ---------------------------------------------------------------------------
# U-V: identity and latency
# ---------------------------------------------------------------------------

class TestIdentityAndLatency:
    def test_U_provider_model_identity_recorded(self):
        provider = FakeGeminiProvider([_valid_response()])
        result = _runner(provider).run(_ranking_input())
        assert result.provider_name == provider.provider_name == "gemini"
        assert result.model_name == provider.model_name

    def test_U_identity_read_from_provider_not_hardcoded(self):
        provider = FakeGeminiProvider(
            [_valid_response()], model_name="gemini-2.5-flash-custom",
        )
        result = _runner(provider).run(_ranking_input())
        assert result.model_name == "gemini-2.5-flash-custom"

    def test_V_latency_recorded(self):
        provider = FakeGeminiProvider([_valid_response()])
        result = _runner(provider).run(_ranking_input())
        assert result.elapsed_ms > 0.0
        assert result.attempts == 1


# ---------------------------------------------------------------------------
# W: bounded retry semantics match the existing convention
# ---------------------------------------------------------------------------

class TestRetrySemantics:
    def test_W_repair_retry_then_success(self):
        provider = FakeGeminiProvider(["broken json", _valid_response()])
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.ACCEPT
        assert result.attempts == 2
        assert provider.calls == 2

    def test_W_negative_retry_budget_rejected(self):
        provider = FakeGeminiProvider([_valid_response()])
        with pytest.raises(ValueError):
            CloudBranchRunner(provider=provider, max_retries=-1)


# ---------------------------------------------------------------------------
# X: no network in unit tests
# ---------------------------------------------------------------------------

class TestNoNetwork:
    def test_X_full_run_without_any_network(self, monkeypatch):
        def _deny(*args, **kwargs):
            raise AssertionError("network call attempted in unit test")

        monkeypatch.setattr(urllib.request, "urlopen", _deny)
        monkeypatch.setattr(socket, "create_connection", _deny)
        provider = FakeGeminiProvider([_valid_response()])
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.ACCEPT


# ---------------------------------------------------------------------------
# Y: ROUTER_ENABLED=false ⇒ production path unchanged
# ---------------------------------------------------------------------------

class TestRouterDisabled:
    def test_Y_router_flag_is_off(self):
        assert router_enabled() is False

    def test_Y_cloud_runner_not_wired_into_production_agent(self):
        import app.agent.agent as agent_mod

        src = inspect.getsource(agent_mod)
        assert "CloudBranchRunner" not in src
        assert "cloud_branch" not in src
        assert "BranchPolicy" not in src
        assert "LocalBranchRunner" not in src

    def test_Y_redaction_report_defaults_none_in_contract(self):
        """Branches without external transmission carry no redaction data."""
        from app.llm.local_branch import LocalBranchRunner

        class FakeLocal(FakeGeminiProvider):
            @property
            def provider_name(self) -> str:
                return "local"

        provider = FakeLocal([_valid_response()])
        result = LocalBranchRunner(provider=provider).run(_ranking_input())
        assert result.redaction_report is None
