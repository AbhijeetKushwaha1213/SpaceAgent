"""
test_generate_crash_dump.py — Unit tests for SatelliteFaultSimulator.generate_crash_dump
=========================================================================================

Covers task 4.2:
  - All 8 top-level keys are present for each fault type
  - An invalid fault type raises ValueError
  - scenario_id in the output equals the argument
  - timestamp matches ISO 8601 format and falls within 2026
  - fault_type field equals the argument

Requirements: 3.1–3.4, 4.1–4.4
"""

import re
import datetime
import os
import sys
import unittest
from unittest.mock import patch

# Ensure backend/ root is on sys.path for standalone execution
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from simulation.fault_simulator import SatelliteFaultSimulator


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

VALID_FAULT_TYPES = [
    "EPS_SOLAR_UNDERVOLT",
    "ADCS_GYRO_SEU",
    "OBC_WATCHDOG_OVERFLOW",
    "TCS_THERMAL_RUNAWAY",
    "COMMS_TRANSPONDER_LOSS",
    "MULTI_CASCADE",
]

REQUIRED_TOP_LEVEL_KEYS = {
    "scenario_id",
    "timestamp",
    "fault_type",
    "fault_register",
    "pre_fault_telemetry",
    "event_log",
    "hardware_state",
    "operating_context",
}

# ISO 8601 pattern for "2026-MM-DDTHH:MM:SSZ"
ISO8601_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
)


def _make_sim() -> SatelliteFaultSimulator:
    """Return a fresh, deterministic simulator instance."""
    return SatelliteFaultSimulator(seed=42)


# ---------------------------------------------------------------------------
# Requirement 3.1 — generate_crash_dump method exists and returns a dict
# ---------------------------------------------------------------------------

class TestGenerateCrashDumpExists(unittest.TestCase):
    """generate_crash_dump is a public method that returns a dict (Req 3.1)."""

    def setUp(self):
        self.sim = _make_sim()

    def test_method_is_callable(self):
        """generate_crash_dump must be a callable attribute of the class."""
        self.assertTrue(
            callable(getattr(self.sim, "generate_crash_dump", None)),
            msg="generate_crash_dump must be a callable method.",
        )

    def test_returns_dict(self):
        """generate_crash_dump must return a dict for every valid fault type."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                self.assertIsInstance(
                    result,
                    dict,
                    msg=f"generate_crash_dump({fault_type!r}) should return a dict.",
                )

    def test_has_docstring(self):
        """generate_crash_dump must have a docstring (Req 3.4)."""
        self.assertIsNotNone(
            self.sim.generate_crash_dump.__doc__,
            msg="generate_crash_dump must have a docstring.",
        )
        self.assertTrue(
            len(self.sim.generate_crash_dump.__doc__.strip()) > 0,
            msg="generate_crash_dump docstring must not be empty.",
        )


# ---------------------------------------------------------------------------
# Requirement 3.2, 4.1–4.4 — top-level key presence
# ---------------------------------------------------------------------------

class TestTopLevelKeys(unittest.TestCase):
    """All 8 required top-level keys must be present for every fault type (Req 3.2, 4.1–4.4)."""

    def setUp(self):
        self.sim = _make_sim()

    def test_all_8_keys_present_for_each_fault_type(self):
        """Every valid fault type must produce a dict with exactly the 8 required keys."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                missing = REQUIRED_TOP_LEVEL_KEYS - set(result.keys())
                self.assertFalse(
                    missing,
                    msg=(
                        f"generate_crash_dump({fault_type!r}) is missing "
                        f"top-level keys: {missing}"
                    ),
                )

    def test_no_extra_top_level_keys(self):
        """The output dict must contain exactly the 8 required keys — no extras."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                extra = set(result.keys()) - REQUIRED_TOP_LEVEL_KEYS
                self.assertFalse(
                    extra,
                    msg=(
                        f"generate_crash_dump({fault_type!r}) returned "
                        f"unexpected top-level keys: {extra}"
                    ),
                )

    def test_exactly_8_top_level_keys(self):
        """The constant REQUIRED_TOP_LEVEL_KEYS must enumerate exactly 8 keys."""
        self.assertEqual(
            len(REQUIRED_TOP_LEVEL_KEYS),
            8,
            msg="REQUIRED_TOP_LEVEL_KEYS must list exactly 8 items.",
        )


# ---------------------------------------------------------------------------
# Requirement 3.3 — invalid fault_type raises ValueError
# ---------------------------------------------------------------------------

class TestInvalidFaultTypeRaisesValueError(unittest.TestCase):
    """ValueError must be raised for unrecognised fault types (Req 3.3)."""

    def setUp(self):
        self.sim = _make_sim()

    def test_unknown_string_raises_value_error(self):
        """An arbitrary unrecognised string must raise ValueError."""
        with self.assertRaises(ValueError):
            self.sim.generate_crash_dump("UNKNOWN_FAULT", scenario_id=1)

    def test_empty_string_raises_value_error(self):
        """An empty string must raise ValueError."""
        with self.assertRaises(ValueError):
            self.sim.generate_crash_dump("", scenario_id=1)

    def test_lowercase_variant_raises_value_error(self):
        """Lower-case versions of valid fault types must raise ValueError (case-sensitive)."""
        with self.assertRaises(ValueError):
            self.sim.generate_crash_dump("eps_power_fault", scenario_id=1)

    def test_partial_fault_type_raises_value_error(self):
        """A partial match of a valid fault type must raise ValueError."""
        with self.assertRaises(ValueError):
            self.sim.generate_crash_dump("EPS_POWER", scenario_id=1)

    def test_numeric_string_raises_value_error(self):
        """A numeric string must raise ValueError."""
        with self.assertRaises(ValueError):
            self.sim.generate_crash_dump("1", scenario_id=1)

    def test_none_raises_value_error(self):
        """None as fault_type must raise ValueError (or TypeError — not silently accepted)."""
        with self.assertRaises((ValueError, TypeError)):
            self.sim.generate_crash_dump(None, scenario_id=1)

    def test_value_error_message_is_descriptive(self):
        """ValueError message should mention the invalid fault type."""
        try:
            self.sim.generate_crash_dump("BAD_FAULT_TYPE", scenario_id=1)
            self.fail("Expected ValueError was not raised.")
        except ValueError as exc:
            self.assertIn(
                "BAD_FAULT_TYPE",
                str(exc),
                msg="ValueError message should include the invalid fault type value.",
            )

    def test_all_6_valid_types_do_not_raise(self):
        """None of the 6 valid fault types should raise ValueError."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                # Should not raise
                self.sim.generate_crash_dump(fault_type, scenario_id=1)


# ---------------------------------------------------------------------------
# Requirement 4.1 — scenario_id field equals the argument
# ---------------------------------------------------------------------------

class TestScenarioIdField(unittest.TestCase):
    """scenario_id in the output must equal the argument (Req 4.1)."""

    def setUp(self):
        self.sim = _make_sim()

    def test_scenario_id_echoed_for_each_fault_type(self):
        """scenario_id must equal the argument for every valid fault type."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=42)
                self.assertEqual(
                    result["scenario_id"],
                    42,
                    msg=f"scenario_id should be 42 for fault_type={fault_type!r}.",
                )

    def test_scenario_id_echoed_value_1(self):
        """scenario_id=1 must appear unchanged in the output."""
        result = self.sim.generate_crash_dump("EPS_SOLAR_UNDERVOLT", scenario_id=1)
        self.assertEqual(result["scenario_id"], 1)

    def test_scenario_id_echoed_value_100(self):
        """scenario_id=100 must appear unchanged in the output."""
        result = self.sim.generate_crash_dump("EPS_SOLAR_UNDERVOLT", scenario_id=100)
        self.assertEqual(result["scenario_id"], 100)

    def test_scenario_id_echoed_value_zero(self):
        """scenario_id=0 must appear unchanged in the output."""
        result = self.sim.generate_crash_dump("EPS_SOLAR_UNDERVOLT", scenario_id=0)
        self.assertEqual(result["scenario_id"], 0)

    def test_scenario_id_is_integer(self):
        """The scenario_id field must be stored as an int."""
        result = self.sim.generate_crash_dump("EPS_SOLAR_UNDERVOLT", scenario_id=7)
        self.assertIsInstance(
            result["scenario_id"],
            int,
            msg="scenario_id should be stored as int.",
        )

    def test_different_scenario_ids_are_independent(self):
        """Two calls with different scenario_ids must each return the correct value."""
        r1 = self.sim.generate_crash_dump("EPS_SOLAR_UNDERVOLT", scenario_id=10)
        r2 = self.sim.generate_crash_dump("EPS_SOLAR_UNDERVOLT", scenario_id=20)
        self.assertEqual(r1["scenario_id"], 10)
        self.assertEqual(r2["scenario_id"], 20)


# ---------------------------------------------------------------------------
# Requirement 4.2 — timestamp is ISO 8601 and falls within 2026
# ---------------------------------------------------------------------------

class TestTimestampField(unittest.TestCase):
    """timestamp must be ISO 8601 and within calendar year 2026 (Req 4.2)."""

    def setUp(self):
        self.sim = _make_sim()

    def test_timestamp_is_string(self):
        """timestamp must be a string."""
        result = self.sim.generate_crash_dump("EPS_SOLAR_UNDERVOLT", scenario_id=1)
        self.assertIsInstance(
            result["timestamp"],
            str,
            msg="timestamp must be a string.",
        )

    def test_timestamp_matches_iso8601_pattern(self):
        """timestamp must match the pattern YYYY-MM-DDTHH:MM:SSZ."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                self.assertRegex(
                    result["timestamp"],
                    ISO8601_PATTERN,
                    msg=(
                        f"timestamp {result['timestamp']!r} does not match "
                        f"ISO 8601 pattern YYYY-MM-DDTHH:MM:SSZ."
                    ),
                )

    def test_timestamp_year_is_2026(self):
        """The year component of the timestamp must be 2026."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                year = int(result["timestamp"][:4])
                self.assertEqual(
                    year,
                    2026,
                    msg=f"timestamp year should be 2026, got {year}.",
                )

    def test_timestamp_is_parseable_as_datetime(self):
        """timestamp must be parseable by datetime.datetime.strptime."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                try:
                    dt = datetime.datetime.strptime(
                        result["timestamp"], "%Y-%m-%dT%H:%M:%SZ"
                    )
                except ValueError:
                    self.fail(
                        f"timestamp {result['timestamp']!r} could not be parsed "
                        f"with format '%Y-%m-%dT%H:%M:%SZ'."
                    )

    def test_timestamp_within_2026_bounds(self):
        """The parsed datetime must fall between 2026-01-01 and 2026-12-31 inclusive."""
        start_2026 = datetime.datetime(2026, 1, 1, 0, 0, 0)
        end_2026 = datetime.datetime(2026, 12, 31, 23, 59, 59)

        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                dt = datetime.datetime.strptime(
                    result["timestamp"], "%Y-%m-%dT%H:%M:%SZ"
                )
                self.assertGreaterEqual(
                    dt,
                    start_2026,
                    msg=f"timestamp {result['timestamp']!r} is before 2026-01-01.",
                )
                self.assertLessEqual(
                    dt,
                    end_2026,
                    msg=f"timestamp {result['timestamp']!r} is after 2026-12-31.",
                )

    def test_timestamp_ends_with_z(self):
        """timestamp must end with the UTC designator 'Z'."""
        result = self.sim.generate_crash_dump("EPS_SOLAR_UNDERVOLT", scenario_id=1)
        self.assertTrue(
            result["timestamp"].endswith("Z"),
            msg=f"timestamp {result['timestamp']!r} must end with 'Z'.",
        )

    def test_timestamp_has_correct_length(self):
        """ISO 8601 timestamp 'YYYY-MM-DDTHH:MM:SSZ' must be exactly 20 characters."""
        result = self.sim.generate_crash_dump("EPS_SOLAR_UNDERVOLT", scenario_id=1)
        self.assertEqual(
            len(result["timestamp"]),
            20,
            msg=(
                f"timestamp {result['timestamp']!r} should be 20 characters long, "
                f"got {len(result['timestamp'])}."
            ),
        )


# ---------------------------------------------------------------------------
# Requirement 4.3 — fault_type field equals the argument
# ---------------------------------------------------------------------------

class TestFaultTypeField(unittest.TestCase):
    """fault_type in the output must equal the argument (Req 4.3)."""

    def setUp(self):
        self.sim = _make_sim()

    def test_fault_type_echoed_for_all_valid_types(self):
        """fault_type output must equal the fault_type argument for each valid type."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                self.assertEqual(
                    result["fault_type"],
                    fault_type,
                    msg=(
                        f"fault_type field should be {fault_type!r}, "
                        f"got {result['fault_type']!r}."
                    ),
                )

    def test_fault_type_is_string(self):
        """fault_type field must be stored as a str."""
        result = self.sim.generate_crash_dump("EPS_SOLAR_UNDERVOLT", scenario_id=1)
        self.assertIsInstance(
            result["fault_type"],
            str,
            msg="fault_type must be stored as str.",
        )

    def test_fault_type_not_mutated(self):
        """The fault_type value in the output must not be modified (e.g. lowercased)."""
        original = "MULTI_CASCADE"
        result = self.sim.generate_crash_dump(original, scenario_id=1)
        self.assertEqual(
            result["fault_type"],
            original,
            msg="fault_type must be stored verbatim without case or content changes.",
        )


# ---------------------------------------------------------------------------
# Requirement 4.4 — fault_register field format
# ---------------------------------------------------------------------------

class TestFaultRegisterField(unittest.TestCase):
    """fault_register must be a hex-encoded bitmask string (Req 4.4)."""

    # Pattern: '0x' followed by 8 hex digits (case-insensitive)
    HEX_BITMASK_PATTERN = re.compile(r"^0x[0-9a-fA-F]{8}$")

    def setUp(self):
        self.sim = _make_sim()

    def test_fault_register_is_string(self):
        """fault_register must be a str for every fault type."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                self.assertIsInstance(
                    result["fault_register"],
                    str,
                    msg=f"fault_register should be str for fault_type={fault_type!r}.",
                )

    def test_fault_register_matches_hex_bitmask_pattern(self):
        """fault_register must match the pattern '0x########' (8 hex digits)."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                self.assertRegex(
                    result["fault_register"],
                    self.HEX_BITMASK_PATTERN,
                    msg=(
                        f"fault_register {result['fault_register']!r} does not match "
                        f"the expected hex bitmask format '0x########'."
                    ),
                )


# ---------------------------------------------------------------------------
# Supplementary structural checks for pre_fault_telemetry and event_log
# ---------------------------------------------------------------------------

class TestPreFaultTelemetryStructure(unittest.TestCase):
    """pre_fault_telemetry must be a list of 8–15 dicts (Req 4.5, 4.6)."""

    def setUp(self):
        self.sim = _make_sim()

    def test_pre_fault_telemetry_is_list(self):
        """pre_fault_telemetry must be a list."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                self.assertIsInstance(
                    result["pre_fault_telemetry"],
                    list,
                    msg=f"pre_fault_telemetry should be a list for {fault_type!r}.",
                )

    def test_pre_fault_telemetry_count_between_8_and_15(self):
        """pre_fault_telemetry must contain between 8 and 15 readings inclusive."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                count = len(result["pre_fault_telemetry"])
                self.assertGreaterEqual(
                    count, 8,
                    msg=f"pre_fault_telemetry for {fault_type!r} has fewer than 8 readings ({count}).",
                )
                self.assertLessEqual(
                    count, 15,
                    msg=f"pre_fault_telemetry for {fault_type!r} has more than 15 readings ({count}).",
                )

    def test_each_telemetry_reading_is_dict(self):
        """Every reading in pre_fault_telemetry must be a dict."""
        result = self.sim.generate_crash_dump("EPS_SOLAR_UNDERVOLT", scenario_id=1)
        for i, reading in enumerate(result["pre_fault_telemetry"]):
            with self.subTest(index=i):
                self.assertIsInstance(reading, dict)


class TestEventLogStructure(unittest.TestCase):
    """event_log must be a list of 4–8 dicts (Req 4.11)."""

    def setUp(self):
        self.sim = _make_sim()

    def test_event_log_is_list(self):
        """event_log must be a list."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                self.assertIsInstance(
                    result["event_log"],
                    list,
                    msg=f"event_log should be a list for {fault_type!r}.",
                )

    def test_event_log_count_between_4_and_8(self):
        """event_log must contain between 4 and 8 events inclusive."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                count = len(result["event_log"])
                self.assertGreaterEqual(
                    count, 4,
                    msg=f"event_log for {fault_type!r} has fewer than 4 entries ({count}).",
                )
                self.assertLessEqual(
                    count, 8,
                    msg=f"event_log for {fault_type!r} has more than 8 entries ({count}).",
                )

    def test_each_event_is_dict(self):
        """Every entry in event_log must be a dict."""
        result = self.sim.generate_crash_dump("EPS_SOLAR_UNDERVOLT", scenario_id=1)
        for i, event in enumerate(result["event_log"]):
            with self.subTest(index=i):
                self.assertIsInstance(event, dict)


class TestHardwareStateAndOperatingContextTypes(unittest.TestCase):
    """hardware_state and operating_context must be dicts (Req 4.14, 4.15)."""

    def setUp(self):
        self.sim = _make_sim()

    def test_hardware_state_is_dict(self):
        """hardware_state must be a dict for every fault type."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                self.assertIsInstance(result["hardware_state"], dict)

    def test_operating_context_is_dict(self):
        """operating_context must be a dict for every fault type."""
        for fault_type in VALID_FAULT_TYPES:
            with self.subTest(fault_type=fault_type):
                result = self.sim.generate_crash_dump(fault_type, scenario_id=1)
                self.assertIsInstance(result["operating_context"], dict)


# ---------------------------------------------------------------------------
# Reproducibility — same seed, same results
# ---------------------------------------------------------------------------

class TestReproducibility(unittest.TestCase):
    """generate_crash_dump must be reproducible given the same seed (Req 2.1, 2.2)."""

    def test_same_seed_produces_same_timestamp(self):
        """Two simulators with the same seed must produce identical timestamps."""
        sim_a = SatelliteFaultSimulator(seed=99)
        sim_b = SatelliteFaultSimulator(seed=99)
        r_a = sim_a.generate_crash_dump("EPS_SOLAR_UNDERVOLT", scenario_id=1)
        r_b = sim_b.generate_crash_dump("EPS_SOLAR_UNDERVOLT", scenario_id=1)
        self.assertEqual(r_a["timestamp"], r_b["timestamp"])

    def test_same_seed_produces_same_scenario_id(self):
        """scenario_id is deterministic (always equals the argument)."""
        sim = SatelliteFaultSimulator(seed=7)
        r1 = sim.generate_crash_dump("TCS_THERMAL_RUNAWAY", scenario_id=55)
        self.assertEqual(r1["scenario_id"], 55)


if __name__ == "__main__":
    unittest.main(verbosity=2)
