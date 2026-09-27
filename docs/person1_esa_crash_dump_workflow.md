# Person 1: ESA-ADB Crash Dump And Demo Plan

This is the Person 1 handoff for using `ESA-Mission1` in the SENTINEL hackathon
demo. The goal is to be very clear about what is real, what is labelled, what is
synthetic, and what the frontend should show.

## Short Answer

Use a hybrid demo.

Show one polished "Live Safe-Mode Stream" flow for the story:

1. Telemetry streams normally.
2. A labelled anomaly threshold trips.
3. The system freezes the last telemetry window.
4. A crash dump is generated.
5. The agent starts diagnosis.
6. The UI shows hypotheses, causal chain, recovery plan, and human-review flag.

Also keep 5-6 selectable incident presets for reliability:

- 3 synthetic safe-mode incidents with full root-cause and recovery labels.
- 2 real ESA-ADB telemetry incidents with real anomaly labels.
- 1 communication-gap or rare-event example to show dataset breadth.

This gives the judges both theatre and proof. The live stream makes the demo
feel like mission control. The presets make sure the demo never depends on
timing, loading, or network surprises.

## What Is Actually In ESA-Mission1

`ESA-Mission1` is real, anonymized spacecraft telemetry prepared for anomaly
detection. It is not a literal onboard crash dump. It is still very valuable
because it gives us real telemetry streams, real telecommand timestamps, and
curated anomaly labels.

Local counts from `sentinel/backend/data/esa_crash_dumps/mission1_summary.json`:

| Dataset Item | Count |
|---|---:|
| Channels | 76 |
| Target/evaluation channels | 58 |
| Telecommand metadata rows | 698 |
| Channel-level label rows | 3,589 |
| Unique labelled events | 200 |
| `Anomaly` events | 118 |
| `Rare Event` events | 78 |
| `Communication Gap` events | 4 |
| Multivariate events | 164 |
| Univariate events | 32 |
| Subsequence events | 184 |
| Point events | 12 |

## Actual Inputs From The Dataset

These are the real inputs we can read from `ESA-Mission1`.

### `channels.csv`

Channel metadata. This tells us what channels exist and how ESA grouped them.

| Column | Meaning | Example |
|---|---|---|
| `Channel` | Anonymized telemetry parameter name | `channel_41` |
| `Subsystem` | Anonymized subsystem bucket | `subsystem_5` |
| `Physical Unit` | Anonymized physical unit bucket | `physical_unit_4` |
| `Group` | Anonymized group ID | `8` |
| `Target` | Whether the channel is included in labelled evaluation | `YES` |

Important: `Subsystem` is anonymized. We cannot honestly say `subsystem_5` is
ADCS, EPS, TCS, etc. We can say "an anonymized spacecraft subsystem group".

### `channels/channel_*.zip`

Actual telemetry streams. Each zip contains one pickled pandas DataFrame.

Shape:

- Index: timestamp (`DatetimeIndex`)
- Column: one channel, for example `channel_41`
- Value: normalized float32 telemetry value

Example from `id_109`:

| Channel | Normal Baseline | Labelled Event Value | Direction |
|---|---:|---:|---|
| `channel_41` | about `0.812` | `0.960-0.962` | high spike |
| `channel_42` | about `0.785` | `0.0` | drop to zero |
| `channel_43` | about `0.772` | `0.933-0.934` | high spike |
| `channel_44` | about `0.798` | `0.947-0.949` | high spike |
| `channel_45` | about `0.814` | `0.977-0.979` | high spike |
| `channel_46` | about `0.769` | `0.949-0.952` | high spike |

### `telecommands.csv`

Telecommand metadata.

| Column | Meaning | Example |
|---|---|---|
| `Telecommand` | Anonymized command name | `telecommand_270` |
| `Priority` | Priority bucket | `0`, `1`, `2`, `3` |

### `telecommands/telecommand_*.zip`

Actual telecommand occurrence streams. Each zip contains one pickled pandas
DataFrame. The index is the timestamp when the telecommand occurred. Values are
usually `1`.

For `id_109`, the converter found 184 telecommand hits in the configured nearby
time window. The top repeated commands were:

| Telecommand | Count Near Event |
|---|---:|
| `telecommand_270` | 138 |
| `telecommand_198` | 32 |
| `telecommand_233` | 5 |

Important: telecommand names are anonymized. We can show temporal context, but
we cannot claim that `telecommand_270` means "reset gyro" or "switch heater".

## Actual Labels In The Dataset

There are two label files, and they answer different questions.

### `labels.csv`: Where And When The Event Happened

This is the most important ground truth file for checking real ESA telemetry.

| Column | Meaning | How We Use It |
|---|---|---|
| `ID` | Event ID shared across channels | Join key, e.g. `id_109` |
| `Channel` | Affected channel | Check if agent identified the right channel |
| `StartTime` | Start of labelled interval for that channel | Check time-window overlap |
| `EndTime` | End of labelled interval for that channel | Check time-window overlap |

One event ID can map to many channel rows. For example, `id_109` maps to:

```text
id_109 -> channel_41, channel_42, channel_43, channel_44, channel_45, channel_46
```

So for real ESA examples, the agent can be evaluated on:

- Did it identify the labelled channels?
- Did it identify the correct anomaly time window?
- Did it identify the direction of change?
- Did it avoid adding unsupported root-cause claims?

### `anomaly_types.csv`: What Kind Of Event It Was

This is event-level taxonomy.

| Column | Meaning | Values Present In Mission1 |
|---|---|---|
| `ID` | Event ID | `id_1` to `id_200` |
| `Class` | Anonymized anomaly class | `class_1` to `class_22` buckets |
| `Subclass` | Anonymized subclass | `subclass_1`, `subclass_2`, etc., or `unknown` |
| `Category` | High-level event category | `Anomaly`, `Rare Event`, `Communication Gap` |
| `Dimensionality` | Number of channels involved | `Univariate`, `Multivariate` |
| `Locality` | Scope of the event | `Local`, `Global` |
| `Length` | Time shape | `Point`, `Subsequence` |

Mission1 category counts:

| Category | Count | How To Present It |
|---|---:|---|
| `Anomaly` | 118 | Real off-nominal telemetry event |
| `Rare Event` | 78 | Rare but not necessarily failure |
| `Communication Gap` | 4 | Missing/downlink-gap interval |

Mission1 event-shape labels:

| Label Field | Values |
|---|---|
| `Dimensionality` | 164 `Multivariate`, 32 `Univariate`, 4 blank communication gaps |
| `Locality` | 113 `Global`, 83 `Local`, 4 blank communication gaps |
| `Length` | 184 `Subsequence`, 12 `Point`, 4 blank communication gaps |

## Mapping: Dataset Labels To Agent Evaluation

This is the exact mapping we should use.

| Dataset Field | Becomes In Our Crash Dump | Can We Evaluate Agent Against It? |
|---|---|---|
| `labels.ID` | `operating_context.label_id`, `fault_register` string | Yes |
| `labels.Channel` | `pre_fault_telemetry[].parameter` | Yes |
| `labels.StartTime/EndTime` | frozen event window | Yes |
| `anomaly_types.Category` | `fault_type = ESA_ADB_ANOMALY`, etc. | Yes |
| `anomaly_types.Class` | `fault_register` and rich audit metadata | Yes |
| `anomaly_types.Subclass` | `fault_register` and rich audit metadata | Yes |
| `channels.Subsystem` | anonymized subsystem metadata | Partially |
| `telecommands.Telecommand` | event log context | Partially |
| Real root cause | not provided | No |
| Real safe-mode state | not provided | No |
| Real recovery command sequence | not provided | No |

The honest pitch line:

```text
ESA-ADB gives us real spacecraft telemetry and anomaly labels. SENTINEL turns
that telemetry into an agent-readable crash-dump proxy. For recovery-plan
supervision, we use synthetic scenarios because ESA-ADB does not expose real
root-cause or recovery-command labels.
```

## Recommended Frontend Demo

Use three frontend modes.

### Mode 1: Live Safe-Mode Stream

This should be the first thing judges see.

UI behavior:

1. Show a telemetry chart or matrix streaming nominal values.
2. Show status: `NOMINAL`.
3. Inject or replay a labelled anomaly.
4. Highlight anomalous channels.
5. Show status transition: `NOMINAL -> ALERT -> SAFE_MODE`.
6. Freeze the last window.
7. Show "Crash dump generated".
8. Call the agent.
9. Stream reasoning logs.
10. Show diagnosis, causal chain, recovery plan, and safety gate.

This is mostly presentation theatre, but it is good theatre because it matches
how operators think: continuous telemetry becomes an incident packet.

### Mode 2: Incident Library

This is the reliable judge-controlled path.

Recommended dropdown options:

| Preset | Source | Why It Exists |
|---|---|---|
| `Synthetic: ADCS Gyro SEU Safe Mode` | synthetic | Full root cause and recovery labels |
| `Synthetic: EPS Undervoltage Safe Mode` | synthetic | Strong causal cascade |
| `Synthetic: OBC Watchdog Reset` | synthetic | Easy software-fault story |
| `Synthetic: TCS Thermal Runaway` | synthetic | Shows non-power/non-ADCS coverage |
| `Real ESA: id_109 Multivariate Point Anomaly` | ESA-ADB | Real telemetry, strong visible spike/drop |
| `Real ESA: Communication Gap` | ESA-ADB | Shows downlink/data-gap case |

The dropdown should make source type obvious:

- `Synthetic Safe Mode`
- `Real ESA Telemetry`
- `Real ESA Communication Gap`

This prevents judges from thinking every sample has the same kind of ground
truth.

### Mode 3: Upload Crash Dump JSON

Keep the current custom JSON paste/upload option. This is useful for judging
questions:

- "Can it analyze another dump?"
- "Can it handle different schemas?"
- "Where is the actual input?"

Use the compact ESA payload:

```text
sentinel/backend/data/esa_crash_dumps/esa_mission1_id_109_sentinel_only.json
```

## What The Frontend Should Display

### Left Panel: Telemetry Source

Show:

- Source badge: `Synthetic Safe Mode` or `Real ESA Telemetry`.
- Event ID: `id_109` when real ESA data is selected.
- Label category: `Anomaly`, `Rare Event`, or `Communication Gap`.
- Label class/subclass: for example `class_3 / subclass_2`.
- Channels involved.
- Safe-mode truth state:
  - Synthetic: `Known safe-mode scenario`
  - ESA: `Safe mode not provided by dataset`

### Telemetry Matrix Or Chart

For ESA `id_109`, show six cards or six lines:

| Channel | Status |
|---|---|
| `channel_41` | spike high |
| `channel_42` | drop to zero |
| `channel_43` | spike high |
| `channel_44` | spike high |
| `channel_45` | spike high |
| `channel_46` | spike high |

Use the z-score or range breach to highlight anomalies. The strongest number is
`channel_42`, which drops to `0.0` and has max sample z-score around `112.9`.

### Crash Dump Panel

Show the generated packet:

```json
{
  "scenario_id": 109,
  "fault_type": "ESA_ADB_ANOMALY",
  "fault_register": "ESA_LABEL:id_109;CLASS:class_3;SUBCLASS:subclass_2",
  "pre_fault_telemetry": "...",
  "event_log": "...",
  "hardware_state": "NOT_PROVIDED_BY_ESA_ADB",
  "operating_context": {
    "safe_mode_state": "not_provided_by_dataset"
  }
}
```

Do not hide the `NOT_PROVIDED_BY_ESA_ADB` fields. They make the demo more
credible because you are not pretending ESA gave labels that it did not give.

### Agent Output Panel

For synthetic incidents, allow confident recovery plans.

For real ESA incidents, require cautious language:

- "Detected real telemetry anomaly across these channels."
- "Likely affected anonymized subsystem group."
- "Root cause cannot be confirmed from ESA-ADB labels alone."
- "Recovery commands require human review."

### Evaluation Panel

This is the panel that makes Person 1 look strong.

Show a simple checklist:

| Check | Synthetic | Real ESA |
|---|---|---|
| Affected channels found | optional | yes |
| Correct anomaly category | optional | yes |
| Correct class/subclass copied | optional | yes |
| Correct root cause | yes | not available |
| Correct recovery plan | yes | not available |
| Human review flag | yes | yes |

## Best Hackathon Presentation Flow

Use this exact story:

1. "Satellites do not send us a friendly root-cause paragraph. They send
   telemetry, event flags, counters, and sometimes fault registers."
2. "Here is a real ESA telemetry event. The dataset labels tell us which
   channels went anomalous and when."
3. "At the safe-mode boundary, SENTINEL freezes the last telemetry window and
   creates this crash-dump packet."
4. "The agent does not read 76 raw streams directly. Person 1 compresses the
   event into the relevant telemetry, telecommands, label metadata, and baseline
   deviations."
5. "For real ESA data, we evaluate detection and triage correctness."
6. "For root-cause and recovery-plan correctness, we use synthetic safe-mode
   scenarios where ground truth is known."
7. "The final system combines both: real telemetry realism plus supervised
   recovery reasoning."

## What Should Work

For the demo, these should work end to end:

- Select a synthetic safe-mode preset.
- Run agent.
- Show confident root cause and recovery plan.
- Select `Real ESA: id_109`.
- Show the real telemetry spike/drop.
- Generate or load compact crash dump.
- Run agent.
- Show anomaly triage and human-review-required recovery output.
- Show label/audit panel proving which channels and label class came from ESA.

## What Should Not Be Claimed

Do not say:

- "ESA gives us crash dumps."
- "ESA labels are root causes."
- "`class_3` means gyro fault."
- "`telecommand_270` means reset command."
- "This event definitely entered safe mode."
- "The recovery plan for ESA `id_109` is ground-truth verified."

Say instead:

- "ESA gives real anonymized telemetry and curated anomaly labels."
- "We create a crash-dump proxy from the frozen telemetry window."
- "Synthetic data provides full root-cause and recovery-plan ground truth."
- "Real ESA data validates anomaly triage; synthetic data validates recovery
  reasoning."

## Generated Files

Summary:

```text
sentinel/backend/data/esa_crash_dumps/mission1_summary.json
```

Full audit crash dump:

```text
sentinel/backend/data/esa_crash_dumps/esa_mission1_id_109_crash_dump.json
```

Compact agent payload:

```text
sentinel/backend/data/esa_crash_dumps/esa_mission1_id_109_sentinel_only.json
```

Converter script:

```text
sentinel/backend/data_tools/esa_adb_crash_dump.py
```

## Commands

Summarize labels and metadata:

```bash
/Users/nitishbiswas/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 sentinel/backend/data_tools/esa_adb_crash_dump.py summary --dataset ESA-Mission1 --output sentinel/backend/data/esa_crash_dumps/mission1_summary.json
```

Build full and compact crash-dump payloads:

```bash
/Users/nitishbiswas/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 sentinel/backend/data_tools/esa_adb_crash_dump.py build --dataset ESA-Mission1 --event-id id_109 --output sentinel/backend/data/esa_crash_dumps/esa_mission1_id_109_crash_dump.json --compact-output sentinel/backend/data/esa_crash_dumps/esa_mission1_id_109_sentinel_only.json
```

Check extraction size without unpacking:

```bash
/Users/nitishbiswas/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 sentinel/backend/data_tools/esa_adb_crash_dump.py extract --dataset ESA-Mission1 --output ESA-Mission1_extracted --dry-run --kind channels --limit 3
```

Do not fully extract Mission1 unless needed. It expands to about 9 GB, and the
extracted files are still pickled pandas DataFrames.

## Person 1 Ownership

Person 1 owns:

- Dataset summary.
- Crash-dump conversion.
- Label mapping.
- Real vs synthetic explanation.
- Evaluation rules.
- Frontend sample incident list.

Person 1 does not need to own:

- Final LLM reasoning quality.
- ECSS RAG implementation.
- Recovery command whitelist.
- Dashboard styling.

## Sources

- ESA Anomaly Dataset Zenodo record: https://doi.org/10.5281/zenodo.12528696
- ESA-ADB GitHub repository: https://github.com/kplabs-pl/ESA-ADB
- ESA-ADB paper: https://doi.org/10.48550/arXiv.2406.17826
