"""early_warning.py

SENTINEL — Early Warning Anomaly Detector

Scans a pre-fault telemetry window and emits EarlyWarningAlert objects
when parameters deviate beyond thresholds. Uses the existing z-score
anomaly detector infrastructure where possible.

This module works for both:
  - Synthetic crash dumps (known parameter names → fault type heuristic)
  - ESA-ADB real telemetry (anonymized channel_* names → generic alert)

Design rules:
  - Pure Python. No AI calls. No LLM dependency. No new packages.
  - Never crashes on missing/malformed data.
  - If a parameter name is anonymized (channel_*), report it honestly
    without claiming a specific spacecraft subsystem.

Public API:
  scan_telemetry(crash_dump, z_threshold=3.0) -> list[EarlyWarningAlert]
"""

from __future__ import annotations

import math
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

class EarlyWarningAlert(BaseModel):
    """A single early-warning alert emitted before safe-mode entry."""

    warning_offset: str = Field(
        ...,
        description=(
            "Time offset from event start when the anomaly was first detected, "
            "e.g. 'T-120.0s'"
        ),
    )
    anomalous_parameters: list[str] = Field(
        default_factory=list,
        description="Parameter names that triggered the alert.",
    )
    max_z_score: float | None = Field(
        default=None,
        description="Largest |z-score| among the anomalous parameters (None if z-score unavailable).",
    )
    suspected_fault_type: str = Field(
        ...,
        description=(
            "Best-guess fault type based on parameter heuristic. "
            "Set to 'UNKNOWN' for anonymized channels."
        ),
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Heuristic confidence in the suspected fault type (0.0–1.0).",
    )
    message: str = Field(
        ...,
        min_length=1,
        description="Human-readable description of the alert.",
    )


# ---------------------------------------------------------------------------
# Parameter → fault type heuristic mapping (synthetic parameters only)
# ---------------------------------------------------------------------------

# Maps known SENTINEL synthetic parameter names to canonical fault types.
# Anonymized ESA-ADB channel names (channel_*) deliberately excluded —
# we don't fabricate subsystem mappings for anonymized data.
_PARAMETER_FAULT_MAP: dict[str, tuple[str, float]] = {
    # (fault_type, confidence)
    "Gyro_rate_degs":       ("ADCS_GYRO_SEU", 0.85),
    "SEU_counter":          ("ADCS_GYRO_SEU", 0.90),
    "Attitude_error_deg":   ("ADCS_GYRO_SEU", 0.70),
    "RW_speed_rpm":         ("ADCS_GYRO_SEU", 0.55),

    "V_bat":                ("EPS_SOLAR_UNDERVOLT", 0.80),
    "I_sa":                 ("EPS_SOLAR_UNDERVOLT", 0.85),
    "SoC_pct":              ("EPS_SOLAR_UNDERVOLT", 0.80),
    "V_bus":                ("EPS_SOLAR_UNDERVOLT", 0.65),

    "CPU_load_pct":         ("OBC_WATCHDOG_OVERFLOW", 0.85),
    "Memory_usage_MB":      ("OBC_WATCHDOG_OVERFLOW", 0.75),
    "Watchdog_counter":     ("OBC_WATCHDOG_OVERFLOW", 0.90),

    "Component_temp_C":     ("TCS_THERMAL_RUNAWAY", 0.85),
    "Heater_power_W":       ("TCS_THERMAL_RUNAWAY", 0.75),
    "OBC_temp_C":           ("TCS_THERMAL_RUNAWAY", 0.55),

    "Transponder_lock":     ("COMMS_TRANSPONDER_LOSS", 0.80),
    "SNR_dB":               ("COMMS_TRANSPONDER_LOSS", 0.75),
}


def _is_nan_like(value: Any) -> bool:
    """Check if a value is NaN or NaN-like."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().upper() in ("NAN", "NONE", "", "N/A", "NULL")
    if isinstance(value, float):
        return math.isnan(value)
    return False


def _compute_z(value: float, nominal_min: float, nominal_max: float) -> float | None:
    """Compute a z-score proxy from nominal bounds.

    Uses (nominal_min + nominal_max) / 2 as mean and
    (nominal_max - nominal_min) / 6 as std (3-sigma band).
    Returns None if bounds are degenerate.
    """
    span = nominal_max - nominal_min
    if span <= 0:
        return None
    mean = (nominal_min + nominal_max) / 2.0
    std = span / 6.0  # nominal range ≈ ±3σ
    if std == 0:
        return None
    return (value - mean) / std


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_telemetry(
    crash_dump: dict[str, Any],
    z_threshold: float = 3.0,
) -> list[EarlyWarningAlert]:
    """Scan pre-fault telemetry and emit early-warning alerts.

    Looks at the ``pre_fault_telemetry`` list in the crash dump. For each
    parameter reading that is out-of-nominal or explicitly marked anomalous,
    computes a z-score proxy and groups by time offset.

    Args:
        crash_dump: Crash dump dict (synthetic or ESA-ADB format).
        z_threshold: Z-score magnitude threshold for flagging (default 3.0).

    Returns:
        List of EarlyWarningAlert objects, sorted by time offset (earliest first).
    """
    # Phase 3: read the CANONICAL window. This used to read only the deprecated
    # ``pre_fault_telemetry`` field, so channels carried solely in
    # ``pre_fault_telemetry_window`` never produced an early-warning alert.
    try:
        from app.api.adapters import canonical_window_dicts

        telemetry = canonical_window_dicts(crash_dump)
    except Exception:
        telemetry = crash_dump.get("pre_fault_telemetry", [])

    if not telemetry:
        return []

    # Group anomalous readings by time offset
    # Each entry: (offset_str, parameter, z_score)
    anomalies_by_offset: dict[str, list[tuple[str, float | None]]] = {}

    for reading in telemetry:
        if not isinstance(reading, dict):
            continue

        param = reading.get("parameter", "")
        value = reading.get("value")
        nominal_min = reading.get("nominal_min")
        nominal_max = reading.get("nominal_max")
        is_anomalous = reading.get("anomalous", False)
        offset = reading.get("timestamp_offset", reading.get("timestamp", "T-0s"))

        # Check if out-of-nominal
        value_is_nan = _is_nan_like(value)
        out_of_bounds = False
        z_score: float | None = None

        if not value_is_nan and nominal_min is not None and nominal_max is not None:
            try:
                fval = float(value)
                fmin = float(nominal_min)
                fmax = float(nominal_max)
                if fval < fmin or fval > fmax:
                    out_of_bounds = True
                z_score = _compute_z(fval, fmin, fmax)
            except (ValueError, TypeError):
                pass

        # Flag if: explicitly anomalous, NaN, out-of-bounds, or high z-score
        flagged = (
            is_anomalous
            or value_is_nan
            or out_of_bounds
            or (z_score is not None and abs(z_score) >= z_threshold)
        )

        if flagged:
            if offset not in anomalies_by_offset:
                anomalies_by_offset[offset] = []
            anomalies_by_offset[offset].append((param, z_score))

    if not anomalies_by_offset:
        return []

    # Build alerts sorted by offset
    alerts: list[EarlyWarningAlert] = []

    for offset, param_scores in sorted(
        anomalies_by_offset.items(),
        key=lambda item: _parse_offset_seconds(item[0]),
    ):
        params = [p for p, _ in param_scores]
        z_scores = [abs(z) for _, z in param_scores if z is not None]
        max_z = max(z_scores) if z_scores else None

        # Determine suspected fault type by voting
        fault_type, confidence = _vote_fault_type(params)

        message_parts = [
            f"Early warning at {offset}:",
            f"{len(params)} parameter(s) anomalous",
            f"[{', '.join(params[:5])}]",
        ]
        if max_z is not None:
            message_parts.append(f"max |z|={max_z:.1f}")
        message_parts.append(f"→ suspected {fault_type} (conf={confidence:.0%})")

        alerts.append(EarlyWarningAlert(
            warning_offset=offset,
            anomalous_parameters=params,
            max_z_score=round(max_z, 3) if max_z is not None else None,
            suspected_fault_type=fault_type,
            confidence=round(confidence, 2),
            message=" ".join(message_parts),
        ))

    return alerts


def _vote_fault_type(params: list[str]) -> tuple[str, float]:
    """Vote on the most likely fault type from a list of anomalous parameters.

    Returns (fault_type, confidence). If no known parameters match, returns
    ("UNKNOWN", 0.30) for anonymized channels or ("MULTI_CASCADE", 0.40)
    for a mix of known subsystems.
    """
    if not params:
        return ("UNKNOWN", 0.20)

    votes: dict[str, list[float]] = {}
    unknown_count = 0

    for param in params:
        if param in _PARAMETER_FAULT_MAP:
            ft, conf = _PARAMETER_FAULT_MAP[param]
            if ft not in votes:
                votes[ft] = []
            votes[ft].append(conf)
        else:
            unknown_count += 1

    if not votes:
        # All parameters are anonymized (ESA-ADB) or unrecognized
        return ("UNKNOWN", 0.30)

    # If multiple subsystems are implicated, it's a cascade
    if len(votes) > 1:
        # Average the top two confidences, discounted
        all_confs = [max(confs) for confs in votes.values()]
        all_confs.sort(reverse=True)
        avg = sum(all_confs[:2]) / 2.0
        return ("MULTI_CASCADE", round(min(avg * 0.6, 0.55), 2))

    # Single fault type
    fault_type = list(votes.keys())[0]
    max_conf = max(votes[fault_type])
    # Discount if many unknowns mixed in
    if unknown_count > len(params) * 0.5:
        max_conf *= 0.7
    return (fault_type, min(max_conf, 0.95))


def _parse_offset_seconds(offset: str) -> float:
    """Parse a time offset string like 'T-120.5s' or 'T+0.000s' to float seconds."""
    try:
        s = offset.strip()
        if s.startswith("T"):
            s = s[1:]
        if s.endswith("s"):
            s = s[:-1]
        return float(s)
    except (ValueError, AttributeError):
        return 0.0
