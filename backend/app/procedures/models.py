"""
SENTINEL — Procedure Data Models (procedures/models.py)

Phase 9.  Typed data structures for structured recovery procedures, citations,
retrieval results, and evaluation metrics.

Every field required by the Phase 9 specification is present:
  procedure_id, title, subsystem, fault_class, steps, command_id,
  preconditions, postconditions, risk, source, source_version,
  section, clause, provenance.

The ``command_id`` field on each ProcedureStep is validated against the
command registry at library-build time (see library.py), not at model
definition time, so models.py has no import-time dependency on the registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.api.models import RiskLevel, SubsystemID


# ═══════════════════════════════════════════════════════════════════════════
# SOURCE TYPE — honest labeling of where a procedure came from
# ═══════════════════════════════════════════════════════════════════════════

class SourceType(str, Enum):
    """Where a retrieved procedure actually came from.

    Phase 9 rule 6: never label something ECSS unless it actually came
    from an ECSS source document.

    Phase 9 rule 7: if the fallback KB is used, explicitly return FALLBACK_KB.

    Phase 9 rule 8: if retrieval is insufficient, return INSUFFICIENT_EVIDENCE
    rather than forcing irrelevant documents into the context.
    """

    ECSS = "ECSS"
    """Procedure content is directly derived from a specific ECSS standard
    with traceable section/clause references."""

    FALLBACK_KB = "FALLBACK_KB"
    """Procedure is from the SENTINEL fallback knowledge base, written
    *from* ECSS principles but without clause-level citation."""

    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    """No procedure in the library met the minimum relevance threshold.
    Do not force irrelevant documents into the context."""


# ═══════════════════════════════════════════════════════════════════════════
# PROCEDURE STEP — a single command in a recovery sequence
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ProcedureStep:
    """A single step in a structured recovery procedure.

    ``command_id`` must be a key in ``COMMAND_REGISTRY``.  This invariant
    is enforced at library-build time, not here, so models.py can be
    imported without triggering registry validation.
    """

    step_number: int
    """1-indexed position in the procedure."""

    command_id: str
    """Registry command ID, e.g. ``CMD_GYRO_A_DRIVER_RESET``."""

    description: str
    """Human-readable description of what this step does and why."""

    wait_seconds: int
    """Seconds to wait after issuing command before verifying."""

    verification: str
    """Condition to check after the wait period."""

    risk: RiskLevel
    """Risk level for this individual step."""


# ═══════════════════════════════════════════════════════════════════════════
# CITATION — stable, referenceable provenance for a procedure
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Citation:
    """A stable citation that can be referenced in LLM responses.

    Phase 9 requirement 9: stable citation IDs.
    Phase 9 requirement 10: every LLM claim based on a procedure should
    reference citation IDs.

    The citation_id is deterministic and stable — it does not change
    between runs or deployments. Format: ``CIT-{procedure_id}``.
    """

    citation_id: str
    """Stable identifier, e.g. ``CIT-PROC-ADCS-SEU-001``."""

    procedure_id: str
    """The procedure this citation references."""

    source: str
    """Source document name, e.g. ``ECSS-E-ST-70-11C``."""

    source_version: str
    """Version of the source document, e.g. ``Rev.1``."""

    section: str
    """Section reference within the source, e.g. ``Section 5.3``.
    ``N/A`` when no specific section is attributable."""

    clause: str
    """Clause reference within the section, e.g. ``Clause 5.3.2.1``.
    ``N/A`` when no specific clause is attributable."""

    provenance: str
    """Human-readable provenance description."""


# ═══════════════════════════════════════════════════════════════════════════
# PROCEDURE DEFINITION — the complete typed procedure
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ProcedureDefinition:
    """A complete structured recovery procedure with full provenance.

    Contains all 13 fields required by the Phase 9 specification:
    procedure_id, title, subsystem, fault_class, steps, command_id(s),
    preconditions, postconditions, risk, source, source_version,
    section, clause, provenance.

    ``command_id`` is implicit — it lives on each step.  The ``command_ids``
    property extracts the ordered list of all command_ids for convenience.
    """

    procedure_id: str
    """Stable procedure identifier, e.g. ``PROC-ADCS-SEU-001``."""

    title: str
    """Human-readable title."""

    subsystem: SubsystemID
    """Primary subsystem this procedure addresses."""

    fault_class: str
    """Fault class this procedure recovers, e.g. ``ADCS_GYRO_SEU``."""

    steps: tuple[ProcedureStep, ...]
    """Ordered recovery steps."""

    preconditions: tuple[str, ...]
    """Conditions that must hold before executing this procedure."""

    postconditions: tuple[str, ...]
    """Expected state after successful execution."""

    risk: RiskLevel
    """Overall risk level (worst-case across steps)."""

    source: str
    """Source document, e.g. ``ECSS-E-ST-70-11C`` or ``FALLBACK_KB``."""

    source_version: str
    """Version string, e.g. ``Rev.1`` or ``v1.0``."""

    section: str
    """Section reference, or ``N/A``."""

    clause: str
    """Clause reference, or ``N/A``."""

    provenance: str
    """Human-readable provenance description."""

    trigger_cues: tuple[str, ...] = ()
    """Keywords / telemetry parameter names that indicate this fault.
    Carried over from the fallback KB to support keyword matching."""

    @property
    def command_ids(self) -> tuple[str, ...]:
        """Ordered tuple of all command_ids referenced by this procedure."""
        return tuple(step.command_id for step in self.steps)

    @property
    def source_type(self) -> SourceType:
        """Classify this procedure's source honestly.

        Phase 9 rule 6: only return ECSS if the source is actually an ECSS
        standard.  All fallback KB procedures return FALLBACK_KB.
        """
        if self.source.startswith("ECSS"):
            return SourceType.ECSS
        return SourceType.FALLBACK_KB


# ═══════════════════════════════════════════════════════════════════════════
# RETRIEVAL RESULT — a single procedure with retrieval metadata
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RetrievalResult:
    """A procedure returned by retrieval, with relevance and provenance."""

    procedure: ProcedureDefinition
    """The matched procedure."""

    relevance_score: float
    """Relevance score between 0.0 and 1.0."""

    source_type: SourceType
    """Honest source classification."""

    citation: Citation
    """Stable citation for this procedure."""

    matched_filters: dict[str, Any] = field(default_factory=dict)
    """What query elements matched (for debugging/audit)."""


# ═══════════════════════════════════════════════════════════════════════════
# RETRIEVAL RESPONSE — the full retrieval output
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RetrievalResponse:
    """The complete output of a procedure retrieval operation.

    Phase 9 rule 8: when no results meet the minimum relevance threshold,
    ``source_type`` is ``INSUFFICIENT_EVIDENCE`` and ``results`` is empty.
    """

    results: list[RetrievalResult]
    """Procedures that passed the relevance threshold, ranked best first."""

    source_type: SourceType
    """Overall source classification.  INSUFFICIENT_EVIDENCE when empty."""

    query_metadata: dict[str, Any] = field(default_factory=dict)
    """Diagnostic metadata about the query (filters, thresholds, etc.)."""

    evaluation: "RetrievalEvaluation | None" = None
    """Optional retrieval quality metrics (when ground truth is known)."""


# ═══════════════════════════════════════════════════════════════════════════
# RETRIEVAL EVALUATION — quality metrics
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RetrievalEvaluation:
    """Retrieval quality metrics for a single query.

    Phase 9 requirement 12: precision, recall, relevance.
    """

    precision: float
    """Fraction of returned procedures that are relevant to the query."""

    recall: float
    """Fraction of relevant procedures in the library that were returned."""

    relevance: float
    """Mean relevance score across all returned results."""

    expected_fault_class: str = ""
    """The ground-truth fault class used for evaluation."""

    returned_fault_classes: tuple[str, ...] = ()
    """Fault classes of the returned procedures."""
