# PHASE 23 STEP 1 — HYBRID ROUTER CONTRACT REPORT

Scope: router **language** only — immutable contracts, test skeletons, and a
disabled-by-default enable flag. No routing behavior was implemented.

---

## A. Objective

Define the router's language before implementing its behavior: the minimal
immutable contract layer (`RoutingDecision`, `RoutingReason`, `BranchResult`,
plus the tiny supporting types they require) that all future router
components will speak, together with contract-level test skeletons A–P from
the Phase 22 test plan. Per the mandate, the router stays **disabled**
(`ROUTER_ENABLED=false`) and the existing single-provider execution path is
unchanged.

## B. Existing contracts reused (nothing duplicated)

| Reused type | From | Used as |
|---|---|---|
| `LLMRankingOutput` | `app/llm/models.py` | `BranchResult.validated_output` — the only field arbitration may read |
| `GuardrailResult` / `GuardrailViolation` / `ViolationType` | `app/llm/models.py` | `BranchResult.guardrail_result` — validation violations with original/corrected outputs |
| `EvidenceStatus` values | `app/llm/models.py` | `BranchResult.evidence_status` context reference |
| `PhysicsVerdict` / `PhysicsValidationReport` | `app/validation/physics.py` | referenced by consumers only — deliberately NOT carried in the contract |
| `ValidationResult` / `SafetyStatus` | `app/agent/safety.py` / `app/api/models.py` | referenced by consumers only — deliberately NOT carried in the contract |
| `ModelMode` / `ProviderConfig` | `app/agent/agent.py`, `app/llm/provider.py` | untouched; provider selection unchanged |

No hypothesis, evidence, safety-status, physics-verdict, or procedure-
authorization model was redefined.

## C. New contracts

All in `app/llm/router_contract.py` (294 lines, exported from
`app/llm/__init__.py`, no production caller yet):

| Type | Kind | Role |
|---|---|---|
| `router_enabled()` | function | `ROUTER_ENABLED` env gate; default **False** |
| `Branch` | str Enum | LOCAL / CLOUD identity |
| `RoutingDecision` | str Enum | what Sentinel should do next |
| `RoutingReason` | str Enum | enumerable deterministic reason codes |
| `BranchOutcome` | str Enum | ACCEPT / ESCALATION / FAILURE / NOT_RUN |
| `BranchResult` | frozen dataclass | one branch's execution envelope |
| `RoutingRecord` | frozen dataclass | one decision + justification (future ROUTING audit unit) |
| `combine_human_review()` | function | the only sanctioned OR-accumulator for review flags |

## D. RoutingDecision semantics

"Sentinel's next action", never model-generated authority:

| Value | Meaning |
|---|---|
| `LOCAL_ACCEPT` | Use the local branch's validated result; no cloud call |
| `CLOUD_ACCEPT` | Use the cloud branch's validated result |
| `CLOUD_ESCALATE` | Run/adopt the cloud branch because local failed, violated, or is untrusted |
| `HUMAN_REVIEW` | Mandatory operator review (insufficient evidence, unresolvable disagreement, both-invalid) |
| `BLOCKED` | Deterministic safety blocked the outcome |
| `NO_INFERENCE` | Neither branch could run; deterministic stages only, explicit fail-closed terminal |

`is_terminal_review` is True for HUMAN_REVIEW / BLOCKED / NO_INFERENCE:
these decisions mandate review regardless of any model opinion.

## E. RoutingReason semantics

Every value maps to a real Phase 22 condition or existing Sentinel failure.
Mandated minimum covered: `valid_local_result`, `invalid_structured_output`,
`evidence_failure`, `insufficient_evidence`, `physics_conflict`,
`procedure_invalid`, `safety_block`, `local_timeout`, `local_unavailable`,
`cloud_unavailable`, `model_disagreement`, `unresolved_ambiguity`,
`human_review_required`. Plus the non-speculative additions demanded by
Phase 21/22 findings: `valid_cloud_result`, `branch_agreement`,
`prompt_echo_truncation` (S1-type is a distinct deterministic classifier),
`local_escalation`, `both_unavailable`, `both_invalid`.

## F. BranchResult semantics

Nine mandated aspects mapped:

1. **branch identity** — `branch: Branch`
2. **execution status** — `outcome: BranchOutcome` (deterministically classified)
3. **model/provider identity** — `provider_name`, `model_name`
4. **raw success/failure** — `inference_performed` (call completed) is
   independent of usability: a prompt-echo completion is a successful call
   but a FAILURE outcome (`succeeded` vs `is_usable`)
5. **validated reasoning result** — `validated_output: LLMRankingOutput`
   (reused, not duplicated)
6. **validation violations** — `guardrail_result: GuardrailResult`
7. **deterministic context references** — `evidence_status`, `scenario_id`
   (references only; the bundle is owned by the pipeline)
8. **latency/audit metadata** — `elapsed_ms`, `attempts`, `reason_codes`
9. **human review raised** — `human_review_required: bool`

Trust separation: `raw_text_head` is explicitly UNTRUSTED diagnostic text —
truncated, never parsed for IDs, never transmitted, never promotable to
validated state. Physics and safety verdicts have NO field in the contract.

## G. Human-review monotonicity

- `human_review_required` is a plain bool on frozen dataclasses; mutation
  raises `FrozenInstanceError` (tested).
- The contract exposes no API that can turn True back into False.
- `combine_human_review(*flags)` is OR-only accumulation; terminal decisions
  (`is_terminal_review`) force review regardless of branch flags (tested in
  `TestMonotoneHumanReview`).

## H. Authority boundaries

- No `model_physics_verdict` / `model_safety_verdict` / `safety_status`
  field exists on `BranchResult` or `RoutingRecord` (asserted by
  `TestTrustSeparation` against the dataclass field set).
- Physics verdicts remain in `app/validation/physics.py`; safety verdicts in
  `app/agent/safety.py`; the router contract can reference neither as owned
  state, authorize no command, and override nothing.
- `RoutingDecision` and `RoutingReason` are produced by deterministic code
  only; the contract gives a model no path to emit them.

## I. ROUTER_ENABLED behavior

- `router_enabled()` reads `ROUTER_ENABLED`; only `true` / `1` / `yes`
  (case-insensitive) enable it; default and every other value ⇒ **False**.
- No production code path consults the flag yet — the flag exists so the
  next steps can gate behind it. Existing behavior is byte-identical:
  provider selection (`LLM_MODE` → `ModelMode` → `create_provider`),
  Gemini path, Local path, safety, physics, and RAG are untouched.
  `ModelMode.HYBRID` was NOT introduced.

## J. Test matrix

`tests/test_phase23_router_contract.py` — 55 tests (53 passed, 2 skipped as
explicit `PHASE 23 FOLLOW-UP` markers for future Arbitrator behavior):

| Case | Expected Decision | Reason | Contract Covered |
|---|---|---|---|
| A | LOCAL_ACCEPT | valid_local_result | yes |
| B | CLOUD_ESCALATE | invalid_structured_output | yes |
| C | CLOUD_ESCALATE/HUMAN_REVIEW | evidence_failure | yes |
| D | CLOUD_ESCALATE | physics_conflict | yes |
| E | CLOUD_ESCALATE/HUMAN_REVIEW | procedure_invalid | yes |
| F | CLOUD_ESCALATE | local_escalation | yes |
| G | CLOUD_ACCEPT | valid_cloud_result | yes |
| H | ACCEPT | branch_agreement | yes (adoption tie-break: follow-up) |
| I | ARBITRATION | model_disagreement | future (representation only) |
| J | HUMAN_REVIEW | both_invalid | yes |
| K | HUMAN_REVIEW | insufficient_evidence | yes |
| L | BLOCKED | safety_block | yes |
| M | HUMAN_REVIEW | human_review_required | yes |
| N | FALLBACK/HUMAN | cloud_unavailable | yes |
| O | FALLBACK/HUMAN | local_unavailable | yes |
| P | HUMAN_REVIEW (NO_INFERENCE) | both_unavailable | yes |

Additionally covered: enum exhaustiveness, frozen-dataclass immutability,
tuple-typed collections, monotone review accumulation, raw/untrusted
separation, authority-boundary field absence, fail-closed terminal
representation, and `ROUTER_ENABLED` truth table. No assertion was weakened
to achieve green; the two skips are honest deferrals.

## K. Security review (Part 13 checklist)

1. No API keys added. ✅
2. No `.env` files committed. ✅
3. No model weights committed. ✅
4. No raw secrets logged (contract logs nothing). ✅
5. Raw model output cannot become validated evidence — `raw_text_head` is
   diagnostics-only; `validated_output` is populated only from parsed,
   guardrail-checked structures. ✅ (tested)
6. Router contracts cannot authorize commands — no command/registry fields. ✅
7. Router contracts cannot override physics — no physics field exists. ✅ (tested)
8. Router contracts cannot override safety — no safety field exists. ✅ (tested)
9. `requires_human_review=true` cannot be silently downgraded — frozen
   dataclasses + OR-only combiner. ✅ (tested)
10. Router remains disabled — `router_enabled()` defaults False and no
    production code path reads it yet. ✅

## L. Regression results

```
python3 -m pytest tests/ -q
1155 passed, 2 skipped, 1 warning, 2625 subtests passed in 49.93s
```

Baseline before this step: 1102 passed. Net: +53 new passing tests, 0
regressions, 0 weakened/deleted tests. The 2 skips are the intentional
`PHASE 23 FOLLOW-UP` Arbitrator markers.

## M. Files changed

| File | Change |
|---|---|
| `app/llm/router_contract.py` | NEW — contracts + `router_enabled()` (294 lines) |
| `app/llm/__init__.py` | MODIFIED — dormant exports of the contract types |
| `tests/test_phase23_router_contract.py` | NEW — skeletons A–P + matrix (719 lines) |
| `PHASE_23_STEP1_CONTRACT_REPORT.md` | NEW — this report |

No other file modified. No production execution path touched.

## N. Explicitly deferred components

NOT IMPLEMENTED YET:

- BranchPolicy
- LocalBranchRunner
- CloudBranchRunner
- Arbitrator
- MergeResolver
- RouterOrchestrator
- ROUTING audit stage
- production routing
- parallel execution
- `ModelMode.HYBRID` / provider privacy-assertion re-scoping (Phase 22 D3)
- cloud-redaction move into a cloud branch adapter (Phase 22 D2)

---

*Phase 23 Step 1 deliverable. The router is a dormant contract: defined,
tested, and disabled.*
