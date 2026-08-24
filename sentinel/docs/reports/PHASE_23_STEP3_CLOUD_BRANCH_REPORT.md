# PHASE 23 STEP 3 — CLOUD BRANCH + MANDATORY REDACTION GATE REPORT

Scope: the cloud-branch adapter and its mandatory fail-closed redaction
gate. The router remains **disabled** (`ROUTER_ENABLED=false`); the
production execution path is unchanged. No Arbitrator, MergeResolver, or
orchestration exists yet.

---

## A. Objective

Make the CLOUD branch independently safe and contract-compatible before
arbitration exists:

1. **CloudBranchRunner** — an adapter around the existing constrained
   pipeline for the cloud branch (mirrors `LocalBranchRunner`).
2. **Mandatory cloud-redaction gate** — fail-closed, executed BEFORE prompt
   construction, BEFORE `provider.call`, BEFORE any network transmission.
3. Contract/integration tests with provider payload interception, plus
   adversarial redaction security tests.

Phase 22 had identified the launch-blocking gap this step closes: cloud
redaction was wired only into the legacy `analyze_crash_dump` path
(agent.py), while the constrained ranking path bypassed it (Phase 22 §4,
defect D2).

## B. Existing redaction architecture discovered

Reused — **no second framework invented**:

| Component | Location | Role in the gate |
|---|---|---|
| `apply_cloud_redaction(payload, config)` | `app/security/exfiltration.py` | key-name classification + `[REDACTED]` replacement of CONFIDENTIAL fields + removal of `SENTINEL_CLOUD_REDACT_PARAMETERS` telemetry points; returns `(redacted_copy, report)` without mutating the original |
| `classify_payload` / `classify_data` | `app/security/exfiltration.py` / `app/security/redaction.py` | repository privacy classification: CONFIDENTIAL (key/secret/token/auth/password names), RESTRICTED_TELEMETRY (telemetry/window/raw_response/prompt names), PUBLIC |
| `_SECRET_PATTERNS` | `app/security/redaction.py` | existing secret shapes (credential key=value, Google `AIzaSy…`, OpenAI `sk-…`) |
| `SecurityConfig.cloud_redact_parameters` | `app/security/config.py` | env-driven parameter-level redaction list |

The repository classification is **key-name based**. The constrained
ranking bundle additionally carries free-text summaries derived from the
crash dump (`anomaly_summary`, `state_summary`, `residual_summary`,
`physics.summary`), which a key-name classifier cannot inspect. The gate
therefore adds a deterministic **verification layer** on top of the
existing redaction (it is not a parallel redaction engine).

## C. Cloud trust boundary

```
DETERMINISTIC PIPELINE
        ↓
validated evidence bundle (LLMRankingInput)
        ↓
CLOUD REDACTION GATE          ← fail closed (this step)
        ↓
redacted cloud-safe ranking input (dict)
        ↓
cloud prompt built FROM THE REDACTED dict (private helper)
        ↓
GeminiProvider.call
        ↓
JSON extraction (_extract_json, existing)
        ↓
LLMRankingOutput.from_dict (existing)
        ↓
existing guardrails (validate_ranking_output)
        ↓
BranchResult (Step 1 contract)
```

The Gemini model never receives the raw pre-redaction bundle. The runner
**owns the sequence** `redact → prompt → call`; there is no public entry
point that accepts prebuilt messages or an unredacted prompt, so a bypass
through the normal API is impossible (Part 13). Privacy is enforced before
the network boundary and never relies on model instructions, refusal
behavior, or the model's understanding of sensitive data (Part 14).

## D. CloudBranchRunner design

Location: `app/llm/cloud_branch.py` (522 lines).

- Same adapter discipline as `LocalBranchRunner`: reuses
  `_CONSTRAINED_SYSTEM_PROMPT`, `_extract_json`, `LLMRankingOutput.from_dict`,
  `validate_ranking_output` — no second ranker.
- The only structural difference from the local adapter: the user-prompt
  JSON is serialized from the **gate's redacted dict** instead of
  `ranking_input.as_prompt_dict()`.
- Bounded repair retry (`max_retries=1` default; negative rejected),
  identical to the existing `run_constrained_ranking` convention.
  Echo-shaped completions are never retried in-process.
- Real latency (`time.perf_counter()`) and truthful attempt counts; latency
  is audit metadata only.
- Identity from the injected provider (`provider_name`, `model_name` —
  read from configuration, never hardcoded; expected baseline
  `gemini-2.5-flash`).
- `redaction_report` carried on the `BranchResult` (new optional contract
  field, `None` for non-transmitting branches) — classification + redaction
  findings for the future ROUTING/EXTERNAL_TRANSMISSION audit stage.
- Confidence semantics untouched: no calibration, no cross-branch
  comparison, no averaging (Part 16 — arbitration belongs to the next
  phase).

## E. Exact redaction contract

| RAW FIELD / CONDITION | REDACTED REPRESENTATION | REASON |
|---|---|---|
| Any dict field whose KEY matches the repository CONFIDENTIAL vocabulary (key/secret/token/auth/password) | value → `[REDACTED]` (key kept) | existing `classify_data` classification; marker is the intended redacted representation |
| Telemetry points whose parameter ∈ `SENTINEL_CLOUD_REDACT_PARAMETERS` | point removed entirely | existing policy: absent readings carry nothing |
| Free-text summaries containing confidential-key vocabulary (`anomaly_summary`, `state_summary`, `residual_summary`, `physics.summary`) | gate **fails closed** — no transmission | derived crash-dump content cannot be safely rewritten by deterministic code; refusal is the honest answer |
| Any field matching existing `_SECRET_PATTERNS` (credential shapes, `AIzaSy…`, `sk-…`) | gate **fails closed** | secret shapes must never be certifiable as cloud-safe |
| Internal filesystem paths (`/Users/`, `/home/`, `/var/`, `/etc/`, drive letters) in free text | gate **fails closed** | environment-derived information |
| Quantitative physics material: residuals (observed/predicted/residual/tolerance/exceedance), verdict lists, deterministic scores, evidence IDs, procedure IDs | **preserved unchanged** | repository classifies it PUBLIC; ranking, physics interpretation, evidence grounding and procedure selection depend on it (minimum necessary disclosure, Part 5) |

Fail-closed rule (Part 4): if redaction fails, raises, produces malformed
output, or the gate cannot PROVE the payload cloud-safe → `CloudRedactionError`
→ `BranchResult(FAILURE, REDACTION_GATE_FAILURE, human_review_required=True)`
and **the provider is never called**.

New contract additions (smallest necessary, per Part 4):
`RoutingReason.CLOUD_TIMEOUT`, `RoutingReason.REDACTION_GATE_FAILURE`,
`BranchResult.redaction_report: Optional[dict]`.

## F. Before/after payload examples (synthetic values only)

Before (deterministic bundle, as built today):

```json
{
  "anomaly_summary": "gyro rate excursion preceding safe mode",
  "operator_api_key": "RAW_OPERATOR_SECRET_9f8e7d",
  "spacecraft_state": {
    "residuals": [
      {"channel": "GYRO_A_RATE", "observed": 0.52, "predicted": 0.01,
       "residual": 0.51, "tolerance": 0.05, "exceedance": 10.2}
    ]
  },
  "valid_fault_ids": ["ADCS_GYRO_SEU"],
  "evidence_status": "ADEQUATE"
}
```

After (what the gate certifies for transmission):

```json
{
  "anomaly_summary": "gyro rate excursion preceding safe mode",
  "operator_api_key": "[REDACTED]",
  "spacecraft_state": {
    "residuals": [
      {"channel": "GYRO_A_RATE", "observed": 0.52, "predicted": 0.01,
       "residual": 0.51, "tolerance": 0.05, "exceedance": 10.2}
    ]
  },
  "valid_fault_ids": ["ADCS_GYRO_SEU"],
  "evidence_status": "ADEQUATE"
}
```

(No real API keys or sensitive telemetry appear anywhere in this report;
the `RAW_OPERATOR_SECRET_…` value is a synthetic test constant.)

## G. Failure mapping

| Cloud failure | BranchOutcome | RoutingReason |
|---|---|---|
| Redaction gate refuses transmission | FAILURE | REDACTION_GATE_FAILURE (+ forced human review) |
| Provider timeout (message-based classification) | FAILURE | CLOUD_TIMEOUT |
| Provider unavailable / other ProviderError | FAILURE | CLOUD_UNAVAILABLE |
| Unparseable JSON after bounded repair retry | FAILURE | INVALID_STRUCTURED_OUTPUT |
| Echo-shaped unusable completion (S1-type) | FAILURE | PROMPT_ECHO_TRUNCATION |
| Schema drift (`from_dict` raises) | FAILURE | INVALID_STRUCTURED_OUTPUT |
| Fabricated/unsupported evidence | FAILURE | EVIDENCE_FAILURE |
| Insufficient-evidence claim | FAILURE | INSUFFICIENT_EVIDENCE |
| Invalid procedure / unknown command | FAILURE | PROCEDURE_INVALID |
| Physics override attempt | FAILURE | PHYSICS_CONFLICT (+ forced review) |
| Zero violations | ACCEPT | VALID_CLOUD_RESULT |

No silent repair of unsafe output; raw output never becomes validated
output.

## H. Security tests

`tests/test_phase23_cloud_redaction.py` — 18 tests: gate success/audit
report, deterministic-field preservation, authority non-mutation, six
fail-closed variants (confidential substring, secret shape, path leak,
redactor exception, malformed serialization, malformed redactor output),
redacted-representation marker, bypass impossibility (runner public API is
exactly `run`; prompt builder is module-private and unexported), and
model-independence of the privacy decision.

`tests/test_phase23_cloud_branch.py` — 32 tests covering the full A–Y
matrix with a capturing FakeGeminiProvider. No GEMINI_API_KEY, internet,
Gemini API, or Ollama required.

## I. Provider payload interception test

`FakeGeminiProvider` records every message list it receives. The
integration tests then assert over the **full path** input → gate → prompt
→ provider:

- **L**: a raw CONFIDENTIAL value present in the deterministic input is
  absent from every captured message (`RAW_SECRET not in payload_text`).
- **M**: the `[REDACTED]` marker is present in the captured payload.
- **N**: ranking material (fault IDs, evidence IDs, procedure IDs,
  evidence status) does reach the provider.
- **K**: on gate failure the provider's call counter stays 0 and nothing
  is captured.

## J. Fail-closed behavior

Every gate refusal path returns `FAILURE + REDACTION_GATE_FAILURE` with
`human_review_required=True`, `inference_performed=False`, no redaction
report (nothing was certified), and zero provider calls. An explicitly
empty `SecurityConfig` cannot disable the verification layer; the gate is
not a config toggle.

## K. Router-disabled verification

- `ROUTER_ENABLED` unset ⇒ `router_enabled()` returns **False**.
- `grep CloudBranchRunner|cloud_branch|BranchPolicy|LocalBranchRunner
  app/agent/agent.py` → zero references: the runner is dormant, no Gemini
  call is introduced into production routing.
- Existing single-provider behavior and existing Gemini baseline tests are
  unchanged (full suite green, §L).
- `ModelMode.HYBRID` is not activated anywhere; the new `redaction_report`
  contract field defaults to `None` and local-branch results carry it as
  `None` (verified by test).

## L. Regression results

```
python3 -m pytest tests/ -q
1248 passed, 2 skipped, 1 warning, 2625 subtests passed in 46.44s
```

- New: 50 tests (18 redaction + 32 cloud branch), all passing.
- Baseline before this step: 1198 passed, 2 skipped. Delta = +50, 0
  failures, 0 regressions. The 2 skips remain the intentional Step 1
  `PHASE 23 FOLLOW-UP` stubs. No existing test weakened or deleted;
  Phase 18 Gemini benchmark numbers untouched; no Gemini benchmark rerun.

## M. Files changed

| File | Change |
|---|---|
| `app/llm/cloud_branch.py` | NEW — `CloudBranchRunner`, `redact_ranking_input_for_cloud`, `CloudRedactionError`, `CloudRedactionResult` |
| `app/llm/router_contract.py` | +`RoutingReason.CLOUD_TIMEOUT`, +`RoutingReason.REDACTION_GATE_FAILURE`, +`BranchResult.redaction_report` |
| `app/llm/__init__.py` | dormant exports for the cloud branch + gate |
| `tests/test_phase23_cloud_branch.py` | NEW — 32 runner tests (A–Y, capturing fake provider) |
| `tests/test_phase23_cloud_redaction.py` | NEW — 18 gate/security tests |
| `PHASE_23_STEP3_CLOUD_BRANCH_REPORT.md` | NEW — this report |

No production file behavior changed. `git diff --check` clean; secret scan
clean (synthetic test placeholders only).

## N. Explicitly deferred work

NOT IMPLEMENTED:

- Arbitrator
- MergeResolver
- RouterOrchestrator
- parallel execution
- HYBRID activation
- production routing
- 429 quota backoff inside the runner (proven in
  `scripts/phase21_run_benchmark.py`; belongs with orchestration)
- EXTERNAL_TRANSMISSION audit-stage wiring for the router path (needs the
  orchestrator; the redaction report is already carried on BranchResult)
