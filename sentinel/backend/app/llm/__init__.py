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
    EvidenceStatus,
    ViolationType,
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
    compute_evidence_status,
)
from app.llm.explainer import (  # noqa: F401
    explain_ranking,
    explain_evidence,
    explain_physics,
    explain_uncertainty,
    identify_contradictions,
)
# Phase 23 Step 1: dormant hybrid-router contracts (ROUTER_ENABLED=false).
from app.llm.router_contract import (  # noqa: F401
    Branch,
    BranchOutcome,
    BranchResult,
    RoutingDecision,
    RoutingReason,
    RoutingRecord,
    combine_human_review,
    router_enabled,
)
# Phase 23 Step 2: dormant branch policy + local branch adapter.
from app.llm.branch_policy import (  # noqa: F401
    BranchPolicy,
    PolicyInput,
)
from app.llm.local_branch import (  # noqa: F401
    LocalBranchRunner,
)
# Phase 23 Step 3: dormant cloud branch adapter + redaction gate.
from app.llm.cloud_branch import (  # noqa: F401
    CloudBranchRunner,
    CloudRedactionError,
    CloudRedactionResult,
    redact_ranking_input_for_cloud,
)
# Phase 23 Step 4: dormant deterministic arbitrator + merge resolver.
from app.llm.arbitrator import (  # noqa: F401
    Arbitrator,
    ArbitrationResult,
)
from app.llm.merge_resolver import (  # noqa: F401
    MergeResolver,
)
# Phase 23 Step 5: dormant dry-run orchestrator (sequencing only).
from app.llm.router_orchestrator import (  # noqa: F401
    OrchestrationResult,
    RouterOrchestrator,
    SafetyValidationResult,
    default_safety_validation,
    reassert_physics,
    record_routing_audit,
    routing_audit_payload,
)

__all__ = [
    # models
    "LLMRankingInput",
    "LLMRankingOutput",
    "RankedHypothesis",
    "GuardrailViolation",
    "GuardrailResult",
    "EvidenceStatus",
    "ViolationType",
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
    "compute_evidence_status",
    # explainer
    "explain_ranking",
    "explain_evidence",
    "explain_physics",
    "explain_uncertainty",
    "identify_contradictions",
    # router contract (Phase 23 Step 1 — dormant, no routing behavior)
    "Branch",
    "BranchOutcome",
    "BranchResult",
    "RoutingDecision",
    "RoutingReason",
    "RoutingRecord",
    "combine_human_review",
    "router_enabled",
    # branch policy + local runner (Phase 23 Step 2 — dormant)
    "BranchPolicy",
    "PolicyInput",
    "LocalBranchRunner",
    # cloud branch + redaction gate (Phase 23 Step 3 — dormant)
    "CloudBranchRunner",
    "CloudRedactionError",
    "CloudRedactionResult",
    "redact_ranking_input_for_cloud",
    # arbitrator + merge resolver (Phase 23 Step 4 — dormant)
    "Arbitrator",
    "ArbitrationResult",
    "MergeResolver",
    # router orchestrator (Phase 23 Step 5 — dormant dry-run only)
    "RouterOrchestrator",
    "OrchestrationResult",
    "SafetyValidationResult",
    "default_safety_validation",
    "reassert_physics",
    "record_routing_audit",
    "routing_audit_payload",
]
