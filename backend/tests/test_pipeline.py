"""test_pipeline.py

Integration test for the SENTINEL pipeline.  Verifies that fault_simulator,
dataset_generator, and anomaly_detector work together correctly end-to-end.

Run with:
    python test_pipeline.py

Exits with code 0 if all 10 tests pass, code 1 if any fail.
"""

import json
import os
import sys

# Ensure backend/ root is on sys.path for standalone execution
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from simulation.fault_simulator import SatelliteFaultSimulator
from simulation.dataset_generator import generate_dataset
from app.analytics.anomaly_detector import ZScoreAnomalyDetector, SATELLITE_NOMINAL_RANGES

# ---------------------------------------------------------------------------
# Constants shared across tests
# ---------------------------------------------------------------------------

FAULT_TYPES = [
    "EPS_SOLAR_UNDERVOLT",
    "ADCS_GYRO_SEU",
    "OBC_WATCHDOG_OVERFLOW",
    "TCS_THERMAL_RUNAWAY",
    "COMMS_TRANSPONDER_LOSS",
    "MULTI_CASCADE",
]

REQUIRED_KEYS = {
    "scenario_id",
    "timestamp",
    "fault_type",
    "fault_register",
    "pre_fault_telemetry",
    "event_log",
    "hardware_state",
    "operating_context",
}

SUBSYSTEM_MAP = {
    "EPS":  {"V_bat", "SoC_pct", "I_sa", "V_bus"},
    "ADCS": {"RW_speed_rpm", "Gyro_rate_degs", "Attitude_error_deg"},
    "OBC":  {"CPU_load_pct", "Memory_usage_MB", "Watchdog_counter"},
    "TCS":  {"Component_temp_C", "Heater_power_W"},
}

# ---------------------------------------------------------------------------
# Test runner infrastructure
# ---------------------------------------------------------------------------

results: list[tuple[bool, str, str]] = []   # (passed, name, reason)


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
# Shared simulator instance (seed=0, generated once and reused)
# ---------------------------------------------------------------------------

_sim = SatelliteFaultSimulator(seed=0)
_dumps: dict[str, dict] = {
    ft: _sim.generate_crash_dump(ft, scenario_id=i)
    for i, ft in enumerate(FAULT_TYPES, start=1)
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_1_all_fault_types():
    """Simulator generates all 6 fault types with required keys."""
    for ft in FAULT_TYPES:
        dump = _dumps[ft]
        missing = REQUIRED_KEYS - set(dump.keys())
        assert not missing, (
            f"{ft}: missing top-level keys: {missing}"
        )
        assert isinstance(dump["pre_fault_telemetry"], list) and dump["pre_fault_telemetry"], (
            f"{ft}: pre_fault_telemetry is empty or not a list"
        )
        assert isinstance(dump["event_log"], list) and dump["event_log"], (
            f"{ft}: event_log is empty or not a list"
        )


def test_2_fault_register_hex():
    """Fault register is a valid hex string for all fault types."""
    for ft in FAULT_TYPES:
        fr = _dumps[ft]["fault_register"]
        assert isinstance(fr, str) and fr.startswith("0x"), (
            f"{ft}: fault_register {fr!r} does not start with '0x'"
        )
        try:
            int(fr, 16)
        except ValueError:
            raise AssertionError(
                f"{ft}: fault_register {fr!r} is not parseable as hex"
            )


def test_3_eps_anomalous_isa_vbat():
    """EPS fault has anomalous I_sa and V_bat readings."""
    dump = _dumps["EPS_SOLAR_UNDERVOLT"]
    telemetry = dump["pre_fault_telemetry"]

    isa_anomalous  = any(
        r["parameter"] == "I_sa"  and r.get("anomalous") is True
        for r in telemetry
    )
    vbat_anomalous = any(
        r["parameter"] == "V_bat" and r.get("anomalous") is True
        for r in telemetry
    )

    isa_readings  = [r for r in telemetry if r["parameter"] == "I_sa"]
    vbat_readings = [r for r in telemetry if r["parameter"] == "V_bat"]

    assert isa_anomalous, (
        f"I_sa anomalous flag not found in {len(isa_readings)} I_sa reading(s)"
    )
    assert vbat_anomalous, (
        f"V_bat anomalous flag not found in {len(vbat_readings)} V_bat reading(s)"
    )


def test_4_adcs_nan_gyro():
    """ADCS fault has a NaN Gyro_rate_degs reading."""
    dump = _dumps["ADCS_GYRO_SEU"]
    telemetry = dump["pre_fault_telemetry"]

    gyro_readings = [r for r in telemetry if r["parameter"] == "Gyro_rate_degs"]
    nan_found = any(r.get("value") == "NaN" for r in gyro_readings)
    assert nan_found, (
        f"No Gyro_rate_degs reading with value='NaN' found "
        f"({len(gyro_readings)} Gyro_rate_degs reading(s) present)"
    )


def test_5_obc_cpu_at_100():
    """OBC fault shows CPU_load_pct >= 95.0 in at least one reading."""
    dump = _dumps["OBC_WATCHDOG_OVERFLOW"]
    telemetry = dump["pre_fault_telemetry"]

    cpu_readings = [r for r in telemetry if r["parameter"] == "CPU_load_pct"]
    high_cpu = [
        r for r in cpu_readings
        if isinstance(r.get("value"), (int, float)) and r["value"] >= 95.0
    ]
    assert high_cpu, (
        f"No CPU_load_pct reading >= 95.0 found; "
        f"values seen: {[r.get('value') for r in cpu_readings]}"
    )


def test_6_jsonl_generator():
    """JSONL generator produces 12 valid, well-structured output lines."""
    output_path = "test_output.jsonl"
    try:
        generate_dataset(n_samples=12, output_path=output_path, seed=1)

        assert os.path.exists(output_path), "Output file was not created"

        with open(output_path, "r", encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]

        assert len(lines) == 12, (
            f"Expected 12 lines, got {len(lines)}"
        )

        for i, raw in enumerate(lines, start=1):
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"Line {i}: JSON parse error — {exc}")

            assert "messages" in obj, f"Line {i}: missing 'messages' key"
            msgs = obj["messages"]
            assert len(msgs) == 3, (
                f"Line {i}: expected 3 messages, got {len(msgs)}"
            )
            expected_roles = ("system", "user", "assistant")
            for idx, (msg, role) in enumerate(zip(msgs, expected_roles)):
                assert msg.get("role") == role, (
                    f"Line {i} message[{idx}]: role={msg.get('role')!r}, "
                    f"expected {role!r}"
                )

            # Assistant content must be valid JSON with a "hypotheses" key
            assistant_content = msgs[2]["content"]
            try:
                response = json.loads(assistant_content)
            except json.JSONDecodeError as exc:
                raise AssertionError(
                    f"Line {i}: assistant content is not valid JSON — {exc}"
                )
            assert "hypotheses" in response, (
                f"Line {i}: assistant JSON missing 'hypotheses' key"
            )
    finally:
        if os.path.exists(output_path):
            os.remove(output_path)


def test_7_detector_flags_eps_params():
    """Z-score detector flags anomalous parameters for EPS_SOLAR_UNDERVOLT."""
    dump = _dumps["EPS_SOLAR_UNDERVOLT"]
    detector = ZScoreAnomalyDetector(z_threshold=3.0, window_size=10)
    detector.fit_from_nominal_ranges(SATELLITE_NOMINAL_RANGES)

    filtered = detector.filter_crash_dump(dump)
    report   = filtered["anomaly_report"]

    anomalous_params = {e["parameter"] for e in report["anomalous_parameters"]}

    assert anomalous_params, (
        "anomaly_report['anomalous_parameters'] is empty for EPS_SOLAR_UNDERVOLT"
    )
    eps_flagged = anomalous_params & {"I_sa", "V_bat"}
    assert eps_flagged, (
        f"Neither I_sa nor V_bat appears in anomalous_parameters; "
        f"flagged: {anomalous_params}"
    )


def test_8_detector_gyro_critical():
    """Detector classifies NaN Gyro_rate_degs as CRITICAL for ADCS fault."""
    dump = _dumps["ADCS_GYRO_SEU"]
    detector = ZScoreAnomalyDetector(z_threshold=3.0, window_size=10)
    detector.fit_from_nominal_ranges(SATELLITE_NOMINAL_RANGES)

    report = detector.detect(dump["pre_fault_telemetry"])

    critical_gyro = [
        e for e in report["anomalous_parameters"]
        if e["parameter"] == "Gyro_rate_degs"
        and e["anomaly_severity"] == "CRITICAL"
    ]
    assert critical_gyro, (
        "No Gyro_rate_degs entry with anomaly_severity='CRITICAL' found; "
        f"anomalous_parameters: {report['anomalous_parameters']}"
    )


def test_9_cascade_spans_multiple_subsystems():
    """Cascade fault anomalies span at least 2 different subsystems."""
    dump = _dumps["MULTI_CASCADE"]
    detector = ZScoreAnomalyDetector(z_threshold=3.0, window_size=10)
    detector.fit_from_nominal_ranges(SATELLITE_NOMINAL_RANGES)

    report = detector.detect(dump["pre_fault_telemetry"])
    anomalous_params = {e["parameter"] for e in report["anomalous_parameters"]}

    covered_subsystems = {
        subsystem
        for subsystem, params in SUBSYSTEM_MAP.items()
        if anomalous_params & params
    }
    assert len(covered_subsystems) >= 2, (
        f"Anomalous parameters cover only {len(covered_subsystems)} subsystem(s) "
        f"({covered_subsystems}); expected >= 2. "
        f"Flagged params: {anomalous_params}"
    )


def test_10_ground_truth_confidence():
    """Ground truth confidence is high for single-system faults, lower for cascade."""
    single_system = [ft for ft in FAULT_TYPES if ft != "MULTI_CASCADE"]

    for ft in single_system:
        gt = _sim.get_ground_truth(ft)
        assert gt["confidence"] >= 0.80, (
            f"{ft}: confidence {gt['confidence']:.2f} < 0.80"
        )

    gt_cascade = _sim.get_ground_truth("MULTI_CASCADE")
    assert gt_cascade["confidence"] < 0.80, (
        f"MULTI_CASCADE: confidence {gt_cascade['confidence']:.2f} "
        f"expected < 0.80"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run_test(1,  "Simulator generates all 6 fault types",          test_1_all_fault_types)
    run_test(2,  "Fault register is a valid hex string",           test_2_fault_register_hex)
    run_test(3,  "EPS fault has anomalous I_sa and V_bat",         test_3_eps_anomalous_isa_vbat)
    run_test(4,  "ADCS fault has NaN gyro reading",                test_4_adcs_nan_gyro)
    run_test(5,  "OBC fault shows CPU at 100%",                    test_5_obc_cpu_at_100)
    run_test(6,  "JSONL generator produces valid output",          test_6_jsonl_generator)
    run_test(7,  "Z-score detector flags correct params for EPS",  test_7_detector_flags_eps_params)
    run_test(8,  "Z-score detector flags NaN gyro as CRITICAL",    test_8_detector_gyro_critical)
    run_test(9,  "Multi-system cascade has multiple anomalous subsystems", test_9_cascade_spans_multiple_subsystems)
    run_test(10, "Ground truth confidence is consistent with fault complexity", test_10_ground_truth_confidence)

    passed = sum(1 for ok, _, _ in results if ok)
    total  = len(results)

    print()
    print("=" * 16)
    print(f"Results: {passed}/{total} passed")
    print("=" * 16)

    sys.exit(0 if passed == total else 1)
