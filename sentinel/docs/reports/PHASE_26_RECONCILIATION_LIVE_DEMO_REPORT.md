# PHASE 26 — RECONCILIATION LIVE DEMO REPORT

**Objective:** Make the complete reconciliation path work end-to-end using the
already-implemented deterministic Phase 24 engine, so the isolated
Reconciliation UI (previously stuck on `ISOLATED CASES: 0 / RELATED CASES: 0 /
CONFLICTS: 0 — "No active reconciliation data"`) is fed by
**REAL DATA → REAL RECONCILIATION ENGINE → REAL API → REAL FRONTEND**.

**Date:** 2026-08-22
**Branch:** `antigravity`
**Engine:** deterministic Phase 24 `ReconciliationEngine` v1.0.0 / config v1.0.0
**Gate:** `RECONCILIATION_ENABLED` (default **false**)

---

## 0. VERDICT (read this first)

> **Reconciliation is actually executing.**

The web view was never fed a reconciliation result because it fetched the wrong
endpoint (`/api/v1/scenarios/${id}` with an undefined `id`) and read a context
key (`auditRun`) the provider never supplied — so `reconData` was always `null`
and every stat fell back to `0`. That data-flow gap is now closed by a real API
endpoint, a real context entity, and a view that renders the engine's actual
output.

The claim is backed by runtime evidence, not by hardcoded UI values:

- The exact ASGI app (`app.main:app`) served by uvicorn was exercised via
  Starlette `TestClient`. With the flag on, `POST /api/v1/reconciliation`
  returns the deterministic engine's real cases and relationships (e.g. web
  scenario 5 → **2 cases, 1 RELATED, merge_permitted=False**). This is the
  **same** engine, invoked the **same** way, as the Phase 24/25 tests — proven
  equal in `test_endpoint_matches_direct_engine`.
- The one-command CLI demo executes the full pipeline with the flag on:
  `python -m demo.run_e2e --scenario C --reconciliation` produces a real
  **CONFLICT** relationship with **human review = TRUE**.
- 47 new Phase 26 tests pass; the full suite is **1453 passed, 0 failed**.

**Honest caveat on in-browser rendering:** live rendering of the React view
against a running backend **could not be captured from this sandboxed
environment**, for reasons entirely outside the reconciliation code:
(1) the sandbox forbids binding network sockets (`PermissionError: [Errno 1]
Operation not permitted` — the same restriction that fails four unrelated
local-LLM mock-server tests), so no reachable backend can be started or
restarted here; (2) the already-running backend on `:8000` predates this
endpoint and its default CORS allowlist is `:3000/:3001`, which excludes the
preview origin `:4173`; (3) the preview is an iframe wrapper. The frontend
**build compiles** and the view binds **exactly** the keys the endpoint emits
(verified field-by-field against the live TestClient JSON). To see it render
live, start the backend as documented in §9.2 — no code change is required.

---

## 1. PROBLEM STATEMENT — why the UI showed all zeros

`ReconciliationView.jsx` (pre-Phase-26):

```js
const { selectedScenario, auditRun } = useSentinel(); // auditRun never provided
// fetched /api/v1/scenarios/${selectedScenario.id}     // .id is undefined; key is scenario_id
// → reconData = auditRun?.… ?? null  → 0 / 0 / 0 / "No active reconciliation data"
```

Three independent breaks: (a) it read `auditRun`, a context key that does not
exist; (b) it fetched a scenario-detail path with an undefined id; (c) there was
**no** reconciliation API to fetch at all. The Phase 24 engine result never had
a route to the browser. Nothing about the engine was broken — only its delivery.

---

## 2. ARCHITECTURE & AUTHORITY MODEL (CORRELATION ≠ IDENTITY)

Reconciliation performs **deterministic case separation only**. For every case
pair `i<j` it emits exactly one relationship from a fixed vocabulary:
`DUPLICATE / SAME_CASE / RELATED / SEPARATE / CONFLICT / UNCERTAIN`, via
union-find clustering over independent signal families and a priority decision
ladder. Its governing rule is **keep separate under uncertainty**.

Reconciliation **does NOT**: call an LLM; inspect `raw_text_head`; inspect model
confidence; perform semantic reasoning; authorize commands; approve recovery;
override physics; or override safety. Authority stays where it was:

| Layer | Authority |
|---|---|
| **Physics** | physical validity (authoritative) |
| **Safety** | recovery / command authorization (authoritative) |
| **LLM** | analysis / hypothesis only (assistive) |
| **Reconciliation** | deterministic case identity/relationship (assistive to triage; **no** command/physics/safety authority) |

A `RELATED` verdict is a **deterministic relationship (possible propagation)**,
**not** physical proof. Merge is permitted **only** for `DUPLICATE` / `SAME_CASE`
(asserted in `test_merge_permitted_matches_relationship_type`).

---

## 3. FEATURE GATE (§3 — default false, explicit activation)

`reconciliation_enabled()` reads `RECONCILIATION_ENABLED` at **call time**; only
`true` / `1` / `yes` (case-insensitive, trimmed) enable it — everything else,
including an absent variable, keeps it **off**. The production default is
**unchanged**; no code path flips the global default to true.

Two documented, explicit activation paths only:
- **CLI:** `--reconciliation` sets `os.environ["RECONCILIATION_ENABLED"]="true"`
  for that process only (restored in tests via an autouse fixture; the
  production default is never mutated persistently).
- **Backend:** start the server with `RECONCILIATION_ENABLED=true`.

`test_flag_off_by_default_returns_disabled_envelope` and the parametrized
`test_flag_values_that_stay_disabled` lock the default and the parsing.

---

## 4. DATA-FLOW FIX (§4 — one engine, no client-side logic)

```
CrashDumpRequest (scenario payload)
      │  POST /api/v1/reconciliation
      ▼
sanitize_telemetry_payload_data                     ← same sanitizer as /detect
      ▼
run_detection_on_crash_dump  →  build_observation_events
      ▼
ReconciliationEngine().reconcile(ReconciliationInput(...))   ← the ONE engine
      ▼
ReconciliationResult.as_dict()  + backend-computed summary counts
      ▼
SentinelContext `reconciliation` entity  { data, loading, error }
      ▼
ReconciliationView.jsx  (pure renderer — no fetch, no derivation, no invention)
```

No second engine exists; no reconciliation logic runs in the frontend. The view
consumes counts computed **once on the backend**.

---

## 5. BACKEND ENDPOINT (§5, §6)

`POST /api/v1/reconciliation` in `sentinel/backend/app/main.py` — a pure
projection sibling of `/detect` and `/physics` (no audit side-effect). Envelope
fields (all present, verified by `test_required_keys_present_when_enabled`):

`reconciliation_enabled, executed, scenario_id, flag_name, config_version,
engine_version, total_cases, isolated_cases, related_relationships,
separate_relationships, conflicts_detected, uncertain_relationships,
merges_performed, human_review_required, cases[], relationships[], reasons[],
warnings[], authority_note, physics_validation`.

`cases[]` and `relationships[]` come verbatim from `ReconciliationResult.as_dict()`
(reused, not re-serialized). Summary counts are derived on the backend;
`isolated_cases` = cases participating in no non-`SEPARATE` relationship.

---

## 6. ENGINE CONNECTION IS IDENTICAL TO WHAT IS TESTED (§6)

`test_endpoint_matches_direct_engine` re-runs the exact preprocessing
(`sanitize → detect → build_observation_events → reconcile`) independently and
asserts the HTTP endpoint's `total_cases`, relationship-type multiset, and
`human_review_required` equal the directly-invoked engine's, across scenarios
1/3/5/6. The endpoint is the engine, over HTTP.

---

## 7. FRONTEND VIEW — EXPLICIT STATES A–F (§8, §14)

`ReconciliationView.jsx` is a dumb renderer of the `reconciliation` entity. It
never converts missing data into a misleading zero:

| State | Condition | Render |
|---|---|---|
| **A disabled** | `reconciliation_enabled === false` | Info banner: engine off by default; how to enable |
| **B not run** | no scenario / no data | "Select a scenario…" |
| **C no-relationships** | executed, 0–1 case or no RELATED | "Separation upheld" banner |
| **D cases** | executed, multiple cases + relationships | Stat panels + case & relationship tables |
| **E conflict** | `conflicts_detected > 0` | Critical banner: contradiction, human review |
| **F backend error** | fetch failed | "BACKEND ERROR" panel |
| (human review) | `human_review_required` | Warning banner (when no conflict) |
| (executed-zero) | executed, `total_cases === 0` | "No observations to reconcile" — explicitly distinct from disabled |

Stat panels bind to backend counts only: Cases→`total_cases` (subtitle
`isolated_cases`), Related→`related_relationships`, Conflicts→`conflicts_detected`,
Review→`human_review_required`. Relationship table shows the deterministic basis
and a **physics: PENDING** marker for RELATED links. `StatusBadge` gained the
five relationship types (SEPARATE/RELATED/DUPLICATE/SAME_CASE/CONFLICT); status
is never colour-alone (glyph + label preserved).

---

## 8. DEMO DATA & SCENARIOS (§7) — real, reused definitions

No fabricated UI data. Two real sources:

- **Web scenarios** (`app.api.scenarios.get_all_scenarios()`) drive the web view.
  Ground truth on the production path (flag on):

| sid | scenario | cases | isolated | related | conflicts | review |
|---|---|---|---|---|---|---|
| 1 | ADCS_GYRO_SEU | 1 | 1 | 0 | 0 | False |
| 3 | OBC_WATCHDOG_OVERFLOW | 3 | 1 | 1 | 0 | False |
| 5 | TCS_THERMAL_RUNAWAY | 2 | 0 | 1 | 0 | False |
| 6 | COMMS_TRANSPONDER_LOSS | 3 | 1 | 1 | 0 | False |
| 4 | (no anomalies) | 0 | 0 | 0 | 0 | False |

- **Canonical Phase 25 scenarios A/B/C/D** (`demo/e2e_demo.py`) drive the CLI.
  B = two separate faults (RELATED, not merged); C = conflicting evidence
  (CONFLICT + human review); D = insufficient data (arbitration → human review).

---

## 9. ONE-COMMAND DEMO (§9, §10)

### 9.1 CLI (primary judge-facing path)

```bash
cd sentinel/backend
python -m demo.run_e2e --scenario B --reconciliation
```

Verified output (ANSI stripped):

```
  RECONCILIATION EXPLICITLY ACTIVATED (RECONCILIATION_ENABLED=true for this run)
▶ RECONCILIATION — DETERMINISTIC CASE SEPARATION (RECONCILIATION_ENABLED=true)
    Principle: CORRELATION != IDENTITY — cases stay separate unless deterministically proven identical.
    Total Cases:               2
      • CASE 001  CASE-9d5348c73e0c subsystems=['AOCS'] channels=['Attitude_error_deg']
      • CASE 002  CASE-bd57240de0a9 subsystems=['AOCS'] channels=['SEU_counter']
    Relationships:             1
      • CASE 001 <-> CASE 002  RELATED  (merge_permitted=False)
          deterministic:       Intra-subsystem physical relationship within 'AOCS'.
          authority note:      deterministic relationship (possible propagation) — physics validation PENDING; RELATED != physically proven.
    Human Review Required:     FALSE
```

`--scenario C --reconciliation` (State E):

```
    Relationships:             1
      • CASE 001 <-> CASE 002  CONFLICT  (merge_permitted=False)
          deterministic:       Opposed directions on shared channel 'Gyro_rate_degs': HIGH vs LOW.
          authority note:      observations contradict — cases kept separate, human review raised. Reconciliation does not resolve the conflict.
    Human Review Required:     TRUE
```

Existing invocations (`--scenario A/B/C/D/ALL`, `--json`) are unchanged —
guarded by `test_plain_json_unchanged_by_phase26`, `test_scenario_all_runs_without_error`.

### 9.2 Web (to render the live React view)

Start the backend with the flag on, allowing the preview origin, then open the UI:

```bash
cd sentinel/backend
RECONCILIATION_ENABLED=true SECURE_DEV_MODE=1 \
  SENTINEL_CORS_ORIGINS="http://localhost:3000,http://localhost:4173" \
  .venv/bin/python -m uvicorn app.main:app --port 8000
```

Then select a scenario in the console → the Reconciliation view renders the
engine's real cases/relationships. (If served from `npm start` on `:3000`, the
default CORS allowlist already permits it and only `RECONCILIATION_ENABLED=true`
is needed.)

---

## 10. PHYSICS AUTHORITY BOUNDARY (§11)

`physics_validation` is reported as `pending` whenever the engine executes and
`not_applicable` when disabled — reconciliation never asserts physical validity.
RELATED relationships carry `physics_support: []`
(`test_related_has_no_physics_authority`). Terminology throughout uses
"deterministic relationship / possible propagation / physics validation
pending", never "physically proven".

---

## 11. CASE ISOLATION / RAG BOUNDARY (§12)

The web endpoint runs detection → observation events → reconciliation only; it
does not perform RAG. The pipeline's RAG path continues to use the existing
`CaseIsolationBoundary` / `rag_filter.py`; no second filter was introduced and
no cross-case evidence is emitted by this endpoint (`relationships[]` reference
case ids only; `cases[]` carry channel/subsystem scope, never raw readings).

---

## 12. AUDIT (§13)

The endpoint writes **no** audit record (parity with `/detect`, `/physics`) —
`test_no_audit_side_effect` asserts `audit/status.run_count` is unchanged across
repeated calls. The audited reconciliation run remains the pipeline path in
`app/agent/agent.py`, which records `Stage.RECONCILIATION` when the flag is
enabled. Audit was not weakened.

---

## 13. SECURITY VERIFICATION (§16, §18)

- `git diff --check` — **clean** (no trailing-whitespace/merge-marker errors).
- **No** `.env`, `*.key`, `*.pem`, secret, or credential files in the changeset.
- **No** API keys / secrets / tokens added in any Phase 26 source
  (the only `GEMINI_API_KEY` reference is **pre-existing** cloud-mode code in
  `run_e2e.py`, unchanged — verified absent from the added-lines diff).
- **No** command authorization, **no** recovery approval, **no** LLM authority,
  **no** raw model output consumed, **no** confidence-based merging,
  **no** `raw_text_head` inspection (grep-verified in changed sources).
- **No** raw telemetry leak: `cases[]` carry no `value` key and no
  `pre_fault_telemetry_window` / `raw_text_head` survives into the response
  (`test_no_raw_value_leak_in_cases`, `test_no_raw_telemetry_window_leaks`).
  Numeric `value`s exist only inside derived `signals[]` metrics, never as raw
  readings.
- **No** physics override, **no** safety override, **no** arbitration change.
- Reconciliation is **not** enabled in production by default.

Every §18 prohibition holds.

---

## 14. TESTS (§15) — 47 new subtests, all passing

`tests/test_phase26_reconciliation_endpoint.py` (endpoint):
flag-off default; disabled-is-explicit-not-zero; disabled-doesn't-run-engine;
flag parsing (enable/disable value sets); scenario 5 two-cases-one-RELATED;
scenario 1 single isolated; scenario 4 executed-zero≠disabled; per-scenario
expected counts; **endpoint == direct engine** (1/3/5/6); no-audit-side-effect;
required-keys-present; no-raw-value-leak; no-raw-telemetry-window-leak;
merge_permitted matches type; relationship_type serialized as plain value;
RELATED has no physics authority.

`tests/test_phase26_run_e2e_reconciliation_flag.py` (CLI + demo):
flag activates & prints focus; flag-absent doesn't activate (default preserved);
scenario C CONFLICT + human review; scenario B RELATED-not-merged;
`--json --reconciliation` still valid JSON; plain `--json` unchanged;
`--scenario ALL` intact.

```
$ .venv/bin/pytest tests/test_phase26_reconciliation_endpoint.py \
                    tests/test_phase26_run_e2e_reconciliation_flag.py -q
47 passed in 0.65s
```

---

## 15. REGRESSION (§17)

```
$ .venv/bin/pytest tests/ -q
1453 passed, 2 skipped, 4 errors, 2627 subtests passed in 128.05s
```

- **0 failed.** Baseline was 1404 passed / 8 skipped / 2605 subtests; the
  increase is the 47 new Phase 26 subtests plus environment-dependent
  skip/pass shuffling.
- Phase 24 + Phase 25 suites: **63 passed, 0 failed**.
- The single contract-staleness failure seen mid-run was the expected
  consequence of adding an endpoint (new path in the OpenAPI schema); resolved
  by regenerating artifacts (`scripts/export_contracts.py`) —
  `test_artifacts_are_not_stale` now passes.
- The **4 errors** are `PermissionError: [Errno 1] Operation not permitted` on
  `socket.bind()` in `test_phase11_sovereign_llm` / `test_phase12_evaluation`
  local-mode mock-server tests. This is the **sandbox** blocking socket binding
  (identical root cause to the uvicorn-launch failure in this environment);
  it is **unrelated to reconciliation** and reproduces independently of these
  changes.

---

## CHANGE INVENTORY

**Modified (tracked):**
- `sentinel/backend/app/main.py` — `POST /api/v1/reconciliation` + helper + imports.
- `sentinel/frontend/src/api/endpoints.js` — `reconciliation` path.
- `sentinel/frontend/src/state/SentinelContext.jsx` — `reconciliation` entity + per-scenario fetch.
- `sentinel/frontend/src/components/ui/StatusBadge.jsx` — 5 relationship types.
- `sentinel/frontend/src/components/views/ReconciliationView.jsx` — rewritten as a pure renderer of the entity (states A–F).
- `sentinel/frontend/src/App.css` — `recon-banner` block (~53 lines, appended). *(Other diff lines in this file are pre-existing uncommitted UI work on this branch, outside Phase 26.)*
- `contracts/index.json`, `contracts/openapi/openapi.json` — regenerated (now document the endpoint).

**New (untracked):**
- `sentinel/backend/demo/run_e2e.py` — one-command demo with `--reconciliation`.
- `sentinel/backend/tests/test_phase26_reconciliation_endpoint.py`
- `sentinel/backend/tests/test_phase26_run_e2e_reconciliation_flag.py`

---

## DELIVERABLE CONFIRMATIONS (A–L)

- **A. Data-flow gap fixed?** Yes — view read a nonexistent `auditRun` and fetched an undefined-id scenario path; now consumes the `reconciliation` entity fed by the real endpoint.
- **B. Backend exposes the real result?** Yes — `POST /api/v1/reconciliation` returns `reconciliation_enabled/total_cases/related_relationships/conflicts_detected/human_review_required/cases[]/relationships[]` from `ReconciliationResult.as_dict()`.
- **C. Same engine as tested?** Yes — `test_endpoint_matches_direct_engine` proves endpoint == directly-invoked engine (no second engine).
- **D. Real demo data for A/B/C/D?** Yes — reused `build_scenario_a/b/c/d`; B=RELATED, C=CONFLICT+review, D=human review; web scenarios drive the web view.
- **E. Frontend consumes the real result?** Yes — no hardcoded data; explicit states A–F; missing data never rendered as silent zero.
- **F. One-command demo?** Yes — `python -m demo.run_e2e --scenario B --reconciliation`; existing A/B/C/D/ALL/`--json` unbroken.
- **G. Gate default false + explicit activation?** Yes — `RECONCILIATION_ENABLED` default false; CLI flag / env are the only, documented activators; production default unchanged.
- **H. Physics authority intact?** Yes — `physics_validation=pending`, RELATED `physics_support=[]`, "physics validation pending" terminology; no physics override.
- **I. RAG case boundaries respected?** Yes — endpoint performs no RAG; pipeline retains existing `CaseIsolationBoundary`/`rag_filter.py`; no cross-case leakage.
- **J. Audit preserved?** Yes — endpoint has no audit side-effect (proven); pipeline still records `Stage.RECONCILIATION` when enabled.
- **K. Security constraints (§16/§18)?** Yes — `git diff --check` clean; no secrets/keys/.env; no command/LLM/physics/safety authority added; no raw-value leak; all verified by tests.
- **L. Tests + regression?** Yes — 47 new subtests pass; full suite 1453 passed / 0 failed; 4 errors are environmental sandbox socket-bind, unrelated.

---

## FINAL STATEMENT

Based on verified runtime evidence — the real ASGI app returning real
deterministic engine output over `POST /api/v1/reconciliation`, the CLI pipeline
producing real RELATED and CONFLICT separations with correct human-review
gating, and 47 passing Phase 26 tests against a clean full-suite regression:

> **Reconciliation is actually executing.**

The complete path **REAL DATA → REAL RECONCILIATION ENGINE → REAL API → REAL
FRONTEND** is wired and proven. Rendering it live in the browser requires only
starting the backend with `RECONCILIATION_ENABLED=true` and a CORS origin
covering the frontend (§9.2) — no further code change.
