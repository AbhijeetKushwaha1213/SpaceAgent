"""
SENTINEL — Reconciliation Audit Payload (app/reconciliation/audit.py)

Phase 24.  Deterministic audit payload generation for Stage.RECONCILIATION.

Invariants:
  - Carries only structured case definitions, relationship records, signal verdicts,
    and deterministic rationales.
  - Zero secrets, zero API keys, zero raw model output.
  - Stable and replayable for audit verification.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.reconciliation.config import (
    DEFAULT_CONFIG,
    RECONCILIATION_CONFIG_VERSION,
    RECONCILIATION_ENGINE_VERSION,
    ReconciliationConfig,
)
from app.reconciliation.contract import (
    ReconciliationInput,
    ReconciliationResult,
)


def build_reconciliation_audit_payload(
    input_ctx: ReconciliationInput,
    result: ReconciliationResult,
    config: ReconciliationConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Construct an append-only audit payload for Stage.RECONCILIATION."""
    payload: dict[str, Any] = {
        "stage": "reconciliation",
        "config_version": RECONCILIATION_CONFIG_VERSION,
        "engine_version": RECONCILIATION_ENGINE_VERSION,
        "scenario_id": input_ctx.scenario_id,
        "input_event_count": len(input_ctx.events),
        "input_event_ids": [e.event_id for e in input_ctx.events],
        "case_count": result.case_count,
        "cases": [c.as_dict() for c in result.cases],
        "relationships": [r.as_dict() for r in result.relationships],
        "event_assignments": [list(p) for p in result.event_assignments],
        "merges_performed": [list(p) for p in result.merges_performed],
        "human_review_required": result.human_review_required,
        "reasons": list(result.reasons),
        "warnings": list(result.warnings),
        "thresholds_used": {
            "temporal_same_case_window_s": config.temporal_same_case_window_s,
            "temporal_related_window_s": config.temporal_related_window_s,
            "channel_overlap_min_jaccard": config.channel_overlap_min_jaccard,
            "pattern_similarity_min": config.pattern_similarity_min,
            "propagation_min_strength": config.propagation_min_strength,
            "identity_min_supporting_signals": config.identity_min_supporting_signals,
        },
    }

    # Derive deterministic hash of the reconciliation outcome
    canonical_repr = json.dumps(
        {
            "cases": [c.case_id for c in result.cases],
            "relationships": [r.relationship_id for r in result.relationships],
            "assignments": result.event_assignments,
        },
        sort_keys=True,
    )
    payload["outcome_hash"] = hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()[:16]

    return payload
