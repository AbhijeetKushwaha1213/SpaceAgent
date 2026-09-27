"""Anomaly Detection Metrics (app/evaluation/metrics/anomaly.py)

Measures precision, recall, F1 score, false positive rate (FPR), and latency
for telemetry anomaly detectors.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnomalyMetrics:
    precision: float
    recall: float
    f1: float
    false_positive_rate: float
    latency_ms: float

    def to_dict(self) -> dict[str, float]:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "false_positive_rate": round(self.false_positive_rate, 4),
            "latency_ms": round(self.latency_ms, 2),
        }


def compute_anomaly_metrics(
    predicted_channels: set[str] | list[str],
    ground_truth_anomalous: set[str] | list[str],
    ground_truth_nominal: set[str] | list[str],
    latency_ms: float = 0.0,
) -> AnomalyMetrics:
    """Compute anomaly detection metrics against ground truth.

    TP: predicted anomalous & ground truth anomalous
    FP: predicted anomalous & ground truth nominal
    FN: predicted nominal & ground truth anomalous
    TN: predicted nominal & ground truth nominal
    """
    pred_set = set(predicted_channels)
    anom_set = set(ground_truth_anomalous)
    nom_set = set(ground_truth_nominal)

    tp = len(pred_set & anom_set)
    fp = len(pred_set & nom_set)
    fn = len(anom_set - pred_set)
    tn = len(nom_set - pred_set)

    precision = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if not pred_set and not anom_set else 0.0)
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0

    return AnomalyMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        false_positive_rate=fpr,
        latency_ms=latency_ms,
    )
