"""test_e2e_integration.py — End-to-end HTTP integration tests (Phase 2).

Spins up the FastAPI server in-process via TestClient and exercises the
full HTTP stack:

  1. Happy path   — POST /api/v1/analyze → SSE event stream → the terminal
                    RESULT event validates as a SentinelOutput.
  2. Invalid input — malformed crash dump payload → HTTP 422.
  3. Rate limit   — SecurityMiddleware sliding-window limiter → HTTP 429.
  4. Auth         — production config (auth_required) rejects anonymous
                    requests with 401 and accepts a valid SENTINEL_API_KEY.

The LLM stream is mocked (as in test_streaming.py) so no network is touched;
everything else runs through the real app: routing, validation, SSE
serialization, middleware ordering, and audit recording.

Run via pytest (recommended):
    cd sentinel/backend && python -m pytest tests/test_e2e_integration.py -v

Or standalone:
    cd sentinel/backend && python tests/test_e2e_integration.py
"""

import json
import os
import sys
import unittest
from collections.abc import Generator
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Standalone runs bypass pytest's conftest.py; keep the test server in
# SECURE_DEV_MODE so no API key is required (auth is tested explicitly).
os.environ.setdefault("SECURE_DEV_MODE", "1")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.models import (
    Hypothesis,
    RecoveryStep,
    RiskLevel,
    SentinelOutput,
    SSEEvent,
    SSEEventType,
)
from app.main import app
from app.security.config import SecurityConfig
from app.security.middleware import SecurityMiddleware

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_SENTINEL_OUTPUT = SentinelOutput(
    hypotheses=[
        Hypothesis(
            rank=1,
            root_cause="ADCS_GYRO_SEU",
            affected_component="GYRO_A",
            confidence=0.92,
            causal_chain=[
                "SEU burst corrupted gyroscope memory registers",
                "Gyro_rate_degs became NaN",
                "ADCS attitude error grew beyond threshold",
                "OBC entered safe mode",
            ],
        ),
        Hypothesis(
            rank=2,
            root_cause="OBC_WATCHDOG_OVERFLOW",
            affected_component="OBC_FLIGHT_SOFTWARE",
            confidence=0.05,
            causal_chain=[
                "Software fault possible but CPU load is nominal",
                "Watchdog counter not elevated",
            ],
        ),
        Hypothesis(
            rank=3,
            root_cause="MULTI_CASCADE",
            affected_component="ADCS_EPS_TCS_CHAIN",
            confidence=0.03,
            causal_chain=[
                "Cascade scenario present as low-probability alternative",
                "Insufficient evidence to rule out completely",
            ],
        ),
    ],
    recovery_plan=[
        RecoveryStep(
            step=1,
            command="CMD_VERIFY_SEU_COUNTER",
            rationale="Confirm radiation-induced SEU as the initiating event",
            wait_seconds=15,
            verify="SEU_counter value recorded and stable",
            risk=RiskLevel.LOW,
        ),
        RecoveryStep(
            step=2,
            command="CMD_GYRO_A_DRIVER_RESET",
            rationale="Reset gyroscope driver to clear corrupted register state",
            wait_seconds=30,
            verify="Gyro_rate_degs returns valid numeric reading",
            risk=RiskLevel.MEDIUM,
        ),
    ],
    confidence=0.92,
    requires_human_review=False,
    reasoning_summary=(
        "SEU burst corrupted GYRO_A registers causing attitude divergence. "
        "Single-event radiation fault with high confidence."
    ),
)

SAMPLE_CRASH_DUMP = {
    "scenario_id": 1,
    "fault_type": "ADCS_GYRO_SEU",
    "fault_register": "0x00000080",
    "pre_fault_telemetry": [
        {"parameter": "Gyro_rate_degs", "value": "NaN", "nominal_min": 0.0, "nominal_max": 7.0},
        {"parameter": "SEU_counter", "value": 3.0, "nominal_min": 0.0, "nominal_max": 0.0},
    ],
    "event_log": [
        {"timestamp": "T-62s", "source": "OBC_KERNEL", "message": "SEU counter incremented: 3"},
        {"timestamp": "T-0s", "source": "FDIR_CORE", "message": "Safe Mode entry triggered by ADCS_ERROR"},
    ],
    "hardware_state": {"active_gyro": "A", "seu_flags": "0x03"},
    "operating_context": {"eclipse_fraction": 0.0, "sun_sensor_angle_deg": 12.5},
}


def _make_mock_stream(output: SentinelOutput) -> Generator:
    """Yield a realistic SSE sequence as analyze_crash_dump_stream would."""
    yield SSEEvent(event_type=SSEEventType.STATUS, data="Ingesting raw spacecraft crash dump...")
    yield SSEEvent(event_type=SSEEventType.THOUGHT, data="Analyzing pre-fault telemetry.", step_number=1)
    yield SSEEvent(event_type=SSEEventType.OBSERVATION, data="SEU_counter spike detected.", step_number=1)
    yield SSEEvent(event_type=SSEEventType.STATUS, data="Analysis complete. Safety validation passed.")
    yield SSEEvent(event_type=SSEEventType.RESULT, data=output.model_dump_json())


def _collect_sse_events(streaming_response) -> list[dict]:
    """Parse `data: <JSON>` blocks out of a text/event-stream body."""
    raw_body = b"".join(streaming_response.iter_bytes())
    text = raw_body.decode("utf-8")
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if block.startswith("data: "):
            payload = block[6:].strip()
            if payload:
                try:
                    events.append(json.loads(payload))
                except json.JSONDecodeError:
                    pass
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestE2EHappyPath(unittest.TestCase):
    """POST a crash dump → SSE stream → terminal RESULT validates."""

    def test_post_analyze_streams_and_validates_output(self):
        client = TestClient(app, raise_server_exceptions=False)
        with patch(
            "app.agent.agent.SentinelAgent.analyze_crash_dump_stream",
            return_value=_make_mock_stream(MOCK_SENTINEL_OUTPUT),
        ), client.stream("POST", "/api/v1/analyze", json=SAMPLE_CRASH_DUMP) as resp:
            self.assertEqual(resp.status_code, 200)
            self.assertIn("text/event-stream", resp.headers.get("content-type", ""))
            events = _collect_sse_events(resp)

        self.assertTrue(events, "expected at least one SSE event")
        result_events = [e for e in events if e.get("event_type") == SSEEventType.RESULT.value]
        self.assertEqual(len(result_events), 1, "exactly one terminal RESULT event expected")

        parsed = SentinelOutput.model_validate_json(result_events[0]["data"])
        self.assertEqual(parsed.hypotheses[0].root_cause, "ADCS_GYRO_SEU")
        self.assertEqual(parsed.hypotheses[0].rank, 1)
        self.assertGreaterEqual(len(parsed.recovery_plan), 2)
        self.assertGreater(parsed.confidence, 0.0)

    def test_stream_first_event_is_status(self):
        client = TestClient(app, raise_server_exceptions=False)
        with patch(
            "app.agent.agent.SentinelAgent.analyze_crash_dump_stream",
            return_value=_make_mock_stream(MOCK_SENTINEL_OUTPUT),
        ), client.stream("POST", "/api/v1/analyze", json=SAMPLE_CRASH_DUMP) as resp:
            events = _collect_sse_events(resp)
        self.assertEqual(events[0]["event_type"], SSEEventType.STATUS.value)


class TestE2EInvalidInput(unittest.TestCase):
    """Malformed crash dumps must be rejected by schema validation."""

    def test_wrong_typed_field_returns_422(self):
        client = TestClient(app)
        bad = dict(SAMPLE_CRASH_DUMP)
        bad["pre_fault_telemetry"] = "not-a-list"
        resp = client.post("/api/v1/analyze", json=bad)
        self.assertEqual(resp.status_code, 422)

    def test_non_json_body_returns_422(self):
        client = TestClient(app)
        resp = client.post("/api/v1/analyze", content="not-json{", headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status_code, 422)

    def test_untyped_payload_returns_422(self):
        client = TestClient(app)
        resp = client.post("/api/v1/analyze", json={"scenario_id": "not-an-int", "fault_type": 42})
        self.assertEqual(resp.status_code, 422)


class TestE2ERateLimit(unittest.TestCase):
    """Sliding window limiter must 429 past the configured budget."""

    def test_rate_limited_after_budget_exhausted(self):
        test_app = FastAPI()

        @test_app.get("/api/health")
        def health():
            return {"status": "ok"}

        test_app.add_middleware(
            SecurityMiddleware,
            config=SecurityConfig(rate_limit_per_minute=3),
        )
        client = TestClient(test_app)
        for _ in range(3):
            self.assertEqual(client.get("/api/health").status_code, 200)
        resp = client.get("/api/health")
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()["error_code"], "RATE_LIMIT_EXCEEDED")


class TestE2EAuthRejection(unittest.TestCase):
    """Production config rejects anonymous access; valid key is accepted."""

    def test_401_without_key_then_200_with_valid_key(self):
        test_app = FastAPI()

        @test_app.get("/api/health")
        def health():
            return {"status": "ok"}

        test_app.add_middleware(
            SecurityMiddleware,
            config=SecurityConfig(auth_required=True, api_key="E2E_MISSION_KEY"),
        )
        client = TestClient(test_app)

        resp = client.get("/api/health")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error_code"], "UNAUTHORIZED")

        resp = client.get("/api/health", headers={"X-API-Key": "E2E_MISSION_KEY"})
        self.assertEqual(resp.status_code, 200)

    def test_401_with_wrong_key(self):
        test_app = FastAPI()

        @test_app.get("/api/health")
        def health():
            return {"status": "ok"}

        test_app.add_middleware(
            SecurityMiddleware,
            config=SecurityConfig(auth_required=True, api_key="E2E_MISSION_KEY"),
        )
        client = TestClient(test_app)
        resp = client.get("/api/health", headers={"Authorization": "Bearer WRONG"})
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
