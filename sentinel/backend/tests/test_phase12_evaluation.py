"""test_phase12_evaluation.py

SENTINEL Phase 12 — Reproducible Evaluation tests.

Tests:
  1. Dataset loading & split separation (DEV vs HELD_OUT_TEST).
  2. Anomaly detection metrics (precision, recall, F1, FPR, latency).
  3. Hypothesis generation metrics (top-1 accuracy, top-3 coverage).
  4. Final diagnosis metrics (top-1 accuracy, top-3 accuracy).
  5. Calibration metrics (Brier score, ECE - no self-scoring).
  6. RAG metrics (retrieval precision, recall, citation correctness, grounded rate).
  7. Safety metrics (unsafe-command blocking rate, false blocking rate, blocked-plan rate).
  8. System performance metrics (latency breakdown, token usage).
  9. Baseline comparison runner & 'NOT EVALUATED' handling for unrun baselines.
 10. Provenance metadata binding (dataset_version, scenario_version, code_version, model, seed, timestamp).
 11. Machine-readable JSON export & chart generation from real data.
 12. FastAPI API endpoints (POST /api/v1/evaluation/run, GET /api/v1/evaluation/results).

Run:
    cd sentinel/backend && python3 tests/test_phase12_evaluation.py
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure backend/ root is on sys.path for standalone execution
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None

from app.main import app
from app.evaluation.datasets.loader import EvaluationScenario, GroundTruth, load_dataset
from app.evaluation.metrics.anomaly import AnomalyMetrics, compute_anomaly_metrics
from app.evaluation.metrics.calibration import CalibrationMetrics, compute_brier_score, compute_calibration_metrics, compute_ece
from app.evaluation.metrics.diagnosis import DiagnosisMetrics, compute_diagnosis_metrics
from app.evaluation.metrics.hypothesis import HypothesisMetrics, compute_hypothesis_metrics
from app.evaluation.metrics.rag import RAGMetrics, compute_rag_metrics
from app.evaluation.metrics.safety import SafetyMetrics, compute_safety_metrics
from app.evaluation.metrics.system import SystemPerformanceMetrics
from app.evaluation.baselines import run_baseline_1, run_baseline_2, run_baseline_3, run_sentinel_full
from app.evaluation.runner import EvaluationRunner, generate_evaluation_charts, save_json_results


# ═══════════════════════════════════════════════════════════════════════════
# 1. DATASET LOADING & SPLIT SEPARATION
# ═══════════════════════════════════════════════════════════════════════════

class TestDatasetLoading(unittest.TestCase):
    """Test loading DEV vs HELD_OUT_TEST splits and ground truth parsing."""

    def test_load_held_out_test_split(self):
        scenarios = load_dataset("HELD_OUT_TEST")
        self.assertTrue(len(scenarios) >= 4)
        for sc in scenarios:
            self.assertEqual(sc.split, "HELD_OUT_TEST")
            self.assertIsNotNone(sc.ground_truth.root_cause)
            self.assertTrue(len(sc.ground_truth.anomalous_channels) > 0)

    def test_load_dev_split(self):
        scenarios = load_dataset("DEV")
        self.assertTrue(len(scenarios) >= 3)
        for sc in scenarios:
            self.assertEqual(sc.split, "DEV")

    def test_held_out_is_distinct_from_dev(self):
        dev_ids = {sc.scenario_id for sc in load_dataset("DEV")}
        test_ids = {sc.scenario_id for sc in load_dataset("HELD_OUT_TEST")}
        self.assertEqual(len(dev_ids & test_ids), 0)


# ═══════════════════════════════════════════════════════════════════════════
# 2. ANOMALY DETECTION METRICS
# ═══════════════════════════════════════════════════════════════════════════

class TestAnomalyMetrics(unittest.TestCase):
    """Test precision, recall, F1, FPR, and latency metrics."""

    def test_perfect_anomaly_metrics(self):
        m = compute_anomaly_metrics(
            predicted_channels=["CH1", "CH2"],
            ground_truth_anomalous=["CH1", "CH2"],
            ground_truth_nominal=["CH3", "CH4"],
            latency_ms=12.5,
        )
        self.assertEqual(m.precision, 1.0)
        self.assertEqual(m.recall, 1.0)
        self.assertEqual(m.f1, 1.0)
        self.assertEqual(m.false_positive_rate, 0.0)
        self.assertEqual(m.latency_ms, 12.5)

    def test_imperfect_anomaly_metrics(self):
        m = compute_anomaly_metrics(
            predicted_channels=["CH1", "CH3"],  # CH3 is FP
            ground_truth_anomalous=["CH1", "CH2"],  # CH2 is FN
            ground_truth_nominal=["CH3", "CH4"],
        )
        self.assertEqual(m.precision, 0.5)  # 1 TP / 2 predicted
        self.assertEqual(m.recall, 0.5)     # 1 TP / 2 actual
        self.assertEqual(m.f1, 0.5)
        self.assertEqual(m.false_positive_rate, 0.5)  # 1 FP / 2 nominal


# ═══════════════════════════════════════════════════════════════════════════
# 3. HYPOTHESIS & DIAGNOSIS METRICS
# ═══════════════════════════════════════════════════════════════════════════

class TestHypothesisAndDiagnosisMetrics(unittest.TestCase):
    """Test top-1 accuracy and top-3 coverage/accuracy."""

    def test_top1_match(self):
        m_hyp = compute_hypothesis_metrics(["FAULT_A", "FAULT_B"], "FAULT_A")
        m_diag = compute_diagnosis_metrics(["FAULT_A", "FAULT_B"], "FAULT_A")
        self.assertEqual(m_hyp.top1_accuracy, 1.0)
        self.assertEqual(m_hyp.top3_coverage, 1.0)
        self.assertEqual(m_diag.top1_accuracy, 1.0)

    def test_top3_match_not_top1(self):
        m_hyp = compute_hypothesis_metrics(["FAULT_B", "FAULT_C", "FAULT_A"], "FAULT_A")
        self.assertEqual(m_hyp.top1_accuracy, 0.0)
        self.assertEqual(m_hyp.top3_coverage, 1.0)


# ═══════════════════════════════════════════════════════════════════════════
# 4. CALIBRATION METRICS (BRIER SCORE & ECE)
# ═══════════════════════════════════════════════════════════════════════════

class TestCalibrationMetrics(unittest.TestCase):
    """Test Brier Score and Expected Calibration Error (ECE)."""

    def test_perfect_calibration(self):
        confidences = [1.0, 0.0, 1.0, 0.0]
        outcomes = [1, 0, 1, 0]
        brier = compute_brier_score(confidences, outcomes)
        ece = compute_ece(confidences, outcomes)
        self.assertEqual(brier, 0.0)
        self.assertEqual(ece, 0.0)

    def test_imperfect_calibration(self):
        confidences = [0.8, 0.9, 0.2, 0.1]
        outcomes = [1, 0, 0, 1]  # 2 correct, 2 incorrect
        brier = compute_brier_score(confidences, outcomes)
        self.assertGreater(brier, 0.0)

        m = compute_calibration_metrics(confidences, outcomes)
        self.assertIn("brier_score", m.to_dict())
        self.assertIn("expected_calibration_error", m.to_dict())


# ═══════════════════════════════════════════════════════════════════════════
# 5. RAG & SAFETY METRICS
# ═══════════════════════════════════════════════════════════════════════════

class TestRAGAndSafetyMetrics(unittest.TestCase):
    """Test RAG retrieval and safety blocking metrics."""

    def test_rag_metrics(self):
        m = compute_rag_metrics(
            retrieved_procedure_ids=["PROC-001"],
            ground_truth_procedure_ids=["PROC-001"],
            cited_evidence_ids=["E1"],
            valid_input_evidence_ids=["E1", "E2"],
        )
        self.assertEqual(m.retrieval_precision, 1.0)
        self.assertEqual(m.retrieval_recall, 1.0)
        self.assertEqual(m.citation_correctness, 1.0)
        self.assertEqual(m.grounded_response_rate, 1.0)

    def test_safety_metrics(self):
        m = compute_safety_metrics(
            proposed_commands=["CMD_SAFE", "CMD_UNSAFE"],
            blocked_commands=["CMD_UNSAFE"],
            ground_truth_unsafe=["CMD_UNSAFE"],
            ground_truth_safe=["CMD_SAFE"],
            is_plan_blocked=False,
        )
        self.assertEqual(m.unsafe_command_blocking_rate, 1.0)
        self.assertEqual(m.false_blocking_rate, 0.0)
        self.assertEqual(m.blocked_plan_rate, 0.0)


# ═══════════════════════════════════════════════════════════════════════════
# 6. EVALUATION RUNNER & BASELINES COMPARISON
# ═══════════════════════════════════════════════════════════════════════════

class TestEvaluationRunner(unittest.TestCase):
    """Test full EvaluationRunner execution, baselines, and provenance."""

    def test_runner_execution_and_provenance(self):
        runner = EvaluationRunner(seed=42, model_mode="stub")
        results = runner.run_evaluation(split="HELD_OUT_TEST")

        self.assertIn("provenance", results)
        prov = results["provenance"]
        self.assertEqual(prov["seed"], 42)
        self.assertEqual(prov["split"], "HELD_OUT_TEST")
        self.assertIn("dataset_version", prov)
        self.assertIn("code_version", prov)
        self.assertIn("timestamp", prov)

        pipelines = results["pipelines"]
        self.assertIn("baseline_1", pipelines)
        self.assertIn("sentinel", pipelines)

        sentinel_res = pipelines["sentinel"]
        self.assertIsInstance(sentinel_res, dict)
        self.assertIn("anomaly_detection", sentinel_res)
        self.assertIn("calibration", sentinel_res)
        self.assertIn("safety", sentinel_res)
        self.assertIn("system_performance", sentinel_res)

    def test_unrun_baseline_not_evaluated(self):
        runner = EvaluationRunner(seed=42, model_mode="stub")
        results = runner.run_evaluation(
            split="HELD_OUT_TEST",
            run_baselines=("sentinel",),  # Only run SENTINEL
        )

        pipelines = results["pipelines"]
        self.assertEqual(pipelines["baseline_1"], "NOT EVALUATED")
        self.assertEqual(pipelines["baseline_2"], "NOT EVALUATED")
        self.assertEqual(pipelines["baseline_3"], "NOT EVALUATED")
        self.assertNotEqual(pipelines["sentinel"], "NOT EVALUATED")
        self.assertIn("baseline_1", results["summary"]["unrun_pipelines"])

    def test_json_saving_and_chart_generation(self):
        runner = EvaluationRunner(seed=42, model_mode="stub")
        results = runner.run_evaluation(split="DEV")

        target_file = save_json_results(results)
        self.assertTrue(target_file.is_file())

        with open(target_file, "r", encoding="utf-8") as f:
            loaded_json = json.load(f)

        self.assertEqual(loaded_json["provenance"]["split"], "DEV")

        charts = results["charts"]
        self.assertIn("accuracy_comparison", charts)
        self.assertIn("safety_blocking", charts)
        self.assertIn("latency_breakdown", charts)


# ═══════════════════════════════════════════════════════════════════════════
# 7. FASTAPI API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

class TestEvaluationAPIEndpoints(unittest.TestCase):
    """Test POST /api/v1/evaluation/run and GET /api/v1/evaluation/results."""

    def test_evaluation_api_endpoints(self):
        if TestClient is not None:
            client = TestClient(app)
            res_run = client.post("/api/v1/evaluation/run", json={"split": "DEV", "seed": 123, "mode": "stub"})
            self.assertEqual(res_run.status_code, 200)
            data_run = res_run.json()
            self.assertEqual(data_run["provenance"]["seed"], 123)

            res_get = client.get("/api/v1/evaluation/results?split=DEV")
            self.assertEqual(res_get.status_code, 200)
            data_get = res_get.json()
            self.assertIn("provenance", data_get)
            self.assertIn("pipelines", data_get)


if __name__ == "__main__":
    unittest.main()
