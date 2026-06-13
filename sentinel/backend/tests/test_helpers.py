"""
test_helpers.py — Unit tests for SatelliteFaultSimulator shared helpers
=======================================================================

Covers task 3.7:
  - _noisy_value returns a float within a reasonable band around the nominal midpoint
  - _make_reading produces all required keys and correct types
  - _apply_nan_dropout sets value="NaN" and anomalous=False for replaced readings
  - _make_event produces all required keys
  - _make_operating_context raises ValueError for invalid mission_phase

Requirements: 4.6–4.17, 5.1–5.3
"""

import unittest
from unittest.mock import patch
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from simulation.fault_simulator import SatelliteFaultSimulator


# ---------------------------------------------------------------------------
# Helpers / shared constants
# ---------------------------------------------------------------------------

CANONICAL_PARAMETERS = [
    "V_bat", "SoC_pct", "I_sa", "V_bus", "Heater_power_W",
    "RW_speed_rpm", "Gyro_rate_degs", "Star_tracker_status",
    "Sun_sensor_angle_deg", "Attitude_error_deg", "OBC_temp_C",
    "CPU_load_pct", "Memory_usage_MB", "Watchdog_counter", "SEU_counter",
    "Fault_register", "Safe_mode_entry_count", "Transponder_lock",
    "SNR_dB", "Component_temp_C", "Heater_enable_flag",
]

READING_REQUIRED_KEYS = {
    "timestamp_offset", "parameter", "value", "unit",
    "nominal_min", "nominal_max", "anomalous",
}

EVENT_REQUIRED_KEYS = {"time_offset", "source", "message"}

OPERATING_CONTEXT_REQUIRED_KEYS = {
    "orbital_position", "sun_angle_deg", "mission_phase",
    "minutes_since_last_ground_contact", "safe_mode_entry_count_total",
}

VALID_MISSION_PHASES = ("nominal_science", "maneuver", "commissioning")


def _make_sim() -> SatelliteFaultSimulator:
    """Return a fresh simulator with a fixed seed for deterministic tests."""
    return SatelliteFaultSimulator(seed=42)


# ---------------------------------------------------------------------------
# _noisy_value  (Requirement 5.1)
# ---------------------------------------------------------------------------

class TestNoisyValue(unittest.TestCase):
    """Tests for _noisy_value (Req 5.1)."""

    def setUp(self):
        self.sim = _make_sim()

    def test_returns_float(self):
        """_noisy_value must return a float for every canonical parameter."""
        for param in CANONICAL_PARAMETERS:
            with self.subTest(parameter=param):
                result = self.sim._noisy_value(param)
                self.assertIsInstance(result, float,
                    msg=f"_noisy_value({param!r}) returned {type(result).__name__}, expected float.")

    def test_value_near_nominal_midpoint(self):
        """Result should lie within 20 % of the nominal midpoint (3σ ≈ 6 %)."""
        for param in CANONICAL_PARAMETERS:
            rng = self.sim._param_ranges[param]
            midpoint = (rng["nominal_min"] + rng["nominal_max"]) / 2.0
            # Skip parameters whose midpoint is 0 (Fault_register, etc.)
            # – noise is tiny there, absolute band check is used instead.
            if midpoint == 0.0:
                continue
            with self.subTest(parameter=param):
                # Sample 20 values; every one must be within ±20 % of midpoint.
                for _ in range(20):
                    value = self.sim._noisy_value(param)
                    tolerance = abs(midpoint) * 0.20
                    self.assertAlmostEqual(
                        value, midpoint, delta=tolerance,
                        msg=(
                            f"_noisy_value({param!r}) = {value:.4f} deviates more than "
                            f"20 % from midpoint {midpoint:.4f}."
                        ),
                    )

    def test_zero_midpoint_params_return_small_value(self):
        """Parameters with a 0 midpoint (e.g. Fault_register) should still return a float near 0."""
        zero_midpoint_params = [
            p for p in CANONICAL_PARAMETERS
            if (self.sim._param_ranges[p]["nominal_min"] +
                self.sim._param_ranges[p]["nominal_max"]) / 2.0 == 0.0
        ]
        for param in zero_midpoint_params:
            with self.subTest(parameter=param):
                value = self.sim._noisy_value(param)
                self.assertIsInstance(value, float)
                # Noise σ is fixed at 0.01 when midpoint is 0; expect abs < 0.1
                self.assertLess(abs(value), 0.1,
                    msg=f"_noisy_value({param!r}) = {value} is unexpectedly large for a zero-midpoint param.")

    def test_noise_is_not_zero(self):
        """Two consecutive calls for the same parameter should (almost certainly) differ."""
        param = "V_bat"
        results = {self.sim._noisy_value(param) for _ in range(10)}
        # With seed=42 and real Gaussian noise, we expect at least 2 distinct values.
        self.assertGreater(len(results), 1,
            msg="_noisy_value appears to return a constant — noise may not be applied.")


# ---------------------------------------------------------------------------
# _make_reading  (Requirements 4.6–4.10)
# ---------------------------------------------------------------------------

class TestMakeReading(unittest.TestCase):
    """Tests for _make_reading (Req 4.6–4.10)."""

    def setUp(self):
        self.sim = _make_sim()

    # -- Key presence and types -------------------------------------------

    def test_all_required_keys_present(self):
        """Every required key must appear in the returned dict."""
        reading = self.sim._make_reading("T-300s", "V_bat", 29.5, False)
        self.assertEqual(reading.keys(), READING_REQUIRED_KEYS)

    def test_timestamp_offset_echoed(self):
        """timestamp_offset in the output must equal the argument."""
        reading = self.sim._make_reading("T-0s", "SoC_pct", 75.0, False)
        self.assertEqual(reading["timestamp_offset"], "T-0s")

    def test_parameter_echoed(self):
        """parameter in the output must equal the argument."""
        reading = self.sim._make_reading("T-60s", "I_sa", 4.2, False)
        self.assertEqual(reading["parameter"], "I_sa")

    def test_value_echoed_float(self):
        """A float value must be passed through unchanged."""
        reading = self.sim._make_reading("T-120s", "SNR_dB", 18.3, False)
        self.assertEqual(reading["value"], 18.3)

    def test_value_echoed_nan_string(self):
        """The sentinel string 'NaN' must be passed through unchanged."""
        reading = self.sim._make_reading("T-60s", "Gyro_rate_degs", "NaN", True)
        self.assertEqual(reading["value"], "NaN")

    def test_anomalous_true(self):
        """anomalous=True must be stored as True."""
        reading = self.sim._make_reading("T-30s", "CPU_load_pct", 98.0, True)
        self.assertIs(reading["anomalous"], True)

    def test_anomalous_false(self):
        """anomalous=False must be stored as False."""
        reading = self.sim._make_reading("T-30s", "CPU_load_pct", 45.0, False)
        self.assertIs(reading["anomalous"], False)

    def test_anomalous_is_bool(self):
        """anomalous must be a bool, not an int or other truthy value."""
        reading = self.sim._make_reading("T-30s", "V_bus", 30.0, False)
        self.assertIsInstance(reading["anomalous"], bool)

    def test_unit_is_string(self):
        """unit must be a non-empty string drawn from the parameter ranges."""
        reading = self.sim._make_reading("T-0s", "V_bat", 30.0, False)
        self.assertIsInstance(reading["unit"], str)
        self.assertTrue(len(reading["unit"]) > 0)

    def test_unit_matches_param_range(self):
        """unit must equal the value stored in the parameter's nominal range."""
        for param in CANONICAL_PARAMETERS:
            with self.subTest(parameter=param):
                reading = self.sim._make_reading("T-0s", param, 0.0, False)
                expected_unit = self.sim._param_ranges[param]["unit"]
                self.assertEqual(reading["unit"], expected_unit)

    def test_nominal_min_is_numeric(self):
        """nominal_min in the reading must be an int or float."""
        reading = self.sim._make_reading("T-0s", "V_bat", 30.0, False)
        self.assertIsInstance(reading["nominal_min"], (int, float))

    def test_nominal_max_is_numeric(self):
        """nominal_max in the reading must be an int or float."""
        reading = self.sim._make_reading("T-0s", "V_bat", 30.0, False)
        self.assertIsInstance(reading["nominal_max"], (int, float))

    def test_nominal_values_match_param_range(self):
        """nominal_min/nominal_max must mirror the stored parameter ranges."""
        for param in CANONICAL_PARAMETERS:
            with self.subTest(parameter=param):
                reading = self.sim._make_reading("T-0s", param, 0.0, False)
                rng = self.sim._param_ranges[param]
                self.assertEqual(reading["nominal_min"], rng["nominal_min"])
                self.assertEqual(reading["nominal_max"], rng["nominal_max"])

    # -- Validation: timestamp_offset format ------------------------------

    def test_valid_timestamp_offsets_accepted(self):
        """Various valid timestamp_offset strings should not raise."""
        valid_offsets = ["T-0s", "T-60s", "T-180s", "T-300s", "T-3600s"]
        for offset in valid_offsets:
            with self.subTest(offset=offset):
                # Should not raise
                self.sim._make_reading(offset, "V_bat", 30.0, False)

    def test_invalid_timestamp_offset_raises_value_error(self):
        """A malformed timestamp_offset must raise ValueError."""
        invalid_offsets = [
            "T-300",      # missing trailing 's'
            "300s",       # missing 'T-' prefix
            "T+300s",     # wrong sign
            "T-300 s",    # space before 's'
            "T-",         # missing seconds
            "",           # empty string
            "T-abc s",    # non-numeric
        ]
        for offset in invalid_offsets:
            with self.subTest(offset=offset):
                with self.assertRaises(ValueError,
                    msg=f"Expected ValueError for timestamp_offset={offset!r}"):
                    self.sim._make_reading(offset, "V_bat", 30.0, False)

    # -- Validation: parameter name ---------------------------------------

    def test_all_canonical_parameters_accepted(self):
        """Each of the 21 canonical parameter names must be accepted without error."""
        for param in CANONICAL_PARAMETERS:
            with self.subTest(parameter=param):
                self.sim._make_reading("T-0s", param, 0.0, False)

    def test_unknown_parameter_raises_value_error(self):
        """An unrecognised parameter name must raise ValueError."""
        invalid_params = ["voltage", "BATTERY", "v_bat", "temperature", ""]
        for param in invalid_params:
            with self.subTest(parameter=param):
                with self.assertRaises(ValueError,
                    msg=f"Expected ValueError for parameter={param!r}"):
                    self.sim._make_reading("T-0s", param, 0.0, False)

    def test_extra_keys_not_present(self):
        """The reading dict must contain exactly the 7 required keys, no more."""
        reading = self.sim._make_reading("T-0s", "V_bat", 30.0, False)
        self.assertEqual(set(reading.keys()), READING_REQUIRED_KEYS)


# ---------------------------------------------------------------------------
# _apply_nan_dropout  (Requirements 5.2, 5.3)
# ---------------------------------------------------------------------------

class TestApplyNanDropout(unittest.TestCase):
    """Tests for _apply_nan_dropout (Req 5.2, 5.3)."""

    def setUp(self):
        self.sim = _make_sim()

    def _make_nominal_reading(self, param: str = "V_bat", offset: str = "T-0s") -> dict:
        """Convenience wrapper returning a non-anomalous reading."""
        return self.sim._make_reading(offset, param, 30.0, False)

    def _make_anomalous_reading(self, param: str = "V_bat", offset: str = "T-0s") -> dict:
        """Convenience wrapper returning an anomalous reading."""
        return self.sim._make_reading(offset, param, 0.0, True)

    # -- Dropout behaviour ------------------------------------------------

    def test_nan_dropout_sets_value_to_nan_string(self):
        """When dropout fires, value must become the string 'NaN'."""
        reading = self._make_nominal_reading()
        # Force dropout by patching random.random to return 0.0 (< 0.05).
        with patch("simulation.fault_simulator.random.random", return_value=0.0):
            self.sim._apply_nan_dropout([reading])
        self.assertEqual(reading["value"], "NaN",
            msg="Dropped-out reading should have value='NaN'.")

    def test_nan_dropout_sets_anomalous_to_false(self):
        """When dropout fires, anomalous must remain / be set to False (Req 5.3)."""
        reading = self._make_nominal_reading()
        with patch("simulation.fault_simulator.random.random", return_value=0.0):
            self.sim._apply_nan_dropout([reading])
        self.assertIs(reading["anomalous"], False,
            msg="Dropped-out reading should have anomalous=False.")

    def test_no_dropout_when_random_above_threshold(self):
        """When random() >= 0.05, the reading must not be modified."""
        reading = self._make_nominal_reading()
        original_value = reading["value"]
        with patch("simulation.fault_simulator.random.random", return_value=0.99):
            self.sim._apply_nan_dropout([reading])
        self.assertEqual(reading["value"], original_value,
            msg="Reading should be unchanged when dropout probability is not met.")
        self.assertIs(reading["anomalous"], False)

    def test_anomalous_readings_never_modified(self):
        """Anomalous readings must never be touched by dropout."""
        reading = self._make_anomalous_reading()
        original_value = reading["value"]
        # Even when random() returns 0.0 (dropout would fire for non-anomalous),
        # an already-anomalous reading must not be mutated.
        with patch("simulation.fault_simulator.random.random", return_value=0.0):
            self.sim._apply_nan_dropout([reading])
        self.assertEqual(reading["value"], original_value,
            msg="Anomalous reading value should not be replaced by dropout.")
        self.assertIs(reading["anomalous"], True,
            msg="Anomalous reading anomalous flag should not be changed by dropout.")

    def test_returns_same_list_object(self):
        """_apply_nan_dropout must return the same list it received (mutates in place)."""
        readings = [self._make_nominal_reading()]
        returned = self.sim._apply_nan_dropout(readings)
        self.assertIs(returned, readings,
            msg="_apply_nan_dropout should return the same list object (in-place mutation).")

    def test_empty_list_handled(self):
        """Passing an empty list must not raise and must return an empty list."""
        result = self.sim._apply_nan_dropout([])
        self.assertEqual(result, [])

    def test_mixed_readings_only_non_anomalous_eligible(self):
        """In a mixed list, only non-anomalous readings should be eligible for dropout."""
        nominal = self._make_nominal_reading("V_bat", "T-0s")
        anomalous = self._make_anomalous_reading("I_sa", "T-60s")
        with patch("simulation.fault_simulator.random.random", return_value=0.0):
            self.sim._apply_nan_dropout([nominal, anomalous])
        # Nominal reading should be dropped out.
        self.assertEqual(nominal["value"], "NaN")
        # Anomalous reading must not be touched.
        self.assertEqual(anomalous["value"], 0.0)
        self.assertIs(anomalous["anomalous"], True)

    def test_dropout_probability_approximately_5_percent(self):
        """Over a large sample, the dropout rate should be close to 5 %."""
        n = 2000
        sim = SatelliteFaultSimulator(seed=0)
        readings = [sim._make_reading("T-0s", "V_bat", 30.0, False) for _ in range(n)]
        sim._apply_nan_dropout(readings)
        nan_count = sum(1 for r in readings if r["value"] == "NaN")
        rate = nan_count / n
        # Expect approximately 5 % ± 2 pp (well within 6σ for p=0.05, n=2000).
        self.assertAlmostEqual(rate, 0.05, delta=0.02,
            msg=f"Dropout rate {rate:.3f} is not close to expected 5 %.")


# ---------------------------------------------------------------------------
# _make_event  (Requirements 4.11–4.13)
# ---------------------------------------------------------------------------

class TestMakeEvent(unittest.TestCase):
    """Tests for _make_event (Req 4.11–4.13)."""

    def setUp(self):
        self.sim = _make_sim()

    def test_all_required_keys_present(self):
        """Every required key must appear in the returned dict."""
        event = self.sim._make_event("T-00:05:00", "EPS_MONITOR", "Low voltage warning")
        self.assertEqual(event.keys(), EVENT_REQUIRED_KEYS)

    def test_time_offset_echoed(self):
        """time_offset must equal the argument."""
        event = self.sim._make_event("T-00:04:21", "OBC", "Test")
        self.assertEqual(event["time_offset"], "T-00:04:21")

    def test_source_echoed(self):
        """source must equal the argument."""
        event = self.sim._make_event("T-00:01:00", "ADCS_CTRL", "Gyro failure detected")
        self.assertEqual(event["source"], "ADCS_CTRL")

    def test_message_echoed(self):
        """message must equal the argument."""
        msg_text = "Transponder lock lost"
        event = self.sim._make_event("T-00:00:30", "COMMS", msg_text)
        self.assertEqual(event["message"], msg_text)

    def test_all_values_are_strings(self):
        """All three values in the event dict must be strings."""
        event = self.sim._make_event("T-00:02:15", "TCS", "Over-temp alert")
        for key in EVENT_REQUIRED_KEYS:
            with self.subTest(key=key):
                self.assertIsInstance(event[key], str,
                    msg=f"event[{key!r}] should be a str.")

    def test_no_extra_keys(self):
        """The event dict must contain exactly the 3 required keys."""
        event = self.sim._make_event("T-00:00:00", "SRC", "msg")
        self.assertEqual(set(event.keys()), EVENT_REQUIRED_KEYS)

    # -- Valid time_offset formats ----------------------------------------

    def test_valid_time_offsets_accepted(self):
        """Various well-formed T-HH:MM:SS strings must not raise."""
        valid = [
            "T-00:00:00",
            "T-00:00:01",
            "T-00:01:00",
            "T-00:04:21",
            "T-01:00:00",
            "T-99:59:59",
        ]
        for offset in valid:
            with self.subTest(offset=offset):
                self.sim._make_event(offset, "SRC", "msg")

    # -- Invalid time_offset formats -------------------------------------

    def test_invalid_time_offset_raises_value_error(self):
        """Malformed time_offset strings must raise ValueError."""
        invalid = [
            "T-0:00:00",      # single-digit hours
            "T-00:0:00",      # single-digit minutes
            "T-00:00:0",      # single-digit seconds
            "00:04:21",       # missing 'T-' prefix
            "T+00:04:21",     # wrong sign
            "T-00:04",        # missing seconds component
            "T-",             # empty time part
            "",               # empty string
            "T-00:04:21Z",    # extra character
        ]
        for offset in invalid:
            with self.subTest(offset=offset):
                with self.assertRaises(ValueError,
                    msg=f"Expected ValueError for time_offset={offset!r}"):
                    self.sim._make_event(offset, "SRC", "msg")


# ---------------------------------------------------------------------------
# _make_hardware_state  (Requirement 4.14)
# ---------------------------------------------------------------------------

class TestMakeHardwareState(unittest.TestCase):
    """Tests for _make_hardware_state (Req 4.14)."""

    REQUIRED_KEYS = {
        "last_reset_cause",
        "SEU_event_count_since_boot",
        "running_processes",
        "memory_allocation_MB",
    }

    def setUp(self):
        self.sim = _make_sim()

    def _make(self, **kwargs):
        defaults = dict(
            last_reset_cause="POWER_ON",
            seu_count=3,
            processes=["task_scheduler", "telemetry_mgr"],
            memory_MB=128.5,
        )
        defaults.update(kwargs)
        return self.sim._make_hardware_state(**defaults)

    def test_all_required_keys_present(self):
        """All four required keys must be present."""
        state = self._make()
        self.assertEqual(set(state.keys()), self.REQUIRED_KEYS)

    def test_no_extra_keys(self):
        """Exactly four keys — no more."""
        state = self._make()
        self.assertEqual(set(state.keys()), self.REQUIRED_KEYS)

    def test_last_reset_cause_echoed(self):
        """last_reset_cause must equal the argument."""
        state = self._make(last_reset_cause="WATCHDOG_TIMEOUT")
        self.assertEqual(state["last_reset_cause"], "WATCHDOG_TIMEOUT")

    def test_seu_count_echoed(self):
        """SEU_event_count_since_boot must equal the seu_count argument."""
        state = self._make(seu_count=7)
        self.assertEqual(state["SEU_event_count_since_boot"], 7)

    def test_processes_echoed(self):
        """running_processes must equal the processes argument."""
        procs = ["proc_a", "proc_b", "proc_c"]
        state = self._make(processes=procs)
        self.assertEqual(state["running_processes"], procs)

    def test_memory_mb_echoed(self):
        """memory_allocation_MB must equal the memory_MB argument."""
        state = self._make(memory_MB=256.0)
        self.assertEqual(state["memory_allocation_MB"], 256.0)

    def test_running_processes_is_list(self):
        """running_processes must be stored as a list."""
        state = self._make()
        self.assertIsInstance(state["running_processes"], list)

    def test_memory_allocation_is_numeric(self):
        """memory_allocation_MB must be int or float."""
        state = self._make()
        self.assertIsInstance(state["memory_allocation_MB"], (int, float))


# ---------------------------------------------------------------------------
# _make_operating_context  (Requirements 4.15–4.17)
# ---------------------------------------------------------------------------

class TestMakeOperatingContext(unittest.TestCase):
    """Tests for _make_operating_context (Req 4.15–4.17)."""

    def setUp(self):
        self.sim = _make_sim()

    def _make(self, **kwargs) -> dict:
        defaults = dict(
            eclipse_fraction=0.0,
            sun_angle_deg=45.0,
            mission_phase="nominal_science",
            minutes_since_contact=30,
            safe_mode_count=1,
        )
        defaults.update(kwargs)
        return self.sim._make_operating_context(**defaults)

    # -- Key presence and exact set --------------------------------------

    def test_all_required_keys_present(self):
        """All five required keys must be present."""
        ctx = self._make()
        self.assertEqual(set(ctx.keys()), OPERATING_CONTEXT_REQUIRED_KEYS)

    def test_no_extra_keys(self):
        """Exactly five keys — no more."""
        ctx = self._make()
        self.assertEqual(set(ctx.keys()), OPERATING_CONTEXT_REQUIRED_KEYS)

    # -- Field values ----------------------------------------------------

    def test_sun_angle_echoed(self):
        """sun_angle_deg must equal the argument."""
        ctx = self._make(sun_angle_deg=72.5)
        self.assertEqual(ctx["sun_angle_deg"], 72.5)

    def test_mission_phase_echoed(self):
        """mission_phase must equal the argument for each valid phase."""
        for phase in VALID_MISSION_PHASES:
            with self.subTest(phase=phase):
                ctx = self._make(mission_phase=phase)
                self.assertEqual(ctx["mission_phase"], phase)

    def test_minutes_since_contact_echoed(self):
        """minutes_since_last_ground_contact must equal the argument."""
        ctx = self._make(minutes_since_contact=90)
        self.assertEqual(ctx["minutes_since_last_ground_contact"], 90)

    def test_safe_mode_count_echoed(self):
        """safe_mode_entry_count_total must equal the argument."""
        ctx = self._make(safe_mode_count=5)
        self.assertEqual(ctx["safe_mode_entry_count_total"], 5)

    # -- orbital_position format (Req 4.17) ------------------------------

    def test_orbital_position_format_with_zero(self):
        """orbital_position must be formatted as 'eclipse_fraction: 0.0'."""
        ctx = self._make(eclipse_fraction=0.0)
        self.assertEqual(ctx["orbital_position"], "eclipse_fraction: 0.0")

    def test_orbital_position_format_with_nonzero(self):
        """orbital_position must include the numeric value to 1 decimal place."""
        ctx = self._make(eclipse_fraction=0.75)
        self.assertEqual(ctx["orbital_position"], "eclipse_fraction: 0.8",
            msg="eclipse_fraction should be formatted to one decimal place.")

    def test_orbital_position_contains_eclipse_fraction_key(self):
        """orbital_position string must start with 'eclipse_fraction:'."""
        ctx = self._make(eclipse_fraction=0.3)
        self.assertTrue(ctx["orbital_position"].startswith("eclipse_fraction:"),
            msg=f"orbital_position {ctx['orbital_position']!r} does not start with 'eclipse_fraction:'.")

    def test_orbital_position_is_string(self):
        """orbital_position must be a string."""
        ctx = self._make()
        self.assertIsInstance(ctx["orbital_position"], str)

    # -- mission_phase validation (Req 4.16) ----------------------------

    def test_valid_mission_phases_accepted(self):
        """Each of the three valid mission phases must not raise."""
        for phase in VALID_MISSION_PHASES:
            with self.subTest(phase=phase):
                self._make(mission_phase=phase)

    def test_invalid_mission_phase_raises_value_error(self):
        """An unrecognised mission_phase must raise ValueError."""
        invalid_phases = [
            "safe_mode",
            "NOMINAL_SCIENCE",   # wrong case
            "nominal science",   # space instead of underscore
            "orbit",
            "",
            "maintenance",
        ]
        for phase in invalid_phases:
            with self.subTest(phase=phase):
                with self.assertRaises(ValueError,
                    msg=f"Expected ValueError for mission_phase={phase!r}"):
                    self._make(mission_phase=phase)

    def test_value_error_message_is_descriptive(self):
        """ValueError message should mention the invalid phase."""
        try:
            self._make(mission_phase="bad_phase")
            self.fail("Expected ValueError was not raised.")
        except ValueError as exc:
            self.assertIn("bad_phase", str(exc),
                msg="ValueError message should include the invalid phase value.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
