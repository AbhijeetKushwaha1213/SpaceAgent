"""SENTINEL Metrics Package (app/evaluation/metrics)

7 Evaluation Categories:
  1. Anomaly Detection (precision, recall, F1, FPR, latency)
  2. Hypothesis Generation (top-1 accuracy, top-3 coverage)
  3. Final Diagnosis (top-1 accuracy, top-3 accuracy)
  4. Calibration (Brier score, ECE)
  5. RAG Retrieval (precision, recall, citation correctness, grounded response rate)
  6. Safety (unsafe-command blocking rate, false blocking rate, blocked-plan rate)
  7. System Performance (latency breakdown, token usage)
"""

from __future__ import annotations
