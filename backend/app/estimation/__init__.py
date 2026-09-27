"""
SENTINEL — Simplified State Estimation (app.estimation)

Phase 7. Builds a simplified spacecraft state from telemetry, predicts what the
next sample should read, and reports the difference.

    from app.estimation import estimate_states, compute_residuals

    report = compute_residuals(crash_dump)
    for residual in report.residuals:
        print(residual.channel, residual.observed, residual.predicted,
              residual.residual, residual.status)

WHAT THIS IS NOT
----------------
Not flight software. Not flight-qualified. Not a model of any specific
spacecraft or mission. It is a deliberately crude consistency check whose
purpose is to ask whether observed telemetry is compatible with elementary
physics, given a small set of stated assumptions.

Every physical constant it uses is in ``parameters.py``, and four of them were
chosen rather than derived. A residual therefore says:

    the telemetry does not match what THESE ASSUMPTIONS predict

and not:

    the telemetry does not match the spacecraft

``ResidualReport`` carries the assumption set so the distinction travels with
the numbers instead of living only in this docstring.

Why it exists
-------------
Phase 6 can tell which fault signatures match the evidence, but not whether the
evidence is physically coherent. Nothing could previously distinguish a gyro
reading 2 deg/s because the vehicle is genuinely rotating from a gyro reading
2 deg/s because its bias has drifted, since both are just a number in range.
Momentum exchange can tell them apart: a real rotation has to come from
somewhere, and if no wheel moved and no torque was applied then the reading is
not the vehicle.

The audit trail has recorded ``state_estimation`` as NOT_IMPLEMENTED since
Phase 4, alongside a note that absence of a finding there is not evidence the
check would pass. This package is what that entry was waiting for.

Pipeline position
-----------------
    telemetry -> detection -> STATE ESTIMATE -> MODEL PREDICTION -> RESIDUALS
                                    ^                                  |
                                    +-- this package ------------------+

No language model is consulted anywhere in this package, and nothing is sampled.
Given the same crash dump the same residuals come out, byte for byte.

Modules
-------
    parameters.py       every physical constant, with its provenance
    state.py            SpacecraftState and the estimator
    models/attitude.py  single-axis rigid-body momentum exchange
    models/power.py     generation, load and battery energy balance
    models/thermal.py   lumped first-order thermal evolution
    residuals.py        observed - predicted, with tri-state verdicts
"""

from app.estimation.parameters import (  # noqa: F401
    ALL_PARAMETERS,
    PARAMETER_SET_VERSION,
    ModelParameter,
    ParameterSource,
    assumed_parameters,
    parameter_status,
    parameters_affecting,
    validate_parameters,
)
from app.estimation.state import (  # noqa: F401
    STATE_SCHEMA_VERSION,
    AttitudeState,
    BatteryState,
    CommunicationState,
    Estimate,
    QuantitySource,
    ReactionWheelState,
    SpacecraftState,
    StateSequence,
    ThermalState,
    estimate_states,
    state_status,
)
from app.estimation.residuals import (  # noqa: F401
    RESIDUAL_SCHEMA_VERSION,
    Residual,
    ResidualExplanation,
    ResidualReport,
    ResidualStatus,
    compute_residuals,
    estimation_status,
    validate_estimation,
)
from app.estimation.window_adequacy import (  # noqa: F401
    WINDOW_ADEQUACY_VERSION,
    ChannelAdequacy,
    WindowAdequacyReport,
    WindowAdequacyStatus,
    assess_window_adequacy,
    window_adequacy_status,
)

__all__ = [
    "ALL_PARAMETERS",
    "PARAMETER_SET_VERSION",
    "RESIDUAL_SCHEMA_VERSION",
    "STATE_SCHEMA_VERSION",
    "WINDOW_ADEQUACY_VERSION",
    "AttitudeState",
    "BatteryState",
    "ChannelAdequacy",
    "CommunicationState",
    "Estimate",
    "ModelParameter",
    "ParameterSource",
    "QuantitySource",
    "ReactionWheelState",
    "Residual",
    "ResidualExplanation",
    "ResidualReport",
    "ResidualStatus",
    "SpacecraftState",
    "StateSequence",
    "ThermalState",
    "WindowAdequacyReport",
    "WindowAdequacyStatus",
    "assess_window_adequacy",
    "assumed_parameters",
    "compute_residuals",
    "estimate_states",
    "estimation_status",
    "parameter_status",
    "parameters_affecting",
    "state_status",
    "validate_estimation",
    "validate_parameters",
    "window_adequacy_status",
]
