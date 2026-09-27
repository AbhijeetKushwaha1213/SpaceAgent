"""
SENTINEL — Reconciliation & Separation Subsystem (app/reconciliation)

Phase 24.  Deterministic observation clustering, case isolation, and relationship reasoning.

Principle:
  CORRELATION != IDENTITY.
  If the system cannot deterministically prove that two observations belong together,
  it must preserve separation rather than force a merge.

Package exports:
  - Configuration: reconciliation_enabled, ReconciliationConfig, DEFAULT_CONFIG, config_status
  - Contracts: ObservationEvent, Case, CaseRelationship, RelationshipType,
               ReconciliationSignal, SignalVerdict, SignalOutcome,
               ReconciliationInput, ReconciliationResult,
               make_event_id, make_case_id, make_relationship_id
  - Events: build_observation_events, build_events_from_dicts
  - Signals: evaluate_all_signals
  - Engine: ReconciliationEngine
  - Cases: build_case_from_events, CaseEvidenceIndex
  - Isolation: CaseIsolationBoundary, CrossCaseLeakageError
  - RAG Filter: filter_rag_context_for_case
  - Audit: build_reconciliation_audit_payload
"""

from __future__ import annotations

from app.reconciliation.audit import build_reconciliation_audit_payload
from app.reconciliation.cases import (
    CaseEvidenceIndex,
    build_case_from_events,
)
from app.reconciliation.config import (
    DEFAULT_CONFIG,
    RECONCILIATION_CONFIG_VERSION,
    RECONCILIATION_ENGINE_VERSION,
    ReconciliationConfig,
    config_status,
    reconciliation_enabled,
)
from app.reconciliation.contract import (
    Case,
    CaseRelationship,
    ObservationEvent,
    ReconciliationInput,
    ReconciliationResult,
    ReconciliationSignal,
    RelationshipType,
    SignalOutcome,
    SignalVerdict,
    make_case_id,
    make_event_id,
    make_relationship_id,
)
from app.reconciliation.engine import ReconciliationEngine
from app.reconciliation.events import (
    build_events_from_dicts,
    build_observation_events,
)
from app.reconciliation.isolation import (
    CaseIsolationBoundary,
    CrossCaseLeakageError,
)
from app.reconciliation.rag_filter import filter_rag_context_for_case
from app.reconciliation.signals import (
    evaluate_all_signals,
    evaluate_channel_relationship,
    evaluate_contradiction_indicator,
    evaluate_data_quality,
    evaluate_duplicate_signature,
    evaluate_hypothesis_compatibility,
    evaluate_physical_relationship,
    evaluate_signal_pattern_similarity,
    evaluate_subsystem_relationship,
    evaluate_temporal_proximity,
)

__all__ = [
    "Case",
    "CaseEvidenceIndex",
    "CaseIsolationBoundary",
    "CaseRelationship",
    "CrossCaseLeakageError",
    "DEFAULT_CONFIG",
    "ObservationEvent",
    "RECONCILIATION_CONFIG_VERSION",
    "RECONCILIATION_ENGINE_VERSION",
    "ReconciliationConfig",
    "ReconciliationEngine",
    "ReconciliationInput",
    "ReconciliationResult",
    "ReconciliationSignal",
    "RelationshipType",
    "SignalOutcome",
    "SignalVerdict",
    "build_case_from_events",
    "build_events_from_dicts",
    "build_observation_events",
    "build_reconciliation_audit_payload",
    "config_status",
    "evaluate_all_signals",
    "evaluate_channel_relationship",
    "evaluate_contradiction_indicator",
    "evaluate_data_quality",
    "evaluate_duplicate_signature",
    "evaluate_hypothesis_compatibility",
    "evaluate_physical_relationship",
    "evaluate_signal_pattern_similarity",
    "evaluate_subsystem_relationship",
    "evaluate_temporal_proximity",
    "filter_rag_context_for_case",
    "make_case_id",
    "make_event_id",
    "make_relationship_id",
    "reconciliation_enabled",
]
