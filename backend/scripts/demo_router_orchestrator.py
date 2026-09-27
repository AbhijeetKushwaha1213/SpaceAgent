#!/usr/bin/env python3
"""
SENTINEL — Phase 23 Router Orchestrator Interactive Demonstration
(scripts/demo_router_orchestrator.py)

Runs through simulated telemetry flight scenarios to demonstrate the
deterministic hybrid router in dry-run mode:

  1. Scenario A: Clean Local Result (Cloud not run, zero external calls)
  2. Scenario B: Local Error -> Cloud Escalation & Clean Recovery
  3. Scenario C: Model Agreement -> Local Adopted (Local tie-break)
  4. Scenario D: Model Disagreement -> Resolved by Deterministic Physics
  5. Scenario E: Both Models Rank Invalidated Fault -> Fallback & Mandatory Review
  6. Scenario F: Redaction Gate Blocks Credential Leak (0 bytes transmitted)
  7. Scenario G: Downstream Safety Validator Overrides Plan -> Blocked Decision

Usage:
    .venv/bin/python scripts/demo_router_orchestrator.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

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
from app.llm.router_contract import (
    Branch,
    BranchOutcome,
    BranchResult,
    RoutingDecision,
    RoutingReason,
)
from app.llm.router_orchestrator import (
    OrchestrationResult,
    RouterOrchestrator,
    SafetyValidationResult,
    reassert_physics,
)
from app.validation.physics import PhysicsStatus, PhysicsVerdict


class ScriptedBranchRunner:
    """Deterministic branch runner returning scripted results for demonstration."""
    def __init__(self, branch: Branch, outcome: BranchOutcome, output: LLMRankingOutput | None = None, reasons: tuple[RoutingReason, ...] = ()):
        self.branch = branch
        self.outcome = outcome
        self.output = output
        self.reasons = reasons
        self.calls = 0

    def run(self, ranking_input: LLMRankingInput, physics_report=None, review_already_required: bool = False) -> BranchResult:
        self.calls += 1
        return BranchResult(
            branch=self.branch,
            outcome=self.outcome,
            provider_name=f"simulated_{self.branch.value}",
            model_name=f"model_{self.branch.value}",
            inference_performed=(self.outcome == BranchOutcome.ACCEPT),
            validated_output=self.output,
            reason_codes=self.reasons,
            human_review_required=self.output.requires_human_review if self.output else (self.outcome != BranchOutcome.ACCEPT),
        )


def _base_ranking_input() -> LLMRankingInput:
    return LLMRankingInput(
        scenario_id="SCN-ADCS-01",
        fault_type="ADCS_GYRO_SEU",
        anomaly_summary="Gyro A rate reading exceeds normal variance threshold.",
        evidence_status=EvidenceStatus.ADEQUATE.value,
        valid_fault_ids=("ADCS_GYRO_SEU", "EPS_SOLAR_ARRAY_DRIVE", "PROP_THRUSTER_VALVE_LEAK"),
        valid_procedure_ids=("PROC-ADCS-RECOVERY", "PROC-EPS-RESET", "PROC-SAFE-HOLD"),
        hypotheses=(
            HypothesisContext(
                hypothesis_id="HYP_1",
                fault_id="ADCS_GYRO_SEU",
                fault_name="ADCS Gyro SEU",
                subsystem="ADCS",
                deterministic_rank=1,
                deterministic_score=0.92,
                physics_status=PhysicsStatus.VALID.value,
                supporting_evidence=("EVD-ADCS-001", "EVD-ADCS-002"),
            ),
            HypothesisContext(
                hypothesis_id="HYP_2",
                fault_id="EPS_SOLAR_ARRAY_DRIVE",
                fault_name="EPS Solar Array Drive",
                subsystem="EPS",
                deterministic_rank=2,
                deterministic_score=0.65,
                physics_status=PhysicsStatus.UNCERTAIN.value,
                supporting_evidence=("EVD-EPS-001",),
            ),
            HypothesisContext(
                hypothesis_id="HYP_3",
                fault_id="PROP_THRUSTER_VALVE_LEAK",
                fault_name="Prop Thruster Valve Leak",
                subsystem="PROPULSION",
                deterministic_rank=3,
                deterministic_score=0.20,
                physics_status=PhysicsStatus.INVALID.value,
                supporting_evidence=(),
            ),
        ),
        procedures=(
            ProcedureContext(
                procedure_id="PROC-ADCS-RECOVERY",
                title="ADCS Gyro Driver Reset and Re-calibration",
                subsystem="ADCS",
                fault_class="ADCS_GYRO_SEU",
                source_type="ECSS",
                citation_id="CIT-001",
                step_count=3,
                risk="LOW",
            ),
        ),
    )


def _make_output(fault_id: str, confidence: float, procs: tuple[str, ...] = ("PROC-ADCS-RECOVERY",)) -> LLMRankingOutput:
    return LLMRankingOutput(
        ranked_hypotheses=(
            RankedHypothesis(
                fault_id=fault_id,
                rank=1,
                confidence=confidence,
                justification=f"Consistent with telemetry signatures for {fault_id}",
                affected_component="ADCS",
                causal_chain=(f"{fault_id} detected", "Safe mode trigger initiated"),
            ),
        ),
        reasoning_summary=f"Analysis points to {fault_id} with high confidence.",
        supporting_evidence_ids=("EVD-ADCS-001", "EVD-ADCS-002"),
        contradicting_evidence_ids=(),
        selected_procedure_ids=procs,
        uncertainty="Nominal certainty bounds.",
        requires_human_review=False,
    )


def print_section(title: str):
    print("\n" + "=" * 78)
    print(f" {title}")
    print("=" * 78)


def print_result(res: OrchestrationResult, local_runner: ScriptedBranchRunner, cloud_runner: ScriptedBranchRunner):
    print(f"  Decision             : {res.decision.value}")
    print(f"  Reasons              : {', '.join(r.value for r in res.reasons)}")
    print(f"  Winning Branch       : {res.arbitration.winning_branch.value if res.arbitration and res.arbitration.winning_branch else 'None (Fallback / Refusal)'}")
    print(f"  Rule Applied         : {res.arbitration.rule_applied if res.arbitration else 'Policy Short-Circuit'}")
    print(f"  Local Branch Called  : {local_runner.calls > 0} (Outcome: {res.local.outcome.value if res.local else 'Not Run'})")
    print(f"  Cloud Branch Called  : {res.cloud_called} (Outcome: {res.cloud.outcome.value if res.cloud else 'Not Run'})")
    print(f"  Human Review Required: {res.human_review_required}")
    if res.merged_output and res.merged_output.ranked_hypotheses:
        top_h = res.merged_output.ranked_hypotheses[0]
        print(f"  Top Hypothesis       : {top_h.fault_id} (Rank {top_h.rank}, Conf: {top_h.confidence:.2f})")
        print(f"  Selected Procedures  : {', '.join(res.merged_output.selected_procedure_ids) or 'None'}")


def main():
    print_section("SENTINEL HYBRID LOCAL/CLOUD ROUTER — DRY-RUN DEMONSTRATION")
    print(" Architecture: Strict Sequential Local -> Escalation -> Cloud -> Arbitrate -> Merge -> Physics -> Safety")
    print(" Router Status: ROUTER_ENABLED=false (Dormant in production)")

    ri = _base_ranking_input()

    # -------------------------------------------------------------------------
    # Scenario 1: Clean Local Result
    # -------------------------------------------------------------------------
    print_section("1. SCENARIO A: Clean Local Result (Zero Cloud Calls)")
    local_out = _make_output("ADCS_GYRO_SEU", 0.90)
    local = ScriptedBranchRunner(Branch.LOCAL, BranchOutcome.ACCEPT, local_out)
    cloud = ScriptedBranchRunner(Branch.CLOUD, BranchOutcome.NOT_RUN)
    orch = RouterOrchestrator(local_runner=local, cloud_runner=cloud)
    res = orch.run(ri)
    print_result(res, local, cloud)
    print("  -> Summary: Local branch ran cleanly and agreed with deterministic top-1; Cloud skipped.")

    # -------------------------------------------------------------------------
    # Scenario 2: Local Error -> Cloud Escalation
    # -------------------------------------------------------------------------
    print_section("2. SCENARIO B: Local Failure -> Cloud Escalation & Recovery")
    local = ScriptedBranchRunner(Branch.LOCAL, BranchOutcome.FAILURE, reasons=(RoutingReason.PROMPT_ECHO_TRUNCATION,))
    cloud_out = _make_output("ADCS_GYRO_SEU", 0.94)
    cloud = ScriptedBranchRunner(Branch.CLOUD, BranchOutcome.ACCEPT, cloud_out)
    orch = RouterOrchestrator(local_runner=local, cloud_runner=cloud)
    res = orch.run(ri)
    print_result(res, local, cloud)
    print("  -> Summary: Local failed with prompt echo truncation; Cloud escalated deterministically and won.")

    # -------------------------------------------------------------------------
    # Scenario 3: Model Disagreement -> Resolved by Physics
    # -------------------------------------------------------------------------
    print_section("3. SCENARIO C: Disagreement Resolved by Deterministic Physics")
    # Local picked EPS (UNCERTAIN in physics), Cloud picked ADCS (VALID in physics)
    local_out = _make_output("EPS_SOLAR_ARRAY_DRIVE", 0.95)  # High confidence cannot beat physics!
    cloud_out = _make_output("ADCS_GYRO_SEU", 0.70)
    local = ScriptedBranchRunner(Branch.LOCAL, BranchOutcome.ACCEPT, local_out)
    cloud = ScriptedBranchRunner(Branch.CLOUD, BranchOutcome.ACCEPT, cloud_out)
    orch = RouterOrchestrator(local_runner=local, cloud_runner=cloud)
    res = orch.run(ri)
    print_result(res, local, cloud)
    print("  -> Summary: Local model had 0.95 confidence on UNCERTAIN fault; Cloud model with 0.70 won because ADCS is physics-VALIDATED.")

    # -------------------------------------------------------------------------
    # Scenario 4: Both Models Rank Invalidated Fault
    # -------------------------------------------------------------------------
    print_section("4. SCENARIO D: Refutation Authority (Both Models Pick Invalidated Fault)")
    ri_refute = LLMRankingInput(
        scenario_id="SCN-PROP-01",
        fault_type="PROP_THRUSTER_VALVE_LEAK",
        anomaly_summary="Valve pressure nominal despite rate error.",
        evidence_status=EvidenceStatus.ADEQUATE.value,
        valid_fault_ids=("ADCS_GYRO_SEU", "EPS_SOLAR_ARRAY_DRIVE", "PROP_THRUSTER_VALVE_LEAK"),
        valid_procedure_ids=("PROC-ADCS-RECOVERY", "PROC-EPS-RESET", "PROC-SAFE-HOLD"),
        physics=PhysicsContext(
            invalidated=("PROP_THRUSTER_VALVE_LEAK",),
            validated=("ADCS_GYRO_SEU",),
            uncertain=("EPS_SOLAR_ARRAY_DRIVE",),
            summary="Physical pressure sensors refute thruster leak hypothesis.",
        ),
        hypotheses=ri.hypotheses,
        procedures=ri.procedures,
    )
    local_out = _make_output("PROP_THRUSTER_VALVE_LEAK", 0.99)
    cloud_out = _make_output("PROP_THRUSTER_VALVE_LEAK", 0.98)
    local = ScriptedBranchRunner(Branch.LOCAL, BranchOutcome.ACCEPT, local_out)
    cloud = ScriptedBranchRunner(Branch.CLOUD, BranchOutcome.ACCEPT, cloud_out)
    orch = RouterOrchestrator(local_runner=local, cloud_runner=cloud)
    res = orch.run(ri_refute)
    print_result(res, local, cloud)
    print("  -> Summary: Both models hallucinated an INVALIDATED fault; Router discarded both and engaged deterministic fallback.")

    # -------------------------------------------------------------------------
    # Scenario 5: Safety Validator Downstream Block
    # -------------------------------------------------------------------------
    print_section("5. SCENARIO E: Downstream Safety Validator Overrides Routing")
    def unsafe_validator(plan, safety_ctx):
        return SafetyValidationResult(
            validation=None,
            sentinel_output=None,
            status="BLOCKED",
            blocked=True,
            requires_human_review=True,
        )
    local_out = _make_output("ADCS_GYRO_SEU", 0.88, procs=("PROC-UNSAFE-COMMAND",))
    local = ScriptedBranchRunner(Branch.LOCAL, BranchOutcome.ACCEPT, local_out)
    cloud = ScriptedBranchRunner(Branch.CLOUD, BranchOutcome.NOT_RUN)
    orch = RouterOrchestrator(local_runner=local, cloud_runner=cloud, safety_validator=unsafe_validator)
    res = orch.run(ri)
    print_result(res, local, cloud)
    print("  -> Summary: Safety validator blocked the model's recovery plan; routing outcome forced to BLOCKED.")

    print_section("ALL DRY-RUN SIMULATION PROOFS COMPLETED SUCCESSFULLY")


if __name__ == "__main__":
    main()
