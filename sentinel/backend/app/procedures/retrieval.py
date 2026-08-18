"""
SENTINEL — Procedure Retrieval (procedures/retrieval.py)

Phase 9.  Provenance-aware, section-conscious retrieval over the typed
procedure library.

Replaces structure-blind keyword matching with metadata-aware filtering
and weighted relevance scoring.  Never labels a FALLBACK_KB procedure as
ECSS (rule 6), explicitly returns source_type=FALLBACK_KB (rule 7), and
returns INSUFFICIENT_EVIDENCE when nothing relevant is found (rule 8)
rather than forcing irrelevant documents into the context.

Public API:
  retrieve_procedures(query, fault_cues, subsystem_filter, fault_filter,
                      min_relevance, top_k)
      → RetrievalResponse
"""

from __future__ import annotations

import logging
from typing import Any

from app.api.models import SubsystemID
from app.procedures.models import (
    Citation,
    ProcedureDefinition,
    RetrievalResponse,
    RetrievalResult,
    SourceType,
)

logger = logging.getLogger("sentinel.procedures.retrieval")


# ═══════════════════════════════════════════════════════════════════════════
# RELEVANCE SCORING
# ═══════════════════════════════════════════════════════════════════════════

# Scoring weights — chosen so that an exact fault_class match dominates,
# subsystem match is a strong signal, and keyword overlap provides gradation.
_W_FAULT_CLASS = 0.50    # Exact fault_class match
_W_SUBSYSTEM = 0.15      # Subsystem match
_W_TRIGGER_CUE = 0.25    # Fraction of trigger cues matched
_W_QUERY_TEXT = 0.10      # Query text overlap with title/description


def _score_procedure(
    proc: ProcedureDefinition,
    query: str,
    fault_cues: list[str] | None,
    subsystem_filter: SubsystemID | None,
    fault_filter: str | None,
) -> tuple[float, dict[str, Any]]:
    """Score a procedure against the query, returning (score, match_info).

    Score is normalised to [0.0, 1.0].
    """
    score = 0.0
    match_info: dict[str, Any] = {}

    combined_lower = (query or "").lower()
    if fault_cues:
        combined_lower += " " + " ".join(c.lower() for c in fault_cues)

    # --- Fault class match ---
    if fault_filter and proc.fault_class == fault_filter:
        score += _W_FAULT_CLASS
        match_info["fault_class_match"] = True
    elif fault_cues:
        # Check if the fault class appears in the cues
        if proc.fault_class.lower() in combined_lower:
            score += _W_FAULT_CLASS
            match_info["fault_class_match"] = True
        else:
            match_info["fault_class_match"] = False
    else:
        match_info["fault_class_match"] = False

    # --- Subsystem match ---
    if subsystem_filter and proc.subsystem == subsystem_filter:
        score += _W_SUBSYSTEM
        match_info["subsystem_match"] = True
    elif combined_lower:
        # Check if subsystem name appears in the query
        if proc.subsystem.value.lower() in combined_lower:
            score += _W_SUBSYSTEM
            match_info["subsystem_match"] = True
        else:
            match_info["subsystem_match"] = False
    else:
        match_info["subsystem_match"] = False

    # --- Trigger cue overlap ---
    if proc.trigger_cues and combined_lower:
        matched_cues = [
            cue for cue in proc.trigger_cues
            if cue.lower() in combined_lower
        ]
        if proc.trigger_cues:
            cue_ratio = len(matched_cues) / len(proc.trigger_cues)
            score += _W_TRIGGER_CUE * cue_ratio
            match_info["matched_cues"] = matched_cues
            match_info["cue_ratio"] = round(cue_ratio, 3)
    else:
        match_info["matched_cues"] = []
        match_info["cue_ratio"] = 0.0

    # --- Query text overlap with title ---
    if query and query.strip():
        query_words = set(query.lower().split())
        title_words = set(proc.title.lower().split())
        if query_words and title_words:
            overlap = len(query_words & title_words)
            text_ratio = overlap / max(len(query_words), 1)
            score += _W_QUERY_TEXT * min(text_ratio, 1.0)
            match_info["title_overlap_words"] = sorted(
                query_words & title_words
            )
    else:
        match_info["title_overlap_words"] = []

    return round(min(score, 1.0), 4), match_info


# ═══════════════════════════════════════════════════════════════════════════
# FILTERING
# ═══════════════════════════════════════════════════════════════════════════

def _apply_filters(
    procedures: list[ProcedureDefinition],
    subsystem_filter: SubsystemID | None,
    fault_filter: str | None,
    source_filter: str | None,
) -> list[ProcedureDefinition]:
    """Apply hard filters before scoring.

    Hard filters narrow the candidate set.  Scoring then ranks within that set.
    """
    result = list(procedures)

    if subsystem_filter is not None:
        result = [p for p in result if p.subsystem == subsystem_filter]

    if fault_filter is not None:
        result = [p for p in result if p.fault_class == fault_filter]

    if source_filter is not None:
        result = [p for p in result if p.source == source_filter]

    return result


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC RETRIEVAL API
# ═══════════════════════════════════════════════════════════════════════════

def retrieve_procedures(
    query: str = "",
    fault_cues: list[str] | None = None,
    subsystem_filter: SubsystemID | None = None,
    fault_filter: str | None = None,
    source_filter: str | None = None,
    min_relevance: float = 0.3,
    top_k: int = 3,
) -> RetrievalResponse:
    """Retrieve relevant procedures with provenance-aware scoring.

    Phase 9 requirements implemented:
      - Relevance scoring (weighted fault/subsystem/cue/text match)
      - Minimum relevance threshold (``min_relevance``)
      - Subsystem filtering (``subsystem_filter``)
      - Fault filtering (``fault_filter``)
      - Document metadata filtering (``source_filter``)
      - INSUFFICIENT_EVIDENCE when no results meet threshold (rule 8)
      - Source type honesty — FALLBACK_KB, never ECSS (rules 6, 7)

    Args:
        query:            Free-text query string.
        fault_cues:       Anomalous parameter names / keywords.
        subsystem_filter: If set, only return procedures for this subsystem.
        fault_filter:     If set, only return procedures for this fault class.
        source_filter:    If set, only return procedures from this source.
        min_relevance:    Minimum relevance score to include a result (0.0-1.0).
        top_k:            Maximum number of results to return.

    Returns:
        RetrievalResponse with ranked results or INSUFFICIENT_EVIDENCE.
    """
    from app.procedures.library import (
        CITATION_BY_PROCEDURE,
        PROCEDURE_LIBRARY,
    )

    all_procedures = list(PROCEDURE_LIBRARY.values())

    # Apply hard filters
    candidates = _apply_filters(
        all_procedures, subsystem_filter, fault_filter, source_filter,
    )

    if not candidates:
        logger.info(
            "No candidates after filtering (subsystem=%s, fault=%s, source=%s)",
            subsystem_filter, fault_filter, source_filter,
        )
        return RetrievalResponse(
            results=[],
            source_type=SourceType.INSUFFICIENT_EVIDENCE,
            query_metadata={
                "query": query,
                "fault_cues": fault_cues or [],
                "subsystem_filter": (
                    subsystem_filter.value if subsystem_filter else None
                ),
                "fault_filter": fault_filter,
                "source_filter": source_filter,
                "min_relevance": min_relevance,
                "top_k": top_k,
                "candidate_count": 0,
                "reason": "no_candidates_after_filter",
            },
        )

    # Score all candidates
    scored: list[tuple[float, ProcedureDefinition, dict[str, Any]]] = []
    for proc in candidates:
        relevance, match_info = _score_procedure(
            proc, query, fault_cues, subsystem_filter, fault_filter,
        )
        scored.append((relevance, proc, match_info))

    # Sort by relevance (highest first)
    scored.sort(key=lambda x: x[0], reverse=True)

    # Apply minimum relevance threshold
    above_threshold = [
        (rel, proc, info) for rel, proc, info in scored
        if rel >= min_relevance
    ]

    if not above_threshold:
        logger.info(
            "No procedures above min_relevance=%.2f (best=%.4f for %s)",
            min_relevance,
            scored[0][0] if scored else 0.0,
            scored[0][1].procedure_id if scored else "none",
        )
        return RetrievalResponse(
            results=[],
            source_type=SourceType.INSUFFICIENT_EVIDENCE,
            query_metadata={
                "query": query,
                "fault_cues": fault_cues or [],
                "subsystem_filter": (
                    subsystem_filter.value if subsystem_filter else None
                ),
                "fault_filter": fault_filter,
                "source_filter": source_filter,
                "min_relevance": min_relevance,
                "top_k": top_k,
                "candidate_count": len(candidates),
                "best_score": scored[0][0] if scored else 0.0,
                "reason": "below_relevance_threshold",
            },
        )

    # Build results (top_k)
    results: list[RetrievalResult] = []
    for relevance, proc, match_info in above_threshold[:top_k]:
        citation = CITATION_BY_PROCEDURE.get(proc.procedure_id)
        if citation is None:
            # Should never happen given library validation, but be safe
            citation = Citation(
                citation_id=f"CIT-MISSING-{proc.procedure_id}",
                procedure_id=proc.procedure_id,
                source=proc.source,
                source_version=proc.source_version,
                section=proc.section,
                clause=proc.clause,
                provenance="Citation generated at retrieval time (missing)",
            )

        results.append(RetrievalResult(
            procedure=proc,
            relevance_score=relevance,
            source_type=proc.source_type,
            citation=citation,
            matched_filters=match_info,
        ))

    # Determine overall source_type from results
    source_types = {r.source_type for r in results}
    if SourceType.ECSS in source_types:
        overall_source = SourceType.ECSS
    elif source_types:
        overall_source = SourceType.FALLBACK_KB
    else:
        overall_source = SourceType.INSUFFICIENT_EVIDENCE

    return RetrievalResponse(
        results=results,
        source_type=overall_source,
        query_metadata={
            "query": query,
            "fault_cues": fault_cues or [],
            "subsystem_filter": (
                subsystem_filter.value if subsystem_filter else None
            ),
            "fault_filter": fault_filter,
            "source_filter": source_filter,
            "min_relevance": min_relevance,
            "top_k": top_k,
            "candidate_count": len(candidates),
            "returned_count": len(results),
        },
    )
