# PHASE 22 — DUAL-BRANCH LOCAL/CLOUD ROUTER ARCHITECTURE PLAN

Status: PLAN ONLY — no code written, no production files modified.
Basis: Phase 21 frozen 47-case benchmark (`results/phase21/phase21_failure_metrics.json`),
repository inspection of the actual pipeline (commit 8de74a7, branch antigravity).

---

## 1. Executive Summary

SENTINEL today runs **one LLM provider per analysis**, selected statically by
`LLM_MODE` → `ModelMode` → `create_provider()`. Everything around the LLM is
already deterministic and hardened: anomaly detection, hypothesis generation,
physics validation, evidence allowlists, procedure allowlists, guardrails,
safety validation, and a chain-verified audit trail.

Phase 21 measured the local branch (Phi-3 Mini, n=47): top-1 34.0%, structured
validity 72.3%, evidence grounding 85.1%, S1-type (prompt-echo/truncation)
failures 23.4%, physics consistency 95.7%, and 100% deterministic-safety
containment (0 fabricated IDs survived 47 live runs). The cloud reference
(Gemini 2.5 Flash, Phase 20 + the 7 measurable Phase 21 cases) is 100%
structured validity, 100% top-1, ~5.2 s mean latency.

This plan specifies the **dual-branch router** that inserts between the
validated-evidence bundle and the safety validator:

```
VALIDATED EVIDENCE + PHYSICS ENGINE (existing, unchanged)
        → ROUTER / BRANCH POLICY (new, deterministic)
              → LOCAL BRANCH  (Phi-3, existing constrained pipeline)
              → CLOUD BRANCH  (Gemini, existing constrained pipeline + redaction)
        → ARBITRATION (new, deterministic precedence — never confidence-wins)
        → MERGE SEMANTICS (new, field-by-field, conservative)
        → PHYSICS RECHECK (existing reconcile_llm_claim, reused)
        → SAFETY VALIDATOR (existing, unchanged)
        → HUMAN REVIEW (existing operator-decision path, reused)
```

Key decisions made in this document, so an implementer makes no architectural
choices independently:

1. **Default hybrid route is sequential LOCAL→CLOUD**, not parallel.
   Parallel LOCAL+CLOUD exists as a policy value but is not the default.
2. **Escalation is decided only by deterministic signals** — provider errors,
   timeouts, parse failures, guardrail violations, `evidence_status`,
   deterministic hypothesis ambiguity. An LLM can never emit a field that
   causes the router to trust that same LLM.
3. **Arbitration is deterministic precedence**, not confidence comparison.
   Physics verdicts > guardrail validity > evidence contract > deterministic
   hypothesis scores > agreement tie-break. Unresolvable disagreement goes to
   human review with both rankings attached.
4. **`requires_human_review` is monotone**: any branch or any deterministic
   rule may set it true; no model output may clear it.
5. **The legacy free-form fallback is removed from the router path.** A
   both-branch failure terminates in an explicit DETERMINISTIC_ONLY state,
   never a silent downgrade to the unguarded legacy pipeline.
6. **Two existing discrepancies are identified** (§10.3) — the legacy path
   lacks the evidence/physics guardrails, and cloud redaction is not applied
   on the constrained streaming path. Phase 23 must fix the second before the
   cloud branch is enabled; both are documented, neither is modified now.

New components required: **8** (§16). Critical gap: **no branch point exists;
provider selection is a static process-wide mode, and the provider-level
privacy assertion is keyed to the `LLM_MODE` env var, so a hybrid mode must be
introduced explicitly** (§4, §10.3).

---

## 2. Current Architecture (traced from the repository)

Entry point for the full pipeline: `SentinelAgent.analyze_crash_dump_stream()`
in `app/agent/agent.py` (called by the `/analyze` SSE endpoint in
`app/main.py`). Stages in execution order:

| # | Stage | File / function | Input → Output | Authority | Deterministic? | Exists | Phase 22 calls it differently? |
|---|-------|-----------------|----------------|-----------|----------------|--------|-------------------------------|
| 1 | Ingest + canonicalize | `agent.py::analyze_crash_dump_stream`, `app/api/adapters.py::canonical_window / with_canonical_window` | raw crash dump → canonical telemetry dict | n/a | yes | EXISTS | No |
| 2 | Anomaly detection | `app/detection::run_detection_on_crash_dump` | crash dump → `DetectionReport` (channels, severities, detectors) | L3 evidence producer | yes | EXISTS | No |
| 3 | State estimation | `app/estimation::estimate_states / compute_residuals` | crash dump → state sequence + `ResidualReport` | L3 evidence producer | yes | EXISTS | No |
| 4 | Hypothesis generation | `app/diagnosis::generate_hypotheses(detection_report, crash_dump)` | detection + dump → `HypothesisSet` (fault_ids, scores, supporting/contradicting evidence IDs, causal chains) | L3 (defines the ONLY authorized fault-ID universe) | yes | EXISTS | No — router must treat its top-score margin as a routing signal |
| 5 | Physics validation | `app/validation/physics.py::validate_crash_dump` | crash dump → `PhysicsValidationReport` (per-hypothesis VALIDATED / INVALIDATED / UNCERTAIN verdicts) | **L2 — cannot be overridden by any model** | yes | EXISTS | Reused as-is for the post-merge PHYSICS RECHECK |
| 6 | Procedure retrieval (RAG) | `app/agent/rag.py::retrieve_procedures_traced` + `app/procedures/retrieval::retrieve_procedures` | query + fault cues → snippets + structured `procedure_results` | L3 (defines the ONLY authorized procedure-ID universe) | yes | EXISTS | No |
| 7a | Security: sanitization | `app/security/sanitization::sanitize_telemetry_payload_data` | dump → injection-neutralized payload | L1 boundary | yes | EXISTS | Must apply to BOTH branches (it already does at API entry, `main.py`) |
| 7b | Security: cloud redaction + transmission record | `app/security/exfiltration::apply_cloud_redaction / record_external_transmission` | dump → redacted copy + report | L1 boundary | yes | **PARTIALLY EXISTS** — applied ONLY in the legacy `analyze_crash_dump` path (agent.py:1049), NOT in the constrained streaming path | YES — must be invoked by the cloud branch adapter (§7) |
| 8 | Evidence bundle | `app/llm/ranker.py::build_ranking_input` + `compute_evidence_status` | all stage outputs → frozen `LLMRankingInput` incl. `evidence_status` (ADEQUATE/PARTIAL/INSUFFICIENT/CONTRADICTORY), `valid_fault_ids`, `valid_procedure_ids` | L3 contract | yes | EXISTS | Called ONCE per run; the same bundle feeds both branches |
| 9 | Constrained prompt | `app/llm/ranker.py::build_constrained_prompt` | `LLMRankingInput` → messages | L4 prompt | yes | EXISTS | No |
| 10 | Provider selection | `app/llm/provider.py::create_provider(mode, config)`; `ModelMode` from `LLM_MODE` env | mode string → exactly ONE of `GeminiProvider` / `LocalProvider` / `StubProvider` | config | yes | EXISTS | **YES — the router needs two providers in one run; today impossible without a new mode** |
| 11 | LLM call + parse + guardrails | `app/llm/ranker.py::run_constrained_ranking` → `_extract_json` → `LLMRankingOutput.from_dict` → `validate_ranking_output` | messages → (`LLMRankingOutput`, `GuardrailResult`, elapsed_ms); violations auto-corrected, originals preserved | L4 output gated by L1–L3 validators | call=no, validation=yes | EXISTS | Called ONCE PER BRANCH by the branch adapters, unchanged |
| 12 | Physics reconciliation | `app/validation/physics.py::reconcile_llm_claim` | deterministic verdict + model claim → verdict UNCHANGED + `LLMOverrideAttempt` record | **L2 enforcement point** | yes | EXISTS | Reused per-branch and on merged output |
| 13 | Conversion | `app/llm/ranker.py::convert_to_sentinel_output` | `LLMRankingOutput` + procedures → `SentinelOutput` dict (confidence monotonicity enforced, Phase 21) | contract | yes | EXISTS | Called on the MERGED/winner output |
| 14 | Safety validation | `app/agent/safety.py::validate_recovery_plan / apply_validation_to_output / derive_safety_status` | `SentinelOutput` + dump → `ValidationResult` (VALIDATED / PARTIALLY_BLOCKED / BLOCKED / REQUIRES_HUMAN_REVIEW) | **L1 — final authority on commands** | yes | EXISTS | Unchanged; runs AFTER merge |
| 15 | Audit | `app/audit/record.py::Stage` (INPUT, DETECTION, STATE_ESTIMATION, RAG, LLM, EXTERNAL_TRANSMISSION, HYPOTHESES, PHYSICS_VALIDATION, SAFETY_VALIDATION, DIAGNOSIS, OPERATOR_DECISION), chain-verified store | stage records → append-only run | audit | yes | EXISTS | YES — a ROUTING stage must be added (§13) |
| 16 | Human review | `SentinelOutput.requires_human_review` + `main.py::record_operator_decision` + audit `OPERATOR_DECISION` | output → operator decision (ACKNOWLEDGED/APPROVED/REJECTED/MODIFIED/ESCALATED) | **L5** | yes | EXISTS | Unchanged; router increases how often it is required, never decreases |
| 17 | Legacy fallback | `analyze_crash_dump_stream` catch-all → `analyze_crash_dump` (free-form prompt, schema+safety only, NO evidence/physics guardrails) | any constrained-stage exception → legacy result | degraded | call=no | EXISTS | **REMOVED from the router path** (§11) |

Current topology in one line:

```
Telemetry → Ingest/canonicalize → Detection → State estimation →
Hypothesis generation → Physics validation → RAG/procedures →
Evidence bundle → ONE provider (static mode) → guardrails →
SentinelOutput → safety validation → (human review)
```

---

## 3. Target Architecture

Authoritative topology for Phase 22 (from the mandate):

```
                    VALIDATED EVIDENCE
                           │
                     PHYSICS ENGINE
                           │
                    ROUTER / BRANCH POLICY
                       /           \
             LOCAL BRANCH       CLOUD BRANCH
               Phi-3             Gemini
                   \               /
                    └─ ARBITRATION ┘
                           │
                     MERGE SEMANTICS
                           │
                    PHYSICS RECHECK
                           │
                    SAFETY VALIDATOR
                           │
                    FINAL SENTINEL
                           │
                    HUMAN REVIEW (WHEN REQUIRED)
```

Node semantics:

- **ROUTER / BRANCH POLICY** — pure function of deterministic signals
  (§5). Decides one of: LOCAL_ONLY, LOCAL→CLOUD, CLOUD_ONLY, LOCAL+CLOUD,
  HUMAN_REVIEW_IMMEDIATE. Runs BEFORE any inference.
- **LOCAL BRANCH** — the existing constrained pipeline with `LocalProvider`
  (§6). Ends in LOCAL_ACCEPT / LOCAL_FAILURE / LOCAL_ESCALATION.
- **CLOUD BRANCH** — the same constrained pipeline with `GeminiProvider`,
  gated by redaction + transmission audit (§7).
- **ARBITRATION** — deterministic precedence over branch results (§8).
- **MERGE SEMANTICS** — conservative, field-by-field combination (§9).
- **PHYSICS RECHECK** — `reconcile_llm_claim` applied to the merged claims;
  verdicts recomputed, never inherited from a model.
- **SAFETY VALIDATOR** — unchanged; last deterministic gate.
- **HUMAN REVIEW** — terminal requirement whenever any deterministic rule or
  either branch set it.

The "PHYSICS RECHECK" node is deliberately not a second physics engine: it is
re-application of the SAME deterministic verdicts (already computed in stage 5
from the crash dump, which neither branch can alter) to whatever the merge
produced. Physics is a function of telemetry, not of model output.

---

## 4. Component Mapping (target node → repository)

| Target node | Status | Existing component | Gap |
|---|---|---|---|
| VALIDATED EVIDENCE | EXISTS | `build_ranking_input` + `compute_evidence_status` (`app/llm/ranker.py`) | None — one bundle, shared by both branches |
| PHYSICS ENGINE | EXISTS | `app/validation/physics.py` (`validate_crash_dump`, verdicts) | None |
| ROUTER / BRANCH POLICY | **MISSING** | — | New deterministic policy module (§5, §16) |
| LOCAL BRANCH | PARTIALLY EXISTS | `run_constrained_ranking` + `LocalProvider` + guardrails | Missing: branch-outcome classification (ACCEPT/FAILURE/ESCALATION), S1-type detection as a first-class failure signal, per-branch result envelope |
| CLOUD BRANCH | PARTIALLY EXISTS | `run_constrained_ranking` + `GeminiProvider` + `apply_cloud_redaction` | Missing: branch adapter that composes redaction + transmission record + constrained ranking (redaction is currently wired only into the legacy path); quota backoff for free-tier 429s (proven in `scripts/phase21_run_benchmark.py`, not in app code) |
| ARBITRATION | **MISSING** | — | New deterministic precedence engine (§8, §16) |
| MERGE SEMANTICS | **MISSING** | — | New field-by-field merge (§9, §16) |
| PHYSICS RECHECK | EXISTS | `reconcile_llm_claim` (+ guardrail rule 4 PHYSICS_OVERRIDE) | None — reuse |
| SAFETY VALIDATOR | EXISTS | `app/agent/safety.py` | None |
| FINAL SENTINEL | EXISTS | `convert_to_sentinel_output` → `SentinelOutput` | Called on merged output instead of single-branch output |
| HUMAN REVIEW | EXISTS | `requires_human_review`, `record_operator_decision`, audit OPERATOR_DECISION | None |
| ROUTING AUDIT | PARTIALLY EXISTS | `app/audit` (append-only, chain-verified, redacting recorder) | No ROUTING stage/reason codes yet (§13) |
| PROVIDER COEXISTENCE | PARTIALLY EXISTS | `create_provider`, `ProviderConfig` | `ModelMode` is a single static mode; `GeminiProvider.call()` raises when `LLM_MODE` env ∈ {local, fallback} (privacy assertion) — a HYBRID mode must be introduced and the assertion re-scoped to per-branch policy (§10.3) |

**MISSING component count: 3 wholly missing (branch policy, arbitration,
merge) + 5 partial gaps = 8 new work items, §16.**

---

## 5. Branch Policy

### 5.1 Decisions

The policy outputs exactly one of:

| Decision | Meaning |
|---|---|
| `LOCAL_ONLY` | Run local branch; accept its guardrailed result whatever it is; NO cloud call. Used when cloud transmission is prohibited (sovereign deployment, CONFIDENTIAL classification) or cloud is unavailable. |
| `LOCAL_THEN_CLOUD` | Run local first; escalate to cloud on LOCAL_FAILURE or LOCAL_ESCALATION. **Default hybrid decision.** |
| `CLOUD_ONLY` | Skip local. Used when local provider health check fails before inference, or deployment config is cloud-only. |
| `LOCAL_AND_CLOUD` | Run both (parallel or sequential); arbitrate. Used only under an explicit high-consequence configuration flag; NOT the default. Cost: double latency/quota; Phase 21 gives no evidence it is needed for accuracy. |
| `HUMAN_REVIEW_IMMEDIATE` | No branch may produce a diagnosis — deterministic evidence state already decides the outcome (§5.3, rule H1). Inference may still run solely to fill the audit record, but the final result is fixed: empty diagnosis, `requires_human_review=true`. |

### 5.2 Routing signals — semantics (no numeric thresholds in this phase)

For every signal: WHO produces it, CAN THE LLM modify it, IS IT TRUSTED,
CAN IT CAUSE ESCALATION.

| Signal | Producer | LLM can modify? | Trusted? | Causes escalation? |
|---|---|---|---|---|
| `evidence_status` | `compute_evidence_status()` — deterministic function of hypothesis evidence sets + window adequacy | **No.** Guardrails enforce it (INSUFFICIENT_EVIDENCE_CLAIM); prompt compliance irrelevant | YES (L3) | YES — INSUFFICIENT ⇒ HUMAN_REVIEW_IMMEDIATE (hard); CONTRADICTORY ⇒ soft cloud escalation |
| Evidence quantity (supporting/contradicting counts) | deterministic hypothesis generation | No | YES | Indirectly — it is the input to `evidence_status` |
| Structured-output validity | `_extract_json` + `LLMRankingOutput.from_dict` (deterministic parse of untrusted text) | No — the check is deterministic; the text is untrusted | YES (as a check) | YES — parse failure after one repair retry ⇒ LOCAL_FAILURE ⇒ cloud escalation |
| Evidence grounding | guardrails (`NONEXISTENT_EVIDENCE`, allowlist intersection) | No | YES | YES (soft) — any fabrication violation ⇒ LOCAL_ESCALATION when cloud permitted |
| Physics consistency | `validate_crash_dump` verdicts + guardrail rule 4 + `reconcile_llm_claim` | **No — structurally impossible** (reconcile has no branch that mutates the verdict) | YES (L2) | YES (hard) — PHYSICS_OVERRIDE attempt ⇒ escalation + forced human review flag |
| Hypothesis ambiguity | deterministic hypothesis set: margin between top-2 deterministic scores; count of physics-UNCERTAIN hypotheses | No | YES | YES (soft) — a narrow deterministic margin marks the case as "worth a second opinion" |
| Deterministic-top disagreement | comparison of local branch top-1 fault vs deterministic hypothesis-set top-1 | No (computed from validated fault_id only) | YES | YES (soft) — local model contradicting the deterministic ranking is the single best observable proxy for wrong-diagnosis cases (§15) |
| Confidence values | LLM output | Yes — that is exactly why they are distrusted | **NO** | **NO.** Confidence can never, alone, select a branch outcome or trigger escalation (anti-manipulation, §14) |
| Uncertainty text | LLM output, free text | Yes | NO | NO — informational only; may be surfaced to the operator |
| `requires_human_review` (model-emitted) | LLM output | Yes | Partially | **One-way only**: model setting it true is honored; model setting it false can never clear a deterministic or cross-branch true (§8 rule A9) |
| Procedure validity | guardrails (`INVALID_PROCEDURE`, allowlist) | No | YES | YES (soft) — violation stripped; contributes to escalation decision |
| Safety status | `derive_safety_status` after `validate_recovery_plan` | No | YES (L1) | Post-merge only — a BLOCKED plan ends in review, never in re-routing |
| Timeout | provider `timeout_seconds` (deterministic clock) | No | YES | YES (hard) — timeout ⇒ branch FAILURE |
| Local inference failure | `ProviderError` / connection refused / empty response | No | YES | YES (hard) ⇒ cloud escalation or LOCAL_ONLY degraded path |
| S1-type signature | deterministic detector on raw text (prompt-echo head, `finish_reason`-equivalent length exhaustion, ≥ ~2000-char unparseable blob — the classifier proven in `scripts/phase21_run_benchmark.py`) | No | YES | YES (hard) — S1 output is unusable; treated as LOCAL_FAILURE, not retried in-process (retry reproduces the same echo, Phase 21 §F) |
| RAG quality | retrieval trace: relevance scores, fallback-used flag, zero-results flag (`retrieve_procedures_traced`) | No | YES (deterministic metadata) | YES (soft) — zero procedures retrieved ⇒ empty `valid_procedure_ids` ⇒ procedure selection forbidden for both branches; contributes to escalation |
| Data classification | `app/security/exfiltration::classify_payload` | No | YES (L1) | YES (hard) — CONFIDENTIAL ⇒ cloud branch prohibited ⇒ LOCAL_ONLY |
| Provider availability | pre-flight health probe (local endpoint reachable; cloud key present) | No | YES | YES (hard) — unavailable branch excluded from policy input |

### 5.3 Policy rules (evaluated in order; first match wins)

- **H1 (insufficient evidence):** `evidence_status == INSUFFICIENT` ⇒
  `HUMAN_REVIEW_IMMEDIATE`. Rationale: Phase 21 showed the correct answer for
  these cases is an empty diagnosis; neither model adds information the
  telemetry lacks. The branch may still run (to audit model compliance) but
  the final result is fixed before inference.
- **H2 (sovereignty/classification):** payload CONFIDENTIAL, or deployment
  flag `ROUTER_CLOUD_ALLOWED=false`, or no cloud key ⇒ any decision degrades
  to `LOCAL_ONLY`.
- **H3 (local unavailable):** local health probe fails ⇒ `CLOUD_ONLY` (if H2
  permits) else `HUMAN_REVIEW_IMMEDIATE` with DETERMINISTIC_ONLY output.
- **H4 (both unavailable):** ⇒ DETERMINISTIC_ONLY terminal state (§11).
- **H5 (contradictory evidence):** `evidence_status == CONTRADICTORY` ⇒
  `LOCAL_THEN_CLOUD` with forced human review regardless of branch outcomes.
- **S1 (default):** all other cases ⇒ `LOCAL_THEN_CLOUD`.
- **S2 (configured):** deployment flag `ROUTER_PARALLEL=true` ⇒ S1 becomes
  `LOCAL_AND_CLOUD`.
- **S3 (local-only deployment):** `LLM_MODE=local` ⇒ `LOCAL_ONLY` (the
  provider privacy assertion remains the enforcement backstop).

All rules are functions of deterministic signals evaluated before any
inference. Nothing in the policy reads model output.

---

## 6. Local Branch Semantics

Flow (all existing components):

```
LLMRankingInput → build_constrained_prompt → LocalProvider.call
→ _extract_json → LLMRankingOutput.from_dict → validate_ranking_output
→ (per-hypothesis reconcile_llm_claim)
```

Plus a branch-specific S1-type detector on the raw text (Phase 21 §F
classifier). Retry budget: exactly ONE repair retry on parse failure
(existing `max_retries=1`); S1-type signatures are NOT retried.

### 6.1 LOCAL_ACCEPT

All of:
1. Raw response parsed into `LLMRankingOutput` (after ≤1 repair retry).
2. Not an S1-type signature.
3. `GuardrailResult.is_valid == True` — **zero violations**. (Phase 21: a
   corrected output is safe, but a branch that needed correction has
   demonstrated unreliability; the router treats correction as escalation
   evidence, not as success.)
4. The `evidence_status` contract honored (no INSUFFICIENT_EVIDENCE_CLAIM).
5. No PHYSICS_OVERRIDE attempt.
6. If `evidence_status` is PARTIAL: accept additionally requires the local
   top-1 to match the deterministic hypothesis-set top-1; otherwise
   LOCAL_ESCALATION (the deterministic layer cannot corroborate the model's
   reordering on partial evidence).

### 6.2 LOCAL_FAILURE

Any of:
- `ProviderError`, timeout, connection failure, empty response.
- JSON unparseable after one repair retry.
- S1-type signature (prompt echo / length exhaustion).

A LOCAL_FAILURE carries **no model claims whatsoever**. Nothing from a failed
local run enters arbitration as content; only the failure classification and
reason codes propagate. Invalid local output must never become trusted state.

### 6.3 LOCAL_ESCALATION

Parseable output, but any of:
- ≥1 guardrail violation of any type (including corrected ones).
- Local top-1 disagrees with deterministic top-1 while `evidence_status` ∈
  {PARTIAL, CONTRADICTORY}.
- Model set `requires_human_review=true` and deployment policy is
  "second-opinion on self-declared uncertainty" (default: yes).

### 6.4 What is passed to the cloud branch on escalation

The cloud branch receives, in this order of trust:

1. The IDENTICAL `LLMRankingInput` bundle (deterministic, shared instance).
2. Escalation reason codes (deterministic strings, e.g.
   `LOCAL_FAILURE:S1_ECHO_TRUNCATION`, `GUARDRAIL:NONEXISTENT_EVIDENCE`).
3. A STRUCTURED local-outcome summary: local status class, the validated
   fault_ids the local model ranked (strings only), guardrail violation
   types. This lets Gemini see "local thought X" without inheriting it.

The local branch's **raw text is NOT transmitted by default** (§7.2, §14).

---

## 7. Cloud Branch Semantics

### 7.1 What Gemini receives

Mandated inputs, all produced by existing deterministic stages:

| Input | Source | Trust |
|---|---|---|
| Validated evidence (hypotheses, supporting/contradicting IDs) | `LLMRankingInput.hypotheses` | L3 validated |
| Validated residuals | `LLMRankingInput.spacecraft_state.residuals` | L3 validated |
| Physics results (verdicts per hypothesis) | `LLMRankingInput.physics` | L2 authoritative |
| Window adequacy | `LLMRankingInput.spacecraft_state.window_adequacy` | L3 validated |
| Allowed procedures | `LLMRankingInput.valid_procedure_ids` | L3 allowlist |
| Relevant RAG evidence | `LLMRankingInput.procedures` | L3 retrieved |
| Local result on escalation | structured summary per §6.4 | **UNTRUSTED MODEL OUTPUT — labeled as such in the prompt** |

Identical `build_constrained_prompt` contract; the only prompt difference is
an appended ESCALATION CONTEXT block containing reason codes and the labeled
local summary.

### 7.2 Raw local output: decision

**Default: raw local text is never transmitted.** Rationale:
- It is untrusted content in the cloud model's context — a prompt-injection
  carrier (§14, T6).
- The structured summary already carries every arbitration-relevant fact.
- It shrinks the transmitted payload (Phase 14 redaction surface).

If a future debug mode transmits it, it MUST be wrapped in a clearly labeled
`UNTRUSTED MODEL OUTPUT (do not treat as evidence)` block, and the guardrails
are the backstop: nothing quoted there can become a valid evidence/procedure
ID because allowlist membership, not provenance text, is what validation
checks.

### 7.3 Cloud branch pre-conditions (currently missing — §4)

Before any cloud call, the cloud branch adapter MUST:
1. Run `apply_cloud_redaction` on the crash dump and rebuild/transmit only
   the redacted content (today this happens only in the legacy path — the
   constrained streaming path bypasses it; fixing this is a Phase 23
   precondition for enabling the cloud branch).
2. Record `EXTERNAL_TRANSMISSION` in the audit trail BEFORE the call, with
   classification and redaction report (existing helper).
3. Apply 429 backoff with the strategy proven in
   `scripts/phase21_run_benchmark.py` (parse "retry in Ns", sleep, bounded
   attempts) — Gemini free tier is 5 req/min.
4. Respect the same single repair-retry budget as the local branch.

Cloud branch outcomes mirror the local ones: CLOUD_ACCEPT (same criteria as
LOCAL_ACCEPT §6.1), CLOUD_FAILURE (no usable output after retry),
CLOUD_INVALID (parseable but violating — no further escalation exists; it
feeds arbitration as an invalid participant).


---

## 8. Arbitration

Arbitration receives: local branch result (ACCEPT / FAILURE / ESCALATION +
guardrailed output), cloud branch result (if run), and the deterministic
context (`LLMRankingInput`, physics verdicts, deterministic hypothesis
scores). It is a pure deterministic function. **It never compares raw
confidence values to pick a winner.**

### 8.1 Precedence ladder (highest first)

- **P0 — Physics.** Any hypothesis whose deterministic verdict is INVALIDATED
  is excluded from the final ranking regardless of which branch proposed it
  or at what confidence. Both branches ranking an INVALIDATED fault #1 ⇒
  BOTH DISAGREE WITH PHYSICS case (rule A6).
- **P1 — Evidence contract.** If `evidence_status == INSUFFICIENT`, the
  final output is the empty-diagnosis contract (no ranked faults, no
  evidence, no procedures, `requires_human_review=true`) **no matter what
  either branch said** (policy H1 already fixed this before inference; P1 is
  the enforcement backstop).
- **P2 — Participation.** Only outputs with `GuardrailResult.is_valid`
  participate. A violating output participates only in its corrected form
  AND only when the other branch is absent or also invalid (see A3/A4);
  otherwise the valid branch wins outright.

### 8.2 Case table (mandated cases)

| Case | Rule | Outcome |
|---|---|---|
| LOCAL = CLOUD (same top-1 fault) | A1 | Adopt the LOCAL output (sovereignty + latency tie-break). Record agreement. Confidence taken from local as-is. |
| LOCAL ≠ CLOUD | A2 | Resolve by deterministic discriminators, in order: (a) physics — if exactly one of the two faults is VALIDATED, that fault wins; (b) deterministic score — the fault with the higher deterministic hypothesis score wins; (c) evidence support — the fault with more deterministic supporting-evidence IDs wins. If still tied ⇒ A10 CONFLICT. The adopted output is re-derived from the WINNING BRANCH's full output; `requires_human_review` is forced true because the branches disagreed. |
| LOCAL VALID + CLOUD INVALID/FAILURE | A3 | Adopt local. Cloud failure is not evidence against local. If the local result was a LOCAL_ESCALATION trigger, keep forced human review. |
| LOCAL INVALID/FAILURE + CLOUD VALID | A4 | Adopt cloud — this is the purpose of escalation. Record the escalation reason codes in the diagnosis audit. |
| BOTH INVALID / BOTH FAILURE | A5 | DETERMINISTIC_ONLY terminal state (§11): final ranking is the deterministic hypothesis-set order with confidences capped to the PARTIAL-corroboration semantics (§9.3), recovery plan empty, `requires_human_review=true`, safety_status derived from the empty plan. Never fall back to the legacy free-form pipeline. |
| BOTH DISAGREE WITH PHYSICS (both top-1 INVALIDATED) | A6 | Both top-1 claims discarded. Re-rank among non-INVALIDATED hypotheses using deterministic order (A5 machinery). Forced human review + `LLMOverrideAttempt`-style audit entry for each branch. |
| BOTH LOW CONFIDENCE | A7 | "Low confidence" is not read from the numbers alone. Operational definition: both branches set `requires_human_review=true`, OR both top confidences sit at or below the deterministic-floor semantics (§9.3), OR `evidence_status ∈ {PARTIAL, CONTRADICTORY}` and the branches disagree. Outcome: human review; diagnosis still emitted if A1/A2 resolved it. |
| ONE MODEL CITES INVALID EVIDENCE | A8 | The cited ID was already stripped by guardrails before arbitration. Arbitration proceeds on corrected outputs; the violating branch loses participation priority per P2/A3/A4. The violation type is recorded in the routing audit. |
| ONE MODEL SELECTS INVALID PROCEDURE | A8' | Identical treatment: stripped by guardrails; branch loses priority; recorded. |
| ONE MODEL REQUIRES HUMAN REVIEW | A9 | Final `requires_human_review = true`. **Monotone escalation: any source may raise it, no model output may lower it.** |
| UNRESOLVABLE DISAGREEMENT (A2 tie) | A10 | CONFLICT state: no model output adopted. Deterministic-order ranking (A5 machinery), both branch rankings attached to the audit record, mandatory human review with an explicit "branches disagreed" operator-facing note. |

### 8.3 What arbitration is forbidden to do

- Compare confidences across branches to choose a winner.
- Weight branches by historical accuracy at runtime (no implicit learned
  policy in Phase 23; that would reintroduce unvalidated decision-making).
- Invent a third diagnosis not present in either branch's validated output.
- Downgrade `requires_human_review` from true to false under any condition.

---

## 9. Merge Semantics

Merge assembles the final `SentinelOutput` fields from the arbitration
outcome. Per-field semantics:

| Field | Semantics | Rule |
|---|---|---|
| Hypothesis set (which faults appear) | WINNER-SOURCED | From the adopted branch (A1–A4). Under A5/A6/A10: the deterministic hypothesis set, non-INVALIDATED only. |
| Ranking order | DETERMINISTIC RECOMPUTATION on conflict; winner's order otherwise | If arbitration resolved by A2 discriminators or fell to A5/A6/A10, order is recomputed from deterministic scores + physics verdicts. Otherwise the winning branch's order stands (with the Phase 21 monotonicity sort applied regardless). |
| Confidence | **NEVER averaged.** DETERMINISTIC RECOMPUTATION rules | Adopted as-is from the winning branch in A1/A3/A4. In any conflict-resolved path (A2/A5/A6/A10) confidences are recomputed from deterministic scores with a fixed spread, capped so the top value expresses "deterministic support only" (semantics: no model claim is being endorsed). The exact mapping is a Phase 23 calibration parameter — its SEMANTICS are fixed here: model-free paths must not carry model-grade confidence. |
| Evidence IDs (supporting) | INTERSECTION, then allowlist | Only IDs cited by BOTH branches survive into the merged field, and only if in the deterministic evidence universe. In winner-only paths (A3/A4): the winner's cited IDs intersected with the allowlist. IDs cited by one branch only are logged in the audit, never merged. |
| Contradicting evidence IDs | VALIDATED UNION | Any real (allowlist-valid) contradicting ID cited by either branch survives — contradiction claims are safety-relevant and conservative retention is correct. |
| Causal chains | WINNER-SOURCED, never merged | Free-text narrative; mixing chains from two models produces fiction. Conflict paths carry the deterministic causal chain from the hypothesis set instead. |
| Uncertainty | WINNER-SOURCED + deterministic append | Winner's uncertainty text plus a deterministic suffix naming window-adequacy limits and branch disagreement when applicable. |
| Procedure IDs | INTERSECTION, allowlist-gated | Only procedures selected by BOTH branches and present in `valid_procedure_ids`. Empty intersection ⇒ empty recovery plan (conservative: the operator adds procedures, models only propose). Winner-only paths: winner's selection ∩ allowlist. |
| Reasoning summary | WINNER-SOURCED; DETERMINISTIC TEMPLATE on conflict | Conflict paths (A5/A6/A10) use a deterministic template: which branches ran, why neither/both were adopted, what the deterministic fallback chose. Models never narrate their own rejection. |
| `requires_human_review` | OR (monotone) | true if any of: either branch true, H1/H5 policy, A2/A5/A6/A7/A10 paths, safety REQUIRES_HUMAN_REVIEW. Never false unless ALL of those are false. |
| Physics verdicts / safety status | DETERMINISTIC RECOMPUTATION | Never merged, never inherited from a branch. `reconcile_llm_claim` re-run on merged claims; `validate_recovery_plan` re-run on the merged plan. |

---

## 10. Authority Hierarchy

Mandated hierarchy vs the existing implementation:

| Level | Mandate | Implementation | Match? |
|---|---|---|---|
| L1 | Deterministic safety constraints | `app/agent/safety.py` (whitelist via `app/validation/command_registry.py`, condition checks, `derive_safety_status`), plus Phase 14 security boundary (sanitization, redaction, transmission audit) | ✅ matches |
| L2 | Deterministic physics validation | `app/validation/physics.py`; `reconcile_llm_claim` structurally cannot mutate verdicts; guardrail PHYSICS_OVERRIDE | ✅ matches |
| L3 | Validated evidence / RAG | hypothesis evidence sets, residual reports, procedure allowlists, `compute_evidence_status`, guardrail allowlist checks | ✅ matches |
| L4 | LLM reasoning | constrained ranker: may rank/explain within the L1–L3 envelope only | ✅ matches on the constrained path |
| L5 | Human review | `requires_human_review`, operator-decision endpoint, audit OPERATOR_DECISION | ✅ matches |

### 10.3 Discrepancies found (documented, NOT modified in this phase)

1. **D1 — Legacy free-form path bypasses L2/L3 gating.**
   `analyze_crash_dump` (agent.py:953) validates only output schema + L1
   safety; it has no evidence allowlist, no physics-override guardrail, no
   `evidence_status` contract. The streaming entry point silently falls back
   to it on any constrained-stage exception (agent.py:1988). Consequence: a
   constraint violation that the router would treat as branch failure today
   degrades to a LESS-guarded pipeline. Phase 23 action: the router path
   never calls the legacy path; the legacy path is retained only for
   ablation/API backward compatibility and flagged as such.
2. **D2 — Cloud redaction absent from the constrained streaming path.**
   `apply_cloud_redaction` + `record_external_transmission` run only inside
   `analyze_crash_dump` (agent.py:1049–1063). The constrained path builds
   `LLMRankingInput` straight from `crash_dict`; in a CLOUD-mode deployment
   that prompt leaves the host without the recorded redaction step. Phase 23
   precondition: the cloud branch adapter (§7.3) owns redaction before ANY
   Gemini call; enabling the cloud branch without this fix is prohibited.
3. **D3 — Provider privacy assertion is keyed to a process-wide env var.**
   `GeminiProvider.call` raises when `LLM_MODE ∈ {local, fallback}`. A hybrid
   run under `LLM_MODE=local` is therefore impossible by construction — the
   correct fail-safe today, an obstacle for the router. Phase 23 action:
   introduce `ModelMode.HYBRID` with an explicit per-call sovereignty flag;
   the provider assertion then checks the FLAG (cloud-allowed for this
   payload, decided by the deterministic policy H2), not the env string. The
   assertion stays fail-closed: absent an explicit allow, no cloud call.

---

## 11. Fail-Closed Behavior

Every failure condition terminates in an explicit named state. There is no
silent fallback anywhere in the router path.

Terminal states:

- `FINAL_VALIDATED` — merged output passed physics recheck + safety.
- `FINAL_WITH_REVIEW` — output emitted, human review required.
- `DETERMINISTIC_ONLY` — no model output adopted; deterministic ranking +
  empty recovery plan + mandatory review.
- `NO_INFERENCE` — neither branch could run (both unavailable); detection +
  physics + hypotheses still reported; mandatory review.
- `BLOCKED_TERMINAL` — safety blocked the entire plan; review mandatory.

| Condition | Terminal state / action |
|---|---|
| Malformed JSON (either branch, after 1 repair retry) | Branch FAILURE; arbitration with remaining branch; both failing ⇒ DETERMINISTIC_ONLY |
| Timeout | Identical to malformed: branch FAILURE (hard), no partial output used |
| Empty response | Branch FAILURE |
| Prompt echo (S1-type signature) | Branch FAILURE — explicitly NOT retried in-process (Phase 21: reproduction rate ~100% on the same prompt) |
| Fabricated evidence | Guardrails strip IDs; branch treated as violating (loses arbitration priority); escalation if other branch not yet run; audit records the attempt |
| Fabricated procedure | Identical to fabricated evidence (INVALID_PROCEDURE) |
| Insufficient telemetry (`evidence_status=INSUFFICIENT`) | H1 ⇒ HUMAN_REVIEW_IMMEDIATE; final is the empty-diagnosis contract regardless of model behavior |
| Missing channels (window inadequacy) | Deterministic window-adequacy drives evidence_status to PARTIAL/INSUFFICIENT; same routing as above; the adequacy reason string is surfaced to the operator |
| Physics contradiction (model vs verdict) | Deterministic verdict stands (`reconcile_llm_claim`); attempt recorded; branch loses priority; forced human review flag |
| Safety violation (post-merge) | `validate_recovery_plan` blocks offending steps; all blocked ⇒ BLOCKED_TERMINAL; partial ⇒ FINAL_WITH_REVIEW |
| Local/cloud disagreement | A2 discriminators; unresolved ⇒ A10 CONFLICT ⇒ DETERMINISTIC_ONLY + both rankings audited + mandatory review |
| Cloud unavailable (policy allowed it) | LOCAL_ONLY degradation recorded as routing reason `CLOUD_UNAVAILABLE`; local result stands per A3 semantics; if local also failed ⇒ DETERMINISTIC_ONLY |
| Local unavailable | CLOUD_ONLY (H3) if permitted; else DETERMINISTIC_ONLY |
| Both unavailable | NO_INFERENCE — deterministic stages only (detection, hypotheses, physics) reported; mandatory review. This state is reachable and must be a first-class outcome, not an exception. |

---

## 12. Router State Machine

States (derived from the existing architecture; the mandated list is covered):

```
INPUT_VALIDATION → ROUTING_DECISION → ┬→ LOCAL_INFERENCE → LOCAL_VALIDATION ─┐
                                      │        (escalation needed?)            │
                                      │            │ yes                       │
                                      │            ▼                           │
                                      │  CLOUD_TRANSMISSION_GATE →             │
                                      │  CLOUD_INFERENCE → CLOUD_VALIDATION ───┤
                                      │                                        ▼
                                      └→ (CLOUD_ONLY path joins at) ────→ ARBITRATION
                                                                               │
                                                                               ▼
                                   NO_INFERENCE ◄─(both unavailable)      MERGE
                                                                               │
                                                                               ▼
                                                                       PHYSICS_RECHECK
                                                                               │
                                                                               ▼
                                                                    SAFETY_VALIDATION
                                                                               │
                                                    ┌──────────────────────────┤
                                                    ▼                          ▼
                                              HUMAN_REVIEW               FINAL_OUTPUT
                                                    │                          │
                                                    └──────────► FINAL_OUTPUT ◄┘
```

Transitions:

| # | SOURCE → DESTINATION | TRIGGER | AUTHORITY | FAILURE BEHAVIOR |
|---|---|---|---|---|
| T1 | INPUT_VALIDATION → ROUTING_DECISION | dump parses, canonical window built, sanitization applied | deterministic | invalid dump ⇒ run FAILED at input; no routing, audit INPUT=FAILED |
| T2 | ROUTING_DECISION → LOCAL_INFERENCE | policy ∈ {LOCAL_ONLY, LOCAL_THEN_CLOUD, LOCAL_AND_CLOUD}; local probe OK | deterministic policy | probe fails ⇒ H3 re-route |
| T3 | ROUTING_DECISION → CLOUD_TRANSMISSION_GATE | policy = CLOUD_ONLY | deterministic policy | redaction failure ⇒ abort cloud, degrade to LOCAL_ONLY + reason CLOUD_REDACTION_FAILED |
| T4 | ROUTING_DECISION → HUMAN_REVIEW (via fixed outcome) | H1 (INSUFFICIENT) or H4 (both unavailable ⇒ NO_INFERENCE first) | deterministic policy | none — terminal intent |
| T5 | LOCAL_INFERENCE → LOCAL_VALIDATION | raw response received (or failure classified) | provider | timeout/error ⇒ FAILURE class, proceed to T7 |
| T6 | LOCAL_VALIDATION → ARBITRATION | LOCAL_ACCEPT and policy LOCAL_ONLY, or LOCAL_THEN_CLOUD with ACCEPT and no forced review | guardrails (deterministic) | validation crash ⇒ treat as FAILURE |
| T7 | LOCAL_VALIDATION → CLOUD_TRANSMISSION_GATE | LOCAL_FAILURE or LOCAL_ESCALATION, policy allows cloud | guardrails + policy | cloud gate refuses (H2) ⇒ ARBITRATION with local-only input |
| T8 | LOCAL_VALIDATION → ARBITRATION | LOCAL_FAILURE/ESCALATION but cloud NOT allowed | policy | deterministic |
| T9 | CLOUD_TRANSMISSION_GATE → CLOUD_INFERENCE | redaction applied + transmission audited + sovereignty flag set | L1 security | any gate failure ⇒ T3 failure path |
| T10 | CLOUD_INFERENCE → CLOUD_VALIDATION | response or failure class | provider | same as T5 |
| T11 | CLOUD_VALIDATION → ARBITRATION | always (valid, invalid, or failed) | guardrails | n/a |
| T12 | ARBITRATION → MERGE | rule A1–A4/A8 resolved an adopted output | deterministic arbitration | A5/A6/A10 ⇒ MERGE in deterministic-recomputation mode |
| T13 | MERGE → PHYSICS_RECHECK | merged fields assembled | deterministic merge | merge defect ⇒ DETERMINISTIC_ONLY (fail-closed) |
| T14 | PHYSICS_RECHECK → SAFETY_VALIDATION | verdicts re-asserted unchanged; overrides recorded | L2 | structurally cannot fail the verdict; recording only |
| T15 | SAFETY_VALIDATION → FINAL_OUTPUT | safety ∈ {VALIDATED} and review flag false | L1 | PARTIALLY_BLOCKED/BLOCKED/REQUIRES_HUMAN_REVIEW ⇒ HUMAN_REVIEW first |
| T16 | SAFETY_VALIDATION → HUMAN_REVIEW | any review trigger (§9 monotone OR) | L1/L5 | n/a |
| T17 | HUMAN_REVIEW → FINAL_OUTPUT | operator decision recorded (ACKNOWLEDGED/APPROVED/REJECTED/MODIFIED/ESCALATED) | L5 | no decision within SLA ⇒ output remains in review; no auto-approval, ever |

No transition exists from any validation state back into inference (no
re-run loop): retries are bounded INSIDE a branch (one repair retry), and the
state machine itself is acyclic.

---

## 13. Audit Trail

Every routing decision is explainable from the audit record alone.

New audit stage: `Stage.ROUTING` (added to `app/audit/record.py`), recorded
once per run, plus per-branch sub-records reusing the existing `Stage.LLM`
shape with a branch discriminator.

Mandatory recorded fields:

| Category | Fields |
|---|---|
| Routing decision | decision enum (LOCAL_ONLY / LOCAL_THEN_CLOUD / CLOUD_ONLY / LOCAL_AND_CLOUD / HUMAN_REVIEW_IMMEDIATE), policy rule that fired (H1–H5, S1–S3) |
| Reason codes | ordered list, e.g. `LOCAL_FAILURE:S1_ECHO_TRUNCATION`, `GUARDRAIL:NONEXISTENT_EVIDENCE`, `POLICY:H1_INSUFFICIENT`, `CLOUD_UNAVAILABLE`, `ESCALATION:DETERMINISTIC_TOP_DISAGREEMENT` |
| Deterministic signals snapshot | evidence_status, window adequacy status, hypothesis count + top-2 margin bucket, data classification, provider probe results |
| Local result status | class (ACCEPT/FAILURE/ESCALATION/not-run), violation types, top fault_id, elapsed_ms, retry count |
| Cloud result status | same + redaction report reference + transmission record ID |
| Arbitration outcome | rule applied (A1–A10), adopted branch (local/cloud/deterministic/none) |
| Merge outcome | per-field provenance tags (winner/conflict-recomputed/intersection/empty) |
| Physics verdict | report ID + any LLMOverrideAttempt records |
| Safety verdict | ValidationResult summary, blocked step count |
| Human-review requirement | final boolean + every source that contributed a true |

Prohibited content (enforced by the existing redacting recorder):

- API keys / credentials (recorder already redacts; cloud key must never
  enter any record — transmission records store provider+model+classification
  only).
- Raw telemetry beyond what INPUT already stores.
- Raw cloud response bodies (metadata: length, parse status, violation
  types). Local raw text may be stored locally per existing LLM-stage policy
  but is never transmitted.


---

## 14. Security Analysis

| # | Threat | Analysis | Mitigation (this architecture) |
|---|---|---|---|
| T1 | Prompt injection via telemetry | Injection-shaped strings in dump fields could steer either model | EXISTING: `sanitize_telemetry_payload_data` at API entry neutralizes before any prompt (both branches inherit it). Residual effect bounded by allowlists — injected text cannot mint fault/evidence/procedure IDs |
| T2 | Routing manipulation (model steers itself to the more permissive branch) | A model would need to influence policy signals evaluated BEFORE it runs | IMPOSSIBLE BY CONSTRUCTION: ROUTING_DECISION consumes only pre-inference deterministic signals (§5.3). Post-hoc escalation triggers (parse failure, violations) move the run toward MORE validation, never less |
| T3 | Confidence manipulation | Inflated confidence wins arbitration | FORBIDDEN: arbitration never compares confidences (§8.3). Confidence affects nothing except display and the monotonicity invariant |
| T4 | Evidence spoofing | Fabricated evidence IDs survive to the final output | EXISTING guardrails (allowlist intersection, empty-set-authorizes-nothing, Phase 21) + merge INTERSECTION (§9) makes survival require fooling allowlists AND both branches. Phase 21 live evidence: 0/47 survived |
| T5 | Procedure spoofing | Unauthorized command/procedure reaches the recovery plan | Allowlist guardrail + safety whitelist (L1) + merge intersection. Double-gated, unchanged |
| T6 | Model-to-model contamination | Compromised local output poisons the cloud prompt (or vice versa) | Raw local text not transmitted by default (§7.2); structured summary is fault_ids + reason codes only; the escalation block is labeled UNTRUSTED. Reverse direction impossible: sequential default means local never sees cloud output; parallel mode feeds both from the same deterministic bundle, never from each other |
| T7 | Cloud escalation abuse / DoS via repeated escalation | Attacker crafts inputs that always escalate, burning quota/cost | Escalation budget: ≤1 cloud call per analysis (no router-level re-escalation loops; state machine acyclic §12). 429 backoff + pacing already proven. Policy H2 can disable cloud entirely per deployment |
| T8 | Unsafe merge behavior | Merging creates a claim neither model made | Merge never unions hypothesis sets or causal chains; intersections + deterministic recomputation only (§9). No field in the merged output lacks a single authoritative source |
| T9 | Evidence-status override | Model asserts ADEQUATE where telemetry is INSUFFICIENT | EXISTING: INSUFFICIENT_EVIDENCE_CLAIM guardrail + P1 enforcement; H1 fixes the outcome pre-inference |
| T10 | Privacy/exfiltration via cloud branch | Telemetry leaves the host unredacted | D2 fix is a Phase 23 precondition (§7.3, §10.3); transmission audited BEFORE the call; sovereignty flag fail-closed |

**The mandated key question:** *"Can an LLM-generated field cause the router
to trust that same LLM?"*

**Answer: NO**, and the argument is structural, not behavioral:

1. Every routing SIGNAL is produced before inference or by deterministic
   validators applied to model output (§5.2). No signal is read from model
   output without passing a deterministic check first.
2. The one model-emitted field with routing power, `requires_human_review`,
   is one-way: it can only make the outcome MORE conservative (§8 A9).
3. Guardrail violation flags — which do trigger escalation — are produced by
   `validate_ranking_output`, not by the model's self-assessment.
4. Parse/echo/timeout classes are measured on the raw byte stream by
   deterministic classifiers.

A model can at worst cause its OWN output to be rejected or escalated to a
second model plus human review. It cannot cause acceptance.

---

## 15. 47-Case Routing Mapping (Phase 21 frozen results)

Failure composition measured in `results/phase21/phase21_failure_metrics.json`
(per-case classification, n=47):

| Bucket | n | Scenario IDs | Theoretical routing in the target topology |
|---|---|---|---|
| CORRECT (valid, top-1 right, no violations) | 14 | 2, 5, 6, 1003, 1009, 1010, 1013, 1016, 1021, 1022, 1027, 1028, 1033, 1500 | **LOCAL_ACCEPT** — never escalate; this is the sovereign path's payoff |
| S1 prompt-echo/truncation | 11 | 1002, 1005, 1007, 1012, 1014, 1015, 1019, 1024, 1030, 1032, 1036 | **LOCAL_FAILURE (hard) → cloud escalation.** No usable local output exists; classifier fires on raw text. Gemini's 100% structural validity recovers all 11 in principle |
| Malformed JSON (non-S1) | 2 | 1004, 1031 | Repair retry (bounded 1) → still failing ⇒ **LOCAL_FAILURE → cloud escalation** |
| Parseable but empty ranking | 7 | 1, 200, 1006, 1008, 1018, 1020, 1026 | Split by evidence state: where `evidence_status=INSUFFICIENT` an empty ranking is the CORRECT contract ⇒ **HUMAN_REVIEW** (no escalation — the cloud has no extra telemetry); where evidence is ADEQUATE/PARTIAL an empty ranking is a local contract failure ⇒ **LOCAL_ESCALATION → cloud** (case 1's schema-drift class lands here) |
| S200-type fabrication under INSUFFICIENT | 4 | 4, 203, 201, 202 | Guardrails already correct to the INSUFFICIENT contract (Phase 21: post-guardrail compliance 100%). Routing: **H1 fixed outcome HUMAN_REVIEW**; escalation NOT triggered — cloud inference cannot add evidence, so escalation adds cost without information. Branch marked violating for audit |
| Fabrication on valid case (guardrailed) | 2 | 3, 1034 | Violation ⇒ branch loses priority ⇒ **escalate when cloud permitted.** Case 1034 additionally: wrong fault but legitimately retrieved procedure passed — a retrieval-allowlist breadth issue; cloud second opinion + recorded disagreement |
| Wrong diagnosis, clean contract | 7 | 1001, 1011, 1017, 1023, 1025, 1029, 1035 | The honest boundary: no deterministic signal observes CORRECTNESS. Escalation depends on observable correlates: (a) local top-1 ≠ deterministic top-1 ⇒ soft escalation (the best proxy); (b) PARTIAL evidence + disagreement with deterministic order ⇒ escalation; (c) local top-1 = deterministic top-1 and contract clean ⇒ LOCAL_ACCEPT — these residual errors are the irreducible local-only risk, quantified by the shadow run (§18.10), not hidden |
| Evidence-state cross-tab (valid-JSON cases) | — | ADEQUATE 10/19 correct, PARTIAL 5/10, INSUFFICIENT 1/5 | Corroborates the policy: accuracy degrades monotonically with evidence quality; PARTIAL is the natural soft-escalation band, INSUFFICIENT is a review band, not an inference band |

**Theoretical escalation volume:** hard escalations ≈ 13–15 (S1 + malformed +
empty-ranking-on-adequate-evidence); soft escalations ≈ up to 9 (wrong-
diagnosis correlates + guardrailed fabrication); local-accepted ≈ 14–16;
review-band (INSUFFICIENT contract) ≈ 5–6. Exact per-case assignments for
the wrong-diagnosis bucket require the deterministic-top comparison, which
the Phase 23 shadow run computes on the same frozen set — deliberately NOT
computed here to avoid retrofitting routing rules to known answers.

Design implications confirmed by the mapping:

1. The router's PRIMARY value is recovering the ~13 structural failures
   (27.7% of cases) via cloud escalation — not improving diagnosis accuracy.
2. INSUFFICIENT-band cases must route to review, not to the cloud.
3. Wrong-diagnosis-clean cases set the ceiling: routing cannot detect
   silently-wrong-but-confident local output on ADEQUATE evidence. That
   residual risk is disclosed in the audit as the cost of LOCAL acceptance,
   and is the standing argument for keeping the operator in the loop.

---

## 16. Required New Components (minimum set)

| # | Name | Responsibility | Input | Output | Why necessary | Interacts with |
|---|---|---|---|---|---|---|
| 1 | `RoutingDecision` + `RoutingReason` contract (proposed: `app/llm/router_contract.py`) | Typed decision enum, ordered reason-code list, signal snapshot, per-branch result envelope (`BranchResult`) | — | dataclasses/enums | Nothing can be built or audited without the shared vocabulary | everything |
| 2 | `BranchPolicy` (`app/llm/branch_policy.py`) | Pure function: deterministic signals → decision per §5.3 rules | signal snapshot (evidence_status, classification, probes, margins) | `RoutingDecision` | The routing brain; must be unit-testable with zero I/O | router contract, security classification |
| 3 | `LocalBranchRunner` (`app/llm/branches.py`) | Runs `run_constrained_ranking` with `LocalProvider`; classifies ACCEPT/FAILURE/ESCALATION per §6; S1-type detector | `LLMRankingInput`, physics report | `BranchResult` | Wraps existing code; adds the missing outcome classification | ranker, provider |
| 4 | `CloudBranchRunner` (same module) | Redaction → transmission audit → `run_constrained_ranking` with `GeminiProvider` → classification; 429 backoff; sovereignty flag check | same + escalation context | `BranchResult` | Fixes D2 by construction; the ONLY code path authorized to call Gemini in hybrid mode | security/exfiltration, ranker, provider, audit |
| 5 | `Arbitrator` (`app/llm/arbitration.py`) | Rules A1–A10 per §8 | two `BranchResult`s + deterministic context | adopted-branch decision + rule ID | Core of the topology; does not exist today | branch results, physics verdicts |
| 6 | `MergeResolver` (`app/llm/merge.py`) | §9 field semantics → merged `LLMRankingOutput` → `convert_to_sentinel_output` | arbitration outcome + both branch outputs | merged `SentinelOutput` dict | Merge does not exist; must not be ad hoc in the orchestrator | ranker conversion |
| 7 | `RouterOrchestrator` (`app/llm/router.py`) | State machine of §12 wiring policy → branches → arbitration → merge → recheck → safety; feature-flagged entry replacing the Stage-7 block of `analyze_crash_dump_stream` | crash pipeline stage outputs | final `SentinelOutput` + routing audit record | The single composition point; keeps agent.py thin | all of the above |
| 8 | Audit extension: `Stage.ROUTING` + reason-code schema (`app/audit/record.py` modification) | Records §13 fields | routing artifacts | append-only record | Every decision explainable | audit store |

Plus two bounded MODIFICATIONS (not new components): `ModelMode.HYBRID` +
re-scoped provider privacy assertion (D3), and removal of the legacy
free-form fallback from the streaming path under the router flag (D1).

Nothing else is added. Physics, safety, detection, hypothesis generation,
RAG, evidence contract, and the constrained prompt are reused unchanged.

---

## 17. Test Plan (designed before implementation)

All tests use `StubProvider`-shaped fakes for both branches — no live model
calls in CI. Expected results are stated as postconditions.

| ID | Scenario | Setup | Expected result |
|---|---|---|---|
| A | Local accepted | stub local returns clean valid output; policy LOCAL_THEN_CLOUD | final output = local's; NO cloud call made; audit shows decision + rule S1→A1-not-needed; review flag false unless contract demands |
| B | Local malformed | stub local returns garbage twice (initial + repair) | BranchResult=FAILURE; cloud stub called exactly once; final = cloud output; reason code LOCAL_FAILURE:MALFORMED_JSON |
| C | Local fabricated evidence | stub local cites nonexistent evidence ID | guardrail violation recorded; escalation; final evidence IDs exclude fabrication; even if cloud unavailable, fabricated ID absent from final |
| D | Local physics conflict | local ranks an INVALIDATED fault #1 with high confidence | PHYSICS_OVERRIDE recorded; invalidated fault NOT final top-1 regardless of confidence; review flag true |
| E | Local procedure violation | local selects non-allowlisted procedure | procedure stripped; safety never sees it; final plan ⊆ allowlist |
| F | Local → Gemini escalation | local timeout stub | exactly one cloud call; transmission record exists BEFORE call; redaction report attached; final = cloud |
| G | Cloud accepted (CLOUD_ONLY policy) | local probe failing stub | no local call; cloud output adopted; reason CLOUD_ONLY:H3 |
| H | Local/cloud agreement | both stubs same top-1, both valid | final adopts LOCAL output (A1); agreement recorded; single review flag per contract |
| I | Local/cloud disagreement, deterministic discriminator | stubs differ; one fault physics-VALIDATED | winner = VALIDATED fault's branch (A2a); review flag FORCED true; merge provenance tags correct |
| I' | Unresolvable disagreement | stubs differ; all discriminators tied | A10: deterministic-order ranking, both rankings in audit, mandatory review, no model confidence in final |
| J | Both invalid | both stubs violating/unparseable | A5 DETERMINISTIC_ONLY: deterministic ranking, empty recovery plan, review true, safety derived; legacy pipeline NEVER invoked (assert no legacy call) |
| K | Insufficient evidence | evidence_status=INSUFFICIENT; local stub asserts diagnosis anyway | H1: final is empty-diagnosis contract; local violation recorded; cloud NOT called for diagnosis purposes |
| L | Safety block | merged plan contains non-whitelisted command (stub) | blocked steps recorded; BLOCKED_TERMINAL if all blocked; review mandatory |
| M | Human review | any setup with branch-emitted review=true | final review=true even though all validations pass; A9 monotonicity asserted (also test: model false cannot clear deterministic true) |
| N | Cloud unavailable | policy allows cloud; cloud stub raises | degrade to local-only (A3 semantics); reason CLOUD_UNAVAILABLE; no crash |
| O | Local unavailable | local probe stub fails | CLOUD_ONLY (H3) when permitted; LOCAL_ONLY+degraded otherwise |
| P | Both unavailable | both probes fail | NO_INFERENCE terminal: deterministic stages reported, review mandatory, explicit state — NOT an exception |

Additional regression requirements:
- Q1: routing never mutates `LLMRankingInput` (frozen-dataclass invariant).
- Q2: `reconcile_llm_claim` verdicts identical before and after merge.
- Q3: 47-case frozen dataset labels untouched by any routing code.
- Q4: full existing suite (1102 tests) stays green; router behind a flag.

---

## 18. Implementation Order

Ordered for Phase 23; each step independently testable, all behind a
`ROUTER_ENABLED` feature flag until step 11:

1. **Router contract** — `router_contract.py` (decisions, reason codes,
   `BranchResult`). No behavior yet. Tests: serialization, enum exhaustiveness.
2. **Mode + sovereignty plumbing** — `ModelMode.HYBRID`; re-scope the
   `GeminiProvider` privacy assertion to the per-call sovereignty flag (D3).
   Tests: LOCAL still refuses cloud (existing tests unchanged); HYBRID
   without flag also refuses (fail-closed).
3. **Branch policy** — pure module, rules H1–H5/S1–S3. Tests: table-driven
   over every rule with synthetic signal snapshots.
4. **Local branch runner** — wraps existing `run_constrained_ranking`;
   S1-type classifier ported from `scripts/phase21_run_benchmark.py`.
   Tests: A, B, C, D, E shapes with stubs.
5. **Cloud branch runner** — redaction + transmission audit + backoff (D2
   fix; hard precondition before ANY hybrid enablement). Tests: F, G, N +
   redaction-before-call assertion.
6. **Arbitrator** — rules A1–A10. Tests: H, I, I', J, M.
7. **Merge resolver** — §9 semantics. Tests: merge provenance, procedure
   intersection, confidence never averaged, review OR-monotonicity.
8. **Orchestrator + state machine** — wire §12 into
   `analyze_crash_dump_stream` behind `ROUTER_ENABLED`; legacy fallback
   removed from the flagged path (D1). Tests: Q4 + end-to-end stub runs for
   K, L, O, P.
9. **Audit ROUTING stage** — schema + recorder wiring + chain verification.
   Tests: every §13 field present; no secrets (redaction test with a fake
   key).
10. **Shadow mode** — router computes routing/arbitration/merge but the
    FINAL output remains today's single-provider output; both recorded. Run
    against the frozen 47-case set with live Phi-3 (+ throttled Gemini).
    Exit data: per-case routing decisions, counterfactual top-1, escalation
    volume, cloud call count, latency P95.
11. **Enablement decision** — review shadow data against §20 criteria; only
    then flip `ROUTER_ENABLED` for the streaming endpoint. Legacy single-
    provider mode stays available via `LLM_MODE` for the whole of Phase 23.

---

## 19. Open Questions

1. **Parallel LOCAL+CLOUD**: worth the double quota/cost for high-consequence
   scenarios? Phase 21 provides no accuracy argument for it; defer to post-
   shadow data. The policy value exists but defaults off.
2. **Gemini free-tier quota in production**: 5 req/min means a burst of
   concurrent escalations queues or drops. Decision needed: paid tier, or an
   explicit escalation rate limiter with DETERMINISTIC_ONLY degradation on
   budget exhaustion. This plan assumes the latter until told otherwise.
3. **Deterministic-floor confidence mapping** (§9.3): the semantics are fixed
   (model-free paths carry no model-grade confidence); the exact spread is a
   calibration parameter to set during the shadow run.
4. **Raw local text transmission** (§7.2): kept off by default. If debugging
   ever demands it, the labeling + allowlist backstop are specified, but the
   privacy cost must be re-reviewed (Phase 14 boundary).
5. **Case 1034's retrieval breadth**: wrong-fault cases where a legitimately
   retrieved procedure passes all gates. Retrieval-side fix, out of router
   scope; tracked for a future procedures phase.
6. **Timeout budget for sequential hybrid**: local mean ~87 s + cloud ~5 s.
   Operator-facing SLA for the escalated path needs an explicit ceiling
   (proposal: local timeout bounds the local leg; cloud leg bounded by
   backoff attempts). Value to be set in Phase 23 with operational input.

---

## 20. Phase 23 Entry Criteria

Phase 23 (implementation) may begin when ALL of the following hold:

1. This document reviewed and approved; no section marked open question may
   be resolved silently during implementation — resolutions are recorded
   here first.
2. Frozen 47-case dataset (`local_benchmark_v1.json`) unchanged and its
   labels writable by no routing code (Q3 enforced).
3. Test plan §17 written and failing for the right reasons against stubs
   (tests land with step 1, before router behavior exists).
4. D2 (cloud redaction on the constrained path) fix designed as part of step
   5 and treated as a launch-blocker for any cloud enablement.
5. Feature flag default OFF; single-provider behavior byte-identical when
   the flag is off (Q4).
6. Success criteria for the shadow run agreed in advance:
   - structural-failure recovery: ≥ 11/13 S1+malformed cases produce a valid
     cloud output in shadow replay;
   - zero fabricated IDs reach any final (shadow) output;
   - escalation volume within §15's theoretical band (hard ≈ 13–15);
   - zero routing decisions unexplainable from the audit record;
   - no degradation of the 14 local-accepted cases.
7. Full suite green at the last Phase 21 checkpoint (1102 tests, commit
   8de74a7) remains the regression baseline.

---

*Prepared as the Phase 22 deliverable. No production files were modified; no
code was written; nothing was committed or pushed.*
