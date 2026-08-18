"""Calibration Metrics (app/evaluation/metrics/calibration.py)

Phase 12 requirement: Measures Brier score and Expected Calibration Error (ECE).
Strict Rule: No self-scoring! Confidence predictions f_i come strictly from model
predictions, and outcomes o_i are binary ground-truth correctness.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CalibrationMetrics:
    brier_score: float
    expected_calibration_error: float  # ECE

    def to_dict(self) -> dict[str, float]:
        return {
            "brier_score": round(self.brier_score, 4),
            "expected_calibration_error": round(self.expected_calibration_error, 4),
        }


def compute_brier_score(confidences: list[float], outcomes: list[int | float]) -> float:
    """Compute Brier Score: Mean squared error between confidence and binary outcome.

    Brier = (1/N) * sum((f_i - o_i)^2)
    Range: [0.0, 1.0]. Lower is better.
    """
    if not confidences or len(confidences) != len(outcomes):
        return 0.0

    n = len(confidences)
    sq_err_sum = sum((f - o) ** 2 for f, o in zip(confidences, outcomes))
    return sq_err_sum / n


def compute_ece(confidences: list[float], outcomes: list[int | float], num_bins: int = 5) -> float:
    """Compute Expected Calibration Error (ECE) across M equal-width confidence bins.

    ECE = sum_{m=1}^M (|B_m|/N) * |acc(B_m) - conf(B_m)|
    """
    if not confidences or len(confidences) != len(outcomes):
        return 0.0

    n = len(confidences)
    bin_size = 1.0 / num_bins
    ece = 0.0

    for m in range(num_bins):
        bin_lower = m * bin_size
        bin_upper = (m + 1) * bin_size

        # Find items in bin B_m
        bin_items = [
            (f, o) for f, o in zip(confidences, outcomes)
            if (bin_lower <= f < bin_upper) or (m == num_bins - 1 and f == bin_upper)
        ]

        if bin_items:
            bin_count = len(bin_items)
            avg_acc = sum(o for _, o in bin_items) / bin_count
            avg_conf = sum(f for f, _ in bin_items) / bin_count
            ece += (bin_count / n) * abs(avg_acc - avg_conf)

    return ece


def compute_calibration_metrics(
    confidences: list[float],
    outcomes: list[int | float],
    num_bins: int = 5,
) -> CalibrationMetrics:
    """Compute Brier score and ECE from confidence predictions and binary outcomes."""
    if not confidences or len(confidences) != len(outcomes):
        return CalibrationMetrics(brier_score=0.0, expected_calibration_error=0.0)

    brier = compute_brier_score(confidences, outcomes)
    ece = compute_ece(confidences, outcomes, num_bins=num_bins)

    return CalibrationMetrics(
        brier_score=brier,
        expected_calibration_error=ece,
    )
