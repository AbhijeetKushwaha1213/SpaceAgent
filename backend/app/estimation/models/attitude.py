"""
SENTINEL — Simplified Attitude Dynamics (app/estimation/models/attitude.py)

Phase 7. Single-axis rigid-body momentum exchange between the vehicle and one
reaction wheel.

NOT flight software. NOT flight-qualified. NOT a model of any specific mission.


THE PHYSICS
===========

Continuous form
---------------
For a rigid body carrying one reaction wheel, total angular momentum about the
measured axis is

    (1)   H  =  I_sc * w_sc  +  I_w * w_w

where ``I_sc`` is the spacecraft moment of inertia about that axis, ``w_sc`` the
body rate, ``I_w`` the wheel inertia and ``w_w`` the wheel rate. Angular momentum
changes only under external torque:

    (2)   dH/dt  =  tau_ext

so

    (3)   I_sc * dw_sc/dt  +  I_w * dw_w/dt  =  tau_ext

The torque the wheel applies to the body — the ACTUATOR CONTRIBUTION — is the
reaction to its own acceleration:

    (4)   tau_wheel  =  -I_w * dw_w/dt

and therefore the ANGULAR ACCELERATION of the body is

    (5)   dw_sc/dt  =  (tau_wheel + tau_ext) / I_sc

Equation (5) is the rigid-body rotational equation of motion restricted to one
axis. ``tau_wheel`` is the control torque actually delivered, and ``tau_ext`` is
everything else: aerodynamic drag, solar radiation pressure, gravity gradient,
residual magnetic dipole, and any thruster firing. This model does not attempt to
separate those; they are lumped into a single implied disturbance torque.

Discrete form, which is what is evaluated
-----------------------------------------
Integrating (3) across one telemetry step of length ``dt`` and assuming no
external torque gives the PREDICTION:

    (6)   w_sc_pred[k+1]  =  w_sc[k]  -  (I_w / I_sc) * (w_w[k+1] - w_w[k])

Only the RATIO ``I_w / I_sc`` appears, which is why ``parameters.py`` derives that
ratio from the channel dictionary's declared rate limits rather than inventing
two separate inertias to obtain one number. See the note in Section 4 there.

The residual on (6) is the body rate that the wheel's motion cannot account for.
Rearranging (3), it converts to an implied external torque:

    (7)   tau_ext_implied  =  I_sc * (w_sc_obs[k+1] - w_sc_pred[k+1]) / dt

This is the one place the absolute ``I_sc`` is used, and it is reported for
interpretation only. No consistency verdict depends on it, so an
order-of-magnitude error in ``I_sc`` changes a figure in newton-metres and
nothing else.

Attitude error: a BOUND, not a point prediction
-----------------------------------------------
``Attitude_error_deg`` is the angle between commanded and estimated attitude — a
magnitude, per the channel dictionary. Integrating the body rate gives how far
the vehicle turns:

    (8)   theta_pred[k+1]  =  theta[k]  +  |w_sc_avg| * dt
          with w_sc_avg = (w_sc[k] + w_sc[k+1]) / 2

Equation (8) is an OPEN-LOOP result: it assumes nothing corrects the error. A
working attitude controller drives the error towards zero, so a healthy
spacecraft will sit WELL BELOW (8), and comparing against it two-sided would
report a discrepancy on every correctly controlled vehicle. Modelling the
controller instead is not an option — this repository contains no control gains,
no commanded attitude and no mode information.

So (8) is treated as an UPPER BOUND. Observing less error than the open-loop
integral is consistent, because that is what control does. Observing MORE is a
genuine inconsistency: the pointing error grew faster than the measured body rate
can explain, which means either the rate sensor is under-reporting the motion or
the reported error is not real rotation.

This asymmetry is deliberate and is declared as ``Comparison.UPPER_BOUND`` so
``residuals.py`` cannot accidentally apply a two-sided test to it.


ASSUMPTIONS, ALL OF THEM
========================
 1. Rigid body. No structural flexibility, no fuel slosh, no appendage motion.
 2. One axis. The channel dictionary declares one unlabelled body rate and one
    wheel speed. Cross-axis coupling and gyroscopic terms (w x H) are absent from
    the model because the data needed to compute them is absent from the vehicle.
 3. One wheel, aligned with the measured axis. A real distribution of three or
    four wheels is not represented.
 4. Wheel sizing convention closes the inertia ratio. See parameters.py.
 5. Forward Euler at telemetry spacing. Telemetry here is sampled 10 to 300 s
    apart; over a 300 s step this integration is crude.
 6. No magnetorquers, no thrusters, no momentum dumping. A commanded momentum
    dump would appear as an unexplained wheel change and be reported as an
    implied external torque.
 7. Rate and wheel readings are taken at the same instant. Telemetry is grouped
    by reported offset, so any real skew inside one sample time is invisible.
 8. The gyro measures the body, not itself. A bias makes this false, which is
    precisely what the residual is able to expose.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from app.estimation.models import (
    Comparison,
    ModelPrediction,
    PredictionStatus,
    describe_unusable,
    not_predictable,
)
from app.estimation.parameters import (
    DEG_TO_RAD,
    RPM_TO_RAD_PER_S,
    SPACECRAFT_INERTIA,
    WHEEL_TO_BODY_INERTIA_RATIO,
)
from app.estimation.state import SpacecraftState

MODEL_NAME = "app.estimation.models.attitude"

_RATE_ASSUMPTIONS: tuple[str, ...] = (
    "Rigid body, single unlabelled axis, one reaction wheel aligned with it.",
    "No external torque. Any real disturbance appears in the residual and is "
    "reported as an implied torque rather than being modelled.",
    "No momentum dumping, magnetorquer or thruster activity.",
    "Forward-Euler integration across one telemetry step.",
)

_ERROR_ASSUMPTIONS: tuple[str, ...] = (
    "Open loop: no attitude controller is modelled, because no control gains, "
    "commanded attitude or mode information exist in this repository.",
    "Therefore an UPPER BOUND, not a point prediction. Observing less error "
    "than this is what a working controller does and is consistent.",
    "Small-angle single-axis integration of the measured body rate.",
    "Trapezoidal rate average across the step.",
)


def predict_angular_velocity(
    previous: SpacecraftState,
    current: SpacecraftState,
    dt_s: float,
) -> ModelPrediction:
    """Predict the body rate at ``current`` from momentum exchange — equation (6).

    The prediction consumes the wheel speed at BOTH sample times, because it is
    the wheel's CHANGE that transfers momentum. It consumes the body rate only at
    the previous time; the body rate at the current time is the observation this
    will be compared against, and using it here would make the residual
    identically zero.
    """
    equation = (
        "w_sc_pred[k+1] = w_sc[k] - (I_w/I_sc) * (w_w[k+1] - w_w[k])"
    )

    previous_rate = previous.angular_velocity
    previous_wheel = previous.reaction_wheel_state.speed_rpm
    current_wheel = current.reaction_wheel_state.speed_rpm

    if not previous_rate.within_budget:
        return not_predictable(
            "Gyro_rate_degs", MODEL_NAME, "deg/s", equation,
            f"at the previous sample ({previous.timestamp}), "
            + describe_unusable(previous_rate, "body rate"),
        )
    if not previous_wheel.within_budget or not current_wheel.within_budget:
        return not_predictable(
            "Gyro_rate_degs", MODEL_NAME, "deg/s", equation,
            "wheel speed unusable at one or both sample times, so the "
            "momentum transferred across the step is unknown. "
            + describe_unusable(previous_wheel, f"wheel speed at "
                                f"{previous.timestamp}")
            + "; "
            + describe_unusable(current_wheel, f"wheel speed at "
                                f"{current.timestamp}"),
        )
    if dt_s <= 0.0:
        return not_predictable(
            "Gyro_rate_degs", MODEL_NAME, "deg/s", equation,
            f"non-positive step dt={dt_s}s",
        )

    # Work in rad/s for the momentum exchange, then return deg/s to match the
    # channel. Mixing the two is the classic way to be silently wrong by 57x.
    wheel_delta_rad_s = (
        (current_wheel.value - previous_wheel.value) * RPM_TO_RAD_PER_S.value
    )
    body_delta_rad_s = -WHEEL_TO_BODY_INERTIA_RATIO.value * wheel_delta_rad_s
    body_delta_deg_s = body_delta_rad_s / DEG_TO_RAD.value

    predicted = previous_rate.value + body_delta_deg_s

    # Reported for interpretation, never used to decide consistency.
    wheel_torque_Nm = (
        -SPACECRAFT_INERTIA.value * WHEEL_TO_BODY_INERTIA_RATIO.value
        * wheel_delta_rad_s / dt_s
    )
    angular_acceleration_deg_s2 = body_delta_deg_s / dt_s

    return ModelPrediction(
        channel="Gyro_rate_degs",
        model=MODEL_NAME,
        status=PredictionStatus.PREDICTED,
        predicted=predicted,
        unit="deg/s",
        equation=equation,
        assumptions=_RATE_ASSUMPTIONS,
        parameters_used=(
            WHEEL_TO_BODY_INERTIA_RATIO.symbol,
            RPM_TO_RAD_PER_S.symbol,
            DEG_TO_RAD.symbol,
        ),
        inputs={
            "w_sc_previous_deg_s": previous_rate.value,
            "w_sc_previous_source": previous_rate.source.value,
            "w_wheel_previous_rpm": previous_wheel.value,
            "w_wheel_previous_source": previous_wheel.source.value,
            "w_wheel_previous_staleness_s": previous_wheel.staleness_s,
            "w_wheel_current_rpm": current_wheel.value,
            "w_wheel_current_source": current_wheel.source.value,
            "dt_s": dt_s,
        },
        comparison=Comparison.TWO_SIDED,
        extras={
            "wheel_delta_rpm": current_wheel.value - previous_wheel.value,
            "body_rate_change_from_wheel_deg_s": body_delta_deg_s,
            "predicted_angular_acceleration_deg_s2": (
                angular_acceleration_deg_s2),
            "actuator_torque_Nm": wheel_torque_Nm,
            "actuator_torque_note": (
                "Control torque delivered by the wheel, equation (4), scaled by "
                "the ASSUMED spacecraft inertia. Interpretation only."
            ),
        },
    )


def predict_attitude_error(
    previous: SpacecraftState,
    current: SpacecraftState,
    dt_s: float,
) -> ModelPrediction:
    """Bound the attitude error at ``current`` — equation (8).

    Returns an UPPER_BOUND prediction. See the module docstring on why this is
    not a two-sided point prediction.
    """
    equation = (
        "theta_bound[k+1] = theta[k] + |(w_sc[k] + w_sc[k+1]) / 2| * dt   "
        "(open loop, upper bound)"
    )

    previous_error = previous.attitude.attitude_error
    previous_rate = previous.angular_velocity
    current_rate = current.angular_velocity

    if not previous_error.within_budget:
        return not_predictable(
            "Attitude_error_deg", MODEL_NAME, "deg", equation,
            f"at the previous sample ({previous.timestamp}), "
            + describe_unusable(previous_error, "attitude error"),
        )
    if not previous_rate.within_budget or not current_rate.within_budget:
        return not_predictable(
            "Attitude_error_deg", MODEL_NAME, "deg", equation,
            "body rate unusable at one or both sample times, so the angle "
            "turned across the step cannot be integrated. "
            + describe_unusable(previous_rate, f"body rate at "
                                f"{previous.timestamp}")
            + "; "
            + describe_unusable(current_rate, f"body rate at "
                                f"{current.timestamp}"),
        )
    if dt_s <= 0.0:
        return not_predictable(
            "Attitude_error_deg", MODEL_NAME, "deg", equation,
            f"non-positive step dt={dt_s}s",
        )

    mean_rate = (previous_rate.value + current_rate.value) / 2.0
    angle_turned = abs(mean_rate) * dt_s
    predicted = previous_error.value + angle_turned

    return ModelPrediction(
        channel="Attitude_error_deg",
        model=MODEL_NAME,
        status=PredictionStatus.PREDICTED,
        predicted=predicted,
        unit="deg",
        equation=equation,
        assumptions=_ERROR_ASSUMPTIONS,
        parameters_used=(),
        inputs={
            "theta_previous_deg": previous_error.value,
            "w_sc_previous_deg_s": previous_rate.value,
            "w_sc_current_deg_s": current_rate.value,
            "dt_s": dt_s,
        },
        comparison=Comparison.UPPER_BOUND,
        extras={
            "angle_turned_deg": angle_turned,
            "mean_body_rate_deg_s": mean_rate,
            "interpretation": (
                "Observed error at or below this bound is consistent: a working "
                "controller reduces error. Observed error ABOVE it grew faster "
                "than the measured body rate can explain."
            ),
        },
    )


def implied_disturbance_torque(
    rate_residual_deg_s: Optional[float],
    dt_s: float,
) -> Optional[float]:
    """Convert an unexplained body rate into an implied external torque.

    Equation (7). Returns None when the residual or the step is unusable, rather
    than a zero that would read as "no disturbance".

    Uses the ASSUMED spacecraft inertia, so the magnitude carries that
    assumption's uncertainty. The SIGN and whether it is non-zero do not depend
    on the inertia at all, and those are the parts worth reading.
    """
    if rate_residual_deg_s is None or dt_s <= 0.0:
        return None
    if not math.isfinite(rate_residual_deg_s):
        return None
    residual_rad_s = rate_residual_deg_s * DEG_TO_RAD.value
    return SPACECRAFT_INERTIA.value * residual_rad_s / dt_s


def predict(
    previous: SpacecraftState,
    current: SpacecraftState,
    dt_s: float,
) -> tuple[ModelPrediction, ...]:
    """Every attitude prediction for one step."""
    return (
        predict_angular_velocity(previous, current, dt_s),
        predict_attitude_error(previous, current, dt_s),
    )


def model_status() -> dict[str, Any]:
    """Describe this model, for the API and tests."""
    return {
        "model": MODEL_NAME,
        "predicts": ["Gyro_rate_degs", "Attitude_error_deg"],
        "axes": 1,
        "wheels": 1,
        "represents": [
            "angular velocity",
            "angular acceleration",
            "control torque",
            "actuator contribution",
            "disturbance torque (as an implied residual quantity)",
        ],
        "equations": {
            "momentum": "H = I_sc*w_sc + I_w*w_w",
            "momentum_rate": "I_sc*dw_sc/dt + I_w*dw_w/dt = tau_ext",
            "actuator_torque": "tau_wheel = -I_w * dw_w/dt",
            "angular_acceleration": "dw_sc/dt = (tau_wheel + tau_ext) / I_sc",
            "rate_prediction": (
                "w_sc_pred[k+1] = w_sc[k] - (I_w/I_sc)*(w_w[k+1] - w_w[k])"),
            "implied_torque": (
                "tau_ext = I_sc * (w_sc_obs - w_sc_pred) / dt"),
            "attitude_error_bound": (
                "theta_bound[k+1] = theta[k] + |w_sc_avg| * dt"),
        },
        "assumptions": list(_RATE_ASSUMPTIONS) + list(_ERROR_ASSUMPTIONS),
        "uses_llm": False,
        "deterministic": True,
        "flight_qualified": False,
        "claim": (
            "A single-axis rigid-body consistency check. It can show that a "
            "reported body rate is not accounted for by wheel motion. It cannot "
            "determine attitude, and it is not a substitute for an attitude "
            "determination and control system model."
        ),
    }
