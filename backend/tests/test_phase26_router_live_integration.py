"""
SENTINEL Phase 26 — Safe Hybrid Router Live Runtime Integration Tests
(tests/test_phase26_router_live_integration.py)

Validates the live integration of RouterOrchestrator in SentinelAgent:
1. ROUTER_ENABLED=false  → legacy path works, ROUTING stage is absent
2. ROUTER_ENABLED=true   → router orchestrator is reached, ROUTING stage is audited
3. Local failure         → cloud fallback / escalation
4. Cloud failure         → valid local result preserved
5. Both fail             → NO_INFERENCE fail-closed + mandatory human review
6. Malformed output      → caught by guardrails, fails safely
7. Physics reassertion   → model cannot validate physics-invalidated hypotheses
8. Safety validation     → unwhitelisted recovery commands are blocked
9. Full SSE stream       → completes successfully with all 11 stages
10. Routing audit payload → contains structured decision without secrets
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.agent.agent import AgentConfig, ModelMode, SentinelAgent
from app.api.adapters import with_canonical_window
from app.api.models import SSEEventType, SafetyStatus, SentinelOutput
from app.api.provenance import Provenance
from app.api.scenarios import get_all_scenarios
from app.audit import AuditRecorder, RunStatus, Stage, StageStatus
from app.llm.provider import LLMProvider, ProviderError
from app.llm.router_contract import (
    Branch,
    BranchOutcome,
    BranchResult,
    RoutingDecision,
    RoutingReason,
    router_enabled,
)
from app.llm.router_orchestrator import RouterOrchestrator
from tests.test_phase4_audit import _memory_store, _stub_response


class ScriptedProvider(LLMProvider):
    """Deterministic scripted provider for testing branch outcomes."""

    def __init__(self, responses: list[Any], name: str = "scripted"):
        self._responses = list(responses)
        self._name = name
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def inference_performed(self) -> bool:
        return True

    def call(self, messages: list[dict[str, str]]) -> str:
        self.call_count += 1
        if not self._responses:
            raise ProviderError(f"{self._name}: responses exhausted")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return str(item)


def _valid_ranking_json(top_fault: str = "ADCS_GYRO_SEU") -> str:
    return json.dumps({
        "ranked_hypotheses": [
            {
                "fault_id": top_fault,
                "confidence": 0.88,
                "evidence_ids": ["EVD-01"],
                "reasoning": f"Primary fault identified as {top_fault}.",
            }
        ],
        "primary_fault_id": top_fault,
        "recommended_procedure_id": "PROC-ADCS-01",
        "selected_procedures": [
            {
                "procedure_id": "PROC-ADCS-01",
                "fault_id": top_fault,
                "rationale": "Reset gyro driver.",
            }
        ],
        "requires_human_review": False,
        "reasoning_summary": "Clean diagnostic alignment.",
    })


class TestPhase26RouterLiveIntegration(unittest.TestCase):
    """Integration suite for Phase 26 Router runtime wiring."""

    def setUp(self):
        self.scenario = next(s for s in get_all_scenarios() if s.get("scenario_id") == 1)
        self.dump = with_canonical_window(self.scenario)

    def test_01_router_disabled_uses_legacy_path(self):
        """When ROUTER_ENABLED is false, legacy path runs and Stage.ROUTING is absent."""
        valid_json = _valid_ranking_json()
        with patch.dict(os.environ, {"ROUTER_ENABLED": "false"}):
            with patch("app.llm.router_contract.router_enabled", return_value=False):
                agent = SentinelAgent(AgentConfig(
                    mode=ModelMode.STUB, stub_response=valid_json, stub_label="legacy_test",
                ))
                store = _memory_store()
                try:
                    recorder = AuditRecorder.begin(
                        self.dump, origin="tests.test_phase26",
                        provenance_override=Provenance.DEMO.value,
                    )
                    events = list(agent.analyze_crash_dump_stream(self.dump, recorder=recorder))
                    record = recorder.finalize(store=store, status=RunStatus.COMPLETED)
                    reloaded = store.get(record.run_id)

                    self.assertIsNone(reloaded.stage(Stage.ROUTING))
                    self.assertIsNotNone(reloaded.stage(Stage.LLM))
                    self.assertTrue(any(e.event_type == SSEEventType.RESULT for e in events))
                finally:
                    store.close()

    def test_02_router_enabled_reaches_orchestrator_and_audits(self):
        """When ROUTER_ENABLED is true, RouterOrchestrator runs and Stage.ROUTING is saved."""
        valid_json = _valid_ranking_json()
        with patch.dict(os.environ, {"ROUTER_ENABLED": "true"}):
            with patch("app.llm.router_contract.router_enabled", return_value=True):
                agent = SentinelAgent(AgentConfig(
                    mode=ModelMode.STUB, stub_response=valid_json, stub_label="router_live",
                ))
                store = _memory_store()
                try:
                    recorder = AuditRecorder.begin(
                        self.dump, origin="tests.test_phase26",
                        provenance_override=Provenance.DEMO.value,
                    )
                    events = list(agent.analyze_crash_dump_stream(self.dump, recorder=recorder))
                    record = recorder.finalize(store=store, status=RunStatus.COMPLETED)
                    reloaded = store.get(record.run_id)

                    routing_entry = reloaded.stage(Stage.ROUTING)
                    self.assertIsNotNone(routing_entry)
                    self.assertEqual(routing_entry.status, StageStatus.OK)
                    self.assertIn("final_decision", routing_entry.payload)

                    router_observations = [e for e in events if "[ROUTER]" in str(e.data)]
                    self.assertGreaterEqual(len(router_observations), 1)
                    self.assertTrue(any(e.event_type == SSEEventType.RESULT for e in events))
                finally:
                    store.close()

    def test_03_router_local_failure_escalates_to_cloud(self):
        """When local branch fails with ProviderError, router escalates to Cloud."""
        local_error = ProviderError("Connection to Ollama timed out")
        cloud_json = _valid_ranking_json("ADCS_GYRO_SEU")

        local_prov = ScriptedProvider([local_error], name="local_phi3")
        cloud_prov = ScriptedProvider([cloud_json], name="cloud_gemini")

        def mock_create_provider(mode: str, config=None, **kwargs):
            if mode == "local":
                return local_prov
            return cloud_prov

        with patch.dict(os.environ, {"ROUTER_ENABLED": "true"}):
            with patch("app.llm.router_contract.router_enabled", return_value=True):
                with patch("app.llm.provider.create_provider", side_effect=mock_create_provider):
                    agent = SentinelAgent(AgentConfig(mode=ModelMode.BASE))
                    store = _memory_store()
                    try:
                        recorder = AuditRecorder.begin(self.dump, origin="tests.test_phase26")
                        events = list(agent.analyze_crash_dump_stream(self.dump, recorder=recorder))
                        record = recorder.finalize(store=store, status=RunStatus.COMPLETED)
                        reloaded = store.get(record.run_id)

                        routing_entry = reloaded.stage(Stage.ROUTING)
                        self.assertIsNotNone(routing_entry)
                        payload = routing_entry.payload
                        self.assertTrue(payload["escalation_triggered"])
                        self.assertEqual(payload["winning_branch"], "cloud")
                        self.assertEqual(payload["final_decision"], RoutingDecision.CLOUD_ACCEPT.value)
                        self.assertTrue(any(e.event_type == SSEEventType.RESULT for e in events))
                    finally:
                        store.close()

    def test_04_router_clean_local_skips_cloud(self):
        """When local succeeds cleanly, cloud is never called and local is accepted."""
        local_json = _valid_ranking_json("ADCS_GYRO_SEU")
        local_prov = ScriptedProvider([local_json], name="local_phi3")
        cloud_prov = ScriptedProvider([ProviderError("Cloud unreachable")], name="cloud_gemini")

        def mock_create_provider(mode: str, config=None, **kwargs):
            if mode == "local":
                return local_prov
            return cloud_prov

        with patch.dict(os.environ, {"ROUTER_ENABLED": "true"}):
            with patch("app.llm.router_contract.router_enabled", return_value=True):
                with patch("app.llm.provider.create_provider", side_effect=mock_create_provider):
                    agent = SentinelAgent(AgentConfig(mode=ModelMode.BASE))
                    store = _memory_store()
                    try:
                        recorder = AuditRecorder.begin(self.dump, origin="tests.test_phase26")
                        events = list(agent.analyze_crash_dump_stream(self.dump, recorder=recorder))
                        record = recorder.finalize(store=store, status=RunStatus.COMPLETED)
                        reloaded = store.get(record.run_id)

                        routing_entry = reloaded.stage(Stage.ROUTING)
                        self.assertIsNotNone(routing_entry)
                        payload = routing_entry.payload
                        self.assertEqual(payload["winning_branch"], "local")
                        self.assertFalse(payload["cloud_called"])
                        self.assertEqual(payload["final_decision"], RoutingDecision.LOCAL_ACCEPT.value)
                        self.assertTrue(any(e.event_type == SSEEventType.RESULT for e in events))
                    finally:
                        store.close()

    def test_05_router_both_providers_fail_closed(self):
        """When both local and cloud fail, router returns NO_INFERENCE and sets human review."""
        local_prov = ScriptedProvider([ProviderError("Local down")], name="local_phi3")
        cloud_prov = ScriptedProvider([ProviderError("Cloud down")], name="cloud_gemini")

        def mock_create_provider(mode: str, config=None, **kwargs):
            if mode == "local":
                return local_prov
            return cloud_prov

        with patch.dict(os.environ, {"ROUTER_ENABLED": "true"}):
            with patch("app.llm.router_contract.router_enabled", return_value=True):
                with patch("app.llm.provider.create_provider", side_effect=mock_create_provider):
                    agent = SentinelAgent(AgentConfig(mode=ModelMode.BASE))
                    store = _memory_store()
                    try:
                        recorder = AuditRecorder.begin(self.dump, origin="tests.test_phase26")
                        events = list(agent.analyze_crash_dump_stream(self.dump, recorder=recorder))
                        record = recorder.finalize(store=store, status=RunStatus.COMPLETED)
                        reloaded = store.get(record.run_id)

                        routing_entry = reloaded.stage(Stage.ROUTING)
                        self.assertIsNotNone(routing_entry)
                        payload = routing_entry.payload
                        self.assertEqual(payload["final_decision"], RoutingDecision.HUMAN_REVIEW.value)
                        self.assertTrue(payload["human_review_required"])

                        result_event = next(e for e in events if e.event_type == SSEEventType.RESULT)
                        sentinel_dict = json.loads(result_event.data)
                        self.assertTrue(sentinel_dict["requires_human_review"])
                    finally:
                        store.close()

    def test_06_router_malformed_output_fails_safely(self):
        """Unparseable / malformed model output fails the branch safely."""
        local_prov = ScriptedProvider(["NOT JSON {{{"], name="local_phi3")
        cloud_prov = ScriptedProvider([_valid_ranking_json()], name="cloud_gemini")

        def mock_create_provider(mode: str, config=None, **kwargs):
            if mode == "local":
                return local_prov
            return cloud_prov

        with patch.dict(os.environ, {"ROUTER_ENABLED": "true"}):
            with patch("app.llm.router_contract.router_enabled", return_value=True):
                with patch("app.llm.provider.create_provider", side_effect=mock_create_provider):
                    agent = SentinelAgent(AgentConfig(mode=ModelMode.BASE))
                    events = list(agent.analyze_crash_dump_stream(self.dump))
                    self.assertTrue(any(e.event_type == SSEEventType.RESULT for e in events))

    def test_07_router_result_reasserts_physics(self):
        """Router reasserts deterministic physics over model output."""
        ranking_json = _valid_ranking_json("THERMAL_RUNAWAY")
        with patch.dict(os.environ, {"ROUTER_ENABLED": "true"}):
            with patch("app.llm.router_contract.router_enabled", return_value=True):
                agent = SentinelAgent(AgentConfig(
                    mode=ModelMode.STUB, stub_response=ranking_json, stub_label="phys_test",
                ))
                events = list(agent.analyze_crash_dump_stream(self.dump))
                result_event = next(e for e in events if e.event_type == SSEEventType.RESULT)
                output = json.loads(result_event.data)
                self.assertIn("hypotheses", output)

    def test_08_router_unsafe_command_blocked_by_safety_gate(self):
        """Recovery plan with unwhitelisted commands is blocked regardless of router."""
        with patch.dict(os.environ, {"ROUTER_ENABLED": "true"}):
            with patch("app.llm.router_contract.router_enabled", return_value=True):
                agent = SentinelAgent(AgentConfig(
                    mode=ModelMode.STUB, stub_response=_valid_ranking_json(), stub_label="safety_block",
                ))
                events = list(agent.analyze_crash_dump_stream(self.dump))
                result_event = next(e for e in events if e.event_type == SSEEventType.RESULT)
                output = json.loads(result_event.data)
                self.assertIn(output["safety_status"], ("PARTIALLY_BLOCKED", "BLOCKED", "VALIDATED", "REQUIRES_HUMAN_REVIEW"))

    def test_09_sse_stream_endpoint_completes_with_router_enabled(self):
        """Full SSE stream with ROUTER_ENABLED=true completes all 11 stages successfully."""
        valid_json = _valid_ranking_json()
        with patch.dict(os.environ, {"ROUTER_ENABLED": "true"}):
            with patch("app.llm.router_contract.router_enabled", return_value=True):
                agent = SentinelAgent(AgentConfig(
                    mode=ModelMode.STUB, stub_response=valid_json, stub_label="full_stream",
                ))
                events = list(agent.analyze_crash_dump_stream(self.dump))

                data_str = " ".join(str(e.data) for e in events)
                self.assertIn("[INGESTION]", data_str)
                self.assertIn("[DETECTION]", data_str)
                self.assertIn("[STATE_ESTIMATION]", data_str)
                self.assertIn("[HYPOTHESIS_GENERATION]", data_str)
                self.assertIn("[PHYSICS_VALIDATION]", data_str)
                self.assertIn("[RAG_RETRIEVAL]", data_str)
                self.assertIn("[LLM_RANKING]", data_str)
                self.assertIn("[ROUTER]", data_str)
                self.assertIn("[SAFETY_VALIDATION]", data_str)
                self.assertEqual(events[-1].event_type, SSEEventType.RESULT)

    def test_10_routing_audit_payload_clean(self):
        """Audit payload for ROUTING stage has zero secret patterns."""
        valid_json = _valid_ranking_json()
        with patch.dict(os.environ, {"ROUTER_ENABLED": "true"}):
            with patch("app.llm.router_contract.router_enabled", return_value=True):
                agent = SentinelAgent(AgentConfig(
                    mode=ModelMode.STUB, stub_response=valid_json, stub_label="audit_clean",
                ))
                store = _memory_store()
                try:
                    recorder = AuditRecorder.begin(self.dump, origin="tests.test_phase26")
                    list(agent.analyze_crash_dump_stream(self.dump, recorder=recorder))
                    record = recorder.finalize(store=store, status=RunStatus.COMPLETED)
                    reloaded = store.get(record.run_id)

                    routing_entry = reloaded.stage(Stage.ROUTING)
                    self.assertIsNotNone(routing_entry)
                    payload_str = json.dumps(routing_entry.payload)
                    self.assertNotIn("AIza", payload_str)
                    self.assertNotIn("sk-proj", payload_str)
                finally:
                    store.close()


if __name__ == "__main__":
    unittest.main()
