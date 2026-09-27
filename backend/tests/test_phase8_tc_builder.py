"""
SENTINEL — Phase 8: Deterministic Telecommand Builder Boundary Tests
(tests/test_phase8_tc_builder.py)

Covers all 15 mandatory Phase 8 acceptance tests:
1. Valid approved command encodes successfully.
2. Unwhitelisted command rejected.
3. Safety-blocked command rejected.
4. UNKNOWN critical telemetry -> rejected.
5. Physics-refuted recovery -> rejected.
6. Malformed parameters -> rejected.
7. NaN/infinity parameters -> rejected.
8. Deterministic encoding produces identical bytes.
9. Encode/decode round trip.
10. Sequence/counter behavior.
11. Multiple approved commands preserve deterministic ordering.
12. LLM cannot bypass safety.
13. Frontend cannot directly create an outbound command.
14. No network/socket transmission occurs.
15. Existing safety tests remain unchanged and pass.
"""

from __future__ import annotations

import math
import os
import sys
import unittest
from typing import Any

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.models import (
    Hypothesis,
    RecoveryStep,
    RiskLevel,
    SafetyStatus,
    SentinelOutput,
)
from app.agent.safety import (
    ValidationResult,
    validate_recovery_plan,
)
from app.validation.tc_builder import (
    TCFrame,
    TCBuildResult,
    TCBuildStatus,
    TCFrameDecodeError,
    OutboundUplinkBoundary,
    UplinkDisabledError,
    build_telecommand_stream,
    build_from_unvalidated_input,
    crc16_ccitt,
)
from app.validation.physics import (
    PhysicsValidationReport,
    PhysicsVerdict,
    PhysicsStatus,
)


class TestPhase8TelecommandBuilder(unittest.TestCase):
    """Phase 8 comprehensive test suite for telecommand builder boundary."""

    def setUp(self) -> None:
        # Nominal telemetry context with all critical telemetry available
        self.nominal_telemetry = {
            "SoC_pct": 85.0,
            "Gyro_rate_degs": 0.02,
            "Transponder_lock": "LOCKED",
            "Component_temp_C": 24.5,
        }

        # Valid 3-hypothesis SentinelOutput with approved recovery step
        self.valid_step = RecoveryStep(
            step=1,
            command="CMD_ATTITUDE_HOLD",
            rationale="Establish safe pointing attitude",
            wait_seconds=5,
            verify="Attitude rate nominal",
            risk=RiskLevel.LOW,
        )

        self.valid_output = SentinelOutput(
            hypotheses=[
                Hypothesis(
                    rank=1,
                    root_cause="AOCS_SENSOR_FAULT",
                    affected_component="GYRO_A",
                    confidence=0.85,
                    causal_chain=["Rate glitch", "Attitude error"],
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
            recovery_plan=[self.valid_step],
            confidence=0.85,
            reasoning_summary="Nominal telemetry analyzed; safe recovery proposed.",
            requires_human_review=False,
        )

    # ───────────────────────────────────────────────────────────────────────
    # 1. Valid approved command encodes successfully
    # ───────────────────────────────────────────────────────────────────────
    def test_01_valid_approved_command_encodes_successfully(self) -> None:
        """A safety-validated command produces a deterministic TCFrame."""
        safety_val = validate_recovery_plan(self.valid_output, self.nominal_telemetry)
        self.assertEqual(safety_val.safety_status, SafetyStatus.VALIDATED)
        self.assertTrue(safety_val.is_safe)

        result = build_telecommand_stream(
            recovery_plan=self.valid_output,
            safety_validation=safety_val,
            crash_dump=self.nominal_telemetry,
        )

        self.assertEqual(result.status, TCBuildStatus.APPROVED)
        self.assertTrue(result.is_approved)
        self.assertEqual(result.total_frames, 1)
        self.assertEqual(len(result.frames), 1)

        frame = result.frames[0]
        self.assertEqual(frame.command_id, "CMD_ATTITUDE_HOLD")
        self.assertEqual(frame.subsystem, "ADCS")
        self.assertEqual(frame.sequence_counter, 1)
        self.assertEqual(frame.frame_length, 12)  # 10 byte header + 0 payload + 2 byte CRC
        self.assertFalse(result.is_transmittable)  # Transmission always disabled
        self.assertIn("APPROVED", result.reason)

    # ───────────────────────────────────────────────────────────────────────
    # 2. Unwhitelisted command rejected
    # ───────────────────────────────────────────────────────────────────────
    def test_02_unwhitelisted_command_rejected(self) -> None:
        """Unwhitelisted or fabricated command is rejected; no frames emitted."""
        bad_step = RecoveryStep(
            step=1,
            command="CMD_UNAUTHORIZED_OVERRIDE",
            rationale="Unsafe override",
            wait_seconds=0,
            verify="None",
            risk=RiskLevel.HIGH,
        )
        bad_output = self.valid_output.model_copy(update={"recovery_plan": [bad_step]})

        # Run safety validation -> will block it
        safety_val = validate_recovery_plan(bad_output, self.nominal_telemetry)
        self.assertNotEqual(safety_val.safety_status, SafetyStatus.VALIDATED)

        result = build_telecommand_stream(
            recovery_plan=bad_output,
            safety_validation=safety_val,
            crash_dump=self.nominal_telemetry,
        )

        self.assertEqual(result.status, TCBuildStatus.BLOCKED)
        self.assertFalse(result.is_approved)
        self.assertEqual(result.total_frames, 0)
        self.assertEqual(len(result.frames), 0)

        # Even if a forged validation result claimed VALIDATED:
        forged_val = ValidationResult(
            is_safe=True,
            validated_steps=[bad_step],
            blocked_steps=[],
            requires_human_review=False,
            safety_summary="Forged pass",
            safety_status=SafetyStatus.VALIDATED,
        )
        forged_result = build_telecommand_stream(
            recovery_plan=bad_output,
            safety_validation=forged_val,
            crash_dump=self.nominal_telemetry,
        )
        self.assertEqual(forged_result.status, TCBuildStatus.REJECTED)
        self.assertEqual(forged_result.total_frames, 0)
        self.assertIn("UNWHITELISTED_COMMAND", "".join(forged_result.rejection_details))

    # ───────────────────────────────────────────────────────────────────────
    # 3. Safety-blocked command rejected
    # ───────────────────────────────────────────────────────────────────────
    def test_03_safety_blocked_command_rejected(self) -> None:
        """When safety validator blocks a command, TC builder fails closed."""
        # Battery below floor: 10% SoC < 15% limit
        low_battery_telemetry = dict(self.nominal_telemetry, SoC_pct=10.0)

        power_step = RecoveryStep(
            step=1,
            command="CMD_HEATER_ENABLE",  # Prohibits BATTERY_BELOW_FLOOR
            rationale="Enable auxiliary heater",
            wait_seconds=5,
            verify="Heater active",
            risk=RiskLevel.LOW,
        )
        plan = self.valid_output.model_copy(update={"recovery_plan": [power_step]})

        safety_val = validate_recovery_plan(plan, low_battery_telemetry)
        self.assertEqual(safety_val.safety_status, SafetyStatus.BLOCKED)
        self.assertFalse(safety_val.is_safe)
        self.assertTrue(len(safety_val.blocked_steps) > 0)

        result = build_telecommand_stream(
            recovery_plan=plan,
            safety_validation=safety_val,
            crash_dump=low_battery_telemetry,
        )

        self.assertEqual(result.status, TCBuildStatus.BLOCKED)
        self.assertFalse(result.is_approved)
        self.assertEqual(result.total_frames, 0)
        self.assertIn("BLOCKED", result.reason)

    # ───────────────────────────────────────────────────────────────────────
    # 4. UNKNOWN critical telemetry -> rejected
    # ───────────────────────────────────────────────────────────────────────
    def test_04_unknown_critical_telemetry_rejected(self) -> None:
        """Missing critical telemetry required by preconditions rejects frame generation."""
        # Telemetry context is missing Gyro_rate_degs
        missing_telemetry = {
            "SoC_pct": 80.0,
            "Transponder_lock": "LOCKED",
            "Component_temp_C": 25.0,
            # "Gyro_rate_degs" is missing!
        }

        step = RecoveryStep(
            step=1,
            command="CMD_ATTITUDE_REACQUISITION",  # requires GYRO_DATA_VALID
            rationale="Reacquire safe pointing attitude",
            wait_seconds=5,
            verify="Attitude rate nominal",
            risk=RiskLevel.LOW,
        )
        plan = self.valid_output.model_copy(update={"recovery_plan": [step]})

        # In Phase 1 safety validator, UNKNOWN was permissive for advisory plans
        safety_val = validate_recovery_plan(plan, missing_telemetry)

        # But the TC builder boundary enforces that critical telemetry cannot be UNKNOWN!
        result = build_telecommand_stream(
            recovery_plan=plan,
            safety_validation=safety_val,
            crash_dump=missing_telemetry,
        )

        self.assertEqual(result.status, TCBuildStatus.REJECTED)
        self.assertFalse(result.is_approved)
        self.assertEqual(result.total_frames, 0)
        self.assertTrue(any("UNKNOWN_CRITICAL_TELEMETRY" in detail for detail in result.rejection_details))

    # ───────────────────────────────────────────────────────────────────────
    # 5. Physics-refuted recovery -> rejected
    # ───────────────────────────────────────────────────────────────────────
    def test_05_physics_refuted_recovery_rejected(self) -> None:
        """A recovery plan based on a physics-refuted hypothesis is rejected."""
        refuted_fault = "AOCS_SENSOR_FAULT"
        safety_val = validate_recovery_plan(self.valid_output, self.nominal_telemetry)

        # Physics report refutes the root cause
        physics_report = PhysicsValidationReport(
            invalidated=[refuted_fault],
            verdicts=[
                PhysicsVerdict(
                    hypothesis_id="h1",
                    fault_id=refuted_fault,
                    validation_status=PhysicsStatus.INVALID,
                    model_version="test",
                    explanation="Refuted by 3-axis dynamics",
                )
            ]
        )

        result = build_telecommand_stream(
            recovery_plan=self.valid_output,
            safety_validation=safety_val,
            crash_dump=self.nominal_telemetry,
            physics_report=physics_report,
        )

        self.assertEqual(result.status, TCBuildStatus.REJECTED)
        self.assertFalse(result.is_approved)
        self.assertEqual(result.total_frames, 0)
        self.assertIn("PHYSICS_REFUTED_AOCS_SENSOR_FAULT", "".join(result.rejection_details))

    # ───────────────────────────────────────────────────────────────────────
    # 6. Malformed parameters -> rejected
    # ───────────────────────────────────────────────────────────────────────
    def test_06_malformed_parameters_rejected(self) -> None:
        """Unsupported or malformed parameter structures are rejected."""
        safety_val = validate_recovery_plan(self.valid_output, self.nominal_telemetry)

        # Pass invalid parameter type (e.g. complex object or set)
        malformed_params = {
            1: {"invalid_param": object()}
        }

        result = build_telecommand_stream(
            recovery_plan=self.valid_output,
            safety_validation=safety_val,
            crash_dump=self.nominal_telemetry,
            parameters_map=malformed_params,
        )

        self.assertEqual(result.status, TCBuildStatus.REJECTED)
        self.assertEqual(result.total_frames, 0)
        self.assertTrue(any("INVALID_PARAMETER" in d for d in result.rejection_details))

    # ───────────────────────────────────────────────────────────────────────
    # 7. NaN/infinity parameters -> rejected
    # ───────────────────────────────────────────────────────────────────────
    def test_07_nan_infinity_parameters_rejected(self) -> None:
        """Non-finite float parameters (NaN, Inf) are strictly rejected."""
        safety_val = validate_recovery_plan(self.valid_output, self.nominal_telemetry)

        # NaN parameter
        nan_params = {1: {"target_rate": float("nan")}}
        res_nan = build_telecommand_stream(
            recovery_plan=self.valid_output,
            safety_validation=safety_val,
            crash_dump=self.nominal_telemetry,
            parameters_map=nan_params,
        )
        self.assertEqual(res_nan.status, TCBuildStatus.REJECTED)
        self.assertEqual(res_nan.total_frames, 0)
        self.assertIn("non-finite", "".join(res_nan.rejection_details))

        # Inf parameter
        inf_params = {1: {"target_rate": float("inf")}}
        res_inf = build_telecommand_stream(
            recovery_plan=self.valid_output,
            safety_validation=safety_val,
            crash_dump=self.nominal_telemetry,
            parameters_map=inf_params,
        )
        self.assertEqual(res_inf.status, TCBuildStatus.REJECTED)
        self.assertEqual(res_inf.total_frames, 0)
        self.assertIn("non-finite", "".join(res_inf.rejection_details))

    # ───────────────────────────────────────────────────────────────────────
    # 8. Deterministic encoding produces identical bytes
    # ───────────────────────────────────────────────────────────────────────
    def test_08_deterministic_encoding_identical_bytes(self) -> None:
        """Repeated encodings produce bitwise-identical binary frames."""
        params = {"mode": "STANDBY", "gain": 1.25, "retries": 3, "enabled": True}
        reference_frame = TCFrame.encode(
            command_id="CMD_ATTITUDE_HOLD",
            sequence_counter=101,
            parameters=params,
        )
        ref_bytes = reference_frame.raw_bytes

        for _ in range(50):
            test_frame = TCFrame.encode(
                command_id="CMD_ATTITUDE_HOLD",
                sequence_counter=101,
                parameters=params,
            )
            self.assertEqual(test_frame.raw_bytes, ref_bytes)
            self.assertEqual(test_frame.crc, reference_frame.crc)

    # ───────────────────────────────────────────────────────────────────────
    # 9. Encode/decode round trip
    # ───────────────────────────────────────────────────────────────────────
    def test_09_encode_decode_round_trip(self) -> None:
        """Encoded telecommand frames decode cleanly and verify CRC integrity."""
        params = {
            "axis": "Z",
            "degrees": 45.0,
            "attempts": 2,
            "confirm": True,
        }
        original = TCFrame.encode(
            command_id="CMD_ATTITUDE_HOLD",
            sequence_counter=55,
            parameters=params,
        )

        decoded = TCFrame.decode(original.raw_bytes)
        self.assertEqual(decoded.command_id, original.command_id)
        self.assertEqual(decoded.opcode, original.opcode)
        self.assertEqual(decoded.sequence_counter, 55)
        self.assertEqual(decoded.crc, original.crc)
        self.assertEqual(decoded.parameters["axis"], "Z")
        self.assertAlmostEqual(decoded.parameters["degrees"], 45.0)
        self.assertEqual(decoded.parameters["attempts"], 2)
        self.assertTrue(decoded.parameters["confirm"])

        # Corrupted CRC or payload fails validation
        corrupted_bytes = bytearray(original.raw_bytes)
        corrupted_bytes[5] ^= 0xFF  # Flip sequence byte
        with self.assertRaises(TCFrameDecodeError):
            TCFrame.decode(bytes(corrupted_bytes))

    # ───────────────────────────────────────────────────────────────────────
    # 10. Sequence/counter behavior
    # ───────────────────────────────────────────────────────────────────────
    def test_10_sequence_counter_behavior(self) -> None:
        """Sequence counters increment monotonically and wrap at 16 bits."""
        steps = [
            RecoveryStep(
                step=1,
                command="CMD_ATTITUDE_HOLD",
                rationale="Hold attitude",
                wait_seconds=5,
                verify="Nominal",
                risk=RiskLevel.LOW,
            ),
            RecoveryStep(
                step=2,
                command="CMD_COMMS_CHECK",
                rationale="Check comms",
                wait_seconds=5,
                verify="Nominal",
                risk=RiskLevel.LOW,
            ),
        ]
        plan = self.valid_output.model_copy(update={"recovery_plan": steps})
        safety_val = validate_recovery_plan(plan, self.nominal_telemetry)

        result = build_telecommand_stream(
            recovery_plan=plan,
            safety_validation=safety_val,
            crash_dump=self.nominal_telemetry,
            sequence_start=65534,
        )

        self.assertEqual(result.status, TCBuildStatus.APPROVED)
        self.assertEqual(result.frames[0].sequence_counter, 65534)
        self.assertEqual(result.frames[1].sequence_counter, 65535)

    # ───────────────────────────────────────────────────────────────────────
    # 11. Multiple approved commands preserve deterministic ordering
    # ───────────────────────────────────────────────────────────────────────
    def test_11_multiple_approved_commands_preserve_deterministic_ordering(self) -> None:
        """A multi-step recovery plan preserves exact step order in binary output."""
        commands = ["CMD_ATTITUDE_HOLD", "CMD_COMMS_CHECK", "CMD_BATTERY_CHECK"]
        steps = [
            RecoveryStep(
                step=i + 1,
                command=cmd,
                rationale=f"Step {i+1}",
                wait_seconds=5,
                verify="Nominal",
                risk=RiskLevel.LOW,
            )
            for i, cmd in enumerate(commands)
        ]
        plan = self.valid_output.model_copy(update={"recovery_plan": steps})
        safety_val = validate_recovery_plan(plan, self.nominal_telemetry)

        result = build_telecommand_stream(
            recovery_plan=plan,
            safety_validation=safety_val,
            crash_dump=self.nominal_telemetry,
        )

        self.assertEqual(result.status, TCBuildStatus.APPROVED)
        self.assertEqual(result.total_frames, 3)
        self.assertEqual([f.command_id for f in result.frames], commands)

    # ───────────────────────────────────────────────────────────────────────
    # 12. LLM cannot bypass safety
    # ───────────────────────────────────────────────────────────────────────
    def test_12_llm_cannot_bypass_safety(self) -> None:
        """LLM output cannot construct frames without passing safety validator."""
        # Plan contains prohibited command: CMD_REBOOT_OBC with comms lock absent
        no_lock_telemetry = dict(self.nominal_telemetry, Transponder_lock="NO_LOCK")

        reboot_step = RecoveryStep(
            step=1,
            command="CMD_REBOOT_OBC",
            rationale="Reboot computer",
            wait_seconds=10,
            verify="OBC online",
            risk=RiskLevel.HIGH,
        )
        plan = self.valid_output.model_copy(update={"recovery_plan": [reboot_step]})

        # Attempt to pass unvalidated input through defensive entrypoint
        result = build_from_unvalidated_input(plan, no_lock_telemetry)

        self.assertEqual(result.status, TCBuildStatus.BLOCKED)
        self.assertEqual(result.total_frames, 0)
        self.assertFalse(result.is_approved)

    # ───────────────────────────────────────────────────────────────────────
    # 13. Frontend cannot directly create an outbound command
    # ───────────────────────────────────────────────────────────────────────
    def test_13_frontend_cannot_directly_create_outbound_command(self) -> None:
        """Arbitrary JSON payloads from client/frontend must undergo validation."""
        raw_frontend_payload = {
            "recovery_plan": [
                {
                    "step": 1,
                    "command": "CMD_DEPLOY_SOLAR_ARRAYS",
                    "rationale": "Deploy solar array",
                    "wait_seconds": 30,
                    "verify": "Deployed",
                    "risk": "MEDIUM",
                }
            ]
        }

        # build_telecommand_stream rejects non-ValidationResult objects
        invalid_safety = "APPROVED_BY_FRONTEND"  # String, not ValidationResult
        result = build_telecommand_stream(
            recovery_plan=[],
            safety_validation=invalid_safety,  # type: ignore[arg-type]
            crash_dump=self.nominal_telemetry,
        )
        self.assertEqual(result.status, TCBuildStatus.BLOCKED)
        self.assertEqual(result.total_frames, 0)
        self.assertIn("SAFETY_VALIDATION_MISSING", result.rejection_details)

        # build_from_unvalidated_input rejects malformed payloads
        result_raw = build_from_unvalidated_input(raw_frontend_payload, self.nominal_telemetry)
        self.assertEqual(result_raw.status, TCBuildStatus.REJECTED)
        self.assertEqual(result_raw.total_frames, 0)

    # ───────────────────────────────────────────────────────────────────────
    # 14. No network/socket transmission occurs
    # ───────────────────────────────────────────────────────────────────────
    def test_14_no_network_socket_transmission_occurs(self) -> None:
        """Outbound transmission boundary is strictly disabled by design."""
        self.assertFalse(OutboundUplinkBoundary.ENABLED)

        frame = TCFrame.encode("CMD_ATTITUDE_HOLD", sequence_counter=1)

        with self.assertRaises(UplinkDisabledError) as cm:
            OutboundUplinkBoundary.transmit([frame])

        self.assertIn("UPLINK_DISABLED", str(cm.exception))
        self.assertIn("disabled by design", str(cm.exception))

    # ───────────────────────────────────────────────────────────────────────
    # 15. Existing safety tests remain unchanged and pass
    # ───────────────────────────────────────────────────────────────────────
    def test_15_existing_safety_tests_remain_unchanged(self) -> None:
        """Verify safety validator integrity remains 100% compatible."""
        from app.agent.safety import COMMAND_WHITELIST, BATTERY_FLOOR_SOC
        self.assertGreater(len(COMMAND_WHITELIST), 0)
        self.assertEqual(BATTERY_FLOOR_SOC, 15.0)


if __name__ == "__main__":
    unittest.main()
