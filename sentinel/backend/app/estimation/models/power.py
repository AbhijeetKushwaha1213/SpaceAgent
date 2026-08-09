"""
SENTINEL — Simplified Power Balance (app/estimation/models/power.py)

Phase 7. Generation against load, integrated into stored energy.

NOT flight software. NOT flight-qualified. NOT a model of any specific mission.


THE PHYSICS
===========

Generation
----------
    (1)   P_gen  =  V_bus * I_sa

Both terms are MEASURED. This is the reason the power model needs no illumination
parameter, no solar array area and no eclipse geometry: whether the vehicle is
sunlit is already reflected in the array current it reports. Introducing a solar
model would mean inventing two constants in order to predict a quantity the
telemetry states directly.

(Whether a near-zero ``I_sa`` is a legitimate eclipse or an array fault is a
different question and belongs to Phase 6, where ``EPS_SOLAR_UNDERVOLT`` scores
the recorded eclipse context against it. This model takes the current as read.)

Load
----
    (2)   P_load  =  P_load_base  +  P_heater

``P_load_base`` is DERIVED in parameters.py from an energy-balance argument: a
healthy spacecraft holding a steady state of charge must consume what it
generates, so the channel dictionary's nominal generation point is its nominal
load. ``P_heater`` is measured, and is drawn separately precisely because it is
the one load the telemetry reports.

Energy balance
--------------
    (3)   P_net  =  P_gen  -  P_load
    (4)   dE/dt  =  P_net

Discretising (4) across one telemetry step and expressing stored energy as a
percentage of capacity gives the STATE OF CHARGE PREDICTION:

    (5)   SoC_pred[k+1]  =  SoC[k]  +  100 * P_net * dt / (3600 * E_cap)

The 3600 converts the capacity from watt-hours to watt-seconds. ``E_cap`` is
ASSUMED, and the SoC residual is inversely proportional to it — the single
widest-reaching assumption in Phase 7. parameters.py says so on the parameter
itself.

Terminal voltage
----------------
A linear open-circuit-voltage map over the declared nominal band:

    (6)   V_bat_pred  =  V_bat_lo  +  (SoC_pred / 100) * (V_bat_hi - V_bat_lo)

Both endpoints are DERIVED from ``V_bat``'s nominal range. Equation (6) is the
weakest prediction in this package and is labelled as such:

  * A real lithium-ion open-circuit curve is markedly non-linear and nearly flat
    across the middle of its range, which is exactly where a spacecraft normally
    operates. A linear map is therefore at its worst in normal operation.
  * There is no internal-resistance term, so the IR drop under load is missing.
    Voltage sags under current draw and recovers when the load drops, and this
    model cannot represent either.

It is retained rather than dropped because a gross voltage inconsistency — a
collapse the state of charge does not account for — is still worth surfacing, and
the wide tolerance in residuals.py reflects how loose the map is.


ASSUMPTIONS, ALL OF THEM
========================
 1. Baseline load is constant. A real load varies with mode: payload duty cycle,
    transmitter on or off, heaters beyond the measured circuit. A mode change
    inside the window appears as a power residual attributed to nothing.
 2. Battery capacity is assumed and fixed. No ageing, no temperature derating.
    Battery_temp_C is carried in the state but does NOT enter this model, so a
    cold battery's reduced usable capacity is not represented.
 3. Perfect conversion. No charge or discharge efficiency, no regulator losses,
    no round-trip loss. Real efficiencies of 85-95% mean this model
    systematically over-predicts stored energy on charge.
 4. Linear OCV against state of charge, with no internal resistance.
 5. The measured array current is the whole of generation. A second array wing or
    an unmonitored string would be invisible.
 6. State of charge is a faithful measure of stored energy. On a real vehicle it
    is itself an onboard estimate, often coulomb-counted with its own drift — so
    comparing a predicted SoC against a reported SoC compares a model against
    another model, not against a measurement.
 7. Forward Euler at telemetry spacing.
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
    BASELINE_ELECTRICAL_LOAD,
    BATTERY_CAPACITY,
    BATTERY_OCV_EMPTY,
    BATTERY_OCV_FULL,
    NOMINAL_BUS_VOLTAGE,
)
from app.estimation.state import SpacecraftState

MODEL_NAME = "app.estimation.models.power"

SECONDS_PER_HOUR = 3600.0

_SOC_ASSUMPTIONS: tuple[str, ...] = (
    "Baseline electrical load is constant; only heater power is treated as a "
    "separate measured load.",
    "Battery capacity is ASSUMED and fixed — no ageing and no temperature "
    "derating, so Battery_temp_C does not enter this prediction.",
    "Perfect energy conversion: no charge/discharge efficiency and no regulator "
    "losses, so stored energy is over-predicted while charging.",
    "The measured array current is the whole of generation.",
    "Reported state of charge is treated as stored energy, though on a real "
    "vehicle it is itself an onboard estimate with its own drift.",
    "Forward-Euler integration across one telemetry step.",
)

_VBAT_ASSUMPTIONS: tuple[str, ...] = (
    "LINEAR open-circuit voltage against state of charge across the declared "
    "nominal band. A real lithium-ion curve is non-linear and nearly flat in "
    "mid-range, so this map is least accurate in normal operation.",
    "No internal resistance, so the IR drop under load is not represented.",
    "Inherits every assumption of the state-of-charge prediction it is built on.",
)


def _net_power(previous: SpacecraftState) -> tuple[
        float | None, dict[str, Any], str | None]:
    """Net power at the previous sample — equations (1) to (3).

    Returns ``(P_net, inputs, reason_if_none)``. The bus voltage falls back to the
    derived nominal only when the sample carries no reading; a measured value is
    always preferred, and the fallback is recorded in the inputs so it is visible
    in the audit record rather than silently applied.
    """
    battery = previous.battery_state
    inputs: dict[str, Any] = {}

    if not battery.array_current.within_budget:
        return None, inputs, (
            f"generation is unknown: at the previous sample "
            f"({previous.timestamp}), "
            + describe_unusable(battery.array_current, "array current")
        )

    # Each input records its OWN provenance rather than a blanket "OBSERVED". A
    # value carried forward from an earlier sample is not a measurement at this
    # sample time, and the audit record must not imply that it is.
    if battery.bus_voltage.within_budget:
        bus_voltage = battery.bus_voltage.value
        inputs["V_bus_source"] = battery.bus_voltage.source.value
        inputs["V_bus_staleness_s"] = battery.bus_voltage.staleness_s
    else:
        bus_voltage = NOMINAL_BUS_VOLTAGE.value
        inputs["V_bus_source"] = "ASSUMED_NOMINAL_MIDPOINT"

    inputs["I_sa_source"] = battery.array_current.source.value
    inputs["I_sa_staleness_s"] = battery.array_current.staleness_s

    generation = bus_voltage * battery.array_current.value

    heater = previous.thermal_state.heater_power
    if heater.within_budget:
        heater_power = heater.value
        inputs["P_heater_source"] = heater.source.value
        inputs["P_heater_staleness_s"] = heater.staleness_s
    else:
        heater_power = 0.0
        inputs["P_heater_source"] = "ABSENT_TREATED_AS_ZERO"

    load = BASELINE_ELECTRICAL_LOAD.value + heater_power

    inputs.update({
        "V_bus_V": bus_voltage,
        "I_sa_A": battery.array_current.value,
        "P_gen_W": generation,
        "P_load_base_W": BASELINE_ELECTRICAL_LOAD.value,
        "P_heater_W": heater_power,
        "P_load_W": load,
        "P_net_W": generation - load,
    })
    return generation - load, inputs, None


def predict_state_of_charge(
    previous: SpacecraftState,
    current: SpacecraftState,
    dt_s: float,
) -> ModelPrediction:
    """Predict state of charge at ``current`` — equation (5)."""
    equation = (
        "SoC_pred[k+1] = SoC[k] + 100 * (P_gen - P_load) * dt / (3600 * E_cap)"
    )

    previous_soc = previous.battery_state.state_of_charge
    if not previous_soc.within_budget:
        return not_predictable(
            "SoC_pct", MODEL_NAME, "%", equation,
            f"at the previous sample ({previous.timestamp}), "
            + describe_unusable(previous_soc, "state of charge"),
        )
    if dt_s <= 0.0:
        return not_predictable(
            "SoC_pct", MODEL_NAME, "%", equation,
            f"non-positive step dt={dt_s}s",
        )

    net_power, inputs, reason = _net_power(previous)
    if net_power is None:
        return not_predictable(
            "SoC_pct", MODEL_NAME, "%", equation,
            reason or "net power could not be computed",
        )

    energy_capacity_ws = BATTERY_CAPACITY.value * SECONDS_PER_HOUR
    delta_soc = 100.0 * net_power * dt_s / energy_capacity_ws
    predicted = previous_soc.value + delta_soc

    inputs.update({"SoC_previous_pct": previous_soc.value, "dt_s": dt_s})

    return ModelPrediction(
        channel="SoC_pct",
        model=MODEL_NAME,
        status=PredictionStatus.PREDICTED,
        predicted=predicted,
        unit="%",
        equation=equation,
        assumptions=_SOC_ASSUMPTIONS,
        parameters_used=(
            BASELINE_ELECTRICAL_LOAD.symbol,
            BATTERY_CAPACITY.symbol,
            NOMINAL_BUS_VOLTAGE.symbol,
        ),
        inputs=inputs,
        comparison=Comparison.TWO_SIDED,
        extras={
            "predicted_soc_change_pct": delta_soc,
            "net_power_W": net_power,
            "energy_direction": (
                "CHARGING" if net_power > 0 else
                "DISCHARGING" if net_power < 0 else "BALANCED"
            ),
            "capacity_caveat": (
                "Inversely proportional to the ASSUMED battery capacity of "
                f"{BATTERY_CAPACITY.value} Wh. A capacity wrong by a factor of "
                "two makes this predicted change wrong by a factor of two."
            ),
        },
    )


def predict_terminal_voltage(
    previous: SpacecraftState,
    current: SpacecraftState,
    dt_s: float,
) -> ModelPrediction:
    """Predict battery terminal voltage at ``current`` — equation (6).

    Built on the state-of-charge prediction, so it inherits every assumption
    there and adds a linear voltage map on top. The weakest prediction in this
    package; see the module docstring.
    """
    equation = (
        "V_bat_pred = V_bat_lo + (SoC_pred / 100) * (V_bat_hi - V_bat_lo)"
    )

    soc_prediction = predict_state_of_charge(previous, current, dt_s)
    if not soc_prediction.is_predicted:
        return not_predictable(
            "V_bat", MODEL_NAME, "V", equation,
            f"depends on the state-of-charge prediction, which was not "
            f"available: {soc_prediction.reason}",
        )

    span = BATTERY_OCV_FULL.value - BATTERY_OCV_EMPTY.value
    fraction = max(0.0, min(100.0, soc_prediction.predicted or 0.0)) / 100.0
    predicted = BATTERY_OCV_EMPTY.value + fraction * span

    return ModelPrediction(
        channel="V_bat",
        model=MODEL_NAME,
        status=PredictionStatus.PREDICTED,
        predicted=predicted,
        unit="V",
        equation=equation,
        assumptions=_VBAT_ASSUMPTIONS + _SOC_ASSUMPTIONS,
        parameters_used=(
            BATTERY_OCV_EMPTY.symbol,
            BATTERY_OCV_FULL.symbol,
            BATTERY_CAPACITY.symbol,
            BASELINE_ELECTRICAL_LOAD.symbol,
        ),
        inputs={
            "SoC_predicted_pct": soc_prediction.predicted,
            "V_bat_lo_V": BATTERY_OCV_EMPTY.value,
            "V_bat_hi_V": BATTERY_OCV_FULL.value,
            "dt_s": dt_s,
        },
        comparison=Comparison.TWO_SIDED,
        extras={
            "soc_clamped": not (
                0.0 <= (soc_prediction.predicted or 0.0) <= 100.0),
            "weakest_prediction": True,
            "why_weak": (
                "A linear map stands in for a non-linear open-circuit curve that "
                "is flattest in the operating mid-range, and there is no "
                "internal-resistance term. Only a gross discrepancy should be "
                "read as meaningful."
            ),
        },
    )


def predict(
    previous: SpacecraftState,
    current: SpacecraftState,
    dt_s: float,
) -> tuple[ModelPrediction, ...]:
    """Every power prediction for one step."""
    return (
        predict_state_of_charge(previous, current, dt_s),
        predict_terminal_voltage(previous, current, dt_s),
    )


def model_status() -> dict[str, Any]:
    """Describe this model, for the API and tests."""
    return {
        "model": MODEL_NAME,
        "predicts": ["SoC_pct", "V_bat"],
        "represents": ["generation", "load", "battery state"],
        "equations": {
            "generation": "P_gen = V_bus * I_sa   (both measured)",
            "load": "P_load = P_load_base + P_heater",
            "net": "P_net = P_gen - P_load",
            "energy": "dE/dt = P_net",
            "soc_prediction": (
                "SoC_pred[k+1] = SoC[k] + 100*P_net*dt / (3600*E_cap)"),
            "voltage_map": (
                "V_bat_pred = V_bat_lo + (SoC_pred/100)*(V_bat_hi - V_bat_lo)"),
        },
        "assumptions": list(_SOC_ASSUMPTIONS) + list(_VBAT_ASSUMPTIONS),
        "needs_illumination_model": False,
        "why_no_illumination_model": (
            "Generation uses the MEASURED array current, so eclipse is already "
            "reflected in the telemetry. A solar model would invent constants to "
            "predict a value the vehicle reports directly."
        ),
        "uses_llm": False,
        "deterministic": True,
        "flight_qualified": False,
        "claim": (
            "An energy bookkeeping check. It can show that a state-of-charge "
            "change is not accounted for by measured generation and assumed "
            "load. It is not a power system model and cannot size a battery, a "
            "bus or an array."
        ),
    }
