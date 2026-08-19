"""
SENTINEL — Constrained LLM Models (llm/models.py)

Phase 10.  Structured input/output schemas for the constrained ranking pipeline.

The LLM receives ``LLMRankingInput`` and must return ``LLMRankingOutput``.

The LLM may:
  - Rank existing hypotheses
  - Explain evidence
  - Identify contradictions
  - Summarize uncertainty
  - Explain physics validation
  - Select existing procedure IDs
  - Communicate recommended actions

The LLM may NOT:
  - Invent telemetry
  - Invent procedures
  - Invent commands
  - Override safety
  - Override physics validation
  - Claim unsupported certainty
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════════════════════
# INPUT — everything the LLM receives
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class EvidenceContext:
    """One deterministic evidence item, as presented to the LLM (Phase 17).

    ``evidence_id`` is the stable id referenced by hypotheses and by the
    LLM's supporting/contradicting evidence lists. Quantitative residual
    detail lives in ``ResidualContext``; this context carries what the
    deterministic detector layer actually recorded.
    """
    evidence_id: str
    type: str                       # SUPPORTING | CONTRADICTING | UNDETERMINED
    source: str                     # TELEMETRY | CONTEXT
    description: str                # rationale/condition text
    channel: str
    condition: str
    state: str
    detectors: tuple[str, ...] = ()
    severity: str = ""
    timestamp: str = ""
    weight: float = 0.0
    provenance: str = ""            # observed_from or detector provenance


@dataclass(frozen=True)
class ResidualContext:
    """One observed-vs-predicted residual, with its quantitative values."""
    channel: str
    unit: str
    status: str                     # CONSISTENT | INCONSISTENT | UNDECIDABLE
    observed: Optional[float] = None
    predicted: Optional[float] = None
    residual: Optional[float] = None
    tolerance: Optional[float] = None
    exceedance: Optional[float] = None
    model: str = ""
    equation: str = ""
    comparison: str = ""


@dataclass(frozen=True)
class WindowAdequacyContext:
    """Whether the telemetry window can support physics at all (Phase 17).

    status is one of the WindowAdequacyStatus values: ADEQUATE_FOR_PHYSICS,
    UNDER_SAMPLED, MISSING_REQUIRED_CHANNELS, INVALID_TIMESTAMPS,
    CONTRADICTORY_DATA. The LLM must not override it.
    """
    status: str = "MISSING_REQUIRED_CHANNELS"
    sample_count: int = 0           # fresh samples across modelled channels
    required_sample_count: int = 0  # 2 per checkable channel (one step = two)
    channels_checked: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class HypothesisContext:
    """One deterministic hypothesis, as presented to the LLM.

    The LLM sees the hypothesis_id, fault_id, rank, score, and evidence.
    It may re-rank but may not invent new fault_ids.
    """
    hypothesis_id: str
    fault_id: str
    fault_name: str
    subsystem: str
    deterministic_rank: int
    deterministic_score: float
    supporting_evidence: tuple[str, ...] = ()
    contradicting_evidence: tuple[str, ...] = ()
    undetermined_evidence: tuple[str, ...] = ()
    causal_chain: tuple[str, ...] = ()
    affected_channels: tuple[str, ...] = ()
    physics_status: str = "UNCERTAIN"
    notes: str = ""


@dataclass(frozen=True)
class ProcedureContext:
    """A retrieved procedure, as presented to the LLM."""
    procedure_id: str
    title: str
    subsystem: str
    fault_class: str
    source_type: str
    citation_id: str
    step_count: int
    risk: str


@dataclass(frozen=True)
class PhysicsContext:
    """Physics validation summary, as presented to the LLM."""
    hypotheses_examined: int = 0
    invalidated: tuple[str, ...] = ()
    validated: tuple[str, ...] = ()
    uncertain: tuple[str, ...] = ()
    summary: str = ""
    assumed_parameters: tuple[str, ...] = ()
    model_limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpacecraftStateContext:
    """Simplified spacecraft state estimate, as presented to the LLM."""
    state_summary: str = ""
    anomalous_channels: tuple[str, ...] = ()
    residual_summary: str = ""
    channels_modelled: tuple[str, ...] = ()
    residuals: tuple[ResidualContext, ...] = ()
    window_adequacy: WindowAdequacyContext = WindowAdequacyContext()


@dataclass(frozen=True)
class SafetyContext:
    """Safety constraints the LLM must respect."""
    valid_command_ids: tuple[str, ...] = ()
    blocked_constraint_types: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class LLMRankingInput:
    """The complete context bundle sent to the LLM.

    Every field the user's specification requires:
    - anomaly reports
    - deterministic hypotheses
    - physics validation
    - spacecraft state
    - residuals
    - procedure evidence
    - citations
    - safety constraints
    """
    # Anomaly report
    anomaly_summary: str = ""
    anomalous_channels: tuple[str, ...] = ()
    anomaly_count: int = 0

    # Deterministic hypotheses (Phase 6)
    hypotheses: tuple[HypothesisContext, ...] = ()
    valid_fault_ids: tuple[str, ...] = ()

    # Physics validation (Phase 8)
    physics: PhysicsContext = PhysicsContext()

    # Spacecraft state (Phase 7)
    spacecraft_state: SpacecraftStateContext = SpacecraftStateContext()

    # Procedure evidence (Phase 9)
    procedures: tuple[ProcedureContext, ...] = ()
    valid_procedure_ids: tuple[str, ...] = ()

    # Safety constraints
    safety: SafetyContext = SafetyContext()

    # Crash dump metadata
    scenario_id: str = ""
    fault_type: str = ""
    safe_mode_trigger: str = ""

    def as_prompt_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for JSON-encoding into the prompt."""
        return {
            "anomaly_summary": self.anomaly_summary,
            "anomalous_channels": list(self.anomalous_channels),
            "anomaly_count": self.anomaly_count,
            "hypotheses": [
                {
                    "hypothesis_id": h.hypothesis_id,
                    "fault_id": h.fault_id,
                    "fault_name": h.fault_name,
                    "subsystem": h.subsystem,
                    "deterministic_rank": h.deterministic_rank,
                    "deterministic_score": h.deterministic_score,
                    "supporting_evidence": list(h.supporting_evidence),
                    "contradicting_evidence": list(h.contradicting_evidence),
                    "causal_chain": list(h.causal_chain),
                    "affected_channels": list(h.affected_channels),
                    "physics_status": h.physics_status,
                }
                for h in self.hypotheses
            ],
            "valid_fault_ids": list(self.valid_fault_ids),
            "physics": {
                "hypotheses_examined": self.physics.hypotheses_examined,
                "invalidated": list(self.physics.invalidated),
                "validated": list(self.physics.validated),
                "uncertain": list(self.physics.uncertain),
                "summary": self.physics.summary,
            },
            "spacecraft_state": {
                "state_summary": self.spacecraft_state.state_summary,
                "anomalous_channels": list(
                    self.spacecraft_state.anomalous_channels
                ),
                "residual_summary": self.spacecraft_state.residual_summary,
            },
            "procedures": [
                {
                    "procedure_id": p.procedure_id,
                    "title": p.title,
                    "subsystem": p.subsystem,
                    "fault_class": p.fault_class,
                    "source_type": p.source_type,
                    "citation_id": p.citation_id,
                }
                for p in self.procedures
            ],
            "valid_procedure_ids": list(self.valid_procedure_ids),
            "safety_constraints": {
                "notes": self.safety.notes,
            },
            "scenario_id": self.scenario_id,
            "fault_type": self.fault_type,
            "safe_mode_trigger": self.safe_mode_trigger,
        }


# ═══════════════════════════════════════════════════════════════════════════
# OUTPUT — what the LLM must return
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class RankedHypothesis:
    """One hypothesis as ranked by the LLM.

    The fault_id MUST come from the deterministic hypothesis set.
    The LLM may adjust the rank and provide a justification.
    """
    fault_id: str
    rank: int
    confidence: float
    justification: str = ""
    affected_component: str = ""
    causal_chain: tuple[str, ...] = ()


@dataclass(frozen=True)
class LLMRankingOutput:
    """The constrained JSON output the LLM must return.

    All 7 required fields from the Phase 10 specification.
    """
    ranked_hypotheses: tuple[RankedHypothesis, ...] = ()
    reasoning_summary: str = ""
    supporting_evidence_ids: tuple[str, ...] = ()
    contradicting_evidence_ids: tuple[str, ...] = ()
    selected_procedure_ids: tuple[str, ...] = ()
    uncertainty: str = ""
    requires_human_review: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LLMRankingOutput:
        """Parse the LLM's JSON response into a typed output.

        Tolerant of missing fields — defaults to safe values.
        """
        ranked = []
        for h in data.get("ranked_hypotheses", []):
            ranked.append(RankedHypothesis(
                fault_id=h.get("fault_id", ""),
                rank=h.get("rank", 0),
                confidence=max(0.0, min(1.0, float(h.get("confidence", 0.0)))),
                justification=h.get("justification", ""),
                affected_component=h.get("affected_component", ""),
                causal_chain=tuple(h.get("causal_chain", [])),
            ))

        return cls(
            ranked_hypotheses=tuple(ranked),
            reasoning_summary=data.get("reasoning_summary", ""),
            supporting_evidence_ids=tuple(
                data.get("supporting_evidence_ids", [])
            ),
            contradicting_evidence_ids=tuple(
                data.get("contradicting_evidence_ids", [])
            ),
            selected_procedure_ids=tuple(
                data.get("selected_procedure_ids", [])
            ),
            uncertainty=data.get("uncertainty", ""),
            requires_human_review=data.get("requires_human_review", True),
        )


# ═══════════════════════════════════════════════════════════════════════════
# GUARDRAIL RESULTS
# ═══════════════════════════════════════════════════════════════════════════

class ViolationType(str, Enum):
    """Categories of guardrail violations."""
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
    NONEXISTENT_EVIDENCE = "NONEXISTENT_EVIDENCE"
    PHYSICS_OVERRIDE = "PHYSICS_OVERRIDE"
    INVALID_PROCEDURE = "INVALID_PROCEDURE"
    UNSUPPORTED_HYPOTHESIS = "UNSUPPORTED_HYPOTHESIS"
    INVENTED_TELEMETRY = "INVENTED_TELEMETRY"
    UNSUPPORTED_CERTAINTY = "UNSUPPORTED_CERTAINTY"


@dataclass(frozen=True)
class GuardrailViolation:
    """A single guardrail violation detected in the LLM output."""
    violation_type: ViolationType
    detail: str
    offending_value: str = ""
    corrective_action: str = ""


@dataclass(frozen=True)
class GuardrailResult:
    """Result of post-call guardrail validation.

    ``is_valid`` is True only when there are zero violations.
    When violations are found, the corrected output preserves the
    deterministic validation as authoritative and strips the offending
    claims.

    ``raw_response`` (set by the ranker after validation) preserves the
    verbatim model output for audit, so the record shows what the model
    asked for AND what the guardrails refused.
    """
    is_valid: bool
    violations: tuple[GuardrailViolation, ...] = ()
    corrected_output: Optional[LLMRankingOutput] = None
    original_output: Optional[LLMRankingOutput] = None
    raw_response: str = ""

    @property
    def violation_count(self) -> int:
        return len(self.violations)

    @property
    def violation_types(self) -> tuple[ViolationType, ...]:
        return tuple(v.violation_type for v in self.violations)
