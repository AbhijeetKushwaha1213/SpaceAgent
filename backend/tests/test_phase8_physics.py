"""
SENTINEL — Phase 8 physics validation tests (test_phase8_physics.py)

Run:
    python3 -m unittest tests.test_phase8_physics -v

Grouped by the guarantee under test:

  1. CONTRACT          every field the Phase 8 specification requires is present
  2. CONSTRAINTS       the catalogue and the per-fault claims table are coherent
  3. WRONG HYPOTHESES  a hypothesis the physics contradicts is REJECTED, one
                       case per check family that can refute
  4. RIGHT HYPOTHESES  a hypothesis the physics supports is not rejected
  5. ASYMMETRY         missing evidence yields UNCERTAIN, never INVALID
  6. NO COVERAGE       a fault the models cannot reach is UNCERTAIN by
                       construction, and says so
  7. LLM BOUNDARY      no code path lets a language model change a verdict
  8. DOWNGRADE         a contradicted hypothesis is demoted and re-ranked, and
                       corroboration never promotes
  9. DETERMINISM       the same inputs always give the same verdicts
 10. INTEGRATION       the stage is wired in, recorded, and no longer absent

All telemetry here is SYNTHETIC and constructed to make one physical situation
unambiguous. None of it is spacecraft data. The "LLM-style" hypotheses are
likewise constructed: they are what a model might assert from a symptom alone,
used to show that deterministic validation rejects them.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.api.scenarios import get_all_scenarios                     # noqa: E402
from app.diagnosis import fault_ids, generate_hypotheses, get_fault  # noqa: E402
from app.diagnosis.candidates import (                              # noqa: E402
    Hypothesis,
    HypothesisOrigin,
)
from app.detection import run_detection_on_crash_dump               # noqa: E402
from app.estimation import compute_residuals, estimate_states       # noqa: E402
from app.validation.physics import (                                # noqa: E402
    CLAIMS_BY_FAULT,
    CONSTRAINTS,
    CONSTRAINTS_BY_ID,
    INVALID_SCORE_MULTIPLIER,
    SATURATION_REFUTATION_CEILING,
    VALID_SCORE_MULTIPLIER,
    CheckFamily,
    CheckOutcome,
    PhysicsStatus,
    Trend,
    apply_physics_verdicts,
    model_version,
    observed_trend,
    physics_status,
    reconcile_llm_claim,
    validate_crash_dump,
    validate_hypotheses,
    validate_hypothesis,
    validate_physics_layer,
)
from simulation.fault_simulator import SatelliteFaultSimulator      # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# SYNTHETIC FIXTURES
# ═══════════════════════════════════════════════════════════════════════════

#: Body-rate change a 1000 rpm wheel change demands, in deg/s. Worked by hand
#: from the declared limits: -(7/36000) * 1000 * 6 = -7/6. See
#: tests/test_phase7_estimation.py, which pins the same constant.
BODY_RATE_PER_1000_RPM = -7.0 / 6.0

#: Heater-off steady state of the Phase 7 thermal node.
THERMAL_QUIESCENT_C = 30.0

#: Array current that exactly balances the derived baseline load at 30 V.
BALANCED_ARRAY_CURRENT_A = 5.0


def synthetic_dump(*readings: tuple[float, str, object]) -> dict:
    """Build a SYNTHETIC crash dump from ``(offset_s, channel, value)`` triples."""
    return {
        "scenario_id": 9200,
        "fault_type": "SYNTHETIC_TEST_FIXTURE",
        "provenance": "SYNTHETIC",
        "source_type": "SYNTHETIC_UNIT_TEST",
        "source_note": (
            "Constructed in tests/test_phase8_physics.py to make one physical "
            "situation unambiguous. NOT spacecraft data."
        ),
        "pre_fault_telemetry_window": [
            {
                "parameter": channel,
                "value": value,
                "timestamp": f"T{offset:+.0f}s",
                "relative_time_s": float(offset),
            }
            for offset, channel, value in readings
        ],
    }


def llm_style_hypothesis(fault_id: str, score: float = 0.9) -> Hypothesis:
    """A hypothesis of the kind a language model might assert from a symptom.

    Carries a high score and NO matched evidence, which is exactly the shape of
    the problem Phase 8 exists to catch: confident, plausible-sounding, and
    untested against the physics.
    """
    definition = get_fault(fault_id)
    assert definition is not None, f"unknown fault {fault_id}"
    return Hypothesis(
        hypothesis_id=f"HYP-llm-{fault_id.lower()}",
        fault_id=definition.fault_id,
        fault_name=definition.fault_name,
        subsystem=definition.subsystem,
        rank=1,
        score=score,
        affected_channels=list(definition.affected_channels),
        origin=HypothesisOrigin.LLM_RANKED,
        severity=definition.severity.value,
    )


def evaluate(dump: dict, fault_id: str):
    """Validate one fault against a dump. Returns ``(verdict, residual_report)``."""
    sequence = estimate_states(dump)
    report = compute_residuals(dump, sequence)
    verdict = validate_hypothesis(
        llm_style_hypothesis(fault_id), report, sequence)
    return verdict, report


def check_for(verdict, constraint_id: str):
    for check in verdict.checks:
        if check.constraint_id == constraint_id:
            return check
    return None


# A wheel that spins up and moves the body by exactly the momentum-exchange
# amount. The actuator is demonstrably working, and the sensors demonstrably
# agree.
HEALTHY_ACTUATOR = synthetic_dump(
    (-30.0, "Gyro_rate_degs", 0.0),
    (-30.0, "RW_speed_rpm", 1000.0),
    (0.0, "Gyro_rate_degs", BODY_RATE_PER_1000_RPM),
    (0.0, "RW_speed_rpm", 2000.0),
)

# The same wheel change, but the body barely moves: the wheel is not delivering.
UNDER_RESPONDING_ACTUATOR = synthetic_dump(
    (-30.0, "Gyro_rate_degs", 0.0),
    (-30.0, "RW_speed_rpm", 1000.0),
    (0.0, "Gyro_rate_degs", -0.1),
    (0.0, "RW_speed_rpm", 2000.0),
)

# Body rate appears with the wheel untouched: momentum came from outside.
UNEXPLAINED_TORQUE = synthetic_dump(
    (-30.0, "Gyro_rate_degs", 0.0),
    (-30.0, "RW_speed_rpm", 1000.0),
    (0.0, "Gyro_rate_degs", 2.0),
    (0.0, "RW_speed_rpm", 1000.0),
)

# Both sensor checks agree with the reported rate.
SENSORS_AGREE = synthetic_dump(
    (-30.0, "Gyro_rate_degs", 0.0),
    (-30.0, "RW_speed_rpm", 1000.0),
    (-30.0, "Attitude_error_deg", 0.10),
    (0.0, "Gyro_rate_degs", BODY_RATE_PER_1000_RPM),
    (0.0, "RW_speed_rpm", 2000.0),
    (0.0, "Attitude_error_deg", 0.05),
)

# Pointing error grows far beyond what the reported rate can explain.
SENSOR_UNDER_REPORTING = synthetic_dump(
    (-30.0, "Gyro_rate_degs", 0.0),
    (-30.0, "RW_speed_rpm", 1000.0),
    (-30.0, "Attitude_error_deg", 0.10),
    (0.0, "Gyro_rate_degs", 0.0),
    (0.0, "RW_speed_rpm", 1000.0),
    (0.0, "Attitude_error_deg", 5.00),
)

# Generation exactly covers the modelled load and charge holds steady.
HEALTHY_POWER = synthetic_dump(
    (-30.0, "SoC_pct", 80.0),
    (-30.0, "I_sa", BALANCED_ARRAY_CURRENT_A),
    (-30.0, "V_bus", 30.0),
    (0.0, "SoC_pct", 80.0),
    (0.0, "I_sa", BALANCED_ARRAY_CURRENT_A),
    (0.0, "V_bus", 30.0),
)

# Charge climbing with no array current: an energy gap, and rising, not draining.
CHARGE_RISING = synthetic_dump(
    (-30.0, "SoC_pct", 50.0),
    (-30.0, "I_sa", 0.0),
    (-30.0, "V_bus", 30.0),
    (0.0, "SoC_pct", 70.0),
    (0.0, "I_sa", 0.0),
    (0.0, "V_bus", 30.0),
)

# The node sits exactly at its modelled steady state with the heater off.
HEALTHY_THERMAL = synthetic_dump(
    (-30.0, "Component_temp_C", THERMAL_QUIESCENT_C),
    (-30.0, "Heater_power_W", 0.0),
    (0.0, "Component_temp_C", THERMAL_QUIESCENT_C),
    (0.0, "Heater_power_W", 0.0),
)

# The node cools steadily while the heater is off.
THERMAL_COOLING = synthetic_dump(
    (-30.0, "Component_temp_C", 60.0),
    (-30.0, "Heater_power_W", 0.0),
    (0.0, "Component_temp_C", 40.0),
    (0.0, "Heater_power_W", 0.0),
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. CONTRACT
# ═══════════════════════════════════════════════════════════════════════════

class TestContract(unittest.TestCase):
    """Every field the Phase 8 specification names is present and populated."""

    def test_verdict_carries_every_required_field(self):
        verdict, _ = evaluate(HEALTHY_ACTUATOR,
                              "AOCS_REACTION_WHEEL_DEGRADATION")
        for field in (
            "hypothesis_id", "validation_status", "violated_constraints",
            "supporting_residuals", "relevant_channels", "timestamps",
            "explanation", "model_version",
        ):
            self.assertTrue(hasattr(verdict, field), f"verdict lacks {field}")

        self.assertTrue(verdict.hypothesis_id)
        self.assertTrue(verdict.violated_constraints)
        self.assertTrue(verdict.supporting_residuals)
        self.assertTrue(verdict.relevant_channels)
        self.assertTrue(verdict.timestamps)
        self.assertTrue(verdict.explanation)
        self.assertTrue(verdict.model_version)

    def test_the_three_statuses_are_exactly_those_specified(self):
        self.assertEqual(
            {s.value for s in PhysicsStatus},
            {"VALID", "INVALID", "UNCERTAIN"},
        )

    def test_all_seven_check_families_exist(self):
        self.assertEqual(
            {f.value for f in CheckFamily},
            {
                "PHYSICAL_CONSISTENCY", "TELEMETRY_CONSISTENCY",
                "STATE_TRANSITION_CONSISTENCY", "ACTUATOR_FEASIBILITY",
                "SENSOR_CONSISTENCY", "ENERGY_CONSISTENCY",
                "THERMAL_CONSISTENCY",
            },
        )

    def test_every_family_is_covered_by_at_least_one_constraint(self):
        covered = {c.family for c in CONSTRAINTS}
        for family in CheckFamily:
            self.assertIn(
                family, covered,
                f"{family.value} has no constraint, so the family would never "
                f"be evaluated",
            )

    def test_model_version_names_every_model_the_verdict_depends_on(self):
        version = model_version()
        for component in ("physics/", "params/", "residuals/", "faults/"):
            self.assertIn(
                component, version,
                f"model_version omits {component}; a verdict depends on it and "
                f"would be silently incomparable across a change to it",
            )

    def test_report_serialises_for_the_api_and_the_audit_record(self):
        report, _h, _r, _s = validate_crash_dump(HEALTHY_ACTUATOR)
        payload = report.model_dump(mode="json")
        json.dumps(payload)
        self.assertFalse(payload["uses_llm"])
        self.assertFalse(payload["flight_qualified"])
        self.assertIn("claim", payload)


# ═══════════════════════════════════════════════════════════════════════════
# 2. CONSTRAINTS AND CLAIMS
# ═══════════════════════════════════════════════════════════════════════════

class TestConstraintCatalogue(unittest.TestCase):
    """The declarative layer is coherent and complete."""

    def test_layer_validates_without_errors(self):
        findings = validate_physics_layer()
        self.assertEqual(findings["errors"], [])

    def test_every_fault_in_the_dictionary_has_a_claims_entry(self):
        """Silence would make an UNCERTAIN verdict indistinguishable from an
        oversight."""
        for fault_id in fault_ids():
            self.assertIn(
                fault_id, CLAIMS_BY_FAULT,
                f"{fault_id} has no physics claims entry; add one, using an "
                f"empty claim set with a stated reason if coverage is absent",
            )

    def test_every_constraint_states_what_would_refute_it(self):
        for constraint in CONSTRAINTS:
            self.assertGreater(
                len(constraint.refutation_rule.strip()), 30,
                f"{constraint.constraint_id} has no reviewable refutation rule",
            )

    def test_faults_without_coverage_say_why(self):
        for fault_id, claims in CLAIMS_BY_FAULT.items():
            if claims.constraint_ids():
                continue
            self.assertIn(
                "NO PHYSICS COVERAGE", claims.rationale,
                f"{fault_id} declares no constraints but does not say why",
            )

    def test_status_exposes_the_catalogue_and_the_thresholds(self):
        status = physics_status()
        self.assertEqual(len(status["constraints"]), len(CONSTRAINTS))
        self.assertIn("saturation_refutation_ceiling", status)
        self.assertIn("status_rule", status)
        self.assertFalse(status["uses_llm"])
        self.assertFalse(status["llm_can_override"])
        self.assertFalse(status["flight_qualified"])


# ═══════════════════════════════════════════════════════════════════════════
# 3. WRONG HYPOTHESES ARE REJECTED
# ═══════════════════════════════════════════════════════════════════════════

class TestWrongHypothesesAreRejected(unittest.TestCase):
    """The core Phase 8 claim: deterministic validation rejects a bad hypothesis.

    Each case constructs telemetry in which the hypothesis is demonstrably
    wrong, then asserts the verdict is INVALID and names the constraint that
    refuted it.
    """

    def test_reaction_wheel_failure_rejected_when_the_wheel_is_working(self):
        """The Phase 8 specification's own worked example.

        The wheel spins up by 1000 rpm and the body counter-rotates by exactly
        the momentum-exchange amount. The wheel is delivering its torque, so a
        degraded-authority claim is refuted by measurement.
        """
        verdict, report = evaluate(HEALTHY_ACTUATOR,
                                   "AOCS_REACTION_WHEEL_DEGRADATION")

        self.assertIs(verdict.validation_status, PhysicsStatus.INVALID)
        self.assertIn("PHYS_ACTUATOR_AUTHORITY", verdict.violated_constraints)

        check = check_for(verdict, "PHYS_ACTUATOR_AUTHORITY")
        self.assertIs(check.outcome, CheckOutcome.FAIL)
        self.assertIs(check.family, CheckFamily.ACTUATOR_FEASIBILITY)

        # The refutation must cite the residual it rests on.
        self.assertTrue(verdict.supporting_residuals)
        self.assertEqual(
            verdict.supporting_residuals[0].channel, "Gyro_rate_degs")
        self.assertEqual(
            verdict.supporting_residuals[0].status, "CONSISTENT")

    def test_saturation_rejected_when_the_wheel_has_capacity_left(self):
        verdict, _ = evaluate(HEALTHY_ACTUATOR,
                              "AOCS_REACTION_WHEEL_DEGRADATION")
        check = check_for(verdict, "PHYS_ACTUATOR_SATURATION")
        self.assertIs(check.outcome, CheckOutcome.FAIL)
        # 2000 rpm against a declared 6000 limit is a third, below the ceiling.
        self.assertLess(2000.0 / 6000.0, SATURATION_REFUTATION_CEILING)

    def test_external_disturbance_rejected_when_momentum_is_accounted_for(self):
        verdict, _ = evaluate(HEALTHY_ACTUATOR, "AOCS_EXTERNAL_DISTURBANCE")

        self.assertIs(verdict.validation_status, PhysicsStatus.INVALID)
        self.assertIn("PHYS_MOMENTUM_ACCOUNTED", verdict.violated_constraints)
        check = check_for(verdict, "PHYS_MOMENTUM_ACCOUNTED")
        self.assertIs(check.family, CheckFamily.PHYSICAL_CONSISTENCY)

    def test_sensor_fault_rejected_when_both_sensor_checks_agree(self):
        verdict, _ = evaluate(SENSORS_AGREE, "AOCS_SENSOR_FAULT")

        self.assertIs(verdict.validation_status, PhysicsStatus.INVALID)
        self.assertIn("PHYS_SENSOR_CORROBORATION",
                      verdict.violated_constraints)
        check = check_for(verdict, "PHYS_SENSOR_CORROBORATION")
        self.assertIs(check.family, CheckFamily.SENSOR_CONSISTENCY)

    def test_gyro_seu_rejected_when_the_rate_sensor_is_corroborated(self):
        """An upset claims the rate data is corrupt. Here it demonstrably is not."""
        verdict, _ = evaluate(SENSORS_AGREE, "ADCS_GYRO_SEU")
        self.assertIs(verdict.validation_status, PhysicsStatus.INVALID)
        self.assertIn("PHYS_SENSOR_CORROBORATION",
                      verdict.violated_constraints)

    def test_solar_undervolt_rejected_when_the_energy_balance_holds(self):
        verdict, _ = evaluate(HEALTHY_POWER, "EPS_SOLAR_UNDERVOLT")

        self.assertIs(verdict.validation_status, PhysicsStatus.INVALID)
        self.assertIn("PHYS_ENERGY_BALANCE", verdict.violated_constraints)
        check = check_for(verdict, "PHYS_ENERGY_BALANCE")
        self.assertIs(check.family, CheckFamily.ENERGY_CONSISTENCY)

    def test_battery_drain_rejected_when_charge_is_rising(self):
        verdict, _ = evaluate(CHARGE_RISING, "EPS_BATTERY_DEGRADATION")

        self.assertIs(verdict.validation_status, PhysicsStatus.INVALID)
        self.assertIn("PHYS_ENERGY_DIRECTION", verdict.violated_constraints)
        check = check_for(verdict, "PHYS_ENERGY_DIRECTION")
        self.assertIs(check.family, CheckFamily.STATE_TRANSITION_CONSISTENCY)
        self.assertIn("ROSE", check.detail)

    def test_thermal_runaway_rejected_when_the_heat_balance_holds(self):
        verdict, _ = evaluate(HEALTHY_THERMAL, "TCS_THERMAL_RUNAWAY")

        self.assertIs(verdict.validation_status, PhysicsStatus.INVALID)
        self.assertIn("PHYS_HEAT_BALANCE", verdict.violated_constraints)
        check = check_for(verdict, "PHYS_HEAT_BALANCE")
        self.assertIs(check.family, CheckFamily.THERMAL_CONSISTENCY)

    def test_thermal_runaway_rejected_when_the_component_is_cooling(self):
        verdict, _ = evaluate(THERMAL_COOLING, "TCS_THERMAL_RUNAWAY")

        self.assertIs(verdict.validation_status, PhysicsStatus.INVALID)
        self.assertIn("PHYS_THERMAL_DIRECTION", verdict.violated_constraints)
        check = check_for(verdict, "PHYS_THERMAL_DIRECTION")
        self.assertIn("COOLED", check.detail)

    def test_an_invalid_verdict_explains_itself_in_physical_terms(self):
        verdict, _ = evaluate(HEALTHY_ACTUATOR,
                              "AOCS_REACTION_WHEEL_DEGRADATION")
        explanation = verdict.explanation

        self.assertIn("INVALID", explanation)
        self.assertIn("PHYS_ACTUATOR_AUTHORITY", explanation)
        # The qualification must travel with the rejection.
        self.assertIn("SIMPLIFIED", explanation)
        self.assertIn("not proof about the hardware", explanation)


# ═══════════════════════════════════════════════════════════════════════════
# 4. RIGHT HYPOTHESES SURVIVE
# ═══════════════════════════════════════════════════════════════════════════

class TestCorrectHypothesesSurvive(unittest.TestCase):
    """Validation must not reject a hypothesis the evidence supports."""

    def test_wheel_degradation_corroborated_when_the_body_under_responds(self):
        verdict, _ = evaluate(UNDER_RESPONDING_ACTUATOR,
                              "AOCS_REACTION_WHEEL_DEGRADATION")

        self.assertIsNot(verdict.validation_status, PhysicsStatus.INVALID)
        check = check_for(verdict, "PHYS_ACTUATOR_AUTHORITY")
        self.assertIs(check.outcome, CheckOutcome.PASS)
        self.assertIn("PHYS_ACTUATOR_AUTHORITY",
                      verdict.corroborated_constraints)

    def test_external_disturbance_corroborated_when_momentum_is_unexplained(self):
        verdict, _ = evaluate(UNEXPLAINED_TORQUE, "AOCS_EXTERNAL_DISTURBANCE")

        self.assertIs(verdict.validation_status, PhysicsStatus.VALID)
        self.assertEqual(verdict.violated_constraints, [])
        self.assertIn("PHYS_MOMENTUM_ACCOUNTED",
                      verdict.corroborated_constraints)

    def test_sensor_fault_corroborated_when_pointing_exceeds_its_bound(self):
        verdict, _ = evaluate(SENSOR_UNDER_REPORTING, "AOCS_SENSOR_FAULT")

        self.assertIs(verdict.validation_status, PhysicsStatus.VALID)
        self.assertIn("PHYS_SENSOR_CORROBORATION",
                      verdict.corroborated_constraints)

    def test_valid_does_not_claim_confirmation(self):
        verdict, _ = evaluate(UNEXPLAINED_TORQUE, "AOCS_EXTERNAL_DISTURBANCE")
        self.assertIn("not confirmed", verdict.explanation.lower())

    def test_refuting_one_alternative_mechanism_does_not_refute_the_fault(self):
        """Pins the aggregation rule for alternative mechanisms.

        ``AOCS_REACTION_WHEEL_DEGRADATION`` is named "degradation OR saturation".
        On this telemetry the wheel is at a third of its speed limit — so the
        saturation alternative is refuted — while the body under-responds to the
        wheel, which corroborates the degradation alternative. Refuting one
        alternative must not reject the fault.
        """
        verdict, _ = evaluate(UNDER_RESPONDING_ACTUATOR,
                              "AOCS_REACTION_WHEEL_DEGRADATION")

        self.assertIs(
            check_for(verdict, "PHYS_ACTUATOR_SATURATION").outcome,
            CheckOutcome.FAIL,
        )
        self.assertIs(
            check_for(verdict, "PHYS_ACTUATOR_AUTHORITY").outcome,
            CheckOutcome.PASS,
        )
        self.assertIs(
            verdict.validation_status, PhysicsStatus.VALID,
            "one refuted alternative among several must not reject the fault",
        )
        # The refuted alternative is still reported, not silently dropped...
        self.assertIn("PHYS_ACTUATOR_SATURATION", verdict.violated_constraints)
        self.assertIn("alternative mechanism was refuted", verdict.explanation)
        # ...but it did NOT refute the hypothesis, and the contract says so
        # separately so a reader cannot mistake a failure for a rejection.
        self.assertEqual(verdict.refuted_by, [])

    def test_refuted_by_names_only_the_failures_that_rejected_the_fault(self):
        verdict, _ = evaluate(HEALTHY_ACTUATOR,
                              "AOCS_REACTION_WHEEL_DEGRADATION")
        self.assertIs(verdict.validation_status, PhysicsStatus.INVALID)
        self.assertEqual(
            sorted(verdict.refuted_by), sorted(verdict.violated_constraints),
            "when every alternative fails, all of them are refuting",
        )

    def test_both_alternatives_refuted_does_reject_the_fault(self):
        """The other half of the rule: refute every alternative and it is out."""
        verdict, _ = evaluate(HEALTHY_ACTUATOR,
                              "AOCS_REACTION_WHEEL_DEGRADATION")
        self.assertEqual(
            sorted(verdict.violated_constraints),
            ["PHYS_ACTUATOR_AUTHORITY", "PHYS_ACTUATOR_SATURATION"],
        )
        self.assertIs(verdict.validation_status, PhysicsStatus.INVALID)

    def test_a_real_simulated_thermal_fault_is_not_rejected(self):
        """End to end on simulator output rather than a hand-built fixture."""
        sim = SatelliteFaultSimulator(seed=1)
        dump = sim.generate_crash_dump("TCS_THERMAL_RUNAWAY", 900)
        report, _h, _r, _s = validate_crash_dump(dump)

        verdict = report.verdict_for_fault("TCS_THERMAL_RUNAWAY")
        if verdict is None:
            self.skipTest(
                "TCS_THERMAL_RUNAWAY was not a candidate for this dump")
        self.assertIsNot(
            verdict.validation_status, PhysicsStatus.INVALID,
            "the labelled fault must not be contradicted by the physics on its "
            "own simulated telemetry",
        )


# ═══════════════════════════════════════════════════════════════════════════
# 5. ASYMMETRY: ABSENT EVIDENCE IS NOT REFUTATION
# ═══════════════════════════════════════════════════════════════════════════

class TestAsymmetry(unittest.TestCase):
    """A missing corroboration yields UNCERTAIN, never INVALID."""

    def test_an_empty_dump_yields_uncertain_not_invalid(self):
        report = compute_residuals({})
        verdict = validate_hypothesis(
            llm_style_hypothesis("AOCS_REACTION_WHEEL_DEGRADATION"),
            report, estimate_states({}),
        )
        self.assertIs(verdict.validation_status, PhysicsStatus.UNCERTAIN)
        self.assertEqual(verdict.violated_constraints, [])

    def test_undecidable_residuals_yield_indeterminate_checks(self):
        # One sample: no step, so no residual can be decided.
        dump = synthetic_dump((0.0, "Gyro_rate_degs", 0.5))
        verdict, _ = evaluate(dump, "AOCS_EXTERNAL_DISTURBANCE")

        self.assertIs(verdict.validation_status, PhysicsStatus.UNCERTAIN)
        for check in verdict.checks:
            self.assertIsNot(check.outcome, CheckOutcome.FAIL)
        self.assertTrue(verdict.indeterminate_constraints)

    def test_uncertain_explanation_denies_being_a_pass(self):
        dump = synthetic_dump((0.0, "Gyro_rate_degs", 0.5))
        verdict, _ = evaluate(dump, "AOCS_EXTERNAL_DISTURBANCE")
        self.assertIn("not a pass", verdict.explanation.lower())

    def test_no_hypotheses_is_not_a_clean_bill_of_health(self):
        report = validate_hypotheses([], compute_residuals({}), None)
        self.assertEqual(report.verdicts, [])
        self.assertIn("not", report.summary.lower())
        self.assertIn("clean bill", report.summary.lower())

    def test_status_for_an_unexamined_fault_is_uncertain(self):
        report, _h, _r, _s = validate_crash_dump(HEALTHY_ACTUATOR)
        self.assertIs(
            report.status_for_fault("A_FAULT_NOBODY_EVALUATED"),
            PhysicsStatus.UNCERTAIN,
            "a fault physics never examined has not passed anything",
        )

    def test_saturation_indeterminate_without_a_wheel_reading(self):
        dump = synthetic_dump(
            (-30.0, "Gyro_rate_degs", 0.0),
            (0.0, "Gyro_rate_degs", 0.0),
        )
        verdict, _ = evaluate(dump, "AOCS_REACTION_WHEEL_DEGRADATION")
        check = check_for(verdict, "PHYS_ACTUATOR_SATURATION")
        self.assertIs(check.outcome, CheckOutcome.INDETERMINATE)


# ═══════════════════════════════════════════════════════════════════════════
# 6. FAULTS THE MODELS CANNOT REACH
# ═══════════════════════════════════════════════════════════════════════════

class TestNoCoverage(unittest.TestCase):
    """A coverage gap must be reported as such, not as a pass or a rejection."""

    def test_obc_and_comms_faults_are_uncertain_by_construction(self):
        for fault_id in ("OBC_WATCHDOG_OVERFLOW", "COMMS_TRANSPONDER_LOSS"):
            with self.subTest(fault=fault_id):
                verdict, _ = evaluate(HEALTHY_ACTUATOR, fault_id)
                self.assertIs(
                    verdict.validation_status, PhysicsStatus.UNCERTAIN)
                self.assertFalse(verdict.has_physics_coverage)
                self.assertEqual(verdict.applicable_constraints, [])
                self.assertIn("NO PHYSICS COVERAGE", verdict.claims_rationale)

    def test_a_coverage_gap_is_never_reported_as_a_violation(self):
        verdict, _ = evaluate(HEALTHY_ACTUATOR, "OBC_WATCHDOG_OVERFLOW")
        self.assertEqual(verdict.violated_constraints, [])
        self.assertIn("coverage decision", verdict.explanation)
        self.assertIn("not evidence about the", verdict.explanation)

    def test_the_report_warns_when_coverage_is_absent(self):
        report = validate_hypotheses(
            [llm_style_hypothesis("OBC_WATCHDOG_OVERFLOW")],
            compute_residuals(HEALTHY_ACTUATOR),
            estimate_states(HEALTHY_ACTUATOR),
        )
        joined = " ".join(report.warnings)
        self.assertIn("no physics coverage", joined.lower())

    def test_multi_cascade_has_no_single_mechanism_to_check(self):
        verdict, _ = evaluate(HEALTHY_ACTUATOR, "MULTI_CASCADE")
        self.assertIs(verdict.validation_status, PhysicsStatus.UNCERTAIN)
        self.assertFalse(verdict.has_physics_coverage)


# ═══════════════════════════════════════════════════════════════════════════
# 7. THE LLM BOUNDARY
# ═══════════════════════════════════════════════════════════════════════════

class TestLLMCannotOverride(unittest.TestCase):
    """No code path lets a language model change a verdict."""

    def test_an_llm_claiming_valid_does_not_change_an_invalid_verdict(self):
        verdict, _ = evaluate(HEALTHY_ACTUATOR,
                              "AOCS_REACTION_WHEEL_DEGRADATION")
        self.assertIs(verdict.validation_status, PhysicsStatus.INVALID)

        returned, attempt = reconcile_llm_claim(verdict, "VALID")

        self.assertIs(returned.validation_status, PhysicsStatus.INVALID)
        self.assertIs(returned, verdict, "the verdict object must be unchanged")
        self.assertFalse(attempt.overridden)
        self.assertTrue(attempt.disagreement)
        self.assertEqual(attempt.llm_claimed_status, "VALID")
        self.assertIs(attempt.deterministic_status, PhysicsStatus.INVALID)

    def test_llm_agreement_is_recorded_as_carrying_no_weight(self):
        verdict, _ = evaluate(HEALTHY_ACTUATOR,
                              "AOCS_REACTION_WHEEL_DEGRADATION")
        _returned, attempt = reconcile_llm_claim(verdict, "INVALID")
        self.assertFalse(attempt.disagreement)
        self.assertIn("changes nothing", attempt.note.lower())

    def test_reconcile_never_reports_an_override_whatever_is_claimed(self):
        verdict, _ = evaluate(HEALTHY_ACTUATOR,
                              "AOCS_REACTION_WHEEL_DEGRADATION")
        for claim in ("VALID", "valid", "UNCERTAIN", "", None, "NONSENSE", 42):
            with self.subTest(claim=claim):
                returned, attempt = reconcile_llm_claim(verdict, claim)
                self.assertIs(
                    returned.validation_status, PhysicsStatus.INVALID)
                self.assertFalse(attempt.overridden)

    def test_validate_hypothesis_has_no_llm_parameter(self):
        """Structural, not behavioural: there is nowhere to inject model output."""
        import inspect

        for function in (validate_hypothesis, validate_hypotheses):
            names = set(inspect.signature(function).parameters)
            for forbidden in ("llm", "llm_output", "model_output", "claim",
                              "llm_status", "override"):
                self.assertNotIn(
                    forbidden, names,
                    f"{function.__name__} exposes {forbidden!r}, which would "
                    f"give a language model a route into the verdict",
                )

    def test_no_model_client_appears_in_the_physics_source(self):
        from app.validation import physics

        source = Path(physics.__file__).read_text(encoding="utf-8")
        for token in ("google.genai", "google.generativeai", "openai",
                      "anthropic", "ollama", "_call_llm", "chat.completions"):
            self.assertNotIn(
                token, source,
                f"physics.py references {token!r}; validation must not be able "
                f"to consult a language model",
            )

    def test_physics_does_not_import_the_agent(self):
        from app.validation import physics

        source = Path(physics.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from app.agent", source)
        self.assertNotIn("import app.agent", source)


# ═══════════════════════════════════════════════════════════════════════════
# 8. DOWNGRADING
# ═══════════════════════════════════════════════════════════════════════════

class TestDowngrading(unittest.TestCase):
    """A contradicted hypothesis is demoted, retained, and re-ranked."""

    #: A dump where the power system is demonstrably healthy — generation covers
    #: the modelled load and charge holds — while the wheel is demonstrably
    #: under-delivering. So an EPS hypothesis is refuted on the same telemetry
    #: that corroborates the wheel hypothesis, which is what makes a relative
    #: demotion observable. On uniformly healthy telemetry every AOCS hypothesis
    #: is correctly refuted and there would be no survivor to be demoted below.
    MIXED = synthetic_dump(
        (-30.0, "Gyro_rate_degs", 0.0),
        (-30.0, "RW_speed_rpm", 1000.0),
        (-30.0, "SoC_pct", 80.0),
        (-30.0, "I_sa", BALANCED_ARRAY_CURRENT_A),
        (-30.0, "V_bus", 30.0),
        (0.0, "Gyro_rate_degs", -0.1),
        (0.0, "RW_speed_rpm", 2000.0),
        (0.0, "SoC_pct", 80.0),
        (0.0, "I_sa", BALANCED_ARRAY_CURRENT_A),
        (0.0, "V_bus", 30.0),
    )

    def _set_with_invalid_leader(self):
        """A set whose top-scoring hypothesis the physics contradicts.

        Leader: EPS_SOLAR_UNDERVOLT at 0.90, refuted because the energy balance
        holds. Runner-up: the wheel fault at 0.40, corroborated because the body
        under-responded to the wheel.
        """
        from app.diagnosis.candidates import HypothesisSet

        leader = llm_style_hypothesis("EPS_SOLAR_UNDERVOLT", score=0.90)
        runner_up = llm_style_hypothesis(
            "AOCS_REACTION_WHEEL_DEGRADATION", score=0.40).model_copy(
                update={"rank": 2})
        return HypothesisSet(hypotheses=[leader, runner_up])

    def _validated(self):
        hypothesis_set = self._set_with_invalid_leader()
        sequence = estimate_states(self.MIXED)
        residuals = compute_residuals(self.MIXED, sequence)
        physics = validate_hypotheses(hypothesis_set, residuals, sequence)
        return hypothesis_set, physics

    def test_a_contradicted_leader_is_demoted_below_a_surviving_candidate(self):
        hypothesis_set, physics = self._validated()

        self.assertIn("EPS_SOLAR_UNDERVOLT", physics.invalidated)
        self.assertIn("AOCS_REACTION_WHEEL_DEGRADATION", physics.validated)

        adjusted = apply_physics_verdicts(hypothesis_set, physics)
        ranked = [h.fault_id for h in adjusted.hypotheses]

        self.assertEqual(
            ranked[0], "AOCS_REACTION_WHEEL_DEGRADATION",
            "the surviving hypothesis must outrank the refuted one, even though "
            "the refuted one scored higher on signature evidence",
        )
        self.assertEqual(ranked[1], "EPS_SOLAR_UNDERVOLT")

    def test_a_contradicted_hypothesis_is_retained_not_deleted(self):
        hypothesis_set, physics = self._validated()
        adjusted = apply_physics_verdicts(hypothesis_set, physics)

        faults = {h.fault_id for h in adjusted.hypotheses}
        self.assertIn(
            "EPS_SOLAR_UNDERVOLT", faults,
            "the demotion must stay visible; deleting it would hide the "
            "disagreement from the operator",
        )

    def test_the_demotion_is_recorded_on_the_hypothesis(self):
        hypothesis_set, physics = self._validated()
        adjusted = apply_physics_verdicts(hypothesis_set, physics)

        demoted = next(h for h in adjusted.hypotheses
                       if h.fault_id == "EPS_SOLAR_UNDERVOLT")
        self.assertIn("PHYSICS INVALID", demoted.notes)
        self.assertIn("refuted by", demoted.notes)
        self.assertIn("PHYS_ENERGY_BALANCE", demoted.notes)
        self.assertLess(demoted.score, 0.90)
        self.assertAlmostEqual(
            demoted.score, round(0.90 * INVALID_SCORE_MULTIPLIER, 4), places=6)

    def test_corroboration_never_raises_a_score(self):
        """Physics demotes; it does not promote.

        A simplified model with four assumed parameters must not outrank the
        measured detector evidence Phase 6 scored a hypothesis on.
        """
        self.assertEqual(VALID_SCORE_MULTIPLIER, 1.0)

        from app.diagnosis.candidates import HypothesisSet

        corroborated = llm_style_hypothesis(
            "AOCS_EXTERNAL_DISTURBANCE", score=0.42)
        hypothesis_set = HypothesisSet(hypotheses=[corroborated])
        sequence = estimate_states(UNEXPLAINED_TORQUE)
        residuals = compute_residuals(UNEXPLAINED_TORQUE, sequence)
        physics = validate_hypotheses(hypothesis_set, residuals, sequence)
        self.assertIn("AOCS_EXTERNAL_DISTURBANCE", physics.validated)

        adjusted = apply_physics_verdicts(hypothesis_set, physics)
        self.assertEqual(adjusted.hypotheses[0].score, 0.42)

    def test_the_multiplier_genuinely_demotes(self):
        self.assertGreaterEqual(INVALID_SCORE_MULTIPLIER, 0.0)
        self.assertLess(INVALID_SCORE_MULTIPLIER, 1.0)

    def test_the_set_warns_that_a_hypothesis_was_contradicted(self):
        hypothesis_set, physics = self._validated()
        adjusted = apply_physics_verdicts(hypothesis_set, physics)

        joined = " ".join(adjusted.warnings)
        self.assertIn("contradicted", joined.lower())
        self.assertIn("simplified models", joined.lower())


# ═══════════════════════════════════════════════════════════════════════════
# 9. DETERMINISM
# ═══════════════════════════════════════════════════════════════════════════

class TestDeterminism(unittest.TestCase):
    """The same inputs must always give the same verdicts."""

    def test_synthetic_fixtures_are_byte_identical_across_runs(self):
        for dump in (HEALTHY_ACTUATOR, UNDER_RESPONDING_ACTUATOR,
                     UNEXPLAINED_TORQUE, HEALTHY_POWER, CHARGE_RISING,
                     HEALTHY_THERMAL, THERMAL_COOLING):
            first, *_ = validate_crash_dump(dump)
            second, *_ = validate_crash_dump(dump)
            self.assertEqual(
                json.dumps(first.model_dump(mode="json"), sort_keys=True),
                json.dumps(second.model_dump(mode="json"), sort_keys=True),
            )

    def test_every_shipped_scenario_is_deterministic(self):
        for scenario in get_all_scenarios():
            dump = scenario.get("crash_dump", scenario)
            first, *_ = validate_crash_dump(dump)
            second, *_ = validate_crash_dump(dump)
            self.assertEqual(
                json.dumps(first.model_dump(mode="json"), sort_keys=True),
                json.dumps(second.model_dump(mode="json"), sort_keys=True),
                f"scenario {dump.get('scenario_id')} is not deterministic",
            )

    def test_simulated_dumps_are_deterministic(self):
        for fault in ("ADCS_GYRO_SEU", "EPS_SOLAR_UNDERVOLT",
                      "TCS_THERMAL_RUNAWAY", "MULTI_CASCADE"):
            sim = SatelliteFaultSimulator(seed=11)
            dump = sim.generate_crash_dump(fault, 900)
            first, *_ = validate_crash_dump(dump)
            second, *_ = validate_crash_dump(dump)
            self.assertEqual(
                json.dumps(first.model_dump(mode="json"), sort_keys=True),
                json.dumps(second.model_dump(mode="json"), sort_keys=True),
                f"{fault} is not deterministic",
            )

    def test_malformed_input_degrades_rather_than_raising(self):
        for malformed in (None, {}, [], "not a dump", 42,
                          {"pre_fault_telemetry_window": "not a list"}):
            report, *_ = validate_crash_dump(malformed)  # type: ignore[arg-type]
            self.assertIsNotNone(report)
            json.dumps(report.model_dump(mode="json"))

    def test_trend_helper_needs_two_fresh_readings(self):
        single = estimate_states(synthetic_dump((0.0, "SoC_pct", 80.0)))
        trend, values = observed_trend(single, "SoC_pct")
        self.assertIs(trend, Trend.UNKNOWN)
        self.assertEqual(len(values), 1)

        rising = estimate_states(CHARGE_RISING)
        trend, values = observed_trend(rising, "SoC_pct")
        self.assertIs(trend, Trend.RISING)
        self.assertEqual(values, [50.0, 70.0])


# ═══════════════════════════════════════════════════════════════════════════
# 10. PIPELINE AND API INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

class TestIntegration(unittest.TestCase):
    """The stage is wired in, recorded, and no longer advertised as absent."""

    def _record(self, dump: dict):
        from app.agent.agent import _audit_record_physics_validation
        from app.audit import AuditRecorder

        recorder = AuditRecorder.begin(dump, origin="test_phase8")
        _audit_record_physics_validation(recorder, dump)
        return recorder.build()

    def test_physics_validation_is_recorded_with_real_verdicts(self):
        from app.audit import Stage, StageStatus

        record = self._record(HEALTHY_ACTUATOR)
        entries = [e for e in record.entries
                   if e.stage is Stage.PHYSICS_VALIDATION]
        self.assertEqual(len(entries), 1)

        entry = entries[0]
        self.assertIsNot(entry.status, StageStatus.NOT_IMPLEMENTED)
        self.assertIn(entry.status, (StageStatus.OK, StageStatus.DEGRADED))
        self.assertIn("physics_report", entry.payload)
        self.assertFalse(entry.payload["uses_llm"])
        self.assertFalse(entry.payload["llm_can_override"])
        self.assertTrue(entry.payload["runs_on_deterministic_candidates"])

    def test_the_recorded_verdicts_come_from_deterministic_candidates(self):
        record = self._record(HEALTHY_ACTUATOR)
        payload = record.entries[-1].payload
        self.assertEqual(
            payload["hypothesis_source"], "app.diagnosis.generate_hypotheses",
            "verdicts must be computed on the deterministic candidate set, not "
            "on whatever the model proposed",
        )

    def test_the_not_implemented_placeholder_is_gone(self):
        agent_source = (
            Path(_BACKEND) / "app" / "agent" / "agent.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            "_audit_record_physics_absent", agent_source,
            "the Phase 4 NOT_IMPLEMENTED placeholder is still referenced",
        )

    def test_audit_status_lists_no_unimplemented_stages(self):
        try:
            from app.main import audit_status
        except Exception as exc:  # pragma: no cover — fastapi may be absent
            self.skipTest(f"app.main unavailable: {exc}")

        response = audit_status()
        self.assertEqual(
            response.not_implemented_stages, [],
            "every stage now records a result, so the list must be empty",
        )

    def test_the_api_endpoint_returns_a_validation_report(self):
        try:
            from app.main import physics_validation_endpoint
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"app.main unavailable: {exc}")

        from app.api.models import CrashDumpRequest

        request = CrashDumpRequest.model_validate(HEALTHY_ACTUATOR)
        report = physics_validation_endpoint(request)

        self.assertGreaterEqual(report.hypotheses_examined, 0)
        self.assertTrue(report.model_version)
        self.assertFalse(report.uses_llm)
        json.dumps(report.model_dump(mode="json"))

    def test_the_constraints_endpoint_serves_the_catalogue(self):
        try:
            from app.main import physics_constraints_endpoint
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"app.main unavailable: {exc}")

        payload = physics_constraints_endpoint()
        self.assertEqual(len(payload["constraints"]), len(CONSTRAINTS))
        self.assertIn("faults_without_coverage", payload)
        self.assertFalse(payload["llm_can_override"])
        json.dumps(payload)

    def test_the_full_chain_composes_without_an_llm(self):
        report, hypotheses, residuals, sequence = validate_crash_dump(
            HEALTHY_ACTUATOR)
        self.assertFalse(report.uses_llm)
        self.assertFalse(residuals.uses_llm)
        self.assertFalse(hypotheses.uses_llm)
        self.assertGreater(len(sequence), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
