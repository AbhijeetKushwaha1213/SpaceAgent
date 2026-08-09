"""
SENTINEL — Signature Matching (app/diagnosis/signature_match.py)

Phase 6. Turns an ``AnomalyReport`` into per-channel evidence, then evaluates every
fault's signatures against it. No LLM, no randomness, no network.

Determinism
-----------
Everything here is a pure function of the ``AnomalyReport`` and the crash dump.
Given the same inputs the same matches come out, in the same order, with the same
scores. That is testable and it is tested: ``match_faults()`` is asserted to be
byte-identical across repeated calls.

Tri-state evidence
------------------
Each predicate resolves to PRESENT, ABSENT or UNKNOWN, and the distinction does
real work:

    PRESENT   the detector found this
    ABSENT    the channel was examined and this was not found
    UNKNOWN   the channel has no reading in this dump

UNKNOWN never counts against a fault. A dump that omits ``SEU_counter`` is not
evidence that no radiation event occurred, so it must not eliminate an upset
hypothesis, and it must not satisfy a contradiction either. Collapsing UNKNOWN
into ABSENT is the standard way a rule engine ends up confidently wrong about
partial telemetry — and crash dumps are frequently partial.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from app.diagnosis.fault_dictionary import (
    ConditionKind,
    ContextConditionKind,
    FaultDefinition,
    Signature,
    SignatureRole,
    all_faults,
)


class EvidenceState(str, Enum):
    """Whether a predicate holds, does not hold, or cannot be decided."""

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"

    @property
    def is_decided(self) -> bool:
        return self is not EvidenceState.UNKNOWN


#: Detector names, referenced as strings so this module does not depend on the
#: detection package's enum identity. Values match ``DetectorName``.
_HARD_LIMIT = "HARD_LIMIT"
_DISCRETE_STATE = "DISCRETE_STATE"
_COUNTER = "COUNTER"
_DATA_QUALITY = "DATA_QUALITY"
_TREND = "TREND"
_RATE_OF_CHANGE = "RATE_OF_CHANGE"
_PERSISTENCE = "PERSISTENCE"
_SUDDEN_CHANGE = "SUDDEN_CHANGE"


# ═══════════════════════════════════════════════════════════════════════════
# CONTEXT FACTS
# ═══════════════════════════════════════════════════════════════════════════
#
# Two dump formats carry the same facts under different keys and different
# shapes, and a third carries explicit "not provided" placeholders. One extractor
# handles all three, because the alternative — a per-format extractor — is how a
# predicate ends up silently deciding one way on preset dumps and another way on
# simulator dumps.
#
#   fact           preset scenarios              fault simulator                ESA-ADB
#   eclipse        operating_context             operating_context              null
#                  .eclipse_fraction = 0.0       .orbital_position =
#                  (float)                       "eclipse_fraction: 0.8" (str)
#   SEU            hardware_state.seu_flags      hardware_state                 absent
#                  = "0x03" (hex str)            .SEU_event_count_since_boot
#                                                = 3 (int)
#   reset cause    hardware_state                hardware_state                 "NOT_PROVIDED_
#                  .last_reboot_cause            .last_reset_cause              BY_ESA_ADB"
#   solar relay    hardware_state.solar_relay    absent                         absent
#                  = "open"
#   command gap    telecommand_context           absent                         "not_provided"
#                  .gap_classification

#: ESA-ADB records absent fields with these placeholders rather than omitting
#: them. Treating them as values would turn "we do not know" into "not the case".
_NOT_PROVIDED_MARKERS = frozenset({
    "", "NOT_PROVIDED_BY_ESA_ADB", "NOT_PROVIDED", "NOT PROVIDED", "UNKNOWN",
    "NONE", "NULL", "N/A", "NA",
})


@dataclass(frozen=True)
class ContextFacts:
    """Recorded context facts, each resolved to PRESENT, ABSENT or UNKNOWN.

    ``sources`` records which dump field decided each predicate and what it held,
    so a hypothesis citing a context fact can point at the field it came from.
    Without that the operator gets an assertion with no traceable origin.
    """

    states: dict[ContextConditionKind, EvidenceState] = field(
        default_factory=dict)
    sources: dict[ContextConditionKind, str] = field(default_factory=dict)
    observations: dict[str, Any] = field(default_factory=dict)
    """Raw values read, keyed by dump field path, for the audit record."""

    def state_for(self, condition: ContextConditionKind) -> EvidenceState:
        return self.states.get(condition, EvidenceState.UNKNOWN)

    def source_for(self, condition: ContextConditionKind) -> str:
        return self.sources.get(condition, "context (not recorded)")

    def as_dict(self) -> dict[str, Any]:
        return {
            "states": {k.value: v.value for k, v in sorted(
                self.states.items(), key=lambda kv: kv[0].value)},
            "sources": {k.value: v for k, v in sorted(
                self.sources.items(), key=lambda kv: kv[0].value)},
            "observations": dict(sorted(self.observations.items())),
            "decided_count": sum(1 for v in self.states.values()
                                 if v.is_decided),
        }


def _clean_text(value: object) -> Optional[str]:
    """Uppercased text, or None when the value is absent or a placeholder."""
    if value is None:
        return None
    text = str(value).strip().upper()
    return None if text in _NOT_PROVIDED_MARKERS else text


def _eclipse_fraction(operating_context: dict[str, Any]) -> tuple[
        Optional[float], Optional[str]]:
    """Read eclipse fraction from either dump shape.

    Returns ``(value, source_field)``. The simulator stores it inside a free-text
    ``orbital_position`` string, so that is parsed rather than ignored — ignoring
    it would make every simulator dump undecidable on the one fact that separates
    an array fault from an eclipse.
    """
    raw = operating_context.get("eclipse_fraction")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw), "operating_context.eclipse_fraction"

    position = operating_context.get("orbital_position")
    if isinstance(position, str) and "eclipse_fraction" in position:
        tail = position.split("eclipse_fraction", 1)[1].lstrip(": \t")
        token = tail.split()[0].rstrip(",;") if tail.split() else ""
        try:
            return float(token), "operating_context.orbital_position"
        except ValueError:
            return None, "operating_context.orbital_position"

    return None, None


def extract_context_facts(
    crash_dump: Optional[dict[str, Any]],
) -> ContextFacts:
    """Resolve every ``ContextConditionKind`` against a crash dump.

    Pure and deterministic: no model, no randomness, no network. Every predicate
    resolves from a recorded field, and a field that is missing or marked not
    provided yields UNKNOWN rather than a default.
    """
    states: dict[ContextConditionKind, EvidenceState] = {}
    sources: dict[ContextConditionKind, str] = {}
    observations: dict[str, Any] = {}

    if not isinstance(crash_dump, dict):
        return ContextFacts(states=states, sources=sources,
                            observations=observations)

    operating = crash_dump.get("operating_context") or {}
    hardware = crash_dump.get("hardware_state") or {}
    telecommand = crash_dump.get("telecommand_context") or {}
    if not isinstance(operating, dict):
        operating = {}
    if not isinstance(hardware, dict):
        hardware = {}
    if not isinstance(telecommand, dict):
        telecommand = {}

    def decide(condition: ContextConditionKind, state: EvidenceState,
               source: str) -> None:
        states[condition] = state
        sources[condition] = source

    # ── illumination ───────────────────────────────────────────────────
    # A fraction strictly between 0 and 1 is deliberately UNDECIDED. The field
    # names a fraction of the orbit spent in eclipse; it does not say where in
    # that orbit the dump was taken. Only the endpoints are unambiguous, and
    # reading 0.35 as "eclipsed" would be inventing a position the dump does not
    # record.
    fraction, field_name = _eclipse_fraction(operating)
    if fraction is None:
        note = field_name or "operating_context (no eclipse field)"
        decide(ContextConditionKind.SPACECRAFT_SUNLIT,
               EvidenceState.UNKNOWN, note)
        decide(ContextConditionKind.SPACECRAFT_IN_ECLIPSE,
               EvidenceState.UNKNOWN, note)
    else:
        observations[field_name] = fraction
        detail = f"{field_name} = {fraction}"
        if fraction <= 0.0:
            decide(ContextConditionKind.SPACECRAFT_SUNLIT,
                   EvidenceState.PRESENT, detail)
            decide(ContextConditionKind.SPACECRAFT_IN_ECLIPSE,
                   EvidenceState.ABSENT, detail)
        elif fraction >= 1.0:
            decide(ContextConditionKind.SPACECRAFT_SUNLIT,
                   EvidenceState.ABSENT, detail)
            decide(ContextConditionKind.SPACECRAFT_IN_ECLIPSE,
                   EvidenceState.PRESENT, detail)
        else:
            undecided = (
                f"{detail} — a partial orbit fraction does not establish where "
                f"in the orbit this dump was taken"
            )
            decide(ContextConditionKind.SPACECRAFT_SUNLIT,
                   EvidenceState.UNKNOWN, undecided)
            decide(ContextConditionKind.SPACECRAFT_IN_ECLIPSE,
                   EvidenceState.UNKNOWN, undecided)

    # ── radiation event ────────────────────────────────────────────────
    seu_state = EvidenceState.UNKNOWN
    seu_source = "hardware_state (no SEU record)"
    count = hardware.get("SEU_event_count_since_boot")
    if isinstance(count, (int, float)) and not isinstance(count, bool):
        observations["hardware_state.SEU_event_count_since_boot"] = count
        seu_state = (EvidenceState.PRESENT if count > 0
                     else EvidenceState.ABSENT)
        seu_source = f"hardware_state.SEU_event_count_since_boot = {count}"
    else:
        flags = _clean_text(hardware.get("seu_flags"))
        if flags is not None:
            observations["hardware_state.seu_flags"] = flags
            try:
                value = int(flags, 16) if flags.startswith("0X") else int(flags)
            except ValueError:
                seu_source = (
                    f"hardware_state.seu_flags = {flags!r} (unparseable)")
            else:
                seu_state = (EvidenceState.PRESENT if value > 0
                             else EvidenceState.ABSENT)
                seu_source = f"hardware_state.seu_flags = {flags}"

    decide(ContextConditionKind.SEU_EVENT_RECORDED, seu_state, seu_source)
    decide(
        ContextConditionKind.NO_SEU_EVENT_RECORDED,
        {
            EvidenceState.PRESENT: EvidenceState.ABSENT,
            EvidenceState.ABSENT: EvidenceState.PRESENT,
            EvidenceState.UNKNOWN: EvidenceState.UNKNOWN,
        }[seu_state],
        seu_source,
    )

    # ── reset cause ────────────────────────────────────────────────────
    cause = None
    cause_field = None
    for candidate in ("last_reset_cause", "last_reboot_cause"):
        cause = _clean_text(hardware.get(candidate))
        if cause is not None:
            cause_field = f"hardware_state.{candidate}"
            break

    if cause is None:
        note = "hardware_state (no reset cause recorded)"
        decide(ContextConditionKind.RESET_CAUSE_UNDERVOLTAGE,
               EvidenceState.UNKNOWN, note)
        decide(ContextConditionKind.RESET_CAUSE_WATCHDOG,
               EvidenceState.UNKNOWN, note)
    else:
        observations[cause_field] = cause
        detail = f"{cause_field} = {cause}"
        decide(
            ContextConditionKind.RESET_CAUSE_UNDERVOLTAGE,
            EvidenceState.PRESENT if "UNDERVOLT" in cause
            else EvidenceState.ABSENT,
            detail,
        )
        decide(
            ContextConditionKind.RESET_CAUSE_WATCHDOG,
            EvidenceState.PRESENT if "WATCHDOG" in cause
            else EvidenceState.ABSENT,
            detail,
        )

    # ── array relay ────────────────────────────────────────────────────
    relay = _clean_text(hardware.get("solar_relay"))
    if relay is None:
        decide(ContextConditionKind.SOLAR_RELAY_OPEN, EvidenceState.UNKNOWN,
               "hardware_state (no solar_relay recorded)")
    else:
        observations["hardware_state.solar_relay"] = relay
        decide(
            ContextConditionKind.SOLAR_RELAY_OPEN,
            EvidenceState.PRESENT if relay == "OPEN" else EvidenceState.ABSENT,
            f"hardware_state.solar_relay = {relay}",
        )

    # ── telecommand timing ─────────────────────────────────────────────
    gap = _clean_text(telecommand.get("gap_classification"))
    if gap is None or gap == "NOT_PROVIDED":
        decide(ContextConditionKind.RECENT_TELECOMMAND_BURST,
               EvidenceState.UNKNOWN,
               "telecommand_context (gap classification not provided)")
    else:
        observations["telecommand_context.gap_classification"] = gap
        decide(
            ContextConditionKind.RECENT_TELECOMMAND_BURST,
            EvidenceState.PRESENT if gap == "BURST" else EvidenceState.ABSENT,
            f"telecommand_context.gap_classification = {gap}",
        )

    return ContextFacts(states=states, sources=sources,
                        observations=observations)


@dataclass(frozen=True)
class ChannelEvidence:
    """What the detectors found on one channel, reduced to checkable facts."""

    channel: str
    examined: bool
    """True when the dump carried at least one reading for this channel."""

    anomalous: bool = False
    detectors: frozenset[str] = frozenset()
    severities: frozenset[str] = frozenset()
    max_severity: Optional[str] = None
    anomaly_count: int = 0

    above_limit: bool = False
    below_limit: bool = False
    data_invalid: bool = False
    counter_incremented: bool = False
    discrete_violation: bool = False
    rising: bool = False
    falling: bool = False
    sudden_change: bool = False
    persistent: bool = False

    first_seen: Optional[str] = None
    onset_seconds: Optional[float] = None
    anomaly_ids: tuple[str, ...] = ()
    subsystem: str = "UNKNOWN"

    def state_for(self, condition: ConditionKind) -> EvidenceState:
        """Resolve one predicate against this channel's evidence."""
        if not self.examined:
            return EvidenceState.UNKNOWN

        flag = {
            ConditionKind.ANOMALOUS: self.anomalous,
            ConditionKind.NOMINAL: not self.anomalous,
            ConditionKind.ABOVE_LIMIT: self.above_limit,
            ConditionKind.BELOW_LIMIT: self.below_limit,
            ConditionKind.DATA_INVALID: self.data_invalid,
            ConditionKind.COUNTER_INCREMENTED: self.counter_incremented,
            ConditionKind.DISCRETE_VIOLATION: self.discrete_violation,
            ConditionKind.RISING: self.rising,
            ConditionKind.FALLING: self.falling,
            ConditionKind.SUDDEN_CHANGE: self.sudden_change,
            ConditionKind.PERSISTENT: self.persistent,
        }.get(condition)

        if flag is None:  # pragma: no cover — ConditionKind is exhaustive above
            return EvidenceState.UNKNOWN
        return EvidenceState.PRESENT if flag else EvidenceState.ABSENT

    def observed_summary(self) -> dict[str, Any]:
        """Compact record of the evidence, for the hypothesis payload."""
        return {
            "channel": self.channel,
            "examined": self.examined,
            "anomalous": self.anomalous,
            "anomaly_count": self.anomaly_count,
            "detectors": sorted(self.detectors),
            "max_severity": self.max_severity,
            "first_seen": self.first_seen,
            "onset_seconds": self.onset_seconds,
            "subsystem": self.subsystem,
        }


@dataclass
class EvidenceIndex:
    """Per-channel evidence for a whole dump, plus what it says by subsystem."""

    channels: dict[str, ChannelEvidence] = field(default_factory=dict)
    anomalous_subsystems: tuple[str, ...] = ()
    subsystem_onsets: dict[str, Optional[float]] = field(default_factory=dict)
    unknown_channels: tuple[str, ...] = ()
    total_readings: int = 0
    total_anomalies: int = 0
    context: ContextFacts = field(default_factory=ContextFacts)
    """Recorded context facts. Empty when no crash dump was supplied, in which
    case every context predicate is UNKNOWN and none of them scores."""
    subsystem_evidence_strength: dict[str, float] = field(default_factory=dict)
    """Per-subsystem weight of the evidence implicating it. See
    ``_SUBSYSTEM_EVIDENCE_WEIGHT`` for why a raw count will not do."""

    def for_channel(self, channel: str) -> ChannelEvidence:
        """Evidence for a channel, resolving aliases through the dictionary.

        An unexamined channel gets a placeholder whose predicates are all UNKNOWN,
        so a signature on a channel the dump never mentioned neither helps nor
        harms the fault.
        """
        direct = self.channels.get(channel)
        if direct is not None:
            return direct

        from app.ingest.channel_dict import get_channel

        definition = get_channel(channel)
        if definition is not None:
            resolved = self.channels.get(definition.channel_id)
            if resolved is not None:
                return resolved

        return ChannelEvidence(channel=channel, examined=False)


def _severity_rank(name: Optional[str]) -> int:
    order = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    return order.get((name or "").upper(), -1)


#: How strongly a detector's finding implicates a real fault in the channel's
#: subsystem. Used to weight subsystem involvement, NOT to score signatures.
#:
#: The failure this fixes, found by measurement: propagation scoring counted
#: subsystems, so a subsystem implicated by ONE data-quality dropout weighed the
#: same as one with three hard-limit violations. On a simulated comms fault the
#: injected 5% NaN dropout put a single ``V_bat`` reading into the anomaly list,
#: which made EPS a subsystem the comms hypothesis had to explain and could not.
#: Telemetry-path noise was deciding the root cause.
#:
#: DATA_QUALITY is lowest for that reason: a NaN can come from the downlink path
#: rather than from the subsystem. A discrete-state violation or a limit
#: exceedance is a measurement the vehicle made about itself.
_SUBSYSTEM_EVIDENCE_WEIGHT: dict[str, float] = {
    _HARD_LIMIT: 1.0,
    _DISCRETE_STATE: 1.0,
    _COUNTER: 1.0,
    _SUDDEN_CHANGE: 0.7,
    _RATE_OF_CHANGE: 0.7,
    _TREND: 0.7,
    _PERSISTENCE: 0.5,
    _DATA_QUALITY: 0.25,
}

#: Weight for a detector not in the table, so an unrecognised detector still
#: counts for something rather than silently dropping to zero.
_DEFAULT_EVIDENCE_WEIGHT = 0.5


def _channel_evidence_weight(evidence: "ChannelEvidence") -> float:
    """Strength of one channel's contribution to implicating its subsystem.

    The MAXIMUM across the channel's detectors, not the sum: three detectors
    firing on one reading is one observation described three ways, and summing
    would let a single channel outweigh three independent ones.
    """
    if not evidence.anomalous:
        return 0.0
    if not evidence.detectors:
        return _DEFAULT_EVIDENCE_WEIGHT
    return max(
        _SUBSYSTEM_EVIDENCE_WEIGHT.get(d, _DEFAULT_EVIDENCE_WEIGHT)
        for d in evidence.detectors
    )


def build_evidence_index(
    report: Any,
    crash_dump: Optional[dict[str, Any]] = None,
) -> EvidenceIndex:
    """Reduce an ``AnomalyReport`` to per-channel checkable evidence.

    ``crash_dump`` is optional and used only to learn which channels were
    EXAMINED. Without it, a channel with no anomaly is indistinguishable from a
    channel with no reading, and the two must not be conflated: the first is
    evidence of health and the second is evidence of nothing.
    """
    from app.ingest.channel_dict import get_channel, subsystem_of

    examined: dict[str, bool] = {}
    if crash_dump is not None:
        try:
            from app.api.adapters import canonical_window_dicts

            for row in canonical_window_dicts(crash_dump):
                name = row.get("parameter")
                if not name:
                    continue
                definition = get_channel(name)
                key = definition.channel_id if definition else str(name)
                examined[key] = True
        except Exception:  # pragma: no cover — adapter is in-tree
            pass

    # Channels the report itself examined.
    for finding in getattr(report, "channels", []) or []:
        name = getattr(finding, "channel", None)
        if name:
            definition = get_channel(name)
            examined[definition.channel_id if definition else str(name)] = True

    accumulator: dict[str, dict[str, Any]] = {}
    for anomaly in getattr(report, "anomalies", []) or []:
        raw_name = getattr(anomaly, "channel", None)
        if not raw_name:
            continue
        definition = get_channel(raw_name)
        key = definition.channel_id if definition else str(raw_name)
        examined[key] = True

        bucket = accumulator.setdefault(key, {
            "detectors": set(), "severities": set(), "ids": [],
            "count": 0, "first_seen": None, "onset": None,
            "above": False, "below": False, "invalid": False,
            "counter": False, "discrete": False, "rising": False,
            "falling": False, "sudden": False, "persistent": False,
        })

        detector = getattr(getattr(anomaly, "detector", None), "value",
                           str(getattr(anomaly, "detector", "")))
        severity = getattr(getattr(anomaly, "severity", None), "value",
                           str(getattr(anomaly, "severity", "")))
        evidence = getattr(anomaly, "evidence", None) or {}

        bucket["detectors"].add(detector)
        bucket["severities"].add(severity)
        bucket["count"] += 1
        anomaly_id = getattr(anomaly, "anomaly_id", None)
        if anomaly_id:
            bucket["ids"].append(str(anomaly_id))

        timestamp = getattr(anomaly, "timestamp", None)
        if timestamp and bucket["first_seen"] is None:
            bucket["first_seen"] = str(timestamp)
        onset = _parse_offset(timestamp)
        if onset is not None:
            bucket["onset"] = (onset if bucket["onset"] is None
                               else min(bucket["onset"], onset))

        # Map detector + evidence onto the predicate vocabulary. Read from the
        # evidence dict the detector itself produced rather than re-deriving,
        # so a limits finding and this module cannot disagree about direction.
        if detector == _HARD_LIMIT:
            exceeded = str(evidence.get("limit_exceeded", "")).upper()
            if exceeded == "MAX":
                bucket["above"] = True
            elif exceeded == "MIN":
                bucket["below"] = True
        elif detector == _DATA_QUALITY:
            bucket["invalid"] = True
        elif detector == _COUNTER:
            bucket["counter"] = True
        elif detector == _DISCRETE_STATE:
            bucket["discrete"] = True
        elif detector == _TREND:
            direction = str(evidence.get("direction", "")).upper()
            if direction in ("RISING", "INCREASING", "UP"):
                bucket["rising"] = True
            elif direction in ("FALLING", "DECREASING", "DOWN"):
                bucket["falling"] = True
        elif detector in (_RATE_OF_CHANGE, _SUDDEN_CHANGE):
            bucket["sudden"] = True
        elif detector == _PERSISTENCE:
            bucket["persistent"] = True

    channels: dict[str, ChannelEvidence] = {}
    for name, was_examined in examined.items():
        bucket = accumulator.get(name)
        if bucket is None:
            channels[name] = ChannelEvidence(
                channel=name, examined=was_examined, anomalous=False,
                subsystem=subsystem_of(name).value,
            )
            continue
        severities = {s for s in bucket["severities"] if s}
        channels[name] = ChannelEvidence(
            channel=name,
            examined=True,
            anomalous=True,
            detectors=frozenset(bucket["detectors"]),
            severities=frozenset(severities),
            max_severity=max(severities, key=_severity_rank, default=None),
            anomaly_count=bucket["count"],
            above_limit=bucket["above"],
            below_limit=bucket["below"],
            data_invalid=bucket["invalid"],
            counter_incremented=bucket["counter"],
            discrete_violation=bucket["discrete"],
            rising=bucket["rising"],
            falling=bucket["falling"],
            sudden_change=bucket["sudden"],
            persistent=bucket["persistent"],
            first_seen=bucket["first_seen"],
            onset_seconds=bucket["onset"],
            anomaly_ids=tuple(sorted(bucket["ids"])),
            subsystem=subsystem_of(name).value,
        )

    anomalous_subsystems: dict[str, Optional[float]] = {}
    strength: dict[str, float] = {}
    for evidence in channels.values():
        if not evidence.anomalous:
            continue
        subsystem = evidence.subsystem
        if subsystem == "UNKNOWN":
            continue
        current = anomalous_subsystems.get(subsystem, None)
        if evidence.onset_seconds is not None:
            anomalous_subsystems[subsystem] = (
                evidence.onset_seconds if current is None
                else min(current, evidence.onset_seconds)
            )
        else:
            anomalous_subsystems.setdefault(subsystem, None)
        strength[subsystem] = round(
            strength.get(subsystem, 0.0) + _channel_evidence_weight(evidence), 4)

    return EvidenceIndex(
        channels=channels,
        anomalous_subsystems=tuple(sorted(anomalous_subsystems)),
        subsystem_onsets=anomalous_subsystems,
        unknown_channels=tuple(sorted(
            name for name, e in channels.items()
            if e.subsystem == "UNKNOWN"
        )),
        total_readings=int(getattr(report, "total_readings", 0) or 0),
        total_anomalies=int(getattr(report, "anomaly_count", 0) or 0),
        context=extract_context_facts(crash_dump),
        subsystem_evidence_strength=strength,
    )


def _parse_offset(timestamp: object) -> Optional[float]:
    """Parse 'T-120s' into -120.0. None when unparseable."""
    if timestamp is None:
        return None
    text = str(timestamp).strip()
    if not text:
        return None
    if text[:1] in ("T", "t"):
        text = text[1:]
    if text.endswith(("s", "S")):
        text = text[:-1]
    text = text.strip().lstrip("+")
    try:
        return float(text)
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# MATCHING
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MatchedSignature:
    """One signature evaluated against the evidence."""

    channel: str
    condition: str
    role: str
    state: str
    weight: float
    rationale: str
    detectors: tuple[str, ...] = ()
    severity: Optional[str] = None
    timestamp: Optional[str] = None
    anomaly_ids: tuple[str, ...] = ()

    source: str = "TELEMETRY"
    """TELEMETRY for a channel predicate, CONTEXT for a recorded-context one.
    Explicit because the two are not interchangeable to a reviewer: one is a
    measurement with limits and a sampling rate, the other is a logged fact."""

    signature_key: str = ""
    """The originating signature's ``key``, so a match can be traced back to the
    exact predicate that produced it.

    Needed rather than reconstructing from channel and condition. A context
    record puts the dump FIELD PATH in ``channel``, so the pair no longer
    reconstructs the key; and within one fault two discriminating signatures can
    share a condition on different channels — TCS_THERMAL_RUNAWAY has ANOMALOUS
    on both Component_temp_C and Battery_temp_C — so the condition alone does not
    identify a predicate either."""

    observed_from: Optional[str] = None
    """For a context predicate, the dump field that decided it and what it held,
    e.g. ``operating_context.eclipse_fraction = 0.0``. None for telemetry, where
    ``channel`` and ``detectors`` already carry the provenance."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "condition": self.condition,
            "role": self.role,
            "state": self.state,
            "weight": self.weight,
            "rationale": self.rationale,
            "detectors": list(self.detectors),
            "severity": self.severity,
            "timestamp": self.timestamp,
            "anomaly_ids": list(self.anomaly_ids),
            "source": self.source,
            "observed_from": self.observed_from,
            "signature_key": self.signature_key,
        }


@dataclass(frozen=True)
class SignatureMatch:
    """The result of evaluating one fault against the evidence."""

    fault_id: str
    eligible: bool
    """False when a REQUIRED signature was definitively unmet."""

    ineligible_reason: Optional[str]
    matched: tuple[MatchedSignature, ...]
    unmatched: tuple[MatchedSignature, ...]
    contradicted: tuple[MatchedSignature, ...]
    undetermined: tuple[MatchedSignature, ...]

    matched_weight: float
    contradiction_weight: float
    max_weight: float
    signature_score: float
    """Normalised to [0, 1]: matched weight minus contradictions, over the total
    weight available. Not a probability, and not presented as one."""

    affected_channels: tuple[str, ...]
    timestamps: tuple[str, ...]
    earliest_onset: Optional[float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "fault_id": self.fault_id,
            "eligible": self.eligible,
            "ineligible_reason": self.ineligible_reason,
            "signature_score": self.signature_score,
            "matched_weight": self.matched_weight,
            "contradiction_weight": self.contradiction_weight,
            "max_weight": self.max_weight,
            "matched": [m.as_dict() for m in self.matched],
            "unmatched": [m.as_dict() for m in self.unmatched],
            "contradicted": [m.as_dict() for m in self.contradicted],
            "undetermined": [m.as_dict() for m in self.undetermined],
            "affected_channels": list(self.affected_channels),
            "timestamps": list(self.timestamps),
            "earliest_onset": self.earliest_onset,
        }


def _describe(signature: Signature, evidence: ChannelEvidence,
              state: EvidenceState) -> MatchedSignature:
    return MatchedSignature(
        channel=signature.channel,
        condition=signature.condition.value,
        role=signature.role.value,
        state=state.value,
        weight=signature.weight,
        rationale=signature.rationale,
        detectors=tuple(sorted(evidence.detectors)),
        severity=evidence.max_severity,
        timestamp=evidence.first_seen,
        anomaly_ids=evidence.anomaly_ids,
        signature_key=signature.key,
    )


def match_fault(
    definition: FaultDefinition,
    index: EvidenceIndex,
) -> SignatureMatch:
    """Evaluate one fault's signatures against the evidence.

    Eligibility: a fault is excluded only when a REQUIRED signature is
    definitively ABSENT. An UNKNOWN required signature leaves the fault eligible,
    because a channel the dump never reported cannot rule a fault out — the same
    policy the Phase 1 safety validator uses for missing preconditions, and for
    the same reason.
    """
    matched: list[MatchedSignature] = []
    unmatched: list[MatchedSignature] = []
    contradicted: list[MatchedSignature] = []
    undetermined: list[MatchedSignature] = []

    eligible = True
    reason: Optional[str] = None
    matched_weight = 0.0
    contradiction_weight = 0.0
    involved: set[str] = set()
    timestamps: set[str] = set()
    onsets: list[float] = []

    for signature in definition.expected_signatures:
        evidence = index.for_channel(signature.channel)
        state = evidence.state_for(signature.condition)
        record = _describe(signature, evidence, state)

        if signature.role is SignatureRole.CONTRADICTING:
            if state is EvidenceState.PRESENT:
                contradicted.append(record)
                contradiction_weight += abs(signature.weight)
                involved.add(evidence.channel)
                if evidence.first_seen:
                    timestamps.add(evidence.first_seen)
            elif state is EvidenceState.UNKNOWN:
                undetermined.append(record)
            continue

        if state is EvidenceState.PRESENT:
            matched.append(record)
            matched_weight += signature.weight
            involved.add(evidence.channel)
            if evidence.first_seen:
                timestamps.add(evidence.first_seen)
            if evidence.onset_seconds is not None:
                onsets.append(evidence.onset_seconds)
        elif state is EvidenceState.ABSENT:
            unmatched.append(record)
            if signature.role is SignatureRole.REQUIRED and eligible:
                eligible = False
                reason = (
                    f"required signature {signature.channel}:"
                    f"{signature.condition.value} is absent — the channel was "
                    f"examined and the condition does not hold"
                )
        else:
            undetermined.append(record)

    # ── recorded context, scored on the same footing ───────────────────
    # Same roles, same weights, same UNKNOWN policy. A context fact is evidence:
    # "the relay is open" bears on an array fault at least as directly as any
    # single reading in the window does.
    for context_signature in definition.context_signatures:
        state = index.context.state_for(context_signature.condition)
        record = MatchedSignature(
            channel=index.context.source_for(context_signature.condition),
            condition=context_signature.condition.value,
            role=context_signature.role.value,
            state=state.value,
            weight=context_signature.weight,
            rationale=context_signature.rationale,
            source="CONTEXT",
            observed_from=index.context.source_for(
                context_signature.condition),
            signature_key=context_signature.key,
        )

        if context_signature.role is SignatureRole.CONTRADICTING:
            if state is EvidenceState.PRESENT:
                contradicted.append(record)
                contradiction_weight += abs(context_signature.weight)
            elif state is EvidenceState.UNKNOWN:
                undetermined.append(record)
            continue

        if state is EvidenceState.PRESENT:
            matched.append(record)
            matched_weight += context_signature.weight
        elif state is EvidenceState.ABSENT:
            unmatched.append(record)
        else:
            undetermined.append(record)

    # Context contributes no entry to affected_channels or timestamps: it names
    # no channel and carries no sample time. Adding a pseudo-channel there would
    # put a non-channel into a list the rest of the system reads as telemetry.

    max_weight = definition.max_positive_weight
    raw = (matched_weight - contradiction_weight) / max_weight
    signature_score = round(max(0.0, min(1.0, raw)), 4)

    return SignatureMatch(
        fault_id=definition.fault_id,
        eligible=eligible,
        ineligible_reason=reason,
        matched=tuple(matched),
        unmatched=tuple(unmatched),
        contradicted=tuple(contradicted),
        undetermined=tuple(undetermined),
        matched_weight=round(matched_weight, 4),
        contradiction_weight=round(contradiction_weight, 4),
        max_weight=round(max_weight, 4),
        signature_score=signature_score,
        affected_channels=tuple(sorted(involved)),
        timestamps=tuple(sorted(timestamps)),
        earliest_onset=min(onsets) if onsets else None,
    )


def match_faults(
    report: Any,
    crash_dump: Optional[dict[str, Any]] = None,
    index: Optional[EvidenceIndex] = None,
) -> tuple[EvidenceIndex, tuple[SignatureMatch, ...]]:
    """Evaluate EVERY fault against the evidence.

    Returns ``(index, matches)`` with matches in a stable order: descending
    signature score, then ascending fault_id. Deterministic — the tie-break is on
    the id rather than on dictionary order, so adding a fault cannot reorder
    unrelated results.

    Every fault is returned, including ineligible ones and zero-scoring ones, so
    a caller can see what was considered and rejected. ``candidates.py`` filters.
    """
    evidence_index = index or build_evidence_index(report, crash_dump)
    matches = [match_fault(d, evidence_index) for d in all_faults()]
    matches.sort(key=lambda m: (-m.signature_score, m.fault_id))
    return evidence_index, tuple(matches)


def matching_status() -> dict:
    """Describe the matcher, for the API and tests."""
    return {
        "condition_kinds": [c.value for c in ConditionKind],
        "context_condition_kinds": [c.value for c in ContextConditionKind],
        "signature_roles": [r.value for r in SignatureRole],
        "evidence_states": [s.value for s in EvidenceState],
        "subsystem_evidence_weights": dict(_SUBSYSTEM_EVIDENCE_WEIGHT),
        "deterministic": True,
        "uses_llm": False,
        "claim": (
            "Candidate generation is a pure function of detector output. No "
            "language model is consulted, and no value is sampled."
        ),
        "unknown_policy": (
            "A predicate on a channel with no reading resolves to UNKNOWN. "
            "UNKNOWN never eliminates a fault and never satisfies a "
            "contradiction: absence of a reading is not evidence. The same "
            "applies to a context fact the dump does not record, including one "
            "recorded as an explicit not-provided placeholder."
        ),
        "context_policy": (
            "Context predicates read recorded fields from the crash dump only. "
            "One extractor covers every dump format, so a predicate cannot "
            "decide one way on preset scenarios and another on simulator output. "
            "An eclipse fraction strictly between 0 and 1 is left UNDECIDED: it "
            "names a fraction of the orbit and does not locate the dump within "
            "that orbit."
        ),
    }
