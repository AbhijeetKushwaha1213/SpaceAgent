"""RAG Retrieval Metrics (app/evaluation/metrics/rag.py)

Measures retrieval precision, retrieval recall, citation correctness, and
grounded response rate for procedure retrieval and reasoning trace.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RAGMetrics:
    retrieval_precision: float
    retrieval_recall: float
    citation_correctness: float
    grounded_response_rate: float

    def to_dict(self) -> dict[str, float]:
        return {
            "retrieval_precision": round(self.retrieval_precision, 4),
            "retrieval_recall": round(self.retrieval_recall, 4),
            "citation_correctness": round(self.citation_correctness, 4),
            "grounded_response_rate": round(self.grounded_response_rate, 4),
        }


def compute_rag_metrics(
    retrieved_procedure_ids: list[str],
    ground_truth_procedure_ids: list[str],
    cited_evidence_ids: list[str],
    valid_input_evidence_ids: list[str],
    has_hallucinations: bool = False,
) -> RAGMetrics:
    """Compute RAG quality metrics for a scenario.

    retrieval_precision: fraction of retrieved procedures that are ground truth relevant
    retrieval_recall: fraction of ground truth relevant procedures retrieved
    citation_correctness: fraction of cited evidence IDs present in input
    grounded_response_rate: 1.0 if no hallucinated procedures/evidence else 0.0
    """
    ret_set = set(retrieved_procedure_ids)
    gt_set = set(ground_truth_procedure_ids)
    valid_ev_set = set(valid_input_evidence_ids)

    tp_procs = len(ret_set & gt_set)
    precision = tp_procs / len(ret_set) if ret_set else (1.0 if not gt_set else 0.0)
    recall = tp_procs / len(gt_set) if gt_set else 1.0

    if cited_evidence_ids:
        valid_citations = sum(1 for cid in cited_evidence_ids if cid in valid_ev_set)
        citation_correctness = valid_citations / len(cited_evidence_ids)
    else:
        citation_correctness = 1.0

    is_grounded = not has_hallucinations and (citation_correctness >= 0.99)
    grounded_rate = 1.0 if is_grounded else 0.0

    return RAGMetrics(
        retrieval_precision=precision,
        retrieval_recall=recall,
        citation_correctness=citation_correctness,
        grounded_response_rate=grounded_rate,
    )
