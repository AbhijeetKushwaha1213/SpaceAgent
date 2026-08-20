"""
SENTINEL — Hybrid Router Branch Policy (app/llm/branch_policy.py)

Phase 23 Step 2.  The deterministic answer to:

    "Given deterministic pipeline state, should Sentinel execute the local
    branch, escalate, require review, or perform no inference?"

This module is PURE: no network, no filesystem mutation, no model calls, no
randomness, no timestamps, no environment-dependent hidden behavior.  Same
inputs -> same decision, always.

What this module is NOT:

    - it does not inspect model confidence
    - it does not inspect model-generated reasoning
    - it does not arbitrate, merge, or call any LLM
    - it does not duplicate the physics or safety engines — it consumes
      already-computed deterministic results passed in via PolicyInput

The policy is dormant: nothing in the production execution path invokes it
while ROUTER_ENABLED=false.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.llm.models import EvidenceStatus
from app.llm.router_contract import (
    RoutingDecision,
    RoutingReason,
    RoutingRecord,
)

# The only evidence states the policy recognizes.  Anything else is
# malformed input and must fail closed (Rule 7), never produce an
# optimistic LOCAL_ACCEPT.
_VALID_EVIDENCE_STATUSES = frozenset(s.value for s in EvidenceStatus)


@dataclass(frozen=True)
class PolicyInput:
    """Deterministic pre-inference state consumed by the policy.

    Every field is produced by deterministic pipeline stages (detection,
    estimation, hypothesis generation, physics validation, safety
    validation) or by infrastructure probes — never by a model.

    Fields:
        evidence_status:          value of app.llm.models.EvidenceStatus
        safety_blocked:           deterministic safety already produced a
                                  terminal BLOCKED condition
        physics_space_invalidated: deterministic physics invalidated the
                                  entire relevant hypothesis space (hyps
                                  exist, all invalidated, none uncertain)
        local_available:          local inference endpoint reachable
        human_review_required:    human review already mandatory upstream
        hypotheses_generated:     deterministic hypothesis set is non-empty
    """
    evidence_status: str = EvidenceStatus.INSUFFICIENT.value
    safety_blocked: bool = False
    physics_space_invalidated: bool = False
    local_available: bool = True
    human_review_required: bool = False
    hypotheses_generated: bool = True

    def signal_snapshot(self) -> tuple[tuple[str, str], ...]:
        """Deterministic (name, value) pairs for the routing audit."""
        return (
            ("evidence_status", str(self.evidence_status)),
            ("safety_blocked", str(self.safety_blocked)),
            ("physics_space_invalidated", str(self.physics_space_invalidated)),
            ("local_available", str(self.local_available)),
            ("human_review_required", str(self.human_review_required)),
            ("hypotheses_generated", str(self.hypotheses_generated)),
        )


class BranchPolicy:
    """Deterministic branch policy (Phase 22 §5.3, Phase 23 Step 2 rules).

    Rules are evaluated in fixed priority order; the first match decides.
    The policy returns a RoutingRecord with no branch results attached —
    branches have not run yet at decision time.
    """

    def evaluate(self, state: PolicyInput) -> RoutingRecord:
        # ── RULE 1 — SAFETY BLOCK ────────────────────────────────────────
        # Deterministic safety already decided terminally.  No local run,
        # no cloud escalation, no inference may attempt to override safety.
        if state.safety_blocked:
            return RoutingRecord(
                decision=RoutingDecision.BLOCKED,
                reasons=(RoutingReason.SAFETY_BLOCK,),
                signal_snapshot=state.signal_snapshot(),
                human_review_required=True,
            )

        # Input well-formedness gate (Rule 7, fail closed): an evidence
        # status outside the deterministic enum is malformed input and can
        # never justify inference.
        if state.evidence_status not in _VALID_EVIDENCE_STATUSES:
            return RoutingRecord(
                decision=RoutingDecision.HUMAN_REVIEW,
                reasons=(RoutingReason.UNRESOLVED_AMBIGUITY,),
                signal_snapshot=state.signal_snapshot(),
                human_review_required=True,
            )

        # ── RULE 6 — HUMAN REVIEW IS MONOTONE ───────────────────────────
        # A pre-existing review requirement can never be downgraded to
        # LOCAL_ACCEPT / CLOUD_ACCEPT by this policy.
        if state.human_review_required:
            return RoutingRecord(
                decision=RoutingDecision.HUMAN_REVIEW,
                reasons=(RoutingReason.HUMAN_REVIEW_REQUIRED,),
                signal_snapshot=state.signal_snapshot(),
                human_review_required=True,
            )

        # ── RULE 2 — INSUFFICIENT EVIDENCE ──────────────────────────────
        # Repository semantics (Phase 21/22 H1): the correct answer when
        # evidence is missing is an empty diagnosis plus mandatory human
        # review.  The LLM is never asked to compensate for missing
        # telemetry.
        if state.evidence_status == EvidenceStatus.INSUFFICIENT.value:
            return RoutingRecord(
                decision=RoutingDecision.HUMAN_REVIEW,
                reasons=(RoutingReason.INSUFFICIENT_EVIDENCE,),
                signal_snapshot=state.signal_snapshot(),
                human_review_required=True,
            )

        # ── RULE 3 — PHYSICS INVALIDATION ───────────────────────────────
        # Deterministic physics invalidated the whole hypothesis space; an
        # LLM may not override that.  (Individual INVALIDATED hypotheses do
        # NOT trigger this rule — guardrails handle them per-hypothesis.)
        if state.physics_space_invalidated:
            return RoutingRecord(
                decision=RoutingDecision.HUMAN_REVIEW,
                reasons=(RoutingReason.PHYSICS_CONFLICT,),
                signal_snapshot=state.signal_snapshot(),
                human_review_required=True,
            )

        # ── RULE 4 — VALID LOCAL INPUT ──────────────────────────────────
        # Evidence adequate/partial/contradictory, no terminal safety
        # block, no deterministic physics stop, hypotheses exist, and the
        # local endpoint is reachable: the LOCAL branch is eligible.  The
        # policy declares eligibility; the runner produces the result.
        if (
            state.local_available
            and state.hypotheses_generated
        ):
            return RoutingRecord(
                decision=RoutingDecision.LOCAL_ACCEPT,
                reasons=(RoutingReason.VALID_LOCAL_RESULT,),
                signal_snapshot=state.signal_snapshot(),
                human_review_required=False,
            )

        # ── RULE 5 — LOCAL UNAVAILABLE ──────────────────────────────────
        # Deterministic escalation decision only: this step never calls
        # the cloud branch.
        if not state.local_available:
            return RoutingRecord(
                decision=RoutingDecision.CLOUD_ESCALATE,
                reasons=(RoutingReason.LOCAL_UNAVAILABLE,),
                signal_snapshot=state.signal_snapshot(),
                human_review_required=False,
            )

        # ── RULE 7 — FAIL CLOSED ────────────────────────────────────────
        # No deterministic hypothesis space to rank (and no earlier rule
        # fired): nothing to infer over.  Never an optimistic accept.
        return RoutingRecord(
            decision=RoutingDecision.NO_INFERENCE,
            reasons=(RoutingReason.UNRESOLVED_AMBIGUITY,),
            signal_snapshot=state.signal_snapshot(),
            human_review_required=True,
        )
