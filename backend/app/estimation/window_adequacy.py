"""
SENTINEL — Telemetry Window Adequacy Contract (estimation/window_adequacy.py)

Phase 15. The explicit contract between a telemetry window and the physics
layer: what a window must contain before a physical consistency check may be
claimed to have happened at all.

The five statuses
-----------------
``ADEQUATE_FOR_PHYSICS``       at least one modelled channel can be stepped:
                               two freshly reported, timed samples with a
                               positive dt.
``UNDER_SAMPLED``              modelled channels ARE present, but none is
                               reported freshly at two or more timed samples,
                               so no prediction can be stepped.
``MISSING_REQUIRED_CHANNELS``  no modelled channel appears in the window at
                               all. Nothing the physics layer consumes was
                               measured.
``INVALID_TIMESTAMPS``         timestamps cannot support a forward step: an
                               unparseable offset, or a modelled channel whose
                               fresh samples are not strictly increasing.
``CONTRADICTORY_DATA``         the same channel reports two different usable
                               values at the same instant. The window is
                               self-contradictory and cannot be trusted.

Anything other than ``ADEQUATE_FOR_PHYSICS`` is surfaced as
``UNDER_SAMPLED_FOR_PHYSICS``: the window was inspected, physics did NOT run
on it, and nothing was checked. A dump that cannot be checked must say so —
silence must never read as a clean bill of physical health.

Freshness is exactly what ``StateSequence.fresh_states_for`` defines: the
reading must be usable (a finite number) and measured AT the state's own
time, never carried forward. This module deliberately builds its verdict on
the same state sequence the residuals consume, so the contract and the
physics layer cannot disagree about what was present.

Nothing here invents data. A window that lacks samples, timestamps or
modelled channels is reported as lacking them; it is never padded.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from app.estimation.state import MODELLED_CHANNELS

WINDOW_ADEQUACY_VERSION = "1.0.0"

#: What each physics model needs from the window. The predicted channel must be
#: freshly reported at two or more timed samples; the auxiliary channels are
#: consumed (carried within their staleness budget) by the model's prediction.
MODEL_REQUIREMENTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Gyro_rate_degs", "attitude.predict_angular_velocity",
     ("RW_speed_rpm",)),
    ("Attitude_error_deg", "attitude.predict_attitude_error",
     ("Gyro_rate_degs",)),
    ("SoC_pct", "power.predict_state_of_charge",
     ("I_sa", "V_bus", "Heater_power_W")),
    ("V_bat", "power.predict_terminal_voltage",
     ("SoC_pct", "I_sa", "V_bus", "Heater_power_W")),
    ("Component_temp_C", "thermal.predict_component_temperature",
     ("Heater_power_W",)),
)

_REQUIREMENT_BY_CHANNEL = {name: (model, aux)
                           for name, model, aux in MODEL_REQUIREMENTS}

_UNDER_SAMPLED = "UNDER_SAMPLED_FOR_PHYSICS"


class WindowAdequacyStatus(str, Enum):
    ADEQUATE_FOR_PHYSICS = "ADEQUATE_FOR_PHYSICS"
    UNDER_SAMPLED = "UNDER_SAMPLED"
    MISSING_REQUIRED_CHANNELS = "MISSING_REQUIRED_CHANNELS"
    INVALID_TIMESTAMPS = "INVALID_TIMESTAMPS"
    CONTRADICTORY_DATA = "CONTRADICTORY_DATA"


@dataclass
class ChannelAdequacy:
    """What the window offers one modelled channel."""

    channel: str
    model: str
    fresh_sample_count: int
    sample_times_s: list[Optional[float]]
    last_step_dt_s: Optional[float]
    checkable: bool
    rows_in_window: bool = False
    """Whether the channel appears in the window at all, usable or not."""
    auxiliary_channels: tuple[str, ...] = ()
    auxiliary_present: dict[str, bool] = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "model": self.model,
            "fresh_sample_count": self.fresh_sample_count,
            "sample_times_s": self.sample_times_s,
            "last_step_dt_s": self.last_step_dt_s,
            "checkable": self.checkable,
            "rows_in_window": self.rows_in_window,
            "auxiliary_channels": list(self.auxiliary_channels),
            "auxiliary_present": self.auxiliary_present,
            "note": self.note,
        }


@dataclass
class WindowAdequacyReport:
    """The adequacy verdict for one crash dump's telemetry window."""

    status: WindowAdequacyStatus
    channels: list[ChannelAdequacy]
    untimed_or_unparseable: int
    contradictions: list[dict[str, Any]]
    summary: str
    warnings: tuple[str, ...] = ()

    @property
    def is_adequate(self) -> bool:
        return self.status is WindowAdequacyStatus.ADEQUATE_FOR_PHYSICS

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_adequacy_version": WINDOW_ADEQUACY_VERSION,
            "window_adequacy_status": self.status.value,
            "adequate_for_physics": self.is_adequate,
            "channels": [c.as_dict() for c in self.channels],
            "untimed_or_unparseable_rows": self.untimed_or_unparseable,
            "contradictory_readings": self.contradictions,
            "warnings": list(self.warnings),
            "summary": self.summary,
            "policy": (
                "A window that cannot support a forward step on a modelled "
                "channel is reported UNDER_SAMPLED_FOR_PHYSICS. It is never "
                "presented as a physical consistency check, and no residual is "
                "claimed for it."
            ),
        }


def _usable(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    return False


def _summary(
    status: WindowAdequacyStatus,
    checkable: list[str],
    present: list[str],
    contradictions: list[dict[str, Any]],
    unparseable: int,
) -> str:
    if status is WindowAdequacyStatus.ADEQUATE_FOR_PHYSICS:
        return (
            f"Adequate for physics: {len(checkable)} modelled channel(s) can "
            f"be stepped ({', '.join(sorted(checkable))})."
        )
    if status is WindowAdequacyStatus.CONTRADICTORY_DATA:
        return (
            f"{_UNDER_SAMPLED}: {len(contradictions)} contradictory reading(s) "
            f"make the window self-inconsistent; no physical claim is made."
        )
    if status is WindowAdequacyStatus.INVALID_TIMESTAMPS:
        return (
            f"{_UNDER_SAMPLED}: timestamps do not support a forward step "
            f"({unparseable} unparseable offset(s)); no physical claim is made."
        )
    if status is WindowAdequacyStatus.MISSING_REQUIRED_CHANNELS:
        return (
            f"{_UNDER_SAMPLED}: no modelled channel is present in the window "
            f"at all; no physical claim is made."
        )
    return (
        f"{_UNDER_SAMPLED}: {len(present)} modelled channel(s) present but none "
        f"reported freshly at two or more timed samples; no physical claim is "
        f"made."
    )


def assess_window_adequacy(
    crash_dump: Optional[dict[str, Any]],
    state_sequence: Any = None,
) -> WindowAdequacyReport:
    """Assess whether a crash dump's window can support physics at all.

    Args:
        crash_dump: Any crash dump dict. None or malformed input yields a
            ``MISSING_REQUIRED_CHANNELS`` report rather than raising.
        state_sequence: A pre-built ``StateSequence``, to avoid re-estimating
            when the caller already has one. When omitted, the sequence is
            estimated here through the same canonical adapter the residuals use.

    Returns:
        A ``WindowAdequacyReport``. Deterministic — no randomness, no model
        call, no network access.
    """
    if not isinstance(crash_dump, dict):
        return WindowAdequacyReport(
            status=WindowAdequacyStatus.MISSING_REQUIRED_CHANNELS,
            channels=[],
            untimed_or_unparseable=0,
            contradictions=[],
            summary=(
                f"{_UNDER_SAMPLED}: no crash dump was supplied, so there is no "
                f"window to assess."
            ),
        )

    rows: list[dict[str, Any]] = []
    try:
        from app.api.adapters import canonical_window_dicts

        rows = canonical_window_dicts(crash_dump)
    except Exception:  # pragma: no cover — adapter is in-tree
        return WindowAdequacyReport(
            status=WindowAdequacyStatus.MISSING_REQUIRED_CHANNELS,
            channels=[],
            untimed_or_unparseable=0,
            contradictions=[],
            summary=(
                f"{_UNDER_SAMPLED}: the telemetry window could not be read, so "
                f"nothing was assessed."
            ),
        )

    if state_sequence is None:
        try:
            from app.estimation.state import estimate_states

            state_sequence = estimate_states(crash_dump)
        except Exception:  # pragma: no cover — estimation is in-tree
            state_sequence = None

    # ── Scan the raw window for integrity problems ──────────────────────────
    # Alias resolution must match estimate_states() exactly, otherwise the
    # contract could flag a channel the estimator never sees.
    from app.ingest.channel_dict import get_channel

    contradictions: list[dict[str, Any]] = []
    unparseable = 0
    by_channel_time: dict[str, dict[str, list[Any]]] = {}
    resolved_rows: list[tuple[str, dict[str, Any]]] = []

    for row in rows:
        raw_name = row.get("parameter")
        if not raw_name:
            continue
        definition = get_channel(raw_name)
        channel = definition.channel_id if definition else str(raw_name)
        resolved_rows.append((channel, row))
        timestamp = str(row.get("timestamp") or "UNKNOWN")
        value = row.get("value")

        relative = row.get("relative_time_s")
        if not (
            isinstance(relative, (int, float))
            and math.isfinite(float(relative))
        ):
            unparseable += 1

        if _usable(value):
            by_channel_time.setdefault(channel, {}).setdefault(
                timestamp, []).append(float(value))

    for channel, times in by_channel_time.items():
        for timestamp, values in times.items():
            distinct = sorted({v for v in values if _usable(v)})
            if len(distinct) > 1:
                contradictions.append({
                    "channel": channel,
                    "timestamp": timestamp,
                    "values": distinct,
                })

    # ── Per modelled channel: what the residuals will actually see ──────────
    # Only the PREDICTED channels need stepping. The auxiliary channels in
    # ``MODELLED_CHANNELS`` (RW_speed_rpm, V_bus, I_sa, Heater_power_W,
    # Heater_enable_flag) are consumed by a prediction, never predicted
    # themselves, so they are reported as auxiliary presence per channel.
    rows_present: set[str] = {
        resolved for resolved, _row in resolved_rows
        if resolved in _REQUIREMENT_BY_CHANNEL
    }
    channels: list[ChannelAdequacy] = []
    for channel, (model, aux) in _REQUIREMENT_BY_CHANNEL.items():
        fresh_times: list[Optional[float]] = []
        if state_sequence is not None:
            try:
                fresh_times = [
                    s.relative_time_s for s in
                    state_sequence.fresh_states_for(channel)
                ]
            except Exception:  # pragma: no cover — sequence is in-tree
                fresh_times = []

        dt: Optional[float] = None
        if len(fresh_times) >= 2:
            previous, current = fresh_times[-2], fresh_times[-1]
            if previous is not None and current is not None:
                dt = float(current) - float(previous)

        present_aux = {
            name: any(
                _usable(row.get("value"))
                for resolved, row in resolved_rows
                if resolved == name
            )
            for name in aux
        }

        note = ""
        if len(fresh_times) < 2:
            note = (
                f"reported freshly at {len(fresh_times)} timed sample(s); a "
                f"step needs two."
            )
        elif dt is not None and dt <= 0.0:
            note = f"the last two fresh samples are {dt:.1f}s apart, not a forward step."
        elif dt is not None:
            note = f"steppable: last step dt={dt:.1f}s."

        channels.append(ChannelAdequacy(
            channel=channel,
            model=model,
            fresh_sample_count=len(fresh_times),
            sample_times_s=fresh_times,
            last_step_dt_s=dt,
            checkable=(len(fresh_times) >= 2 and dt is not None and dt > 0.0),
            rows_in_window=channel in rows_present,
            auxiliary_channels=aux,
            auxiliary_present=present_aux,
            note=note,
        ))

    # ── Aggregate ───────────────────────────────────────────────────────────
    present = [c for c in channels if c.rows_in_window]
    checkable = [c.channel for c in channels if c.checkable]

    if contradictions:
        status = WindowAdequacyStatus.CONTRADICTORY_DATA
    elif not present:
        status = WindowAdequacyStatus.MISSING_REQUIRED_CHANNELS
    elif unparseable:
        status = WindowAdequacyStatus.INVALID_TIMESTAMPS
    elif any(
        c.fresh_sample_count >= 2 and c.last_step_dt_s is not None
        and c.last_step_dt_s <= 0.0
        for c in present
    ):
        status = WindowAdequacyStatus.INVALID_TIMESTAMPS
    elif not checkable:
        status = WindowAdequacyStatus.UNDER_SAMPLED
    else:
        status = WindowAdequacyStatus.ADEQUATE_FOR_PHYSICS

    warnings: list[str] = []
    if unparseable:
        warnings.append(
            f"{unparseable} row(s) carry an unparseable timestamp and cannot "
            f"participate in any step."
        )
    if contradictions:
        warnings.append(
            f"{len(contradictions)} contradictory reading(s) found; the window "
            f"cannot be trusted as given."
        )
    for channel in present:
        if not channel.checkable:
            warnings.append(f"{channel.channel}: {channel.note}")
    if not present:
        warnings.append(
            "No modelled channel is present in this window. This is the "
            "expected outcome for anonymized ESA-ADB channels, which carry no "
            "subsystem or physical meaning."
        )

    return WindowAdequacyReport(
        status=status,
        channels=channels,
        untimed_or_unparseable=unparseable,
        contradictions=contradictions,
        summary=_summary(status, checkable,
                         [c.channel for c in present], contradictions,
                         unparseable),
        warnings=tuple(warnings),
    )


def empty_window_adequacy() -> WindowAdequacyReport:
    """The default for a report that was never assessed."""
    return WindowAdequacyReport(
        status=WindowAdequacyStatus.MISSING_REQUIRED_CHANNELS,
        channels=[],
        untimed_or_unparseable=0,
        contradictions=[],
        summary=f"{_UNDER_SAMPLED}: no window was assessed.",
    )


def window_adequacy_status() -> dict[str, Any]:
    """Describe the contract, for the API and tests."""
    return {
        "window_adequacy_version": WINDOW_ADEQUACY_VERSION,
        "statuses": [s.value for s in WindowAdequacyStatus],
        "under_sampled_phrase": _UNDER_SAMPLED,
        "modelled_channel_requirements": [
            {
                "channel": name,
                "model": model,
                "auxiliary_channels": list(aux),
            }
            for name, model, aux in MODEL_REQUIREMENTS
        ],
        "freshness_rule": (
            "A fresh sample is a usable (finite) reading measured at its "
            "state's own time, exactly as StateSequence.fresh_states_for "
            "defines. Carried-forward values never count."
        ),
        "claim": (
            "The adequacy contract says whether physics MAY run on a window, "
            "not what physics concludes. ADEQUATE_FOR_PHYSICS does not mean "
            "healthy; UNDER_SAMPLED_FOR_PHYSICS means nothing was checked."
        ),
    }