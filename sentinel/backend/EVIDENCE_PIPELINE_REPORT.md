# Evidence Pipeline Fix — Phase 15 Report

Status: COMPLETE. All Phase 15 work items implemented, measured, and tested.
Full suite: 1042 passed (baseline 1020), 2626 subtests, 0 failures, 1 warning
(chromadb deprecation — pre-existing, unrelated).

Baseline comparison data: `BASELINE_REPORT.md` (prior audit, commit `988e455`).
Measurements: `/tmp/phase15_measurements.json` (scenarios 1–6, 200–203).

---

## A. Files changed

| File | Change |
|---|---|
| `app/estimation/window_adequacy.py` | NEW — telemetry-window adequacy contract (PART 1) |
| `app/estimation/residuals.py` | `ResidualReport.window_adequacy` field, `as_dict` key, summary/warnings when not adequate |
| `app/validation/physics.py` | `PhysicsValidationReport.window_adequacy: dict` field + warning, populated on validation |
| `app/ingest/esa_mapping.py` | NEW — ESA channel mapping layer + curated mapping table (PART 3) |
| `app/diagnosis/candidates.py` | `HypothesisSet.esa_channel_mappings` field, UNMAPPED_CHANNEL warning, populated during hypothesis generation |
| `app/agent/agent.py` | `[ESA_MAPPING]` SSE observations during streaming (first 6, step 4) |
| `app/api/scenarios.py` | Scenarios 1, 2, 3, 5, 6 resampled (PART 2); Phase 15 sampling policy documented in module docstring |
| `app/estimation/__init__.py`, `app/ingest/__init__.py` | Public exports |
| `contracts/openapi/openapi.json`, `contracts/schemas/PhysicsValidationReport.schema.json` | Regenerated via `scripts/export_contracts.py` (37 artifacts) |
| `tests/test_phase15_evidence_pipeline.py` | NEW — 22 tests / 20 subtests (PART 6) |
| `EVIDENCE_PIPELINE_REPORT.md` | This report (PART 7) |

No changes to the LLM, the safety policy, the architecture, or any cloud path.

## B. Data-flow changes

1. Crash dump → `assess_window_adequacy` (PART 1) → adequacy status attached to the residual report (part of the state-estimation audit record).
2. Hypothesis generation now also emits `esa_channel_mappings` (PART 3): every channel present in the dump is run through `map_esa_channel`; outcome recorded on the hypothesis set and streamed as `[ESA_MAPPING]` observations.
3. Physics validation consumes the pre-computed residual report; verdicts are produced only from decided residuals. A not-adequate window yields a `UNDER_SAMPLED_FOR_PHYSICS` warning on the validation report.

## C. Telemetry-window adequacy contract

`app/estimation/window_adequacy.py`. Per-channel adequacy (`ChannelAdequacy`) counts the rows of each modelled channel inside the pre-fault window; the window-level status aggregates them.

Status precedence (most severe wins):

| Status | Condition |
|---|---|
| `CONTRADICTORY_DATA` | same channel, same offset, conflicting values in merged window |
| `MISSING_REQUIRED_CHANNELS` | no row at all for any of the 5 predicted channels |
| `INVALID_TIMESTAMPS` | unparseable offset or non-monotonic dt on a predicted channel |
| `UNDER_SAMPLED` | rows present but nothing checkable (e.g. NaN-only, 1 row) |
| `ADEQUATE_FOR_PHYSICS` | ≥2 usable rows per predicted channel, ≥1 decided residual step |

`MODELLED_CHANNELS` = 10 (5 predicted + auxiliaries RW_speed_rpm / V_bus / I_sa / Heater_power_W / Heater_enable_flag). The contract steps only the 5 predicted channels; auxiliaries are reported per-channel.

Predicted channels and requirements (`MODEL_REQUIREMENTS`):

| Channel | Model | Required rows |
|---|---|---|
| `SoC_pct` | SoC_pred[k+1] = SoC[k] + 100·(P_gen − P_load)·dt / (3600·E_cap) | ≥2 |
| `V_bat` | V_pred[k+1] = V[k] + dV_corr·dt | ≥2 |
| `Component_temp_C` | T_pred[k+1] = T[k] + dt·(Q_int + P_heater − k_th·(T[k] − T_sink)) / C_th | ≥2 |
| `Gyro_rate_degs` | w_sc_pred[k+1] = w_sc[k] − (I_w/I_sc)·(w_w[k+1] − w_w[k]) | ≥2 |
| `Attitude_error_deg` | theta_bound[k+1] = theta[k] + |(w_sc[k] + w_sc[k+1])/2|·dt (open loop, upper bound) | ≥2 |

Not-adequate reports carry the literal `UNDER_SAMPLED_FOR_PHYSICS` in the summary so a non-checkable window is never silently reported as a clean physics pass.

## D. ESA mapping layer

`app/ingest/esa_mapping.py`. `map_esa_channel(channel)` resolves a raw ESA channel name through `CURATED_ESA_MAPPINGS`, returning `{channel, mapped_to, confidence, provenance}` with `MappingConfidence` = `MAPPED` / `UNMAPPED`. Provenance carries the resolution path so every mapping is attributable.

`CURATED_ESA_MAPPINGS` is deliberately empty: the ESA-Mission1 dataset is anonymised at physical-unit level (`channel_N`, no semantic claims — workflow doc), and the raw dataset is not present in the repo. Asserting a semantic mapping would be a fabricated claim, so `UNMAPPED_CHANNEL` is the correct, honest outcome. The curated table exists so a MAPPED path is representable and testable without inventing real mappings.

## E. Scenario results — before / after

Before (audit): every scenario reported "No residual could be decided"; ESA scenarios produced 0 hypotheses (channels `channel_N` matched no candidate); scenario 1 score was timing-sensitive (~0.76 live vs 0.965 in-process, F-08).

After (this run, `run_detection_on_crash_dump`, deterministic):

| # | Fault | Adequacy | Anomalies | Hyps | Top | Residuals (channel: status, obs, pred, res, tol, window) | Physics |
|---|---|---|---|---|---|---|---|
| 1 | ADCS_GYRO_SEU | ADEQUATE_FOR_PHYSICS | 11 | 7 | ADCS_GYRO_SEU 0.965 | Gyro_rate_degs INCONSISTENT (4.50, 0.525, +3.98, 0.115, T-60s→T-30s); Attitude_error_deg UNDECIDABLE (7.40, —, —, —, T-30s→T-0s) | AOCS_EXTERNAL_DISTURBANCE VALID; 6 UNCERTAIN |
| 2 | EPS_SOLAR_UNDERVOLT | ADEQUATE_FOR_PHYSICS | 20 | 3 | EPS_SOLAR_UNDERVOLT 0.869 | SoC_pct INCONSISTENT (14.20, 86.16, −71.96, 2.80, T-300s→T-180s); V_bat INCONSISTENT (21.80, 28.55, −6.75, 2.18, T-180s→T-0s) | EPS_SOLAR_UNDERVOLT VALID, EPS_BATTERY_DEGRADATION VALID; MULTI_CASCADE UNCERTAIN |
| 3 | OBC_WATCHDOG_OVERFLOW | ADEQUATE_FOR_PHYSICS | 8 | 2 | OBC_WATCHDOG_OVERFLOW 0.794 | SoC_pct CONSISTENT (90.00, 89.91, +0.09, 1.84, T-10s→T-0s); V_bat CONSISTENT (31.10, 32.50, −1.40, 1.54, T-10s→T-0s) | EPS_SOLAR_UNDERVOLT INVALID (PHYS_ENERGY_BALANCE); OBC_WATCHDOG_OVERFLOW UNCERTAIN |
| 5 | TCS_THERMAL_RUNAWAY | ADEQUATE_FOR_PHYSICS | 13 | 3 | TCS_THERMAL_RUNAWAY 0.880 | Component_temp_C INCONSISTENT (64.00, 52.40, +11.60, 6.40, T-60s→T-0s) | TCS_THERMAL_RUNAWAY VALID; MULTI_CASCADE, EPS_SOLAR_UNDERVOLT UNCERTAIN |
| 6 | COMMS_TRANSPONDER_LOSS | ADEQUATE_FOR_PHYSICS | 21 | 2 | COMMS_TRANSPONDER_LOSS 0.663 | SoC_pct CONSISTENT (88.00, 89.19, −1.19, 2.80, T-120s→T-0s); V_bat CONSISTENT (30.80, 32.46, −1.66, 1.95, T-120s→T-0s) | EPS_SOLAR_UNDERVOLT INVALID (PHYS_ENERGY_BALANCE); COMMS_TRANSPONDER_LOSS UNCERTAIN |
| 4, 200–203 | (ESA / legacy no-telemetry) | MISSING_REQUIRED_CHANNELS | — | 0 | — | No predicted channels present; anomaly-only windows. Mappings: UNMAPPED_CHANNEL for all channels | — |

Every residual above carries unit, equation, `from_timestamp`/`to_timestamp`, observed, predicted, residual, and tolerance — attributable in full.

Scenario 1 note: `Attitude_error_deg` stays UNDECIDABLE because the gyro is NaN at T-0 (the injected SEU); the NaN is preserved deliberately to keep `GYRO_DATA_VALID` gating intact. The momentum residual (wheel −4 rpm over the step) is INCONSISTENT with the body model, which is exactly what a gyro fault (or an external disturbance) produces; physics therefore raises `AOCS_EXTERNAL_DISTURBANCE` VALID while the SEU stays UNCERTAIN — the honest ceiling: a single-channel gyro fault and an external torque are not separable from these models.

## F. Physics verdicts — before / after

Before: verdicts existed structurally but were never decided (nothing to corroborate/refute) — effectively all UNCERTAIN.
After: every scenario with an adequate window yields decided verdicts with attributable inputs:

- Scenario 2: SoC −71.96 vs tolerance 2.80 corroborates `EPS_SOLAR_UNDERVOLT` and `EPS_BATTERY_DEGRADATION` (VALID); `PHYS_ENERGY_BALANCE` corroborated via the SoC residual.
- Scenario 5: Component_temp_C +11.60 vs tolerance 6.40 corroborates `TCS_THERMAL_RUNAWAY` (VALID).
- Scenario 3/6: consistent energy residuals violate `PHYS_ENERGY_BALANCE` for `EPS_SOLAR_UNDERVOLT` → INVALID (refuted). `OBC_WATCHDOG_OVERFLOW` / `COMMS_TRANSPONDER_LOSS` are extra-power hypotheses with no energy signature → UNCERTAIN (not a pass).
- Scenario 1: as described in E.

UNCERTAIN is never reported as a pass; the residual summary states: "An exceedance indicates disagreement with the stated model assumptions, not a confirmed hardware fault."

## G. New tests

`tests/test_phase15_evidence_pipeline.py` — 22 tests / 20 subtests covering all 10 required cases:

1. Adequate window → ADEQUATE_FOR_PHYSICS + decided residuals (scenarios 1, 2, 3, 5, 6).
2. Under-sampled window → UNDER_SAMPLED + `UNDER_SAMPLED_FOR_PHYSICS` summary literal.
3. Missing predicted channel → MISSING_REQUIRED_CHANNELS (ESA scenarios 200–203).
4. Invalid timestamps → INVALID_TIMESTAMPS.
5. ESA channel mapped via curated table → MAPPED with provenance.
6. ESA channel unmapped → UNMAPPED_CHANNEL with provenance.
7. Resampled synthetic presets produce ≥2 rows per predicted channel and decided residuals.
8. Detection preserved: scenario 1 top = ADCS_GYRO_SEU, 7 hypotheses; scenario 3 top = OBC_WATCHDOG_OVERFLOW; scenario 5 top = TCS_THERMAL_RUNAWAY.
9. Physics VALID (scenarios 2, 5), INVALID (scenarios 3, 6 EPS refuted), UNCERTAIN (scenarios 1, 3, 6) with attributable constraints/residuals.
10. Legacy-anchored (no-timestamp) rows agree with the T-0s window sample — no self-contradiction.

## H. Remaining limitations (STOP-and-report items)

- **ESA semantic mapping is intentionally empty.** The anonymised ESA dataset forbids physical-unit semantics and the raw dataset is absent from the repo; `UNMAPPED_CHANNEL` is the designed outcome, not a gap. A real mapping requires the original dataset or an ESA-provided dictionary.
- **Gyro-fault vs external-disturbance ambiguity (scenario 1)** cannot be resolved by momentum alone; requires a second attitude sensor or commanded-vs-actual actuator telemetry.
- **Extra-power faults (OBC_WATCHDOG_OVERFLOW, COMMS_TRANSPONDER_LOSS) stay UNCERTAIN** — they carry no energy signature; corroboration needs power-budget or transponder-specific telemetry.
- **UNDECIDABLE residuals (scenario 1 attitude bound)** are inherent to preserving NaN gating; they count as "nothing checkable" rather than contradiction.
- LLM, safety policy, and architecture were untouched per directive; nothing in this report requires them.