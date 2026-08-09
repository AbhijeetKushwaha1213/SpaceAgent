"""
SENTINEL — Residuals (app/estimation/residuals.py)

Phase 7. Compares observed telemetry against model predictions and reports the
difference.

    residual  =  observed  -  predicted

NOT flight software. NOT flight-qualified. NOT a model of any specific mission.


WHAT A RESIDUAL HERE DOES AND DOES NOT MEAN
===========================================
It means: the telemetry does not match what the assumptions in
``parameters.py`` predict.

It does NOT mean: the telemetry does not match the spacecraft.

Four of the ten model parameters were chosen rather than derived, and every model
is a deliberate simplification — one attitude axis, one wheel, one thermal node, a
linear battery curve, no illumination. An inconsistent residual is therefore a
prompt to look, not a finding about hardware. ``ResidualReport`` carries the
assumption set and the structural limitations so that qualification travels with
the numbers.


TRI-STATE, FOR THE SAME REASON AS EVERYWHERE ELSE
=================================================
    CONSISTENT     observed and predicted agree within tolerance
    INCONSISTENT   they do not
    UNDECIDABLE    no comparison was possible

UNDECIDABLE is not a soft CONSISTENT. If the previous sample carried no reading,
or the step exceeded the integrator's stability bound, there is no evidence either
way and the report says so. Phase 1's condition evaluator established the cost of
collapsing these: treating absent data as a satisfied precondition silently
unblocked commands on a spacecraft whose sensor had dropped out. The same mistake
here would report a spacecraft as physically consistent on the strength of
telemetry nobody could check.


THE TOLERANCE IS A CHOSEN SENSITIVITY
=====================================
The tolerance decides where CONSISTENT ends and INCONSISTENT begins, which makes
it as consequential as any physical parameter — so it is declared, justified and
validated rather than sprinkled through the comparison code.

It is NOT a statistical confidence bound. Deriving one honestly would need a
measurement noise model and a parameter covariance, and this repository has
neither. What each tolerance is instead:

    floor        a stated fraction of the channel's DECLARED nominal span, so
                 the sensitivity scales with the channel rather than being an
                 absolute number chosen per channel
    dt growth    a term that widens the tolerance with step length, because
                 forward-Euler error grows with the step and every model here
                 integrates that way

Both fractions are SENTINEL choices. They are reported in every residual so a
reader can see the threshold that produced a verdict, and ``validate_estimation()``
checks that each one has a rationale attached.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from app.estimation.models import Comparison, ModelPrediction, PredictionStatus
from app.estimation.models import attitude as attitude_model
from app.estimation.models import power as power_model
from app.estimation.models import thermal as thermal_model
from app.estimation.parameters import (
    PARAMETER_SET_VERSION,
    assumed_parameters,
    parameter_status,
)
from app.estimation.state import (
    STATE_SCHEMA_VERSION,
    SpacecraftState,
    StateSequence,
    estimate_states,
)

RESIDUAL_SCHEMA_VERSION = "1.0.0"
"""Version of the residual contract. Bump when a field or a tolerance changes,
since residuals are only comparable within one version."""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — VOCABULARIES
# ═══════════════════════════════════════════════════════════════════════════

class ResidualStatus(str, Enum):
    """Verdict on one comparison."""

    CONSISTENT = "CONSISTENT"
    """Observed agrees with predicted within tolerance. Given the assumptions."""

    INCONSISTENT = "INCONSISTENT"
    """They disagree by more than tolerance. Either the spacecraft is behaving
    in a way elementary physics does not account for, or an assumption is
    wrong. This module cannot tell those apart and does not claim to."""

    UNDECIDABLE = "UNDECIDABLE"
    """No comparison was possible: a missing input, an unusable reading, or a
    step outside an integrator's stability bound. NOT a passing check."""

    @property
    def is_decided(self) -> bool:
        return self is not ResidualStatus.UNDECIDABLE


class ExplanationKind(str, Enum):
    """A candidate account of an inconsistent residual.

    Several are offered rather than one, and each carries whether the evidence
    actually supports it. The reasoning is the same as Phase 6's: presenting a
    single explanation for ambiguous evidence is an assertion dressed as a
    diagnosis.
    """

    EXTERNAL_TORQUE = "EXTERNAL_TORQUE"
    """The body rate changed with no wheel motion to account for it, so
    something outside the model applied a torque — drag, radiation pressure,
    gravity gradient, a thruster, or a commanded momentum dump."""

    ACTUATOR_UNDER_RESPONSE = "ACTUATOR_UNDER_RESPONSE"
    """The wheel speed changed but the body responded by less than that change
    implies. Consistent with a wheel not delivering the torque its speed change
    suggests — a degrading bearing or drive fault — and equally consistent with
    the assumed inertia ratio being too large."""

    SENSOR_UNDER_REPORTING = "SENSOR_UNDER_REPORTING"
    """Pointing error grew by more than the measured body rate can explain, so
    the rate sensor is reporting less motion than occurred. A drifting or failed
    rate sensor produces this; a healthy one cannot."""

    ENERGY_BOOKKEEPING_GAP = "ENERGY_BOOKKEEPING_GAP"
    """Stored energy moved differently from measured generation minus assumed
    load. An unmodelled load, a load mode change, or a battery not delivering
    its assumed capacity."""

    UNMODELLED_HEAT_PATH = "UNMODELLED_HEAT_PATH"
    """Temperature moved differently from what measured heater power and assumed
    dissipation predict. A stuck heater, a lost radiator path, or the
    illumination change this model openly does not represent."""

    MODEL_PARAMETER_ERROR = "MODEL_PARAMETER_ERROR"
    """An assumed parameter is wrong. Always offered alongside any inconsistent
    residual, because with four chosen parameters it is never excluded — and a
    report that omitted it would be overstating what the physics shows."""

    MODEL_STRUCTURE_LIMITATION = "MODEL_STRUCTURE_LIMITATION"
    """The residual falls in a gap the model declares: a second attitude axis, a
    second wheel, another thermal node, or an eclipse transition."""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — TOLERANCES
# ═══════════════════════════════════════════════════════════════════════════

#: Reference step for the dt-growth term. Telemetry here is spaced 10 to 300 s
#: apart, so 100 s sits in the middle of the observed range.
REFERENCE_DT_S = 100.0


@dataclass(frozen=True)
class ToleranceSpec:
    """How much disagreement is tolerated on one channel, and why."""

    channel: str
    span_fraction: float
    """Floor as a fraction of the channel's declared nominal span. Scales with
    the channel instead of being an absolute number chosen per channel."""

    dt_growth: float
    """Additional tolerance per REFERENCE_DT_S of step, as a multiple of the
    floor. Reflects forward-Euler error growing with step length."""

    rationale: str
    """Why this sensitivity. Required — a threshold without a stated reason
    cannot be argued with, only accepted."""

    def floor(self) -> float:
        """Absolute floor from the channel's declared nominal span.

        Raises when the span is unspecified, rather than defaulting. A tolerance
        silently falling back to an invented constant is the drift Phase 5 set
        out to remove.
        """
        from app.ingest.channel_dict import nominal_range

        low, high = nominal_range(self.channel)
        if low is None or high is None:
            raise ValueError(
                f"{self.channel}: no declared nominal span, so no tolerance can "
                f"be derived from it"
            )
        span = abs(float(high) - float(low))
        if span <= 0.0:
            raise ValueError(
                f"{self.channel}: declared nominal span is zero, so no "
                f"tolerance can be derived from it"
            )
        return self.span_fraction * span

    def at(self, dt_s: float) -> float:
        """Tolerance for a step of ``dt_s`` seconds."""
        base = self.floor()
        growth = self.dt_growth * base * (max(dt_s, 0.0) / REFERENCE_DT_S)
        return base + growth

    def as_dict(self) -> dict[str, Any]:
        try:
            floor = self.floor()
        except ValueError as exc:
            floor = None
            self_note = str(exc)
        else:
            self_note = None
        return {
            "channel": self.channel,
            "span_fraction": self.span_fraction,
            "dt_growth_per_reference_dt": self.dt_growth,
            "reference_dt_s": REFERENCE_DT_S,
            "floor": floor,
            "floor_error": self_note,
            "rationale": self.rationale,
        }


#: Per-channel sensitivities. Every fraction here is a SENTINEL choice, stated so
#: it can be disagreed with. There is no statistical basis for any of them and
#: none is claimed.
TOLERANCES: dict[str, ToleranceSpec] = {
    "Gyro_rate_degs": ToleranceSpec(
        channel="Gyro_rate_degs",
        span_fraction=0.10,
        dt_growth=0.5,
        rationale=(
            "10% of the 1.0 deg/s nominal span gives a 0.1 deg/s floor. An "
            "unexplained body rate of that size is worth an operator's "
            "attention while staying above the integration error of a "
            "single-axis Euler step. Grows by half the floor per 100 s because "
            "the momentum-exchange prediction is a one-step integral."
        ),
    ),
    "Attitude_error_deg": ToleranceSpec(
        channel="Attitude_error_deg",
        span_fraction=0.10,
        dt_growth=1.0,
        rationale=(
            "10% of the 1.0 deg nominal span. Compared as an UPPER BOUND, so "
            "this tolerance only ever permits the observation to sit ABOVE the "
            "open-loop bound before being called inconsistent. Grows a full "
            "floor per 100 s: the bound accumulates rate error across the whole "
            "step, so a longer step makes it looser."
        ),
    ),
    "SoC_pct": ToleranceSpec(
        channel="SoC_pct",
        span_fraction=0.05,
        dt_growth=0.5,
        rationale=(
            "5% of the 35-point nominal span gives a 1.75-point floor. Tighter "
            "in relative terms than the attitude channels because energy "
            "bookkeeping over one step is a straightforward integral, but note "
            "the whole prediction is inversely proportional to an ASSUMED "
            "capacity, so the tolerance is not the dominant uncertainty here."
        ),
    ),
    "V_bat": ToleranceSpec(
        channel="V_bat",
        span_fraction=0.30,
        dt_growth=0.25,
        rationale=(
            "30% of the 5.0 V nominal span gives a 1.5 V floor — by far the "
            "loosest tolerance here, and deliberately so. The prediction maps "
            "state of charge to voltage LINEARLY across a real curve that is "
            "nearly flat in mid-range, and it carries no internal-resistance "
            "term, so a tight tolerance would report inconsistency on healthy "
            "telemetry. Only a gross voltage discrepancy should register."
        ),
    ),
    "Component_temp_C": ToleranceSpec(
        channel="Component_temp_C",
        span_fraction=0.05,
        dt_growth=1.0,
        rationale=(
            "5% of the 80 K nominal span gives a 4 K floor. Grows a full floor "
            "per 100 s because the assumed time constant is 600 s, so a 300 s "
            "step covers half a time constant and forward-Euler error over it "
            "is substantial."
        ),
    ),
}


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Residual:
    """One observed-minus-predicted comparison at one step."""

    channel: str
    unit: str
    status: ResidualStatus

    observed: Optional[float]
    predicted: Optional[float]
    residual: Optional[float]
    """observed - predicted. None when either side is missing."""

    tolerance: Optional[float]
    exceedance: Optional[float]
    """How far past tolerance the residual reached. None when within it."""

    comparison: str
    model: str
    equation: str

    from_timestamp: str
    to_timestamp: str
    dt_s: Optional[float]

    assumptions: tuple[str, ...] = ()
    parameters_used: tuple[str, ...] = ()
    inputs: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)
    tolerance_rationale: Optional[str] = None
    undecidable_reason: Optional[str] = None

    @property
    def is_inconsistent(self) -> bool:
        return self.status is ResidualStatus.INCONSISTENT

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "unit": self.unit,
            "status": self.status.value,
            "observed": self.observed,
            "predicted": self.predicted,
            "residual": self.residual,
            "tolerance": self.tolerance,
            "exceedance": self.exceedance,
            "comparison": self.comparison,
            "model": self.model,
            "equation": self.equation,
            "from_timestamp": self.from_timestamp,
            "to_timestamp": self.to_timestamp,
            "dt_s": self.dt_s,
            "assumptions": list(self.assumptions),
            "parameters_used": list(self.parameters_used),
            "inputs": dict(self.inputs),
            "extras": dict(self.extras),
            "tolerance_rationale": self.tolerance_rationale,
            "undecidable_reason": self.undecidable_reason,
        }


@dataclass(frozen=True)
class ResidualExplanation:
    """One candidate account of an inconsistent residual, and its support."""

    kind: ExplanationKind
    channel: str
    supported: bool
    """Whether the deterministic rule for this explanation actually fired."""

    rule: str
    """The rule evaluated, so a reader can check the inference."""

    evidence: dict[str, Any] = field(default_factory=dict)
    caveat: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "channel": self.channel,
            "supported": self.supported,
            "rule": self.rule,
            "evidence": dict(self.evidence),
            "caveat": self.caveat,
        }


@dataclass
class ResidualReport:
    """Everything Phase 7 produces for one crash dump."""

    residuals: list[Residual] = field(default_factory=list)
    explanations: list[ResidualExplanation] = field(default_factory=list)

    residual_schema_version: str = RESIDUAL_SCHEMA_VERSION
    state_schema_version: str = STATE_SCHEMA_VERSION
    parameter_set_version: str = PARAMETER_SET_VERSION

    state_count: int = 0
    step_count: int = 0
    channels_modelled: tuple[str, ...] = ()
    channels_not_modelled: tuple[str, ...] = ()

    limitations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    summary: str = ""

    uses_llm: bool = False
    flight_qualified: bool = False
    deterministic: bool = True

    # ── counts ─────────────────────────────────────────────────────────

    @property
    def consistent(self) -> list[Residual]:
        return [r for r in self.residuals
                if r.status is ResidualStatus.CONSISTENT]

    @property
    def inconsistent(self) -> list[Residual]:
        return [r for r in self.residuals
                if r.status is ResidualStatus.INCONSISTENT]

    @property
    def undecidable(self) -> list[Residual]:
        return [r for r in self.residuals
                if r.status is ResidualStatus.UNDECIDABLE]

    @property
    def physically_consistent(self) -> Optional[bool]:
        """Whether everything checkable was consistent.

        None when nothing could be checked at all. That is deliberately not
        False: no evidence of inconsistency is not evidence of consistency, and
        a boolean cannot express the difference.
        """
        if not self.residuals:
            return None
        if all(r.status is ResidualStatus.UNDECIDABLE for r in self.residuals):
            return None
        return not self.inconsistent

    def supported_explanations(self) -> list[ResidualExplanation]:
        return [e for e in self.explanations if e.supported]

    def as_dict(self) -> dict[str, Any]:
        return {
            "residual_schema_version": self.residual_schema_version,
            "state_schema_version": self.state_schema_version,
            "parameter_set_version": self.parameter_set_version,
            "state_count": self.state_count,
            "step_count": self.step_count,
            "residual_count": len(self.residuals),
            "consistent_count": len(self.consistent),
            "inconsistent_count": len(self.inconsistent),
            "undecidable_count": len(self.undecidable),
            "physically_consistent": self.physically_consistent,
            "channels_modelled": list(self.channels_modelled),
            "channels_not_modelled": list(self.channels_not_modelled),
            "residuals": [r.as_dict() for r in self.residuals],
            "explanations": [e.as_dict() for e in self.explanations],
            "supported_explanations": [
                e.kind.value for e in self.supported_explanations()
            ],
            "assumed_parameters": [
                p.as_dict() for p in assumed_parameters()
            ],
            "limitations": list(self.limitations),
            "warnings": list(self.warnings),
            "summary": self.summary,
            "uses_llm": self.uses_llm,
            "flight_qualified": self.flight_qualified,
            "deterministic": self.deterministic,
            "claim": (
                "A residual shows inconsistency with the assumptions in "
                "app/estimation/parameters.py, NOT with the spacecraft. Four of "
                "the ten parameters were chosen rather than derived, and every "
                "model is a deliberate simplification. An inconsistent residual "
                "is a prompt to investigate, not a finding about hardware. "
                "UNDECIDABLE is not a passing check."
            ),
        }


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — COMPARISON
# ═══════════════════════════════════════════════════════════════════════════

def _observed_value(state: SpacecraftState, channel: str) -> Optional[float]:
    """The FRESHLY reported reading for a channel in a state snapshot.

    Freshness is mandatory here, unlike for a model's inputs. This is the value a
    residual is measured against, and a carried-forward value would make the
    residual an artefact: the prediction starts from the same carried number, so
    observed minus predicted would return the model's own step rather than
    anything the spacecraft did.
    """
    lookup = {
        "Gyro_rate_degs": state.angular_velocity,
        "Attitude_error_deg": state.attitude.attitude_error,
        "RW_speed_rpm": state.reaction_wheel_state.speed_rpm,
        "SoC_pct": state.battery_state.state_of_charge,
        "V_bat": state.battery_state.terminal_voltage,
        "V_bus": state.battery_state.bus_voltage,
        "I_sa": state.battery_state.array_current,
        "Component_temp_C": state.thermal_state.component_temperature,
        "Heater_power_W": state.thermal_state.heater_power,
    }
    estimate = lookup.get(channel)
    if estimate is None or not estimate.is_fresh:
        return None
    return estimate.value


def _to_residual(
    prediction: ModelPrediction,
    previous: SpacecraftState,
    current: SpacecraftState,
    dt_s: float,
) -> Residual:
    """Compare one prediction against the observation, applying the tolerance."""
    observed = _observed_value(current, prediction.channel)
    spec = TOLERANCES.get(prediction.channel)

    common = {
        "channel": prediction.channel,
        "unit": prediction.unit,
        "comparison": prediction.comparison.value,
        "model": prediction.model,
        "equation": prediction.equation,
        "from_timestamp": previous.timestamp,
        "to_timestamp": current.timestamp,
        "dt_s": dt_s,
        "assumptions": prediction.assumptions,
        "parameters_used": prediction.parameters_used,
        "inputs": dict(prediction.inputs),
        "extras": dict(prediction.extras),
        "tolerance_rationale": spec.rationale if spec else None,
    }

    if prediction.status is PredictionStatus.NOT_PREDICTABLE:
        return Residual(
            status=ResidualStatus.UNDECIDABLE, observed=observed,
            predicted=None, residual=None, tolerance=None, exceedance=None,
            undecidable_reason=prediction.reason or "no prediction was produced",
            **common,
        )

    if observed is None:
        return Residual(
            status=ResidualStatus.UNDECIDABLE, observed=None,
            predicted=prediction.predicted, residual=None, tolerance=None,
            exceedance=None,
            undecidable_reason=(
                f"a prediction was produced but {prediction.channel} has no "
                f"FRESHLY reported value at {current.timestamp}, so there is "
                f"nothing measured to compare it against. A carried-forward "
                f"value is deliberately not accepted here: the prediction "
                f"starts from the same carried number, so the residual would be "
                f"the model's own step rather than an observation."
            ),
            **common,
        )

    if spec is None:
        return Residual(
            status=ResidualStatus.UNDECIDABLE, observed=observed,
            predicted=prediction.predicted,
            residual=observed - (prediction.predicted or 0.0),
            tolerance=None, exceedance=None,
            undecidable_reason=(
                f"no tolerance is declared for {prediction.channel}, so the "
                f"residual cannot be judged. Declaring one requires a stated "
                f"sensitivity, which is not invented here."
            ),
            **common,
        )

    try:
        tolerance = spec.at(dt_s)
    except ValueError as exc:
        return Residual(
            status=ResidualStatus.UNDECIDABLE, observed=observed,
            predicted=prediction.predicted,
            residual=observed - (prediction.predicted or 0.0),
            tolerance=None, exceedance=None,
            undecidable_reason=f"tolerance could not be derived: {exc}",
            **common,
        )

    residual_value = observed - prediction.predicted

    # An UPPER_BOUND prediction is only violated from above. Coming in below the
    # open-loop attitude bound is what a working controller does, and testing it
    # two-sided would report every correctly controlled vehicle as inconsistent.
    if prediction.comparison is Comparison.UPPER_BOUND:
        overshoot = residual_value - tolerance
        status = (ResidualStatus.INCONSISTENT if overshoot > 0.0
                  else ResidualStatus.CONSISTENT)
        exceedance = overshoot if overshoot > 0.0 else None
    else:
        overshoot = abs(residual_value) - tolerance
        status = (ResidualStatus.INCONSISTENT if overshoot > 0.0
                  else ResidualStatus.CONSISTENT)
        exceedance = overshoot if overshoot > 0.0 else None

    return Residual(
        status=status, observed=observed, predicted=prediction.predicted,
        residual=residual_value, tolerance=tolerance, exceedance=exceedance,
        **common,
    )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — EXPLANATION
# ═══════════════════════════════════════════════════════════════════════════
#
# Deterministic rules over the residuals, offered as candidates rather than as an
# answer. Every rule is stated on the explanation it produces.
#
# One rule is worth reading closely, because it is the only one that can separate
# a sensor fault from real motion:
#
#   The rate prediction is anchored on the PREVIOUS OBSERVED rate, so a CONSTANT
#   gyro bias cancels exactly and produces no residual. That is a genuine
#   limitation and it is stated rather than glossed. What a bias DOES break is
#   the attitude-error bound: if the gyro under-reports the motion, the vehicle
#   turns further than the integrated rate accounts for, and the observed
#   pointing error exceeds the open-loop ceiling. Attitude error above its bound
#   is therefore evidence that the rate sensor is under-reporting, and a healthy
#   sensor cannot produce it.

def _significant(value: Optional[float], threshold: float) -> bool:
    return (
        value is not None and math.isfinite(value) and abs(value) > threshold
    )


def explain_residuals(residuals: list[Residual]) -> list[ResidualExplanation]:
    """Offer candidate accounts of the inconsistent residuals.

    Returns candidates for every rule that was evaluable, each marked with
    whether its rule fired. Unsupported candidates are retained so a reader can
    see what was considered and ruled out, which is the same reason
    ``candidates.py`` reports rejected faults.
    """
    explanations: list[ResidualExplanation] = []
    by_channel = {r.channel: r for r in residuals}

    rate = by_channel.get("Gyro_rate_degs")
    error = by_channel.get("Attitude_error_deg")
    soc = by_channel.get("SoC_pct")
    temperature = by_channel.get("Component_temp_C")

    # ── attitude: external torque vs actuator under-response ───────────
    if rate is not None and rate.status.is_decided:
        wheel_change = rate.extras.get("wheel_delta_rpm")
        wheel_effect = rate.extras.get("body_rate_change_from_wheel_deg_s")
        tolerance = rate.tolerance or 0.0

        wheel_moved = _significant(wheel_effect, tolerance)
        rate_unexplained = rate.is_inconsistent

        torque = attitude_model.implied_disturbance_torque(
            rate.residual, rate.dt_s or 0.0)

        explanations.append(ResidualExplanation(
            kind=ExplanationKind.EXTERNAL_TORQUE,
            channel="Gyro_rate_degs",
            supported=bool(rate_unexplained and not wheel_moved),
            rule=(
                "The rate residual exceeds tolerance AND the wheel's predicted "
                "contribution to body rate is within tolerance, i.e. the body "
                "accelerated with no actuator input to account for it."
            ),
            evidence={
                "rate_residual_deg_s": rate.residual,
                "tolerance_deg_s": rate.tolerance,
                "wheel_delta_rpm": wheel_change,
                "wheel_effect_on_body_rate_deg_s": wheel_effect,
                "implied_external_torque_Nm": torque,
            },
            caveat=(
                "The implied torque uses the ASSUMED spacecraft inertia, so its "
                "magnitude carries that uncertainty; its sign and non-zero-ness "
                "do not. A commanded momentum dump or a thruster firing would "
                "also present exactly this way, and neither is represented."
            ),
        ))

        under_response = bool(
            rate_unexplained
            and wheel_moved
            and wheel_effect is not None
            and rate.residual is not None
            # Residual opposes the wheel's intended effect: the body moved less
            # than the wheel change implies.
            and (rate.residual * wheel_effect) < 0.0
        )
        explanations.append(ResidualExplanation(
            kind=ExplanationKind.ACTUATOR_UNDER_RESPONSE,
            channel="Gyro_rate_degs",
            supported=under_response,
            rule=(
                "The rate residual exceeds tolerance, the wheel DID produce a "
                "significant predicted body-rate change, and the residual has "
                "the OPPOSITE sign to that change — so the body responded by "
                "less than the wheel's motion implies."
            ),
            evidence={
                "rate_residual_deg_s": rate.residual,
                "wheel_effect_on_body_rate_deg_s": wheel_effect,
                "opposite_sign": (
                    None if (rate.residual is None or wheel_effect is None)
                    else (rate.residual * wheel_effect) < 0.0
                ),
            },
            caveat=(
                "Indistinguishable here from an assumed inertia ratio that is "
                "too large: both make the body respond less than predicted. "
                "Separating them needs a wheel torque or motor current channel, "
                "which the channel dictionary does not carry."
            ),
        ))

    # ── attitude error above its bound implies the rate sensor under-reports ──
    if error is not None and error.status.is_decided:
        supported = error.is_inconsistent
        explanations.append(ResidualExplanation(
            kind=ExplanationKind.SENSOR_UNDER_REPORTING,
            channel="Attitude_error_deg",
            supported=supported,
            rule=(
                "Observed pointing error exceeds the OPEN-LOOP upper bound "
                "obtained by integrating the measured body rate. The vehicle "
                "turned further than the rate sensor accounts for, so the sensor "
                "is under-reporting the motion."
            ),
            evidence={
                "observed_error_deg": error.observed,
                "open_loop_bound_deg": error.predicted,
                "exceedance_deg": error.exceedance,
                "mean_body_rate_deg_s": error.extras.get(
                    "mean_body_rate_deg_s"),
            },
            caveat=(
                "A CONSTANT rate-sensor bias cancels out of the rate residual, "
                "because that prediction is anchored on the previous observed "
                "rate. This bound is the only check here that a biased or "
                "failed rate sensor can break, and it detects under-reporting "
                "only — a sensor over-reporting motion makes the bound more "
                "generous and stays undetected."
            ),
        ))

    if soc is not None and soc.status.is_decided:
        explanations.append(ResidualExplanation(
            kind=ExplanationKind.ENERGY_BOOKKEEPING_GAP,
            channel="SoC_pct",
            supported=soc.is_inconsistent,
            rule=(
                "The state-of-charge residual exceeds tolerance: stored energy "
                "moved differently from measured generation minus assumed load."
            ),
            evidence={
                "soc_residual_pct": soc.residual,
                "tolerance_pct": soc.tolerance,
                "net_power_W": soc.extras.get("net_power_W"),
                "energy_direction": soc.extras.get("energy_direction"),
            },
            caveat=(
                "The prediction is inversely proportional to an ASSUMED battery "
                "capacity and uses a constant baseline load, so an unmodelled "
                "load or a payload duty-cycle change presents identically to a "
                "battery fault."
            ),
        ))

    if temperature is not None and temperature.status.is_decided:
        explanations.append(ResidualExplanation(
            kind=ExplanationKind.UNMODELLED_HEAT_PATH,
            channel="Component_temp_C",
            supported=temperature.is_inconsistent,
            rule=(
                "The temperature residual exceeds tolerance: the node moved "
                "differently from what measured heater power and assumed "
                "dissipation predict."
            ),
            evidence={
                "temp_residual_K": temperature.residual,
                "tolerance_K": temperature.tolerance,
                "thermal_direction": temperature.extras.get(
                    "thermal_direction"),
                "steady_state_temp_degC": temperature.extras.get(
                    "steady_state_temp_degC"),
            },
            caveat=(
                "This model does not represent illumination, so a telemetry "
                "window spanning an eclipse transition produces exactly this "
                "residual with no fault present."
            ),
        ))

    # Always offered when anything is inconsistent. With four chosen parameters
    # it is never excluded, and a report that omitted it would overstate what
    # the physics shows.
    any_inconsistent = any(r.is_inconsistent for r in residuals)
    if any_inconsistent:
        explanations.append(ResidualExplanation(
            kind=ExplanationKind.MODEL_PARAMETER_ERROR,
            channel="*",
            supported=True,
            rule=(
                "At least one residual is inconsistent. Four of the ten model "
                "parameters were chosen rather than derived, so a wrong "
                "assumption is always an available explanation and is never "
                "excluded by the evidence."
            ),
            evidence={
                "assumed_parameters": [p.symbol for p in assumed_parameters()],
                "inconsistent_channels": sorted(
                    r.channel for r in residuals if r.is_inconsistent),
            },
            caveat=(
                "This is not a hedge. It is the honest ceiling on what a model "
                "with chosen parameters can conclude, and it is why an "
                "inconsistent residual is a prompt to investigate rather than a "
                "finding about hardware."
            ),
        ))

    return explanations


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6 — THE PIPELINE STAGE
# ═══════════════════════════════════════════════════════════════════════════

def compute_residuals(
    crash_dump: Optional[dict[str, Any]],
    sequence: Optional[StateSequence] = None,
) -> ResidualReport:
    """Run the whole Phase 7 stage: state estimate, prediction, residuals.

        telemetry -> state estimate -> model prediction -> residuals

    Args:
        crash_dump: Any crash dump dict. None or malformed input yields an empty
            report with a warning rather than raising.
        sequence: A pre-built state sequence, to avoid re-estimating when the
            caller already has one.

    Returns:
        A ``ResidualReport``. Deterministic: no randomness, no language model, no
        network access. The same dump yields the same report.

    Each predicted channel is evaluated on ITS OWN step grid: the consecutive
    sample times at which that channel was freshly reported. Telemetry here is
    asynchronous, so a single shared grid would be the wrong shape — preset
    scenario 1 reports the gyro at T-120s, T-60s and T-0s but the wheel only at
    T-0s, and forcing both onto the same steps would throw away the gyro history
    that is present. Auxiliary inputs are carried forward within the staleness
    budget their declared cadence allows.

    Only the LAST step of each channel's grid is reported. Every step could be,
    but the stage's purpose is to characterise the state the spacecraft actually
    reached, and the most recent transition is the one that produced it.
    """
    states = sequence if sequence is not None else estimate_states(crash_dump)

    report = ResidualReport(
        state_count=len(states.states),
        channels_modelled=states.channels_modelled,
        channels_not_modelled=states.channels_ignored,
        limitations=states.limitations,
    )

    warnings = list(states.warnings)

    #: Which model predicts which channel. Kept explicit so a channel cannot be
    #: predicted twice, which ``validate_estimation()`` also checks.
    predictors = (
        ("Gyro_rate_degs", attitude_model.predict_angular_velocity),
        ("Attitude_error_deg", attitude_model.predict_attitude_error),
        ("SoC_pct", power_model.predict_state_of_charge),
        ("V_bat", power_model.predict_terminal_voltage),
        ("Component_temp_C", thermal_model.predict_component_temperature),
    )

    residuals: list[Residual] = []
    steps_evaluated = 0
    skipped: list[str] = []

    for channel, predict_fn in predictors:
        fresh = states.fresh_states_for(channel)
        if len(fresh) < 2:
            skipped.append(
                f"{channel}: reported freshly at {len(fresh)} sample time(s); a "
                f"prediction needs two so there is a value to start from and a "
                f"measured value to compare against"
            )
            continue

        previous, current = fresh[-2], fresh[-1]
        dt_s = (float(current.relative_time_s)      # type: ignore[arg-type]
                - float(previous.relative_time_s))  # type: ignore[arg-type]
        if dt_s <= 0.0:
            skipped.append(
                f"{channel}: the last two fresh samples are {dt_s:.1f}s apart, "
                f"which is not a forward step"
            )
            continue

        steps_evaluated += 1
        residuals.append(
            _to_residual(predict_fn(previous, current, dt_s),
                         previous, current, dt_s))

    report.step_count = steps_evaluated

    # Inconsistent first, then undecidable, then consistent; ties on channel so
    # the order is total and reproducible.
    order = {
        ResidualStatus.INCONSISTENT: 0,
        ResidualStatus.UNDECIDABLE: 1,
        ResidualStatus.CONSISTENT: 2,
    }
    residuals.sort(key=lambda r: (order[r.status], r.channel))

    report.residuals = residuals
    report.explanations = explain_residuals(residuals)

    for message in skipped:
        warnings.append(f"not evaluated — {message}")

    if not residuals:
        warnings.append(
            "No channel was reported freshly at two or more sample times, so no "
            "residual could be computed. This is NOT a clean bill of physical "
            "health: nothing was checked."
        )
        report.warnings = tuple(warnings)
        report.summary = (
            f"No residuals from {len(states.states)} state snapshot(s): no "
            f"modelled channel has two fresh samples to step between."
        )
        return report

    if report.undecidable:
        warnings.append(
            f"{len(report.undecidable)} residual(s) are UNDECIDABLE: "
            f"{', '.join(r.channel for r in report.undecidable)}. No physical "
            f"claim is made about these channels."
        )
    if not any(r.status.is_decided for r in residuals):
        warnings.append(
            "Every residual is UNDECIDABLE, so this dump received no physical "
            "consistency check at all."
        )

    report.warnings = tuple(warnings)

    consistent = len(report.consistent)
    inconsistent = len(report.inconsistent)
    undecidable = len(report.undecidable)
    if inconsistent:
        channels = ", ".join(r.channel for r in report.inconsistent)
        report.summary = (
            f"{inconsistent} residual(s) exceed tolerance ({channels}); "
            f"{consistent} consistent, {undecidable} undecidable across "
            f"{steps_evaluated} step(s). An exceedance indicates disagreement "
            f"with the stated model assumptions, not a confirmed hardware fault."
        )
    elif consistent:
        report.summary = (
            f"{consistent} residual(s) within tolerance, {undecidable} "
            f"undecidable across {steps_evaluated} step(s). Observed behaviour "
            f"is consistent with the simplified models, given their assumptions."
        )
    else:
        report.summary = (
            f"No residual could be decided across {steps_evaluated} step(s); "
            f"{undecidable} undecidable. Nothing was checked."
        )

    return report


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7 — SELF-VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

def validate_estimation() -> dict[str, list[str]]:
    """Check the estimation package for defects that would distort residuals."""
    from app.estimation.parameters import validate_parameters
    from app.estimation.state import MODELLED_CHANNELS
    from app.ingest.channel_dict import is_known_channel

    errors: list[str] = []
    warnings: list[str] = []

    findings = validate_parameters()
    errors.extend(f"parameters: {m}" for m in findings["errors"])
    warnings.extend(f"parameters: {m}" for m in findings["warnings"])

    for channel in MODELLED_CHANNELS:
        if not is_known_channel(channel):
            errors.append(
                f"{channel} is listed as modelled but is not in the channel "
                f"dictionary, so no reading will ever be found for it"
            )

    predicted_channels: set[str] = set()
    for module in (attitude_model, power_model, thermal_model):
        for channel in module.model_status()["predicts"]:
            if channel in predicted_channels:
                errors.append(
                    f"{channel} is predicted by more than one model, so two "
                    f"residuals would be produced for it"
                )
            predicted_channels.add(channel)
            if not is_known_channel(channel):
                errors.append(
                    f"{module.MODEL_NAME} predicts {channel}, which is not in "
                    f"the channel dictionary"
                )

    # Every predicted channel needs a tolerance, or its residual is forever
    # UNDECIDABLE and the model silently never contributes.
    for channel in sorted(predicted_channels):
        if channel not in TOLERANCES:
            errors.append(
                f"{channel} is predicted but has no declared tolerance, so its "
                f"residual can never be decided"
            )

    for channel, spec in sorted(TOLERANCES.items()):
        if channel not in predicted_channels:
            warnings.append(
                f"a tolerance is declared for {channel} but no model predicts "
                f"it, so the tolerance is unused"
            )
        if not 0.0 < spec.span_fraction < 1.0:
            errors.append(
                f"{channel}: span_fraction {spec.span_fraction} outside (0, 1)")
        if spec.dt_growth < 0.0:
            errors.append(
                f"{channel}: negative dt_growth {spec.dt_growth} would tighten "
                f"the tolerance as the step lengthens, which inverts the "
                f"integration-error argument"
            )
        if len(spec.rationale.strip()) < 20:
            errors.append(
                f"{channel}: tolerance rationale too short to be reviewable")
        try:
            spec.floor()
        except ValueError as exc:
            errors.append(f"{channel}: {exc}")

    return {"errors": errors, "warnings": warnings}


def estimation_status() -> dict:
    """Summary for the API, the audit record and tests."""
    findings = validate_estimation()
    from app.estimation.state import state_status

    return {
        "residual_schema_version": RESIDUAL_SCHEMA_VERSION,
        "residual_statuses": [s.value for s in ResidualStatus],
        "explanation_kinds": [k.value for k in ExplanationKind],
        "tolerances": {c: s.as_dict() for c, s in sorted(TOLERANCES.items())},
        "reference_dt_s": REFERENCE_DT_S,
        "models": {
            "attitude": attitude_model.model_status(),
            "power": power_model.model_status(),
            "thermal": thermal_model.model_status(),
        },
        "state": state_status(),
        "parameters": parameter_status(),
        "uses_llm": False,
        "deterministic": True,
        "flight_qualified": False,
        "represents_specific_mission": False,
        "pipeline": (
            "telemetry -> state estimate -> model prediction -> residuals"
        ),
        "claim": (
            "Simplified research-grade physical consistency checking. NOT flight "
            "software, NOT flight-qualified, NOT a model of any specific "
            "spacecraft. A residual shows disagreement with the stated "
            "assumptions rather than with the vehicle, and UNDECIDABLE is never "
            "a passing check."
        ),
        "validation": findings,
    }


def _main() -> int:
    """``python3 -m app.estimation.residuals`` — print and validate."""
    import json

    status = estimation_status()
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
