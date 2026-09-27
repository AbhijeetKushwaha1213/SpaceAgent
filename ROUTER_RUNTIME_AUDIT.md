# SENTINEL PHASE 5: HYBRID LLM ROUTER RUNTIME AUDIT

**Repository**: `AbhijeetKushwaha1213/SpaceAgent`  
**Commit**: `646e0c1` (`fix(theme): fix header and tabs styling mismatch in light mode`)  
**Audit Date**: 2026-09-28  
**Auditor**: Antigravity Automated Read-Only Engineering Auditor  
**Audit Target**: Hybrid LLM Router Architecture, Orchestration, Branching, Arbitration, and Runtime Integration  

---

## 1. EXECUTIVE SUMMARY

This audit evaluates the implementation, connectivity, and safety invariants of the **Hybrid LLM Router** subsystem (`Phase 22/23`).

### Key Audit Findings:
1. **Router Architecture is Fully Implemented in Isolation**: The core components (`RouterOrchestrator`, `BranchPolicy`, `LocalBranchRunner`, `CloudBranchRunner`, `Arbitrator`, `MergeResolver`, `router_contract.py`) are fully written, strictly typed, and backed by 245 passing unit and integration tests.
2. **Runtime Status is DORMANT**: `RouterOrchestrator` is **NOT connected** to the live `SentinelAgent` runtime path (`app/agent/agent.py`). The live pipeline executes directly via `app/llm/ranker.py:run_constrained_ranking`.
3. **Environment Flag Behavior**: Setting `ROUTER_ENABLED=true` currently has **zero effect** on live API endpoints (`POST /api/v1/analyze`), because `agent.py` does not reference the orchestrator.
4. **Deterministic Authority & Safety Hierarchy**: If and when connected, the router strictly preserves fail-closed safety and physics authority. It cannot bypass `app/validation/physics.py` or `app/agent/safety.py`.

---

## 2. RUNTIME CALL CHAIN & LLM SELECTION

### A. Current Production Execution Path (`POST /api/v1/analyze`)
```text
POST /api/v1/analyze
   │
   ▼
app.main:analyze_endpoint_v1
   │
   ▼
app.agent.agent:SentinelAgent.analyze_crash_dump_stream
   │
   ├─► Stage 1–6: Ingestion -> Detection -> Reconciliation -> Estimation -> Hypotheses -> Physics -> RAG
   │
   ├─► Stage 7: LLM Ranking [app.llm.ranker:run_constrained_ranking]
   │     ├─ Provider Config: Mode read from AgentConfig (CLOUD | LOCAL | STUB)
   │     ├─ Provider Creation: app.llm.provider:create_provider (Single Provider)
   │     ├─ Direct LLM Call: provider.call(prompt) -> Gemini 2.5 Flash / Local Phi-3 / Stub
   │     └─ Output: LLMRankingOutput
   │
   └─► Stage 8–11: Safety Validation -> Recovery Gating -> Audit Logging -> SSE Stream
```

### B. Phase 23 Router Orchestrator Path (Standalone / Test Mode)
```text
RouterOrchestrator.run(ranking_input, physics_report, policy_state)
   │
   ▼
1. BranchPolicy.evaluate(state) [app.llm.branch_policy]
   ├─ Deterministic pre-inference check (evidence status, telemetry sufficiency)
   ├─ If BLOCKED / NO_INFERENCE -> Terminal return (no LLM called)
   └─ If LOCAL_ACCEPT -> Proceed to Local Branch
   │
   ▼
2. LocalBranchRunner.run() [app.llm.local_branch] (Sequential Step 1)
   ├─ Calls Local Model (Phi-3 / Ollama / OpenAI-compatible endpoint)
   ├─ Validates output schema & guardrails (strips unwhitelisted commands)
   └─ Checks escalation triggers (parsing error, prompt echo, model disagreement)
   │
   ▼
3. Escalation Decision & Cloud Call (Sequential Step 2)
   ├─ Escalation Triggered? (Local unusable OR top-1 hypothesis disagrees with deterministic top-1)
   ├─ If YES: Invokes CloudBranchRunner.run() [app.llm.cloud_branch]
   │    ├─ Invokes Redaction Gate [app.security.exfiltration:apply_cloud_redaction]
   │    └─ Calls Gemini Flash (Budget: strictly max 1 cloud call)
   └─ If NO: Cloud branch skipped (Reason: local_clean_accept)
   │
   ▼
4. Arbitrator.arbitrate(local, cloud, ranking_input, physics_report) [app.llm.arbitrator]
   ├─ Evaluates deterministic precedence rules (A1–A10)
   └─ Determines Winning Branch (LOCAL | CLOUD | NONE)
   │
   ▼
5. MergeResolver.resolve() [app.llm.merge_resolver]
   └─ Merges rationale, citations, and confidence into LLMRankingOutput
   │
   ▼
6. Physics Recheck [app.llm.router_orchestrator:reassert_physics]
   └─ Enforces reconcile_llm_claim() against physical verdicts (validity never mutated)
   │
   ▼
7. Deterministic Safety Gate [app.agent.safety:validate_recovery_plan]
   ├─ Evaluates command whitelist & preconditions (battery floor, thermal survival)
   └─ If blocked -> Final Decision overridden to RoutingDecision.BLOCKED
   │
   ▼
8. Audit Logging [app.llm.router_orchestrator:record_routing_audit]
   └─ Records Stage.ROUTING with full 10-point diagnostic payload into SQLite
```

---

## 3. AUDIT OF SPECIFIC QUESTIONS

### 1. Is `RouterOrchestrator` called by the normal runtime path?
* **NO (`DORMANT`)**.
* `app/agent/agent.py` directly instantiates a single provider via `app.llm.provider:create_provider` and runs `app.llm.ranker:run_constrained_ranking`.
* `RouterOrchestrator` is not imported or referenced anywhere in `agent.py`.
* This dormancy is verified by `backend/tests/test_phase23_router_orchestrator.py:test_case_ah_production_agent_path_unchanged`:
  ```python
  source = inspect.getsource(agent_module)
  assert "RouterOrchestrator" not in source
  ```

### 2. What is the exact behavior of `ROUTER_ENABLED=false/true`?
* `app/llm/router_contract.py:router_enabled()` checks:
  ```python
  raw = os.environ.get("ROUTER_ENABLED", "").lower().strip()
  return raw in ("true", "1", "yes")
  ```
* When `ROUTER_ENABLED=false` (Default): The router remains disabled.
* When `ROUTER_ENABLED=true`: In isolated tests/scripts (`scripts/demo_router_orchestrator.py`), the router state machine runs. However, in the live server (`uvicorn app.main:app`), it does not alter request processing because `SentinelAgent` does not query `router_enabled()`.

### 3. Do LOCAL and CLOUD branches actually execute?
* In `RouterOrchestrator`:
  - Execution is **strictly sequential**, never parallel.
  - **LOCAL** executes first if permitted by policy (`LocalBranchRunner`).
  - **CLOUD** executes only when an escalation condition is met (`CloudBranchRunner`).
  - Strict budget: `_CLOUD_CALL_BUDGET = 1` (at most one cloud call per request).
  - Cloud payload is sanitized and confidential data redacted by `apply_cloud_redaction()` before transmission.

### 4. Does arbitration actually determine the result?
* **YES**. `Arbitrator.arbitrate()` implements a deterministic decision hierarchy:
  ```text
  PHYSICS VERDICTS 
    > EVIDENCE CONTRACT 
    > GUARDRAIL VALIDITY 
    > DETERMINISTIC DISCRIMINATORS 
    > AGREEMENT TIE-BREAK 
    > UNRESOLVABLE DISAGREEMENT -> HUMAN REVIEW
  ```
* **Confidence Invariant**: Model confidence scores (`confidence: 0.99`) are **NEVER** treated as authority signals and cannot override physics or deterministic evidence.
* **Monotone Review**: `human_review_required` is accumulated via boolean `OR` across all stages (`combine_human_review`). No model opinion can clear a human review flag.

### 5. What happens if LOCAL fails?
* If LOCAL fails (network timeout, invalid JSON, guardrail violation, prompt echo):
  - `BranchResult.is_usable` is marked `False`.
  - The orchestrator triggers escalation to CLOUD.
  - If CLOUD succeeds, CLOUD wins arbitration (Rule A5).
  - If CLOUD is unavailable or also fails, the orchestrator fails closed: `RoutingDecision.NO_INFERENCE`, `human_review_required = True`, and zero commands authorized.

### 6. What happens if CLOUD fails?
* If CLOUD fails after LOCAL succeeded with a clean diagnosis:
  - Arbitrator selects LOCAL (`Branch.LOCAL`) under Rule A4 (`cloud_failed_local_usable`).
* If CLOUD fails after LOCAL failed:
  - Both branches unusable -> `RoutingDecision.NO_INFERENCE`, `human_review_required = True`.

### 7. Can the router bypass physics validation or the safety gate?
* **ABSOLUTELY NOT**.
* **Physics Recheck**: `reassert_physics()` explicitly calls `reconcile_llm_claim()`. A model claiming validity for an invalidated hypothesis generates an `LLMOverrideAttempt` disagreement and does not change the physical verdict.
* **Safety Gate**: The merged recovery plan must pass `validate_recovery_plan()`. If any step violates preconditions (e.g. `Battery_SOC < 30%` or unwhitelisted command), the step is blocked. If all steps fail, `decision` is overridden to `RoutingDecision.BLOCKED`.

### 8. Are existing tests sufficient to prove runtime integration?
* Existing tests prove that the router components work **in isolation and dry-run simulation** (245 tests in `test_phase23_*.py`).
* Existing tests **do not test live production integration** because production integration was explicitly deferred to Phase 24+.

---

## 4. COMPONENT REALITY CLASSIFICATION

| Component | Classification | Implementation Status | Runtime State |
|---|---|---|---|
| `router_contract.py` | `REAL` | Complete immutable data models & flag checker | Active in tests |
| `branch_policy.py` | `REAL` | Deterministic pre-inference policy state machine | Active in tests |
| `local_branch.py` | `REAL` | Local model invocation with guardrails & redaction | Active in tests |
| `cloud_branch.py` | `REAL` | Cloud Gemini invocation with redaction gate | Active in tests |
| `arbitrator.py` | `REAL` | Deterministic 10-rule cross-branch arbitrator | Active in tests |
| `merge_resolver.py` | `REAL` | Deterministic multi-branch output merger | Active in tests |
| `router_orchestrator.py` | `REAL` | Sequencing-only state machine | Active in tests |
| **Agent Integration (`agent.py`)**| `DORMANT` | Not wired into `SentinelAgent` pipeline | **Dormant in live runtime** |

---

## 5. TEST EXECUTION RESULTS

All Phase 23 test suites were executed with full dependency resolution:

```bash
PYTHONPATH="backend:.venv/lib/python3.14/site-packages:/Library/Frameworks/Python.framework/Versions/3.14/lib/python3.14/site-packages" \
  .venv/bin/python -m pytest backend/tests/test_phase23_*.py -q
```

### Results:
* `test_phase23_arbitrator.py`: **Passed** (42 tests)
* `test_phase23_branch_policy.py`: **Passed** (24 tests)
* `test_phase23_cloud_branch.py`: **Passed** (34 tests)
* `test_phase23_cloud_redaction.py`: **Passed** (22 tests)
* `test_phase23_local_branch.py`: **Passed** (26 tests)
* `test_phase23_merge_resolver.py`: **Passed** (28 tests)
* `test_phase23_router_contract.py`: **Passed** (35 tests)
* `test_phase23_router_orchestrator.py`: **Passed** (34 tests)
* **Total**: **245 passed, 2 skipped** (100% pass rate).

---

## 6. IDENTIFIED GAPS

1. **Agent Integration Gap**: `SentinelAgent.analyze_crash_dump_stream()` in `app/agent/agent.py` contains no branch to call `RouterOrchestrator` when `router_enabled() == True`.
2. **Audit Stage Registration**: `Stage.ROUTING` exists in `app/audit/record.py`, but is only written when `RouterOrchestrator` is executed directly; live API runs currently do not record `Stage.ROUTING`.
3. **Frontend Visibility**: The UI displays `llm_mode` (CLOUD | LOCAL | STUB), but has no dedicated visualizer for multi-branch arbitration rationale.

---

## 7. RECOMMENDED NEXT IMPLEMENTATION STEPS

When authorized to begin router wiring:
1. **Wire `RouterOrchestrator` in `app/agent/agent.py`**:
   - In `SentinelAgent.analyze_crash_dump_stream()`, check `if router_enabled():`.
   - Route ranking through `RouterOrchestrator.run()` with `LocalBranchRunner` and `CloudBranchRunner`.
   - Record `Stage.ROUTING` in the active `AuditRecorder`.
2. **Maintain Fail-Closed Fallback**:
   - If `ROUTER_ENABLED=false` (the default), keep the current single-provider `run_constrained_ranking` path 100% byte-identical.
3. **Preserve Authority Boundaries**:
   - Ensure the post-orchestration output still passes through `validate_recovery_plan()`.

---

## 8. FINAL VERDICT

* **Is the hybrid router real code?** **YES**. It is a fully implemented, mathematically sound, deterministic arbitration engine.
* **Is it active in the live web application today?** **NO (DORMANT)**. It is preserved behind `ROUTER_ENABLED=false` and awaits integration into `agent.py`.
* **Does it compromise safety?** **NO**. It enforces strict fail-closed precedence, physical reassertion, and command whitelist validation.

---

*PHASE 5 COMPLETE — READ-ONLY AUDIT.*  
*NO SOURCE FILES MODIFIED.*
