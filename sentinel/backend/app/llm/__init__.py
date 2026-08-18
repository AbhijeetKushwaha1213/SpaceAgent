"""
SENTINEL — LLM Package (app.llm)

Phase 10.  Constrained LLM ranking and explanation.

Transforms the LLM from "diagnose spacecraft" into
"rank and explain deterministic engineering hypotheses."

Modules:
    models.py     constrained input/output schemas
    provider.py   pluggable LLM provider abstraction
    ranker.py     constrained ranking pipeline + guardrails
    explainer.py  operational explanation generation
"""

from app.llm.models import (  # noqa: F401
    LLMRankingInput,
    LLMRankingOutput,
    RankedHypothesis,
    GuardrailViolation,
    GuardrailResult,
)
from app.llm.provider import (  # noqa: F401
    LLMProvider,
    GeminiProvider,
    LocalProvider,
    StubProvider,
    create_provider,
)
from app.llm.ranker import (  # noqa: F401
    build_ranking_input,
    build_constrained_prompt,
    validate_ranking_output,
    run_constrained_ranking,
    convert_to_sentinel_output,
)
from app.llm.explainer import (  # noqa: F401
    explain_ranking,
    explain_evidence,
    explain_physics,
    explain_uncertainty,
    identify_contradictions,
)

__all__ = [
    # models
    "LLMRankingInput",
    "LLMRankingOutput",
    "RankedHypothesis",
    "GuardrailViolation",
    "GuardrailResult",
    # provider
    "LLMProvider",
    "GeminiProvider",
    "LocalProvider",
    "StubProvider",
    "create_provider",
    # ranker
    "build_ranking_input",
    "build_constrained_prompt",
    "validate_ranking_output",
    "run_constrained_ranking",
    "convert_to_sentinel_output",
    # explainer
    "explain_ranking",
    "explain_evidence",
    "explain_physics",
    "explain_uncertainty",
    "identify_contradictions",
]
