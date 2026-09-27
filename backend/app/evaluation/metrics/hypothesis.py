"""Hypothesis Generation Metrics (app/evaluation/metrics/hypothesis.py)

Measures top-1 accuracy and top-3 coverage for generated fault hypotheses.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HypothesisMetrics:
    top1_accuracy: float
    top3_coverage: float

    def to_dict(self) -> dict[str, float]:
        return {
            "top1_accuracy": round(self.top1_accuracy, 4),
            "top3_coverage": round(self.top3_coverage, 4),
        }


def compute_hypothesis_metrics(
    candidate_fault_ids: list[str],
    ground_truth_root_cause: str,
) -> HypothesisMetrics:
    """Compute hypothesis generation metrics for a single scenario.

    top1_accuracy: 1.0 if candidate_fault_ids[0] == ground_truth_root_cause else 0.0
    top3_coverage: 1.0 if ground_truth_root_cause in candidate_fault_ids[:3] else 0.0
    """
    if not candidate_fault_ids:
        return HypothesisMetrics(top1_accuracy=0.0, top3_coverage=0.0)

    top1 = 1.0 if candidate_fault_ids[0] == ground_truth_root_cause else 0.0
    top3 = 1.0 if ground_truth_root_cause in candidate_fault_ids[:3] else 0.0

    return HypothesisMetrics(
        top1_accuracy=top1,
        top3_coverage=top3,
    )
