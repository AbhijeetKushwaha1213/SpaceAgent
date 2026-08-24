# PHASE 23 STEP 2 — BRANCH POLICY + LOCAL BRANCH RUNNER REPORT

Scope: the deterministic branch policy and the local-branch adapter. The
router remains **disabled** (`ROUTER_ENABLED=false`); the production
execution path is unchanged.

---

## A. Objective

Implement exactly two dormant components on top of the Step 1 contracts:

1. **BranchPolicy** — the pure, deterministic answer to "given deterministic
   pipeline state, should Sentinel execute the local branch, escalate,
   require review, or perform no inference?"
2. **LocalBranchRunner** — an *adapter* around the existing Phi-3 constrained
   pipeline (never a second implementation of it) that converts the outcome
   into a Step 1 `BranchResult` with honest failure mapping.

Not implemented in this step (see §L): CloudBranchRunner, Arbitrator,
MergeResolver, RouterOrchestrator, parallel execution, production routing,
HYBRID mode activation, cloud redaction, Gemini-through-router.

## B. BranchPolicy design

Location: `app/llm/branch_policy.py` (183 lines).

- `PolicyInput` — frozen dataclass holding **only deterministic,
  pre-inference signals**: `evidence_status` (value of the existing
  `EvidenceStatus` enum), `safety_blocked`, `physics_space_invalidated`,
  `local_available`, `human_review_required`, `hypotheses_generated`.
  No field is ever model-derived. `signal_snapshot()` emits the
  (name, value) audit tuple consumed by `RoutingRecord`.
- `BranchPolicy.evaluate(state) -> RoutingRecord` — rules evaluated in a
  fixed priority order, first match wins. Returns a `RoutingRecord` with no
  branch results attached (branches have not run at decision time).

Purity guarantees (Part 3): no network, no filesystem mutation, no model
calls, no randomness, no timestamps, no environment-dependent hidden
behavior. Identical inputs always produce identical decisions (test 8).
The policy **cannot see** model confidence or model-generated reasoning —
its signature accepts only `PolicyInput` (tests 9–10).

## C. Policy decision table

| Priority | Condition | Decision | Reason | Human review |
|---|---|---|---|---|
| R1 | `safety_blocked` | BLOCKED | SAFETY_BLOCK | forced True |
| R7-gate | `evidence_status` outside `EvidenceStatus` (malformed) | HUMAN_REVIEW | UNRESOLVED_AMBIGUITY | forced True |
| R6 | `human_review_required` already True | HUMAN_REVIEW | HUMAN_REVIEW_REQUIRED | stays True (monotone) |
| R2 | `evidence_status == INSUFFICIENT` | HUMAN_REVIEW | INSUFFICIENT_EVIDENCE | forced True |
| R3 | `physics_space_invalidated` | HUMAN_REVIEW | PHYSICS_CONFLICT | forced True |
| R4 | local available ∧ hypotheses generated (evidence adequate/partial/contradictory) | LOCAL_ACCEPT | VALID_LOCAL_RESULT | False |
| R5 | `not local_available` | CLOUD_ESCALATE | LOCAL_UNAVAILABLE | False |
| R7 | no deterministic hypothesis space | NO_INFERENCE | UNRESOLVED_AMBIGUITY | forced True |

Semantics notes:

- **R1** blocks *everything* — no local run, no cloud escalation, no
  inference may attempt to override a terminal deterministic safety block.
- **R2** uses the repository's existing Phase 21/22 semantics (policy H1):
  missing evidence ⇒ empty diagnosis + mandatory human review; the LLM is
  never asked to compensate for missing telemetry.
- **R3** fires only when physics invalidated the *whole* hypothesis space;
  per-hypothesis INVALIDATED verdicts are handled by guardrails post-LLM.
- **R6** placement before R4/R5 makes downgrade structurally impossible.
- **R7** fail-closed default: unknown/malformed inputs can never yield an
  optimistic `LOCAL_ACCEPT`.

## D. LocalBranchRunner design

Location: `app/llm/local_branch.py` (301 lines).

The runner reuses the existing pipeline end-to-end — it adds nothing except
classification and contract conversion:

```
ranking_input → build_constrained_prompt   (existing)
              → provider.call               (existing LocalProvider)
              → _extract_json               (existing parser)
              → LLMRankingOutput.from_dict  (existing typed parse)
              → validate_ranking_output     (existing guardrails)
              → BranchResult                (Step 1 contract)
```

Key properties:

- **Bounded repair retry only** (`max_retries=1` default, negative values
  rejected; identical to the existing `run_constrained_ranking` repair
  convention). S1-type prompt echoes are never retried in-process (Phase 21:
  reproduction on the same prompt is near-certain).
- **Real latency + attempts**: `elapsed_ms` via `time.perf_counter()` around
  the actual call, `attempts` counted truthfully. Latency is audit metadata
  only — it never influences validity.
- **Identity from configuration**: `provider_name` / `model_name` are read
  from the injected `LLMProvider` instance (`LocalProvider` reads the
  configured Phi-3 model), never hardcoded.
- **Raw text cap**: untrusted raw completion stored only as a 500-char
  `raw_text_head` diagnostic blob.

## E. Phi-3 failure mapping (Phase 21 distribution)

| Phase 21 failure | Detected by | BranchOutcome | RoutingReason |
|---|---|---|---|
| Prompt-echo truncation (S1, 23.4%) | unparseable ∧ first JSON key ∈ {scenario_id, satellite_id, window, telemetry} ∨ >2000 chars | FAILURE | PROMPT_ECHO_TRUNCATION |
| Output-token exhaustion / invalid JSON | `_extract_json` fails after bounded repair retry | FAILURE | INVALID_STRUCTURED_OUTPUT |
| Schema drift (parseable, wrong shape) | `LLMRankingOutput.from_dict` raises | FAILURE | INVALID_STRUCTURED_OUTPUT |
| Hallucinated evidence IDs (S200-type) | existing guardrails `NONEXISTENT_EVIDENCE` / `UNSUPPORTED_HYPOTHESIS` / `INVENTED_TELEMETRY` | FAILURE | EVIDENCE_FAILURE |
| Insufficient-evidence claim | existing guardrail `INSUFFICIENT_EVIDENCE_CLAIM` | FAILURE | INSUFFICIENT_EVIDENCE |
| Invalid procedure ID | existing guardrail `INVALID_PROCEDURE` / `UNKNOWN_COMMAND` | FAILURE | PROCEDURE_INVALID |
| Physics override attempt | existing guardrail `PHYSICS_OVERRIDE` | FAILURE | PHYSICS_CONFLICT |
| Timeout | ProviderError message contains "timeout"/"timed out" | FAILURE | LOCAL_TIMEOUT |
| Daemon unavailable | other ProviderError | FAILURE | LOCAL_UNAVAILABLE |
| Clean completion, zero violations | guardrails pass | ACCEPT | VALID_LOCAL_RESULT |

No hiding of weakness (Part 6): no infinite retries, no silent prompt
shortening, no silent output modification, no silent repair of hallucinated
evidence, invalid output is never converted to success, validation standards
are the *existing* standards, and the benchmark is untouched.

## F. Trust boundaries

- `validated_output` is populated **only** after `_extract_json` +
  `LLMRankingOutput.from_dict` + `validate_ranking_output` with **zero
  violations** succeed (tests L, K).
- On any guardrail violation the branch is FAILURE; the guardrail-corrected
  output travels inside `guardrail_result` for audit only — never promoted
  to `validated_output`.
- The model can never populate `RoutingDecision`, `RoutingReason`, safety
  status, physics verdicts, or command authorization: those types have no
  model-writable path in `BranchResult` (tests M, N, O).
- Review-forcing violations (`PHYSICS_OVERRIDE`,
  `INSUFFICIENT_EVIDENCE_CLAIM`, `INVENTED_TELEMETRY`, `UNKNOWN_COMMAND`)
  additionally force `human_review_required=True`.

## G. Human-review behavior

`human_review_required` is monotone everywhere:

- Policy R6 never downgrades a pre-existing review requirement.
- Runner OR-combines `review_already_required` with anything the run raises,
  via `combine_human_review()` (contract helper). True → False is impossible
  (test J).

## H. Disabled-router verification (Part 12)

Verified after implementation:

- `ROUTER_ENABLED` unset ⇒ `router_enabled()` returns **False**.
- `BranchPolicy` / `LocalBranchRunner` are importable but **dormant**:
  `grep router|branch_policy|LocalBranchRunner app/agent/agent.py` finds
  zero references — the production path never constructs or calls them.
- The existing provider path (`LLM_MODE` → `create_provider` →
  `run_constrained_ranking`) is byte-for-byte unchanged in this commit.
- Step 1 contract test `router_enabled()` default-false still passes; no
  `ModelMode.HYBRID` activation exists anywhere.

## I. Test results

| Suite | Result |
|---|---|
| `tests/test_phase23_branch_policy.py` (new) | **20 passed** — policy tests 1–10 incl. determinism, confidence-independence, model-output-independence, fail-closed |
| `tests/test_phase23_local_branch.py` (new) | **23 passed** — runner tests A–R incl. echo, truncation, invalid IDs, timeout, unavailable, trust-boundary injections, identity, no-network |
| Full regression `python3 -m pytest tests/ -q` | **1198 passed, 2 skipped** (skips are the intentional Step 1 `PHASE 23 FOLLOW-UP` stubs), 0 failures, 0 regressions |

All runner tests use a fake provider — no Ollama, no Gemini, no network.

## J. Security review (Part 13)

- No API keys, secrets, or `.env` modifications; no model weights.
- No command authorization produced or consumed by either component.
- No physics or safety mutation: neither component imports or touches
  `app.validation.physics` or `app.agent.safety` verdicts.
- No evidence promotion from raw text: raw completion stays diagnostic only.
- No cloud calls; BranchPolicy has zero I/O of any kind; LocalBranchRunner
  performs exactly one network path — the existing `LocalProvider.call`
  (test R: zero network outside the injected provider).
- No hidden fallback around guardrails: violations ⇒ FAILURE, always.
- `git diff --check`: clean (no whitespace errors, no conflict markers).

## K. Files changed

| File | Change |
|---|---|
| `app/llm/branch_policy.py` | NEW — `PolicyInput` + `BranchPolicy` (rules 1–7) |
| `app/llm/local_branch.py` | NEW — `LocalBranchRunner` adapter |
| `app/llm/__init__.py` | dormant exports: `BranchPolicy`, `PolicyInput`, `LocalBranchRunner` |
| `tests/test_phase23_branch_policy.py` | NEW — 20 policy tests |
| `tests/test_phase23_local_branch.py` | NEW — 23 runner tests (fake provider) |
| `PHASE_23_STEP2_BRANCH_POLICY_LOCAL_REPORT.md` | NEW — this report |

No production file behavior changed.

## L. Explicitly deferred work

NOT IMPLEMENTED:

- CloudBranchRunner
- Arbitrator
- MergeResolver
- RouterOrchestrator
- parallel execution
- production routing
- HYBRID mode activation
- cloud redaction
- Gemini router calls
