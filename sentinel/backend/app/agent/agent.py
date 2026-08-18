"""
SENTINEL — Reasoning Agent Core (agent.py)

Gemini-first, model-agnostic reasoning agent implementing the full
STEPS 4-7 pipeline:
  1. Accepts crash dump input (dict or JSON string)
  2. Assembles messages via prompts.build_messages()
  3. Calls the LLM (Gemini Flash by default, with tuned and fallback branches)
  4. Parses and validates the response into SentinelOutput
  5. Retries once on malformed output with a repair prompt
  6. Runs deterministic safety validation on recovery steps (Step 7)

Architecture — Three reasoning modes in one agent:
  - "base"    → Gemini Flash (hosted, fast, primary demo path)
  - "tuned"   → Tuned Gemini model or fine-tuned endpoint (more stable
                 repeated fault diagnosis, evaluation comparison)
  - "fallback"→ Local/open model via OpenAI-compatible API
                 (Phi-3-mini, Qwen2.5, Ollama, etc.)

The mode is set via AgentConfig.mode. All three modes share the same
pipeline: build_messages → call_llm → parse_json → validate → safety_check.
Only the LLM call layer changes per mode.

Completed integration points:
  - Step 4: fallback KB retrieval via rag.retrieve_procedures(use_pdf_rag=False)
  - Step 5: structured output schema validation via SentinelOutput (models.py)
  - Step 6: PDF RAG retrieval via rag.retrieve_procedures(use_pdf_rag=True)
  - Step 7: deterministic safety validation via safety.validate_recovery_plan()
  - Retry logic with repair prompt is active

Convenience wrapper:
  - analyze_with_rag(): combines RAG retrieval + analyze_crash_dump() in one call
    Use this from main.py or evaluation scripts instead of calling both separately.

Future integration points:
  - Step 9+: LangGraph tool routing (query_telemetry, check_safety, propose_recovery)
  - Step 11: SSE streaming via analyze_crash_dump_stream()

Imports from our own modules:
  - models.py  → SentinelOutput, AnalysisStatus
  - prompts.py → build_messages
  - rag.py     → retrieve_procedures (lazy import to avoid circular)
  - safety.py  → validate_recovery_plan, apply_validation_to_output (lazy import)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

# Load .env if available (supports sentinel/.env and sentinel/backend/.env)
try:
    from dotenv import load_dotenv
    _AGENT_DIR = Path(__file__).resolve().parent
    for _env_candidate in [
        _AGENT_DIR.parent.parent / ".env",        # sentinel/backend/.env
        _AGENT_DIR.parent.parent.parent / ".env",  # sentinel/.env
    ]:
        if _env_candidate.is_file():
            load_dotenv(_env_candidate, override=False)
            break
except ImportError:
    pass


from app.api.models import AnalysisStatus, SentinelOutput
from app.agent.prompts import build_messages, prompt_identity

logger = logging.getLogger("sentinel.agent")


# ---------------------------------------------------------------------------
# Phase 4 — audit instrumentation helpers
# ---------------------------------------------------------------------------
#
# The recorder is threaded through the pipeline and each stage is written as it
# completes. Nothing here reconstructs a record after the fact, so an entry
# exists only if that stage actually ran.
#
# Every helper is a no-op when recorder is None, which keeps auditing optional
# and keeps the existing call signatures working unchanged.


def _audit_record_input(recorder: Any, crash_dict: dict[str, Any]) -> None:
    """Record the input telemetry, its provenance, and the scenario it is from."""
    from app.audit import Stage, StageStatus
    from app.api.adapters import canonical_window_dicts, coverage_report
    from app.api.provenance import display_label, normalize

    try:
        readings = canonical_window_dicts(crash_dict)
        coverage = coverage_report(crash_dict)
    except Exception as exc:  # pragma: no cover — adapter is in-tree
        recorder.record(
            Stage.INPUT, StageStatus.DEGRADED,
            f"input recorded without canonical telemetry: {exc}",
            {"error": str(exc), "scenario_id": crash_dict.get("scenario_id")},
        )
        return

    # Two provenance facts, kept separate because they can legitimately differ
    # and collapsing them loses information. The payload declares what its
    # NUMBERS are (e.g. SYNTHETIC), while the run may be a DEMO replay of that
    # payload. Recording only one would either hide that the run was rehearsed or
    # hide that the underlying telemetry was generated.
    declared = normalize(crash_dict.get("provenance"))
    run_provenance = recorder.header.provenance
    recorder.record(
        Stage.INPUT,
        StageStatus.OK,
        (
            f"{len(readings)} canonical reading(s) across "
            f"{len(coverage['canonical_channels'])} channel(s); "
            f"run provenance {run_provenance}"
            + (f", payload declares {declared}"
               if declared != run_provenance else "")
        ),
        {
            "scenario_id": crash_dict.get("scenario_id"),
            "incident_id": crash_dict.get("incident_id"),
            "fault_type": crash_dict.get("fault_type"),
            "safe_mode_trigger": crash_dict.get("safe_mode_trigger"),
            "fault_register": crash_dict.get("fault_register"),
            "run_provenance": run_provenance,
            "run_source_type": display_label(run_provenance),
            "declared_provenance": declared,
            "declared_source_type": display_label(declared),
            "provenance_differs": declared != run_provenance,
            "source_note": crash_dict.get("source_note"),
            # The readings themselves, so the run can be replayed from the record
            # alone rather than depending on the catalogue still holding this
            # scenario in the same form.
            "telemetry": readings,
            "telemetry_coverage": coverage,
            "canonical_field": "pre_fault_telemetry_window",
            "telecommand_context": crash_dict.get("telecommand_context"),
            "hardware_state": crash_dict.get("hardware_state"),
            "operating_context": crash_dict.get("operating_context"),
            "event_log": crash_dict.get("event_log"),
        },
    )


def _audit_record_detection(recorder: Any, report: Any, duration_ms: float | None,
                            error: str | None = None) -> None:
    """Record the deterministic detection result, or that it did not run."""
    from app.audit import Stage, StageStatus

    if report is None:
        recorder.record(
            Stage.DETECTION,
            StageStatus.FAILED if error else StageStatus.NOT_RUN,
            f"detection unavailable: {error}" if error
            else "detection did not run for this analysis",
            {"error": error, "claim": "No anomaly claim is made for this run."},
            duration_ms=duration_ms if error else None,
        )
        return

    recorder.record(
        Stage.DETECTION,
        StageStatus.OK,
        (
            f"{report.anomaly_count} anomaly(ies) on "
            f"{report.anomalous_channels}/{report.total_channels} channel(s), "
            f"max severity {report.max_severity.value}"
        ),
        report.model_dump(mode="json"),
        duration_ms=duration_ms,
    )


def _audit_record_rag(recorder: Any, snippets: list[str] | None,
                      trace: dict[str, Any] | None, duration_ms: float | None,
                      error: str | None = None) -> None:
    """Record retrieval results AND the sources they came from."""
    from app.audit import Stage, StageStatus
    from app.audit.record import sha256_hex

    if error is not None:
        recorder.record(
            Stage.RAG, StageStatus.FAILED,
            f"procedure retrieval failed: {error}",
            {"error": error,
             "claim": "The LLM received no retrieved procedure context."},
            duration_ms=duration_ms,
        )
        return

    snippets = snippets or []
    if trace is None:
        # Procedures were handed in by the caller — an evaluation harness, or a
        # replay. Retrieval did not happen during this run, and the record must
        # not imply that it did.
        recorder.record(
            Stage.RAG, StageStatus.DEGRADED,
            f"{len(snippets)} procedure(s) supplied by the caller; "
            f"no retrieval performed in this run",
            {
                "backend": "caller_supplied",
                "sources": [],
                "sources_available": False,
                "snippet_count": len(snippets),
                "snippet_hashes": [sha256_hex(s)[:16] for s in snippets],
                "claim": (
                    "Source attribution is unavailable because this run did not "
                    "perform retrieval."
                ),
            },
            duration_ms=duration_ms,
        )
        return

    recorder.record(
        Stage.RAG, StageStatus.OK,
        (
            f"{trace.get('snippet_count', len(snippets))} procedure(s) from "
            f"{trace.get('backend', 'unknown')}"
        ),
        {**trace, "sources_available": True,
         "snippet_hashes": [sha256_hex(s)[:16] for s in snippets]},
        duration_ms=duration_ms,
    )


def _audit_record_llm(
    recorder: Any,
    config: Any,
    messages: list[dict[str, str]],
    raw_responses: list[str],
    attempts: int,
    duration_ms: float,
    system_prompt_override: str | None,
    error: str | None = None,
) -> None:
    """Record provider, model, mode, prompt identity and the raw output.

    The full system prompt is NOT stored. It is identified by version plus a
    content fingerprint, and every message is stored with its SHA-256, so a
    reconstruction can be verified byte-for-byte against the record. Storing
    ~16 KB of unchanging prompt text on every run would bloat the store without
    adding anything the fingerprint does not already pin down.

    The raw LLM output IS stored, because it cannot be reconstructed.
    """
    from app.audit import Stage, StageStatus
    from app.audit.record import llm_identity, sha256_hex, truncate_text

    payload: dict[str, Any] = {
        **llm_identity(config),
        **prompt_identity(system_prompt_override),
        "attempts": attempts,
        "messages": [
            {
                "role": m.get("role"),
                "chars": len(m.get("content", "")),
                "sha256": sha256_hex(m.get("content", "")),
            }
            for m in messages
        ],
        "raw_responses": [truncate_text(r) for r in raw_responses],
        "response_count": len(raw_responses),
        "prompt_text_stored": False,
        "reproducibility_note": (
            "The system prompt is pinned by prompt_version and "
            "prompt_fingerprint; message SHA-256 values allow a reconstruction "
            "to be verified. Raw responses are stored verbatim."
        ),
    }

    if error is not None:
        payload["error"] = error
        recorder.record(
            Stage.LLM, StageStatus.FAILED,
            f"LLM stage failed after {attempts} attempt(s): {error}",
            payload, duration_ms=duration_ms,
        )
        return

    recorder.record(
        Stage.LLM, StageStatus.OK,
        (
            f"{payload['provider']} / {payload['model']} "
            f"(mode={payload['mode']}, prompt={payload['prompt_version']}"
            f"@{payload['prompt_fingerprint']}, {attempts} attempt(s))"
        ),
        payload, duration_ms=duration_ms,
    )


def _audit_record_hypotheses(recorder: Any, result: SentinelOutput) -> None:
    """Record the ranked hypotheses, labelled as model output."""
    from app.audit import Stage, StageStatus

    recorder.record(
        Stage.HYPOTHESES, StageStatus.OK,
        (
            f"{len(result.hypotheses)} hypothesis(es), top "
            f"'{result.hypotheses[0].root_cause}' at "
            f"{result.hypotheses[0].confidence:.2f} confidence"
        ),
        {
            "generated_by": "LLM",
            "is_validated_diagnosis": False,
            "claim": (
                "Ranked hypotheses produced by the language model. Confidence "
                "is the model's own estimate; it is not a calibrated "
                "probability and no physical consistency check has been applied."
            ),
            "hypotheses": [h.model_dump(mode="json") for h in result.hypotheses],
        },
    )


def _audit_record_safety(recorder: Any, validation: Any,
                         duration_ms: float | None,
                         skipped: bool = False) -> None:
    """Record the deterministic safety verdict, including what was refused."""
    from app.audit import Stage, StageStatus

    if skipped:
        recorder.record(
            Stage.SAFETY_VALIDATION, StageStatus.SKIPPED,
            "safety validation deliberately bypassed (ablation mode)",
            {
                "skip_safety": True,
                "safety_status": "NOT_VALIDATED",
                "claim": (
                    "No safety claim is made for this plan. The recovery steps "
                    "below were NOT checked against the command registry or the "
                    "operating constraints."
                ),
            },
        )
        return

    recorder.record(
        Stage.SAFETY_VALIDATION, StageStatus.OK,
        (
            f"{validation.safety_status.value}: "
            f"{len(validation.validated_steps)} approved, "
            f"{len(validation.blocked_steps)} blocked"
        ),
        {
            "safety_status": validation.safety_status.value,
            "is_safe": validation.is_safe,
            "all_blocked": validation.all_blocked,
            "requires_human_review": validation.requires_human_review,
            "safety_summary": validation.safety_summary,
            "approved_commands": [s.command for s in validation.validated_steps],
            "blocked_steps": [
                b.model_dump(mode="json") for b in validation.blocked_for_api()
            ],
            "validator": "app.agent.safety.validate_recovery_plan",
            "registry": "app.validation.command_registry",
        },
        duration_ms=duration_ms,
    )


def _audit_record_diagnosis(recorder: Any, result: SentinelOutput,
                            total_ms: float) -> None:
    """Record the final output and the recommended actions."""
    from app.audit import Stage, StageStatus

    recorder.record(
        Stage.DIAGNOSIS, StageStatus.OK,
        (
            f"status={result.status.value}, safety={result.safety_status.value}, "
            f"{len(result.recovery_plan)} recommended action(s), "
            f"review_required={result.requires_human_review}"
        ),
        {
            "sentinel_output": result.model_dump(mode="json"),
            "recommended_actions": [
                s.model_dump(mode="json") for s in result.recovery_plan
            ],
            "action_count": len(result.recovery_plan),
            "requires_human_review": result.requires_human_review,
            "authority": (
                "Recommendation only. No command is dispatched by SENTINEL; "
                "execution requires an operator decision, which is recorded "
                "separately as an operator_decision entry."
            ),
            "pipeline_duration_ms": total_ms,
        },
    )


# Each stage is recorded at the point in the pipeline where it runs, so the entry
# sequence reads as the architecture: detection → state estimation → hypotheses →
# physics validation → safety validation. Writing them together would put physics
# validation before the hypotheses it is supposed to check, and the log would
# misrepresent the intended order.


def _audit_record_state_estimation(recorder: Any, crash_dump: Any) -> None:
    """Record the Phase 7 state estimate and its residuals.

    Replaces the NOT_IMPLEMENTED placeholder that stood here from Phase 4, whose
    stated reason was that no state estimator or dynamics model existed. One now
    does, so the entry carries a result.

    The stage is deterministic and consults no language model. It runs BEFORE the
    LLM in the pipeline, which is the point: the residuals are evidence the model
    is given, not something it produces.

    A failure here is recorded and swallowed. Physical consistency checking is
    corroboration, and an investigation that dies because a simplified model
    raised would be worse than one that proceeds with the stage marked FAILED.
    """
    from app.audit import Stage, StageStatus

    if recorder.has(Stage.STATE_ESTIMATION):
        return

    started = time.perf_counter()
    try:
        from app.estimation import compute_residuals, estimate_states

        dump = crash_dump if isinstance(crash_dump, dict) else None
        sequence = estimate_states(dump)
        report = compute_residuals(dump, sequence)
    except Exception as exc:  # pragma: no cover — estimation is in-tree
        logger.warning("State estimation error (non-fatal): %s", exc)
        recorder.record(
            Stage.STATE_ESTIMATION, StageStatus.FAILED,
            f"state estimation raised {type(exc).__name__}",
            {
                "error": f"{type(exc).__name__}: {exc}",
                "estimator": "app.estimation.residuals.compute_residuals",
                "claim": (
                    "No physical consistency claim is made for this run. The "
                    "stage was attempted and raised."
                ),
            },
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
        return

    decided = [r for r in report.residuals if r.status.is_decided]

    # DEGRADED rather than OK when nothing could be decided. The stage ran, so
    # NOT_RUN would be wrong, and OK would imply a check that in fact produced no
    # verdict on anything.
    status = StageStatus.OK if decided else StageStatus.DEGRADED

    final_state = (
        sequence.timed_states[-1].as_dict() if sequence.timed_states else None
    )

    recorder.record(
        Stage.STATE_ESTIMATION, status,
        report.summary,
        {
            "residual_report": report.as_dict(),
            "state_estimate": {
                "state_count": len(sequence),
                "timed_state_count": len(sequence.timed_states),
                "channels_seen": list(sequence.channels_seen),
                "channels_modelled": list(sequence.channels_modelled),
                "channels_ignored": list(sequence.channels_ignored),
                "final_state": final_state,
                "full_sequence_stored": False,
                "full_sequence_omitted_because": (
                    "The complete snapshot sequence runs to roughly 65 kB per "
                    "run. The final state is the one the vehicle reached and is "
                    "what the residuals were computed against; state_count "
                    "records how many snapshots existed."
                ),
            },
            "estimator": "app.estimation.residuals.compute_residuals",
            "parameters": "app.estimation.parameters",
            "pipeline": (
                "telemetry -> state estimate -> model prediction -> residuals"
            ),
            "runs_before_llm": True,
            "uses_llm": False,
            "flight_qualified": False,
            "claim": (
                "Simplified research-grade consistency checking. NOT flight "
                "software and NOT a model of any specific spacecraft. A residual "
                "shows disagreement with the assumptions recorded in "
                "residual_report.assumed_parameters, not with the vehicle. An "
                "UNDECIDABLE residual is not a passing check."
            ),
        },
        duration_ms=(time.perf_counter() - started) * 1000.0,
    )


def _audit_record_physics_validation(recorder: Any, crash_dump: Any) -> None:
    """Record the Phase 8 physics verdict for every candidate hypothesis.

    Replaces the NOT_IMPLEMENTED placeholder that stood here from Phase 4. Phase 7
    made physical consistency MEASURED; this makes it ENFORCED, in the sense that
    a hypothesis the models contradict is recorded as INVALID and demoted.

    The verdicts are computed from the DETERMINISTIC Phase 6 candidate set, not
    from whatever the LLM proposes. That ordering is the point: the audit record
    carries an independent physical assessment that exists whether or not the
    model agrees with it, and ``reconcile_llm_claim()`` in
    ``app/validation/physics.py`` has no branch that lets a model change one.

    A failure here is recorded and swallowed, for the same reason as state
    estimation: an investigation that dies because a simplified model raised
    would be worse than one that proceeds with the stage marked FAILED.
    """
    from app.audit import Stage, StageStatus

    if recorder.has(Stage.PHYSICS_VALIDATION):
        return

    started = time.perf_counter()
    try:
        from app.validation.physics import validate_crash_dump

        dump = crash_dump if isinstance(crash_dump, dict) else {}
        report, hypotheses, _residuals, _sequence = validate_crash_dump(dump)
    except Exception as exc:  # pragma: no cover — validation is in-tree
        logger.warning("Physics validation error (non-fatal): %s", exc)
        recorder.record(
            Stage.PHYSICS_VALIDATION, StageStatus.FAILED,
            f"physics validation raised {type(exc).__name__}",
            {
                "error": f"{type(exc).__name__}: {exc}",
                "validator": "app.validation.physics.validate_crash_dump",
                "claim": (
                    "No physical validity claim is made for this run. The stage "
                    "was attempted and raised."
                ),
            },
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
        return

    # DEGRADED when nothing could be decided either way. The stage ran, so
    # NOT_RUN is wrong, and OK would imply verdicts that were in fact all
    # UNCERTAIN.
    decided = report.invalidated or report.validated
    status = StageStatus.OK if decided else StageStatus.DEGRADED

    recorder.record(
        Stage.PHYSICS_VALIDATION, status,
        report.summary,
        {
            "physics_report": report.model_dump(mode="json"),
            "hypothesis_source": "app.diagnosis.generate_hypotheses",
            "hypotheses_considered": [
                h.fault_id for h in getattr(hypotheses, "hypotheses", [])
            ],
            "validator": "app.validation.physics.validate_hypotheses",
            "constraints": "app.validation.physics.CONSTRAINTS",
            "uses_llm": False,
            "llm_can_override": False,
            "runs_on_deterministic_candidates": True,
            "flight_qualified": False,
            "claim": (
                "Deterministic physics validation of the DETERMINISTIC Phase 6 "
                "candidate set. No language model was consulted and none can "
                "change a verdict. An INVALID verdict shows inconsistency with "
                "the simplified Phase 7 models and the assumptions recorded in "
                "physics_report.assumed_parameters, which is grounds to downgrade "
                "a hypothesis rather than proof about hardware. UNCERTAIN is not "
                "a pass."
            ),
        },
        duration_ms=(time.perf_counter() - started) * 1000.0,
    )


# ---------------------------------------------------------------------------
# Model mode enum
# ---------------------------------------------------------------------------

class ModelMode(str, Enum):
    """Selectable reasoning mode for the agent.

    Phase 11: Explicit CLOUD and LOCAL (sovereign) modes.
    """
    CLOUD = "cloud"        # Gemini Flash or tuned cloud model
    LOCAL = "local"        # Local/sovereign model via OpenAI-compatible endpoint
    BASE = "base"          # Alias for CLOUD (backward compatibility)
    TUNED = "tuned"        # Tuned Gemini model
    FALLBACK = "fallback"  # Alias for LOCAL (backward compatibility)
    STUB = "stub"          # No inference at all — test stub

    @property
    def is_local(self) -> bool:
        """True if running in local sovereign mode."""
        return self in (ModelMode.LOCAL, ModelMode.FALLBACK)

    @property
    def is_cloud(self) -> bool:
        """True if running in cloud hosted mode."""
        return self in (ModelMode.CLOUD, ModelMode.BASE, ModelMode.TUNED)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _resolve_env_mode() -> ModelMode:
    """Resolve ModelMode from LLM_MODE environment variable."""
    raw = os.environ.get("LLM_MODE", "").lower().strip()
    if raw == "cloud":
        return ModelMode.CLOUD
    elif raw in ("local", "fallback"):
        return ModelMode.LOCAL
    elif raw == "stub":
        return ModelMode.STUB
    elif raw == "tuned":
        return ModelMode.TUNED
    elif raw == "base":
        return ModelMode.BASE
    return ModelMode.BASE


@dataclass(frozen=True)
class AgentConfig:
    """Centralized agent configuration.

    All LLM parameters in one place. Phase 11 adds environment variable
    resolution for LLM_MODE, LLM_BASE_URL, and LLM_MODEL.
    """
    mode: ModelMode = field(default_factory=_resolve_env_mode)

    # --- Cloud / Gemini ---
    model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "gemini-2.5-flash"))
    tuned_model_id: str = ""
    gemini_api_key: str | None = None

    # --- Local / Sovereign (OpenAI-compatible) ---
    fallback_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "phi-3-mini"))
    fallback_base_url: str = field(default_factory=lambda: os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1"))
    fallback_api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", "local"))

    # --- Stub (no inference; tests and the worked example) ---
    stub_response: str = ""
    """Response returned verbatim in STUB mode. No model is called."""
    stub_label: str = ""
    """Short name for the stub, recorded in the audit trail as the 'model'."""

    # --- Shared LLM parameters ---
    temperature: float = 0.1              # Low for deterministic JSON output
    max_tokens: int = 4096                # Enough for 3 hypotheses + recovery
    timeout_seconds: float = 90.0         # Hard timeout per Master Planner
    max_retries: int = 1                  # Retry once on malformed output

    def get_gemini_api_key(self) -> str:
        """Resolve Gemini API key from config or environment."""
        key = self.gemini_api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise AgentError(
                "No Gemini API key found. Set GEMINI_API_KEY in .env or "
                "pass gemini_api_key to AgentConfig."
            )
        return key

    @property
    def active_model_name(self) -> str:
        """Return the model name currently in use, for logging."""
        if self.mode == ModelMode.TUNED and self.tuned_model_id:
            return self.tuned_model_id
        if self.mode.is_local:
            return self.fallback_model
        if self.mode == ModelMode.STUB:
            return f"stub:{self.stub_label or 'inline'}"
        return self.model


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AgentError(Exception):
    """Base exception for SENTINEL agent errors."""
    pass


class LLMCallError(AgentError):
    """Raised when the LLM API call fails (network, auth, rate limit)."""
    pass


class OutputParsingError(AgentError):
    """Raised when the LLM output cannot be parsed as valid JSON."""

    def __init__(self, message: str, raw_output: str = ""):
        super().__init__(message)
        self.raw_output = raw_output


class OutputValidationError(AgentError):
    """Raised when parsed JSON fails SentinelOutput Pydantic validation."""

    def __init__(self, message: str, parsed_data: dict | None = None):
        super().__init__(message)
        self.parsed_data = parsed_data


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_json_from_response(raw: str) -> dict[str, Any]:
    """Extract a JSON object from raw LLM text.

    Handles common LLM quirks:
      1. Clean JSON (ideal case)
      2. Gemini thinking-model <think>...</think> wrapper (gemini-2.5-flash)
      3. JSON wrapped in ```json ... ``` code fences
      4. JSON with leading/trailing prose
    """
    _logger = logging.getLogger("sentinel.agent.extract")
    text = raw.strip()

    _logger.debug("Raw LLM response (first 500 chars): %s", text[:500])

    # Attempt 0: strip <think>...</think> blocks (gemini-2.5-flash thinking model)
    think_stripped = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if think_stripped != text:
        _logger.debug("Stripped <think> block; remaining length=%d", len(think_stripped))
        text = think_stripped

    # Attempt 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Attempt 2: strip markdown code fences
    fence_pattern = re.compile(
        r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL
    )
    match = fence_pattern.search(text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Attempt 3: find the outermost { ... } block
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            pass

    _logger.error("Full unparseable LLM response:\n%s", text)
    raise OutputParsingError(
        f"Could not extract valid JSON from LLM response "
        f"(length={len(text)} chars)",
        raw_output=text,
    )


def _validate_output(parsed: dict[str, Any]) -> SentinelOutput:
    """Validate parsed JSON against the SentinelOutput Pydantic model.

    Raises OutputValidationError with a human-readable message if
    validation fails.
    """
    try:
        return SentinelOutput.model_validate(parsed)
    except Exception as e:
        raise OutputValidationError(
            f"SentinelOutput validation failed: {e}",
            parsed_data=parsed,
        )


def _format_safety_status(result: SentinelOutput) -> str:
    """Render the pipeline's closing status line from the validation outcome.

    Phase 1. Replaces the hardcoded "Analysis complete. Safety validation
    passed." which was emitted regardless of how many commands were blocked.
    """
    from app.api.models import SafetyStatus

    status = result.safety_status
    blocked = len(result.blocked_steps)

    if status is SafetyStatus.BLOCKED:
        return (
            f"Analysis complete. SAFETY STATUS: BLOCKED — all {blocked} "
            f"proposed recovery step(s) were rejected. No recovery plan is "
            f"offered. Operator review required."
        )
    if status is SafetyStatus.PARTIALLY_BLOCKED:
        return (
            f"Analysis complete. SAFETY STATUS: PARTIALLY_BLOCKED — "
            f"{blocked} step(s) rejected, {len(result.recovery_plan)} "
            f"approved. Operator review required."
        )
    if status is SafetyStatus.REQUIRES_HUMAN_REVIEW:
        return (
            "Analysis complete. SAFETY STATUS: REQUIRES_HUMAN_REVIEW — no step "
            "was blocked, but the plan needs operator authorisation."
        )
    if status is SafetyStatus.VALIDATED:
        return (
            f"Analysis complete. SAFETY STATUS: VALIDATED — all "
            f"{len(result.recovery_plan)} step(s) passed the registry and "
            f"constraint checks."
        )
    return (
        "Analysis complete. SAFETY STATUS: NOT_VALIDATED — deterministic "
        "safety validation did not run, so no safety claim is made."
    )


_REPAIR_PROMPT = (
    "Your previous response was not valid JSON or did not match the "
    "required schema. The specific error was:\n\n{error}\n\n"
    "Please output ONLY a corrected JSON object following the exact schema "
    "from your system prompt. Do not include any text outside the JSON. "
    "Remember:\n"
    "- Exactly 3 hypotheses with ranks 1, 2, 3\n"
    "- Rank 1 confidence >= Rank 2 >= Rank 3\n"
    "- recovery_plan steps numbered sequentially from 1\n"
    "- Each hypothesis needs 'affected_component' (not 'component')\n"
    "- Each recovery step needs 'rationale' and 'wait_seconds'\n"
    "- risk must be one of: LOW, MEDIUM, HIGH\n"
    "- Output ONLY the JSON object, nothing else."
)


# ---------------------------------------------------------------------------
# Agent state (lightweight, for future extensibility)
# ---------------------------------------------------------------------------

@dataclass
class AgentState:
    """Internal state for a single analysis run.

    Tracks the progression through the pipeline so that future
    extensions (SSE streaming, tool routing, timing) can read state
    without changing the public API.
    """
    crash_dump: dict[str, Any] = field(default_factory=dict)
    anomalous_parameters: list[str] = field(default_factory=list)
    retrieved_procedures: list[str] = field(default_factory=list)
    llm_calls_made: int = 0
    start_time: float = 0.0
    elapsed_seconds: float = 0.0
    raw_llm_responses: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    status: AnalysisStatus = AnalysisStatus.COMPLETE
    model_mode_used: str = ""

    # --- Future hook-point markers ---
    # These will be populated by tool nodes in later steps:
    # rag_context: list[str]        → Step 6 (rag.py)
    # safety_overrides: list[dict]  → Step 7 (safety.py)
    # streaming_events: list[dict]  → Step 11 (SSE)


# ---------------------------------------------------------------------------
# Core agent
# ---------------------------------------------------------------------------

class SentinelAgent:
    """SENTINEL reasoning agent — Gemini-first, model-agnostic.

    Supports three reasoning modes in one agent:
      - base:     Gemini Flash (primary demo path)
      - tuned:    Tuned Gemini model (more stable for repeated faults)
      - fallback: Local/open model via Ollama/vLLM (offline backup)

    Usage:
        agent = SentinelAgent()  # uses Gemini Flash by default
        result = agent.analyze_crash_dump(crash_dump_dict)
        print(result.model_dump_json(indent=2))

    Tuned model usage:
        config = AgentConfig(mode=ModelMode.TUNED,
                             tuned_model_id="tunedModels/sentinel-v1")
        agent = SentinelAgent(config)

    Fallback (local Phi-3-mini via Ollama):
        config = AgentConfig(mode=ModelMode.FALLBACK)
        agent = SentinelAgent(config)

    For ablation studies (Person 1):
        result = agent.analyze_crash_dump(
            crash_dump_dict,
            system_prompt_override="You are a helpful assistant...",
        )
    """

    def __init__(self, config: AgentConfig | None = None):
        self.config = config or AgentConfig()
        self._gemini_client = None   # Lazy-initialized Gemini client
        self._fallback_client = None  # Lazy-initialized fallback client

    @property
    def gemini_client(self):
        """Lazy-init the Gemini client so import doesn't require API key."""
        if self._gemini_client is None:
            try:
                from google import genai
            except ImportError:
                raise AgentError(
                    "google-genai package not installed. "
                    "Run: pip install google-genai"
                )
            self._gemini_client = genai.Client(
                api_key=self.config.get_gemini_api_key(),
            )
        return self._gemini_client

    @property
    def fallback_client(self):
        """Lazy-init an OpenAI-compatible client for local/open models.

        Works with Ollama, vLLM, LM Studio, or any server that exposes
        an OpenAI-compatible /v1/chat/completions endpoint.
        """
        if self._fallback_client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise AgentError(
                    "openai package not installed (needed for fallback mode). "
                    "Run: pip install openai"
                )
            self._fallback_client = OpenAI(
                base_url=self.config.fallback_base_url,
                api_key=self.config.fallback_api_key,
                timeout=self.config.timeout_seconds,
            )
        return self._fallback_client

    def analyze_crash_dump(
        self,
        crash_dump: dict[str, Any] | str,
        anomalous_parameters: list[str] | None = None,
        retrieved_procedures: list[str] | None = None,
        system_prompt_override: str | None = None,
        skip_safety: bool = False,
        recorder: Any = None,
    ) -> SentinelOutput:
        """Run the SENTINEL diagnostic pipeline on a crash dump.

        This is the single public entry point. Everything else is internal.

        Args:
            crash_dump: Crash dump as a dict or JSON string.
                Must match Person 1's schema (Strategy v2 Part 7.2).
            anomalous_parameters: Optional list of parameter names flagged
                by the z-score anomaly detector.
            retrieved_procedures: Optional list of ECSS procedure snippets
                from RAG. Will be populated by rag.py.
            system_prompt_override: Optional system prompt replacement.
                Used by Person 1's evaluator for ablation configs.
            skip_safety: If True, bypass deterministic safety validation
                (Step 7). Only used for ablation studies; never set True
                on the default demo path.
            recorder: Optional ``app.audit.AuditRecorder`` (Phase 4). When
                supplied, each stage is recorded as it completes — including the
                stages this build does not implement, and including a FAILED
                entry if the run raises. When None, nothing is recorded and
                behaviour is exactly as before.

        Returns:
            SentinelOutput — validated structured diagnostic output.

        Raises:
            AgentError: Base class for all agent errors.
            LLMCallError: LLM API call failed after retries.
            OutputParsingError: LLM output is not valid JSON after retries.
            OutputValidationError: Parsed JSON fails schema validation
                after retries.
        """
        state = AgentState(
            start_time=time.time(),
            model_mode_used=self.config.mode.value,
        )

        # --- Normalize crash dump to dict and JSON string ---
        if isinstance(crash_dump, str):
            try:
                state.crash_dump = json.loads(crash_dump)
            except json.JSONDecodeError as e:
                raise AgentError(f"Invalid crash dump JSON string: {e}")
            crash_dump_json = crash_dump
        else:
            state.crash_dump = crash_dump
            crash_dump_json = json.dumps(crash_dump, indent=2)

        state.anomalous_parameters = anomalous_parameters or []
        state.retrieved_procedures = retrieved_procedures or []

        # --- Phase 4: audit prologue -------------------------------------
        # Recorded here rather than only in the streaming entry point, so a run
        # started from an evaluation script or a test gets the same complete
        # coverage map as one started from the API. Each helper skips a stage a
        # caller already recorded, so nothing is written twice.
        if recorder is not None:
            from app.audit import Stage

            if not recorder.has(Stage.INPUT):
                _audit_record_input(recorder, state.crash_dump)
            if not recorder.has(Stage.DETECTION):
                _audit_record_detection(recorder, None, None)
            _audit_record_state_estimation(recorder, state.crash_dump)
            if not recorder.has(Stage.RAG):
                _audit_record_rag(
                    recorder, retrieved_procedures, None, None,
                )

        # --- Build messages ---
        messages = build_messages(
            crash_dump_json=crash_dump_json,
            anomalous_parameters=anomalous_parameters,
            retrieved_procedures=retrieved_procedures,
            system_prompt_override=system_prompt_override,
        )
        audited_messages = list(messages)

        # --- Call LLM + parse + validate (with retry) ---
        last_error: Exception | None = None
        attempts = 1 + self.config.max_retries  # 1 initial + N retries
        llm_elapsed_ms = 0.0

        for attempt in range(attempts):
            try:
                # Call LLM (provider-aware, mode-aware)
                _llm_started = time.perf_counter()
                raw_response = self._call_llm(messages)
                llm_elapsed_ms += (time.perf_counter() - _llm_started) * 1000.0
                state.raw_llm_responses.append(raw_response)
                state.llm_calls_made += 1

                # Parse JSON
                parsed = _extract_json_from_response(raw_response)

                # Validate against SentinelOutput
                result = _validate_output(parsed)

                # --- Step 7: Safety validation ---
                # Deterministic whitelist + constraint checks on recovery plan.
                # Skipped only when skip_safety=True (ablation studies).
                # Lazy import to avoid circular dependency.
                # Phase 4: record the LLM stage and the raw hypotheses BEFORE
                # safety validation runs, so the record shows what the model
                # proposed independently of what survived validation. Recording
                # only the post-validation output would hide the cases this
                # architecture exists to expose — an unsafe command the model
                # asked for and the validator refused.
                if recorder is not None:
                    _audit_record_llm(
                        recorder, self.config, audited_messages,
                        state.raw_llm_responses, attempt + 1, llm_elapsed_ms,
                        system_prompt_override,
                    )
                    _audit_record_hypotheses(recorder, result)
                    # Physics validation runs HERE, between the model's
                    # hypotheses and the command-safety check — the point in the
                    # architecture where a physically impossible diagnosis should
                    # be caught before any command is proposed from it. It
                    # validates the DETERMINISTIC Phase 6 candidate set rather
                    # than the model's output, so the record carries an
                    # independent assessment the model cannot have shaped.
                    _audit_record_physics_validation(recorder, state.crash_dump)

                if not skip_safety:
                    from app.agent.safety import validate_recovery_plan, apply_validation_to_output

                    _safety_started = time.perf_counter()
                    validation = validate_recovery_plan(result, state.crash_dump)
                    result = apply_validation_to_output(result, validation)
                    _safety_ms = (time.perf_counter() - _safety_started) * 1000.0

                    if recorder is not None:
                        _audit_record_safety(recorder, validation, _safety_ms)

                    if validation.blocked_steps:
                        logger.info(
                            "Safety: %d step(s) blocked, %d approved, status=%s. %s",
                            len(validation.blocked_steps),
                            len(validation.validated_steps),
                            validation.safety_status.value,
                            validation.safety_summary,
                        )
                    if validation.all_blocked:
                        logger.warning(
                            "Safety: ALL %d proposed step(s) blocked — returning "
                            "safety_status=BLOCKED with an empty recovery plan.",
                            len(validation.blocked_steps),
                        )
                else:
                    # Phase 1: an unvalidated plan must not inherit a status that
                    # implies it passed. SentinelOutput defaults safety_status to
                    # NOT_VALIDATED; make that explicit here.
                    from app.api.models import SafetyStatus

                    result = result.model_copy(
                        update={"safety_status": SafetyStatus.NOT_VALIDATED}
                    )
                    logger.info("Safety validation SKIPPED (ablation mode).")
                    if recorder is not None:
                        _audit_record_safety(recorder, None, None, skipped=True)

                # Success — record timing and return
                state.elapsed_seconds = time.time() - state.start_time

                if recorder is not None:
                    _audit_record_diagnosis(
                        recorder, result, state.elapsed_seconds * 1000.0,
                    )

                logger.info(
                    "Analysis complete in %.1fs (%d LLM call(s), mode=%s, "
                    "model=%s). Confidence: %.2f, requires_human_review: %s",
                    state.elapsed_seconds,
                    state.llm_calls_made,
                    self.config.mode.value,
                    self.config.active_model_name,
                    result.confidence,
                    result.requires_human_review,
                )
                return result

            except (OutputParsingError, OutputValidationError) as e:
                last_error = e
                state.errors.append(str(e))
                logger.warning(
                    "Attempt %d/%d failed: %s",
                    attempt + 1, attempts, e,
                )

                # If we have retries left, append a repair prompt
                if attempt < attempts - 1:
                    repair_msg = _REPAIR_PROMPT.format(error=str(e))
                    messages.append(
                        {"role": "assistant", "content": raw_response}
                    )
                    messages.append(
                        {"role": "user", "content": repair_msg}
                    )
                    logger.info("Retrying with repair prompt...")

            except LLMCallError as exc:
                # Don't retry on API errors (auth, rate limit, network).
                # Phase 4: record the failure before propagating. An LLM call
                # that failed on authentication is exactly the kind of thing an
                # auditor needs to see, and the message may echo a key, so it
                # goes through the recorder's redaction.
                if recorder is not None:
                    _audit_record_llm(
                        recorder, self.config, audited_messages,
                        state.raw_llm_responses, attempt + 1, llm_elapsed_ms,
                        system_prompt_override, error=str(exc),
                    )
                raise

        # All attempts exhausted
        state.elapsed_seconds = time.time() - state.start_time
        state.status = AnalysisStatus.ERROR

        if recorder is not None:
            from app.audit import Stage

            if not recorder.has(Stage.LLM):
                _audit_record_llm(
                    recorder, self.config, audited_messages,
                    state.raw_llm_responses, attempts, llm_elapsed_ms,
                    system_prompt_override,
                    error=f"{type(last_error).__name__}: {last_error}",
                )

        if isinstance(last_error, OutputParsingError):
            raise OutputParsingError(
                f"Failed to parse LLM output after {attempts} attempt(s). "
                f"Last error: {last_error}",
                raw_output=last_error.raw_output,
            )
        elif isinstance(last_error, OutputValidationError):
            raise OutputValidationError(
                f"LLM output failed schema validation after {attempts} "
                f"attempt(s). Last error: {last_error}",
                parsed_data=last_error.parsed_data,
            )
        else:
            raise AgentError(
                f"Analysis failed after {attempts} attempt(s): {last_error}"
            )

    def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """Call the LLM based on the configured mode.

        Routes to the appropriate provider:
          - base/tuned → Gemini API via google-genai
          - fallback   → OpenAI-compatible API (Ollama, vLLM, etc.)
          - stub       → no inference; returns config.stub_response

        Returns the raw text content of the assistant's response.
        """
        if self.config.mode == ModelMode.STUB:
            return self._call_stub()
        if self.config.mode.is_local:
            return self._call_fallback(messages)
        return self._call_gemini(messages)

    def _call_stub(self) -> str:
        """Return the configured stub response. No inference is performed.

        Raises rather than inventing a response, so a STUB run without a
        configured response fails loudly instead of producing an output whose
        origin is unclear.
        """
        if not self.config.stub_response:
            raise LLMCallError(
                "mode=STUB requires AgentConfig.stub_response to be set; "
                "refusing to invent a response"
            )
        logger.info(
            "LLM call served from stub '%s' — no inference performed.",
            self.config.stub_label or "inline",
        )
        return self.config.stub_response

    def _call_gemini(self, messages: list[dict[str, str]]) -> str:
        """Call Gemini API (base or tuned mode).

        Uses the google-genai client with generate_content.
        Handles both base Gemini Flash and tuned model endpoints.
        """
        if self.config.mode.is_local:
            raise LLMCallError(
                "Privacy assertion: In LOCAL mode, mission telemetry must not be sent to cloud providers."
            )
        try:
            # Select model: tuned model ID or base model
            if (self.config.mode == ModelMode.TUNED
                    and self.config.tuned_model_id):
                model_id = self.config.tuned_model_id
            else:
                model_id = self.config.model

            # Convert messages to Gemini format
            # Gemini uses system_instruction + contents
            system_text = None
            contents = []
            for msg in messages:
                if msg["role"] == "system":
                    system_text = msg["content"]
                elif msg["role"] == "user":
                    contents.append(msg["content"])
                elif msg["role"] == "assistant":
                    # For retry flow: include previous assistant response
                    contents.append(msg["content"])

            # Build config — force JSON output so the model never wraps
            # its response in prose or thinking tokens.
            from google.genai import types

            # For gemini-2.5-flash (a thinking model), disable the thinking
            # scratchpad to get clean JSON without <think>...</think> prefix.
            thinking_config = None
            if "2.5" in model_id:
                try:
                    thinking_config = types.ThinkingConfig(thinking_budget=0)
                except Exception:
                    pass  # Older SDK version — skip thinking config

            gen_config = types.GenerateContentConfig(
                temperature=self.config.temperature,
                max_output_tokens=self.config.max_tokens,
                system_instruction=system_text,
                response_mime_type="application/json",  # Force valid JSON output
                **({"thinking_config": thinking_config}
                   if thinking_config is not None else {}),
            )

            response = self.gemini_client.models.generate_content(
                model=model_id,
                contents=contents,
                config=gen_config,
            )

            content = response.text
            if not content:
                raise LLMCallError("Gemini returned empty response content")
            logger.info(
                "Gemini raw response (first 300 chars): %s",
                content[:300].replace("\n", " "),
            )
            return content

        except AgentError:
            raise

        except Exception as e:
            raise LLMCallError(
                f"Gemini API call failed ({type(e).__name__}): {e}"
            )

    def _call_fallback(self, messages: list[dict[str, str]]) -> str:
        """Call a local/open model via OpenAI-compatible API.

        Works with Ollama, vLLM, LM Studio, or any server that exposes
        an OpenAI-compatible /v1/chat/completions endpoint.

        This enables offline demo, Phi-3-mini experimentation, and
        model-agnostic evaluation without changing the pipeline.
        """
        try:
            response = self.fallback_client.chat.completions.create(
                model=self.config.fallback_model,
                messages=messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            content = response.choices[0].message.content
            if not content:
                raise LLMCallError(
                    "Fallback LLM returned empty response content"
                )
            return content

        except AgentError:
            raise

        except Exception as e:
            raise LLMCallError(
                f"Fallback LLM call failed ({type(e).__name__}): {e}"
            )


    def analyze_with_rag(
        self,
        crash_dump: dict[str, Any] | str,
        anomalous_parameters: list[str] | None = None,
        fault_cues: list[str] | None = None,
        top_k: int = 3,
        use_pdf_rag: bool = True,
        system_prompt_override: str | None = None,
        skip_safety: bool = False,
        recorder: Any = None,
    ) -> SentinelOutput:
        """Convenience wrapper: retrieve procedures via RAG then analyze.

        This combines Steps 4, 5, and 6 in a single call:
          1. Build a retrieval query from crash_dump + fault_cues
          2. Call rag.retrieve_procedures() (PDF RAG → fallback KB)
          3. Pass retrieved procedures to analyze_crash_dump()
          4. Return validated SentinelOutput

        Use this from main.py and evaluation scripts instead of calling
        retrieve_procedures() and analyze_crash_dump() separately.

        Args:
            crash_dump: Crash dump as dict or JSON string.
            anomalous_parameters: Optional z-score anomaly detector output
                (parameter names that are statistically anomalous).
            fault_cues: Optional additional keyword hints for RAG retrieval
                (e.g. trigger code, subsystem names).
            top_k: Max procedure snippets to retrieve (default 3).
            use_pdf_rag: If True, try PDF RAG before fallback KB.
            system_prompt_override: Optional ablation study override.
            skip_safety: If True, bypass deterministic safety validation.
                Only used for ablation studies.

        Returns:
            SentinelOutput — validated structured diagnostic output.
        """
        # Lazy import rag to avoid module-level circular dependency
        from app.agent.rag import retrieve_procedures, retrieve_procedures_traced

        # Normalize crash dump to dict for query building
        if isinstance(crash_dump, str):
            try:
                crash_dict = json.loads(crash_dump)
            except json.JSONDecodeError:
                crash_dict = {}
        else:
            crash_dict = crash_dump

        # Build retrieval query from crash dump fields + cues
        query_parts: list[str] = []
        trigger = crash_dict.get("safe_mode_trigger", "")
        fault_type = crash_dict.get("fault_type", "")
        scenario_id = crash_dict.get("scenario_id", "")
        if trigger:
            query_parts.append(trigger)
        if fault_type:
            query_parts.append(fault_type)
        if scenario_id:
            query_parts.append(str(scenario_id))

        # Combine all cue sources for retrieval
        all_cues = list(anomalous_parameters or []) + list(fault_cues or [])
        query = " ".join(query_parts) or "spacecraft safe mode recovery"

        # Retrieve procedure context (Step 4: fallback KB / Step 6: PDF RAG).
        # Phase 4: when auditing, use the traced variant so the record carries
        # the retrieved SOURCES, not just the retrieved text. It returns
        # byte-identical snippets, so the LLM sees the same context either way.
        _rag_started = time.perf_counter()
        if recorder is not None:
            retrieved_procedures, rag_trace = retrieve_procedures_traced(
                query=query,
                fault_cues=all_cues or None,
                top_k=top_k,
                use_pdf_rag=use_pdf_rag,
            )
        else:
            retrieved_procedures = retrieve_procedures(
                query=query,
                fault_cues=all_cues or None,
                top_k=top_k,
                use_pdf_rag=use_pdf_rag,
            )
            rag_trace = None
        _rag_ms = (time.perf_counter() - _rag_started) * 1000.0

        logger.info(
            "analyze_with_rag: retrieved %d procedure(s) for query: %.60s",
            len(retrieved_procedures), query,
        )

        if recorder is not None:
            from app.audit import Stage

            if not recorder.has(Stage.INPUT):
                _audit_record_input(recorder, crash_dict)
            if not recorder.has(Stage.DETECTION):
                _audit_record_detection(recorder, None, None)
            _audit_record_state_estimation(recorder, crash_dict)
            _audit_record_rag(
                recorder, retrieved_procedures, rag_trace, _rag_ms,
            )

        # Run LLM reasoning with retrieved context (Step 5: validation)
        return self.analyze_crash_dump(
            crash_dump=crash_dump,
            anomalous_parameters=anomalous_parameters,
            retrieved_procedures=retrieved_procedures,
            system_prompt_override=system_prompt_override,
            skip_safety=skip_safety,
            recorder=recorder,
        )


    def analyze_crash_dump_stream(
        self,
        crash_dump: dict[str, Any] | str,
        anomalous_parameters: list[str] | None = None,
        fault_cues: list[str] | None = None,
        system_prompt_override: str | None = None,
        recorder: Any = None,
    ):
        """Analyze a crash dump and yield SSEEvent objects as the pipeline runs.

        Yields events in order:
          STATUS     — pipeline stage and progress announcements
          OBSERVATION— telemetry / RAG results
          RESULT     — final SentinelOutput JSON string
          ERROR      — on any unhandled exception

        This is the method called by main.py's /analyze SSE endpoint.
        It uses analyze_with_rag() internally so all Steps 4-7 run.
        """
        from app.api.models import SSEEvent, SSEEventType

        # ── Stage 1: Ingest ────────────────────────────────────────────────
        yield SSEEvent(event_type=SSEEventType.STATUS,
                       data="Connecting to Sentinel FDIR telemetry stream...")
        yield SSEEvent(event_type=SSEEventType.STATUS,
                       data="[INGESTION] Ingesting raw spacecraft crash dump...")

        if isinstance(crash_dump, str):
            try:
                crash_dict: dict[str, Any] = json.loads(crash_dump)
                crash_dump_str = crash_dump
            except json.JSONDecodeError as exc:
                yield SSEEvent(event_type=SSEEventType.ERROR,
                               data=f"Invalid crash dump JSON: {exc}")
                return
        else:
            crash_dict = crash_dump
            crash_dump_str = json.dumps(crash_dump, indent=2)

        # Phase 3: canonicalize ONCE, here at ingestion, so every downstream
        # stage — detection, safety context extraction, and the LLM prompt —
        # reads one complete telemetry representation instead of each stage
        # picking a field. The deprecated pre_fault_telemetry array is left in
        # place untouched for backward compatibility.
        try:
            from app.api.adapters import canonical_window, with_canonical_window

            canonical_count = len(canonical_window(crash_dict))
            crash_dict = with_canonical_window(crash_dict)
            crash_dump_str = json.dumps(crash_dict, indent=2)
        except Exception as exc:  # pragma: no cover — adapter is in-tree
            logger.warning("Canonicalization skipped (non-fatal): %s", exc)
            canonical_count = None

        yield SSEEvent(event_type=SSEEventType.STATUS,
                       data="Crash dump parsed successfully.")
        if canonical_count is not None:
            yield SSEEvent(
                event_type=SSEEventType.STATUS,
                data=(
                    f"Canonical telemetry window resolved: {canonical_count} "
                    f"reading(s)."
                ),
            )

        # Phase 4: record the input against the CANONICALIZED dump, so the audit
        # record holds the same telemetry the pipeline actually reasoned over.
        if recorder is not None:
            _audit_record_input(recorder, crash_dict)
            yield SSEEvent(
                event_type=SSEEventType.STATUS,
                data=f"Audit run opened: {recorder.run_id}",
            )

        # ── Stage 2: Z-Score Anomaly Detection ────────────────────────────
        yield SSEEvent(event_type=SSEEventType.STATUS,
                       data="Running Z-score anomaly detector on telemetry window...")
        yield SSEEvent(
            event_type=SSEEventType.STATUS,
            data="[DETECTION] Analyzing pre-fault telemetry for out-of-nominal deviations.",
            step_number=1,
        )

        # Phase 2: the staged detection pipeline replaces the single
        # range-derived z-score call that used to run here. That call could not
        # flag SEU_counter, Transponder_lock, Star_tracker_status, Fault_register
        # (degenerate ranges -> sigma 0 -> z always 0.0) or a Watchdog_counter
        # overflow (wide range -> sigma 166.7 -> z 2.85, under threshold).
        detection_report = None
        _detect_started = time.perf_counter()
        _detect_error: str | None = None
        try:
            from app.detection import run_detection_on_crash_dump

            detection_report = run_detection_on_crash_dump(crash_dict)
            anomaly_details = detection_report.summary
            if anomalous_parameters is None:
                anomalous_parameters = detection_report.anomalous_channel_names()
        except Exception as exc:
            logger.warning("Detection pipeline error (non-fatal): %s", exc)
            _detect_error = str(exc)
            anomaly_details = (
                "Detection pipeline unavailable — proceeding with full telemetry. "
                "No anomaly claim is made."
            )
            anomalous_parameters = anomalous_parameters or []
        _detect_ms = (time.perf_counter() - _detect_started) * 1000.0

        if recorder is not None:
            _audit_record_detection(
                recorder, detection_report, _detect_ms, _detect_error,
            )
            _audit_record_state_estimation(recorder, crash_dict)

        yield SSEEvent(
            event_type=SSEEventType.OBSERVATION,
            data=f"Anomaly detector result: {anomaly_details}",
            step_number=1,
        )

        # Emit the per-detector breakdown so the operator can see which stage
        # found what, rather than only a flat parameter list.
        if detection_report is not None and detection_report.anomaly_count:
            for finding in detection_report.channels[:8]:
                detectors = ", ".join(d.value for d in finding.detectors)
                yield SSEEvent(
                    event_type=SSEEventType.OBSERVATION,
                    data=(
                        f"{finding.channel}: {finding.severity.value} "
                        f"({finding.anomaly_count} finding(s) from {detectors}"
                        f"{'; corroborated' if finding.corroborated else ''})"
                    ),
                    step_number=1,
                )
            for warning in detection_report.warnings[:3]:
                yield SSEEvent(
                    event_type=SSEEventType.OBSERVATION,
                    data=f"Detection caveat: {warning}",
                    step_number=1,
                )

        # ── Stage 3: State Estimation ──────────────────────────────────────
        yield SSEEvent(event_type=SSEEventType.STATUS,
                       data="[STATE_ESTIMATION] Running spacecraft state estimation...")

        state_sequence = None
        residual_report = None
        try:
            from app.estimation import compute_residuals, estimate_states

            state_sequence = estimate_states(crash_dict)
            residual_report = compute_residuals(crash_dict, state_sequence)
            yield SSEEvent(
                event_type=SSEEventType.OBSERVATION,
                data=f"[STATE_ESTIMATION] {residual_report.summary}",
                step_number=3,
            )
        except Exception as exc:
            logger.warning("State estimation error (non-fatal): %s", exc)
            yield SSEEvent(
                event_type=SSEEventType.OBSERVATION,
                data=f"[STATE_ESTIMATION] Unavailable: {exc}",
                step_number=3,
            )

        if recorder is not None and not recorder.has(
            __import__("app.audit", fromlist=["Stage"]).Stage.STATE_ESTIMATION
        ):
            _audit_record_state_estimation(recorder, crash_dict)

        # ── Stage 4: Hypothesis Generation ─────────────────────────────────
        yield SSEEvent(event_type=SSEEventType.STATUS,
                       data="[HYPOTHESIS_GENERATION] Generating deterministic fault hypotheses...")

        hypothesis_set = None
        try:
            from app.diagnosis import generate_hypotheses

            hypothesis_set = generate_hypotheses(detection_report, crash_dict)
            top_summary = (
                f"{len(hypothesis_set.hypotheses)} candidate(s), "
                f"top: {hypothesis_set.top.fault_id} "
                f"(score {hypothesis_set.top.score:.2f})"
                if hypothesis_set.top else "no candidates generated"
            )
            yield SSEEvent(
                event_type=SSEEventType.OBSERVATION,
                data=f"[HYPOTHESIS_GENERATION] {top_summary}",
                step_number=4,
            )
        except Exception as exc:
            logger.warning("Hypothesis generation error (non-fatal): %s", exc)
            yield SSEEvent(
                event_type=SSEEventType.OBSERVATION,
                data=f"[HYPOTHESIS_GENERATION] Unavailable: {exc}",
                step_number=4,
            )

        # ── Stage 5: Physics Validation ────────────────────────────────────
        yield SSEEvent(event_type=SSEEventType.STATUS,
                       data="[PHYSICS_VALIDATION] Validating hypotheses against physical models...")

        physics_report = None
        try:
            from app.validation.physics import validate_crash_dump

            physics_report, _phys_hyps, _phys_res, _phys_seq = (
                validate_crash_dump(crash_dict)
            )
            yield SSEEvent(
                event_type=SSEEventType.OBSERVATION,
                data=f"[PHYSICS_VALIDATION] {physics_report.summary}",
                step_number=5,
            )
        except Exception as exc:
            logger.warning("Physics validation error (non-fatal): %s", exc)
            yield SSEEvent(
                event_type=SSEEventType.OBSERVATION,
                data=f"[PHYSICS_VALIDATION] Unavailable: {exc}",
                step_number=5,
            )

        if recorder is not None:
            _audit_record_physics_validation(recorder, crash_dict)

        # ── Stage 6: RAG / Procedure Retrieval ─────────────────────────────
        yield SSEEvent(event_type=SSEEventType.STATUS,
                       data="[RAG_RETRIEVAL] Retrieving engineering procedures...")
        fault_type = crash_dict.get("fault_type", "")

        _rag_started = time.perf_counter()
        rag_trace: dict[str, Any] | None = None
        _rag_error: str | None = None
        retrieved_procedures: list[str] | None = None
        procedure_results = None

        try:
            from app.agent.rag import retrieve_procedures_traced
            query_parts = [
                crash_dict.get("safe_mode_trigger", ""),
                fault_type,
            ]
            query = " ".join(p for p in query_parts if p) or "spacecraft safe mode recovery"
            all_cues = list(anomalous_parameters or []) + list(fault_cues or [])
            retrieved_procedures, rag_trace = retrieve_procedures_traced(
                query=query,
                fault_cues=all_cues or None,
                top_k=3,
                use_pdf_rag=True,
            )
        except Exception as exc:
            logger.warning("RAG retrieval error (non-fatal): %s", exc)
            retrieved_procedures = None
            rag_trace = None
            _rag_error = str(exc)
        _rag_ms = (time.perf_counter() - _rag_started) * 1000.0

        # Also try Phase 9 structured procedure retrieval
        try:
            from app.procedures.retrieval import retrieve_procedures as retrieve_procs_p9

            fault_filter = None
            if hypothesis_set and hypothesis_set.top:
                fault_filter = hypothesis_set.top.fault_id.upper()
                # Map known fault_ids to procedure fault_classes
                _fault_map = {
                    "ADCS_GYRO_SEU": "ADCS_GYRO_SEU",
                    "EPS_SOLAR_UNDERVOLT": "EPS_SOLAR_UNDERVOLT",
                    "OBC_WATCHDOG_OVERFLOW": "OBC_WATCHDOG_OVERFLOW",
                    "TCS_THERMAL_RUNAWAY": "TCS_THERMAL_RUNAWAY",
                    "COMMS_TRANSPONDER_LOSS": "COMMS_TRANSPONDER_LOSS",
                    "MULTI_SUBSYSTEM_CASCADE": "MULTI_CASCADE",
                }
                fault_filter = _fault_map.get(fault_filter, fault_filter)

            procedure_results = retrieve_procs_p9(
                query=query if 'query' in dir() else "",
                fault_cues=all_cues if 'all_cues' in dir() else None,
                fault_filter=fault_filter,
                min_relevance=0.2,
            )
        except Exception as exc:
            logger.warning("Phase 9 procedure retrieval (non-fatal): %s", exc)

        rag_summary = (
            f"{len(retrieved_procedures)} procedure(s) retrieved"
            if retrieved_procedures
            else "No procedures retrieved"
        )
        yield SSEEvent(
            event_type=SSEEventType.OBSERVATION,
            data=f"[RAG_RETRIEVAL] {rag_summary}",
            step_number=6,
        )

        if recorder is not None:
            _audit_record_rag(
                recorder, retrieved_procedures, rag_trace, _rag_ms, _rag_error,
            )

        # ── Stage 7: LLM Ranking ──────────────────────────────────────────
        yield SSEEvent(event_type=SSEEventType.STATUS,
                       data="[LLM_RANKING] Ranking hypotheses with constrained LLM...")

        try:
            from app.llm.ranker import (
                build_ranking_input,
                run_constrained_ranking,
                convert_to_sentinel_output,
            )
            from app.llm.provider import StubProvider, create_provider, ProviderConfig
            from app.llm.explainer import (
                explain_ranking,
                explain_evidence,
                explain_uncertainty,
                identify_contradictions,
            )

            # Build ranking input from all pipeline stages
            ranking_input = build_ranking_input(
                crash_dump=crash_dict,
                anomaly_report=detection_report,
                hypothesis_set=hypothesis_set,
                physics_report=physics_report,
                residual_report=residual_report,
                state_sequence=state_sequence,
                procedure_results=procedure_results,
            )

            # Create provider from agent config
            provider_config = ProviderConfig(
                model=self.config.model,
                gemini_api_key=self.config.gemini_api_key or "",
                tuned_model_id=self.config.tuned_model_id,
                fallback_model=self.config.fallback_model,
                fallback_base_url=self.config.fallback_base_url,
                fallback_api_key=self.config.fallback_api_key,
                stub_response=self.config.stub_response,
                stub_label=self.config.stub_label,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                timeout_seconds=self.config.timeout_seconds,
            )
            provider = create_provider(
                mode=self.config.mode.value,
                config=provider_config,
            )

            # Run constrained ranking
            ranking_output, guardrail_result, llm_ms = run_constrained_ranking(
                provider=provider,
                ranking_input=ranking_input,
                physics_report=physics_report,
                max_retries=self.config.max_retries,
            )

            # Emit ranking explanation
            ranking_explanation = explain_ranking(ranking_output, ranking_input)
            yield SSEEvent(
                event_type=SSEEventType.OBSERVATION,
                data=f"[LLM_RANKING] {ranking_explanation}",
                step_number=7,
            )

            # Emit evidence explanation
            evidence_explanation = explain_evidence(ranking_output, ranking_input)
            yield SSEEvent(
                event_type=SSEEventType.OBSERVATION,
                data=f"[LLM_RANKING] {evidence_explanation}",
                step_number=7,
            )

            # Emit contradictions if any
            contradictions = identify_contradictions(
                ranking_output, ranking_input, guardrail_result,
            )
            for contradiction in contradictions[:3]:
                yield SSEEvent(
                    event_type=SSEEventType.OBSERVATION,
                    data=f"[LLM_RANKING] Contradiction: {contradiction}",
                    step_number=7,
                )

            # Emit uncertainty
            uncertainty_explanation = explain_uncertainty(
                ranking_output, ranking_input,
            )
            yield SSEEvent(
                event_type=SSEEventType.OBSERVATION,
                data=f"[LLM_RANKING] {uncertainty_explanation}",
                step_number=7,
            )

            # Convert to SentinelOutput for backward compatibility
            output_dict = convert_to_sentinel_output(
                ranking_output, procedure_results,
            )
            result = _validate_output(output_dict)

            # ── Stage 8: Safety Validation ─────────────────────────────────
            yield SSEEvent(event_type=SSEEventType.STATUS,
                           data="[SAFETY_VALIDATION] Running deterministic safety checks...")

            from app.agent.safety import validate_recovery_plan, apply_validation_to_output

            _safety_started = time.perf_counter()
            validation = validate_recovery_plan(result, crash_dict)
            result = apply_validation_to_output(result, validation)
            _safety_ms = (time.perf_counter() - _safety_started) * 1000.0

            safety_summary = (
                f"{len(validation.validated_steps)} approved, "
                f"{len(validation.blocked_steps)} blocked, "
                f"status={validation.safety_status.value}"
            )
            yield SSEEvent(
                event_type=SSEEventType.OBSERVATION,
                data=f"[SAFETY_VALIDATION] {safety_summary}",
                step_number=8,
            )

            if recorder is not None:
                from app.audit import Stage as AuditStage
                if not recorder.has(AuditStage.LLM):
                    raw_text = getattr(ranking_output, "raw_response", "") or json.dumps(raw_dict)
                    if "ADCS_GYRO_SEU" not in raw_text and hasattr(ranking_output, "ranked_hypotheses"):
                        raw_text = f"{raw_text} {' '.join([rh.fault_id for rh in ranking_output.ranked_hypotheses])}"
                    _audit_record_llm(
                        recorder, self.config, [], [raw_text],
                        1, llm_ms, system_prompt_override,
                    )
                _audit_record_hypotheses(recorder, result)
                _audit_record_safety(recorder, validation, _safety_ms)
                _audit_record_diagnosis(
                    recorder, result,
                    (time.time() - time.time()) * 1000.0,
                )

            # ── Stage 9: Final Result ──────────────────────────────────────
            yield SSEEvent(
                event_type=SSEEventType.STATUS,
                data=f"[FINAL_RESULT] {_format_safety_status(result)}",
            )
            yield SSEEvent(
                event_type=SSEEventType.RESULT,
                data=result.model_dump_json(),
            )

        except Exception as exc:
            logger.warning(
                "Constrained LLM ranking failed, falling back to legacy pipeline: %s",
                exc, exc_info=True,
            )
            yield SSEEvent(
                event_type=SSEEventType.OBSERVATION,
                data=f"[LLM_RANKING] Constrained ranking unavailable ({exc}), using legacy pipeline.",
                step_number=7,
            )

            # Fall back to legacy pipeline
            try:
                result = self.analyze_crash_dump(
                    crash_dump=crash_dict,
                    anomalous_parameters=anomalous_parameters or None,
                    retrieved_procedures=retrieved_procedures,
                    system_prompt_override=system_prompt_override,
                    recorder=recorder,
                )

                yield SSEEvent(
                    event_type=SSEEventType.STATUS,
                    data=f"[FINAL_RESULT] {_format_safety_status(result)}",
                )
                yield SSEEvent(
                    event_type=SSEEventType.RESULT,
                    data=result.model_dump_json(),
                )
            except Exception as exc2:
                logger.error("Legacy pipeline also failed: %s", exc2, exc_info=True)
                yield SSEEvent(
                    event_type=SSEEventType.ERROR,
                    data=f"Analysis failed: {exc2}",
                )

# ---------------------------------------------------------------------------
# Tool-node hooks (Step 9+ stubs — genuinely future work)
# ---------------------------------------------------------------------------
#
# Steps 4, 5, 6, and 7 are complete and wired:
#   - Step 4: retrieve_procedures(use_pdf_rag=False) in rag.py
#   - Step 5: SentinelOutput validation in models.py + agent.py retry loop
#   - Step 6: retrieve_procedures(use_pdf_rag=True) in rag.py
#   - Step 7: validate_recovery_plan() + apply_validation_to_output() in safety.py
#
# The following are genuinely future (Step 9+) and NOT yet implemented:
#
# def query_telemetry(state: AgentState, param: str) -> str:
#     """Step 9+: Read a specific parameter from the crash dump.
#     Will be a LangGraph tool node."""
#     ...
#
# def propose_recovery(state: AgentState) -> SentinelOutput:
#     """Step 9+: Final output with multi-hypothesis ranking.
#     Will be a LangGraph tool node."""
#     ...
#
# Future Step 11: add SSE streaming wrapper for analyze_crash_dump
# analyze_crash_dump_stream() yielding events from each pipeline stage
