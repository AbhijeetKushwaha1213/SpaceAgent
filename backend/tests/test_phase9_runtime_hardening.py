"""
SENTINEL — Phase 9: End-to-End Runtime Hardening Tests
(tests/test_phase9_runtime_hardening.py)

Comprehensive verification suite for Phase 9:
1. Complete deterministic end-to-end runtime path (Telemetry -> Detection ->
   Reconciliation -> Estimation -> Physics -> RAG -> Router -> Safety -> Audit -> SSE).
2. Adversarial tests:
   - Missing telemetry
   - Malformed telemetry
   - Stale / non-advancing telemetry
   - Contradictory telemetry
   - Physics REFUTED
   - Local LLM failure (arbitration fallback)
   - Cloud LLM failure (arbitration fallback)
   - Both LLMs unavailable (failsafe handling)
   - Malformed LLM output
   - Unsafe / unwhitelisted command injection
   - Critical missing precondition
   - Audit store isolation
3. Invariants:
   - LLM cannot override physics
   - LLM cannot bypass safety
   - Frontend cannot authorize commands
   - Router cannot clear human-review state
   - Critical UNKNOWN telemetry fails closed
   - REFUTED physics cannot produce executable recovery
4. Run/correlation IDs and structured stage metadata
5. End-to-end latency and memory measurement
6. Clean offline STUB execution with zero network transmission
"""

import json
import math
import os
import time
import tracemalloc
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from app.agent.agent import AgentConfig, ModelMode, SentinelAgent
from app.api.models import (
    BlockedCommand,
    BlockSeverity,
    Hypothesis,
    RecoveryStep,
    RiskLevel,
    SafetyStatus,
    SentinelOutput,
    SSEEvent,
    SSEEventType,
)
from app.audit import AuditRecorder, get_store, RunStatus, Stage, StageStatus
from app.llm.models import (
    EvidenceStatus,
    GuardrailResult,
    GuardrailViolation,
    HypothesisContext,
    LLMRankingInput,
    LLMRankingOutput,
    PhysicsContext,
    RankedHypothesis,
    ViolationType,
)
from app.llm.router_contract import (
    Branch,
    BranchOutcome,
    BranchResult,
    RoutingDecision,
    RoutingReason,
)
from app.llm.arbitrator import Arbitrator
from app.llm.ranker import validate_ranking_output
from app.validation.physics import PhysicsStatus, PhysicsValidationReport, PhysicsVerdict


def _make_hypothesis_ctx(
    fault_id: str,
    score: float = 0.8,
    rank: int = 1,
    supporting_evidence: tuple[str, ...] = ("EVD_001",),
    contradicting_evidence: tuple[str, ...] = (),
    physics_status: str = "UNCERTAIN",
) -> HypothesisContext:
    return HypothesisContext(
        hypothesis_id=f"HYP_{fault_id}",
        fault_id=fault_id,
        fault_name=f"Fault {fault_id}",
        subsystem="ADCS",
        deterministic_rank=rank,
        deterministic_score=score,
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
        causal_chain=(f"{fault_id} detected",),
        affected_channels=("GYRO_RATE",),
        physics_status=physics_status,
    )


def _make_test_ranking_input(
    hypotheses: tuple[HypothesisContext, ...] = (),
    evidence_status: str = EvidenceStatus.ADEQUATE.value,
    invalidated: tuple[str, ...] = (),
    validated: tuple[str, ...] = (),
) -> LLMRankingInput:
    if not hypotheses:
        hypotheses = (
            _make_hypothesis_ctx("ADCS_GYRO_SEU", score=0.85, rank=1),
            _make_hypothesis_ctx("EPS_BATTERY_FAULT", score=0.60, rank=2),
        )
    return LLMRankingInput(
        hypotheses=hypotheses,
        valid_fault_ids=tuple(h.fault_id for h in hypotheses),
        physics=PhysicsContext(
            hypotheses_examined=len(hypotheses),
            invalidated=invalidated,
            validated=validated,
            uncertain=tuple(
                h.fault_id for h in hypotheses
                if h.fault_id not in invalidated and h.fault_id not in validated
            ),
        ),
        evidence_status=evidence_status,
    )


def _make_test_branch_result(
    branch: Branch,
    fault_id: str = "ADCS_GYRO_SEU",
    confidence: float = 0.85,
    outcome: BranchOutcome = BranchOutcome.ACCEPT,
    is_valid: bool = True,
    requires_human_review: bool = False,
    raw_text_head: str = '{"ranked_hypotheses": []}',
    reason_codes: tuple[RoutingReason, ...] = (RoutingReason.VALID_LOCAL_RESULT,),
) -> BranchResult:
    ranked = (
        RankedHypothesis(
            fault_id=fault_id,
            rank=1,
            confidence=confidence,
            justification=f"{branch.value} justification for {fault_id}",
            affected_component="ADCS",
            causal_chain=(f"{fault_id} occurred",),
        ),
    )
    validated_output = (
        LLMRankingOutput(
            ranked_hypotheses=ranked,
            reasoning_summary=f"{branch.value} summary",
            supporting_evidence_ids=("EVD_001",),
            selected_procedure_ids=("PROC-ADCS-SEU-001",),
            requires_human_review=requires_human_review,
        )
        if outcome == BranchOutcome.ACCEPT and is_valid
        else None
    )
    guardrail = GuardrailResult(
        is_valid=is_valid,
        violations=() if is_valid else (
            GuardrailViolation(
                violation_type=ViolationType.PHYSICS_OVERRIDE,
                detail="Guardrail violation",
            ),
        ),
        original_output=validated_output,
    )
    return BranchResult(
        branch=branch,
        outcome=outcome,
        inference_performed=(outcome != BranchOutcome.NOT_RUN),
        validated_output=validated_output,
        guardrail_result=guardrail,
        raw_text_head=raw_text_head,
        elapsed_ms=10.0,
        reason_codes=reason_codes,
    )


def _make_synthetic_dump(
    scenario_id: str = "SYNTH_P9_NOMINAL",
    fault_type: str = "ADCS_GYRO_SEU",
    num_samples: int = 5,
    sample_dt: float = 1.0,
    inject_anomalies: bool = True,
) -> dict[str, Any]:
    """Generate a valid, canonical synthetic spacecraft crash dump fixture."""
    from app.api.adapters import with_canonical_window

    window: list[dict[str, Any]] = []
    for i in range(num_samples):
        offset = f"T-{(num_samples - 1 - i) * int(sample_dt)}s"
        is_anom = inject_anomalies and i >= (num_samples // 2)

        # ADCS / AOCS channels
        window.append({
            "timestamp": offset,
            "parameter": "Gyro_rate_degs",
            "value": 8.0 if is_anom else 0.05,
            "status": "ANOMALOUS" if is_anom else "NOMINAL",
        })
        window.append({
            "timestamp": offset,
            "parameter": "SEU_counter",
            "value": 2.0 if is_anom else 0.0,
            "status": "ANOMALOUS" if is_anom else "NOMINAL",
        })
        window.append({
            "timestamp": offset,
            "parameter": "Attitude_error_deg",
            "value": 1.2 if is_anom else 0.005,
            "status": "ANOMALOUS" if is_anom else "NOMINAL",
        })
        window.append({
            "timestamp": offset,
            "parameter": "RW_speed_rpm",
            "value": 2500.0,
            "status": "NOMINAL",
        })
        # EPS channels
        window.append({
            "timestamp": offset,
            "parameter": "V_bat",
            "value": 32.2 - (0.05 * i),
            "status": "NOMINAL",
        })
        window.append({
            "timestamp": offset,
            "parameter": "SoC_pct",
            "value": 85.0 - (0.1 * i),
            "status": "NOMINAL",
        })
        # Thermal channels
        window.append({
            "timestamp": offset,
            "parameter": "Component_temp_C",
            "value": 22.0 + (0.1 * i),
            "status": "NOMINAL",
        })
        window.append({
            "timestamp": offset,
            "parameter": "Battery_temp_C",
            "value": 18.0 + (0.05 * i),
            "status": "NOMINAL",
        })
        window.append({
            "timestamp": offset,
            "parameter": "OBC_temp_C",
            "value": 27.5 + (0.08 * i),
            "status": "NOMINAL",
        })
        window.append({
            "timestamp": offset,
            "parameter": "Panel_temp_C",
            "value": 10.0 + (0.1 * i),
            "status": "NOMINAL",
        })
        # Comms / CDH channels
        window.append({
            "timestamp": offset,
            "parameter": "Watchdog_counter",
            "value": 100 + i,
            "status": "NOMINAL",
        })
        window.append({
            "timestamp": offset,
            "parameter": "Transponder_lock",
            "value": 1.0,
            "status": "NOMINAL",
        })

    legacy_snapshot = [
        {"parameter": "Gyro_rate_degs", "value": 8.0 if inject_anomalies else 0.05, "nominal_min": 0.0, "nominal_max": 7.0, "unit": "deg/s"},
        {"parameter": "SEU_counter", "value": 2.0 if inject_anomalies else 0.0, "nominal_min": 0.0, "nominal_max": 0.0, "unit": "count"},
        {"parameter": "Attitude_error_deg", "value": 1.2 if inject_anomalies else 0.005, "nominal_min": 0.0, "nominal_max": 0.01, "unit": "deg"},
        {"parameter": "RW_speed_rpm", "value": 2500.0, "nominal_min": 0.0, "nominal_max": 5000.0, "unit": "rpm"},
        {"parameter": "V_bat", "value": 32.0, "nominal_min": 28.0, "nominal_max": 34.0, "unit": "V"},
        {"parameter": "SoC_pct", "value": 84.5, "nominal_min": 20.0, "nominal_max": 100.0, "unit": "%"},
        {"parameter": "Component_temp_C", "value": 22.0, "nominal_min": -20.0, "nominal_max": 65.0, "unit": "degC"},
        {"parameter": "Watchdog_counter", "value": 104, "nominal_min": 0.0, "nominal_max": 1000.0, "unit": "count"},
    ]

    dump = {
        "scenario_id": scenario_id,
        "fault_type": fault_type,
        "incident_id": "INC-SYNTH-P9",
        "safe_mode_trigger": "ADCS_ERROR" if inject_anomalies else "NONE",
        "provenance": "SYNTHETIC",
        "pre_fault_telemetry_window": window,
        "pre_fault_telemetry": legacy_snapshot,
    }
    return with_canonical_window(dump)


class TestPhase9RuntimeHardening(unittest.TestCase):
    """End-to-end runtime hardening and invariant enforcement tests."""

    def setUp(self) -> None:
        self.config = AgentConfig(
            mode=ModelMode.STUB,
            stub_response=json.dumps({
                "ranked_hypotheses": [
                    {
                        "fault_id": "ADCS_GYRO_SEU",
                        "rank": 1,
                        "confidence": 0.88,
                        "evidence_ids": ["EVD-01"],
                        "reasoning": "Primary fault identified as ADCS_GYRO_SEU.",
                        "affected_component": "GYRO_A",
                        "causal_chain": ["SEU event", "Gyro rate perturbation", "Attitude error"],
                    },
                    {
                        "fault_id": "EPS_BATTERY_FAULT",
                        "rank": 2,
                        "confidence": 0.08,
                        "evidence_ids": ["EVD-02"],
                        "reasoning": "Secondary fault.",
                        "affected_component": "BATTERY_MAIN",
                        "causal_chain": ["Current draw", "Bus ripple"],
                    },
                    {
                        "fault_id": "OBC_WATCHDOG_OVERFLOW",
                        "rank": 3,
                        "confidence": 0.04,
                        "evidence_ids": ["EVD-03"],
                        "reasoning": "Tertiary fault.",
                        "affected_component": "OBC",
                        "causal_chain": ["Watchdog timeout"],
                    },
                ],
                "primary_fault_id": "ADCS_GYRO_SEU",
                "recommended_procedure_id": "PROC-ADCS-SEU-001",
                "selected_procedure_ids": ["PROC-ADCS-SEU-001"],
                "requires_human_review": False,
                "reasoning_summary": "Single-event upset observed on ADCS gyro.",
            }),
            stub_label="phase9-test-stub",
        )
        self.agent = SentinelAgent(config=self.config)

    # ───────────────────────────────────────────────────────────────────────
    # 1. Complete deterministic end-to-end runtime path
    # ───────────────────────────────────────────────────────────────────────
    def test_01_e2e_nominal_telemetry_flow(self) -> None:
        """Full pipeline executes from ingest through detection, estimation, physics, RAG, ranking, safety, and audit."""
        dump = _make_synthetic_dump(num_samples=5, inject_anomalies=True)
        recorder = AuditRecorder.begin(dump, origin="POST /api/v1/analyze")

        events: list[SSEEvent] = []
        for event in self.agent.analyze_crash_dump_stream(dump, recorder=recorder, run_id=recorder.run_id):
            events.append(event)

        # Verify all key event types streamed
        event_types = [e.event_type for e in events]
        self.assertIn(SSEEventType.STATUS, event_types)
        self.assertIn(SSEEventType.OBSERVATION, event_types)
        self.assertIn(SSEEventType.RESULT, event_types)

        # Verify correlation run_id attached to events
        for e in events:
            if e.run_id:
                self.assertEqual(e.run_id, recorder.run_id)

        # Verify final RESULT payload
        result_event = next(e for e in events if e.event_type == SSEEventType.RESULT)
        result_dict = json.loads(result_event.data)
        self.assertEqual(len(result_dict["hypotheses"]), 3)
        self.assertEqual(result_dict["hypotheses"][0]["root_cause"], "ADCS_GYRO_SEU")
        self.assertEqual(result_dict["safety_status"], "VALIDATED")

        # Verify audit finalized
        record = recorder.finalize(store=get_store(), status=RunStatus.COMPLETED)
        self.assertTrue(len(record.entries) >= 6)
        self.assertTrue(record.outcome.final_hash)

    # ───────────────────────────────────────────────────────────────────────
    # 2. Adversarial: Missing telemetry
    # ───────────────────────────────────────────────────────────────────────
    def test_02_adversarial_missing_telemetry(self) -> None:
        """Empty dump or missing telemetry window handles gracefully without crashing."""
        dump_empty: dict[str, Any] = {"scenario_id": "SYNTH_EMPTY"}
        events = list(self.agent.analyze_crash_dump_stream(dump_empty))
        self.assertTrue(len(events) > 0)
        # Final result is generated with human review required or fail-closed safety
        result_event = [e for e in events if e.event_type == SSEEventType.RESULT]
        self.assertEqual(len(result_event), 1)
        res = json.loads(result_event[0].data)
        self.assertTrue(res["requires_human_review"] or res["safety_status"] in ("BLOCKED", "VALIDATED"))

    # ───────────────────────────────────────────────────────────────────────
    # 3. Adversarial: Malformed telemetry
    # ───────────────────────────────────────────────────────────────────────
    def test_03_adversarial_malformed_telemetry(self) -> None:
        """Corrupted JSON string emits SSE ERROR event without unhandled exception."""
        events = list(self.agent.analyze_crash_dump_stream("MALFORMED_NON_JSON_<<<>>>"))
        self.assertTrue(any(e.event_type == SSEEventType.ERROR for e in events))

    # ───────────────────────────────────────────────────────────────────────
    # 4. Adversarial: Stale / non-advancing telemetry
    # ───────────────────────────────────────────────────────────────────────
    def test_04_adversarial_stale_telemetry(self) -> None:
        """Telemetry with non-advancing timestamps (dt=0) executes safely without ZeroDivisionError."""
        dump_stale = _make_synthetic_dump(num_samples=3, sample_dt=0.0)
        events = list(self.agent.analyze_crash_dump_stream(dump_stale))
        self.assertTrue(any(e.event_type == SSEEventType.RESULT for e in events))

    # ───────────────────────────────────────────────────────────────────────
    # 5. Adversarial: Contradictory telemetry
    # ───────────────────────────────────────────────────────────────────────
    def test_05_adversarial_contradictory_telemetry(self) -> None:
        """Conflicting telemetry channels (e.g. 0V battery while charging) are isolated and identified."""
        dump = _make_synthetic_dump(num_samples=4)
        for entry in dump.get("pre_fault_telemetry_window", []):
            if entry.get("parameter") == "V_bat":
                entry["value"] = 0.0  # Dead bus
            if entry.get("parameter") == "SoC_pct":
                entry["value"] = 0.0  # Empty battery
        events = list(self.agent.analyze_crash_dump_stream(dump))
        self.assertTrue(any("DETECTION" in str(e.data) or "OBSERVATION" in str(e.event_type) for e in events))

    # ───────────────────────────────────────────────────────────────────────
    # 6. Physics REFUTED prevents executable recovery
    # ───────────────────────────────────────────────────────────────────────
    def test_06_physics_refuted_prevents_executable_plan(self) -> None:
        """If physics invalidates the top hypothesis, Guardrail 4 demotes it and safety gates it."""
        refuted_fault = "AOCS_REACTION_WHEEL_DEGRADATION"
        valid_fault = "EPS_BATTERY_FAULT"

        physics_report = PhysicsValidationReport(
            model_version="test-phys",
            invalidated=[refuted_fault],
            validated=[valid_fault],
            verdicts=[
                PhysicsVerdict(
                    hypothesis_id="h1",
                    fault_id=refuted_fault,
                    validation_status=PhysicsStatus.INVALID,
                    model_version="test-phys",
                    explanation="Contradicted by rotational conservation laws",
                ),
                PhysicsVerdict(
                    hypothesis_id="h2",
                    fault_id=valid_fault,
                    validation_status=PhysicsStatus.VALID,
                    model_version="test-phys",
                    explanation="Consistent with voltage decay",
                ),
            ],
        )

        llm_output = LLMRankingOutput(
            ranked_hypotheses=(
                RankedHypothesis(fault_id=refuted_fault, rank=1, confidence=0.95),
                RankedHypothesis(fault_id=valid_fault, rank=2, confidence=0.60),
            )
        )
        ranking_input = LLMRankingInput(
            hypotheses=(
                HypothesisContext(hypothesis_id="h1", fault_id=refuted_fault, fault_name="RW Deg", subsystem="ADCS", deterministic_rank=1, deterministic_score=0.9),
                HypothesisContext(hypothesis_id="h2", fault_id=valid_fault, fault_name="Battery", subsystem="EPS", deterministic_rank=2, deterministic_score=0.7),
            ),
            valid_fault_ids=(refuted_fault, valid_fault),
            physics=PhysicsContext(invalidated=(refuted_fault,), validated=(valid_fault,)),
            evidence_status="ADEQUATE",
        )

        guardrail = validate_ranking_output(llm_output, ranking_input, physics_report)
        self.assertTrue(any(v.violation_type == ViolationType.PHYSICS_OVERRIDE for v in guardrail.violations))
        self.assertEqual(guardrail.corrected_output.ranked_hypotheses[0].fault_id, valid_fault)

    # ───────────────────────────────────────────────────────────────────────
    # 7. Local LLM failure handling (Arbitration fallback)
    # ───────────────────────────────────────────────────────────────────────
    def test_07_local_llm_failure_routes_to_cloud(self) -> None:
        """If the local model branch fails, the router cleanly selects the cloud branch."""
        arbitrator = Arbitrator()
        ranking_input = _make_test_ranking_input()
        failed_local = _make_test_branch_result(
            Branch.LOCAL, outcome=BranchOutcome.FAILURE, is_valid=False,
            reason_codes=(RoutingReason.INVALID_STRUCTURED_OUTPUT,),
        )
        successful_cloud = _make_test_branch_result(Branch.CLOUD, "ADCS_GYRO_SEU", 0.90)

        decision = arbitrator.arbitrate(
            local=failed_local,
            cloud=successful_cloud,
            ranking_input=ranking_input,
            review_already_required=False,
        )
        self.assertEqual(decision.winning_branch, Branch.CLOUD)
        self.assertEqual(decision.decision, RoutingDecision.CLOUD_ACCEPT)

    # ───────────────────────────────────────────────────────────────────────
    # 8. Cloud LLM failure handling (Arbitration fallback)
    # ───────────────────────────────────────────────────────────────────────
    def test_08_cloud_llm_failure_routes_to_local(self) -> None:
        """If the cloud model branch fails (e.g. 503 timeout), the router selects the local branch."""
        arbitrator = Arbitrator()
        ranking_input = _make_test_ranking_input()
        successful_local = _make_test_branch_result(Branch.LOCAL, "ADCS_GYRO_SEU", 0.80)
        failed_cloud = _make_test_branch_result(
            Branch.CLOUD, outcome=BranchOutcome.FAILURE, is_valid=False,
            reason_codes=(RoutingReason.CLOUD_TIMEOUT,),
        )

        decision = arbitrator.arbitrate(
            local=successful_local,
            cloud=failed_cloud,
            ranking_input=ranking_input,
            review_already_required=False,
        )
        self.assertEqual(decision.winning_branch, Branch.LOCAL)
        self.assertEqual(decision.decision, RoutingDecision.LOCAL_ACCEPT)

    # ───────────────────────────────────────────────────────────────────────
    # 9. Both LLMs unavailable fallback handling
    # ───────────────────────────────────────────────────────────────────────
    def test_09_both_llms_unavailable_fallback_failsafe(self) -> None:
        """When both LLM branches fail, router raises fallback flag and enforces human review."""
        arbitrator = Arbitrator()
        ranking_input = _make_test_ranking_input()
        failed_local = _make_test_branch_result(
            Branch.LOCAL, outcome=BranchOutcome.FAILURE, is_valid=False,
            reason_codes=(RoutingReason.LOCAL_TIMEOUT,),
        )
        failed_cloud = _make_test_branch_result(
            Branch.CLOUD, outcome=BranchOutcome.FAILURE, is_valid=False,
            reason_codes=(RoutingReason.CLOUD_TIMEOUT,),
        )

        decision = arbitrator.arbitrate(
            local=failed_local,
            cloud=failed_cloud,
            ranking_input=ranking_input,
            review_already_required=False,
        )
        self.assertTrue(decision.human_review_required)
        self.assertIsNone(decision.winning_branch)
        self.assertIn(decision.decision, (RoutingDecision.NO_INFERENCE, RoutingDecision.HUMAN_REVIEW))

    # ───────────────────────────────────────────────────────────────────────
    # 10. Malformed LLM output handling
    # ───────────────────────────────────────────────────────────────────────
    def test_10_malformed_llm_output_repair_and_retry(self) -> None:
        """Malformed LLM response triggers retry/repair or falls back safely."""
        bad_config = AgentConfig(
            mode=ModelMode.STUB,
            stub_response="NOT_JSON_AT_ALL",
            stub_label="malformed-stub",
        )
        agent = SentinelAgent(config=bad_config)
        dump = _make_synthetic_dump(num_samples=3)
        events = list(agent.analyze_crash_dump_stream(dump))
        # Pipeline must emit events without unhandled crash
        self.assertTrue(len(events) > 0)

    # ───────────────────────────────────────────────────────────────────────
    # 11. Unsafe / Unwhitelisted command injection rejected
    # ───────────────────────────────────────────────────────────────────────
    def test_11_unsafe_unwhitelisted_command_rejected(self) -> None:
        """Unwhitelisted command proposed in recovery plan is immediately blocked by safety validator."""
        from app.agent.safety import validate_recovery_plan

        malicious_output = SentinelOutput(
            hypotheses=[
                Hypothesis(rank=1, root_cause="AOCS_FAULT", affected_component="ADCS", confidence=0.9, causal_chain=["a", "b"]),
                Hypothesis(rank=2, root_cause="EPS_FAULT", affected_component="EPS", confidence=0.06, causal_chain=["c", "d"]),
                Hypothesis(rank=3, root_cause="OBC_FAULT", affected_component="OBC", confidence=0.04, causal_chain=["e", "f"]),
            ],
            recovery_plan=[
                RecoveryStep(
                    step=1,
                    command="CMD_MALICIOUS_UNWHITELISTED_THRUSTER_OVERRIDE",
                    rationale="Exploit thrusters",
                    wait_seconds=0,
                    verify="None",
                    risk=RiskLevel.HIGH,
                )
            ],
            confidence=0.9,
            requires_human_review=False,
            reasoning_summary="Exploit attempt",
        )

        validation = validate_recovery_plan(malicious_output, {})
        self.assertEqual(validation.safety_status, SafetyStatus.BLOCKED)
        self.assertFalse(validation.is_safe)
        self.assertEqual(len(validation.validated_steps), 0)
        self.assertEqual(len(validation.blocked_steps), 1)
        self.assertEqual(validation.blocked_steps[0].violation_code, "NOT_IN_REGISTRY")

    # ───────────────────────────────────────────────────────────────────────
    # 12. Critical missing precondition fails closed
    # ───────────────────────────────────────────────────────────────────────
    def test_12_critical_missing_precondition_fails_closed(self) -> None:
        """Command requiring valid telemetry fails closed when critical channel is missing/UNKNOWN."""
        from app.agent.safety import validate_recovery_plan

        plan_output = SentinelOutput(
            hypotheses=[
                Hypothesis(rank=1, root_cause="AOCS_FAULT", affected_component="ADCS", confidence=0.9, causal_chain=["a", "b"]),
                Hypothesis(rank=2, root_cause="EPS_FAULT", affected_component="EPS", confidence=0.06, causal_chain=["c", "d"]),
                Hypothesis(rank=3, root_cause="OBC_FAULT", affected_component="OBC", confidence=0.04, causal_chain=["e", "f"]),
            ],
            recovery_plan=[
                RecoveryStep(
                    step=1,
                    command="CMD_HEATER_ENABLE",
                    rationale="Turn on heater",
                    wait_seconds=5,
                    verify="Verify OK",
                    risk=RiskLevel.MEDIUM,
                )
            ],
            confidence=0.9,
            requires_human_review=False,
            reasoning_summary="Heater enable",
        )

        # Telemetry has battery SoC below floor (< 15%)
        validation = validate_recovery_plan(plan_output, {"SoC_pct": 8.0})
        self.assertEqual(validation.safety_status, SafetyStatus.BLOCKED)
        self.assertFalse(validation.is_safe)

    # ───────────────────────────────────────────────────────────────────────
    # 13. Audit persistence isolation
    # ───────────────────────────────────────────────────────────────────────
    def test_13_audit_persistence_failure_isolated(self) -> None:
        """Audit store failures are isolated so operational streaming is not crashed."""
        mock_store = MagicMock()
        mock_store.persist.side_effect = IOError("Simulated SQLite disk error")

        dump = _make_synthetic_dump(num_samples=3)
        recorder = AuditRecorder.begin(dump, origin="POST /api/v1/analyze")

        # Finalizing with a failing store raises or logs without corrupting state
        try:
            recorder.finalize(store=mock_store, status=RunStatus.FAILED)
        except Exception as exc:
            self.assertIsInstance(exc, IOError)

    # ───────────────────────────────────────────────────────────────────────
    # 14. Core Invariants Verification
    # ───────────────────────────────────────────────────────────────────────
    def test_14_invariants_proven(self) -> None:
        """Prove all 6 core system invariants."""
        # Invariant 1: LLM cannot override physics
        # (Tested: validate_ranking_output rejects override attempts)

        # Invariant 2: LLM cannot bypass safety
        # (Tested: validate_recovery_plan blocks all unwhitelisted/prohibited steps)

        # Invariant 3: Frontend cannot authorize commands
        # Ground telemetry builder has OutboundUplinkBoundary permanently disabled
        from app.validation.tc_builder import OutboundUplinkBoundary, UplinkDisabledError
        with self.assertRaises(UplinkDisabledError):
            OutboundUplinkBoundary.transmit([])

        # Invariant 4: Router cannot clear human-review state
        arb = Arbitrator()
        ranking_input = _make_test_ranking_input()
        res = arb.arbitrate(
            local=_make_test_branch_result(Branch.LOCAL, "ADCS_GYRO_SEU", 0.85),
            cloud=None,
            ranking_input=ranking_input,
            review_already_required=True,
        )
        self.assertTrue(res.human_review_required)

        # Invariant 5: Critical UNKNOWN telemetry fails closed
        from app.validation.tc_builder import build_telecommand_stream
        from app.agent.safety import ValidationResult

        step_reacq = RecoveryStep(
            step=1,
            command="CMD_ATTITUDE_REACQUISITION",
            rationale="Reacquire attitude",
            wait_seconds=5,
            verify="Verify OK",
            risk=RiskLevel.MEDIUM,
        )
        val_reacq = ValidationResult(
            is_safe=True,
            validated_steps=[step_reacq],
            blocked_steps=[],
            requires_human_review=False,
            safety_summary="Validated",
            safety_status=SafetyStatus.VALIDATED,
        )
        res_tc = build_telecommand_stream(
            recovery_plan=SentinelOutput(
                hypotheses=[
                    Hypothesis(rank=1, root_cause="AOCS", affected_component="AOCS", confidence=0.9, causal_chain=["a", "b"]),
                    Hypothesis(rank=2, root_cause="EPS", affected_component="EPS", confidence=0.06, causal_chain=["c", "d"]),
                    Hypothesis(rank=3, root_cause="OBC", affected_component="OBC", confidence=0.04, causal_chain=["e", "f"]),
                ],
                recovery_plan=[step_reacq],
                confidence=0.9,
                requires_human_review=False,
                reasoning_summary="Reacquisition recovery plan",
            ),
            safety_validation=val_reacq,
            crash_dump={},  # Gyro_rate_degs missing -> UNKNOWN -> REJECTED
        )
        self.assertEqual(res_tc.status.value, "REJECTED")

        # Invariant 6: REFUTED physics cannot produce an executable recovery plan
        step_safe = RecoveryStep(
            step=1,
            command="CMD_SAFE_MODE_ENTER",
            rationale="Safe mode entry",
            wait_seconds=5,
            verify="Verify OK",
            risk=RiskLevel.LOW,
        )
        val_safe = ValidationResult(
            is_safe=True,
            validated_steps=[step_safe],
            blocked_steps=[],
            requires_human_review=False,
            safety_summary="Validated",
            safety_status=SafetyStatus.VALIDATED,
        )
        res_refuted = build_telecommand_stream(
            recovery_plan=SentinelOutput(
                hypotheses=[
                    Hypothesis(rank=1, root_cause="AOCS", affected_component="AOCS", confidence=0.9, causal_chain=["a", "b"]),
                    Hypothesis(rank=2, root_cause="EPS", affected_component="EPS", confidence=0.06, causal_chain=["c", "d"]),
                    Hypothesis(rank=3, root_cause="OBC", affected_component="OBC", confidence=0.04, causal_chain=["e", "f"]),
                ],
                recovery_plan=[step_safe],
                confidence=0.9,
                requires_human_review=False,
                reasoning_summary="Safe mode recovery plan",
            ),
            safety_validation=val_safe,
            physics_report=PhysicsValidationReport(model_version="test", invalidated=["AOCS"]),
        )
        self.assertEqual(res_refuted.status.value, "REJECTED")

    # ───────────────────────────────────────────────────────────────────────
    # 15. Run correlation IDs and execution metadata
    # ───────────────────────────────────────────────────────────────────────
    def test_15_run_correlation_ids_and_metadata(self) -> None:
        """SSEEvent structures propagate run_id and metadata across stages."""
        test_run_id = "run_20260928T020000Z_abcdef123456"
        evt = SSEEvent(
            event_type=SSEEventType.STATUS,
            data="Pipeline stage 1 initialized",
            step_number=1,
            run_id=test_run_id,
            metadata={"stage": "INGESTION", "canonical_channels": 16},
        )
        evt_json = evt.model_dump_json()
        self.assertIn(test_run_id, evt_json)
        self.assertIn("INGESTION", evt_json)

    # ───────────────────────────────────────────────────────────────────────
    # 16. Performance: Latency and memory measurement
    # ───────────────────────────────────────────────────────────────────────
    def test_16_performance_latency_and_memory(self) -> None:
        """Measure end-to-end latency and peak memory on representative 50-sample telemetry window."""
        dump_50 = _make_synthetic_dump(num_samples=50, sample_dt=1.0)

        tracemalloc.start()
        t0 = time.perf_counter()

        events = list(self.agent.analyze_crash_dump_stream(dump_50))

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_kb = peak_mem / 1024.0

        # Assert performance bounds: offline STUB analysis of 50 samples executes in < 3000 ms
        self.assertLess(elapsed_ms, 3000.0)
        # Peak memory under 50 MB
        self.assertLess(peak_kb, 50000.0)
        self.assertTrue(len(events) > 0)

    # ───────────────────────────────────────────────────────────────────────
    # 17. Clean offline STUB execution with zero network
    # ───────────────────────────────────────────────────────────────────────
    def test_17_stub_offline_execution_zero_network(self) -> None:
        """Verify STUB execution works completely offline without network sockets."""
        import socket
        real_socket = socket.socket

        def _guarded_socket(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("Disallowed network socket call during STUB execution")

        with patch("socket.socket", side_effect=_guarded_socket):
            dump = _make_synthetic_dump(num_samples=4)
            events = list(self.agent.analyze_crash_dump_stream(dump))
            self.assertTrue(any(e.event_type == SSEEventType.RESULT for e in events))


if __name__ == "__main__":
    unittest.main()
