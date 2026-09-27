# Sentinel Live Hackathon Demonstration Report

**System Name**: SENTINEL — Spacecraft Autonomous Fault Detection, Isolation & Recovery (FDIR)  
**Evaluation Mode**: 17-Stage Complete Diagnostic Pipeline (Live Demo)  
**Status**: COMPLETE & VERIFIED  

---

## 1. Demo Architecture

Sentinel is engineered to solve a fundamental problem in aerospace AI: **Large Language Models cannot be trusted as authorities over physical reality or safety-critical actuation.**

Instead of allowing an unconstrained model to ingest raw telemetry and emit uncontrolled commands, Sentinel encloses the AI inside a deterministic, physics-bound, safety-interlocked state machine:

```
[SYNTHETIC TELEMETRY]
        ↓
[CANONICALIZATION & REDUCTION] ──> Reduces 32k channels to 6 state features
        ↓
[ANOMALY DETECTION (MULTI-FUSION)] ──> Statistical Z-score + Limit checks + CUSUM
        ↓
[STATE ESTIMATION & RESIDUALS] ──> Power / Thermal / Momentum state estimators
        ↓
[CANDIDATE HYPOTHESES] ──> Deterministic symptom signature matching (H1, H2, H3)
        ↓
[PHYSICS VALIDATION (BINDING)] ──> Conservation laws (P = η·A·S₀·cos θ)
        ↓
[RAG PROCEDURE RETRIEVAL] ──> ECSS flight operations procedure library (ChromaDB)
        ↓
[EVIDENCE GROUNDING CONTRACT] ──> Verifiable Evidence IDs (EVID-...)
        ↓
[LLM REASONING & RANKING] ──> Gemini 2.5 Flash / Phi-3 Mini / Grounded Adapter
        ↓
[GUARDRAILS & SCHEMA ENFORCEMENT] ──> Pydantic SentinelOutput schema validation
        ↓
[HYBRID ROUTER & ARBITRATION] ──> Phase 23 Local vs. Cloud deterministic arbitration
        ↓
[MERGE RESOLVER & PHYSICS RECHECK] ──> Refutation dominance & monotone review
        ↓
[DETERMINISTIC SAFETY VALIDATOR] ──> Evaluates interlocks; BLOCKS unsafe commands
        ↓
[AUDIT LEDGER & HUMAN REVIEW] ──> SHA-256 hash chaining + flight controller approval
```

---

## 2. Exact Execution Path (17 Stages)

Execution is initiated via a single terminal command:
```bash
python -m demo.run
```

| Step | Stage Name | Implementation Module | Output Summary |
| :--- | :--- | :--- | :--- |
| **01** | Telemetry Ingest | `demo/data/synthetic_scenario.json` | Ingests 12 timestamped pre-fault readings with explicit synthetic disclosure |
| **02** | Canonicalization | `app.api.adapters` | Maps readings to canonical channels (`I_sa`, `V_bat`, `SoC_pct`, `V_bus`, etc.) |
| **03** | Telemetry Reduction | `app.ingest.reduction` | Demonstrates 32k $\rightarrow$ 12 local $\rightarrow$ 4 anomalous $\rightarrow$ 6 diagnostic features |
| **04** | Anomaly Detection | `app.detection` | Identifies 20 limit/rate violations; flags critical array drop & voltage sag |
| **05** | State Estimation | `app.estimation` | Computes $P_{\text{gen}}=0.0\text{W}, P_{\text{load}}=345.0\text{W}, \Delta P=-345.0\text{W}, \text{SoC}=14.2\%$ |
| **06** | Hypothesis Generation| `app.diagnosis.candidates` | Emits `EPS_SOLAR_UNDERVOLT`, `MULTI_CASCADE`, `EPS_BATTERY_DEGRADATION` |
| **07** | Physics Validation | `app.validation.physics` | `EPS_SOLAR_UNDERVOLT` $\rightarrow$ **VALIDATED**; `EPS_BATTERY_DEGRADATION` $\rightarrow$ **REFUTED** |
| **08** | RAG Procedure Retrieval| `app.agent.rag` | Retrieves `PROC-EPS-UNDERVOLT-001` (ECSS-E-ST-70-11C §4.3.2) via ChromaDB |
| **09** | Evidence Grounding | `app.agent.prompts` | Formulates `EVID-EPS-001`, `EVID-EPS-002`, `EVID-PHYS-001`, `EVID-PROC-001` |
| **10** | LLM Reasoning | `app.llm.provider` | Gemini 2.5 Flash / Grounded Adapter generates causal reasoning |
| **11** | Output Parsing | `app.api.models` | Validates JSON against `SentinelOutput` (3 hypotheses, 5 recovery steps) |
| **12** | Guardrails Validation| `app.llm.ranker` | Evidence grounding PASS, procedure citation PASS, certainty bounds PASS |
| **13** | Hybrid Arbitration | `app.llm.arbitrator` | Dry-run arbitration: Local + Cloud concurrence $\rightarrow$ `LOCAL_ACCEPT` |
| **14** | Merge Resolution | `app.llm.merge_resolver` | Monotone human review preserved; refutation dominance strictly enforced |
| **15** | Physics Reassertion | `app.validation.physics` | Confirms primary hypothesis is physically validated and not refuted |
| **16** | Safety Interlocks | `app.agent.safety` | Approves Steps 1–4; **BLOCKS Step 5 (`CMD_SAFE_MODE_EXIT`) on `BATTERY_FLOOR`** |
| **17** | Final Output | `demo/run.py` | Emits flight recommendation mandating human operator review |

---

## 3. Real vs. Synthetic Components (Truthfulness Audit)

* **REAL Code Execution**:
  * Telemetry Ingest, Canonicalization, and Channel Attribution
  * Multi-Detector Anomaly Fusion (Z-Score, Hard Limits, CUSUM)
  * State Estimation & Residual Calculation
  * Deterministic Hypothesis Generation & Fault Dictionary
  * Deterministic Physics Validator & Constraint Catalogue
  * RAG Retrieval (ChromaDB + ECSS procedure library)
  * Pydantic Schema Validation & Guardrails
  * Deterministic Command Registry & Safety Interlocks
  * SHA-256 Hash Chained Audit Ledger
  * Phase 23 Hybrid Local/Cloud Arbitrator, MergeResolver, and RouterOrchestrator
* **SYNTHETIC INPUT**:
  * Telemetry data is synthetic time-series modelled on ECSS fault dynamics (`scenario_id = 2`). Real on-orbit telemetry is restricted.
* **DEMO ADAPTER**:
  * Deterministic Grounded Reasoning Adapter utilized when cloud API keys or local GPU models are offline.
  * Hybrid Router executed in simulation/dry-run mode (`ROUTER_ENABLED=false` remains intact).

---

## 4. Telemetry Reduction Numbers

* **Mission-Scale Conceptual Channels**: 32,000 channels
* **Subsystem Ingested Samples**: 12 timestamped channel samples in pre-fault window
* **Monitored Active Parameters**: 7 channels (`I_sa`, `V_bat`, `V_bus`, `SoC_pct`, `Heater_power_W`, `Attitude_error_deg`, `OBC_temp_C`)
* **Extracted Anomalous Signals**: 4 critical channels
* **Derived Physics State Features**: 6 state variables
* **Grounding Evidence Bundle**: 4 verifiable Evidence IDs

---

## 5. Physics Validation Example

```text
Deterministic Conservation Laws vs. Observed Telemetry:
• EPS_SOLAR_UNDERVOLT      → ✓ VALIDATED
  Observed I_sa:             0.0 A @ T-180s (Sun sensor angle: 42.0°, Eclipse: 0.0)
  Predicted I_sa:            8.4 A (Solar constant model: P = η·A·S₀·cos θ)
  Residual / Tol:            -8.4 A (Tolerance: 0.5 A) → CORROBORATED
• MULTI_CASCADE            → ？ UNCERTAIN
• EPS_BATTERY_DEGRADATION  → ✓ VALIDATED (ESR degradation refuted by nominal discharge profile)
```

**Key Takeaway**: The physics validator is deterministic and binding. The LLM has zero authority to create, alter, or ignore physics verdicts.

---

## 6. Safety Interlock & Command Blocking Demonstration

During Step 16, the AI proposed a 5-step recovery plan:
1. `CMD_VERIFY_SUN_ANGLE` (Verify sun sensor angle < 90°) $\rightarrow$ **APPROVED**
2. `CMD_SOLAR_ARRAY_A_RESET` (Power cycle array drive assembly A) $\rightarrow$ **APPROVED**
3. `CMD_SWITCH_SOLAR_ARRAY` (Switch to alternate array wing B) $\rightarrow$ **APPROVED**
4. `CMD_POWER_SHED_NONESSENTIAL` (Shed payload loads) $\rightarrow$ **APPROVED**
5. `CMD_SAFE_MODE_EXIT` (Exit safe mode) $\rightarrow$ **BLOCKED**

```text
==================== CRITICAL SAFETY INTERLOCK TRIPPED ====================
✗  BLOCKED COMMAND: CMD_SAFE_MODE_EXIT (Step 5)
Violated Constraint:         BATTERY_FLOOR
Constraint Policy:           Battery State of Charge must be >= 15.0% before exiting safe mode.
Observed Spacecraft SoC:     14.2% (< 15.0% safety floor)
Refusal Rationale:           Battery SoC is 14.2% (below the 15% floor). Command 'CMD_SAFE_MODE_EXIT' declares this constraint and is therefore blocked.
===========================================================================

Overall Safety Status:       PARTIALLY_BLOCKED
Human Review Mandated:       TRUE (Autonomous command execution prohibited)
```

**Principle for Judges**: Even if an AI hallucinates or recommends an unsafe action, the deterministic safety engine makes it impossible to execute.

---

## 7. Hybrid Routing Demonstration (Phase 23)

* **Production Setting**: `ROUTER_ENABLED=false` (Production router remains safely disabled)
* **Simulation Mode**: Sequential local-first evaluation with fail-closed cloud escalation and cloud redaction gate.
* **Arbitrator Evaluation**: Local Branch (Phi-3) and Cloud Branch (Gemini) both identify `EPS_SOLAR_UNDERVOLT` as Rank 1 $\rightarrow$ `LOCAL_ACCEPT / CLOUD_CONCURRENCE`.
* **Merge Resolution**: Strictly enforces refutation dominance and monotone human review.

---

## 8. Commands to Reproduce

```bash
# 1. Run the live demo
cd sentinel/backend
.venv/bin/python -m demo.run

# 2. Run the complete backend test suite
.venv/bin/pytest tests/ -q
```
