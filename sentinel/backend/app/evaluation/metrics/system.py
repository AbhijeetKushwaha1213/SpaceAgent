"""System Performance Metrics (app/evaluation/metrics/system.py)

Measures end-to-end latency, stage latency breakdown (detector, physics, RAG, LLM),
and token usage statistics.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SystemPerformanceMetrics:
    end_to_end_latency_ms: float
    detector_latency_ms: float
    physics_latency_ms: float
    rag_latency_ms: float
    llm_latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "end_to_end_latency_ms": round(self.end_to_end_latency_ms, 2),
            "detector_latency_ms": round(self.detector_latency_ms, 2),
            "physics_latency_ms": round(self.physics_latency_ms, 2),
            "rag_latency_ms": round(self.rag_latency_ms, 2),
            "llm_latency_ms": round(self.llm_latency_ms, 2),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }
