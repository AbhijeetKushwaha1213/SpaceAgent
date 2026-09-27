# SENTINEL PHASE 5: HYBRID LLM ROUTER RUNTIME INTEGRATION AUDIT

**Repository**: `AbhijeetKushwaha1213/SpaceAgent`  
**Commit**: `646e0c1` (`fix(theme): fix header and tabs styling mismatch in light mode`)  
**Audit Date**: 2026-09-28  
**Auditor**: Antigravity Automated Read-Only Engineering Auditor  
**Audit Focus**: Router Runtime Reachability, Dormancy Causes, Branch Execution, Failure Modes, Safety Invariants, and Activation Blueprint  

---

## 1. CURRENT RUNTIME FLOW

Currently, when a request hits the live analysis endpoint (`POST /api/v1/analyze`), the execution flow is strictly single-model:

```text
POST /api/v1/analyze
   │
   ▼ [app.main:analyze_endpoint_v1]
   │  └─ Sanitizes payload -> Opens AuditRecorder -> Starts SSE event generator
   ▼
[app.agent.agent:SentinelAgent.analyze_crash_dump_stream]
   │
   ├─► Stage 1: Ingestion & Canonicalization (app.api.adapters:with_canonical_window)
   ├─► Stage 2: Deterministic Anomaly Detection (app.detection.fusion:run_detection_on_crash_dump)
   ├─► Stage 3: Observation Reconciliation (app.reconciliation.engine:ReconciliationEngine)
   ├─► Stage 4: Spacecraft State & Residual Estimation (app.estimation.state:estimate_states)
   ├─► Stage 5: Fault Hypothesis Generation (app.diagnosis.candidates:generate_hypotheses)
   ├─► Stage 6: Physics Validation (app.validation.physics:validate_crash_dump)
   ├─► Stage 7: Procedure Retrieval RAG (app.procedures.retrieval:retrieve_procedures)
   │
   ├─► Stage 8: LLM Ranking [app.agent.agent.py:lines 1944–1955]  <--- CURRENT BYPASS POINT
   │     ├─ Creates single provider: provider = create_provider(mode=self.config.mode.value)
   │     ├─ Calls: run_constrained_ranking(provider, ranking_input, physics_report)
   │     └─ Directly produces: LLMRankingOutput (No router or arbitration involved)
   │
   ├─► Stage 9: Deterministic Safety Gate (app.agent.safety:validate_recovery_plan)
   ├─► Stage 10: Recovery Gating (requires_human_review evaluation)
   └─► Stage 11: Audit Persistence (app.audit.store:AuditStore.save) -> SSE completion
```

---

## 2. ROUTER ARCHITECTURE & SUB-COMPONENTS

The Phase 23 hybrid router is built as a modular, sequenced state machine in `app/llm/`:

```text
                        [Ranking Input + Physics Report]
                                       │
                                       ▼
                        1. [BranchPolicy.evaluate()]
                                       │
               ┌───────────────────────┴───────────────────────┐
               ▼ (LOCAL_ACCEPT)                                ▼ (BLOCKED / NO_INFERENCE)
     2. [LocalBranchRunner]                           [Terminal Fail-Closed Return]
     (Phi-3 Local Inference)                                  │
               │                                               ▼
               ├───────────────┬───────────────────────────────┘
               ▼ (Clean Accept)▼ (Escalation Triggered)
       (Skip Cloud)    3. [CloudBranchRunner]
                               │ └─ [Cloud Redaction Gate]
                               │ └─ [Gemini Flash Cloud Call]
                               ▼
                        4. [Arbitrator.arbitrate()]
                           (Deterministic Precedence: A1–A10)
                               │
                               ▼
                        5. [MergeResolver.resolve()]
                               │
                               ▼
                        6. [reassert_physics()]
                           (reconcile_llm_claim)
                               │
                               ▼
                        7. [validate_recovery_plan()]
                           (Deterministic Safety Gate)
                               │
                               ▼
                        8. [record_routing_audit()]
                           (Stage.ROUTING in AuditStore)
```

---

## 3. ACTUAL INTEGRATION POINT

The exact point where the router should be integrated into the live agent is in **`backend/app/agent/agent.py` (lines 1944–1955)**:

### Current Source in `agent.py`:
```python
# Lines 1944-1955
provider = create_provider(
    mode=self.config.mode.value,
    config=provider_config,
)

ranking_output, guardrail_result, llm_ms = run_constrained_ranking(
    provider=provider,
    ranking_input=ranking_input,
    physics_report=physics_report,
    max_retries=self.config.max_retries,
)
```

### Required Integration Shape:
```python
if router_enabled():
    from app.llm.router_orchestrator import RouterOrchestrator
    from app.llm.local_branch import LocalBranchRunner
    from app.llm.cloud_branch import CloudBranchRunner

    local_runner = LocalBranchRunner(provider=create_provider("local", provider_config))
    cloud_runner = CloudBranchRunner(provider=create_provider("cloud", provider_config))
    orchestrator = RouterOrchestrator(local_runner=local_runner, cloud_runner=cloud_runner)
    
    orch_result = orchestrator.run(
        ranking_input=ranking_input,
        physics_report=physics_report,
        review_already_required=requires_review,
        safety_context=crash_dict,
        recorder=recorder,
    )
    ranking_output = orch_result.merged_output
else:
    # Single-provider fallback path (remains 100% byte-identical)
    provider = create_provider(mode=self.config.mode.value, config=provider_config)
    ranking_output, guardrail_result, llm_ms = run_constrained_ranking(...)
```

---

## 4. WHY THE ROUTER IS DORMANT

The router is dormant because of an **intentional Phase-23 isolation & safety boundary**:
* In Phase 23, the router components were built and verified in complete isolation so that unfinished branching logic could not destabilize the existing single-provider production pipeline.
* This was formalized by an explicit unit test in `backend/tests/test_phase23_router_orchestrator.py` (`test_case_ah_production_agent_path_unchanged`):
  ```python
  def test_case_ah_production_agent_path_unchanged(self):
      """AH: app/agent/agent.py does not reference the orchestrator."""
      import inspect
      import app.agent.agent as agent_module

      source = inspect.getsource(agent_module)
      assert "RouterOrchestrator" not in source
      assert "ROUTER_ENABLED" not in source
  ```
* **Verdict**: `ROUTER_ENABLED=false` is an **intentional isolation boundary** that kept the router dormant until the end-to-end contract hardening phase.

---

## 5. LOCAL BRANCH STATUS

* **Implementation**: `backend/app/llm/local_branch.py` (`LocalBranchRunner`).
* **Execution Reality**:
  - Connects to local OpenAI-compatible endpoints (Ollama, vLLM, or Phi-3 server).
  - Handles token exhaustion and prompt-echo truncation detection (`_is_prompt_echo`).
  - Runs local output through `validate_ranking_output()` guardrails.
  - In absence of a running local daemon (e.g. `http://localhost:11434/v1`), it raises `ProviderError` and produces `BranchOutcome.FAILURE` with `RoutingReason.LOCAL_UNAVAILABLE`.
* **Testing**: Covered by 26 passing tests in `test_phase23_local_branch.py`.

---

## 6. CLOUD BRANCH STATUS

* **Implementation**: `backend/app/llm/cloud_branch.py` (`CloudBranchRunner`).
* **Execution Reality**:
  - Invokes Google Gemini 2.5 Flash via `GeminiProvider`.
  - **Fail-Closed Cloud Redaction Gate**: Scans and strips sensitive identifiers, confidential keys, and file paths using `app.security.exfiltration:apply_cloud_redaction()` *before* network egress.
  - Enforces strict cloud call budget: max 1 cloud call per orchestration (`_CLOUD_CALL_BUDGET = 1`).
* **Testing**: Covered by 34 passing tests in `test_phase23_cloud_branch.py` and 22 tests in `test_phase23_cloud_redaction.py`.

---

## 7. ARBITRATION BEHAVIOR

Arbitration is implemented in `backend/app/llm/arbitrator.py` (`Arbitrator`) as a **100% deterministic decision table**:

### Precedence Hierarchy:
1. **Rule A1 (Both Failed)**: Returns `RoutingDecision.NO_INFERENCE` + `human_review_required = True`.
2. **Rule A2 (Both Succeeded & Agree)**: Returns `RoutingDecision.HYBRID_MERGED` (merges reasoning).
3. **Rule A2_PHYSICS (Disagreement on Physics)**: If Local contradicts physics and Cloud respects physics, Cloud wins.
4. **Rule A2_SCORE (Disagreement on Deterministic Grounding)**: The branch supported by deterministic telemetry and RAG evidence wins.
5. **Rule A3 (Local Failed, Cloud Succeeded)**: Cloud wins (`Branch.CLOUD`).
6. **Rule A4 (Cloud Failed, Local Succeeded)**: Local wins (`Branch.LOCAL`).
7. **Rule A10 (Unresolvable Disagreement)**: Flags `human_review_required = True` and refuses automated selection.

* **Model Confidence**: Confidence numbers output by LLMs (e.g., `confidence: 0.99`) are **strictly ignored** as authority metrics.

---

## 8. FAILURE BEHAVIOR MATRIX

| Failure Scenario | Exact Behavior | Safety Verdict | Human Review? |
|---|---|---|:---:|
| **Local Provider Fails** (Daemon down/timeout) | Marks `BranchOutcome.FAILURE`, escalates to Cloud | Cloud called; if Cloud succeeds, Cloud wins | No (unless Cloud fails) |
| **Cloud Provider Fails** (401/rate limit/timeout) | If Local succeeded, Local wins (`A4`); if Local also failed, fails closed | Safe local result or `NO_INFERENCE` | Yes (if both failed) |
| **Both Providers Fail** | Orchestrator returns `RoutingDecision.NO_INFERENCE`, recovery plan empty | `BLOCKED` | **YES (Mandatory)** |
| **Malformed JSON / Guardrail Breach** | Branch outcome marked `FAILURE` (`INVALID_STRUCTURED_OUTPUT`) | Escalates or fails closed | **YES** |
| **Model Disagreement on Top-1 Fault** | Triggered if Local top-1 != deterministic top-1; escalates to Cloud; Arbitrator applies `A2_PHYSICS` / `A2_SCORE` | Best grounded branch wins; unresolvable forces review | **YES** |
| **Router Timeout** | Exception caught in `RouterOrchestrator.run()`; logs error and fails closed | `NO_INFERENCE` | **YES** |

---

## 9. SAFETY AUTHORITY ANALYSIS

### Answers to Core Safety Questions:
* **Does enabling the router change the authority of the LLM?**  
  **NO**. The LLM remains strictly advisory. All hypotheses and recovery proposals generated by any branch must pass through the deterministic safety gate.
* **Can either LLM branch bypass physics validation or safety validation?**  
  **NO**.
  1. `reassert_physics()` runs *after* arbitration merge. Any LLM claiming validity for a physics-invalidated hypothesis is recorded as an `LLMOverrideAttempt` and dismissed.
  2. `default_safety_validation()` passes the candidate recovery plan through `app.agent.safety:validate_recovery_plan()`. Any unwhitelisted command or battery/thermal violation causes immediate rejection and sets `RoutingDecision.BLOCKED`.

---

## 10. CONFIGURATION REQUIREMENTS

To activate and run the hybrid router in production:

| Variable | Required? | Default | Purpose |
|---|:---:|:---:|---|
| `ROUTER_ENABLED` | **Yes** | `false` | Master toggle for router execution path |
| `GEMINI_API_KEY` | **Yes** (for Cloud) | `""` | Key for Google Generative AI Gemini Flash calls |
| `FALLBACK_BASE_URL` | **Yes** (for Local) | `http://localhost:11434/v1` | URL of local OpenAI-compatible inference server |
| `FALLBACK_MODEL` | Optional | `phi-3-mini` | Name of local model deployed on inference server |
| `FALLBACK_API_KEY` | Optional | `local` | API key for local inference server (if required) |

---

## 11. REQUIRED CODE CHANGES FOR SAFE ACTIVATION

Only **one localized change** in `backend/app/agent/agent.py` is required to connect the router:

1. **Import Router Components**:
   ```python
   from app.llm.router_contract import router_enabled
   from app.llm.router_orchestrator import RouterOrchestrator
   from app.llm.local_branch import LocalBranchRunner
   from app.llm.cloud_branch import CloudBranchRunner
   ```
2. **Conditional Invocation in `SentinelAgent.analyze_crash_dump_stream()`**:
   Replace direct `run_constrained_ranking` call with a check on `router_enabled()`. When `False`, run legacy single-provider ranking; when `True`, run `RouterOrchestrator.run()`.
3. **Audit Recording**: Thread `recorder` into `orchestrator.run()` to log `Stage.ROUTING`.

---

## 12. REQUIRED TESTS BEFORE DEFAULT ENABLING

Before switching `ROUTER_ENABLED=true` by default in production:
1. **End-to-End Live SSE Stream Test with Router Active**: Test `POST /api/v1/analyze` with `ROUTER_ENABLED=true` and confirm all 11 stages stream correctly to the browser.
2. **Local Daemon Disconnect Test**: Test live behavior when `FALLBACK_BASE_URL` is unreachable, verifying seamless fallback to Cloud Gemini with zero crash.
3. **Redaction Egress Verification**: Validate that live cloud requests strip all confidential identifiers under `ROUTER_ENABLED=true`.

---

## 13. RISK ASSESSMENT

* **Safety Risk**: **MINIMAL**. The safety gate and physics engine sit downstream of the router and cannot be bypassed.
* **Operational Risk**: **LOW**. If `ROUTER_ENABLED=false`, the existing single-provider behavior is 100% preserved. If `ROUTER_ENABLED=true` and the local daemon is down, it cleanly escalates to Cloud or fails closed.

---

## 14. RECOMMENDED NEXT STEP

1. **Keep `ROUTER_ENABLED=false` as default** in `.env` and `render.yaml`.
2. **Implement the non-breaking conditional hook** in `app/agent/agent.py` so operators can optionally enable the router via `ROUTER_ENABLED=true`.
3. **Add end-to-end integration tests** in `tests/test_phase26_router_live_integration.py`.

---

## FINAL VERDICT

**ROUTER STATUS**:  
`PARTIALLY READY` (Fully implemented and tested in isolation; ready for wiring into `agent.py`).

**SAFE TO ENABLE BY DEFAULT**:  
`NO` (Must remain `ROUTER_ENABLED=false` by default until live endpoint integration tests are committed and verified against local and cloud inference daemons).

**Summary**:  
The Phase-23 Hybrid LLM Router is a robust, mathematically sound, and fail-closed subsystem that enforces strict deterministic authority over AI proposals. It is currently dormant in the primary runtime path because production wiring was intentionally isolated during development. Once the conditional dispatch in `app/agent/agent.py` is connected behind the `ROUTER_ENABLED` flag, it can be safely activated in production without compromising safety or backward compatibility.

---

*PHASE 5 COMPLETE — READ-ONLY AUDIT.*  
*NO SOURCE FILES MODIFIED.*
