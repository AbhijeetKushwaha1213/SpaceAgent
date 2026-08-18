"""
SENTINEL — Structured Output Contract (models.py)

This module defines the Pydantic schemas for SENTINEL's LLM agent output.
It is the single source of truth for the JSON shape that:
  - The LLM agent must produce (Person 2)
  - The FastAPI backend serializes over SSE (Person 4)
  - The React frontend renders (Person 3)
  - The evaluator scores against ground truth (Person 1)

Schema decisions are derived from:
  - SENTINEL_Hackathon_Strategy_v2.md Part 4.3 (system prompt output format)
  - SENTINEL_4Day_Master_Planner.md Section D (P2 deliverables)
  - ECSS-E-ST-70-11C safe mode recovery procedures
"""

from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Contract version
# ---------------------------------------------------------------------------

CONTRACT_VERSION = "1.0.0"
"""Version of the backend/frontend data contract.

Phase 3. Bump the MAJOR component on any breaking change to a request or
response shape, the MINOR component when adding an optional field, and the PATCH
component for documentation-only changes.

This value is exported into contracts/ and asserted by the contract tests, so a
schema change that is not accompanied by a regenerated contract fails the build.
"""

API_VERSION = "v1"
"""URL path segment for the versioned API, e.g. /api/v1/scenarios."""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SubsystemID(str, Enum):
    """Satellite subsystem identifiers used across all SENTINEL components.

    SYSTEM was added in Phase 1 for bus-level / cross-subsystem commands
    (health checks, telemetry dumps, status reads). It matches the "SYSTEM"
    group that already existed in safety.py's command whitelist.
    """
    ADCS = "ADCS"   # Attitude Determination & Control
    EPS = "EPS"     # Electrical Power System
    OBC = "OBC"     # On-Board Computer
    TCS = "TCS"     # Thermal Control System
    COMMS = "COMMS"  # Communications
    PYLD = "PYLD"   # Payload
    SYSTEM = "SYSTEM"  # Bus-level / cross-subsystem


class RiskLevel(str, Enum):
    """Risk classification for individual recovery steps."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    BLOCKED = "BLOCKED"  # Safety validator blocked this command


class AnalysisStatus(str, Enum):
    """Overall status of the agent's analysis run."""
    COMPLETE = "complete"
    PARTIAL = "partial"    # Graceful degradation — some steps succeeded
    TIMEOUT = "timeout"    # Agent hit the hard 90-second limit
    ERROR = "error"        # Unrecoverable failure


class SafetyStatus(str, Enum):
    """Outcome of deterministic safety validation on the recovery plan.

    Added in Phase 1. Before this existed, the pipeline emitted the literal
    string "Analysis complete. Safety validation passed." unconditionally,
    regardless of how many commands had been blocked — so a fully rejected plan
    was indistinguishable from a clean one.

    Precedence when several could apply (most severe wins):
        BLOCKED > PARTIALLY_BLOCKED > REQUIRES_HUMAN_REVIEW > VALIDATED

    ``requires_human_review`` remains a separate boolean on SentinelOutput, so
    collapsing to a single status never loses the review flag.
    """

    NOT_VALIDATED = "NOT_VALIDATED"
    """Safety validation did not run (e.g. skip_safety ablation). No claim is
    made about the plan's safety."""

    VALIDATED = "VALIDATED"
    """Every proposed step passed the registry and constraint checks, and no
    escalation was triggered."""

    PARTIALLY_BLOCKED = "PARTIALLY_BLOCKED"
    """At least one step was blocked and at least one survived. The surviving
    steps are in recovery_plan; the rejected ones are in blocked_steps."""

    BLOCKED = "BLOCKED"
    """Every proposed step was blocked. recovery_plan is empty. SENTINEL has no
    safe action to offer for this fault."""

    REQUIRES_HUMAN_REVIEW = "REQUIRES_HUMAN_REVIEW"
    """Nothing was blocked, but a HIGH/BLOCKED-risk step or low confidence
    forces operator review before execution."""


class BlockSeverity(str, Enum):
    """How consequential executing a blocked command anyway would be."""
    CRITICAL = "CRITICAL"   # Could cause loss of vehicle or loss of contact
    HIGH = "HIGH"           # Could cause hardware damage or deepen the fault
    MEDIUM = "MEDIUM"       # Likely ineffective or would worsen margins
    LOW = "LOW"             # Advisory


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class RecoveryStep(BaseModel):
    """A single step in the recovery command sequence.

    Each step maps to a spacecraft command, its rationale, timing,
    verification criteria, and risk level.  The safety validator
    may override the risk to BLOCKED.
    """
    step: int = Field(..., ge=1, description="1-indexed step number")
    command: str = Field(
        ...,
        min_length=3,
        description="Spacecraft command name, e.g. CMD_GYRO_A_DRIVER_RESET",
    )
    rationale: str = Field(
        ...,
        min_length=5,
        description="Why this command is issued at this point in the sequence",
    )
    wait_seconds: int = Field(
        ...,
        ge=0,
        description="Seconds to wait after issuing command before verifying",
    )
    verify: str = Field(
        ...,
        min_length=3,
        description="Condition to check after wait, e.g. 'GYRO_A_RATE returns valid'",
    )
    risk: RiskLevel = Field(
        ...,
        description="Risk level of this step (LOW / MEDIUM / HIGH / BLOCKED)",
    )


class BlockedCommand(BaseModel):
    """A recovery action the safety validator refused, as structured data.

    Added in Phase 1. Previously this information was flattened into a
    ``[SAFETY: ...]`` string appended to ``reasoning_summary``, which meant the
    thing an operator most needs to see — what the AI proposed and why it was
    refused — was the thing the API threw away.

    This is deliberately a separate model from ``safety.BlockedStep``:
    BlockedStep is the validator's internal record and embeds the whole
    RecoveryStep; this is the stable API surface.
    """

    step: Optional[int] = Field(
        default=None,
        description="Position this command held in the model's original plan",
    )
    command: str = Field(
        ...,
        min_length=1,
        description="The command that was refused, exactly as proposed",
    )
    reason: str = Field(
        ...,
        min_length=5,
        description="Operator-facing explanation of why it was refused",
    )
    violated_constraint: str = Field(
        ...,
        min_length=1,
        description=(
            "Machine-readable constraint code, e.g. NOT_IN_REGISTRY, "
            "BATTERY_FLOOR, COMMS_LOCK_REBOOT, THERMAL_SURVIVAL"
        ),
    )
    severity: BlockSeverity = Field(
        ...,
        description="Consequence of executing this command anyway",
    )
    subsystem: Optional[str] = Field(
        default=None,
        description="Subsystem the command or constraint belongs to",
    )
    supporting_context: Dict[str, object] = Field(
        default_factory=dict,
        description=(
            "Observed values the decision was based on, e.g. "
            "{'battery_soc_pct': 12.0, 'floor_pct': 15.0}. Only values actually "
            "read from the crash dump appear here."
        ),
    )


class Hypothesis(BaseModel):
    """A single ranked diagnosis hypothesis.

    The agent MUST always produce exactly 3 hypotheses.
    Even for obvious faults: H1 (high confidence), H2 and H3 (low).
    This enables multi-hypothesis reasoning and graceful degradation
    when the top hypothesis is wrong.
    """
    rank: int = Field(..., ge=1, le=3, description="Rank 1 = most likely")
    root_cause: str = Field(
        ...,
        min_length=3,
        description="Fault class, e.g. ADCS_GYRO_SEU, EPS_SOLAR_UNDERVOLT",
    )
    affected_component: str = Field(
        ...,
        min_length=2,
        description="Affected component, e.g. SOLAR_ARRAY_A, GYRO_A",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in this hypothesis (0.0–1.0)",
    )
    causal_chain: List[str] = Field(
        ...,
        min_length=2,
        description=(
            "Ordered list of events from trigger to safe mode, "
            "e.g. ['I_sa drops to 0A', 'V_bat falls to 24V', 'EPS fault flag set']"
        ),
    )

    @field_validator("confidence")
    @classmethod
    def round_confidence(cls, v: float) -> float:
        """Keep confidence to 2 decimal places for clean display."""
        return round(v, 2)

    @field_validator("causal_chain")
    @classmethod
    def validate_causal_chain(cls, value: List[str]) -> List[str]:
        cleaned = [item.strip() for item in value]

        if any(not item for item in cleaned):
            raise ValueError(
                "causal_chain entries must be non-empty strings"
            )

        return cleaned


class SentinelOutput(BaseModel):
    """Top-level structured output from the SENTINEL agent.

    This is the exact JSON shape that:
      - The LLM must return (via system prompt enforcement + retry)
      - The FastAPI endpoint serializes
      - The React frontend destructures
      - The evaluator compares to ground truth

    Contract invariants:
      1. Exactly 3 hypotheses, ranked 1-2-3
      2. Hypothesis confidences are descending (rank 1 ≥ rank 2 ≥ rank 3)
      3. requires_human_review is True when overall confidence < 0.70
         OR any recovery step has risk HIGH or BLOCKED
      4. recovery_plan steps are numbered sequentially from 1
      5. Overall confidence matches hypothesis rank-1 confidence
      6. recovery_plan may be empty ONLY when safety_status is BLOCKED,
         and a BLOCKED plan must carry at least one blocked_steps entry
    """

    hypotheses: List[Hypothesis] = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Exactly 3 ranked diagnostic hypotheses",
    )
    recovery_plan: List[RecoveryStep] = Field(
        ...,
        min_length=0,
        description=(
            "Ordered recovery command sequence for the top hypothesis. "
            "MAY be empty only when safety_status is BLOCKED — see invariant 6. "
            "Phase 1 relaxed this from min_length=1: previously an all-blocked "
            "plan had to be padded with a fabricated CMD_HEALTH_CHECK step, "
            "which made total rejection look like a clean one-step recovery."
        ),
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Overall confidence (should equal top-hypothesis confidence)",
    )
    requires_human_review: bool = Field(
        ...,
        description=(
            "True when confidence < 0.70 or any step is HIGH/BLOCKED risk"
        ),
    )
    reasoning_summary: str = Field(
        ...,
        min_length=10,
        description="2–4 sentence summary of the diagnostic reasoning chain",
    )
    status: AnalysisStatus = Field(
        default=AnalysisStatus.COMPLETE,
        description="Analysis completion status",
    )
    safety_status: SafetyStatus = Field(
        default=SafetyStatus.NOT_VALIDATED,
        description=(
            "Outcome of deterministic safety validation. Defaults to "
            "NOT_VALIDATED so a raw, unvalidated LLM output can never claim to "
            "have passed safety checks."
        ),
    )
    blocked_steps: List[BlockedCommand] = Field(
        default_factory=list,
        description=(
            "Recovery actions the safety validator refused, as structured data. "
            "Never flattened into reasoning_summary."
        ),
    )

    @field_validator("confidence")
    @classmethod
    def round_confidence(cls, v: float) -> float:
        return round(v, 2)

    @field_validator("reasoning_summary")
    @classmethod
    def validate_reasoning_summary(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError(
                "reasoning_summary must not be blank"
            )

        return value

    @model_validator(mode="after")
    def validate_output_invariants(self) -> "SentinelOutput":
        """Enforce the contract invariants documented above."""

        # --- Invariant 1: ranks must be exactly {1, 2, 3} ---
        ranks = sorted(h.rank for h in self.hypotheses)
        if ranks != [1, 2, 3]:
            raise ValueError(
                f"Hypotheses must have ranks [1, 2, 3], got {ranks}"
            )

        # --- Invariant 2: confidences must be non-increasing by rank ---
        sorted_by_rank = sorted(self.hypotheses, key=lambda h: h.rank)
        for i in range(len(sorted_by_rank) - 1):
            if sorted_by_rank[i].confidence < sorted_by_rank[i + 1].confidence:
                raise ValueError(
                    f"Hypothesis rank {sorted_by_rank[i].rank} confidence "
                    f"({sorted_by_rank[i].confidence}) must be >= rank "
                    f"{sorted_by_rank[i+1].rank} confidence "
                    f"({sorted_by_rank[i+1].confidence})"
                )

        # --- Invariant 3: recovery steps must be sequential ---
        step_numbers = [step.step for step in self.recovery_plan]

        expected_steps = list(
            range(
                1,
                len(self.recovery_plan) + 1
            )
        )

        if step_numbers != expected_steps:
            raise ValueError(
                f"Recovery steps must be sequential "
                f"{expected_steps}, got {step_numbers}"
            )


        # --- Invariant 4: auto-set requires_human_review ---
        # Only auto-ESCALATE to True, never auto-downgrade to False.
        # This ensures safety.py's requires_human_review=True is preserved.
        has_high_risk = any(
            s.risk in (RiskLevel.HIGH, RiskLevel.BLOCKED)
            for s in self.recovery_plan
        )
        should_flag = self.confidence < 0.70 or has_high_risk
        if should_flag and not self.requires_human_review:
            # Auto-correct rather than reject — safer for hackathon reliability
            object.__setattr__(self, "requires_human_review", True)

        # --- Invariant 5: overall confidence matches rank-1 ---
        top_hyp = next(h for h in self.hypotheses if h.rank == 1)
        if abs(self.confidence - top_hyp.confidence) > 0.01:
            # Auto-correct to top hypothesis confidence
            object.__setattr__(self, "confidence", top_hyp.confidence)

        # --- Invariant 6: an empty plan is only legal when BLOCKED ---
        # This is what stops an entirely-unsafe plan from being presented as a
        # successful one. Rejected, not raised-and-swallowed: a caller that
        # empties the plan without setting safety_status=BLOCKED gets an error.
        if not self.recovery_plan:
            if self.safety_status is not SafetyStatus.BLOCKED:
                raise ValueError(
                    "recovery_plan may only be empty when safety_status is "
                    f"BLOCKED, got '{self.safety_status.value}'"
                )
            if not self.blocked_steps:
                raise ValueError(
                    "safety_status is BLOCKED with an empty recovery_plan, so "
                    "blocked_steps must explain what was refused"
                )
        # A BLOCKED plan can never also present executable steps.
        elif self.safety_status is SafetyStatus.BLOCKED:
            raise ValueError(
                "safety_status is BLOCKED but recovery_plan is non-empty "
                f"({len(self.recovery_plan)} step(s)); BLOCKED means no step "
                "survived validation"
            )

        # A BLOCKED or PARTIALLY_BLOCKED plan always needs operator review.
        if self.safety_status in (
            SafetyStatus.BLOCKED,
            SafetyStatus.PARTIALLY_BLOCKED,
            SafetyStatus.REQUIRES_HUMAN_REVIEW,
        ) and not self.requires_human_review:
            object.__setattr__(self, "requires_human_review", True)

        return self


# ---------------------------------------------------------------------------
# System Status & Sovereignty Response (Phase 11)
# ---------------------------------------------------------------------------

class SovereigntyInfo(BaseModel):
    """Factual sovereignty and data privacy indicators.

    Does NOT claim security or compliance certifications (e.g. FedRAMP/HIPAA).
    """
    local_execution: bool = Field(
        ...,
        description="True if LLM reasoning is executed on a local sovereign endpoint",
    )
    cloud_telemetry_disabled: bool = Field(
        ...,
        description="True if cloud telemetry transmission is blocked",
    )
    disclaimer: str = Field(
        default="Factual operational mode indicator. No security or compliance certifications (e.g. FedRAMP/HIPAA) claimed.",
        description="Factual disclaimer regarding compliance claims",
    )


class SystemStatusResponse(BaseModel):
    """Comprehensive system status including local/cloud LLM mode."""
    backend_status: str = Field(default="ok", description="FastAPI service status")
    detector_status: str = Field(default="ok", description="Telemetry detector status")
    physics_model_status: str = Field(default="ok", description="Physics validation model status")
    rag_status: str = Field(default="ok", description="RAG procedure retrieval status")
    llm_mode: str = Field(..., description="LLM operational mode: CLOUD | LOCAL | STUB")
    llm_provider: str = Field(..., description="Active provider: gemini | local | stub")
    model: str = Field(..., description="Active LLM model identifier")
    version: str = Field(default=CONTRACT_VERSION, description="Contract version")
    simulation_live_status: str = Field(default="live", description="Pipeline data source status")
    sovereignty: SovereigntyInfo = Field(..., description="Sovereignty & privacy status")


# ---------------------------------------------------------------------------
# Evaluation Models (Phase 12)
# ---------------------------------------------------------------------------

class EvaluationRunRequest(BaseModel):
    """Request payload to trigger an evaluation run."""
    split: str = Field(default="HELD_OUT_TEST", description="Dataset split: HELD_OUT_TEST or DEV")
    seed: int = Field(default=42, description="Random seed for reproducibility")
    mode: str = Field(default="stub", description="LLM mode: stub | cloud | local")


class EvaluationResultsResponse(BaseModel):
    """Machine-readable evaluation results response."""
    provenance: dict = Field(..., description="Evaluation provenance metadata")
    summary: dict = Field(..., description="Evaluation summary statistics")
    pipelines: dict = Field(..., description="Per-pipeline evaluation metrics")
    charts: dict = Field(..., description="Evaluation charts data derived from real metrics")


# ---------------------------------------------------------------------------
# SSE event wrapper (used by Person 4's streaming endpoint)
# ---------------------------------------------------------------------------

class SSEEventType(str, Enum):
    """Event types streamed over SSE to the frontend."""
    THOUGHT = "thought"         # Agent reasoning step
    ACTION = "action"           # Tool call initiated
    OBSERVATION = "observation"  # Tool call result
    RESULT = "result"           # Final SentinelOutput
    ERROR = "error"             # Error message
    STATUS = "status"           # Progress updates


class SSEEvent(BaseModel):
    """A single SSE event sent from backend to frontend.

    Person 3 uses `event_type` to route data to the correct UI panel:
      - THOUGHT/ACTION/OBSERVATION → Panel 2 (Reasoning Trace)
      - RESULT → Panel 3 (Causal DAG) + Panel 4 (Recovery Plan)
      - ERROR → Error toast
      - STATUS → Header status indicator
    """
    event_type: SSEEventType
    data: str = Field(
        ...,
        description="Payload — plain text for trace events, JSON for RESULT",
    )
    step_number: Optional[int] = Field(
        default=None,
        ge=1,
        description="Agent reasoning step index (for trace events)",
    )
# ---------------------------------------------------------------------------
# INPUT SCHEMAS — Crash Dump Intake Validation
# ---------------------------------------------------------------------------


class TelecommandContext(BaseModel):
    """Behavioral layer: telecommand interval analysis.

    Captures the execution timing pattern of the command correlated with the
    safe-mode entry event.

    Phase 3 made ``gap_seconds`` and ``gap_percentile`` optional. They were
    required, but presets 4, 5 and 6 ship them as null (the interval statistics
    were not computed for those payloads), so ``CrashDumpRequest.model_validate``
    rejected three of the ten scenarios the API serves. A missing statistic is
    represented as absent rather than as a fabricated number.
    """
    event_id: int = Field(..., description="Unique sequence identifier for the telecommand execution log")
    telecommand: str = Field(..., description="System command identifier (e.g. telecommand_63)")
    execution_timestamp: datetime = Field(..., description="ISO 8601 timestamp of command execution")
    gap_seconds: Optional[float] = Field(
        default=None,
        description=(
            "Delta-T in seconds since the previous execution of this command. "
            "None when the interval statistics were not computed."
        ),
    )
    gap_classification: str = Field(..., description="Statistical interval classification (burst, nominal, stale)")
    gap_percentile: Optional[float] = Field(
        default=None,
        description=(
            "Historical percentile rating of this execution delta. None when not "
            "computed."
        ),
    )
    anomaly_flag: bool = Field(..., description="True if the interval crosses baseline bounds")


class TelemetryStatus(str, Enum):
    """Per-reading status label.

    Phase 3 widened this from a bare ``str`` validated against three values to a
    closed enum covering the labels the repository actually uses. The old
    three-value check rejected real data: presets 5 and 6 use WARNING, and the
    ESA-ADB dumps use NOMINAL_CONTEXT and LABELLED_ANOMALY, so
    ``CrashDumpRequest.model_validate`` failed on scenarios 4, 5 and 6.

    ``normalize()`` accepts the aliases found in the existing data so no stored
    payload has to be rewritten.
    """

    NOMINAL = "NOMINAL"
    """Within expected behaviour."""

    WARNING = "WARNING"
    """Degraded but not yet out of limits."""

    ANOMALOUS = "ANOMALOUS"
    """Outside expected behaviour."""

    CRITICAL = "CRITICAL"
    """Outside a limit, or unusable (NaN / dropout)."""

    NOMINAL_CONTEXT = "NOMINAL_CONTEXT"
    """Nominal, included only to give the anomaly temporal context.
    Emitted by data_tools/esa_adb_crash_dump.py."""

    LABELLED_ANOMALY = "LABELLED_ANOMALY"
    """Inside an interval the source dataset labels anomalous. Carries no
    root-cause claim — ESA-ADB provides no root-cause labels."""

    UNKNOWN = "UNKNOWN"
    """Status not supplied. Never treated as NOMINAL."""

    @classmethod
    def normalize(cls, value: object) -> "TelemetryStatus":
        """Coerce an incoming status to a member, accepting known aliases."""
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.UNKNOWN
        text = str(value).strip().upper().replace(" ", "_").replace("-", "_")
        if not text:
            return cls.UNKNOWN
        try:
            return cls(text)
        except ValueError:
            pass
        aliases = {
            "OK": cls.NOMINAL,
            "NOM": cls.NOMINAL,
            "WARN": cls.WARNING,
            "ANOMALY": cls.ANOMALOUS,
            "CRIT": cls.CRITICAL,
            "LABELED_ANOMALY": cls.LABELLED_ANOMALY,
        }
        return aliases.get(text, cls.UNKNOWN)

    @property
    def is_nominal(self) -> bool:
        """True for statuses that assert nothing is wrong."""
        return self in (TelemetryStatus.NOMINAL, TelemetryStatus.NOMINAL_CONTEXT)


class TelemetryEntry(BaseModel):
    """CANONICAL telemetry reading — one channel at one time step.

    Phase 3 made this the single representation of a telemetry sample. It is
    deliberately SELF-DESCRIBING: everything a detector or a safety check needs
    is on the entry, so no consumer has to reach for a parallel legacy array.

    Before Phase 3 the bounds lived only on the legacy ``pre_fault_telemetry``
    dicts while status and timing lived only on the window entries, which is why
    Phase 2 found a scenario reporting zero anomalies when read from the window
    alone. ``app/api/adapters.py`` merges both into this shape.
    """

    # --- identity and timing ---
    timestamp: str = Field(
        ..., description="Relative timeline marker, e.g. 'T-60s'",
    )
    parameter: str = Field(
        ..., description="Telemetry channel designation, e.g. V_bat, channel_41",
    )
    relative_time_s: Optional[float] = Field(
        default=None,
        description="Parsed offset in seconds; negative is before the event",
    )

    # --- value ---
    value: Optional[float] = Field(
        default=None,
        description="Numeric reading. None when unusable — see value_text.",
    )
    value_text: Optional[str] = Field(
        default=None,
        description=(
            "The raw reading when it is not a usable number, e.g. 'NaN' or "
            "'DEGRADED'. Preserved so a NaN stays distinguishable from a "
            "missing sample; the old schema coerced both to None."
        ),
    )
    unit: Optional[str] = Field(default=None, description="Unit of measure")

    # --- classification ---
    status: TelemetryStatus = Field(
        default=TelemetryStatus.UNKNOWN,
        description="Per-reading status. Defaults to UNKNOWN, never to NOMINAL.",
    )
    anomalous: Optional[bool] = Field(
        default=None,
        description=(
            "Source-supplied anomaly flag, when the source provides one. "
            "SENTINEL's own verdict lives in the AnomalyReport, not here."
        ),
    )

    # --- bounds and baseline ---
    nominal_min: Optional[float] = Field(
        default=None, description="Lower bound of the nominal range",
    )
    nominal_max: Optional[float] = Field(
        default=None, description="Upper bound of the nominal range",
    )
    baseline_mean: Optional[float] = Field(
        default=None,
        description="Observed baseline mean, when the source measured one",
    )
    baseline_std: Optional[float] = Field(
        default=None,
        description="Observed baseline standard deviation, when measured",
    )

    @field_validator("status", mode="before")
    @classmethod
    def coerce_status(cls, v: object) -> "TelemetryStatus":
        """Accept the status spellings present in the existing data."""
        return TelemetryStatus.normalize(v)

    @field_validator("value", mode="before")
    @classmethod
    def coerce_value(cls, v: object) -> Optional[float]:
        """Map unusable readings to None rather than rejecting the payload.

        'NaN' arrives as a string in the legacy scenarios. Rejecting it would
        make a real dropout unrepresentable; ``value_text`` keeps the original.
        """
        if v is None or isinstance(v, bool):
            return None if v is None else float(v)
        if isinstance(v, str):
            text = v.strip()
            if not text:
                return None
            try:
                parsed = float(text)
            except ValueError:
                return None
            return None if math.isnan(parsed) or math.isinf(parsed) else parsed
        if isinstance(v, (int, float)):
            f = float(v)
            return None if math.isnan(f) or math.isinf(f) else f
        return None

    @model_validator(mode="after")
    def preserve_unusable_reading(self) -> "TelemetryEntry":
        """Ensure an unusable reading is visible rather than silently absent."""
        if self.value is None and self.value_text is None:
            object.__setattr__(self, "value_text", "MISSING")
        return self

    @property
    def is_usable(self) -> bool:
        return self.value is not None


class CrashDumpRequest(BaseModel):
    """Unified data-intake schema validated by FastAPI before AI processing.

    Design goals:
      1. Strict validation for required fields (fault_type, scenario_id).
      2. Backward-compatible: fields added by the new layered schema
         (incident_id, fault_register, telecommand_context,
         pre_fault_telemetry_window) have sensible defaults so the existing
         frontend LOCAL_PRESET_SCENARIOS still pass validation.
      3. Extra keys (hardware_state, operating_context) are silently
         forwarded via ``extra = "allow"`` — the LLM prompt sees them.
    """

    # --- Core identifiers (always required) ---
    scenario_id: Optional[int] = Field(default=None, description="Scenario identifier used by the demo UI")
    fault_type: Optional[str] = Field(default=None, description="Fault category, e.g. ADCS_GYRO_SEU")

    # --- New structured fields (optional for backward compat) ---
    incident_id: Optional[str] = Field(
        default=None,
        description="Unique alphanumeric identifier for the safe-mode incident case",
    )
    fault_register: Optional[str] = Field(
        default=None,
        description="Hexadecimal bitmask of HW/SW flags that tripped FDIR",
    )
    safe_mode_trigger: Optional[str] = Field(
        default=None,
        description="Trigger string that caused safe-mode entry",
    )
    telecommand_context: Optional[TelecommandContext] = Field(
        default=None,
        description="Behavioral interval log for the correlated command",
    )

    # --- CANONICAL telemetry representation (Phase 3) ---
    pre_fault_telemetry_window: Optional[List[TelemetryEntry]] = Field(
        default=None,
        description=(
            "CANONICAL pre-fault telemetry. All consumers read this field, via "
            "app.api.adapters.canonical_window(). Entries are self-describing: "
            "timing, value, status, bounds and baseline statistics all live on "
            "the entry."
        ),
    )

    # --- DEPRECATED legacy shape, retained for backward compatibility ---
    pre_fault_telemetry: Optional[List[Dict]] = Field(
        default=None,
        deprecated=True,
        description=(
            "DEPRECATED — superseded by pre_fault_telemetry_window. Accepted on "
            "input and merged into the canonical window by "
            "app.api.adapters.canonical_window(), which is the only place the two "
            "shapes are reconciled. Do not read this field directly: it lacks "
            "timing and status, and reading only the window instead cost Phase 2 "
            "a scenario that reported zero anomalies because the bounds lived "
            "here and nowhere else."
        ),
    )
    event_log: Optional[List[Dict]] = Field(
        default=None,
        description="Raw event log entries from the spacecraft",
    )

    class Config:
        extra = "allow"  # forward hardware_state, operating_context, etc.
        json_schema_extra = {
            "example": {
                "scenario_id": 1,
                "fault_type": "ADCS_GYRO_SEU",
                "incident_id": "INC-2026-0036",
                "fault_register": "0x00000008",
                "telecommand_context": {
                    "event_id": 36,
                    "telecommand": "telecommand_63",
                    "execution_timestamp": "2026-06-13T00:15:22Z",
                    "gap_seconds": 90.0,
                    "gap_classification": "burst",
                    "gap_percentile": 16.2,
                    "anomaly_flag": True,
                },
                "pre_fault_telemetry_window": [
                    {"timestamp": "T-120s", "parameter": "V_bat",
                     "value": 31.2, "status": "NOMINAL"},
                    {"timestamp": "T-60s", "parameter": "TCS_HEATER_ZONE_2_TEMP",
                     "value": 68.4, "status": "ANOMALOUS"},
                    {"timestamp": "T-10s", "parameter": "TCS_HEATER_ZONE_2_TEMP",
                     "value": 85.2, "status": "CRITICAL"},
                ],
            }
        }

    @field_validator("fault_type")
    @classmethod
    def strip_fault_type(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("safe_mode_trigger")
    @classmethod
    def strip_trigger(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


# ---------------------------------------------------------------------------
# SCENARIO CATALOGUE — response contract for GET /api/v1/scenarios
# ---------------------------------------------------------------------------

class Scenario(CrashDumpRequest):
    """A catalogue scenario: a crash dump plus its provenance declaration.

    Phase 3. ``GET /scenarios`` previously returned an unmodelled ``list[dict]``,
    so nothing checked that what the API served matched what the frontend
    expected — and the frontend kept its own 188-line copy of the same
    scenarios, which is precisely how two "sources of truth" appear.

    ``provenance`` and ``source_type`` are required here on purpose. A scenario
    the operator cannot attribute has no place in the catalogue, and
    ``app.api.provenance.describe()`` guarantees both fields on every scenario
    ``get_all_scenarios()`` returns — resolving anything unrecognised to UNKNOWN
    rather than to REAL.
    """

    provenance: str = Field(
        ...,
        description=(
            "Machine-readable provenance code: REAL | SYNTHETIC | "
            "SYNTHETIC_FROM_REAL_METADATA | UNKNOWN. See app/api/provenance.py. "
            "Only REAL means the numeric telemetry itself came from a mission "
            "dataset."
        ),
    )
    source_type: str = Field(
        ...,
        description=(
            "Human-readable provenance label, DERIVED from `provenance` so the "
            "two cannot drift. Never author this field by hand."
        ),
    )
    source_note: Optional[str] = Field(
        default=None,
        description="One-sentence provenance disclaimer shown to the operator",
    )


class ScenarioListResponse(BaseModel):
    """Envelope for the scenario catalogue.

    Carries the contract version alongside the data so a client can detect that
    it is talking to a backend it was not generated against, instead of failing
    on a missing field somewhere deep in a render.
    """

    contract_version: str = Field(
        default=CONTRACT_VERSION,
        description="Version of the data contract this payload conforms to",
    )
    api_version: str = Field(
        default=API_VERSION, description="API version serving this payload",
    )
    count: int = Field(..., ge=0, description="Number of scenarios returned")
    scenarios: List[Scenario] = Field(
        ...,
        description=(
            "The complete scenario catalogue. This is the ONLY source of "
            "scenario definitions; clients must not embed their own copies."
        ),
    )

    @model_validator(mode="after")
    def validate_count(self) -> "ScenarioListResponse":
        if self.count != len(self.scenarios):
            object.__setattr__(self, "count", len(self.scenarios))
        return self


class ContractInfo(BaseModel):
    """Response for GET /api/v1/contract — what contract this backend serves.

    Exists so the contract tests, CI, and a running frontend can all ask the
    same question of a live server rather than inferring it.
    """

    contract_version: str = Field(default=CONTRACT_VERSION)
    api_version: str = Field(default=API_VERSION)
    canonical_telemetry_field: str = Field(
        default="pre_fault_telemetry_window",
        description="The canonical telemetry representation",
    )
    deprecated_telemetry_fields: List[str] = Field(
        default_factory=lambda: ["pre_fault_telemetry"],
        description=(
            "Accepted on input and merged into the canonical field by "
            "app.api.adapters.canonical_window(). Never read directly."
        ),
    )
    telemetry_status_values: List[str] = Field(
        default_factory=lambda: [s.value for s in TelemetryStatus],
    )
    safety_status_values: List[str] = Field(
        default_factory=lambda: [s.value for s in SafetyStatus],
    )
    subsystem_values: List[str] = Field(
        default_factory=lambda: [s.value for s in SubsystemID],
    )
