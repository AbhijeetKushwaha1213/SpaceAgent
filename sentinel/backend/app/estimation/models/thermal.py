"""
SENTINEL — Simplified Thermal Evolution (app/estimation/models/thermal.py)

Phase 7. One lumped first-order thermal node.

NOT flight software. NOT flight-qualified. NOT a model of any specific mission.


THE PHYSICS
===========

Continuous form
---------------
A single lumped capacitance exchanging heat with an effective sink:

    (1)   C_th * dT/dt  =  Q_in  -  Q_out
    (2)   Q_in          =  Q_int  +  P_heater
    (3)   Q_out         =  k_th * (T - T_sink)

``Q_int`` is internal electrical dissipation, ``P_heater`` is the measured heater
power, ``k_th`` an effective conductance and ``T_sink`` an effective sink
temperature. Substituting:

    (4)   dT/dt  =  [ Q_int + P_heater - k_th * (T - T_sink) ] / C_th

Equation (3) is LINEAR in temperature. Real heat rejection to space is radiative
and goes as ``T^4``, so (3) is a linearisation about the operating point. Over the
80 K nominal band of ``Component_temp_C`` that is a real approximation error, and
it is the reason this model is described as a consistency check rather than a
thermal analysis.

Steady state and the time constant
----------------------------------
Setting ``dT/dt = 0`` in (4):

    (5)   T_ss  =  T_sink  +  (Q_int + P_heater) / k_th

and the node's time constant is

    (6)   tau_th  =  C_th / k_th

``parameters.py`` derives ``Q_int`` by requiring (5) with the heater off to land
exactly on the midpoint of ``Component_temp_C``'s declared nominal band. That
self-consistency matters: a model whose quiescent prediction sat outside the
nominal band would report a standing residual on a perfectly healthy spacecraft,
and ``validate_parameters()`` checks it rather than trusting this paragraph.

Discrete form, which is what is evaluated
-----------------------------------------
Forward Euler on (4):

    (7)   T_pred[k+1]  =  T[k]
                        + dt * [ Q_int + P_heater[k] - k_th*(T[k] - T_sink) ]
                              / C_th

Stability note, and why it is enforced
--------------------------------------
Forward Euler on (7) is stable only while ``dt < 2 * tau_th``. With the assumed
time constant of 600 s that bound is 1200 s, and telemetry in this repository is
spaced 10 to 300 s apart, so it normally holds. It is checked anyway: a longer
step would make the prediction oscillate and diverge, and a diverging prediction
compared against real telemetry would manufacture a large residual that looks
like a spacecraft fault. Beyond the bound the model declines to predict.


ASSUMPTIONS, ALL OF THEM
========================
 1. One lumped node. No spatial distribution, no gradients, no conduction paths
    between components. Panel, battery, OBC and transponder temperatures are
    carried in the state for context and are NOT modelled.
 2. Linear heat rejection, standing in for a radiative ``T^4`` law.
 3. Constant effective sink. It does not vary with attitude, orbital position or
    eclipse, so a thermal change caused by the vehicle turning — one of the
    propagation paths Phase 6 declares from AOCS to TCS — CANNOT be represented
    by this model.
 4. Constant internal dissipation, derived to make the quiescent state nominal.
    A real dissipation tracks electrical load and therefore mode.
 5. Illumination is OUT OF SCOPE and this is a disclosed omission rather than an
    oversight. Modelling absorbed solar flux would need an absorptivity, an
    illuminated area and a sun angle, none of which this repository contains;
    inventing three constants to add one term would weaken every thermal
    residual. The consequence is stated plainly: a telemetry window spanning an
    eclipse transition will show a thermal residual this model attributes to
    nothing.
 6. Heater power measured at the previous sample is constant across the step.
 7. Radiator_eff_pct does NOT enter the model. It is carried in the state and it
    is diagnostically meaningful, but mapping an efficiency percentage onto
    ``k_th`` would require knowing what the percentage is relative to, which the
    channel dictionary does not say. Folding it in on a guess would put an
    invented coupling at the centre of the model.
 8. Forward Euler at telemetry spacing, with the stability bound enforced.
"""

from __future__ import annotations

from typing import Any

from app.estimation.models import (
    Comparison,
    ModelPrediction,
    PredictionStatus,
    describe_unusable,
    not_predictable,
)
from app.estimation.parameters import (
    INTERNAL_DISSIPATION,
    THERMAL_CAPACITANCE,
    THERMAL_CONDUCTANCE,
    THERMAL_SINK_TEMP,
    THERMAL_TIME_CONSTANT,
)
from app.estimation.state import SpacecraftState

MODEL_NAME = "app.estimation.models.thermal"

#: Forward Euler on a first-order node is stable only below this multiple of the
#: time constant. Enforced rather than documented: past it the prediction
#: oscillates and diverges, and a diverging prediction compared against real
#: telemetry fabricates a residual that reads as a spacecraft fault.
EULER_STABILITY_FACTOR = 2.0

_ASSUMPTIONS: tuple[str, ...] = (
    "One lumped node. No spatial gradients and no inter-component conduction; "
    "panel, battery, OBC and transponder temperatures are not modelled.",
    "LINEAR heat rejection k_th*(T - T_sink), standing in for a radiative T^4 "
    "law. An approximation across the 80 K nominal band.",
    "Constant effective sink temperature. It does not vary with attitude or "
    "orbital position, so a thermal change caused by the vehicle turning cannot "
    "be represented.",
    "Constant internal dissipation, derived so the heater-off steady state lands "
    "on the nominal midpoint. A real dissipation tracks electrical mode.",
    "Illumination is out of scope: no absorbed solar term, because absorptivity, "
    "illuminated area and sun angle are all absent from this repository. A "
    "window spanning an eclipse transition will show an unattributed residual.",
    "Heater power measured at the previous sample is held constant across the "
    "step.",
    "Radiator_eff_pct does not enter the model; the channel dictionary does not "
    "state what the percentage is relative to.",
    "Forward-Euler integration, with the dt < 2*tau_th stability bound enforced.",
)


def predict_component_temperature(
    previous: SpacecraftState,
    current: SpacecraftState,
    dt_s: float,
) -> ModelPrediction:
    """Predict the modelled node temperature at ``current`` — equation (7)."""
    equation = (
        "T_pred[k+1] = T[k] + dt * (Q_int + P_heater - k_th*(T[k] - T_sink)) "
        "/ C_th"
    )

    previous_temp = previous.thermal_state.component_temperature
    if not previous_temp.within_budget:
        return not_predictable(
            "Component_temp_C", MODEL_NAME, "degC", equation,
            f"at the previous sample ({previous.timestamp}), "
            + describe_unusable(previous_temp, "component temperature"),
        )
    if dt_s <= 0.0:
        return not_predictable(
            "Component_temp_C", MODEL_NAME, "degC", equation,
            f"non-positive step dt={dt_s}s",
        )

    stability_limit = EULER_STABILITY_FACTOR * THERMAL_TIME_CONSTANT.value
    if dt_s >= stability_limit:
        return not_predictable(
            "Component_temp_C", MODEL_NAME, "degC", equation,
            f"step dt={dt_s:.1f}s reaches the forward-Euler stability bound of "
            f"{stability_limit:.1f}s (2*tau_th). Beyond it the prediction "
            f"diverges, which would fabricate a large residual resembling a "
            f"thermal fault, so no prediction is made.",
        )

    heater = previous.thermal_state.heater_power
    heater_enabled = previous.thermal_state.heater_enabled
    if heater.within_budget:
        heater_power = heater.value
        # The quantity's OWN provenance, not a blanket "OBSERVED". A heater
        # reading carried forward from an earlier sample is not a measurement at
        # this sample time, and the audit record should not imply that it is.
        heater_source = heater.source.value
        heater_staleness = heater.staleness_s
    else:
        heater_power = 0.0
        heater_source = "ABSENT_TREATED_AS_ZERO"
        heater_staleness = None

    heat_in = INTERNAL_DISSIPATION.value + heater_power
    heat_out = THERMAL_CONDUCTANCE.value * (
        previous_temp.value - THERMAL_SINK_TEMP.value)
    rate_of_change = (heat_in - heat_out) / THERMAL_CAPACITANCE.value
    predicted = previous_temp.value + dt_s * rate_of_change

    steady_state = (
        THERMAL_SINK_TEMP.value + heat_in / THERMAL_CONDUCTANCE.value)

    return ModelPrediction(
        channel="Component_temp_C",
        model=MODEL_NAME,
        status=PredictionStatus.PREDICTED,
        predicted=predicted,
        unit="degC",
        equation=equation,
        assumptions=_ASSUMPTIONS,
        parameters_used=(
            INTERNAL_DISSIPATION.symbol,
            THERMAL_CONDUCTANCE.symbol,
            THERMAL_CAPACITANCE.symbol,
            THERMAL_SINK_TEMP.symbol,
        ),
        inputs={
            "T_previous_degC": previous_temp.value,
            "T_previous_source": previous_temp.source.value,
            "P_heater_W": heater_power,
            "P_heater_source": heater_source,
            "P_heater_staleness_s": heater_staleness,
            "heater_enable_flag": (
                heater_enabled.value if heater_enabled.within_budget else None),
            "Q_int_W": INTERNAL_DISSIPATION.value,
            "k_th_W_per_K": THERMAL_CONDUCTANCE.value,
            "C_th_J_per_K": THERMAL_CAPACITANCE.value,
            "T_sink_degC": THERMAL_SINK_TEMP.value,
            "dt_s": dt_s,
        },
        comparison=Comparison.TWO_SIDED,
        extras={
            "net_heat_W": heat_in - heat_out,
            "predicted_rate_degC_per_s": rate_of_change,
            "steady_state_temp_degC": steady_state,
            "thermal_direction": (
                "WARMING" if rate_of_change > 0 else
                "COOLING" if rate_of_change < 0 else "STEADY"
            ),
            "heater_consistency_note": (
                "Heater power drawn while the enable flag is clear indicates a "
                "stuck element. That check belongs to detection and Phase 6 "
                "signature matching, not to this model."
            ),
        },
    )


def predict(
    previous: SpacecraftState,
    current: SpacecraftState,
    dt_s: float,
) -> tuple[ModelPrediction, ...]:
    """Every thermal prediction for one step."""
    return (predict_component_temperature(previous, current, dt_s),)


def model_status() -> dict[str, Any]:
    """Describe this model, for the API and tests."""
    return {
        "model": MODEL_NAME,
        "predicts": ["Component_temp_C"],
        "nodes": 1,
        "represents": ["heat input", "heat rejection", "thermal capacitance"],
        "equations": {
            "energy_balance": "C_th * dT/dt = Q_in - Q_out",
            "heat_in": "Q_in = Q_int + P_heater",
            "heat_out": "Q_out = k_th * (T - T_sink)   (linearised)",
            "rate": "dT/dt = (Q_int + P_heater - k_th*(T - T_sink)) / C_th",
            "steady_state": "T_ss = T_sink + (Q_int + P_heater) / k_th",
            "time_constant": "tau_th = C_th / k_th",
            "prediction": (
                "T_pred[k+1] = T[k] + dt*(Q_int + P_heater "
                "- k_th*(T[k] - T_sink))/C_th"),
        },
        "assumptions": list(_ASSUMPTIONS),
        "euler_stability_bound_s": (
            EULER_STABILITY_FACTOR * THERMAL_TIME_CONSTANT.value),
        "models_illumination": False,
        "why_no_illumination": (
            "Absorptivity, illuminated area and sun angle are all absent from "
            "this repository. Inventing three constants to add one term would "
            "weaken every thermal residual. Disclosed consequence: a window "
            "spanning an eclipse transition shows an unattributed residual."
        ),
        "uses_llm": False,
        "deterministic": True,
        "flight_qualified": False,
        "claim": (
            "A first-order thermal consistency check on one node. It can show "
            "that a temperature change is not accounted for by measured heater "
            "power and assumed dissipation. It is not a thermal analysis and "
            "cannot size a radiator or verify a thermal design."
        ),
    }
