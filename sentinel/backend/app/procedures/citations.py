"""
SENTINEL — Citation Management (procedures/citations.py)

Phase 9.  Stable citation IDs and provenance chain validation.

Every procedure in the library has a deterministic, stable citation_id
(``CIT-{procedure_id}``).  These can be referenced in LLM responses to
trace any claim back to its source document and provenance chain.

Public API:
  get_citation(procedure_id)          → Citation | None
  get_citations_for_source(source)    → list[Citation]
  format_citation(citation)           → str
  validate_citation_chain(proc_id)    → list[str]  (errors, empty = valid)
"""

from __future__ import annotations

from app.procedures.models import Citation


def get_citation(procedure_id: str) -> Citation | None:
    """Return the stable citation for a procedure, or None if not found.

    Lazy import to avoid circular dependency at module level.
    """
    from app.procedures.library import CITATION_BY_PROCEDURE
    return CITATION_BY_PROCEDURE.get(procedure_id)


def get_citations_for_source(source: str) -> list[Citation]:
    """Return all citations from a given source document.

    Args:
        source: Source name, e.g. ``"FALLBACK_KB"`` or ``"ECSS-E-ST-70-11C"``.

    Returns:
        List of citations from that source, sorted by citation_id.
    """
    from app.procedures.library import CITATION_REGISTRY
    return sorted(
        [c for c in CITATION_REGISTRY.values() if c.source == source],
        key=lambda c: c.citation_id,
    )


def format_citation(citation: Citation) -> str:
    """Format a citation as a human-readable string.

    Example output::

        [CIT-PROC-ADCS-SEU-001] FALLBACK_KB v1.0, N/A, N/A
        Provenance: Based on ECSS-E-ST-70-11C / ECSS-Q-ST-30-02C ...

    This format is suitable for inclusion in LLM context or audit logs.
    """
    header = (
        f"[{citation.citation_id}] "
        f"{citation.source} {citation.source_version}, "
        f"{citation.section}, {citation.clause}"
    )
    return f"{header}\nProvenance: {citation.provenance}"


def validate_citation_chain(procedure_id: str) -> list[str]:
    """Validate the full provenance chain for a procedure.

    Checks:
      1. Procedure exists in the library
      2. Citation exists for the procedure
      3. Citation fields are non-empty
      4. Citation.procedure_id matches the procedure
      5. Citation.source matches the procedure's source
      6. Citation.source_version matches the procedure's source_version

    Returns:
        List of error strings.  Empty list means the chain is valid.
    """
    from app.procedures.library import PROCEDURE_LIBRARY, CITATION_BY_PROCEDURE

    errors: list[str] = []

    # 1. Procedure exists
    proc = PROCEDURE_LIBRARY.get(procedure_id)
    if proc is None:
        errors.append(f"Procedure '{procedure_id}' not found in library")
        return errors

    # 2. Citation exists
    citation = CITATION_BY_PROCEDURE.get(procedure_id)
    if citation is None:
        errors.append(
            f"No citation found for procedure '{procedure_id}'"
        )
        return errors

    # 3. Non-empty fields
    for field_name in (
        "citation_id", "procedure_id", "source",
        "source_version", "section", "clause", "provenance",
    ):
        val = getattr(citation, field_name, None)
        if not val or not val.strip():
            errors.append(
                f"Citation {citation.citation_id}: "
                f"field '{field_name}' is empty"
            )

    # 4. procedure_id consistency
    if citation.procedure_id != procedure_id:
        errors.append(
            f"Citation {citation.citation_id}: procedure_id mismatch: "
            f"expected '{procedure_id}', got '{citation.procedure_id}'"
        )

    # 5. Source consistency
    if citation.source != proc.source:
        errors.append(
            f"Citation {citation.citation_id}: source mismatch: "
            f"procedure has '{proc.source}', "
            f"citation has '{citation.source}'"
        )

    # 6. Source version consistency
    if citation.source_version != proc.source_version:
        errors.append(
            f"Citation {citation.citation_id}: source_version mismatch: "
            f"procedure has '{proc.source_version}', "
            f"citation has '{citation.source_version}'"
        )

    return errors
