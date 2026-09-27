"""SENTINEL Phase 23 Step 2 — LocalBranchRunner adapter tests (A-R).

All tests use a fake LocalProvider: no Ollama, no Gemini, no network.
The runner is an ADAPTER around the existing constrained pipeline, so
these tests pin honest failure mapping (Phase 21 distribution), the
raw/validated trust boundary, and authority non-injection.
"""

from __future__ import annotations

import dataclasses
import json
import socket
import time
import urllib.request

import pytest

from app.llm.local_branch import LocalBranchRunner, _is_prompt_echo
from app.llm.models import (
    EvidenceStatus,
    HypothesisContext,
    LLMRankingInput,
    ViolationType,
)
from app.llm.provider import LLMProvider, ProviderError
from app.llm.router_contract import (
    Branch,
    BranchOutcome,
    BranchResult,
    RoutingReason,
)

REAL_EVIDENCE_ID = "EVID-AAAAAAAAAAAA"
REAL_PROCEDURE_ID = "PROC-ADCS-GYRO-RECOVERY"
FAULT_ID = "ADCS_GYRO_SEU"


# ---------------------------------------------------------------------------
# fakes + fixtures
# ---------------------------------------------------------------------------

class FakeLocalProvider(LLMProvider):
    """Scripted provider: returns canned completions or raises queued errors."""

    def __init__(self, outcomes, model_name: str = "phi3:mini-test"):
        self._outcomes = list(outcomes)
        self._model_name = model_name
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "local"

    @property
    def model_name(self) -> str:
        return self._model_name

    def call(self, messages):
        self.calls += 1
        if not self._outcomes:
            raise ProviderError("FakeLocalProvider script exhausted")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _ranking_input() -> LLMRankingInput:
    hyp = HypothesisContext(
        hypothesis_id=f"HYP-{FAULT_ID}",
        fault_id=FAULT_ID,
        fault_name=FAULT_ID,
        subsystem="ADCS",
        deterministic_rank=1,
        deterministic_score=0.9,
        supporting_evidence=(REAL_EVIDENCE_ID,),
    )
    return LLMRankingInput(
        hypotheses=(hyp,),
        valid_fault_ids=(FAULT_ID,),
        valid_procedure_ids=(REAL_PROCEDURE_ID,),
        evidence_status=EvidenceStatus.ADEQUATE.value,
        scenario_id="T-RUNNER",
    )


def _valid_response(
    requires_review: bool = False,
    evidence: tuple[str, ...] = (REAL_EVIDENCE_ID,),
    procedures: tuple[str, ...] = (REAL_PROCEDURE_ID,),
    fault: str = FAULT_ID,
) -> str:
    return json.dumps({
        "ranked_hypotheses": [{
            "fault_id": fault,
            "rank": 1,
            "confidence": 0.8,
            "justification": "gyro SEU consistent with detected anomalies",
            "affected_component": "ADCS gyro assembly",
            "causal_chain": ["SEU", "rate bias", "safe mode"],
        }],
        "reasoning_summary": "stub runner response for contract tests",
        "supporting_evidence_ids": list(evidence),
        "contradicting_evidence_ids": [],
        "selected_procedure_ids": list(procedures),
        "uncertainty": "",
        "requires_human_review": requires_review,
    })


def _runner(provider: FakeLocalProvider) -> LocalBranchRunner:
    return LocalBranchRunner(provider=provider, max_retries=1)


# ---------------------------------------------------------------------------
# A-B: success and valid JSON
# ---------------------------------------------------------------------------

class TestSuccessPath:
    def test_A_successful_phi3_result(self):
        provider = FakeLocalProvider([_valid_response()])
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.ACCEPT
        assert result.is_usable
        assert result.validated_output is not None
        assert (
            result.validated_output.ranked_hypotheses[0].fault_id == FAULT_ID
        )
        assert RoutingReason.VALID_LOCAL_RESULT in result.reason_codes

    def test_B_valid_json_reaches_guardrails(self):
        provider = FakeLocalProvider([_valid_response()])
        result = _runner(provider).run(_ranking_input())
        assert result.guardrail_result is not None
        assert result.guardrail_result.is_valid is True
        assert result.guardrail_result.violations == ()


# ---------------------------------------------------------------------------
# C-E: malformed / echo / truncation (Phase 21 failure modes)
# ---------------------------------------------------------------------------

class TestStructuralFailures:
    def test_C_malformed_json_after_bounded_repair_retry(self):
        provider = FakeLocalProvider(["not json at all", "still not json"])
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.FAILURE
        assert RoutingReason.INVALID_STRUCTURED_OUTPUT in result.reason_codes
        assert result.attempts == 2
        assert provider.calls == 2  # bounded: initial + one repair retry
        assert result.validated_output is None

    def test_D_prompt_echo_not_retried(self):
        echo = '{"scenario_id": "1", "satellite_id": "SENTINEL-2", ' * 3
        provider = FakeLocalProvider([echo])
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.FAILURE
        assert RoutingReason.PROMPT_ECHO_TRUNCATION in result.reason_codes
        assert result.attempts == 1  # S1-type is never retried in-process
        assert provider.calls == 1
        assert result.inference_performed is True

    def test_E_output_token_exhaustion_blob(self):
        blob = "the spacecraft telemetry window shows " * 80  # > 2000 chars
        provider = FakeLocalProvider([blob])
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.FAILURE
        assert RoutingReason.PROMPT_ECHO_TRUNCATION in result.reason_codes
        assert result.attempts == 1

    def test_echo_classifier_never_flags_parseable_json(self):
        assert _is_prompt_echo(_valid_response()) is False
        assert _is_prompt_echo("") is False


# ---------------------------------------------------------------------------
# F-G: guardrail violations (evidence / procedure hallucination)
# ---------------------------------------------------------------------------

class TestGuardrailViolations:
    def test_F_invalid_evidence_id(self):
        provider = FakeLocalProvider(
            [_valid_response(evidence=("EVID-FAKEFAKEFAKE",))],
        )
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.FAILURE
        assert RoutingReason.EVIDENCE_FAILURE in result.reason_codes
        assert ViolationType.NONEXISTENT_EVIDENCE in (
            result.guardrail_result.violation_types
        )
        assert result.validated_output is None  # never promoted to success

    def test_G_invalid_procedure_id(self):
        provider = FakeLocalProvider(
            [_valid_response(procedures=("PROC-IMAGINARY",))],
        )
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.FAILURE
        assert RoutingReason.PROCEDURE_INVALID in result.reason_codes
        assert ViolationType.INVALID_PROCEDURE in (
            result.guardrail_result.violation_types
        )
        assert result.validated_output is None


# ---------------------------------------------------------------------------
# H-I: timeout / unavailable
# ---------------------------------------------------------------------------

class TestInfrastructureFailures:
    def test_H_timeout(self):
        provider = FakeLocalProvider(
            [ProviderError("Local LLM call failed (Timeout): request timed out")],
        )
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.FAILURE
        assert RoutingReason.LOCAL_TIMEOUT in result.reason_codes
        assert result.inference_performed is False

    def test_I_local_provider_unavailable(self):
        provider = FakeLocalProvider(
            [ProviderError(
                "Local LLM call failed (ConnectionRefusedError): "
                "connection refused")],
        )
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.FAILURE
        assert RoutingReason.LOCAL_UNAVAILABLE in result.reason_codes
        assert result.inference_performed is False


# ---------------------------------------------------------------------------
# J: human-review propagation (monotone)
# ---------------------------------------------------------------------------

class TestHumanReviewPropagation:
    def test_J_model_raised_review_survives_accept(self):
        provider = FakeLocalProvider([_valid_response(requires_review=True)])
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.ACCEPT
        assert result.human_review_required is True

    def test_J_pre_existing_review_survives_failure(self):
        provider = FakeLocalProvider(["garbage", "garbage"])
        result = _runner(provider).run(
            _ranking_input(), review_already_required=True,
        )
        assert result.outcome is BranchOutcome.FAILURE
        assert result.human_review_required is True

    def test_J_clean_accept_has_no_forced_review(self):
        provider = FakeLocalProvider([_valid_response()])
        result = _runner(provider).run(_ranking_input())
        assert result.human_review_required is False


# ---------------------------------------------------------------------------
# K-L: trust boundary — raw vs validated
# ---------------------------------------------------------------------------

class TestTrustBoundary:
    def test_K_raw_text_remains_untrusted(self):
        provider = FakeLocalProvider(
            [_valid_response(evidence=("EVID-FAKEFAKEFAKE",))],
        )
        result = _runner(provider).run(_ranking_input())
        # The fabricated ID survives ONLY as untrusted diagnostics.
        assert "EVID-FAKEFAKEFAKE" in result.raw_text_head
        assert result.validated_output is None

    def test_K_raw_head_is_truncated_diagnostics(self):
        blob = "z" * 5000  # parseable? no -> echo class; still truncated
        provider = FakeLocalProvider([blob])
        result = _runner(provider).run(_ranking_input())
        assert len(result.raw_text_head) <= 500

    def test_L_validated_output_only_after_guardrails(self):
        # Violating output: guardrails ran -> no validated_output.
        bad = FakeLocalProvider(
            [_valid_response(evidence=("EVID-FAKEFAKEFAKE",))],
        )
        failing = _runner(bad).run(_ranking_input())
        assert failing.guardrail_result is not None
        assert failing.validated_output is None
        # Clean output: guardrails passed -> validated_output present.
        good = FakeLocalProvider([_valid_response()])
        accepted = _runner(good).run(_ranking_input())
        assert accepted.guardrail_result.is_valid
        assert accepted.validated_output is not None


# ---------------------------------------------------------------------------
# M-O: authority non-injection
# ---------------------------------------------------------------------------

class TestAuthorityNonInjection:
    def test_M_physics_fields_cannot_be_injected(self):
        with pytest.raises(TypeError):
            BranchResult(
                branch=Branch.LOCAL, outcome=BranchOutcome.ACCEPT,
                physics_verdict="VALIDATED",  # not a contract field
            )

    def test_N_safety_fields_cannot_be_injected(self):
        with pytest.raises(TypeError):
            BranchResult(
                branch=Branch.LOCAL, outcome=BranchOutcome.ACCEPT,
                safety_status="VALIDATED",  # not a contract field
            )

    def test_O_runner_cannot_produce_command_authorization(self):
        names = {f.name for f in dataclasses.fields(BranchResult)}
        assert not any("command" in n for n in names)
        provider = FakeLocalProvider([_valid_response()])
        result = _runner(provider).run(_ranking_input())
        assert not any("command" in n for n in vars(result))


# ---------------------------------------------------------------------------
# P-Q: latency + identity metadata
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_P_latency_recorded_for_real(self):
        class SlowProvider(FakeLocalProvider):
            def call(self, messages):
                time.sleep(0.02)
                return super().call(messages)

        provider = SlowProvider([_valid_response()])
        result = _runner(provider).run(_ranking_input())
        assert result.elapsed_ms >= 20.0  # measured, never fabricated
        assert result.attempts == 1

    def test_Q_provider_and_model_identity_from_configuration(self):
        provider = FakeLocalProvider(
            [_valid_response()], model_name="phi3:mini-configured",
        )
        result = _runner(provider).run(_ranking_input())
        assert result.branch is Branch.LOCAL
        assert result.provider_name == "local"
        assert result.model_name == "phi3:mini-configured"
        assert result.evidence_status == EvidenceStatus.ADEQUATE.value
        assert result.scenario_id == "T-RUNNER"


# ---------------------------------------------------------------------------
# R: zero network calls outside the provider
# ---------------------------------------------------------------------------

class TestNetworkIsolation:
    def test_R_no_network_calls_outside_the_provider(self, monkeypatch):
        def _deny(*args, **kwargs):
            raise AssertionError("unexpected network call from runner")

        monkeypatch.setattr(urllib.request, "urlopen", _deny)
        monkeypatch.setattr(socket, "create_connection", _deny)

        provider = FakeLocalProvider([_valid_response()])
        result = _runner(provider).run(_ranking_input())
        assert result.outcome is BranchOutcome.ACCEPT
        assert provider.calls == 1  # the ONLY outbound interaction

    def test_runner_rejects_infinite_retry_configuration(self):
        provider = FakeLocalProvider([])
        with pytest.raises(ValueError):
            LocalBranchRunner(provider=provider, max_retries=-1)
