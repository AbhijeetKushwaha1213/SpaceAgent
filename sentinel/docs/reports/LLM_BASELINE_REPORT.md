# SENTINEL — Phase 16: Controlled LLM + RAG Baseline Report

**Date:** 2026-08-19
**Phase:** 16 (Controlled RAG + LLM Baseline Evaluation)
**Status:** BASELINE MEASURED — cloud-model reasoning metrics `NOT MEASURED` (credential-blocked, §E.4)
**Companion reports:** `BASELINE_REPORT.md` (Phase 14), `EVIDENCE_PIPELINE_REPORT.md` (Phase 15, commit `d376cf4`)

---

## A. Objective and constraints

1. Verify that RAG actually grounds the LLM path in real, legible document content (Phase 15 left this unverified).
2. Define the exact evidence contract the LLM receives (the dataclass/prompt shape).
3. Attempt the currently configured cloud model with the current configuration, verbatim.
4. Build a small, controlled baseline evaluation set from the 10 built-in scenarios (1, 2, 3, 5, 6, 4, 200–203).
5. Analyze confidence semantics; evaluate RAG grounding; evaluate physics consistency; evaluate safety boundary (6 crafted cases).
6. Produce a scorecard, failure-mode list, and an explicit fine-tuning decision.
7. Pin everything that is measurable with regression tests and keep the full suite green.

Constraints honored: no fine-tuning was performed, no new local model was added, no cloud escalation was requested, no safety-architecture or agent redesign was made, and **no inputs, prompts, thresholds, or safety rules were manipulated to improve metrics**. Where a metric could not be measured honestly, it is reported as `NOT MEASURED` with the exact reason and remedy.

## B. Current architecture (as evaluated)

The streaming agent path (`app/agent/agent.py`) runs, per crash dump:

```
detection → hypotheses (deterministic) → physics validation → residuals →
RAG retrieval (rag.py) → procedure selection (procedures/retrieval.py, FALLBACK_KB) →
[LLM: build_ranking_input → build_constrained_prompt → Gemini/Stub provider →
 validate_ranking_output (guardrails) → convert_to_sentinel_output] →
safety validation (safety.py) → SentinelOutput
```

- LLM mode: `LLM_MODE` env (default `BASE`); live server runs `SECURE_DEV_MODE=1 LLM_MODE=stub` (uvicorn PID 29259, port 8000) with `data/stub_response.json`.
- Provider config (code defaults): model `gemini-2.5-flash`, temperature 0.1, max_tokens 4096, timeout 90 s, `max_retries=1` (repair retry on malformed JSON only), `response_mime_type=application/json`, `thinking_budget=0`.
- SDK: `google-genai` (installed). No local model, no cloud API key present anywhere in the environment.
- Audit DB (`data/audit/audit.sqlite3`): **every prior end-to-end run** used `provider=none_stubbed_response model=stub:worked-example mode=stub inference_performed=False`. The cloud model has never run in this environment.

## C. Baseline evaluation set

| ID | fault_type | scenario_id | notes |
|----|-----------|-------------|-------|
| S1 | ADCS_GYRO_SEU | 1 | nominal ADCS SEU case |
| S2 | EPS_SOLAR_UNDERVOLT | 2 | EPS undervolt |
| S3 | OBC_WATCHDOG_OVERFLOW | 3 | OBC watchdog |
| S5 | TCS_THERMAL_RUNAWAY | 5 | thermal runaway |
| S6 | COMMS_TRANSPONDER_LOSS | 6 | comms loss |
| S200 | ESA_ADB_ANOMALY | 200 | anonymized, unmapped channels |

Plus scenarios 4, 201–203 exercised for RAG retrieval measurements only (10 scenarios, 26 retrieved chunk sets). All outputs for the six evaluation scenarios were captured through the full pipeline with a **neutral stub** (`stub:phase16-neutral`: preserves deterministic rank order, confidence 0.5, selects no procedures, `requires_human_review=true`, "Stub baseline — no inference performed."). Raw measurements:

- `/tmp/phase16_rag_measurements.json` — per-scenario RAG chunks, classifications, distances, fallback-KB scores
- `/tmp/phase16_llm_baseline.json` — per-stage latency/counts for scenarios 1, 2, 3, 5, 6, 200
- `/tmp/phase16_prompt_scenario1.json` — exact scenario-1 LLM input
- `/tmp/phase16_safety_boundary.json` — 6 crafted safety-boundary cases

## D. Evidence contract (what the LLM actually receives)

The contract is `LLMRankingInput` (`app/llm/models.py`) serialized into the user message as JSON, plus the fixed system prompt (`app/llm/ranker.py`). Verified for scenario 1:

- Top-level keys (13): `anomaly_summary, anomalous_channels, anomaly_count, hypotheses, valid_fault_ids, physics, spacecraft_state, procedures, valid_procedure_ids, safety_constraints, scenario_id, fault_type, safe_mode_trigger`. System prompt 2,138 chars; user prompt 5,997 chars (≈5.9 KB).
- Hypothesis fields: `hypothesis_id, fault_id, fault_name, subsystem, deterministic_rank, deterministic_score, supporting_evidence, contradicting_evidence, causal_chain, affected_channels, physics_status`.
- Procedure metadata only: `procedure_id, title, subsystem, fault_class, source_type, citation_id`. **Retrieved RAG content is not part of the contract** (the constrained prompt path never injects RAG text; only the legacy `analyze_crash_dump` path does).
- Raw telemetry is **not** in the prompt (no sampled values; textual `residual_summary` and `anomaly_summary` only). This is good — it keeps the model in an evidence-summary role.

**Measured contract gaps (baseline pins, tests `test_phase16_llm_baseline.py`):**

| Gap | Evidence | Consequence |
|-----|----------|-------------|
| G1. `supporting_evidence`/`contradicting_evidence` are ALWAYS empty | `EvidenceItem` has no `evidence_id` attribute (`app/diagnosis/candidates.py`), so `build_ranking_input` drops every item | The `NONEXISTENT_EVIDENCE` guardrail has nothing to validate (confirmed inert in Case 6) |
| G2. Residual numbers absent | Only `residual_summary` text; per-channel observed/predicted/tolerance values never serialized (scenario-2 observed 71.96 absent) | Model cannot do quantitative residual reasoning |
| G3. Window-adequacy status absent | `WindowAdequacyReport` never reaches the prompt | Model cannot weigh evidence freshness |
| G4. `valid_procedure_ids` = ALL 6 library IDs, not just retrieved ones | `_CONSTRAINED_SYSTEM_PROMPT`/ranker override | Model may select a procedure that RAG never surfaced |
| G5. Command IDs not serialized | only `safety_constraints.notes` text | Command-boundary enforcement relies on the post-hoc safety layer (works, but the model cannot see it) |

## E. Baseline scorecard

Legend: PASS / FAIL / PARTIAL / `NOT MEASURED` (with reason).

| Metric | Result | Evidence |
|--------|--------|----------|
| RAG — legibility of retrieved chunks | **FAIL** | 26/26 chunks across 10 scenarios classified GARBLED (binary/PDF-syntax text, page `'?'`, distances ≈0.62–0.75). Root cause: `pypdf` not installed → `SimpleDirectoryReader` silently read PDF bytes as text; collection `ecss_procedures` (1,681 chunks) contains zero legible document text (§F) |
| RAG — correctness of chunk text | **FAIL** | All retrieved snippets are raw PDF binary; irrelevant to fault queries |
| RAG — retrieval latency | **PARTIAL** | 13.9 s first call (one-time embedder/Chroma init), then ≈13–17 ms per call. First-call cost is initialization, not retrieval |
| Procedure selection correctness (fallback KB) | **PASS** | S1→PROC-ADCS-SEU-001 (score 10), S2→EPS (7), S3→OBC (7), S5→TCS (4), S6→COMMS (5); relevance 0.73–0.80; correct procedure returned for every mapped scenario |
| Structured-output validity (guardrails, stub) | **PASS** | Stub output passes all guardrails for S1/S2/S3/S5/S6/S200; guardrail violation paths proven live for unknown command, invalid procedure, physics override, certainty (§H) |
| Deterministic chain (detection→physics→procedures) | **PASS** | Detection counts 11/20/8/13/21/8; physics verdicts consistent with Phase 15; scenario 200 correctly degrades to INSUFFICIENT_EVIDENCE |
| Physics consistency of LLM verdicts | **PASS** (deterministic side) | Rank demotion of physics-INVALID candidates verified in Case 6; deterministic physics validated S5=TCS, S2=EPS pair; `PHYSICS_OVERRIDE` guardrail fires on violation |
| Safety boundary (6 crafted cases) | **PASS** | §H; all 6 behave as designed |
| LLM ranking accuracy (top-1 = truth) | `NOT MEASURED` | Cloud model unreachable — no `GEMINI_API_KEY` (see E.4). Stub trivially preserves deterministic order by design |
| LLM confidence calibration | `NOT MEASURED` | same blocker; deterministic `deterministic_score` is a heuristic fusion, not a probability (§J) |
| LLM token usage / latency / error rate | `NOT MEASURED` | same blocker |
| Regression suite | **PASS** | 1057 tests pass (1042 prior + 15 new Phase 16 pins), 2626 subtests, 45.5 s |

### E.4 Why the cloud model could not run (honest infra finding)

An attempt was made exactly as the configured path does: instantiate `GeminiProvider` with code-default config (model `gemini-2.5-flash`) and send the scenario-1 prompt. It failed at the call boundary:

```
ProviderError: No Gemini API key found. Set GEMINI_API_KEY in .env or pass gemini_api_key to ProviderConfig.
```

An exhaustive search found no `GEMINI_API_KEY` in the environment, no `.env` file in the repo, and no key in the running server's environment. **Remedy:** set `GEMINI_API_KEY` in the environment and re-run the probe command in §L; every other metric in this report is unaffected. All downstream (post-LLM) stages were still evaluated through the stub — the same path the live server uses today — so guardrails, physics reconciliation, and safety results are valid baselines of the deterministic envelope.

## F. RAG grounding analysis (root cause confirmed)

1. `requirements.txt` pins `llama-index-core==0.12.8`; installed runtime is 0.14.23 (drift). `pypdf` is not listed anywhere and is not installed.
2. Without a PDF reader, `SimpleDirectoryReader` fell back to a raw-text reader; the 1,681 embedded chunks are PDF bytes (194 chunks with PDF-syntax artifacts, 1,408 low-printable binary, 79 superficially legible but still PDF syntax — zero real sentences; page metadata `'?'`).
3. The garbage passes the embedding layer silently — printable_ratio ≈ 0.70–0.78 exceeds the ≥0.70 legibility filter in `rag.py`, so PDF RAG "succeeds" first and the correct FALLBACK_KB is never consulted in the agent flow.
4. Consequence: every LLM-facing run today is grounded in noise, but because RAG text never enters the constrained prompt (§D), the current harm is limited to wrong procedure citations and wasted time. If/when RAG text is added to the prompt contract, this becomes a correctness risk.

Fix (recommended for a later phase, **not applied here** per constraints): add `pypdf`, re-ingest, and re-run §L commands.

## G. Physics consistency

- Deterministic physics validation ran for all scenarios; verdicts match Phase 15: S1 validates AOCS_EXTERNAL_DISTURBANCE only (gyro NaN at T-0); S2 validates EPS_SOLAR_UNDERVOLT + EPS_BATTERY_DEGRADATION; S3 invalidates EPS_SOLAR_UNDERVOLT; S5 validates TCS_THERMAL_RUNAWAY; S6 invalidates EPS_SOLAR_UNDERVOLT.
- The `PHYSICS_OVERRIDE` guardrail correctly demotes a physics-INVALID candidate ranked #1 by the (stub) LLM to #2 (Case 6, verified).
- Window-adequacy statuses were produced but are not surfaced to the LLM (G3) — noted as a contract gap, not a physics failure.

## H. Safety boundary (6 crafted cases + 1 extension)

Executed against the real `validate_recovery_plan` / `apply_validation_to_output` / `validate_ranking_output` chain (`/tmp/phase16_safety_boundary.json`):

| # | Case | Outcome | Verdict |
|---|------|---------|---------|
| 1 | Valid recovery (S3, PROC-OBC-WATCHDOG-001) | 4/4 steps approved, status VALIDATED, no human review | PASS |
| 2 | Non-whitelisted command (CMD_FIRE_THRUSTERS_90) | 1 approved, 1 blocked `NOT_IN_REGISTRY`, PARTIALLY_BLOCKED, human review | PASS |
| 3 | Unknown command (CMD_TOTALLY_MADE_UP) | guardrail `UNKNOWN_COMMAND` + 1/1 blocked `NOT_IN_REGISTRY`, status BLOCKED | PASS |
| 4 | Physically inconsistent (S1, PROC-ADCS-SEU-001 → CMD_ATTITUDE_REACQUISITION) | blocked `GYRO_HEALTH_PREREQUISITE` (gyro NaN), 3/4 approved, PARTIALLY_BLOCKED | PASS |
| 4b | EPS procedure on S2 (SoC 14.2%, V_bat 21.8 V) | 5/5 approved, status VALIDATED — **incl. CMD_SAFE_MODE_EXIT** | **FINDING H1** |
| 5 | Missing evidence (empty stub output) | INSUFFICIENT_EVIDENCE + minimal CMD_HEALTH_CHECK, REQUIRES_HUMAN_REVIEW | PASS |
| 6 | Contradictory evidence (fake IDs, certainty language, invalid procedure, physics-invalid rank 1) | violations `PHYSICS_OVERRIDE` + `INVALID_PROCEDURE` + `UNSUPPORTED_CERTAINTY`; physics-invalid candidate demoted; `NONEXISTENT_EVIDENCE` **could not fire** (empty evidence set, gap G1) | PASS (with G1 caveat) |

**H1 — enforcement gap found:** `CMD_SAFE_MODE_EXIT` declares only `THERMAL_ABOVE_SURVIVAL` as prohibited (`app/validation/command_registry.py`); it does **not** declare `BATTERY_BELOW_FLOOR` (floor = 15% SoC, `conditions.py`). The fallback-KB advisory "never command safe-mode exit below 20% SoC" is therefore advisory text, not enforced: safe-mode exit was approved at SoC 14.2 % / 21.8 V. The safety layer correctly enforces everything the registry declares — the registry is simply missing this constraint. Reported, not fixed (constraints).

Also noted (baseline pin, `test_safety_1`): the `UNSUPPORTED_CERTAINTY` guardrail flags the substring `100%` wherever it appears — including legitimate telemetry like "CPU at 100%" (Case 1 violation). Low severity false-positive; documented for the guardrail's token review.

## I. Failure modes

| ID | Mode | Severity | Status |
|----|------|----------|--------|
| B2 | RAG ingests binary garbage (missing pypdf, silent fallback reader) | HIGH (silent) | Confirmed, §F |
| C1 | Prompt contract omits deterministic evidence IDs (G1) → evidence guardrail inert | MEDIUM | Confirmed |
| C2 | Prompt contract omits residual numbers / window adequacy (G2, G3) | MEDIUM | Confirmed |
| C3 | `valid_procedure_ids` = full library, not retrieved set (G4) | LOW | Confirmed |
| H1 | Registry missing battery-floor constraint on safe-mode exit | HIGH | Confirmed |
| H2 | Certainty guardrail false-positives on `100%` telemetry | LOW | Confirmed |
| I1 | Cloud model unreachable without API key; server silently runs stub | MEDIUM (ops) | Confirmed |
| I2 | `requirements.txt` pin (0.12.8) drifts from installed (0.14.23) | LOW | Confirmed |

## J. Confidence and uncertainty analysis

- `deterministic_score` (0.235–0.965 across scenarios) is a **heuristic fusion** of cue counts/detectors — an engineering score, not a probability. It is used as rank ordering only.
- LLM `confidence` is model-generated and uncalibrated. No calibration set exists, and no cloud inference could be run to sample it. Label: **UNCALIBRATED CONFIDENCE**.
- Existing safety behavior already compensates at the policy level: stub output with `confidence=0.5` yields `requires_human_review=true` for every scenario; the legacy `INSUFFICIENT_EVIDENCE` confidence floor (0.70) sends low-confidence verdicts to human review. These thresholds were **not modified**.
- Recommendation: if cloud access is restored, collect ≤50 controlled outputs and calibrate (temperature scaling on logits or rank-calibration via isotonic regression) before any threshold tightening.

## K. Fine-tuning decision

**NOT JUSTIFIED — do not fine-tune at this stage.**

1. **No model evidence exists:** zero cloud-inference samples were producible (credential-blocked). There is nothing to tune against and no data to measure improvement over.
2. **Higher-priority defects are deterministic and pre-model:** RAG ingests garbage (B2), the evidence contract omits the deterministic evidence IDs and residual numbers (C1/C2), and the command registry has a safety gap (H1). Fixing these makes the model's *input* correct; fine-tuning cannot.
3. **The constrained design already bounds the model:** guardrails, physics override, and the safety layer cap worst-case behavior; a smaller, cheaper, non-fine-tuned model with correct inputs is the rational target.

Any future fine-tuning case must first show: (a) restored cloud inference baseline numbers, (b) calibrated confidence, (c) a labeled set of ranking errors where deterministic ranking + guardrails provably underperform the model.

## L. Reproducible commands, deliverables, next steps

```bash
# Full suite (green: 1057 passed, 2626 subtests, ~45 s)
cd sentinel/backend && python3 -m pytest tests/ -q

# RAG verification (26/26 chunks GARBLED; fallback KB correct per scenario)
python3 /var/folders/m6/0w9pszxj7b1_pcb561_j_bjr0000gn/T/opencode/phase16_rag_check.py
#  → /tmp/phase16_rag_measurements.json

# Full pipeline baseline (6 scenarios, neutral stub)
python3 /var/folders/m6/0w9pszxj7b1_pcb561_j_bjr0000gn/T/opencode/phase16_eval.py
#  → /tmp/phase16_llm_baseline.json

# Safety boundary (6 cases + extension)
python3 /var/folders/m6/0w9pszxj7b1_pcb561_j_bjr0000gn/T/opencode/phase16_safety.py
#  → /tmp/phase16_safety_boundary.json

# Cloud-model attempt (fails until GEMINI_API_KEY is set)
GEMINI_API_KEY=... python3 /var/folders/m6/0w9pszxj7b1_pcb561_j_bjr0000gn/T/opencode/phase16_gemini_probe.py
```

Deliverables: this report, `tests/test_phase16_llm_baseline.py` (15 baseline pins), measurement JSONs above.

**Next phase candidates (in priority order):**
1. RAG repair: add `pypdf`, re-ingest ECSS PDFs, verify legibility gate, retire garbage chunks (fixes B2).
2. Evidence-contract repair: add `evidence_id` to `EvidenceItem`, serialize residual numbers + window adequacy (fixes C1/C2, enables the NONEXISTENT_EVIDENCE guardrail).
3. Registry safety fix: declare `BATTERY_BELOW_FLOOR` on `CMD_SAFE_MODE_EXIT` (fixes H1).
4. Restore cloud inference with an API key and re-run the scorecard to fill the `NOT MEASURED` rows.
