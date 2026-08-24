# LOCAL LLM BASELINE REPORT

**Phase:** 19 — Local LLM Baseline Audit  
**System Under Test:** SENTINEL Spacecraft Autonomous FDIR System  
**Audit Date:** August 19, 2026  
**Auditor:** Antigravity (Principal Staff Systems Architect & Aerospace Auditor)  
**Execution Mode:** Controlled Local Model Verification  
**Configured Local Model:** `Microsoft Phi-3 Mini` (`phi-3-mini` / `phi3:mini`, 3.8B parameters) via `LocalProvider`  

---

## SECTION A: Local Model Configuration

| Parameter | Configured Value | Source / Verification Method | Status |
|---|---|---|:---:|
| **Provider Class** | `LocalProvider` | `sentinel/backend/app/llm/provider.py:249` | IMPLEMENTED |
| **Model Name** | `phi-3-mini` (`fallback_model`) | `ProviderConfig.fallback_model` (`app/llm/provider.py:49`) | CONFIGURED |
| **Ollama Target Tag** | `phi3:mini` (3.8B-Instruct, 4k context) | Canonical Ollama Registry identifier | MISSING WEIGHTS |
| **Endpoint URL** | `http://localhost:11434/v1` | `ProviderConfig.fallback_base_url` (`app/llm/provider.py:50`) | ACTIVE (Ollama 0.32.13) |
| **API Key** | `local` | `ProviderConfig.fallback_api_key` (`app/llm/provider.py:51`) | VERIFIED |
| **Temperature** | `0.1` | Low for deterministic JSON generation | CONFIGURED |
| **Max Output Tokens** | `4096` | Accommodates 3 candidate hypotheses + recovery | CONFIGURED |
| **Timeout Seconds** | `90.0 s` | Aerospace hard ceiling | CONFIGURED |
| **Structured Output Mode** | OpenAI-compatible JSON schema / `response_format` | Supported in `LocalProvider` via `urllib` / `openai` | IMPLEMENTED |

---

## SECTION B: Hardware & Runtime Environment

| Hardware Parameter | Measured Host Value | Assessment / Compatibility |
|---|---|---|
| **Host System** | Apple MacBook Air (M1, Model `MacBookAir10,1`) | macOS Darwin 24.x |
| **CPU Architecture** | Apple M1 (8 cores: 4 Performance @ 3.2 GHz, 4 Efficiency @ 2.0 GHz) | ARM64 NEON supported |
| **Memory (RAM)** | 8.00 GB Unified Memory (`hw.memsize = 8589934592`) | Shared system and graphics memory |
| **GPU / Acceleration** | Apple M1 Integrated GPU (7 Cores, Metal 4 support) | Full Metal GPU offloading supported by Ollama |
| **Inference Server** | Ollama daemon v0.32.13 running at `http://localhost:11434` | Running, 0 active models loaded |
| **Expected Model Size** | ~2.2 GB (Q4_K_M quantization for `phi3:mini`) | Fits comfortably in 8 GB unified memory (~35% RAM headroom) |
| **Estimated VRAM Footprint** | ~2.6 GB VRAM with 4k context KV-cache | 100% GPU layer offload feasible via Metal |

---

## SECTION C: Availability Audit & Verification

Per strict Phase 19 directive:
> *If the model is unavailable: STOP and report exactly what is missing. Do not automatically download or replace the model without documenting it.*

### Audit Findings:
1. **Ollama Daemon Status:** Responding HTTP 200 at `http://localhost:11434/api/tags`.
2. **Local Model Manifests:** Inspected `~/.ollama/models/manifests` and executed `ollama list`.
3. **Result:** **`{"models": []}`** (Zero local models installed).
4. **Missing Dependency:** `phi3:mini` (Microsoft Phi-3 Mini 3.8B Instruct Q4_K_M weights, ~2.2 GB) is not present on the host disk.

Live local inference cannot proceed until the `phi3:mini` model weights are pulled to the local Ollama repository.

---

## SECTION D: Local vs Gemini Architecture Comparison

| Dimension | Cloud Baseline (`Gemini 2.5 Flash`) | Local Target (`Phi-3 Mini 3.8B`) |
|---|---|---|
| **Architecture** | Massive Mixture-of-Experts (Cloud Hosted) | Dense 3.8B Small Language Model (Host Local) |
| **Sovereignty / Privacy** | Requires egress to Google Cloud API | **100% Sovereign / Zero Network Egress** |
| **Measured Top-1 Accuracy** | 100.0% (6/6 scenarios in Phase 18) | *Pending live benchmark after weights pull* |
| **Evidence Grounding** | 100.0% (39/39 deterministic evidence IDs) | *Pending live benchmark after weights pull* |
| **Structured Output Compliance** | 100.0% (Valid JSON adherence) | Supported via constrained prompting & JSON schema |
| **Deterministic Safeguards** | Full Command Registry & Safety Validator | Identical full deterministic safety pipeline |
| **Mean Inference Latency** | 5.106 s | *Estimated 2.0 – 4.5 s with M1 Metal GPU offload* |

---

## SECTION E: Deterministic Safety & Guardrail Guarantee

Regardless of whether inference is performed by Gemini 2.5 Flash, Phi-3 Mini, or in fallback mode:
- All LLM outputs must pass through `validate_ranking_output()` (guardrails).
- All hypotheses are reconciled against deterministic physics reports.
- All recovery commands are strictly validated against `command_registry.py` and pre-condition states (`Condition.BATTERY_BELOW_FLOOR`, `Condition.THERMAL_ABOVE_SURVIVAL`, etc.).
- **Unsafe recovery actions cannot reach spacecraft actuators.**

---

## SECTION F: Candidate Routing Signals (For Future Escalation)

The following deterministic routing signals are identified for future local-to-cloud escalation:

1. **Structured Output Parse Failure:** Local model fails to return valid JSON conforming to `LLMRankingOutput`.
2. **Disagreement with Deterministic Physics:** Local model ranks a hypothesis that deterministic physics has marked `INVALIDATED`.
3. **Low / Uncalibrated Confidence:** Local model confidence drops below baseline diagnostic certainty.
4. **Unsupported Evidence Citations:** Local model cites hallucinated evidence IDs or fails to cite available telemetry evidence.
5. **Procedure Whitelist Exceedance:** Local model selects procedure IDs outside the retrieved RAG subset.
6. **Local Inference Timeout:** Local host generation exceeds allowable real-time FDIR latency window.

---

## SECTION G: Next Action Required

To execute the live Phase 19 inference benchmark across scenarios S1, S2, S3, S5, S6, and S200:
1. Pull the model weights into Ollama:
   ```bash
   ollama pull phi3:mini
   ```
2. Execute the local benchmark suite against `http://localhost:11434/v1`.
3. Measure accuracy, evidence grounding, structured JSON validity, and inference latency on Apple M1 Metal.

---

```text
LOCAL MODEL:
Microsoft Phi-3 Mini (3.8B-Instruct, phi3:mini)

LOCAL BASELINE STATUS:
BLOCKED

LOCAL SUITABILITY:
INSUFFICIENT-DATA

FINE-TUNING:
INSUFFICIENT-DATA

GEMINI VS LOCAL:
Gemini 2.5 Flash baseline verified at 100% accuracy (Phase 18); Local Phi-3 Mini benchmark blocked pending local weight pull (ollama pull phi3:mini).

NEXT PHASE:
Pull phi3:mini model weights via Ollama and execute the live local baseline benchmark against scenarios S1, S2, S3, S5, S6, and S200.
```
