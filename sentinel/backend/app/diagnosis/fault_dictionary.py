"""
SENTINEL — Fault Dictionary (app/diagnosis/fault_dictionary.py)

Phase 6. Machine-readable fault definitions, replacing the English prose that
lived in ``app/agent/prompts.py``.

What was there before
---------------------
``FAULT_SIGNATURES`` — six faults described in sentences:

    "1. ADCS_GYRO_SEU — Single Event Upset in gyroscope processor:
        Signature: sudden SEU_COUNTER spike + GYRO_A_RATE becomes NaN
        Causal chain: SEU hit -> gyro data invalid -> ... -> safe mode
        Key rule: check SEU_COUNTER first."

Readable, and completely inert. Nothing could match a signature against telemetry,
score a candidate, or notice that a proposed fault contradicted the evidence. The
only consumer was the LLM, which meant the fault knowledge and the fault judgement
were the same black box.

Each fault is now a ``FaultDefinition`` whose signatures are predicates over
detector output, so a candidate can be generated, scored and argued against.

Signature vocabulary
--------------------
Every ``ConditionKind`` is answerable from an ``AnomalyReport`` alone. Nothing
requires a physics model, and nothing requires the LLM. That constraint is
deliberate: a predicate this layer cannot evaluate is a predicate that would
quietly always pass.

Provenance
----------
The six original faults are carried over from the prose in prompts.py, and the
prose's channel names and causal chains are preserved. The AOCS differential set
(reaction-wheel degradation, gyro bias, control-command anomaly, external
disturbance, sensor fault) is NEW in Phase 6, authored to give an attitude anomaly
competing explanations rather than a single answer.

``provenance`` records which is which. None of it is sourced from a vehicle FMECA,
and none of it claims to be: these are SENTINEL working definitions expressed over
the channels this repository actually carries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

FAULT_DICT_VERSION = "1.0.0"
"""Version of the fault dictionary.

Bump MAJOR when a fault_id is removed or renamed, since stored hypotheses
reference it. MINOR for a new fault or signature, PATCH for text.
"""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — VOCABULARIES
# ═══════════════════════════════════════════════════════════════════════════

class ConditionKind(str, Enum):
    """A predicate over one channel's detector evidence.

    Each is decidable from an ``AnomalyReport``. Where the report cannot answer —
    no reading for the channel — the predicate evaluates to UNKNOWN rather than
    to False, so a missing channel never counts as evidence against a fault. That
    distinction matters: absence of a reading is not absence of a symptom.
    """

    ANOMALOUS = "ANOMALOUS"
    """The channel was flagged by any detector."""

    NOMINAL = "NOMINAL"
    """The channel was examined and NOT flagged. Useful for contradictions."""

    ABOVE_LIMIT = "ABOVE_LIMIT"
    """A hard-limit violation on the upper bound."""

    BELOW_LIMIT = "BELOW_LIMIT"
    """A hard-limit violation on the lower bound."""

    DATA_INVALID = "DATA_INVALID"
    """The reading is unusable — NaN, Inf or a dropout."""

    COUNTER_INCREMENTED = "COUNTER_INCREMENTED"
    """A counter rose above its expected value."""

    DISCRETE_VIOLATION = "DISCRETE_VIOLATION"
    """A status or flag channel left its expected state set."""

    RISING = "RISING"
    """A sustained upward trend."""

    FALLING = "FALLING"
    """A sustained downward trend."""

    SUDDEN_CHANGE = "SUDDEN_CHANGE"
    """A step change or a rate-of-change exceedance."""

    PERSISTENT = "PERSISTENT"
    """The anomaly held across consecutive samples rather than being a spike."""


class ContextConditionKind(str, Enum):
    """A predicate over recorded CONTEXT facts, outside the telemetry channels.

    Phase 6, added while completing requirement B. ``supporting_conditions`` and
    ``contradicting_conditions`` started as free text shown to the operator, which
    left the fault dictionary's two most important rules unenforceable:

        EPS: "if orbital_position is sunlit and I_sa is about 0, this is NOT an
              eclipse, it is a solar array fault"
        ADCS: "check SEU_COUNTER first; if it spiked this is radiation-induced"

    Measured consequence of leaving them as text: on preset scenario 2 a total
    array failure reads I_sa = 0.0 A, which is INSIDE the channel's hard limits
    of (0.0, 12.0) because zero current is legal in eclipse. So the array-fault
    hypothesis lost its discriminating evidence, EPS_BATTERY_DEGRADATION matched
    "I_sa NOMINAL" instead and outranked it — while the dump plainly recorded
    ``eclipse_fraction: 0.0`` and ``solar_relay: "open"``.

    These predicates read facts the dump already contains. They are deterministic
    and auditable; nothing is inferred and no model is consulted.

    Deliberately NOT included: a "prolonged loss of contact" predicate. Both dump
    formats record a contact gap (``time_since_contact_s``,
    ``minutes_since_last_ground_contact``), but neither records the scheduled pass
    interval to compare it against, and no module in this repository defines one.
    Deciding the predicate would mean inventing an engineering constant, and a
    predicate that cannot be decided is one that quietly always passes.
    """

    SPACECRAFT_SUNLIT = "SPACECRAFT_SUNLIT"
    """Illuminated, so zero array current cannot be explained by eclipse."""

    SPACECRAFT_IN_ECLIPSE = "SPACECRAFT_IN_ECLIPSE"
    """In eclipse, where low array current is expected and not a fault."""

    SEU_EVENT_RECORDED = "SEU_EVENT_RECORDED"
    """The vehicle itself recorded a radiation event, in hardware_state."""

    NO_SEU_EVENT_RECORDED = "NO_SEU_EVENT_RECORDED"
    """Hardware state was reported and records no radiation event."""

    RESET_CAUSE_UNDERVOLTAGE = "RESET_CAUSE_UNDERVOLTAGE"
    """The last reset was attributed to undervoltage, implicating power."""

    RESET_CAUSE_WATCHDOG = "RESET_CAUSE_WATCHDOG"
    """The last reset was attributed to the watchdog, implicating software."""

    SOLAR_RELAY_OPEN = "SOLAR_RELAY_OPEN"
    """The array relay is open, which disconnects generation directly."""

    RECENT_TELECOMMAND_BURST = "RECENT_TELECOMMAND_BURST"
    """Command interval classified as a burst, so a commanding fault is in play."""


#: Context conditions that cannot both hold. Validated, so a fault cannot score
#: a fact and its negation as positive evidence and collect credit either way.
#: Contradicting pairs ARE legitimate: "sunlit supports me, eclipsed argues
#: against me" is a coherent pair of claims about the same fact.
_MUTUALLY_EXCLUSIVE_CONTEXT: tuple[tuple["ContextConditionKind", ...], ...] = (
    (ContextConditionKind.SPACECRAFT_SUNLIT,
     ContextConditionKind.SPACECRAFT_IN_ECLIPSE),
    (ContextConditionKind.SEU_EVENT_RECORDED,
     ContextConditionKind.NO_SEU_EVENT_RECORDED),
    (ContextConditionKind.RESET_CAUSE_UNDERVOLTAGE,
     ContextConditionKind.RESET_CAUSE_WATCHDOG),
)


class SignatureRole(str, Enum):
    """What a signature contributes to a candidate."""

    REQUIRED = "REQUIRED"
    """Necessary. If this is demonstrably absent, the fault is not a candidate.

    UNKNOWN does not eliminate: a channel the dump never reported cannot be used
    to rule a fault out. Only a definite NOMINAL or an unmet predicate does.
    """

    SUPPORTING = "SUPPORTING"
    """Raises the score when present. Absence lowers it but does not eliminate."""

    DISCRIMINATING = "DISCRIMINATING"
    """Distinguishes this fault from its near neighbours. Weighted highest,
    because it is the evidence that actually separates competing candidates."""

    CONTRADICTING = "CONTRADICTING"
    """If present, this evidence argues AGAINST the fault. Reported to the
    operator either way — a hypothesis with unstated counter-evidence is an
    assertion, not a diagnosis."""


#: Weight per role. DISCRIMINATING outweighs SUPPORTING because differential
#: diagnosis turns on the evidence that separates candidates, not on the evidence
#: they share. A contradiction costs more than a support is worth, so a candidate
#: cannot be argued into first place by piling up generic symptoms.
ROLE_WEIGHT: dict[SignatureRole, float] = {
    SignatureRole.REQUIRED: 3.0,
    SignatureRole.DISCRIMINATING: 2.5,
    SignatureRole.SUPPORTING: 1.0,
    SignatureRole.CONTRADICTING: -3.0,
}


class FaultSeverity(str, Enum):
    """Consequence if the fault is real and untreated."""

    CATASTROPHIC = "CATASTROPHIC"   # loss of vehicle or of contact
    CRITICAL = "CRITICAL"           # loss of mission capability
    MAJOR = "MAJOR"                 # degraded capability
    MINOR = "MINOR"                 # nuisance or housekeeping


class FaultProvenance(str, Enum):
    """Where a fault definition came from."""

    PROMPT_PROSE = "PROMPT_PROSE"
    """Carried over from the FAULT_SIGNATURES prose in agent/prompts.py, whose
    channel names and causal chain are preserved."""

    SENTINEL_DIFFERENTIAL = "SENTINEL_DIFFERENTIAL"
    """Authored in Phase 6 so an anomaly gets competing explanations. A SENTINEL
    working definition, not a vehicle failure-mode analysis."""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Signature:
    """One predicate over one channel, with its role in the diagnosis."""

    channel: str
    condition: ConditionKind
    role: SignatureRole = SignatureRole.SUPPORTING
    rationale: str = ""
    """Why this evidence bears on this fault. Surfaced to the operator, so it has
    to say something a reviewer can disagree with."""

    @property
    def weight(self) -> float:
        return ROLE_WEIGHT[self.role]

    @property
    def key(self) -> str:
        return f"{self.channel}:{self.condition.value}"


@dataclass(frozen=True)
class ContextSignature:
    """One predicate over recorded context, with its role in the diagnosis.

    Scored exactly like a channel ``Signature`` and weighted by the same
    ``ROLE_WEIGHT`` table, so a discriminating context fact carries the same
    authority as a discriminating measurement. It has to: on preset scenario 2
    the recorded ``solar_relay: "open"`` is stronger evidence of an array fault
    than any single reading in the telemetry window.

    Deliberately NOT a channel predicate. These facts have no channel id, no
    sampling rate and no limits, so putting them in the channel dictionary would
    misrepresent them as telemetry.
    """

    condition: ContextConditionKind
    role: SignatureRole = SignatureRole.SUPPORTING
    rationale: str = ""

    @property
    def weight(self) -> float:
        return ROLE_WEIGHT[self.role]

    @property
    def key(self) -> str:
        return f"context:{self.condition.value}"


@dataclass(frozen=True)
class FaultDefinition:
    """A machine-readable fault, as the Phase 6 specification requires."""

    fault_id: str
    fault_name: str
    subsystem: str
    affected_channels: tuple[str, ...]
    expected_signatures: tuple[Signature, ...]
    supporting_conditions: tuple[str, ...]
    """Contextual facts outside telemetry that raise suspicion, e.g. "spacecraft
    is sunlit", in operator-facing prose.

    Where such a fact is recorded in the dump in a machine-readable form, it is
    ALSO expressed as a ``ContextSignature`` in ``context_signatures`` and scored.
    The prose remains because it covers cases the dump does not record — vehicle
    history, ground schedules, prior maintenance — which SENTINEL can show an
    operator but cannot evaluate."""
    contradicting_conditions: tuple[str, ...]
    """Contextual facts that argue against the fault, in operator-facing prose.
    Same relationship to ``context_signatures``: recorded facts are scored, the
    rest is shown. An operator who knows the vehicle is in eclipse can dismiss a
    solar array fault faster than any signature can."""
    severity: FaultSeverity
    possible_causes: tuple[str, ...]
    recovery_procedure_ids: tuple[str, ...]
    """Command IDs from the Phase 1 registry. Validated, so a fault cannot point
    at a command the safety validator would refuse to recognise."""

    description: str
    causal_chain: tuple[str, ...]
    provenance: FaultProvenance
    notes: Optional[str] = None

    context_signatures: tuple[ContextSignature, ...] = ()
    """Scored predicates over recorded context. Empty for a fault whose context
    conditions are not recorded anywhere in the dump formats — see the note in
    ``ContextConditionKind`` about not inventing decidable-looking predicates."""

    # ── derived ────────────────────────────────────────────────────────────

    @property
    def required_signatures(self) -> tuple[Signature, ...]:
        return tuple(s for s in self.expected_signatures
                     if s.role is SignatureRole.REQUIRED)

    @property
    def contradicting_signatures(self) -> tuple[Signature, ...]:
        return tuple(s for s in self.expected_signatures
                     if s.role is SignatureRole.CONTRADICTING)

    @property
    def positive_signatures(self) -> tuple[Signature, ...]:
        """Channel signatures that count towards the score when matched."""
        return tuple(s for s in self.expected_signatures
                     if s.role is not SignatureRole.CONTRADICTING)

    @property
    def positive_context_signatures(self) -> tuple[ContextSignature, ...]:
        """Context signatures that count towards the score when matched."""
        return tuple(s for s in self.context_signatures
                     if s.role is not SignatureRole.CONTRADICTING)

    @property
    def discriminating_signatures(self) -> tuple[object, ...]:
        """Every DISCRIMINATING predicate, channel and context alike.

        Used for the specificity term. Context has to be included or a fault
        whose distinguishing evidence is contextual — EPS_SOLAR_UNDERVOLT, whose
        separator is "sunlit" — would score zero specificity while holding the
        one fact that actually separates it.
        """
        return tuple(
            s for s in (*self.expected_signatures, *self.context_signatures)
            if s.role is SignatureRole.DISCRIMINATING
        )

    @property
    def max_positive_weight(self) -> float:
        """Total weight available, used to normalise the score to [0, 1].

        Includes context weight, so adding a context signature to a fault cannot
        inflate its normalised score just by adding more available evidence.
        """
        total = (sum(s.weight for s in self.positive_signatures)
                 + sum(s.weight for s in self.positive_context_signatures))
        return total or 1.0


def _sig(channel: str, condition: ConditionKind, role: SignatureRole,
         rationale: str) -> Signature:
    return Signature(channel=channel, condition=condition, role=role,
                     rationale=rationale)


def _ctx(condition: ContextConditionKind, role: SignatureRole,
         rationale: str) -> ContextSignature:
    return ContextSignature(condition=condition, role=role, rationale=rationale)


# Shorthand for readability in the table below.
_REQ = SignatureRole.REQUIRED
_DIS = SignatureRole.DISCRIMINATING
_SUP = SignatureRole.SUPPORTING
_CON = SignatureRole.CONTRADICTING


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — THE FAULTS
# ═══════════════════════════════════════════════════════════════════════════
#
# Channel names are canonical ids from app/ingest/channel_dict.py, validated at
# import. Alias spellings from the prose ("GYRO_A_RATE", "TEMP_OBC") resolve
# through the channel dictionary, so the prose's vocabulary still works.
#
# A note on the temperature and comms channel lists: the shipped preset scenarios
# and the fault simulator flag DIFFERENT channels for the same fault. Preset 5
# (thermal runaway) flags Panel_temp_C, Battery_temp_C, OBC_temp_C and
# Radiator_eff_pct, while the simulator flags Component_temp_C and
# Heater_power_W. Preset 6 flags Link_status, RF_power_dBm, Link_margin_dB and
# Bit_error_rate, while the simulator flags Transponder_lock and SNR_dB. Both are
# legitimate manifestations, so the signatures cover both rather than matching
# only the source that happened to be checked first.

_DEFINITIONS: tuple[FaultDefinition, ...] = (

    # ── 1. ADCS_GYRO_SEU (from prompts.py prose) ─────────────────────────
    FaultDefinition(
        fault_id="ADCS_GYRO_SEU",
        fault_name="Gyroscope single-event upset",
        subsystem="AOCS",
        affected_channels=("SEU_counter", "Gyro_rate_degs",
                           "Attitude_error_deg", "Star_tracker_status"),
        expected_signatures=(
            _sig("SEU_counter", ConditionKind.COUNTER_INCREMENTED, _DIS,
                 "A radiation hit registered on the processor. This is what "
                 "separates an upset from mechanical degradation, and it is why "
                 "the remedy is a software reset rather than a unit swap."),
            _sig("Gyro_rate_degs", ConditionKind.DATA_INVALID, _DIS,
                 "Rate data unusable rather than merely out of range. An upset "
                 "corrupts the reading; a bias or a bearing fault produces a "
                 "wrong but valid number."),
            _sig("Gyro_rate_degs", ConditionKind.ANOMALOUS, _SUP,
                 "The gyro channel is implicated at all."),
            _sig("Attitude_error_deg", ConditionKind.ANOMALOUS, _SUP,
                 "Attitude knowledge degraded once the rate data was lost. A "
                 "consequence, not the cause."),
            _sig("SEU_counter", ConditionKind.NOMINAL, _CON,
                 "No radiation event was recorded, so an upset is a weaker "
                 "explanation than a fault in the gyro itself."),
        ),
        supporting_conditions=(
            "Spacecraft passing through the South Atlantic Anomaly or a "
            "high-radiation region",
            "seu_flags set in hardware_state",
        ),
        contradicting_conditions=(
            "Gyro has a prior history of mechanical degradation",
            "Rate data is wrong but internally consistent, which points to bias "
            "rather than corruption",
        ),
        severity=FaultSeverity.CRITICAL,
        possible_causes=(
            "Cosmic ray or trapped-particle strike on the gyro processor",
            "Single-bit memory corruption in the ADCS control loop",
        ),
        recovery_procedure_ids=(
            "CMD_SEU_CHECK", "CMD_GYRO_A_DRIVER_RESET", "CMD_VERIFY_GYRO_RATE",
            "CMD_ATTITUDE_REACQUISITION",
        ),
        description=(
            "A radiation-induced upset in the gyroscope processor corrupts rate "
            "data, so the attitude estimate degrades and FDIR commands safe mode."
        ),
        causal_chain=(
            "SEU strike registered on the gyro processor",
            "Gyro rate output becomes invalid",
            "ADCS loses attitude knowledge",
            "Attitude error grows past its limit",
            "FDIR commands safe-mode entry",
        ),
        provenance=FaultProvenance.PROMPT_PROSE,
        notes=(
            "The prose's key rule — check SEU_COUNTER first — is now the "
            "DISCRIMINATING signature rather than an instruction to the model. "
            "It is enforced twice over: on the SEU_counter channel, and on the "
            "vehicle's own hardware_state SEU record, because a dump can carry "
            "the second without the first."
        ),
        context_signatures=(
            _ctx(ContextConditionKind.SEU_EVENT_RECORDED, _DIS,
                 "The vehicle itself logged a radiation event in hardware_state. "
                 "Independent of the SEU_counter channel, and present in dumps "
                 "that carry no counter reading at all."),
            _ctx(ContextConditionKind.NO_SEU_EVENT_RECORDED, _CON,
                 "Hardware state was reported and records no radiation event, so "
                 "a fault in the gyro itself explains the data better than an "
                 "upset does."),
        ),
    ),

    # ── 2. EPS_SOLAR_UNDERVOLT (from prompts.py prose) ───────────────────
    FaultDefinition(
        fault_id="EPS_SOLAR_UNDERVOLT",
        fault_name="Solar array power loss",
        subsystem="EPS",
        affected_channels=("I_sa", "V_bat", "V_bus", "SoC_pct",
                           "Panel_temp_C"),
        expected_signatures=(
            _sig("I_sa", ConditionKind.ANOMALOUS, _DIS,
                 "Generation is the origin of this fault. Without an array "
                 "current anomaly, a falling battery is a load or a battery "
                 "problem instead."),
            _sig("V_bat", ConditionKind.ANOMALOUS, _SUP,
                 "The battery is carrying a load the array is not covering."),
            _sig("SoC_pct", ConditionKind.ANOMALOUS, _SUP,
                 "Stored energy is being consumed faster than it is replaced."),
            _sig("V_bus", ConditionKind.ANOMALOUS, _SUP,
                 "The regulated bus has fallen out of range, which is what trips "
                 "EPS FDIR."),
            _sig("V_bat", ConditionKind.FALLING, _SUP,
                 "A sustained decline rather than a single low reading."),
            _sig("I_sa", ConditionKind.NOMINAL, _CON,
                 "Array current is healthy, so generation is not the problem."),
        ),
        supporting_conditions=(
            "operating_context reports the spacecraft is sunlit",
            "eclipse_fraction is 0",
            "Sun sensor angle indicates the array should be illuminated",
        ),
        contradicting_conditions=(
            "Spacecraft is in eclipse, where zero array current is expected",
            "Array was intentionally stowed or off-pointed",
        ),
        severity=FaultSeverity.CRITICAL,
        possible_causes=(
            "Solar array string or panel failure",
            "Array drive or deployment mechanism fault",
            "Power regulator or MPPT failure",
            "Array off-pointing caused by an attitude fault upstream",
        ),
        recovery_procedure_ids=(
            "CMD_SOLAR_ARRAY_CHECK", "CMD_SOLAR_ARRAY_A_RESET",
            "CMD_BUS_VOLTAGE_VERIFY", "CMD_BATTERY_CHECK",
        ),
        description=(
            "The solar array stops delivering current while illuminated, so the "
            "battery discharges until the bus falls out of range."
        ),
        causal_chain=(
            "Array current drops while the spacecraft is sunlit",
            "Battery supplies the whole load",
            "Battery voltage and state of charge fall",
            "Regulated bus voltage leaves its range",
            "EPS FDIR commands safe-mode entry",
        ),
        provenance=FaultProvenance.PROMPT_PROSE,
        notes=(
            "Eclipse cannot be ruled out from the TELEMETRY alone — 0 A is a "
            "legitimate reading in eclipse, and it sits inside the channel's "
            "hard limits of (0.0, 12.0) for exactly that reason, so the limits "
            "detector will not flag it. What rules eclipse out is the recorded "
            "context, and that is now scored rather than merely displayed: "
            "before context signatures existed this fault lost its discriminating "
            "evidence on a total array failure and was outranked by "
            "EPS_BATTERY_DEGRADATION, while the same dump plainly recorded "
            "eclipse_fraction 0.0 and solar_relay open."
        ),
        context_signatures=(
            _ctx(ContextConditionKind.SPACECRAFT_SUNLIT, _DIS,
                 "The vehicle is illuminated, so a collapse in array current "
                 "cannot be explained by eclipse. This is the prose's key rule, "
                 "and it is what separates an array fault from a battery or load "
                 "fault when the array current itself reads a legal zero."),
            _ctx(ContextConditionKind.SOLAR_RELAY_OPEN, _DIS,
                 "The array relay is recorded open, which disconnects generation "
                 "directly. A more specific finding than low current, and it "
                 "points at the mechanism rather than the symptom."),
            _ctx(ContextConditionKind.RESET_CAUSE_UNDERVOLTAGE, _SUP,
                 "The last reset was attributed to undervoltage, which places "
                 "the origin in power rather than downstream of it."),
            _ctx(ContextConditionKind.SPACECRAFT_IN_ECLIPSE, _CON,
                 "The vehicle is eclipsed, where low array current is the "
                 "expected reading and not a fault."),
        ),
    ),

    # ── 3. OBC_WATCHDOG_OVERFLOW (from prompts.py prose) ─────────────────
    FaultDefinition(
        fault_id="OBC_WATCHDOG_OVERFLOW",
        fault_name="Software watchdog overflow",
        subsystem="OBC",
        affected_channels=("CPU_load_pct", "Memory_usage_MB",
                           "Watchdog_counter", "OBC_temp_C"),
        expected_signatures=(
            _sig("CPU_load_pct", ConditionKind.ANOMALOUS, _DIS,
                 "Processor saturation is the mechanism. A watchdog overflow "
                 "without it points at a hung peripheral rather than a runaway "
                 "task."),
            _sig("Watchdog_counter", ConditionKind.ANOMALOUS, _DIS,
                 "The watchdog was not serviced, which is the direct evidence of "
                 "flight software failing to run."),
            _sig("Memory_usage_MB", ConditionKind.RISING, _SUP,
                 "A monotonic climb indicates a leak rather than a transient "
                 "load spike."),
            _sig("Memory_usage_MB", ConditionKind.ANOMALOUS, _SUP,
                 "Memory is implicated."),
            _sig("CPU_load_pct", ConditionKind.PERSISTENT, _SUP,
                 "Saturation held across samples, so it is not a scheduling "
                 "spike."),
            _sig("CPU_load_pct", ConditionKind.NOMINAL, _CON,
                 "The processor was not saturated, so a runaway task is a poor "
                 "explanation."),
        ),
        supporting_conditions=(
            "watchdog_status in hardware_state reports a trip",
            "A software patch or reconfiguration was uplinked recently",
        ),
        contradicting_conditions=(
            "CPU load nominal throughout the window",
            "Reset was commanded from the ground rather than autonomous",
        ),
        severity=FaultSeverity.CRITICAL,
        possible_causes=(
            "Infinite loop or unbounded recursion in flight software",
            "Memory leak exhausting the heap",
            "Task priority inversion starving the watchdog kick",
        ),
        recovery_procedure_ids=(
            "CMD_CPU_LOAD_CHECK", "CMD_CONFIRM_COMMS_LOCK",
            "CMD_OBC_CONTROLLED_REBOOT", "CMD_MEMORY_CHECK",
        ),
        description=(
            "Flight software saturates the processor and stops servicing the "
            "watchdog, which forces a reset and safe-mode entry."
        ),
        causal_chain=(
            "Software defect enters an unbounded loop",
            "CPU load saturates and memory climbs",
            "Watchdog is no longer serviced",
            "Watchdog overflows and forces a reset",
            "Safe mode entered after the reboot",
        ),
        provenance=FaultProvenance.PROMPT_PROSE,
        notes=(
            "The prose's rule about confirming comms lock before rebooting is "
            "enforced by the Phase 1 safety validator, not by this definition. "
            "CMD_CONFIRM_COMMS_LOCK is listed first among the recovery ids for "
            "that reason."
        ),
        context_signatures=(
            _ctx(ContextConditionKind.RESET_CAUSE_WATCHDOG, _DIS,
                 "The vehicle attributed its last reset to the watchdog, which "
                 "is direct evidence of the mechanism rather than of its "
                 "symptoms."),
            _ctx(ContextConditionKind.RESET_CAUSE_UNDERVOLTAGE, _CON,
                 "The reset was attributed to undervoltage, which places the "
                 "origin in power. Software would then be a victim of the "
                 "brownout, not its cause."),
        ),
    ),

    # ── 4. TCS_THERMAL_RUNAWAY (from prompts.py prose) ───────────────────
    FaultDefinition(
        fault_id="TCS_THERMAL_RUNAWAY",
        fault_name="Thermal runaway from a stuck heater",
        subsystem="TCS",
        affected_channels=("Component_temp_C", "Heater_power_W",
                           "Heater_enable_flag", "OBC_temp_C", "Panel_temp_C",
                           "Battery_temp_C", "Radiator_eff_pct"),
        expected_signatures=(
            _sig("Component_temp_C", ConditionKind.ANOMALOUS, _DIS,
                 "A component is outside its thermal limits."),
            _sig("Battery_temp_C", ConditionKind.ANOMALOUS, _DIS,
                 "Battery over-temperature is the most consequential thermal "
                 "excursion, since it degrades cells permanently."),
            _sig("OBC_temp_C", ConditionKind.ANOMALOUS, _SUP,
                 "Processor thermal margin eroded."),
            _sig("Panel_temp_C", ConditionKind.ANOMALOUS, _SUP,
                 "Panel over-temperature, which also reduces array output."),
            _sig("Heater_power_W", ConditionKind.ANOMALOUS, _SUP,
                 "The heater is drawing abnormal power, consistent with being "
                 "stuck on."),
            _sig("Radiator_eff_pct", ConditionKind.ANOMALOUS, _SUP,
                 "Heat rejection has degraded, which explains a rise with no "
                 "heater fault."),
            _sig("Component_temp_C", ConditionKind.RISING, _SUP,
                 "A sustained climb rather than a single hot reading."),
        ),
        supporting_conditions=(
            "Heater commanded off while power draw continues",
            "Temperature rising with no corresponding heater command",
        ),
        contradicting_conditions=(
            "Heater legitimately commanded on for a cold-case operation",
            "Temperature within limits and stable",
        ),
        severity=FaultSeverity.CATASTROPHIC,
        possible_causes=(
            "Heater control relay welded closed",
            "Thermostat or thermistor failure reporting a false cold reading",
            "Radiator surface degradation or blockage",
            "Loss of attitude control leaving a face sun-pointing",
        ),
        recovery_procedure_ids=(
            "CMD_HEATER_CHECK", "CMD_DISABLE_HEATER_ZONE",
            "CMD_THERMAL_CHECK", "CMD_VERIFY_THERMAL",
        ),
        description=(
            "A heater fails on, or heat rejection degrades, so component "
            "temperatures climb past their limits."
        ),
        causal_chain=(
            "Heater control fails on, or radiator efficiency falls",
            "Component temperature rises unchecked",
            "Temperature exceeds its limit",
            "TCS FDIR commands safe-mode entry",
        ),
        provenance=FaultProvenance.PROMPT_PROSE,
        notes=(
            "Signatures cover both manifestations present in the repository: the "
            "simulator drives Component_temp_C and Heater_power_W, while preset "
            "scenario 5 drives Panel_temp_C, Battery_temp_C, OBC_temp_C and "
            "Radiator_eff_pct."
        ),
    ),

    # ── 5. COMMS_TRANSPONDER_LOSS (from prompts.py prose) ────────────────
    FaultDefinition(
        fault_id="COMMS_TRANSPONDER_LOSS",
        fault_name="Communications link loss",
        subsystem="COMMS",
        affected_channels=("Transponder_lock", "Link_status", "SNR_dB",
                           "Link_margin_dB", "RF_power_dBm", "Bit_error_rate",
                           "Antenna_pointing_error_deg", "Transponder_temp_C"),
        expected_signatures=(
            _sig("Transponder_lock", ConditionKind.DISCRETE_VIOLATION, _DIS,
                 "Carrier lock lost. Direct evidence that the receiver is not "
                 "acquiring the uplink."),
            _sig("Link_status", ConditionKind.DISCRETE_VIOLATION, _DIS,
                 "The link is down. Broader than carrier lock: decoding can fail "
                 "with the carrier still locked."),
            _sig("SNR_dB", ConditionKind.ANOMALOUS, _SUP,
                 "Link quality degraded, usually before lock was lost."),
            _sig("Link_margin_dB", ConditionKind.ANOMALOUS, _SUP,
                 "No headroom left above the minimum detectable signal."),
            _sig("RF_power_dBm", ConditionKind.ANOMALOUS, _SUP,
                 "Received signal strength is low."),
            _sig("Bit_error_rate", ConditionKind.ANOMALOUS, _SUP,
                 "Errors are corrupting commands even where the link holds."),
            _sig("Antenna_pointing_error_deg", ConditionKind.ANOMALOUS, _SUP,
                 "Mispointing explains a weak link with no transponder fault, "
                 "which changes the remedy from a unit swap to an attitude fix."),
            _sig("Transponder_temp_C", ConditionKind.ANOMALOUS, _SUP,
                 "Over-temperature degrades transmitter output, so the cause may "
                 "be thermal rather than electronic."),
        ),
        supporting_conditions=(
            "time_since_contact_s far exceeds the scheduled pass interval",
            "Ground station reports no downlink",
        ),
        contradicting_conditions=(
            "Spacecraft is between scheduled passes, so no contact is expected",
            "Antenna deliberately off-pointed during a manoeuvre",
        ),
        severity=FaultSeverity.CATASTROPHIC,
        possible_causes=(
            "Transponder or receiver hardware failure",
            "Antenna mispointing from an attitude fault upstream",
            "Transponder over-temperature",
            "Ground segment fault rather than a spacecraft fault",
        ),
        recovery_procedure_ids=(
            "CMD_TRANSPONDER_CHECK", "CMD_SWITCH_BACKUP_TRANSPONDER",
            "CMD_LOW_GAIN_ANTENNA_SWITCH", "CMD_TRANSPONDER_LOCK_VERIFY",
        ),
        description=(
            "The command link degrades or fails, so the ground cannot uplink "
            "commands and recovery must rely on onboard autonomy."
        ),
        causal_chain=(
            "Link margin and signal strength fall",
            "Bit errors rise",
            "Carrier lock and link status drop",
            "Ground can no longer command the spacecraft",
        ),
        provenance=FaultProvenance.PROMPT_PROSE,
        notes=(
            "Signatures cover both manifestations: the simulator drives "
            "Transponder_lock and SNR_dB, while preset scenario 6 drives "
            "Link_status, RF_power_dBm, Link_margin_dB and Bit_error_rate."
        ),
    ),

    # ── 6. MULTI_CASCADE (from prompts.py prose) ─────────────────────────
    FaultDefinition(
        fault_id="MULTI_CASCADE",
        fault_name="Cross-subsystem cascade",
        subsystem="MULTI",
        affected_channels=("Gyro_rate_degs", "Attitude_error_deg", "I_sa",
                           "V_bat", "Component_temp_C", "SEU_counter"),
        expected_signatures=(
            _sig("Attitude_error_deg", ConditionKind.ANOMALOUS, _SUP,
                 "Attitude is implicated, which is the usual initiator of a "
                 "cascade because it drives array pointing and thermal balance."),
            _sig("Gyro_rate_degs", ConditionKind.ANOMALOUS, _SUP,
                 "Attitude sensing is implicated."),
            _sig("V_bat", ConditionKind.ANOMALOUS, _SUP,
                 "Power is implicated downstream."),
            _sig("I_sa", ConditionKind.ANOMALOUS, _SUP,
                 "Generation is implicated, consistent with lost sun pointing."),
            _sig("Component_temp_C", ConditionKind.ANOMALOUS, _SUP,
                 "Thermal balance is implicated, consistent with lost attitude "
                 "control."),
        ),
        supporting_conditions=(
            "Anomalies span two or more subsystems",
            "Anomaly onset times are ordered rather than simultaneous",
        ),
        contradicting_conditions=(
            "All anomalies confined to one subsystem",
            "Anomalies simultaneous, which suggests a common sensing or bus "
            "fault rather than propagation",
        ),
        severity=FaultSeverity.CATASTROPHIC,
        possible_causes=(
            "An attitude fault that off-points the arrays and the radiators",
            "A power fault that browns out multiple subsystems",
            "A bus or harness fault presenting as several independent faults",
        ),
        recovery_procedure_ids=(
            "CMD_HEALTH_CHECK", "CMD_TELEMETRY_DUMP", "CMD_ATTITUDE_HOLD",
            "CMD_SAFE_MODE_ENTRY",
        ),
        description=(
            "A fault in one subsystem propagates, so several subsystems report "
            "anomalies and the initiating fault is not the most recent symptom."
        ),
        causal_chain=(
            "An initiating fault occurs in one subsystem",
            "Its effect crosses a subsystem boundary",
            "Downstream subsystems report their own anomalies",
            "Multiple FDIR monitors trip",
        ),
        provenance=FaultProvenance.PROMPT_PROSE,
        notes=(
            "Scored deliberately conservatively: this fault has no DISCRIMINATING "
            "signature, because breadth across subsystems is not by itself "
            "evidence of propagation. The cross-subsystem test lives in "
            "propagation.py, which is what can distinguish a cascade from several "
            "coincident faults."
        ),
    ),

    # ══════════════════════════════════════════════════════════════════════
    # AOCS DIFFERENTIAL SET — new in Phase 6
    #
    # An attitude anomaly has several plausible causes, and the pre-Phase-6
    # system had exactly one AOCS fault defined. Whatever the telemetry showed,
    # ADCS_GYRO_SEU was the only attitude answer available, so the differential
    # was decided by the fault dictionary rather than by the evidence.
    #
    # Each of these is separated from the others by DISCRIMINATING evidence, so
    # the ranking is driven by what the detector found and not by list order.
    # ══════════════════════════════════════════════════════════════════════

    # ── 7. Reaction wheel degradation ────────────────────────────────────
    FaultDefinition(
        fault_id="AOCS_REACTION_WHEEL_DEGRADATION",
        fault_name="Reaction wheel degradation or saturation",
        subsystem="AOCS",
        affected_channels=("RW_speed_rpm", "Attitude_error_deg",
                           "Gyro_rate_degs", "V_bat"),
        expected_signatures=(
            _sig("RW_speed_rpm", ConditionKind.ANOMALOUS, _REQ,
                 "The wheel itself must be implicated. Without a wheel anomaly "
                 "this is an actuation fault with no evidence of the actuator."),
            _sig("RW_speed_rpm", ConditionKind.ABOVE_LIMIT, _DIS,
                 "Saturation: the wheel can absorb no more momentum, so pointing "
                 "degrades even though the sensors are healthy."),
            _sig("Attitude_error_deg", ConditionKind.ANOMALOUS, _SUP,
                 "Pointing has degraded, consistent with an actuator that can no "
                 "longer null the error."),
            _sig("RW_speed_rpm", ConditionKind.SUDDEN_CHANGE, _SUP,
                 "A step in wheel speed suggests a bearing seizure or a drive "
                 "fault rather than gradual momentum build-up."),
            _sig("Gyro_rate_degs", ConditionKind.DATA_INVALID, _CON,
                 "Invalid rate data points at the sensor, not the actuator. A "
                 "degraded wheel produces a valid but growing rate."),
            _sig("SEU_counter", ConditionKind.COUNTER_INCREMENTED, _CON,
                 "A radiation event makes an upset the better explanation."),
        ),
        supporting_conditions=(
            "Wheel speed trending towards its limit over several orbits",
            "Increased wheel motor current or torque for the same commanded rate",
        ),
        contradicting_conditions=(
            "Wheel speed nominal and stable",
            "Momentum dump performed recently",
        ),
        severity=FaultSeverity.MAJOR,
        possible_causes=(
            "Bearing wear or lubricant degradation",
            "Momentum saturation from an unrelieved external torque",
            "Wheel drive electronics fault",
        ),
        recovery_procedure_ids=(
            "CMD_REACTION_WHEEL_SPEED_CHECK", "CMD_REACTION_WHEEL_RESET",
            "CMD_ATTITUDE_HOLD", "CMD_VERIFY_ATTITUDE",
        ),
        description=(
            "A reaction wheel loses torque authority or saturates, so attitude "
            "error grows while the attitude sensors remain healthy."
        ),
        causal_chain=(
            "Wheel bearing degrades or momentum accumulates",
            "Available control torque falls",
            "Attitude error grows despite valid sensing",
            "ADCS FDIR trips",
        ),
        provenance=FaultProvenance.SENTINEL_DIFFERENTIAL,
        context_signatures=(
            _ctx(ContextConditionKind.SEU_EVENT_RECORDED, _CON,
                 "A recorded radiation event makes an upset the better "
                 "explanation. Mirrors the SEU_counter contradiction above for "
                 "dumps that carry the hardware record but no counter reading."),
        ),
    ),

    # ── 8. Gyroscope bias drift ──────────────────────────────────────────
    FaultDefinition(
        fault_id="AOCS_GYRO_BIAS_DRIFT",
        fault_name="Gyroscope bias drift",
        subsystem="AOCS",
        affected_channels=("Gyro_rate_degs", "Attitude_error_deg",
                           "Star_tracker_status", "SEU_counter"),
        expected_signatures=(
            _sig("Gyro_rate_degs", ConditionKind.ANOMALOUS, _REQ,
                 "The gyro must be implicated for a gyro bias to be the "
                 "explanation."),
            _sig("Gyro_rate_degs", ConditionKind.RISING, _DIS,
                 "A slow drift in the reported rate is the signature of bias, as "
                 "distinct from the step or dropout an upset produces."),
            _sig("Attitude_error_deg", ConditionKind.RISING, _DIS,
                 "Error accumulating steadily is what integrating a biased rate "
                 "produces."),
            _sig("Attitude_error_deg", ConditionKind.ANOMALOUS, _SUP,
                 "Pointing has degraded."),
            _sig("Gyro_rate_degs", ConditionKind.DATA_INVALID, _CON,
                 "A drifting gyro still returns valid numbers. An unusable "
                 "reading points at corruption or a dead sensor instead."),
            _sig("SEU_counter", ConditionKind.COUNTER_INCREMENTED, _CON,
                 "A recorded radiation event makes an upset the better "
                 "explanation for a sudden change."),
        ),
        supporting_conditions=(
            "Star tracker and gyro attitude solutions diverging over time",
            "Bias growing consistently across several orbits",
        ),
        contradicting_conditions=(
            "Rate output changed as a step rather than a ramp",
            "Gyro was recalibrated recently",
        ),
        severity=FaultSeverity.MAJOR,
        possible_causes=(
            "Sensor ageing or thermal sensitivity in the rate sensor",
            "Loss of on-orbit bias calibration",
            "Uncompensated temperature-dependent drift",
        ),
        recovery_procedure_ids=(
            "CMD_VERIFY_GYRO_RATE", "CMD_GYRO_A_RESET",
            "CMD_GYRO_SWITCH_TO_BACKUP", "CMD_ATTITUDE_REACQUISITION",
        ),
        description=(
            "The rate sensor's zero point drifts, so the integrated attitude "
            "estimate accumulates error while every reading remains valid."
        ),
        causal_chain=(
            "Gyro bias drifts away from its calibrated value",
            "Integrated attitude estimate accumulates error",
            "Attitude error grows steadily",
            "ADCS FDIR trips once the error exceeds its limit",
        ),
        provenance=FaultProvenance.SENTINEL_DIFFERENTIAL,
        context_signatures=(
            _ctx(ContextConditionKind.NO_SEU_EVENT_RECORDED, _SUP,
                 "Hardware state was reported and records no radiation event, so "
                 "gradual drift is a better account of a degrading gyro than "
                 "corruption is."),
            _ctx(ContextConditionKind.SEU_EVENT_RECORDED, _CON,
                 "A recorded radiation event points at an upset, which produces a "
                 "step or a dropout rather than a ramp."),
        ),
    ),

    # ── 9. Control command anomaly ───────────────────────────────────────
    FaultDefinition(
        fault_id="AOCS_CONTROL_COMMAND_ANOMALY",
        fault_name="Control command anomaly",
        subsystem="AOCS",
        affected_channels=("Attitude_error_deg", "RW_speed_rpm",
                           "Gyro_rate_degs", "CPU_load_pct"),
        expected_signatures=(
            _sig("Attitude_error_deg", ConditionKind.ANOMALOUS, _REQ,
                 "A commanding fault manifests as pointing error; without it "
                 "there is nothing to explain."),
            _sig("Attitude_error_deg", ConditionKind.SUDDEN_CHANGE, _DIS,
                 "A step change in error at a command boundary points at the "
                 "command rather than at a degrading component."),
            _sig("RW_speed_rpm", ConditionKind.SUDDEN_CHANGE, _DIS,
                 "The actuator responded abruptly, which is what an erroneous "
                 "command produces — the wheel is doing as it was told."),
            _sig("Gyro_rate_degs", ConditionKind.ANOMALOUS, _SUP,
                 "The vehicle is genuinely moving, so the sensors agree with the "
                 "actuator."),
            _sig("Gyro_rate_degs", ConditionKind.DATA_INVALID, _CON,
                 "Unusable sensing means the vehicle state is unknown, so a "
                 "sensor fault explains the picture better."),
            _sig("RW_speed_rpm", ConditionKind.NOMINAL, _CON,
                 "The actuator never responded, so a bad command was not "
                 "executed."),
        ),
        supporting_conditions=(
            "telecommand_context shows an attitude command immediately before "
            "onset",
            "gap_classification is 'burst', indicating unusual command timing",
        ),
        contradicting_conditions=(
            "No attitude command uplinked in the window",
            "Onset gradual rather than coincident with a command",
        ),
        severity=FaultSeverity.MAJOR,
        possible_causes=(
            "Erroneous or mis-scaled attitude command from the ground",
            "Onboard command sequence defect",
            "Control gain or mode misconfiguration after a software update",
        ),
        recovery_procedure_ids=(
            "CMD_ATTITUDE_HOLD", "CMD_ATTITUDE_RESET",
            "CMD_VERIFY_ATTITUDE", "CMD_TELEMETRY_DUMP",
        ),
        description=(
            "An erroneous attitude command is executed correctly, so the vehicle "
            "slews as instructed and reports a pointing error with healthy "
            "hardware."
        ),
        causal_chain=(
            "An erroneous attitude command is accepted",
            "The wheels execute it faithfully",
            "The vehicle departs the commanded attitude",
            "Attitude error exceeds its limit and FDIR trips",
        ),
        provenance=FaultProvenance.SENTINEL_DIFFERENTIAL,
        notes=(
            "Distinguished from a hardware fault by the hardware being healthy. "
            "The recorded telecommand gap classification is scored as SUPPORTING "
            "and deliberately not as DISCRIMINATING: a burst says the command "
            "timing was unusual, not that the command was an attitude command. "
            "Preset scenario 1 is labelled ADCS_GYRO_SEU and also carries "
            "gap_classification 'burst', which is the direct evidence that a "
            "burst does not separate this fault from its neighbours. Correlating "
            "the command itself with the anomaly onset stays a supporting "
            "CONDITION, since SENTINEL does not yet do that correlation."
        ),
        context_signatures=(
            _ctx(ContextConditionKind.RECENT_TELECOMMAND_BURST, _SUP,
                 "telecommand_context classified the command interval as a burst, "
                 "so unusual commanding activity coincides with the anomaly."),
        ),
    ),

    # ── 10. External disturbance ─────────────────────────────────────────
    FaultDefinition(
        fault_id="AOCS_EXTERNAL_DISTURBANCE",
        fault_name="External disturbance torque",
        subsystem="AOCS",
        affected_channels=("Attitude_error_deg", "RW_speed_rpm",
                           "Gyro_rate_degs", "Sun_sensor_angle_deg"),
        expected_signatures=(
            _sig("Attitude_error_deg", ConditionKind.ANOMALOUS, _REQ,
                 "A disturbance shows up as pointing error."),
            _sig("RW_speed_rpm", ConditionKind.RISING, _DIS,
                 "Wheel speed climbing while the vehicle holds attitude is the "
                 "signature of the control system absorbing an external torque."),
            _sig("Gyro_rate_degs", ConditionKind.ANOMALOUS, _SUP,
                 "A real body rate is present, so the vehicle is genuinely being "
                 "pushed."),
            _sig("Attitude_error_deg", ConditionKind.PERSISTENT, _SUP,
                 "A sustained rather than transient error, consistent with a "
                 "continuing torque."),
            _sig("Gyro_rate_degs", ConditionKind.DATA_INVALID, _CON,
                 "Unusable sensing means the rate is unknown, so a sensor fault "
                 "is the better explanation."),
            _sig("SEU_counter", ConditionKind.COUNTER_INCREMENTED, _CON,
                 "A recorded radiation event points at an upset instead."),
        ),
        supporting_conditions=(
            "Low orbit altitude where aerodynamic torque is significant",
            "Recent deployment or configuration change altering the drag profile",
            "Wheel speed climbing without a commanded manoeuvre",
        ),
        contradicting_conditions=(
            "Wheel speeds stable, so no torque is being absorbed",
            "Disturbance environment unchanged and previously tolerated",
        ),
        severity=FaultSeverity.MINOR,
        possible_causes=(
            "Aerodynamic drag torque at low altitude",
            "Solar radiation pressure on an asymmetric configuration",
            "Gravity-gradient torque after a configuration change",
            "Residual magnetic dipole interacting with the geomagnetic field",
        ),
        recovery_procedure_ids=(
            "CMD_REACTION_WHEEL_SPEED_CHECK", "CMD_ATTITUDE_HOLD",
            "CMD_VERIFY_ATTITUDE",
        ),
        description=(
            "An external torque exceeds what the control system can null "
            "without accumulating momentum, so pointing degrades with no "
            "component fault."
        ),
        causal_chain=(
            "An external torque acts on the vehicle",
            "The control system absorbs it into the wheels",
            "Wheel momentum accumulates",
            "Pointing degrades once control authority is exhausted",
        ),
        provenance=FaultProvenance.SENTINEL_DIFFERENTIAL,
        notes=(
            "The weakest hypothesis of the AOCS set by design: it is the "
            "explanation of last resort when no component shows a fault. It "
            "should rank low when any component signature is present, which is "
            "what the contradicting signatures enforce."
        ),
        context_signatures=(
            _ctx(ContextConditionKind.SEU_EVENT_RECORDED, _CON,
                 "A recorded radiation event points at an onboard upset rather "
                 "than at the environment pushing the vehicle."),
        ),
    ),

    # ── 11. Attitude sensor fault ────────────────────────────────────────
    FaultDefinition(
        fault_id="AOCS_SENSOR_FAULT",
        fault_name="Attitude sensor fault",
        subsystem="AOCS",
        affected_channels=("Star_tracker_status", "Gyro_rate_degs",
                           "Sun_sensor_angle_deg", "Attitude_error_deg"),
        expected_signatures=(
            _sig("Star_tracker_status", ConditionKind.DISCRETE_VIOLATION, _DIS,
                 "The tracker reports it is not delivering a valid attitude "
                 "solution. The sensor is declaring its own failure."),
            _sig("Gyro_rate_degs", ConditionKind.DATA_INVALID, _DIS,
                 "Rate data unusable, which is a sensor failure rather than a "
                 "vehicle dynamics problem."),
            _sig("Sun_sensor_angle_deg", ConditionKind.ANOMALOUS, _SUP,
                 "The coarse attitude reference is also implicated, so more than "
                 "one sensor disagrees."),
            _sig("Attitude_error_deg", ConditionKind.ANOMALOUS, _SUP,
                 "The reported error may itself be an artefact of bad sensing "
                 "rather than real mispointing."),
            _sig("SEU_counter", ConditionKind.COUNTER_INCREMENTED, _CON,
                 "A radiation event makes an upset the more specific "
                 "explanation for the same sensor symptoms."),
            _sig("RW_speed_rpm", ConditionKind.ABOVE_LIMIT, _CON,
                 "A saturated wheel explains real mispointing without any sensor "
                 "fault."),
        ),
        supporting_conditions=(
            "Two attitude sensors disagreeing with each other",
            "Sensor health flag set in hardware_state",
        ),
        contradicting_conditions=(
            "All attitude sensors agreeing, which means the mispointing is real",
            "Sensor recently replaced or recalibrated",
        ),
        severity=FaultSeverity.CRITICAL,
        possible_causes=(
            "Star tracker blinding by the sun, moon or earth limb",
            "Detector or optics degradation",
            "Sensor electronics failure",
            "Loss of the sensor's thermal control",
        ),
        recovery_procedure_ids=(
            "CMD_VERIFY_GYRO_RATE", "CMD_GYRO_SWITCH_TO_BACKUP",
            "CMD_ATTITUDE_REACQUISITION", "CMD_VERIFY_ATTITUDE",
        ),
        description=(
            "An attitude sensor stops delivering a valid solution, so the "
            "reported attitude error may be an artefact rather than real "
            "mispointing."
        ),
        causal_chain=(
            "An attitude sensor fails or is blinded",
            "The attitude estimate degrades or becomes unavailable",
            "Reported attitude error grows",
            "ADCS FDIR trips",
        ),
        provenance=FaultProvenance.SENTINEL_DIFFERENTIAL,
        notes=(
            "Overlaps ADCS_GYRO_SEU on purpose. The two are separated by "
            "SEU_counter: an upset is a sensor fault with a known radiation "
            "cause, so when the counter has moved the more specific fault should "
            "win, and when it has not this one should."
        ),
        context_signatures=(
            _ctx(ContextConditionKind.SEU_EVENT_RECORDED, _CON,
                 "A recorded radiation event makes the upset the more specific "
                 "explanation for the same sensor symptoms."),
        ),
    ),

    # ── 12. Battery degradation ──────────────────────────────────────────
    # Gives EPS a differential too: a falling battery is not always an array
    # fault, and before Phase 6 EPS_SOLAR_UNDERVOLT was the only power answer.
    FaultDefinition(
        fault_id="EPS_BATTERY_DEGRADATION",
        fault_name="Battery capacity degradation",
        subsystem="EPS",
        affected_channels=("V_bat", "SoC_pct", "Battery_temp_C", "I_sa"),
        expected_signatures=(
            _sig("V_bat", ConditionKind.ANOMALOUS, _REQ,
                 "The battery must be implicated."),
            _sig("I_sa", ConditionKind.NOMINAL, _DIS,
                 "Generation is healthy, so the battery is failing to hold "
                 "charge rather than failing to receive it. This is what "
                 "separates the fault from an array failure."),
            _sig("Battery_temp_C", ConditionKind.ANOMALOUS, _DIS,
                 "Battery temperature outside limits both accelerates and "
                 "explains capacity loss."),
            _sig("SoC_pct", ConditionKind.FALLING, _SUP,
                 "Charge falling despite healthy generation."),
            _sig("V_bat", ConditionKind.FALLING, _SUP,
                 "Terminal voltage declining."),
            _sig("I_sa", ConditionKind.ANOMALOUS, _CON,
                 "The array is also faulty, which explains the battery state "
                 "without invoking degradation."),
        ),
        supporting_conditions=(
            "Battery well into its design cycle life",
            "Depth of discharge increasing for the same load profile",
        ),
        contradicting_conditions=(
            "Array current also anomalous, which explains the discharge",
            "Battery recently replaced or reconditioned",
        ),
        severity=FaultSeverity.MAJOR,
        possible_causes=(
            "Cell ageing and capacity fade",
            "Cell imbalance or a failed cell",
            "Operation outside the battery temperature range",
        ),
        recovery_procedure_ids=(
            "CMD_BATTERY_CHECK", "CMD_BATTERY_VERIFY",
            "CMD_BATTERY_HEATER_ENABLE", "CMD_BUS_VOLTAGE_CHECK",
        ),
        description=(
            "The battery no longer holds its rated charge, so the bus sags under "
            "load even though the array is generating normally."
        ),
        causal_chain=(
            "Battery capacity falls below what the load profile requires",
            "Depth of discharge increases each eclipse",
            "Terminal voltage sags under load",
            "EPS FDIR trips on undervoltage",
        ),
        provenance=FaultProvenance.SENTINEL_DIFFERENTIAL,
        notes=(
            "The array relay contradiction matters more than it looks. This "
            "fault's discriminating evidence is 'I_sa NOMINAL', and a totally "
            "failed array reads 0 A, which is INSIDE the channel's hard limits "
            "and therefore nominal to the limits detector. Without the relay "
            "contradiction, a complete array failure reads as healthy generation "
            "and this fault wins the very case it should lose."
        ),
        context_signatures=(
            _ctx(ContextConditionKind.SOLAR_RELAY_OPEN, _CON,
                 "The array relay is recorded open, which accounts for the "
                 "discharge without any loss of battery capacity."),
        ),
    ),
)


FAULTS: dict[str, FaultDefinition] = {d.fault_id: d for d in _DEFINITIONS}

if len(FAULTS) != len(_DEFINITIONS):  # pragma: no cover — guards a bad edit
    raise RuntimeError("Duplicate fault_id in the fault dictionary")


#: The six faults that existed as prose in prompts.py before Phase 6. Pinned so a
#: test can prove none was lost in the move.
ORIGINAL_PROSE_FAULT_IDS: tuple[str, ...] = (
    "ADCS_GYRO_SEU",
    "EPS_SOLAR_UNDERVOLT",
    "OBC_WATCHDOG_OVERFLOW",
    "TCS_THERMAL_RUNAWAY",
    "COMMS_TRANSPONDER_LOSS",
    "MULTI_CASCADE",
)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — LOOKUP
# ═══════════════════════════════════════════════════════════════════════════

def get_fault(fault_id: object) -> Optional[FaultDefinition]:
    """Return a fault definition, or None when the id is unrecognised."""
    if fault_id is None:
        return None
    return FAULTS.get(str(fault_id).strip().upper())


def fault_ids() -> tuple[str, ...]:
    return tuple(sorted(FAULTS))


def all_faults() -> tuple[FaultDefinition, ...]:
    return tuple(FAULTS[fid] for fid in fault_ids())


def faults_for_subsystem(subsystem: object) -> tuple[FaultDefinition, ...]:
    from app.ingest.channel_dict import resolve_subsystem

    target = resolve_subsystem(subsystem)
    out = []
    for definition in all_faults():
        if definition.subsystem == "MULTI":
            continue
        if resolve_subsystem(definition.subsystem) is target:
            out.append(definition)
    return tuple(out)


def channels_referenced() -> tuple[str, ...]:
    """Every channel any fault refers to, for coverage checks."""
    seen: set[str] = set()
    for definition in all_faults():
        seen.update(definition.affected_channels)
        seen.update(s.channel for s in definition.expected_signatures)
    return tuple(sorted(seen))


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — SELF-VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

def validate_fault_dictionary() -> dict[str, list[str]]:
    """Check the fault dictionary against the channel and command registries.

    The failure this prevents: a fault whose signature names a channel that does
    not exist can never match, so the fault silently becomes unreachable and no
    test notices. Likewise a recovery command outside the Phase 1 registry would
    be refused by the safety validator, so a fault recommending it produces a
    plan that always fails.

    Run by ``python3 -m app.diagnosis.fault_dictionary`` and by CI.
    """
    from app.ingest.channel_dict import is_known_channel, resolve_subsystem
    from app.validation.command_registry import enabled_command_ids

    errors: list[str] = []
    warnings: list[str] = []

    known_commands = set(enabled_command_ids())

    for definition in all_faults():
        fid = definition.fault_id

        if definition.subsystem != "MULTI":
            if not resolve_subsystem(definition.subsystem).is_known:
                errors.append(
                    f"{fid}: subsystem {definition.subsystem!r} is not a known "
                    f"subsystem"
                )

        for channel in definition.affected_channels:
            if not is_known_channel(channel):
                errors.append(
                    f"{fid}: affected_channels names {channel!r}, which is not "
                    f"in the channel dictionary"
                )

        for signature in definition.expected_signatures:
            if not is_known_channel(signature.channel):
                errors.append(
                    f"{fid}: signature names channel {signature.channel!r}, "
                    f"which is not in the channel dictionary"
                )
            if not signature.rationale.strip():
                errors.append(
                    f"{fid}: signature {signature.key} has no rationale; an "
                    f"unexplained signature cannot be reviewed"
                )
            # Only POSITIVE signatures need to name an affected channel. A
            # contradicting signature deliberately points at a channel this fault
            # does NOT affect — that is what makes it counter-evidence — so
            # requiring it in affected_channels would be requiring the fault to
            # claim a channel it argues against.
            if (signature.role is not SignatureRole.CONTRADICTING
                    and signature.channel not in definition.affected_channels):
                warnings.append(
                    f"{fid}: positive signature channel {signature.channel!r} "
                    f"is not listed in affected_channels"
                )

        # Context signatures. Checked separately from channel signatures because
        # they name no channel: validating them against the channel dictionary
        # would reject every one of them.
        seen_context: set[ContextConditionKind] = set()
        for context_signature in definition.context_signatures:
            if not context_signature.rationale.strip():
                errors.append(
                    f"{fid}: context signature {context_signature.key} has no "
                    f"rationale; an unexplained signature cannot be reviewed"
                )
            if context_signature.condition in seen_context:
                errors.append(
                    f"{fid}: context condition "
                    f"{context_signature.condition.value} appears twice, so one "
                    f"fact would be scored twice"
                )
            seen_context.add(context_signature.condition)
            if context_signature.role is SignatureRole.REQUIRED:
                errors.append(
                    f"{fid}: context signature {context_signature.key} is "
                    f"REQUIRED. Context facts are frequently absent from a dump, "
                    f"so a REQUIRED one would make the fault unreachable on "
                    f"every dump that omits it"
                )

        for opposed in _MUTUALLY_EXCLUSIVE_CONTEXT:
            present = [c for c in opposed if c in seen_context]
            if len(present) < 2:
                continue
            roles = {c: s.role for c in present
                     for s in definition.context_signatures
                     if s.condition is c}
            positives = [c for c, r in roles.items()
                         if r is not SignatureRole.CONTRADICTING]
            if len(positives) > 1:
                errors.append(
                    f"{fid}: {' and '.join(c.value for c in positives)} are "
                    f"mutually exclusive but are both scored as positive "
                    f"evidence, so the fault would claim credit either way"
                )

        for command in definition.recovery_procedure_ids:
            if command not in known_commands:
                errors.append(
                    f"{fid}: recovery_procedure_ids names {command!r}, which is "
                    f"not an enabled command in the Phase 1 registry"
                )

        if not definition.expected_signatures:
            errors.append(f"{fid}: no expected_signatures, so it can never match")
        if not definition.possible_causes:
            errors.append(f"{fid}: no possible_causes")
        if not definition.recovery_procedure_ids:
            errors.append(f"{fid}: no recovery_procedure_ids")
        if not definition.causal_chain:
            errors.append(f"{fid}: no causal_chain")
        if len(definition.description.strip()) < 20:
            errors.append(f"{fid}: description too short to be useful")

        discriminating = definition.discriminating_signatures
        if not discriminating and fid != "MULTI_CASCADE":
            warnings.append(
                f"{fid}: no DISCRIMINATING signature, so nothing separates it "
                f"from its neighbours"
            )

    for fid in ORIGINAL_PROSE_FAULT_IDS:
        if fid not in FAULTS:
            errors.append(
                f"{fid}: one of the six original prose faults is missing from "
                f"the dictionary"
            )

    return {"errors": errors, "warnings": warnings}


def dictionary_status() -> dict:
    """Summary for the API, tests and status output."""
    by_subsystem: dict[str, list[str]] = {}
    for definition in all_faults():
        by_subsystem.setdefault(definition.subsystem, []).append(
            definition.fault_id)

    findings = validate_fault_dictionary()
    return {
        "fault_dict_version": FAULT_DICT_VERSION,
        "total_faults": len(FAULTS),
        "faults_per_subsystem": {k: sorted(v)
                                 for k, v in sorted(by_subsystem.items())},
        "original_prose_faults": list(ORIGINAL_PROSE_FAULT_IDS),
        "differential_faults_added": sorted(
            d.fault_id for d in all_faults()
            if d.provenance is FaultProvenance.SENTINEL_DIFFERENTIAL
        ),
        "channels_referenced": list(channels_referenced()),
        "signature_counts": {
            d.fault_id: len(d.expected_signatures) for d in all_faults()
        },
        "context_condition_kinds": [c.value for c in ContextConditionKind],
        "context_signature_counts": {
            d.fault_id: len(d.context_signatures) for d in all_faults()
            if d.context_signatures
        },
        "severity_counts": {
            level.value: sum(1 for d in all_faults() if d.severity is level)
            for level in FaultSeverity
        },
        "validation": findings,
    }


def _main() -> int:
    """``python3 -m app.diagnosis.fault_dictionary`` — print and validate."""
    import json

    status = dictionary_status()
    print(json.dumps(status, indent=2))

    findings = status["validation"]
    print()
    print(f"errors   : {len(findings['errors'])}")
    for message in findings["errors"]:
        print(f"  ERROR {message}")
    print(f"warnings : {len(findings['warnings'])}")
    for message in findings["warnings"]:
        print(f"  WARN  {message}")
    return 1 if findings["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(_main())
