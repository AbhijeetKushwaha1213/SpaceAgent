# PHASE 21 — LOCAL LLM FAILURE ANALYSIS + CONTRACT HARDENING

Date: 2026-08-20
Model under test: Microsoft Phi-3 Mini 3.8B-Instruct (`phi3:mini`, Q4_0, Ollama)
Reference model: Gemini 2.5 Flash
Evaluation set: `local_benchmark_v1` v1.0.0 (47 frozen cases)

---

## A. Executive Summary

Phase 21 determined whether Phi-3's two Phase 20 failures (S1 prompt-echo,
S200 hallucinated evidence) were integration artifacts or genuine capability
limits, hardened the LLM contract, and expanded the benchmark from 6 to 47
frozen cases.

**Headline findings**

1. **S1 root cause is reproducible and structural.** A controlled
   reproduction captured `finish_reason="length"` with 1024/1024 completion
   tokens consumed while the model verbatim echoed the prompt context
   (first emitted key: `"scenario_id"`) and never began the requested JSON.
   Prompt was 2,050 tokens of a 4,096 window → **not** context overflow.
   On the expanded set, S1-type failures occur at **23.4%** (11/47) and
   concentrate on longer prompts (≥7 hypotheses).
2. **S200 root cause was a missing contract + a guardrail gap.** The Phase 20
   prompt carried no explicit insufficient-evidence rule, and
   `validate_ranking_output` skipped *all* evidence validation when the valid
   evidence set was empty (`if valid_evidence:`), so fabricated tokens like
   `anomaly_summary` and `procedure_1` passed with **zero violations**.
   Both were fixed. The original S200 case now passes the raw contract;
   4/5 INSUFFICIENT cases still violate it, but guardrails now strip 100%
   of fabricated IDs and force human review.
3. **The expanded benchmark exposes a much weaker Phi-3 boundary than the
   original 6 scenarios suggested.** Top-1 accuracy fell from 83.3% (Phase 20)
   to 34.0% on 47 defensible cases; structured-output problems (bad JSON or
   schema drift) affect 40.4% of cases.
4. **Safety after deterministic validation remains 100%.** No fabricated
   evidence ID and no invented procedure survived guardrails + safety
   validation in any of the 47 runs.
5. **Fine-tuning is NOT justified at this time.** The dominant failure modes
   are structured-output reliability, prompt echo, and output-token budget —
   general small-model limitations that training data does not fix. The
   domain-reasoning component (17% of cases) is the only part fine-tuning
   could plausibly address, and it cannot be isolated until the format
   failures stop masking it.
6. Two additional integration bugs were found and fixed during measurement:
   a confidence-monotonicity crash (2/47 cases) and duplicated-ID pass-through
   at parse time.

---

## B. Current Phi-3 Failure Modes (expanded 47-case set)

| Metric (Phi-3, n=47) | Value |
|---|---|
| Top-1 accuracy | 34.0% |
| Structured-output validity (parseable + schema-conformant) | 72.3% |
| Evidence grounding | 85.1% |
| Fabricated evidence rate | 10.6% (5 cases) |
| Procedure validity (vs frozen labels) | 87.2% |
| Physics consistency | 95.7% |
| Safety violations pre-guardrail | 6 cases |
| Unsafe outputs after guardrails | 0 fabricated IDs survived (see §G note on case 1034) |
| Insufficient-evidence handling (raw model compliance) | 20.0% (1/5) |
| Insufficient-evidence handling (after guardrails) | 100% (5/5) |
| S1-type failure rate | 23.4% (11/47) |
| S200-type failure rate | 8.5% (4/47) |
| Mean latency | 86.9 s |
| P50 / P95 latency | 74.7 s / 171.4 s |

Failure composition (mutually exclusive primary cause per case, 31 failed cases):

| Class | Count | Share | Taxonomy |
|---|---|---|---|
| S1-type: prompt echo + 1024-token truncation | 11 | 35% | B+C (output limit + instruction following) |
| Domain misranking with valid structured output | 8 | 26% | F (domain reasoning) |
| Schema drift: valid JSON, single-hypothesis object instead of wrapper | 6 | 19% | C+F (schema complexity) |
| S200-type: fabrication under INSUFFICIENT evidence | 4 | 13% | G (small-model confabulation) |
| Pipeline crash (confidence monotonicity) — fixed | 2 | 6% | A (integration) |

---

## C. S1 Investigation

**Reproduction** (`scripts/phase21_s1_repro.py`, artifacts in
`results/phase21/s1_repro_*`):

| Measurement | Value |
|---|---|
| System prompt | 2,017 chars (~577 tokens) |
| User prompt | 8,880 chars (~2,538 tokens) |
| Measured prompt tokens (Ollama usage) | **2,050** |
| Model context window | 4,096 |
| Requested max output tokens | 1,024 (Phase 20 contract, unchanged) |
| Actual generated tokens | **1,024 / 1,024** |
| `finish_reason` | **`length`** |
| First key emitted | `"scenario_id"` (input-context key) |
| Began with `ranked_hypotheses` | **No** |
| Complete JSON produced | **No** |
| Wall time | 88.4 s |

**Verdict:** failure cause is **B (output-token limit) + C (instruction
following)**, not A (context overflow: 2,050 < 4,096), not D (Ollama honored
`response_format` — JSON mode is exactly what made the echo syntactically
"valid" JSON-shaped), and not primarily F. The model treats the large input
JSON block as the continuation target and echoes it; the 1,024-token budget is
exhausted before the requested output section begins.

**Repeatability:** on the expanded set, 11/47 cases failed this exact way.
All 11 were simulated scenarios with ≥7 hypotheses (longer prompts); the 5
short hand-built scenarios did not reproduce it in this run. The failure is
prompt-length-correlated and repeatable — a single run is not enough, and the
Phase 20 1/6 rate was an underestimate.

**Why raising `max_tokens` is not the fix (not attempted):** the echo
consumes tokens before any requested content is produced. A larger budget
would extend the echo, not prevent it. The structural fixes (shorter prompts,
schema-only continuation cues, or a model with stronger instruction
separation) belong to Phase 22 router/prompt-structure work.

---

## D. S200 Investigation

**Phase 20 behavior** (from `phi3_baseline_raw.json`, scenario 200):

- Raw output: `fault_id: "fault_1"` at confidence 0.85; cited evidence
  `anomaly_count`, `anomaly_summary`, `anomalous_channels`; selected
  procedures `procedure_1`, `procedure_2`, `procedure_3` — all fabricated
  generic tokens.
- `guardrail_violations: []` — nothing was flagged.

**Root causes (both verified in source):**

1. **Prompt contract gap.** The Phase 20 prompt did not state what to do when
   the input carries no evidence IDs: no "do not invent evidence IDs", no
   "confidence = 0", no "requires_human_review = true" rule for the
   missing-channel condition (`window_adequacy = MISSING_REQUIRED_CHANNELS`).
2. **Guardrail gating bug.** `validate_ranking_output` wrapped evidence
   checks in `if valid_evidence:`. On S200 the valid evidence set is empty,
   so the entire `NONEXISTENT_EVIDENCE` check — and the corrected-output
   filtering — was skipped. Same pattern guarded procedure filtering.

**Fixes (minimal):**

- Added machine-readable `evidence_status` (ADEQUATE / PARTIAL / INSUFFICIENT /
  CONTRADICTORY) computed deterministically by `compute_evidence_status` from
  hypothesis evidence sets + window adequacy; serialized into the prompt
  (`EVIDENCE STATUS: …` block) with explicit INSUFFICIENT rules (empty lists,
  confidence 0, human review, no procedures).
- Removed the empty-set gating: evidence/procedure filtering now always runs;
  an empty allowlist rejects everything.
- New violation type `INSUFFICIENT_EVIDENCE_CLAIM`: any positive confidence,
  any citation, or any procedure under INSUFFICIENT status is flagged
  regardless of prompt compliance.

**Before/after:**

| Case | Before (Phase 20) | After (Phase 21, hardened contract) |
|---|---|---|
| S200 | fabricated tokens, 0 violations flagged | clean output, confidence 0, review forced — **passes raw contract** (13.7 s) |
| S201/S202/S203/id 4 | n/a (not in Phase 20 set) | still violate raw contract (`fault_1`, `evidence_id_N`…) but 9–10 guardrail violations each, all fabricated IDs stripped, review forced |

The hardened prompt fixes the case it was derived from but does **not**
generalize to all INSUFFICIENT inputs: raw model compliance is 1/5 (20%).
Safety is preserved by deterministic guardrails, not by the model.

---

## E. Output Contract Analysis (Part 1 audit results)

Audit of the actual current source (`app/llm/models.py`, `ranker.py`,
`provider.py`) against the supplied excerpt:

- **No duplicated keyword arguments or duplicate fields exist.** The excerpt
  showing duplicated `ranked_hypotheses`/`supporting_evidence_ids`/…
  assignments was a display artifact; the real dataclass constructs each
  field exactly once. No dead legacy-output path exists.
- **Genuine issues found and fixed:**
  1. `LLMRankingOutput.from_dict` accepted duplicated IDs in model output
     (e.g. the same EVID twice), inflating grounding metrics. Fixed with
     order-preserving dedup (`_clean_id_list`).
  2. Guardrail empty-set gating (see §D).
  3. No machine-readable evidence state reached the model (see §D).
  4. **Found during Part 11:** `convert_to_sentinel_output` sorted by the
     model's claimed ranks, but `SentinelOutput` requires confidence to be
     non-increasing with rank. Cases 1004/1031 crashed the *entire pipeline*
     with a pydantic ValidationError on valid model output. Fixed by sorting
     by confidence (rank as tie-break) and capping padding confidence at the
     lowest real confidence. Regression tests added.
- Prompt/output contract is otherwise consistent: one prompt builder, one
  parser, one guardrail path, one converter. No schema mismatch between
  `LLMRankingOutput` and the prompt's requested JSON shape.

Regression tests: `tests/test_phase21_contract_hardening.py` (30 tests).

---

## F. Evidence-ID Security

`tests/test_phase21_contract_hardening.py::TestEvidenceIdConstraints` covers:
valid ID, nonexistent ID (`EVID-FAKE-001`), empty evidence, duplicated
evidence, cross-scenario ID, malformed ID.

**Result:** fabricated evidence is never exposed as validated evidence.

- Guardrails raise `NONEXISTENT_EVIDENCE` for any ID outside the input set,
  including when the input set is empty (the S200 gap is closed).
- Corrected output filtering strips every non-member; measured surviving
  fabricated evidence across all 47 live runs: **0**.
- Under INSUFFICIENT status, *any* citation is flagged
  (`INSUFFICIENT_EVIDENCE_CLAIM`) because no citation can be grounded.
- Live fabricated-evidence attempts (5 cases: ids 3, 4, 201, 202, 203) were
  all neutralized before reaching the final output.

## G. Procedure-ID Security

`TestProcedureConstraints` covers: valid retrieved procedure, nonexistent
procedure, procedure from another scenario, empty list, duplicates.

- Guardrails raise `INVALID_PROCEDURE` for any ID outside
  `valid_procedure_ids` (the retrieved set); corrected output strips them.
- Live runs: 4 cases attempted unauthorized procedures; 0 invented procedure
  IDs survived to the recovery plan.
- **Boundary finding (case 1034):** Phi-3 selected `PROC-MULTI-CASCADE-001`
  for a `TCS_THERMAL_RUNAWAY` scenario and deterministic safety validation
  passed it (VALIDATED, 3 steps). This was **not** a fabrication: the
  procedure exists and was legitimately retrieved (relevance retrieval with
  `min_relevance=0.2` returned it), so the guardrail allowlist — defined as
  "retrieved procedures" — accepted it. It is a *wrong-fault selection*
  against the frozen label, exposing that the retrieval allowlist is broader
  than the fault→procedure mapping. Recommended for Phase 22: restrict
  `valid_procedure_ids` to procedures whose `fault_class` matches a live
  hypothesis. No production code was changed for this in Phase 21 to avoid
  moving guardrail semantics mid-measurement.

---

## H. Expanded Dataset

Builder: `scripts/phase21_build_dataset.py` →
`app/evaluation/datasets/local_benchmark_v1.json` (frozen, v1.0.0, 47 cases).

Composition (existing frameworks only — no invented physics):

| Source | Cases | Provenance |
|---|---|---|
| Hand-built preset scenarios (ids 1,2,3,5,6) | 5 | SYNTHETIC |
| ESA ADB crash dumps (ids 4, 200–203) | 5 | REAL / SYNTHETIC_FROM_REAL_METADATA |
| Seeded `SatelliteFaultSimulator` faults (ids 1001–1036) | 36 | SYNTHETIC |
| Nominal baseline (id 1500) | 1 | SYNTHETIC |

Category coverage: single faults (15, incl. 1 safety-sensitive),
physics-invalid-alternative variants (8), missing-window variants (6),
ambiguous/multiple anomalies (6), insufficient telemetry / missing channels
(4 + the 5 ESA dumps), nominal (1), procedure-selection and conflicting
evidence spread across the simulated set.

Every case carries frozen labels derived **only** from the deterministic
layer: `scenario_id`, `fault_type`, `expected_top1`, `required_evidence`,
`all_evidence_ids` (forbidden-evidence rule), `allowed_procedures`,
`physics_expectation`, `safety_expectation`, `evidence_status`,
`deterministic_top`. `label_policy` in the file forbids model-derived label
changes. Distribution: 30 ADEQUATE / 12 PARTIAL / 5 INSUFFICIENT.

36 seeded simulator cases + 11 existing = 47 ≥ the 30-case target; all fault
behavior comes from the validated simulator and real ESA metadata.

---

## I. Gemini Results

**Constraint:** the expanded Gemini run exhausted the free-tier quota
(5 req/min, HTTP 429) after the first cases; a throttled runner now exists
(`scripts/phase21_run_benchmark.py` with 429 backoff + 13 s pacing), but the
re-run was deferred per phase direction. Measured subset: **7/47 cases**
(the 5 presets + 2 ESA dumps).

| Metric | Gemini (7/47) | Gemini (Phase 20, 6 cases) |
|---|---|---|
| Top-1 accuracy | 100% | 100% |
| Structured output | 100% | 100% |
| Evidence grounding | 100% | 100% |
| Fabricated evidence | 0% | 0% |
| Physics consistency | 100% | 100% |
| INSUFFICIENT handling (raw) | 100% (2/2) | 100% (1/1) |
| Mean latency | 5.2 s | ~5.1 s |

No behavioral difference was observed on the measured subset; the Phase 20
full-set numbers remain the best Gemini reference until a throttled re-run.

## J. Phi-3 Results

Full 47-case results: `results/phase21/benchmark_phi3_expanded.json`;
aggregates: `results/phase21/phase21_failure_metrics.json`.

| Metric | Phase 20 (n=6) | Phase 21 (n=47) |
|---|---|---|
| Top-1 accuracy | 83.3% | **34.0%** |
| Structured output | 83.3% | **72.3%** |
| Evidence grounding | 83.3% | 85.1% |
| Physics consistency | 100% | 95.7% |
| Safety after validation | 100% | 100% (0 fabricated IDs survived) |
| Mean latency | 87.4 s | 86.9 s |
| P50 / P95 | 85.6 / 168.0 s | 74.7 / 171.4 s |

Systematic domain errors (valid structured output, wrong ranking):

- `COMMS_TRANSPONDER_LOSS` → `AOCS_SENSOR_FAULT` in 5/6 COMMS cases
  (1011, 1017, 1023, 1029, 1035).
- `EPS_SOLAR_UNDERVOLT` → `EPS_BATTERY_DEGRADATION` in 2/6 EPS cases.
- `TCS_THERMAL_RUNAWAY` → `MULTI_CASCADE` in 1034.

Schema-drift cases (1, 1006, 1008, 1018, 1020, 1026): model emitted a single
hypothesis object (`"hypothesis_id": …` first key) instead of the wrapper —
the same shape seen at experiment level E.

---

## K. Failure Comparison

| Dimension | Gemini 2.5 Flash | Phi-3 Mini |
|---|---|---|
| Structured-output reliability | 100% | 59.6% free of any format problem |
| Prompt echo / truncation | never observed | 23.4% |
| Evidence fabrication | never observed | 10.6% raw, 0% surviving |
| Domain ranking on hard cases | correct on all measured | 17.0% misrank with valid format |
| INSUFFICIENT discipline | obeys contract raw | obeys only via guardrails (1/5 raw) |
| Latency | ~5 s | 87 s mean, 171 s P95 |

The two models fail differently: Gemini showed no failures on the measured
subset, while Phi-3's failures are dominated by *format*, not *knowledge* —
among the 34 cases where Phi-3 produced parseable output, top-1 accuracy is
47.1%; among the 27 cases where it produced a non-empty ranking at all, it is
15/27 = 55.6%, with errors concentrating in two recurring confusions
(COMMS↔AOCS sensor, EPS solar↔battery).

## L. Fine-Tuning Decision

Failure classification per Part 12 taxonomy:

- **A. Integration:** 2 cases (monotonicity crash) — fixed in code.
- **B. Output-token/context economics:** 11 cases — S1-type echo burns the
  1,024-token budget before output begins. Training does not change the
  budget or the echo tendency at these prompt lengths.
- **C. Structured-output reliability:** 6 schema-drift cases + part of the
  S1 population. Experiment levels A/B (minimal contract) were 100% reliable;
  reliability degrades with schema complexity — a general small-model trait.
- **F. Domain reasoning:** 8 cases — the only fine-tuning-addressable class.
- **G. Small-model confabulation:** 4 S200-type cases — already fully
  contained by deterministic guardrails.

Decision criteria check:

- Evidence pipeline correct: **yes** (deterministic, unit-tested).
- RAG correct: **yes** (grounding measured; allowlist enforced).
- Prompt/schema correct: **hardened**, but Phi-3 still fails the format on
  40.4% of cases — the failure distribution is not yet attributable to
  domain knowledge.
- Repeatable: **yes** (S1 and S200 patterns recur across many cases).
- Domain-specific: **mostly no** — dominant failures are format/echo/length.
- Training examples could address them: only the 8 domain-misranking cases.

**FINE-TUNING: NOT-JUSTIFIED (at this time).**
The dominant, repeatable failures are latency, output-token economics,
structured-output reliability, and basic instruction following — precisely
the classes fine-tuning does not reliably solve for a 3.8B Q4 model. The
correctable domain component (≈17% of cases) is currently masked by format
failures; it should be re-measured after Phase 22 prompt-structure/router
work before any training investment. If Phi-3 remains in scope, LoRA
fine-tuning on Sentinel format+domain pairs becomes justified only if format
validity first exceeds ~95% via prompt restructuring.

## M. Router Readiness

Phase 21 explicitly does **not** implement routing. Readiness inputs for
Phase 22:

- A reliable failure distribution now exists (47 frozen cases, per-case
  labels, deterministic scoring script).
- Local-model boundary: safe for *nothing that requires raw model trust* —
  every Phi-3 output must traverse guardrails; 23% of calls return no usable
  structured output at all.
- Latency boundary: 87 s mean makes Phi-3 unsuitable for interactive paths.
- Guardrails are proven load-bearing: 100% containment of fabricated evidence
  and invented procedures across 47 live local runs.

**ROUTER: READY (for design in Phase 22).**

---

## Regression & artifacts

- Full suite: **1100 passed** (pre-fix run) — post-fix re-run recorded in
  `results/phase21/pytest_full_2.log` (see commit message).
- New tests: `tests/test_phase21_contract_hardening.py` (30 tests incl.
  monotonicity regression).
- Scripts: `phase21_s1_repro.py`, `phase21_structured_output_experiment.py`,
  `phase21_build_dataset.py`, `phase21_run_benchmark.py`, `phase21_measure.py`.
- Artifacts: `results/phase21/` (repro analysis, experiment JSON, dataset,
  per-provider raw results, failure metrics, logs).

### Structured-output experiment (Part 3) summary

| Level | Description | JSON valid | Schema complete |
|---|---|---|---|
| A | minimal schema, 3 trials | 100% | 100% |
| B | schema + 1 hypothesis, 3 trials | 100% | 100% |
| C | schema + evidence IDs, 3 trials | 100% | 0% (nested IDs per-hypothesis instead of top-level keys) |
| D | schema + causal chain, 3 trials | 100% | 0% (same drift) |
| E | full production prompt | 100% | 0% (emitted single hypothesis object) |
| F | full prompt, no `response_format` | 0% | 0% (prompt echo, `finish_reason=length` — S1) |

Interpretation: with JSON mode, Phi-3 virtually always emits *parseable*
JSON; what breaks at complexity level C+ is *shape fidelity*, not
syntax. Without JSON mode, output collapses into prompt continuation.
