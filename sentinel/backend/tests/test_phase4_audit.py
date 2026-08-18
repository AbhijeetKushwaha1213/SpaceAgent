"""
SENTINEL — Phase 4 audit trail tests (test_phase4_audit.py)

Run:
    python3 -m unittest tests.test_phase4_audit -v

Grouped by the property under test rather than by module, because the guarantees
are what matter and most of them are enforced in more than one place:

  1. REDACTION          secrets never reach the store
  2. IMMUTABILITY       recorded entries cannot be altered — API, schema, hashes
  3. HONEST ABSENCE     a stage that did not run never looks like one that did
  4. COMPLETENESS       all 20 required fields are actually persisted
  5. PROVENANCE         REAL / SYNTHETIC / SYNTHETIC_FROM_REAL_METADATA / DEMO
  6. NO FABRICATION     a client cannot write a system stage
  7. PORTABILITY        the schema is not SQLite-specific
  8. PIPELINE           a real end-to-end run produces a complete record

httpx is not installed in this environment, so FastAPI's TestClient is
unavailable. The endpoint functions are exercised directly and the route table is
asserted against the app object — the same approach the Phase 3 suite uses.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.agent.agent import (                                       # noqa: E402
    AgentConfig,
    ModelMode,
    SentinelAgent,
)
from app.agent.prompts import (                                     # noqa: E402
    PROMPT_VERSION,
    prompt_fingerprint,
    prompt_identity,
)
from app.agent.rag import (                                         # noqa: E402
    retrieve_procedures,
    retrieve_procedures_traced,
)
from app.api.adapters import with_canonical_window                  # noqa: E402
from app.api.provenance import Provenance, display_label, normalize  # noqa: E402
from app.api.scenarios import get_all_scenarios                     # noqa: E402
from app.audit import (                                             # noqa: E402
    AUDIT_SCHEMA_VERSION,
    Actor,
    AuditRecord,
    AuditRecorder,
    AuditStore,
    AuditStoreError,
    ImmutabilityError,
    OperatorDecisionInput,
    OperatorDecisionType,
    RunStatus,
    SQLiteAuditStore,
    Stage,
    StageEntry,
    StageStatus,
    canonical_json,
    generate_run_id,
    redact,
    scan_for_secrets,
)
from app.audit.record import (                                      # noqa: E402
    GENESIS_HASH,
    REDACTED,
    llm_identity,
    sha256_hex,
    truncate_text,
)
from app.audit.store import RunNotFoundError, _TABLE_DDL            # noqa: E402

# A string with the shape of a real Google AI key. Not a credential.
FAKE_GOOGLE_KEY = "AIzaSy" + "B" * 33
FAKE_OPENAI_KEY = "sk-proj-" + "c" * 32


def _stub_response() -> str:
    """A schema-valid model response whose step 2 is not in the registry."""
    return json.dumps({
        "hypotheses": [
            {"rank": 1, "root_cause": "ADCS_GYRO_SEU",
             "affected_component": "GYRO_A", "confidence": 0.88,
             "causal_chain": ["SEU counter increments",
                              "Gyro rate returns NaN",
                              "Attitude error grows"]},
            {"rank": 2, "root_cause": "ADCS_GYRO_HARDWARE_FAILURE",
             "affected_component": "GYRO_A", "confidence": 0.07,
             "causal_chain": ["Driver degradation", "Rate invalid"]},
            {"rank": 3, "root_cause": "OBC_SENSOR_BUS_FAULT",
             "affected_component": "OBC", "confidence": 0.05,
             "causal_chain": ["Bus read error", "Telemetry dropout"]},
        ],
        "recovery_plan": [
            {"step": 1, "command": "CMD_GYRO_A_DRIVER_RESET",
             "rationale": "Clear the SEU latch-up in the gyro driver.",
             "wait_seconds": 30, "verify": "Gyro rate valid", "risk": "LOW"},
            {"step": 2, "command": "CMD_TOTALLY_INVENTED_COMMAND",
             "rationale": "Invented, to exercise the registry check.",
             "wait_seconds": 5, "verify": "nothing", "risk": "MEDIUM"},
        ],
        "confidence": 0.88,
        "requires_human_review": False,
        "reasoning_summary": (
            "SEU count preceded the gyro dropout, which preceded the attitude "
            "error. The ordering favours a single-event upset."
        ),
    })


def _memory_store() -> SQLiteAuditStore:
    return SQLiteAuditStore(db_path=":memory:")


def _simple_record(store: SQLiteAuditStore | None = None,
                   provenance: str = "SYNTHETIC") -> AuditRecord:
    rec = AuditRecorder.begin(
        {"scenario_id": 1, "fault_type": "ADCS_GYRO_SEU",
         "provenance": provenance},
        origin="unit-test",
    )
    rec.record(Stage.INPUT, StageStatus.OK, "input", {"reading_count": 3})
    rec.record(Stage.DETECTION, StageStatus.OK, "detection", {"anomaly_count": 2})
    rec.record_not_implemented(Stage.STATE_ESTIMATION, "absent")
    rec.record_not_implemented(Stage.PHYSICS_VALIDATION, "absent")
    return rec.finalize(store=store)


# ═══════════════════════════════════════════════════════════════════════════
# 1 — REDACTION
# ═══════════════════════════════════════════════════════════════════════════

class TestRedaction(unittest.TestCase):

    def test_secret_key_names_are_redacted_whatever_the_value_looks_like(self):
        for key in ("api_key", "API_KEY", "apiKey", "gemini_api_key", "secret",
                    "password", "passwd", "token", "credential",
                    "authorization", "private_key", "access_key", "session_id",
                    "cookie", "signature"):
            with self.subTest(key=key):
                out = redact({key: "some-custom-format-value"})
                self.assertEqual(out[key], REDACTED)

    def test_credential_shapes_are_redacted_inside_free_text(self):
        cases = {
            "google": f"please use {FAKE_GOOGLE_KEY} for the call",
            "openai": f"key={FAKE_OPENAI_KEY}",
            "anthropic": "sk-ant-" + "d" * 30,
            "github": "ghp_" + "e" * 36,
            "slack": "xoxb-" + "1" * 20,
            "aws": "AKIA" + "F" * 16,
            "bearer": "Authorization: Bearer " + "g" * 40,
        }
        for name, text in cases.items():
            with self.subTest(pattern=name):
                out = redact({"note": text})
                self.assertIn(REDACTED, out["note"])
                self.assertEqual(scan_for_secrets(out), [])

    def test_url_credentials_keep_host_and_user_but_lose_the_password(self):
        out = redact({"dsn": "postgresql://sentinel:hunter2@db.internal:5432/a"})
        self.assertIn("sentinel", out["dsn"])
        self.assertIn("db.internal", out["dsn"])
        self.assertNotIn("hunter2", out["dsn"])

    def test_redacted_dsn_is_not_reported_as_a_surviving_secret(self):
        """Regression: the redacted form still matched the URL pattern.

        ``postgres://user:[REDACTED]@host`` matched ``url_credentials`` because
        ``[REDACTED]`` satisfies the password character class. scan_for_secrets
        therefore reported a leak, and the store refuses to persist a record with
        a surviving credential — so any run whose payload mentioned a connection
        string would silently fail to be audited.
        """
        cleaned = redact({"dsn": "postgresql://u:p@h:5432/db"})
        self.assertEqual(scan_for_secrets(cleaned), [])

    def test_metadata_about_a_secret_is_not_redacted(self):
        """Regression: over-redaction destroyed the audit value.

        ``api_key_present`` and ``api_key_source`` are the fields that let an
        auditor tell whether a key was configured and where it came from. The
        key-name rule matched them and replaced both with [REDACTED].
        """
        out = redact({
            "api_key_present": True,
            "api_key_source": "env.GEMINI_API_KEY",
            "api_key_value_recorded": False,
            "token_source": "vault",
            "secret_configured": False,
        })
        self.assertIs(out["api_key_present"], True)
        self.assertEqual(out["api_key_source"], "env.GEMINI_API_KEY")
        self.assertIs(out["api_key_value_recorded"], False)
        self.assertEqual(out["token_source"], "vault")
        self.assertIs(out["secret_configured"], False)

    def test_the_metadata_allowlist_does_not_open_a_hole(self):
        """A real key under an allowlisted name is still caught by value scan."""
        out = redact({"api_key_source": FAKE_GOOGLE_KEY})
        self.assertEqual(out["api_key_source"], REDACTED)
        self.assertEqual(scan_for_secrets(out), [])

    def test_token_count_fields_are_not_redacted(self):
        """Regression: ``max_tokens`` contains the substring "token".

        Every LLM record was storing ``max_tokens: [REDACTED]``. "token" there
        means a unit of text, not a credential.
        """
        payload = {
            "max_tokens": 4096, "prompt_tokens": 1200, "completion_tokens": 340,
            "total_tokens": 1540, "token_count": 99, "max_output_tokens": 8192,
        }
        self.assertEqual(redact(payload), payload)

    def test_real_token_fields_are_still_redacted(self):
        for key in ("access_token", "refresh_token", "auth_token", "token",
                    "bearer_token", "id_token"):
            with self.subTest(key=key):
                self.assertEqual(redact({key: "abc123"})[key], REDACTED)

    def test_a_credential_under_a_token_count_name_is_still_caught(self):
        out = redact({"max_tokens": FAKE_GOOGLE_KEY})
        self.assertEqual(out["max_tokens"], REDACTED)
        self.assertEqual(scan_for_secrets(out), [])

    def test_llm_identity_survives_redaction_intact(self):
        """The whole point: the LLM record must remain readable after redaction."""
        cfg = AgentConfig(gemini_api_key=FAKE_GOOGLE_KEY)
        cleaned = redact(llm_identity(cfg))
        self.assertEqual(cleaned["provider"], "google_gemini")
        self.assertEqual(cleaned["model"], "gemini-2.5-flash")
        self.assertEqual(cleaned["mode"], "base")
        self.assertEqual(cleaned["max_tokens"], 4096)
        self.assertEqual(cleaned["temperature"], 0.1)
        self.assertIs(cleaned["api_key_present"], True)
        self.assertEqual(cleaned["api_key_source"], "config.gemini_api_key")
        self.assertNotIn(FAKE_GOOGLE_KEY, json.dumps(cleaned))

    def test_non_secret_values_survive_untouched(self):
        payload = {"count": 13, "ratio": 0.5, "flag": True, "none": None,
                   "name": "Gyro_rate_degs", "list": [1, 2, 3]}
        self.assertEqual(redact(payload), payload)

    def test_nested_structures_are_walked(self):
        out = redact({"a": [{"b": {"api_key": FAKE_GOOGLE_KEY}}]})
        self.assertEqual(out["a"][0]["b"]["api_key"], REDACTED)

    def test_absurdly_deep_structure_is_replaced_not_partially_redacted(self):
        node: dict = {"api_key": FAKE_GOOGLE_KEY}
        for _ in range(60):
            node = {"next": node}
        out = redact(node)
        self.assertEqual(scan_for_secrets(out), [],
                         "a depth-limited walk must not leave a key exposed")

    def test_scan_reports_the_pattern_names(self):
        found = scan_for_secrets({"a": FAKE_GOOGLE_KEY, "b": FAKE_OPENAI_KEY})
        self.assertIn("google_api_key", found)
        self.assertIn("openai_key", found)

    def test_store_refuses_a_record_carrying_a_credential(self):
        """The store is the last gate, independent of the recorder."""
        store = _memory_store()
        rec = AuditRecorder.begin({"scenario_id": 1}, origin="t")
        rec.record(Stage.INPUT, StageStatus.OK, "ok", {})
        record = rec.build()

        # Bypass both the recorder and StageEntry validation to construct the
        # situation the store must refuse.
        poisoned = record.entries[0].model_copy(
            update={"payload": {"leak": FAKE_GOOGLE_KEY}},
        )
        tampered = AuditRecord(
            header=record.header, entries=[poisoned], outcome=record.outcome,
        )
        with self.assertRaises(AuditStoreError) as ctx:
            store.save(tampered)
        self.assertIn("credential", str(ctx.exception))
        store.close()

    def test_stage_entry_rejects_an_unredacted_payload(self):
        with self.assertRaises(Exception):
            StageEntry(
                seq=1, stage=Stage.LLM, status=StageStatus.OK,
                recorded_at="2026-01-01T00:00:00+00:00", summary="s",
                payload={"k": FAKE_GOOGLE_KEY}, payload_sha256="x",
                prev_hash=GENESIS_HASH, entry_hash="y",
            )

    def test_llm_identity_never_carries_the_key(self):
        cfg = AgentConfig(gemini_api_key=FAKE_GOOGLE_KEY)
        identity = llm_identity(cfg)
        blob = json.dumps(identity)
        self.assertNotIn(FAKE_GOOGLE_KEY, blob)
        self.assertNotIn("AIza", blob)
        self.assertTrue(identity["api_key_present"])
        self.assertEqual(identity["api_key_source"], "config.gemini_api_key")
        self.assertFalse(identity["api_key_value_recorded"])

    def test_llm_identity_records_no_hash_of_the_key(self):
        """A hash would still let a holder of a candidate key confirm it."""
        cfg = AgentConfig(gemini_api_key=FAKE_GOOGLE_KEY)
        blob = json.dumps(llm_identity(cfg))
        for digest in (sha256_hex(FAKE_GOOGLE_KEY),
                       sha256_hex(FAKE_GOOGLE_KEY)[:16]):
            self.assertNotIn(digest, blob)

    def test_truncation_is_explicit_and_keeps_the_full_hash(self):
        text = "x" * 50_000
        out = truncate_text(text, limit=1000)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["chars"], 50_000)
        self.assertEqual(len(out["text"]), 1000)
        self.assertEqual(out["sha256"], sha256_hex(text),
                         "the hash must cover the FULL text, not the excerpt")


# ═══════════════════════════════════════════════════════════════════════════
# 2 — IMMUTABILITY
# ═══════════════════════════════════════════════════════════════════════════

class TestAppendOnlyAPI(unittest.TestCase):

    def test_interface_exposes_no_mutation_method(self):
        forbidden = {"update", "delete", "remove", "upsert", "amend", "edit",
                     "set_status", "patch", "overwrite", "truncate"}
        present = {n for n in dir(AuditStore) if not n.startswith("_")}
        self.assertEqual(present & forbidden, set(),
                         f"AuditStore exposes mutation: {present & forbidden}")

    def test_saving_the_same_run_twice_is_refused(self):
        store = _memory_store()
        record = _simple_record(store)
        with self.assertRaises(ImmutabilityError):
            store.save(record)
        store.close()

    def test_recording_a_stage_twice_is_refused(self):
        rec = AuditRecorder.begin({"scenario_id": 1}, origin="t")
        rec.record(Stage.DETECTION, StageStatus.OK, "first", {})
        with self.assertRaises(ValueError):
            rec.record(Stage.DETECTION, StageStatus.OK, "second", {})

    def test_operator_decisions_may_repeat(self):
        """A run can accumulate several human decisions."""
        store = _memory_store()
        record = _simple_record(store)
        for i in range(3):
            store.append_operator_decision(record.run_id, OperatorDecisionInput(
                decision=OperatorDecisionType.COMMENT,
                operator_id=f"op{i}", rationale="note",
            ))
        reloaded = store.get(record.run_id)
        self.assertEqual(len(reloaded.operator_decisions()), 3)
        self.assertTrue(store.verify_chain(record.run_id).valid)
        store.close()

    def test_stage_entry_is_frozen(self):
        record = _simple_record()
        with self.assertRaises(Exception):
            record.entries[0].summary = "changed"

    def test_audit_record_is_frozen(self):
        record = _simple_record()
        with self.assertRaises(Exception):
            record.entries = []


class TestDatabaseLevelImmutability(unittest.TestCase):
    """The defence that survives someone bypassing this module entirely."""

    def setUp(self):
        self.store = _memory_store()
        self.record = _simple_record(self.store)
        self.conn = self.store.raw_connection()

    def tearDown(self):
        self.store.close()

    def test_update_is_aborted_on_every_audit_table(self):
        statements = [
            "UPDATE audit_entries SET summary = 'tampered'",
            "UPDATE audit_entries SET payload_json = '{}'",
            "UPDATE audit_entries SET entry_hash = 'x'",
            "UPDATE audit_runs SET provenance = 'REAL'",
            "UPDATE audit_runs SET input_sha256 = 'x'",
            "UPDATE audit_run_outcomes SET status = 'COMPLETED'",
        ]
        for sql in statements:
            with self.subTest(sql=sql[:44]):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.conn.execute(sql)

    def test_delete_is_aborted_on_every_audit_table(self):
        for table in ("audit_entries", "audit_runs", "audit_run_outcomes"):
            with self.subTest(table=table):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.conn.execute(f"DELETE FROM {table}")

    def test_insert_still_works(self):
        """Append-only means append, not read-only."""
        before = len(self.store.get(self.record.run_id).entries)
        self.store.append_operator_decision(
            self.record.run_id,
            OperatorDecisionInput(decision=OperatorDecisionType.ACKNOWLEDGED,
                                  operator_id="op", rationale="seen"),
        )
        after = len(self.store.get(self.record.run_id).entries)
        self.assertEqual(after, before + 1)

    def test_duplicate_sequence_number_is_rejected(self):
        """The (run_id, seq) primary key stops a forked chain."""
        row = self.conn.execute(
            "SELECT * FROM audit_entries WHERE run_id = ? AND seq = 1",
            (self.record.run_id,),
        ).fetchone()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO audit_entries (run_id, seq, stage, status, actor, "
                "recorded_at, duration_ms, summary, payload_json, "
                "payload_sha256, prev_hash, entry_hash) "
                "VALUES (?, 1, 'input', 'OK', 'SYSTEM', 'x', NULL, 's', '{}', "
                "'h', 'p', 'e')",
                (self.record.run_id,),
            )


class TestHashChain(unittest.TestCase):

    def test_a_fresh_chain_verifies(self):
        store = _memory_store()
        record = _simple_record(store)
        v = store.verify_chain(record.run_id)
        self.assertTrue(v.valid, v.problems)
        self.assertEqual(v.entry_count, len(record.entries))
        store.close()

    def test_first_entry_links_to_genesis(self):
        record = _simple_record()
        self.assertEqual(record.entries[0].prev_hash, GENESIS_HASH)

    def test_each_entry_links_to_its_predecessor(self):
        record = _simple_record()
        for previous, current in zip(record.entries, record.entries[1:]):
            self.assertEqual(current.prev_hash, previous.entry_hash)

    def test_a_modified_payload_is_detected(self):
        record = _simple_record()
        poisoned = record.entries[0].model_copy(
            update={"payload": {"reading_count": 9999}},
        )
        broken = AuditRecord(
            header=record.header,
            entries=[poisoned, *record.entries[1:]],
            outcome=record.outcome,
        )
        v = broken.verify()
        self.assertFalse(v.valid)
        self.assertTrue(any("payload hash" in p for p in v.problems))

    def test_a_removed_entry_is_detected(self):
        record = _simple_record()
        broken = AuditRecord(
            header=record.header,
            entries=[record.entries[0], *record.entries[2:]],
            outcome=record.outcome,
        )
        v = broken.verify()
        self.assertFalse(v.valid)
        self.assertTrue(any("sequence break" in p or "prev_hash" in p
                            for p in v.problems))

    def test_tampering_that_bypasses_the_triggers_is_still_detected(self):
        """Simulates a doctored file or a restored bad backup.

        The triggers are dropped so the UPDATE succeeds, which is exactly what an
        attacker with file access would achieve. The chain still catches it.
        """
        store = SQLiteAuditStore(db_path=":memory:")
        record = _simple_record(store)
        conn = store.raw_connection()
        conn.execute("DROP TRIGGER trg_audit_entries_no_update")
        conn.execute(
            "UPDATE audit_entries SET payload_json = ? "
            "WHERE run_id = ? AND seq = 1",
            (canonical_json({"reading_count": 4242}), record.run_id),
        )
        v = store.verify_chain(record.run_id)
        self.assertFalse(v.valid, "a direct file write went undetected")
        store.close()

    def test_the_stored_seal_matches_the_last_entry(self):
        store = _memory_store()
        record = _simple_record(store)
        reloaded = store.get(record.run_id)
        self.assertEqual(reloaded.outcome.final_hash,
                         reloaded.entries[-1].entry_hash)
        store.close()

    def test_run_ids_are_unique_and_time_ordered(self):
        ids = [generate_run_id() for _ in range(200)]
        self.assertEqual(len(set(ids)), len(ids), "run id collision")
        self.assertEqual(ids, sorted(ids),
                         "run ids must sort chronologically so listing needs "
                         "no secondary index")


# ═══════════════════════════════════════════════════════════════════════════
# 3 — HONEST ABSENCE
# ═══════════════════════════════════════════════════════════════════════════

class TestAbsenceIsRecorded(unittest.TestCase):

    def test_status_vocabulary_separates_the_kinds_of_absence(self):
        for name in ("OK", "DEGRADED", "FAILED", "SKIPPED", "NOT_RUN",
                     "NOT_IMPLEMENTED"):
            with self.subTest(status=name):
                self.assertTrue(hasattr(StageStatus, name))

    def test_only_ok_and_degraded_count_as_success(self):
        self.assertTrue(StageStatus.OK.is_evidence_of_success)
        self.assertTrue(StageStatus.DEGRADED.is_evidence_of_success)
        for status in (StageStatus.FAILED, StageStatus.SKIPPED,
                       StageStatus.NOT_RUN, StageStatus.NOT_IMPLEMENTED):
            with self.subTest(status=status):
                self.assertFalse(status.is_evidence_of_success)

    def test_an_unrecorded_stage_reads_as_not_run_never_as_ok(self):
        record = _simple_record()
        self.assertIs(record.stage_status(Stage.LLM), StageStatus.NOT_RUN)
        self.assertIsNone(record.stage(Stage.LLM))

    def test_absent_capabilities_are_not_implemented_not_missing(self):
        record = _simple_record()
        for stage in (Stage.STATE_ESTIMATION, Stage.PHYSICS_VALIDATION):
            with self.subTest(stage=stage):
                entry = record.stage(stage)
                self.assertIsNotNone(entry, "the stage must be present")
                self.assertIs(entry.status, StageStatus.NOT_IMPLEMENTED)
                self.assertFalse(entry.payload["implemented"])
                self.assertIn("not evidence", entry.payload["claim"].lower())

    def test_a_stage_that_did_not_run_cannot_report_a_duration(self):
        for status in (StageStatus.NOT_IMPLEMENTED, StageStatus.NOT_RUN):
            with self.subTest(status=status):
                with self.assertRaises(Exception):
                    StageEntry(
                        seq=1, stage=Stage.PHYSICS_VALIDATION, status=status,
                        recorded_at="2026-01-01T00:00:00+00:00",
                        duration_ms=5.0, summary="s", payload={},
                        payload_sha256="x", prev_hash=GENESIS_HASH,
                        entry_hash="y",
                    )

    def test_coverage_reports_every_stage(self):
        coverage = _simple_record().coverage()
        self.assertEqual(set(coverage), {s.value for s in Stage})

    def test_skipped_safety_is_distinguishable_from_passed_safety(self):
        rec = AuditRecorder.begin({"scenario_id": 1}, origin="t")
        from app.agent.agent import _audit_record_safety

        _audit_record_safety(rec, None, None, skipped=True)
        entry = rec.entries[-1]
        self.assertIs(entry.status, StageStatus.SKIPPED)
        self.assertEqual(entry.payload["safety_status"], "NOT_VALIDATED")
        self.assertIn("NOT checked", entry.payload["claim"])


# ═══════════════════════════════════════════════════════════════════════════
# 4 — COMPLETENESS: all 20 required fields
# ═══════════════════════════════════════════════════════════════════════════

class TestAllRequiredFieldsArePersisted(unittest.TestCase):
    """One assertion per numbered Phase 4 requirement, on a real run."""

    @classmethod
    def setUpClass(cls):
        cls.store = _memory_store()
        scenario = next(
            s for s in get_all_scenarios() if s.get("scenario_id") == 1
        )
        dump = with_canonical_window(scenario)
        agent = SentinelAgent(AgentConfig(
            mode=ModelMode.STUB, stub_response=_stub_response(),
            stub_label="phase4-test",
        ))
        cls.recorder = AuditRecorder.begin(
            dump, origin="tests.test_phase4_audit",
            provenance_override=Provenance.DEMO.value,
        )
        list(agent.analyze_crash_dump_stream(dump, recorder=cls.recorder))
        cls.record = cls.recorder.finalize(
            store=cls.store, status=RunStatus.COMPLETED,
        )
        cls.store.append_operator_decision(
            cls.record.run_id,
            OperatorDecisionInput(
                decision=OperatorDecisionType.APPROVED, operator_id="op.1",
                rationale="Reversible and within margins.",
                step_number=1, command="CMD_GYRO_A_DRIVER_RESET",
            ),
        )
        cls.reloaded = cls.store.get(cls.record.run_id)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()

    def payload(self, stage: Stage) -> dict:
        entry = self.reloaded.stage(stage)
        self.assertIsNotNone(entry, f"{stage.value} was not recorded")
        return entry.payload

    def test_00_run_id(self):
        self.assertTrue(self.reloaded.run_id.startswith("run_"))
        self.assertEqual(self.reloaded.header.audit_schema_version,
                         AUDIT_SCHEMA_VERSION)

    def test_01_input_telemetry(self):
        telemetry = self.payload(Stage.INPUT)["telemetry"]
        self.assertGreater(len(telemetry), 0)
        first = telemetry[0]
        for field in ("parameter", "timestamp", "value", "status"):
            self.assertIn(field, first)

    def test_02_input_provenance(self):
        payload = self.payload(Stage.INPUT)
        self.assertEqual(payload["run_provenance"], Provenance.DEMO.value)
        self.assertEqual(payload["declared_provenance"],
                         Provenance.SYNTHETIC.value)
        self.assertTrue(payload["provenance_differs"],
                        "a DEMO replay of synthetic data must record both facts")

    def test_03_scenario_id(self):
        self.assertEqual(self.reloaded.header.scenario_id, 1)
        self.assertEqual(self.payload(Stage.INPUT)["scenario_id"], 1)

    def test_04_detection_results(self):
        payload = self.payload(Stage.DETECTION)
        self.assertIn("anomalies", payload)
        self.assertGreater(payload["anomaly_count"], 0)
        anomaly = payload["anomalies"][0]
        for field in ("anomaly_id", "channel", "timestamp", "detector", "score",
                      "threshold", "severity", "evidence", "provenance"):
            self.assertIn(field, anomaly)

    def test_05_state_estimation_results(self):
        """Phase 7 replaced the NOT_IMPLEMENTED placeholder with a real result.

        This fixture is preset scenario 1, whose telemetry window is sparse
        enough that no residual can be decided, so the stage records DEGRADED.
        All three statuses mean different things and the distinction is the point:
        NOT_IMPLEMENTED means no such capability exists, DEGRADED means the stage
        ran and could check nothing, OK means checks were actually made.
        """
        entry = self.reloaded.stage(Stage.STATE_ESTIMATION)
        self.assertIsNotNone(entry)
        self.assertIsNot(
            entry.status, StageStatus.NOT_IMPLEMENTED,
            "Phase 7 implements state estimation, so recording it as "
            "NOT_IMPLEMENTED would understate the build",
        )
        self.assertIn(entry.status, (StageStatus.OK, StageStatus.DEGRADED))

        payload = entry.payload
        self.assertIn("residual_report", payload)
        self.assertIn("state_estimate", payload)
        self.assertFalse(
            payload["uses_llm"],
            "state estimation must not consult a language model",
        )
        self.assertTrue(payload["runs_before_llm"])
        self.assertFalse(payload["flight_qualified"])

        report = payload["residual_report"]
        for field in ("residuals", "assumed_parameters", "limitations",
                      "physically_consistent", "summary"):
            self.assertIn(field, report)
        self.assertTrue(
            report["assumed_parameters"],
            "the assumptions a residual depends on must travel with it",
        )

        if entry.status is StageStatus.DEGRADED:
            self.assertIsNone(
                report["physically_consistent"],
                "nothing was decidable for this window, so consistency must "
                "read as unknown rather than True",
            )

    def test_06_fault_hypotheses(self):
        payload = self.payload(Stage.HYPOTHESES)
        self.assertEqual(len(payload["hypotheses"]), 3)
        self.assertEqual(payload["generated_by"], "LLM")
        self.assertFalse(payload["is_validated_diagnosis"],
                         "hypotheses must never be recorded as a validated "
                         "diagnosis")

    def test_07_rag_retrieval_results(self):
        payload = self.payload(Stage.RAG)
        self.assertGreater(payload["snippet_count"], 0)
        self.assertIn("query", payload)
        self.assertEqual(len(payload["snippet_hashes"]), payload["snippet_count"])

    def test_08_retrieved_sources(self):
        payload = self.payload(Stage.RAG)
        self.assertTrue(payload["sources_available"])
        self.assertEqual(len(payload["sources"]), payload["snippet_count"])
        source = payload["sources"][0]
        for field in ("source_kind", "identifier", "content_sha256"):
            self.assertIn(field, source)

    def test_09_llm_provider(self):
        self.assertIn("provider", self.payload(Stage.LLM))

    def test_10_llm_model(self):
        self.assertIn("model", self.payload(Stage.LLM))

    def test_11_llm_mode(self):
        self.assertEqual(self.payload(Stage.LLM)["mode"], "stub")

    def test_12_prompt_version(self):
        payload = self.payload(Stage.LLM)
        self.assertEqual(payload["prompt_version"], PROMPT_VERSION)
        self.assertEqual(payload["prompt_fingerprint"], prompt_fingerprint())
        self.assertFalse(payload["is_override"])

    def test_13_llm_output(self):
        payload = self.payload(Stage.LLM)
        self.assertGreater(len(payload["raw_responses"]), 0)
        self.assertIn("text", payload["raw_responses"][0])
        self.assertIn("ADCS_GYRO_SEU", payload["raw_responses"][0]["text"])

    def test_14_safety_validation(self):
        payload = self.payload(Stage.SAFETY_VALIDATION)
        self.assertIn("safety_status", payload)
        self.assertIn("approved_commands", payload)
        self.assertIn("blocked_steps", payload)

    def test_15_physics_validation(self):
        """Phase 8 replaced the NOT_IMPLEMENTED placeholder with real verdicts.

        Like state estimation, the status depends on whether this window let any
        constraint be decided — OK when a hypothesis was corroborated or
        contradicted, DEGRADED when every verdict came back UNCERTAIN. What must
        never appear again is NOT_IMPLEMENTED.
        """
        entry = self.reloaded.stage(Stage.PHYSICS_VALIDATION)
        self.assertIsNotNone(entry)
        self.assertIsNot(
            entry.status, StageStatus.NOT_IMPLEMENTED,
            "Phase 8 implements physics validation, so recording it as "
            "NOT_IMPLEMENTED would understate the build",
        )
        self.assertIn(entry.status, (StageStatus.OK, StageStatus.DEGRADED))

        payload = entry.payload
        self.assertIn("physics_report", payload)
        self.assertFalse(
            payload["uses_llm"],
            "physics validation must not consult a language model",
        )
        self.assertFalse(
            payload["llm_can_override"],
            "the record must state explicitly that a model cannot override a "
            "verdict",
        )
        self.assertTrue(payload["runs_on_deterministic_candidates"])

        report = payload["physics_report"]
        for field in ("verdicts", "model_version", "assumed_parameters",
                      "invalidated", "validated", "uncertain"):
            self.assertIn(field, report)

    def test_16_final_diagnosis(self):
        payload = self.payload(Stage.DIAGNOSIS)
        self.assertIn("sentinel_output", payload)
        self.assertIn("hypotheses", payload["sentinel_output"])

    def test_17_recommended_actions(self):
        payload = self.payload(Stage.DIAGNOSIS)
        self.assertIn("recommended_actions", payload)
        self.assertIn("Recommendation only", payload["authority"])

    def test_18_operator_decisions(self):
        decisions = self.reloaded.operator_decisions()
        self.assertEqual(len(decisions), 1)
        entry = decisions[0]
        self.assertIs(entry.actor, Actor.OPERATOR)
        self.assertEqual(entry.payload["decision"], "APPROVED")
        self.assertEqual(entry.payload["operator_id"], "op.1")
        self.assertIn("rationale", entry.payload)

    def test_19_timestamps(self):
        self.assertTrue(self.reloaded.header.started_at)
        self.assertTrue(self.reloaded.outcome.finished_at)
        for entry in self.reloaded.entries:
            with self.subTest(seq=entry.seq):
                self.assertRegex(entry.recorded_at, r"^\d{4}-\d{2}-\d{2}T")

    def test_20_processing_duration(self):
        self.assertGreater(self.reloaded.outcome.total_duration_ms or 0, 0)
        timed = [e for e in self.reloaded.entries if e.duration_ms is not None]
        self.assertGreater(len(timed), 0, "no stage reported a measured duration")

    # ── properties of the whole record ─────────────────────────────────────

    def test_the_blocked_command_is_visible_in_the_record(self):
        """The record must show what the model asked for AND what was refused.

        Storing only the post-validation output would hide the case this
        architecture exists to expose. In the Phase 10 constrained pipeline
        the invented command is refused at the guardrail layer (the LLM may
        not generate spacecraft commands); the raw response with the invented
        command AND the guardrail refusal must both be on record.
        """
        llm_payload = self.payload(Stage.LLM)
        proposed = json.loads(llm_payload["raw_responses"][0]["text"])
        proposed_commands = {s["command"] for s in proposed["recovery_plan"]}
        self.assertIn("CMD_TOTALLY_INVENTED_COMMAND", proposed_commands)

        violations = llm_payload.get("guardrail_violations", [])
        self.assertTrue(violations, "guardrail refusal must be on record")
        self.assertTrue(
            any("command" in v for v in violations),
            "the invented command must be refused by the command guardrail",
        )

        safety = self.payload(Stage.SAFETY_VALIDATION)
        self.assertIn("blocked_steps", safety)
        self.assertNotIn(
            "CMD_TOTALLY_INVENTED_COMMAND",
            {b["command"] for b in safety["blocked_steps"]},
        )
        self.assertNotIn(
            "CMD_TOTALLY_INVENTED_COMMAND",
            safety["approved_commands"],
        )

        final = self.payload(Stage.DIAGNOSIS)["sentinel_output"]
        self.assertNotIn(
            "CMD_TOTALLY_INVENTED_COMMAND",
            {s["command"] for s in final["recovery_plan"]},
        )

    def test_the_record_contains_no_credential(self):
        self.assertEqual(scan_for_secrets(self.reloaded.model_dump()), [])

    def test_the_stub_run_does_not_claim_a_model_was_called(self):
        payload = self.payload(Stage.LLM)
        self.assertFalse(payload["inference_performed"])
        self.assertEqual(payload["provider"], "none_stubbed_response")
        self.assertIn("No language model was called", payload["claim"])

    def test_every_stage_ran_or_says_why_not(self):
        coverage = dict(self.reloaded.coverage())

        # state_estimation runs as of Phase 7 and physics_validation as of
        # Phase 8. Whether each reaches OK or DEGRADED depends on whether this
        # window supported a decidable result, so the assertion is on the set of
        # acceptable outcomes rather than on one — what must never appear for
        # either is NOT_IMPLEMENTED.
        self.assertIn(coverage.pop("state_estimation"), ("OK", "DEGRADED"))
        self.assertIn(coverage.pop("physics_validation"), ("OK", "DEGRADED"))

        expected = {
            "input": "OK", "detection": "OK",
            "rag": "OK", "llm": "OK",
            "hypotheses": "OK",
            "safety_validation": "OK", "diagnosis": "OK",
            "operator_decision": "OK",
        }
        self.assertEqual(coverage, expected)

    def test_no_stage_is_recorded_as_not_implemented_any_more(self):
        """Every stage in the enum now has an implementation.

        Asserted as a standalone guarantee because it is the Phase 8 milestone:
        the audit trail no longer has to tell a reader that a check is missing.
        It can still tell them a check did not conclude — that is DEGRADED — and
        the distinction is the whole point of keeping both statuses.
        """
        for entry in self.reloaded.entries:
            self.assertIsNot(
                entry.status, StageStatus.NOT_IMPLEMENTED,
                f"{entry.stage.value} is still recorded as NOT_IMPLEMENTED",
            )

    def test_the_persisted_record_matches_what_was_built(self):
        self.assertEqual(
            [e.entry_hash for e in self.reloaded.entries][:len(
                self.record.entries)],
            [e.entry_hash for e in self.record.entries],
        )

    def test_the_chain_verifies_on_disk(self):
        self.assertTrue(self.store.verify_chain(self.record.run_id).valid)


# ═══════════════════════════════════════════════════════════════════════════
# 5 — PROVENANCE
# ═══════════════════════════════════════════════════════════════════════════

class TestProvenancePreserved(unittest.TestCase):

    REQUIRED = ("REAL", "SYNTHETIC", "SYNTHETIC_FROM_REAL_METADATA", "DEMO")

    def test_all_four_required_codes_exist(self):
        values = {p.value for p in Provenance}
        for code in self.REQUIRED:
            with self.subTest(code=code):
                self.assertIn(code, values)

    def test_demo_has_its_own_label_distinct_from_synthetic(self):
        self.assertNotEqual(display_label(Provenance.DEMO),
                            display_label(Provenance.SYNTHETIC))
        self.assertIn("DEMO", display_label(Provenance.DEMO))

    def test_header_round_trips_every_code(self):
        store = _memory_store()
        for code in self.REQUIRED + ("UNKNOWN",):
            with self.subTest(code=code):
                record = _simple_record(store, provenance=code)
                reloaded = store.get(record.run_id)
                self.assertEqual(reloaded.header.provenance, code)
                self.assertEqual(reloaded.header.source_type,
                                 display_label(code))
        store.close()

    def test_an_unrecognised_provenance_becomes_unknown_not_real(self):
        for bogus in ("real", "Real ESA Telemetry", "", "TOTALLY_MADE_UP", None):
            with self.subTest(value=bogus):
                rec = AuditRecorder.begin({"provenance": bogus}, origin="t")
                self.assertEqual(rec.header.provenance, "UNKNOWN")

    def test_source_type_is_derived_not_authored(self):
        rec = AuditRecorder.begin(
            {"provenance": "SYNTHETIC", "source_type": "Real ESA Telemetry"},
            origin="t",
        )
        self.assertEqual(rec.header.source_type,
                         display_label(Provenance.SYNTHETIC))

    def test_listing_can_filter_by_provenance(self):
        store = _memory_store()
        _simple_record(store, provenance="DEMO")
        _simple_record(store, provenance="SYNTHETIC")
        _simple_record(store, provenance="SYNTHETIC")
        self.assertEqual(len(store.list_runs(provenance="DEMO")), 1)
        self.assertEqual(len(store.list_runs(provenance="SYNTHETIC")), 2)
        self.assertEqual(store.count_runs(), 3)
        store.close()

    def test_normalize_is_the_single_gate(self):
        self.assertEqual(normalize("DEMO"), "DEMO")
        self.assertEqual(normalize("  DEMO  "), "DEMO",
                         "surrounding whitespace is a transport artifact")
        self.assertEqual(normalize("nonsense"), "UNKNOWN")

    def test_normalize_is_case_sensitive_and_agrees_with_the_frontend(self):
        """Regression: the backend used to accept case variants.

        ``normalize()`` uppercased its input, so a payload declaring "real" was
        promoted to REAL. The frontend's normalizeProvenance() has always been an
        exact lookup, so the browser showed PROVENANCE UNKNOWN for the same
        payload. The two sides disagreed about the field carrying the strongest
        claim in the system, and the backend held the more permissive position.
        """
        for variant in ("real", "Real", "rEaL", "synthetic", "Demo"):
            with self.subTest(value=variant):
                self.assertEqual(normalize(variant), "UNKNOWN")

        # The exact codes still resolve.
        for code in self.REQUIRED + ("UNKNOWN",):
            with self.subTest(code=code):
                self.assertEqual(normalize(code), code)

    def test_backend_and_frontend_normalization_agree(self):
        """Compare against the generated contract the browser actually loads."""
        import re

        contract = (
            _BACKEND.parent.parent / "contracts" / "frontend" / "contract.js"
        ).read_text(encoding="utf-8")
        match = re.search(
            r"export const PROVENANCE_LABELS = Object\.freeze\(\{(.*?)\}\);",
            contract, re.DOTALL,
        )
        self.assertIsNotNone(match)
        frontend_codes = set(re.findall(r"(\w+):\s*\"", match.group(1)))
        backend_codes = {p.value for p in Provenance}
        self.assertEqual(frontend_codes, backend_codes)


# ═══════════════════════════════════════════════════════════════════════════
# 6 — NO FABRICATION BY A CLIENT
# ═══════════════════════════════════════════════════════════════════════════

class TestClientCannotFabricateRecords(unittest.TestCase):

    def test_operator_input_forbids_extra_fields(self):
        for smuggled in ("stage", "actor", "seq", "entry_hash", "prev_hash",
                         "recorded_at", "payload", "status", "duration_ms",
                         "run_id"):
            with self.subTest(field=smuggled):
                with self.assertRaises(Exception):
                    OperatorDecisionInput(
                        decision=OperatorDecisionType.APPROVED,
                        operator_id="op", rationale="because",
                        **{smuggled: "anything"},
                    )

    def test_operator_input_requires_a_rationale(self):
        with self.assertRaises(Exception):
            OperatorDecisionInput(
                decision=OperatorDecisionType.APPROVED, operator_id="op",
                rationale="",
            )

    def test_the_server_stamps_actor_stage_and_sequence(self):
        store = _memory_store()
        record = _simple_record(store)
        entry = store.append_operator_decision(
            record.run_id,
            OperatorDecisionInput(decision=OperatorDecisionType.REJECTED,
                                  operator_id="op", rationale="no"),
        )
        self.assertIs(entry.actor, Actor.OPERATOR)
        self.assertIs(entry.stage, Stage.OPERATOR_DECISION)
        self.assertEqual(entry.seq, len(record.entries) + 1)
        self.assertTrue(entry.payload["server_stamped"])
        store.close()

    def test_system_stages_are_marked_system_only(self):
        from app.audit.record import SYSTEM_ONLY_STAGES

        for stage in Stage:
            with self.subTest(stage=stage):
                if stage is Stage.OPERATOR_DECISION:
                    self.assertNotIn(stage, SYSTEM_ONLY_STAGES)
                else:
                    self.assertIn(stage, SYSTEM_ONLY_STAGES)

    def test_a_decision_on_an_unknown_run_is_refused(self):
        store = _memory_store()
        with self.assertRaises(RunNotFoundError):
            store.append_operator_decision(
                "run_does_not_exist",
                OperatorDecisionInput(decision=OperatorDecisionType.COMMENT,
                                      operator_id="op", rationale="hi"),
            )
        store.close()

    def test_the_api_exposes_no_record_write_endpoint(self):
        """The only client write path is the decisions endpoint."""
        import app.main as main

        writable = []
        for route in main.app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", set()) or set()
            if not path.startswith("/api/v1/runs"):
                continue
            if methods & {"POST", "PUT", "PATCH", "DELETE"}:
                writable.append((sorted(methods), path))
        self.assertEqual(
            writable, [(["POST"], "/api/v1/runs/{run_id}/decisions")],
            f"unexpected write route(s) on the audit API: {writable}",
        )

    def test_no_endpoint_accepts_an_audit_record_body(self):
        import app.main as main

        for route in main.app.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/v1/runs"):
                continue
            body_field = getattr(route, "body_field", None)
            if body_field is None:
                continue
            annotation = getattr(body_field, "type_", None)
            with self.subTest(path=path):
                self.assertIsNot(
                    annotation, AuditRecord,
                    f"{path} accepts an AuditRecord from the client",
                )
                self.assertIsNot(annotation, StageEntry)


# ═══════════════════════════════════════════════════════════════════════════
# 7 — PORTABILITY
# ═══════════════════════════════════════════════════════════════════════════

class TestPostgresPortability(unittest.TestCase):
    """The migration claim is checked, not just asserted."""

    SQLITE_ONLY = (
        "AUTOINCREMENT", "WITHOUT ROWID", "PRAGMA", "sqlite_",
        " BLOB", "INTEGER PRIMARY KEY AUTOINCREMENT",
    )

    def test_table_ddl_uses_no_sqlite_specific_construct(self):
        for statement in _TABLE_DDL:
            upper = statement.upper()
            for token in self.SQLITE_ONLY:
                with self.subTest(token=token, ddl=statement[:44]):
                    self.assertNotIn(token.upper(), upper)

    def test_table_ddl_uses_only_portable_column_types(self):
        allowed = {"TEXT", "INTEGER", "REAL"}
        for statement in _TABLE_DDL:
            if "CREATE TABLE" not in statement.upper():
                continue
            for line in statement.splitlines():
                parts = line.strip().rstrip(",").split()
                if len(parts) < 2 or parts[0].upper() in (
                    "CREATE", "PRIMARY", ")", "("
                ):
                    continue
                declared = parts[1].upper()
                if declared in ("KEY",):
                    continue
                with self.subTest(line=line.strip()):
                    self.assertIn(declared, allowed)

    def test_the_interface_declares_a_placeholder_hook(self):
        """psycopg needs %s where sqlite3 needs ?."""
        self.assertTrue(hasattr(AuditStore, "_placeholder"))
        self.assertEqual(SQLiteAuditStore._placeholder(), "?")

    def test_immutability_ddl_is_overridable(self):
        self.assertTrue(hasattr(SQLiteAuditStore, "_immutability_ddl"))
        self.assertTrue(hasattr(SQLiteAuditStore, "_configure_connection"))

    def test_timestamps_are_stored_as_sortable_text(self):
        store = _memory_store()
        record = _simple_record(store)
        row = store.raw_connection().execute(
            "SELECT started_at FROM audit_runs WHERE run_id = ?",
            (record.run_id,),
        ).fetchone()
        self.assertIsInstance(row["started_at"], str)
        self.assertRegex(row["started_at"], r"^\d{4}-\d{2}-\d{2}T.*[+-]\d{2}:\d{2}$")
        store.close()

    def test_no_aggregate_is_stored_redundantly(self):
        """entry_count and final_hash are derived, which is what removes the
        need for an UPDATE when appending."""
        columns = set()
        for statement in _TABLE_DDL:
            for line in statement.splitlines():
                parts = line.strip().split()
                if parts:
                    columns.add(parts[0].lower())
        self.assertNotIn("entry_count", columns)
        self.assertNotIn("final_hash", columns)


# ═══════════════════════════════════════════════════════════════════════════
# 8 — SUPPORTING BEHAVIOUR
# ═══════════════════════════════════════════════════════════════════════════

class TestRagTraceMatchesUntraced(unittest.TestCase):
    """The audit record must describe the text the LLM actually received."""

    CASES = (
        ("ADCS_ERROR ADCS_GYRO_SEU", ["Gyro_rate_degs", "SEU_counter"]),
        ("EPS_UNDER_VOLT EPS_SOLAR_UNDERVOLT", ["V_bat"]),
        ("", None),
        ("query matching nothing at all", ["ZZZ_NO_SUCH_CHANNEL"]),
    )

    def test_snippets_are_identical(self):
        for query, cues in self.CASES:
            for top_k in (1, 3, 5):
                with self.subTest(query=query[:24], top_k=top_k):
                    plain = retrieve_procedures(
                        query=query, fault_cues=cues, top_k=top_k,
                        use_pdf_rag=False)
                    traced, _ = retrieve_procedures_traced(
                        query=query, fault_cues=cues, top_k=top_k,
                        use_pdf_rag=False)
                    self.assertEqual(plain, traced)

    def test_one_source_per_snippet(self):
        for query, cues in self.CASES:
            with self.subTest(query=query[:24]):
                snippets, trace = retrieve_procedures_traced(
                    query=query, fault_cues=cues, top_k=3, use_pdf_rag=False)
                self.assertEqual(len(trace["sources"]), len(snippets))

    def test_trace_records_the_backend_and_the_query(self):
        _, trace = retrieve_procedures_traced(
            query="ADCS_GYRO_SEU", fault_cues=["SEU_counter"], top_k=2,
            use_pdf_rag=False)
        self.assertEqual(trace["backend"], "fallback_kb")
        self.assertIn("SEU_counter", trace["query"])
        self.assertIn("rag_status", trace)


class TestPromptVersioning(unittest.TestCase):

    def test_a_version_and_a_fingerprint_both_exist(self):
        self.assertRegex(PROMPT_VERSION, r"^\d+\.\d+\.\d+$")
        self.assertRegex(prompt_fingerprint(), r"^[0-9a-f]{16}$")

    def test_the_fingerprint_changes_when_the_text_changes(self):
        self.assertNotEqual(prompt_fingerprint(), prompt_fingerprint("other"))

    def test_the_fingerprint_is_stable_for_the_same_text(self):
        self.assertEqual(prompt_fingerprint("abc"), prompt_fingerprint("abc"))

    def test_an_override_is_flagged(self):
        self.assertFalse(prompt_identity()["is_override"])
        self.assertTrue(prompt_identity("custom")["is_override"])


class TestStubModeExistsToAvoidMisattribution(unittest.TestCase):

    def test_stub_mode_is_available(self):
        self.assertEqual(ModelMode.STUB.value, "stub")

    def test_the_original_modes_are_untouched(self):
        self.assertEqual(ModelMode.BASE.value, "base")
        self.assertEqual(ModelMode.TUNED.value, "tuned")
        self.assertEqual(ModelMode.FALLBACK.value, "fallback")

    def test_stub_mode_refuses_to_invent_a_response(self):
        from app.agent.agent import LLMCallError

        agent = SentinelAgent(AgentConfig(mode=ModelMode.STUB))
        with self.assertRaises(LLMCallError):
            agent._call_stub()

    def test_stub_identity_states_no_inference_happened(self):
        identity = llm_identity(AgentConfig(
            mode=ModelMode.STUB, stub_response="x", stub_label="demo"))
        self.assertFalse(identity["inference_performed"])
        self.assertEqual(identity["provider"], "none_stubbed_response")
        self.assertEqual(identity["model"], "stub:demo")

    def test_live_modes_report_inference_performed(self):
        for mode in (ModelMode.BASE, ModelMode.TUNED, ModelMode.FALLBACK):
            with self.subTest(mode=mode):
                self.assertTrue(
                    llm_identity(AgentConfig(mode=mode))["inference_performed"]
                )


class TestFailurePathStillRecords(unittest.TestCase):

    def test_a_failed_llm_call_leaves_a_failed_entry(self):
        from app.agent.agent import AgentError

        store = _memory_store()
        scenario = next(
            s for s in get_all_scenarios() if s.get("scenario_id") == 1
        )
        # STUB with no response raises LLMCallError inside the pipeline.
        agent = SentinelAgent(AgentConfig(mode=ModelMode.STUB))
        recorder = AuditRecorder.begin(scenario, origin="failure-test")

        with self.assertRaises(AgentError):
            agent.analyze_crash_dump(scenario, recorder=recorder)

        record = recorder.finalize(store=store, status=RunStatus.FAILED,
                                   error="LLM unavailable")
        self.assertIs(record.stage_status(Stage.LLM), StageStatus.FAILED)
        self.assertIs(record.stage_status(Stage.DIAGNOSIS), StageStatus.NOT_RUN)
        self.assertEqual(record.outcome.status, RunStatus.FAILED)
        self.assertTrue(store.verify_chain(record.run_id).valid)
        store.close()

    def test_an_unpersistable_record_does_not_break_the_caller(self):
        """finalize() must not turn an audit failure into a lost diagnosis."""
        store = _memory_store()
        record = _simple_record(store)

        rec2 = AuditRecorder(record.header)   # same run_id -> duplicate
        rec2.record(Stage.INPUT, StageStatus.OK, "x", {})
        returned = rec2.finalize(store=store)

        self.assertIsNotNone(returned)
        self.assertIsNotNone(store.last_error)
        self.assertIn("already exists", store.last_error)
        store.close()


class TestAuditApiSurface(unittest.TestCase):

    EXPECTED = {
        ("POST", "/api/v1/analyze"),
        ("GET", "/api/v1/audit/status"),
        ("GET", "/api/v1/runs"),
        ("GET", "/api/v1/runs/{run_id}"),
        ("GET", "/api/v1/runs/{run_id}/verify"),
        ("POST", "/api/v1/runs/{run_id}/decisions"),
    }

    def test_all_endpoints_are_registered(self):
        import app.main as main

        registered = set()
        for route in main.app.routes:
            path = getattr(route, "path", "")
            for method in getattr(route, "methods", set()) or set():
                registered.add((method, path))
        for expected in self.EXPECTED:
            with self.subTest(endpoint=expected):
                self.assertIn(expected, registered)

    def test_read_endpoints_declare_response_models(self):
        import app.main as main
        from app.audit import (
            AuditStatusResponse, ChainVerification, OperatorDecisionAccepted,
            RunListResponse,
        )

        expected = {
            "/api/v1/audit/status": AuditStatusResponse,
            "/api/v1/runs": RunListResponse,
            "/api/v1/runs/{run_id}": AuditRecord,
            "/api/v1/runs/{run_id}/verify": ChainVerification,
            "/api/v1/runs/{run_id}/decisions": OperatorDecisionAccepted,
        }
        for route in main.app.routes:
            path = getattr(route, "path", "")
            if path in expected:
                with self.subTest(path=path):
                    self.assertIs(
                        getattr(route, "response_model", None), expected[path],
                    )

    def test_analyze_v1_publishes_the_run_id_header(self):
        """A client must be able to learn the run id before the body streams."""
        source = (_BACKEND / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("X-Sentinel-Run-Id", source)
        self.assertIn("Access-Control-Expose-Headers", source)

    def test_a_client_disconnect_is_recorded_as_abandoned(self):
        source = (_BACKEND / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("GeneratorExit", source)
        self.assertIn("RunStatus.ABANDONED", source)


class TestStoreListing(unittest.TestCase):

    def test_newest_first(self):
        store = _memory_store()
        ids = [_simple_record(store).run_id for _ in range(4)]
        listed = [s.run_id for s in store.list_runs()]
        self.assertEqual(listed, list(reversed(ids)))
        store.close()

    def test_pagination(self):
        store = _memory_store()
        for _ in range(7):
            _simple_record(store)
        self.assertEqual(len(store.list_runs(limit=3)), 3)
        self.assertEqual(len(store.list_runs(limit=3, offset=6)), 1)
        self.assertEqual(len(store.list_runs(limit=3, offset=99)), 0)
        store.close()

    def test_limit_is_clamped(self):
        store = _memory_store()
        _simple_record(store)
        self.assertEqual(len(store.list_runs(limit=100_000)), 1)
        self.assertEqual(len(store.list_runs(limit=0)), 1)
        store.close()

    def test_unknown_run_returns_none_rather_than_raising(self):
        store = _memory_store()
        self.assertIsNone(store.get("run_nope"))
        with self.assertRaises(RunNotFoundError):
            store.verify_chain("run_nope")
        store.close()

    def test_scenario_filter(self):
        store = _memory_store()
        _simple_record(store)
        rec = AuditRecorder.begin({"scenario_id": 42}, origin="t")
        rec.record(Stage.INPUT, StageStatus.OK, "x", {})
        rec.finalize(store=store)
        self.assertEqual(len(store.list_runs(scenario_id=42)), 1)
        self.assertEqual(len(store.list_runs(scenario_id=1)), 1)
        store.close()


class TestCanonicalJsonDeterminism(unittest.TestCase):
    """Hashing depends on this; a non-deterministic dump breaks verification."""

    def test_key_order_does_not_change_the_serialization(self):
        a = {"b": 1, "a": 2, "c": {"z": 1, "y": 2}}
        b = {"c": {"y": 2, "z": 1}, "a": 2, "b": 1}
        self.assertEqual(canonical_json(a), canonical_json(b))

    def test_repeated_calls_are_stable(self):
        payload = {"x": [1, 2, {"k": "v"}], "y": None, "z": 1.5}
        self.assertEqual(canonical_json(payload), canonical_json(payload))


if __name__ == "__main__":
    unittest.main(verbosity=2)
