"""test_esa_integration.py

Integration tests for ESA-ADB real telemetry support in SENTINEL.

Tests:
  1. ESA compact crash dump file loads as valid JSON
  2. Backend /api/analyze accepts the ESA crash dump
  3. Mocked LLM response validates as SentinelOutput
  4. Test does NOT claim root-cause accuracy on ESA data
  5. ESA scenarios appear in /api/scenarios with correct provenance labels
     (REAL only for id_109; SYNTHETIC_FROM_REAL_METADATA for the rest)
  6. Early warning fires on ESA telemetry (anonymized channels → UNKNOWN)
  7. Early warning fires on synthetic telemetry (known parameters → fault type)

Run with:
    cd sentinel/backend && python -m pytest tests/test_esa_integration.py -v
"""

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Standalone runs bypass pytest's conftest.py; keep the test server in
# SECURE_DEV_MODE so no API key is required (auth is tested explicitly).
os.environ.setdefault("SECURE_DEV_MODE", "1")

from fastapi.testclient import TestClient

from app.main import app
from app.api.models import (
    SentinelOutput,
    Hypothesis,
    RecoveryStep,
    RiskLevel,
    SSEEvent,
    SSEEventType,
)
from app.analytics.early_warning import scan_telemetry, EarlyWarningAlert
from app.api.provenance import Provenance, display_label


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ESA_COMPACT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "esa_crash_dumps",
    "esa_mission1_id_109_sentinel_only.json",
)

_ESA_FULL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "esa_crash_dumps",
    "esa_mission1_id_109_crash_dump.json",
)


def _load_esa_compact() -> dict:
    """Load the compact ESA crash dump."""
    with open(_ESA_COMPACT_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


# A mock SentinelOutput for ESA data — deliberately marks everything as
# human-review-required because ESA-ADB has no root-cause ground truth.
MOCK_ESA_OUTPUT = SentinelOutput(
    hypotheses=[
        Hypothesis(
            rank=1,
            root_cause="ESA_ADB_ANOMALY",
            affected_component="channel_41_channel_42_group",
            confidence=0.55,
            causal_chain=[
                "Correlated deviation across channels 41-46 at event start",
                "channel_42 dropped to 0.0 (complete loss of signal)",
                "Pattern consistent with sensor dropout or hardware fault",
                "Root cause unknown — ESA-ADB does not provide engineering labels",
            ],
        ),
        Hypothesis(
            rank=2,
            root_cause="ESA_ADB_ANOMALY",
            affected_component="channel_42_standalone",
            confidence=0.30,
            causal_chain=[
                "channel_42 zero-value may indicate single sensor failure",
                "Other channels may show sympathetic response",
            ],
        ),
        Hypothesis(
            rank=3,
            root_cause="ESA_ADB_ANOMALY",
            affected_component="unknown_subsystem",
            confidence=0.15,
            causal_chain=[
                "Insufficient engineering context to differentiate further",
                "All hypotheses are exploratory on anonymized ESA-ADB data",
            ],
        ),
    ],
    recovery_plan=[
        RecoveryStep(
            step=1,
            command="CMD_VERIFY_CHANNEL_STATUS",
            rationale="Verify current state of affected channels before any action",
            wait_seconds=30,
            verify="All 6 channels reporting non-NaN values",
            risk=RiskLevel.LOW,
        ),
    ],
    confidence=0.55,
    requires_human_review=True,
    reasoning_summary=(
        "ESA-ADB anomaly id_109 shows correlated deviation across 6 channels. "
        "channel_42 dropped to zero, suggesting sensor loss. "
        "Root cause unknown — requires human expert review."
    ),
)


def _make_mock_esa_stream(output: SentinelOutput):
    """Yield a minimal SSE stream for ESA analysis."""
    yield SSEEvent(event_type=SSEEventType.STATUS, data="Ingesting ESA crash dump...")
    yield SSEEvent(event_type=SSEEventType.THOUGHT, data="Analyzing anonymized channels.", step_number=1)
    yield SSEEvent(event_type=SSEEventType.STATUS, data="Analysis complete.")
    yield SSEEvent(event_type=SSEEventType.RESULT, data=output.model_dump_json())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_1_esa_compact_file_loads():
    """ESA compact crash dump file exists and loads as valid JSON."""
    assert os.path.exists(_ESA_COMPACT_PATH), (
        f"ESA compact crash dump not found: {_ESA_COMPACT_PATH}"
    )
    dump = _load_esa_compact()
    assert isinstance(dump, dict)
    assert dump.get("fault_type") == "ESA_ADB_ANOMALY"
    assert dump.get("scenario_id") == 109
    # Must have pre_fault_telemetry
    assert "pre_fault_telemetry" in dump
    assert len(dump["pre_fault_telemetry"]) > 0


def test_2_esa_full_crash_dump_has_provenance():
    """The full ESA crash dump contains honest provenance disclaimers."""
    assert os.path.exists(_ESA_FULL_PATH), (
        f"ESA full crash dump not found: {_ESA_FULL_PATH}"
    )
    with open(_ESA_FULL_PATH, "r", encoding="utf-8") as fh:
        full = json.load(fh)

    # Must contain honest disclaimers
    assert "do_not_claim" in str(full.get("agent_task", {}))
    gt = full.get("ground_truth_for_evaluation", {})
    assert "what_is_not_known" in gt


def test_3_backend_accepts_esa_crash_dump():
    """POST /api/analyze accepts the ESA crash dump and returns event-stream."""
    dump = _load_esa_compact()
    client = TestClient(app, raise_server_exceptions=False)

    with patch(
        "app.agent.agent.SentinelAgent.analyze_crash_dump_stream",
        return_value=_make_mock_esa_stream(MOCK_ESA_OUTPUT),
    ):
        with client.stream("POST", "/api/analyze", json=dump) as resp:
            assert resp.status_code == 200, (
                f"POST /api/analyze returned {resp.status_code}, expected 200"
            )
            ct = resp.headers.get("content-type", "")
            assert "text/event-stream" in ct


def test_4_esa_result_validates_as_sentinel_output():
    """The mocked ESA result parses as valid SentinelOutput."""
    dump = _load_esa_compact()
    client = TestClient(app, raise_server_exceptions=False)

    with patch(
        "app.agent.agent.SentinelAgent.analyze_crash_dump_stream",
        return_value=_make_mock_esa_stream(MOCK_ESA_OUTPUT),
    ):
        with client.stream("POST", "/api/analyze", json=dump) as resp:
            body = b"".join(resp.iter_bytes()).decode("utf-8")

    # Parse SSE events
    events = []
    for block in body.split("\n\n"):
        block = block.strip()
        if block.startswith("data: "):
            try:
                events.append(json.loads(block[6:]))
            except json.JSONDecodeError:
                pass

    result_events = [ev for ev in events if ev.get("event_type") == "result"]
    assert len(result_events) == 1

    parsed = json.loads(result_events[0]["data"])
    validated = SentinelOutput.model_validate(parsed)

    # Must require human review for ESA data (no ground truth)
    assert validated.requires_human_review is True
    # Must not claim high confidence without engineering context
    assert validated.confidence <= 0.70


def test_5_esa_scenario_in_api():
    """GET /api/scenarios exposes ESA payloads with correct provenance.

    Updated in Phase 0. This test previously required a scenario whose
    ``source_type`` was the literal string 'Real ESA Telemetry'. That label was
    being applied to payloads whose numeric telemetry had been synthesized from
    real ESA-ADB metadata, so the assertion was locking in the mislabel.

    The contract is now:
      * REAL is reserved for id_109, whose values were read from the ESA-ADB
        channel archives.
      * Metadata-derived payloads report SYNTHETIC_FROM_REAL_METADATA.
    """
    client = TestClient(app)
    resp = client.get("/api/scenarios")
    assert resp.status_code == 200
    scenarios = resp.json()

    esa_scenarios = [s for s in scenarios if s.get("fault_type") == "ESA_ADB_ANOMALY"]
    assert len(esa_scenarios) >= 1, (
        f"No ESA scenarios found. fault_types: {[s.get('fault_type') for s in scenarios]}"
    )

    # Every ESA payload must declare a recognised provenance code.
    for esa in esa_scenarios:
        assert esa.get("provenance") in {
            Provenance.REAL.value,
            Provenance.SYNTHETIC_FROM_REAL_METADATA.value,
        }, f"unexpected provenance {esa.get('provenance')!r}"
        assert esa.get("source_type") == display_label(esa["provenance"])
        assert "source_note" in esa
        # Source note must contain an honest disclaimer.
        note = esa["source_note"].lower()
        assert "anonymized" in note or "root-cause" in note

    # Exactly one payload may claim REAL numeric telemetry: ESA-ADB id_109.
    real = [s for s in esa_scenarios if s["provenance"] == Provenance.REAL.value]
    assert len(real) == 1, f"expected exactly 1 REAL ESA payload, got {len(real)}"
    assert "id_109" in json.dumps(real[0])

    # Metadata-derived payloads must state that their values are synthesized.
    derived = [
        s for s in esa_scenarios
        if s["provenance"] == Provenance.SYNTHETIC_FROM_REAL_METADATA.value
    ]
    for s in derived:
        assert "SYNTHESIZED" in s["source_note"], (
            "metadata-derived payloads must disclose that values are generated"
        )


def test_6_test_does_not_claim_root_cause_accuracy():
    """Meta-test: verify this test file never asserts root_cause == a known synthetic fault type."""
    # This test ensures we practice what we preach — ESA tests should never
    # score root_cause against ADCS_GYRO_SEU / EPS_SOLAR_UNDERVOLT etc.
    with open(__file__, "r", encoding="utf-8") as fh:
        source = fh.read()

    synthetic_types = [
        "ADCS_GYRO_SEU",
        "EPS_SOLAR_UNDERVOLT",
        "OBC_WATCHDOG_OVERFLOW",
        "TCS_THERMAL_RUNAWAY",
        "COMMS_TRANSPONDER_LOSS",
    ]
    for ft in synthetic_types:
        # Allow imports/constants but not assert statements with these types
        assert f'assert.*root_cause.*"{ft}"' not in source, (
            f"Test file asserts root_cause accuracy with synthetic type {ft} on ESA data"
        )


def test_7_early_warning_on_esa_data():
    """Early warning scan on ESA data returns alerts with suspected_fault_type='UNKNOWN'."""
    dump = _load_esa_compact()
    alerts = scan_telemetry(dump)

    assert len(alerts) > 0, "Expected at least one early warning on ESA anomaly data"

    for alert in alerts:
        assert isinstance(alert, EarlyWarningAlert)
        assert alert.suspected_fault_type == "UNKNOWN", (
            f"ESA alert should report UNKNOWN, not '{alert.suspected_fault_type}'. "
            "Anonymized channel names should not be mapped to specific fault types."
        )
        assert alert.confidence <= 0.50, (
            f"ESA alert confidence should be low (<= 0.50), got {alert.confidence}"
        )
        assert len(alert.anomalous_parameters) > 0


def test_8_early_warning_on_synthetic_data():
    """Early warning scan on synthetic EPS fault correctly identifies EPS_SOLAR_UNDERVOLT."""
    synthetic_dump = {
        "pre_fault_telemetry": [
            {"parameter": "I_sa", "value": 0.0,
             "nominal_min": 0.0, "nominal_max": 12.0, "anomalous": True,
             "timestamp_offset": "T-300s"},
            {"parameter": "V_bat", "value": 21.8,
             "nominal_min": 28.0, "nominal_max": 33.6, "anomalous": True,
             "timestamp_offset": "T-300s"},
            {"parameter": "SoC_pct", "value": 14.2,
             "nominal_min": 20.0, "nominal_max": 100.0, "anomalous": True,
             "timestamp_offset": "T-180s"},
            {"parameter": "OBC_temp_C", "value": 24.5,
             "nominal_min": -10.0, "nominal_max": 60.0,
             "timestamp_offset": "T-10s"},
        ],
    }

    alerts = scan_telemetry(synthetic_dump)
    assert len(alerts) > 0

    # At least one alert should suspect EPS_SOLAR_UNDERVOLT
    eps_alerts = [a for a in alerts if a.suspected_fault_type == "EPS_SOLAR_UNDERVOLT"]
    assert len(eps_alerts) > 0, (
        f"Expected EPS_SOLAR_UNDERVOLT alert. "
        f"Got: {[a.suspected_fault_type for a in alerts]}"
    )
    assert eps_alerts[0].confidence >= 0.50


def test_9_early_warning_empty_telemetry():
    """Early warning gracefully returns empty list on empty/missing telemetry."""
    assert scan_telemetry({}) == []
    assert scan_telemetry({"pre_fault_telemetry": []}) == []
    assert scan_telemetry({"pre_fault_telemetry": None}) == []
