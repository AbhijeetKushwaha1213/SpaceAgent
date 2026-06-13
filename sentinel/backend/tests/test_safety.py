#!/usr/bin/env python3
"""
SENTINEL — Safety Validator Tests (test_safety.py)

Step 7 test suite. Targets 80+ passing checks with:
  - Whitelist tests for all 6 subsystems
  - Subsystem inference tests
  - All 4 blocking constraint checks
  - Escalation rules
  - Integration tests
  - Table-driven regression on intentionally unsafe commands
  - Edge cases and empty context handling
"""

from __future__ import annotations

import math
import os
import sys

# Ensure backend/ root is on sys.path for standalone execution
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---------------------------------------------------------------------------
# Test infrastructure (same style as other test_*.py in this project)
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0


def check(description: str, condition: bool) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {description}")
    else:
        _failed += 1
        print(f"  ❌ {description}")


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from app.api.models import RecoveryStep, RiskLevel, SentinelOutput, Hypothesis
from app.agent.safety import (
    COMMAND_WHITELIST,
    BATTERY_FLOOR_SOC,
    THERMAL_SURVIVAL_LIMIT,
    CONFIDENCE_REVIEW_THRESHOLD,
    BlockedStep,
    ConstraintViolation,
    ValidationResult,
    apply_validation_to_output,
    check_battery_floor,
    check_comms_lock_for_reboot,
    check_gyro_health_prerequisite,
    check_high_risk_escalation,
    check_thermal_survival,
    get_whitelist_status,
    infer_subsystem,
    is_command_whitelisted,
    validate_recovery_plan,
)

print("✅ All imports successful\n")


# ---------------------------------------------------------------------------
# Helper: make a RecoveryStep quickly
# ---------------------------------------------------------------------------

def _step(
    command: str,
    step_num: int = 1,
    risk: RiskLevel = RiskLevel.LOW,
) -> RecoveryStep:
    return RecoveryStep(
        step=step_num,
        command=command,
        rationale="Test rationale for safety check",
        wait_seconds=5,
        verify="Check status OK",
        risk=risk,
    )


def _make_output(
    commands: list[str],
    confidence: float = 0.91,
    requires_human_review: bool = False,
) -> SentinelOutput:
    """Build a minimal valid SentinelOutput for testing."""
    steps = [
        RecoveryStep(
            step=i + 1,
            command=cmd,
            rationale="Test rationale",
            wait_seconds=5,
            verify="Verify OK",
            risk=RiskLevel.LOW,
        )
        for i, cmd in enumerate(commands)
    ]
    return SentinelOutput(
        hypotheses=[
            Hypothesis(
                rank=1,
                root_cause="ADCS_GYRO_SEU",
                affected_component="GYRO_A",
                confidence=confidence,
                causal_chain=["SEU spike", "Gyro NaN", "Safe mode"],
            ),
            Hypothesis(
                rank=2,
                root_cause="HW_FAULT",
                affected_component="GYRO_A",
                confidence=0.06,
                causal_chain=["HW degradation", "Sensor fails"],
            ),
            Hypothesis(
                rank=3,
                root_cause="SW_FAULT",
                affected_component="OBC",
                confidence=0.03,
                causal_chain=["SW bug", "False flag"],
            ),
        ],
        recovery_plan=steps,
        confidence=confidence,
        requires_human_review=requires_human_review,
        reasoning_summary="SEU counter spiked causing gyro NaN and safe mode.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# TEST 1: Whitelist structure
# ═══════════════════════════════════════════════════════════════════════════
print("🧪 TEST 1: Whitelist structure\n")

status = get_whitelist_status()
check("Has 6+ subsystems", len(status["subsystems"]) >= 6)
check("ADCS in subsystems", "ADCS" in status["subsystems"])
check("EPS in subsystems", "EPS" in status["subsystems"])
check("OBC in subsystems", "OBC" in status["subsystems"])
check("TCS in subsystems", "TCS" in status["subsystems"])
check("COMMS in subsystems", "COMMS" in status["subsystems"])
check("SYSTEM in subsystems", "SYSTEM" in status["subsystems"])
check("Total commands > 50", status["total_commands"] > 50)
check("All subsystem counts > 0",
      all(c > 0 for c in status["counts_per_subsystem"].values()))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2: Known-good commands are whitelisted
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 2: Known-good commands whitelisted\n")

KNOWN_GOOD = [
    ("CMD_GYRO_RESET", "ADCS"),
    ("CMD_ATTITUDE_REACQUISITION", "ADCS"),
    ("CMD_REACTION_WHEEL_DESAT", "ADCS"),
    ("CMD_SUN_ACQUISITION", "ADCS"),
    ("CMD_VERIFY_SEU_COUNTER", None),  # In both ADCS and SYSTEM
    ("CMD_GYRO_SWITCH_TO_BACKUP", "ADCS"),
    ("CMD_SOLAR_ARRAY_VERIFY", "EPS"),
    ("CMD_BATTERY_VERIFY", "EPS"),
    ("CMD_BUS_VOLTAGE_CHECK", "EPS"),
    ("CMD_POWER_SHED_NONESSENTIAL", "EPS"),
    ("CMD_POWER_RESTORE", "EPS"),
    ("CMD_OBC_CONTROLLED_REBOOT", "OBC"),
    ("CMD_OBC_WATCHDOG_CLEAR", "OBC"),
    ("CMD_WATCHDOG_CLEAR", "OBC"),
    ("CMD_CPU_LOAD_CHECK", "OBC"),
    ("CMD_MEMORY_DUMP", "OBC"),
    ("CMD_SAFE_MODE_EXIT", "OBC"),
    ("CMD_HEATER_ENABLE", "TCS"),
    ("CMD_HEATER_DISABLE", "TCS"),
    ("CMD_THERMAL_MONITOR_CHECK", "TCS"),
    ("CMD_TRANSPONDER_LOCK_VERIFY", "COMMS"),
    ("CMD_TRANSPONDER_RESET", "COMMS"),
    ("CMD_COMMS_SIGNAL_CHECK", "COMMS"),
    ("CMD_LOW_GAIN_ANTENNA_SWITCH", "COMMS"),
    ("CMD_HEALTH_CHECK", "SYSTEM"),
    ("CMD_TELEMETRY_DUMP", "SYSTEM"),
]

for cmd, expected_sub in KNOWN_GOOD:
    check(f"'{cmd}' is whitelisted", is_command_whitelisted(cmd))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3: Fake/unknown commands are blocked
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 3: Unknown commands blocked\n")

UNKNOWN_COMMANDS = [
    "CMD_LAUNCH_MISSILE",
    "CMD_FORMAT_DISK",
    "CMD_DELETE_LOGS",
    "CMD_SELF_DESTRUCT",
    "CMD_DEPLOY_PAYLOAD_UNAUTHORIZED",
    "CMD_OVERRIDE_SAFETY",
    "CMD_GYRO_HACK",
    "CMD_RANDOM_REBOOT",
]

for cmd in UNKNOWN_COMMANDS:
    check(f"'{cmd}' is NOT whitelisted", not is_command_whitelisted(cmd))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4: Non-CMD_ strings are blocked
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 4: Non-CMD_ strings blocked\n")

NON_CMD = [
    "RESET_GYRO",
    "gyro_reset",
    "reboot",
    "",
    "RUN_DIAGNOSTICS",
    "verify_status",
    "hello world",
    "SELECT * FROM commands",
]

for cmd in NON_CMD:
    check(f"Non-CMD '{cmd}' blocked", not is_command_whitelisted(cmd))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 5: Subsystem inference
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 5: Subsystem inference\n")

INFERENCE_CASES = [
    ("CMD_GYRO_RESET", "ADCS"),
    ("CMD_ATTITUDE_REACQUISITION", "ADCS"),
    ("CMD_REACTION_WHEEL_DESAT", "ADCS"),
    ("CMD_SUN_ACQUISITION", "ADCS"),
    ("CMD_SEU_CHECK", "ADCS"),
    ("CMD_SOLAR_ARRAY_VERIFY", "EPS"),
    ("CMD_BATTERY_VERIFY", "EPS"),
    ("CMD_BUS_VOLTAGE_CHECK", "EPS"),
    ("CMD_POWER_SHED_NONESSENTIAL", "EPS"),
    ("CMD_OBC_CONTROLLED_REBOOT", "OBC"),
    ("CMD_WATCHDOG_CLEAR", "OBC"),
    ("CMD_CPU_LOAD_CHECK", "OBC"),
    ("CMD_MEMORY_DUMP", "OBC"),
    ("CMD_SAFE_MODE_EXIT", "OBC"),
    ("CMD_HEATER_ENABLE", "TCS"),
    ("CMD_THERMAL_MONITOR_CHECK", "TCS"),
    ("CMD_TRANSPONDER_LOCK_VERIFY", "COMMS"),
    ("CMD_COMMS_SIGNAL_CHECK", "COMMS"),
    ("CMD_ANTENNA_SWITCH", "COMMS"),
    ("CMD_LOW_GAIN_ANTENNA_SWITCH", "COMMS"),
    ("CMD_HEALTH_CHECK", "SYSTEM"),
    ("CMD_TELEMETRY_DUMP", "SYSTEM"),
    ("CMD_VERIFY_STATUS", "SYSTEM"),
    ("CMD_VERIFY_SEU_COUNTER", "SYSTEM"),
]

for cmd, expected in INFERENCE_CASES:
    result = infer_subsystem(cmd)
    check(f"infer '{cmd}' → {expected}", result == expected)

check("infer_subsystem('') returns None", infer_subsystem("") is None)
check("infer_subsystem('REBOOT') returns None",
      infer_subsystem("REBOOT") is None)
check("infer_subsystem(None-like) safe",
      infer_subsystem("") is None)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 6: CMD_VERIFY_* is always safe
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 6: CMD_VERIFY_* always safe\n")

VERIFY_COMMANDS = [
    "CMD_VERIFY_SEU_COUNTER",
    "CMD_VERIFY_STATUS",
    "CMD_VERIFY_HEALTH",
    "CMD_VERIFY_POWER",
    "CMD_VERIFY_ATTITUDE",
    "CMD_VERIFY_THERMAL",
    "CMD_VERIFY_COMMS",
    "CMD_VERIFY_GYRO_RATE",
]

# Verify commands should not be blocked by any constraint check
# even in the worst possible context
worst_ctx = {
    "SOC": 5.0,
    "GYRO_A_RATE": float("nan"),
    "TRANSPONDER_LOCK": 0,
    "Component_temp_C": 120.0,
}

for cmd in VERIFY_COMMANDS:
    step = _step(cmd)
    check(f"'{cmd}' whitelisted", is_command_whitelisted(cmd))
    check(f"'{cmd}' not blocked by battery", check_battery_floor(step, worst_ctx) is None)
    check(f"'{cmd}' not blocked by gyro", check_gyro_health_prerequisite(step, worst_ctx) is None)
    check(f"'{cmd}' not blocked by thermal", check_thermal_survival(step, worst_ctx) is None)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 7: Battery floor blocks dangerous commands below 15% SoC
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 7: Battery floor check\n")

low_battery = {"SOC": 12.0}

# Should block power-requiring commands
for cmd in [
    "CMD_ATTITUDE_REACQUISITION",
    "CMD_SUN_ACQUISITION",
    "CMD_REACTION_WHEEL_DESAT",
    "CMD_OBC_CONTROLLED_REBOOT",
    "CMD_POWER_RESTORE",
    "CMD_HEATER_ENABLE",
]:
    violation = check_battery_floor(_step(cmd), low_battery)
    check(f"Battery blocks '{cmd}' at 12% SoC", violation is not None)
    if violation:
        check(f"  violation code is BATTERY_FLOOR",
              violation.code == "BATTERY_FLOOR")

# Various context key formats
for ctx_key, ctx_val in [
    ({"BATTERY_SOC": 10.0}, "BATTERY_SOC"),
    ({"battery_soc": 8.0}, "battery_soc"),
    ({"SoC_pct": 5.0}, "SoC_pct"),
]:
    v = check_battery_floor(_step("CMD_ATTITUDE_REACQUISITION"), ctx_key)
    check(f"Battery floor via '{ctx_val}' key", v is not None)

# Telemetry nested format
nested_ctx = {
    "pre_fault_telemetry": [
        {"parameter": "SoC_pct", "value": 12.3},
    ]
}
v = check_battery_floor(_step("CMD_ATTITUDE_REACQUISITION"), nested_ctx)
check("Battery floor via nested telemetry", v is not None)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 8: Battery floor allows verification commands at low SoC
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 8: Battery floor — verify commands pass\n")

for cmd in VERIFY_COMMANDS:
    check(f"'{cmd}' NOT blocked at 12% SoC",
          check_battery_floor(_step(cmd), low_battery) is None)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 9: Battery floor does not block at sufficient SoC
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 9: Battery floor — sufficient SoC passes\n")

good_battery = {"SOC": 60.0}
for cmd in ["CMD_ATTITUDE_REACQUISITION", "CMD_OBC_CONTROLLED_REBOOT"]:
    check(f"'{cmd}' passes at 60% SoC",
          check_battery_floor(_step(cmd), good_battery) is None)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 10: Gyro prerequisite blocks attitude maneuvers — missing gyro
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 10: Gyro prerequisite — missing/invalid gyro data\n")

missing_gyro = {"GYRO_A_RATE": None}
nan_gyro = {"GYRO_A_RATE": float("nan")}
str_nan_gyro = {"GYRO_A_RATE": "NaN"}
empty_str_gyro = {"GYRO_A_RATE": ""}

GYRO_DEPENDENT = [
    "CMD_ATTITUDE_REACQUISITION",
    "CMD_SUN_ACQUISITION",
    "CMD_REACTION_WHEEL_DESAT",
]

for cmd in GYRO_DEPENDENT:
    check(f"'{cmd}' blocked when gyro=None",
          check_gyro_health_prerequisite(_step(cmd), missing_gyro) is not None)
    check(f"'{cmd}' blocked when gyro=NaN",
          check_gyro_health_prerequisite(_step(cmd), nan_gyro) is not None)
    check(f"'{cmd}' blocked when gyro='NaN'",
          check_gyro_health_prerequisite(_step(cmd), str_nan_gyro) is not None)
    check(f"'{cmd}' blocked when gyro=''",
          check_gyro_health_prerequisite(_step(cmd), empty_str_gyro) is not None)

# Gyro OK
ok_gyro = {"GYRO_A_RATE": 1.5}
for cmd in GYRO_DEPENDENT:
    check(f"'{cmd}' passes with valid gyro",
          check_gyro_health_prerequisite(_step(cmd), ok_gyro) is None)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 11: Gyro prerequisite — nested telemetry format
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 11: Gyro prerequisite — nested telemetry\n")

nested_nan_gyro = {
    "pre_fault_telemetry": [
        {"parameter": "Gyro_rate_degs", "value": "NaN"},
    ]
}
v = check_gyro_health_prerequisite(
    _step("CMD_ATTITUDE_REACQUISITION"), nested_nan_gyro
)
check("Gyro blocked via nested telemetry NaN", v is not None)

nested_ok_gyro = {
    "pre_fault_telemetry": [
        {"parameter": "Gyro_rate_degs", "value": 2.3},
    ]
}
v = check_gyro_health_prerequisite(
    _step("CMD_ATTITUDE_REACQUISITION"), nested_ok_gyro
)
check("Gyro passes via nested telemetry valid", v is None)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 12: Comms lock blocks reboot
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 12: Comms lock for reboot\n")

no_lock = {"TRANSPONDER_LOCK": 0}
has_lock = {"TRANSPONDER_LOCK": 1}

check("Reboot blocked without lock",
      check_comms_lock_for_reboot(
          _step("CMD_OBC_CONTROLLED_REBOOT"), no_lock) is not None)
check("Reboot allowed with lock",
      check_comms_lock_for_reboot(
          _step("CMD_OBC_CONTROLLED_REBOOT"), has_lock) is None)
check("Soft reset blocked without lock",
      check_comms_lock_for_reboot(
          _step("CMD_OBC_SOFT_RESET"), no_lock) is not None)

# Other commands not affected
check("Gyro reset not affected by lock",
      check_comms_lock_for_reboot(
          _step("CMD_GYRO_RESET"), no_lock) is None)

# Key format variants
for ctx in [
    {"transponder_lock": 0},
    {"TRANSPONDER_LOCK": False},
    {"TRANSPONDER_LOCK": "0"},
    {"TRANSPONDER_LOCK": "false"},
    {"TRANSPONDER_LOCK": "no"},
]:
    check(f"Reboot blocked with {ctx}",
          check_comms_lock_for_reboot(
              _step("CMD_OBC_CONTROLLED_REBOOT"), ctx) is not None)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 13: Thermal survival
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 13: Thermal survival check\n")

hot_ctx = {"Component_temp_C": 90.0}

check("Reboot blocked at 90°C",
      check_thermal_survival(
          _step("CMD_OBC_CONTROLLED_REBOOT"), hot_ctx) is not None)
check("Attitude blocked at 90°C",
      check_thermal_survival(
          _step("CMD_ATTITUDE_REACQUISITION"), hot_ctx) is not None)

# Heater disable is allowed (actively addressing thermal issue)
check("Heater disable allowed at 90°C",
      check_thermal_survival(
          _step("CMD_HEATER_DISABLE"), hot_ctx) is None)
check("Heater off allowed at 90°C",
      check_thermal_survival(
          _step("CMD_HEATER_OFF"), hot_ctx) is None)
check("Thermal check allowed at 90°C",
      check_thermal_survival(
          _step("CMD_THERMAL_MONITOR_CHECK"), hot_ctx) is None)

# Verify commands pass
check("Verify passes at 90°C",
      check_thermal_survival(
          _step("CMD_VERIFY_STATUS"), hot_ctx) is None)

# Normal temp passes
cool_ctx = {"Component_temp_C": 25.0}
check("Reboot passes at 25°C",
      check_thermal_survival(
          _step("CMD_OBC_CONTROLLED_REBOOT"), cool_ctx) is None)

# Nested temperature via telemetry
nested_hot = {
    "pre_fault_telemetry": [
        {"parameter": "Component_temp_C", "value": 95.0},
    ]
}
check("Thermal blocks via nested telemetry",
      check_thermal_survival(
          _step("CMD_ATTITUDE_REACQUISITION"), nested_hot) is not None)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 14: HIGH risk escalation
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 14: HIGH risk escalation\n")

high_step = _step("CMD_GYRO_RESET", risk=RiskLevel.HIGH)
low_step = _step("CMD_GYRO_RESET", risk=RiskLevel.LOW)
medium_step = _step("CMD_GYRO_RESET", risk=RiskLevel.MEDIUM)
blocked_step = _step("CMD_GYRO_RESET", risk=RiskLevel.BLOCKED)

check("HIGH risk → escalation",
      check_high_risk_escalation(high_step, {}) is not None)
check("BLOCKED risk → escalation",
      check_high_risk_escalation(blocked_step, {}) is not None)
check("LOW risk → no escalation",
      check_high_risk_escalation(low_step, {}) is None)
check("MEDIUM risk → no escalation",
      check_high_risk_escalation(medium_step, {}) is None)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 15: Confidence escalation
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 15: Confidence escalation\n")

low_conf_output = _make_output(["CMD_GYRO_RESET"], confidence=0.60)
result = validate_recovery_plan(low_conf_output, {})
check("confidence=0.60 → requires_human_review",
      result.requires_human_review is True)

high_conf_output = _make_output(["CMD_GYRO_RESET"], confidence=0.91)
result = validate_recovery_plan(high_conf_output, {})
check("confidence=0.91 → no forced human review",
      result.requires_human_review is False)

# Edge: exactly at threshold
edge_output = _make_output(["CMD_GYRO_RESET"], confidence=0.70)
result = validate_recovery_plan(edge_output, {})
check("confidence=0.70 → no forced human review (at boundary)",
      result.requires_human_review is False)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 16: Safe plan passes unchanged
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 16: Safe plan passes unchanged\n")

safe_output = _make_output([
    "CMD_VERIFY_SEU_COUNTER",
    "CMD_GYRO_RESET",
    "CMD_ATTITUDE_REACQUISITION",
])
result = validate_recovery_plan(safe_output, {"GYRO_A_RATE": 2.0})
check("Safe plan is_safe=True", result.is_safe is True)
check("Safe plan: 3 validated steps", len(result.validated_steps) == 3)
check("Safe plan: 0 blocked steps", len(result.blocked_steps) == 0)
check("Safe plan: summary is non-empty", len(result.safety_summary) > 0)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 17: Mixed plan — keeps safe, blocks unsafe
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 17: Mixed plan\n")

mixed_output = _make_output([
    "CMD_VERIFY_SEU_COUNTER",      # safe
    "CMD_ATTITUDE_REACQUISITION",  # blocked (NaN gyro)
    "CMD_GYRO_RESET",              # safe
])
mixed_ctx = {"GYRO_A_RATE": float("nan")}
result = validate_recovery_plan(mixed_output, mixed_ctx)
check("Mixed: not fully safe", result.is_safe is False)
check("Mixed: 2 validated steps", len(result.validated_steps) == 2)
check("Mixed: 1 blocked step", len(result.blocked_steps) == 1)
check("Mixed: blocked command is attitude",
      result.blocked_steps[0].original_step.command == "CMD_ATTITUDE_REACQUISITION")
check("Mixed: requires_human_review (has blocked step)",
      result.requires_human_review is True)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 18: BlockedStep has non-empty reason
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 18: BlockedStep reasons\n")

fake_output = _make_output(["CMD_LAUNCH_MISSILE"])
result = validate_recovery_plan(fake_output, {})
check("Fake command blocked", len(result.blocked_steps) == 1)
check("BlockedStep has reason", len(result.blocked_steps[0].reason) > 5)
check("BlockedStep has violation_code",
      len(result.blocked_steps[0].violation_code) > 0)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 19: ValidationResult.safety_summary is non-empty
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 19: Safety summary\n")

result_safe = validate_recovery_plan(
    _make_output(["CMD_GYRO_RESET"]), {}
)
check("Safe summary is non-empty", len(result_safe.safety_summary) > 0)
check("Safe summary mentions 'passed' or 'step'",
      "step" in result_safe.safety_summary.lower()
      or "passed" in result_safe.safety_summary.lower())

result_blocked = validate_recovery_plan(
    _make_output(["CMD_LAUNCH_MISSILE"]), {}
)
check("Blocked summary is non-empty", len(result_blocked.safety_summary) > 0)
check("Blocked summary mentions 'blocked'",
      "blocked" in result_blocked.safety_summary.lower())


# ═══════════════════════════════════════════════════════════════════════════
# TEST 20: Empty crash_dump_context does not crash
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 20: Empty context handling\n")

result = validate_recovery_plan(
    _make_output(["CMD_GYRO_RESET", "CMD_BATTERY_VERIFY"]),
    {},
)
check("Empty context: no crash", True)
check("Empty context: is_safe=True", result.is_safe is True)
check("Empty context: 2 validated steps", len(result.validated_steps) == 2)

result_none = validate_recovery_plan(
    _make_output(["CMD_GYRO_RESET"]),
    {},  # Simulating empty context
)
check("Minimal context: no crash", True)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 21: apply_validation_to_output
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 21: apply_validation_to_output\n")

original = _make_output([
    "CMD_VERIFY_SEU_COUNTER",
    "CMD_ATTITUDE_REACQUISITION",  # will be blocked
    "CMD_GYRO_RESET",
])
ctx21 = {"GYRO_A_RATE": None}
vr = validate_recovery_plan(original, ctx21)
updated = apply_validation_to_output(original, vr)

check("Updated has 2 recovery steps", len(updated.recovery_plan) == 2)
check("Steps are renumbered 1,2",
      [s.step for s in updated.recovery_plan] == [1, 2])
check("Updated requires_human_review",
      updated.requires_human_review is True)
check("Summary mentions SAFETY",
      "SAFETY" in updated.reasoning_summary)
check("Updated is still valid SentinelOutput",
      isinstance(updated, SentinelOutput))

# All steps blocked → fallback health check
all_blocked = _make_output(["CMD_LAUNCH_MISSILE"])
vr_all = validate_recovery_plan(all_blocked, {})
updated_all = apply_validation_to_output(all_blocked, vr_all)
check("All blocked → fallback CMD_HEALTH_CHECK",
      updated_all.recovery_plan[0].command == "CMD_HEALTH_CHECK")
check("All blocked → still valid SentinelOutput",
      isinstance(updated_all, SentinelOutput))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 22: Unsafe command regression set (100% catch rate required)
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 22: Unsafe command regression set (100% catch rate)\n")

UNSAFE_CASES = [
    # (command, context, expected_violation_code, description)
    (
        "CMD_LAUNCH_MISSILE",
        {},
        "NOT_WHITELISTED",
        "Fake command",
    ),
    (
        "CMD_OBC_CONTROLLED_REBOOT",
        {"TRANSPONDER_LOCK": 0},
        "COMMS_LOCK_REBOOT",
        "Reboot with no comms lock",
    ),
    (
        "CMD_ATTITUDE_REACQUISITION",
        {"GYRO_A_RATE": float("nan")},
        "GYRO_HEALTH_PREREQUISITE",
        "Attitude reacq with NaN gyro",
    ),
    (
        "CMD_ATTITUDE_REACQUISITION",
        {"GYRO_A_RATE": None},
        "GYRO_HEALTH_PREREQUISITE",
        "Attitude reacq with None gyro",
    ),
    (
        "CMD_POWER_RESTORE",
        {"SOC": 12.0},
        "BATTERY_FLOOR",
        "Power restore at 12% SoC",
    ),
    (
        "CMD_HEATER_ENABLE",
        {"SOC": 5.0},
        "BATTERY_FLOOR",
        "Heater enable at 5% SoC",
    ),
    (
        "CMD_REACTION_WHEEL_DESAT",
        {"SOC": 10.0},
        "BATTERY_FLOOR",
        "Reaction wheel desat at 10% SoC",
    ),
    (
        "CMD_ATTITUDE_REACQUISITION",
        {"Component_temp_C": 95.0},
        "THERMAL_SURVIVAL",
        "Attitude maneuver during thermal runaway",
    ),
    (
        "CMD_OBC_CONTROLLED_REBOOT",
        {"Component_temp_C": 100.0},
        "THERMAL_SURVIVAL",
        "Reboot during thermal runaway (>85°C)",
    ),
    (
        "REBOOT_NOW",
        {},
        "INVALID_FORMAT",
        "Non-CMD_ prefix string",
    ),
    (
        "gyro_reset_please",
        {},
        "INVALID_FORMAT",
        "Lowercase non-CMD_ string",
    ),
    (
        "CMD_DELETE_ALL_DATA",
        {},
        "NOT_WHITELISTED",
        "Malicious command",
    ),
    (
        "CMD_OVERRIDE_SAFETY",
        {},
        "NOT_WHITELISTED",
        "Safety override attempt",
    ),
    (
        "CMD_SUN_ACQUISITION",
        {"GYRO_A_RATE": "NaN"},
        "GYRO_HEALTH_PREREQUISITE",
        "Sun acq with string NaN gyro",
    ),
    (
        "CMD_REACTION_WHEEL_RESET",
        {"GYRO_A_RATE": ""},
        "GYRO_HEALTH_PREREQUISITE",
        "Reaction wheel reset with empty gyro string",
    ),
]

caught = 0
total_unsafe = len(UNSAFE_CASES)

for cmd, ctx, expected_code, desc in UNSAFE_CASES:
    output = _make_output([cmd])
    result = validate_recovery_plan(output, ctx)
    is_caught = (
        not result.is_safe
        and len(result.blocked_steps) > 0
        and result.blocked_steps[0].violation_code == expected_code
    )
    check(f"UNSAFE: {desc} → caught ({expected_code})", is_caught)
    if is_caught:
        caught += 1

catch_rate = caught / total_unsafe if total_unsafe > 0 else 0
check(f"Overall catch rate: {caught}/{total_unsafe} = {catch_rate:.0%}",
      catch_rate == 1.0)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 23: Table-driven whitelist per subsystem
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 23: Per-subsystem whitelist coverage\n")

for subsystem, commands in COMMAND_WHITELIST.items():
    for cmd in commands:
        check(f"[{subsystem}] '{cmd}' whitelisted in own subsystem",
              is_command_whitelisted(cmd, subsystem))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 24: Cross-subsystem — commands NOT in wrong subsystems
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 24: Cross-subsystem isolation\n")

check("ADCS cmd not in EPS", not is_command_whitelisted("CMD_GYRO_RESET", "EPS"))
check("EPS cmd not in ADCS", not is_command_whitelisted("CMD_BATTERY_VERIFY", "ADCS"))
check("OBC cmd not in TCS", not is_command_whitelisted("CMD_OBC_CONTROLLED_REBOOT", "TCS"))
check("TCS cmd not in COMMS", not is_command_whitelisted("CMD_HEATER_ENABLE", "COMMS"))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 25: Missing context permissiveness
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 25: Missing context — permissive behavior\n")

# Gyro data NOT in context at all → permissive
empty_ctx = {}
v = check_gyro_health_prerequisite(
    _step("CMD_ATTITUDE_REACQUISITION"), empty_ctx
)
check("Gyro: missing entirely → permissive (not blocked)", v is None)

# Battery not in context → permissive
v = check_battery_floor(
    _step("CMD_ATTITUDE_REACQUISITION"), empty_ctx
)
check("Battery: missing entirely → permissive (not blocked)", v is None)

# Transponder not in context → permissive
v = check_comms_lock_for_reboot(
    _step("CMD_OBC_CONTROLLED_REBOOT"), empty_ctx
)
check("Comms lock: missing entirely → permissive (not blocked)", v is None)

# Temperature not in context → permissive
v = check_thermal_survival(
    _step("CMD_ATTITUDE_REACQUISITION"), empty_ctx
)
check("Temperature: missing entirely → permissive (not blocked)", v is None)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 26: hardware_state nested gyro
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 26: hardware_state gyro degraded\n")

hw_degraded = {"hardware_state": {"gyro_health": "degraded"}}
v = check_gyro_health_prerequisite(
    _step("CMD_ATTITUDE_REACQUISITION"), hw_degraded
)
check("Degraded gyro health → blocks attitude", v is not None)

hw_nominal = {"hardware_state": {"gyro_health": "nominal"}}
v = check_gyro_health_prerequisite(
    _step("CMD_ATTITUDE_REACQUISITION"), hw_nominal
)
# No GYRO_A_RATE key found → "NOT_FOUND" → permissive
check("Nominal gyro health (no rate) → permissive", v is None)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 27: Integration — agent.py calls safety validation
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 27: Integration — agent.py safety hook\n")

_agent_path = os.path.join(os.path.dirname(__file__), "..", "app", "agent", "agent.py")
with open(_agent_path) as f:
    agent_source = f.read()

check("agent.py imports safety module",
      "from app.agent.safety import" in agent_source or "import safety" in agent_source)
check("agent.py calls validate_recovery_plan",
      "validate_recovery_plan" in agent_source)
check("agent.py calls apply_validation_to_output",
      "apply_validation_to_output" in agent_source)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 28: Constants are correct
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 28: Safety constants\n")

check("BATTERY_FLOOR_SOC is 15%", BATTERY_FLOOR_SOC == 15.0)
check("THERMAL_SURVIVAL_LIMIT is 85°C", THERMAL_SURVIVAL_LIMIT == 85.0)
check("CONFIDENCE_REVIEW_THRESHOLD is 0.70",
      CONFIDENCE_REVIEW_THRESHOLD == 0.70)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 29: Multiple violations — first blocking violation wins
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 29: Multiple violations\n")

multi_ctx = {
    "SOC": 5.0,
    "GYRO_A_RATE": float("nan"),
    "Component_temp_C": 100.0,
}
output_multi = _make_output(["CMD_ATTITUDE_REACQUISITION"])
result = validate_recovery_plan(output_multi, multi_ctx)
check("Multi-violation: step blocked", len(result.blocked_steps) == 1)
check("Multi-violation: exactly one violation code recorded",
      result.blocked_steps[0].violation_code in
      ("BATTERY_FLOOR", "GYRO_HEALTH_PREREQUISITE", "THERMAL_SURVIVAL"))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 30: HIGH risk in validate_recovery_plan
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 30: HIGH risk via validate_recovery_plan\n")

high_risk_output = _make_output(["CMD_GYRO_RESET"])
# Override the step to have HIGH risk
high_risk_output.recovery_plan[0] = _step("CMD_GYRO_RESET", risk=RiskLevel.HIGH)
result = validate_recovery_plan(high_risk_output, {})
check("HIGH risk step passes whitelist", len(result.validated_steps) == 1)
check("HIGH risk → requires_human_review", result.requires_human_review is True)


# ═══════════════════════════════════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
print(f"Results: {_passed} passed, {_failed} failed")
print(f"{'=' * 60}\n")

if _failed > 0:
    print("⚠️  Some tests failed. Review the errors above.\n")
    sys.exit(1)
else:
    print("🎉 All tests passed! safety.py is verified and ready.\n")
    sys.exit(0)
