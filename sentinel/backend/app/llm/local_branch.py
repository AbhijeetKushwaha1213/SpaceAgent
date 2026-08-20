"""
SENTINEL — Hybrid Router Local Branch Runner (app/llm/local_branch.py)

Phase 23 Step 2.  An ADAPTER around the existing local constrained ranking
pipeline — never a second implementation of it:

    ranking_input → build_constrained_prompt (existing)
                  → provider.call             (existing LocalProvider)
                  → _extract_json             (existing parser)
                  → LLMRankingOutput.from_dict(existing typed parse)
                  → validate_ranking_output   (existing guardrails)
                  → BranchResult              (Step 1 contract)

Trust boundary:

    RAW MODEL OUTPUT is untrusted diagnostic text (``raw_text_head``).
    VALIDATED OUTPUT exists only after the existing parsing + guardrail
    validation succeeds with ZERO violations.  The model can never populate
    RoutingDecision, RoutingReason, safety status, physics verdicts, or any
    command authorization through this runner.

Phase 21 failure modes are mapped honestly (no hiding Phi-3 weakness):

    prompt-echo / output-token exhaustion → FAILURE + PROMPT_ECHO_TRUNCATION
    unparseable JSON after repair retries → FAILURE + INVALID_STRUCTURED_OUTPUT
    any guardrail violation               → FAILURE + mapped reason
    timeout                               → FAILURE + LOCAL_TIMEOUT
    provider/daemon unavailable           → FAILURE + LOCAL_UNAVAILABLE

The runner is dormant: the production path does not invoke it while
ROUTER_ENABLED=false.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import time
from typing import Any, Optional

from app.llm.models import LLMRankingInput, ViolationType
from app.llm.provider import LLMProvider, ProviderError
from app.llm.ranker import (
    _extract_json,
    build_constrained_prompt,
    validate_ranking_output,
)
from app.llm.router_contract import (
    Branch,
    BranchOutcome,
    BranchResult,
    RoutingReason,
    combine_human_review,
)

logger = logging.getLogger("sentinel.llm.local_branch")

# Diagnostics cap for the untrusted raw text blob (Phase 22 §7.2).
_RAW_HEAD_CHARS = 500

# Guardrail violations that, beyond failing the branch, force human review.
_REVIEW_FORCING_VIOLATIONS = frozenset({
    ViolationType.PHYSICS_OVERRIDE,
    ViolationType.INSUFFICIENT_EVIDENCE_CLAIM,
    ViolationType.INVENTED_TELEMETRY,
    ViolationType.UNKNOWN_COMMAND,
})

# Map guardrail violation types to routing reason codes.
_VIOLATION_REASON: dict[ViolationType, RoutingReason] = {
    ViolationType.NONEXISTENT_EVIDENCE: RoutingReason.EVIDENCE_FAILURE,
    ViolationType.INVALID_PROCEDURE: RoutingReason.PROCEDURE_INVALID,
    ViolationType.PHYSICS_OVERRIDE: RoutingReason.PHYSICS_CONFLICT,
    ViolationType.UNSUPPORTED_HYPOTHESIS: RoutingReason.EVIDENCE_FAILURE,
    ViolationType.INVENTED_TELEMETRY: RoutingReason.EVIDENCE_FAILURE,
    ViolationType.INSUFFICIENT_EVIDENCE_CLAIM: (
        RoutingReason.INSUFFICIENT_EVIDENCE
    ),
    ViolationType.UNKNOWN_COMMAND: RoutingReason.PROCEDURE_INVALID,
    ViolationType.UNSUPPORTED_CERTAINTY: RoutingReason.EVIDENCE_FAILURE,
}

# First JSON keys that identify a Phase 21 S1-type prompt echo: the model
# continued the input document instead of answering (classifier proven in
# scripts/phase21_run_benchmark.py).
_ECHO_FIRST_KEYS = frozenset({
    "scenario_id", "satellite_id", "window", "telemetry",
})


def _is_prompt_echo(raw_response: str) -> bool:
    """Deterministic S1-type signature on an UNPARSEABLE raw completion.

    Phase 21 evidence: the model echoed the prompt's leading JSON keys
    and/or exhausted the output budget, producing long unusable text.
    Only applied to text that already failed JSON extraction — a parseable
    completion is by definition not an echo of the input document.
    """
    if not raw_response:
        return False
    head = raw_response.lstrip()[:200]
    m = re.search(r'"(\w+)"\s*:', head)
    first_key = m.group(1) if m else ""
    if first_key in _ECHO_FIRST_KEYS:
        return True
    return len(raw_response) > 2000


def _is_timeout_error(exc: Exception) -> bool:
    """Classify a ProviderError as a timeout by its message (deterministic).

    Both LocalProvider call paths surface the underlying client error text
    (openai SDK timeout / urllib socket.timeout) inside the message.
    """
    msg = str(exc).lower()
    return "timeout" in msg or "timed out" in msg


class LocalBranchRunner:
    """Adapter executing the LOCAL branch through the existing pipeline.

    The runner never authorizes commands, never mutates physics verdicts,
    never mutates safety verdicts, and never converts invalid output into
    success.  If Phi-3 fails, the BranchResult says so; the future router
    decides whether to escalate.
    """

    def __init__(self, provider: LLMProvider, max_retries: int = 1):
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0 (no infinite retries)")
        self._provider = provider
        self._max_retries = max_retries

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def run(
        self,
        ranking_input: LLMRankingInput,
        physics_report: Any = None,
        review_already_required: bool = False,
    ) -> BranchResult:
        """Run the local branch and classify the outcome deterministically.

        ``review_already_required`` is OR-combined (monotone) with anything
        the run itself raises; it can never be downgraded.
        """
        started = time.perf_counter()
        attempts = 0
        raw_response = ""

        def _elapsed_ms() -> float:
            return (time.perf_counter() - started) * 1000.0

        def _failure(
            reasons: tuple[RoutingReason, ...],
            review: bool = False,
            inference_performed: bool = False,
        ) -> BranchResult:
            return BranchResult(
                branch=Branch.LOCAL,
                outcome=BranchOutcome.FAILURE,
                provider_name=self._provider.provider_name,
                model_name=self._provider.model_name,
                inference_performed=inference_performed,
                validated_output=None,
                guardrail_result=None,
                evidence_status=ranking_input.evidence_status,
                scenario_id=ranking_input.scenario_id,
                elapsed_ms=_elapsed_ms(),
                attempts=attempts,
                reason_codes=reasons,
                human_review_required=combine_human_review(
                    review_already_required, review,
                ),
                raw_text_head=raw_response[:_RAW_HEAD_CHARS],
            )

        # ── existing constrained pipeline: prompt → call → parse ────────
        messages = build_constrained_prompt(ranking_input)
        attempts_cap = 1 + self._max_retries

        for attempt in range(attempts_cap):
            attempts = attempt + 1
            try:
                raw_response = self._provider.call(messages)
            except ProviderError as exc:
                if _is_timeout_error(exc):
                    return _failure((RoutingReason.LOCAL_TIMEOUT,))
                return _failure((RoutingReason.LOCAL_UNAVAILABLE,))

            parsed: Optional[dict[str, Any]]
            try:
                parsed = _extract_json(raw_response)
            except (ValueError, KeyError, TypeError):
                parsed = None

            if parsed is not None:
                break  # parseable: proceed to typed parse + guardrails

            # Unparseable.  S1-type echoes are never retried in-process
            # (Phase 21: reproduction is near-certain on the same prompt).
            if _is_prompt_echo(raw_response):
                return _failure(
                    (RoutingReason.PROMPT_ECHO_TRUNCATION,),
                    inference_performed=True,
                )
            if attempt < attempts_cap - 1:
                # Bounded repair retry — identical to the existing
                # run_constrained_ranking convention.
                messages.append({"role": "assistant", "content": raw_response})
                messages.append({
                    "role": "user",
                    "content": (
                        "Your response was not valid JSON.\n"
                        "Please output ONLY a corrected JSON object."
                    ),
                })
                continue
            return _failure(
                (RoutingReason.INVALID_STRUCTURED_OUTPUT,),
                inference_performed=True,
            )

        # ── existing typed parse + guardrails ───────────────────────────
        try:
            from app.llm.models import LLMRankingOutput

            output = LLMRankingOutput.from_dict(parsed)
        except (ValueError, KeyError, TypeError):
            return _failure(
                (RoutingReason.INVALID_STRUCTURED_OUTPUT,),
                inference_performed=True,
            )

        guardrail_result = validate_ranking_output(
            output, ranking_input, physics_report,
            raw_parsed=parsed, raw_response=raw_response,
        )

        human_review = combine_human_review(
            review_already_required,
            output.requires_human_review,
        )

        if guardrail_result.violations:
            # Honest failure: violations are never silently repaired into
            # success.  The corrected output travels inside the guardrail
            # result for audit; the branch itself is a FAILURE.
            reasons = tuple(dict.fromkeys(
                _VIOLATION_REASON.get(v.violation_type,
                                      RoutingReason.EVIDENCE_FAILURE)
                for v in guardrail_result.violations
            ))
            review = combine_human_review(
                human_review,
                any(v.violation_type in _REVIEW_FORCING_VIOLATIONS
                    for v in guardrail_result.violations),
            )
            logger.warning(
                "Local branch FAILURE: %d guardrail violation(s): %s",
                len(guardrail_result.violations),
                [v.violation_type.value for v in guardrail_result.violations],
            )
            return BranchResult(
                branch=Branch.LOCAL,
                outcome=BranchOutcome.FAILURE,
                provider_name=self._provider.provider_name,
                model_name=self._provider.model_name,
                inference_performed=True,
                validated_output=None,
                guardrail_result=guardrail_result,
                evidence_status=ranking_input.evidence_status,
                scenario_id=ranking_input.scenario_id,
                elapsed_ms=_elapsed_ms(),
                attempts=attempts,
                reason_codes=reasons,
                human_review_required=review,
                raw_text_head=raw_response[:_RAW_HEAD_CHARS],
            )

        # ── ACCEPT: zero violations; validated output is trustworthy ────
        return BranchResult(
            branch=Branch.LOCAL,
            outcome=BranchOutcome.ACCEPT,
            provider_name=self._provider.provider_name,
            model_name=self._provider.model_name,
            inference_performed=True,
            validated_output=output,
            guardrail_result=guardrail_result,
            evidence_status=ranking_input.evidence_status,
            scenario_id=ranking_input.scenario_id,
            elapsed_ms=_elapsed_ms(),
            attempts=attempts,
            reason_codes=(RoutingReason.VALID_LOCAL_RESULT,),
            human_review_required=human_review,
            raw_text_head=raw_response[:_RAW_HEAD_CHARS],
        )
