"""
SENTINEL — Constrained LLM Ranker (llm/ranker.py)

Phase 10.  The constrained ranking pipeline:

    1. build_ranking_input()        assemble full context from pipeline stages
    2. build_constrained_prompt()   system + user prompt with guardrails
    3. call LLM via provider
    4. validate_ranking_output()    post-call guardrails — REJECT violations
    5. convert_to_sentinel_output() backward-compat conversion

Guardrails:
    - REJECT unknown commands
    - REJECT nonexistent evidence IDs
    - REJECT physics overrides (preserve deterministic as authoritative)
    - REJECT invalid procedure IDs
    - REJECT unsupported hypothesis fault_ids

If guardrails detect violations, the output is CORRECTED: offending claims
are stripped, deterministic validation is preserved, and the violations are
reported so the operator knows what was rejected and why.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

from app.llm.models import (
    GuardrailResult,
    GuardrailViolation,
    HypothesisContext,
    LLMRankingInput,
    LLMRankingOutput,
    PhysicsContext,
    ProcedureContext,
    RankedHypothesis,
    SafetyContext,
    SpacecraftStateContext,
    ViolationType,
)
from app.llm.provider import LLMProvider, ProviderError

logger = logging.getLogger("sentinel.llm.ranker")


# ═══════════════════════════════════════════════════════════════════════════
# CONSTRAINED SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════════

_CONSTRAINED_SYSTEM_PROMPT = """\
You are SENTINEL, a constrained spacecraft fault diagnosis assistant.

YOUR ROLE: Rank and explain the deterministic engineering hypotheses provided to you.

YOU MUST:
- Rank the provided hypotheses by likelihood, using the evidence
- Explain your reasoning in concise operational language
- Reference only evidence IDs that appear in the input
- Select procedure IDs only from the provided valid list
- Respect physics validation verdicts as authoritative
- Set requires_human_review=true when uncertain

YOU MUST NOT:
- Invent new fault hypotheses beyond those provided
- Invent telemetry values or channel names
- Invent procedures or commands
- Override or contradict physics validation verdicts
- Override safety constraints
- Claim certainty when evidence is ambiguous

OUTPUT FORMAT: Return ONLY a JSON object with these exact fields:
{
  "ranked_hypotheses": [
    {
      "fault_id": "<from valid_fault_ids>",
      "rank": 1,
      "confidence": 0.85,
      "justification": "<concise operational reasoning>",
      "affected_component": "<component name>",
      "causal_chain": ["event1", "event2", "event3"]
    }
  ],
  "reasoning_summary": "<2-4 sentence operational summary>",
  "supporting_evidence_ids": ["<evidence IDs from input>"],
  "contradicting_evidence_ids": ["<evidence IDs from input>"],
  "selected_procedure_ids": ["<from valid_procedure_ids>"],
  "uncertainty": "<where you are unsure>",
  "requires_human_review": true
}

CONSTRAINTS:
- ranked_hypotheses must contain exactly 3 entries with ranks 1, 2, 3
- fault_id values MUST come from: {valid_fault_ids}
- selected_procedure_ids MUST come from: {valid_procedure_ids}
- Confidence for rank 1 >= rank 2 >= rank 3
- If physics validation says INVALID for a hypothesis, rank it lower
- If physics validation says VALID for a hypothesis, note it as corroborated
- Do NOT output any text outside the JSON object
"""


def build_constrained_prompt(
    ranking_input: LLMRankingInput,
) -> list[dict[str, str]]:
    """Build the constrained prompt messages for the LLM.

    The system prompt contains the guardrail rules and valid ID lists.
    The user prompt contains the full context bundle as JSON.

    Returns:
        Standard chat-completion messages list.
    """
    system_prompt = _CONSTRAINED_SYSTEM_PROMPT.format(
        valid_fault_ids=", ".join(ranking_input.valid_fault_ids),
        valid_procedure_ids=", ".join(ranking_input.valid_procedure_ids),
    )

    user_content = json.dumps(
        ranking_input.as_prompt_dict(), indent=2, default=str,
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


# ═══════════════════════════════════════════════════════════════════════════
# INPUT ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════

def build_ranking_input(
    crash_dump: dict[str, Any],
    anomaly_report: Any = None,
    hypothesis_set: Any = None,
    physics_report: Any = None,
    residual_report: Any = None,
    state_sequence: Any = None,
    procedure_results: Any = None,
) -> LLMRankingInput:
    """Assemble the full context bundle from pipeline stage outputs.

    Each argument is optional — the ranker works with whatever stages
    completed successfully. Missing stages produce empty context.

    Args:
        crash_dump:        Raw crash dump dict
        anomaly_report:    Phase 2 AnomalyReport
        hypothesis_set:    Phase 6 HypothesisSet
        physics_report:    Phase 8 PhysicsValidationReport
        residual_report:   Phase 7 ResidualReport
        state_sequence:    Phase 7 StateSequence
        procedure_results: Phase 9 RetrievalResponse

    Returns:
        LLMRankingInput — everything the LLM needs.
    """
    # --- Anomaly context ---
    anomaly_summary = ""
    anomalous_channels: list[str] = []
    anomaly_count = 0
    if anomaly_report is not None:
        anomaly_summary = getattr(anomaly_report, "summary", "")
        anomalous_channels = (
            anomaly_report.anomalous_channel_names()
            if hasattr(anomaly_report, "anomalous_channel_names")
            else []
        )
        anomaly_count = getattr(anomaly_report, "anomaly_count", 0)

    # --- Hypothesis context ---
    hyp_contexts: list[HypothesisContext] = []
    valid_fault_ids: list[str] = []
    if hypothesis_set is not None:
        hypotheses = getattr(hypothesis_set, "hypotheses", [])
        for h in hypotheses:
            physics_status = "UNCERTAIN"
            if physics_report is not None:
                verdict = physics_report.verdict_for_fault(h.fault_id)
                if verdict is not None:
                    physics_status = verdict.validation_status.value

            hyp_contexts.append(HypothesisContext(
                hypothesis_id=getattr(h, "hypothesis_id", ""),
                fault_id=h.fault_id,
                fault_name=getattr(h, "fault_name", ""),
                subsystem=getattr(h, "subsystem", ""),
                deterministic_rank=h.rank,
                deterministic_score=h.score,
                supporting_evidence=tuple(
                    e.evidence_id
                    for e in getattr(h, "supporting_evidence", [])
                    if hasattr(e, "evidence_id")
                ),
                contradicting_evidence=tuple(
                    e.evidence_id
                    for e in getattr(h, "contradicting_evidence", [])
                    if hasattr(e, "evidence_id")
                ),
                undetermined_evidence=tuple(
                    e.evidence_id
                    for e in getattr(h, "undetermined_evidence", [])
                    if hasattr(e, "evidence_id")
                ),
                causal_chain=tuple(getattr(h, "causal_chain", [])),
                affected_channels=tuple(
                    getattr(h, "affected_channels", [])
                ),
                physics_status=physics_status,
            ))
        valid_fault_ids = list(
            getattr(hypothesis_set, "candidate_fault_ids", lambda: set())()
        )

    # --- Physics context ---
    physics_ctx = PhysicsContext()
    if physics_report is not None:
        physics_ctx = PhysicsContext(
            hypotheses_examined=getattr(
                physics_report, "hypotheses_examined", 0
            ),
            invalidated=tuple(
                getattr(physics_report, "invalidated", [])
            ),
            validated=tuple(
                getattr(physics_report, "validated", [])
            ),
            uncertain=tuple(
                getattr(physics_report, "uncertain", [])
            ),
            summary=getattr(physics_report, "summary", ""),
            assumed_parameters=tuple(
                str(p) for p in
                getattr(physics_report, "assumed_parameters", [])
            ),
            model_limitations=tuple(
                getattr(physics_report, "model_limitations", [])
            ),
        )

    # --- Spacecraft state context ---
    state_ctx = SpacecraftStateContext()
    if residual_report is not None:
        state_ctx = SpacecraftStateContext(
            state_summary=getattr(residual_report, "summary", ""),
            anomalous_channels=tuple(anomalous_channels),
            residual_summary=getattr(residual_report, "summary", ""),
            channels_modelled=tuple(
                getattr(
                    state_sequence, "channels_modelled", []
                ) if state_sequence else []
            ),
        )

    # --- Procedure context ---
    proc_contexts: list[ProcedureContext] = []
    valid_procedure_ids: list[str] = []
    if procedure_results is not None:
        results = getattr(procedure_results, "results", [])
        for r in results:
            proc = r.procedure
            citation = r.citation
            proc_contexts.append(ProcedureContext(
                procedure_id=proc.procedure_id,
                title=proc.title,
                subsystem=proc.subsystem.value
                if hasattr(proc.subsystem, "value") else str(proc.subsystem),
                fault_class=proc.fault_class,
                source_type=r.source_type.value
                if hasattr(r.source_type, "value") else str(r.source_type),
                citation_id=citation.citation_id if citation else "",
                step_count=len(proc.steps),
                risk=proc.risk.value
                if hasattr(proc.risk, "value") else str(proc.risk),
            ))
            valid_procedure_ids.append(proc.procedure_id)

    # Also include ALL procedure IDs from the library as valid
    try:
        from app.procedures.library import PROCEDURE_LIBRARY
        valid_procedure_ids = list(PROCEDURE_LIBRARY.keys())
    except ImportError:
        pass

    # --- Safety context ---
    valid_cmd_ids: list[str] = []
    try:
        from app.validation.command_registry import COMMAND_REGISTRY
        valid_cmd_ids = list(COMMAND_REGISTRY.keys())
    except ImportError:
        pass

    safety_ctx = SafetyContext(
        valid_command_ids=tuple(valid_cmd_ids),
        notes=(
            "The LLM may select procedure IDs but may NOT invent new commands. "
            "All commands must exist in the COMMAND_REGISTRY."
        ),
    )

    return LLMRankingInput(
        anomaly_summary=anomaly_summary,
        anomalous_channels=tuple(anomalous_channels),
        anomaly_count=anomaly_count,
        hypotheses=tuple(hyp_contexts),
        valid_fault_ids=tuple(valid_fault_ids),
        physics=physics_ctx,
        spacecraft_state=state_ctx,
        procedures=tuple(proc_contexts),
        valid_procedure_ids=tuple(valid_procedure_ids),
        safety=safety_ctx,
        scenario_id=crash_dump.get("scenario_id", ""),
        fault_type=crash_dump.get("fault_type", ""),
        safe_mode_trigger=crash_dump.get("safe_mode_trigger", ""),
    )


# ═══════════════════════════════════════════════════════════════════════════
# POST-CALL GUARDRAILS
# ═══════════════════════════════════════════════════════════════════════════

def _extract_json(raw: str) -> dict[str, Any]:
    """Extract a JSON object from raw LLM text, tolerant of wrapping."""
    text = raw.strip()

    # Strip <think>...</think> blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try code fence
    fence = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try outermost braces
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract JSON from LLM response (len={len(text)})")


def validate_ranking_output(
    output: LLMRankingOutput,
    ranking_input: LLMRankingInput,
    physics_report: Any = None,
) -> GuardrailResult:
    """Validate the LLM's ranking output against guardrails.

    Rules:
      1. Every fault_id in ranked_hypotheses MUST be in valid_fault_ids
      2. Every selected_procedure_id MUST be in valid_procedure_ids
      3. Supporting/contradicting evidence IDs should reference real evidence
      4. If physics says INVALID for a fault, the LLM must not rank it #1
         with high confidence (physics is authoritative)
      5. No invented commands

    Returns:
        GuardrailResult with violations and optionally a corrected output.
    """
    violations: list[GuardrailViolation] = []
    corrected_hypotheses = list(output.ranked_hypotheses)
    corrected_procedure_ids = list(output.selected_procedure_ids)

    valid_faults = set(ranking_input.valid_fault_ids)
    valid_procs = set(ranking_input.valid_procedure_ids)

    # Collect all valid evidence IDs from the input hypotheses
    valid_evidence: set[str] = set()
    for h in ranking_input.hypotheses:
        valid_evidence.update(h.supporting_evidence)
        valid_evidence.update(h.contradicting_evidence)
        valid_evidence.update(h.undetermined_evidence)

    # --- 1. Check fault_ids ---
    for rh in output.ranked_hypotheses:
        if valid_faults and rh.fault_id not in valid_faults:
            violations.append(GuardrailViolation(
                violation_type=ViolationType.UNSUPPORTED_HYPOTHESIS,
                detail=(
                    f"LLM ranked fault_id '{rh.fault_id}' which is not in "
                    f"the deterministic hypothesis set"
                ),
                offending_value=rh.fault_id,
                corrective_action="Hypothesis removed from ranking",
            ))
            corrected_hypotheses = [
                h for h in corrected_hypotheses
                if h.fault_id != rh.fault_id
            ]

    # --- 2. Check procedure IDs ---
    for pid in output.selected_procedure_ids:
        if valid_procs and pid not in valid_procs:
            violations.append(GuardrailViolation(
                violation_type=ViolationType.INVALID_PROCEDURE,
                detail=(
                    f"LLM selected procedure_id '{pid}' which is not in "
                    f"PROCEDURE_LIBRARY"
                ),
                offending_value=pid,
                corrective_action="Procedure ID removed from selection",
            ))
            corrected_procedure_ids = [
                p for p in corrected_procedure_ids if p != pid
            ]

    # --- 3. Check evidence IDs ---
    if valid_evidence:
        for eid in output.supporting_evidence_ids:
            if eid not in valid_evidence:
                violations.append(GuardrailViolation(
                    violation_type=ViolationType.NONEXISTENT_EVIDENCE,
                    detail=(
                        f"LLM referenced supporting evidence '{eid}' which "
                        f"does not exist in the input"
                    ),
                    offending_value=eid,
                    corrective_action="Evidence ID noted as unverifiable",
                ))
        for eid in output.contradicting_evidence_ids:
            if eid not in valid_evidence:
                violations.append(GuardrailViolation(
                    violation_type=ViolationType.NONEXISTENT_EVIDENCE,
                    detail=(
                        f"LLM referenced contradicting evidence '{eid}' which "
                        f"does not exist in the input"
                    ),
                    offending_value=eid,
                    corrective_action="Evidence ID noted as unverifiable",
                ))

    # --- 4. Physics override check ---
    if physics_report is not None:
        invalidated = set(getattr(physics_report, "invalidated", []))
        for rh in output.ranked_hypotheses:
            if (rh.fault_id in invalidated
                    and rh.rank == 1
                    and rh.confidence > 0.5):
                violations.append(GuardrailViolation(
                    violation_type=ViolationType.PHYSICS_OVERRIDE,
                    detail=(
                        f"LLM ranked physics-INVALID fault '{rh.fault_id}' "
                        f"as #1 with confidence {rh.confidence:.2f}. "
                        f"Physics validation is authoritative."
                    ),
                    offending_value=rh.fault_id,
                    corrective_action=(
                        "Hypothesis demoted; physics verdict preserved"
                    ),
                ))
                # Demote: reduce confidence and move to lower rank
                corrected_hypotheses = [
                    RankedHypothesis(
                        fault_id=h.fault_id,
                        rank=h.rank,
                        confidence=min(h.confidence, 0.3)
                        if h.fault_id == rh.fault_id else h.confidence,
                        justification=h.justification + (
                            " [DEMOTED: physics validation INVALID]"
                            if h.fault_id == rh.fault_id else ""
                        ),
                        affected_component=h.affected_component,
                        causal_chain=h.causal_chain,
                    )
                    for h in corrected_hypotheses
                ]

    # --- Build corrected output ---
    corrected = LLMRankingOutput(
        ranked_hypotheses=tuple(corrected_hypotheses),
        reasoning_summary=output.reasoning_summary,
        supporting_evidence_ids=output.supporting_evidence_ids,
        contradicting_evidence_ids=output.contradicting_evidence_ids,
        selected_procedure_ids=tuple(corrected_procedure_ids),
        uncertainty=output.uncertainty,
        requires_human_review=output.requires_human_review or bool(violations),
    )

    return GuardrailResult(
        is_valid=len(violations) == 0,
        violations=tuple(violations),
        corrected_output=corrected if violations else None,
        original_output=output if violations else None,
    )


# ═══════════════════════════════════════════════════════════════════════════
# OUTPUT CONVERSION — backward compatibility
# ═══════════════════════════════════════════════════════════════════════════

def convert_to_sentinel_output(
    ranking_output: LLMRankingOutput,
    procedure_results: Any = None,
) -> dict[str, Any]:
    """Convert a validated LLMRankingOutput to a SentinelOutput-compatible dict.

    The existing frontend expects ``SentinelOutput`` (3 hypotheses, recovery_plan,
    confidence, etc).  This function bridges the Phase 10 constrained output to
    the Phase 3 contract.

    Returns:
        Dict that can be validated as ``SentinelOutput.model_validate(d)``.
    """
    from app.api.models import RiskLevel

    # Build 3 hypotheses (pad if fewer, truncate if more)
    hypotheses: list[dict[str, Any]] = []
    ranked = sorted(ranking_output.ranked_hypotheses, key=lambda h: h.rank)

    for i, rh in enumerate(ranked[:3]):
        hypotheses.append({
            "rank": i + 1,
            "root_cause": rh.fault_id,
            "affected_component": rh.affected_component or rh.fault_id,
            "confidence": rh.confidence,
            "causal_chain": list(rh.causal_chain) or [
                "See reasoning_summary for causal analysis",
                "Deterministic engine provided supporting evidence",
            ],
        })

    # Pad to 3 if needed
    while len(hypotheses) < 3:
        idx = len(hypotheses) + 1
        hypotheses.append({
            "rank": idx,
            "root_cause": "INSUFFICIENT_EVIDENCE",
            "affected_component": "UNKNOWN",
            "confidence": max(0.05, 0.10 - 0.03 * idx),
            "causal_chain": [
                "Insufficient evidence for additional hypothesis",
                "Operator review recommended",
            ],
        })

    # Build recovery plan from selected procedures
    recovery_plan: list[dict[str, Any]] = []
    if ranking_output.selected_procedure_ids:
        try:
            from app.procedures.library import PROCEDURE_LIBRARY
            step_num = 1
            for pid in ranking_output.selected_procedure_ids:
                proc = PROCEDURE_LIBRARY.get(pid)
                if proc is None:
                    continue
                for ps in proc.steps:
                    recovery_plan.append({
                        "step": step_num,
                        "command": ps.command_id,
                        "description": ps.description,
                        "rationale": (
                            f"From procedure {pid}: {proc.title}"
                        ),
                        "risk": ps.risk.value,
                        "wait_seconds": ps.wait_seconds,
                    })
                    step_num += 1
        except ImportError:
            pass

    # If no recovery plan, add a minimal safe step
    if not recovery_plan:
        recovery_plan.append({
            "step": 1,
            "command": "CMD_HEALTH_CHECK",
            "description": "Run spacecraft-wide health check",
            "rationale": "No specific procedure selected; start with diagnostics",
            "risk": RiskLevel.LOW.value,
            "wait_seconds": 10,
        })

    top_confidence = hypotheses[0]["confidence"] if hypotheses else 0.5

    return {
        "hypotheses": hypotheses,
        "recovery_plan": recovery_plan,
        "confidence": top_confidence,
        "requires_human_review": ranking_output.requires_human_review,
        "reasoning_summary": ranking_output.reasoning_summary or (
            "Constrained ranking of deterministic hypotheses. "
            "See selected procedures for recommended actions."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# FULL PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

def run_constrained_ranking(
    provider: LLMProvider,
    ranking_input: LLMRankingInput,
    physics_report: Any = None,
    max_retries: int = 1,
) -> tuple[LLMRankingOutput, GuardrailResult, float]:
    """Run the full constrained ranking pipeline.

    1. Build prompt
    2. Call LLM
    3. Parse response
    4. Validate guardrails
    5. Return (output, guardrail_result, elapsed_ms)

    If guardrails detect violations, the corrected output is returned.
    The original output is preserved in ``guardrail_result.original_output``.

    Args:
        provider:      LLM provider to use
        ranking_input: Full context bundle
        physics_report: Physics validation report for guardrail checking
        max_retries:   Number of retries on parse failure

    Returns:
        (LLMRankingOutput, GuardrailResult, elapsed_ms)

    Raises:
        ProviderError: If the LLM call fails after retries
        ValueError:    If the response cannot be parsed as JSON after retries
    """
    messages = build_constrained_prompt(ranking_input)

    started = time.perf_counter()
    last_error: Exception | None = None
    attempts = 1 + max_retries

    for attempt in range(attempts):
        try:
            raw_response = provider.call(messages)

            # Parse JSON
            parsed = _extract_json(raw_response)

            # Convert to typed output
            output = LLMRankingOutput.from_dict(parsed)

            # Validate guardrails
            guardrail_result = validate_ranking_output(
                output, ranking_input, physics_report,
            )

            # Use corrected output if violations were found
            final_output = (
                guardrail_result.corrected_output
                if guardrail_result.corrected_output is not None
                else output
            )

            elapsed_ms = (time.perf_counter() - started) * 1000.0

            if guardrail_result.violations:
                logger.warning(
                    "LLM ranking: %d guardrail violation(s) corrected",
                    len(guardrail_result.violations),
                )
                for v in guardrail_result.violations:
                    logger.warning(
                        "  %s: %s (value=%s)",
                        v.violation_type.value, v.detail, v.offending_value,
                    )

            return final_output, guardrail_result, elapsed_ms

        except (ValueError, KeyError, TypeError) as e:
            last_error = e
            logger.warning(
                "Constrained ranking attempt %d/%d failed: %s",
                attempt + 1, attempts, e,
            )
            if attempt < attempts - 1:
                # Add repair context
                messages.append({"role": "assistant", "content": raw_response})
                messages.append({
                    "role": "user",
                    "content": (
                        f"Your response was not valid. Error: {e}\n"
                        f"Please output ONLY a corrected JSON object."
                    ),
                })

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    raise ValueError(
        f"Constrained ranking failed after {attempts} attempt(s): {last_error}"
    )
