"""
SENTINEL — Detection Channel Specs (detection/channels.py)

Phase 2 introduced this module. Phase 5 turned it into a VIEW: the numbers now
come from ``app.ingest.channel_dict``, which is the single authority, and nothing
is declared here.

What the module still contributes
---------------------------------
The detection-facing projection of a channel definition. ``ChannelSpec`` is the
shape the detectors already consume, and ``BoundOrigin`` is a detection concern
that has no place in a vehicle channel dictionary: it records whether a bound is
an engineering limit or a statistic, so a statistical detector cannot treat an
engineering limit as a 3-sigma band and a limits finding cannot describe an
observed statistic as a physical limit.

Why the KIND of a channel matters (unchanged from Phase 2)
---------------------------------------------------------
The pre-Phase-2 detector treated every channel as continuous and Gaussian, with
sigma derived from its range:

    mu = (lo + hi) / 2      sigma = (hi - lo) / 6

That produced two structural blind spots, both verified against the real code:

  1. DEGENERATE RANGES. lo == hi gives sigma == 0, so z was always 0.0:

         SEU_counter        (0, 0)   value 999 -> z=0.0, NOT flagged
         Transponder_lock   (1, 1)   value 0   -> z=0.0, NOT flagged
         Star_tracker_status(0, 0)   value 1   -> z=0.0, NOT flagged
         Fault_register     (0, 0)   value 8   -> z=0.0, NOT flagged

     Exactly the channels the system prompt called most diagnostic.

  2. LIMIT VIOLATIONS BELOW THRESHOLD. A wide range inflates sigma:

         Watchdog_counter   (0, 1000)  value 1002 -> z=2.85 < 3.0, NOT flagged

     sigma = 166.7, so a genuine overflow sat 0.012 sigma from the mean.

Limits and discrete states are therefore checked by exact comparison, never via a
z-score. Which test applies is decided by ``ChannelKind``, projected from the
dictionary's ``value_class``.

Unknown channels
----------------
``spec_or_inferred()`` claims nothing about an unrecognised channel — including
ESA-ADB's anonymized ``channel_*`` names. Subsystem is the explicit string
``"UNKNOWN"``, no expected state is guessed, no rate ceiling is invented, and
limits come only from bounds the reading itself carried.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.ingest.channel_dict import (
    CHANNELS,
    ChannelDefinition,
    Subsystem,
    ValueClass,
    get_channel,
)


class BoundOrigin(str, Enum):
    """Where a channel's limit_min / limit_max came from."""

    ENGINEERING = "ENGINEERING"
    """A declared physical or design limit. Exceeding it is a limit violation."""

    STATISTICAL = "STATISTICAL"
    """Computed from observed statistics, e.g. ESA-ADB's mean +/- 3*std.
    Exceeding it is a statistical exceedance, not a physical limit breach."""

    UNKNOWN = "UNKNOWN"
    """Bounds carried by the reading with no stated origin — typical of an
    anonymized channel. Reported as an exceedance of a declared bound, without
    claiming it is either physical or statistical."""


class ChannelKind(str, Enum):
    """What sort of quantity a channel carries.

    Values mirror ``app.ingest.channel_dict.ValueClass`` one-for-one. The two
    enums are kept distinct so the detection layer does not force its vocabulary
    on the dictionary, and ``_KIND_FOR_VALUE_CLASS`` is the single place the
    correspondence is stated — a test asserts it is total.
    """

    CONTINUOUS = "CONTINUOUS"
    """A physical measurement that varies smoothly. Statistical and temporal
    detection are meaningful. Example: V_bat, OBC_temp_C."""

    COUNTER = "COUNTER"
    """A monotonically non-decreasing event count. Any increase above the
    expected value is meaningful regardless of magnitude, and statistical
    detection is NOT meaningful. Example: SEU_counter, Watchdog_counter."""

    STATUS = "STATUS"
    """An enumerated state or fault code. Only membership in the expected set
    matters. Example: Star_tracker_status, Fault_register."""

    FLAG = "FLAG"
    """A boolean-valued channel. Example: Transponder_lock, Heater_enable_flag."""


_KIND_FOR_VALUE_CLASS: dict[ValueClass, ChannelKind] = {
    ValueClass.CONTINUOUS: ChannelKind.CONTINUOUS,
    ValueClass.COUNTER: ChannelKind.COUNTER,
    ValueClass.STATUS: ChannelKind.STATUS,
    ValueClass.FLAG: ChannelKind.FLAG,
}

#: Subsystem string used when a channel cannot be attributed. Explicit rather
#: than None, so a consumer can tell "not attributable" from "not populated".
UNKNOWN_SUBSYSTEM = Subsystem.UNKNOWN.value


@dataclass(frozen=True)
class ChannelSpec:
    """What one telemetry channel is, and how to test it."""

    name: str
    kind: ChannelKind
    subsystem: Optional[str] = None
    unit: Optional[str] = None

    #: Hard engineering limits. A value outside these is a limit violation,
    #: checked by exact comparison — never via a z-score.
    limit_min: Optional[float] = None
    limit_max: Optional[float] = None

    #: For STATUS / FLAG / COUNTER channels: the value(s) that mean "healthy".
    #: Anything else is a discrete-state violation.
    expected_states: tuple[float, ...] = ()

    #: For COUNTER channels: counters only ever increase, so a decrease means a
    #: reset or a corrupted reading, and is itself worth reporting.
    monotonic_non_decreasing: bool = False

    #: Maximum plausible change per second, when one is known. None disables the
    #: check — no rate limit is invented for a channel we have no basis for.
    max_rate_per_s: Optional[float] = None

    #: Where limit_min/limit_max came from. Statistical detectors must not treat
    #: an ENGINEERING bound as a 3-sigma range; that assumption is the Phase 2
    #: bug. And a limits finding must not describe a STATISTICAL bound as a
    #: physical limit, which would overstate what is known.
    bound_origin: "BoundOrigin" = None  # type: ignore[assignment]

    @property
    def limits_are_engineering(self) -> bool:
        return self.bound_origin is BoundOrigin.ENGINEERING

    @property
    def is_discrete(self) -> bool:
        return self.kind in (ChannelKind.STATUS, ChannelKind.FLAG)

    @property
    def subsystem_is_known(self) -> bool:
        """False when the channel could not be attributed to a subsystem."""
        return bool(self.subsystem) and self.subsystem != UNKNOWN_SUBSYSTEM

    @property
    def statistical_detection_meaningful(self) -> bool:
        """Whether a z-score on this channel means anything.

        False for counters and discrete states: a counter's distribution is not
        Gaussian, and a status code's numeric distance from its expected value
        carries no physical meaning. Running a z-score on them produced the
        blind spots this module documents.
        """
        return self.kind is ChannelKind.CONTINUOUS


#: Dictionary provenance -> how much a detector may read into the bound.
#:
#: Not every declared channel has an engineering limit behind it. A limit carried
#: over from the detector table was applied to every dump and is treated as an
#: engineering limit, as it always was. A limit adopted from a single preset
#: scenario has no stated origin, so it stays UNKNOWN and keeps the conservative
#: treatment Phase 2 gave to undeclared bounds.
#:
#: Getting this wrong was measurable: hardcoding ENGINEERING for every declared
#: channel made the statistical detectors run on eight channels whose bounds came
#: from scenario data, taking scenario 5 from 7 findings to 12 and scenario 6 from
#: 5 to 12 — more findings resting on a weaker bound, presented with the same
#: weight as the rest.
_BOUND_ORIGIN_FOR_PROVENANCE: dict[str, BoundOrigin] = {
    "REPO_DETECTOR_TABLE": BoundOrigin.ENGINEERING,
    "SENTINEL_SAFETY_POLICY": BoundOrigin.ENGINEERING,
    "REPO_SCENARIO_DATA": BoundOrigin.UNKNOWN,
    "REPO_SIMULATOR_TABLE": BoundOrigin.UNKNOWN,
    "SENTINEL_CLASSIFICATION": BoundOrigin.UNKNOWN,
    "UNKNOWN": BoundOrigin.UNKNOWN,
}


def spec_from_definition(definition: ChannelDefinition) -> ChannelSpec:
    """Project a dictionary definition onto the detection-facing spec.

    Hard limits become the detector's limit_min/limit_max. ``nominal_range`` is
    deliberately NOT used: it describes where a healthy spacecraft sits, not
    where a violation begins, and five channels in the dictionary have a nominal
    band wider than their own hard limits. Using it for limit checks would change
    what counts as a violation.
    """
    return ChannelSpec(
        name=definition.channel_id,
        kind=_KIND_FOR_VALUE_CLASS[definition.value_class],
        subsystem=definition.subsystem.value,
        unit=definition.unit,
        limit_min=definition.hard_min,
        limit_max=definition.hard_max,
        expected_states=tuple(definition.expected_states),
        monotonic_non_decreasing=definition.monotonic_non_decreasing,
        max_rate_per_s=definition.max_rate_per_s,
        bound_origin=_BOUND_ORIGIN_FOR_PROVENANCE.get(
            definition.limits_provenance.value, BoundOrigin.UNKNOWN,
        ),
    )


#: Built from the dictionary. Nothing here is declared locally, so the detector
#: and the dictionary cannot disagree.
CHANNEL_SPECS: dict[str, ChannelSpec] = {
    channel_id: spec_from_definition(definition)
    for channel_id, definition in CHANNELS.items()
}


#: The channels the pre-Phase-2 detector was structurally unable to flag.
#: Referenced by the regression tests so the blind spots stay closed.
KNOWN_BLIND_SPOT_CHANNELS: tuple[str, ...] = (
    "SEU_counter",
    "Transponder_lock",
    "Star_tracker_status",
    "Fault_register",
    "Watchdog_counter",
)


def get_channel_spec(name: str) -> Optional[ChannelSpec]:
    """Return the spec for a channel, or None if the channel is unknown.

    Resolves aliases through the dictionary, so ``GYRO_A_RATE`` finds
    ``Gyro_rate_degs``. The returned spec always carries the canonical id.
    """
    spec = CHANNEL_SPECS.get(name)
    if spec is not None:
        return spec
    definition = get_channel(name)
    return CHANNEL_SPECS.get(definition.channel_id) if definition else None


def spec_or_inferred(
    name: str,
    nominal_min: float | None = None,
    nominal_max: float | None = None,
    baseline_derived_bounds: bool = False,
) -> ChannelSpec:
    """Return a channel's declared spec, or a minimal one for an unknown channel.

    For unknown channels (notably ESA-ADB's anonymized ``channel_*``), the
    reading's own bounds are used and the channel is treated as CONTINUOUS —
    the only kind that assumes nothing. Subsystem is the explicit string
    ``"UNKNOWN"``; no subsystem is inferred from the channel name, and no
    expected state is guessed. Either would be fabricating knowledge SENTINEL
    does not have about anonymized data.

    Args:
        name: Channel name or alias.
        nominal_min: Lower bound carried by the reading, if any.
        nominal_max: Upper bound carried by the reading, if any.
        baseline_derived_bounds: True when the caller knows the bounds were
            computed from observed statistics (ESA-ADB emits mean +/- 3 sigma).
            Only then may a statistical detector treat them as a sigma band.
    """
    spec = get_channel_spec(name)
    if spec is not None:
        return spec

    return ChannelSpec(
        name=name,
        kind=ChannelKind.CONTINUOUS,
        subsystem=UNKNOWN_SUBSYSTEM,
        unit=None,
        limit_min=nominal_min,
        limit_max=nominal_max,
        expected_states=(),
        monotonic_non_decreasing=False,
        max_rate_per_s=None,
        # An unknown channel's bounds have no stated origin unless the reading
        # also carries baseline statistics, which tells us they are statistical.
        bound_origin=(
            BoundOrigin.STATISTICAL if baseline_derived_bounds else BoundOrigin.UNKNOWN
        ),
    )


def channel_dictionary_status() -> dict:
    """Diagnostic summary, for tests and status endpoints.

    Includes the dictionary's own validation findings, so a caller inspecting the
    detector's channel view also sees the five documented nominal-versus-limits
    contradictions rather than having to know to look elsewhere.
    """
    from app.ingest.channel_dict import CHANNEL_DICT_VERSION, dictionary_status

    by_kind: dict[str, list[str]] = {}
    for name, spec in CHANNEL_SPECS.items():
        by_kind.setdefault(spec.kind.value, []).append(name)

    upstream = dictionary_status()
    return {
        "source": "app.ingest.channel_dict",
        "channel_dict_version": CHANNEL_DICT_VERSION,
        "total_channels": len(CHANNEL_SPECS),
        "counts_per_kind": {k: len(v) for k, v in sorted(by_kind.items())},
        "channels_per_kind": {k: sorted(v) for k, v in sorted(by_kind.items())},
        "known_blind_spots_covered": sorted(KNOWN_BLIND_SPOT_CHANNELS),
        "statistical_detection_disabled_for": sorted(
            n for n, s in CHANNEL_SPECS.items()
            if not s.statistical_detection_meaningful
        ),
        "rate_limit_declared_for": sorted(
            n for n, s in CHANNEL_SPECS.items() if s.max_rate_per_s is not None
        ),
        "channels_per_subsystem": upstream["channels_per_subsystem"],
        "dictionary_validation": upstream["validation"],
    }
