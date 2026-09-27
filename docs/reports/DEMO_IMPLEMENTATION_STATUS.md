# Sentinel Demo Implementation Status & System Truthfulness Audit

This document provides a factual, component-by-component audit of the Sentinel Spacecraft Autonomous FDIR diagnostic pipeline. It distinguishes genuinely executed production code from synthetic demonstration inputs, deterministic adapters, and dormant or unavailable subsystems.

---

## 1. REAL (Implemented & Executed Directly)

The following components are fully implemented in Python and executed directly as part of the live diagnostic pipeline:

### A. Ingestion, Canonicalization & Telemetry Reduction
* **Channel Dictionary (`app.ingest.channel_dict`)**: Authoritative mapping of ECSS telemetry identifiers, physical units, sensor subsystems, nominal operational bands, and hard limits.
* **Canonical Telemetry Adapter (`app.api.adapters`)**: Normalizes raw, asynchronous telemetry samples into timestamped, structured state vectors.
* **Telemetry Reducer & Feature Extractor (`app.ingest.reduction`)**: Extracts statistical derivatives, voltage droop rates, thermal gradients, and anomaly flags. Drastically reduces high-frequency raw channel data into a concise diagnostic context window before any LLM ingestion.

### B. Anomaly Detection & State Estimation
* **Multi-Detector Fusion (`app.detection`)**: Statistical Z-score estimators, hard limit boundary checkers, and CUSUM rate-of-change detectors (`detector.py`, `fusion.py`, `limits.py`, `statistical.py`).
* **Spacecraft State Estimation (`app.estimation`)**: Deterministic power balance ($P_{\text{gen}} - P_{\text{load}} = \Delta P_{\text{bat}}$), thermal gradients, and ADCS momentum estimators (`estimator.py`, `state.py`, `residuals.py`).
* **Window Adequacy Validator (`app.estimation.window_adequacy`)**: Enforces minimum sampling intervals required for valid physics derivatives.

### C. Deterministic Hypothesis Generation & Physics Validation
* **Candidate Generator (`app.diagnosis.candidates`, `app.diagnosis.fault_dictionary`)**: Produces a strictly bounded hypothesis set based on observed symptom vectors.
* **Deterministic Physics Validator (`app.validation.physics`)**: Computes observed vs. predicted physical telemetry residuals against conservation laws (e.g. Solar Array illumination: $P = \eta A S_0 \cos\theta$).
* **Constraint Catalogue & Verdict Authority**: Emits binding `VALIDATED`, `INVALIDATED` (refuted), or `UNCERTAIN` verdicts. **The LLM has zero authority to override or invent physics verdicts.**

### D. RAG & Evidence Grounding
* **ECSS Procedure Retrieval (`app.agent.rag`, `app.procedures.library`, `app.procedures.retrieval`)**: Vector-based (ChromaDB) and indexed search across formal ECSS procedures (e.g., `PROC-EPS-UNDERVOLT-001`).
* **Evidence Grounding Contract (`app.agent.prompts`, `app.llm.ranker`)**: Generates verifiable Evidence IDs (`EVID-...`) linked to sensor channels, timestamps, and procedure clauses.

### E. Deterministic Safety Validation & Command Blocking
* **Deterministic Safety Validator (`app.agent.safety`)**: Checks proposed recovery commands against spacecraft operating state and safety limits.
* **Command Registry & Interlock Engine (`app.validation.command_registry`, `app.validation.conflicts`)**: Enforces critical flight constraints (e.g., `BATTERY_FLOOR`, `INTERLOCK_EPS_COMM`, `SAFE_MODE_EXIT_PREREQUISITE`).
* **Command Blocking**: Immediately blocks unsafe actions (e.g., `CMD_SAFE_MODE_EXIT` when Battery $\text{SoC} < 15\%$) regardless of model confidence.

### F. Structured Output Schema Enforcement
* **Pydantic Schema Validation (`SentinelOutput` in `app.api.models`)**: Enforces strict JSON shape, exactly 3 ranked hypotheses with causal chains, sequenced recovery actions, confidence bounds, and review flags.

### G. Cryptographic Audit Ledger
* **Append-Only Audit Ledger (`app.audit`)**: SHA-256 hash chaining of all 11 diagnostic stages (`ingest`, `detection`, `state_estimation`, `hypotheses`, `physics`, `rag`, `llm`, `safety`, `diagnosis`, `routing`, `operator_decision`).

### H. Hybrid Local/Cloud Router (Phase 23 Tested Architecture)
* **Deterministic Arbitrator (`app.llm.arbitrator`)**: Implements safety-monotone, physics-constrained decision arbitration between Local (Phi-3) and Cloud (Gemini) branches.
* **Deterministic MergeResolver (`app.llm.merge_resolver`)**: Reconciles dual-branch hypotheses while preserving refutation dominance and human review monotonicity.
* **RouterOrchestrator (`app.llm.router_orchestrator`)**: State machine executing sequential local-first evaluation with fail-closed cloud escalation and cloud redaction gate.

---

## 2. SYNTHETIC INPUT (Simulated Mission Data)

* **Spacecraft Crash Dump Scenarios**:
  * Scenarios 1, 2, 3, 5, and 6 (`EPS_SOLAR_UNDERVOLT`, `ADCS_GYRO_SEU`, `TCS_HEATER_FAILURE`, `COMM_TRANSPONDER_LOCK`, `PROP_VALVE_LEAK`) contain synthetic time-series telemetry modelled on ECSS fault signatures.
  * Real telemetry from operational spacecraft is classified/restricted.
  * Scenario 4 contains real archived numeric telemetry from the ESA-ADB benchmark (`data_tools/esa_adb_crash_dump.py`).
  * In the demo, all input data is explicitly watermarked: `SYNTHETIC DEMONSTRATION DATA — NOT REAL SPACECRAFT TELEMETRY`.

---

## 3. DEMO ADAPTER (Tested Architecture Demonstrated via Adapters)

* **Deterministic Grounded Reasoning Adapter**:
  * Used when live Gemini API keys or local Ollama GPU environments are unavailable.
  * Emits fully structured, schema-compliant `SentinelOutput` strictly grounded in the retrieved ECSS procedure and physics verdicts.
* **Hybrid Router Simulation Mode**:
  * The production router default remains `ROUTER_ENABLED=false`.
  * The demo invokes `RouterOrchestrator.run()` in an explicit, isolated simulation mode to demonstrate multi-model arbitration without changing production flags.
* **Synthetic Telemetry Ingestion Fixture (`demo/data/synthetic_scenario.json`)**:
  * Standardized single-scenario fixture based on Scenario 2 (`EPS_SOLAR_UNDERVOLT`) for consistent, offline demonstration.

---

## 4. NOT AVAILABLE (Honest Architectural Boundaries)

* **Direct Spacecraft Telecommand Uplink**:
  * Sentinel is an advisory copilot; it does **not** dispatch commands directly to spacecraft hardware.
  * All recovery actions require explicit operator review and ground-station approval.
* **Production Auto-Routing Activation**:
  * Production routing remains disabled (`ROUTER_ENABLED=false`) until complete flight qualification.
* **Live Cloud Inference Without Network/Key**:
  * If `GEMINI_API_KEY` is not present, Sentinel will not attempt live cloud calls and cleanly reports offline adapter status.
* **Live Local LLM Without Ollama**:
  * If the local Ollama server is not running, Sentinel will not pretend a local neural network was executed; it transparently switches to the deterministic grounded adapter.
