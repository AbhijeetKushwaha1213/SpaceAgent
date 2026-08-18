"""Final Diagnosis Metrics (app/evaluation/metrics/diagnosis.py)

Measures top-1 accuracy and top-3 accuracy for final ranked diagnoses.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiagnosisMetrics:
    top1_accuracy: float
    top3_accuracy: float

    def to_dict(self) -> dict[str, float]:
        return {
            "top1_accuracy": round(self.top1_accuracy, 4),
            "top3_accuracy": round(self.top3_accuracy, 4),
        }


def compute_diagnosis_metrics(
    ranked_fault_ids: list[str],
    ground_truth_root_cause: str,
) -> DiagnosisMetrics:
    """Compute final diagnosis metrics for a single scenario.

    top1_accuracy: 1.0 if ranked_fault_ids[0] == ground_truth_root_cause else 0.0
    top3_accuracy: 1.0 if ground_truth_root_cause in ranked_fault_ids[:3] else 0.0
    """
    if not ranked_fault_ids:
        return DiagnosisMetrics(top1_accuracy=0.0, top3_accuracy=0.0)

    top1 = 1.0 if ranked_fault_ids[0] == ground_truth_root_cause else 0.0
    top3 = 1.0 if ground_truth_root_cause in ranked_fault_ids[:3] else 0.0

    return DiagnosisMetrics(
        top1_accuracy=top1,
        top3_accuracy=top3,
    )
