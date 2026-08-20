"""
SENTINEL — Hybrid Router Cloud Branch Runner (app/llm/cloud_branch.py)

Phase 23 Step 3.  An ADAPTER around the existing constrained ranking
pipeline for the CLOUD branch, plus the MANDATORY fail-closed cloud
redaction gate that sits BEFORE any network transmission:

    ranking_input → serialize (as_prompt_dict)
                  → CLOUD REDACTION GATE      (this module, fail closed)
                  → cloud prompt from REDACTED dict
                  → provider.call             (existing GeminiProvider)
                  → _extract_json             (existing parser)
                  → LLMRankingOutput.from_dict(existing typed parse)
                  → validate_ranking_output   (existing guardrails)
                  → BranchResult              (Step 1 contract)

The runner OWNS the sequence ``redact → prompt → call``.  There is no
public entry point that builds a cloud prompt from the unredacted bundle,
so a redaction bypass through the normal API is impossible (Phase 23
Step 3 Part 13).  Privacy is enforced BEFORE the network boundary; it
never relies on the model's instructions, refusal behavior, or its
understanding of sensitive data (Part 14).

Redaction reuses the EXISTING framework — no second implementation:

    app.security.exfiltration.apply_cloud_redaction / classify_payload
    app.security.redaction.classify_data / _SECRET_PATTERNS

The gate ADDS a fail-closed verification layer on top of the existing
key-name classification, because the ranking bundle carries free-text
summaries derived from the crash dump (``anomaly_summary``,
``state_summary``, ``residual_summary``, hypothesis ``notes``) that a
key-name classifier alone cannot inspect.  The extra scans are:

    * confidential-key-name substrings inside free-text fields (the same
      key vocabulary ``classify_data`` uses)
    * the existing secret shapes (``_SECRET_PATTERNS``) anywhere in the
      transmitted text
    * internal filesystem paths (environment-derived information)

If redaction fails, raises, produces malformed output, or cannot PROVE
the payload cloud-safe, the gate raises CloudRedactionError and the
runner returns FAILURE + REDACTION_GATE_FAILURE WITHOUT calling the
provider (Part 4: fail closed).

Quantitative physics information (residuals, verdict lists, scores,
evidence IDs, procedure IDs) is preserved — the repository's privacy
contract classifies it PUBLIC, and hypothesis ranking, physics
interpretation, evidence grounding, and procedure selection all depend
on it (Part 5: minimum necessary cloud disclosure).

The runner is dormant: the production path does not invoke it while
ROUTER_ENABLED=false.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from app.llm.models import LLMRankingInput, ViolationType
from app.llm.provider import LLMProvider, ProviderError
from app.llm.ranker import (
    _CONSTRAINED_SYSTEM_PROMPT,
    _extract_json,
    validate_ranking_output,
)
from app.llm.router_contract import (
    Branch,
    BranchOutcome,
    BranchResult,
    RoutingReason,
    combine_human_review,
)
from app.security.config import SecurityConfig
from app.security.exfiltration import apply_cloud_redaction
from app.security.redaction import (
    _SECRET_PATTERNS,
    DataClassification,
    classify_data,
)

logger = logging.getLogger("sentinel.llm.cloud_branch")

# Diagnostics cap for the untrusted raw text blob (Phase 22 §7.2).
_RAW_HEAD_CHARS = 500

# Free-text fields in the transmitted prompt dict that are DERIVED from
# the crash dump and therefore need value-level scanning (the key-name
# classifier cannot inspect what a summary sentence contains).
_FREE_TEXT_FIELDS: tuple[tuple[str, ...], ...] = (
    ("anomaly_summary",),
    ("spacecraft_state", "state_summary"),
    ("spacecraft_state", "residual_summary"),
    ("physics", "summary"),
)

# Confidential key vocabulary — the same vocabulary classify_data() uses
# for field-name classification, applied here to free-text VALUES.
_CONFIDENTIAL_KEY_VOCABULARY = ("key", "secret", "token", "auth", "password")

# Environment-derived information: internal filesystem paths.
_PATH_LEAK = re.compile(r"(/Users/|/home/|/var/|/etc/|[A-Z]:\\\\?)")

# Guardrail violations that, beyond failing the branch, force human review
# (identical semantics to the local branch).
_REVIEW_FORCING_VIOLATIONS = frozenset({
    ViolationType.PHYSICS_OVERRIDE,
    ViolationType.INSUFFICIENT_EVIDENCE_CLAIM,
    ViolationType.INVENTED_TELEMETRY,
    ViolationType.UNKNOWN_COMMAND,
})

# Map guardrail violation types to routing reason codes (same mapping as
# the local branch — a violation means the same thing on either branch).
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


class CloudRedactionError(Exception):
    """The cloud redaction gate failed CLOSED.

    Raised when redaction fails, raises, produces malformed output, or
    cannot prove the payload cloud-safe.  The cloud runner must NOT call
    the provider after this error.
    """


@dataclass(frozen=True)
class CloudRedactionResult:
    """Output of a successful redaction gate pass.

    ``prompt_dict`` is the REDACTED copy — the only representation of the
    ranking input that may ever reach the cloud provider.
    ``report`` is the existing apply_cloud_redaction report extended with
    the gate's verification findings; it is audit metadata.
    """
    prompt_dict: dict[str, Any]
    report: dict[str, Any]


def _iter_string_values(value: Any):
    """Yield every string leaf inside a nested dict/list structure."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_string_values(item)


def _get_path(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _verify_cloud_safe(redacted: dict[str, Any]) -> list[str]:
    """Fail-closed verification that ``redacted`` is cloud-safe.

    Returns a list of findings; an empty list means the payload may be
    transmitted.  Any finding aborts the transmission.
    """
    findings: list[str] = []

    # 1. Existing key-name classification must be clean AFTER redaction:
    #    a surviving CONFIDENTIAL field means redaction did not cover it.
    #    A value already replaced by the redaction marker is the intended
    #    redacted representation, not a leak.
    def _walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child = f"{path}.{key}" if path else str(key)
                if (
                    classify_data(key, item)
                    is DataClassification.CONFIDENTIAL
                    and item != "[REDACTED]"
                ):
                    findings.append(f"confidential field survived: {child}")
                _walk(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                _walk(item, f"{path}[{index}]")

    _walk(redacted, "")

    # 2. Free-text summaries must not carry confidential material or
    #    environment-derived paths (crash-dump-derived content).
    for path in _FREE_TEXT_FIELDS:
        text = _get_path(redacted, path)
        if not isinstance(text, str) or not text:
            continue
        lowered = text.lower()
        for word in _CONFIDENTIAL_KEY_VOCABULARY:
            if word in lowered:
                findings.append(
                    f"confidential substring {word!r} in {'.'.join(path)}"
                )
        if _PATH_LEAK.search(text):
            findings.append(f"filesystem path leaked in {'.'.join(path)}")

    # 3. Existing secret shapes must not survive anywhere in the text that
    #    would be transmitted.
    serialized = json.dumps(redacted, default=str)
    for pattern in _SECRET_PATTERNS:
        if pattern.search(serialized):
            findings.append(
                f"secret pattern {pattern.pattern[:24]!r}... survived"
            )

    return findings


def redact_ranking_input_for_cloud(
    ranking_input: LLMRankingInput,
    config: SecurityConfig | None = None,
) -> CloudRedactionResult:
    """Mandatory cloud redaction gate for the constrained ranking bundle.

    Reuses the existing ``apply_cloud_redaction`` framework on the exact
    dict that the cloud prompt embeds, then VERIFYs the result is
    cloud-safe.  Fail closed: any failure, exception, malformed output,
    or unverifiable payload raises ``CloudRedactionError`` — the provider
    must never be called afterwards.

    The original ranking input is never mutated.
    """
    prompt_dict = copy.deepcopy(ranking_input.as_prompt_dict())
    if not isinstance(prompt_dict, dict) or not prompt_dict:
        raise CloudRedactionError(
            "malformed ranking input serialization: cannot prove cloud-safe"
        )

    try:
        redacted, report = apply_cloud_redaction(prompt_dict, config)
    except CloudRedactionError:
        raise
    except Exception as exc:  # fail closed on ANY redaction failure
        raise CloudRedactionError(f"redaction failed: {exc}") from exc

    if not isinstance(redacted, dict) or not redacted:
        raise CloudRedactionError(
            "redaction produced malformed output: cannot prove cloud-safe"
        )

    findings = _verify_cloud_safe(redacted)
    if findings:
        raise CloudRedactionError(
            "cannot prove payload cloud-safe: " + "; ".join(findings)
        )

    report = {
        **report,
        "gate": "phase23_step3_cloud_redaction",
        "gate_findings": [],
        "target": "constrained ranking prompt bundle",
    }
    return CloudRedactionResult(prompt_dict=redacted, report=report)


def _build_cloud_messages(
    ranking_input: LLMRankingInput,
    redacted_prompt_dict: dict[str, Any],
) -> list[dict[str, str]]:
    """Cloud prompt from the REDACTED dict.

    Structurally identical to the existing build_constrained_prompt (same
    system-prompt template, same ID allowlists, same evidence status), but
    the embedded context JSON is the redacted copy.  Private helper: the
    only sanctioned way to obtain a cloud prompt.
    """
    system_prompt = _CONSTRAINED_SYSTEM_PROMPT.format(
        valid_fault_ids=", ".join(ranking_input.valid_fault_ids),
        valid_procedure_ids=", ".join(ranking_input.valid_procedure_ids),
        evidence_status=ranking_input.evidence_status,
    )
    user_content = json.dumps(redacted_prompt_dict, indent=2, default=str)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def _is_timeout_error(exc: Exception) -> bool:
    """Classify a ProviderError as a timeout by its message (deterministic).

    GeminiProvider surfaces the google-genai client error text (network /
    deadline errors) inside the ProviderError message.
    """
    msg = str(exc).lower()
    return "timeout" in msg or "timed out" in msg or "deadline" in msg


def _is_prompt_echo(raw_response: str) -> bool:
    """Deterministic S1-type signature (shared classifier with the local
    branch).  Rare for Gemini (Phase 21: 0/7 observed) but classified
    honestly if it ever occurs."""
    if not raw_response:
        return False
    head = raw_response.lstrip()[:200]
    m = re.search(r'"(\w+)"\s*:', head)
    first_key = m.group(1) if m else ""
    if first_key in ("scenario_id", "satellite_id", "window", "telemetry"):
        return True
    return len(raw_response) > 2000


class CloudBranchRunner:
    """Adapter executing the CLOUD branch through the existing pipeline.

    The runner owns the mandatory sequence: REDACT → prompt → call.  It
    never authorizes commands, never mutates physics or safety verdicts,
    never converts invalid output into success, and never calls the
    provider when the redaction gate fails.
    """

    def __init__(
        self,
        provider: LLMProvider,
        max_retries: int = 1,
        security_config: SecurityConfig | None = None,
    ):
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0 (no infinite retries)")
        self._provider = provider
        self._max_retries = max_retries
        self._security_config = security_config

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def run(
        self,
        ranking_input: LLMRankingInput,
        physics_report: Any = None,
        review_already_required: bool = False,
    ) -> BranchResult:
        """Run the cloud branch and classify the outcome deterministically.

        ``review_already_required`` is OR-combined (monotone) with anything
        the run itself raises; it can never be downgraded.
        """
        started = time.perf_counter()
        attempts = 0
        raw_response = ""
        redaction_report: Optional[dict] = None

        def _elapsed_ms() -> float:
            return (time.perf_counter() - started) * 1000.0

        def _failure(
            reasons: tuple[RoutingReason, ...],
            review: bool = False,
            inference_performed: bool = False,
            guardrail_result: Any = None,
        ) -> BranchResult:
            return BranchResult(
                branch=Branch.CLOUD,
                outcome=BranchOutcome.FAILURE,
                provider_name=self._provider.provider_name,
                model_name=self._provider.model_name,
                inference_performed=inference_performed,
                validated_output=None,
                guardrail_result=guardrail_result,
                evidence_status=ranking_input.evidence_status,
                scenario_id=ranking_input.scenario_id,
                elapsed_ms=_elapsed_ms(),
                attempts=attempts,
                reason_codes=reasons,
                human_review_required=combine_human_review(
                    review_already_required, review,
                ),
                raw_text_head=raw_response[:_RAW_HEAD_CHARS],
                redaction_report=redaction_report,
            )

        # ── MANDATORY fail-closed redaction gate: BEFORE prompt build,
        #    BEFORE the provider call, BEFORE any network transmission ───
        try:
            gate = redact_ranking_input_for_cloud(
                ranking_input, self._security_config,
            )
        except CloudRedactionError as exc:
            logger.error("Cloud redaction gate FAILED CLOSED: %s", exc)
            return _failure(
                (RoutingReason.REDACTION_GATE_FAILURE,),
                review=True,  # security-boundary failure always escalates
            )
        redaction_report = gate.report

        messages = _build_cloud_messages(ranking_input, gate.prompt_dict)
        attempts_cap = 1 + self._max_retries

        for attempt in range(attempts_cap):
            attempts = attempt + 1
            try:
                raw_response = self._provider.call(messages)
            except ProviderError as exc:
                if _is_timeout_error(exc):
                    return _failure((RoutingReason.CLOUD_TIMEOUT,))
                return _failure((RoutingReason.CLOUD_UNAVAILABLE,))

            parsed: Optional[dict[str, Any]]
            try:
                parsed = _extract_json(raw_response)
            except (ValueError, KeyError, TypeError):
                parsed = None

            if parsed is not None:
                break  # parseable: proceed to typed parse + guardrails

            # Unparseable.  Echo-shaped completions are never retried
            # in-process (same deterministic convention as the local
            # branch).
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
                "Cloud branch FAILURE: %d guardrail violation(s): %s",
                len(guardrail_result.violations),
                [v.violation_type.value for v in guardrail_result.violations],
            )
            return _failure(
                reasons,
                review=review,
                inference_performed=True,
                guardrail_result=guardrail_result,
            )

        # ── ACCEPT: zero violations; validated output is trustworthy ────
        return BranchResult(
            branch=Branch.CLOUD,
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
            reason_codes=(RoutingReason.VALID_CLOUD_RESULT,),
            human_review_required=human_review,
            raw_text_head=raw_response[:_RAW_HEAD_CHARS],
            redaction_report=redaction_report,
        )
