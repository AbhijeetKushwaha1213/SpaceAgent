"""
SENTINEL — Model Parameters (app/estimation/parameters.py)

Phase 7. Every physical constant the simplified models need, each carried behind
an explicit statement of where it came from.

READ THIS FIRST
---------------
This is NOT flight software. It is NOT flight-qualified. It does NOT represent
any specific spacecraft or mission. No number below is traceable to a vehicle
specification, a mass properties report, a battery datasheet or a thermal model,
because this repository contains none of those.

What that means for the residuals computed downstream: a residual says the
telemetry is inconsistent WITH THE ASSUMPTIONS ON THIS PAGE. It does not say the
telemetry is inconsistent with the spacecraft. Those are different claims, and
conflating them is the failure this module is written to prevent.

Why a separate module
---------------------
Phase 7 introduces the first physical constants anywhere in this repository. A
grep for inertia, capacity, thermal mass or torque across the whole backend
returns nothing, so there is no precedent to follow and nothing to reuse. Left in
the three model files, these numbers would be invisible: a reader auditing a
thermal residual would have no way to find the assumed conductance it depends on
without reading the model's source. Collected here, the whole assumption set is
one file long and ``parameter_status()`` renders it into every residual report.

Derived beats assumed
---------------------
Six of the ten parameters are DERIVED from the Phase 5 channel dictionary's own
declared ranges rather than chosen. That matters more than it sounds: a derived
parameter moves when the dictionary moves, so it cannot silently disagree with
the limits the detectors enforce. Where a derivation needs an engineering
assumption to close — the wheel sizing argument below — the assumption is stated
as part of the parameter, not buried in a comment.

The four that remain assumed are called out individually, together with which
residual each one scales, so a reader can see exactly how far to trust a number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional

PARAMETER_SET_VERSION = "1.0.0"
"""Version of this parameter set.

Bump on ANY value change. Residuals are only comparable within one version,
because changing a parameter changes every prediction that depends on it.
"""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — PROVENANCE
# ═══════════════════════════════════════════════════════════════════════════

class ParameterSource(str, Enum):
    """Where a model parameter's value came from.

    Mirrors the spirit of ``Provenance`` in the Phase 5 channel dictionary: the
    point is that a reader can tell a measured number from a chosen one without
    reading the source.
    """

    PHYSICAL_CONSTANT = "PHYSICAL_CONSTANT"
    """A constant of nature or an exact unit conversion. Not open to question."""

    DERIVED_FROM_CHANNEL_DICT = "DERIVED_FROM_CHANNEL_DICT"
    """Computed from the declared ranges in app/ingest/channel_dict.py by a
    stated rule. Tracks the dictionary, so it cannot drift away from the limits
    the detectors actually enforce. Still only as good as the dictionary and the
    stated rule, but it is not a free choice."""

    SENTINEL_ASSUMED = "SENTINEL_ASSUMED"
    """Chosen by SENTINEL as a plausible value for a small satellite. NOT
    measured, NOT from a vehicle specification, and NOT fitted to data. Every
    parameter with this source names the residual it scales, so the reader can
    judge how much the conclusion rests on it."""

    @property
    def is_measured(self) -> bool:
        """False for everything here.

        Deliberately a property that always returns False rather than an absent
        concept. It exists so a caller can ask the question and get an honest
        answer, and so a future parameter backed by a real measurement has an
        obvious place to declare itself.
        """
        return self is ParameterSource.PHYSICAL_CONSTANT


@dataclass(frozen=True)
class ModelParameter:
    """One physical parameter, with its units, origin and reach."""

    name: str
    symbol: str
    value: float
    unit: str
    source: ParameterSource
    rationale: str
    """How the value was arrived at. For a DERIVED parameter, the arithmetic and
    the assumption that closes it. For an ASSUMED one, why this magnitude."""

    affects: tuple[str, ...] = ()
    """Which residuals this parameter scales. Populated for ASSUMED parameters
    especially: a reader looking at an inconsistent SoC residual needs to know
    it is inversely proportional to an assumed battery capacity."""

    caveat: Optional[str] = None
    """The specific way this parameter is wrong, when that is known."""

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "symbol": self.symbol,
            "value": self.value,
            "unit": self.unit,
            "source": self.source.value,
            "rationale": self.rationale,
            "affects": list(self.affects),
            "caveat": self.caveat,
        }


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — READING THE CHANNEL DICTIONARY
# ═══════════════════════════════════════════════════════════════════════════

def _nominal_midpoint(channel: str) -> float:
    """Midpoint of a channel's declared nominal band.

    Raises rather than defaulting. A parameter derived from a band that turns out
    to be unspecified would silently become an invented number wearing a DERIVED
    label, which is worse than having no parameter at all.
    """
    from app.ingest.channel_dict import nominal_range

    low, high = nominal_range(channel)
    if low is None or high is None:
        raise ValueError(
            f"cannot derive a parameter from {channel}: its nominal_range is "
            f"{(low, high)}, so there is no band to take a midpoint of"
        )
    return (float(low) + float(high)) / 2.0


def _hard_limit(channel: str, which: str) -> float:
    """One of a channel's declared hard limits. Raises when unspecified."""
    from app.ingest.channel_dict import hard_limits

    low, high = hard_limits(channel)
    value = low if which == "min" else high
    if value is None:
        raise ValueError(
            f"cannot derive a parameter from {channel}: its hard {which} limit "
            f"is unspecified"
        )
    return float(value)


def _nominal_bound(channel: str, which: str) -> float:
    """One end of a channel's declared nominal band. Raises when unspecified."""
    from app.ingest.channel_dict import nominal_range

    low, high = nominal_range(channel)
    value = low if which == "min" else high
    if value is None:
        raise ValueError(
            f"cannot derive a parameter from {channel}: its nominal {which} is "
            f"unspecified"
        )
    return float(value)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — CONVERSIONS
# ═══════════════════════════════════════════════════════════════════════════

DEG_TO_RAD = ModelParameter(
    name="degrees to radians",
    symbol="pi/180",
    value=math.pi / 180.0,
    unit="rad/deg",
    source=ParameterSource.PHYSICAL_CONSTANT,
    rationale="Exact unit conversion.",
)

RPM_TO_RAD_PER_S = ModelParameter(
    name="revolutions per minute to radians per second",
    symbol="2*pi/60",
    value=2.0 * math.pi / 60.0,
    unit="rad/s per rpm",
    source=ParameterSource.PHYSICAL_CONSTANT,
    rationale="Exact unit conversion.",
)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — ATTITUDE
# ═══════════════════════════════════════════════════════════════════════════
#
# The single most important design point in Phase 7:
#
# The attitude rate residual depends on the RATIO of wheel inertia to spacecraft
# inertia, not on either value. Momentum exchange gives
#
#     I_sc * dw_sc  =  -I_w * dw_w        (no external torque)
#     dw_sc         =  -(I_w / I_sc) * dw_w
#
# so a predicted body rate needs only the ratio. Choosing two independent
# inertias would mean inventing two numbers to obtain one, and would invite the
# reader to check each against a vehicle that does not exist.
#
# The ratio itself is derived from the channel dictionary under one stated
# sizing assumption: that the wheel is sized so its full stored momentum can
# absorb a body rate at the vehicle's own rate limit. That is how a reaction
# wheel IS sized, and both endpoints are declared numbers rather than choices —
# RW_speed_rpm's hard maximum and Gyro_rate_degs's hard maximum.
#
# The absolute spacecraft inertia is needed for ONE purpose only: converting an
# unexplained rate residual into an implied torque in newton-metres, which is
# reported for interpretation and never used to decide consistency.

def _wheel_to_body_inertia_ratio() -> float:
    """Derive I_w / I_sc from the declared rate limits.

    Sizing assumption: at its maximum speed the wheel stores enough angular
    momentum to null a body rate at the gyro channel's hard maximum.

        I_w * w_wheel_max  =  I_sc * w_body_max
        I_w / I_sc         =  w_body_max / w_wheel_max

    Both maxima come from app/ingest/channel_dict.py, so this ratio moves with
    the dictionary instead of being pinned to a number chosen here.
    """
    wheel_max_rpm = _hard_limit("RW_speed_rpm", "max")
    body_max_deg_s = _hard_limit("Gyro_rate_degs", "max")

    wheel_max_rad_s = wheel_max_rpm * RPM_TO_RAD_PER_S.value
    body_max_rad_s = body_max_deg_s * DEG_TO_RAD.value
    return body_max_rad_s / wheel_max_rad_s


WHEEL_TO_BODY_INERTIA_RATIO = ModelParameter(
    name="wheel to spacecraft inertia ratio",
    symbol="I_w / I_sc",
    value=_wheel_to_body_inertia_ratio(),
    unit="dimensionless",
    source=ParameterSource.DERIVED_FROM_CHANNEL_DICT,
    rationale=(
        "w_body_max / w_wheel_max, using Gyro_rate_degs hard max "
        "(7 deg/s = 0.1222 rad/s) and RW_speed_rpm hard max "
        "(6000 rpm = 628.3 rad/s). Closes on the standard wheel sizing "
        "assumption that full wheel momentum can absorb a body rate at the "
        "vehicle's rate limit. This ratio, not either inertia alone, is what "
        "the attitude rate prediction depends on."
    ),
    affects=("Gyro_rate_degs", "Attitude_error_deg"),
    caveat=(
        "The sizing assumption is a convention, not a measurement. If the real "
        "wheel were oversized relative to the vehicle, this ratio would be too "
        "small and the model would under-predict how much body rate a given "
        "wheel change produces."
    ),
)

SPACECRAFT_INERTIA = ModelParameter(
    name="spacecraft moment of inertia about the measured axis",
    symbol="I_sc",
    value=10.0,
    unit="kg*m^2",
    source=ParameterSource.SENTINEL_ASSUMED,
    rationale=(
        "Order-of-magnitude value for a roughly 100 kg microsatellite. Used "
        "ONLY to express an unexplained rate residual as an implied external "
        "torque in N*m for the operator to interpret."
    ),
    affects=("implied_disturbance_torque_Nm",),
    caveat=(
        "Scales the reported implied torque linearly and nothing else. No "
        "consistency verdict depends on it, so an order-of-magnitude error here "
        "changes a reported N*m figure and no conclusion."
    ),
)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — POWER
# ═══════════════════════════════════════════════════════════════════════════
#
# Generation needs no illumination parameter. P_gen = V_bus * I_sa uses the
# MEASURED array current, and eclipse is already reflected in that measurement.
# Introducing an assumed solar input and an eclipse model would add two invented
# numbers to predict a quantity the telemetry reports directly.
#
# (Whether a near-zero I_sa is legitimate eclipse or an array fault is a
# different question, and it is Phase 6's: EPS_SOLAR_UNDERVOLT scores the
# recorded eclipse context against it. Phase 7 does not need to re-decide it.)

NOMINAL_BUS_VOLTAGE = ModelParameter(
    name="nominal regulated bus voltage",
    symbol="V_bus_nom",
    value=_nominal_midpoint("V_bus"),
    unit="V",
    source=ParameterSource.DERIVED_FROM_CHANNEL_DICT,
    rationale=(
        "Midpoint of V_bus's declared nominal band (27.5, 32.5). Used only as a "
        "fallback when a sample carries no bus voltage reading; a measured "
        "V_bus is always preferred."
    ),
    affects=("SoC_pct", "V_bat"),
)

BASELINE_ELECTRICAL_LOAD = ModelParameter(
    name="baseline electrical load excluding heaters",
    symbol="P_load_base",
    value=_nominal_midpoint("I_sa") * _nominal_midpoint("V_bus"),
    unit="W",
    source=ParameterSource.DERIVED_FROM_CHANNEL_DICT,
    rationale=(
        "I_sa nominal midpoint (5.0 A) times V_bus nominal midpoint (30.0 V) = "
        "150 W. The derivation rests on an energy-balance argument rather than a "
        "guess: a healthy spacecraft holding a steady state of charge must be "
        "consuming what it generates, so the nominal generation point IS the "
        "nominal load. Both midpoints are declared in the channel dictionary."
    ),
    affects=("SoC_pct", "V_bat"),
    caveat=(
        "Treated as constant. A real load varies with mode — payload duty cycle, "
        "transmitter on or off — so a mode change inside the telemetry window "
        "will appear as a power residual this model attributes to nothing. "
        "Heater power is the one load drawn separately, because it is measured."
    ),
)

BATTERY_CAPACITY = ModelParameter(
    name="usable battery energy capacity",
    symbol="E_cap",
    value=250.0,
    unit="Wh",
    source=ParameterSource.SENTINEL_ASSUMED,
    rationale=(
        "Plausible for a small satellite carrying the 150 W baseline load above "
        "through an eclipse of roughly 35 minutes at a moderate depth of "
        "discharge. Nothing in the repository declares a capacity, and the "
        "SoC_pct rate ceiling in the channel dictionary is a detector threshold "
        "rather than a physical discharge rate, so it cannot be used to derive "
        "one."
    ),
    affects=("SoC_pct",),
    caveat=(
        "The state-of-charge residual is INVERSELY PROPORTIONAL to this value. "
        "A capacity wrong by a factor of two makes the predicted SoC change "
        "wrong by a factor of two. This is the single assumed parameter with the "
        "most reach over a consistency verdict, and the SoC residual should be "
        "read with that in mind."
    ),
)

BATTERY_OCV_EMPTY = ModelParameter(
    name="battery terminal voltage at the bottom of the nominal band",
    symbol="V_bat_lo",
    value=_nominal_bound("V_bat", "min"),
    unit="V",
    source=ParameterSource.DERIVED_FROM_CHANNEL_DICT,
    rationale="Lower bound of V_bat's declared nominal band (28.0 V).",
    affects=("V_bat",),
)

BATTERY_OCV_FULL = ModelParameter(
    name="battery terminal voltage at the top of the nominal band",
    symbol="V_bat_hi",
    value=_nominal_bound("V_bat", "max"),
    unit="V",
    source=ParameterSource.DERIVED_FROM_CHANNEL_DICT,
    rationale="Upper bound of V_bat's declared nominal band (33.0 V).",
    affects=("V_bat",),
    caveat=(
        "Used with the lower bound as a LINEAR state-of-charge to voltage map. "
        "A real lithium-ion open-circuit curve is markedly non-linear and nearly "
        "flat across the middle of its range, so this map is at its worst "
        "exactly where a spacecraft normally operates. The V_bat prediction is "
        "therefore the weakest of the power predictions, and it carries a wider "
        "tolerance for that reason. It also ignores the IR drop under load."
    ),
)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6 — THERMAL
# ═══════════════════════════════════════════════════════════════════════════
#
# A lumped first-order node. Two parameters set its behaviour: a conductance to
# the sink and a thermal capacitance. Rather than choose both, the physically
# meaningful quantity — the time constant tau = C/k — is stated as the
# assumption, and the capacitance follows from it. A reader can judge "this
# component settles in about ten minutes" far more readily than a figure in
# joules per kelvin.
#
# The sink temperature and the internal dissipation are then DERIVED, so the
# model is self-consistent: with the heater off, the predicted steady state
# lands on the middle of the component's declared nominal band. A thermal model
# whose quiescent prediction sat outside the nominal band would report a
# residual on healthy telemetry.

THERMAL_CONDUCTANCE = ModelParameter(
    name="effective thermal conductance from the component to its sink",
    symbol="k_th",
    value=0.5,
    unit="W/K",
    source=ParameterSource.SENTINEL_ASSUMED,
    rationale=(
        "Chosen so the heater's declared nominal maximum of 10 W produces a "
        "steady-state rise of 20 K, which is a plausible authority for a small "
        "radiatively coupled component within an 80 K nominal band."
    ),
    affects=("Component_temp_C",),
    caveat=(
        "Sets how strongly temperature responds to a given heat input. It also "
        "propagates into the derived internal dissipation below, so the two "
        "move together and the quiescent prediction stays on the nominal "
        "midpoint regardless of the value chosen here."
    ),
)

THERMAL_TIME_CONSTANT = ModelParameter(
    name="thermal time constant",
    symbol="tau_th",
    value=600.0,
    unit="s",
    source=ParameterSource.SENTINEL_ASSUMED,
    rationale=(
        "Ten minutes, a plausible settling time for a small instrumented "
        "component. Stated as a time constant rather than a capacitance because "
        "it is the quantity a reader can actually assess."
    ),
    affects=("Component_temp_C",),
    caveat=(
        "Telemetry in this repository is sampled at 10 to 300 second offsets. "
        "Where a step approaches or exceeds this time constant, forward-Euler "
        "integration carries error of the same order as the prediction itself. "
        "The thermal tolerance widens with step size to reflect that; see "
        "residuals.py."
    ),
)

THERMAL_CAPACITANCE = ModelParameter(
    name="lumped thermal capacitance",
    symbol="C_th",
    value=THERMAL_TIME_CONSTANT.value * THERMAL_CONDUCTANCE.value,
    unit="J/K",
    source=ParameterSource.SENTINEL_ASSUMED,
    rationale=(
        "tau_th * k_th = 600 s * 0.5 W/K = 300 J/K. Consistent with roughly "
        "0.33 kg of aluminium at 900 J/kg/K, which is the right order for a "
        "small component. Follows from the two assumptions above rather than "
        "being an independent choice."
    ),
    affects=("Component_temp_C",),
    caveat=(
        "Not an independent assumption: it is the product of tau_th and k_th, so "
        "its error is entirely determined by those two and it adds no new "
        "uncertainty of its own. Listed as ASSUMED rather than DERIVED because "
        "what it derives from is assumed, and calling it derived would imply a "
        "grounding it does not have."
    ),
)

THERMAL_SINK_TEMP = ModelParameter(
    name="effective sink temperature",
    symbol="T_sink",
    value=_hard_limit("Component_temp_C", "min"),
    unit="degC",
    source=ParameterSource.DERIVED_FROM_CHANNEL_DICT,
    rationale=(
        "Component_temp_C's declared hard minimum, -20 degC. The reasoning: the "
        "coldest the component is expected to reach is where it settles with no "
        "heat input at all, which is the definition of the effective sink for a "
        "first-order node."
    ),
    affects=("Component_temp_C",),
    caveat=(
        "An effective sink standing in for radiation to space, conduction to "
        "structure and albedo together. It is not any physical surface "
        "temperature, and it does not vary with attitude or orbital position — "
        "so this model cannot represent a thermal change caused by the vehicle "
        "turning, which is one of the propagation paths Phase 6 declares."
    ),
)

INTERNAL_DISSIPATION = ModelParameter(
    name="internal electrical dissipation into the thermal node",
    symbol="Q_int",
    value=(
        THERMAL_CONDUCTANCE.value
        * (_nominal_midpoint("Component_temp_C") - _hard_limit(
            "Component_temp_C", "min"))
    ),
    unit="W",
    source=ParameterSource.DERIVED_FROM_CHANNEL_DICT,
    rationale=(
        "k_th * (T_nominal_mid - T_sink) = 0.5 W/K * (30 - (-20)) K = 25 W. "
        "Derived by requiring the heater-off steady state to land on the "
        "midpoint of Component_temp_C's declared nominal band, so the model "
        "predicts healthy telemetry as healthy instead of reporting a standing "
        "residual on a nominal spacecraft."
    ),
    affects=("Component_temp_C",),
)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7 — REGISTRY
# ═══════════════════════════════════════════════════════════════════════════

ALL_PARAMETERS: tuple[ModelParameter, ...] = (
    DEG_TO_RAD,
    RPM_TO_RAD_PER_S,
    WHEEL_TO_BODY_INERTIA_RATIO,
    SPACECRAFT_INERTIA,
    NOMINAL_BUS_VOLTAGE,
    BASELINE_ELECTRICAL_LOAD,
    BATTERY_CAPACITY,
    BATTERY_OCV_EMPTY,
    BATTERY_OCV_FULL,
    THERMAL_CONDUCTANCE,
    THERMAL_TIME_CONSTANT,
    THERMAL_CAPACITANCE,
    THERMAL_SINK_TEMP,
    INTERNAL_DISSIPATION,
)


def assumed_parameters() -> tuple[ModelParameter, ...]:
    """Parameters that were CHOSEN rather than derived or exact.

    Surfaced in every residual report. A reader deciding how much weight a
    residual deserves needs this list without going looking for it.
    """
    return tuple(p for p in ALL_PARAMETERS
                 if p.source is ParameterSource.SENTINEL_ASSUMED)


def parameters_affecting(channel: str) -> tuple[ModelParameter, ...]:
    """Every parameter that feeds a prediction for one channel."""
    return tuple(p for p in ALL_PARAMETERS if channel in p.affects)


def validate_parameters() -> dict[str, list[str]]:
    """Check the parameter set for defects that would distort every residual."""
    errors: list[str] = []
    warnings: list[str] = []

    seen: set[str] = set()
    for parameter in ALL_PARAMETERS:
        if parameter.name in seen:
            errors.append(f"duplicate parameter name {parameter.name!r}")
        seen.add(parameter.name)

        if not math.isfinite(parameter.value):
            errors.append(
                f"{parameter.symbol}: value {parameter.value} is not finite")
        if not parameter.unit.strip():
            errors.append(f"{parameter.symbol}: no unit declared")
        if len(parameter.rationale.strip()) < 20:
            errors.append(
                f"{parameter.symbol}: rationale too short to be reviewable")

        if (parameter.source is ParameterSource.SENTINEL_ASSUMED
                and not parameter.affects):
            errors.append(
                f"{parameter.symbol}: an assumed parameter must declare which "
                f"residuals it affects, otherwise its reach is unauditable"
            )
        if (parameter.source is ParameterSource.SENTINEL_ASSUMED
                and not parameter.caveat):
            warnings.append(
                f"{parameter.symbol}: assumed but carries no caveat explaining "
                f"how it is wrong"
            )

    # Physical sanity, checked rather than asserted in prose.
    if WHEEL_TO_BODY_INERTIA_RATIO.value <= 0.0:
        errors.append("I_w/I_sc must be positive")
    if WHEEL_TO_BODY_INERTIA_RATIO.value >= 1.0:
        errors.append(
            "I_w/I_sc >= 1 means the wheel has more inertia than the "
            "spacecraft, which inverts the momentum exchange"
        )
    if BATTERY_OCV_FULL.value <= BATTERY_OCV_EMPTY.value:
        errors.append(
            "battery voltage at full charge must exceed voltage at empty")
    if BATTERY_CAPACITY.value <= 0.0:
        errors.append("battery capacity must be positive")
    if THERMAL_CAPACITANCE.value <= 0.0 or THERMAL_CONDUCTANCE.value <= 0.0:
        errors.append("thermal capacitance and conductance must be positive")

    # The self-consistency the thermal derivation is built on. If this drifts,
    # the model reports a standing residual on healthy telemetry.
    try:
        quiescent = (
            THERMAL_SINK_TEMP.value
            + INTERNAL_DISSIPATION.value / THERMAL_CONDUCTANCE.value
        )
        expected = _nominal_midpoint("Component_temp_C")
        if abs(quiescent - expected) > 1e-6:
            errors.append(
                f"heater-off steady state {quiescent:.3f} degC does not land on "
                f"the Component_temp_C nominal midpoint {expected:.3f} degC, so "
                f"the thermal model would report a residual on nominal telemetry"
            )
    except ValueError as exc:  # pragma: no cover — dictionary is in-tree
        errors.append(f"thermal self-consistency check could not run: {exc}")

    return {"errors": errors, "warnings": warnings}


def parameter_status() -> dict:
    """Summary for residual reports, the API and tests."""
    findings = validate_parameters()
    return {
        "parameter_set_version": PARAMETER_SET_VERSION,
        "total_parameters": len(ALL_PARAMETERS),
        "source_counts": {
            source.value: sum(1 for p in ALL_PARAMETERS if p.source is source)
            for source in ParameterSource
        },
        "parameters": [p.as_dict() for p in ALL_PARAMETERS],
        "assumed_parameters": [p.symbol for p in assumed_parameters()],
        "flight_qualified": False,
        "represents_specific_mission": False,
        "claim": (
            "Simplified research parameters. NOT flight software, NOT "
            "flight-qualified, and NOT representative of any specific "
            "spacecraft. No value is traceable to a vehicle specification, "
            "because this repository contains none. A residual computed from "
            "these parameters shows inconsistency with THESE ASSUMPTIONS, not "
            "with the spacecraft."
        ),
        "validation": findings,
    }


def _main() -> int:
    """``python3 -m app.estimation.parameters`` — print and validate."""
    import json

    status = parameter_status()
    print(json.dumps(status, indent=2))

    findings = status["validation"]
    print()
    print(f"errors   : {len(findings['errors'])}")
    for message in findings["errors"]:
        print(f"  ERROR {message}")
    print(f"warnings : {len(findings['warnings'])}")
    for message in findings["warnings"]:
        print(f"  WARN  {message}")
    return 1 if findings["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(_main())
