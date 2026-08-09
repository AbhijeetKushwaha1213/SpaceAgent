# Phase 5 — channel definition conflicts

Phase 5 consolidated three independent definitions of the same telemetry channels
into `app/ingest/channel_dict.py`. Consolidating them made five contradictions
visible that had been invisible while the numbers lived in separate files.

**None of them is resolved here.** Resolving one means deciding real spacecraft
physics, and inventing a plausible number would be worse than recording the
conflict. Each is listed with its measured consequence so the decision can be
made on evidence.

## What was consolidated

| Source | Channels | Role |
| --- | --- | --- |
| `app/analytics/anomaly_detector.py` `SATELLITE_NOMINAL_RANGES` | 21 | Drove every limit check. `app/detection/channels.py` imported it. |
| `simulation/fault_simulator.py` | 21 | Stamped `nominal_min` / `nominal_max` onto every generated reading. |
| `app/agent/prompts.py` `NOMINAL_THRESHOLDS` | 12 | Prose telling the LLM what the thresholds were. |

**17 of the 21 channels carried different numbers** in the first two tables.
Neither file referenced the other, and nothing checked they agreed.

The prompt had also drifted: it stated an OBC temperature nominal maximum of
50 °C where the detector used 60 °C.

## Why the two tables were not really disagreeing

They were describing different quantities and both calling them "nominal":

- **`nominal_range`** — the band a healthy spacecraft sits in. What the simulator
  samples from.
- **`hard_limits`** — the band outside which a violation is raised. What the
  detector compares against.

The dictionary records both, separately. `hard_limits` is carried over verbatim
from the detector table and `nominal_range` verbatim from the simulator, so
detector behaviour and generated datasets are unchanged. Verified by
`/tmp` differential runs at implementation time and pinned by
`tests/test_phase5_channel_dict.py`.

## The five conflicts

In each case the nominal operating band falls **outside** the channel's own hard
limits, which cannot both be true.

### 1. `Attitude_error_deg` — nominal `0 – 1.0 deg`, hard limit `0 – 0.01 deg`

The nominal band is 100× the hard limit. The simulator centres generated values
on the midpoint of the nominal band, so nominal-labelled telemetry sits at
~0.5 deg — fifty times the limit.

**Measured consequence:** generating a nominal window and running the Phase 2
detector on it flags `Attitude_error_deg` as `HARD_LIMIT` / `CRITICAL` at
0.4986 deg. Any evaluation using simulator nominal data as a negative control has
a guaranteed false positive on this channel.

**To resolve:** decide whether 0.01 deg is the pointing requirement (in which
case the nominal band is wrong) or whether 1.0 deg is realistic pointing
performance (in which case the hard limit is wrong). Scenario 1 ships a value of
7.3 deg presented as a clear fault, which is consistent with either.

### 2. `SEU_counter` — nominal `0 – 5`, hard limit `0 – 0`

The hard limit says any single-event upset is a violation. The nominal band says
up to five are normal.

**Measured consequence:** nominal-labelled telemetry contains `SEU_counter` ≈ 2.5,
flagged `COUNTER` and `HARD_LIMIT` at `HIGH`. Second guaranteed false positive in
the same nominal control set.

**To resolve:** decide whether the counter is expected to be exactly zero in
nominal orbit — the system prompt's fault signature assumes a *spike* is
diagnostic, which implies a non-zero baseline is normal and the hard limit of 0 is
too strict.

### 3. `Gyro_rate_degs` — nominal `-0.5 – 0.5 deg/s`, hard limit `0 – 7 deg/s`

The nominal band is signed; the hard limits are not. A legitimate negative body
rate reads as a limit violation.

**To resolve:** the hard limit is almost certainly intended as a magnitude
(`|rate| ≤ 7`). Representing that needs either a signed limit of `-7 – 7` or an
explicit magnitude-comparison mode, which the detector does not currently have.

### 4. `V_bus` — nominal `27.5 – 32.5 V`, hard limit `26.6 – 29.4 V`

The nominal band extends 3.1 V above the hard maximum.

**To resolve:** decide which reflects the regulated bus. A regulated 28 V bus with
a ±5% tolerance matches the hard limits closely; the nominal band looks like an
unregulated battery-voltage range applied to the wrong channel.

### 5. `Component_temp_C` — nominal `-10 – 70 °C`, hard limit `-20 – 65 °C`

The nominal band extends 5 °C above the hard maximum.

**To resolve:** minor compared with the others, but the same class of problem.
Note that the *safety* ceiling for this channel is 85 °C — a third number, held in
`safety_limits` and enforced by the validator, which is not in conflict because it
is deliberately looser than both.

## How the conflicts are tracked

- `ChannelDefinition.nominal_within_hard_limits` returns `False` for each of the
  five, `True` where the bands are consistent, and `None` where either is
  unspecified — absent data is not treated as agreement.
- `validate_dictionary()` separates `errors` from `known_conflicts`. These five
  are listed in `KNOWN_NOMINAL_LIMIT_CONFLICTS` and reported as known, so CI can
  require `errors` to be empty without blocking every build until the physics is
  settled.
- A **new** conflict lands in `errors` and fails CI. The gate is
  `python3 -m app.ingest.channel_dict`.
- A conflict that stops conflicting produces a warning telling you to remove it
  from the list, so the list cannot rot.

## Channels adopted from scenario data

Eight channels appeared in shipped preset scenarios with declared bounds but in
neither table: `Panel_temp_C`, `Battery_temp_C`, `Radiator_eff_pct`,
`RF_power_dBm`, `Link_margin_dB`, `Bit_error_rate`,
`Antenna_pointing_error_deg`, `Transponder_temp_C`.

Their bounds are adopted with `Provenance.REPO_SCENARIO_DATA`, which
`app/detection/channels.py` maps to `BoundOrigin.UNKNOWN` rather than
`ENGINEERING` — a bound that appeared in one scenario is weaker evidence than one
applied to every dump, and the statistical detectors must not treat it as an
engineering limit.

**Measured consequence of declaring them:** scenario 5 went from 7 findings to 12
and scenario 6 from 5 to 14. The additional `ZSCORE` findings are corroboration of
hard-limit violations already reported, and every one is tagged `RANGE_DERIVED` /
`LOW` confidence per the Phase 2 weak-evidence policy.

## A detection blind spot found while consolidating

A ninth channel, `Link_status`, was in no table at all — not the detector's, not
the simulator's, not the prompt's. Preset scenario 6 ships it at value `0` with
status `CRITICAL` during a transponder-loss event, and because it carried no
bounds from anywhere, **the detector reported nothing for it**. A loss of link went
unflagged in the scenario built around losing the link.

It is now a COMMS `FLAG` with `expected_states=(1.0,)`. The expected state is
derived from the scenario's own labelling rather than assumed: the scenario asserts
that 0 is the fault state. Scenario 6 now reports `Link_status` as
`DISCRETE_STATE` / `CRITICAL`, which is why its finding count went to 14 rather
than 12.

This is the same class of defect Phase 2 closed for `Transponder_lock`,
`SEU_counter`, `Star_tracker_status`, `Fault_register` and `Watchdog_counter` —
a channel the detector could not act on. Phase 2 closed it for channels with
degenerate ranges; this one was missed because it had no range at all.

## Unresolved by design

The six ESA-ADB channels `channel_41` … `channel_46` are **not** in the
dictionary and must not be. They are anonymized by the source dataset; assigning
them a subsystem would attach a confident label to a channel nobody has
identified, and that label would propagate into diagnoses and audit records.
`resolve_channel()` returns `Subsystem.UNKNOWN` and
`Provenance.UNKNOWN` for them, with only the bounds the reading itself carried.
