"""
SENTINEL — Procedure Library (procedures/library.py)

Phase 9.  Structured equivalents of the 6 fallback KB entries from rag.py.
Each procedure is a fully typed ``ProcedureDefinition`` with individually
typed ``ProcedureStep`` objects referencing commands from the registry.

IMPORT-TIME VALIDATION
─────────────────────
Every ``command_id`` in every procedure step is validated against
``COMMAND_REGISTRY`` when this module is first imported.  If a drift occurs
(procedure references a command the registry does not define), the import
raises ``RuntimeError`` — exactly mirroring the contract enforced by
``conflicts.py`` for the old fallback KB.

Every ``procedure_id`` and ``citation_id`` is asserted unique.

PROVENANCE HONESTY
──────────────────
Phase 9 rule 6: these procedures are labeled ``FALLBACK_KB``, not ``ECSS``.
They are *written from* ECSS-E-ST-70-11C / ECSS-Q-ST-30-02C principles,
but SENTINEL does not have clause-level ECSS citations for them, so
claiming ``source_type=ECSS`` would be dishonest.
"""

from __future__ import annotations

from app.api.models import RiskLevel, SubsystemID
from app.procedures.models import (
    Citation,
    ProcedureDefinition,
    ProcedureStep,
)
from app.validation.command_registry import COMMAND_REGISTRY


# ═══════════════════════════════════════════════════════════════════════════
# HELPER
# ═══════════════════════════════════════════════════════════════════════════

_L = RiskLevel.LOW
_M = RiskLevel.MEDIUM

_FB_SOURCE = "FALLBACK_KB"
_FB_VERSION = "v1.0"
_FB_SECTION = "N/A"
_FB_CLAUSE = "N/A"
_FB_PROVENANCE = (
    "Based on ECSS-E-ST-70-11C / ECSS-Q-ST-30-02C principles for safe mode "
    "recovery.  No clause-level citation available."
)


def _cite(proc_id: str) -> Citation:
    """Build a stable citation for a fallback KB procedure."""
    return Citation(
        citation_id=f"CIT-{proc_id}",
        procedure_id=proc_id,
        source=_FB_SOURCE,
        source_version=_FB_VERSION,
        section=_FB_SECTION,
        clause=_FB_CLAUSE,
        provenance=_FB_PROVENANCE,
    )


# ═══════════════════════════════════════════════════════════════════════════
# PROCEDURE 1 — ADCS Gyroscope SEU Recovery
# ═══════════════════════════════════════════════════════════════════════════

_PROC_ADCS_SEU = ProcedureDefinition(
    procedure_id="PROC-ADCS-SEU-001",
    title="ADCS Gyroscope Single-Event Upset (SEU) Recovery",
    subsystem=SubsystemID.ADCS,
    fault_class="ADCS_GYRO_SEU",
    steps=(
        ProcedureStep(
            step_number=1,
            command_id="CMD_VERIFY_SEU_COUNTER",
            description=(
                "Read SEU counter to confirm radiation event.  "
                "A spike from baseline confirms this is SEU, not hardware failure."
            ),
            wait_seconds=5,
            verification="SEU_COUNTER read successfully",
            risk=_L,
        ),
        ProcedureStep(
            step_number=2,
            command_id="CMD_GYRO_A_DRIVER_RESET",
            description=(
                "Software reset of the gyroscope driver to clear "
                "corrupted state from SEU."
            ),
            wait_seconds=30,
            verification="GYRO_A_RATE returns a valid float value",
            risk=_L,
        ),
        ProcedureStep(
            step_number=3,
            command_id="CMD_ATTITUDE_REACQUISITION",
            description=(
                "Use star tracker and sun sensor to re-establish "
                "attitude knowledge."
            ),
            wait_seconds=60,
            verification="ATTITUDE_ERROR < 1 deg",
            risk=_M,
        ),
        ProcedureStep(
            step_number=4,
            command_id="CMD_SAFE_MODE_EXIT",
            description="Return spacecraft to nominal operations.",
            wait_seconds=30,
            verification="normal_mode_flag = 1",
            risk=_L,
        ),
    ),
    preconditions=(
        "SEU_COUNTER spiked from baseline",
        "GYRO_A_RATE is NaN or frozen",
        "Spacecraft is in safe mode",
    ),
    postconditions=(
        "GYRO_A_RATE returns valid float",
        "ATTITUDE_ERROR < 1 deg",
        "normal_mode_flag = 1",
    ),
    risk=_M,
    source=_FB_SOURCE,
    source_version=_FB_VERSION,
    section=_FB_SECTION,
    clause=_FB_CLAUSE,
    provenance=_FB_PROVENANCE,
    trigger_cues=(
        "GYRO_A_RATE", "GYRO_B_RATE", "SEU_COUNTER", "SEU",
        "NaN", "attitude_error", "ATTITUDE_ERROR", "ADCS",
        "gyro", "gyroscope", "cosmic_ray", "radiation",
        "ADCS_ERROR_THRESHOLD", "attitude",
        "Gyro_rate_degs", "Attitude_error_deg", "gyro_rate",
    ),
)


# ═══════════════════════════════════════════════════════════════════════════
# PROCEDURE 2 — EPS Solar Array Undervoltage Recovery
# ═══════════════════════════════════════════════════════════════════════════

_PROC_EPS_UNDERVOLT = ProcedureDefinition(
    procedure_id="PROC-EPS-UNDERVOLT-001",
    title="EPS Solar Array Undervoltage Recovery",
    subsystem=SubsystemID.EPS,
    fault_class="EPS_SOLAR_UNDERVOLT",
    steps=(
        ProcedureStep(
            step_number=1,
            command_id="CMD_VERIFY_SUN_ANGLE",
            description=(
                "Confirm spacecraft has valid sun pointing using sun sensor."
            ),
            wait_seconds=10,
            verification="sun_sensor_angle < 90 deg",
            risk=_L,
        ),
        ProcedureStep(
            step_number=2,
            command_id="CMD_SOLAR_ARRAY_A_RESET",
            description=(
                "Attempt to re-initialize the solar array drive electronics."
            ),
            wait_seconds=30,
            verification="I_sa > 2A within 30s",
            risk=_L,
        ),
        ProcedureStep(
            step_number=3,
            command_id="CMD_SWITCH_SOLAR_ARRAY",
            description=(
                "If Array A fails, switch to Array B or alternative power path."
            ),
            wait_seconds=30,
            verification="I_sa recovery on alternate array",
            risk=_M,
        ),
        ProcedureStep(
            step_number=4,
            command_id="CMD_POWER_SHED_NONESSENTIAL",
            description=(
                "Shed non-critical loads to preserve remaining battery capacity."
            ),
            wait_seconds=10,
            verification="SoC stabilizes",
            risk=_L,
        ),
        ProcedureStep(
            step_number=5,
            command_id="CMD_SAFE_MODE_EXIT",
            description=(
                "Only after power generation is confirmed restored."
            ),
            wait_seconds=30,
            verification="V_bat > 27V and rising",
            risk=_L,
        ),
    ),
    preconditions=(
        "I_sa dropped to near 0A while spacecraft is sunlit",
        "V_bat drifting downward from nominal",
        "Spacecraft is in safe mode",
    ),
    postconditions=(
        "I_sa > 2A",
        "V_bat > 27V and rising",
        "SoC stabilized",
        "normal_mode_flag = 1",
    ),
    risk=_M,
    source=_FB_SOURCE,
    source_version=_FB_VERSION,
    section=_FB_SECTION,
    clause=_FB_CLAUSE,
    provenance=_FB_PROVENANCE,
    trigger_cues=(
        "I_sa", "V_bat", "V_bus", "SoC", "solar", "SOLAR_ARRAY",
        "EPS", "battery", "power", "undervolt", "eclipse",
        "sunlit", "sun_angle", "EPS_FAULT",
    ),
)


# ═══════════════════════════════════════════════════════════════════════════
# PROCEDURE 3 — OBC Watchdog Overflow Recovery
# ═══════════════════════════════════════════════════════════════════════════

_PROC_OBC_WATCHDOG = ProcedureDefinition(
    procedure_id="PROC-OBC-WATCHDOG-001",
    title="OBC Software Watchdog Overflow Recovery",
    subsystem=SubsystemID.OBC,
    fault_class="OBC_WATCHDOG_OVERFLOW",
    steps=(
        ProcedureStep(
            step_number=1,
            command_id="CMD_CONFIRM_COMMS_LOCK",
            description=(
                "Verify communications lock on low-gain antenna "
                "BEFORE any OBC operations."
            ),
            wait_seconds=5,
            verification="TRANSPONDER_LOCK = 1",
            risk=_L,
        ),
        ProcedureStep(
            step_number=2,
            command_id="CMD_OBC_CONTROLLED_REBOOT",
            description=(
                "Perform a clean controlled reboot of the onboard computer "
                "(not a power cycle)."
            ),
            wait_seconds=60,
            verification="CPU_LOAD returns to nominal (< 70%)",
            risk=_M,
        ),
        ProcedureStep(
            step_number=3,
            command_id="CMD_VERIFY_MEMORY_STATE",
            description=(
                "Check that memory usage is stable "
                "(not monotonically increasing)."
            ),
            wait_seconds=10,
            verification="memory_usage stable",
            risk=_L,
        ),
        ProcedureStep(
            step_number=4,
            command_id="CMD_SAFE_MODE_EXIT",
            description="Return to nominal operations.",
            wait_seconds=30,
            verification="normal_mode_flag = 1",
            risk=_L,
        ),
    ),
    preconditions=(
        "CPU_LOAD at 100%",
        "WATCHDOG_COUNTER overflowed",
        "Spacecraft is in safe mode",
    ),
    postconditions=(
        "CPU_LOAD < 70%",
        "memory_usage stable",
        "normal_mode_flag = 1",
    ),
    risk=_M,
    source=_FB_SOURCE,
    source_version=_FB_VERSION,
    section=_FB_SECTION,
    clause=_FB_CLAUSE,
    provenance=_FB_PROVENANCE,
    trigger_cues=(
        "CPU_LOAD", "WATCHDOG_COUNTER", "watchdog", "OBC",
        "software", "reboot", "memory", "CPU", "100%",
        "overflow", "loop", "infinite", "hang",
        "OBC_WATCHDOG",
    ),
)


# ═══════════════════════════════════════════════════════════════════════════
# PROCEDURE 4 — TCS Thermal Runaway Recovery
# ═══════════════════════════════════════════════════════════════════════════

_PROC_TCS_THERMAL = ProcedureDefinition(
    procedure_id="PROC-TCS-THERMAL-001",
    title="TCS Thermal Runaway — Heater Stuck ON Recovery",
    subsystem=SubsystemID.TCS,
    fault_class="TCS_THERMAL_RUNAWAY",
    steps=(
        ProcedureStep(
            step_number=1,
            command_id="CMD_DISABLE_HEATER_ZONE",
            description=(
                "Disable the affected heater zone immediately.  "
                "Thermal runaway is time-critical — this is the first action."
            ),
            wait_seconds=5,
            verification="HEATER_ZONE status = OFF",
            risk=_L,
        ),
        ProcedureStep(
            step_number=2,
            command_id="CMD_MONITOR_TEMPERATURE",
            description=(
                "Monitor component temperature for cooling trend."
            ),
            wait_seconds=120,
            verification="TEMP reading is decreasing",
            risk=_L,
        ),
        ProcedureStep(
            step_number=3,
            command_id="CMD_VERIFY_THERMAL_MARGIN",
            description=(
                "Confirm temperature has returned to safe operating range."
            ),
            wait_seconds=300,
            verification="TEMP < 50°C for electronics",
            risk=_L,
        ),
        ProcedureStep(
            step_number=4,
            command_id="CMD_SAFE_MODE_EXIT",
            description=(
                "Only after temperature is within safe range."
            ),
            wait_seconds=30,
            verification="normal_mode_flag = 1",
            risk=_L,
        ),
    ),
    preconditions=(
        "HEATER_ZONE_* stuck ON",
        "Component temperature rising beyond operational range",
        "Spacecraft is in safe mode",
    ),
    postconditions=(
        "HEATER_ZONE status = OFF",
        "Temperature within safe operating range",
        "normal_mode_flag = 1",
    ),
    risk=_L,
    source=_FB_SOURCE,
    source_version=_FB_VERSION,
    section=_FB_SECTION,
    clause=_FB_CLAUSE,
    provenance=_FB_PROVENANCE,
    trigger_cues=(
        "TEMP_OBC", "TEMP_", "HEATER_ZONE", "thermal", "TCS",
        "temperature", "heater", "overheating", "stuck_on",
        "85", "survival", "runaway",
    ),
)


# ═══════════════════════════════════════════════════════════════════════════
# PROCEDURE 5 — COMMS Transponder Loss Recovery
# ═══════════════════════════════════════════════════════════════════════════

_PROC_COMMS_TRANSPONDER = ProcedureDefinition(
    procedure_id="PROC-COMMS-TRANSPONDER-001",
    title="COMMS Transponder Loss Recovery",
    subsystem=SubsystemID.COMMS,
    fault_class="COMMS_TRANSPONDER_LOSS",
    steps=(
        ProcedureStep(
            step_number=1,
            command_id="CMD_SWITCH_BACKUP_TRANSPONDER",
            description="Switch to redundant transponder unit.",
            wait_seconds=30,
            verification="TRANSPONDER_LOCK = 1 on backup unit",
            risk=_M,
        ),
        ProcedureStep(
            step_number=2,
            command_id="CMD_VERIFY_SIGNAL_ACQUISITION",
            description="Confirm signal quality with ground station.",
            wait_seconds=15,
            verification="SNR > 10 dB",
            risk=_L,
        ),
        ProcedureStep(
            step_number=3,
            command_id="CMD_CONFIRM_GROUND_CONTACT",
            description=(
                "Verify two-way communication link is established."
            ),
            wait_seconds=10,
            verification="Ground station confirms telemetry reception",
            risk=_L,
        ),
        ProcedureStep(
            step_number=4,
            command_id="CMD_SAFE_MODE_EXIT",
            description="Return to nominal operations.",
            wait_seconds=30,
            verification="normal_mode_flag = 1",
            risk=_L,
        ),
    ),
    preconditions=(
        "TRANSPONDER_LOCK dropped to 0",
        "SNR below 5 dB",
        "Spacecraft may be in safe mode",
    ),
    postconditions=(
        "TRANSPONDER_LOCK = 1",
        "SNR > 10 dB",
        "Two-way communication confirmed",
        "normal_mode_flag = 1",
    ),
    risk=_M,
    source=_FB_SOURCE,
    source_version=_FB_VERSION,
    section=_FB_SECTION,
    clause=_FB_CLAUSE,
    provenance=_FB_PROVENANCE,
    trigger_cues=(
        "TRANSPONDER_LOCK", "SNR", "COMMS", "transponder",
        "signal", "antenna", "comm", "communication",
        "loss_of_signal", "dB",
    ),
)


# ═══════════════════════════════════════════════════════════════════════════
# PROCEDURE 6 — Multi-Subsystem Cascade
# ═══════════════════════════════════════════════════════════════════════════

_PROC_MULTI_CASCADE = ProcedureDefinition(
    procedure_id="PROC-MULTI-CASCADE-001",
    title="Multi-Subsystem Cascade Failure Diagnosis",
    subsystem=SubsystemID.SYSTEM,
    fault_class="MULTI_CASCADE",
    steps=(
        ProcedureStep(
            step_number=1,
            command_id="CMD_HEALTH_CHECK",
            description=(
                "Run a spacecraft-wide health check to identify all "
                "affected subsystems and the temporal order of faults."
            ),
            wait_seconds=10,
            verification="Health summary across all subsystems returned",
            risk=_L,
        ),
        ProcedureStep(
            step_number=2,
            command_id="CMD_TELEMETRY_DUMP",
            description=(
                "Downlink the stored telemetry buffer to identify the "
                "initiating fault from the event timeline."
            ),
            wait_seconds=15,
            verification="Stored telemetry queued for downlink",
            risk=_L,
        ),
        ProcedureStep(
            step_number=3,
            command_id="CMD_VERIFY_STATUS",
            description=(
                "Read the overall spacecraft status word to confirm "
                "which subsystems are still nominal."
            ),
            wait_seconds=5,
            verification="Status word returned",
            risk=_L,
        ),
    ),
    preconditions=(
        "Anomalies in 2+ subsystems with temporal correlation",
        "Most recent anomaly may be symptom, not root cause",
    ),
    postconditions=(
        "Initiating fault identified",
        "Affected subsystems catalogued",
        "Recovery order determined",
    ),
    risk=_L,
    source=_FB_SOURCE,
    source_version=_FB_VERSION,
    section=_FB_SECTION,
    clause=_FB_CLAUSE,
    provenance=_FB_PROVENANCE,
    trigger_cues=(
        "cascade", "multi", "multiple", "chain", "propagat",
        "two subsystem", "cross-subsystem", "secondary",
        "downstream", "initiating",
    ),
)


# ═══════════════════════════════════════════════════════════════════════════
# REGISTRIES
# ═══════════════════════════════════════════════════════════════════════════

_ALL_PROCEDURES: tuple[ProcedureDefinition, ...] = (
    _PROC_ADCS_SEU,
    _PROC_EPS_UNDERVOLT,
    _PROC_OBC_WATCHDOG,
    _PROC_TCS_THERMAL,
    _PROC_COMMS_TRANSPONDER,
    _PROC_MULTI_CASCADE,
)

#: The procedure library, keyed by procedure_id.  Authoritative.
PROCEDURE_LIBRARY: dict[str, ProcedureDefinition] = {
    p.procedure_id: p for p in _ALL_PROCEDURES
}

# Fail on duplicate procedure_id (mirrors command_registry.py pattern).
if len(PROCEDURE_LIBRARY) != len(_ALL_PROCEDURES):
    _seen: set[str] = set()
    _dupes = sorted({
        p.procedure_id for p in _ALL_PROCEDURES
        if p.procedure_id in _seen or _seen.add(p.procedure_id)  # type: ignore[func-returns-value]
    })
    raise RuntimeError(
        f"Duplicate procedure_id in PROCEDURE_LIBRARY: {_dupes}"
    )

#: Lookup by fault_class for fast retrieval.
PROCEDURE_BY_FAULT: dict[str, ProcedureDefinition] = {
    p.fault_class: p for p in _ALL_PROCEDURES
}

#: The citation registry, keyed by citation_id.
CITATION_REGISTRY: dict[str, Citation] = {}
for _proc in _ALL_PROCEDURES:
    _cit = _cite(_proc.procedure_id)
    if _cit.citation_id in CITATION_REGISTRY:
        raise RuntimeError(
            f"Duplicate citation_id: {_cit.citation_id}"
        )
    CITATION_REGISTRY[_cit.citation_id] = _cit

#: Lookup citation by procedure_id for convenience.
CITATION_BY_PROCEDURE: dict[str, Citation] = {
    cit.procedure_id: cit for cit in CITATION_REGISTRY.values()
}


# ═══════════════════════════════════════════════════════════════════════════
# IMPORT-TIME VALIDATION — command registry consistency
# ═══════════════════════════════════════════════════════════════════════════

def _validate_command_references() -> None:
    """Verify every command_id in every procedure step exists in COMMAND_REGISTRY.

    Raises RuntimeError if any drift is detected.  This mirrors the contract
    enforced by conflicts.py for the old fallback KB text.
    """
    errors: list[str] = []
    for proc in _ALL_PROCEDURES:
        for step in proc.steps:
            if step.command_id not in COMMAND_REGISTRY:
                errors.append(
                    f"{proc.procedure_id} step {step.step_number}: "
                    f"command_id '{step.command_id}' not in COMMAND_REGISTRY"
                )
    if errors:
        raise RuntimeError(
            f"Procedure library has {len(errors)} command registry "
            f"violation(s):\n  " + "\n  ".join(errors)
        )


_validate_command_references()
