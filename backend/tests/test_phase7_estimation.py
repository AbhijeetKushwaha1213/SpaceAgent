"""
SENTINEL — Phase 7 state estimation tests (test_phase7_estimation.py)

Run:
    python3 -m unittest tests.test_phase7_estimation -v

Grouped by the guarantee under test:

  1. CONTRACT        SpacecraftState and Residual carry every required field,
                     and residual is exactly observed - predicted
  2. PARAMETERS      every assumed constant declares its reach and its caveat,
                     and the thermal derivation is self-consistent
  3. CONSISTENT      telemetry obeying the models is reported CONSISTENT
  4. VIOLATION       telemetry breaking expected dynamics is INCONSISTENT
  5. SENSOR BIAS     a rate sensor under-reporting motion is caught by the
                     attitude-error bound, and the case that CANNOT be caught
                     is pinned so the limitation stays honest
  6. ACTUATOR        a wheel not delivering its implied torque is caught
  7. UPPER BOUND     coming in below the open-loop attitude bound is CONSISTENT,
                     because that is what a working controller does
  8. STALENESS       asynchronous telemetry is carried forward, never relabelled
                     as observed, and refused once past its declared cadence
  9. UNDECIDABLE     an unavailable comparison is never reported as a pass
 10. DETERMINISM     the same dump yields byte-identical output
 11. NO LLM          nothing in the package can consult a language model

All telemetry in this file is SYNTHETIC and constructed here to exercise a known
physical situation. None of it is spacecraft data and none of it is presented as
a measurement or a result.
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import app.estimation as estimation_package                        # noqa: E402
from app.api.scenarios import get_all_scenarios                    # noqa: E402
from app.estimation.models import (                                # noqa: E402
    Comparison,
    PredictionStatus,
)
from app.estimation.models import attitude as attitude_model       # noqa: E402
from app.estimation.models import power as power_model             # noqa: E402
from app.estimation.models import thermal as thermal_model         # noqa: E402
from app.estimation.parameters import (                            # noqa: E402
    ALL_PARAMETERS,
    BATTERY_CAPACITY,
    INTERNAL_DISSIPATION,
    PARAMETER_SET_VERSION,
    ParameterSource,
    SPACECRAFT_INERTIA,
    THERMAL_CONDUCTANCE,
    THERMAL_SINK_TEMP,
    WHEEL_TO_BODY_INERTIA_RATIO,
    assumed_parameters,
    parameter_status,
    validate_parameters,
)
from app.estimation.residuals import (                             # noqa: E402
    TOLERANCES,
    ExplanationKind,
    ResidualStatus,
    compute_residuals,
    estimation_status,
    validate_estimation,
)
from app.estimation.state import (                                 # noqa: E402
    STALENESS_BUDGET_S,
    QuantitySource,
    estimate_states,
    staleness_budget,
    state_status,
)
from simulation.fault_simulator import SatelliteFaultSimulator     # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# SYNTHETIC FIXTURES
# ═══════════════════════════════════════════════════════════════════════════

def synthetic_dump(*readings: tuple[float, str, object]) -> dict:
    """Build a SYNTHETIC crash dump from ``(offset_s, channel, value)`` triples.

    Marked synthetic in the dump itself, not only in this docstring, so a record
    built from it cannot be mistaken for spacecraft data anywhere downstream.
    """
    window = [
        {
            "parameter": channel,
            "value": value,
            "timestamp": f"T{offset:+.0f}s",
            "relative_time_s": float(offset),
        }
        for offset, channel, value in readings
    ]
    return {
        "scenario_id": 9000,
        "fault_type": "SYNTHETIC_TEST_FIXTURE",
        "provenance": "SYNTHETIC",
        "source_type": "SYNTHETIC_UNIT_TEST",
        "source_note": (
            "Constructed in tests/test_phase7_estimation.py to exercise a known "
            "physical situation. NOT spacecraft data."
        ),
        "pre_fault_telemetry_window": window,
    }


#: Exact body-rate change produced by a 1000 rpm wheel change, in deg/s.
#:
#: Worked by hand rather than read from the parameters, so a parameter change
#: fails this test instead of silently moving the expectation with it:
#:
#:   I_w/I_sc = (7 deg/s in rad) / (6000 rpm in rad/s)
#:            = (7*pi/180) / (6000*2*pi/60) = 7 / 36000
#:   body change for 1000 rpm, in deg/s
#:            = -(7/36000) * 1000 * (2*pi/60) * (180/pi) = -(7/36000)*6000 = -7/6
BODY_RATE_PER_1000_RPM = -7.0 / 6.0

#: Heater-off steady state of the thermal node, from the derived parameters:
#:   T_ss = T_sink + Q_int/k_th = -20 + 25/0.5 = 30 degC
#: This is the midpoint of Component_temp_C's declared nominal band by
#: construction — see the derivation of Q_int in parameters.py.
THERMAL_QUIESCENT_C = 30.0

#: Array current that exactly balances the derived baseline load at the derived
#: nominal bus voltage: 150 W / 30 V = 5 A.
BALANCED_ARRAY_CURRENT_A = 5.0


def residual_for(report, channel):
    """The residual for one channel, or None when none was produced."""
    for residual in report.residuals:
        if residual.channel == channel:
            return residual
    return None


def supported_kinds(report) -> set[ExplanationKind]:
    return {e.kind for e in report.supported_explanations()}


# ═══════════════════════════════════════════════════════════════════════════
# 1. CONTRACT
# ═══════════════════════════════════════════════════════════════════════════

class TestContract(unittest.TestCase):
    """The Phase 7 specification's required fields are all present."""

    def test_spacecraft_state_carries_every_required_field(self):
        dump = synthetic_dump(
            (-30.0, "Gyro_rate_degs", 0.0),
            (-30.0, "RW_speed_rpm", 1000.0),
            (0.0, "Gyro_rate_degs", 0.0),
            (0.0, "RW_speed_rpm", 1000.0),
        )
        sequence = estimate_states(dump)
        self.assertGreaterEqual(len(sequence), 2)

        state = sequence.timed_states[-1]
        for field in (
            "timestamp", "attitude", "angular_velocity",
            "reaction_wheel_state", "battery_state", "thermal_state",
            "communication_state",
        ):
            self.assertTrue(hasattr(state, field), f"state lacks {field}")

        # Serialises without raising, and stays JSON-clean for the audit record.
        json.dumps(state.as_dict())

    def test_residual_is_exactly_observed_minus_predicted(self):
        dump = synthetic_dump(
            (-30.0, "Component_temp_C", 40.0),
            (0.0, "Component_temp_C", 41.0),
        )
        report = compute_residuals(dump)
        residual = residual_for(report, "Component_temp_C")

        self.assertIsNotNone(residual)
        self.assertIsNotNone(residual.observed)
        self.assertIsNotNone(residual.predicted)
        self.assertAlmostEqual(
            residual.residual, residual.observed - residual.predicted,
            places=12,
            msg="residual must be exactly observed - predicted",
        )

    def test_every_residual_states_the_equation_that_produced_it(self):
        sim = SatelliteFaultSimulator(seed=1)
        report = compute_residuals(
            sim.generate_crash_dump("TCS_THERMAL_RUNAWAY", 900))
        self.assertTrue(report.residuals)
        for residual in report.residuals:
            self.assertTrue(
                residual.equation.strip(),
                f"{residual.channel} residual carries no equation",
            )

    def test_report_serialises_for_the_audit_record(self):
        sim = SatelliteFaultSimulator(seed=1)
        report = compute_residuals(
            sim.generate_crash_dump("EPS_SOLAR_UNDERVOLT", 900))
        payload = report.as_dict()
        json.dumps(payload)
        self.assertIn("claim", payload)
        self.assertFalse(payload["flight_qualified"])
        self.assertFalse(payload["uses_llm"])


# ═══════════════════════════════════════════════════════════════════════════
# 2. PARAMETERS
# ═══════════════════════════════════════════════════════════════════════════

class TestParameters(unittest.TestCase):
    """Assumed constants are declared as such, with their reach and caveat."""

    def test_parameter_set_validates(self):
        findings = validate_parameters()
        self.assertEqual(findings["errors"], [])
        self.assertEqual(findings["warnings"], [])

    def test_every_assumed_parameter_declares_reach_and_caveat(self):
        assumed = assumed_parameters()
        self.assertTrue(assumed, "expected at least one assumed parameter")
        for parameter in assumed:
            self.assertTrue(
                parameter.affects,
                f"{parameter.symbol} is assumed but declares no reach, so its "
                f"influence on a verdict cannot be audited",
            )
            self.assertTrue(
                parameter.caveat,
                f"{parameter.symbol} is assumed but carries no caveat",
            )

    def test_nothing_claims_to_be_measured(self):
        for parameter in ALL_PARAMETERS:
            if parameter.source is ParameterSource.PHYSICAL_CONSTANT:
                continue
            self.assertFalse(
                parameter.source.is_measured,
                f"{parameter.symbol} must not claim measured provenance: this "
                f"repository contains no vehicle specification",
            )

    def test_inertia_ratio_is_derived_from_the_channel_dictionary(self):
        # 7 / 36000, worked by hand from the declared hard limits.
        self.assertAlmostEqual(
            WHEEL_TO_BODY_INERTIA_RATIO.value, 7.0 / 36000.0, places=12)
        self.assertIs(
            WHEEL_TO_BODY_INERTIA_RATIO.source,
            ParameterSource.DERIVED_FROM_CHANNEL_DICT,
        )

    def test_thermal_quiescent_state_lands_on_the_nominal_midpoint(self):
        """The derivation that stops a standing residual on healthy telemetry."""
        quiescent = (
            THERMAL_SINK_TEMP.value
            + INTERNAL_DISSIPATION.value / THERMAL_CONDUCTANCE.value
        )
        self.assertAlmostEqual(quiescent, THERMAL_QUIESCENT_C, places=9)

    def test_status_disclaims_flight_qualification(self):
        status = parameter_status()
        self.assertFalse(status["flight_qualified"])
        self.assertFalse(status["represents_specific_mission"])
        self.assertEqual(status["parameter_set_version"], PARAMETER_SET_VERSION)
        self.assertIn("NOT flight", status["claim"])


# ═══════════════════════════════════════════════════════════════════════════
# 3. PHYSICALLY CONSISTENT BEHAVIOUR
# ═══════════════════════════════════════════════════════════════════════════

class TestConsistentBehaviour(unittest.TestCase):
    """Telemetry that obeys the models is reported CONSISTENT."""

    def test_momentum_exchange_obeyed_is_consistent(self):
        """The wheel spins up and the body counter-rotates by exactly the
        momentum-exchange amount."""
        dump = synthetic_dump(
            (-30.0, "Gyro_rate_degs", 0.0),
            (-30.0, "RW_speed_rpm", 1000.0),
            (0.0, "Gyro_rate_degs", BODY_RATE_PER_1000_RPM),
            (0.0, "RW_speed_rpm", 2000.0),
        )
        report = compute_residuals(dump)
        residual = residual_for(report, "Gyro_rate_degs")

        self.assertIsNotNone(residual)
        self.assertIs(residual.status, ResidualStatus.CONSISTENT)
        self.assertAlmostEqual(residual.residual, 0.0, places=9)
        self.assertTrue(report.physically_consistent)

    def test_thermal_quiescent_telemetry_is_consistent(self):
        """Sitting at the heater-off steady state with the heater off."""
        dump = synthetic_dump(
            (-30.0, "Component_temp_C", THERMAL_QUIESCENT_C),
            (-30.0, "Heater_power_W", 0.0),
            (0.0, "Component_temp_C", THERMAL_QUIESCENT_C),
            (0.0, "Heater_power_W", 0.0),
        )
        report = compute_residuals(dump)
        residual = residual_for(report, "Component_temp_C")

        self.assertIsNotNone(residual)
        self.assertIs(residual.status, ResidualStatus.CONSISTENT)
        self.assertAlmostEqual(residual.residual, 0.0, places=9)

    def test_balanced_power_budget_is_consistent(self):
        """Generation exactly covers the derived baseline load, so charge holds."""
        dump = synthetic_dump(
            (-30.0, "SoC_pct", 80.0),
            (-30.0, "I_sa", BALANCED_ARRAY_CURRENT_A),
            (-30.0, "V_bus", 30.0),
            (0.0, "SoC_pct", 80.0),
            (0.0, "I_sa", BALANCED_ARRAY_CURRENT_A),
            (0.0, "V_bus", 30.0),
        )
        report = compute_residuals(dump)
        residual = residual_for(report, "SoC_pct")

        self.assertIsNotNone(residual)
        self.assertIs(residual.status, ResidualStatus.CONSISTENT)
        self.assertAlmostEqual(residual.residual, 0.0, places=9)
        self.assertAlmostEqual(
            residual.extras["net_power_W"], 0.0, places=9,
            msg="a balanced budget must yield exactly zero net power",
        )


# ═══════════════════════════════════════════════════════════════════════════
# 4. VIOLATED DYNAMICS
# ═══════════════════════════════════════════════════════════════════════════

class TestViolatedDynamics(unittest.TestCase):
    """Telemetry that breaks the models is reported INCONSISTENT."""

    def test_body_rate_appearing_without_wheel_motion_is_inconsistent(self):
        """The vehicle accelerates with no actuator input: momentum came from
        outside the model."""
        dump = synthetic_dump(
            (-30.0, "Gyro_rate_degs", 0.0),
            (-30.0, "RW_speed_rpm", 1000.0),
            (0.0, "Gyro_rate_degs", 2.0),
            (0.0, "RW_speed_rpm", 1000.0),
        )
        report = compute_residuals(dump)
        residual = residual_for(report, "Gyro_rate_degs")

        self.assertIsNotNone(residual)
        self.assertIs(residual.status, ResidualStatus.INCONSISTENT)
        self.assertAlmostEqual(residual.residual, 2.0, places=9)
        self.assertFalse(report.physically_consistent)

        self.assertIn(ExplanationKind.EXTERNAL_TORQUE, supported_kinds(report))
        # A wrong assumption is never excluded, so it is always offered too.
        self.assertIn(
            ExplanationKind.MODEL_PARAMETER_ERROR, supported_kinds(report))

    def test_implied_disturbance_torque_is_reported_and_signed(self):
        dump = synthetic_dump(
            (-30.0, "Gyro_rate_degs", 0.0),
            (-30.0, "RW_speed_rpm", 1000.0),
            (0.0, "Gyro_rate_degs", 2.0),
            (0.0, "RW_speed_rpm", 1000.0),
        )
        report = compute_residuals(dump)
        torque = attitude_model.implied_disturbance_torque(2.0, 30.0)

        # tau = I_sc * (2 deg/s in rad) / 30 s
        expected = SPACECRAFT_INERTIA.value * (2.0 * math.pi / 180.0) / 30.0
        self.assertAlmostEqual(torque, expected, places=12)
        self.assertGreater(torque, 0.0)

        for explanation in report.explanations:
            if explanation.kind is ExplanationKind.EXTERNAL_TORQUE:
                self.assertAlmostEqual(
                    explanation.evidence["implied_external_torque_Nm"],
                    expected, places=12)
                break
        else:  # pragma: no cover — the rule is always evaluated here
            self.fail("no EXTERNAL_TORQUE candidate was offered")

    def test_unexplained_temperature_rise_is_inconsistent(self):
        """Temperature climbs far faster than heater power and dissipation
        account for."""
        dump = synthetic_dump(
            (-30.0, "Component_temp_C", 40.0),
            (-30.0, "Heater_power_W", 0.0),
            (0.0, "Component_temp_C", 62.0),
            (0.0, "Heater_power_W", 0.0),
        )
        report = compute_residuals(dump)
        residual = residual_for(report, "Component_temp_C")

        self.assertIsNotNone(residual)
        self.assertIs(residual.status, ResidualStatus.INCONSISTENT)
        self.assertGreater(residual.residual, 0.0)
        self.assertIn(
            ExplanationKind.UNMODELLED_HEAT_PATH, supported_kinds(report))

    def test_charge_rising_while_generation_is_absent_is_inconsistent(self):
        """State of charge climbing with no array current is an energy gap."""
        dump = synthetic_dump(
            (-30.0, "SoC_pct", 50.0),
            (-30.0, "I_sa", 0.0),
            (-30.0, "V_bus", 30.0),
            (0.0, "SoC_pct", 70.0),
            (0.0, "I_sa", 0.0),
            (0.0, "V_bus", 30.0),
        )
        report = compute_residuals(dump)
        residual = residual_for(report, "SoC_pct")

        self.assertIsNotNone(residual)
        self.assertIs(residual.status, ResidualStatus.INCONSISTENT)
        self.assertGreater(
            residual.residual, 0.0,
            msg="charge rose while the model predicted a fall",
        )
        self.assertIn(
            ExplanationKind.ENERGY_BOOKKEEPING_GAP, supported_kinds(report))
        self.assertLess(residual.extras["net_power_W"], 0.0)


# ═══════════════════════════════════════════════════════════════════════════
# 5. SENSOR BIAS
# ═══════════════════════════════════════════════════════════════════════════

class TestSensorBias(unittest.TestCase):
    """A rate sensor reporting less motion than occurred."""

    def test_attitude_error_above_its_bound_implicates_the_rate_sensor(self):
        """Pointing error grows while the gyro reads zero.

        The vehicle demonstrably turned, so a gyro reading nothing is
        under-reporting. This is the check that catches a biased rate sensor.
        """
        dump = synthetic_dump(
            (-30.0, "Attitude_error_deg", 0.1),
            (-30.0, "Gyro_rate_degs", 0.0),
            (0.0, "Attitude_error_deg", 5.0),
            (0.0, "Gyro_rate_degs", 0.0),
        )
        report = compute_residuals(dump)
        residual = residual_for(report, "Attitude_error_deg")

        self.assertIsNotNone(residual)
        self.assertIs(residual.status, ResidualStatus.INCONSISTENT)
        # Open-loop bound with a zero rate is just the previous error.
        self.assertAlmostEqual(residual.predicted, 0.1, places=9)
        self.assertAlmostEqual(residual.residual, 4.9, places=9)
        self.assertIn(
            ExplanationKind.SENSOR_UNDER_REPORTING, supported_kinds(report))

    def test_a_constant_rate_bias_cancels_and_is_NOT_detected(self):
        """Pins the documented limitation rather than leaving it in a docstring.

        The rate prediction is anchored on the previous OBSERVED rate, so a bias
        present in both samples subtracts out exactly. A constant bias is
        invisible to the rate residual, and this test exists so that fact cannot
        quietly stop being true — or be quietly forgotten.
        """
        bias = 0.8
        dump = synthetic_dump(
            (-30.0, "Gyro_rate_degs", 0.0 + bias),
            (-30.0, "RW_speed_rpm", 1000.0),
            (0.0, "Gyro_rate_degs", BODY_RATE_PER_1000_RPM + bias),
            (0.0, "RW_speed_rpm", 2000.0),
        )
        report = compute_residuals(dump)
        residual = residual_for(report, "Gyro_rate_degs")

        self.assertIsNotNone(residual)
        self.assertAlmostEqual(
            residual.residual, 0.0, places=9,
            msg="a constant bias must cancel; if it no longer does, the "
                "SENSOR_UNDER_REPORTING caveat needs rewriting",
        )
        self.assertIs(residual.status, ResidualStatus.CONSISTENT)

        # And the caveat says so, so a reader is not misled by the pass.
        caveats = " ".join(
            e.caveat or "" for e in report.explanations
            if e.kind is ExplanationKind.SENSOR_UNDER_REPORTING
        )
        if caveats:
            self.assertIn("CONSTANT", caveats)


# ═══════════════════════════════════════════════════════════════════════════
# 6. ACTUATOR DEGRADATION
# ═══════════════════════════════════════════════════════════════════════════

class TestActuatorDegradation(unittest.TestCase):
    """A wheel whose speed change does not move the body as much as it should."""

    def test_body_under_responding_to_wheel_motion_is_flagged(self):
        dump = synthetic_dump(
            (-30.0, "Gyro_rate_degs", 0.0),
            (-30.0, "RW_speed_rpm", 1000.0),
            # Momentum exchange calls for -1.1667 deg/s; the body barely moves.
            (0.0, "Gyro_rate_degs", -0.1),
            (0.0, "RW_speed_rpm", 2000.0),
        )
        report = compute_residuals(dump)
        residual = residual_for(report, "Gyro_rate_degs")

        self.assertIsNotNone(residual)
        self.assertIs(residual.status, ResidualStatus.INCONSISTENT)
        self.assertAlmostEqual(
            residual.predicted, BODY_RATE_PER_1000_RPM, places=9)
        self.assertGreater(
            residual.residual, 0.0,
            msg="the body moved less than predicted, so observed - predicted "
                "opposes the wheel's intended effect",
        )
        self.assertIn(
            ExplanationKind.ACTUATOR_UNDER_RESPONSE, supported_kinds(report))

    def test_external_torque_is_not_claimed_when_the_wheel_did_move(self):
        """The two attitude explanations are mutually exclusive by rule."""
        dump = synthetic_dump(
            (-30.0, "Gyro_rate_degs", 0.0),
            (-30.0, "RW_speed_rpm", 1000.0),
            (0.0, "Gyro_rate_degs", -0.1),
            (0.0, "RW_speed_rpm", 2000.0),
        )
        report = compute_residuals(dump)
        self.assertNotIn(
            ExplanationKind.EXTERNAL_TORQUE, supported_kinds(report),
            msg="the wheel did move, so the no-actuator-input rule must not fire",
        )

    def test_actuator_explanation_admits_the_inertia_ambiguity(self):
        dump = synthetic_dump(
            (-30.0, "Gyro_rate_degs", 0.0),
            (-30.0, "RW_speed_rpm", 1000.0),
            (0.0, "Gyro_rate_degs", -0.1),
            (0.0, "RW_speed_rpm", 2000.0),
        )
        report = compute_residuals(dump)
        for explanation in report.explanations:
            if explanation.kind is ExplanationKind.ACTUATOR_UNDER_RESPONSE:
                self.assertIn("inertia", (explanation.caveat or "").lower())
                break
        else:  # pragma: no cover
            self.fail("no ACTUATOR_UNDER_RESPONSE candidate was offered")


# ═══════════════════════════════════════════════════════════════════════════
# 7. UPPER-BOUND SEMANTICS
# ═══════════════════════════════════════════════════════════════════════════

class TestUpperBoundSemantics(unittest.TestCase):
    """The attitude-error comparison is one-sided, and deliberately so."""

    def test_attitude_error_below_the_open_loop_bound_is_consistent(self):
        """A working controller holds error below the open-loop integral.

        Testing this two-sided would report every correctly controlled vehicle
        as physically inconsistent.
        """
        dump = synthetic_dump(
            (-30.0, "Attitude_error_deg", 0.5),
            (-30.0, "Gyro_rate_degs", 0.4),
            (0.0, "Attitude_error_deg", 0.02),
            (0.0, "Gyro_rate_degs", 0.4),
        )
        report = compute_residuals(dump)
        residual = residual_for(report, "Attitude_error_deg")

        self.assertIsNotNone(residual)
        self.assertEqual(residual.comparison, Comparison.UPPER_BOUND.value)
        self.assertLess(residual.residual, 0.0, "observation is below the bound")
        self.assertIs(residual.status, ResidualStatus.CONSISTENT)

    def test_the_attitude_prediction_declares_itself_a_bound(self):
        dump = synthetic_dump(
            (-30.0, "Attitude_error_deg", 0.1),
            (-30.0, "Gyro_rate_degs", 0.0),
            (0.0, "Attitude_error_deg", 0.1),
            (0.0, "Gyro_rate_degs", 0.0),
        )
        sequence = estimate_states(dump)
        previous, current = sequence.timed_states[-2], sequence.timed_states[-1]
        prediction = attitude_model.predict_attitude_error(
            previous, current, 30.0)

        self.assertIs(prediction.comparison, Comparison.UPPER_BOUND)
        self.assertIn("upper bound", prediction.equation.lower())


# ═══════════════════════════════════════════════════════════════════════════
# 8. STALENESS AND ASYNCHRONOUS TELEMETRY
# ═══════════════════════════════════════════════════════════════════════════

class TestStaleness(unittest.TestCase):
    """Carry-forward makes asynchronous telemetry usable without laundering it."""

    def test_carried_forward_value_is_not_labelled_observed(self):
        dump = synthetic_dump(
            (-60.0, "RW_speed_rpm", 1500.0),
            (-30.0, "Gyro_rate_degs", 0.0),
            (0.0, "Gyro_rate_degs", 0.0),
        )
        sequence = estimate_states(dump)
        state = sequence.timed_states[-1]
        wheel = state.reaction_wheel_state.speed_rpm

        self.assertEqual(wheel.value, 1500.0)
        self.assertIs(wheel.source, QuantitySource.CARRIED_FORWARD)
        self.assertEqual(wheel.as_of_s, -60.0)
        self.assertEqual(wheel.staleness_s, 60.0)
        self.assertFalse(wheel.is_fresh)

    def test_staleness_budgets_come_from_declared_sampling_cadence(self):
        # HIGH_RATE gyro is the tightest; LOW_RATE temperature the loosest;
        # an on-change flag never expires.
        self.assertEqual(staleness_budget("Gyro_rate_degs"),
                         STALENESS_BUDGET_S["HIGH_RATE"])
        self.assertEqual(staleness_budget("Component_temp_C"),
                         STALENESS_BUDGET_S["LOW_RATE"])
        self.assertIsNone(staleness_budget("Heater_enable_flag"))
        # An unrecognised channel gets no carry-forward allowance at all.
        self.assertEqual(staleness_budget("channel_41_not_in_dictionary"), 0.0)

    def test_a_value_past_its_budget_is_refused(self):
        """The gyro is HIGH_RATE, so a 120 s old wheel reading is not a usable
        starting point."""
        dump = synthetic_dump(
            (-300.0, "RW_speed_rpm", 1000.0),
            (-30.0, "Gyro_rate_degs", 0.0),
            (0.0, "Gyro_rate_degs", 0.0),
        )
        sequence = estimate_states(dump)
        previous, current = sequence.timed_states[-2], sequence.timed_states[-1]
        prediction = attitude_model.predict_angular_velocity(
            previous, current, 30.0)

        self.assertIs(prediction.status, PredictionStatus.NOT_PREDICTABLE)
        self.assertIn("stale", (prediction.reason or "").lower())

    def test_the_compared_observation_must_be_freshly_reported(self):
        """A carried-forward observation would make the residual an artefact.

        Only one fresh temperature exists, so no step can be formed and no
        residual is produced — rather than one built from the same value twice.
        """
        dump = synthetic_dump(
            (-30.0, "Component_temp_C", 40.0),
            (0.0, "Heater_power_W", 0.0),
        )
        report = compute_residuals(dump)
        self.assertIsNone(residual_for(report, "Component_temp_C"))
        self.assertIsNone(report.physically_consistent)

    def test_asynchronous_window_still_produces_a_residual(self):
        """The case carry-forward exists for: the heater is reported once, far
        earlier, while the temperature is reported at every step."""
        dump = synthetic_dump(
            (-120.0, "Heater_power_W", 0.0),
            (-30.0, "Component_temp_C", THERMAL_QUIESCENT_C),
            (0.0, "Component_temp_C", THERMAL_QUIESCENT_C),
        )
        report = compute_residuals(dump)
        residual = residual_for(report, "Component_temp_C")

        self.assertIsNotNone(residual)
        self.assertIs(residual.status, ResidualStatus.CONSISTENT)
        # The heater value was reused from 90 s earlier, and the audit payload
        # says exactly that rather than calling it an observation.
        self.assertEqual(
            residual.inputs["P_heater_source"],
            QuantitySource.CARRIED_FORWARD.value,
        )
        self.assertEqual(residual.inputs["P_heater_staleness_s"], 90.0)


# ═══════════════════════════════════════════════════════════════════════════
# 9. UNDECIDABLE IS NOT A PASS
# ═══════════════════════════════════════════════════════════════════════════

class TestUndecidable(unittest.TestCase):
    """An impossible comparison is never reported as a passing check."""

    def test_empty_dump_yields_no_consistency_claim(self):
        for empty in (None, {}, {"pre_fault_telemetry_window": []}):
            report = compute_residuals(empty)
            self.assertEqual(report.residuals, [])
            self.assertIsNone(
                report.physically_consistent,
                msg="nothing was checked, so consistency must be unknown "
                    "rather than True or False",
            )
            self.assertTrue(report.warnings)

    def test_all_undecidable_is_reported_as_unknown_not_consistent(self):
        # A single temperature sample: a prediction needs two.
        dump = synthetic_dump((0.0, "Component_temp_C", 40.0))
        report = compute_residuals(dump)
        self.assertFalse(report.inconsistent)
        self.assertIsNone(report.physically_consistent)

    def test_undecidable_residual_states_why(self):
        dump = synthetic_dump(
            (-30.0, "SoC_pct", 80.0),
            (0.0, "SoC_pct", 79.0),
        )
        # No array current anywhere, so generation is unknown.
        report = compute_residuals(dump)
        residual = residual_for(report, "SoC_pct")
        self.assertIsNotNone(residual)
        self.assertIs(residual.status, ResidualStatus.UNDECIDABLE)
        self.assertTrue(residual.undecidable_reason)

    def test_residuals_present_but_all_undecidable_is_still_unknown(self):
        """The case that matters most, and the one an empty report does not cover.

        Here a residual EXISTS — the channel was reported twice — but it could not
        be decided, because generation is unknown without an array current. The
        verdict must be unknown rather than True: `not self.inconsistent` would
        report a spacecraft as physically consistent on the strength of a check
        that never ran.
        """
        dump = synthetic_dump(
            (-30.0, "SoC_pct", 80.0),
            (0.0, "SoC_pct", 79.0),
        )
        report = compute_residuals(dump)

        self.assertTrue(report.residuals, "expected at least one residual")
        self.assertTrue(
            all(r.status is ResidualStatus.UNDECIDABLE
                for r in report.residuals),
            "fixture must produce only undecidable residuals",
        )
        self.assertFalse(report.inconsistent)
        self.assertIsNone(
            report.physically_consistent,
            msg="every check was undecidable, so consistency is unknown; "
                "reporting True here would claim a check that never ran",
        )

    def test_a_carried_forward_value_is_never_used_as_the_observation(self):
        """Unit-level guard on the comparison side.

        `fresh_states_for()` already restricts the step grid to fresh samples, so
        this guard is a second line of defence. It is tested directly because the
        failure it prevents is silent: comparing a carried-forward observation
        against a prediction anchored on the same carried value yields a residual
        equal to the model's own step, with no measurement behind it.
        """
        from app.estimation.residuals import _observed_value

        dump = synthetic_dump(
            (-60.0, "Component_temp_C", 40.0),
            (0.0, "Heater_power_W", 0.0),
        )
        state = estimate_states(dump).timed_states[-1]
        temperature = state.thermal_state.component_temperature

        # The value is present and usable, but it was measured 60 s earlier.
        self.assertEqual(temperature.value, 40.0)
        self.assertTrue(temperature.is_usable)
        self.assertIs(temperature.source, QuantitySource.CARRIED_FORWARD)
        self.assertFalse(temperature.is_fresh)

        self.assertIsNone(
            _observed_value(state, "Component_temp_C"),
            msg="a carried-forward value must not be offered as the observation",
        )

    def test_unavailable_quantities_are_none_never_zero(self):
        dump = synthetic_dump((0.0, "Component_temp_C", 40.0))
        state = estimate_states(dump).timed_states[-1]

        for estimate in (
            state.angular_velocity,
            state.reaction_wheel_state.speed_rpm,
            state.battery_state.state_of_charge,
        ):
            self.assertIsNone(
                estimate.value,
                "an absent quantity must be None, never defaulted to zero",
            )
            self.assertIs(estimate.source, QuantitySource.UNAVAILABLE)

    def test_thermal_model_refuses_a_step_past_its_stability_bound(self):
        """A diverging prediction would fabricate a large residual."""
        dump = synthetic_dump(
            (-2000.0, "Component_temp_C", 40.0),
            (0.0, "Component_temp_C", 41.0),
        )
        report = compute_residuals(dump)
        residual = residual_for(report, "Component_temp_C")
        self.assertIsNotNone(residual)
        self.assertIs(residual.status, ResidualStatus.UNDECIDABLE)
        self.assertIn("stability", (residual.undecidable_reason or "").lower())


# ═══════════════════════════════════════════════════════════════════════════
# 10. DETERMINISM
# ═══════════════════════════════════════════════════════════════════════════

class TestDeterminism(unittest.TestCase):
    """The same dump must always give the same answer."""

    def test_synthetic_dump_is_byte_identical_across_runs(self):
        dump = synthetic_dump(
            (-30.0, "Gyro_rate_degs", 0.0),
            (-30.0, "RW_speed_rpm", 1000.0),
            (0.0, "Gyro_rate_degs", 2.0),
            (0.0, "RW_speed_rpm", 1000.0),
        )
        first = json.dumps(compute_residuals(dump).as_dict(), sort_keys=True)
        second = json.dumps(compute_residuals(dump).as_dict(), sort_keys=True)
        self.assertEqual(first, second)

    def test_every_shipped_scenario_is_deterministic(self):
        for scenario in get_all_scenarios():
            dump = scenario.get("crash_dump", scenario)
            first = json.dumps(compute_residuals(dump).as_dict(), sort_keys=True)
            second = json.dumps(compute_residuals(dump).as_dict(),
                                sort_keys=True)
            self.assertEqual(
                first, second,
                f"scenario {dump.get('scenario_id')} is not deterministic",
            )

    def test_simulated_dumps_are_deterministic(self):
        for fault in ("ADCS_GYRO_SEU", "EPS_SOLAR_UNDERVOLT",
                      "TCS_THERMAL_RUNAWAY", "MULTI_CASCADE"):
            sim = SatelliteFaultSimulator(seed=7)
            dump = sim.generate_crash_dump(fault, 900)
            first = json.dumps(compute_residuals(dump).as_dict(), sort_keys=True)
            second = json.dumps(compute_residuals(dump).as_dict(),
                                sort_keys=True)
            self.assertEqual(first, second, f"{fault} is not deterministic")

    def test_no_dump_raises(self):
        """Robustness: malformed input degrades, it does not explode."""
        for malformed in (
            None, {}, [], "not a dump", 42,
            {"pre_fault_telemetry_window": "not a list"},
            {"pre_fault_telemetry_window": [{"no_parameter": 1}]},
            {"pre_fault_telemetry_window": [
                {"parameter": "Component_temp_C", "value": float("nan"),
                 "timestamp": "T-0s", "relative_time_s": 0.0}]},
        ):
            report = compute_residuals(malformed)  # type: ignore[arg-type]
            self.assertIsNotNone(report)
            json.dumps(report.as_dict())


# ═══════════════════════════════════════════════════════════════════════════
# 11. NO LLM, AND THE PACKAGE SAYS SO
# ═══════════════════════════════════════════════════════════════════════════

class TestNoLanguageModel(unittest.TestCase):
    """Phase 7 is deterministic physics. No model is consulted anywhere."""

    def test_no_model_client_appears_in_the_package_source(self):
        package_root = Path(estimation_package.__file__).resolve().parent
        forbidden = (
            "google.genai", "google.generativeai", "genai.Client",
            "openai", "anthropic", "ollama", "_call_llm", "chat.completions",
        )
        files = sorted(package_root.rglob("*.py"))
        self.assertTrue(files, "expected to find package sources")
        for path in files:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(
                    token, text,
                    f"{path.name} references {token!r}; Phase 7 must not be "
                    f"able to consult a language model",
                )

    def test_package_does_not_import_the_agent(self):
        package_root = Path(estimation_package.__file__).resolve().parent
        for path in sorted(package_root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "from app.agent", text,
                f"{path.name} imports the agent package, which holds the LLM",
            )
            self.assertNotIn("import app.agent", text)

    def test_every_status_declares_no_llm_and_no_flight_qualification(self):
        status = estimation_status()
        self.assertFalse(status["uses_llm"])
        self.assertTrue(status["deterministic"])
        self.assertFalse(status["flight_qualified"])
        self.assertFalse(status["represents_specific_mission"])
        self.assertEqual(
            status["pipeline"],
            "telemetry -> state estimate -> model prediction -> residuals",
        )
        for name in ("attitude", "power", "thermal"):
            self.assertFalse(status["models"][name]["uses_llm"])
            self.assertFalse(status["models"][name]["flight_qualified"])
        self.assertFalse(state_status()["uses_llm"])
        self.assertFalse(state_status()["flight_qualified"])


# ═══════════════════════════════════════════════════════════════════════════
# 12. SELF-VALIDATION AND DOCUMENTED ASSUMPTIONS
# ═══════════════════════════════════════════════════════════════════════════

class TestSelfValidation(unittest.TestCase):
    """The package checks itself, and every model documents its equations."""

    def test_estimation_validates_without_errors(self):
        findings = validate_estimation()
        self.assertEqual(findings["errors"], [])

    def test_every_predicted_channel_has_a_declared_tolerance(self):
        predicted: set[str] = set()
        for module in (attitude_model, power_model, thermal_model):
            predicted.update(module.model_status()["predicts"])
        for channel in predicted:
            self.assertIn(
                channel, TOLERANCES,
                f"{channel} is predicted but has no tolerance, so its residual "
                f"could never be decided",
            )

    def test_every_tolerance_states_a_rationale(self):
        for channel, spec in TOLERANCES.items():
            self.assertGreater(
                len(spec.rationale.strip()), 20,
                f"{channel} tolerance has no reviewable rationale",
            )
            self.assertGreater(spec.floor(), 0.0)

    def test_tolerance_widens_with_step_length(self):
        """Integration error grows with the step, so the tolerance must too."""
        for channel, spec in TOLERANCES.items():
            if spec.dt_growth == 0.0:
                continue
            self.assertGreater(
                spec.at(300.0), spec.at(10.0),
                f"{channel} tolerance must widen as the step lengthens",
            )

    def test_every_model_documents_its_equations_and_assumptions(self):
        for module in (attitude_model, power_model, thermal_model):
            status = module.model_status()
            self.assertTrue(status["equations"],
                            f"{module.MODEL_NAME} documents no equations")
            self.assertTrue(status["assumptions"],
                            f"{module.MODEL_NAME} documents no assumptions")
            for name, equation in status["equations"].items():
                self.assertTrue(str(equation).strip(),
                                f"{module.MODEL_NAME}: equation {name} is empty")

    def test_state_declares_its_structural_limitations(self):
        limitations = state_status()["structural_limitations"]
        joined = " ".join(limitations).lower()
        # The four the models genuinely cannot represent.
        self.assertIn("single-axis", joined)
        self.assertIn("one reaction wheel", joined)
        self.assertIn("one thermal node", joined)
        self.assertIn("no orbit model", joined)

    def test_report_carries_the_assumptions_it_depends_on(self):
        sim = SatelliteFaultSimulator(seed=1)
        report = compute_residuals(
            sim.generate_crash_dump("TCS_THERMAL_RUNAWAY", 900))
        payload = report.as_dict()

        self.assertTrue(payload["assumed_parameters"])
        self.assertTrue(payload["limitations"])
        symbols = {p["symbol"] for p in payload["assumed_parameters"]}
        self.assertIn(BATTERY_CAPACITY.symbol, symbols)

    def test_a_residual_names_the_parameters_behind_it(self):
        dump = synthetic_dump(
            (-30.0, "Component_temp_C", 40.0),
            (0.0, "Component_temp_C", 41.0),
        )
        report = compute_residuals(dump)
        residual = residual_for(report, "Component_temp_C")
        self.assertIsNotNone(residual)
        self.assertIn(THERMAL_CONDUCTANCE.symbol, residual.parameters_used)
        self.assertIn(INTERNAL_DISSIPATION.symbol, residual.parameters_used)


# ═══════════════════════════════════════════════════════════════════════════
# 13. PIPELINE INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

class TestPipelineIntegration(unittest.TestCase):
    """The stage is wired in, recorded, and runs before the LLM."""

    def _record(self, fault: str = "TCS_THERMAL_RUNAWAY"):
        from app.agent.agent import _audit_record_state_estimation
        from app.audit import AuditRecorder

        sim = SatelliteFaultSimulator(seed=1)
        dump = sim.generate_crash_dump(fault, 900)
        recorder = AuditRecorder.begin(dump, origin="test_phase7")
        _audit_record_state_estimation(recorder, dump)
        return recorder.build()

    def test_state_estimation_is_recorded_with_a_real_result(self):
        from app.audit import Stage, StageStatus

        record = self._record()
        entries = [e for e in record.entries
                   if e.stage is Stage.STATE_ESTIMATION]
        self.assertEqual(len(entries), 1)

        entry = entries[0]
        self.assertIs(
            entry.status, StageStatus.OK,
            "a dump with a decidable residual must record OK, not "
            "NOT_IMPLEMENTED",
        )
        self.assertNotEqual(entry.status, StageStatus.NOT_IMPLEMENTED)
        self.assertIsNotNone(entry.duration_ms)

    def test_recorded_payload_carries_residuals_and_its_assumptions(self):
        record = self._record()
        payload = record.entries[-1].payload

        self.assertIn("residual_report", payload)
        report = payload["residual_report"]
        self.assertGreater(report["residual_count"], 0)
        self.assertTrue(report["assumed_parameters"])
        self.assertTrue(report["limitations"])
        self.assertFalse(payload["uses_llm"])
        self.assertFalse(payload["flight_qualified"])
        self.assertTrue(payload["runs_before_llm"])
        self.assertIsNotNone(payload["state_estimate"]["final_state"])

    def test_the_not_implemented_placeholder_is_gone(self):
        """Phase 4's placeholder must not survive alongside a real implementation.

        Both existing would let the audit trail claim the capability is absent on
        one code path and present on another.
        """
        agent_source = (
            Path(_BACKEND) / "app" / "agent" / "agent.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            "_audit_record_state_estimation_absent", agent_source,
            "the Phase 4 NOT_IMPLEMENTED placeholder is still referenced",
        )

    def test_state_estimation_runs_before_the_llm_in_the_pipeline(self):
        """Ordering is the guarantee, not just presence.

        Residuals are evidence the model is GIVEN. If the stage ran after the
        LLM, the model's output could not have been constrained by it, and the
        pipeline would be recording physics as an afterthought rather than as an
        input.
        """
        record = self._record()
        payload = record.entries[-1].payload
        self.assertTrue(payload["runs_before_llm"])
        self.assertEqual(
            payload["pipeline"],
            "telemetry -> state estimate -> model prediction -> residuals",
        )

    def test_audit_status_no_longer_advertises_state_estimation_as_absent(self):
        try:
            from app.main import audit_status
        except Exception as exc:  # pragma: no cover — fastapi may be absent
            self.skipTest(f"app.main unavailable: {exc}")

        from app.audit import Stage

        response = audit_status()
        self.assertNotIn(
            Stage.STATE_ESTIMATION.value, response.not_implemented_stages,
            "state estimation now records a result, so advertising it as "
            "not implemented understates what runs",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
