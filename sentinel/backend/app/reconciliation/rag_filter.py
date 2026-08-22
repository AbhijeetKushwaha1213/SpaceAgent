"""
SENTINEL — Case-Aware RAG Filtering (app/reconciliation/rag_filter.py)

Phase 24.  Deterministic RAG filtering boundary.

Filters retrieved operational procedures and engineering documentation
so that context provided for Case A does not contain unrelated procedures
from separate cases.

When RECONCILIATION_ENABLED=false, this filter is a no-op pass-through.
"""

from __future__ import annotations

from typing import Any, Optional

from app.reconciliation.config import reconciliation_enabled
from app.reconciliation.contract import ReconciliationResult


def filter_rag_context_for_case(
    case_id: str,
    reconciliation_result: Optional[ReconciliationResult],
    retrieved_text: str,
    rag_trace: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Filter RAG snippets and trace metadata to retain only in-scope case procedures.

    Args:
        case_id: Target Case identifier.
        reconciliation_result: ReconciliationResult containing case definitions.
        retrieved_text: Raw retrieved markdown procedure text.
        rag_trace: Metadata dictionary returned by retrieve_procedures_traced().

    Returns:
        Tuple of (filtered_text, filtered_trace).
    """
    if not reconciliation_enabled() or reconciliation_result is None:
        return retrieved_text, dict(rag_trace)

    target_case = reconciliation_result.case(case_id)
    if target_case is None:
        return retrieved_text, dict(rag_trace)

    allowed_subsystems = set(target_case.subsystems)
    for rel_id in reconciliation_result.related_case_ids(case_id):
        rel_case = reconciliation_result.case(rel_id)
        if rel_case:
            allowed_subsystems.update(rel_case.subsystems)

    # Filter snippets from rag_trace
    snippets = list(rag_trace.get("snippets", []))
    filtered_snippets: list[dict[str, Any]] = []

    for snip in snippets:
        snip_text = snip.get("text", "")
        snip_subsys = snip.get("subsystem")

        # If snippet specifies a subsystem, verify it is in allowed_subsystems
        if snip_subsys and snip_subsys not in allowed_subsystems:
            continue

        filtered_snippets.append(snip)

    filtered_trace = dict(rag_trace)
    filtered_trace["snippets"] = filtered_snippets
    filtered_trace["case_id"] = case_id
    filtered_trace["reconciliation_filtered"] = True

    return retrieved_text, filtered_trace
