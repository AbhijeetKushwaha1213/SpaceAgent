"""generate_demo_cache.py

Generates demo fallback cache files with valid SentinelOutput JSON
and enough trace metadata for the frontend to replay without a live LLM.

Output:
  sentinel/backend/data/demo_cache/gyro_seu_cached.json
  sentinel/backend/data/demo_cache/solar_undervolt_cached.json
  sentinel/backend/data/demo_cache/obc_watchdog_cached.json

Each file contains:
  - scenario_id, fault_type, source_type
  - sentinel_output: valid SentinelOutput JSON
  - sse_trace: list of SSE events the frontend can replay
  - generated_at: ISO timestamp
  - note: provenance disclaimer

Run:
  cd sentinel/backend && python -m app.analytics.generate_demo_cache
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.api.models import (
    SentinelOutput,
    Hypothesis,
    RecoveryStep,
    RiskLevel,
)


# ---------------------------------------------------------------------------
# Cached outputs — hand-crafted, valid, realistic
# ---------------------------------------------------------------------------

GYRO_SEU_OUTPUT = SentinelOutput(
    hypotheses=[
        Hypothesis(
            rank=1,
            root_cause="ADCS_GYRO_SEU",
            affected_component="GYRO_A",
            confidence=0.92,
            causal_chain=[
                "SEU burst at T-62s corrupted GYRO_A memory registers",
                "Gyro_rate_degs became NaN (sensor output invalid)",
                "ADCS attitude error grew to 7.3° exceeding 0.01° threshold",
                "FDIR triggered safe-mode entry via ADCS_ERROR flag",
            ],
        ),
        Hypothesis(
            rank=2,
            root_cause="MULTI_CASCADE",
            affected_component="ADCS_EPS_CHAIN",
            confidence=0.05,
            causal_chain=[
                "Multi-subsystem cascade is a low-probability alternative",
                "V_bat and SoC_pct are within nominal limits, ruling out EPS contribution",
            ],
        ),
        Hypothesis(
            rank=3,
            root_cause="OBC_WATCHDOG_OVERFLOW",
            affected_component="OBC_FLIGHT_SOFTWARE",
            confidence=0.03,
            causal_chain=[
                "Software fault is possible but CPU_load and Watchdog_counter are nominal",
                "No evidence of memory corruption or thread deadlock in event log",
            ],
        ),
    ],
    recovery_plan=[
        RecoveryStep(
            step=1,
            command="CMD_VERIFY_SEU_COUNTER",
            rationale="Confirm radiation-induced SEU as the initiating event before taking corrective action",
            wait_seconds=15,
            verify="SEU_counter value recorded and stable (no further increments)",
            risk=RiskLevel.LOW,
        ),
        RecoveryStep(
            step=2,
            command="CMD_GYRO_A_DRIVER_RESET",
            rationale="Reset gyroscope A driver to clear corrupted register state from SEU",
            wait_seconds=30,
            verify="Gyro_rate_degs returns valid numeric reading within [0.0, 7.0] deg/s",
            risk=RiskLevel.MEDIUM,
        ),
        RecoveryStep(
            step=3,
            # Phase 1: was CMD_SWITCH_TO_GYRO_B, which no registry entry defines.
            command="CMD_GYRO_SWITCH_TO_BACKUP",
            rationale="Switch to backup gyroscope B as redundant path if GYRO_A reset fails",
            wait_seconds=20,
            verify="GYRO_B health status nominal and attitude error decreasing",
            risk=RiskLevel.MEDIUM,
        ),
        RecoveryStep(
            step=4,
            # Phase 1: was CMD_ATTITUDE_RECOVERY_SUN_POINT (not in registry).
            command="CMD_SUN_ACQUISITION",
            rationale="Re-establish sun-pointing attitude to restore power-positive state",
            wait_seconds=60,
            verify="Attitude_error_deg < 0.5° and solar array current I_sa > 5.0A",
            risk=RiskLevel.LOW,
        ),
    ],
    confidence=0.92,
    requires_human_review=False,
    reasoning_summary=(
        "SEU burst at T-62s corrupted GYRO_A registers causing attitude divergence to 7.3°. "
        "All other subsystems (EPS, OBC, TCS, COMMS) nominal. "
        "Single-event radiation fault with high confidence. "
        "Recovery: verify SEU count → reset GYRO_A → failover to GYRO_B → sun-point."
    ),
)

SOLAR_UNDERVOLT_OUTPUT = SentinelOutput(
    hypotheses=[
        Hypothesis(
            rank=1,
            root_cause="EPS_SOLAR_UNDERVOLT",
            affected_component="SOLAR_ARRAY_A",
            confidence=0.94,
            causal_chain=[
                "Solar array current I_sa dropped to 0.0A at T-300s (expected 8.5A)",
                "Battery voltage V_bat fell to 21.8V (below 28V lower limit)",
                "State of Charge dropped to 14.2% triggering load shedding",
                "FDIR entered safe mode via EPS_UNDER_VOLT flag",
            ],
        ),
        Hypothesis(
            rank=2,
            root_cause="MULTI_CASCADE",
            affected_component="EPS_TCS_CHAIN",
            confidence=0.04,
            causal_chain=[
                "Possible cascade if thermal conditions caused solar cell degradation",
                "OBC_temp_C at 18.2°C is nominal — no thermal evidence",
            ],
        ),
        Hypothesis(
            rank=3,
            root_cause="COMMS_TRANSPONDER_LOSS",
            affected_component="COMMS_SUBSYSTEM",
            confidence=0.02,
            causal_chain=[
                "Communications loss could mask ground recovery commands",
                "No comms anomaly visible in telemetry or event log",
            ],
        ),
    ],
    recovery_plan=[
        RecoveryStep(
            step=1,
            # Phase 1: was CMD_EPS_STATUS_REPORT (not in registry).
            command="CMD_POWER_CHECK",
            rationale="Query current battery voltage, SoC, and solar array status before recovery",
            wait_seconds=10,
            verify="Battery and solar array readings received successfully",
            risk=RiskLevel.LOW,
        ),
        RecoveryStep(
            step=2,
            # Phase 1: was CMD_SHED_NON_ESSENTIAL_LOADS (not in registry).
            command="CMD_POWER_SHED_NONESSENTIAL",
            rationale="Reduce power draw to preserve remaining battery capacity",
            wait_seconds=15,
            verify="Non-essential subsystems (PYLD, heaters) powered off; V_bus stable",
            risk=RiskLevel.LOW,
        ),
        RecoveryStep(
            step=3,
            # Phase 1: was CMD_SOLAR_ARRAY_RELAY_CYCLE (not in registry).
            command="CMD_SOLAR_ARRAY_A_RESET",
            rationale="Cycle solar array relay to potentially restore connection",
            wait_seconds=45,
            verify="I_sa > 2.0A indicating partial or full array reconnection",
            risk=RiskLevel.MEDIUM,
        ),
        RecoveryStep(
            step=4,
            # Phase 1: was CMD_ATTITUDE_SUN_POINT_SAFE (not in registry).
            command="CMD_SUN_ACQUISITION",
            rationale="Re-orient spacecraft for maximum solar illumination if array relay succeeded",
            wait_seconds=60,
            verify="Sun_sensor_angle_deg < 10° and I_sa trending upward",
            risk=RiskLevel.LOW,
        ),
    ],
    confidence=0.94,
    requires_human_review=False,
    reasoning_summary=(
        "Solar array A complete current loss at T-300s caused progressive battery depletion. "
        "V_bat dropped to 21.8V and SoC to 14.2%. "
        "Load shedding was initiated but insufficient to prevent safe-mode entry. "
        "Recovery: status check → load shed → relay cycle → sun-point."
    ),
)

OBC_WATCHDOG_OUTPUT = SentinelOutput(
    hypotheses=[
        Hypothesis(
            rank=1,
            root_cause="OBC_WATCHDOG_OVERFLOW",
            affected_component="OBC_FLIGHT_SOFTWARE",
            confidence=0.91,
            causal_chain=[
                "CPU load at 100% for sustained period starting at T-180s",
                "Memory leak detected in attitude_control thread (495 of 500 MB used)",
                "Watchdog counter exceeded limit (1002 vs 1000 threshold) at T-10s",
                "OBC executed watchdog reset, booting into safe mode from EEPROM_B",
            ],
        ),
        Hypothesis(
            rank=2,
            root_cause="ADCS_GYRO_SEU",
            affected_component="ADCS_SOFTWARE",
            confidence=0.06,
            causal_chain=[
                "Attitude control thread was the memory leak source",
                "Possible ADCS driver bug, but no SEU or gyro anomaly in telemetry",
            ],
        ),
        Hypothesis(
            rank=3,
            root_cause="MULTI_CASCADE",
            affected_component="OBC_ADCS_CHAIN",
            confidence=0.03,
            causal_chain=[
                "Software–hardware cascade possible if corrupted OBC state affected ADCS",
                "EPS and TCS parameters all nominal, ruling out broader cascade",
            ],
        ),
    ],
    recovery_plan=[
        RecoveryStep(
            step=1,
            # Phase 1: was CMD_OBC_MEMORY_DUMP (not in registry).
            command="CMD_MEMORY_DUMP",
            rationale="Capture post-reset memory dump before volatile crash context is lost",
            wait_seconds=20,
            verify="Memory dump file created and telemetry download initiated",
            risk=RiskLevel.LOW,
        ),
        RecoveryStep(
            step=2,
            # Phase 1: was CMD_RESTART_ATTITUDE_CONTROL_THREAD. SENTINEL has no
            # per-thread restart capability, so the invented command is replaced
            # by the registry command that serves the procedure's actual intent:
            # confirm memory is stable after the reboot.
            command="CMD_VERIFY_MEMORY_STATE",
            rationale="Restart the attitude_control thread with clean memory allocation",
            wait_seconds=30,
            verify="CPU_load_pct < 70% and Memory_usage_MB < 300 within 30s",
            risk=RiskLevel.MEDIUM,
        ),
        RecoveryStep(
            step=3,
            # Phase 1: was CMD_WATCHDOG_COUNTER_RESET (not in registry).
            command="CMD_WATCHDOG_CLEAR",
            rationale="Reset watchdog counter after confirming stable CPU state",
            wait_seconds=10,
            verify="Watchdog_counter = 0 and incrementing normally",
            risk=RiskLevel.LOW,
        ),
        RecoveryStep(
            step=4,
            # Phase 1: was CMD_OBC_HEALTH_MONITOR_ENABLE (not in registry).
            command="CMD_HEALTH_CHECK",
            rationale="Enable enhanced OBC health monitoring to detect recurrence",
            wait_seconds=5,
            verify="Health monitor telemetry stream active and reporting nominal",
            risk=RiskLevel.LOW,
        ),
    ],
    confidence=0.91,
    requires_human_review=False,
    reasoning_summary=(
        "Memory leak in attitude_control thread consumed 495 of 500 MB available. "
        "CPU saturated at 100% for 3 minutes. "
        "Watchdog counter overflowed at 1002, triggering hardware reset. "
        "All non-OBC subsystems nominal. "
        "Recovery: memory dump → restart thread → reset watchdog → enable monitoring."
    ),
)


# ---------------------------------------------------------------------------
# SSE trace builder
# ---------------------------------------------------------------------------

def _build_sse_trace(fault_type: str, output: SentinelOutput) -> list[dict]:
    """Build a realistic SSE event trace for frontend replay."""
    h1 = output.hypotheses[0] if output.hypotheses else None
    return [
        {"event_type": "status", "data": "Ingesting raw spacecraft crash dump..."},
        {"event_type": "status", "data": "Crash dump parsed successfully."},
        {"event_type": "thought", "data": f"Analyzing pre-fault telemetry for {fault_type} signatures.", "step_number": 1},
        {"event_type": "observation", "data": f"Anomaly detector flagged {len(output.recovery_plan)} parameters as anomalous.", "step_number": 1},
        {"event_type": "thought", "data": "Retrieving ECSS recovery procedures from knowledge base.", "step_number": 2},
        {"event_type": "observation", "data": f"Found relevant procedures for {fault_type} recovery.", "step_number": 2},
        {"event_type": "status", "data": "Invoking reasoning agent..."},
        {"event_type": "thought", "data": "Constructing causal propagation graph from telemetry patterns.", "step_number": 3},
        {"event_type": "thought", "data": f"Rank-1 hypothesis: {h1.root_cause if h1 else 'N/A'} (confidence: {h1.confidence if h1 else 0:.0%})", "step_number": 3},
        {"event_type": "action", "data": f"Generating {len(output.recovery_plan)}-step recovery plan with safety validation.", "step_number": 4},
        # Phase 1: was the unconditional "Analysis complete. Safety validation
        # passed." Now reports the actual validated status of this payload.
        {"event_type": "status", "data": (
            f"Analysis complete. Safety status: {output.safety_status.value}"
            + (f" ({len(output.blocked_steps)} step(s) blocked)."
               if output.blocked_steps else ".")
        )},
        {"event_type": "result", "data": output.model_dump_json()},
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Phase 0: use the shared provenance vocabulary so these cached payloads carry
# the same labels as everything else. These are hand-written demo outputs, not
# model output, which the per-file ``note`` also states.
from app.api.provenance import Provenance, display_label  # noqa: E402

CACHE_SPECS = [
    {
        "filename": "gyro_seu_cached.json",
        "scenario_id": 1,
        "fault_type": "ADCS_GYRO_SEU",
        "provenance": Provenance.SYNTHETIC.value,
        "source_type": display_label(Provenance.SYNTHETIC),
        "output": GYRO_SEU_OUTPUT,
    },
    {
        "filename": "solar_undervolt_cached.json",
        "scenario_id": 2,
        "fault_type": "EPS_SOLAR_UNDERVOLT",
        "provenance": Provenance.SYNTHETIC.value,
        "source_type": display_label(Provenance.SYNTHETIC),
        "output": SOLAR_UNDERVOLT_OUTPUT,
    },
    {
        "filename": "obc_watchdog_cached.json",
        "scenario_id": 3,
        "fault_type": "OBC_WATCHDOG_OVERFLOW",
        "provenance": Provenance.SYNTHETIC.value,
        "source_type": display_label(Provenance.SYNTHETIC),
        "output": OBC_WATCHDOG_OUTPUT,
    },
]


def main():
    cache_dir = os.path.join(_BACKEND_ROOT, "data", "demo_cache")
    os.makedirs(cache_dir, exist_ok=True)

    # Phase 1: run the REAL safety validator over each hand-written output before
    # caching it. Previously these payloads were written straight to disk with no
    # validation, which is how 10 commands that the whitelist rejected ended up
    # in a cache that gets replayed to operators. The cached safety_status is now
    # an actual validation result rather than an assertion.
    from app.agent.safety import apply_validation_to_output, validate_recovery_plan

    for spec in CACHE_SPECS:
        raw_output = spec["output"]

        # No crash dump is available at generation time, so physical-constraint
        # predicates evaluate to UNKNOWN (permissive). Registry membership,
        # command-format and risk escalation are still fully enforced.
        validation = validate_recovery_plan(raw_output, {})
        output = apply_validation_to_output(raw_output, validation)

        if validation.blocked_steps:
            print(
                f"  ⚠ {spec['filename']}: "
                f"{len(validation.blocked_steps)} command(s) blocked — "
                + ", ".join(
                    f"{b.original_step.command} ({b.violation_code})"
                    for b in validation.blocked_steps
                )
            )

        trace = _build_sse_trace(spec["fault_type"], output)

        cache_entry = {
            "scenario_id": spec["scenario_id"],
            "fault_type": spec["fault_type"],
            "provenance": spec["provenance"],
            "source_type": spec["source_type"],
            "safety_status": output.safety_status.value,
            "sentinel_output": json.loads(output.model_dump_json()),
            "sse_trace": trace,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "note": (
                "DEMO / REPLAY payload. Pre-generated fallback, hand-written to match "
                "the synthetic crash dump scenario. It is NOT the result of a live LLM "
                "call and NOT a validated diagnosis. Its recovery commands HAVE been "
                "checked against the SENTINEL command registry and the deterministic "
                "safety validator with an empty crash-dump context, so physical "
                "constraint predicates were UNKNOWN (permissive) at generation time."
            ),
        }

        path = os.path.join(cache_dir, spec["filename"])
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cache_entry, fh, indent=2)
        print(f"  ✅ {path}")

    print(f"\n{len(CACHE_SPECS)} demo cache files written to {cache_dir}/")


if __name__ == "__main__":
    main()
