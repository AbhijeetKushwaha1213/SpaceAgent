"""SENTINEL Phase 23 Step 3 — mandatory cloud redaction gate tests.

Gate-centric security tests for ``redact_ranking_input_for_cloud`` and the
fail-closed contract of the cloud branch.  No network, no Gemini API key,
no provider calls in this file — the gate must decide BEFORE any provider
could ever be involved.

Covers the mandate's parts:

    Part 4   fail-closed redaction (no Gemini call on gate failure)
    Part 5   minimum necessary disclosure (physics/evidence preserved)
    Part 8   redaction never alters authority (allowlists, physics, safety)
    Part 12  redacted representation exists where required
    Part 13  no bypass path around the gate through the runner API
    Part 14  privacy enforced before the network boundary, never by the model
"""

from __future__ import annotations

import inspect

import pytest

import app.llm
from app.llm import cloud_branch
from app.llm.cloud_branch import (
    CloudBranchRunner,
    CloudRedactionError,
    redact_ranking_input_for_cloud,
)
from app.llm.models import (
    EvidenceStatus,
    HypothesisContext,
    LLMRankingInput,
    PhysicsContext,
    ResidualContext,
    SpacecraftStateContext,
)
from app.llm.provider import ProviderError
from app.llm.router_contract import BranchOutcome
from app.security.config import SecurityConfig

REAL_EVIDENCE_ID = "EVID-AAAAAAAAAAAA"
REAL_PROCEDURE_ID = "PROC-ADCS-GYRO-RECOVERY"
FAULT_ID = "ADCS_GYRO_SEU"


def _ranking_input(**overrides) -> LLMRankingInput:
    hyp = HypothesisContext(
        hypothesis_id=f"HYP-{FAULT_ID}",
        fault_id=FAULT_ID,
        fault_name=FAULT_ID,
        subsystem="ADCS",
        deterministic_rank=1,
        deterministic_score=0.9,
        supporting_evidence=(REAL_EVIDENCE_ID,),
        physics_status="UNCERTAIN",
    )
    state = SpacecraftStateContext(
        state_summary="attitude stable, power nominal",
        residuals=(ResidualContext(
            channel="GYRO_A_RATE", unit="deg/s", status="EXCEEDED",
            observed=0.52, predicted=0.01, residual=0.51,
            tolerance=0.05, exceedance=10.2,
        ),),
    )
    defaults = dict(
        anomaly_summary="gyro rate excursion preceding safe mode",
        hypotheses=(hyp,),
        physics=PhysicsContext(
            hypotheses_examined=1, uncertain=(FAULT_ID,),
            summary="no deterministic invalidation",
        ),
        spacecraft_state=state,
        valid_fault_ids=(FAULT_ID,),
        valid_procedure_ids=(REAL_PROCEDURE_ID,),
        evidence_status=EvidenceStatus.ADEQUATE.value,
        scenario_id="T-CLOUD-GATE",
        safe_mode_trigger="SBM_GYRO_EXCURSION",
    )
    defaults.update(overrides)
    return LLMRankingInput(**defaults)


# ---------------------------------------------------------------------------
# I — redaction success on a clean deterministic bundle
# ---------------------------------------------------------------------------

class TestRedactionGateSuccess:
    def test_I_clean_bundle_passes_with_audit_report(self):
        gate = redact_ranking_input_for_cloud(_ranking_input())
        assert gate.prompt_dict["scenario_id"] == "T-CLOUD-GATE"
        report = gate.report
        assert report["gate"] == "phase23_step3_cloud_redaction"
        assert report["gate_findings"] == []
        assert "classifications" in report          # existing framework
        assert "confidential_fields_redacted" in report
        assert report["redaction_applied"] is False  # nothing to redact
        assert report["target"] == "constrained ranking prompt bundle"

    def test_N_deterministic_fields_remain_intact(self):
        """Minimum necessary disclosure: everything needed for ranking,
        physics interpretation, evidence grounding and procedure selection
        survives the gate unchanged."""
        original = _ranking_input()
        gate = redact_ranking_input_for_cloud(original)
        out = gate.prompt_dict
        # hypothesis ranking material
        assert out["valid_fault_ids"] == [FAULT_ID]
        assert out["hypotheses"][0]["fault_id"] == FAULT_ID
        assert out["hypotheses"][0]["deterministic_score"] == 0.9
        assert out["hypotheses"][0]["supporting_evidence"] == [REAL_EVIDENCE_ID]
        # physics interpretation (quantitative — preserved per Part 5)
        assert out["physics"]["uncertain"] == [FAULT_ID]
        residuals = out["spacecraft_state"]["residuals"]
        assert residuals[0]["observed"] == 0.52
        assert residuals[0]["residual"] == 0.51
        assert residuals[0]["exceedance"] == 10.2
        # procedure selection
        assert out["valid_procedure_ids"] == [REAL_PROCEDURE_ID]
        # evidence state
        assert out["evidence_status"] == EvidenceStatus.ADEQUATE.value

    def test_gate_never_mutates_original_bundle(self):
        original = _ranking_input()
        before = original.as_prompt_dict()
        redact_ranking_input_for_cloud(original)
        assert original.as_prompt_dict() == before  # frozen + deep copy


# ---------------------------------------------------------------------------
# Part 8 — redaction is a privacy transformation, not an authority change
# ---------------------------------------------------------------------------

class TestRedactionNeverAltersAuthority:
    def test_allowlists_physics_safety_unchanged(self):
        gate = redact_ranking_input_for_cloud(_ranking_input())
        out = gate.prompt_dict
        # no evidence created, destroyed or converted
        assert out["hypotheses"][0]["supporting_evidence"] == [REAL_EVIDENCE_ID]
        # deterministic hypothesis validity untouched
        assert out["hypotheses"][0]["physics_status"] == "UNCERTAIN"
        assert out["physics"]["invalidated"] == []
        # safety context is notes-only proposal context, unmodified
        assert out["safety_constraints"] == {"notes": ""}

    def test_gate_has_no_authority_side_effects(self):
        """The gate returns data; it exposes no command/safety/physics
        mutation surface."""
        api = [n for n in dir(cloud_branch.CloudRedactionResult)
               if not n.startswith("_")]
        assert set(api) <= {"prompt_dict", "report"}


# ---------------------------------------------------------------------------
# J — fail closed: the gate refuses to certify unsafe payloads
# ---------------------------------------------------------------------------

class TestRedactionGateFailClosed:
    def test_J_confidential_substring_in_free_text(self):
        """Crash-dump-derived summaries must not carry credential material."""
        bad = _ranking_input(
            anomaly_summary="operator api_key observed in dump header",
        )
        with pytest.raises(CloudRedactionError) as exc:
            redact_ranking_input_for_cloud(bad)
        assert "cannot prove payload cloud-safe" in str(exc.value)

    def test_J_secret_shape_anywhere_in_bundle(self):
        """Existing secret patterns must never be certifiable as cloud-safe,
        even in a field no free-text scan targets."""
        bad = _ranking_input(
            safe_mode_trigger="AIzaSyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        )
        with pytest.raises(CloudRedactionError):
            redact_ranking_input_for_cloud(bad)

    def test_J_environment_derived_path_leak(self):
        bad = _ranking_input(
            spacecraft_state=SpacecraftStateContext(
                state_summary="dump recovered from /Users/operator/crash.json",
            ),
        )
        with pytest.raises(CloudRedactionError) as exc:
            redact_ranking_input_for_cloud(bad)
        assert "filesystem path" in str(exc.value)

    def test_J_redaction_exception_fails_closed(self, monkeypatch):
        def boom(payload, config=None):
            raise RuntimeError("redactor exploded")

        monkeypatch.setattr(cloud_branch, "apply_cloud_redaction", boom)
        with pytest.raises(CloudRedactionError) as exc:
            redact_ranking_input_for_cloud(_ranking_input())
        assert "redaction failed" in str(exc.value)

    def test_J_malformed_serialization_fails_closed(self, monkeypatch):
        monkeypatch.setattr(
            LLMRankingInput, "as_prompt_dict", lambda self: {},
        )
        with pytest.raises(CloudRedactionError) as exc:
            redact_ranking_input_for_cloud(_ranking_input())
        assert "malformed" in str(exc.value)

    def test_J_malformed_redactor_output_fails_closed(self, monkeypatch):
        monkeypatch.setattr(
            cloud_branch, "apply_cloud_redaction",
            lambda payload, config=None: (None, {}),
        )
        with pytest.raises(CloudRedactionError) as exc:
            redact_ranking_input_for_cloud(_ranking_input())
        assert "malformed output" in str(exc.value)


# ---------------------------------------------------------------------------
# Part 12 — the redacted representation, not the raw value
# ---------------------------------------------------------------------------

class TestRedactedRepresentation:
    def test_confidential_field_becomes_redaction_marker(self, monkeypatch):
        """A CONFIDENTIAL-classified field (repository classification:
        key/secret/token/auth/password names) must reach the cloud dict as
        [REDACTED], never as its raw value."""
        raw_secret = "RAW_OPERATOR_SECRET_9f8e7d"
        base = _ranking_input().as_prompt_dict()

        def extended(self):
            return {**base, "operator_api_key": raw_secret}

        monkeypatch.setattr(LLMRankingInput, "as_prompt_dict", extended)
        gate = redact_ranking_input_for_cloud(_ranking_input())
        assert gate.prompt_dict["operator_api_key"] == "[REDACTED]"
        assert raw_secret not in gate.prompt_dict.values()
        assert gate.report["confidential_fields_redacted"] == [
            "operator_api_key"
        ]


# ---------------------------------------------------------------------------
# Part 13 — bypass is impossible through the normal runner API
# ---------------------------------------------------------------------------

class TestNoBypassThroughRunnerAPI:
    def test_runner_exposes_no_prebuilt_message_entrypoint(self):
        """The runner owns redact → prompt → call.  No public method may
        accept prebuilt messages or a prebuilt prompt (which could skip the
        gate)."""
        public = [
            name for name, member in inspect.getmembers(CloudBranchRunner)
            if not name.startswith("_") and callable(member)
        ]
        assert public == ["run"]

    def test_prompt_builder_is_module_private_and_unexported(self):
        assert not hasattr(app.llm, "_build_cloud_messages")
        assert "_build_cloud_messages" not in getattr(app.llm, "__all__", [])
        assert callable(cloud_branch._build_cloud_messages)  # private only

    def test_run_always_passes_through_gate_first(self, monkeypatch):
        """run() owns the sequence gate → prompt → call: the gate executes
        exactly once before the provider is ever attempted."""
        calls = {"gate": 0, "provider": 0}
        real_gate = cloud_branch.redact_ranking_input_for_cloud

        def counting_gate(ranking_input, config=None):
            calls["gate"] += 1
            return real_gate(ranking_input, config)

        monkeypatch.setattr(
            cloud_branch, "redact_ranking_input_for_cloud", counting_gate,
        )

        class HaltProvider:
            provider_name = "gemini"
            model_name = "gemini-2.5-flash"

            def call(self, messages):
                calls["provider"] += 1
                raise ProviderError("halt after gate")

        result = CloudBranchRunner(provider=HaltProvider()).run(
            _ranking_input(),
        )
        assert calls["gate"] == 1
        assert calls["provider"] == 1  # only after the gate passed
        assert result.outcome is BranchOutcome.FAILURE
        assert result.inference_performed is False

    def test_security_config_override_cannot_disable_gate(self):
        """Even an explicitly empty security config still runs the full
        fail-closed verification (gate != config toggle)."""
        gate = redact_ranking_input_for_cloud(
            _ranking_input(), config=SecurityConfig(),
        )
        assert gate.report["gate_findings"] == []
        bad = _ranking_input(
            anomaly_summary="dump exposes the service token",
        )
        with pytest.raises(CloudRedactionError):
            redact_ranking_input_for_cloud(bad, config=SecurityConfig())


# ---------------------------------------------------------------------------
# Part 14 — privacy enforced before the network boundary
# ---------------------------------------------------------------------------

class TestPrivacyNotModelDependent:
    def test_gate_never_consults_provider_or_model(self):
        """The gate's signature carries no provider/model argument; the
        decision is taken before any model could be asked."""
        sig = inspect.signature(redact_ranking_input_for_cloud)
        params = set(sig.parameters)
        assert params == {"ranking_input", "config"}

    def test_gate_source_contains_no_model_instructions(self):
        src = inspect.getsource(cloud_branch.redact_ranking_input_for_cloud)
        for forbidden in ("system", "prompt instruction", "refuse"):
            assert forbidden not in src.lower()
