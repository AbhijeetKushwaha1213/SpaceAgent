"""
SENTINEL — Hybrid Router Contract (app/llm/router_contract.py)

Phase 23 Step 1.  The router's LANGUAGE, not its behavior.

This module defines the immutable data contracts that every future router
component (BranchPolicy, LocalBranchRunner, CloudBranchRunner, Arbitrator,
MergeResolver, RouterOrchestrator — see PHASE_22_ROUTER_ARCHITECTURE_PLAN.md)
will speak.  It contains NO routing logic:

    - it does not select a branch
    - it does not arbitrate
    - it does not merge
    - it does not call any provider
    - it cannot authorize commands, override physics, or override safety

Authority boundaries encoded here (Phase 22 rules 1-13):

    * Physics and safety verdicts have NO representation in this module.
      They live in app.validation.physics and app.agent.safety and are
      referenced by consumers, never carried inside a BranchResult.
    * Raw model output is untrusted.  BranchResult stores validated
      structures (LLMRankingOutput / GuardrailResult) and, separately, a
      truncated raw text blob explicitly marked UNTRUSTED for diagnostics.
    * ``human_review_required`` is MONOTONE: TRUE propagates through the
      whole routing pipeline; no LLM result may clear it.  The contract
      provides no API that could express "was required, then cleared".

The router itself is DISABLED by default.  ``router_enabled()`` returns
False unless the deployment explicitly sets ``ROUTER_ENABLED=true``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from app.llm.models import GuardrailResult, LLMRankingOutput


# ═══════════════════════════════════════════════════════════════════════════
# ROUTER ENABLE FLAG (Phase 23 Step 1, Part 9)
# ═══════════════════════════════════════════════════════════════════════════

def router_enabled() -> bool:
    """Whether the hybrid router participates in request handling.

    Default is False.  The existing single-provider execution path must
    remain byte-identical while this returns False.  Only the literal
    values ``true``/``1``/``yes`` (case-insensitive) enable the router;
    every other value — including an absent variable — keeps it disabled.
    The router stays disabled until the complete implementation is
    verified (Phase 22 rule 13).
    """
    raw = os.environ.get("ROUTER_ENABLED", "").lower().strip()
    return raw in ("true", "1", "yes")


# ═══════════════════════════════════════════════════════════════════════════
# BRANCH IDENTITY
# ═══════════════════════════════════════════════════════════════════════════

class Branch(str, Enum):
    """Which reasoning branch produced a BranchResult."""
    LOCAL = "local"    # sovereign local model (Phi-3 Mini via OpenAI-compat)
    CLOUD = "cloud"    # cloud model (Gemini)


# ═══════════════════════════════════════════════════════════════════════════
# ROUTING DECISION — "what should Sentinel do next?"
# ═══════════════════════════════════════════════════════════════════════════

class RoutingDecision(str, Enum):
    """Explicit routing outcome.  Never model-generated authority.

    A RoutingDecision is always derived by deterministic router code from
    validated signals; an LLM can never emit one of these values directly.

    Semantics (Phase 22 §5/§8/§11 mapped to the Phase 23 Step 1 mandate):

    LOCAL_ACCEPT       Use the local branch's validated result as-is; no
                       cloud call.
    CLOUD_ACCEPT       Use the cloud branch's validated result as-is
                       (CLOUD_ONLY policy or post-escalation acceptance).
    CLOUD_ESCALATE     Run (or adopt) the cloud branch because the local
                       branch failed, violated, or could not be trusted.
    HUMAN_REVIEW       Mandatory operator review.  Terminal intent for
                       insufficient evidence, unresolvable disagreement,
                       both-branch failure, or unavailable providers.
    BLOCKED            Deterministic safety blocked the outcome; the plan
                       must not proceed.
    NO_INFERENCE       Neither branch could run and no model output exists;
                       deterministic stages only, explicit fail-closed state.
    """
    LOCAL_ACCEPT = "LOCAL_ACCEPT"
    CLOUD_ACCEPT = "CLOUD_ACCEPT"
    CLOUD_ESCALATE = "CLOUD_ESCALATE"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    BLOCKED = "BLOCKED"
    NO_INFERENCE = "NO_INFERENCE"

    @property
    def is_terminal_review(self) -> bool:
        """True when the decision mandates human review regardless of any
        model opinion (monotone escalation, Phase 22 rule A9)."""
        return self in (
            RoutingDecision.HUMAN_REVIEW,
            RoutingDecision.BLOCKED,
            RoutingDecision.NO_INFERENCE,
        )


# ═══════════════════════════════════════════════════════════════════════════
# ROUTING REASON CODES
# ═══════════════════════════════════════════════════════════════════════════

class RoutingReason(str, Enum):
    """Structured, enumerable reason codes for routing decisions.

    Every value maps to a real Phase 22 condition or an existing Sentinel
    failure condition.  This enum is deliberately NOT a dumping ground:
    add a value only when a deterministic producer can raise it.
    """
    # Positive outcome
    VALID_LOCAL_RESULT = "valid_local_result"
    VALID_CLOUD_RESULT = "valid_cloud_result"
    BRANCH_AGREEMENT = "branch_agreement"

    # Local branch failures (deterministically classified)
    INVALID_STRUCTURED_OUTPUT = "invalid_structured_output"
    PROMPT_ECHO_TRUNCATION = "prompt_echo_truncation"   # Phase 21 S1-type
    LOCAL_TIMEOUT = "local_timeout"
    LOCAL_UNAVAILABLE = "local_unavailable"

    # Deterministic contract violations (guardrail-produced)
    EVIDENCE_FAILURE = "evidence_failure"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PHYSICS_CONFLICT = "physics_conflict"
    PROCEDURE_INVALID = "procedure_invalid"
    SAFETY_BLOCK = "safety_block"

    # Cloud branch availability
    CLOUD_UNAVAILABLE = "cloud_unavailable"

    # Cross-branch arbitration inputs
    MODEL_DISAGREEMENT = "model_disagreement"
    UNRESOLVED_AMBIGUITY = "unresolved_ambiguity"

    # Escalation/review triggers
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    LOCAL_ESCALATION = "local_escalation"

    # Fail-closed terminals
    BOTH_UNAVAILABLE = "both_unavailable"
    BOTH_INVALID = "both_invalid"


# ═══════════════════════════════════════════════════════════════════════════
# BRANCH EXECUTION STATUS
# ═══════════════════════════════════════════════════════════════════════════

class BranchOutcome(str, Enum):
    """Execution status of one branch, classified by deterministic code.

    ACCEPT      Output parsed AND passed guardrails with zero violations.
    ESCALATION  Output parsed but violated guardrails or broke the
                deterministic contract; usable only as escalation evidence.
    FAILURE     No usable output at all (provider error, timeout, parse
                failure after the bounded repair retry, S1-type echo).
    NOT_RUN     The policy never started this branch.
    """
    ACCEPT = "ACCEPT"
    ESCALATION = "ESCALATION"
    FAILURE = "FAILURE"
    NOT_RUN = "NOT_RUN"


# ═══════════════════════════════════════════════════════════════════════════
# BRANCH RESULT — the output envelope of one reasoning branch
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class BranchResult:
    """Immutable result of running one reasoning branch.

    Trust separation (Phase 22 §6/§7, Phase 23 mandate Part 5):

    * ``validated_output`` and ``guardrail_result`` are the ONLY fields
      downstream code may reason over.  They are products of deterministic
      parsing + guardrail validation, not of the model's say-so.
    * ``raw_text_head`` is UNTRUSTED diagnostic data.  It must never be
      parsed for evidence/procedure IDs, never transmitted to another
      model, and never promoted into validated state.  It is truncated
      (diagnostics only, not a transport of the full completion).
    * Physics and safety verdicts are intentionally ABSENT.  They are
      deterministic pipeline facts referenced by the orchestrator from
      app.validation.physics / app.agent.safety — never owned by a branch.

    ``human_review_required`` is MONOTONE: once any producer sets it True,
    the routing pipeline may only OR further sources into it.  There is no
    setter and no API here that can express clearing it; a frozen dataclass
    rejects mutation outright.
    """

    # 1. Branch identity
    branch: Branch

    # 2. Execution status (deterministically classified)
    outcome: BranchOutcome

    # 3. Model/provider identity (audit metadata)
    provider_name: str = ""
    model_name: str = ""

    # 4. Raw model success/failure state.  True only when the provider
    #    returned a completion; independent of whether that completion was
    #    usable (a prompt-echo completion is a "successful" call but a
    #    FAILURE outcome).
    inference_performed: bool = False

    # 5. Validated reasoning result — the only field arbitration may read.
    #    Reuses the existing Phase 10/21 contract types; NOT duplicated.
    validated_output: Optional[LLMRankingOutput] = None

    # 6. Validation violations (guardrail result; original + corrected
    #    outputs preserved by the existing GuardrailResult contract).
    guardrail_result: Optional[GuardrailResult] = None

    # 7. Deterministic context references — the shared evidence bundle and
    #    evidence state this branch was asked to respect.  References only;
    #    the bundle itself is owned by the pipeline and immutable.
    evidence_status: str = ""
    scenario_id: str = ""

    # 8. Latency / audit metadata
    elapsed_ms: float = 0.0
    attempts: int = 0
    reason_codes: tuple[RoutingReason, ...] = ()

    # 9. Human review raised by this branch (model-emitted OR deterministic).
    #    TRUE is monotone through the routing pipeline.  No LLM result may
    #    clear it.
    human_review_required: bool = False

    # UNTRUSTED diagnostics: truncated head of the raw model text, kept
    # strictly separate from validated_output.  See trust note above.
    raw_text_head: str = ""

    @property
    def succeeded(self) -> bool:
        """The provider call completed (independent of usability)."""
        return self.inference_performed

    @property
    def is_usable(self) -> bool:
        """Only an ACCEPT outcome may feed arbitration as a participant."""
        return self.outcome is BranchOutcome.ACCEPT


# ═══════════════════════════════════════════════════════════════════════════
# ROUTING RECORD — one routing decision plus its justification
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class RoutingRecord:
    """A single routing decision with the deterministic justification.

    This is the unit the future ROUTING audit stage will persist.  It holds
    no model-generated authority: ``decision`` and ``reasons`` are produced
    by deterministic router code; ``signal_snapshot`` is a tuple of
    (name, value) pairs of the pre-inference deterministic signals.
    """
    decision: RoutingDecision
    reasons: tuple[RoutingReason, ...]
    local: Optional[BranchResult] = None
    cloud: Optional[BranchResult] = None
    # Deterministic pre-inference signals, as (name, value) string pairs.
    signal_snapshot: tuple[tuple[str, str], ...] = ()
    # Monotone OR of every human-review source (branches + deterministic
    # rules).  True here can never become False downstream.
    human_review_required: bool = False


def combine_human_review(*flags: bool) -> bool:
    """The ONLY sanctioned way to accumulate human-review requirements.

    OR-combination makes the contract monotone by construction: the
    function has no signature through which a True could be turned back
    into False.  Future arbitration/merge code must use this (or an
    equivalent OR) rather than selecting one branch's flag.
    """
    return any(flags)
