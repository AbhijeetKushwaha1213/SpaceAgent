"""test_streaming.py

Integration tests for the SENTINEL streaming backend.

Tests the FastAPI HTTP contract:
  - GET /api/health → 200 {"status": "ok"}
  - GET /api/scenarios → list of scenario dicts with required keys
  - POST /api/analyze → text/event-stream with valid SSEEvent chunks
  - First events have both event_type and data fields
  - A mocked result event validates as SentinelOutput

All LLM calls are mocked so no real Gemini/OpenAI call is made.

Run via pytest (recommended):
    cd sentinel/backend && python -m pytest tests/test_streaming.py -v

Or standalone (also works):
    cd sentinel/backend && python tests/test_streaming.py
"""

import json
import os
import sys
from typing import Generator
from unittest.mock import MagicMock, patch

# Ensure backend/ root is on sys.path for standalone execution
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Standalone runs bypass pytest's conftest.py; keep the test server in
# SECURE_DEV_MODE so no API key is required (auth is tested explicitly).
os.environ.setdefault("SECURE_DEV_MODE", "1")

# ---------------------------------------------------------------------------
# Imports (must come after sys.path setup)
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient

# Import app lazily to avoid triggering agent startup
from app.main import app
from app.api.models import (
    SentinelOutput,
    Hypothesis,
    RecoveryStep,
    RiskLevel,
    SSEEvent,
    SSEEventType,
)

# ---------------------------------------------------------------------------
# Test fixtures / helpers
# ---------------------------------------------------------------------------

# A valid SentinelOutput that mocked LLM will "return"
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
            root_cause="MULTI_CASCADE",
            affected_component="ADCS_EPS_TCS_CHAIN",
            confidence=0.05,
            causal_chain=[
                "Cascade scenario present as low-probability alternative",
                "Insufficient evidence to rule out completely",
            ],
        ),
        Hypothesis(
            rank=3,
            root_cause="OBC_WATCHDOG_OVERFLOW",
            affected_component="OBC_FLIGHT_SOFTWARE",
            confidence=0.03,
            causal_chain=[
                "Software fault possible but CPU load is nominal",
                "Watchdog counter not elevated",
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
        "SEU burst at T-62s corrupted GYRO_A registers causing attitude divergence. "
        "All other subsystems nominal. Single-event radiation fault with high confidence."
    ),
)

# A minimal crash dump payload matching CrashDumpRequest schema
SAMPLE_CRASH_DUMP = {
    "scenario_id": 1,
    "fault_type": "ADCS_GYRO_SEU",
    "fault_register": "0x00000080",
    "pre_fault_telemetry": [
        {
            "parameter": "Gyro_rate_degs",
            "value": "NaN",
            "nominal_min": 0.0,
            "nominal_max": 7.0,
        },
        {
            "parameter": "SEU_counter",
            "value": 3.0,
            "nominal_min": 0.0,
            "nominal_max": 0.0,
        },
    ],
    "event_log": [
        {
            "timestamp": "T-62s",
            "source": "OBC_KERNEL",
            "message": "SEU counter incremented: 3",
        },
        {
            "timestamp": "T-0s",
            "source": "FDIR_CORE",
            "message": "Safe Mode entry triggered by ADCS_ERROR",
        },
    ],
    "hardware_state": {"active_gyro": "A", "seu_flags": "0x03"},
    "operating_context": {"eclipse_fraction": 0.0, "sun_sensor_angle_deg": 12.5},
}


def _collect_sse_events(streaming_response) -> list[dict]:
    """Parse `data: <JSON>\\n\\n` blocks from a streaming response body.

    Works with both bytes and string content.
    """
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


def _make_mock_stream(output: SentinelOutput) -> Generator:
    """Yield a realistic SSE event sequence matching what analyze_crash_dump_stream emits."""
    yield SSEEvent(event_type=SSEEventType.STATUS, data="Ingesting raw spacecraft crash dump...")
    yield SSEEvent(event_type=SSEEventType.STATUS, data="Crash dump parsed successfully.")
    yield SSEEvent(
        event_type=SSEEventType.THOUGHT,
        data="Analyzing pre-fault telemetry parameters for anomalies.",
        step_number=1,
    )
    yield SSEEvent(
        event_type=SSEEventType.OBSERVATION,
        data="Anomaly detector result: SEU_counter spike detected.",
        step_number=1,
    )
    yield SSEEvent(
        event_type=SSEEventType.THOUGHT,
        data="Retrieving ECSS procedures for ADCS_GYRO_SEU.",
        step_number=2,
    )
    yield SSEEvent(
        event_type=SSEEventType.OBSERVATION,
        data="ECSS Document Match: ADCS recovery procedure found.",
        step_number=2,
    )
    yield SSEEvent(event_type=SSEEventType.STATUS, data="Invoking reasoning agent...")
    yield SSEEvent(
        event_type=SSEEventType.THOUGHT,
        data="Constructing causal propagation graph.",
        step_number=3,
    )
    yield SSEEvent(event_type=SSEEventType.STATUS, data="Analysis complete. Safety validation passed.")
    yield SSEEvent(event_type=SSEEventType.RESULT, data=output.model_dump_json())


# ---------------------------------------------------------------------------
# Test runner infrastructure (for standalone execution)
# ---------------------------------------------------------------------------

results: list[tuple[bool, str, str]] = []


def run_test(number: int, name: str, fn) -> bool:
    """Execute *fn*, record PASS/FAIL, and return True on pass."""
    try:
        fn()
        results.append((True, f"Test {number}: {name}", ""))
        print(f"[PASS] Test {number}: {name}")
        return True
    except AssertionError as exc:
        reason = str(exc) if str(exc) else "Assertion failed (no message)"
        results.append((False, f"Test {number}: {name}", reason))
        print(f"[FAIL] Test {number}: {name}")
        print(f"       Reason: {reason}")
        return False
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        results.append((False, f"Test {number}: {name}", reason))
        print(f"[FAIL] Test {number}: {name}")
        print(f"       Reason: {reason}")
        return False


# ---------------------------------------------------------------------------
# Tests — duplicated as bare functions (pytest) and via run_test() (standalone)
# ---------------------------------------------------------------------------

def test_1_health_check():
    """GET /api/health returns 200 and {'status': 'ok'}."""
    client = TestClient(app)
    for path in ("/health", "/api/health"):
        resp = client.get(path)
        assert resp.status_code == 200, (
            f"GET {path} returned {resp.status_code}, expected 200"
        )
        body = resp.json()
        assert body.get("status") == "ok", (
            f"GET {path} body {body!r} missing 'status': 'ok'"
        )


def test_2_scenarios_endpoint():
    """GET /api/scenarios returns a non-empty list of scenario dicts."""
    client = TestClient(app)
    for path in ("/scenarios", "/api/scenarios"):
        resp = client.get(path)
        assert resp.status_code == 200, (
            f"GET {path} returned {resp.status_code}, expected 200"
        )
        data = resp.json()
        assert isinstance(data, list), f"GET {path} response is not a list: {data!r}"
        assert len(data) > 0, f"GET {path} returned empty scenario list"


def test_3_scenarios_have_required_fields():
    """Each scenario has scenario_id, fault_type, source_type, and pre_fault_telemetry."""
    client = TestClient(app)
    resp = client.get("/api/scenarios")
    assert resp.status_code == 200
    scenarios = resp.json()
    required = {"scenario_id", "fault_type"}
    for s in scenarios:
        missing = required - set(s.keys())
        assert not missing, (
            f"Scenario {s.get('scenario_id')!r} missing keys: {missing}"
        )
        # source_type should be present (added by Task 4)
        assert "source_type" in s, (
            f"Scenario {s.get('scenario_id')!r} missing 'source_type' provenance field"
        )
        assert s["source_type"], (
            f"Scenario {s.get('scenario_id')!r} has empty 'source_type'"
        )


def test_4_post_analyze_returns_event_stream():
    """POST /api/analyze returns Content-Type: text/event-stream."""
    client = TestClient(app, raise_server_exceptions=False)
    with patch(
        "app.agent.agent.SentinelAgent.analyze_crash_dump_stream",
        return_value=_make_mock_stream(MOCK_SENTINEL_OUTPUT),
    ):
        with client.stream("POST", "/api/analyze", json=SAMPLE_CRASH_DUMP) as resp:
            assert resp.status_code == 200, (
                f"POST /api/analyze returned {resp.status_code}, expected 200"
            )
            ct = resp.headers.get("content-type", "")
            assert "text/event-stream" in ct, (
                f"Expected Content-Type: text/event-stream, got: {ct!r}"
            )


def test_5_stream_events_have_required_fields():
    """Each SSE event from POST /api/analyze has 'event_type' and 'data' fields."""
    client = TestClient(app, raise_server_exceptions=False)
    with patch(
        "app.agent.agent.SentinelAgent.analyze_crash_dump_stream",
        return_value=_make_mock_stream(MOCK_SENTINEL_OUTPUT),
    ):
        with client.stream("POST", "/api/analyze", json=SAMPLE_CRASH_DUMP) as resp:
            events = _collect_sse_events(resp)

    assert events, "No SSE events received from POST /api/analyze"
    for i, ev in enumerate(events):
        assert "event_type" in ev, (
            f"Event {i}: missing 'event_type' field. Got keys: {list(ev.keys())}"
        )
        assert "data" in ev, (
            f"Event {i}: missing 'data' field. Got keys: {list(ev.keys())}"
        )


def test_6_stream_event_types_are_valid():
    """All event_type values in the stream are one of the 6 canonical SSEEventType values."""
    valid_types = {t.value for t in SSEEventType}
    client = TestClient(app, raise_server_exceptions=False)
    with patch(
        "app.agent.agent.SentinelAgent.analyze_crash_dump_stream",
        return_value=_make_mock_stream(MOCK_SENTINEL_OUTPUT),
    ):
        with client.stream("POST", "/api/analyze", json=SAMPLE_CRASH_DUMP) as resp:
            events = _collect_sse_events(resp)

    for i, ev in enumerate(events):
        et = ev.get("event_type")
        assert et in valid_types, (
            f"Event {i}: event_type {et!r} is not a valid SSEEventType. "
            f"Valid: {valid_types}"
        )


def test_7_stream_first_event_is_status():
    """The first SSE event from the stream has event_type 'status'."""
    client = TestClient(app, raise_server_exceptions=False)
    with patch(
        "app.agent.agent.SentinelAgent.analyze_crash_dump_stream",
        return_value=_make_mock_stream(MOCK_SENTINEL_OUTPUT),
    ):
        with client.stream("POST", "/api/analyze", json=SAMPLE_CRASH_DUMP) as resp:
            events = _collect_sse_events(resp)

    assert events, "No events received"
    first = events[0]
    assert first.get("event_type") == "status", (
        f"First event type is {first.get('event_type')!r}, expected 'status'"
    )


def test_8_stream_contains_result_event():
    """The stream contains exactly one 'result' event."""
    client = TestClient(app, raise_server_exceptions=False)
    with patch(
        "app.agent.agent.SentinelAgent.analyze_crash_dump_stream",
        return_value=_make_mock_stream(MOCK_SENTINEL_OUTPUT),
    ):
        with client.stream("POST", "/api/analyze", json=SAMPLE_CRASH_DUMP) as resp:
            events = _collect_sse_events(resp)

    result_events = [ev for ev in events if ev.get("event_type") == "result"]
    assert len(result_events) == 1, (
        f"Expected exactly 1 'result' event, got {len(result_events)}"
    )


def test_9_result_event_validates_as_sentinel_output():
    """The 'result' event's data field parses as a valid SentinelOutput."""
    client = TestClient(app, raise_server_exceptions=False)
    with patch(
        "app.agent.agent.SentinelAgent.analyze_crash_dump_stream",
        return_value=_make_mock_stream(MOCK_SENTINEL_OUTPUT),
    ):
        with client.stream("POST", "/api/analyze", json=SAMPLE_CRASH_DUMP) as resp:
            events = _collect_sse_events(resp)

    result_events = [ev for ev in events if ev.get("event_type") == "result"]
    assert result_events, "No result event found"

    result_data_str = result_events[0].get("data", "")
    assert result_data_str, "Result event 'data' field is empty"

    try:
        parsed = json.loads(result_data_str)
    except json.JSONDecodeError as e:
        raise AssertionError(
            f"Result event 'data' is not valid JSON: {e}\nRaw: {result_data_str[:200]}"
        )

    # Validate against the full SentinelOutput Pydantic schema
    try:
        validated = SentinelOutput.model_validate(parsed)
    except Exception as e:
        raise AssertionError(
            f"Result event 'data' failed SentinelOutput validation: {e}\n"
            f"Parsed keys: {list(parsed.keys())}"
        )

    # Extra spot-checks
    assert len(validated.hypotheses) == 3, (
        f"Expected 3 hypotheses, got {len(validated.hypotheses)}"
    )
    assert validated.hypotheses[0].rank == 1
    assert validated.hypotheses[0].affected_component  # must be non-empty
    assert len(validated.recovery_plan) >= 1


def test_10_error_event_on_agent_failure():
    """If the agent raises, an 'error' SSE event is emitted (no server crash)."""
    def _failing_stream(*args, **kwargs):
        raise RuntimeError("Simulated LLM failure for testing")

    client = TestClient(app, raise_server_exceptions=False)
    with patch(
        "app.agent.agent.SentinelAgent.analyze_crash_dump_stream",
        side_effect=_failing_stream,
    ):
        with client.stream("POST", "/api/analyze", json=SAMPLE_CRASH_DUMP) as resp:
            assert resp.status_code == 200, (
                f"Expected 200 even on agent failure, got {resp.status_code}"
            )
            events = _collect_sse_events(resp)

    error_events = [ev for ev in events if ev.get("event_type") == "error"]
    assert error_events, (
        f"Expected at least one 'error' event when agent fails; "
        f"got event_types: {[ev.get('event_type') for ev in events]}"
    )


# ---------------------------------------------------------------------------
# Main (standalone execution)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run_test(1,  "GET /health and /api/health return 200 {status: ok}",     test_1_health_check)
    run_test(2,  "GET /scenarios and /api/scenarios return scenario list",   test_2_scenarios_endpoint)
    run_test(3,  "Each scenario has required + source_type fields",          test_3_scenarios_have_required_fields)
    run_test(4,  "POST /api/analyze returns text/event-stream",              test_4_post_analyze_returns_event_stream)
    run_test(5,  "All SSE events have event_type and data fields",           test_5_stream_events_have_required_fields)
    run_test(6,  "All event_type values are canonical SSEEventType values",  test_6_stream_event_types_are_valid)
    run_test(7,  "First SSE event has event_type='status'",                  test_7_stream_first_event_is_status)
    run_test(8,  "Stream contains exactly one result event",                 test_8_stream_contains_result_event)
    run_test(9,  "Result event data validates as SentinelOutput",            test_9_result_event_validates_as_sentinel_output)
    run_test(10, "Agent failure emits error event without crashing server",  test_10_error_event_on_agent_failure)

    passed = sum(1 for ok, _, _ in results if ok)
    total  = len(results)

    print()
    print("=" * 50)
    print(f"Results: {passed} passed, {total - passed} failed")
    print("=" * 50)

    sys.exit(0 if passed == total else 1)
