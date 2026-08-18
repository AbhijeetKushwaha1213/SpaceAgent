"""test_phase11_sovereign_llm.py

SENTINEL Phase 11 — Local / Sovereign LLM Mode tests.

Tests:
  1. Environment variable configuration parsing (LLM_MODE, LLM_BASE_URL, LLM_MODEL).
  2. Provider abstraction (CLOUD -> GeminiProvider, LOCAL -> LocalProvider).
  3. LocalProvider generic OpenAI format (no vendor hardcoding, urllib fallback).
  4. Privacy & Telemetry isolation assertion (LOCAL mode blocks cloud Gemini calls).
  5. System status endpoint (GET /api/v1/system/status and /system/status).
  6. Audit identity recording of llm_mode, model, and provider.
  7. Factual sovereignty indicator & disclaimer verification.

Run:
    cd sentinel/backend && python3 tests/test_phase11_sovereign_llm.py
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure backend/ root is on sys.path for standalone execution
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None

from app.main import app, get_system_status
from app.agent.agent import AgentConfig, AgentError, LLMCallError, ModelMode, SentinelAgent
from app.audit.record import llm_identity
from app.llm.provider import (
    GeminiProvider,
    LLMProvider,
    LocalProvider,
    ProviderConfig,
    ProviderError,
    StubProvider,
    create_provider,
)
from app.api.models import SystemStatusResponse, SovereigntyInfo


# ═══════════════════════════════════════════════════════════════════════════
# 1. CONFIGURATION & ENVIRONMENT VARIABLES
# ═══════════════════════════════════════════════════════════════════════════

class TestConfigurationAndEnvVars(unittest.TestCase):
    """Test resolution of LLM_MODE, LLM_BASE_URL, and LLM_MODEL from environment."""

    def test_default_cloud_mode(self):
        with patch.dict(os.environ, {}, clear=True):
            config = AgentConfig()
            self.assertTrue(config.mode.is_cloud)
            self.assertFalse(config.mode.is_local)

    def test_env_llm_mode_local(self):
        with patch.dict(os.environ, {"LLM_MODE": "LOCAL", "LLM_MODEL": "llama3:8b", "LLM_BASE_URL": "http://localhost:8080/v1"}):
            config = AgentConfig()
            self.assertEqual(config.mode, ModelMode.LOCAL)
            self.assertTrue(config.mode.is_local)
            self.assertFalse(config.mode.is_cloud)
            self.assertEqual(config.fallback_model, "llama3:8b")
            self.assertEqual(config.fallback_base_url, "http://localhost:8080/v1")
            self.assertEqual(config.active_model_name, "llama3:8b")

    def test_env_llm_mode_cloud(self):
        with patch.dict(os.environ, {"LLM_MODE": "CLOUD", "LLM_MODEL": "gemini-2.5-pro"}):
            config = AgentConfig()
            self.assertEqual(config.mode, ModelMode.CLOUD)
            self.assertTrue(config.mode.is_cloud)
            self.assertEqual(config.model, "gemini-2.5-pro")
            self.assertEqual(config.active_model_name, "gemini-2.5-pro")

    def test_provider_config_env_defaults(self):
        with patch.dict(os.environ, {"LLM_MODEL": "qwen-2.5", "LLM_BASE_URL": "http://127.0.0.1:11434/v1"}):
            pconfig = ProviderConfig()
            self.assertEqual(pconfig.fallback_model, "qwen-2.5")
            self.assertEqual(pconfig.fallback_base_url, "http://127.0.0.1:11434/v1")


# ═══════════════════════════════════════════════════════════════════════════
# 2. PROVIDER FACTORY ABSTRACTION
# ═══════════════════════════════════════════════════════════════════════════

class TestProviderFactory(unittest.TestCase):
    """Test create_provider factory for CLOUD, LOCAL, and STUB modes."""

    def test_create_provider_cloud_returns_gemini(self):
        provider = create_provider(mode="cloud")
        self.assertIsInstance(provider, GeminiProvider)
        self.assertEqual(provider.provider_name, "gemini")

    def test_create_provider_local_returns_local_provider(self):
        provider = create_provider(mode="local")
        self.assertIsInstance(provider, LocalProvider)
        self.assertEqual(provider.provider_name, "local")

    def test_create_provider_stub(self):
        provider = create_provider(mode="stub", stub_response='{"test": true}')
        self.assertIsInstance(provider, StubProvider)
        self.assertEqual(provider.provider_name, "stub")
        self.assertFalse(provider.inference_performed)

    def test_create_provider_invalid_mode_raises(self):
        with self.assertRaises(ValueError):
            create_provider(mode="invalid_mode_name")


# ═══════════════════════════════════════════════════════════════════════════
# 3. LOCAL PROVIDER (OPENAI-COMPATIBLE, NO VENDOR HARDCODING)
# ═══════════════════════════════════════════════════════════════════════════

class TestLocalProviderExecution(unittest.TestCase):
    """Test LocalProvider HTTP execution (zero vendor hardcoding)."""

    def test_local_provider_call_http_urllib_fallback(self):
        """Test urllib fallback call formatting when openai package is absent."""
        pconfig = ProviderConfig(
            fallback_model="phi-3-mini",
            fallback_base_url="http://localhost:11434/v1",
            fallback_api_key="local-key",
        )
        provider = LocalProvider(config=pconfig)

        mock_response_data = {
            "choices": [
                {
                    "message": {
                        "content": '{"ranked_hypotheses": [], "reasoning_summary": "local success"}'
                    }
                }
            ]
        }

        class MockHTTPResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return json.dumps(mock_response_data).encode("utf-8")

        with patch.dict("sys.modules", {"openai": None}):
            with patch("urllib.request.urlopen", return_value=MockHTTPResponse()) as mock_urlopen:
                messages = [{"role": "user", "content": "hello local"}]
                result = provider.call(messages)
                self.assertIn("local success", result)
                self.assertTrue(mock_urlopen.called)

                req = mock_urlopen.call_args[0][0]
                self.assertEqual(req.full_url, "http://localhost:11434/v1/chat/completions")
                headers = dict(req.headers)
                self.assertEqual(headers.get("Content-type"), "application/json")
                self.assertEqual(headers.get("Authorization"), "Bearer local-key")


# ═══════════════════════════════════════════════════════════════════════════
# 4. PRIVACY & TELEMETRY ISOLATION IN LOCAL MODE
# ═══════════════════════════════════════════════════════════════════════════

class TestPrivacyTelemetryIsolation(unittest.TestCase):
    """Verify that in LOCAL mode, mission telemetry is NEVER transmitted to cloud APIs."""

    def test_call_gemini_blocked_in_local_mode(self):
        config = AgentConfig(mode=ModelMode.LOCAL)
        agent = SentinelAgent(config)
        
        with self.assertRaises(LLMCallError):
            agent._call_gemini([{"role": "user", "content": "sensitive crash dump"}])

    def test_call_gemini_blocked_in_fallback_mode(self):
        config = AgentConfig(mode=ModelMode.FALLBACK)
        agent = SentinelAgent(config)

        with self.assertRaises(LLMCallError):
            agent._call_gemini([{"role": "user", "content": "sensitive crash dump"}])

    def test_local_agent_routing(self):
        config = AgentConfig(mode=ModelMode.LOCAL, stub_response='{"test": 1}')
        agent = SentinelAgent(config)

        with patch.object(agent, "_call_fallback", return_value="local_response") as mock_fallback:
            with patch.object(agent, "_call_gemini") as mock_gemini:
                res = agent._call_llm([{"role": "user", "content": "hi"}])
                self.assertEqual(res, "local_response")
                self.assertTrue(mock_fallback.called)
                self.assertFalse(mock_gemini.called)


# ═══════════════════════════════════════════════════════════════════════════
# 5. SYSTEM STATUS API ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════

class TestSystemStatusAPI(unittest.TestCase):
    """Test GET /api/v1/system/status and GET /system/status endpoints."""

    def test_get_system_status_v1(self):
        if TestClient is not None:
            client = TestClient(app)
            res = client.get("/api/v1/system/status")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            status_model = SystemStatusResponse.model_validate(data)
        else:
            status_model = get_system_status()

        self.assertEqual(status_model.backend_status, "ok")
        self.assertEqual(status_model.detector_status, "ok")
        self.assertEqual(status_model.physics_model_status, "ok")
        self.assertEqual(status_model.rag_status, "ok")
        self.assertIn(status_model.llm_mode, ("CLOUD", "LOCAL", "STUB"))
        self.assertIn(status_model.llm_provider, ("gemini", "local", "stub"))
        self.assertTrue(len(status_model.model) > 0)
        self.assertIsNotNone(status_model.sovereignty.disclaimer)
        self.assertIn("certifications", status_model.sovereignty.disclaimer)

    def test_get_system_status_unversioned_alias(self):
        if TestClient is not None:
            client = TestClient(app)
            res = client.get("/system/status")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["backend_status"], "ok")
        else:
            res = get_system_status()
            self.assertEqual(res.backend_status, "ok")

    def test_system_status_reflects_local_mode(self):
        with patch.dict(os.environ, {"LLM_MODE": "LOCAL", "LLM_MODEL": "mistral-7b"}):
            local_agent = SentinelAgent(AgentConfig(mode=ModelMode.LOCAL, fallback_model="mistral-7b"))
            with patch("app.main.agent", local_agent):
                if TestClient is not None:
                    client = TestClient(app)
                    res = client.get("/api/v1/system/status")
                    self.assertEqual(res.status_code, 200)
                    data = res.json()
                else:
                    data = get_system_status().model_dump()
                self.assertEqual(data["llm_mode"], "LOCAL")
                self.assertEqual(data["llm_provider"], "local")
                self.assertEqual(data["model"], "mistral-7b")
                self.assertTrue(data["sovereignty"]["local_execution"])
                self.assertTrue(data["sovereignty"]["cloud_telemetry_disabled"])


# ═══════════════════════════════════════════════════════════════════════════
# 6. AUDIT IDENTITY RECORDING
# ═══════════════════════════════════════════════════════════════════════════

class TestAuditIdentityRecording(unittest.TestCase):
    """Test audit log recording of llm_mode, model, and provider."""

    def test_llm_identity_cloud_mode(self):
        config = AgentConfig(mode=ModelMode.CLOUD, model="gemini-2.5-flash")
        identity = llm_identity(config)
        self.assertEqual(identity["llm_mode"], "CLOUD")
        self.assertEqual(identity["provider"], "google_gemini")
        self.assertEqual(identity["model"], "gemini-2.5-flash")
        self.assertFalse(identity["local_inference"])

    def test_llm_identity_local_mode(self):
        config = AgentConfig(mode=ModelMode.LOCAL, fallback_model="phi-3-mini", fallback_base_url="http://localhost:11434/v1")
        identity = llm_identity(config)
        self.assertEqual(identity["llm_mode"], "LOCAL")
        self.assertEqual(identity["provider"], "openai_compatible_local")
        self.assertEqual(identity["model"], "phi-3-mini")
        self.assertEqual(identity["endpoint"], "http://localhost:11434/v1")
        self.assertTrue(identity["local_inference"])

    def test_llm_identity_stub_mode(self):
        config = AgentConfig(mode=ModelMode.STUB, stub_label="unit-test")
        identity = llm_identity(config)
        self.assertEqual(identity["llm_mode"], "STUB")
        self.assertEqual(identity["provider"], "none_stubbed_response")
        self.assertFalse(identity["inference_performed"])


# ═══════════════════════════════════════════════════════════════════════════
# 7. FACTUAL DISCLAIMER VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════

class TestFactualDisclaimer(unittest.TestCase):
    """Verify that sovereignty information contains factual operational statements without false compliance claims."""

    def test_sovereignty_disclaimer_content(self):
        sov = SovereigntyInfo(local_execution=True, cloud_telemetry_disabled=True)
        self.assertIn("No security or compliance certifications", sov.disclaimer)


if __name__ == "__main__":
    unittest.main()
