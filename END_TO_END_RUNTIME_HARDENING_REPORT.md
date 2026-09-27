# SENTINEL PHASE 9: END-TO-END RUNTIME HARDENING REPORT

**Author:** SENTINEL Core Engineering  
**Subsystem:** Runtime Path Hardening, Integration, and Core Invariants Verification  
**Date:** September 2026  
**Status:** COMPLETE (Phase 9 Target Reached)

---

## 1. Actual Runtime Architecture

The production runtime architecture operates as a strictly unidirectional, fail-closed diagnostic pipeline with zero autonomous spacecraft command uplink authority:

```
[ POST /api/v1/analyze ] (Raw Ingestion & Pydantic Validation)
          │
          ▼
1. Telemetry Ingest & Adapter (app.api.adapters)
   - Canonical window extraction & bounds merging
   - Nan/Inf/out-of-range value sanitization
   - Provenance stamping & run_id allocation
          │
          ▼
2. Anomaly Detection Engine (app.detection.fusion)
   - 10 deterministic detectors: Hard Limits, Discrete State, Counter,
     Data Quality, Z-Score, Robust Z-Score, Rate of Change, Trend,
     Persistence, Sudden Change
   - Cross-channel fusion & earliest offset computation
          │
          ▼
3. Channel Reconciliation (app.reconciliation)
   - Cross-checks nominal limits against operational observations
   - Resolves telemetry discrepancies without physics invention
          │
          ▼
4. State & Residual Estimation (app.estimation)
   - Physical state sequence propagation (Euler angles, body rates, currents, voltages)
   - Multi-node thermal residual calculation
          │
          ▼
5. 3-Axis Rigid-Body Attitude Dynamics (app.validation.physics)
   - Quaternion kinematic integration: q_dot = 1/2 * Ω(ω) * q
   - Euler 3-axis rotational dynamics: I * ω_dot + ω × (I * ω) = τ_ext
   - 3×3 inertia tensor cross-product validation
          │
          ▼
6. Multi-Node Thermal Network (app.validation.physics)
   - Multi-node heat transfer: C_i * dT_i/dt = Q_internal + Q_heater + Σ G_ij(T_j - T_i) - Q_rad
   - Stefan-Boltzmann T^4 radiation against 3 K space sink
          │
          ▼
7. Procedure Retrieval / RAG (app.procedures.retrieval)
   - TF-IDF and structured procedure library lookup (PROCEDURE_LIBRARY)
   - Mandatory ECSS / fallback provenance citations
          │
          ▼
8. Constrained LLM Ranking & Hybrid Router (app.llm.ranker & router_orchestrator)
   - Behind ROUTER_ENABLED flag (default false)
   - Constrained JSON prompt enforcing deterministic candidate set
   - 7 guardrail checks (prevents invented faults, commands, and physics overrides)
   - Cross-branch deterministic arbitration (Physics > Evidence > Guardrails > Discriminators)
          │
          ▼
9. Physics Reassertion Gate
   - Verification that no model reasoning can override a REFUTED physics verdict
          │
          ▼
10. Fail-Closed Safety Gate (app.agent.safety)
    - Command whitelist validation (CMD_UPPER_SNAKE_CASE & COMMAND_REGISTRY)
    - Subsystem constraint checking (battery SoC floor, gyro rate limit, thermal bounds)
    - Monotone human review enforcement
          │
          ▼
11. Cryptographic Audit Finalization (app.audit)
    - Append-only hash chain linking (SHA-256)
    - Stage-by-stage recording with nanosecond-resolution timing
    - SQLite persistence isolation
          │
          ▼
12. SSE / API Streaming Output (app.main)
    - SSEEvent streaming with run correlation IDs and structured stage metadata
    - Final SentinelOutput payload delivery
```

---

## 2. Complete Execution Trace

A representative request (`POST /api/v1/analyze`) executes across 12 discrete stages with zero bypasses:

| Stage # | Stage Name | Implementation Module | Emitted Event / Audit Stage | Authority / Trust Level |
|---------|------------|-----------------------|-----------------------------|-------------------------|
| 1 | Ingestion | `app.api.adapters` | `SSEEventType.STATUS` (Stage 1) | Fully Deterministic |
| 2 | Detection | `app.detection.fusion` | `SSEEventType.OBSERVATION` (Stage 2) | Fully Deterministic |
| 3 | Reconciliation | `app.reconciliation` | `SSEEventType.OBSERVATION` (Stage 3) | Fully Deterministic |
| 4 | Hypothesis Gen | `app.diagnosis` | `SSEEventType.OBSERVATION` (Stage 4) | Fully Deterministic |
| 5 | Physics Validation | `app.validation.physics` | `SSEEventType.OBSERVATION` (Stage 5) | Fully Deterministic |
| 6 | State Estimation | `app.estimation` | `SSEEventType.OBSERVATION` (Stage 6) | Fully Deterministic |
| 7 | RAG Retrieval | `app.procedures.retrieval`| `SSEEventType.OBSERVATION` (Stage 6.5)| Fully Deterministic |
| 8 | Constrained LLM | `app.llm.ranker` | `SSEEventType.OBSERVATION` (Stage 7) | UNTRUSTED Advisory |
| 9 | Guardrail Filter | `app.llm.ranker` | Violation Warnings (Stage 7) | Deterministic Filter |
| 10 | Router Arbitration| `app.llm.arbitrator` | `Stage.ROUTING` (when enabled) | Fully Deterministic |
| 11 | Safety Gate | `app.agent.safety` | `SSEEventType.OBSERVATION` (Stage 8) | Fail-Closed Authority |
| 12 | Audit & Delivery | `app.audit`, `app.main` | `SSEEventType.RESULT`, Audit Record | Tamper-Evident Seal |

---

## 3. Failures & Defects Discovered During Audit

1. **Diagnosis Audit Duration Calculation Defect**:
   - *Location*: `backend/app/agent/agent.py` line 2128.
   - *Defect*: `_audit_record_diagnosis(recorder, result, (time.time() - time.time()) * 1000.0)`.
   - *Impact*: The diagnosis stage latency recorded in the cryptographic audit entry was always identically `0.0 ms`.
   - *Resolution*: Replaced with `(time.perf_counter() - _pipeline_started) * 1000.0`.

2. **SSEEvent Metadata & Correlation ID Propagation Gap**:
   - *Location*: `backend/app/api/models.py`, `backend/app/agent/agent.py`, `backend/app/main.py`.
   - *Defect*: `SSEEvent` lacked a first-class `run_id` and structured `metadata` field, meaning streaming events from `analyze_crash_dump_stream` could not be correlated with the underlying audit trace.
   - *Resolution*: Added `run_id: Optional[str] = None` and `metadata: Optional[dict[str, Any]] = None` to `SSEEvent`. Wired `effective_run_id` into event generation helper `_make_event` in `agent.py` and connected it in `main.py`.

3. **Telemetry Key Reconciliation in Synthetic Telemetry Windows**:
   - *Location*: `backend/tests/test_phase9_runtime_hardening.py`.
   - *Defect*: Direct usage of uncanonicalized dictionary keys (`telemetry_window` vs `pre_fault_telemetry_window` and `pre_fault_telemetry`) bypassed `with_canonical_window()`, causing detection to observe 0 readings.
   - *Resolution*: Formatted synthetic telemetry fixtures to include both timed window arrays and baseline legacy rows, wrapping with `with_canonical_window()` to match production intake.

---

## 4. Fixes Made

1. **`backend/app/api/models.py`**:
   - Added `run_id: Optional[str] = None` and `metadata: Optional[dict[str, Any]] = None` to `SSEEvent`.
   - Added `Any` import to `from typing import Any, Dict, List, Optional`.

2. **`backend/app/agent/agent.py`**:
   - Added `run_id: str | None = None` parameter to `analyze_crash_dump_stream`.
   - Initialized pipeline start counter `_pipeline_started = time.perf_counter()`.
   - Fixed diagnosis audit timing at line 2128 to `(time.perf_counter() - _pipeline_started) * 1000.0`.
   - Added `_make_event` helper attaching `run_id=effective_run_id` to events.

3. **`backend/app/main.py`**:
   - Forwarded `run_id=recorder.run_id` from `analyze_endpoint_v1` into `agent.analyze_crash_dump_stream`.
   - Attached `run_id` and audit metadata to the initial `opened`, intermediate error, and terminal `done` SSE events.

4. **`backend/tests/test_phase9_runtime_hardening.py`**:
   - Created comprehensive 17-test integration suite covering all 12 stages, 12 adversarial scenarios, and 6 core system invariants.

---

## 5. Tests and Exact Results

### Targeted Phase 9 Suite (`test_phase9_runtime_hardening.py`)
```bash
python3 -m unittest tests/test_phase9_runtime_hardening.py
```
**Output:**
```
Ran 17 tests in 0.696s
OK
```

#### Individual Test Results:
1. `test_01_e2e_nominal_telemetry_flow`: **PASS** (Ingestion -> Detection -> Reconciliation -> State -> Physics -> RAG -> LLM -> Safety -> Audit -> Result)
2. `test_02_adversarial_missing_telemetry`: **PASS** (Empty/missing telemetry handled safely without crash)
3. `test_03_adversarial_malformed_telemetry`: **PASS** (Malformed types sanitized safely)
4. `test_04_adversarial_stale_telemetry`: **PASS** (Zero-dt timestamps handled without ZeroDivisionError)
5. `test_05_adversarial_contradictory_telemetry`: **PASS** (Contradictory battery/solar telemetry detected and flagged)
6. `test_06_physics_refuted_prevents_executable_plan`: **PASS** (Guardrail 4 demotes physics-refuted hypothesis)
7. `test_07_local_llm_failure_routes_to_cloud`: **PASS** (Local branch failure cleanly arbitration-routed to cloud branch)
8. `test_08_cloud_llm_failure_routes_to_local`: **PASS** (Cloud timeout cleanly arbitration-routed to local branch)
9. `test_09_both_llms_unavailable_fallback_failsafe`: **PASS** (Both branches unavailable enforces terminal HUMAN_REVIEW)
10. `test_10_malformed_llm_output_repair_and_retry`: **PASS** (Malformed JSON LLM output handled safely without crash)
11. `test_11_unsafe_unwhitelisted_command_rejected`: **PASS** (Unwhitelisted commands blocked with NOT_IN_REGISTRY)
12. `test_12_critical_missing_precondition_fails_closed`: **PASS** (Precondition violations fail closed)
13. `test_13_audit_persistence_failure_isolated`: **PASS** (Audit disk/store failure isolated from streaming loop)
14. `test_14_invariants_proven`: **PASS** (All 6 core invariants verified)
15. `test_15_run_correlation_ids_and_metadata`: **PASS** (Correlation IDs and structured metadata verified on SSEEvent)
16. `test_16_performance_latency_and_memory`: **PASS** (50-sample telemetry analysis in < 3000 ms, peak memory < 50 MB)
17. `test_17_stub_offline_execution_zero_network`: **PASS** (Offline execution verified with socket patching)

### Comprehensive Regression Battery
```bash
python3 -m unittest tests/test_phase8_thermal_dynamics.py \
                     tests/test_phase8_tc_builder.py \
                     tests/test_phase8_physics.py \
                     tests/test_phase7_attitude_dynamics.py \
                     tests/test_safety.py \
                     tests/test_phase26_router_live_integration.py \
                     tests/test_phase4_audit.py
```
**Output:**
```
Ran 234 tests in 1.062s
OK
```

---

## 6. End-to-End Performance Evidence

Measurements were taken using `time.perf_counter()` and Python's `tracemalloc` on synthetic spacecraft telemetry windows under offline `STUB` execution:

| Telemetry Window Size | End-to-End Latency (ms) | Peak Memory Allocation (MB) | SSE Events Emitted |
|-----------------------|-------------------------|------------------------------|--------------------|
| 10 Samples (Cold Run) | 1,229.14 ms             | 23.79 MB                     | 30 events          |
| 50 Samples (Warmed)   | 184.15 ms               | 2.60 MB                      | 30 events          |
| 100 Samples (Warmed)  | 280.18 ms               | 5.02 MB                      | 33 events          |

- **Throughput**: ~350 samples/second processed through the complete 12-stage pipeline.
- **Memory Footprint**: Steady-state peak memory is approximately 2.6 MB for a standard 50-sample window.

---

## 7. Remaining Limitations

1. **Single-Process SQLite Storage**:
   - The production audit store uses an embedded SQLite database (`audit.db`). Under high-concurrency multi-threaded writes, file-locking contention could occur.
2. **Deterministic Fallback Model**:
   - When external LLM inference is unreachable (both local Ollama and cloud Gemini fail), the system degrades to deterministic procedural ranking and enforces mandatory operator human review. It does not perform autonomous heuristic reasoning.
3. **Synchronous Generator Streaming**:
   - Streaming currently relies on Python generator iteration (`yield SSEEvent`) wrapped by FastAPI's `StreamingResponse`. Network backpressure or dropped client connections are detected at the socket boundary.

---

## 8. REAL vs SIMULATED Classification

| Component | Classification | Description & Evidence |
|-----------|----------------|------------------------|
| **Anomaly Detection** | **REAL** | 10 real mathematical algorithms (hard limits, z-score, linear trend, rate-of-change) executing on telemetry. |
| **3-Axis Physics Model** | **REAL** | Numerical Runge-Kutta / Euler integration of real rigid-body dynamics ($I \dot{\omega} + \omega \times I \omega = \tau$) with quaternions. |
| **Multi-Node Thermal Model**| **REAL** | Deterministic finite-difference multi-node thermal nodal network with Stefan-Boltzmann radiative transfer. |
| **Fail-Closed Safety Gate** | **REAL** | Exhaustive command whitelist and physical constraint validator; blocks unauthorized steps. |
| **Telecommand Builder** | **REAL** | Deterministic ground-segment binary serializer (CCSDS TC frame framing, CRC-16-CCITT calculation). |
| **Cryptographic Audit Log** | **REAL** | SHA-256 hash-chained immutable audit records with SQLite storage. |
| **Spacecraft Telemetry Data**| **SIMULATED / BENCHMARK**| Telemetry originates from synthetic fault generator or archival ESA-ADB mission datasets; no live spacecraft link. |
| **Spacecraft Command Uplink**| **STRICTLY SIMULATED**| Outbound hardware uplink is disabled by design (`OutboundUplinkBoundary.ENABLED = False`). |

---

## 9. Explicit Statement of What Is NOT Implemented

1. **NO Autonomous Spacecraft Commanding**:
   - SENTINEL has **zero** autonomous authority to transmit RF signals, send telecommands over network sockets, or command hardware actuators.
   - `OutboundUplinkBoundary.transmit()` unconditionally raises `UplinkDisabledError`.
2. **NO Flight Hardware Qualification**:
   - This software is not qualified for flight avionics execution (ECSS-Q-ST-80C / DO-178C). It is designed solely as ground-segment advisory software.
3. **NO Real-Time RF Spacecraft Link**:
   - No hardware transceivers, ground station modem interfaces, or direct CCSDS RF baseband encoding are present.
4. **NO LLM Self-Authorization**:
   - The LLM cannot authorize, unblock, or transmit commands, nor can it override physical model verdicts or human review flags.
5. **NO Phase 10 Features**:
   - Work strictly ceased at the Phase 9 runtime hardening boundary.
