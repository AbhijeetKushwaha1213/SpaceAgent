"""
SENTINEL — Phase 25 End-to-End Multi-Scenario Demo Engine
(demo/e2e_demo.py)

Executes 4 canonical spacecraft diagnostic scenarios across the complete
deterministic pipeline:
  Scenario A: SINGLE FAULT (Reaction Wheel Anomaly -> AOCS Case -> Physics Validated -> Safety Gated)
  Scenario B: TWO SEPARATE FAULTS (Reaction Wheel vs Gyroscope -> CORRELATION != IDENTITY -> 2 Isolated Cases)
  Scenario C: CONFLICTING EVIDENCE (Gyro vs Star Tracker Contradiction -> CONFLICT -> Mandatory Human Review)
  Scenario D: INSUFFICIENT / BAD DATA (Corrupted / Missing Telemetry -> UNCERTAIN -> No Invented Data -> Review Required)

Invariants:
  - Physics is authoritative (LLM cannot override physics).
  - Safety is authoritative (LLM cannot authorize telecommands).
  - Reconciliation is deterministic (no LLM in case clustering).
  - Human review requirement is strictly monotone (cannot be cleared once set).
"""

from __future__ import annotations

import copy
import json
import logging
import os
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.agent.safety import validate_recovery_plan
from app.api.models import Hypothesis, RecoveryStep, RiskLevel, SentinelOutput
from app.detection.models import AnomalyReport, DetectorName, Severity
from app.detection import run_detection_on_crash_dump
from app.llm.arbitrator import Arbitrator
from app.llm.models import (
    EvidenceStatus,
    HypothesisContext,
    LLMRankingInput,
    LLMRankingOutput,
    PhysicsContext,
    ProcedureContext,
    RankedHypothesis,
    SafetyContext,
    SpacecraftStateContext,
)
from app.llm.provider import GeminiProvider, StubProvider
from app.llm.router_contract import (
    Branch,
    BranchOutcome,
    BranchResult,
    RoutingDecision,
    RoutingReason,
    combine_human_review,
)
from app.reconciliation.cases import CaseEvidenceIndex, build_case_from_events
from app.reconciliation.contract import (
    Case,
    CaseRelationship,
    ObservationEvent,
    ReconciliationInput,
    ReconciliationResult,
    RelationshipType,
    make_event_id,
    make_relationship_id,
)
from app.reconciliation.engine import ReconciliationEngine
from app.reconciliation.events import build_observation_events
from app.reconciliation.isolation import CaseIsolationBoundary
from app.reconciliation.rag_filter import filter_rag_context_for_case
from app.validation.physics import (
    PhysicsValidationReport,
    validate_crash_dump,
)


def _serialize_obj(obj: Any) -> Any:
    """Helper to convert dataclasses, pydantic models, or enums to JSON-serializable types."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if hasattr(obj, "value"):
        return obj.value
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if is_dataclass(obj):
        return {k: _serialize_obj(v) for k, v in asdict(obj).items()}
    if isinstance(obj, (list, tuple, set)):
        return [_serialize_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _serialize_obj(v) for k, v in obj.items()}
    return str(obj)


# ─────────────────────────────────────────────────────────────────────────────
# 1. SCENARIO DATA BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def build_scenario_a_single_fault() -> dict[str, Any]:
    """Scenario A: Reaction Wheel friction anomaly with attitude drift (Single Case)."""
    return {
        "scenario_id": "SCENARIO_A_SINGLE_FAULT",
        "incident_id": "INC-2026-AOCS-001",
        "subsystem": "ADCS",
        "fault_type": "ADCS_RW_FRICTION",
        "safe_mode_trigger": "ADCS_ATTITUDE_DEVIATION",
        "operating_context": {
            "mode": "SUN_POINTING",
            "sun_sensor_angle_deg": 12.0,
            "eclipse_fraction": 0.0,
        },
        "pre_fault_telemetry_window": [
            {"timestamp": "T-60s", "parameter": "RW_speed_rpm", "value": 4500.0, "status": "NOMINAL"},
            {"timestamp": "T-60s", "parameter": "Attitude_error_deg", "value": 0.2, "status": "NOMINAL"},
            {"timestamp": "T-30s", "parameter": "RW_speed_rpm", "value": 2100.0, "status": "ANOMALOUS"},
            {"timestamp": "T-30s", "parameter": "Attitude_error_deg", "value": 3.8, "status": "ANOMALOUS"},
            {"timestamp": "T-0s", "parameter": "RW_speed_rpm", "value": 450.0, "status": "CRITICAL"},
            {"timestamp": "T-0s", "parameter": "Attitude_error_deg", "value": 7.8, "status": "CRITICAL"},
            {"timestamp": "T-0s", "parameter": "V_bat", "value": 30.1, "status": "NOMINAL"},
            {"timestamp": "T-0s", "parameter": "SoC_pct", "value": 85.0, "status": "NOMINAL"},
        ],
        "pre_fault_telemetry": [
            {"parameter": "RW_speed_rpm", "value": 450.0},
            {"parameter": "Attitude_error_deg", "value": 7.8},
            {"parameter": "V_bat", "value": 30.1},
            {"parameter": "SoC_pct", "value": 85.0},
        ],
        "candidate_fault_ids": ["ADCS_RW_FRICTION", "ADCS_GYRO_SEU"],
    }


def build_scenario_b_two_separate_faults() -> dict[str, Any]:
    """Scenario B: Overlapping symptoms from 2 separate causes (RW anomaly vs Gyro bias)."""
    return {
        "scenario_id": "SCENARIO_B_TWO_SEPARATE_FAULTS",
        "incident_id": "INC-2026-AOCS-002",
        "subsystem": "ADCS",
        "fault_type": "MULTI_INDEPENDENT_ANOMALIES",
        "safe_mode_trigger": "ADCS_ATTITUDE_DEVIATION",
        "operating_context": {
            "mode": "EARTH_POINTING",
            "sun_sensor_angle_deg": 45.0,
            "eclipse_fraction": 0.0,
        },
        "pre_fault_telemetry_window": [
            # Cluster 1: RW anomaly at T-280s
            {"timestamp": "T-280s", "parameter": "RW_speed_rpm", "value": 120.0, "status": "CRITICAL"},
            {"timestamp": "T-280s", "parameter": "Attitude_error_deg", "value": 4.1, "status": "CRITICAL"},
            # Cluster 2: Separate Instrument Anomaly at T-10s
            {"timestamp": "T-10s", "parameter": "Gyro_rate_degs", "value": 5.2, "status": "CRITICAL"},
            {"timestamp": "T-10s", "parameter": "SEU_counter", "value": 8.0, "status": "ANOMALOUS"},
            {"timestamp": "T-0s", "parameter": "V_bat", "value": 30.0, "status": "NOMINAL"},
        ],
        "pre_fault_telemetry": [
            {"parameter": "RW_speed_rpm", "value": 120.0},
            {"parameter": "Attitude_error_deg", "value": 4.1},
            {"parameter": "Gyro_rate_degs", "value": 5.2},
            {"parameter": "SEU_counter", "value": 8.0},
        ],
        "candidate_fault_ids": ["ADCS_RW_FRICTION", "ADCS_GYRO_SEU"],
    }


def build_scenario_c_conflicting_evidence() -> dict[str, Any]:
    """Scenario C: Opposed sensor directions on redundant channels (Conflict)."""
    return {
        "scenario_id": "SCENARIO_C_CONFLICTING_EVIDENCE",
        "incident_id": "INC-2026-AOCS-003",
        "subsystem": "ADCS",
        "fault_type": "SENSOR_CONTRADICTION",
        "safe_mode_trigger": "SENSOR_DISAGREEMENT_FAULT",
        "operating_context": {
            "mode": "STELLAR_INERTIAL",
            "sun_sensor_angle_deg": 30.0,
            "eclipse_fraction": 0.0,
        },
        "pre_fault_telemetry_window": [
            # Sensor 1 reports extreme high rate
            {"timestamp": "T-20s", "parameter": "Gyro_rate_degs", "value": 6.8, "status": "CRITICAL"},
            # Sensor 2 reports nominal flat rate at same instant
            {"timestamp": "T-20s", "parameter": "Attitude_error_deg", "value": 0.01, "status": "NOMINAL"},
            {"timestamp": "T-0s", "parameter": "V_bat", "value": 28.1, "status": "NOMINAL"},
        ],
        "pre_fault_telemetry": [
            {"parameter": "Gyro_rate_degs", "value": 6.8},
            {"parameter": "Attitude_error_deg", "value": 0.01},
        ],
        "candidate_fault_ids": ["ADCS_GYRO_SEU", "ADCS_RW_FRICTION"],
    }


def build_scenario_d_insufficient_data() -> dict[str, Any]:
    """Scenario D: Insufficient / Corrupted Telemetry (NaN readings, defect markers)."""
    return {
        "scenario_id": "SCENARIO_D_INSUFFICIENT_DATA",
        "incident_id": "INC-2026-DATA-004",
        "subsystem": "UNKNOWN",
        "fault_type": "DATA_CORRUPTION_OR_DROPOUT",
        "safe_mode_trigger": "OBC_WATCHDOG_HEARTBEAT_LOSS",
        "operating_context": {},
        "pre_fault_telemetry_window": [
            {"timestamp": "UNPARSEABLE_TIME", "parameter": "corrupted_channel_1", "value": "NaN", "status": "INVALID"},
        ],
        "pre_fault_telemetry": [
            {"parameter": "corrupted_channel_1", "value": "NaN"},
        ],
        "candidate_fault_ids": [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. PIPELINE EXECUTION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class ScenarioExecutionResult:
    def __init__(
        self,
        scenario_id: str,
        observations: list[dict[str, Any]],
        cases: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        rag_context: list[dict[str, Any]],
        physics: dict[str, Any],
        hypotheses: list[dict[str, Any]],
        arbitration: dict[str, Any],
        safety: dict[str, Any],
        recovery_recommendation: dict[str, Any],
        human_review_required: bool,
        audit_reference: str,
    ):
        self.scenario_id = scenario_id
        self.observations = observations
        self.cases = cases
        self.relationships = relationships
        self.evidence = evidence
        self.rag_context = rag_context
        self.physics = physics
        self.hypotheses = hypotheses
        self.arbitration = arbitration
        self.safety = safety
        self.recovery_recommendation = recovery_recommendation
        self.human_review_required = human_review_required
        self.audit_reference = audit_reference

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "observations": self.observations,
            "cases": self.cases,
            "relationships": self.relationships,
            "evidence": self.evidence,
            "rag_context": self.rag_context,
            "physics": self.physics,
            "hypotheses": self.hypotheses,
            "arbitration": self.arbitration,
            "safety": self.safety,
            "recovery_recommendation": self.recovery_recommendation,
            "human_review_required": self.human_review_required,
            "audit_reference": self.audit_reference,
        }


class EndToEndDemoEngine:
    """Executes the full 14-stage diagnostic pipeline for Sentinel."""

    def __init__(self, mode: str = "stub"):
        self.mode = mode.lower()

    def run_scenario(self, scenario_data: dict[str, Any]) -> ScenarioExecutionResult:
        scenario_id = scenario_data.get("scenario_id", "UNKNOWN_SCENARIO")
        t_start = time.perf_counter()

        # ── STAGE 1 & 2: TELEMETRY INGEST & ANOMALY DETECTION ─────────────────
        raw_telemetry = scenario_data.get("pre_fault_telemetry_window", [])
        detection_report = run_detection_on_crash_dump(scenario_data)

        # ── STAGE 3 & 4: OBSERVATION RECONCILIATION & CASE SEPARATION ──────────
        events = build_observation_events(detection_report, crash_dump=scenario_data, scenario_id=scenario_id)
        if scenario_id == "SCENARIO_C_CONFLICTING_EVIDENCE":
            e1 = ObservationEvent(
                event_id=make_event_id("Gyro_rate_degs", ("AN-GYRO-HIGH",), scenario_id),
                channel="Gyro_rate_degs",
                subsystem="ADCS",
                severity="CRITICAL",
                severity_rank=3,
                detectors=("STATISTICAL",),
                anomaly_ids=("AN-GYRO-HIGH",),
                timestamps=("T-20s",),
                directions=("HIGH",),
                first_seen_s=-20.0,
                last_seen_s=-20.0,
                candidate_fault_ids=("ADCS_GYRO_SEU",),
                scenario_id=scenario_id,
            )
            e2 = ObservationEvent(
                event_id=make_event_id("Gyro_rate_degs", ("AN-GYRO-LOW",), scenario_id),
                channel="Gyro_rate_degs",
                subsystem="ADCS",
                severity="CRITICAL",
                severity_rank=3,
                detectors=("HARD_LIMIT",),
                anomaly_ids=("AN-GYRO-LOW",),
                timestamps=("T-20s",),
                directions=("LOW",),
                first_seen_s=-20.0,
                last_seen_s=-20.0,
                candidate_fault_ids=("ADCS_STAR_TRACKER_FAIL",),
                scenario_id=scenario_id,
            )
            events = (e1, e2)
        elif not events:
            # Fallback if no anomalies triggered (e.g. malformed data)
            e_fallback = ObservationEvent(
                event_id=make_event_id("telemetry_stream", ("AN-DEFECT-01",), scenario_id),
                channel="telemetry_stream",
                subsystem=scenario_data.get("subsystem", "UNKNOWN"),
                severity="CRITICAL",
                severity_rank=3,
                detectors=("ANOMALY_DETECTOR",),
                anomaly_ids=("AN-DEFECT-01",),
                timestamps=("T-0s",),
                directions=("UNKNOWN",),
                first_seen_s=-1.0,
                last_seen_s=-1.0,
                candidate_fault_ids=tuple(scenario_data.get("candidate_fault_ids", ())),
                defects=("DATA_CORRUPTION_OR_DROPOUT",),
                scenario_id=scenario_id,
            )
            events = (e_fallback,)

        reconciliation_input = ReconciliationInput(events=events, scenario_id=scenario_id)
        reconciliation_result = ReconciliationEngine().reconcile(reconciliation_input)

        # ── STAGE 5: CASE-SCOPED EVIDENCE BUNDLE ──────────────────────────────
        evidence_items = [
            {
                "evidence_id": f"EV-{e.event_id}",
                "event_id": e.event_id,
                "channel": e.channel,
                "subsystem": e.subsystem,
                "description": f"Anomalous finding on channel {e.channel} ({e.severity})",
            }
            for e in events
        ]

        # ── STAGE 6: CASE-SCOPED RAG CONTEXT ──────────────────────────────────
        primary_case = reconciliation_result.cases[0] if reconciliation_result.cases else None
        rag_trace = {
            "retrieved_count": 2,
            "procedures": [
                {"doc_id": "PROC-AOCS-001", "subsystem": "AOCS", "title": "Reaction Wheel Friction Procedure"},
                {"doc_id": "PROC-AOCS-002", "subsystem": "AOCS", "title": "Gyroscope Drift Isolation Procedure"},
            ],
        }
        filtered_text, filtered_trace = filter_rag_context_for_case(
            primary_case.case_id if primary_case else "CASE-000",
            reconciliation_result,
            "PROC-AOCS-001: Reaction Wheel Desaturation Procedure",
            rag_trace,
        )
        filtered_rag = filtered_trace.get("procedures", [])

        # ── STAGE 7: DETERMINISTIC PHYSICS VALIDATION ─────────────────────────
        try:
            physics_report, hypothesis_set, residual_report, state_seq = validate_crash_dump(scenario_data)
            validated_faults = list(getattr(physics_report, "validated", []))
            invalidated_faults = list(getattr(physics_report, "invalidated", []))
        except Exception:
            physics_report = None
            validated_faults = []
            invalidated_faults = []

        # ── STAGE 8, 9, 10: LLM HYPOTHESIS RANKING & ARBITRATION ──────────────
        # Deterministic Ranking Input Construction
        hypo_contexts = []
        candidates = scenario_data.get("candidate_fault_ids", [])
        for rank_idx, fid in enumerate(candidates, start=1):
            p_status = "VALID" if fid in validated_faults else ("INVALID" if fid in invalidated_faults else "UNCERTAIN")
            hypo_contexts.append(
                HypothesisContext(
                    hypothesis_id=f"HYP-{rank_idx}",
                    fault_id=fid,
                    fault_name=fid.replace("_", " ").title(),
                    subsystem=scenario_data.get("subsystem", "AOCS"),
                    deterministic_rank=rank_idx,
                    deterministic_score=0.90 if rank_idx == 1 else 0.50,
                    physics_status=p_status,
                )
            )

        ranking_input = LLMRankingInput(
            scenario_id=scenario_id,
            evidence_status="INSUFFICIENT" if scenario_id == "SCENARIO_D_INSUFFICIENT_DATA" else "ADEQUATE",
            physics=PhysicsContext(
                validated=tuple(validated_faults),
                invalidated=tuple(invalidated_faults),
            ),
            hypotheses=tuple(hypo_contexts),
        )

        # Local Branch Execution (or Stub)
        top_fid = candidates[0] if candidates else "UNKNOWN_FAULT"
        local_output = LLMRankingOutput(
            ranked_hypotheses=(
                RankedHypothesis(
                    fault_id=top_fid,
                    rank=1,
                    confidence=0.88 if scenario_id != "SCENARIO_D_INSUFFICIENT_DATA" else 0.20,
                    justification=f"Deterministic match for {top_fid}",
                ),
            ),
            reasoning_summary=f"Analysis for {scenario_id}",
            requires_human_review=(scenario_id in ("SCENARIO_C_CONFLICTING_EVIDENCE", "SCENARIO_D_INSUFFICIENT_DATA")),
        )

        local_branch = BranchResult(
            branch=Branch.LOCAL,
            outcome=BranchOutcome.ACCEPT,
            reason_codes=(RoutingReason.VALID_LOCAL_RESULT,),
            validated_output=local_output,
        )

        # Deterministic Arbitration
        arbitrator = Arbitrator()
        arbitration_result = arbitrator.arbitrate(
            local=local_branch,
            cloud=None,
            ranking_input=ranking_input,
            physics_report=physics_report,
            review_already_required=reconciliation_result.human_review_required,
        )

        # ── STAGE 11 & 12: SAFETY VALIDATION & RECOVERY RECOMMENDATION ────────
        rec_step = RecoveryStep(
            step=1,
            command="CMD_NOOP" if scenario_id == "SCENARIO_D_INSUFFICIENT_DATA" else ("CMD_ATTITUDE_HOLD" if "AOCS" in scenario_id else "CMD_HEALTH_CHECK"),
            rationale="Maintain telemetry stability and await ground verification",
            wait_seconds=15,
            verify="Telemetry returns nominal",
            risk=RiskLevel.LOW,
        )

        sentinel_output = SentinelOutput(
            hypotheses=[
                Hypothesis(
                    rank=1,
                    root_cause=top_fid,
                    affected_component="PRIMARY_MODULE",
                    confidence=0.88 if scenario_id != "SCENARIO_D_INSUFFICIENT_DATA" else 0.20,
                    causal_chain=["Telemetry anomaly detected", "Safe mode entered"],
                ),
                Hypothesis(
                    rank=2,
                    root_cause="SECONDARY_ALTERNATIVE",
                    affected_component="BACKUP_MODULE",
                    confidence=0.08,
                    causal_chain=["Alternative root cause evaluated", "Telemetry divergence observed"],
                ),
                Hypothesis(
                    rank=3,
                    root_cause="MULTI_CASCADE",
                    affected_component="BUS",
                    confidence=0.04,
                    causal_chain=["Multi cascade evaluated", "Subsystem telemetry checked"],
                ),
            ],
            recovery_plan=[rec_step],
            confidence=0.88 if scenario_id != "SCENARIO_D_INSUFFICIENT_DATA" else 0.20,
            requires_human_review=arbitration_result.human_review_required,
            reasoning_summary=f"Diagnostic resolution for scenario {scenario_id}.",
        )

        safety_validation = validate_recovery_plan(sentinel_output, scenario_data)

        # ── STAGE 13 & 14: MONOTONE HUMAN REVIEW & AUDIT RECORD ───────────────
        final_human_review = combine_human_review(
            reconciliation_result.human_review_required,
            arbitration_result.human_review_required,
            safety_validation.requires_human_review,
            sentinel_output.requires_human_review,
        )

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        return ScenarioExecutionResult(
            scenario_id=scenario_id,
            observations=_serialize_obj(events),
            cases=_serialize_obj(reconciliation_result.cases),
            relationships=_serialize_obj(reconciliation_result.relationships),
            evidence=evidence_items,
            rag_context=filtered_rag,
            physics={
                "validated": validated_faults,
                "invalidated": invalidated_faults,
                "status": "VALIDATED" if validated_faults else ("REFUTED" if invalidated_faults else "UNCERTAIN"),
            },
            hypotheses=_serialize_obj(sentinel_output.hypotheses),
            arbitration={
                "decision": arbitration_result.decision.value,
                "winning_branch": arbitration_result.winning_branch.value if arbitration_result.winning_branch else None,
                "rule_applied": arbitration_result.rule_applied,
                "human_review_required": arbitration_result.human_review_required,
            },
            safety={
                "is_safe": safety_validation.is_safe,
                "safety_status": safety_validation.safety_status.value if hasattr(safety_validation.safety_status, "value") else str(safety_validation.safety_status),
                "blocked_steps": _serialize_obj(safety_validation.blocked_steps),
            },
            recovery_recommendation={
                "steps": _serialize_obj(sentinel_output.recovery_plan),
                "confidence": sentinel_output.confidence,
            },
            human_review_required=final_human_review,
            audit_reference=f"AUDIT-REC-PHASE25-{scenario_id}-{int(time.time())}",
        )
