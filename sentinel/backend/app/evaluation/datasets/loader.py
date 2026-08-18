"""SENTINEL Evaluation Dataset Loader (app/evaluation/datasets/loader.py)

Phase 12 requirement: Versioned fault scenarios with ground-truth labels.
Strictly separates TRAINING/DEVELOPMENT ("DEV") from HELD-OUT TEST ("HELD_OUT_TEST").
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DATASETS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class GroundTruth:
    """Ground truth annotations for a versioned scenario."""
    anomalous_channels: tuple[str, ...]
    nominal_channels: tuple[str, ...]
    root_cause: str
    candidate_hypotheses: tuple[str, ...]
    relevant_procedure_ids: tuple[str, ...]
    unsafe_commands: tuple[str, ...]
    safe_commands: tuple[str, ...]
    expected_safety_status: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroundTruth:
        return cls(
            anomalous_channels=tuple(data.get("anomalous_channels", [])),
            nominal_channels=tuple(data.get("nominal_channels", [])),
            root_cause=data.get("root_cause", ""),
            candidate_hypotheses=tuple(data.get("candidate_hypotheses", [])),
            relevant_procedure_ids=tuple(data.get("relevant_procedure_ids", [])),
            unsafe_commands=tuple(data.get("unsafe_commands", [])),
            safe_commands=tuple(data.get("safe_commands", [])),
            expected_safety_status=data.get("expected_safety_status", "VALIDATED"),
        )


@dataclass(frozen=True)
class EvaluationScenario:
    """A versioned scenario bundle with telemetry and ground truth."""
    scenario_id: str
    version: str
    split: str
    fault_type: str
    description: str
    ground_truth: GroundTruth
    telemetry: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationScenario:
        return cls(
            scenario_id=str(data.get("scenario_id", "")),
            version=data.get("version", "1.0.0"),
            split=data.get("split", "DEV"),
            fault_type=data.get("fault_type", ""),
            description=data.get("description", ""),
            ground_truth=GroundTruth.from_dict(data.get("ground_truth", {})),
            telemetry=data.get("telemetry", {}),
        )


def load_dataset(split: str = "HELD_OUT_TEST") -> list[EvaluationScenario]:
    """Load evaluation scenarios for the requested split.

    Args:
        split: "DEV" (training/development) or "HELD_OUT_TEST" (held-out test)

    Returns:
        List of EvaluationScenario objects with ground truth.
    """
    split_upper = (split or "HELD_OUT_TEST").upper().strip()

    if split_upper in ("DEV", "DEVELOPMENT", "TRAINING"):
        file_path = _DATASETS_DIR / "dev_scenarios.json"
    else:
        file_path = _DATASETS_DIR / "test_scenarios.json"

    if not file_path.is_file():
        raise FileNotFoundError(f"Evaluation dataset file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        raw_list = json.load(f)

    return [EvaluationScenario.from_dict(item) for item in raw_list]
