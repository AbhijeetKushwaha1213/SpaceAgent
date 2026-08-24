# SENTINEL FDIR — Baseline Audit Report

**Scope:** Understand → Run → Measure → Document the system as it exists today.
**Runtime under audit:** `LLM_MODE=stub` (the project's configured local-safe default), `SECURE_DEV_MODE=1`.
**Commit at audit start:** `2b3305e` (roadmap Phase 1–2). Head of `sentinel-v2`.
**Date:** 2026-08-19. **Auditor:** opencode (baseline only — no code changes, no tuning, no redesign).

---

## A. Executive summary

SENTINEL is a deterministic-first FDIR pipeline: telemetry ingestion → anomaly
detection → state estimation → residual generation → hypothesis generation →
physics validation → RAG retrieval → constrained LLM ranking → deterministic
safety validation → output. The deterministic layers are real, tested
(1020 passing tests), and well structured. The pipeline **runs end-to-end on
every scenario** with a bounded latency (~7.5–8.3 s) and fails safe.

The baseline reveals that the *decision chain is starved before the LLM ever
runs*:

1. **State estimation never decides.** Every scenario produces
   "No residual could be decided across N step(s); N undecidable. Nothing was
   checked." The spacecraft physics models need the *same* modelled channel
   freshly reported at ≥2 consecutive sample times; the crash-dump telemetry
   windows provide only sparse snapshots (T-120s/T-60s/T-30s/T-10s) with each
   modelled channel present once. Result: **zero residuals → physics cannot
   validate or invalidate anything → all hypotheses stay UNCERTAIN → the LLM
   has no physical evidence to rank.**
2. **Hypothesis generation only works for synthetic scenarios.** The 5 synthetic
   scenarios yield 1–7 candidates (top score 0.63–0.76). All 5 ESA-derived
   scenarios (4, 200–203) produce **zero candidates**: ESA telemetry channels are
   anonymised (`channel_13`, `channel_41`, …) and the fault dictionary has no
   mapping for them.
3. **The stub LLM response is incompatible with the constrained-ranking schema.**
   `data/stub_response.json` is in the legacy SentinelOutput format (`hypotheses`,
   `recovery_plan[].command`). The ranker's typed parser expects
   `ranked_hypotheses`/`selected_procedure_ids`, so the stub's hypotheses are
   silently dropped, and the key-ban guardrail fires `UNKNOWN_COMMAND` on the
   literal JSON key `command` inside the recovery plan. Every run therefore ends
   with a padded `INSUFFICIENT_EVIDENCE` hypothesis and a `CMD_HEALTH_CHECK`
   fallback plan. **No scenario produces a scenario-specific diagnosis or plan
   in stub mode.** The safety layer then approves the fallback
   (`REQUIRES_HUMAN_REVIEW`, nothing blocked).
4. **RAG retrieves, but the retrieved PDF text is unreadable.** The ChromaDB
   chunks contain compressed/garbled text (control characters), so even if the
   procedure text reached the LLM it would be noise. It does not reach the LLM:
   the constrained prompt only exposes procedure *IDs*, and the Phase-9
   structured retrieval returns nothing in these runs.

Safety is the strongest subsystem: a single derived command whitelist backed by
declared preconditions, a fail-closed validator, static CI consistency checks
(`conflicts.py`), and three independent hallucination gates. But it is a
*final-stage filter*, not a decision-maker — it validates whatever plan it is
given, and its `REQUIRES_HUMAN_REVIEW` is a status flag with no enforcement
gate downstream.

**Target architecture gaps (from the roadmap):** no confidence router, no
cloud/Gemini escalation path in the running configuration, no human-in-the-loop
authorisation gate, LangGraph tool-routing stubs are commented out, no
data-driven model fitting (4 of 10 physics parameters are hand-assumed).

---

## B. Environment & runtime baseline (STEP 1)

| Item | Value |
|---|---|
| Host Python | 3.14.3 (no `python_requires` pin in requirements; CI uses 3.11) |
| Node / npm | v24.19.0 / 11.17.0 |
| Backend entrypoint | `python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000` (from `sentinel/backend`) |
| Frontend dev | `npm start` (react-scripts, port 3000); `npm run dev` (serve, 3001); `npm run build` (DISABLE_ESLINT_PLUGIN=true) |
| Frontend tests | none in `frontend/src` (`npm test` runs jest with no tests); frontend covered by backend contract tests |
| Key pinned deps | fastapi==0.115.6, pydantic==2.10.4, google-genai==1.1.0, openai==1.58.1, sentence-transformers==3.3.1, chromadb==0.6.3, llama-index-core==0.12.8, numpy==2.2.1, scipy==1.15.1, httpx==0.28.1 |
| LLM mode resolution | unset `LLM_MODE` → `ModelMode.BASE` → `GeminiProvider`, model `gemini-2.5-flash`, requires `GEMINI_API_KEY` (raises if absent). `stub` → `StubProvider`; `local`/`fallback` → `LocalProvider` (phi-3-mini @ localhost:11434/v1) |
| Env vars consumed | `LLM_MODE`, `LLM_MODEL`, `LLM_BASE_URL`, `GEMINI_API_KEY`, `LLM_API_KEY`, `SENTINEL_API_KEY`, `SECURE_DEV_MODE`, `SENTINEL_AUDIT_DB`, `SENTINEL_CLOUD_REDACT_PARAMETERS`, `SENTINEL_CORS_ORIGINS`, `SENTINEL_MAX_PAYLOAD_BYTES`, `SENTINEL_RATE_LIMIT`, `SENTINEL_STUB_RESPONSE`(+`_FILE`) |
| Running server status | `/api/v1/system/status`: llm_mode STUB, provider stub, model `stub:worked-example`, detector/physics/rag ok, sovereignty local |
| RAG index | ChromaDB 56 MB at `data/chroma_db`, ECSS-E-ST-70-11C-Rev.1.pdf + ECSS-Q-ST-30-02C.pdf in `data/ecss` |
| API surface | `/api/v1/analyze` (SSE), `/detect`, `/physics`, `/scenarios`, `/system/status`, `/audit/status`; auth fail-closed 401 when `SENTINEL_API_KEY` required but unset |

---

## C. Test suite results (STEP 2)

**Command:** `python3 -m pytest tests/ -p no:cacheprovider --ignore=tests/run_tests.py`

**Result: 1020 passed, 0 failed, 0 skipped, 25 files, ~49.7 s.**
One warning (third-party chromadb `asyncio.iscoroutinefunction`
DeprecationWarning — not from SENTINEL code; the previously present Pydantic
deprecation is fixed).

**Standalone test not collected under pytest:** `python3 tests/test_rag.py`
→ 147 passed / 1 failed. The failure is `test_rag.py`'s "Enriched content has
ECSS section": `retrieve_by_fault_class(..., use_pdf_rag=True)` does not append
the "ADDITIONAL ECSS CONTEXT" section the test expects.

| Category | Meaning | Items |
|---|---|---|
| A (green under pytest) | 1020 passing tests | full contract, unit, e2e, security suites |
| B (flaky/failing standalone) | test_rag.py 147/1 | PDF-RAG enrichment expectation vs implementation |
| C (runtime smoke) | 10/10 scenario pipelines ran end-to-end | see Section D |
| D (untested in baseline) | confidence router, cloud escalation, HITL gate | do not exist (Section H) |
| E (outdated expectation) | test_rag ECSS-context assertion | likely predates RAG redesign (chroma chunk schema) |
| F (third-party) | chromadb warning | noise, no action |

---

## D. Scenario run results (STEP 3)

All 10 scenarios POSTed to `/api/v1/analyze` (SSE) + `/api/v1/detect` +
`/api/v1/physics` on the running stub server. Latency 7.5–8.3 s, 34–39 SSE events each.

| # | Fault | Source | Det. count / max sev | Candidates (top) | Physics verdicts | Final hyp (rank 1) | Plan | Safety |
|---|---|---|---|---|---|---|---|---|
| 1 | ADCS_GYRO_SEU | SYNTH | 10 / CRITICAL | 6 — ADCS_GYRO_SEU 0.76 | all UNCERTAIN | INSUFFICIENT_EVIDENCE 0.07 | CMD_HEALTH_CHECK | REQUIRES_HUMAN_REVIEW |
| 2 | EPS_SOLAR_UNDERVOLT | SYNTH | 12 / CRITICAL | 3 — EPS_BATTERY_DEGRADATION 0.63 | all UNCERTAIN | INSUFFICIENT_EVIDENCE 0.07 | CMD_HEALTH_CHECK | REQUIRES_HUMAN_REVIEW |
| 3 | OBC_WATCHDOG_OVERFLOW | SYNTH | 6 / HIGH | 1 — OBC_WATCHDOG_OVERFLOW | UNCERTAIN | INSUFFICIENT_EVIDENCE 0.07 | CMD_HEALTH_CHECK | REQUIRES_HUMAN_REVIEW |
| 5 | TCS_THERMAL_RUNAWAY | SYNTH | 12 / CRITICAL | 1 — TCS_THERMAL_RUNAWAY | UNCERTAIN | INSUFFICIENT_EVIDENCE 0.07 | CMD_HEALTH_CHECK | REQUIRES_HUMAN_REVIEW |
| 6 | COMMS_TRANSPONDER_LOSS | SYNTH | 14 / CRITICAL | 1 — COMMS_TRANSPONDER_LOSS | UNCERTAIN | INSUFFICIENT_EVIDENCE 0.07 | CMD_HEALTH_CHECK | REQUIRES_HUMAN_REVIEW |
| 4 | ESA_ADB_ANOMALY | **REAL ESA** | 6 / CRITICAL | **0 (none)** | none (no hypotheses) | INSUFFICIENT_EVIDENCE 0.07 | CMD_HEALTH_CHECK | REQUIRES_HUMAN_REVIEW |
| 200 | ESA_ADB_ANOMALY | SYNTH-from-ESA | 8 / CRITICAL | **0** | none | INSUFFICIENT_EVIDENCE 0.07 | CMD_HEALTH_CHECK | REQUIRES_HUMAN_REVIEW |
| 201 | ESA_ADB_ANOMALY | SYNTH-from-ESA | 3 / CRITICAL | **0** | none | INSUFFICIENT_EVIDENCE 0.07 | CMD_HEALTH_CHECK | REQUIRES_HUMAN_REVIEW |
| 202 | ESA_ADB_ANOMALY | SYNTH-from-ESA | 8 / CRITICAL | **0** | none | INSUFFICIENT_EVIDENCE 0.07 | CMD_HEALTH_CHECK | REQUIRES_HUMAN_REVIEW |
| 203 | ESA_ADB_ANOMALY | SYNTH-from-ESA | 8 / CRITICAL | **0** | none | INSUFFICIENT_EVIDENCE 0.07 | CMD_HEALTH_CHECK | REQUIRES_HUMAN_REVIEW |

Every run also emitted 3× `UNKNOWN_COMMAND` guardrail violations
(`'$.recovery_plan[0..2].command'`) and `safety: 1 approved, 0 blocked,
REQUIRES_HUMAN_REVIEW`.

Per-stage observation (scenario 1, representative):

- Detection: "10 anomaly(ies) on 3 of 8 channel(s) across 13 reading(s).
  Highest severity: CRITICAL on Attitude_error_deg. By detector: COUNTER=2,
  DATA_QUALITY=2, HARD_LIMIT=4, ZSCORE=2." Detection caveats: counters checked
  deterministically (no Z-score), no baseline for 7/8 channels (sigma from
  nominal range), temporal detection skipped where <2 samples.
- State estimation: "No residual could be decided across 2 step(s); 2
  undecidable. Nothing was checked."
- Physics: "No verdict reached on 6 hypothesis(es): the state model could not
  decide any applicable constraint."
- RAG: "3 procedure(s) retrieved."
- LLM: "No hypotheses ranked. Insufficient evidence for diagnosis" + 3
  guardrail violations (see Section E).
- Safety: "1 approved, 0 blocked, status=REQUIRES_HUMAN_REVIEW."

**Scenario-1 anomaly detail:** 10 anomalies — Attitude_error_deg CRITICAL
HARD_LIMIT 7.29 vs 0.01 (corroborated), SEU_counter CRITICAL COUNTER/HARD_LIMIT
(corroborated), Gyro_rate_degs HIGH DATA_QUALITY. All detection is deterministic
and provenance-tracked.

---

## E. LLM boundary (STEP 4)

Verified in-process against the running pipeline's exact code path
(`analyze_crash_dump_stream` → `build_ranking_input` → `build_constrained_prompt`
→ `create_provider(stub)` → `run_constrained_ranking` →
`validate_ranking_output` → `convert_to_sentinel_output` →
`validate_recovery_plan` → `apply_validation_to_output`).

### E.1 RAW input → deterministic evidence (real run, scenario 1)

Input is the canonicalized crash dump (13 readings; 8 channels; T-120s/T-60s/
T-30s/T-10s; event log; hardware state; fault register `0x00000080`).

Deterministic evidence actually delivered to the LLM context:
- anomaly_summary (10 anomalies, 3 channels, CRITICAL on Attitude_error_deg)
- 7 hypothesis contexts: ADCS_GYRO_SEU 0.965 (rank 1) … each with
  `physics_status=UNCERTAIN` (from `build_ranking_input`)
- physics summary "No verdict reached on 6 hypothesis(es)…" (invalidated/validated empty)
- state/residual summary "No residual could be decided across 2 step(s)…"
- RAG: 2 retrieved string snippets (garbled; see E.4)
- valid_fault_ids = the 6 generated candidates

### E.2 LLM INPUT (prompt)

- System prompt: 2,138 chars, `PROMPT_VERSION=1.0.0`, built from IDENTITY,
  SUBSYSTEM_DEFINITIONS, CHANNEL_SEMANTICS (threshold-free, generated from
  channel_dict), FAULT_SIGNATURES, SAFETY_RULES, APPROVED_COMMANDS (81 commands,
  derived from the registry), OUTPUT_FORMAT (schema: `ranked_hypotheses`,
  `selected_procedure_ids`, …), CONFIDENCE_GUIDANCE.
- User prompt: 6,764 chars — the serialized `LLMRankingInput` (anomaly summary,
  anomalous channels, hypotheses with deterministic scores + physics status,
  physics summary, state summary, valid procedure IDs).

### E.3 LLM OUTPUT → GUARDRAIL

In stub mode the provider returns `data/stub_response.json` verbatim — a legacy
SentinelOutput-format JSON (`hypotheses[]`, `recovery_plan[].command`).

- `LLMRankingOutput.from_dict` reads `ranked_hypotheses` / `selected_procedure_ids`;
  the stub's `hypotheses` / `recovery_plan` keys are **not mapped** → typed output
  is empty.
- `validate_ranking_output` rule 5 (`_find_command_keys`) scans the *raw parsed*
  JSON and flags every occurrence of the literal key `command` →
  **3× `UNKNOWN_COMMAND` violations** at `$.recovery_plan[0..2].command`,
  `is_valid=False`, `requires_human_review=True`. This is a key-name ban — the
  command *values* (`CMD_GYRO_A_DRIVER_RESET`, …) are never checked against the
  registry by this rule.
- Result: **no hypotheses ranked** ("Insufficient evidence"), `selected_procedure_ids`
  empty → `convert_to_sentinel_output` pads rank-1..3 as `INSUFFICIENT_EVIDENCE`
  (conf 0.07/0.04/0.01) and appends the `CMD_HEALTH_CHECK` fallback step.

### E.4 SAFETY HANDOFF

`validate_recovery_plan` receives the fallback plan: `CMD_HEALTH_CHECK` is
registered/enabled, has no preconditions → approved (LOW risk). Confidence 0.07
< 0.70 → `requires_human_review=True`. Status `REQUIRES_HUMAN_REVIEW`,
0 blocked. **The deterministic guards would also have caught the stub's real
commands** (`CMD_PURGE_SEU_MEMORY_BANK` is not in the registry and would be
blocked as NOT_IN_REGISTRY had it reached the validator).

### E.5 Boundary summary

The LLM is a **ranking/explanation layer over deterministic hypotheses only**;
it cannot generate commands (key-ban), cannot override physics (demotion rule
+ `reconcile_llm_claim` returns deterministic verdict), and cannot invent faults
(fault_id whitelist). This is a sound boundary. In the configured (stub) runtime
it adds nothing: its input is already empty of physical evidence, and its
response is schema-incompatible, so the output is the padded fallback on
every scenario.

---

## F. Physics validation (STEP 5)

### F.1 Models (app/estimation + app/validation/physics.py)

| Model | Equation (as coded) | Inputs | Output | Constants | Notes |
|---|---|---|---|---|---|
| Momentum exchange (Gyro_rate_degs) | `w_pred = w[k] − (I_w/I_sc)·(w_w[k+1]−w_w[k])` | Gyro_rate_degs prev, RW_speed_rpm both ends | TWO_SIDED residual + implied torque | `I_w/I_sc = 1.944e-4` (derived from channel hard maxima), `I_sc = 10 kg·m²` (assumed) | rigid body, 1 axis, no external torque; gyro bias cancels from residual |
| Attitude error bound | `θ[k+1] = θ[k] + \|mean(w_sc)\|·dt` | prev error, rate both ends | UPPER_BOUND residual (over-shoot only) | — | detects under-reported motion only |
| Power balance (SoC_pct) | `SoC[k+1] = SoC[k] + 100·(P_gen−P_load)·dt/(3600·E_cap)` | SoC prev, I_sa, V_bus (or 30 V), heater (or 0) | TWO_SIDED residual + energy direction | `E_cap = 250 Wh` (assumed), base load 150 W (derived) | load-mode change looks like an energy fault |
| Linear V_bat map | `V_bat = V_lo + (SoC/100)·(V_hi−V_lo)` | SoC prediction | TWO_SIDED residual (widest tolerance) | V_lo 28 V, V_hi 33 V | no IR drop; weakest prediction |
| Lumped thermal node | `T[k+1] = T[k] + dt·(Q_int+P_heater − k_th·(T−T_sink))/C_th` | Component_temp_C prev, heater prev | TWO_SIDED residual + steady-state temp | `k_th=0.5 W/K`, τ=600 s, C=300 J/K, sink −20 °C, Q_int=25 W (all assumed/derived) | declines at dt ≥ 1200 s; no illumination/eclipse term |
| Wheel saturation | `sat = \|RW_speed_rpm\|/6000` | RW reading | PASS ≥ 0.5, FAIL < 0.5, INDET none | 6000 rpm limit; 0.5 = "SENTINEL threshold, not a vehicle spec" | |
| Trend checks | `Δ = last − first` of fresh readings; FLAT ≤ 2% of span | fresh series (≥2) | RISING/FALLING/FLAT/UNKNOWN | `flat_fraction=0.02` | |

9 constraints (PHYS_ACTUATOR_AUTHORITY, PHYS_ACTUATOR_SATURATION,
PHYS_MOMENTUM_ACCOUNTED, PHYS_SENSOR_CORROBORATION, PHYS_ENERGY_BALANCE,
PHYS_ENERGY_DIRECTION, PHYS_HEAT_BALANCE, PHYS_THERMAL_DIRECTION,
PHYS_TELEMETRY_OVERLAP). Tolerances are linear in dt
(`tol = floor + dt_growth·floor·max(dt,0)/100 s`), all "SENTINEL choices, no
statistical basis". Verdict semantics: VALID = not refuted + ≥1 decided
corroboration; INVALID = ≥1 decided FAIL; UNCERTAIN = nothing decided.
**A missing corroboration never produces INVALID.** Faults without claims
(OBC_WATCHDOG_OVERFLOW, COMMS_TRANSPONDER_LOSS, AOCS_CONTROL_COMMAND_ANOMALY,
MULTI_CASCADE) are UNCERTAIN by construction.

### F.2 Why nothing is ever decided (root cause)

`compute_residuals` requires the **same channel** freshly reported at ≥2
timed sample times (`fresh_states_for`), and each residual is only computed for
the last step per channel. The crash-dump windows supply each modelled channel
once (or twice with one NaN), so `len(fresh) < 2` → skipped; when no channel
qualifies the report reads "No residuals from N state snapshot(s): no modelled
channel has two fresh samples to step between." In scenario 1 two steps were
evaluated but every residual was UNDECIDABLE (observation side requires a fresh
reading at the current time; a carried-forward value would make the residual an
artefact). A missing/failed reading is therefore **indistinguishable from an
absent one** — the pipeline cannot tell "gyro failed" from "gyro never sampled."

### F.3 Model limitations (stated in code)

Single unlabelled attitude axis (2 axes UNAVAILABLE, never zero); one wheel; one
thermal node; no orbit/eclipse model; no Kalman filtering ("estimation ≈
identity"); forward-Euler error "comparable to the prediction itself" at 300 s
steps; 4 of 10 parameters assumed (not derived or measured); nominal-band vs
hard-limit contradictions in channel_dict unresolved by design; LLM cannot
override any verdict.

---

## G. Safety architecture (STEP 6)

**Single derived whitelist, fail-closed validation, static CI consistency.**

- **Registry:** `command_registry.py` — 81 commands (ADCS 16, EPS 15, OBC 11,
  TCS 12, COMMS 12, SYSTEM 15), all currently enabled. Each declares subsystem,
  risk level, description, and required preconditions. Duplicate IDs fail at
  import. Whitelist is *derived* from the registry (`enabled_only=True`), so
  whitelist and registry cannot drift.
- **Conditions** (`conditions.py`): tri-state SATISFIED/VIOLATED/UNKNOWN, policy
  **"UNKNOWN NEVER BLOCKS"**. Thresholds from channel_dict with hardcoded
  fallbacks: SoC floor **15.0 %** (`BATTERY_FLOOR`), thermal ceiling **85.0 °C**
  (`THERMAL_SURVIVAL`), gyro validity (NaN/degraded → VIOLATED → blocks
  attitude actuation, `GYRO_HEALTH_PREREQUISITE`), transponder lock
  (0/False/"no" → VIOLATED → blocks OBC reboot, `COMMS_LOCK_REBOOT`). Latest
  sample wins (a T-0 dropout is not masked by an older good value).
- **Blocking** (validate_recovery_plan order): `INVALID_FORMAT` (no `CMD_`
  prefix) → `NOT_IN_REGISTRY` (unregistered) → `COMMAND_DISABLED` →
  condition-violation (battery/gyro/comms/thermal) → non-blocking escalation.
  Severities: format/registry/gyro/comms = CRITICAL; disabled/battery/thermal = HIGH.
- **Conflicts** (`conflicts.py`): a build-time/CI static checker (exits 1 on
  ERROR, `--strict` on warnings). Proves every consumer (procedures KB, prompts,
  dataset generator, demo cache) agrees with the registry; catches
  impossible preconditions, whitelist drift, unknown procedure commands, prefix
  mismatches. **No runtime step-vs-step conflict resolution** — duplicates pass
  through and are renumbered.
- **Hallucination gates (3 independent):** (1) ranker key-ban on command-shaped
  JSON keys (`command`, `commands`, `command_sequence`, `raw_command`,
  `actuator_command` — any path); (2) registry-membership value check in
  `validate_recovery_plan` (blocks, not drops); (3) `conflicts.py` CI gate.
- **Exception behavior:** safety never raises; unknown conditions evaluate
  UNKNOWN (permissive); LLM failures fall back to the legacy pipeline; a failed
  run yields SSE `ERROR`, never a fabricated clean result.
- **requires_human_review is set by:** any HIGH/BLOCKED-risk step, confidence
  < 0.70, any blocked step, any guardrail violation, or a model invariant.
- **Total rejection:** an all-blocked plan stays EMPTY with `safety_status=BLOCKED`
  + `blocked_steps` (the old fabricated `CMD_HEALTH_CHECK` substitution was
  removed in Phase 1).

**Notable:** `REQUIRES_HUMAN_REVIEW` is a computed status flag. Nothing in the
pipeline stops or defers execution on it — there is no human-authorisation gate.

---

## H. Architectural gap analysis vs target (STEP 7)

Target chain: Telemetry → Evidence → Physics → RAG → Local LLM → Confidence
Router → Cloud Escalation → Physics Re-validation → Safety Validator → Human
Authorization.

| Component | Status | Baseline evidence |
|---|---|---|
| Telemetry ingestion + canonicalization | **IMPLEMENTED** | 13-reading canonical windows; bounds enrichment from channel_dict |
| Anomaly detection (evidence) | **IMPLEMENTED** | Z-score + HARD_LIMIT + COUNTER + DATA_QUALITY + DISCRETE_STATE + temporal; caveats recorded |
| State estimation / residuals | **IMPLEMENTED but starved** | never decides on current window shapes (F.2) |
| Hypothesis generation | **IMPLEMENTED (synthetic only)** | 0 candidates on all ESA scenarios (anonymised channels unmapped) |
| Physics validation | **IMPLEMENTED but starved** | all UNCERTAIN, nothing invalidated/validated, on every run |
| RAG (ECSS procedures) | **PARTIALLY IMPLEMENTED** | retrieves 3 chunks, but chunk text is garbled (compressed PDF data) and never reaches the LLM as content |
| Local LLM ranking | **IMPLEMENTED (stub default); Gemini cloud available when keyed** | stub schema-incompatible → padded output; Gemini path untested in baseline |
| Confidence router | **MISSING** | single unconditional ranking path |
| Cloud escalation | **MISSING** | no escalation logic; `ModelMode.BASE` is cloud (Gemini) only when `GEMINI_API_KEY` set |
| Physics re-validation after LLM | **IMPLEMENTED** | `reconcile_llm_claim` — LLM cannot override deterministic verdict |
| Safety validator | **IMPLEMENTED (strong)** | 81-command registry, conditions, CI consistency, fail-closed |
| Human authorization gate | **MISSING (flag only)** | `REQUIRES_HUMAN_REVIEW` is informational |
| LangGraph tool routing | **MISSING (stubs commented out)** | agent.py:2024–2031 |
| Audit trail | **IMPLEMENTED** | audit DB records 9+ entries/run, sealed (SHA-256), `/api/v1/audit/status` |

Additional gaps: no data-driven parameter fitting (4/10 physics constants
assumed); ESA channel anonymisation has no mapping layer; no
model-vs-window adequacy check (the pipeline proceeds happily on zero residuals
rather than declaring the dump under-sampled); frontend renders results but
pipeline never produces scenario-specific ones in stub mode.

---

## I. Findings ledger

| ID | Severity | Category | Finding |
|---|---|---|---|
| F-01 | **HIGH** | E/D | State estimation never produces residuals on current window shapes → physics and LLM starved (F.2). |
| F-02 | **HIGH** | D | ESA-derived scenarios yield zero hypotheses (anonymised channels unmapped) → real data cannot be diagnosed. |
| F-03 | **HIGH** | D | Stub LLM response is schema-incompatible with constrained ranking → every run emits UNKNOWN_COMMAND guardrails and the padded INSUFFICIENT_EVIDENCE fallback (E.3). |
| F-04 | MEDIUM | D | RAG chunk text is garbled (compressed PDF bytes); not usable as LLM content (E.4/Section H). |
| F-05 | MEDIUM | C | RAG-enrichment standalone test fails (test_rag.py 1 fail; category B/E). |
| F-06 | MEDIUM | D | No confidence router, no cloud escalation, no human-authorisation gate (H). |
| F-07 | LOW | E | 4/10 physics parameters assumed; tolerances "no statistical basis". |
| F-08 | LOW | E | Hypothesis top-score varies 0.76–0.97 / 6–7 candidates by payload canonicalization (input-representation sensitivity). |
| F-09 | INFO | C | Tests green (1020), CI (ruff + pytest + docker build) wired on `sentinel-v2`. |

Severity: HIGH = blocks the core value (scenario-specific diagnosis/recovery);
MEDIUM = degrades correctness or safety visibility; LOW = precision/repro;
INFO = process.

---

## J. Risk register

| Risk | Likelihood | Impact | Existing control | Residual |
|---|---|---|---|---|
| Recovery plan rejected wholesale | Certain (stub) | Low | fail-closed; empty-plan+BLOCKED invariant | operator gets no actionable plan; availability concern |
| False negative on real ESA data | High (0 candidates) | High | none (no mapping) | ESA anomalies undiagnosable |
| Unauthorised command execution | Very low | Catastrophic | registry + conditions + key-ban + CI | none identified |
| Physics model misfit accepted as truth | Medium | High | stated assumptions + UNCERTAIN-not-pass semantics | operator may read UNCERTAIN as "checked" (summaries are explicit) |
| Cold-start (no Gemini key) | Medium | Medium | fail-closed 401 on auth; stub default is safe but non-functional | documented in this report |

---

## K. Reproducibility

- Full test suite: `cd sentinel/backend && python3 -m pytest tests/ -p no:cacheprovider --ignore=tests/run_tests.py` (captured: `/tmp/sentinel_tests_full.txt`).
- Standalone RAG test: `python3 tests/test_rag.py` (147/1).
- Scenario runs: `POST /api/v1/analyze` per scenario against the stub server
  (captured: `/tmp/sentinel_scenario_runs.json`; runner at
  `/var/folders/m6/0w9pszxj7b1_pcb561_j_bjr0000gn/T/opencode/scenario_runner.py`).
- LLM-boundary reproduction: in-process script walking the exact agent code path
  (scenario 1), incl. canonicalized window, stages, prompt sizes, stub response,
  guardrail violations, safety handoff.
- Runtime baseline: `GET /api/v1/system/status`, `GET /api/v1/scenarios`.

---

## NEXT ACTION (prioritised)

1. **Fix the evidence-starvation root cause (F-01/F-02) — highest leverage.**
   Decide the intended telemetry-window contract: either the simulator/mission
   files must provide ≥2 samples per modelled channel per window (so state
   estimation and physics can actually decide), or the pipeline must treat a
   no-residual dump as UNDER-SAMPLED and surface that loudly instead of
   proceeding to an LLM ranking of un-evidenced hypotheses. Align the ESA
   channel anonymisation with the fault dictionary (channel mapping layer) so
   real telemetry yields candidates.
2. **Make the stub a faithful constrained-ranking fixture (F-03).** Convert
   `data/stub_response.json` to the `LLMRankingOutput` schema (`ranked_hypotheses`,
   `selected_procedure_ids`) with no command-key fields, so the configured
   default runtime exercises the real boundary and produces scenario-specific
   output instead of the padded fallback. Optionally repair the RAG chunk
   extraction (F-04) so retrieved text is legible and reaches the LLM.
3. **Close the decision-loop gaps (F-06): confidence router + human-authorisation
   gate.** Route low-confidence/UNCERTAIN outcomes to operator authorization
   with an explicit gate (block or defer), and define the cloud/Gemini escalation
   path (when it is allowed, with what redaction) rather than leaving escalation
   absent or implicit in `ModelMode.BASE`.

*Baseline complete. No code was modified to produce this report.*