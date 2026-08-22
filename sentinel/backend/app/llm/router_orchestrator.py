"""
SENTINEL — Hybrid Router Orchestrator, DRY-RUN / SIMULATION ONLY
(app/llm/router_orchestrator.py)

Phase 23 Step 5.  The sequencing-only state machine that wires the already
implemented deterministic components into one hybrid-routing topology:

    Policy
     ↓
    Local
     ↓
    Escalate?
     ↓
    Cloud
     ↓
    Arbitrate
     ↓
    Merge
     ↓
    Physics Recheck
     ↓
    Safety
     ↓
    Human Review
     ↓
    Final Output

This module contains NO new physics logic, NO safety logic, NO ranking
logic, and NO LLM reasoning.  It only sequences existing components:

    BranchPolicy            app/llm/branch_policy.py
    LocalBranchRunner       app/llm/local_branch.py
    CloudBranchRunner       app/llm/cloud_branch.py   (owns the redaction gate)
    Arbitrator              app/llm/arbitrator.py
    MergeResolver           app/llm/merge_resolver.py
    reconcile_llm_claim     app/validation/physics.py (existing reassertion)
    validate_recovery_plan  app/agent/safety.py       (existing validator)

Hard constraints honoured here:

    * ROUTER_ENABLED=false.  Nothing in the production path (app/agent/
      agent.py) invokes this orchestrator; its existence changes no
      runtime behaviour.
    * No parallel execution.  The state machine is strictly sequential
      LOCAL → optional CLOUD escalation, with a hard budget of ONE cloud
      call per orchestration (Phase 22 T7).
    * No ModelMode.HYBRID.  The orchestrator talks to branch ADAPTERS
      (runners), never to a provider directly.
    * Cloud escalation happens ONLY for deterministic reasons already
      represented by the BranchResult contract (prompt echo, invalid
      structured output, evidence failure, insufficient evidence,
      procedure invalidity, physics conflict, timeout, unavailable,
      explicit escalation) or the Phase 22 §15 soft trigger (local top-1
      differs from the DETERMINISTIC top-1 — a discriminator computed
      from the ranking input, never from model confidence or capability).
      "Gemini is more capable" is not a reason.
    * Fail closed: every failure becomes an explicit BranchResult /
      RoutingDecision.  There is no fallback to a legacy unconstrained
      LLM path.
    * Human review is MONOTONE: the final flag is the OR of every stage's
      review requirement via combine_human_review().
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.llm.arbitrator import Arbitrator, ArbitrationResult
from app.llm.branch_policy import BranchPolicy, PolicyInput
from app.llm.merge_resolver import MergeResolver
from app.llm.models import (
    LLMRankingInput,
    LLMRankingOutput,
)
from app.llm.router_contract import (
    BranchOutcome,
    BranchResult,
    RoutingDecision,
    RoutingReason,
    RoutingRecord,
    combine_human_review,
    router_enabled,
)

logger = logging.getLogger("sentinel.llm.router_orchestrator")

#: Hard budget: at most one cloud call per orchestration (Phase 22 T7).
_CLOUD_CALL_BUDGET = 1

#: Branch runner callable: (ranking_input, physics_report, review_flag) -> BranchResult
BranchRunner = Callable[[LLMRankingInput, Any, bool], BranchResult]


def _as_branch_adapter(adapter: Any) -> Optional[BranchRunner]:
    """Normalize a branch adapter into a callable.

    Accepts the existing runner OBJECTS (LocalBranchRunner /
    CloudBranchRunner expose ``.run``) or plain callables with the same
    signature.  Returns None for an absent adapter.
    """
    if adapter is None:
        return None
    run = getattr(adapter, "run", None)
    if callable(run):
        return run
    if callable(adapter):
        return adapter
    raise TypeError(
        "branch adapter must expose .run(...) or be callable with "
        "(ranking_input, physics_report, review_already_required)"
    )


# ═══════════════════════════════════════════════════════════════════════════
# SAFETY ENVELOPE (existing validator only — no new safety logic)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SafetyValidationResult:
    """Deterministic outcome of the existing safety validator (envelope only).

    ``validation`` is the existing ``app.agent.safety.ValidationResult``.
    ``sentinel_output`` is the existing ``app.api.models.SentinelOutput``
    produced by ``apply_validation_to_output``.  This class adds no safety
    judgement — it only carries the existing validator's verdict.
    """
    validation: Any
    sentinel_output: Any
    status: str
    blocked: bool = False
    requires_human_review: bool = False


def default_safety_validation(
    merged_output: LLMRankingOutput,
    safety_context: Optional[dict] = None,
) -> SafetyValidationResult:
    """Run the EXISTING deterministic safety chain on the merged output.

    Sequence (all pre-existing components):
        convert_to_sentinel_output   (app/llm/ranker.py)
        → SentinelOutput.model_validate
        → validate_recovery_plan     (app/agent/safety.py)
        → apply_validation_to_output (app/agent/safety.py)

    Only called when a model-sourced plan actually reached the merge (a
    winning branch exists).  Never authorizes a command itself: the
    recovery plan is built by the existing procedure-library conversion
    and every step is then passed through the existing validator.
    """
    from app.agent.safety import (
        SafetyStatus,
        apply_validation_to_output,
        validate_recovery_plan,
    )
    from app.api.models import SentinelOutput
    from app.llm.ranker import convert_to_sentinel_output

    sentinel = SentinelOutput.model_validate(
        convert_to_sentinel_output(merged_output)
    )
    validation = validate_recovery_plan(sentinel, safety_context or {})
    final_output = apply_validation_to_output(sentinel, validation)
    return SafetyValidationResult(
        validation=validation,
        sentinel_output=final_output,
        status=validation.safety_status.value,
        blocked=validation.safety_status is SafetyStatus.BLOCKED,
        requires_human_review=validation.requires_human_review,
    )


# ═══════════════════════════════════════════════════════════════════════════
# PHYSICS RECHECK (existing reconcile_llm_claim only — no new physics logic)
# ═══════════════════════════════════════════════════════════════════════════

def reassert_physics(
    merged_output: LLMRankingOutput,
    ranking_input: LLMRankingInput,
    physics_report: Any = None,
) -> tuple[Any, ...]:
    """Reassert the deterministic physics verdicts over the merged output.

    For every merged hypothesis the model implicitly claimed validity by
    ranking it.  The existing ``reconcile_llm_claim`` is applied: the
    deterministic verdict is returned UNCHANGED and the model's claim is
    recorded as an ``LLMOverrideAttempt`` (disagreement when the claim
    contradicts the verdict).  Physics is never mutated.
    """
    from app.validation.physics import (
        PhysicsStatus,
        PhysicsVerdict,
        reconcile_llm_claim,
    )

    attempts: list[Any] = []
    for hyp in merged_output.ranked_hypotheses:
        verdict = _verdict_for_fault(physics_report, ranking_input, hyp.fault_id)
        if verdict is None:
            continue
        _, attempt = reconcile_llm_claim(verdict, "VALID")
        attempts.append(attempt)
    return tuple(attempts)


def _verdict_for_fault(
    physics_report: Any,
    ranking_input: LLMRankingInput,
    fault_id: str,
) -> Any:
    """Locate the DETERMINISTIC verdict for a fault, or None.

    Prefers the existing ``PhysicsValidationReport.verdict_for_fault``;
    otherwise mirrors the status the ranking-input physics sets already
    encode (invalidated/validated/uncertain).  No verdict is computed
    here — the sets are authoritative and consumed as-is.
    """
    if physics_report is not None:
        lookup = getattr(physics_report, "verdict_for_fault", None)
        if callable(lookup):
            verdict = lookup(fault_id)
            if verdict is not None:
                return verdict
    from app.validation.physics import PhysicsStatus, PhysicsVerdict

    invalidated = frozenset(ranking_input.physics.invalidated)
    validated = frozenset(ranking_input.physics.validated)
    if fault_id in invalidated:
        status = PhysicsStatus.INVALID
    elif fault_id in validated:
        status = PhysicsStatus.VALID
    else:
        status = PhysicsStatus.UNCERTAIN
    return PhysicsVerdict(
        hypothesis_id=f"HYP-{fault_id}",
        validation_status=status,
        explanation=(
            "Deterministic physics verdict carried by the ranking input "
            "physics context (reasserted by the router, never mutated)."
        ),
        model_version="router-dry-run",
        fault_id=fault_id,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ORCHESTRATION RESULT
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class OrchestrationResult:
    """Immutable result of one dry-run orchestration.

    Carries every deterministic artifact a RoutingRecord and the ROUTING
    audit stage need.  No raw model text, no telemetry, no credentials.
    """
    decision: RoutingDecision
    reasons: tuple[RoutingReason, ...]
    routing_record: RoutingRecord
    policy_record: RoutingRecord
    arbitration: Optional[ArbitrationResult] = None
    merged_output: Optional[LLMRankingOutput] = None
    physics_recheck: tuple[Any, ...] = ()
    safety: Optional[SafetyValidationResult] = None
    human_review_required: bool = False
    local: Optional[BranchResult] = None
    cloud: Optional[BranchResult] = None
    cloud_called: bool = False
    escalated: bool = False
    escalation_reason: tuple[RoutingReason, ...] = ()
    redaction_gate_invoked: bool = False


# ═══════════════════════════════════════════════════════════════════════════
# ROUTING AUDIT STAGE (existing RoutingRecord + audit conventions)
# ═══════════════════════════════════════════════════════════════════════════

def routing_audit_payload(result: OrchestrationResult) -> dict[str, Any]:
    """Deterministic ROUTING-stage payload answering the 10 audit questions.

    Built exclusively from RoutingDecision / RoutingReason / BranchOutcome
    values and small counts.  Never includes API keys, secrets, raw
    telemetry, raw model output, or unredacted cloud payloads.  The audit
    recorder additionally redacts and the store refuses any surviving
    credential pattern.
    """
    policy = result.policy_record
    local = result.local
    cloud = result.cloud
    arbitration = result.arbitration

    def _outcome(branch: Optional[BranchResult]) -> str:
        return (
            branch.outcome.value
            if branch is not None
            else BranchOutcome.NOT_RUN.value
        )

    def _reasons(branch: Optional[BranchResult]) -> list[str]:
        return (
            [r.value for r in branch.reason_codes]
            if branch is not None
            else []
        )

    if result.cloud_called:
        cloud_skipped_reason = ""
    elif result.escalated:
        cloud_skipped_reason = "escalation_no_cloud_adapter"
    elif (
        local is not None
        and local.is_usable
    ):
        cloud_skipped_reason = "local_clean_accept"
    else:
        cloud_skipped_reason = "policy_terminal"

    return {
        "router_enabled": str(router_enabled()),
        "mode": "dry_run",
        # 1. Why was LOCAL selected? (policy decision + deterministic signals)
        "policy_decision": policy.decision.value,
        "policy_reasons": [r.value for r in policy.reasons],
        "policy_signal_snapshot": dict(policy.signal_snapshot),
        # 2/3. Why was CLOUD selected / skipped?
        "local_outcome": _outcome(local),
        "local_reasons": _reasons(local),
        "cloud_outcome": _outcome(cloud),
        "cloud_reasons": _reasons(cloud),
        "cloud_called": result.cloud_called,
        "cloud_skipped_reason": cloud_skipped_reason,
        # 4. Why was arbitration required? / 5. Which branch won?
        "arbitration_required": arbitration is not None,
        "arbitration_rule": arbitration.rule_applied if arbitration else "",
        "arbitration_decision": (
            arbitration.decision.value if arbitration else ""
        ),
        "winning_branch": (
            arbitration.winning_branch.value
            if arbitration and arbitration.winning_branch
            else "none"
        ),
        # 6. Which deterministic reason caused the decision?
        "final_decision": result.decision.value,
        "final_reasons": [r.value for r in result.reasons],
        "escalation_triggered": result.escalated,
        "escalation_reasons": [r.value for r in result.escalation_reason],
        # 7. Was human review required?
        "human_review_required": result.human_review_required,
        # 8. Was the cloud redaction gate invoked?
        "redaction_gate_invoked": result.redaction_gate_invoked,
        "redaction_report_present": bool(
            cloud is not None and cloud.redaction_report is not None
        ),
        # 9. Was safety validation executed?
        "safety_executed": result.safety is not None,
        "safety_status": result.safety.status if result.safety else "NOT_RUN",
        # 10. Was physics reasserted?
        "physics_reasserted": bool(result.physics_recheck),
        "physics_recheck_attempts": len(result.physics_recheck),
        "physics_recheck_disagreements": sum(
            1 for a in result.physics_recheck if getattr(a, "disagreement", False)
        ),
    }


def record_routing_audit(
    recorder: Any,
    result: OrchestrationResult,
    duration_ms: Optional[float] = None,
) -> None:
    """Write the ROUTING stage entry through the existing audit recorder.

    ``recorder`` is the existing ``app.audit.AuditRecorder`` (or any
    object with the same ``record(stage, status, summary, payload,
    duration_ms)`` signature).  The payload is redacted by the recorder
    and the store refuses any surviving credential pattern.
    """
    from app.audit import Stage, StageStatus

    summary = (
        f"router(dry-run): {result.decision.value} "
        f"({', '.join(r.value for r in result.reasons)})"
    )
    recorder.record(
        Stage.ROUTING,
        StageStatus.OK,
        summary,
        routing_audit_payload(result),
        duration_ms=duration_ms,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR — sequencing only
# ═══════════════════════════════════════════════════════════════════════════

class RouterOrchestrator:
    """Sequencing-only hybrid-router state machine (dry-run, Phase 23 Step 5).

    All behaviour lives in the existing components it wires together.  The
    orchestrator itself decides only WHAT to run next, and only from
    deterministic signals.
    """

    def __init__(
        self,
        policy: Optional[BranchPolicy] = None,
        local_runner: Any = None,
        cloud_runner: Any = None,
        safety_validator: Optional[
            Callable[[LLMRankingOutput, Optional[dict]], SafetyValidationResult]
        ] = None,
    ):
        self._policy = policy if policy is not None else BranchPolicy()
        # Branch adapters may be injected either as the existing runner
        # objects (LocalBranchRunner / CloudBranchRunner expose .run) or as
        # plain callables with the same signature.  Sequencing glue only.
        self._local_runner = _as_branch_adapter(local_runner)
        self._cloud_runner = _as_branch_adapter(cloud_runner)
        self._safety_validator = (
            safety_validator
            if safety_validator is not None
            else default_safety_validation
        )
        self._arbitrator = Arbitrator()
        self._merge_resolver = MergeResolver()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def run(
        self,
        ranking_input: LLMRankingInput,
        physics_report: Any = None,
        policy_state: Optional[PolicyInput] = None,
        review_already_required: bool = False,
        safety_context: Optional[dict] = None,
        recorder: Any = None,
    ) -> OrchestrationResult:
        """Execute one dry-run orchestration deterministically.

        Parameters:
            ranking_input:           deterministic context bundle.
            physics_report:          existing PhysicsValidationReport (or None).
            policy_state:            deterministic pre-inference state for the
                                     BranchPolicy; defaults derived from the
                                     ranking input (evidence status, hypothesis
                                     presence).
            review_already_required: monotone upstream review flag.
            safety_context:          crash-dump context dict passed to the
                                     existing safety validator.
            recorder:                optional existing AuditRecorder; the
                                     ROUTING stage is written when provided.
        """
        started = time.perf_counter()
        try:
            result = self._orchestrate(
                ranking_input=ranking_input,
                physics_report=physics_report,
                policy_state=policy_state,
                review_already_required=review_already_required,
                safety_context=safety_context,
            )
            if recorder is not None:
                record_routing_audit(
                    recorder,
                    result,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
            return result
        except Exception as exc:
            logger.error(
                "RouterOrchestrator failed closed: %s", type(exc).__name__
            )
            if recorder is not None:
                from app.audit import Stage, StageStatus

                recorder.record(
                    Stage.ROUTING,
                    StageStatus.FAILED,
                    f"router(dry-run) failed closed: {type(exc).__name__}",
                    {
                        "router_enabled": str(router_enabled()),
                        "mode": "dry_run",
                        "error_type": type(exc).__name__,
                        "detail": (
                            "See application logs; no model output is promoted "
                            "after a routing failure."
                        ),
                    },
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
            raise

    # ------------------------------------------------------------------
    # state machine (private — pure sequencing)
    # ------------------------------------------------------------------

    def _orchestrate(
        self,
        ranking_input: LLMRankingInput,
        physics_report: Any,
        policy_state: Optional[PolicyInput],
        review_already_required: bool,
        safety_context: Optional[dict],
    ) -> OrchestrationResult:
        state = (
            policy_state
            if policy_state is not None
            else self._default_policy_state(ranking_input)
        )
        policy_record = self._policy.evaluate(state)

        review = combine_human_review(
            review_already_required, policy_record.human_review_required,
        )

        # ── TERMINAL POLICY DECISIONS: no branch may execute ────────────
        if policy_record.decision in (
            RoutingDecision.BLOCKED,
            RoutingDecision.NO_INFERENCE,
            RoutingDecision.HUMAN_REVIEW,
        ):
            return self._terminal_result(
                policy_record=policy_record,
                decision=policy_record.decision,
                reasons=policy_record.reasons,
                ranking_input=ranking_input,
                physics_report=physics_report,
                review=review,
            )

        # ── LOCAL BRANCH ────────────────────────────────────────────────
        local: Optional[BranchResult] = None
        if policy_record.decision is RoutingDecision.LOCAL_ACCEPT:
            if self._local_runner is None:
                # Eligible LOCAL but no adapter configured: fail closed.
                return self._terminal_result(
                    policy_record=policy_record,
                    decision=RoutingDecision.NO_INFERENCE,
                    reasons=(RoutingReason.LOCAL_UNAVAILABLE,),
                    ranking_input=ranking_input,
                    physics_report=physics_report,
                    review=True,
                )
            local = self._local_runner(ranking_input, physics_report, review)

        # ── ESCALATION DECISION (deterministic triggers only) ───────────
        escalated = False
        escalation_reason: tuple[RoutingReason, ...] = ()

        # Hard trigger: local produced no usable output (Phase 22 §15).
        if local is not None and not local.is_usable:
            escalated = True
            escalation_reason = local.reason_codes
        elif policy_record.decision is RoutingDecision.CLOUD_ESCALATE:
            # Deterministic policy escalation (local unavailable).
            escalated = True
            escalation_reason = (RoutingReason.LOCAL_UNAVAILABLE,)
        elif (
            local is not None
            and local.is_usable
            and self._cloud_runner is not None
        ):
            # Soft trigger (Phase 22 §15(a)): local is clean but its top-1
            # disagrees with the DETERMINISTIC top-1 hypothesis.  Computed
            # from the ranking input + validated output — never from model
            # confidence or capability.
            local_top = _top_fault_id(local.validated_output)
            det_top = _deterministic_top_fault(ranking_input)
            if local_top and det_top and local_top != det_top:
                escalated = True
                escalation_reason = (RoutingReason.MODEL_DISAGREEMENT,)

        # ── CLOUD BRANCH (hard budget: at most ONE call) ────────────────
        cloud: Optional[BranchResult] = None
        cloud_called = False
        if escalated:
            if self._cloud_runner is None:
                # Escalation required but no cloud adapter configured:
                # fail closed — no inference, mandatory review.
                return self._terminal_result(
                    policy_record=policy_record,
                    decision=RoutingDecision.NO_INFERENCE,
                    reasons=(*escalation_reason, RoutingReason.CLOUD_UNAVAILABLE),
                    ranking_input=ranking_input,
                    physics_report=physics_report,
                    review=True,
                )
            cloud = self._cloud_runner(ranking_input, physics_report, review)
            cloud_called = True

        # ── ARBITRATION (existing component) ────────────────────────────
        arbitration = self._arbitrator.arbitrate(
            local, cloud, ranking_input, physics_report,
            review_already_required=review,
        )

        # ── MERGE (existing component — never duplicated here) ──────────
        merged = self._merge_resolver.resolve(
            arbitration, local, cloud, ranking_input, physics_report,
        )

        # ── PHYSICS RECHECK (existing reconcile_llm_claim) ──────────────
        recheck = reassert_physics(merged, ranking_input, physics_report)

        # ── SAFETY VALIDATION (existing validator, model plan only) ─────
        safety: Optional[SafetyValidationResult] = None
        if arbitration.winning_branch is not None:
            safety = self._safety_validator(merged, safety_context)

        # ── FINAL DECISION ──────────────────────────────────────────────
        decision = arbitration.decision
        reasons = list(arbitration.reasons)
        if safety is not None and safety.blocked:
            # Safety is the FINAL authority: a block overrides the
            # arbitration outcome; it can never be downgraded.
            decision = RoutingDecision.BLOCKED
            reasons = [RoutingReason.SAFETY_BLOCK, *arbitration.reasons]

        final_review = combine_human_review(
            review,
            local.human_review_required if local is not None else False,
            cloud.human_review_required if cloud is not None else False,
            merged.requires_human_review,
            safety.requires_human_review if safety is not None else False,
            _physics_dispute(recheck),
        )

        final_record = RoutingRecord(
            decision=decision,
            reasons=tuple(dict.fromkeys(reasons)),
            local=local,
            cloud=cloud,
            signal_snapshot=policy_record.signal_snapshot,
            human_review_required=final_review,
        )

        return OrchestrationResult(
            decision=final_record.decision,
            reasons=final_record.reasons,
            routing_record=final_record,
            policy_record=policy_record,
            arbitration=arbitration,
            merged_output=merged,
            physics_recheck=recheck,
            safety=safety,
            human_review_required=final_review,
            local=local,
            cloud=cloud,
            cloud_called=cloud_called,
            escalated=escalated,
            escalation_reason=escalation_reason,
            redaction_gate_invoked=(
                cloud is not None
                and (
                    cloud.redaction_report is not None
                    or RoutingReason.REDACTION_GATE_FAILURE in cloud.reason_codes
                )
            ),
        )

    # ------------------------------------------------------------------
    # terminal helpers (deterministic-only final states)
    # ------------------------------------------------------------------

    @staticmethod
    def _default_policy_state(ranking_input: LLMRankingInput) -> PolicyInput:
        """Deterministic policy state derived from the ranking input."""
        return PolicyInput(
            evidence_status=ranking_input.evidence_status,
            hypotheses_generated=bool(ranking_input.hypotheses),
        )

    def _deterministic_merge(
        self,
        decision: RoutingDecision,
        reasons: tuple[RoutingReason, ...],
        ranking_input: LLMRankingInput,
        physics_report: Any,
    ) -> LLMRankingOutput:
        """Merge with no branch participation via the existing MergeResolver.

        INSUFFICIENT evidence → the existing empty-diagnosis contract.
        Other terminal decisions → the existing deterministic fallback
        (non-invalidated hypotheses ordered by deterministic score,
        model-free confidence cap, mandatory review).
        """
        import dataclasses

        from app.llm.models import EvidenceStatus

        arbitration = ArbitrationResult(
            decision=decision,
            reasons=reasons,
            winning_branch=None,
            human_review_required=True,
            disagreement=False,
            rule_applied="POLICY_TERMINAL",
        )
        # When the policy determined INSUFFICIENT evidence (possibly via a
        # policy_state override whose evidence_status is more restrictive than
        # ranking_input.evidence_status), propagate that status to the merge
        # resolver so PATH A (empty-diagnosis contract) fires correctly.
        effective_input = ranking_input
        if RoutingReason.INSUFFICIENT_EVIDENCE in reasons:
            if (
                ranking_input.evidence_status
                != EvidenceStatus.INSUFFICIENT.value
            ):
                effective_input = dataclasses.replace(
                    ranking_input,
                    evidence_status=EvidenceStatus.INSUFFICIENT.value,
                )
        return self._merge_resolver.resolve(
            arbitration, None, None, effective_input, physics_report,
        )

    def _terminal_result(
        self,
        policy_record: RoutingRecord,
        decision: RoutingDecision,
        reasons: tuple[RoutingReason, ...],
        ranking_input: LLMRankingInput,
        physics_report: Any,
        review: bool,
    ) -> OrchestrationResult:
        """Terminal decision: no branches, deterministic output only."""
        merged = self._deterministic_merge(
            decision, reasons, ranking_input, physics_report,
        )
        recheck = reassert_physics(merged, ranking_input, physics_report)
        final_review = combine_human_review(review, merged.requires_human_review)
        final_record = RoutingRecord(
            decision=decision,
            reasons=reasons,
            local=None,
            cloud=None,
            signal_snapshot=policy_record.signal_snapshot,
            human_review_required=final_review,
        )
        return OrchestrationResult(
            decision=final_record.decision,
            reasons=final_record.reasons,
            routing_record=final_record,
            policy_record=policy_record,
            arbitration=None,
            merged_output=merged,
            physics_recheck=recheck,
            safety=None,
            human_review_required=final_review,
            local=None,
            cloud=None,
            cloud_called=False,
            escalated=False,
            escalation_reason=(),
            redaction_gate_invoked=False,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Deterministic discriminators used for sequencing (no model authority)
# ═══════════════════════════════════════════════════════════════════════════

def _top_fault_id(output: Optional[Any]) -> str:
    """Top-1 fault of a validated branch output (rank ascending)."""
    if output is None or not output.ranked_hypotheses:
        return ""
    sorted_hyps = sorted(
        output.ranked_hypotheses, key=lambda h: getattr(h, "rank", 999)
    )
    return sorted_hyps[0].fault_id if sorted_hyps else ""


def _deterministic_top_fault(ranking_input: LLMRankingInput) -> str:
    """Top-1 fault of the DETERMINISTIC hypothesis set (rank ascending).

    Never reads model output or confidence: the deterministic rank is the
    ordering the Phase 6 pipeline produced.
    """
    hyps = sorted(
        ranking_input.hypotheses,
        key=lambda h: (getattr(h, "deterministic_rank", 999),),
    )
    return hyps[0].fault_id if hyps else ""


def _physics_dispute(recheck: tuple[Any, ...]) -> bool:
    """True only when a model claim disputed a DEFINITIVE refutation.

    A disagreement against an INVALID verdict (a deterministic refusal)
    forces review.  A claim against an UNCERTAIN verdict is not a dispute
    of a deterministic refusal and does not by itself force review —
    the verdicts themselves are never mutated either way.
    """
    for attempt in recheck:
        if not getattr(attempt, "disagreement", False):
            continue
        status = getattr(attempt, "deterministic_status", None)
        value = status.value if hasattr(status, "value") else str(status)
        if value == "INVALID":
            return True
    return False