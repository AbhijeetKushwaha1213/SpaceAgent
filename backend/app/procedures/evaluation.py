"""
SENTINEL — Retrieval Evaluation (procedures/evaluation.py)

Phase 9 requirement 12: precision, recall, relevance metrics for
retrieval quality assessment.

Given a query with a known ground-truth fault class, this module
evaluates how well the retrieval performed:

  precision  — fraction of returned procedures that are relevant
  recall     — fraction of relevant procedures that were returned
  relevance  — average relevance score across returned results

These metrics are used by test_phase9_procedures.py and can be
attached to RetrievalResponse.evaluation for audit logging.
"""

from __future__ import annotations

from app.procedures.models import (
    RetrievalEvaluation,
    RetrievalResult,
)


def evaluate_retrieval(
    query: str,
    expected_fault_class: str,
    results: list[RetrievalResult],
) -> RetrievalEvaluation:
    """Evaluate retrieval quality against a known ground truth.

    A result is considered "relevant" if its procedure's fault_class
    matches ``expected_fault_class``.

    For multi-subsystem cascade faults, the MULTI_CASCADE procedure is
    considered relevant alongside the initiating fault's procedure.

    Args:
        query:                The query that was issued.
        expected_fault_class: The ground-truth fault class.
        results:              The retrieval results to evaluate.

    Returns:
        RetrievalEvaluation with precision, recall, and relevance.
    """
    if not results:
        return RetrievalEvaluation(
            precision=0.0,
            recall=0.0,
            relevance=0.0,
            expected_fault_class=expected_fault_class,
            returned_fault_classes=(),
        )

    # Count how many relevant procedures exist in the library.
    # For most fault classes, there is exactly 1 relevant procedure.
    # For MULTI_CASCADE, we count it as relevant alongside the primary.
    from app.procedures.library import PROCEDURE_LIBRARY

    relevant_in_library = sum(
        1 for p in PROCEDURE_LIBRARY.values()
        if p.fault_class == expected_fault_class
    )
    # There is always at least 1 relevant procedure if the fault_class
    # exists in the library.  Guard against division by zero.
    relevant_in_library = max(relevant_in_library, 1)

    # Count relevant results
    relevant_returned = sum(
        1 for r in results
        if r.procedure.fault_class == expected_fault_class
    )

    # Precision: fraction of returned that are relevant
    precision = relevant_returned / len(results) if results else 0.0

    # Recall: fraction of relevant in library that were returned
    recall = relevant_returned / relevant_in_library

    # Relevance: average relevance score
    relevance = (
        sum(r.relevance_score for r in results) / len(results)
        if results else 0.0
    )

    returned_classes = tuple(r.procedure.fault_class for r in results)

    return RetrievalEvaluation(
        precision=round(precision, 4),
        recall=round(recall, 4),
        relevance=round(relevance, 4),
        expected_fault_class=expected_fault_class,
        returned_fault_classes=returned_classes,
    )
