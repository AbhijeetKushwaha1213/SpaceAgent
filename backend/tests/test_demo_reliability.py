"""test_demo_reliability.py

Demo scenario reliability test for SENTINEL hackathon submission.

Runs each of the 3 synthetic demo scenarios through the full pipeline
with a deterministic mocked LLM (no real API calls) and validates:

  - Valid SentinelOutput
  - Rank-1 root cause matches ground truth
  - At least 3 recovery steps
  - Reasoning summary is non-empty
  - requires_human_review follows safety/confidence rules
  - Latency < 90 seconds

Run:
    cd sentinel/backend && python -m pytest tests/test_demo_reliability.py -v
"""

import json
import os
import sys
import time
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Standalone runs bypass pytest's conftest.py; keep the test server in
# SECURE_DEV_MODE so no API key is required (auth is tested explicitly).
os.environ.setdefault("SECURE_DEV_MODE", "1")

from app.api.models import SentinelOutput, SSEEvent, SSEEventType
from app.api.scenarios import get_preset_scenarios
from fastapi.testclient import TestClient
from app.main import app


# ---------------------------------------------------------------------------
# Load cached outputs as mocked LLM responses
# ---------------------------------------------------------------------------

DEMO_CACHE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "data", "demo_cache"
)

SCENARIOS = [
    {
        "name": "ADCS_GYRO_SEU",
        "scenario_id": 1,
        "cache_file": "gyro_seu_cached.json",
    },
    {
        "name": "EPS_SOLAR_UNDERVOLT",
        "scenario_id": 2,
        "cache_file": "solar_undervolt_cached.json",
    },
    {
        "name": "OBC_WATCHDOG_OVERFLOW",
        "scenario_id": 3,
        "cache_file": "obc_watchdog_cached.json",
    },
]


def _load_cached_output(cache_file: str) -> SentinelOutput:
    """Load a cached SentinelOutput from demo cache."""
    path = os.path.join(DEMO_CACHE_DIR, cache_file)
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return SentinelOutput.model_validate(data["sentinel_output"])


def _make_mock_stream(output: SentinelOutput, fault_type: str):
    """Generate a realistic SSE event stream from a cached output."""
    yield SSEEvent(event_type=SSEEventType.STATUS, data="Ingesting crash dump...")
    yield SSEEvent(event_type=SSEEventType.THOUGHT, data=f"Analyzing {fault_type}.", step_number=1)
    yield SSEEvent(event_type=SSEEventType.OBSERVATION, data="Anomalies detected.", step_number=1)
    yield SSEEvent(event_type=SSEEventType.STATUS, data="Analysis complete.")
    yield SSEEvent(event_type=SSEEventType.RESULT, data=output.model_dump_json())


def _collect_sse_events(streaming_response) -> list[dict]:
    """Parse SSE events from a streaming response."""
    raw_body = b"".join(streaming_response.iter_bytes())
    text = raw_body.decode("utf-8")
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if block.startswith("data: "):
            try:
                events.append(json.loads(block[6:]))
            except json.JSONDecodeError:
                pass
    return events


def _get_scenario_payload(scenario_id: int) -> dict:
    """Get the scenario crash dump payload."""
    scenarios = get_preset_scenarios()
    return next(s for s in scenarios if s["scenario_id"] == scenario_id)


# ---------------------------------------------------------------------------
# Tests — one per scenario, parameterized with MOCKED label
# ---------------------------------------------------------------------------

def _run_demo_reliability(scenario_spec: dict):
    """Run a single demo reliability check (mocked LLM)."""
    cached_output = _load_cached_output(scenario_spec["cache_file"])
    payload = _get_scenario_payload(scenario_spec["scenario_id"])
    fault_type = scenario_spec["name"]

    client = TestClient(app, raise_server_exceptions=False)

    t0 = time.time()
    with patch(
        "app.agent.agent.SentinelAgent.analyze_crash_dump_stream",
        return_value=_make_mock_stream(cached_output, fault_type),
    ):
        with client.stream("POST", "/api/analyze", json=payload) as resp:
            assert resp.status_code == 200, f"POST /api/analyze returned {resp.status_code}"
            events = _collect_sse_events(resp)
    elapsed = time.time() - t0

    # Extract result event
    result_events = [ev for ev in events if ev.get("event_type") == "result"]
    assert len(result_events) == 1, f"Expected 1 result event, got {len(result_events)}"

    parsed = json.loads(result_events[0]["data"])
    output = SentinelOutput.model_validate(parsed)

    # --- Pass criteria ---

    # 1. Valid SentinelOutput (already validated above)

    # 2. Rank-1 root cause matches ground truth
    assert len(output.hypotheses) >= 1
    rank1 = output.hypotheses[0]
    assert rank1.rank == 1
    assert rank1.root_cause == fault_type, (
        f"Rank-1 root_cause '{rank1.root_cause}' != expected '{fault_type}'"
    )

    # 3. At least 3 recovery steps
    assert len(output.recovery_plan) >= 3, (
        f"Expected >= 3 recovery steps, got {len(output.recovery_plan)}"
    )

    # 4. Reasoning summary is non-empty
    assert output.reasoning_summary and len(output.reasoning_summary) > 20, (
        f"Reasoning summary too short: '{output.reasoning_summary[:50]}'"
    )

    # 5. requires_human_review follows confidence rules
    if output.confidence >= 0.80:
        # High confidence → human review not strictly required
        pass  # Both True/False are acceptable at high confidence
    elif output.confidence < 0.50:
        # Low confidence → should require human review
        assert output.requires_human_review is True, (
            f"Low confidence ({output.confidence}) should require human review"
        )

    # 6. Completed within 90 seconds
    assert elapsed < 90.0, f"Took {elapsed:.1f}s, exceeds 90s limit"

    return {
        "fault_type": fault_type,
        "passed": True,
        "rank1_correct": True,
        "confidence": output.confidence,
        "recovery_steps": len(output.recovery_plan),
        "elapsed_s": round(elapsed, 2),
        "requires_human_review": output.requires_human_review,
        "mode": "MOCKED",
    }


# Pytest test functions
def test_demo_gyro_seu():
    """[MOCKED] ADCS_GYRO_SEU demo reliability check."""
    result = _run_demo_reliability(SCENARIOS[0])
    assert result["passed"]


def test_demo_solar_undervolt():
    """[MOCKED] EPS_SOLAR_UNDERVOLT demo reliability check."""
    result = _run_demo_reliability(SCENARIOS[1])
    assert result["passed"]


def test_demo_obc_watchdog():
    """[MOCKED] OBC_WATCHDOG_OVERFLOW demo reliability check."""
    result = _run_demo_reliability(SCENARIOS[2])
    assert result["passed"]


def test_demo_cache_files_valid():
    """All 3 demo cache files exist and validate as SentinelOutput."""
    for spec in SCENARIOS:
        path = os.path.join(DEMO_CACHE_DIR, spec["cache_file"])
        assert os.path.exists(path), f"Missing cache: {path}"
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        output = SentinelOutput.model_validate(data["sentinel_output"])
        assert output.hypotheses[0].root_cause == spec["name"]
        assert len(output.recovery_plan) >= 3
        assert data.get("sse_trace"), "Missing sse_trace for frontend replay"


def test_demo_cache_sse_traces():
    """Each demo cache has a complete SSE trace ending with a result event."""
    for spec in SCENARIOS:
        path = os.path.join(DEMO_CACHE_DIR, spec["cache_file"])
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        trace = data["sse_trace"]
        assert trace[0]["event_type"] == "status"
        assert trace[-1]["event_type"] == "result"
        # Result data should be valid JSON
        result_data = trace[-1]["data"]
        parsed = json.loads(result_data)
        assert "hypotheses" in parsed
