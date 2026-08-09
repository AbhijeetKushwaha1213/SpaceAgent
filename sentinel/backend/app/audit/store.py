"""
SENTINEL — Audit Store (app/audit/store.py)

Phase 4. Defines WHERE audit records are kept, and enforces that they cannot be
changed after the fact.

SQLite today. The interface is deliberately narrow so a PostgreSQL
implementation is a drop-in — see "Migrating to PostgreSQL" below.

Why append-only is structural here, not a convention
----------------------------------------------------
All three tables are INSERT-only, and nothing derived is stored twice:

    audit_runs           one row per run, the header. Never updated.
    audit_entries        one row per stage entry. Never updated.
    audit_run_outcomes   one row per outcome observation. Never updated.

``entry_count`` and ``final_hash`` are NOT columns. They are computed from
``audit_entries`` at read time. That removes the usual reason an "append-only"
log ends up with an UPDATE statement in it: a counter on the parent row that has
to be bumped whenever a child is added. Appending an operator decision to a
finished run therefore inserts rows and updates nothing.

An outcome is an observation, not a mutable field. Finalizing a run inserts an
outcome row; a later correction inserts another. The latest row by ``seq`` is the
current view, and the earlier ones remain visible, so a status change is part of
the audit trail rather than something that erases it.

Three independent defences, because each has a different failure mode:

    1. API      — there is no update or delete method to call.
    2. Schema   — BEFORE UPDATE and BEFORE DELETE triggers RAISE(ABORT), so
                  bypassing the API with raw SQL still fails.
    3. Hashes   — each entry chains to the previous one, so tampering that
                  bypasses both (rewriting the file, restoring a doctored
                  backup) is still detectable by ``verify_chain()``.

Migrating to PostgreSQL
-----------------------
Subclass ``AuditStore`` and reuse the DDL in ``_TABLE_DDL``, which is written in
portable SQL: TEXT / INTEGER / REAL only, ISO-8601 timestamps as TEXT, JSON as
TEXT, no AUTOINCREMENT, no SQLite-only clauses. What must change:

  * placeholders: ``?`` becomes ``%s`` — override ``_placeholder``
  * immutability triggers: SQLite's ``RAISE(ABORT, ...)`` becomes a PL/pgSQL
    function raising an exception — override ``_immutability_ddl``
  * ``PRAGMA`` setup is SQLite-only and lives in ``_configure_connection``
  * ``BEGIN IMMEDIATE`` becomes ``BEGIN`` with ``SELECT ... FOR UPDATE`` on the
    run row, or a serializable transaction

A test asserts the table DDL contains no SQLite-specific construct, so the
portability claim is checked rather than asserted.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from app.audit.record import (
    GENESIS_HASH,
    Actor,
    AuditRecord,
    ChainVerification,
    OperatorDecisionInput,
    RunHeader,
    RunOutcome,
    RunStatus,
    RunSummary,
    Stage,
    StageEntry,
    StageStatus,
    SYSTEM_ONLY_STAGES,
    canonical_json,
    hash_entry,
    redact,
    scan_for_secrets,
    sha256_hex,
    utc_now_iso,
    verify_entries,
)

logger = logging.getLogger("sentinel.audit")


class AuditStoreError(RuntimeError):
    """Base class for audit store failures."""


class ImmutabilityError(AuditStoreError):
    """Raised on an attempt to alter something already recorded."""


class RunNotFoundError(AuditStoreError):
    """Raised when a run id does not exist."""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — INTERFACE
# ═══════════════════════════════════════════════════════════════════════════

class AuditStore(ABC):
    """Append-only audit record store.

    Note what is absent: no ``update``, no ``delete``, no ``upsert``. A backend
    cannot offer mutation through this interface because the interface does not
    describe it.
    """

    last_error: Optional[str] = None

    @abstractmethod
    def save(self, record: AuditRecord) -> None:
        """Persist a complete run. Raises if the run id already exists."""

    @abstractmethod
    def get(self, run_id: str) -> Optional[AuditRecord]:
        """Return a run, or None when it does not exist."""

    @abstractmethod
    def list_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        scenario_id: Optional[int] = None,
        provenance: Optional[str] = None,
    ) -> list[RunSummary]:
        """Newest-first summaries."""

    @abstractmethod
    def count_runs(self) -> int:
        """Total runs held."""

    @abstractmethod
    def append_operator_decision(
        self, run_id: str, decision: OperatorDecisionInput,
    ) -> StageEntry:
        """Append a human decision to an existing run, extending its chain."""

    @abstractmethod
    def verify_chain(self, run_id: str) -> ChainVerification:
        """Recompute the stored hash chain for a run."""

    def close(self) -> None:  # pragma: no cover — trivial default
        """Release resources. Safe to call more than once."""

    # ── hooks a PostgreSQL subclass overrides ──────────────────────────────

    @staticmethod
    def _placeholder() -> str:
        """Parameter placeholder for this dialect. ``%s`` for psycopg."""
        return "?"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — PORTABLE SCHEMA
# ═══════════════════════════════════════════════════════════════════════════
#
# Portable SQL only. Timestamps are TEXT in ISO-8601 UTC, which sorts correctly
# and needs no dialect-specific conversion. JSON is TEXT; PostgreSQL may widen
# these to JSONB without changing any query in this module.

_TABLE_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS audit_runs (
        run_id               TEXT    NOT NULL PRIMARY KEY,
        audit_schema_version TEXT    NOT NULL,
        contract_version     TEXT    NOT NULL,
        started_at           TEXT    NOT NULL,
        scenario_id          INTEGER,
        incident_id          TEXT,
        fault_type           TEXT,
        provenance           TEXT    NOT NULL,
        source_type          TEXT    NOT NULL,
        source_note          TEXT,
        origin               TEXT    NOT NULL,
        input_sha256         TEXT    NOT NULL,
        header_json          TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_entries (
        run_id         TEXT    NOT NULL,
        seq            INTEGER NOT NULL,
        stage          TEXT    NOT NULL,
        status         TEXT    NOT NULL,
        actor          TEXT    NOT NULL,
        recorded_at    TEXT    NOT NULL,
        duration_ms    REAL,
        summary        TEXT    NOT NULL,
        payload_json   TEXT    NOT NULL,
        payload_sha256 TEXT    NOT NULL,
        prev_hash      TEXT    NOT NULL,
        entry_hash     TEXT    NOT NULL,
        PRIMARY KEY (run_id, seq)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_run_outcomes (
        run_id            TEXT    NOT NULL,
        seq               INTEGER NOT NULL,
        status            TEXT    NOT NULL,
        finished_at       TEXT,
        total_duration_ms REAL,
        error             TEXT,
        recorded_at       TEXT    NOT NULL,
        PRIMARY KEY (run_id, seq)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_runs_started "
    "ON audit_runs (started_at)",
    "CREATE INDEX IF NOT EXISTS idx_audit_runs_scenario "
    "ON audit_runs (scenario_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_entries_stage "
    "ON audit_entries (run_id, stage)",
)

#: Names guarded by immutability triggers.
_APPEND_ONLY_TABLES = ("audit_runs", "audit_entries", "audit_run_outcomes")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — SQLITE IMPLEMENTATION
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_DB_FILENAME = "audit.sqlite3"


def default_db_path() -> Path:
    """Resolve the audit database location.

    ``SENTINEL_AUDIT_DB`` overrides; otherwise ``backend/data/audit/``, beside
    the other data the backend owns.
    """
    override = os.environ.get("SENTINEL_AUDIT_DB")
    if override:
        return Path(override).expanduser()
    backend_root = Path(__file__).resolve().parent.parent.parent
    return backend_root / "data" / "audit" / DEFAULT_DB_FILENAME


class SQLiteAuditStore(AuditStore):
    """SQLite-backed append-only audit store.

    Thread-safe through a lock plus one connection per store. SQLite's own
    ``check_same_thread=False`` is combined with serialized access rather than
    relied upon, because FastAPI serves requests from a thread pool and two
    concurrent appends to the same run must not read the same sequence number.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        enforce_immutability: bool = True,
    ):
        self._path = Path(db_path) if db_path is not None else default_db_path()
        self._is_memory = str(self._path) == ":memory:"
        self._lock = threading.RLock()
        self._enforce_immutability = enforce_immutability
        self.last_error = None

        if not self._is_memory:
            self._path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._configure_connection()
        self._create_schema()

    # ── setup ──────────────────────────────────────────────────────────────

    def _configure_connection(self) -> None:
        """SQLite-only tuning. A PostgreSQL subclass overrides this to a no-op."""
        cur = self._conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        if not self._is_memory:
            # WAL lets readers proceed during a write, which matters because the
            # API reads runs while an investigation is still appending.
            cur.execute("PRAGMA journal_mode = WAL")
        cur.execute("PRAGMA synchronous = FULL")
        cur.close()

    def _immutability_ddl(self) -> tuple[str, ...]:
        """Triggers that abort UPDATE and DELETE on the append-only tables.

        This is the defence that survives someone bypassing this module and
        opening the database directly. PostgreSQL expresses the same rule with a
        trigger function raising an exception.
        """
        statements: list[str] = []
        for table in _APPEND_ONLY_TABLES:
            statements.append(
                f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_update\n"
                f"BEFORE UPDATE ON {table}\n"
                f"BEGIN\n"
                f"  SELECT RAISE(ABORT, "
                f"'{table} is append-only: UPDATE is not permitted');\n"
                f"END"
            )
            statements.append(
                f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete\n"
                f"BEFORE DELETE ON {table}\n"
                f"BEGIN\n"
                f"  SELECT RAISE(ABORT, "
                f"'{table} is append-only: DELETE is not permitted');\n"
                f"END"
            )
        return tuple(statements)

    def _create_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            for statement in _TABLE_DDL:
                cur.execute(statement)
            if self._enforce_immutability:
                for statement in self._immutability_ddl():
                    cur.execute(statement)
            cur.close()

    # ── writing ────────────────────────────────────────────────────────────

    def save(self, record: AuditRecord) -> None:
        """Persist a complete run in one transaction.

        Refuses the write when a payload still carries a recognisable credential.
        Redaction already ran in the recorder; this is the gate that makes the
        guarantee hold even for a caller that built the record by another route.
        Losing one record is preferable to persisting a leaked key forever in a
        store that by design cannot be edited.
        """
        run_id = record.header.run_id

        leaked = scan_for_secrets([e.payload for e in record.entries])
        if leaked:
            raise AuditStoreError(
                f"refusing to persist run {run_id}: unredacted credential "
                f"pattern(s) present: {', '.join(leaked)}"
            )

        verification = record.verify()
        if not verification.valid:
            raise AuditStoreError(
                f"refusing to persist run {run_id}: hash chain is invalid: "
                f"{'; '.join(verification.problems[:3])}"
            )

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE")
                if self._run_exists(cur, run_id):
                    raise ImmutabilityError(
                        f"run {run_id} already exists: an audit record cannot be "
                        f"rewritten"
                    )
                self._insert_header(cur, record.header)
                for entry in record.entries:
                    self._insert_entry(cur, run_id, entry)
                self._insert_outcome(cur, run_id, 1, record.outcome)
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
            finally:
                cur.close()

        logger.info(
            "audit: stored run %s (%d entries, status=%s, provenance=%s)",
            run_id, len(record.entries), record.outcome.status.value,
            record.header.provenance,
        )

    def append_operator_decision(
        self, run_id: str, decision: OperatorDecisionInput,
    ) -> StageEntry:
        """Append a human decision, extending the run's hash chain.

        The server supplies the sequence number, the timestamp, the actor and the
        stage. A client controls only the decision content. Combined with the
        SYSTEM_ONLY_STAGES check below, that is what stops a client from writing
        a detection result or a safety verdict into someone's audit trail.

        Runs inside ``BEGIN IMMEDIATE`` so two concurrent decisions cannot read
        the same tail of the chain and produce a fork.
        """
        payload = redact({
            "decision": decision.decision.value,
            "operator_id": decision.operator_id,
            "rationale": decision.rationale,
            "step_number": decision.step_number,
            "command": decision.command,
            "submitted_via": "POST /api/v1/runs/{run_id}/decisions",
            "server_stamped": True,
        })

        stage = Stage.OPERATOR_DECISION
        if stage in SYSTEM_ONLY_STAGES:  # pragma: no cover — guards a future edit
            raise AuditStoreError(
                "OPERATOR_DECISION is marked system-only; refusing to accept a "
                "client-supplied entry"
            )

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE")
                if not self._run_exists(cur, run_id):
                    raise RunNotFoundError(f"run {run_id} not found")

                cur.execute(
                    "SELECT seq, entry_hash FROM audit_entries "
                    "WHERE run_id = ? ORDER BY seq DESC LIMIT 1",
                    (run_id,),
                )
                tail = cur.fetchone()
                seq = (tail["seq"] + 1) if tail else 1
                prev_hash = tail["entry_hash"] if tail else GENESIS_HASH

                recorded_at = utc_now_iso()
                entry = StageEntry(
                    seq=seq,
                    stage=stage,
                    status=StageStatus.OK,
                    actor=Actor.OPERATOR,
                    recorded_at=recorded_at,
                    duration_ms=None,
                    summary=(
                        f"Operator {decision.operator_id} "
                        f"{decision.decision.value}"
                        + (f" step {decision.step_number}"
                           if decision.step_number else "")
                        + (f" ({decision.command})" if decision.command else "")
                    ),
                    payload=payload,
                    payload_sha256=sha256_hex(canonical_json(payload)),
                    prev_hash=prev_hash,
                    entry_hash=hash_entry(
                        prev_hash=prev_hash,
                        run_id=run_id,
                        seq=seq,
                        stage=stage.value,
                        status=StageStatus.OK.value,
                        actor=Actor.OPERATOR.value,
                        recorded_at=recorded_at,
                        payload=payload,
                    ),
                )
                self._insert_entry(cur, run_id, entry)
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
            finally:
                cur.close()

        logger.info(
            "audit: run %s decision %s by %s (seq=%d)",
            run_id, decision.decision.value, decision.operator_id, entry.seq,
        )
        return entry

    # ── reading ────────────────────────────────────────────────────────────

    def get(self, run_id: str) -> Optional[AuditRecord]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(
                    "SELECT header_json FROM audit_runs WHERE run_id = ?",
                    (run_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                header = RunHeader.model_validate_json(row["header_json"])
                entries = self._load_entries(cur, run_id)
                outcome = self._load_outcome(cur, run_id, entries)
            finally:
                cur.close()
        return AuditRecord(header=header, entries=entries, outcome=outcome)

    def list_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        scenario_id: Optional[int] = None,
        provenance: Optional[str] = None,
    ) -> list[RunSummary]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))

        where: list[str] = []
        params: list[Any] = []
        if scenario_id is not None:
            where.append("r.scenario_id = ?")
            params.append(int(scenario_id))
        if provenance:
            where.append("r.provenance = ?")
            params.append(str(provenance))
        clause = f"WHERE {' AND '.join(where)}" if where else ""

        # entry_count and the decision count are COUNTed, not stored. Nothing has
        # to be updated when an entry is appended, which is what keeps every
        # table INSERT-only.
        sql = f"""
            SELECT r.run_id, r.started_at, r.scenario_id, r.fault_type,
                   r.provenance, r.source_type, r.origin,
                   (SELECT COUNT(*) FROM audit_entries e
                     WHERE e.run_id = r.run_id) AS entry_count,
                   (SELECT COUNT(*) FROM audit_entries e
                     WHERE e.run_id = r.run_id AND e.stage = ?)
                     AS operator_decision_count
              FROM audit_runs r
              {clause}
             ORDER BY r.started_at DESC, r.run_id DESC
             LIMIT ? OFFSET ?
        """
        bind = [Stage.OPERATOR_DECISION.value, *params, limit, offset]

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(sql, bind)
                rows = cur.fetchall()
                summaries: list[RunSummary] = []
                for row in rows:
                    outcome = self._load_outcome_row(cur, row["run_id"])
                    summaries.append(RunSummary(
                        run_id=row["run_id"],
                        started_at=row["started_at"],
                        finished_at=outcome["finished_at"] if outcome else None,
                        status=RunStatus(outcome["status"]) if outcome
                        else RunStatus.IN_PROGRESS,
                        scenario_id=row["scenario_id"],
                        fault_type=row["fault_type"],
                        provenance=row["provenance"],
                        source_type=row["source_type"],
                        entry_count=row["entry_count"],
                        total_duration_ms=(outcome["total_duration_ms"]
                                           if outcome else None),
                        origin=row["origin"],
                        operator_decision_count=row["operator_decision_count"],
                    ))
            finally:
                cur.close()
        return summaries

    def count_runs(self) -> int:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT COUNT(*) AS n FROM audit_runs")
                return int(cur.fetchone()["n"])
            finally:
                cur.close()

    def verify_chain(self, run_id: str) -> ChainVerification:
        """Recompute the chain from what is actually on disk.

        Reads the stored rows rather than an in-memory record, so it detects
        tampering performed outside this process.
        """
        with self._lock:
            cur = self._conn.cursor()
            try:
                if not self._run_exists(cur, run_id):
                    raise RunNotFoundError(f"run {run_id} not found")
                entries = self._load_entries(cur, run_id)
            finally:
                cur.close()
        return verify_entries(run_id, entries)

    # ── row helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _run_exists(cur: sqlite3.Cursor, run_id: str) -> bool:
        cur.execute("SELECT 1 FROM audit_runs WHERE run_id = ?", (run_id,))
        return cur.fetchone() is not None

    @staticmethod
    def _insert_header(cur: sqlite3.Cursor, header: RunHeader) -> None:
        cur.execute(
            """
            INSERT INTO audit_runs (
                run_id, audit_schema_version, contract_version, started_at,
                scenario_id, incident_id, fault_type, provenance, source_type,
                source_note, origin, input_sha256, header_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                header.run_id, header.audit_schema_version,
                header.contract_version, header.started_at, header.scenario_id,
                header.incident_id, header.fault_type, header.provenance,
                header.source_type, header.source_note, header.origin,
                header.input_sha256, header.model_dump_json(),
            ),
        )

    @staticmethod
    def _insert_entry(cur: sqlite3.Cursor, run_id: str, entry: StageEntry) -> None:
        cur.execute(
            """
            INSERT INTO audit_entries (
                run_id, seq, stage, status, actor, recorded_at, duration_ms,
                summary, payload_json, payload_sha256, prev_hash, entry_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, entry.seq, entry.stage.value, entry.status.value,
                entry.actor.value, entry.recorded_at, entry.duration_ms,
                entry.summary, canonical_json(entry.payload),
                entry.payload_sha256, entry.prev_hash, entry.entry_hash,
            ),
        )

    @staticmethod
    def _insert_outcome(
        cur: sqlite3.Cursor, run_id: str, seq: int, outcome: RunOutcome,
    ) -> None:
        cur.execute(
            """
            INSERT INTO audit_run_outcomes (
                run_id, seq, status, finished_at, total_duration_ms, error,
                recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, seq, outcome.status.value, outcome.finished_at,
                outcome.total_duration_ms, outcome.error, utc_now_iso(),
            ),
        )

    @staticmethod
    def _load_entries(cur: sqlite3.Cursor, run_id: str) -> list[StageEntry]:
        import json

        cur.execute(
            "SELECT seq, stage, status, actor, recorded_at, duration_ms, "
            "summary, payload_json, payload_sha256, prev_hash, entry_hash "
            "FROM audit_entries WHERE run_id = ? ORDER BY seq ASC",
            (run_id,),
        )
        return [
            StageEntry(
                seq=row["seq"],
                stage=Stage(row["stage"]),
                status=StageStatus(row["status"]),
                actor=Actor(row["actor"]),
                recorded_at=row["recorded_at"],
                duration_ms=row["duration_ms"],
                summary=row["summary"],
                payload=json.loads(row["payload_json"]),
                payload_sha256=row["payload_sha256"],
                prev_hash=row["prev_hash"],
                entry_hash=row["entry_hash"],
            )
            for row in cur.fetchall()
        ]

    @staticmethod
    def _load_outcome_row(cur: sqlite3.Cursor, run_id: str) -> Optional[sqlite3.Row]:
        """Latest outcome observation for a run.

        Latest wins, earlier ones are retained. A status change is therefore
        itself auditable instead of overwriting the previous claim.
        """
        cur.execute(
            "SELECT status, finished_at, total_duration_ms, error "
            "FROM audit_run_outcomes WHERE run_id = ? ORDER BY seq DESC LIMIT 1",
            (run_id,),
        )
        return cur.fetchone()

    def _load_outcome(
        self, cur: sqlite3.Cursor, run_id: str, entries: list[StageEntry],
    ) -> RunOutcome:
        row = self._load_outcome_row(cur, run_id)
        final_hash = entries[-1].entry_hash if entries else GENESIS_HASH
        if row is None:
            return RunOutcome(
                status=RunStatus.IN_PROGRESS,
                entry_count=len(entries),
                final_hash=final_hash,
            )
        return RunOutcome(
            status=RunStatus(row["status"]),
            finished_at=row["finished_at"],
            total_duration_ms=row["total_duration_ms"],
            error=row["error"],
            entry_count=len(entries),
            final_hash=final_hash,
        )

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # pragma: no cover
                pass

    # ── introspection, for tests and operators ─────────────────────────────

    @property
    def db_path(self) -> Path:
        return self._path

    def raw_connection(self) -> sqlite3.Connection:
        """Direct connection. Exposed so tests can prove the triggers fire."""
        return self._conn

    def table_ddl(self) -> tuple[str, ...]:
        """The portable table DDL, for the portability test."""
        return _TABLE_DDL


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — PROCESS-WIDE STORE
# ═══════════════════════════════════════════════════════════════════════════

_store: Optional[AuditStore] = None
_store_lock = threading.Lock()


def get_store() -> AuditStore:
    """Return the process-wide audit store, creating it on first use.

    Lazy so importing the audit package never touches the filesystem, which
    keeps unit tests that only exercise the schema free of side effects.
    """
    global _store
    with _store_lock:
        if _store is None:
            _store = SQLiteAuditStore()
            logger.info("audit: store opened at %s", _store.db_path)
        return _store


def set_store(store: AuditStore) -> None:
    """Install a specific store. Used by tests and by an alternative backend."""
    global _store
    with _store_lock:
        _store = store


def reset_store() -> None:
    """Close and forget the process-wide store."""
    global _store
    with _store_lock:
        if _store is not None:
            _store.close()
        _store = None
