"""
SENTINEL — Audit Trail (app.audit)

Phase 4. Makes every FDIR investigation reproducible and auditable.

    from app.audit import AuditRecorder, get_store

    recorder = AuditRecorder.begin(crash_dump, origin="POST /api/v1/analyze")
    result = agent.analyze_with_rag(crash_dump, recorder=recorder)
    record = recorder.finalize(store=get_store())
    print(record.run_id)

Two modules:

    record.py   what is recorded — the schema, run ids, redaction, hash chain
    store.py    where it is recorded — an append-only store, SQLite today

Design rules this package enforces, rather than merely documents:

  * A stage that did not run is NOT_RUN. A capability this build does not have
    is NOT_IMPLEMENTED. Neither is ever an empty-but-successful-looking record.
    State estimation and physics validation do not exist yet, so every run says
    so explicitly.
  * Secrets are redacted on the way in, and the store re-scans and REFUSES the
    write if a secret pattern survives.
  * Entries are append-only: the store has no update or delete path, database
    triggers abort UPDATE and DELETE, and a hash chain makes tampering that
    bypasses both detectable.
  * A client can contribute operator decisions and nothing else. The store
    rejects an OPERATOR actor on any system stage, so the frontend cannot
    fabricate detection results, an LLM output, or a safety verdict.
"""

from app.audit.record import (  # noqa: F401
    AUDIT_SCHEMA_VERSION,
    Actor,
    AuditRecord,
    AuditRecorder,
    AuditStatusResponse,
    ChainVerification,
    OperatorDecisionAccepted,
    OperatorDecisionInput,
    OperatorDecisionType,
    RunHeader,
    RunListResponse,
    RunOutcome,
    RunStatus,
    RunSummary,
    Stage,
    StageEntry,
    StageStatus,
    canonical_json,
    generate_run_id,
    hash_entry,
    redact,
    scan_for_secrets,
)
from app.audit.store import (  # noqa: F401
    AuditStore,
    AuditStoreError,
    ImmutabilityError,
    RunNotFoundError,
    SQLiteAuditStore,
    get_store,
    reset_store,
    set_store,
)

__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "Actor",
    "AuditRecord",
    "AuditRecorder",
    "AuditStatusResponse",
    "AuditStore",
    "AuditStoreError",
    "ChainVerification",
    "ImmutabilityError",
    "OperatorDecisionAccepted",
    "OperatorDecisionInput",
    "OperatorDecisionType",
    "RunHeader",
    "RunListResponse",
    "RunOutcome",
    "RunStatus",
    "RunSummary",
    "SQLiteAuditStore",
    "Stage",
    "StageEntry",
    "StageStatus",
    "canonical_json",
    "generate_run_id",
    "get_store",
    "hash_entry",
    "redact",
    "reset_store",
    "scan_for_secrets",
    "set_store",
]
