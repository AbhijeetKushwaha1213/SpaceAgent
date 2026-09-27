# PHASE 1J — SECURITY INVARIANT CHECK — VERIFICATION REPORT

**Subject:** SENTINEL — Spacecraft Diagnostic Copilot
**Scope:** Phase 1 (Security, Demo Reachability & Release Hardening), sub-phase **1J**.
**Date:** 2026-08-23
**Method:** Map each architectural / security invariant to the authoritative
module that enforces it **and** to at least one passing automated test. No
invariant is asserted on prose alone; every row below is backed by a named test
that was executed and observed to pass in the run recorded in §2.

This report makes **scoped** claims only. It does **not** claim "all tests
pass", "fully verified", or "production ready". Known environmental caveats and
the exact boundary of what was verified are stated in §5.

---

## 1. Two robustness fixes made during 1J (each with a regression test)

The invariant probe surfaced two latent defects. Both were fixed with the
smallest change that restores the module's own documented contract; neither
weakens an authority or alters a test to pass.

| # | Defect | Fix | Regression test |
|---|---|---|---|
| 1 | `initialize_pdf_rag()` called `collection.count()` **unwrapped** — the only bare ChromaDB call in the function. A version-incompatible persisted index (`data/chroma_db`) raised `InternalError`, escaped, and hard-failed the RAG audit stage, defeating the module's documented "ChromaDB error → fallback KB / never returns an empty list" guarantee. | Wrapped `count()` exactly like its sibling ChromaDB calls: on exception set `_rag_status.available=False`, record `last_error`, `return False` → retrieval degrades to the always-available FALLBACK_KB. `app/agent/rag.py`. | `tests/test_phase29_rag_graceful_degradation.py` (2 tests, env-independent: monkeypatches a collection whose `count()` raises; asserts no raise + fallback retrieval still attributable) |
| 2 | The reconciliation audit block in `agent.py` referenced `Stage.RECONCILIATION`/`StageStatus` but — unlike every sibling audit block — never did the local `from app.audit import Stage, StageStatus`. When reconciliation was **enabled**, both the OK record and the DEGRADED fallback threw `NameError`, silently **dropping** the reconciliation audit entry. | Added the local import at the top of the reconciliation `try` block, matching the established per-block pattern. `app/agent/agent.py`. | `tests/test_phase30_reconciliation_audit_recorded.py` (1 test, env-independent: enables recon via monkeypatch, asserts the `RECONCILIATION` stage is actually persisted with `status==OK` and a payload) |

A version-incompatible persisted ChromaDB index was also **rebuilt** offline
(bundled ONNX all-MiniLM-L6-v2 embeddings; `pdf_count=2`, `chunk_count=214`),
which is what restores `test_phase17::test_pdf_rag_initialization_and_readable_retrieval`
to passing. The graceful-degradation fix improves the *failure* mode; the
rebuild restores the *live* PDF-RAG path.

---

## 2. Verification environment (CI-matching, reconciliation-disabled)

The local, git-ignored `.env` sets `RECONCILIATION_ENABLED=true`, and **two**
import-time `load_dotenv(override=True)` sites (`app/reconciliation/config.py`
and `app/main.py`) re-inject it, defeating `env -u`. To reproduce the
**default / CI** posture (reconciliation dormant), both override sites are
imported first and the flag is pinned last:

```bash
cd sentinel/backend
python3 -c "import app.reconciliation.config, app.main; import os; \
os.environ['RECONCILIATION_ENABLED']='false'; import pytest, sys; \
sys.exit(pytest.main([<security-spine files>, '-q']))"
```

**Observed result — security-spine suite (12 files):** `279 passed, 216 subtests
passed, 0 failed`, with `reconciliation_enabled()` confirmed `False`. This run
covers every test cited in the §4 matrix except the standalone repo-wide
`test_secret_scan.py` (I11), which scans the git tree rather than the pipeline
and was verified separately: `3 passed`.

Files: `test_phase8_physics.py`, `test_safety.py`, `test_phase14_security.py`,
`test_phase17_evidence_rag_safety.py`, `test_phase23_cloud_redaction.py`,
`test_phase25_adversarial_security.py`, `test_phase24_reconciliation_engine.py`,
`test_phase24_reconciliation_contract.py`, `test_phase27_demo_mode.py`,
`test_phase4_audit.py`, `test_phase29_rag_graceful_degradation.py`,
`test_phase30_reconciliation_audit_recorded.py`.

---

## 3. The authority spine (fixed, mode-independent)

From `app/startup_report.py` — these three lines are architectural invariants,
not runtime-variable state:

- **PHYSICS** — `AUTHORITY (deterministic; LLM cannot override)` → `app/validation/physics.py`
- **SAFETY** — `AUTHORITY (final recovery gate; fail-closed)` → `app/agent/safety.py`
- **LLM** — `ASSISTIVE (ranks & explains; non-authoritative)`

---

## 4. Invariant → authority → test matrix

| # | Invariant | Authoritative module(s) | Backing test(s) — all passing (§2) | Status |
|---|---|---|---|---|
| **I1** | **Physics is authority**: a deterministic physics refutation cannot be overridden by model confidence. | `app/validation/physics.py` | `test_phase25_adversarial_security::test_high_confidence_cannot_override_physics_refutation`; physics recorded with real verdicts + architectural isolation from the LLM: `test_phase8_physics::test_physics_validation_is_recorded_with_real_verdicts`, `::test_no_model_client_appears_in_the_physics_source`, `::test_physics_does_not_import_the_agent` | ✅ VERIFIED |
| **I2** | **Safety is the final recovery authority**: unsafe / unauthorized commands are blocked at the safety gate. | `app/agent/safety.py`, `app/validation/command_registry.py` | `test_phase25_adversarial_security::test_safety_interlock_blocks_unsafe_recovery_command`; `test_safety.py` suite; hallucinated-command drop: `test_phase4_audit` (`CMD_TOTALLY_INVENTED_COMMAND` absent from blocked/approved/final plan) | ✅ VERIFIED |
| **I3** | **LLM is non-authoritative**: model agreement cannot clear human review or decide a verdict. | `app/llm/ranker.py`, `app/agent/agent.py` | `test_phase25_adversarial_security::test_human_review_cannot_be_cleared_by_model_agreement`; `::test_high_confidence_cannot_override_physics_refutation` | ✅ VERIFIED |
| **I4** | **Reconciliation is deterministic**: order-independent, threshold-driven, versioned. | `app/reconciliation/engine.py`, `signals.py`, `config.py` | `test_phase24_reconciliation_engine::test_deterministic_ids_are_order_independent`; `::test_same_case_multi_signal_corroboration_merged` | ✅ VERIFIED |
| **I5** | **Correlation ≠ identity**: pairs are not merged on correlation alone; physics non-opposition required; recon never "overrides". | `app/reconciliation/engine.py`, `app/reconciliation/audit.py` | `test_phase24_reconciliation_engine::test_related_cases_preserved_separately_with_relationship`; `::test_completely_separate_cases_preserved`; `test_phase8_physics::test_reconcile_never_reports_an_override_whatever_is_claimed` | ✅ VERIFIED |
| **I6** | **Case isolation**: evidence from a separate case does not bleed across cases. | `app/reconciliation/isolation.py`, `app/reconciliation/rag_filter.py` | `test_phase24_reconciliation_contract::test_isolate_evidence_removes_separate_case_evidence`; `test_phase25_adversarial_security::test_isolation_boundary_filters_unrelated_evidence` | ✅ VERIFIED |
| **I7** | **RAG boundaries**: retrieval is advisory & attributable, degrades to the fallback KB, and never returns an empty list; RAG is never authoritative. | `app/agent/rag.py` | `test_phase17_evidence_rag_safety::test_pdf_rag_initialization_and_readable_retrieval`, `::test_evidence_ids_in_candidates_and_prompt`; `test_phase29_rag_graceful_degradation` (never-empty / fallback); `test_phase4_audit::test_07_rag_retrieval_results`, `::test_08_retrieved_sources` (attributable snippets + sources) | ✅ VERIFIED |
| **I8** | **Auditability**: append-only, hash-sealed, every stage recorded (OK/DEGRADED/NOT_RUN) — never silently dropped. | `app/audit/record.py`, `app/audit/store.py` | `test_phase4_audit`: `TestHashChain` (chain integrity/tamper detection), `TestAppendOnlyAPI`, `TestDatabaseLevelImmutability`, `::test_every_stage_ran_or_says_why_not`; recon-record-not-dropped: `test_phase30_reconciliation_audit_recorded` | ✅ VERIFIED |
| **I9** | **Local/cloud LLM separation**: a redaction gate runs before any cloud egress; local mode blocks cloud transmission; redaction failure fails closed. | `app/llm/cloud_branch.py`, `app/security/exfiltration.py` | `test_phase14_security::test_local_mode_blocks_cloud_transmission`, `::test_gemini_provider_refuses_when_llm_mode_is_local`, `::test_apply_cloud_redaction_strips_confidential_and_configured_params`, `::test_cloud_mode_records_external_transmission_in_audit`; fail-closed: `test_phase23_cloud_redaction::test_J_redaction_exception_fails_closed`, `::test_J_malformed_redactor_output_fails_closed` | ✅ VERIFIED |
| **I10** | **Fail-closed security posture**: no API key in production → fail closed (not open); startup validation exits non-zero for the misconfigured state. | `app/security/auth.py`, `app/security/config.py`, `app/startup_report.py` | `test_phase14_security::test_middleware_fails_closed_when_key_unconfigured`, `::test_middleware_requires_valid_key_when_configured`; `test_phase27_demo_mode::test_production_no_credentials_fails_closed`, `::test_configured_production_rejects_invalid_credentials` | ✅ VERIFIED |
| **I11** | **No credential leakage**: the secret never appears in audit records, logs, the startup report, or output; regression-scanned. | `app/security/exfiltration.py`, `app/audit/record.py`, `app/startup_report.py` | `test_phase27_demo_mode::test_startup_report_never_exposes_api_key_value`; `test_phase14_security::test_redact_log_message_masks_secrets`; `test_phase4_audit::test_the_record_contains_no_credential`; repo-wide `tests/test_secret_scan.py` | ✅ VERIFIED |
| **I12** | **Demo path preserves authority**: demo mode relaxes auth only — it does not touch physics, safety, or reconciliation authority. | `app/security/config.py`, `app/startup_report.py` | `test_phase27_demo_mode::test_demo_mode_preserves_physics_and_safety_authority`, `::test_demo_mode_does_not_enable_reconciliation`, `::test_demo_mode_serves_without_auth`, `::test_modes_are_distinct_and_labelled` | ✅ VERIFIED |

---

## 5. Honest caveats — boundary of what was verified

- **Reconciliation-disabled scope.** §2/§4 reflect the **default** posture
  (`RECONCILIATION_ENABLED=false`), which matches CI. With reconciliation
  **enabled**, the recon stage correctly records `OK` (fix #2) — which then
  legitimately conflicts with `test_phase4_audit::test_every_stage_ran`'s
  hard-coded `NOT_RUN` expectation. That expectation is valid only in the
  disabled default; the enabled-path behaviour is separately covered by
  `test_phase30`. This is an environment-posture distinction, not a defect.
- **Local `.env` contamination is real and documented.** Because a local `.env`
  sets `RECONCILIATION_ENABLED=true` and two `load_dotenv(override=True)` sites
  re-inject it, a naïve `pytest` run (or `env -u`) does **not** reproduce the CI
  posture. Use the recipe in §2. Two recon-flag tests fail under the raw local
  `.env`; that is contamination, not a code regression.
- **ChromaDB index compatibility is environmental.** The persisted index must be
  built by the installed chromadb version; a mismatched index raises at
  `count()`. Fix #1 makes that degrade gracefully; a live PDF-RAG path requires
  the rebuild noted in §1.
- **Sandbox-only failures.** In this sandbox, `test_phase11_sovereign_llm` /
  `test_phase12_evaluation` LocalMode tests ERROR on `socket.bind()`
  (`PermissionError`) — a sandbox networking restriction, not a code fault.
- **Not claimed here.** This report does not assert a clean *global* test run,
  production readiness, or that no issues exist anywhere. It asserts, with named
  passing tests, that the 12 invariants above hold in the verified posture.

---

## 6. Conclusion

All 12 security / architectural invariants are backed by named automated tests
observed passing in a reconciliation-disabled, CI-matching run (§2: 279 passed,
0 failed across the 12 security-spine files). The two robustness defects found
during the probe were fixed with contract-restoring changes and pinned by new
regression tests, without weakening any authority or modifying a test to pass.
Phase 1J is complete under the scope and caveats stated above.
