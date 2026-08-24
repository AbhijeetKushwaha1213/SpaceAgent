# PHASE 23 STEP 4 — DETERMINISTIC ARBITRATOR + MERGE RESOLVER REPORT

**Status**: IMPLEMENTED & TESTED
**Scope**: Deterministic Arbitrator and Deterministic MergeResolver modules, exhaustive test suite (cases A–AF + adversarial), and safety/physics boundary proofs.
**Router Status**: DORMANT (`ROUTER_ENABLED=false`). Production execution paths remain 100% byte-identical.

---

## A. Objective

Implement the pure deterministic decision and reconciliation logic for the Sentinel Hybrid Local/Cloud Router:
1. **Arbitrator** (`app/llm/arbitrator.py`): Decides which branch wins, whether human review is required, and what reason codes explain the decision.
2. **MergeResolver** (`app/llm/merge_resolver.py`): Reconciles branch outputs into an authoritative `LLMRankingOutput` envelope enforcing field-level invariants (intersection of supporting evidence, union of contradicting evidence, allowlisted intersection of procedures, non-averaged confidence, winner causal chains, and monotone human review).
3. **Exhaustive Test Suite** (`tests/test_phase23_arbitrator.py`, `tests/test_phase23_merge_resolver.py`): 38 dedicated tests covering all cases A–AF, adversarial confidence inversion, raw output isolation, procedure containment, evidence grounding, and physics non-mutation.

---

## B. Exact Precedence Hierarchy

Arbitration follows strict deterministic precedence; model confidence is **never** an authority signal:

```
1. PHYSICS VERDICTS           (Refutation is authoritative; invalidated hypotheses cannot be #1)
         ↓
2. EVIDENCE CONTRACT          (INSUFFICIENT evidence enforces empty diagnosis + mandatory human review)
         ↓
3. GUARDRAIL VALIDITY         (Only ACCEPT outcomes participate; invalid branches yield to clean branches)
         ↓
4. DETERMINISTIC DISCRIMINATORS (When clean top hypotheses disagree: physics status > score > evidence count)
         ↓
5. AGREEMENT TIE-BREAK        (Same valid top hypothesis → LOCAL adopted per Phase 22 policy)
         ↓
6. UNRESOLVABLE DISAGREEMENT  (All discriminators tied → HUMAN_REVIEW with MODEL_DISAGREEMENT)
```

---

## C. Physics Authority

- Physics verdicts (`VALID`, `INVALID`, `UNCERTAIN`) are deterministic facts computed by `app/validation/physics.py`.
- **Refutation is absolute**: If a hypothesis is marked `INVALID` by deterministic physics, it cannot be promoted to #1 or accepted as true, even if both models agree or assign 1.0 confidence (Rule A6).
- If one branch selects a non-invalidated candidate and the other selects an invalidated candidate, the non-invalidated candidate wins outright.
- If both models select an invalidated candidate as #1, neither model is adopted; execution falls back to deterministic re-ranking among non-invalidated hypotheses and flags mandatory human review.
- Physics is **not merged** and cannot be mutated by `reconcile_llm_claim`.

---

## D. Evidence Authority

- If `evidence_status == INSUFFICIENT`:
  - `LLMRankingOutput` is constrained to empty ranked hypotheses (`()`), empty supporting evidence (`()`), empty selected procedures (`()`), and `requires_human_review = True`.
  - No model output may assert confidence or diagnosis over missing telemetry.
- **Supporting Evidence Merge**: Validated intersection `(local ∩ cloud) ∩ valid_evidence` when both branches participate; `winner ∩ valid_evidence` on single-winner paths.
- **Contradicting Evidence Merge**: Validated union `(local ∪ cloud) ∩ valid_evidence` (conservative preservation of safety-critical contradiction cues).
- Any fabricated or unallowlisted evidence ID cited by a model is stripped before merge.

---

## E. Guardrail Authority

- Only branch outputs with `outcome == BranchOutcome.ACCEPT` and `guardrail_result.is_valid == True` may participate in normal arbitration.
- If `LOCAL` is valid and `CLOUD` is invalid/failed: `LOCAL` wins (`LOCAL_ACCEPT`, `VALID_LOCAL_RESULT`).
- If `LOCAL` is invalid/failed and `CLOUD` is valid: `CLOUD` wins (`CLOUD_ACCEPT`, `VALID_CLOUD_RESULT` / `LOCAL_ESCALATION`).
- If both branches fail or violate guardrails: `HUMAN_REVIEW` (`BOTH_INVALID` or `BOTH_UNAVAILABLE`), with zero model authority promoted.

---

## F. Deterministic Discriminators

When both branches are valid but disagree on the top-1 hypothesis, resolution is determined in strict order:
1. **Physics `VALIDATED` Status**: If exactly one candidate is verified `VALID` by physics models, that candidate wins.
2. **Deterministic Hypothesis Score**: If scores differ in `HypothesisContext.deterministic_score`, the higher deterministic score wins.
3. **Deterministic Evidence Support Count**: The candidate with more deterministic supporting evidence items (`len(supporting_evidence)`) wins.
4. **Unresolvable Disagreement (A10)**: If all discriminators tie, the router emits `HUMAN_REVIEW` with `MODEL_DISAGREEMENT` and `UNRESOLVED_AMBIGUITY`.

---

## G. Agreement & Disagreement Rules

| Condition | Rule | Decision | Reasons | Human Review |
|---|---|---|---|---|
| Both valid, same top-1 fault | A1 | `LOCAL_ACCEPT` | `BRANCH_AGREEMENT` | Monotone OR |
| Both valid, diff top-1, physics favors one | A2 (Physics) | Winner accept | `MODEL_DISAGREEMENT`, `VALID_<WINNER>_RESULT` | `True` (Disagreement) |
| Both valid, diff top-1, score favors one | A2 (Score) | Winner accept | `MODEL_DISAGREEMENT`, `VALID_<WINNER>_RESULT` | `True` (Disagreement) |
| Both valid, diff top-1, evidence favors one | A2 (Evidence)| Winner accept | `MODEL_DISAGREEMENT`, `VALID_<WINNER>_RESULT` | `True` (Disagreement) |
| Both valid, diff top-1, all tied | A10 (Tie) | `HUMAN_REVIEW` | `MODEL_DISAGREEMENT`, `UNRESOLVED_AMBIGUITY` | `True` (Conflict) |
| Local valid, Cloud invalid | A3 | `LOCAL_ACCEPT` | `VALID_LOCAL_RESULT` | Local review flag |
| Local invalid, Cloud valid | A4 | `CLOUD_ACCEPT` | `LOCAL_ESCALATION`, `VALID_CLOUD_RESULT` | Cloud review flag |
| Both invalid / failed | A5 | `HUMAN_REVIEW` | `BOTH_INVALID` / `BOTH_UNAVAILABLE` | `True` (Terminal) |
| Both top-1 physics-invalidated | A6 | `HUMAN_REVIEW` | `PHYSICS_CONFLICT` | `True` (Terminal) |
| Insufficient evidence | P1 | `HUMAN_REVIEW` | `INSUFFICIENT_EVIDENCE` | `True` (Terminal) |

---

## H. Merge Semantics

| Field | Winning Path (A1, A2, A3, A4) | Conflict Path (A5, A6, A10) |
|---|---|---|
| **Hypotheses** | Winner's hypotheses (filtered against `valid_fault_ids` & physics demotion) | Non-invalidated deterministic hypotheses ordered by score |
| **Causal Chains** | Winner-sourced (never mixed across models) | Deterministic hypothesis causal chain |
| **Reasoning Summary**| Winner-sourced | Deterministic template explaining conflict & fallback |
| **Uncertainty** | Winner uncertainty + deterministic disagreement note | Deterministic note stating model-free fallback |
| **Supporting Evidence** | Intersection (both) / Winner allowlisted (single) | Empty `()` |
| **Contradicting Evidence** | Validated union of all participating outputs | Validated union of participating outputs |
| **Selected Procedures**| Allowlisted intersection (both) / Winner allowlisted (single) | Empty `()` (conservative recovery plan) |
| **Confidence** | Winner confidence (demoted if physics invalid) | Conservative model-free score (capped at ≤ 0.40) |
| **Human Review** | `combine_human_review()` across all sources | `True` |

---

## I. Trust Boundary & Security Verification

- **Raw Model Text Isolation**: Neither `Arbitrator` nor `MergeResolver` reads `BranchResult.raw_text_head`. Tests confirm that injecting hostile instructions, prompt ejections, fake commands, or manipulated confidence into `raw_text_head` produces identical outcomes.
- **No Command Authorization**: Merge output is strictly typed to `LLMRankingOutput`. No raw commands, actuator overrides, or bypassed safety constraints can be emitted.
- **Monotone Human Review**: `combine_human_review()` guarantees that `True` can never be cleared by any model or resolution rule.
- **Purity**: Zero network calls, zero file I/O, zero random state; 100 consecutive invocations produce identical results.

---

## J. Test Matrix & Regression Results

### Dedicated Phase 23 Step 4 Tests (38 passed):
- `tests/test_phase23_arbitrator.py`: 22 passed
  - Cases A, B, C, D, E, F, G, H, I, J, K, L, M, N, O, P, Q, W, Y, Z, AA
  - Adversarial confidence inversion (favored low-confidence branch wins over 0.99 cloud)
  - Equal-signal confidence tie-break rejection
  - Purity / idempotency repeatability
  - `router_enabled() == False` guarantee
- `tests/test_phase23_merge_resolver.py`: 16 passed
  - Cases R, S, T, U, V, X, Y, Z, AA, AB, AC, AD, AE, AF
  - Supporting evidence intersection & fabricated ID stripping
  - Contradicting evidence union
  - Allowlisted procedure intersection & empty intersection on disjoint selections
  - Confidence non-averaging verification
  - Single-winner (A3/A4) merge semantics
  - Conflict deterministic fallback & model-free confidence capping
  - Physics verdict non-mutation verification (`reconcile_llm_claim`)

### Full Regression Suite:
```text
=================== 1280 passed, 8 skipped, 2603 subtests passed ===================
```
- Total passed: **1280**
- Skipped: **8** (explicitly marked Phase 23 follow-up router integration stubs)
- Failures: **0**
- Regressions: **0**

---

## K. Files Created / Modified

- `app/llm/arbitrator.py` (NEW)
- `app/llm/merge_resolver.py` (NEW)
- `tests/test_phase23_arbitrator.py` (NEW)
- `tests/test_phase23_merge_resolver.py` (NEW)
- `PHASE_23_STEP4_ARBITRATION_MERGE_REPORT.md` (NEW)

---

## L. Explicitly Deferred Work

- `RouterOrchestrator`
- Production pipeline wiring (`app/agent/agent.py`)
- `ModelMode.HYBRID` activation
- Parallel branch execution
- Provider selection modifications
