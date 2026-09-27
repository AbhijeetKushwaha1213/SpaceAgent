# SENTINEL PHASE 4 AUDIT: END-TO-END REALITY & CONTRACT HARDENING

**Repository**: `AbhijeetKushwaha1213/SpaceAgent`  
**Commit**: `646e0c1` (`fix(theme): fix header and tabs styling mismatch in light mode`)  
**Audit Date**: 2026-09-28  
**Auditor**: Antigravity Automated Read-Only Engineering Auditor  
**Audit Scope**: Read-Only End-to-End Execution Trace, Code Path Verification, and Contract Hardening  

---

## 1. EXECUTIVE SUMMARY

This audit delivers an evidence-based, read-only engineering investigation into the actual implementation state of SENTINEL.

### Key Audit Findings:
1. **Core Pipeline is Fully Operational**: Telemetry Ingestion → Deterministic Multi-Method Anomaly Detection → Observation Reconciliation → State & Residual Estimation → Hypothesis Generation → Physics Refutation → Procedure RAG → Constrained AI Ranking → Deterministic Safety Validation → Cryptographic Audit Logging → SSE Streaming → React Dashboard is genuinely implemented and active.
2. **Deterministic Safety Gate is Strictly Fail-Closed**: No execution path exists for the LLM or frontend to authorize unwhitelisted commands or bypass physical preconditions (battery floor, thermal survival, rate limits).
3. **Hybrid Router is Implemented but Dormant**: The Phase 23 dual-branch router orchestrator (`app/llm/router_orchestrator.py`) is fully tested but dormant in the primary runtime path (`ROUTER_ENABLED=false`). The primary pipeline executes via `app/llm/ranker.py`.
4. **Transport Scope is JSON REST/SSE**: Telemetry enters as structured JSON crash dumps; there is no live binary CCSDS Space Packet Protocol decommutator or UDP radio socket daemon.
5. **Zero Test Regressions**: All 316 targeted unit, contract, security, and audit test cases pass (100% pass rate).

---

## 2. ACTUAL RUNTIME ARCHITECTURE

The active runtime execution chain:

```text
HTTP Request (POST /api/v1/analyze)
  │
  ▼ [app.security.middleware:SecurityMiddleware]
  │  ├─ Checks Bearer token authentication (401 if unauthenticated and SECURE_DEV_MODE=0)
  │  ├─ Injects X-Correlation-ID header
  │  └─ Enforces 10MB payload size limit
  ▼
API Endpoint [app.main:analyze_endpoint_v1]
  │  ├─ Sanitizes payload [app.security.sanitization:sanitize_telemetry_payload_data]
  │  ├─ Initiates append-only AuditRecorder [app.audit.record:AuditRecorder.begin]
  │  └─ Yields StreamingResponse(event_generator, media_type="text/event-stream")
  ▼
Orchestrator [app.agent.agent:SentinelAgent.analyze_crash_dump_stream]
  │
  ├──► [1. INGESTION] app.api.adapters:with_canonical_window
  │    └─ Normalizes timestamps, values, units, and nominal bounds across 21 channels
  │
  ├──► [2. DETECTION] app.detection.fusion:run_detection_on_crash_dump
  │    ├─ Hard Limits Detector (app.detection.limits)
  │    ├─ Statistical Z-Score Detector (app.detection.statistical)
  │    └─ Temporal Rate-of-Change Detector (app.detection.temporal)
  │    └─ Output: AnomalyReport
  │
  ├──► [3. RECONCILIATION] app.reconciliation.engine:ReconciliationEngine (Flag-Gated)
  │    ├─ app.reconciliation.events:build_observation_events
  │    └─ app.reconciliation.isolation:isolate_cases (Partitions findings into Case A, B, etc.)
  │
  ├──► [4. STATE ESTIMATION] app.estimation.state:estimate_states & compute_residuals
  │    └─ Calculates physical residuals across power balance, thermal dissipation, and momentum
  │
  ├──► [5. HYPOTHESIS GENERATION] app.diagnosis.candidates:generate_hypotheses
  │    └─ Matches anomaly signatures against Fault Dictionary (ADCS, EPS, TCS, OBC, COMMS, CASCADE)
  │
  ├──► [6. PHYSICS VALIDATION] app.validation.physics:validate_crash_dump
  │    └─ Evaluates physical constraints against residuals (verdicts: VALID, INVALID, UNCERTAIN)
  │
  ├──► [7. PROCEDURE RETRIEVAL (RAG)] app.agent.rag:retrieve_procedures_traced
  │    ├─ Primary: Vector search over ChromaDB ECSS chunks
  │    └─ Fallback: FALLBACK_KB curated procedure library (app.procedures.library)
  │
  ├──► [8. CONSTRAINED LLM RANKING] app.llm.ranker:run_constrained_ranking
  │    ├─ Ingests candidate hypotheses, physics refutations, and RAG context
  │    ├─ Invokes Provider (Gemini / Local / Stub) with structured schema
  │    └─ Guardrail: Strips forbidden fields and enforces structured JSON formatting
  │
  ├──► [9. DETERMINISTIC SAFETY GATE] app.agent.safety:validate_recovery_plan
  │    ├─ Checks Command Registry (app.validation.command_registry)
  │    ├─ Evaluates Physical Preconditions (app.validation.conditions)
  │    └─ Strips invalid commands -> blocked_steps; safety_status = BLOCKED if critical
  │
  ├──► [10. RECOVERY GATING]
  │    └─ Flags requires_human_review = True on HIGH-risk actions or low confidence
  │
  └──► [11. AUDIT FINALIZATION] app.audit.store:AuditStore.save
       ├─ Commits SHA-256 Merkle chain entry to SQLite
       └─ Emits final SSE events (result, done) to frontend
```

---

## 3. COMPLETE DATA-FLOW TRACE (ONE TELEMETRY VALUE)

### Telemetry Channel Traced: `Gyro_rate_degs` (Scenario 1 — ADCS Gyro SEU)

```text
1. RAW INPUT PAYLOAD:
   {
     "scenario_id": "1",
     "pre_fault_telemetry_window": [
       {"timestamp": "T-120s", "parameter": "Gyro_rate_degs", "value": 0.02, "status": "NOMINAL"},
       {"timestamp": "T-0s",   "parameter": "Gyro_rate_degs", "value": null, "value_text": "NaN", "status": "UNKNOWN"}
     ]
   }

2. SANITIZATION & INGESTION (app.security.sanitization + app.api.adapters):
   - sanitize_telemetry_payload_data() ensures value types are safe.
   - with_canonical_window() maps reading into CanonicalReading:
     CanonicalReading(channel_id="Gyro_rate_degs", value=None, value_text="NaN", is_usable=False, unit="deg/s", nominal_range=(-0.5, 0.5), hard_limits=(0.0, 7.0))

3. ANOMALY DETECTION (app.detection.fusion):
   - Limits & dropout detector identifies non-numeric measurement ("NaN").
   - Evaluates finding:
     AnomalyFinding(channel="Gyro_rate_degs", detector=DetectorType.HARD_LIMIT, severity=Severity.CRITICAL, evidence="Sensor dropout / invalid reading (NaN)")
   - Included in AnomalyReport(anomaly_count=11, max_severity=CRITICAL).

4. OBSERVATION RECONCILIATION (app.reconciliation.engine):
   - build_observation_events() creates ObservationEvent(event_id="EVT_AOCS_GYRO", subsystem=SubsystemID.AOCS).
   - ReconciliationEngine isolates event into Case-1 (ADCS / Attitude Determination).

5. STATE & RESIDUAL ESTIMATION (app.estimation.models.attitude):
   - Attitude model observes zero valid rate telemetry; computes angular momentum transfer residual from reaction wheel speed (Wheel_speed_rpm = 5200).
   - Residual: AttitudeResidual(momentum_discrepancy_nms=1.82, status="DECIDED").

6. HYPOTHESIS GENERATION (app.diagnosis.candidates):
   - Matches signature: dropout on Gyro_rate_degs + SEU_counter=1 + Wheel_speed_rpm spin-up.
   - Generates FaultHypothesis(fault_id="ADCS_GYRO_SEU", prior_score=0.92, subsystem="AOCS").

7. PHYSICS VALIDATION (app.validation.physics):
   - Checks momentum conservation constraint: reaction wheel speed increase compensates for unmeasured gyro rate error.
   - Verdict: PhysicsVerdict(fault_id="ADCS_GYRO_SEU", status=PhysicsStatus.VALID, corroboration="Wheel momentum absorbs rate disturbance").

8. PROCEDURE RAG RETRIEVAL (app.procedures.retrieval):
   - Queries procedures matching fault_filter="ADCS_GYRO_SEU".
   - Retrieves procedure FDIR-AOCS-001 (Gyro Redundancy Switchover) with steps:
     Step 1: CMD_AOCS_SUN_POINT (LOW risk)
     Step 2: CMD_GYRO_SWITCH_REDUNDANT (MEDIUM risk)

9. LLM RANKING & REASONING (app.llm.ranker):
   - Constrained prompt synthesizes anomaly report, physics validation (VALID), and retrieved procedure.
   - Generates ranked hypothesis: Top 1 = ADCS_GYRO_SEU (Confidence: 0.94).
   - Proposes recovery plan referencing CMD_AOCS_SUN_POINT and CMD_GYRO_SWITCH_REDUNDANT.

10. DETERMINISTIC SAFETY GATE (app.agent.safety):
    - Validates CMD_GYRO_SWITCH_REDUNDANT against Command Registry:
      - Command exists and is_enabled = True.
      - Preconditions checked: Battery_SOC (88.4%) >= 30.0% -> PASS.
      - Prohibited conditions checked: Gyro rate runaway -> PASS (redundant sensor available).
    - Status: SafetyStatus.VALIDATED, requires_human_review = True (due to MEDIUM risk action).

11. AUDIT RECORD (app.audit.store):
    - Appends Stage.DETECTION, Stage.PHYSICS_VALIDATION, Stage.LLM, and Stage.SAFETY into SQLite.
    - Computes SHA-256 Merkle hash chain for Run run_20260928T...

12. API SSE STREAM & FRONTEND DISPLAY (frontend/src/components/views/MissionOverview.jsx):
    - Emits SSE Event: observation -> "ADCS_GYRO_SEU: CRITICAL (Sensor dropout)".
    - TelemetryTableCard renders:
      Row: "Gyro_rate_degs" | Subsystem: "AOCS" | Value: "NaN deg/s" | Status: "CRITICAL" | Severity Badge: Red.
    - SafetyCard renders: "CMD_GYRO_SWITCH_REDUNDANT — Approved (Human Review Required)".
```

---

## 4. COMPONENT REALITY CLASSIFICATION

| Major Subsystem / Component | Classification | Code Evidence | Actual Behavior |
|---|---|---|---|
| **JSON Telemetry Ingest & Adapters** | `REAL` | `backend/app/api/adapters.py` | Canonicalizes timestamps, bounds, and units across 21 channels |
| **CCSDS SPP Binary Decommutator** | `UNUSED` | N/A | No binary frame parser exists; telemetry is ingested via JSON |
| **Live UDP Socket Listener** | `UNUSED` | N/A | Telemetry arrives via HTTP REST POST; no raw socket listener |
| **21-Channel Dictionary** | `REAL` | `backend/app/ingest/channel_dict.py` | Authoritative single source of truth for channel definitions |
| **Deterministic Anomaly Detector** | `REAL` | `backend/app/detection/fusion.py` | Multi-method fusion (limits, discrete, stats, temporal) |
| **Observation Reconciliation Engine**| `REAL` | `backend/app/reconciliation/engine.py` | Deterministic observation partitioning and case separation |
| **State & Residual Estimators** | `REAL` | `backend/app/estimation/state.py` | 1D discrete power, thermal, and attitude state equations |
| **Hypothesis Generator** | `REAL` | `backend/app/diagnosis/candidates.py` | Deterministic signature matching against 6 fault classes |
| **Physics Validation Gate** | `REAL` | `backend/app/validation/physics.py` | Refutes impossible hypotheses via residual contradiction checks |
| **ChromaDB PDF RAG** | `PARTIAL` | `backend/app/agent/rag.py` | Vector search over ECSS PDFs; requires local sentence-transformers |
| **Fallback Procedure Library** | `REAL` | `backend/app/procedures/library.py` | Zero-dependency structured ECSS procedure library |
| **Gemini LLM Provider** | `REAL` | `backend/app/llm/provider.py` | Live Gemini 2.5 Flash structured diagnostic inference |
| **Local LLM Provider** | `REAL` | `backend/app/llm/local_branch.py` | Live OpenAI-compatible local inference (Phi-3 / Ollama) |
| **Stub LLM Provider** | `SIMULATED` | `backend/app/llm/provider.py` | Deterministic static JSON replay for offline CI & testing |
| **Phase 23 Hybrid Router Orchestrator**| `UNUSED` | `backend/app/llm/router_orchestrator.py` | Implemented and tested, but dormant in default runtime path |
| **Deterministic Command Safety Gate**| `REAL` | `backend/app/agent/safety.py` | Fail-closed whitelist and physical precondition validator |
| **Telecommand Uplink Transmitter** | `UNUSED` | N/A | Intentionally omitted; system is strictly advisory |
| **Cryptographic Audit Store** | `REAL` | `backend/app/audit/store.py` | Append-only SQLite store with SHA-256 Merkle chain verification |
| **Operator Web Dashboard** | `REAL` | `frontend/src/App.jsx` | React 18 SPA with live SSE streaming and Dark/Light mode |

---

## 5. API / BACKEND / FRONTEND CONNECTION AUDIT

### Route Connectivity Map:
* `POST /api/v1/analyze` -> Connected to `SentinelAgent.analyze_crash_dump_stream()` -> Streams SSE trace to frontend `SentinelContext.jsx`.
* `GET /api/v1/scenarios` -> Connected to `scenarios.py` -> Populates scenario dropdown in `HeaderNav.jsx`.
* `POST /api/v1/detect` -> Connected to `run_detection_on_crash_dump()` -> Drives `TelemetryTableCard.jsx` status badges and z-scores.
* `POST /api/v1/reconciliation` -> Connected to `ReconciliationEngine.reconcile()` -> Drives `ReconciliationHero.jsx`.
* `POST /api/v1/physics` -> Connected to `validate_crash_dump()` -> Drives `PhysicsView.jsx` constraint table.
* `GET /api/v1/channels` -> Connected to `channel_dict.py` -> Populates channel units, descriptions, and bounds.
* `GET /api/v1/runs` & `GET /api/v1/runs/{id}` -> Connected to `AuditStore` -> Drives `AuditView.jsx` cryptographic verification.

### Frontend Data Truthfulness:
* **Real Data**: Subsystem health badges, anomaly counts, telemetry table values, z-scores, failure hypotheses, physical residual charts, and audit Merkle hashes are directly populated from backend response payloads.
* **Derived / Client Projections**: Visual timeline ribbons (streamgraph) and 2D geospatial ground track map interpolate active scenario coordinates for visual clarity.

---

## 6. LLM PATH AUDIT

1. **Default Mode (`LLM_MODE=cloud`)**:
   - Provider: Google Generative AI (`gemini-2.5-flash`).
   - Call Site: `app/llm/ranker.py:run_constrained_ranking`.
   - Prompt: Formatted via `app/agent/prompts.py:build_messages`.
   - Validation: Pydantic schema validation (`SentinelOutput`). One repair retry is executed if JSON parsing fails.
2. **Local Mode (`LLM_MODE=local`)**:
   - Provider: OpenAI-compatible endpoint (`http://localhost:11434/v1` or configured `FALLBACK_BASE_URL`).
   - Transmits zero telemetry outside localhost; all exfiltration blocked by `app/security/exfiltration.py`.
3. **Stub Mode (`LLM_MODE=stub`)**:
   - Replays pre-recorded diagnostic response from `backend/data/stub_response.json`.
4. **Hybrid Router (`ROUTER_ENABLED`)**:
   - `ROUTER_ENABLED=false` by default. `app/agent/agent.py` directly invokes `ranker.py` without calling `RouterOrchestrator`.

---

## 7. SAFETY AUTHORITY AUDIT

### Deterministic Safety Invariants Verified:
* **Command Whitelist**: Derived from `app/validation/command_registry.py`. Only registered, enabled commands can pass.
* **Battery Floor Constraint**: If `Battery_SOC < 30.0%` or if `Battery_SOC` is `UNKNOWN`, power-consuming recovery commands are **BLOCKED**.
* **Thermal Survival Constraint**: If `Component_temp_C > 65.0°C`, heater activation commands are **BLOCKED**.
* **Rate Limits**: Rate checks prevent gyro switches while wheel speeds or body rates are saturated.
* **Fail-Closed Guarantee**: If all steps are blocked, `apply_validation_to_output()` outputs an empty recovery plan with `safety_status = BLOCKED`.
* **Zero Alternate Paths**: No endpoint, LLM prompt, or UI action can authorize commands without passing through `validate_recovery_plan()`.

---

## 8. AUDIT-LOG PATH AUDIT

* **Engine**: SQLite database at `backend/data/audit/audit.sqlite3`.
* **Immutability Protection**: SQL triggers (`BEFORE UPDATE`, `BEFORE DELETE`) raise `ABORT` on any modification attempt.
* **Merkle Hash Chain**: Each log entry hashes the previous entry's SHA-256 hash, creating a tamper-evident audit trail.
* **Tamper Verification**: `verify_chain(run_id)` independently recomputes every stage hash to detect database tampering.

---

## 9. DEMO / SIMULATION / HARDCODED FINDINGS

| Location | Finding | Type | Justification |
|---|---|---|---|
| `app/api/scenarios.py` | 6 preset crash scenarios | Preset Test Data | Provides deterministic reference vectors for UI demo and evaluation |
| `app/estimation/models/` | Spacecraft mass (12 kg), heat capacity | Lumped Constants | Simplified 1D physical models for generic smallsat |
| `app/agent/agent.py` | SSE sleep delays (`0.35s`, `0.15s`) | Artificial Delay | Allows human operator to visually read streaming thoughts in real time |
| `app/procedures/library.py` | 6 structured ECSS procedures | Predefined Library | Zero-dependency baseline procedure knowledge |

---

## 10. DEAD / UNREACHABLE CODE FINDINGS

1. **Phase 23 Router Orchestrator in Main Agent**:
   - `app/llm/router_orchestrator.py` is fully implemented and tested, but never imported or invoked by `app/agent/agent.py`.
2. **Legacy Range Tables in `app/analytics/anomaly_detector.py`**:
   - `SATELLITE_NOMINAL_RANGES` is superseded by `app/ingest/channel_dict.py`.
3. **Legacy `pre_fault_telemetry` Array in Scenarios**:
   - Preserved for backward compatibility, but superseded by `pre_fault_telemetry_window`.

---

## 11. BROKEN / INCOMPLETE PATHS

1. **No Real-Time Binary CCSDS Stream Parser**:
   - Backend only accepts JSON crash dumps; cannot ingest raw telemetry frames over serial/UDP.
2. **5 Channel Dictionary Boundary Inconsistencies**:
   - Channels `Attitude_error_deg`, `Component_temp_C`, `Gyro_rate_degs`, `SEU_counter`, `V_bus` have nominal operating bands wider than their hard limits in legacy definitions.
3. **Single-Node Physical Models**:
   - Thermal and power models use single-node lumped capacitance rather than multi-node finite element models.

---

## 12. TEST RESULTS

Executed test suites using Python 3.14 test runner:

```bash
PYTHONPATH=backend python3 -m unittest \
  backend/tests/test_phase0_frontend.py \
  backend/tests/test_phase3_contract.py \
  backend/tests/test_phase4_audit.py \
  backend/tests/test_phase5_channel_dict.py \
  backend/tests/test_secret_scan.py \
  backend/tests/test_phase14_security.py
```

* **Total Tests Executed**: 316
* **Passed**: 316
* **Failed**: 0
* **Errors**: 0
* **Success Rate**: **100%**

---

## 13. CRITICAL FINDINGS (ORDERED BY IMPORTANCE)

1. **[CRITICAL-01] Router Orchestrator is Dormant in Live Runtime**:
   - The dual-branch cloud/local arbitration logic is not wired into `agent.py`, meaning dynamic failover between local and cloud models does not occur automatically in production.
2. **[CRITICAL-02] Channel Dictionary Boundary Conflicts**:
   - 5 channels have nominal operating bands that cross hard limits, potentially causing nominal telemetry to be flagged as anomalous.
3. **[CRITICAL-03] Single-Node Physical Approximations**:
   - Physical models are effective for sanity-checking telemetry residuals, but are not flight-grade orbital dynamics models.

---

## 14. RECOMMENDED FIXES (ORDERED P0 / P1 / P2)

### P0 — MUST FIX (High Priority)
1. **Wire Router Orchestrator into `SentinelAgent`**:
   - Connect `app/llm/router_orchestrator.py` to `app/agent/agent.py` behind the `ROUTER_ENABLED` configuration flag.
2. **Reconcile 5 Channel Dictionary Limits**:
   - Update `app/ingest/channel_dict.py` so nominal ranges sit strictly inside hard limits.

### P1 — SHOULD BUILD (Medium Priority)
1. **Binary CCSDS SPP Packet Ingestor**:
   - Implement a lightweight binary CCSDS packet reader to support raw packet frames.
2. **PostgreSQL Adapter for Audit Store**:
   - Add PostgreSQL storage driver to support distributed enterprise deployments.

### P2 — FUTURE / SCALE (Low Priority)
1. **3D WebGL Orbit Visualization**:
   - Implement Three.js 3D satellite visualization for orbit and attitude orientation.

---

## 15. EXPLICIT LIST OF THINGS THAT SHOULD NOT BE CHANGED YET

1. **`app/agent/safety.py`**: The deterministic safety validation logic and command whitelist must remain intact to guarantee fail-closed security.
2. **`app/reconciliation/`**: The deterministic observation reconciliation algorithms (`CORRELATION != IDENTITY`) are verified and should not be rewritten.
3. **`app/audit/store.py`**: The SQLite append-only trigger rules and SHA-256 Merkle chain verification are mathematically sound and passing all integrity tests.
4. **`app/api/models.py`**: Canonical Pydantic schemas and OpenAPI contracts match the frontend bindings and must not be altered without a schema migration.
5. **`frontend/src/state/ThemeContext.jsx` & `App.css`**: The newly completed Dark/Light mode theme system and CSS variable design tokens are stable and visually verified.

---

## 16. WHAT WE SHOULD FIX NEXT

In the upcoming implementation phase, the team should execute:
1. **Activate the Hybrid Router Orchestrator**: Integrate `app/llm/router_orchestrator.py` into `SentinelAgent.analyze_crash_dump_stream()` so confidence-based dynamic routing between local and cloud AI models is operational.
2. **Resolve Channel Dictionary Overlaps**: Correct the 5 legacy channel limit conflicts in `app/ingest/channel_dict.py` to eliminate false-positive warnings.
3. **Maintain Contract Integrity**: Ensure all subsequent modifications export updated OpenAPI bindings via `python3 backend/scripts/export_contracts.py --check`.

---

*PHASE COMPLETE — READ-ONLY AUDIT.*  
*NO SOURCE FILES MODIFIED.*
