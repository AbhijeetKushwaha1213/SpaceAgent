"""
SENTINEL — Operational Explainer (llm/explainer.py)

Phase 10.  Post-processing functions that generate concise operational
explanations from validated ranking output.

These operate on VALIDATED output — they do not call the LLM.
They are used by the SSE stream to emit concise operational summaries
instead of hidden chain-of-thought.

Public API:
    explain_ranking()        why hypotheses are ordered this way
    explain_evidence()       what evidence supports/contradicts
    explain_physics()        what physics validation found
    explain_uncertainty()    where the system is unsure
    identify_contradictions() where LLM and deterministic outputs disagree
"""

from __future__ import annotations

from typing import Any, Optional

from app.llm.models import (
    GuardrailResult,
    LLMRankingInput,
    LLMRankingOutput,
)


def explain_ranking(
    output: LLMRankingOutput,
    ranking_input: LLMRankingInput,
) -> str:
    """Generate a concise explanation of why hypotheses are ranked this way.

    Focuses on the top hypothesis and what distinguishes it from alternatives.
    No hidden chain-of-thought — just operational facts.
    """
    if not output.ranked_hypotheses:
        return "No hypotheses ranked. Insufficient evidence for diagnosis."

    ranked = sorted(output.ranked_hypotheses, key=lambda h: h.rank)
    top = ranked[0]

    parts: list[str] = []

    # Top hypothesis
    parts.append(
        f"Top hypothesis: {top.fault_id} "
        f"(confidence {top.confidence:.0%})"
    )

    # Justification if available
    if top.justification:
        # Truncate to operational summary length
        justification = top.justification
        if len(justification) > 200:
            justification = justification[:197] + "..."
        parts.append(f"Reasoning: {justification}")

    # How many hypotheses considered
    parts.append(
        f"{len(ranked)} hypothesis(es) ranked from "
        f"{len(ranking_input.hypotheses)} deterministic candidate(s)."
    )

    # Physics status of top hypothesis
    for h_ctx in ranking_input.hypotheses:
        if h_ctx.fault_id == top.fault_id:
            if h_ctx.physics_status == "VALID":
                parts.append("Physics: corroborated (not confirmed).")
            elif h_ctx.physics_status == "INVALID":
                parts.append("Physics: INVALID — contradicted by models.")
            else:
                parts.append("Physics: UNCERTAIN — no decisive evidence.")
            break

    return " ".join(parts)


def explain_evidence(
    output: LLMRankingOutput,
    ranking_input: LLMRankingInput,
) -> str:
    """Summarize what evidence supports and contradicts the diagnosis.

    Returns a concise operational summary.
    """
    parts: list[str] = []

    supporting = output.supporting_evidence_ids
    contradicting = output.contradicting_evidence_ids

    if supporting:
        parts.append(f"Supporting evidence: {len(supporting)} item(s).")
    else:
        parts.append("No supporting evidence cited.")

    if contradicting:
        parts.append(f"Contradicting evidence: {len(contradicting)} item(s).")
    else:
        parts.append("No contradicting evidence cited.")

    # Anomaly context
    if ranking_input.anomaly_count:
        parts.append(
            f"{ranking_input.anomaly_count} anomaly(ies) detected across "
            f"{len(ranking_input.anomalous_channels)} channel(s)."
        )

    return " ".join(parts)


def explain_physics(
    output: LLMRankingOutput,
    ranking_input: LLMRankingInput,
) -> str:
    """Summarize what the physics validation found.

    Returns a concise operational summary.
    """
    physics = ranking_input.physics

    if physics.hypotheses_examined == 0:
        return "Physics validation: not run for this analysis."

    parts: list[str] = [
        f"Physics validation examined {physics.hypotheses_examined} "
        f"hypothesis(es)."
    ]

    if physics.validated:
        parts.append(
            f"Corroborated: {', '.join(physics.validated)}."
        )
    if physics.invalidated:
        parts.append(
            f"INVALID (contradicted by models): {', '.join(physics.invalidated)}."
        )
    if physics.uncertain:
        parts.append(
            f"Uncertain (no decisive evidence): {', '.join(physics.uncertain)}."
        )

    if physics.summary:
        summary = physics.summary
        if len(summary) > 200:
            summary = summary[:197] + "..."
        parts.append(f"Summary: {summary}")

    return " ".join(parts)


def explain_uncertainty(
    output: LLMRankingOutput,
    ranking_input: LLMRankingInput,
) -> str:
    """Describe where the system is unsure.

    Returns a concise operational summary.
    """
    parts: list[str] = []

    if output.uncertainty:
        parts.append(f"LLM uncertainty: {output.uncertainty}")

    # Check for low-confidence top hypothesis
    if output.ranked_hypotheses:
        top = sorted(output.ranked_hypotheses, key=lambda h: h.rank)[0]
        if top.confidence < 0.5:
            parts.append(
                f"Top hypothesis confidence is low ({top.confidence:.0%}). "
                f"Multiple fault modes may be plausible."
            )

    # Physics uncertainties
    if ranking_input.physics.uncertain:
        parts.append(
            f"Physics could not decide on "
            f"{len(ranking_input.physics.uncertain)} hypothesis(es)."
        )

    if output.requires_human_review:
        parts.append("Operator review recommended.")

    if not parts:
        return "System confidence is adequate. No specific uncertainty flagged."

    return " ".join(parts)


def identify_contradictions(
    output: LLMRankingOutput,
    ranking_input: LLMRankingInput,
    guardrail_result: Optional[GuardrailResult] = None,
) -> list[str]:
    """Identify where the LLM and deterministic outputs disagree.

    Returns a list of concise contradiction descriptions.
    Used by the SSE stream and audit record.
    """
    contradictions: list[str] = []

    # Check rank disagreements
    if output.ranked_hypotheses and ranking_input.hypotheses:
        det_ranking = {
            h.fault_id: h.deterministic_rank
            for h in ranking_input.hypotheses
        }
        for rh in output.ranked_hypotheses:
            det_rank = det_ranking.get(rh.fault_id)
            if det_rank is not None and det_rank != rh.rank:
                contradictions.append(
                    f"Rank disagreement: {rh.fault_id} is deterministic "
                    f"rank {det_rank} but LLM ranked it {rh.rank}."
                )

    # Check physics contradictions
    invalidated = set(ranking_input.physics.invalidated)
    if output.ranked_hypotheses:
        top = sorted(output.ranked_hypotheses, key=lambda h: h.rank)[0]
        if top.fault_id in invalidated:
            contradictions.append(
                f"Physics contradiction: LLM's top hypothesis "
                f"'{top.fault_id}' was INVALIDATED by physics validation. "
                f"Deterministic verdict is authoritative."
            )

    # Guardrail violations
    if guardrail_result and guardrail_result.violations:
        for v in guardrail_result.violations:
            contradictions.append(
                f"Guardrail: {v.violation_type.value} — {v.detail}"
            )

    return contradictions
