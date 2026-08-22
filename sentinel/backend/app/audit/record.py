"""
SENTINEL — Audit Record Schema (app/audit/record.py)

Phase 4. Defines WHAT is recorded for every FDIR investigation.

An audit record answers, for a run that happened months ago: what telemetry went
in, where it came from, what the deterministic layers found, what the LLM was
asked and what it said, what the safety validator refused, what was recommended,
what the operator decided, and how long each step took.

The three properties that make it worth trusting
------------------------------------------------
1. ABSENCE IS RECORDED. ``StageStatus`` distinguishes NOT_IMPLEMENTED (this
   build has no such capability) from NOT_RUN (it exists but did not run) from
   FAILED. State estimation and physics validation are NOT_IMPLEMENTED in every
   record this build writes, because they genuinely do not exist yet. A record
   that simply omitted them would read as if the checks had passed.

2. NOTHING IS RECONSTRUCTED. ``AuditRecorder`` is threaded through the live
   pipeline and each stage is written at the moment it completes, with its
   duration measured rather than supplied. A record cannot describe a stage that
   did not execute, because there is no code path that writes one.

3. SECRETS NEVER ENTER. ``redact()`` runs on every payload at record time, and
   ``scan_for_secrets()`` is re-run by the store, which refuses the write if
   anything survives. API keys are represented by presence and source only —
   never by value, and never by a hash of the value.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.models import CONTRACT_VERSION
from app.api.provenance import Provenance, display_label, normalize

AUDIT_SCHEMA_VERSION = "1.0.0"
"""Version of the audit record schema itself.

Stored on every run. A record written by an older build stays readable because
its schema version says which shape to expect, rather than the reader having to
guess from which fields happen to be present.
"""

#: Payload text longer than this is truncated, with the truncation recorded.
#: Raw LLM responses are the only field that routinely approaches it.
MAX_TEXT_CHARS = 20_000


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — VOCABULARIES
# ═══════════════════════════════════════════════════════════════════════════

class Stage(str, Enum):
    """The pipeline stages an audit record can carry.

    Ordered as the architecture runs them: telemetry → detection → state
    estimation → hypotheses → physics/safety validation → recommendation. The
    LLM sits between evidence gathering and validation, and is recorded as one
    stage among several rather than as the pipeline.
    """

    INPUT = "input"
    """Input telemetry, its provenance, and the scenario it came from."""

    DETECTION = "detection"
    """Deterministic anomaly detection (Phase 2 pipeline)."""

    RECONCILIATION = "reconciliation"
    """Deterministic observation reconciliation & separation (Phase 24)."""

    STATE_ESTIMATION = "state_estimation"
    """Simplified dynamics / state estimation. Not implemented in this build."""

    RAG = "rag"
    """Procedure retrieval, its query, backend and per-snippet sources."""

    ROUTING = "routing"
    """Deterministic hybrid-router decision (Phase 22 §13, Phase 23 Step 5).

    Written only by the RouterOrchestrator while the router is DORMANT
    (ROUTER_ENABLED=false). Carries the RoutingRecord facts: policy signal
    snapshot, per-branch outcomes, escalation trigger, arbitration rule,
    winning branch, physics recheck result, safety outcome, and the
    monotone human-review flag. It never carries raw model output, raw
    telemetry, unredacted payloads, or credentials.
    """

    LLM = "llm"
    """Provider, model, mode, prompt identity, and the raw output."""

    EXTERNAL_TRANSMISSION = "external_transmission"
    """Data leaving the host to an external LLM provider, recorded at send time.

    Written by the cloud transmission guard BEFORE any external call: it carries
    the payload classification, the redaction report, and the destination. In
    LOCAL mode it is written as BLOCKED instead, so the record shows the guard
    was engaged rather than merely absent.
    """

    HYPOTHESES = "hypotheses"
    """Ranked fault hypotheses. LLM-generated; not a validated diagnosis."""

    PHYSICS_VALIDATION = "physics_validation"
    """Physical-consistency checks. Not implemented in this build."""

    SAFETY_VALIDATION = "safety_validation"
    """Deterministic command safety validation (Phase 1 registry)."""

    DIAGNOSIS = "diagnosis"
    """The final structured output and the recommended actions."""

    OPERATOR_DECISION = "operator_decision"
    """A human decision on the run. The only stage a client may contribute."""


#: Stages that only the server may write. Enforced by the store, so a client
#: cannot post a fabricated detection result or safety verdict.
SYSTEM_ONLY_STAGES: frozenset[Stage] = frozenset(
    s for s in Stage if s is not Stage.OPERATOR_DECISION
)


class StageStatus(str, Enum):
    """Outcome of a stage. The distinctions here are the point of the enum."""

    OK = "OK"
    """Ran and completed."""

    DEGRADED = "DEGRADED"
    """Ran with reduced capability. The payload says how."""

    FAILED = "FAILED"
    """Attempted and raised. The payload carries the error."""

    SKIPPED = "SKIPPED"
    """Deliberately bypassed, e.g. safety validation in an ablation study."""

    NOT_RUN = "NOT_RUN"
    """The capability exists but this run did not reach it."""

    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    """This build has no such capability.

    Distinct from NOT_RUN on purpose. NOT_RUN invites the question "why not this
    time"; NOT_IMPLEMENTED answers "there is nothing to run". Recording physics
    validation as anything else would imply a check that does not exist.
    """

    @property
    def is_evidence_of_success(self) -> bool:
        """True only for a stage that actually completed."""
        return self in (StageStatus.OK, StageStatus.DEGRADED)


class Actor(str, Enum):
    """Who produced an entry."""

    SYSTEM = "SYSTEM"
    """Written by the SENTINEL pipeline."""

    OPERATOR = "OPERATOR"
    """Submitted by a human through the API."""


class RunStatus(str, Enum):
    """Terminal state of a run."""

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"
    """Started but never finalized — e.g. the client disconnected mid-stream."""


class OperatorDecisionType(str, Enum):
    """What a human decided about a run."""

    ACKNOWLEDGED = "ACKNOWLEDGED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MODIFIED = "MODIFIED"
    ESCALATED = "ESCALATED"
    EXECUTED = "EXECUTED"
    DEFERRED = "DEFERRED"
    COMMENT = "COMMENT"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — REDACTION
# ═══════════════════════════════════════════════════════════════════════════
#
# Two independent mechanisms, because either alone has a gap: key-name matching
# misses a key pasted into free text, and value matching misses a short or
# custom-format credential sitting under an obviously-named key.

REDACTED = "[REDACTED]"

#: Dict keys whose VALUE is always replaced, whatever it looks like.
_SECRET_KEY_PATTERN = re.compile(
    r"(api[_\-]?key|secret|token|password|passwd|pwd|credential|"
    r"authorization|auth[_\-]?header|bearer|private[_\-]?key|"
    r"access[_\-]?key|session[_\-]?id|cookie|signature)",
    re.IGNORECASE,
)

#: Suffixes marking a key that DESCRIBES a credential rather than holding one.
#:
#: Without this, ``api_key_present: false`` and ``api_key_source: "env.GEMINI_
#: API_KEY"`` were both redacted — the exact fields that let an auditor tell
#: whether a key was configured and where it came from. Over-redaction is not a
#: safe default; it silently strips the audit value the record exists to provide.
#:
#: This only suppresses the KEY-NAME heuristic. The value of an allowlisted key
#: is still scanned for credential shapes, so a real key placed under
#: ``api_key_source`` by mistake is still caught by ``_redact_text``. The
#: residual risk is a custom-format credential deliberately stored under a
#: metadata key name, which redaction cannot distinguish from a legitimate value.
_DESCRIBES_SECRET_SUFFIXES: tuple[str, ...] = (
    "_present", "_source", "_configured", "_recorded", "_required",
    "_value_recorded",
)

#: Exact key names that match the secret pattern but are not credentials.
#:
#: ``max_tokens`` contains the substring "token" and was being replaced with
#: [REDACTED] in every LLM record. These are all LLM accounting or configuration
#: fields where "token" means a unit of text, and no purely lexical rule
#: separates that sense from a credential — so the exceptions are enumerated
#: rather than guessed at. As with the suffix rule, only the key-name heuristic is
#: suppressed; the value is still scanned for credential shapes.
_SAFE_KEY_NAMES: frozenset[str] = frozenset({
    "max_tokens", "min_tokens", "max_output_tokens", "max_input_tokens",
    "input_tokens", "output_tokens", "total_tokens", "prompt_tokens",
    "completion_tokens", "token_count", "tokens_used", "token_limit",
    "tokens_per_second", "estimated_tokens",
})


def _key_holds_secret(key: str) -> bool:
    """True when a dict key names a credential rather than describing one.

    Over-redaction is treated as a defect, not a safe default: an audit record
    whose provider metadata and token budget read ``[REDACTED]`` has lost the
    information it exists to carry, while gaining no protection.
    """
    if not _SECRET_KEY_PATTERN.search(key):
        return False
    lowered = key.lower()
    if lowered in _SAFE_KEY_NAMES:
        return False
    return not lowered.endswith(_DESCRIBES_SECRET_SUFFIXES)

#: Credential shapes recognised inside free text. Ordered longest-first so a
#: broader pattern cannot partially consume a more specific one.
_SECRET_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pem_block", re.compile(
        r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?-----END[ A-Z]*PRIVATE KEY-----",
        re.DOTALL)),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("slack_token", re.compile(r"xox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("bearer_header", re.compile(
        r"Bearer\s+[A-Za-z0-9\-._~+/]{16,}={0,2}", re.IGNORECASE)),
    ("url_credentials", re.compile(
        r"(?P<scheme>[a-z][a-z0-9+.\-]*://)(?P<user>[^:/@\s]+):"
        r"(?P<pw>[^@/\s]+)@")),
)


def scan_for_secrets(value: Any) -> list[str]:
    """Return the names of credential patterns found anywhere in ``value``.

    Used by the store as a final gate before writing. An empty list means no
    recognised credential shape survived redaction; it is not a proof that the
    payload is secret-free, which is why redaction runs first rather than this
    being the only defence.

    A match already containing the redaction marker is not reported. Without
    that, redacting a connection string to ``postgres://user:[REDACTED]@host``
    would still match the URL-credential pattern, the store would refuse the
    write, and any run whose payload mentioned a DSN would silently fail to be
    audited. The suppression cannot hide a real key: the fixed-shape patterns
    (AIza…, sk-…, AKIA…) use character classes that exclude brackets, so the
    marker can never fall inside one of their matches.
    """
    found: list[str] = []
    for text in _iter_strings(value):
        for name, pattern in _SECRET_VALUE_PATTERNS:
            for match in pattern.finditer(text):
                if REDACTED in match.group(0):
                    continue
                found.append(name)
                break
    return sorted(set(found))


def _iter_strings(value: Any) -> Iterable[str]:
    """Yield every string reachable in a nested structure, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_strings(item)


def redact(value: Any, _depth: int = 0) -> Any:
    """Return a copy of ``value`` with credentials replaced by ``[REDACTED]``.

    Applies both mechanisms:
      * a dict key that looks like a secret has its whole value replaced, so a
        custom-format token under ``"api_key"`` is caught even though its shape
        is unrecognised
      * every remaining string is scanned for known credential shapes, so a key
        pasted into a prompt or an error message is caught even though its
        surrounding key name is innocuous

    URL credentials are rewritten to ``scheme://user:[REDACTED]@`` rather than
    blanked entirely: the host and username are operationally useful and are not
    the secret.

    Recursion is depth-limited. A structure deeper than the limit is replaced by
    a marker rather than partially redacted, because a partially redacted
    payload is worse than an absent one.
    """
    if _depth > 40:
        return "[REDACTED: structure too deep to audit safely]"

    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _key_holds_secret(key):
                out[key] = REDACTED
            else:
                out[key] = redact(item, _depth + 1)
        return out

    if isinstance(value, list):
        return [redact(item, _depth + 1) for item in value]
    if isinstance(value, tuple):
        return [redact(item, _depth + 1) for item in value]

    if isinstance(value, str):
        return _redact_text(value)

    return value


def _redact_text(text: str) -> str:
    for name, pattern in _SECRET_VALUE_PATTERNS:
        if name == "url_credentials":
            text = pattern.sub(
                lambda m: f"{m.group('scheme')}{m.group('user')}:{REDACTED}@",
                text,
            )
        else:
            text = pattern.sub(REDACTED, text)
    return text


def truncate_text(text: str, limit: int = MAX_TEXT_CHARS) -> dict[str, Any]:
    """Represent possibly-large text with its truncation made explicit.

    Returns ``{"text": ..., "chars": N, "truncated": bool, "sha256": ...}``. The
    hash is of the FULL text, so a truncated record still proves what the full
    output was if a copy is produced later.
    """
    full_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if len(text) <= limit:
        return {"text": text, "chars": len(text), "truncated": False,
                "sha256": full_hash}
    return {
        "text": text[:limit],
        "chars": len(text),
        "truncated": True,
        "truncated_at": limit,
        "sha256": full_hash,
    }


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — IDENTITY AND HASHING
# ═══════════════════════════════════════════════════════════════════════════

def generate_run_id(now: datetime | None = None) -> str:
    """Return a new run id: ``run_20260808T142233123456Z_a1b2c3d4e5f6``.

    Time-ordered on purpose: lexicographic sort equals chronological sort, so
    listing recent runs needs no secondary index and stays correct after a
    migration to PostgreSQL.

    MICROSECOND precision, not milliseconds. With milliseconds, two runs created
    in the same millisecond sorted by their random suffix instead of by creation
    order, so the ordering guarantee was only approximately true — and the store's
    ``ORDER BY started_at DESC, run_id DESC`` inherited the same flaw. A test
    creating four runs in a loop reproduced it. Building the id from
    ``time.time_ns()`` would be an alternative, but microseconds already exceed
    the rate at which a single process can complete a run.

    The 12 random hex characters guard against a collision between processes; the
    store's UNIQUE constraint turns any remaining collision into an error rather
    than an overwrite.
    """
    moment = now or datetime.now(timezone.utc)
    stamp = moment.strftime("%Y%m%dT%H%M%S") + f"{moment.microsecond:06d}"
    return f"run_{stamp}Z_{uuid.uuid4().hex[:12]}"


def canonical_json(value: Any) -> str:
    """Serialize deterministically, for hashing.

    Sorted keys and no incidental whitespace, so the same content always hashes
    the same regardless of dict insertion order or the Python version.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str,
    )


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


GENESIS_HASH = "0" * 64
"""prev_hash of the first entry in a run's chain."""


def hash_entry(
    prev_hash: str,
    run_id: str,
    seq: int,
    stage: str,
    status: str,
    actor: str,
    recorded_at: str,
    payload: Any,
) -> str:
    """Compute an entry's chain hash.

    Every field that gives the entry meaning is covered, not just the payload —
    otherwise a stage label or a timestamp could be altered without breaking the
    chain. Chaining on ``prev_hash`` means altering entry *n* invalidates every
    entry after it, so tampering cannot be localised.
    """
    material = canonical_json({
        "prev": prev_hash,
        "run_id": run_id,
        "seq": seq,
        "stage": stage,
        "status": status,
        "actor": actor,
        "recorded_at": recorded_at,
        "payload": payload,
    })
    return sha256_hex(material)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — RECORD MODELS
# ═══════════════════════════════════════════════════════════════════════════

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class StageEntry(BaseModel):
    """One append-only entry in a run's log.

    Frozen. The store has no update path and the database aborts UPDATE, so an
    entry is immutable at three levels: the object, the API, and the schema.
    """

    model_config = _FROZEN

    seq: int = Field(..., ge=1, description="1-indexed position within the run")
    stage: Stage
    status: StageStatus
    actor: Actor = Field(
        default=Actor.SYSTEM,
        description="SYSTEM for pipeline output, OPERATOR for a human decision",
    )
    recorded_at: str = Field(
        ..., description="ISO-8601 UTC instant the entry was written",
    )
    duration_ms: Optional[float] = Field(
        default=None, ge=0.0,
        description=(
            "Measured wall-clock duration of the stage. None when the stage did "
            "not execute, so a NOT_IMPLEMENTED stage cannot report a runtime."
        ),
    )
    summary: str = Field(
        ..., min_length=1,
        description="One line an operator can read without opening the payload",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Stage detail, already redacted",
    )
    payload_sha256: str = Field(..., description="SHA-256 of the canonical payload")
    prev_hash: str = Field(..., description="entry_hash of the previous entry")
    entry_hash: str = Field(..., description="This entry's chain hash")

    @field_validator("payload")
    @classmethod
    def payload_must_be_redacted(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Reject a payload carrying a recognisable credential.

        Redaction is applied by the recorder before construction; this is the
        second line, so a caller building a StageEntry by hand cannot skip it.
        """
        found = scan_for_secrets(value)
        if found:
            raise ValueError(
                f"payload contains unredacted credential pattern(s): "
                f"{', '.join(found)} — call app.audit.record.redact() first"
            )
        return value

    @field_validator("duration_ms")
    @classmethod
    def no_runtime_for_a_stage_that_did_not_run(
        cls, value: Optional[float], info,
    ) -> Optional[float]:
        status = (info.data or {}).get("status")
        if value is not None and status in (
            StageStatus.NOT_IMPLEMENTED, StageStatus.NOT_RUN,
        ):
            raise ValueError(
                f"status={status.value} cannot carry duration_ms={value}: a "
                f"stage that did not execute has no runtime"
            )
        return value


class RunHeader(BaseModel):
    """Immutable facts established when a run starts."""

    model_config = _FROZEN

    run_id: str = Field(..., min_length=8)
    audit_schema_version: str = Field(default=AUDIT_SCHEMA_VERSION)
    contract_version: str = Field(default=CONTRACT_VERSION)
    started_at: str = Field(..., description="ISO-8601 UTC")

    scenario_id: Optional[int] = Field(
        default=None, description="Catalogue scenario id, when the input was one",
    )
    incident_id: Optional[str] = None
    fault_type: Optional[str] = None

    provenance: str = Field(
        default=Provenance.UNKNOWN.value,
        description=(
            "REAL | SYNTHETIC | SYNTHETIC_FROM_REAL_METADATA | DEMO | UNKNOWN. "
            "Normalized on construction: an unrecognised value becomes UNKNOWN, "
            "never REAL."
        ),
    )
    source_type: str = Field(
        default="",
        description="Display label DERIVED from provenance, never authored",
    )
    source_note: Optional[str] = None

    origin: str = Field(
        default="unknown",
        description=(
            "How the run was requested, e.g. 'POST /api/v1/analyze'. Recorded so "
            "a run triggered by a test or a script is distinguishable from one an "
            "operator started."
        ),
    )
    input_sha256: str = Field(
        default="",
        description="SHA-256 of the canonical input crash dump, for dedup and proof",
    )

    @field_validator("provenance")
    @classmethod
    def normalize_provenance(cls, value: str) -> str:
        return normalize(value)

    def with_derived_fields(self) -> "RunHeader":
        """Return a copy whose source_type matches its provenance."""
        return self.model_copy(update={
            "source_type": display_label(self.provenance),
        })


class RunOutcome(BaseModel):
    """Terminal facts, written once when a run finishes."""

    model_config = _FROZEN

    status: RunStatus = Field(default=RunStatus.IN_PROGRESS)
    finished_at: Optional[str] = None
    total_duration_ms: Optional[float] = Field(default=None, ge=0.0)
    error: Optional[str] = None
    entry_count: int = Field(default=0, ge=0)
    final_hash: str = Field(
        default=GENESIS_HASH,
        description="entry_hash of the last entry — the run's tamper seal",
    )


class AuditRecord(BaseModel):
    """A complete run: header, append-only entries, outcome.

    The entry list IS the record. The typed accessors below read from it rather
    than duplicating it, so a summary can never disagree with the log.
    """

    model_config = ConfigDict(frozen=True)

    header: RunHeader
    entries: list[StageEntry] = Field(default_factory=list)
    outcome: RunOutcome = Field(default_factory=RunOutcome)

    @property
    def run_id(self) -> str:
        return self.header.run_id

    def stage(self, stage: Stage) -> Optional[StageEntry]:
        """Last entry for a stage, or None if the stage was never written."""
        matches = [e for e in self.entries if e.stage is stage]
        return matches[-1] if matches else None

    def stage_status(self, stage: Stage) -> StageStatus:
        """Status of a stage, defaulting to NOT_RUN when absent.

        Absent means NOT_RUN, never OK. A reader asking about a stage that was
        never recorded gets an answer that reflects the ignorance.
        """
        entry = self.stage(stage)
        return entry.status if entry else StageStatus.NOT_RUN

    def operator_decisions(self) -> list[StageEntry]:
        return [e for e in self.entries if e.stage is Stage.OPERATOR_DECISION]

    def coverage(self) -> dict[str, str]:
        """Status of every stage — the honest completeness report for a run."""
        return {s.value: self.stage_status(s).value for s in Stage}

    def verify(self) -> "ChainVerification":
        """Recompute the hash chain over the in-memory entries."""
        return verify_entries(self.header.run_id, self.entries)


class RunSummary(BaseModel):
    """Listing row. Enough to choose a run without loading its payloads."""

    model_config = _FROZEN

    run_id: str
    started_at: str
    finished_at: Optional[str] = None
    status: RunStatus
    scenario_id: Optional[int] = None
    fault_type: Optional[str] = None
    provenance: str
    source_type: str = ""
    entry_count: int = 0
    total_duration_ms: Optional[float] = None
    origin: str = "unknown"
    operator_decision_count: int = 0


class RunListResponse(BaseModel):
    """Envelope for ``GET /api/v1/runs``."""

    model_config = _FROZEN

    contract_version: str = Field(default=CONTRACT_VERSION)
    audit_schema_version: str = Field(default=AUDIT_SCHEMA_VERSION)
    total: int = Field(..., ge=0, description="Runs held by the store in total")
    count: int = Field(..., ge=0, description="Runs in this page")
    limit: int = Field(..., ge=1)
    offset: int = Field(..., ge=0)
    runs: list["RunSummary"] = Field(default_factory=list)


class AuditStatusResponse(BaseModel):
    """Envelope for ``GET /api/v1/audit/status``."""

    model_config = _FROZEN

    contract_version: str = Field(default=CONTRACT_VERSION)
    audit_schema_version: str = Field(default=AUDIT_SCHEMA_VERSION)
    backend: str = Field(..., description="Store implementation in use")
    location: str = Field(..., description="Database location")
    run_count: int = Field(..., ge=0)
    append_only: bool = Field(
        default=True,
        description="Whether the store enforces append-only semantics",
    )
    enforcement: list[str] = Field(
        default_factory=list,
        description="The mechanisms actually in force, for an auditor to check",
    )
    stages_recorded: list[str] = Field(default_factory=list)
    not_implemented_stages: list[str] = Field(
        default_factory=list,
        description=(
            "Stages this build records as NOT_IMPLEMENTED on every run. Exposed "
            "so a client cannot mistake their absence for a passing check."
        ),
    )
    last_error: Optional[str] = None


class OperatorDecisionAccepted(BaseModel):
    """Response to a recorded operator decision."""

    model_config = _FROZEN

    run_id: str
    seq: int = Field(..., ge=1, description="Server-assigned position")
    stage: str = Field(default=Stage.OPERATOR_DECISION.value)
    actor: str = Field(default=Actor.OPERATOR.value)
    recorded_at: str
    entry_hash: str
    chain_valid: bool = Field(
        ...,
        description="Whether the run's chain still verifies after the append",
    )
    note: str = Field(
        default=(
            "Recorded as an operator decision. operator_id is stored verbatim "
            "and is NOT authenticated by this endpoint."
        ),
    )


class ChainVerification(BaseModel):
    """Result of recomputing a run's hash chain."""

    model_config = _FROZEN

    run_id: str
    valid: bool
    entry_count: int
    checked_at: str
    final_hash: str = GENESIS_HASH
    problems: list[str] = Field(default_factory=list)


def verify_entries(run_id: str, entries: list[StageEntry]) -> ChainVerification:
    """Recompute every hash and check sequence continuity.

    Detects three things independently: a modified payload (payload_sha256 and
    entry_hash both change), a re-linked chain (prev_hash mismatch), and a
    deleted entry (sequence gap). A tamperer would have to rewrite every
    subsequent entry to stay consistent, and the run's stored final_hash still
    would not match.
    """
    problems: list[str] = []
    prev = GENESIS_HASH

    for index, entry in enumerate(entries, start=1):
        if entry.seq != index:
            problems.append(
                f"sequence break at position {index}: entry claims seq={entry.seq}"
            )
        if entry.prev_hash != prev:
            problems.append(
                f"seq {entry.seq}: prev_hash does not match the preceding entry"
            )
        expected_payload = sha256_hex(canonical_json(entry.payload))
        if entry.payload_sha256 != expected_payload:
            problems.append(f"seq {entry.seq}: payload hash mismatch")
        expected_entry = hash_entry(
            prev_hash=entry.prev_hash,
            run_id=run_id,
            seq=entry.seq,
            stage=entry.stage.value,
            status=entry.status.value,
            actor=entry.actor.value,
            recorded_at=entry.recorded_at,
            payload=entry.payload,
        )
        if entry.entry_hash != expected_entry:
            problems.append(f"seq {entry.seq}: entry hash mismatch")
        prev = entry.entry_hash

    return ChainVerification(
        run_id=run_id,
        valid=not problems,
        entry_count=len(entries),
        checked_at=utc_now_iso(),
        final_hash=prev,
        problems=problems,
    )


class OperatorDecisionInput(BaseModel):
    """What a client may submit about a run — and nothing more.

    Deliberately minimal. There is no field here through which a client could
    supply a detection result, an LLM output or a safety verdict, and the store
    rejects an OPERATOR actor on any system stage. So the frontend cannot
    fabricate an audit record; it can only add an attributed human decision to
    one the server already wrote.

    The server stamps the sequence number, the timestamp and the actor. A
    client-supplied timestamp is not accepted, because an audit trail whose
    ordering the client controls is not an audit trail.
    """

    model_config = ConfigDict(extra="forbid")

    decision: OperatorDecisionType = Field(
        ..., description="What the operator decided",
    )
    operator_id: str = Field(
        ..., min_length=1, max_length=128,
        description="Who decided. Recorded verbatim; no identity check is made.",
    )
    rationale: str = Field(
        ..., min_length=1, max_length=4000,
        description="Why. Required — an unexplained decision is not auditable.",
    )
    step_number: Optional[int] = Field(
        default=None, ge=1,
        description="Recovery step the decision applies to, if step-specific",
    )
    command: Optional[str] = Field(
        default=None, max_length=128,
        description="Command the decision applies to, if command-specific",
    )

    @field_validator("operator_id", "rationale", "command")
    @classmethod
    def strip_and_require(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — RECORDER
# ═══════════════════════════════════════════════════════════════════════════

def utc_now_iso() -> str:
    """Current instant, ISO-8601 with an explicit UTC offset.

    Stored as TEXT rather than a native timestamp type: unambiguous, sorts
    correctly as a string, and portable to PostgreSQL without a conversion step.

    Microsecond precision, matching ``generate_run_id()``. At millisecond
    precision two runs started in the same millisecond shared a ``started_at``,
    and the store's ordering fell through to the run id's random suffix — so a
    listing could report runs out of creation order.
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class StageTimer:
    """Context manager that measures a stage and records it exactly once.

    The duration is measured here rather than passed in, so a record cannot
    claim a runtime the stage did not take. An exception inside the block is
    recorded as FAILED with its message, then re-raised — a stage that blew up
    leaves a FAILED entry rather than no entry at all.
    """

    def __init__(self, recorder: "AuditRecorder", stage: Stage):
        self._recorder = recorder
        self._stage = stage
        self._start = 0.0
        self.payload: dict[str, Any] = {}
        self.summary: str = ""
        self.status: StageStatus = StageStatus.OK

    def __enter__(self) -> "StageTimer":
        self._start = time.perf_counter()
        return self

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000.0

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration = self.elapsed_ms
        if exc is not None:
            self._recorder.record(
                self._stage,
                status=StageStatus.FAILED,
                summary=f"{self._stage.value} failed: {exc}",
                payload={**self.payload, "error": str(exc),
                         "error_type": type(exc).__name__},
                duration_ms=duration,
            )
            return False  # re-raise
        self._recorder.record(
            self._stage,
            status=self.status,
            summary=self.summary or f"{self._stage.value} completed",
            payload=self.payload,
            duration_ms=duration,
        )
        return False


class AuditRecorder:
    """Builds a run's append-only entry list as the pipeline executes.

    Threaded through the live pipeline rather than reconstructing a record
    afterwards. Consequences that matter:

      * a stage entry exists only if that stage ran
      * durations are measured, not reported
      * a stage cannot be recorded twice, matching the store's append-only
        semantics — a second write is a bug, and it raises rather than silently
        overwriting the first result

    Not thread-safe. One recorder per run.
    """

    def __init__(self, header: RunHeader):
        self._header = header.with_derived_fields()
        self._entries: list[StageEntry] = []
        self._prev_hash = GENESIS_HASH
        self._started = time.perf_counter()
        self._recorded_stages: set[Stage] = set()

    # ── construction ───────────────────────────────────────────────────────

    @classmethod
    def begin(
        cls,
        crash_dump: dict[str, Any] | None = None,
        origin: str = "unknown",
        run_id: str | None = None,
        provenance_override: str | None = None,
    ) -> "AuditRecorder":
        """Start a run, deriving the header from the crash dump.

        Provenance is read from the payload and normalized, so an unrecognised
        or absent value becomes UNKNOWN rather than defaulting to something
        reassuring. ``provenance_override`` exists for the demo/replay path,
        which knows it is a DEMO even when the payload it replays does not say so.
        """
        dump = crash_dump if isinstance(crash_dump, dict) else {}
        declared = provenance_override or dump.get("provenance")
        header = RunHeader(
            run_id=run_id or generate_run_id(),
            started_at=utc_now_iso(),
            scenario_id=dump.get("scenario_id") if isinstance(
                dump.get("scenario_id"), int) else None,
            incident_id=(str(dump["incident_id"])
                         if dump.get("incident_id") is not None else None),
            fault_type=(str(dump["fault_type"])
                        if dump.get("fault_type") is not None else None),
            provenance=normalize(declared),
            source_note=(str(dump["source_note"])
                         if dump.get("source_note") is not None else None),
            origin=origin,
            input_sha256=sha256_hex(canonical_json(redact(dump))),
        )
        return cls(header)

    # ── properties ─────────────────────────────────────────────────────────

    @property
    def run_id(self) -> str:
        return self._header.run_id

    @property
    def header(self) -> RunHeader:
        return self._header

    @property
    def entries(self) -> list[StageEntry]:
        return list(self._entries)

    def has(self, stage: Stage) -> bool:
        return stage in self._recorded_stages

    # ── recording ──────────────────────────────────────────────────────────

    def record(
        self,
        stage: Stage,
        status: StageStatus,
        summary: str,
        payload: dict[str, Any] | None = None,
        duration_ms: float | None = None,
        actor: Actor = Actor.SYSTEM,
    ) -> StageEntry:
        """Append one entry. Raises if the stage was already recorded."""
        if stage in self._recorded_stages and stage is not Stage.OPERATOR_DECISION:
            raise ValueError(
                f"stage {stage.value} already recorded for run "
                f"{self._header.run_id}: entries are append-only and a stage "
                f"result cannot be revised"
            )

        safe_payload = redact(payload or {})
        if not isinstance(safe_payload, dict):  # pragma: no cover — redact keeps type
            safe_payload = {"value": safe_payload}

        seq = len(self._entries) + 1
        recorded_at = utc_now_iso()
        entry = StageEntry(
            seq=seq,
            stage=stage,
            status=status,
            actor=actor,
            recorded_at=recorded_at,
            duration_ms=duration_ms,
            summary=summary,
            payload=safe_payload,
            payload_sha256=sha256_hex(canonical_json(safe_payload)),
            prev_hash=self._prev_hash,
            entry_hash=hash_entry(
                prev_hash=self._prev_hash,
                run_id=self._header.run_id,
                seq=seq,
                stage=stage.value,
                status=status.value,
                actor=actor.value,
                recorded_at=recorded_at,
                payload=safe_payload,
            ),
        )
        self._entries.append(entry)
        self._recorded_stages.add(stage)
        self._prev_hash = entry.entry_hash
        return entry

    def stage_timer(self, stage: Stage) -> StageTimer:
        """Measure and record a stage with a ``with`` block."""
        return StageTimer(self, stage)

    def record_not_implemented(self, stage: Stage, reason: str) -> StageEntry:
        """Record that this build has no such capability.

        Used for state estimation and physics validation. They are absent from
        the architecture today, and a record that stayed silent about them would
        let a reader assume the checks ran and passed.
        """
        return self.record(
            stage,
            status=StageStatus.NOT_IMPLEMENTED,
            summary=f"{stage.value}: not implemented in this build",
            payload={
                "implemented": False,
                "reason": reason,
                "claim": (
                    "No result is asserted for this stage. Absence of a finding "
                    "here is not evidence that the check would pass."
                ),
            },
        )

    # ── completion ─────────────────────────────────────────────────────────

    def build(
        self,
        status: RunStatus = RunStatus.COMPLETED,
        error: str | None = None,
    ) -> AuditRecord:
        """Assemble the immutable record. Does not persist."""
        total_ms = (time.perf_counter() - self._started) * 1000.0
        outcome = RunOutcome(
            status=status,
            finished_at=utc_now_iso(),
            total_duration_ms=total_ms,
            error=_redact_text(error) if error else None,
            entry_count=len(self._entries),
            final_hash=self._prev_hash,
        )
        return AuditRecord(
            header=self._header, entries=list(self._entries), outcome=outcome,
        )

    def finalize(
        self,
        store: Any = None,
        status: RunStatus = RunStatus.COMPLETED,
        error: str | None = None,
    ) -> AuditRecord:
        """Build the record and persist it if a store is supplied.

        A persistence failure is logged and swallowed: an audit trail that can
        take down an investigation is one that gets switched off, and a lost
        record is less harmful than a lost diagnosis. The caller still gets the
        record, and ``store.last_error`` reports what went wrong — so the failure
        is visible rather than silent.
        """
        record = self.build(status=status, error=error)
        if store is not None:
            try:
                store.save(record)
                store.last_error = None
            except Exception as exc:
                import logging

                logging.getLogger("sentinel.audit").error(
                    "audit: failed to persist run %s: %s",
                    record.run_id, exc, exc_info=True,
                )
                store.last_error = f"{type(exc).__name__}: {exc}"
        return record


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6 — LLM IDENTITY (secret-free by construction)
# ═══════════════════════════════════════════════════════════════════════════

def llm_identity(config: Any) -> dict[str, Any]:
    """Describe the LLM configuration without touching credentials.

    Phase 11 & 12: Records mode_str (base, tuned, cloud, fallback, local, stub)
    and llm_mode (CLOUD | LOCAL | STUB).
    """
    mode_obj = getattr(config, "mode", "cloud")
    mode_str = getattr(mode_obj, "value", str(mode_obj)).lower()

    if mode_str == "stub":
        return {
            "provider": "none_stubbed_response",
            "model": getattr(config, "active_model_name", "stub"),
            "mode": mode_str,
            "llm_mode": "STUB",
            "endpoint": "",
            "local_inference": False,
            "inference_performed": False,
            "stub_label": getattr(config, "stub_label", "") or "inline",
            "api_key_present": False,
            "api_key_source": "none",
            "api_key_value_recorded": False,
            "temperature": None,
            "max_tokens": None,
            "timeout_seconds": None,
            "max_retries": getattr(config, "max_retries", None),
            "claim": (
                "No language model was called. The output recorded for this run "
                "was supplied verbatim from a fixed stub response and must not "
                "be cited as model behaviour."
            ),
        }

    is_local = mode_str in ("local", "fallback")
    if is_local:
        provider = "openai_compatible_local"
        model = getattr(config, "fallback_model", "") or getattr(config, "model", "")
        endpoint = getattr(config, "fallback_base_url", "")
        key_source = "config.fallback_api_key" if getattr(
            config, "fallback_api_key", "") else "none"
        llm_mode_label = "LOCAL"
    else:
        provider = "google_gemini"
        model = getattr(config, "active_model_name", "") or getattr(
            config, "model", "")
        endpoint = "https://generativelanguage.googleapis.com"
        llm_mode_label = "CLOUD"
        if getattr(config, "gemini_api_key", None):
            key_source = "config.gemini_api_key"
        elif os.environ.get("GEMINI_API_KEY"):
            key_source = "env.GEMINI_API_KEY"
        else:
            key_source = "none"

    return {
        "provider": provider,
        "model": model,
        "mode": mode_str,
        "llm_mode": llm_mode_label,
        "endpoint": endpoint,
        "local_inference": is_local,
        "inference_performed": True,
        "api_key_present": key_source != "none",
        "api_key_source": key_source,
        "api_key_value_recorded": False,
        "temperature": getattr(config, "temperature", None),
        "max_tokens": getattr(config, "max_tokens", None),
        "timeout_seconds": getattr(config, "timeout_seconds", None),
        "max_retries": getattr(config, "max_retries", None),
    }
