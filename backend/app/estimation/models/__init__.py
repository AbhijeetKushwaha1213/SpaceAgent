"""
SENTINEL — Simplified Physical Models (app.estimation.models)

Phase 7. Three deliberately crude models that predict what the next telemetry
sample should read, given the previous one.

NOT flight software. NOT flight-qualified. NOT a model of any specific mission.

    attitude.py   single-axis rigid-body momentum exchange
    power.py      generation, load and battery energy balance
    thermal.py    lumped first-order thermal evolution

Common contract
---------------
Every model exposes::

    predict(previous, current, dt_s) -> tuple[ModelPrediction, ...]

and every prediction is either PREDICTED with an equation attached, or
NOT_PREDICTABLE with a reason. There is no third option and no default value: a
model that cannot predict says so, and ``residuals.py`` turns that into an
UNDECIDABLE residual rather than a passing check. That mirrors the tri-state
discipline in Phase 1's condition evaluator, where the alternative — treating
absent data as a satisfied precondition — was the documented failure mode.

Each prediction also carries the parameter symbols it consumed, so a residual can
be traced back to the assumptions behind it without reading any source.

Direction of the step
---------------------
Models predict FORWARD: given the state at sample k and the elapsed time to
sample k+1, they predict the reading at k+1. Nothing here filters, smooths or
predicts backwards, and nothing is fitted to the data it is later compared
against — that would make the residual a measure of the fit rather than of the
physics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class PredictionStatus(str, Enum):
    """Whether a model produced a prediction."""

    PREDICTED = "PREDICTED"
    """A number was produced, and the equation that produced it is attached."""

    NOT_PREDICTABLE = "NOT_PREDICTABLE"
    """No prediction. The inputs were missing, unusable, or the step was
    degenerate. ``reason`` says which, and the resulting residual is UNDECIDABLE
    rather than being reported as consistent."""


class Comparison(str, Enum):
    """How an observation should be compared against a prediction.

    Most predictions are two-sided point estimates. One is not, and pretending
    otherwise would produce false findings on healthy telemetry — see
    ``attitude.py`` on the open-loop attitude error bound.
    """

    TWO_SIDED = "TWO_SIDED"
    """A point prediction. Deviation in either direction counts."""

    UPPER_BOUND = "UPPER_BOUND"
    """The prediction is a ceiling the observation should not exceed. Coming in
    below it is consistent, not a discrepancy."""


@dataclass(frozen=True)
class ModelPrediction:
    """One model's prediction for one channel at one sample time."""

    channel: str
    model: str
    """Module that produced this, e.g. 'app.estimation.models.attitude'."""

    status: PredictionStatus
    predicted: Optional[float]
    unit: str

    equation: str
    """The discretised equation actually evaluated, in the symbols used by
    parameters.py. Required: a prediction whose equation is not stated cannot be
    checked by a reader, only trusted."""

    assumptions: tuple[str, ...] = ()
    """Modelling assumptions this prediction rests on, in plain language."""

    parameters_used: tuple[str, ...] = ()
    """Symbols from parameters.py that fed this prediction, so its dependence on
    assumed values is traceable."""

    inputs: dict[str, Any] = field(default_factory=dict)
    """The observed values consumed, for the audit record. Only values actually
    read from telemetry appear here."""

    comparison: Comparison = Comparison.TWO_SIDED
    reason: Optional[str] = None
    """Why no prediction was produced, when status is NOT_PREDICTABLE."""

    extras: dict[str, Any] = field(default_factory=dict)
    """Derived quantities worth reporting alongside, e.g. the implied
    disturbance torque. Never used to decide consistency."""

    @property
    def is_predicted(self) -> bool:
        return (
            self.status is PredictionStatus.PREDICTED
            and self.predicted is not None
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "model": self.model,
            "status": self.status.value,
            "predicted": self.predicted,
            "unit": self.unit,
            "equation": self.equation,
            "assumptions": list(self.assumptions),
            "parameters_used": list(self.parameters_used),
            "inputs": dict(self.inputs),
            "comparison": self.comparison.value,
            "reason": self.reason,
            "extras": dict(self.extras),
        }


def describe_unusable(estimate: Any, label: str) -> str:
    """Say precisely why a model cannot consume a quantity.

    Distinguishes absent from stale, because they call for different responses: an
    absent channel means the dump does not carry it, whereas a stale one means the
    value exists but is older than its own declared cadence allows. Reporting both
    as 'unavailable' would hide a recoverable gap behind an unrecoverable one.
    """
    if not getattr(estimate, "is_usable", False):
        return f"{label} is absent or unusable ({estimate.notes or 'not reported'})"

    staleness = getattr(estimate, "staleness_s", None) or 0.0
    budget = getattr(estimate, "staleness_budget_s", None)
    if budget is None:
        return f"{label} is unavailable"
    return (
        f"{label} is {staleness:.0f}s stale, beyond the {budget:.0f}s budget its "
        f"declared sampling cadence allows, so it is not a usable starting point "
        f"for this step"
    )


def not_predictable(channel: str, model: str, unit: str, equation: str,
                    reason: str) -> ModelPrediction:
    """Build a NOT_PREDICTABLE result.

    The equation is still recorded. A reader needs to see what WOULD have been
    evaluated to judge whether the missing input matters.
    """
    return ModelPrediction(
        channel=channel, model=model, status=PredictionStatus.NOT_PREDICTABLE,
        predicted=None, unit=unit, equation=equation, reason=reason,
    )


__all__ = [
    "Comparison",
    "ModelPrediction",
    "PredictionStatus",
    "describe_unusable",
    "not_predictable",
]
