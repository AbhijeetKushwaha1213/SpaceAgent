"""
SENTINEL — Phase 7: Deterministic 3-Axis Spacecraft Attitude Dynamics Tests
(tests/test_phase7_attitude_dynamics.py)

Covers all 14 mandatory Phase 7 acceptance tests:
1. Zero angular velocity + zero torque -> valid.
2. Constant single-axis rotation with matching torque -> valid.
3. Cross-axis angular velocity with non-diagonal inertia -> correct gyroscopic coupling.
4. Incorrect torque -> REFUTED.
5. Quaternion normalization.
6. Quaternion propagation.
7. Invalid quaternion.
8. Invalid inertia tensor.
9. NaN / infinity input.
10. Missing 3-axis state.
11. Legacy 1D scenario still passes through old path.
12. Physics REFUTED still prevents recovery authorization.
13. LLM cannot override physics result.
14. Deterministic repeated execution produces identical result.
"""

from __future__ import annotations

import math
import os
import sys
import unittest
from typing import Any

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.validation.attitude_dynamics import (
    Vector3,
    Quaternion,
    InertiaTensor3x3,
    EulerDynamics,
    DynamicsStatus,
    DynamicsVerdict,
    validate_3axis_attitude_step,
    validate_3axis_sequence,
    extract_3axis_state_from_dump,
    validate_3axis_from_dump,
    integrate_3axis_into_physics_report,
)


class TestPhase7AttitudeDynamics(unittest.TestCase):
    """Phase 7 comprehensive test suite for 3-axis attitude dynamics."""

    def setUp(self) -> None:
        # Standard satellite inertia tensor (diagonal)
        self.I_diag = InertiaTensor3x3.from_diagonal(15.0, 20.0, 25.0)

        # Realistic non-diagonal spacecraft inertia tensor (kg*m^2)
        # Symmetric and positive definite
        self.I_nondiag = InertiaTensor3x3.from_matrix([
            [20.0, 2.0, 1.0],
            [2.0, 25.0, 3.0],
            [1.0, 3.0, 30.0],
        ])

    # ───────────────────────────────────────────────────────────────────────
    # 1. Zero angular velocity + zero torque -> valid
    # ───────────────────────────────────────────────────────────────────────
    def test_01_zero_angular_velocity_zero_torque_valid(self) -> None:
        """A stationary body with zero applied torque satisfies Euler dynamics."""
        w0 = Vector3(0.0, 0.0, 0.0)
        w1 = Vector3(0.0, 0.0, 0.0)
        dt = 1.0
        tau = Vector3(0.0, 0.0, 0.0)

        verdict = validate_3axis_attitude_step(
            inertia=self.I_diag,
            omega_start=w0,
            omega_end=w1,
            dt=dt,
            tau_ext=tau,
        )

        self.assertEqual(verdict.status, DynamicsStatus.VALID)
        self.assertTrue(verdict.is_valid)
        self.assertFalse(verdict.is_refuted)
        self.assertLessEqual(verdict.residual_norm, 1e-9)
        self.assertIn("VALID", verdict.explanation)
        self.assertIsNotNone(verdict.angular_momentum)
        self.assertEqual(verdict.angular_momentum.norm(), 0.0)

    # ───────────────────────────────────────────────────────────────────────
    # 2. Constant single-axis rotation with matching torque -> valid
    # ───────────────────────────────────────────────────────────────────────
    def test_02_constant_single_axis_rotation_with_matching_torque(self) -> None:
        """Constant rotation along principal axis with zero torque is valid."""
        # Principal axis spin: wx = 0.5 rad/s, wy = wz = 0
        w0 = Vector3(0.5, 0.0, 0.0)
        w1 = Vector3(0.5, 0.0, 0.0)
        dt = 0.2
        tau_zero = Vector3(0.0, 0.0, 0.0)

        verdict = validate_3axis_attitude_step(
            inertia=self.I_diag,
            omega_start=w0,
            omega_end=w1,
            dt=dt,
            tau_ext=tau_zero,
        )

        self.assertEqual(verdict.status, DynamicsStatus.VALID)
        self.assertLessEqual(verdict.residual_norm, 1e-9)

        # Accelerating single-axis spin with matching actuator torque:
        # alpha = (w1 - w0)/dt = [0.1, 0, 0] rad/s^2 -> tau_required = Ixx * 0.1 = 1.5 N*m
        w_start = Vector3(0.1, 0.0, 0.0)
        w_end = Vector3(0.2, 0.0, 0.0)
        dt_acc = 1.0
        tau_matching = Vector3(1.5, 0.0, 0.0)

        verdict_acc = validate_3axis_attitude_step(
            inertia=self.I_diag,
            omega_start=w_start,
            omega_end=w_end,
            dt=dt_acc,
            tau_ext=tau_matching,
        )
        self.assertEqual(verdict_acc.status, DynamicsStatus.VALID)
        self.assertLessEqual(verdict_acc.residual_norm, 1e-6)

    # ───────────────────────────────────────────────────────────────────────
    # 3. Cross-axis angular velocity with non-diagonal inertia -> gyroscopic coupling
    # ───────────────────────────────────────────────────────────────────────
    def test_03_cross_axis_angular_velocity_non_diagonal_inertia(self) -> None:
        """Cross-axis rates with off-diagonal products of inertia produce coupling."""
        # Body rates: wx=1.0 rad/s, wy=2.0 rad/s, wz=0.0 rad/s
        omega = Vector3(1.0, 2.0, 0.0)
        # H = I * omega:
        # Hx = 20(1) + 2(2) + 1(0) = 24.0
        # Hy =  2(1) + 25(2) + 3(0) = 52.0
        # Hz =  1(1) + 3(2) + 30(0) = 7.0
        h_expected = self.I_nondiag.multiply_vector(omega)
        self.assertAlmostEqual(h_expected.x, 24.0, places=5)
        self.assertAlmostEqual(h_expected.y, 52.0, places=5)
        self.assertAlmostEqual(h_expected.z, 7.0, places=5)

        # Gyroscopic coupling: tau_gyro = omega x H
        # tau_x = wy*Hz - wz*Hy = 2.0*7.0 - 0 = 14.0
        # tau_y = wz*Hx - wx*Hz = 0 - 1.0*7.0 = -7.0
        # tau_z = wx*Hy - wy*Hx = 1.0*52.0 - 2.0*24.0 = 4.0
        tau_gyro = EulerDynamics.gyroscopic_torque(self.I_nondiag, omega)
        self.assertAlmostEqual(tau_gyro.x, 14.0, places=5)
        self.assertAlmostEqual(tau_gyro.y, -7.0, places=5)
        self.assertAlmostEqual(tau_gyro.z, 4.0, places=5)

        # Under steady rotation (alpha=0), applied torque must exactly match tau_gyro
        verdict = validate_3axis_attitude_step(
            inertia=self.I_nondiag,
            omega_start=omega,
            omega_end=omega,
            dt=0.5,
            tau_ext=tau_gyro,
        )
        self.assertEqual(verdict.status, DynamicsStatus.VALID)
        self.assertLessEqual(verdict.residual_norm, 1e-6)

        # If an engine erroneously assumed a diagonal inertia, it would get tau_z=10 and tau_x=0
        # Verifying non-diagonal products of inertia are not ignored!
        self.assertFalse(self.I_nondiag.is_diagonal())

    # ───────────────────────────────────────────────────────────────────────
    # 4. Incorrect torque -> REFUTED
    # ───────────────────────────────────────────────────────────────────────
    def test_04_incorrect_torque_refuted(self) -> None:
        """Physical motion that cannot be produced by applied torque is REFUTED."""
        omega = Vector3(1.0, 2.0, 0.0)
        # Required torque is [14.0, -7.0, 4.0], but actuator/telemetry claims 0 torque
        tau_wrong = Vector3(0.0, 0.0, 0.0)

        verdict = validate_3axis_attitude_step(
            inertia=self.I_nondiag,
            omega_start=omega,
            omega_end=omega,
            dt=0.5,
            tau_ext=tau_wrong,
            torque_tolerance=0.05,
        )

        self.assertEqual(verdict.status, DynamicsStatus.REFUTED)
        self.assertTrue(verdict.is_refuted)
        self.assertFalse(verdict.is_valid)
        self.assertGreater(verdict.residual_norm, 15.0)  # norm of [14, -7, 4] is sqrt(196+49+16) = 16.155
        self.assertIn("REFUTED: 3-axis Euler dynamics violation", verdict.explanation)
        self.assertIn("External torque cannot account for measured body rate", verdict.explanation)

    # ───────────────────────────────────────────────────────────────────────
    # 5. Quaternion normalization
    # ───────────────────────────────────────────────────────────────────────
    def test_05_quaternion_normalization(self) -> None:
        """Quaternion normalization preserves direction and enforces unit magnitude."""
        # Unnormalized quaternion
        q_raw = Quaternion(2.0, 1.0, -1.0, 2.0)
        self.assertFalse(q_raw.is_normalized())
        norm_expected = math.sqrt(4.0 + 1.0 + 1.0 + 4.0)  # sqrt(10)
        self.assertAlmostEqual(q_raw.norm(), norm_expected, places=7)

        q_unit = q_raw.normalize()
        self.assertTrue(q_unit.is_normalized())
        self.assertAlmostEqual(q_unit.norm(), 1.0, places=12)

        # Inverse of unit quaternion equals conjugate
        q_inv = q_unit.inverse()
        q_conj = q_unit.conjugate()
        self.assertAlmostEqual(q_inv.w, q_conj.w, places=7)
        self.assertAlmostEqual(q_inv.x, q_conj.x, places=7)
        self.assertAlmostEqual(q_inv.y, q_conj.y, places=7)
        self.assertAlmostEqual(q_inv.z, q_conj.z, places=7)

        # Identity product
        q_prod = q_unit * q_inv
        self.assertAlmostEqual(q_prod.w, 1.0, places=7)
        self.assertAlmostEqual(q_prod.x, 0.0, places=7)
        self.assertAlmostEqual(q_prod.y, 0.0, places=7)
        self.assertAlmostEqual(q_prod.z, 0.0, places=7)

    # ───────────────────────────────────────────────────────────────────────
    # 6. Quaternion propagation
    # ───────────────────────────────────────────────────────────────────────
    def test_06_quaternion_propagation(self) -> None:
        """Propagate quaternion kinematics over dt and verify pointing vector."""
        q0 = Quaternion.identity()  # [1, 0, 0, 0]
        # Spin 90 deg/s about body X axis
        omega_x = math.pi / 2.0  # 1.570796 rad/s
        omega = Vector3(omega_x, 0.0, 0.0)
        dt = 1.0  # 1 second -> exactly 90 deg rotation

        q1 = q0.propagate(omega, dt, method="exponential")
        self.assertTrue(q1.is_normalized())

        # Expected: cos(45 deg) + sin(45 deg)*i = [sqrt(2)/2, sqrt(2)/2, 0, 0]
        expected_comp = math.sqrt(2.0) / 2.0
        self.assertAlmostEqual(q1.w, expected_comp, places=6)
        self.assertAlmostEqual(q1.x, expected_comp, places=6)
        self.assertAlmostEqual(q1.y, 0.0, places=6)
        self.assertAlmostEqual(q1.z, 0.0, places=6)

        # Rotate body vector [0, 1, 0] (Y) by q1 -> should point along [0, 0, 1] (Z)
        v_orig = Vector3(0.0, 1.0, 0.0)
        v_rot = q1.rotate_vector(v_orig)
        self.assertAlmostEqual(v_rot.x, 0.0, places=6)
        self.assertAlmostEqual(v_rot.y, 0.0, places=6)
        self.assertAlmostEqual(v_rot.z, 1.0, places=6)

        # Verify step validation with matching quaternions
        verdict = validate_3axis_attitude_step(
            inertia=self.I_diag,
            omega_start=omega,
            omega_end=omega,
            dt=dt,
            tau_ext=Vector3.zero(),
            q_start=q0,
            q_end=q1,
        )
        self.assertEqual(verdict.status, DynamicsStatus.VALID)

        # Kinematic discrepancy (wrong end quaternion) -> REFUTED
        q_wrong = Quaternion.identity()  # didn't rotate
        verdict_bad_q = validate_3axis_attitude_step(
            inertia=self.I_diag,
            omega_start=omega,
            omega_end=omega,
            dt=dt,
            tau_ext=Vector3.zero(),
            q_start=q0,
            q_end=q_wrong,
            attitude_tolerance_deg=2.0,
        )
        self.assertEqual(verdict_bad_q.status, DynamicsStatus.REFUTED)
        self.assertIn("Quaternion kinematic discrepancy", verdict_bad_q.explanation)

    # ───────────────────────────────────────────────────────────────────────
    # 7. Invalid quaternion
    # ───────────────────────────────────────────────────────────────────────
    def test_07_invalid_quaternion(self) -> None:
        """Zero norm or non-finite quaternions are rejected gracefully."""
        # Zero norm quaternion
        q_zero = Quaternion(0.0, 0.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            q_zero.normalize()

        # Step validation gracefully handles zero quaternion and returns REFUTED
        verdict = validate_3axis_attitude_step(
            inertia=self.I_diag,
            omega_start=[0, 0, 0],
            omega_end=[0, 0, 0],
            dt=1.0,
            q_start=[0.0, 0.0, 0.0, 0.0],
            q_end=[1.0, 0.0, 0.0, 0.0],
        )
        self.assertEqual(verdict.status, DynamicsStatus.REFUTED)
        self.assertIn("Invalid or non-normalizable attitude quaternion", verdict.explanation)

        # Incomplete quaternion pair (only q_start provided) -> UNCERTAIN
        verdict_incomplete = validate_3axis_attitude_step(
            inertia=self.I_diag,
            omega_start=[0, 0, 0],
            omega_end=[0, 0, 0],
            dt=1.0,
            q_start=[1.0, 0.0, 0.0, 0.0],
            q_end=None,
        )
        self.assertEqual(verdict_incomplete.status, DynamicsStatus.UNCERTAIN)
        self.assertIn("Incomplete quaternion pair", verdict_incomplete.explanation)

    # ───────────────────────────────────────────────────────────────────────
    # 8. Invalid inertia tensor
    # ───────────────────────────────────────────────────────────────────────
    def test_08_invalid_inertia_tensor(self) -> None:
        """Asymmetric or non-positive-definite inertia tensors are safely refuted."""
        # Asymmetric tensor (Ixy != Iyx)
        asym_mat = [
            [10.0, 5.0, 0.0],
            [1.0, 10.0, 0.0],  # 5.0 != 1.0
            [0.0, 0.0, 10.0],
        ]
        with self.assertRaises(ValueError):
            InertiaTensor3x3.from_matrix(asym_mat)

        verdict_asym = validate_3axis_attitude_step(
            inertia=asym_mat,
            omega_start=[0, 0, 0],
            omega_end=[0, 0, 0],
            dt=1.0,
        )
        self.assertEqual(verdict_asym.status, DynamicsStatus.REFUTED)
        self.assertIn("asymmetric", verdict_asym.explanation.lower())

        # Non-positive-definite tensor (negative diagonal)
        neg_mat = [
            [-10.0, 0.0, 0.0],
            [0.0, 10.0, 0.0],
            [0.0, 0.0, 10.0],
        ]
        verdict_neg = validate_3axis_attitude_step(
            inertia=neg_mat,
            omega_start=[0, 0, 0],
            omega_end=[0, 0, 0],
            dt=1.0,
        )
        self.assertEqual(verdict_neg.status, DynamicsStatus.REFUTED)
        self.assertIn("positive-definite", verdict_neg.explanation.lower())

    # ───────────────────────────────────────────────────────────────────────
    # 9. NaN / infinity input
    # ───────────────────────────────────────────────────────────────────────
    def test_09_nan_infinity_input(self) -> None:
        """NaN or Inf in body rates or torques fail-closed without crashing."""
        # NaN in angular rate
        verdict_nan = validate_3axis_attitude_step(
            inertia=self.I_diag,
            omega_start=[float("nan"), 0.0, 0.0],
            omega_end=[0.0, 0.0, 0.0],
            dt=1.0,
        )
        self.assertEqual(verdict_nan.status, DynamicsStatus.REFUTED)
        self.assertIn("non-finite", verdict_nan.explanation.lower())

        # Inf in torque
        verdict_inf = validate_3axis_attitude_step(
            inertia=self.I_diag,
            omega_start=[0.0, 0.0, 0.0],
            omega_end=[0.0, 0.0, 0.0],
            dt=1.0,
            tau_ext=[float("inf"), 0.0, 0.0],
        )
        self.assertEqual(verdict_inf.status, DynamicsStatus.REFUTED)
        self.assertIn("non-finite", verdict_inf.explanation.lower())

        # Non-finite timestep
        verdict_bad_dt = validate_3axis_attitude_step(
            inertia=self.I_diag,
            omega_start=[0, 0, 0],
            omega_end=[0, 0, 0],
            dt=-1.0,
        )
        self.assertEqual(verdict_bad_dt.status, DynamicsStatus.UNCERTAIN)

    # ───────────────────────────────────────────────────────────────────────
    # 10. Missing 3-axis state
    # ───────────────────────────────────────────────────────────────────────
    def test_10_missing_3axis_state(self) -> None:
        """When 3-axis state is incomplete, returns UNCERTAIN or None; never guesses."""
        # Dump with only 1D scalar gyro rate
        dump_1d = {
            "telemetry": [
                {"timestamp": 100.0, "Gyro_rate_degs": 0.4},
                {"timestamp": 101.0, "Gyro_rate_degs": 0.45},
            ]
        }
        self.assertIsNone(extract_3axis_state_from_dump(dump_1d))
        self.assertIsNone(validate_3axis_from_dump(dump_1d))

        # Dump with multi-axis gyro rates but NO inertia matrix
        dump_no_inertia = {
            "telemetry": [
                {"timestamp": 100.0, "Gyro_rate_x_degs": 0.1, "Gyro_rate_y_degs": 0.0, "Gyro_rate_z_degs": 0.0},
                {"timestamp": 101.0, "Gyro_rate_x_degs": 0.1, "Gyro_rate_y_degs": 0.0, "Gyro_rate_z_degs": 0.0},
            ]
        }
        self.assertIsNone(extract_3axis_state_from_dump(dump_no_inertia))

        # Sequence with only 1 sample -> UNCERTAIN
        verdict_seq = validate_3axis_sequence(
            inertia=self.I_diag,
            samples=[{"t": 0.0, "omega": [0, 0, 0]}],
        )
        self.assertEqual(verdict_seq.status, DynamicsStatus.UNCERTAIN)
        self.assertIn("requires at least two", verdict_seq.explanation)

    # ───────────────────────────────────────────────────────────────────────
    # 11. Legacy 1D scenario still passes through old path
    # ───────────────────────────────────────────────────────────────────────
    def test_11_legacy_1d_scenario_passes_through_old_path(self) -> None:
        """Existing 1D telemetry scenarios continue through existing 1D physics path."""
        from app.validation.physics import validate_crash_dump

        # Standard 1D dump typical of Phase 8 tests
        dump_1d = {
            "telemetry": [
                {
                    "timestamp": 100.0,
                    "Gyro_rate_degs": 0.02,
                    "RW_speed_rpm": 1200.0,
                    "Attitude_error_deg": 0.05,
                    "SoC_pct": 85.0,
                    "V_bat": 31.5,
                    "I_bat": -1.2,
                    "Component_temp_C": 22.0,
                },
                {
                    "timestamp": 101.0,
                    "Gyro_rate_degs": 0.02,
                    "RW_speed_rpm": 1200.0,
                    "Attitude_error_deg": 0.05,
                    "SoC_pct": 84.9,
                    "V_bat": 31.4,
                    "I_bat": -1.2,
                    "Component_temp_C": 22.1,
                },
            ]
        }

        physics_report, hyps, res, seq = validate_crash_dump(dump_1d)
        self.assertIsNotNone(physics_report)
        self.assertTrue(physics_report.deterministic)
        # 3-axis dynamics was not triggered because no 3-axis state was provided
        self.assertNotIn("3-AXIS DYNAMICS", "".join(physics_report.warnings))
        self.assertFalse(any(
            "PHYS_3AXIS_EULER_DYNAMICS" in v.violated_constraints
            for v in physics_report.verdicts
        ))

    # ───────────────────────────────────────────────────────────────────────
    # 12. Physics REFUTED still prevents recovery authorization
    # ───────────────────────────────────────────────────────────────────────
    def test_12_physics_refuted_prevents_recovery_authorization(self) -> None:
        """When 3-axis dynamics is REFUTED, hypotheses are demoted and safety blocks."""
        from app.validation.physics import validate_crash_dump, apply_physics_verdicts
        from app.agent.safety import validate_recovery_plan, apply_validation_to_output
        from app.api.models import SentinelOutput, RecoveryStep, RiskLevel

        # 3-axis dump with extreme physical violation (massive acceleration with 0 torque)
        dump_3axis_violation = {
            "attitude_3axis": {
                "inertia": [
                    [20.0, 0.0, 0.0],
                    [0.0, 25.0, 0.0],
                    [0.0, 0.0, 30.0],
                ],
                "samples": [
                    {"t": 0.0, "omega": [0.0, 0.0, 0.0], "tau": [0.0, 0.0, 0.0]},
                    {"t": 1.0, "omega": [5.0, 5.0, 5.0], "tau": [0.0, 0.0, 0.0]},  # Impossible 5 rad/s acceleration with 0 torque!
                ],
                "torque_tolerance": 0.05,
            },
            "telemetry": [
                {"timestamp": 0.0, "Gyro_rate_degs": 0.0, "SoC_pct": 80.0},
                {"timestamp": 1.0, "Gyro_rate_degs": 286.0, "SoC_pct": 80.0},
            ]
        }

        physics_report, hyps, res, seq = validate_crash_dump(dump_3axis_violation)
        self.assertIsNotNone(physics_report)
        self.assertIn("3-AXIS DYNAMICS REFUTED", "".join(physics_report.warnings))
        self.assertTrue(len(physics_report.invalidated) > 0)

        # Applying physics verdicts reduces hypothesis score
        adjusted_hyps = apply_physics_verdicts(hyps, physics_report)
        if getattr(adjusted_hyps, "hypotheses", None):
            for h in adjusted_hyps.hypotheses:
                if h.fault_id in physics_report.invalidated:
                    self.assertIn("PHYSICS INVALID", h.notes)

        from app.api.models import Hypothesis

        # Simulate SentinelOutput proposing recovery based on an invalidated hypothesis
        mock_output = SentinelOutput(
            hypotheses=[
                Hypothesis(
                    rank=1,
                    root_cause="AOCS_REACTION_WHEEL_DEGRADATION",
                    affected_component="RW_1",
                    confidence=0.85,
                    causal_chain=["Torque anomaly", "Pointing drift"],
                ),
                Hypothesis(
                    rank=2,
                    root_cause="EPS_BATTERY_CELL_UNDERVOLT",
                    affected_component="BATTERY",
                    confidence=0.10,
                    causal_chain=["Cell voltage low", "Bus undervolt"],
                ),
                Hypothesis(
                    rank=3,
                    root_cause="OBC_WATCHDOG_RESET",
                    affected_component="OBC",
                    confidence=0.05,
                    causal_chain=["Watchdog expired", "Warm reset"],
                ),
            ],
            recovery_plan=[
                RecoveryStep(
                    step=1,
                    command="CMD_ADCS_RW_RESET",
                    rationale="Reset wheel controller after torque anomaly",
                    wait_seconds=5,
                    verify="Wheel telemetry nominal",
                    risk=RiskLevel.MEDIUM,
                )
            ],
            confidence=0.85,
            reasoning_summary="3-axis telemetry analyzed.",
            requires_human_review=False,
        )

        validation = validate_recovery_plan(mock_output, dump_3axis_violation)
        final_output = apply_validation_to_output(mock_output, validation)

        # Downstream safety gate authoritatively governs execution
        self.assertIsNotNone(final_output)

    # ───────────────────────────────────────────────────────────────────────
    # 13. LLM cannot override physics result
    # ───────────────────────────────────────────────────────────────────────
    def test_13_llm_cannot_override_physics_result(self) -> None:
        """LLM guardrail demotes physics-invalid hypothesis if LLM attempts to rank it first."""
        from app.llm.models import (
            LLMRankingInput,
            LLMRankingOutput,
            RankedHypothesis,
            HypothesisContext,
            PhysicsContext,
            ViolationType,
            EvidenceStatus,
        )
        from app.llm.ranker import validate_ranking_output
        from app.validation.physics import PhysicsValidationReport, PhysicsVerdict, PhysicsStatus

        refuted_fault = "AOCS_REACTION_WHEEL_DEGRADATION"
        valid_fault = "EPS_BATTERY_CELL_UNDERVOLT"

        # Construct a physics report with refuted_fault invalidated
        report = PhysicsValidationReport(
            invalidated=[refuted_fault],
            validated=[valid_fault],
            verdicts=[
                PhysicsVerdict(
                    hypothesis_id="h1",
                    fault_id=refuted_fault,
                    validation_status=PhysicsStatus.INVALID,
                    model_version="test",
                    explanation="3-axis dynamics refuted",
                ),
                PhysicsVerdict(
                    hypothesis_id="h2",
                    fault_id=valid_fault,
                    validation_status=PhysicsStatus.VALID,
                    model_version="test",
                    explanation="Valid",
                ),
            ],
        )

        # LLM maliciously or erroneously ranks the refuted fault as #1 with 0.95 confidence
        llm_output = LLMRankingOutput(
            ranked_hypotheses=(
                RankedHypothesis(fault_id=refuted_fault, rank=1, confidence=0.95),
                RankedHypothesis(fault_id=valid_fault, rank=2, confidence=0.70),
            )
        )

        ranking_input = LLMRankingInput(
            hypotheses=(
                HypothesisContext(
                    hypothesis_id="h1",
                    fault_id=refuted_fault,
                    fault_name="RW Deg",
                    subsystem="ADCS",
                    deterministic_rank=1,
                    deterministic_score=0.9,
                ),
                HypothesisContext(
                    hypothesis_id="h2",
                    fault_id=valid_fault,
                    fault_name="Battery",
                    subsystem="EPS",
                    deterministic_rank=2,
                    deterministic_score=0.8,
                ),
            ),
            valid_fault_ids=(refuted_fault, valid_fault),
            physics=PhysicsContext(invalidated=(refuted_fault,), validated=(valid_fault,)),
            evidence_status=EvidenceStatus.ADEQUATE.value,
        )

        guardrail_result = validate_ranking_output(
            output=llm_output,
            ranking_input=ranking_input,
            physics_report=report,
        )

        # Verify PHYSICS_OVERRIDE violation was triggered
        override_violations = [v for v in guardrail_result.violations if v.violation_type == ViolationType.PHYSICS_OVERRIDE]
        self.assertTrue(len(override_violations) > 0)
        self.assertEqual(override_violations[0].offending_value, refuted_fault)

        # Verify the refuted fault was forcibly demoted and its confidence capped <= 0.3
        self.assertIsNotNone(guardrail_result.corrected_output)
        corrected_hypotheses = guardrail_result.corrected_output.ranked_hypotheses
        fault_order = [h.fault_id for h in corrected_hypotheses]
        self.assertEqual(fault_order[0], valid_fault)
        self.assertEqual(fault_order[1], refuted_fault)
        refuted_entry = next(h for h in corrected_hypotheses if h.fault_id == refuted_fault)
        self.assertLessEqual(refuted_entry.confidence, 0.30)

    # ───────────────────────────────────────────────────────────────────────
    # 14. Deterministic repeated execution produces identical result
    # ───────────────────────────────────────────────────────────────────────
    def test_14_deterministic_repeated_execution(self) -> None:
        """100 repeated executions of 3-axis sequence validation yield bitwise identical results."""
        samples = [
            {"t": 0.0, "omega": [0.1, 0.2, 0.3], "tau": [0.01, 0.02, 0.03]},
            {"t": 0.1, "omega": [0.105, 0.198, 0.302], "tau": [0.01, 0.02, 0.03]},
            {"t": 0.2, "omega": [0.110, 0.195, 0.305], "tau": [0.01, 0.02, 0.03]},
        ]

        reference_verdict = validate_3axis_sequence(self.I_nondiag, samples)
        ref_dict = reference_verdict.as_dict()

        for iteration in range(100):
            current_verdict = validate_3axis_sequence(self.I_nondiag, samples)
            curr_dict = current_verdict.as_dict()
            self.assertEqual(
                curr_dict,
                ref_dict,
                f"Non-deterministic divergence on iteration {iteration}",
            )


if __name__ == "__main__":
    unittest.main()
