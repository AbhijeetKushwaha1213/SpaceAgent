# PHASE 24 — STEP 0/1/2/3: PREREQUISITE AUDIT, BASELINE, AND IMPLEMENTATION PLAN

Reconciliation / Separation Logic. Produced **before any code was modified**, as
required by the specification (§0, §31 STEP 1–3: *"Do not guess the architecture.
Base implementation decisions on the actual repository."*).

Nothing in this document is aspirational. Every claim below was read out of the
working tree at commit `e42e284` on branch `antigravity`.

---

## PART A — STEP 2: BASELINE TEST RESULT (run first, so regressions are measurable)

```
1343 passed, 2 skipped, 4 errors, 2626 subtests passed in 127.57s
exit code 0
```

The **4 errors are pre-existing and environmental**, not code defects:

| Test | Failure |
|---|---|
| `test_phase11_sovereign_llm.py::TestLocalModeEndToEnd::test_full_pipeline_runs_local_and_audits_local` | `socket.bind` → `PermissionError: [Errno 1] Operation not permitted` |
| `test_phase11_sovereign_llm.py::TestLocalModeEndToEnd::test_no_cloud_call_during_local_pipeline` | same |
| `test_phase12_evaluation.py::TestEvaluationRunnerLocalMode::test_all_requests_hit_local_endpoint_only` | same |
| `test_phase12_evaluation.py::TestEvaluationRunnerLocalMode::test_evaluation_runs_local_only` | same |

All four fail inside `ThreadingHTTPServer(("127.0.0.1", 0), Handler)`. The sandbox
this session runs in forbids listening sockets. **The post-implementation
acceptance criterion is therefore `1343 + N passed, 2 skipped, 4 errors`** — the
same four, with no new failures.

---

## PART B — STEP 1: PREREQUISITE / AUDIT REPORT

### B.1 Existing relevant components

| Concern | Module | What it already provides |
|---|---|---|
| Telemetry canonicalization | `app/api/adapters.py` | `canonical_window()`, `with_canonical_window()`, `canonical_window_dicts()`, `coverage_report()`; parses `relative_time_s` **once**, here |
| Channel/subsystem vocabulary | `app/ingest/channel_dict.py` | `Subsystem` (EPS/AOCS/TCS/OBC/COMMS/PYLD/UNKNOWN), `SUBSYSTEM_ALIASES` (ADCS→AOCS), `resolve_subsystem()`, `subsystem_of()`, `get_channel()`, `hard_limits()` |
| Anomaly / event substrate | `app/detection/models.py` | `Anomaly`, `AnomalyReport`, `ChannelFinding`, `DetectorName` (10), `Severity` + `severity_rank()`, `AnomalyProvenance`, `Anomaly.make_id()` |
| Detection pipeline | `app/detection/fusion.py` | `run_detection_on_crash_dump()` → `AnomalyReport` |
| State estimation | `app/estimation/` | `estimate_states()`, `compute_residuals()` |
| Hypotheses + evidence | `app/diagnosis/candidates.py` | `EvidenceItem`, `Hypothesis`, `HypothesisSet`, `generate_hypotheses()`, `classify_llm_fault()` |
| **Physical relationship authority** | `app/diagnosis/propagation.py` | `PROPAGATION_EDGES` (mechanism + strength + `typical_delay`), `get_edge()`, `is_plausible_propagation()`, `explain_path()`, `explained_subsystems()`, `onset_ordering()`, `MULTI` |
| Physics authority | `app/validation/physics.py` | `PhysicsStatus`, `PhysicsVerdict`, `PhysicsValidationReport.verdict_for_fault()`, `validate_crash_dump()`, `reconcile_llm_claim()`, `LLMOverrideAttempt` |
| Constrained LLM contract | `app/llm/models.py` | `LLMRankingInput`, `HypothesisContext`, `EvidenceContext`, `EvidenceStatus`, `LLMRankingOutput`, `GuardrailResult`, `ViolationType` |
| Ranking / bundle assembly | `app/llm/ranker.py` | `build_ranking_input()`, `build_constrained_prompt()`, `run_constrained_ranking()`, `validate_ranking_output()`, `convert_to_sentinel_output()`, `compute_evidence_status()` |
| Hybrid router (dormant) | `app/llm/router_contract.py`, `branch_policy.py`, `local_branch.py`, `cloud_branch.py`, `arbitrator.py`, `merge_resolver.py`, `router_orchestrator.py` | `router_enabled()`, `Branch`, `RoutingDecision`, `RoutingReason`, `BranchOutcome`, `BranchResult`, `RoutingRecord`, `combine_human_review()` |
| Safety authority | `app/agent/safety.py` | `validate_recovery_plan()`, `apply_validation_to_output()`, `derive_safety_status()`, `ValidationResult`, `BlockedStep`; registry gates `is_registered()`/`is_enabled()` |
| Cloud redaction gate | `app/security/exfiltration.py` | `classify_payload()`, `apply_cloud_redaction()`, `record_external_transmission()` |
| Audit | `app/audit/record.py` | `Stage` (12), `StageStatus`, `AuditRecorder`, hash chain, `redact()`, `SYSTEM_ONLY_STAGES` |
| RAG | `app/agent/rag.py` | `retrieve_procedures()`, `retrieve_procedures_traced()` |
| Orchestration | `app/agent/agent.py` | `analyze_crash_dump()` (legacy), **`analyze_crash_dump_stream()` (main, 9 stages)** |
| API / contract | `app/main.py`, `app/api/models.py` | `CONTRACT_VERSION="1.0.0"`, `SafetyStatus`, `SSEEvent`, `SentinelOutput` |
| Demo | `demo/run.py` + `demo/data/synthetic_scenario.json` | 17-stage judge-facing runner, `p_step(n, title)` = `[NN/17]` |
| Frontend | `sentinel/frontend/src/` | `components/views/*.jsx`, `components/ui/Panel.jsx`, `state/SentinelContext.jsx`, `api/endpoints.js`, `generated/contract.js` |

### B.2 Existing data flow (`analyze_crash_dump_stream`, `app/agent/agent.py:1519-2047`)

```
1. INGEST            1540-1593  parse -> canonical_window() -> with_canonical_window()
                                -> _audit_record_input                (Stage.INPUT)
2. DETECTION         1595-1660  run_detection_on_crash_dump(crash_dict) -> AnomalyReport
                                -> _audit_record_detection            (Stage.DETECTION)
                                -> _audit_record_state_estimation     (Stage.STATE_ESTIMATION)
   ▲▲▲ ANOMALY / EVENT INFORMATION FULLY EXISTS HERE — line 1633 ▲▲▲
3. STATE ESTIMATION  1662-1689  estimate_states / compute_residuals
4. HYPOTHESES        1691-....  generate_hypotheses(detection_report, crash_dict)
5. PHYSICS           ....-1756  validate_crash_dump  -> Stage.PHYSICS_VALIDATION
6. RAG               ....-1829  retrieve_procedures_traced + Phase 9 retrieve_procs_p9
                                                       -> Stage.RAG
7. LLM               ....-1964  build_ranking_input -> run_constrained_ranking
                                -> convert_to_sentinel_output          -> Stage.LLM
8. SAFETY            1965-1972  validate_recovery_plan -> apply_validation_to_output
                                             -> Stage.HYPOTHESES, Stage.SAFETY_VALIDATION
9. RESULT            1973-2047  SSE RESULT                             -> Stage.DIAGNOSIS
```

`router_orchestrator` is **never called** from this function — router dormancy is
structural, not merely flag-gated.

### B.3 Existing identifiers (reuse these; do not build a parallel identity system)

| ID | Producer | Formula |
|---|---|---|
| `anomaly_id` | `Anomaly.make_id()` | `AN-{detector[:4]}-{sha1("det\|channel\|timestamp\|discriminator")[:12]}` |
| `evidence_id` | `candidates._evidence_id()` | `EVID-{sha256("channel\|condition\|role\|state\|source\|observed_from")[:12]}` |
| `hypothesis_id` | `candidates` | `HYP-{sha256(...)[:12]}` |
| `run_id` | `AuditRecorder.begin()` | audit run identity |
| `scenario_id` | crash dump payload | scenario identity |
| `fault_id` | `diagnosis/fault_dictionary` | fault-class identity |
| `procedure_id` / `citation_id` | `app/procedures`, RAG | procedure identity |
| `PhysicsVerdict.hypothesis_id` / `.fault_id` | physics | verdict linkage |

**Repo-wide invariant: content-derived, truncated-hash, zero-randomness IDs.**
Phase 24 will follow it exactly (`CASE-…`, `EVT-…`, `REL-…`).

**Gap: there is no `case_id` anywhere in the repository.**

### B.4 Existing evidence representation

`app/diagnosis/candidates.py` — frozen `EvidenceItem`:
`evidence_id, channel, condition, role, state, rationale, detectors, severity,
timestamp, anomaly_ids, weight, source, observed_from`.
Roles: `SUPPORTING | CONTRADICTING | UNDETERMINED`.

`app/llm/models.py` — frozen `EvidenceContext` (the LLM-facing projection):
`evidence_id, type, source, description, channel, condition, state, detectors,
severity, timestamp, weight, provenance`.

`EvidenceStatus` = `ADEQUATE | PARTIAL | INSUFFICIENT | CONTRADICTORY`, computed
deterministically by `ranker.compute_evidence_status()`; default `INSUFFICIENT`
(fail-safe).

**Gap: `EvidenceItem` carries `anomaly_ids` but no case scope.**

### B.5 Existing hypothesis representation

Three distinct, intentional representations:
1. `diagnosis.candidates.Hypothesis` — deterministic, scored
   (`SCORE_WEIGHTS = signature .60 / propagation .20 / specificity .13 / onset .07`,
   `MIN_SCORE_TO_REPORT = 0.05`, `MAX_HYPOTHESES = 8`, `HYPOTHESIS_ENGINE_VERSION "1.0.0"`).
   `HypothesisSet.generated_by = "deterministic_signature_matching"`, `uses_llm = False`.
2. `llm.models.HypothesisContext` — LLM-facing projection, carries `physics_status`.
3. `api.models.Hypothesis` — API projection, `rank` constrained 1–3.

`HypothesisOrigin` = `DETERMINISTIC | LLM_RANKED | UNSUPPORTED_HYPOTHESIS`.

### B.6 Existing physics representation

`PhysicsStatus` verdicts on `PhysicsVerdict`; aggregated in
`PhysicsValidationReport` with `.validated / .invalidated / .uncertain` and
`verdict_for_fault(fault_id)`.

`reconcile_llm_claim(verdict, llm_claimed_status) -> (verdict, LLMOverrideAttempt)`
**returns the verdict object unchanged — there is no branch that returns a
modified verdict.** `LLMOverrideAttempt.overridden` is documented as *"Always
False."* This is the structural guarantee §10 requires; Phase 24 will not touch it.

### B.7 Existing safety representation

`SafetyStatus` precedence: `BLOCKED > PARTIALLY_BLOCKED > REQUIRES_HUMAN_REVIEW > VALIDATED`
(`NOT_VALIDATED` is the un-run state).

`validate_recovery_plan()` runs five ordered checks — CMD_ prefix, registry
membership, enabled, declared preconditions, non-blocking escalation — then
`derive_safety_status()`. `apply_validation_to_output()` rebuilds a new
`SentinelOutput` (never mutates) and **ORs** human review:
`sentinel_output.requires_human_review or validation_result.requires_human_review`.

`SentinelOutput.validate_output_invariants` **auto-raises** `requires_human_review`
when `confidence < 0.70` or a HIGH-risk step exists, and invariant 6 permits an
empty `recovery_plan` only when `safety_status is BLOCKED` (which must carry
`blocked_steps`). Monotone human review is already an enforced repo invariant.

### B.8 Existing audit representation

`Stage` (12): `INPUT, DETECTION, STATE_ESTIMATION, RAG, ROUTING, LLM,
EXTERNAL_TRANSMISSION, HYPOTHESES, PHYSICS_VALIDATION, SAFETY_VALIDATION,
DIAGNOSIS, OPERATOR_DECISION`.
`StageStatus`: `OK, DEGRADED, FAILED, SKIPPED, NOT_RUN, NOT_IMPLEMENTED`.

`AuditRecorder.record(stage, status, summary, payload, ...)` is **append-only and
raises `ValueError` if the same stage is recorded twice.** Redaction
(`_SECRET_KEY_PATTERN`, `_key_holds_secret`, `redact`, `truncate_text`) runs on
every payload. Entries are hash-chained (`prev_hash`/`entry_hash`, `GENESIS_HASH`).

`SYSTEM_ONLY_STAGES` is computed generically (`frozenset(s for s in Stage if s is
not Stage.OPERATOR_DECISION)`), so a new `Stage` member is automatically covered
by `test_phase4_audit.py::test_system_stages_are_marked_system_only`.

**Contract coupling found:** `contracts/index.json` publishes
`enums.AuditStage` (all 12 values), and
`tests/test_phase3_contract.py::test_artifacts_are_not_stale` executes
`scripts/export_contracts.py --check`. Adding a `Stage` member therefore
**requires regenerating `contracts/`** (the documented, intended workflow —
*"changing a model without regenerating the contract fails the build. That is the
whole point."*). `NOT_IMPLEMENTED_STAGES` is hard-coded `[]` in the exporter and
is unaffected.

### B.9 Exact integration point selected

| # | Site | Change |
|---|---|---|
| 1 | `app/agent/agent.py:1633` (immediately after `_audit_record_detection` / `_audit_record_state_estimation`, before Stage 3) | **Primary insertion.** Anomaly/event info exists; no case-specific evidence has been assembled yet. Satisfies §3 exactly. |
| 2 | `app/llm/ranker.py::build_ranking_input` | Optional `case_context=` kwarg. When present, assert every evidence id is in-case before the bundle is built (§11). Default `None` ⇒ byte-identical behaviour. |
| 3 | `app/agent/rag.py::retrieve_procedures_traced` | Optional `case_context=` kwarg → delegates to the new **filter** layer. Retrieval itself is untouched (§15 "smallest safe filtering layer"). |
| 4 | `app/audit/record.py::Stage` | `+ RECONCILIATION = "reconciliation"`, then regenerate `contracts/`. |
| 5 | `app/main.py` | `GET/POST /api/v1/reconciliation/cases` — read-only, case-separated projection (§26). |
| 6 | Frontend | New `ReconciliationView.jsx` rendering **one `<Panel>` per case** (§26: *"Do not combine the two case panels."*). |

### B.10 Existing tests that must remain unchanged (43 files)

```
conftest.py  run_tests.py  test_agent.py  test_constructor.py
test_demo_reliability.py  test_e2e_integration.py  test_esa_integration.py
test_generate_crash_dump.py  test_helpers.py  test_models.py
test_phase0_frontend.py  test_phase0_provenance.py  test_phase10_llm.py
test_phase11_sovereign_llm.py  test_phase12_evaluation.py  test_phase14_security.py
test_phase15_evidence_pipeline.py  test_phase16_llm_baseline.py
test_phase17_evidence_rag_safety.py  test_phase1_blocked_plans.py
test_phase1_registry.py  test_phase21_contract_hardening.py
test_phase23_arbitrator.py  test_phase23_branch_policy.py
test_phase23_cloud_branch.py  test_phase23_cloud_redaction.py
test_phase23_local_branch.py  test_phase23_merge_resolver.py
test_phase23_router_contract.py  test_phase23_router_orchestrator.py
test_phase2_detection.py  test_phase3_contract.py  test_phase4_audit.py
test_phase5_channel_dict.py  test_phase7_estimation.py  test_phase8_physics.py
test_phase9_procedures.py  test_pipeline.py  test_prompts.py  test_rag.py
test_safety.py  test_schema_alignment.py  test_streaming.py
```

Not one will be weakened, skipped, deleted, or rewritten.

### B.11 Prerequisites MISSING from the repository

| # | Gap | Minimum prerequisite (per §0: *implement the minimum necessary prerequisite*) |
|---|---|---|
| P1 | No case identity concept | `CaseID` (`CASE-{sha256[:12]}`) + `Case` record in a new `app/reconciliation/` package |
| P2 | No comparable event model | `ObservationEvent` (`EVT-…`) projected **by reference** from `Anomaly`/`ChannelFinding` — no raw telemetry copied |
| P3 | Evidence has no case scope | External `CaseEvidenceIndex` mapping `case_id → (evidence_id, event_ids, channel, validation_status)`. **`EvidenceItem` is left untouched** so no existing `EVID-` hash changes. |
| P4 | RAG is not case-aware | `app/reconciliation/rag_filter.py` — filters/annotates results of the existing retriever. No second RAG framework. |
| P5 | Audit has no reconciliation stage | `Stage.RECONCILIATION` + contract regeneration |
| P6 | `build_ranking_input` performs no case-scope assertion | opt-in `case_context=` kwarg |
| P7 | No feature-flag entry for this layer | `reconciliation_enabled()` reading `RECONCILIATION_ENABLED`, modelled byte-for-byte on `router_enabled()`. **No `.env` file is modified** (§28). |

---

## PART C — STEP 3: EXACT IMPLEMENTATION PLAN

### C.0 Naming decision (§29)

The specification suggests `app/llm/reconciliation.py` but instructs *"Do NOT
blindly use these filenames if the repository has an established naming
convention."* Two repo facts override the suggestion:

1. §6 forbids the engine from reading **any** LLM output. Housing it in `app/llm/`
   would misrepresent its authority.
2. The repo groups by domain package (`app/detection/`, `app/diagnosis/`,
   `app/estimation/`, `app/validation/`, `app/security/`, `app/audit/`), each with
   a `models.py`-style contract module and a re-exporting `__init__.py`.

**Decision: `app/reconciliation/`**, tests named `test_phase24_*.py` (matching
`test_phase23_*.py`).

### C.1 Files to create

```
app/reconciliation/__init__.py            re-exports (mirrors app/detection/__init__.py)
app/reconciliation/config.py              RECONCILIATION_ENABLED + versioned thresholds
app/reconciliation/contract.py            enums + frozen dataclasses (the "language", no logic)
app/reconciliation/events.py              ObservationEvent projection from AnomalyReport
app/reconciliation/signals.py             the 8 independent deterministic signal evaluators
app/reconciliation/engine.py              ReconciliationEngine — priority-ordered decision
app/reconciliation/cases.py               CaseRegistry: identity, separation, conservative merge
app/reconciliation/isolation.py           evidence-isolation assertions + case-scoped bundle
app/reconciliation/rag_filter.py          smallest safe case-aware RAG filter
app/reconciliation/audit.py               audit payload builder (uses existing AuditRecorder)
demo/data/reconciliation_scenarios.json   6 deterministic INPUT scenarios (synthetic)
demo/reconciliation_demo.py               runs them through the real pipeline
tests/test_phase24_reconciliation.py
tests/test_phase24_reconciliation_security.py
tests/test_phase24_reconciliation_integration.py
PHASE_24_RECONCILIATION_SEPARATION_REPORT.md
sentinel/frontend/src/components/views/ReconciliationView.jsx
```

### C.2 Files to modify (additive only, all flag-gated)

| File | Modification | Behaviour when `RECONCILIATION_ENABLED=false` |
|---|---|---|
| `app/agent/agent.py` | Stage 2.5 block after line 1633 | not entered; identical stream |
| `app/llm/ranker.py` | `build_ranking_input(..., case_context=None)` | `None` ⇒ existing code path |
| `app/agent/rag.py` | `retrieve_procedures_traced(..., case_context=None)` | `None` ⇒ existing code path |
| `app/audit/record.py` | `Stage.RECONCILIATION` member | member exists but is never recorded |
| `app/main.py` | new read-only endpoint | endpoint returns `enabled: false` + empty cases |
| `contracts/*`, `frontend/src/generated/contract.js` | regenerated by `scripts/export_contracts.py` | — |

### C.3 The eight independent signals (§8) — all deterministic, all explainable

| # | Signal | Deterministic source | Never |
|---|---|---|---|
| 1 | `TEMPORAL_PROXIMITY` | `relative_time_s` from `canonical_window` | no single "60 s ⇒ same case" rule |
| 2 | `SUBSYSTEM_RELATIONSHIP` | `channel_dict.resolve_subsystem()` | no string matching on free text |
| 3 | `CHANNEL_RELATIONSHIP` | set algebra over channel names (shared / Jaccard) | no embeddings |
| 4 | `SIGNAL_PATTERN_SIMILARITY` | detector-set + severity-rank + direction tuple equality | no vector similarity |
| 5 | `PHYSICAL_RELATIONSHIP` | `propagation.get_edge()` / `explain_path()` / `typical_delay` vs observed Δt | physics is an **authority**, never a similarity score |
| 6 | `HYPOTHESIS_COMPATIBILITY` | candidate `fault_id` overlap + mutual-exclusion groups | no model confidence |
| 7 | `DUPLICATE_SIGNATURE` | exact equality of (channels, detectors, timestamps, states) | no fuzzy match |
| 8 | `CONTRADICTION_INDICATOR` | opposing direction on a shared channel; physics `VALIDATED` vs `INVALIDATED` | no silent discard |

Each evaluator returns a frozen `SignalOutcome(signal, verdict, value,
threshold_used, threshold_name, explanation)`.
`verdict ∈ {SUPPORTS_IDENTITY, SUPPORTS_RELATION, NEUTRAL, OPPOSES, CONTRADICTS}`.

### C.4 Decision rules (priority-ordered; first match wins; conservative default)

```
R1  any CONTRADICTS                              -> CONFLICT     (both sources preserved)
R2  DUPLICATE_SIGNATURE exact                    -> DUPLICATE    (merge permitted)
R3  >= IDENTITY_MIN_SUPPORTING_SIGNALS identity
    signals AND physics not OPPOSES AND subsystem
    same-or-propagating AND within temporal window
    AND hypothesis-compatible                    -> SAME_CASE    (merge permitted)
R4  propagation-explained AND onset ordering
    consistent with typical_delay                -> RELATED      (NO merge)
R5  no shared channels AND unrelated subsystems
    AND outside temporal window                  -> SEPARATE     (NO merge)
R6  otherwise                                    -> UNCERTAIN    (NO merge — keep separate)
```

`merge_permitted` is a property of the decision, computed only for `DUPLICATE`
and `SAME_CASE`. §13 default-to-separate is therefore structural: rules R4–R6 have
no path to a merge.

### C.5 Thresholds (§9 — explicit, versioned, documented as engineering assumptions)

`app/reconciliation/config.py`, `RECONCILIATION_CONFIG_VERSION = "1.0.0"`:

```
TEMPORAL_SAME_CASE_WINDOW_S      = 30.0   engineering assumption
TEMPORAL_RELATED_WINDOW_S        = 300.0  engineering assumption
CHANNEL_OVERLAP_MIN_JACCARD      = 0.50   engineering assumption
PATTERN_SIMILARITY_MIN           = 0.75   engineering assumption
IDENTITY_MIN_SUPPORTING_SIGNALS  = 3      engineering assumption
PROPAGATION_MIN_STRENGTH         = 0.50   matches propagation.py default
```

Each carries a docstring stating it is a **reviewable engineering assumption, not
a measured physical constant** (§9: *"Do NOT invent fake scientific precision."*).
Boundary tests will pin `< / == / >` behaviour at each value.

### C.6 Authority boundaries the implementation will enforce structurally

- Engine signature accepts **only** `ObservationEvent`/deterministic reports. It
  has no parameter through which `raw_text_head`, model confidence, LLM reasoning,
  or a model-supplied case id could arrive (§6) — mirroring how `BranchResult`
  makes monotonicity unexpressible.
- `reconcile_llm_claim` and `PhysicsVerdict` are **read-only imports**; no code
  path constructs a modified verdict (§10).
- Reconciliation emits no `RecoveryStep`, touches no command registry, and calls
  neither `validate_recovery_plan` nor `apply_validation_to_output` (§18/§19).
- Human review accumulates **only** through `combine_human_review()` (§19).
- Cloud path continues through `apply_cloud_redaction()` +
  `record_external_transmission()` unchanged; a gate failure remains fail-closed
  via `RoutingReason.REDACTION_GATE_FAILURE` (§16/§28).

### C.7 Demo (§21–22)

`demo/data/reconciliation_scenarios.json` holds **6 synthetic INPUT scenarios** —
`DUPLICATE`, `SAME_CASE`, `SEPARATE`, `RELATED`, `CONFLICT`, `UNCERTAIN`.
Inputs are fixed; **no decision, case id, relationship, or diagnosis is
hardcoded** — every one is computed by running the real engine. `demo/reconciliation_demo.py`
reuses `demo/run.py`'s `p_header`/`p_step`/`p_item`/`p_pass`/`p_block` helpers.

### C.8 Test matrix (§23, items A–AN) → files

| File | Covers |
|---|---|
| `test_phase24_reconciliation.py` | A–T: six relationship classes; temporal & subsystem boundary conditions; channel/pattern similarity; physical + hypothesis compatibility; deterministic case ids; idempotent relationship records; repeat-reconciliation determinism; conservative merge; UNCERTAIN does not force merge; CONFLICT preserves both sources |
| `test_phase24_reconciliation_security.py` | U–AD: physics verdict immutability; safety not bypassable; human-review monotonicity; raw LLM text/confidence cannot affect reconciliation; model cannot assign case ids; no command authorization; cloud redaction enforced; router dormant when disabled |
| `test_phase24_reconciliation_integration.py` | AE–AN: evidence isolation & no cross-case leak in `build_ranking_input`; RAG respects case boundaries; local/cloud branch case context; existing behaviour unchanged with the flag off; audit completeness; malformed event / missing timestamp / missing subsystem / bad telemetry reference; replay idempotency; concurrency & ordering robustness |

All fixtures synthetic. No API keys, no secrets, no real telemetry.

### C.9 Execution order

1. `config.py` → `contract.py` → `events.py` (no dependencies on the pipeline)
2. `signals.py` → `engine.py` → `cases.py` (pure, testable in isolation)
3. `isolation.py` → `rag_filter.py` → `audit.py`
4. `Stage.RECONCILIATION` + `scripts/export_contracts.py` regeneration
5. Flag-gated call sites: `agent.py`, `ranker.py`, `rag.py`
6. `main.py` endpoint + `ReconciliationView.jsx`
7. Demo data + demo runner
8. Three test files
9. `python -m pytest tests/test_phase24_*` → `tests/test_phase23_*` → full suite
10. `git diff --check`, `git status`, §28 security review
11. `PHASE_24_RECONCILIATION_SEPARATION_REPORT.md` (24 sections) + §32 final report

### C.10 Acceptance criteria

- Full suite: **≥ 1343 passed, 2 skipped, exactly the same 4 sandbox errors, 0 new failures**
- `RECONCILIATION_ENABLED` unset ⇒ `analyze_crash_dump_stream` emits an identical event sequence
- `ROUTER_ENABLED` unset ⇒ router still never invoked
- `scripts/export_contracts.py --check` exits 0
- `git diff --check` clean; no `.env` modified; no secrets added
