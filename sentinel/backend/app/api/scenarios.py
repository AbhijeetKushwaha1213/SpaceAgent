"""
SENTINEL — Preset Crash Dump Scenarios (scenarios.py)

Provides the demo UI with ready-made crash dump payloads that match the
CrashDumpRequest schema defined in models.py.

Each scenario is a complete dict that FastAPI can validate against
CrashDumpRequest.  The frontend fetches these via GET /scenarios and
renders them in the scenario selector dropdown.
"""

from __future__ import annotations

from typing import Any


def get_preset_scenarios() -> list[dict[str, Any]]:
    """Return the built-in demo scenarios.

    Each entry mirrors the CrashDumpRequest schema.  Legacy fields
    (pre_fault_telemetry, event_log, hardware_state, operating_context)
    are preserved so the existing frontend renders them correctly.
    """
    return [
        # ── Scenario 1: ADCS Gyroscope SEU ────────────────────────────────
        {
            "scenario_id": 1,
            "fault_type": "ADCS_SENSOR_FAULT",
            "incident_id": "INC-2026-0001",
            "fault_register": "0x00000080",
            "safe_mode_trigger": "ADCS_ERROR",
            "telecommand_context": {
                "event_id": 1,
                "telecommand": "telecommand_12",
                "execution_timestamp": "2026-06-12T03:47:23Z",
                "gap_seconds": 62.0,
                "gap_classification": "burst",
                "gap_percentile": 8.4,
                "anomaly_flag": True,
            },
            "pre_fault_telemetry_window": [
                {"timestamp": "T-120s", "parameter": "Gyro_rate_degs",
                 "value": 0.5, "status": "NOMINAL"},
                {"timestamp": "T-60s", "parameter": "Gyro_rate_degs",
                 "value": None, "status": "CRITICAL"},
                {"timestamp": "T-60s", "parameter": "SEU_counter",
                 "value": 3.0, "status": "ANOMALOUS"},
                {"timestamp": "T-30s", "parameter": "Attitude_error_deg",
                 "value": 7.3, "status": "CRITICAL"},
                {"timestamp": "T-10s", "parameter": "V_bat",
                 "value": 30.2, "status": "NOMINAL"},
            ],
            # --- Legacy fields consumed by the existing frontend ---
            "pre_fault_telemetry": [
                {"parameter": "Gyro_rate_degs", "value": "NaN",
                 "nominal_min": 0.0, "nominal_max": 7.0},
                {"parameter": "Attitude_error_deg", "value": 7.3,
                 "nominal_min": 0.0, "nominal_max": 0.01},
                {"parameter": "SEU_counter", "value": 3.0,
                 "nominal_min": 0.0, "nominal_max": 0.0},
                {"parameter": "RW_speed_rpm", "value": 4500.0,
                 "nominal_min": -6000.0, "nominal_max": 6000.0},
                {"parameter": "V_bat", "value": 30.2,
                 "nominal_min": 28.0, "nominal_max": 33.6},
                {"parameter": "SoC_pct", "value": 85.0,
                 "nominal_min": 20.0, "nominal_max": 100.0},
                {"parameter": "I_sa", "value": 8.4,
                 "nominal_min": 0.0, "nominal_max": 12.0},
                {"parameter": "OBC_temp_C", "value": 24.5,
                 "nominal_min": -10.0, "nominal_max": 60.0},
            ],
            "event_log": [
                {"timestamp": "T-62s", "source": "OBC_KERNEL",
                 "message": "SEU counter incremented: 3"},
                {"timestamp": "T-60s", "source": "ADCS_MANAGER",
                 "message": "GYRO_A health status: NaN"},
                {"timestamp": "T-30s", "source": "ADCS_ATTITUDE",
                 "message": "Attitude error exceeded threshold (7.3 deg)"},
                {"timestamp": "T-0s", "source": "FDIR_CORE",
                 "message": "Safe Mode entry triggered by ADCS_ERROR"},
            ],
            "hardware_state": {
                "active_gyro": "A",
                "seu_flags": "0x03",
                "watchdog_status": "nominal",
            },
            "operating_context": {
                "eclipse_fraction": 0.0,
                "sun_sensor_angle_deg": 12.5,
                "time_since_contact_s": 1200,
            },
        },
        # ── Scenario 2: EPS Power Fault ───────────────────────────────────
        {
            "scenario_id": 2,
            "fault_type": "EPS_POWER_FAULT",
            "incident_id": "INC-2026-0002",
            "fault_register": "0x00000002",
            "safe_mode_trigger": "EPS_UNDER_VOLT",
            "telecommand_context": {
                "event_id": 2,
                "telecommand": "telecommand_45",
                "execution_timestamp": "2026-06-12T04:10:00Z",
                "gap_seconds": 300.0,
                "gap_classification": "stale",
                "gap_percentile": 92.1,
                "anomaly_flag": True,
            },
            "pre_fault_telemetry_window": [
                {"timestamp": "T-300s", "parameter": "I_sa",
                 "value": 0.0, "status": "CRITICAL"},
                {"timestamp": "T-180s", "parameter": "V_bat",
                 "value": 21.8, "status": "CRITICAL"},
                {"timestamp": "T-180s", "parameter": "SoC_pct",
                 "value": 14.2, "status": "CRITICAL"},
                {"timestamp": "T-120s", "parameter": "V_bus",
                 "value": 24.1, "status": "ANOMALOUS"},
                {"timestamp": "T-10s", "parameter": "OBC_temp_C",
                 "value": 18.2, "status": "NOMINAL"},
            ],
            "pre_fault_telemetry": [
                {"parameter": "I_sa", "value": 0.0,
                 "nominal_min": 0.0, "nominal_max": 12.0},
                {"parameter": "V_bat", "value": 21.8,
                 "nominal_min": 28.0, "nominal_max": 33.6},
                {"parameter": "SoC_pct", "value": 14.2,
                 "nominal_min": 20.0, "nominal_max": 100.0},
                {"parameter": "V_bus", "value": 24.1,
                 "nominal_min": 26.6, "nominal_max": 29.4},
                {"parameter": "Heater_power_W", "value": 15.0,
                 "nominal_min": 0.0, "nominal_max": 50.0},
                {"parameter": "Attitude_error_deg", "value": 0.004,
                 "nominal_min": 0.0, "nominal_max": 0.01},
                {"parameter": "OBC_temp_C", "value": 18.2,
                 "nominal_min": -10.0, "nominal_max": 60.0},
            ],
            "event_log": [
                {"timestamp": "T-300s", "source": "EPS_SENSORS",
                 "message": "Solar Array A Current dropped to 0A (expected: 8.5A)"},
                {"timestamp": "T-180s", "source": "EPS_MANAGER",
                 "message": "State of Charge low (14.2%). Starting load shedding."},
                {"timestamp": "T-120s", "source": "OBC_CORE",
                 "message": "Command issued: Power off PYLD subsystem"},
                {"timestamp": "T-0s", "source": "FDIR_CORE",
                 "message": "Safe Mode entry triggered by EPS_UNDER_VOLT"},
            ],
            "hardware_state": {
                "solar_relay": "open",
                "battery_relays": "closed",
                "shed_status": "active",
            },
            "operating_context": {
                "eclipse_fraction": 0.0,
                "sun_sensor_angle_deg": 42.0,
                "time_since_contact_s": 2400,
            },
        },
        # ── Scenario 3: OBC Software Fault ────────────────────────────────
        {
            "scenario_id": 3,
            "fault_type": "OBC_SOFTWARE_FAULT",
            "incident_id": "INC-2026-0003",
            "fault_register": "0x00000040",
            "safe_mode_trigger": "WATCHDOG_RESET",
            "telecommand_context": {
                "event_id": 3,
                "telecommand": "telecommand_01",
                "execution_timestamp": "2026-06-12T04:25:55Z",
                "gap_seconds": 10.0,
                "gap_classification": "nominal",
                "gap_percentile": 50.0,
                "anomaly_flag": False,
            },
            "pre_fault_telemetry_window": [
                {"timestamp": "T-180s", "parameter": "CPU_load_pct",
                 "value": 100.0, "status": "CRITICAL"},
                {"timestamp": "T-120s", "parameter": "Memory_usage_MB",
                 "value": 495.0, "status": "ANOMALOUS"},
                {"timestamp": "T-10s", "parameter": "Watchdog_counter",
                 "value": 1002.0, "status": "CRITICAL"},
                {"timestamp": "T-10s", "parameter": "V_bat",
                 "value": 31.1, "status": "NOMINAL"},
            ],
            "pre_fault_telemetry": [
                {"parameter": "CPU_load_pct", "value": 100.0,
                 "nominal_min": 0.0, "nominal_max": 70.0},
                {"parameter": "Memory_usage_MB", "value": 495.0,
                 "nominal_min": 0.0, "nominal_max": 500.0},
                {"parameter": "Watchdog_counter", "value": 1002.0,
                 "nominal_min": 0.0, "nominal_max": 1000.0},
                {"parameter": "V_bat", "value": 31.1,
                 "nominal_min": 28.0, "nominal_max": 33.6},
                {"parameter": "SoC_pct", "value": 90.0,
                 "nominal_min": 20.0, "nominal_max": 100.0},
                {"parameter": "Attitude_error_deg", "value": 0.003,
                 "nominal_min": 0.0, "nominal_max": 0.01},
            ],
            "event_log": [
                {"timestamp": "T-180s", "source": "OBC_MONITOR",
                 "message": "CPU load exceeded 95%"},
                {"timestamp": "T-120s", "source": "OBC_MONITOR",
                 "message": "Memory leak signature detected in thread 'attitude_control'"},
                {"timestamp": "T-10s", "source": "WATCHDOG_TIMER",
                 "message": "Watchdog counter exceeded limit (value=1002)"},
                {"timestamp": "T-0s", "source": "OBC_BOOT",
                 "message": "Watchdog reset triggered. Booting in Safe Mode."},
            ],
            "hardware_state": {
                "watchdog_state": "expired",
                "active_bank": "EEPROM_B",
                "last_reboot_cause": "WATCHDOG_RESET",
            },
            "operating_context": {
                "eclipse_fraction": 0.2,
                "sun_sensor_angle_deg": 15.0,
                "time_since_contact_s": 50,
            },
        },
    ]
