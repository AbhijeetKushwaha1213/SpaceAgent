"""
SENTINEL — Engineering Procedure Library (procedures/)

Phase 9.  Separates ENGINEERING KNOWLEDGE (general domain context) from
STRUCTURED RECOVERY PROCEDURES (typed, step-by-step, command-referenced
sequences with full provenance).

Public API:
  models          — ProcedureDefinition, ProcedureStep, Citation, SourceType
  library         — PROCEDURE_LIBRARY, CITATION_REGISTRY
  retrieval       — retrieve_procedures(), RetrievalResult, RetrievalResponse
  citations       — get_citation(), format_citation(), validate_citation_chain()
  evaluation      — evaluate_retrieval(), RetrievalEvaluation
"""

from app.procedures.models import (
    Citation,
    ProcedureDefinition,
    ProcedureStep,
    RetrievalEvaluation,
    RetrievalResult,
    RetrievalResponse,
    SourceType,
)
from app.procedures.library import (
    CITATION_REGISTRY,
    PROCEDURE_LIBRARY,
)
from app.procedures.retrieval import retrieve_procedures
from app.procedures.citations import (
    format_citation,
    get_citation,
    get_citations_for_source,
    validate_citation_chain,
)
from app.procedures.evaluation import evaluate_retrieval

__all__ = [
    # Models
    "Citation",
    "ProcedureDefinition",
    "ProcedureStep",
    "RetrievalEvaluation",
    "RetrievalResult",
    "RetrievalResponse",
    "SourceType",
    # Library
    "CITATION_REGISTRY",
    "PROCEDURE_LIBRARY",
    # Retrieval
    "retrieve_procedures",
    # Citations
    "format_citation",
    "get_citation",
    "get_citations_for_source",
    "validate_citation_chain",
    # Evaluation
    "evaluate_retrieval",
]
