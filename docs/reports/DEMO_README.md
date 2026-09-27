# SENTINEL — Spacecraft Autonomous FDIR Live Demo

Welcome to the **Sentinel Live Hackathon Demonstration**.

Sentinel is a physics-grounded, safety-interlocked autonomous fault detection, isolation, and recovery (FDIR) copilot for spacecraft operations.

```
================================================================================
  AI assists diagnosis.
  Physics validates reality.
  Safety controls action.
  Human remains the final authority.
================================================================================
```

---

## 1. Quick Start (Single Command)

To run the complete 17-stage live demonstration:

```bash
# 1. Navigate to the backend directory
cd sentinel/backend

# 2. Run the single-command demo
python -m demo.run
# or if using the local virtual environment:
.venv/bin/python -m demo.run
```

---

## 2. Environment & Prerequisites

* **Python Version**: Python 3.10+ (tested on Python 3.11 & 3.13)
* **Virtual Environment**:
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  ```
* **Optional Cloud AI (Gemini)**:
  * Set `GEMINI_API_KEY=AIzaSy...` in `sentinel/backend/.env` or export in shell.
  * Model used: `gemini-2.5-flash` via `google-genai` SDK.
* **Optional Local Model (Phi-3 Mini)**:
  * Install and start [Ollama](https://ollama.ai): `ollama run phi3:mini`
  * Default endpoint: `http://localhost:11434/v1`
* **Zero-Configuration Offline Mode**:
  * If neither `GEMINI_API_KEY` nor Ollama is present, Sentinel automatically and truthfully uses its **deterministic grounded adapter** with identical schema validation, physics enforcement, and safety interlocks.

---

## 3. The 17-Stage Diagnostic Pipeline

When you execute `python -m demo.run`, Sentinel steps through the following sequence:

| Step | Stage Name | Implementation Status | Core Responsibility |
| :--- | :--- | :--- | :--- |
| **01** | **Telemetry Ingestion** | Synthetic Input | Loads crash dump window with explicit truthfulness disclosure |
| **02** | **Canonicalization** | Real (`app.api.adapters`) | Normalizes raw telemetry into timestamped channel vectors |
| **03** | **Telemetry Reduction** | Real (`app.ingest.reduction`) | Funnels high-frequency data into 6 key physics state features |
| **04** | **Anomaly Detection** | Real (`app.detection`) | Multi-detector fusion (Z-Score, Limit checks, CUSUM) |
| **05** | **State Estimation** | Real (`app.estimation`) | Calculates power deficit ($P_{\text{gen}}=0\text{W}, P_{\text{load}}=345\text{W}, \text{SoC}=14.2\%$) |
| **06** | **Hypothesis Generation** | Real (`app.diagnosis`) | Deterministic symptom signature matching (H1, H2, H3) |
| **07** | **Physics Validation** | Real (`app.validation.physics`)| Binding physical conservation laws ($P = \eta A S_0 \cos\theta$) |
| **08** | **Procedure Retrieval (RAG)**| Real (`app.agent.rag`) | Vector & keyword retrieval from ECSS operations library |
| **09** | **Evidence Grounding** | Real (`app.agent.prompts`) | Constructs verifiable cryptographic evidence IDs (`EVID-...`) |
| **10** | **LLM Reasoning** | Live Gemini / Phi-3 / Adapter | Ranks candidate set and explains causal mechanisms |
| **11** | **Structured Parsing** | Real (`app.api.models`) | Pydantic `SentinelOutput` schema enforcement |
| **12** | **Guardrails Validation** | Real (`app.llm.ranker`) | Evidence grounding & procedure validity checks |
| **13** | **Hybrid Arbitration** | Real (`app.llm.arbitrator`) | Phase 23 Local vs. Cloud deterministic arbitration (Dry-Run) |
| **14** | **Merge Resolution** | Real (`app.llm.merge_resolver`)| Refutation dominance & monotone human review merge |
| **15** | **Physics Reassertion** | Real (`app.validation.physics`)| Reasserts physics authority over merged outputs |
| **16** | **Safety Interlock** | Real (`app.agent.safety`) | Evaluates commands; **blocks `CMD_SAFE_MODE_EXIT` on `BATTERY_FLOOR`** |
| **17** | **Final Sentinel Output** | Real | Delivers structured recommendation requiring human authorization |

---

## 4. Expected Demonstration Output

```text
================================================================================
  SENTINEL — SPACECRAFT ANOMALY DIAGNOSTIC PIPELINE (LIVE DEMO)
================================================================================
  Deterministic Physics · Evidence-Grounded RAG · Safety Interlocks · Hybrid Router

[01/17] SYNTHETIC TELEMETRY INGESTION
      Input Provenance:            SYNTHETIC DEMONSTRATION DATA (ECSS-E-ST-70-11C)
      Incident Identifier:         INC-2026-0002
      Fault Type Declaration:      EPS_SOLAR_UNDERVOLT

[02/17] CANONICALIZATION
      Canonical State Vector:      18 validated entries mapped to ECSS channel dictionary
      Unique Active Channels:      7 parameters monitored

[03/17] TELEMETRY REDUCTION & FEATURE EXTRACTION
      Funneling High-Rate Telemetry to Diagnostic Features:
      ┌─────────────────────────────────────────────────────────────┐
      │  [1] Mission-Scale Concept:   32,000 spacecraft channels   │
      │  [2] Subsystem Filtered:      12 local EPS/OBC readings    │
      │  [3] Extracted Key Signals:   4 anomalous channels         │
      │  [4] Diagnostic Features:     6 physics state variables    │
      │  [5] Evidence Grounding:      4 verifiable Evidence IDs    │
      └─────────────────────────────────────────────────────────────┘
      ✓  Sentinel DOES NOT send raw, uncompressed telemetry streams to the LLM.

[07/17] PHYSICS VALIDATION (BINDING AUTHORITY)
      Deterministic Conservation Laws vs. Observed Telemetry:
      • EPS_SOLAR_UNDERVOLT      → ✓ VALIDATED
        Observed I_sa:             0.0 A @ T-180s (Sun sensor angle: 42.0°, Eclipse: 0.0)
        Predicted I_sa:            8.4 A (Solar constant model: P = η·A·S₀·cos θ)
        Residual / Tol:            -8.4 A (Tolerance: 0.5 A) → CORROBORATED
      • MULTI_CASCADE            → ？ UNCERTAIN
      • EPS_BATTERY_DEGRADATION  → ✓ VALIDATED (ESR refuted)
      ✓  ARCHITECTURAL INVARIANT: LLM DOES NOT DECIDE PHYSICS VERDICTS.

[16/17] SAFETY VALIDATION & COMMAND BLOCKING (CRITICAL BOUNDARY)
      Deterministic Safety Evaluation of Proposed Recovery Sequence:
      ✓  Step 1: CMD_VERIFY_SUN_ANGLE [Risk: LOW] — APPROVED
      ✓  Step 2: CMD_SOLAR_ARRAY_A_RESET [Risk: LOW] — APPROVED
      ✓  Step 3: CMD_SWITCH_SOLAR_ARRAY [Risk: MEDIUM] — APPROVED
      ✓  Step 4: CMD_POWER_SHED_NONESSENTIAL [Risk: LOW] — APPROVED

      ==================== CRITICAL SAFETY INTERLOCK TRIPPED ====================
      ✗  BLOCKED COMMAND: CMD_SAFE_MODE_EXIT (Step 5)
      Violated Constraint:         BATTERY_FLOOR
      Constraint Policy:           Battery State of Charge must be >= 15.0% before exiting safe mode.
      Observed Spacecraft SoC:     14.2% (< 15.0% safety floor)
      Refusal Rationale:           Battery SoC is 14.2% (below the 15% floor). Command 'CMD_SAFE_MODE_EXIT' declares this constraint and is therefore blocked.
      ===========================================================================

      Overall Safety Status:       PARTIALLY_BLOCKED
      Human Review Mandated:       TRUE (Autonomous command execution prohibited)
      ✓  KEY PRINCIPLE: Even if the AI proposes an unsafe action, the AI CANNOT execute it.
```

---

## 5. Failure Modes & Architectural Defenses

| Failure Condition | Sentinel Architectural Defense |
| :--- | :--- |
| **Gemini API Unavailable** | Gracefully falls back to local Phi-3 or deterministic grounded adapter; transparently labels mode. |
| **Local Ollama Unavailable** | Transparently switches to deterministic grounded adapter with zero crash. |
| **Sparse / Insufficient Telemetry** | `assess_window_adequacy()` flags `UNDER_SAMPLED_FOR_PHYSICS`; physics yields `UNCERTAIN`. |
| **RAG / Vector DB Offline** | Falls back to in-memory static ECSS procedure catalogue. |
| **Physics Model Contradicts LLM** | `reconcile_llm_claim()` strictly enforces physics verdict; model cannot override reality. |
| **LLM Recommends Unsafe Action** | Deterministic Safety Validator evaluates command constraints against telemetry and immediately blocks the action. |

---

## 6. System Truthfulness & Ethics Disclosure

* **Synthetic Data**: Telemetry used in the demonstration is synthetic time-series modelled on European Space Agency (ECSS) standard failure modes. Real on-orbit telemetry is restricted.
* **Advisory Role Only**: Sentinel generates structured recovery recommendations with safety verdicts; it has **no direct telecommand uplink** to physical spacecraft hardware. Execution strictly requires ground-controller authorization.
* **Production Routing Status**: The production router flag remains `ROUTER_ENABLED=false`. Hybrid routing is demonstrated in isolated simulation mode to prove architectural correctness without bypassing flight controls.
