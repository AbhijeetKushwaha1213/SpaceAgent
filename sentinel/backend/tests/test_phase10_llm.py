"""test_phase10_llm.py

Phase 10 — Constrained LLM Ranking and Explanation tests (unittest format).

Tests the full Phase 10 pipeline:
  - LLM provider abstraction (Stub, factory)
  - Constrained ranking input assembly
  - Guardrail validation (hypotheses, evidence, procedures, commands, physics, certainty)
  - Explainer functions
  - SSE event sequence (no fake THOUGHT events)
  - Provider failure → deterministic fallback

Run:
    cd sentinel/backend && python3 tests/test_phase10_llm.py
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

# Ensure backend/ root is on sys.path for standalone execution
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.llm.models import (
    GuardrailResult,
    GuardrailViolation,
    HypothesisContext,
    LLMRankingInput,
    LLMRankingOutput,
    PhysicsContext,
    ProcedureContext,
    RankedHypothesis,
    SafetyContext,
    SpacecraftStateContext,
    ViolationType,
)
from app.llm.provider import (
    GeminiProvider,
    LLMProvider,
    LocalProvider,
    ProviderConfig,
    ProviderError,
    StubProvider,
    create_provider,
)
from app.llm.ranker import (
    _extract_json,
    build_constrained_prompt,
    build_ranking_input,
    convert_to_sentinel_output,
    run_constrained_ranking,
    validate_ranking_output,
)
from app.llm.explainer import (
    explain_evidence,
    explain_physics,
    explain_ranking,
    explain_uncertainty,
    identify_contradictions,
)


def _make_ranking_input(
    fault_ids=("ADCS_GYRO_SEU", "EPS_SOLAR_UNDERVOLT", "OBC_WATCHDOG_OVERFLOW"),
    evidence_ids=("E1", "E2", "E3"),
    procedure_ids=("PROC-001", "PROC-002"),
    physics_validated=("ADCS_GYRO_SEU",),
    physics_invalidated=(),
    physics_uncertain=("EPS_SOLAR_UNDERVOLT", "OBC_WATCHDOG_OVERFLOW"),
) -> LLMRankingInput:
    hypotheses = []
    for i, fid in enumerate(fault_ids):
        status = "UNCERTAIN"
        if fid in physics_validated:
            status = "VALID"
        elif fid in physics_invalidated:
            status = "INVALID"
        hypotheses.append(HypothesisContext(
            hypothesis_id=f"H{i+1}",
            fault_id=fid,
            fault_name=fid.replace("_", " ").title(),
            subsystem=fid.split("_")[0],
            deterministic_rank=i + 1,
            deterministic_score=round(0.9 - i * 0.25, 2),
            supporting_evidence=tuple(evidence_ids[:2]),
            contradicting_evidence=tuple(evidence_ids[2:]),
            physics_status=status,
        ))

    return LLMRankingInput(
        anomaly_summary="3 anomalies detected",
        anomalous_channels=("GYRO_A_RATE", "SOLAR_CURRENT"),
        anomaly_count=3,
        hypotheses=tuple(hypotheses),
        valid_fault_ids=tuple(fault_ids),
        physics=PhysicsContext(
            hypotheses_examined=len(fault_ids),
            validated=tuple(physics_validated),
            invalidated=tuple(physics_invalidated),
            uncertain=tuple(physics_uncertain),
            summary="Physics examined 3 hypotheses.",
        ),
        spacecraft_state=SpacecraftStateContext(
            state_summary="Spacecraft in safe mode",
            anomalous_channels=("GYRO_A_RATE",),
            residual_summary="Residuals computed",
        ),
        procedures=tuple(
            ProcedureContext(
                procedure_id=pid,
                title=f"Procedure {pid}",
                subsystem="ADCS",
                fault_class="ADCS_GYRO_SEU",
                source_type="FALLBACK_KB",
                citation_id=f"CIT-{pid}",
                step_count=3,
                risk="MEDIUM",
            )
            for pid in procedure_ids
        ),
        valid_procedure_ids=tuple(procedure_ids),
        safety=SafetyContext(notes="LLM may not generate commands"),
        scenario_id="TEST_001",
        fault_type="ADCS_GYRO_SEU",
        safe_mode_trigger="Gyro rate exceeded threshold",
        # Phase 21: this fixture carries supporting evidence on a healthy
        # window, i.e. the ADEQUATE evidence state. The INSUFFICIENT default
        # would (correctly) forbid every positive claim this fixture makes.
        evidence_status="ADEQUATE",
    )


def _make_valid_output(
    fault_ids=("ADCS_GYRO_SEU", "EPS_SOLAR_UNDERVOLT", "OBC_WATCHDOG_OVERFLOW"),
    evidence_ids=("E1", "E2"),
    procedure_ids=("PROC-001",),
) -> LLMRankingOutput:
    ranked = []
    for i, fid in enumerate(fault_ids):
        ranked.append(RankedHypothesis(
            fault_id=fid,
            rank=i + 1,
            confidence=round(0.85 - i * 0.2, 2),
            justification=f"{fid} is the most likely cause.",
            affected_component=fid.split("_")[0],
            causal_chain=(f"Step {j+1}" for j in range(3)),
        ))

    return LLMRankingOutput(
        ranked_hypotheses=tuple(ranked),
        reasoning_summary="Analysis based on telemetry anomalies.",
        supporting_evidence_ids=tuple(evidence_ids),
        contradicting_evidence_ids=("E3",),
        selected_procedure_ids=tuple(procedure_ids),
        uncertainty="MEDIUM",
        requires_human_review=True,
    )


def _make_valid_json_str(**overrides) -> str:
    base = {
        "ranked_hypotheses": [
            {
                "fault_id": "ADCS_GYRO_SEU",
                "rank": 1,
                "confidence": 0.85,
                "justification": "Gyro anomaly is consistent with SEU.",
                "affected_component": "GYRO_A",
                "causal_chain": ["SEU corrupted registers", "Rate went NaN"],
            },
            {
                "fault_id": "EPS_SOLAR_UNDERVOLT",
                "rank": 2,
                "confidence": 0.65,
                "justification": "Solar current drop observed.",
                "affected_component": "SOLAR_ARRAY",
                "causal_chain": ["Undervoltage detected"],
            },
            {
                "fault_id": "OBC_WATCHDOG_OVERFLOW",
                "rank": 3,
                "confidence": 0.25,
                "justification": "Watchdog counter elevated.",
                "affected_component": "OBC",
                "causal_chain": ["Counter overflow"],
            },
        ],
        "reasoning_summary": "Analysis based on telemetry anomalies.",
        "supporting_evidence_ids": ["E1", "E2"],
        "contradicting_evidence_ids": ["E3"],
        "selected_procedure_ids": ["PROC-001"],
        "uncertainty": "MEDIUM",
        "requires_human_review": True,
    }
    base.update(overrides)
    return json.dumps(base)


class TestProviderAbstraction(unittest.TestCase):
    def test_stub_provider_returns_configured_response(self):
        stub = StubProvider(response='{"hello": "world"}', label="test")
        result = stub.call([{"role": "user", "content": "test"}])
        self.assertEqual(result, '{"hello": "world"}')
        self.assertEqual(stub.provider_name, "stub")
        self.assertIn("test", stub.model_name)
        self.assertFalse(stub.inference_performed)

    def test_stub_provider_empty_response_raises(self):
        stub = StubProvider(response="", label="empty")
        with self.assertRaises(ProviderError):
            stub.call([{"role": "user", "content": "test"}])

    def test_create_provider_base(self):
        provider = create_provider(mode="base", config=ProviderConfig())
        self.assertIsInstance(provider, GeminiProvider)
        self.assertEqual(provider.provider_name, "gemini")

    def test_create_provider_tuned(self):
        provider = create_provider(mode="tuned", config=ProviderConfig())
        self.assertIsInstance(provider, GeminiProvider)

    def test_create_provider_fallback(self):
        provider = create_provider(mode="fallback", config=ProviderConfig())
        self.assertIsInstance(provider, LocalProvider)
        self.assertEqual(provider.provider_name, "local")

    def test_create_provider_stub(self):
        provider = create_provider(
            mode="stub", stub_response='{"test": true}', stub_label="unit",
        )
        self.assertIsInstance(provider, StubProvider)

    def test_create_provider_unknown_raises(self):
        with self.assertRaises(ValueError):
            create_provider(mode="nonexistent")


class TestJsonExtraction(unittest.TestCase):
    def test_plain_json(self):
        result = _extract_json('{"a": 1}')
        self.assertEqual(result, {"a": 1})

    def test_json_in_code_fence(self):
        raw = '```json\n{"a": 1}\n```'
        result = _extract_json(raw)
        self.assertEqual(result, {"a": 1})

    def test_json_with_surrounding_text(self):
        raw = 'Here is my response:\n{"a": 1}\nDone.'
        result = _extract_json(raw)
        self.assertEqual(result, {"a": 1})

    def test_think_tags_stripped(self):
        raw = '<think>internal reasoning</think>\n{"a": 1}'
        result = _extract_json(raw)
        self.assertEqual(result, {"a": 1})

    def test_malformed_json_raises(self):
        with self.assertRaises(ValueError):
            _extract_json("this is not json at all")


class TestValidRanking(unittest.TestCase):
    def test_valid_output_passes_guardrails(self):
        ranking_input = _make_ranking_input()
        output = _make_valid_output()
        result = validate_ranking_output(output, ranking_input)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.violation_count, 0)
        self.assertIsNone(result.corrected_output)

    def test_valid_run_constrained_ranking(self):
        ranking_input = _make_ranking_input()
        valid_json = _make_valid_json_str()
        provider = StubProvider(response=valid_json, label="valid-test")

        output, guardrail_result, elapsed_ms = run_constrained_ranking(
            provider=provider,
            ranking_input=ranking_input,
        )
        self.assertEqual(len(output.ranked_hypotheses), 3)
        self.assertGreaterEqual(elapsed_ms, 0)
        valid_faults = set(ranking_input.valid_fault_ids)
        for rh in output.ranked_hypotheses:
            self.assertIn(rh.fault_id, valid_faults)


class TestUnknownHypothesis(unittest.TestCase):
    def test_unknown_hypothesis_rejected(self):
        ranking_input = _make_ranking_input()
        output = LLMRankingOutput(
            ranked_hypotheses=(
                RankedHypothesis(fault_id="H999_INVENTED", rank=1, confidence=0.9),
                RankedHypothesis(fault_id="ADCS_GYRO_SEU", rank=2, confidence=0.5),
                RankedHypothesis(fault_id="EPS_SOLAR_UNDERVOLT", rank=3, confidence=0.3),
            ),
            reasoning_summary="Test",
            supporting_evidence_ids=("E1",),
            contradicting_evidence_ids=(),
            selected_procedure_ids=(),
            uncertainty="HIGH",
            requires_human_review=True,
        )

        result = validate_ranking_output(output, ranking_input)
        self.assertFalse(result.is_valid)
        self.assertIn(ViolationType.UNSUPPORTED_HYPOTHESIS, result.violation_types)
        corrected_faults = {
            h.fault_id for h in result.corrected_output.ranked_hypotheses
        }
        self.assertNotIn("H999_INVENTED", corrected_faults)


class TestUnknownEvidence(unittest.TestCase):
    def test_unknown_evidence_rejected(self):
        ranking_input = _make_ranking_input()
        output = _make_valid_output()
        output = LLMRankingOutput(
            ranked_hypotheses=output.ranked_hypotheses,
            reasoning_summary=output.reasoning_summary,
            supporting_evidence_ids=("E1", "E99_INVENTED"),
            contradicting_evidence_ids=("E3",),
            selected_procedure_ids=output.selected_procedure_ids,
            uncertainty=output.uncertainty,
            requires_human_review=True,
        )

        result = validate_ranking_output(output, ranking_input)
        self.assertFalse(result.is_valid)
        self.assertIn(ViolationType.NONEXISTENT_EVIDENCE, result.violation_types)
        self.assertNotIn("E99_INVENTED", result.corrected_output.supporting_evidence_ids)

    def test_unknown_contradicting_evidence_rejected(self):
        ranking_input = _make_ranking_input()
        output = LLMRankingOutput(
            ranked_hypotheses=_make_valid_output().ranked_hypotheses,
            reasoning_summary="Test",
            supporting_evidence_ids=("E1",),
            contradicting_evidence_ids=("E3", "E_FAKE"),
            selected_procedure_ids=(),
            uncertainty="HIGH",
            requires_human_review=True,
        )

        result = validate_ranking_output(output, ranking_input)
        self.assertFalse(result.is_valid)
        self.assertIn(ViolationType.NONEXISTENT_EVIDENCE, result.violation_types)
        self.assertNotIn("E_FAKE", result.corrected_output.contradicting_evidence_ids)


class TestUnknownProcedure(unittest.TestCase):
    def test_unknown_procedure_rejected(self):
        ranking_input = _make_ranking_input()
        output = LLMRankingOutput(
            ranked_hypotheses=_make_valid_output().ranked_hypotheses,
            reasoning_summary="Test",
            supporting_evidence_ids=("E1",),
            contradicting_evidence_ids=(),
            selected_procedure_ids=("PROC-001", "PROC-999"),
            uncertainty="HIGH",
            requires_human_review=True,
        )

        result = validate_ranking_output(output, ranking_input)
        self.assertFalse(result.is_valid)
        self.assertIn(ViolationType.INVALID_PROCEDURE, result.violation_types)
        self.assertNotIn("PROC-999", result.corrected_output.selected_procedure_ids)
        self.assertIn("PROC-001", result.corrected_output.selected_procedure_ids)


class TestGeneratedCommand(unittest.TestCase):
    def test_command_key_rejected(self):
        ranking_input = _make_ranking_input()
        output = _make_valid_output()
        raw_parsed = json.loads(_make_valid_json_str())
        raw_parsed["commands"] = ["CMD_FIRE_THRUSTER"]

        result = validate_ranking_output(
            output, ranking_input, raw_parsed=raw_parsed,
        )
        self.assertFalse(result.is_valid)
        self.assertIn(ViolationType.UNKNOWN_COMMAND, result.violation_types)


class TestPhysicsContradiction(unittest.TestCase):
    def test_physics_invalid_hypothesis_demoted(self):
        ranking_input = _make_ranking_input(
            physics_invalidated=("ADCS_GYRO_SEU",),
            physics_validated=(),
        )

        output = LLMRankingOutput(
            ranked_hypotheses=(
                RankedHypothesis(
                    fault_id="ADCS_GYRO_SEU", rank=1, confidence=0.9,
                    justification="I think this is the cause.",
                ),
                RankedHypothesis(
                    fault_id="EPS_SOLAR_UNDERVOLT", rank=2, confidence=0.5,
                ),
                RankedHypothesis(
                    fault_id="OBC_WATCHDOG_OVERFLOW", rank=3, confidence=0.2,
                ),
            ),
            reasoning_summary="Test",
            uncertainty="HIGH",
            requires_human_review=True,
        )

        class MockPhysicsReport:
            invalidated = ["ADCS_GYRO_SEU"]
            validated = []
            uncertain = ["EPS_SOLAR_UNDERVOLT", "OBC_WATCHDOG_OVERFLOW"]

        result = validate_ranking_output(
            output, ranking_input, physics_report=MockPhysicsReport(),
        )
        self.assertFalse(result.is_valid)
        self.assertIn(ViolationType.PHYSICS_OVERRIDE, result.violation_types)

        for h in result.corrected_output.ranked_hypotheses:
            if h.fault_id == "ADCS_GYRO_SEU":
                self.assertLessEqual(h.confidence, 0.3)
                self.assertIn("DEMOTED", h.justification)


class TestUnsupportedCertainty(unittest.TestCase):
    def test_certainty_in_reasoning_flagged(self):
        ranking_input = _make_ranking_input()
        output = LLMRankingOutput(
            ranked_hypotheses=_make_valid_output().ranked_hypotheses,
            reasoning_summary="This is definitely the root cause with confirmed certainty.",
            supporting_evidence_ids=("E1",),
            contradicting_evidence_ids=(),
            selected_procedure_ids=(),
            uncertainty="LOW",
            requires_human_review=False,
        )

        result = validate_ranking_output(output, ranking_input)
        self.assertFalse(result.is_valid)
        self.assertIn(ViolationType.UNSUPPORTED_CERTAINTY, result.violation_types)
        self.assertTrue(result.corrected_output.requires_human_review)


class TestExplainer(unittest.TestCase):
    def test_explain_ranking_returns_string(self):
        ranking_input = _make_ranking_input()
        output = _make_valid_output()
        result = explain_ranking(output, ranking_input)
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)
        self.assertIn("ADCS_GYRO_SEU", result)

    def test_explain_evidence_returns_string(self):
        ranking_input = _make_ranking_input()
        output = _make_valid_output()
        result = explain_evidence(output, ranking_input)
        self.assertIsInstance(result, str)
        self.assertIn("Supporting", result)


class TestSSEEventSequence(unittest.TestCase):
    def test_no_thought_events_in_stream(self):
        from app.agent.agent import SentinelAgent, AgentConfig, ModelMode
        from app.api.models import SSEEventType

        valid_json = _make_valid_json_str()

        config = AgentConfig(mode=ModelMode.STUB, stub_response=valid_json, stub_label="sse-test")
        agent = SentinelAgent(config)

        crash_dump = {
            "scenario_id": "TEST_SSE",
            "fault_type": "ADCS_GYRO_SEU",
            "safe_mode_trigger": "Gyro rate exceeded",
            "pre_fault_telemetry_window": [],
        }

        events = list(agent.analyze_crash_dump_stream(crash_dump))
        event_types = [e.event_type for e in events]
        self.assertNotIn(SSEEventType.THOUGHT, event_types)


if __name__ == "__main__":
    unittest.main()
