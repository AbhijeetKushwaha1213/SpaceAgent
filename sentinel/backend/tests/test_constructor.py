"""
test_constructor.py — Unit tests for SatelliteFaultSimulator constructor
========================================================================

Covers task 2.2:
  - Default seed is 42 and random.seed is called with it
  - Custom seed is accepted and forwarded to random.seed
  - All 21 nominal-range attributes are present and contain the required keys
"""

import unittest
from unittest.mock import patch, call
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from simulation.fault_simulator import SatelliteFaultSimulator


# ---------------------------------------------------------------------------
# The 21 canonical parameter names defined in Requirement 4.8
# ---------------------------------------------------------------------------
CANONICAL_PARAMETERS = [
    "V_bat",
    "SoC_pct",
    "I_sa",
    "V_bus",
    "Heater_power_W",
    "RW_speed_rpm",
    "Gyro_rate_degs",
    "Star_tracker_status",
    "Sun_sensor_angle_deg",
    "Attitude_error_deg",
    "OBC_temp_C",
    "CPU_load_pct",
    "Memory_usage_MB",
    "Watchdog_counter",
    "SEU_counter",
    "Fault_register",
    "Safe_mode_entry_count",
    "Transponder_lock",
    "SNR_dB",
    "Component_temp_C",
    "Heater_enable_flag",
]

REQUIRED_RANGE_KEYS = {"nominal_min", "nominal_max", "unit"}


class TestConstructorDefaultSeed(unittest.TestCase):
    """Tests for default seed behaviour (Requirement 2.1, 2.2)."""

    def test_default_seed_is_42(self):
        """Constructing without arguments should call random.seed(42)."""
        with patch("simulation.fault_simulator.random.seed") as mock_seed:
            SatelliteFaultSimulator()
            mock_seed.assert_called_once_with(42)

    def test_default_seed_value_is_42_not_other(self):
        """Verify the default is specifically 42, not any other value."""
        with patch("simulation.fault_simulator.random.seed") as mock_seed:
            SatelliteFaultSimulator()
            args, _ = mock_seed.call_args
            self.assertEqual(args[0], 42)


class TestConstructorCustomSeed(unittest.TestCase):
    """Tests for custom seed forwarding (Requirement 2.1, 2.2)."""

    def test_custom_seed_is_forwarded(self):
        """A custom seed should be passed directly to random.seed."""
        with patch("simulation.fault_simulator.random.seed") as mock_seed:
            SatelliteFaultSimulator(seed=7)
            mock_seed.assert_called_once_with(7)

    def test_custom_seed_zero(self):
        """Seed value 0 should be accepted and forwarded."""
        with patch("simulation.fault_simulator.random.seed") as mock_seed:
            SatelliteFaultSimulator(seed=0)
            mock_seed.assert_called_once_with(0)

    def test_custom_seed_large_value(self):
        """Large seed integers should be accepted and forwarded unchanged."""
        with patch("simulation.fault_simulator.random.seed") as mock_seed:
            SatelliteFaultSimulator(seed=999_999_999)
            mock_seed.assert_called_once_with(999_999_999)

    def test_seed_not_called_multiple_times(self):
        """random.seed should be called exactly once per construction."""
        with patch("simulation.fault_simulator.random.seed") as mock_seed:
            SatelliteFaultSimulator(seed=123)
            self.assertEqual(mock_seed.call_count, 1)


class TestConstructorNominalRanges(unittest.TestCase):
    """Tests for nominal-range initialisation (Requirement 2.3)."""

    def setUp(self):
        # Patch random.seed to avoid side effects on the global RNG state.
        with patch("simulation.fault_simulator.random.seed"):
            self.sim = SatelliteFaultSimulator(seed=42)

    def test_all_21_attributes_present(self):
        """Every canonical parameter should exist as an instance attribute."""
        for param in CANONICAL_PARAMETERS:
            with self.subTest(parameter=param):
                self.assertTrue(
                    hasattr(self.sim, param),
                    msg=f"Instance attribute '{param}' is missing from the constructor.",
                )

    def test_each_attribute_is_a_dict(self):
        """Every nominal-range attribute should be a dict."""
        for param in CANONICAL_PARAMETERS:
            with self.subTest(parameter=param):
                value = getattr(self.sim, param)
                self.assertIsInstance(
                    value,
                    dict,
                    msg=f"'{param}' should be a dict, got {type(value).__name__}.",
                )

    def test_each_range_has_required_keys(self):
        """Each dict must contain nominal_min, nominal_max, and unit."""
        for param in CANONICAL_PARAMETERS:
            with self.subTest(parameter=param):
                value = getattr(self.sim, param)
                missing = REQUIRED_RANGE_KEYS - value.keys()
                self.assertFalse(
                    missing,
                    msg=f"'{param}' is missing keys: {missing}.",
                )

    def test_nominal_min_is_numeric(self):
        """nominal_min for every parameter should be an int or float."""
        for param in CANONICAL_PARAMETERS:
            with self.subTest(parameter=param):
                value = getattr(self.sim, param)
                self.assertIsInstance(
                    value["nominal_min"],
                    (int, float),
                    msg=f"'{param}.nominal_min' is not numeric.",
                )

    def test_nominal_max_is_numeric(self):
        """nominal_max for every parameter should be an int or float."""
        for param in CANONICAL_PARAMETERS:
            with self.subTest(parameter=param):
                value = getattr(self.sim, param)
                self.assertIsInstance(
                    value["nominal_max"],
                    (int, float),
                    msg=f"'{param}.nominal_max' is not numeric.",
                )

    def test_unit_is_a_string(self):
        """unit for every parameter should be a non-empty string."""
        for param in CANONICAL_PARAMETERS:
            with self.subTest(parameter=param):
                value = getattr(self.sim, param)
                self.assertIsInstance(
                    value["unit"],
                    str,
                    msg=f"'{param}.unit' is not a string.",
                )
                self.assertTrue(
                    len(value["unit"]) > 0,
                    msg=f"'{param}.unit' should not be an empty string.",
                )

    def test_nominal_min_lte_nominal_max(self):
        """nominal_min should be less than or equal to nominal_max for every parameter."""
        for param in CANONICAL_PARAMETERS:
            with self.subTest(parameter=param):
                value = getattr(self.sim, param)
                self.assertLessEqual(
                    value["nominal_min"],
                    value["nominal_max"],
                    msg=(
                        f"'{param}.nominal_min' ({value['nominal_min']}) "
                        f"exceeds 'nominal_max' ({value['nominal_max']})."
                    ),
                )

    def test_no_extra_unexpected_keys(self):
        """Nominal-range dicts should contain exactly the three required keys (no extras)."""
        for param in CANONICAL_PARAMETERS:
            with self.subTest(parameter=param):
                value = getattr(self.sim, param)
                extra = set(value.keys()) - REQUIRED_RANGE_KEYS
                self.assertFalse(
                    extra,
                    msg=f"'{param}' has unexpected extra keys: {extra}.",
                )

    def test_exact_count_of_canonical_parameters(self):
        """Exactly 21 canonical parameters should be listed in CANONICAL_PARAMETERS."""
        self.assertEqual(
            len(CANONICAL_PARAMETERS),
            21,
            msg="CANONICAL_PARAMETERS must list exactly 21 items.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
