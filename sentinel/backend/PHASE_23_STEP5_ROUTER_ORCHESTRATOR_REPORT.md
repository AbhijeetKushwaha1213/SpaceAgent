# PHASE 23 STEP 5 — ROUTER ORCHESTRATOR DRY-RUN INTEGRATION REPORT

**Status**: IMPLEMENTED & TESTED  
**Scope**: Deterministic RouterOrchestrator state machine (`app/llm/router_orchestrator.py`), comprehensive dry-run test suite (`tests/test_phase23_router_orchestrator.py`), end-to-end topology verification, privacy/authority boundary proofs, and dormancy certification.  
**Router Status**: DORMANT (`ROUTER_ENABLED=false`). Production execution paths remain 100% byte-identical.

---

## A. Executive Summary

Phase 23 Step 5 establishes the **RouterOrchestrator in dry-run / simulation mode only**.

The orchestrator wires together all the previously implemented, isolated Phase 23 components into a single deterministic hybrid-routing pipeline:
1. **BranchPolicy** (`app/llm/branch_policy.py`)
2. **LocalBranchRunner** (`app/llm/local_branch.py`)
3. **CloudBranchRunner** with cloud redaction gate (`app/llm/cloud_branch.py`)
4. **Arbitrator** (`app/llm/arbitrator.py`)
5. **MergeResolver** (`app/llm/merge_resolver.py`)
6. **Downstream Physics Recheck** (`app/validation/physics.py::reconcile_llm_claim`)
7. **Downstream Safety Validator** (`app/agent/safety.py::validate_recovery_plan`)

The orchestrator acts as a **pure sequencing state machine**. It invents no physics, contains no safety models, issues no raw provider calls directly, and never uses model confidence as an authority signal.

---

## B. Exact Pipeline Topology

```
                  ┌──────────────────────┐
                  │   LLMRankingInput    │
                  └──────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │     BranchPolicy     │
                  └──────────┬───────────┘
                             │
                   [Policy Allows Branch?]
                    ├─ NO  ──► Terminal Fallback / Empty Diagnosis (INSUFFICIENT)
                    └─ YES
                             │
                             ▼
                  ┌──────────────────────┐
                  │  LocalBranchRunner   │
                  └──────────┬───────────┘
                             │
                    [Escalation Triggered?]
                    │  (Hard error / timeout / local top != deterministic top)
                    │
                    ├─ NO  ──► Skip Cloud (cloud_called=False)
                    │
                    └─ YES ──► ┌───────────────────────────┐
                               │ Cloud Redaction Gate      │
                               │ (Fail-closed verification)│
                               └─────────────┬─────────────┘
                                             │
                                    [Gate Verified?]
                                     ├─ NO  ──► REDACTION_GATE_FAILURE (Calls=0)
                                     └─ YES ──► CloudBranchRunner (Budget <= 1)
                             │
                             ▼
                  ┌──────────────────────┐
                  │      Arbitrator      │
                  │ (Precedence Rules)   │
                  └──────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │    MergeResolver     │
                  │ (Field-Level Merge)  │
                  └──────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │   Physics Recheck    │
                  │(reconcile_llm_claim) │
                  └──────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │  Safety Validation   │
                  │(validate_recovery_p.)│
                  └──────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │ Monotone HumanReview │
                  │(combine_human_review)│
                  └──────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │ OrchestrationResult  │
                  └──────────────────────┘
```

---

## C. Hard Architectural Constraints Honoured

1. **Production Dormancy (`ROUTER_ENABLED=false`)**:
   - `router_enabled()` remains `False`.
   - `app/agent/agent.py` does not invoke the orchestrator in production; its runtime behavior is unchanged.
2. **No Direct Provider Access**:
   - The orchestrator communicates exclusively through branch runner adapters (`LocalBranchRunner`, `CloudBranchRunner`).
   - Modules `GeminiProvider`, `LocalProvider`, and `LLMProvider` are not imported or referenced by `RouterOrchestrator`.
3. **Strict Sequential Execution & Single-Cloud Call Budget**:
   - No parallel execution.
   - Local branch executes first.
   - Cloud branch executes only if deterministic escalation fires, with a hard ceiling of `_CLOUD_CALL_BUDGET = 1`.
4. **No `ModelMode.HYBRID`**:
   - The existing modes (`LOCAL`, `CLOUD`, `HYBRID` policy scaffolding) are maintained without introducing runtime hybrid provider modes.
5. **No Confidence-Based Authority**:
   - Model confidence is strictly ignored during routing, escalation, arbitration, merge, and validation.
   - Escalation triggers rely solely on deterministic error conditions or deterministic top-hypothesis divergence (`_deterministic_top_fault`).
6. **Fail-Closed Semantics**:
   - Every failure or timeout maps to an explicit `RoutingReason` and `BranchResult`.
   - Redaction gate failures immediately abort external transmission before any HTTP/SDK call can occur (`provider.calls == 0`).
7. **Monotone Human Review**:
   - `human_review_required` accumulates monotonically via `combine_human_review(policy, local, cloud, merged, safety, physics)`.
   - An upstream review requirement can never be cleared by any downstream model response.

---

## D. Deterministic Escalation Logic

Escalation from Local to Cloud occurs **only** when one of the following deterministic criteria is met:

| Escalation Reason | Trigger Condition |
|---|---|
| `LOCAL_TIMEOUT` | Local branch execution timed out |
| `LOCAL_UNAVAILABLE` | Local provider / Ollama daemon unavailable |
| `PROMPT_ECHO_TRUNCATION` | Local branch emitted prompt echo or was truncated |
| `INVALID_STRUCTURED_OUTPUT`| Local branch emitted malformed / unparseable JSON |
| `GUARDRAIL_VIOLATION` | Local branch violated output guardrails |
| `EVIDENCE_FAILURE` | Local branch cited unallowlisted / invalid evidence |
| `PROCEDURE_INVALID` | Local branch selected unallowlisted / illegal procedures |
| `PHYSICS_CONFLICT` | Local branch selected a physics-`INVALIDATED` candidate |
| `MODEL_DISAGREEMENT` | Soft trigger: Local top-1 hypothesis differs from the **deterministic top-1** candidate (Phase 22 §15) |

---

## E. Downstream Authority Integration

### 1. Deterministic Physics Recheck
- Post-merge, `reassert_physics` runs the authoritative `reconcile_llm_claim` from `app/validation/physics.py` against all hypotheses in the merged output.
- Deterministic physics verdicts (`VALID`, `INVALID`, `UNCERTAIN`) cannot be mutated or overridden by any model claim.
- If a model's claim disagrees with physics, an `LLMOverrideAttempt` is recorded with `overridden=False` and `disagreement=True`.

### 2. Downstream Safety Validation
- Post-merge, `validate_recovery_plan` from `app/agent/safety.py` validates the procedures in the merged recovery plan against safety constraints.
- If safety blocks the plan (`safety.blocked=True`), the orchestrator overrides the routing decision to `RoutingDecision.BLOCKED` with `RoutingReason.SAFETY_BLOCK`.
- Safety authority is strictly one-way and cannot be downgraded.

### 3. Cloud Redaction Gate
- Prior to any cloud transmission, `redact_ranking_input_for_cloud` sanitizes the ranking input bundle and runs fail-closed verification (`_verify_cloud_safe`).
- If any confidential key or un-redacted secret is detected, `CloudRedactionError` is raised, transmission is halted (`provider.calls == 0`), and `REDACTION_GATE_FAILURE` is recorded.

---

## F. Test Suite & Verification Results

### 1. RouterOrchestrator Unit & Dry-Run Matrix (`tests/test_phase23_router_orchestrator.py`)
- **Matrix A–AJ**: 61 dedicated tests covering:
  - Policy enforcement & terminal short-circuits (A, B, C, D, E, F, G, H, I, S)
  - Unavailability & timeout fallbacks (J, K, L, M, N, O, P, Q, R)
  - Soft escalation via deterministic top-hypothesis divergence (D)
  - Privacy boundaries & cloud redaction gate fail-closed guarantees (V, W, H, I, AI, AJ)
  - Authority preservation: Physics immutability & Safety block override (X, Y, Z, AA, AB, AC)
  - Monotone human review accumulation across all 6 pipeline stages (U, T)
  - Audit record verification (AD, AE, AF)
  - Production dormancy proofs (AG, AH)
  - Pure execution repeatability & determinism proofs (Purity)

### 2. Complete Phase 23 Suite
| Test File | Tests Passed | Status |
|---|---|---|
| `tests/test_phase23_arbitrator.py` | 22 / 22 | PASSED |
| `tests/test_phase23_merge_resolver.py` | 16 / 16 | PASSED |
| `tests/test_phase23_router_orchestrator.py` | 61 / 61 | PASSED |
| **Total Phase 23 Step 4 & 5 Suite** | **99 / 99** | **PASSED (100%)** |

### 3. Full Backend Regression Suite
- **1,341 passed**, 8 skipped, 2,604 subtests passed in 38.84s.
- Zero regressions across detection, RAG, safety, physics, audit, contracts, and telemetry.

---

## G. Deliverable Artifacts

| Component | File Path | Role |
|---|---|---|
| Orchestrator | `app/llm/router_orchestrator.py` | Pure deterministic state machine wiring Phase 23 components |
| Orchestrator Tests | `tests/test_phase23_router_orchestrator.py` | 61 exhaustive unit, authority, safety, privacy, and dry-run tests |
| Arbitrator | `app/llm/arbitrator.py` | Deterministic cross-branch winner selection |
| MergeResolver | `app/llm/merge_resolver.py` | Deterministic field-level branch output reconciliation |
| Step 4 Report | `PHASE_23_STEP4_ARBITRATION_MERGE_REPORT.md` | Arbitration & merge resolver specification & verification report |
| Step 5 Report | `PHASE_23_STEP5_ROUTER_ORCHESTRATOR_REPORT.md` | Router orchestrator dry-run integration & verification report |
