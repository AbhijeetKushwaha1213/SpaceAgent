"""
SENTINEL — Spacecraft State (app/estimation/state.py)

Phase 7. Turns a crash dump's telemetry window into a time-ordered sequence of
``SpacecraftState`` snapshots that the simplified models can predict forward from.

NOT flight software, NOT flight-qualified, NOT a model of any specific mission.

The honesty problem this module solves
--------------------------------------
A state vector is a very inviting place to fabricate. The obvious implementation
declares a full three-axis attitude quaternion and body rate vector, and then
quietly fills the axes telemetry does not carry with zeros. Downstream, a zero is
indistinguishable from a measurement, so the residuals would be computed against
invented values and reported with the same confidence as real ones.

What the telemetry actually carries is ONE scalar body rate (``Gyro_rate_degs``),
ONE scalar wheel speed (``RW_speed_rpm``) and ONE scalar pointing error
(``Attitude_error_deg``). There is no axis label on any of them and no second
axis anywhere in the channel dictionary.

So every quantity here is an ``Estimate`` that knows whether it was OBSERVED,
DERIVED, ASSUMED or UNAVAILABLE, and the attitude state is explicitly
single-axis. A three-axis model is not approximated, it is declined — with the
reason recorded in ``StateSequence.limitations`` so it reaches the operator
rather than living in this docstring.

What a state snapshot is
------------------------
One telemetry sample time, with the channels that were reported at it. It is not
a filtered estimate: there is no Kalman filter, no smoothing and no sensor
fusion. Values are the readings, and ``DERIVED`` marks the few quantities
computed from them by an explicit formula (wheel momentum from wheel speed, for
instance). Calling this "estimation" is conventional for the pipeline stage; the
estimator is close to an identity function, and saying otherwise would overstate
it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from app.estimation.parameters import (
    RPM_TO_RAD_PER_S,
    WHEEL_TO_BODY_INERTIA_RATIO,
)

STATE_SCHEMA_VERSION = "1.0.0"
"""Version of the state representation. Bump when a field is added or removed."""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — QUANTITY PROVENANCE
# ═══════════════════════════════════════════════════════════════════════════

class QuantitySource(str, Enum):
    """Where one quantity in a state snapshot came from.

    The same tri-state discipline the rest of SENTINEL uses: Phase 1's condition
    evaluator, Phase 2's baselines and Phase 6's evidence states all distinguish
    "we measured this", "we worked it out" and "we do not know". A state vector
    without that distinction is the easiest place in the system to launder an
    assumption into a measurement.
    """

    OBSERVED = "OBSERVED"
    """Read directly from a telemetry channel at this sample time."""

    CARRIED_FORWARD = "CARRIED_FORWARD"
    """Last known value, measured at an EARLIER sample time and still the most
    recent reading available.

    Necessary rather than convenient. Telemetry in this repository is
    asynchronous: preset scenario 1 reports the gyro alone at T-120s, a single
    battery voltage at T-10s, and eight channels at T-0s. Channels are sampled at
    the different cadences the dictionary declares, so a snapshot built only from
    what arrived at one instant is almost empty and nothing can be predicted
    from it.

    A carried value is NOT labelled OBSERVED. It records ``as_of_s`` and
    ``staleness_s``, and a model refuses it once it is older than the cadence its
    channel declares — see ``STALENESS_BUDGET_S``.
    """

    DERIVED = "DERIVED"
    """Computed from observed values by a stated formula in this module."""

    ASSUMED = "ASSUMED"
    """Taken from parameters.py because telemetry does not carry it."""

    UNAVAILABLE = "UNAVAILABLE"
    """Not reported and not derivable. The value is None.

    Never silently replaced by a default. A model asked to predict from an
    UNAVAILABLE quantity declines to predict, which is why residuals can come
    back UNDECIDABLE instead of confidently wrong.
    """

    @property
    def is_usable(self) -> bool:
        return self is not QuantitySource.UNAVAILABLE


#: How stale a carried-forward reading may be before a model must refuse it,
#: per the cadence class the channel declares in the Phase 5 dictionary.
#:
#: Each budget is tied to the band that class documents, so these are read off
#: the dictionary's own definitions rather than chosen:
#:
#:   HIGH_RATE    band "sub-second to ~1 s"        -> 10 s, ten times the slow end
#:   MEDIUM_RATE  band "~1 s to ~30 s"             -> 60 s, twice the slow end
#:   LOW_RATE     band "~30 s to a few minutes"    -> 300 s, the top of that
#:   ON_CHANGE    "emitted when the value changes" -> no limit; see below
#:   UNKNOWN      no declared cadence              -> 0 s, fresh readings only
#:
#: ON_CHANGE is unlimited by SEMANTICS, not by leniency. A channel that reports
#: only on change is asserting that its last reported value is still current —
#: that is what on-change reporting means. Expiring it after some interval would
#: discard a valid state and, for a flag like Heater_enable_flag, would throw away
#: the only record of what the heater was told to do.
STALENESS_BUDGET_S: dict[str, Optional[float]] = {
    "HIGH_RATE": 10.0,
    "MEDIUM_RATE": 60.0,
    "LOW_RATE": 300.0,
    "ON_CHANGE": None,
    "UNKNOWN": 0.0,
}


def staleness_budget(channel: str) -> Optional[float]:
    """Staleness budget for a channel, or None when unlimited.

    An unrecognised channel gets the UNKNOWN budget of zero, so only a fresh
    reading is acceptable. Granting an unknown channel a generous budget would
    mean carrying a value forward on no declared basis at all.
    """
    from app.ingest.channel_dict import get_channel

    definition = get_channel(channel)
    if definition is None:
        return STALENESS_BUDGET_S["UNKNOWN"]
    return STALENESS_BUDGET_S.get(
        definition.sampling_rate.value, STALENESS_BUDGET_S["UNKNOWN"])


@dataclass(frozen=True)
class Estimate:
    """One scalar quantity, its unit, and where and when it came from."""

    value: Optional[float]
    unit: str
    source: QuantitySource
    channel: Optional[str] = None
    """The telemetry channel this came from, for an OBSERVED quantity."""

    notes: Optional[str] = None

    as_of_s: Optional[float] = None
    """When the value was actually MEASURED, in the same relative seconds as the
    state's own time. Equal to the state time for a fresh reading, earlier for a
    carried-forward one."""

    staleness_s: Optional[float] = None
    """State time minus ``as_of_s``. Zero for a fresh reading."""

    staleness_budget_s: Optional[float] = None
    """How stale this channel's readings may be, from its declared cadence.
    None means unlimited, which applies to on-change channels."""

    @property
    def is_usable(self) -> bool:
        """True when there is a finite number here that a model may use."""
        return (
            self.source.is_usable
            and self.value is not None
            and math.isfinite(self.value)
        )

    @property
    def is_fresh(self) -> bool:
        """True when this value was measured AT the state's own time.

        Required of any value used as the OBSERVATION in a residual. Comparing a
        carried-forward value against a prediction that started from the same
        carried value would produce a residual manufactured entirely by the
        model's own step — a finding with no measurement behind it.
        """
        return self.is_usable and (self.staleness_s or 0.0) <= 0.0

    @property
    def within_budget(self) -> bool:
        """True when this value is fresh enough for a model to consume.

        Always true for a fresh reading. For a carried-forward one, true while
        its age is inside the budget its declared cadence implies.
        """
        if not self.is_usable:
            return False
        if self.staleness_budget_s is None:
            return True
        return (self.staleness_s or 0.0) <= self.staleness_budget_s

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "unit": self.unit,
            "source": self.source.value,
            "channel": self.channel,
            "notes": self.notes,
            "as_of_s": self.as_of_s,
            "staleness_s": self.staleness_s,
            "staleness_budget_s": self.staleness_budget_s,
            "is_fresh": self.is_fresh,
            "within_budget": self.within_budget,
        }


def _unavailable(unit: str, channel: Optional[str] = None,
                 reason: str = "not reported in this telemetry sample",
                 ) -> Estimate:
    return Estimate(value=None, unit=unit, source=QuantitySource.UNAVAILABLE,
                    channel=channel, notes=reason)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — SUBSTATES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class AttitudeState:
    """Attitude and rate, as far as the telemetry supports.

    SINGLE AXIS. ``Gyro_rate_degs`` and ``Attitude_error_deg`` are unlabelled
    scalars in the channel dictionary, so there is no basis for assigning them to
    a body axis or for populating the other two. ``axis_label`` says so
    explicitly rather than leaving a reader to assume roll, pitch or yaw.
    """

    angular_velocity: Estimate
    """Body rate about the measured axis, deg/s. From Gyro_rate_degs."""

    attitude_error: Estimate
    """Angle between commanded and estimated attitude, deg."""

    sun_sensor_angle: Estimate
    star_tracker_status: Estimate
    """Health code; 0 is healthy per the channel dictionary."""

    axis_label: str = "UNLABELLED_SINGLE_AXIS"
    """No axis is claimed. The telemetry does not identify one."""

    axes_represented: int = 1
    axes_unavailable: int = 2

    def as_dict(self) -> dict[str, Any]:
        return {
            "angular_velocity": self.angular_velocity.as_dict(),
            "attitude_error": self.attitude_error.as_dict(),
            "sun_sensor_angle": self.sun_sensor_angle.as_dict(),
            "star_tracker_status": self.star_tracker_status.as_dict(),
            "axis_label": self.axis_label,
            "axes_represented": self.axes_represented,
            "axes_unavailable": self.axes_unavailable,
            "claim": (
                "Single-axis scalar state. No three-axis attitude is "
                "represented, because no channel in the dictionary carries a "
                "second axis. The two unrepresented axes are UNAVAILABLE, not "
                "zero."
            ),
        }


@dataclass(frozen=True)
class ReactionWheelState:
    """One reaction wheel, plus the momentum derived from its speed."""

    speed_rpm: Estimate
    speed_rad_s: Estimate
    """DERIVED: speed_rpm * 2*pi/60."""

    normalised_momentum: Estimate
    """DERIVED: wheel momentum as a fraction of what the vehicle's own rate limit
    corresponds to, using the derived inertia ratio. Dimensionless, and the
    quantity the attitude model actually exchanges."""

    saturation_fraction: Estimate
    """DERIVED: |speed| / declared hard limit. 1.0 means saturated, at which
    point the wheel can absorb no further disturbance torque."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "speed_rpm": self.speed_rpm.as_dict(),
            "speed_rad_s": self.speed_rad_s.as_dict(),
            "normalised_momentum": self.normalised_momentum.as_dict(),
            "saturation_fraction": self.saturation_fraction.as_dict(),
            "wheel_count_represented": 1,
            "claim": (
                "One wheel. A real vehicle carries three or four; the channel "
                "dictionary declares a single RW_speed_rpm, so no wheel "
                "distribution is represented."
            ),
        }


@dataclass(frozen=True)
class BatteryState:
    """Power and energy storage."""

    terminal_voltage: Estimate     # V_bat
    bus_voltage: Estimate          # V_bus
    state_of_charge: Estimate      # SoC_pct
    array_current: Estimate        # I_sa
    temperature: Estimate          # Battery_temp_C

    generation_power: Estimate
    """DERIVED: bus voltage * array current, W."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "terminal_voltage": self.terminal_voltage.as_dict(),
            "bus_voltage": self.bus_voltage.as_dict(),
            "state_of_charge": self.state_of_charge.as_dict(),
            "array_current": self.array_current.as_dict(),
            "temperature": self.temperature.as_dict(),
            "generation_power": self.generation_power.as_dict(),
        }


@dataclass(frozen=True)
class ThermalState:
    """The single lumped thermal node, plus the temperatures around it."""

    component_temperature: Estimate    # Component_temp_C — the modelled node
    heater_power: Estimate             # Heater_power_W
    heater_enabled: Estimate           # Heater_enable_flag
    radiator_efficiency: Estimate      # Radiator_eff_pct
    panel_temperature: Estimate        # Panel_temp_C
    obc_temperature: Estimate          # OBC_temp_C

    def as_dict(self) -> dict[str, Any]:
        return {
            "component_temperature": self.component_temperature.as_dict(),
            "heater_power": self.heater_power.as_dict(),
            "heater_enabled": self.heater_enabled.as_dict(),
            "radiator_efficiency": self.radiator_efficiency.as_dict(),
            "panel_temperature": self.panel_temperature.as_dict(),
            "obc_temperature": self.obc_temperature.as_dict(),
            "nodes_represented": 1,
            "modelled_node": "Component_temp_C",
            "claim": (
                "One lumped node. Panel, battery, OBC and transponder "
                "temperatures are carried for context but are NOT modelled, so "
                "no residual is produced for them."
            ),
        }


@dataclass(frozen=True)
class CommunicationState:
    """Link state. Carried in the state vector, not dynamically modelled.

    Included because the Phase 7 specification asks for it and because an
    operator reading a state snapshot needs to know whether the vehicle was in
    contact. No propagation model is offered: predicting a link budget needs
    antenna gain patterns, range and pointing geometry, none of which this
    repository contains. ``residuals.py`` therefore produces no COMMS residual,
    which is stated there rather than left to be inferred from its absence.
    """

    transponder_lock: Estimate
    link_status: Estimate
    snr: Estimate
    link_margin: Estimate
    rf_power: Estimate
    bit_error_rate: Estimate
    antenna_pointing_error: Estimate

    modelled: bool = False
    not_modelled_reason: str = (
        "No link budget model. Predicting SNR or margin requires antenna gain "
        "patterns, slant range and pointing geometry; none is available in this "
        "repository, so no COMMS prediction is attempted and no COMMS residual "
        "is reported."
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "transponder_lock": self.transponder_lock.as_dict(),
            "link_status": self.link_status.as_dict(),
            "snr": self.snr.as_dict(),
            "link_margin": self.link_margin.as_dict(),
            "rf_power": self.rf_power.as_dict(),
            "bit_error_rate": self.bit_error_rate.as_dict(),
            "antenna_pointing_error": self.antenna_pointing_error.as_dict(),
            "modelled": self.modelled,
            "not_modelled_reason": self.not_modelled_reason,
        }


@dataclass(frozen=True)
class SpacecraftState:
    """The simplified spacecraft state at one telemetry sample time."""

    timestamp: str
    """Relative offset as reported, e.g. 'T-120s'."""

    relative_time_s: Optional[float]
    """Parsed offset in seconds, negative before the fault. None when the sample
    carried no parseable time, in which case no model can step from it."""

    attitude: AttitudeState
    angular_velocity: Estimate
    """Promoted to the top level because the Phase 7 specification names it
    separately. The same Estimate as ``attitude.angular_velocity``."""

    reaction_wheel_state: ReactionWheelState
    battery_state: BatteryState
    thermal_state: ThermalState
    communication_state: CommunicationState

    observed_channels: tuple[str, ...] = ()
    """Channels that reported a usable value at this sample time."""

    unusable_channels: tuple[str, ...] = ()
    """Channels present but carrying NaN, Inf or a dropout. Distinct from simply
    absent: a dropout is a finding, an absence is not."""

    @property
    def has_time(self) -> bool:
        return (
            self.relative_time_s is not None
            and math.isfinite(self.relative_time_s)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "relative_time_s": self.relative_time_s,
            "attitude": self.attitude.as_dict(),
            "angular_velocity": self.angular_velocity.as_dict(),
            "reaction_wheel_state": self.reaction_wheel_state.as_dict(),
            "battery_state": self.battery_state.as_dict(),
            "thermal_state": self.thermal_state.as_dict(),
            "communication_state": self.communication_state.as_dict(),
            "observed_channels": list(self.observed_channels),
            "unusable_channels": list(self.unusable_channels),
        }


@dataclass
class StateSequence:
    """Time-ordered states from one crash dump, with what could not be built."""

    states: list[SpacecraftState] = field(default_factory=list)
    schema_version: str = STATE_SCHEMA_VERSION
    channels_seen: tuple[str, ...] = ()
    channels_modelled: tuple[str, ...] = ()
    channels_ignored: tuple[str, ...] = ()
    """Channels present in the dump that no model consumes. Listed so a reader
    can see the model's coverage rather than assuming it is total."""

    limitations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.states)

    @property
    def timed_states(self) -> list[SpacecraftState]:
        """States carrying a parseable time, in ascending time order.

        Models step between consecutive elements of THIS list, not of
        ``states``. A sample with no time cannot bound a dt, and guessing one
        would invent the very quantity the prediction divides by.
        """
        return sorted(
            (s for s in self.states if s.has_time),
            key=lambda s: s.relative_time_s,  # type: ignore[arg-type,return-value]
        )

    def fresh_states_for(self, channel: str) -> list[SpacecraftState]:
        """Timed states where ``channel`` was FRESHLY reported, in time order.

        The step grid a model should use for one channel. Telemetry here is
        asynchronous, so a single grid shared by every model is the wrong shape:
        preset scenario 1 reports the gyro at T-120s, T-60s and T-0s but the
        wheel only at T-0s, and forcing both onto the same steps would discard
        the gyro history that is actually present.

        Freshness is required at BOTH ends of a step. The prediction is anchored
        on the value at the start, and the value at the end is the observation it
        is compared against — if that were carried forward from the start, the
        residual would be produced entirely by the model's own step with no
        measurement behind it.
        """
        return [s for s in self.timed_states if channel in s.observed_channels]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state_count": len(self.states),
            "timed_state_count": len(self.timed_states),
            "channels_seen": list(self.channels_seen),
            "channels_modelled": list(self.channels_modelled),
            "channels_ignored": list(self.channels_ignored),
            "limitations": list(self.limitations),
            "warnings": list(self.warnings),
            "states": [s.as_dict() for s in self.states],
        }


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — READING TELEMETRY
# ═══════════════════════════════════════════════════════════════════════════

#: Channels each model consumes. Anything outside this set is reported in
#: ``channels_ignored`` rather than silently dropped, so the model's coverage is
#: visible instead of implied.
MODELLED_CHANNELS: tuple[str, ...] = (
    "Gyro_rate_degs", "Attitude_error_deg", "RW_speed_rpm",
    "V_bat", "V_bus", "SoC_pct", "I_sa",
    "Component_temp_C", "Heater_power_W", "Heater_enable_flag",
)

#: Carried in the state for context but not predicted.
CONTEXT_CHANNELS: tuple[str, ...] = (
    "Sun_sensor_angle_deg", "Star_tracker_status", "Battery_temp_C",
    "Radiator_eff_pct", "Panel_temp_C", "OBC_temp_C",
    "Transponder_lock", "Link_status", "SNR_dB", "Link_margin_dB",
    "RF_power_dBm", "Bit_error_rate", "Antenna_pointing_error_deg",
)


def _numeric(value: Any) -> Optional[float]:
    """Coerce a reading to a finite float, or None.

    None for NaN, Inf, a dropout marker or anything non-numeric. The caller
    distinguishes "absent" from "present but unusable" by checking whether the
    row existed at all, which is what keeps a gyro dropout visible as a finding
    rather than collapsing into a missing sample.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.upper() in (
            "NAN", "INF", "-INF", "NONE", "NULL", "MISSING", "N/A", "NA",
        ):
            return None
        try:
            parsed = float(text)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


@dataclass(frozen=True)
class _Reading:
    """One channel's value at one measured time, before carry-forward."""

    value: Optional[float]
    at_s: Optional[float]
    unusable: bool
    """True when the channel WAS reported but the value is NaN, Inf or a
    dropout. Distinct from absent: a dropout is a finding in itself."""


def _observed(readings: dict[str, "_Reading"], channel: str, unit: str,
              state_time_s: Optional[float]) -> Estimate:
    """Build an Estimate for one channel, fresh or carried forward."""
    reading = readings.get(channel)
    budget = staleness_budget(channel)

    if reading is None:
        return Estimate(
            value=None, unit=unit, source=QuantitySource.UNAVAILABLE,
            channel=channel, staleness_budget_s=budget,
            notes=(
                "no reading for this channel at or before this sample time"
            ),
        )

    if reading.unusable or reading.value is None:
        return Estimate(
            value=None, unit=unit, source=QuantitySource.UNAVAILABLE,
            channel=channel, as_of_s=reading.at_s, staleness_budget_s=budget,
            notes=(
                "channel reported but the value is unusable (NaN, Inf or a "
                "dropout); this is a finding, not an absent sample"
            ),
        )

    staleness: Optional[float] = None
    if state_time_s is not None and reading.at_s is not None:
        staleness = state_time_s - reading.at_s

    fresh = staleness is not None and staleness <= 0.0
    source = (QuantitySource.OBSERVED if fresh
              else QuantitySource.CARRIED_FORWARD)
    notes = None
    if not fresh and staleness is not None:
        notes = (
            f"last known value, measured {staleness:.0f}s earlier at "
            f"t={reading.at_s:.0f}s"
        )

    return Estimate(
        value=reading.value, unit=unit, source=source, channel=channel,
        notes=notes, as_of_s=reading.at_s,
        staleness_s=0.0 if fresh else staleness,
        staleness_budget_s=budget,
    )


def _derived(value: Optional[float], unit: str, formula: str) -> Estimate:
    if value is None or not math.isfinite(value):
        return Estimate(value=None, unit=unit,
                        source=QuantitySource.UNAVAILABLE,
                        notes=f"cannot derive: {formula}")
    return Estimate(value=value, unit=unit, source=QuantitySource.DERIVED,
                    notes=formula)


def _unit_of(channel: str, fallback: str = "") -> str:
    """Unit from the channel dictionary, so units are never restated here."""
    from app.ingest.channel_dict import get_channel

    definition = get_channel(channel)
    return (definition.unit or fallback) if definition else fallback


def _build_state(timestamp: str, relative_time_s: Optional[float],
                 readings: dict[str, "_Reading"]) -> SpacecraftState:
    """Assemble one snapshot at one sample time.

    ``readings`` holds the most recent reading of every channel at or before this
    time, so a value may be fresh or carried forward. Each Estimate records which
    it is.
    """
    from app.ingest.channel_dict import hard_limits

    def obs(channel: str, fallback: str = "") -> Estimate:
        return _observed(readings, channel, _unit_of(channel, fallback),
                         relative_time_s)

    # ── attitude ───────────────────────────────────────────────────────
    rate = obs("Gyro_rate_degs", "deg/s")
    attitude = AttitudeState(
        angular_velocity=rate,
        attitude_error=obs("Attitude_error_deg", "deg"),
        sun_sensor_angle=obs("Sun_sensor_angle_deg", "deg"),
        star_tracker_status=obs("Star_tracker_status", "state"),
    )

    # ── reaction wheel ─────────────────────────────────────────────────
    wheel_rpm = obs("RW_speed_rpm", "rpm")
    wheel_rad_s = _derived(
        wheel_rpm.value * RPM_TO_RAD_PER_S.value if wheel_rpm.is_usable
        else None,
        "rad/s", "RW_speed_rpm * 2*pi/60",
    )
    # Normalised momentum: the body rate this wheel's momentum corresponds to,
    # via the derived inertia ratio. Expressed in deg/s so it is directly
    # comparable with the gyro channel, which is what the attitude model needs.
    normalised = _derived(
        (wheel_rpm.value * RPM_TO_RAD_PER_S.value
         * WHEEL_TO_BODY_INERTIA_RATIO.value / _deg_to_rad())
        if wheel_rpm.is_usable else None,
        "deg/s",
        "RW_speed_rpm -> rad/s, times (I_w/I_sc), expressed as an equivalent "
        "body rate in deg/s",
    )
    _, wheel_limit = hard_limits("RW_speed_rpm")
    saturation = _derived(
        abs(wheel_rpm.value) / abs(wheel_limit)
        if wheel_rpm.is_usable and wheel_limit else None,
        "fraction", "|RW_speed_rpm| / RW_speed_rpm hard limit",
    )
    wheel = ReactionWheelState(
        speed_rpm=wheel_rpm, speed_rad_s=wheel_rad_s,
        normalised_momentum=normalised, saturation_fraction=saturation,
    )

    # ── power ──────────────────────────────────────────────────────────
    bus = obs("V_bus", "V")
    array_current = obs("I_sa", "A")
    generation = _derived(
        bus.value * array_current.value
        if bus.is_usable and array_current.is_usable else None,
        "W", "V_bus * I_sa",
    )
    battery = BatteryState(
        terminal_voltage=obs("V_bat", "V"),
        bus_voltage=bus,
        state_of_charge=obs("SoC_pct", "%"),
        array_current=array_current,
        temperature=obs("Battery_temp_C", "degC"),
        generation_power=generation,
    )

    # ── thermal ────────────────────────────────────────────────────────
    thermal = ThermalState(
        component_temperature=obs("Component_temp_C", "degC"),
        heater_power=obs("Heater_power_W", "W"),
        heater_enabled=obs("Heater_enable_flag", "flag"),
        radiator_efficiency=obs("Radiator_eff_pct", "%"),
        panel_temperature=obs("Panel_temp_C", "degC"),
        obc_temperature=obs("OBC_temp_C", "degC"),
    )

    # ── comms ──────────────────────────────────────────────────────────
    comms = CommunicationState(
        transponder_lock=obs("Transponder_lock", "state"),
        link_status=obs("Link_status", "state"),
        snr=obs("SNR_dB", "dB"),
        link_margin=obs("Link_margin_dB", "dB"),
        rf_power=obs("RF_power_dBm", "dBm"),
        bit_error_rate=obs("Bit_error_rate", "ratio"),
        antenna_pointing_error=obs("Antenna_pointing_error_deg", "deg"),
    )

    # Freshly reported AT this sample time, as distinct from carried forward.
    fresh = tuple(sorted(
        name for name, reading in readings.items()
        if not reading.unusable and reading.value is not None
        and relative_time_s is not None and reading.at_s is not None
        and reading.at_s >= relative_time_s
    ))
    unusable = tuple(sorted(
        name for name, reading in readings.items()
        if reading.unusable and relative_time_s is not None
        and reading.at_s is not None and reading.at_s >= relative_time_s
    ))

    return SpacecraftState(
        timestamp=timestamp,
        relative_time_s=relative_time_s,
        attitude=attitude,
        angular_velocity=rate,
        reaction_wheel_state=wheel,
        battery_state=battery,
        thermal_state=thermal,
        communication_state=comms,
        observed_channels=fresh,
        unusable_channels=unusable,
    )


def _deg_to_rad() -> float:
    from app.estimation.parameters import DEG_TO_RAD

    return DEG_TO_RAD.value


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — THE ESTIMATOR
# ═══════════════════════════════════════════════════════════════════════════

#: Stated once, carried into every StateSequence, and rendered into every
#: residual report. These are the limits of the representation itself, as
#: distinct from the parameter caveats in parameters.py.
STRUCTURAL_LIMITATIONS: tuple[str, ...] = (
    "Single-axis attitude only. The channel dictionary declares one unlabelled "
    "Gyro_rate_degs and one Attitude_error_deg, so no three-axis attitude, "
    "quaternion or rate vector is represented. The other two axes are "
    "UNAVAILABLE rather than zero.",
    "One reaction wheel. A real vehicle carries three or four in a distribution "
    "that determines which axes it can control; the dictionary declares one "
    "RW_speed_rpm.",
    "One thermal node. Panel, battery, OBC and transponder temperatures are "
    "carried for context and are not modelled.",
    "No orbit model. There is no position, velocity, eclipse geometry or "
    "attitude-dependent illumination, so a thermal or power change caused by the "
    "vehicle turning cannot be represented.",
    "No sensor fusion and no filtering. There is no Kalman filter and no "
    "smoothing; state values are the readings themselves. The word "
    "'estimation' names the pipeline stage, not a statistical estimator.",
    "No link budget. Communication state is carried but never predicted.",
    "Forward-Euler integration at telemetry sample spacing, which in this "
    "repository is 10 to 300 seconds. At the longer spacings the integration "
    "error is comparable to the prediction itself.",
)


def estimate_states(crash_dump: Optional[dict[str, Any]]) -> StateSequence:
    """Build the state sequence for a crash dump.

    Reads the canonical telemetry window through ``app.api.adapters``, the same
    representation detection and safety validation use, so Phase 7 cannot end up
    looking at a different set of readings than the rest of the pipeline. Phase 3
    established the cost of not doing this: a channel carried in only one of the
    two telemetry fields was invisible to the layer that scanned the other.

    Args:
        crash_dump: Any crash dump dict. None or malformed input yields an empty
            sequence with a warning rather than raising.

    Returns:
        A ``StateSequence``. Deterministic — no randomness, no model call, and no
        network access.
    """
    sequence = StateSequence(limitations=STRUCTURAL_LIMITATIONS)

    if not isinstance(crash_dump, dict):
        sequence.warnings = (
            "No crash dump supplied, so no state could be estimated.",
        )
        return sequence

    try:
        from app.api.adapters import canonical_window_dicts

        rows = canonical_window_dicts(crash_dump)
    except Exception as exc:  # pragma: no cover — adapter is in-tree
        sequence.warnings = (
            f"Could not read the canonical telemetry window ({exc}), so no "
            f"state was estimated.",
        )
        return sequence

    if not rows:
        sequence.warnings = (
            "The crash dump carries no telemetry window, so no state could be "
            "estimated and no residual can be computed.",
        )
        return sequence

    # Group readings by sample time. Resolve each name through the channel
    # dictionary so an alias spelling lands on the canonical id — otherwise
    # 'GYRO_A_RATE' and 'Gyro_rate_degs' would occupy separate slots and the
    # model would see two half-populated samples instead of one.
    from app.ingest.channel_dict import get_channel

    raw: dict[str, dict[str, Any]] = {}
    times: dict[str, Optional[float]] = {}
    seen: set[str] = set()

    for row in rows:
        raw_name = row.get("parameter")
        if not raw_name:
            continue
        definition = get_channel(raw_name)
        channel = definition.channel_id if definition else str(raw_name)
        seen.add(channel)

        timestamp = str(row.get("timestamp") or row.get("timestamp_offset")
                        or "UNKNOWN")
        bucket = raw.setdefault(timestamp, {})

        relative = row.get("relative_time_s")
        if isinstance(relative, (int, float)) and math.isfinite(float(relative)):
            times.setdefault(timestamp, float(relative))
        else:
            times.setdefault(timestamp, None)

        # Later rows for the same channel at the same time do not overwrite an
        # earlier usable value with an unusable one. The adapter merges two
        # telemetry fields, and a legacy row lacking a value must not erase a
        # window row that had one.
        incoming = row.get("value")
        if channel in bucket and _numeric(bucket[channel]) is not None:
            if _numeric(incoming) is None:
                continue
        bucket[channel] = incoming

    # Ordered sample times. Untimed samples come last and cannot participate in
    # carry-forward, since without a time there is no way to say what precedes
    # what.
    ordered = sorted(
        raw.keys(),
        key=lambda ts: (
            0 if times.get(ts) is not None else 1,
            times.get(ts) if times.get(ts) is not None else 0.0,
            ts,
        ),
    )

    # Walk forward carrying the last known value of every channel. This is what
    # makes an asynchronous window usable: without it, a snapshot holds only the
    # one or two channels that happened to arrive at that instant and no model can
    # step from it. Each carried value keeps the time it was measured, so
    # staleness stays visible downstream.
    carried: dict[str, _Reading] = {}
    states: list[SpacecraftState] = []

    for timestamp in ordered:
        state_time = times.get(timestamp)
        for channel, value in raw[timestamp].items():
            numeric = _numeric(value)
            carried[channel] = _Reading(
                value=numeric, at_s=state_time, unusable=numeric is None,
            )
        # A copy per sample time: a state must not see a value that arrived later.
        states.append(_build_state(timestamp, state_time, dict(carried)))

    sequence.states = states
    sequence.channels_seen = tuple(sorted(seen))
    sequence.channels_modelled = tuple(
        sorted(c for c in seen if c in MODELLED_CHANNELS))
    sequence.channels_ignored = tuple(sorted(
        c for c in seen
        if c not in MODELLED_CHANNELS and c not in CONTEXT_CHANNELS
    ))

    warnings: list[str] = []
    timed = len(sequence.timed_states)
    if timed < 2:
        warnings.append(
            f"Only {timed} sample(s) carry a parseable time. Every model here "
            f"steps between consecutive timed samples, so at least two are "
            f"needed and no residual can be computed from this dump."
        )
    if not sequence.channels_modelled:
        warnings.append(
            "None of the channels in this dump is consumed by any model, so no "
            "prediction is possible. This is the expected outcome for "
            "anonymized ESA-ADB channels, which carry no subsystem or physical "
            "meaning."
        )
    if sequence.channels_ignored:
        warnings.append(
            f"{len(sequence.channels_ignored)} channel(s) are neither modelled "
            f"nor carried as context: "
            f"{', '.join(sequence.channels_ignored[:6])}"
            + ("..." if len(sequence.channels_ignored) > 6 else "")
        )
    sequence.warnings = tuple(warnings)

    return sequence


def state_status() -> dict:
    """Describe the state representation, for the API and tests."""
    return {
        "state_schema_version": STATE_SCHEMA_VERSION,
        "quantity_sources": [s.value for s in QuantitySource],
        "modelled_channels": list(MODELLED_CHANNELS),
        "context_channels": list(CONTEXT_CHANNELS),
        "staleness_budget_s": dict(STALENESS_BUDGET_S),
        "staleness_policy": (
            "Telemetry is asynchronous, so a snapshot is built by carrying each "
            "channel's last known value forward. A carried value is labelled "
            "CARRIED_FORWARD, never OBSERVED, and records when it was measured. "
            "A model refuses it once it is older than the cadence its channel "
            "declares. The value a residual is compared AGAINST must always be "
            "freshly reported, never carried."
        ),
        "attitude_axes_represented": 1,
        "reaction_wheels_represented": 1,
        "thermal_nodes_represented": 1,
        "uses_llm": False,
        "deterministic": True,
        "flight_qualified": False,
        "structural_limitations": list(STRUCTURAL_LIMITATIONS),
        "claim": (
            "A simplified single-axis research state representation. Not flight "
            "software, not flight-qualified, and not a model of any specific "
            "spacecraft. Quantities telemetry does not carry are UNAVAILABLE, "
            "never defaulted to zero."
        ),
    }
