# Phase 1 — Command Conflict Resolutions

This document records every command conflict found during Phase 1 and how each
one was resolved. It exists because Phase 1's rule was *"do not simply remove
commands because they fail validation"* — each conflict had to be diagnosed as
one of three things:

| Diagnosis | Action |
|---|---|
| The **whitelist was incomplete** | Register the command with full metadata |
| The **procedure was wrong** | Correct the citing source to a canonical `command_id` |
| The command **needs preconditions** | Register it with declared conditions |

## How the conflicts arose

Before Phase 1 there were two independent, hand-maintained lists of command
names: the `COMMAND_WHITELIST` literal in `app/agent/safety.py`, and whatever
`CMD_*` strings happened to appear in `app/agent/rag.py`'s procedure text, the
demo cache, and the training-data generator. Nothing compared them.

The result: **22 commands were recommended by a procedure, a training plan, or
the replayed demo cache while being absent from the whitelist.** A model that
followed a retrieved procedure exactly had those steps rejected. In the thermal
case the rejected command was the remedy for the fault being remediated.

`app/validation/command_registry.py` is now the single source of truth, and
`app/validation/conflicts.py` fails the build if any consumer drifts from it.

---

## Resolution A — the whitelist was incomplete (11 commands)

These are real capabilities the procedures legitimately need. Each is now a
registry entry under the exact name the procedure already used, so no procedure
text had to change.

| Command | Subsystem | Risk | Conditions | Why the whitelist was wrong |
|---|---|---|---|---|
| `CMD_VERIFY_SUN_ANGLE` | SYSTEM | LOW | none | Observation-only. Reads the sun sensor; cannot change spacecraft state. Blocking it served no safety purpose. |
| `CMD_VERIFY_MEMORY_STATE` | SYSTEM | LOW | none | Observation-only. The OBC watchdog procedure's post-reboot check. |
| `CMD_VERIFY_SIGNAL_ACQUISITION` | SYSTEM | LOW | none | Observation-only. Confirms SNR after a comms recovery action. |
| `CMD_VERIFY_THERMAL_MARGIN` | SYSTEM | LOW | none | Observation-only. Confirms a temperature returned to its safe range. |
| `CMD_DISABLE_HEATER_ZONE` | TCS | LOW | none | **The most dangerous conflict.** The thermal-runaway procedure says to disable the stuck heater *immediately, before other diagnostics*. The command did not exist in the whitelist, so the one action that stops the fault was rejected. Registered with **no thermal prohibition** — a remedy must never be blocked by the hazard it remedies. |
| `CMD_MONITOR_TEMPERATURE` | TCS | LOW | none | Observation-only. Watches for the cooling trend after the heater is disabled. |
| `CMD_SWITCH_BACKUP_TRANSPONDER` | COMMS | MEDIUM | prohibits `THERMAL_ABOVE_SURVIVAL` | Genuine redundancy capability. The whitelist had `CMD_ANTENNA_SWITCH` but no transponder switch, so the primary COMMS recovery action was unavailable. |
| `CMD_CONFIRM_COMMS_LOCK` | COMMS | LOW | none | Observation-only, and it is the **prerequisite** the OBC procedure requires before any reboot. The validator enforced "confirm comms lock before reboot" while rejecting the command that confirms it. |
| `CMD_CONFIRM_GROUND_CONTACT` | COMMS | LOW | none | Observation-only. Confirms the two-way link after a transponder switch. |
| `CMD_SOLAR_ARRAY_A_RESET` | EPS | LOW | prohibits `THERMAL_ABOVE_SURVIVAL` | The whitelist had the generic `CMD_SOLAR_PANEL_RESET` but not the array-specific name the procedure and the training data both use. |
| `CMD_SWITCH_SOLAR_ARRAY` | EPS | MEDIUM | prohibits `THERMAL_ABOVE_SURVIVAL` | Genuine redundancy capability with no whitelist entry. |

All eleven are recorded with `source = PROCEDURE_KB`, so the registry states
plainly that they entered the system through the procedure knowledge base rather
than the original whitelist.

### Note on the observation-only commands

Seven of the eleven are observation-only: they read telemetry and cannot
actuate anything. The old validator had a concept for this
(`_is_verify_command`), but it was implemented as
`command.startswith("CMD_VERIFY_")` — a command was treated as safe because of
how it was *spelled*. That is why `CMD_DISABLE_HEATER_ZONE` and
`CMD_MONITOR_TEMPERATURE` were subject to constraint checks they had no business
being subject to, and why `CMD_CONFIRM_COMMS_LOCK` would not have been exempt
even once registered.

It is now a property derived from declared metadata: a command with **both**
condition lists empty is unconditionally executable
(`CommandSpec.is_observation_only`).

---

## Resolution B — the procedure or demo data was wrong (11 commands)

For these, an equivalent capability already existed under a canonical name. The
invented name was **not** added to the registry — legitimising a made-up command
would defeat the purpose. The citing source was corrected instead.

| Invented name | Corrected to | Cited by | Reasoning |
|---|---|---|---|
| `CMD_BATTERY_CONSERVATION` | `CMD_POWER_SHED_NONESSENTIAL` | `rag.py` (EPS_SOLAR_UNDERVOLT) | Same action — shed non-critical loads. Two names for one capability is exactly the drift the registry prevents. |
| `CMD_SWITCH_TO_GYRO_B` | `CMD_GYRO_SWITCH_TO_BACKUP` | demo cache (gyro SEU) | Canonical name already registered. |
| `CMD_ATTITUDE_RECOVERY_SUN_POINT` | `CMD_SUN_ACQUISITION` | demo cache (gyro SEU) | Same action — slew to sun-pointing. |
| `CMD_ATTITUDE_SUN_POINT_SAFE` | `CMD_SUN_ACQUISITION` | demo cache (solar undervolt) | Third spelling of the same action. |
| `CMD_EPS_STATUS_REPORT` | `CMD_POWER_CHECK` | demo cache (solar undervolt) | Same action — read the EPS status summary. |
| `CMD_SHED_NON_ESSENTIAL_LOADS` | `CMD_POWER_SHED_NONESSENTIAL` | demo cache (solar undervolt) | Hyphenation variant of a registered command. |
| `CMD_SOLAR_ARRAY_RELAY_CYCLE` | `CMD_SOLAR_ARRAY_A_RESET` | demo cache (solar undervolt) | Same action — re-initialise the array drive electronics. |
| `CMD_OBC_MEMORY_DUMP` | `CMD_MEMORY_DUMP` | demo cache (OBC watchdog) | Prefix variant of a registered command. |
| `CMD_WATCHDOG_COUNTER_RESET` | `CMD_WATCHDOG_CLEAR` | demo cache (OBC watchdog) | Same action — clear the watchdog counter. |
| `CMD_RESTART_ATTITUDE_CONTROL_THREAD` | `CMD_VERIFY_MEMORY_STATE` | demo cache (OBC watchdog) | **No equivalent capability exists.** SENTINEL has no per-thread restart command, and inventing one would fabricate a capability. Replaced with the registry command that serves the procedure's actual intent at that step: confirm memory is stable after the reboot. |
| `CMD_OBC_HEALTH_MONITOR_ENABLE` | `CMD_HEALTH_CHECK` | demo cache (OBC watchdog) | No "enable monitoring" capability exists; the intent is a post-recovery health check. |

Ten of the eleven were in `data/demo_cache/`, which is **replayed to operators**.
Before Phase 1 that cache contained ten commands live safety would have
rejected — the replay showed an operator a plan the real system would refuse.
`generate_demo_cache.py` now runs the real validator before writing, and all
three cached payloads report `safety_status: VALIDATED` with zero blocked steps.

---

## Resolution C — commands needing added preconditions (0 new, 4 formalised)

No conflict required a *new* precondition. The four constraints that already
existed were moved from hardcoded sets in `safety.py` onto the commands
themselves, so a command's constraints now travel with its definition:

| Condition | Applied to | Previously |
|---|---|---|
| `GYRO_DATA_VALID` (required) | 5 attitude/wheel actuation commands | `_GYRO_DEPENDENT_COMMANDS` set |
| `COMMS_LOCK_CONFIRMED` (required) | `CMD_OBC_CONTROLLED_REBOOT`, `CMD_OBC_SOFT_RESET` | hardcoded tuple in the check |
| `BATTERY_BELOW_FLOOR` (prohibited) | 11 power-drawing commands | `_POWER_REQUIRING_COMMANDS` set |
| `THERMAL_ABOVE_SURVIVAL` (prohibited) | all actuating commands except thermal remedies | implicit "everything except this exemption list" |

The behaviour is unchanged for every pre-existing command. This was verified
differentially against the pre-Phase-1 validator over a matrix of
command × context combinations.

---

## Two further inconsistencies fixed in passing

**`CMD_VERIFY_SEU_COUNTER` was filed under two subsystems.** It appeared in both
the `ADCS` and `SYSTEM` whitelist groups, so `get_whitelist_status()` reported it
as a duplicate. The registry is keyed by `command_id`, so this class of problem
is now impossible by construction; the command is filed under `SYSTEM`.

**`infer_subsystem()` returned `None` for legitimate commands.** It worked purely
from name prefixes, so commands that do not follow a subsystem prefix
(`CMD_DISABLE_HEATER_ZONE`, `CMD_SWITCH_BACKUP_TRANSPONDER`,
`CMD_CONFIRM_COMMS_LOCK`, `CMD_SWITCH_SOLAR_ARRAY`, `CMD_MONITOR_TEMPERATURE`)
had no subsystem attribution in the operator's blocked-step list. It now consults
the registry's declared subsystem first and falls back to the prefix heuristic
only for unregistered commands.

---

## Verification

```bash
cd sentinel/backend

# The CI gate — exits non-zero on any conflict
python3 -m app.validation.conflicts

# Regression tests for all 22 conflicts
python3 -m unittest tests.test_phase1_registry

# Blocked-plan behaviour, including "an unsafe plan cannot look successful"
python3 -m unittest tests.test_phase1_blocked_plans

# The pre-existing safety suite still passes
python3 tests/test_safety.py
```

## What Phase 1 did NOT do

- No command was removed to make validation pass.
- No ECSS clause number is claimed for any command. The registry's
  `source_reference` states plainly that clause-level citations are unavailable;
  `CommandSource.PROCEDURE_KB` means "written from ECSS principles without
  attribution", which is what `rag.py` itself says.
- The 43 registry commands that no procedure currently cites were left in place
  and reported as a warning, not deleted. They are spare capability, and
  removing them would narrow what the system can propose for no safety gain.
