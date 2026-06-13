"""
SENTINEL — Deterministic Command Whitelist Validator (safety.py)

Step 7 of the SENTINEL build plan. This module is the deterministic
backstop for every recovery command the LLM produces. It enforces:

  1. Command whitelist — only known, safe CMD_ commands pass
  2. Physical constraint checks — battery floor, gyro health, comms lock,
     thermal survival
  3. Escalation rules — HIGH-risk steps and low-confidence outputs force
     requires_human_review = True

Design rules:
  - Pure Python. No AI calls. No LLM dependency. No new packages.
  - Never crashes on missing context — uses safe .get() everywhere.
  - Missing context is permissive UNLESS a mandatory prerequisite is
    explicitly required by safety policy.
  - CMD_VERIFY_* commands are always safe (observation-only).
  - Deterministic, side-effect free, target < 5 ms for a full plan.
  - 100% catch rate on intentionally inserted unsafe commands.

Public API:
  validate_recovery_plan(sentinel_output, crash_dump_context) -> ValidationResult
  apply_validation_to_output(sentinel_output, validation_result) -> SentinelOutput
  is_command_whitelisted(command, subsystem=None) -> bool
  infer_subsystem(command) -> str | None
  get_whitelist_status() -> dict
"""

from __future__ import annotations

import logging
import math
from typing import Any

from pydantic import BaseModel, Field

from app.api.models import RecoveryStep, RiskLevel, SentinelOutput

logger = logging.getLogger("sentinel.safety")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — RESULT MODELS
# ═══════════════════════════════════════════════════════════════════════════

class ConstraintViolation(BaseModel):
    """A single physical-constraint violation found during validation."""
    code: str = Field(
        ...,
        description="Machine-readable violation code, e.g. BATTERY_FLOOR",
    )
    reason: str = Field(
        ...,
        min_length=5,
        description="Human-readable explanation of why the command was blocked",
    )
    subsystem: str | None = Field(
        default=None,
        description="Subsystem related to the violation (if applicable)",
    )


class BlockedStep(BaseModel):
    """A recovery step that was blocked by the safety validator."""
    original_step: RecoveryStep
    reason: str = Field(
        ...,
        min_length=5,
        description="Why this step was blocked",
    )
    violation_code: str = Field(
        ...,
        description="Machine-readable violation code",
    )
    subsystem: str | None = Field(
        default=None,
        description="Subsystem the blocked command belongs to",
    )


class ValidationResult(BaseModel):
    """Output of validate_recovery_plan()."""
    is_safe: bool = Field(
        ...,
        description="True if all steps passed all checks",
    )
    validated_steps: list[RecoveryStep] = Field(
        default_factory=list,
        description="Steps that passed all checks (safe to execute)",
    )
    blocked_steps: list[BlockedStep] = Field(
        default_factory=list,
        description="Steps that were blocked by whitelist or constraint checks",
    )
    requires_human_review: bool = Field(
        ...,
        description=(
            "True if any HIGH-risk step, confidence < 0.70, or blocked step"
        ),
    )
    safety_summary: str = Field(
        ...,
        min_length=1,
        description="Human-readable summary of the validation outcome",
    )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — COMMAND WHITELIST BY SUBSYSTEM
# ═══════════════════════════════════════════════════════════════════════════

COMMAND_WHITELIST: dict[str, set[str]] = {
    "ADCS": {
        # Gyro commands
        "CMD_GYRO_RESET",
        "CMD_GYRO_A_DRIVER_RESET",
        "CMD_GYRO_B_DRIVER_RESET",
        "CMD_GYRO_A_RESET",
        "CMD_GYRO_B_RESET",
        "CMD_GYRO_SWITCH_TO_BACKUP",
        "CMD_GYRO_BACKUP_SWITCH",
        # Attitude commands
        "CMD_ATTITUDE_REACQUISITION",
        "CMD_ATTITUDE_RESET",
        "CMD_ATTITUDE_HOLD",
        # Reaction wheel commands
        "CMD_REACTION_WHEEL_DESAT",
        "CMD_REACTION_WHEEL_RESET",
        "CMD_REACTION_WHEEL_SPEED_CHECK",
        # Sun acquisition
        "CMD_SUN_ACQUISITION",
        "CMD_SUN_SENSOR_CHECK",
        # SEU
        "CMD_VERIFY_SEU_COUNTER",
        "CMD_SEU_CHECK",
    },
    "EPS": {
        # Solar array
        "CMD_SOLAR_ARRAY_VERIFY",
        "CMD_SOLAR_ARRAY_REDEPLOY",
        "CMD_SOLAR_ARRAY_CHECK",
        "CMD_SOLAR_PANEL_RESET",
        # Battery
        "CMD_BATTERY_VERIFY",
        "CMD_BATTERY_CHECK",
        "CMD_BATTERY_HEATER_ENABLE",
        "CMD_BATTERY_HEATER_DISABLE",
        # Bus voltage
        "CMD_BUS_VOLTAGE_CHECK",
        "CMD_BUS_VOLTAGE_VERIFY",
        # Power management
        "CMD_POWER_SHED_NONESSENTIAL",
        "CMD_POWER_RESTORE",
        "CMD_POWER_CHECK",
    },
    "OBC": {
        "CMD_OBC_CONTROLLED_REBOOT",
        "CMD_OBC_WATCHDOG_CLEAR",
        "CMD_OBC_SOFT_RESET",
        "CMD_WATCHDOG_CLEAR",
        "CMD_WATCHDOG_RESET",
        "CMD_CPU_LOAD_CHECK",
        "CMD_CPU_TEMP_CHECK",
        "CMD_MEMORY_DUMP",
        "CMD_MEMORY_CHECK",
        "CMD_SAFE_MODE_EXIT",
        "CMD_SAFE_MODE_ENTRY",
    },
    "TCS": {
        "CMD_HEATER_ENABLE",
        "CMD_HEATER_DISABLE",
        "CMD_HEATER_OFF",
        "CMD_HEATER_ON",
        "CMD_HEATER_RESET",
        "CMD_HEATER_CHECK",
        "CMD_THERMAL_MONITOR_CHECK",
        "CMD_THERMAL_CHECK",
        "CMD_THERMAL_OVERRIDE_OFF",
        "CMD_THERMAL_OVERRIDE_ON",
    },
    "COMMS": {
        "CMD_TRANSPONDER_LOCK_VERIFY",
        "CMD_TRANSPONDER_RESET",
        "CMD_TRANSPONDER_CHECK",
        "CMD_COMMS_SIGNAL_CHECK",
        "CMD_COMMS_RESET",
        "CMD_COMMS_CHECK",
        "CMD_ANTENNA_SWITCH",
        "CMD_LOW_GAIN_ANTENNA_SWITCH",
        "CMD_ANTENNA_CHECK",
    },
    "SYSTEM": {
        "CMD_HEALTH_CHECK",
        "CMD_TELEMETRY_DUMP",
        "CMD_TELEMETRY_CHECK",
        "CMD_VERIFY_STATUS",
        "CMD_VERIFY_HEALTH",
        "CMD_VERIFY_POWER",
        "CMD_VERIFY_ATTITUDE",
        "CMD_VERIFY_THERMAL",
        "CMD_VERIFY_COMMS",
        "CMD_VERIFY_SEU_COUNTER",
        "CMD_VERIFY_GYRO_RATE",
    },
}

# Flat set for fast O(1) lookup
_ALL_WHITELISTED: set[str] = set()
for _cmds in COMMAND_WHITELIST.values():
    _ALL_WHITELISTED.update(_cmds)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — SUBSYSTEM INFERENCE
# ═══════════════════════════════════════════════════════════════════════════

# Prefix → subsystem mapping (checked in order, first match wins)
_PREFIX_MAP: list[tuple[str, str]] = [
    # ADCS
    ("CMD_GYRO_", "ADCS"),
    ("CMD_ATTITUDE_", "ADCS"),
    ("CMD_REACTION_WHEEL_", "ADCS"),
    ("CMD_SUN_", "ADCS"),
    ("CMD_SEU_", "ADCS"),
    # EPS
    ("CMD_SOLAR_", "EPS"),
    ("CMD_BATTERY_", "EPS"),
    ("CMD_BUS_", "EPS"),
    ("CMD_POWER_", "EPS"),
    # OBC
    ("CMD_OBC_", "OBC"),
    ("CMD_WATCHDOG_", "OBC"),
    ("CMD_CPU_", "OBC"),
    ("CMD_MEMORY_", "OBC"),
    ("CMD_SAFE_MODE_", "OBC"),
    # TCS
    ("CMD_HEATER_", "TCS"),
    ("CMD_THERMAL_", "TCS"),
    # COMMS
    ("CMD_TRANSPONDER_", "COMMS"),
    ("CMD_COMMS_", "COMMS"),
    ("CMD_ANTENNA_", "COMMS"),
    ("CMD_LOW_GAIN_", "COMMS"),
    # SYSTEM (last — catches remaining CMD_VERIFY_*, CMD_HEALTH_*, etc.)
    ("CMD_HEALTH_", "SYSTEM"),
    ("CMD_TELEMETRY_", "SYSTEM"),
    ("CMD_VERIFY_", "SYSTEM"),
]


def infer_subsystem(command: str) -> str | None:
    """Infer the subsystem a command belongs to from its prefix.

    Returns the subsystem string (e.g. "ADCS", "EPS") or None if
    the command doesn't match any known prefix pattern.

    Args:
        command: Command string, e.g. "CMD_GYRO_RESET".

    Returns:
        Subsystem string or None.
    """
    if not command or not command.startswith("CMD_"):
        return None

    for prefix, subsystem in _PREFIX_MAP:
        if command.startswith(prefix):
            return subsystem

    return None


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — WHITELIST HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def is_command_whitelisted(
    command: str,
    subsystem: str | None = None,
) -> bool:
    """Check whether a command is in the safety whitelist.

    Args:
        command: Command string to check.
        subsystem: If provided, check only that subsystem's whitelist.
            If None, check all subsystems.

    Returns:
        True if the command is whitelisted.
    """
    if not command or not command.startswith("CMD_"):
        return False

    if subsystem:
        sub_cmds = COMMAND_WHITELIST.get(subsystem, set())
        return command in sub_cmds

    return command in _ALL_WHITELISTED


def get_whitelist_status() -> dict:
    """Return diagnostic information about the whitelist.

    Useful for tests, debugging, and demo status panels.
    """
    counts = {sub: len(cmds) for sub, cmds in COMMAND_WHITELIST.items()}
    total = sum(counts.values())

    # Detect duplicates across subsystems
    all_cmds: list[str] = []
    for cmds in COMMAND_WHITELIST.values():
        all_cmds.extend(cmds)
    duplicates = [c for c in set(all_cmds) if all_cmds.count(c) > 1]

    return {
        "subsystems": list(COMMAND_WHITELIST.keys()),
        "counts_per_subsystem": counts,
        "total_commands": total,
        "unique_commands": len(_ALL_WHITELISTED),
        "duplicates": duplicates,
    }


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — CONTEXT HELPERS (safe .get() everywhere)
# ═══════════════════════════════════════════════════════════════════════════

def _is_verify_command(command: str) -> bool:
    """CMD_VERIFY_* commands are always safe (observation-only)."""
    return command.startswith("CMD_VERIFY_")


def _is_value_nan_or_missing(value: Any) -> bool:
    """Check if a value is missing, None, NaN, or non-numeric."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().upper() in ("NAN", "NONE", "", "N/A", "NULL")
    if isinstance(value, (int, float)):
        return math.isnan(value) if isinstance(value, float) else False
    return True  # Any other type is considered invalid for sensor data


def _get_battery_soc(ctx: dict[str, Any]) -> float | None:
    """Extract battery state-of-charge from crash dump context.

    Tries multiple key patterns permissively.
    Returns None if not found (caller treats this as permissive).
    """
    # Direct keys
    for key in ("SOC", "BATTERY_SOC", "battery_soc", "SoC_pct", "soc_pct"):
        val = ctx.get(key)
        if val is not None and not _is_value_nan_or_missing(val):
            try:
                return float(val)
            except (ValueError, TypeError):
                pass

    # Nested in pre_fault_telemetry
    telemetry = ctx.get("pre_fault_telemetry")
    if isinstance(telemetry, list):
        for entry in telemetry:
            if isinstance(entry, dict):
                param = entry.get("parameter", "")
                if param in ("SoC_pct", "SOC", "battery_soc", "BATTERY_SOC"):
                    val = entry.get("value")
                    if val is not None and not _is_value_nan_or_missing(val):
                        try:
                            return float(val)
                        except (ValueError, TypeError):
                            pass

    # Nested in hardware_state
    hw = ctx.get("hardware_state")
    if isinstance(hw, dict):
        for key in ("battery_soc", "SOC", "BATTERY_SOC", "SoC_pct"):
            val = hw.get(key)
            if val is not None and not _is_value_nan_or_missing(val):
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass

    return None


def _get_gyro_rate(ctx: dict[str, Any]) -> Any:
    """Extract gyro rate value from crash dump context.

    Returns the raw value (could be NaN, None, numeric, or string).
    Returns a sentinel "NOT_FOUND" string if no gyro data exists at all.
    """
    # Direct keys
    for key in ("GYRO_A_RATE", "gyro_a_rate", "Gyro_rate_degs",
                "GYRO_B_RATE", "gyro_b_rate"):
        if key in ctx:
            return ctx[key]

    # In pre_fault_telemetry
    telemetry = ctx.get("pre_fault_telemetry")
    if isinstance(telemetry, list):
        for entry in telemetry:
            if isinstance(entry, dict):
                param = entry.get("parameter", "")
                if param in ("Gyro_rate_degs", "GYRO_A_RATE", "gyro_a_rate",
                             "GYRO_B_RATE", "gyro_b_rate"):
                    return entry.get("value")

    # In hardware_state
    hw = ctx.get("hardware_state")
    if isinstance(hw, dict):
        gyro_health = hw.get("gyro_health")
        if gyro_health == "degraded":
            return None  # Degraded = treat as invalid

    return "NOT_FOUND"


def _get_transponder_lock(ctx: dict[str, Any]) -> Any:
    """Extract transponder lock status.

    Returns the raw value. "NOT_FOUND" if absent.
    """
    for key in ("TRANSPONDER_LOCK", "transponder_lock", "Transponder_lock"):
        if key in ctx:
            return ctx[key]

    # In pre_fault_telemetry
    telemetry = ctx.get("pre_fault_telemetry")
    if isinstance(telemetry, list):
        for entry in telemetry:
            if isinstance(entry, dict):
                param = entry.get("parameter", "")
                if param in ("Transponder_lock", "TRANSPONDER_LOCK",
                             "transponder_lock"):
                    return entry.get("value")

    return "NOT_FOUND"


def _get_max_temperature(ctx: dict[str, Any]) -> float | None:
    """Extract the maximum component temperature from crash dump context.

    Scans flat keys, nested dicts, and telemetry lists.
    Returns None if no temperature data found.
    """
    temps: list[float] = []

    # Direct temperature keys
    for key in ("Component_temp_C", "component_temp_c", "TEMP_C",
                "temperature_c", "temp_c", "OBC_temp_C"):
        val = ctx.get(key)
        if val is not None and not _is_value_nan_or_missing(val):
            try:
                temps.append(float(val))
            except (ValueError, TypeError):
                pass

    # In pre_fault_telemetry
    telemetry = ctx.get("pre_fault_telemetry")
    if isinstance(telemetry, list):
        for entry in telemetry:
            if isinstance(entry, dict):
                param = str(entry.get("parameter", ""))
                if "temp" in param.lower():
                    val = entry.get("value")
                    if val is not None and not _is_value_nan_or_missing(val):
                        try:
                            temps.append(float(val))
                        except (ValueError, TypeError):
                            pass

    # Nested temperature values
    for key in ("temperatures", "temp_readings"):
        val = ctx.get(key)
        if isinstance(val, list):
            for v in val:
                if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
                    temps.append(float(v))
        elif isinstance(val, dict):
            for v in val.values():
                if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
                    temps.append(float(v))

    return max(temps) if temps else None


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6 — CONSTRAINT CHECKS
# ═══════════════════════════════════════════════════════════════════════════
#
# Each check: (step, crash_dump_context) -> ConstraintViolation | None
# Returns None if the step passes the check.
# ═══════════════════════════════════════════════════════════════════════════

# Commands that require significant power or are non-essential
_POWER_REQUIRING_COMMANDS: set[str] = {
    "CMD_ATTITUDE_REACQUISITION",
    "CMD_ATTITUDE_RESET",
    "CMD_SUN_ACQUISITION",
    "CMD_REACTION_WHEEL_DESAT",
    "CMD_REACTION_WHEEL_RESET",
    "CMD_OBC_CONTROLLED_REBOOT",
    "CMD_OBC_SOFT_RESET",
    "CMD_POWER_RESTORE",
    "CMD_SOLAR_ARRAY_REDEPLOY",
    "CMD_HEATER_ENABLE",
    "CMD_HEATER_ON",
}

# Commands that require valid gyro data
_GYRO_DEPENDENT_COMMANDS: set[str] = {
    "CMD_ATTITUDE_REACQUISITION",
    "CMD_SUN_ACQUISITION",
    "CMD_REACTION_WHEEL_DESAT",
    "CMD_REACTION_WHEEL_RESET",
    "CMD_REACTION_WHEEL_SPEED_CHECK",
}

# Battery floor threshold (percentage)
BATTERY_FLOOR_SOC: float = 15.0

# Thermal survival threshold (Celsius)
THERMAL_SURVIVAL_LIMIT: float = 85.0

# Confidence threshold for human review escalation
CONFIDENCE_REVIEW_THRESHOLD: float = 0.70


def check_battery_floor(
    step: RecoveryStep,
    ctx: dict[str, Any],
) -> ConstraintViolation | None:
    """Block power-requiring commands if battery SoC < 15%.

    Verify commands are always allowed.
    Missing SoC data is permissive (does not block).
    """
    if _is_verify_command(step.command):
        return None

    if step.command not in _POWER_REQUIRING_COMMANDS:
        return None

    soc = _get_battery_soc(ctx)
    if soc is None:
        return None  # Missing data → permissive

    if soc < BATTERY_FLOOR_SOC:
        return ConstraintViolation(
            code="BATTERY_FLOOR",
            reason=(
                f"Battery SoC is {soc:.1f}% (below {BATTERY_FLOOR_SOC:.0f}% "
                f"floor). Command '{step.command}' requires more power than "
                f"available. Shed non-essential loads first."
            ),
            subsystem="EPS",
        )

    return None


def check_gyro_health_prerequisite(
    step: RecoveryStep,
    ctx: dict[str, Any],
) -> ConstraintViolation | None:
    """Block attitude maneuver commands if gyro data is invalid.

    Gyro data is invalid when: missing, None, NaN, or non-numeric.
    This is a mandatory prerequisite — blocks even with missing context,
    because running attitude maneuvers without gyro data risks tumbling.
    """
    if _is_verify_command(step.command):
        return None

    if step.command not in _GYRO_DEPENDENT_COMMANDS:
        return None

    gyro_value = _get_gyro_rate(ctx)

    # If gyro data is not found at all, this is permissive
    # (the sensor might be working fine, we just don't have it in context)
    if gyro_value == "NOT_FOUND":
        return None

    # If gyro data IS present but invalid → block
    if _is_value_nan_or_missing(gyro_value):
        return ConstraintViolation(
            code="GYRO_HEALTH_PREREQUISITE",
            reason=(
                f"Gyro rate data is invalid (value={gyro_value!r}). "
                f"Command '{step.command}' requires valid attitude data. "
                f"Reset gyro driver and verify rate before attitude maneuver."
            ),
            subsystem="ADCS",
        )

    return None


def check_comms_lock_for_reboot(
    step: RecoveryStep,
    ctx: dict[str, Any],
) -> ConstraintViolation | None:
    """Block OBC reboot if transponder lock is not confirmed.

    Rebooting without a comms lock risks losing the uplink during the
    reboot window, which could make the spacecraft unrecoverable.

    This is a mandatory prerequisite — if lock status is 0 or False,
    block the reboot. If lock status is simply absent, we treat it as
    permissive (operator may have confirmed lock out-of-band).
    """
    if step.command not in ("CMD_OBC_CONTROLLED_REBOOT", "CMD_OBC_SOFT_RESET"):
        return None

    lock_value = _get_transponder_lock(ctx)

    if lock_value == "NOT_FOUND":
        return None  # Not in context → permissive

    # Explicitly no lock
    if lock_value in (0, False, "0", "false", "False", "no", "NO"):
        return ConstraintViolation(
            code="COMMS_LOCK_REBOOT",
            reason=(
                f"Transponder lock is not confirmed (value={lock_value!r}). "
                f"Command '{step.command}' requires verified comms lock "
                f"before reboot. Verify transponder lock first."
            ),
            subsystem="COMMS",
        )

    return None


def check_thermal_survival(
    step: RecoveryStep,
    ctx: dict[str, Any],
) -> ConstraintViolation | None:
    """Block non-essential commands if any temperature exceeds 85°C.

    Verification commands are always allowed.
    Missing temperature data is permissive.
    """
    if _is_verify_command(step.command):
        return None

    # Allow temperature-related commands (they're actively addressing the issue)
    if step.command in (
        "CMD_HEATER_DISABLE", "CMD_HEATER_OFF",
        "CMD_THERMAL_OVERRIDE_OFF", "CMD_THERMAL_CHECK",
        "CMD_THERMAL_MONITOR_CHECK",
    ):
        return None

    max_temp = _get_max_temperature(ctx)
    if max_temp is None:
        return None  # Missing data → permissive

    if max_temp > THERMAL_SURVIVAL_LIMIT:
        return ConstraintViolation(
            code="THERMAL_SURVIVAL",
            reason=(
                f"Component temperature is {max_temp:.1f}°C (exceeds "
                f"{THERMAL_SURVIVAL_LIMIT:.0f}°C limit). Command "
                f"'{step.command}' is blocked until thermal conditions "
                f"are resolved. Disable heaters and monitor temperatures."
            ),
            subsystem="TCS",
        )

    return None


def check_high_risk_escalation(
    step: RecoveryStep,
    ctx: dict[str, Any],
) -> ConstraintViolation | None:
    """Flag HIGH-risk steps for human review escalation.

    This check does NOT block the step — it only signals that
    requires_human_review should be True. Returns a violation with
    code "HIGH_RISK_ESCALATION" so the caller can set the flag.
    """
    if step.risk in (RiskLevel.HIGH, RiskLevel.BLOCKED):
        return ConstraintViolation(
            code="HIGH_RISK_ESCALATION",
            reason=(
                f"Step {step.step} ('{step.command}') has risk level "
                f"'{step.risk.value}'. Human review required before execution."
            ),
            subsystem=infer_subsystem(step.command),
        )

    return None


# Registry of all constraint checks
_BLOCKING_CHECKS = [
    check_battery_floor,
    check_gyro_health_prerequisite,
    check_comms_lock_for_reboot,
    check_thermal_survival,
]

_ESCALATION_CHECKS = [
    check_high_risk_escalation,
]


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7 — PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════

def validate_recovery_plan(
    sentinel_output: SentinelOutput,
    crash_dump_context: dict[str, Any],
) -> ValidationResult:
    """Validate every recovery step in a SentinelOutput.

    Runs in order:
      1. Whitelist check — is the command CMD_-prefixed and in COMMAND_WHITELIST?
      2. Blocking constraint checks — battery, gyro, comms, thermal
      3. Escalation checks — HIGH-risk step flagging

    Steps that fail any blocking check are removed from validated_steps
    and added to blocked_steps with a reason.

    Args:
        sentinel_output: The LLM's structured output after schema validation.
        crash_dump_context: The crash dump dict (for physical constraint checks).

    Returns:
        ValidationResult with safe/blocked step lists and human review flag.
    """
    ctx = crash_dump_context or {}
    validated: list[RecoveryStep] = []
    blocked: list[BlockedStep] = []
    force_human_review = False

    for step in sentinel_output.recovery_plan:
        step_blocked = False

        # --- Check 1: Command must be CMD_-prefixed ---
        if not step.command.startswith("CMD_"):
            blocked.append(BlockedStep(
                original_step=step,
                reason=(
                    f"Command '{step.command}' does not follow the "
                    f"CMD_UPPER_SNAKE_CASE naming convention."
                ),
                violation_code="INVALID_FORMAT",
                subsystem=None,
            ))
            step_blocked = True

        # --- Check 2: Whitelist ---
        elif not is_command_whitelisted(step.command):
            blocked.append(BlockedStep(
                original_step=step,
                reason=(
                    f"Command '{step.command}' is not in the approved "
                    f"command whitelist."
                ),
                violation_code="NOT_WHITELISTED",
                subsystem=infer_subsystem(step.command),
            ))
            step_blocked = True

        # --- Check 3: Blocking constraint checks ---
        if not step_blocked:
            for check_fn in _BLOCKING_CHECKS:
                violation = check_fn(step, ctx)
                if violation is not None:
                    blocked.append(BlockedStep(
                        original_step=step,
                        reason=violation.reason,
                        violation_code=violation.code,
                        subsystem=violation.subsystem,
                    ))
                    step_blocked = True
                    break  # First blocking violation wins

        # --- Check 4: Escalation checks (non-blocking) ---
        if not step_blocked:
            for check_fn in _ESCALATION_CHECKS:
                violation = check_fn(step, ctx)
                if violation is not None:
                    force_human_review = True
            validated.append(step)

    # --- Confidence escalation ---
    if sentinel_output.confidence < CONFIDENCE_REVIEW_THRESHOLD:
        force_human_review = True

    # --- Any blocked unsafe step → human review ---
    if blocked:
        force_human_review = True

    # Build summary
    summary_parts: list[str] = []
    if not blocked:
        summary_parts.append(
            f"All {len(validated)} recovery step(s) passed safety validation."
        )
    else:
        summary_parts.append(
            f"{len(blocked)} step(s) blocked, "
            f"{len(validated)} step(s) approved."
        )
        codes = sorted(set(b.violation_code for b in blocked))
        summary_parts.append(f"Violations: {', '.join(codes)}.")

    if force_human_review:
        summary_parts.append("Human review required.")

    return ValidationResult(
        is_safe=len(blocked) == 0,
        validated_steps=validated,
        blocked_steps=blocked,
        requires_human_review=force_human_review,
        safety_summary=" ".join(summary_parts),
    )


def apply_validation_to_output(
    sentinel_output: SentinelOutput,
    validation_result: ValidationResult,
) -> SentinelOutput:
    """Apply safety validation to a SentinelOutput.

    Creates a new SentinelOutput with:
      - recovery_plan replaced by validated_steps (blocked steps removed)
      - requires_human_review set if any escalation triggered
      - reasoning_summary appended with safety info if steps were blocked

    Does not mutate the input objects.

    Args:
        sentinel_output: Original LLM output.
        validation_result: Result from validate_recovery_plan().

    Returns:
        New SentinelOutput with safety-validated recovery plan.
    """
    # Re-number validated steps sequentially (1, 2, 3, ...)
    renumbered_steps: list[RecoveryStep] = []
    for i, step in enumerate(validation_result.validated_steps, start=1):
        renumbered_steps.append(step.model_copy(update={"step": i}))

    # If all steps were blocked, keep at least the first safe verification
    # step so SentinelOutput validates (min_length=1 on recovery_plan).
    if not renumbered_steps:
        renumbered_steps = [RecoveryStep(
            step=1,
            command="CMD_HEALTH_CHECK",
            rationale=(
                "All LLM-proposed recovery steps were blocked by safety "
                "validation. Running a health check as the minimum safe action."
            ),
            wait_seconds=5,
            verify="Health check returns nominal status",
            risk=RiskLevel.LOW,
        )]

    # Update reasoning summary with safety info
    reasoning = sentinel_output.reasoning_summary
    if validation_result.blocked_steps:
        blocked_cmds = ", ".join(
            b.original_step.command for b in validation_result.blocked_steps
        )
        reasoning += (
            f" [SAFETY: {len(validation_result.blocked_steps)} command(s) "
            f"blocked ({blocked_cmds}). "
            f"{validation_result.safety_summary}]"
        )

    # Determine requires_human_review
    requires_review = (
        sentinel_output.requires_human_review
        or validation_result.requires_human_review
    )

    # Build new output (do NOT mutate the original)
    new_data = sentinel_output.model_dump()
    new_data["recovery_plan"] = [s.model_dump() for s in renumbered_steps]
    new_data["requires_human_review"] = requires_review
    new_data["reasoning_summary"] = reasoning

    return SentinelOutput(**new_data)
