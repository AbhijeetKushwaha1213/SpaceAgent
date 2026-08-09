"""
SENTINEL — Spacecraft Channel Dictionary (app/ingest/channel_dict.py)

Phase 5. THE authoritative definition of every telemetry channel.

What this replaces
------------------
The same 21 channels were defined three times, and the copies had drifted:

    app/analytics/anomaly_detector.py  SATELLITE_NOMINAL_RANGES
        21 channels. Drove every limit check in the detector.

    simulation/fault_simulator.py      21 per-attribute dicts
        Stamped nominal_min / nominal_max onto every generated reading.

    app/agent/prompts.py               NOMINAL_THRESHOLDS
        Hand-written prose telling the LLM what the thresholds were.

Measured before this module existed: 17 of the 21 channels carried different
numbers in the first two tables, and the prompt's OBC_temp_C upper bound (50 °C)
disagreed with the detector's (60 °C). Three sources, no checker, no way for a
reader to know which one the system actually acted on.

nominal_range and hard_limits are DIFFERENT THINGS
--------------------------------------------------
This is what resolves the divergence without anyone having to invent physics:

    nominal_range   the band a healthy spacecraft sits in. What the simulator
                    samples from, and what an operator reads as "normal".

    hard_limits     the band outside which something is wrong. What a detector
                    compares against to raise a limit violation.

The two tables were not really disagreeing about one quantity — they were each
describing a different one and calling it "nominal". Recording both, separately,
keeps detector behaviour and generated data bit-identical while making the
relationship between them inspectable for the first time.

It also makes five genuine contradictions visible, where a channel's nominal band
falls OUTSIDE its own hard limits:

    Attitude_error_deg   nominal 0 – 1.0      hard 0 – 0.01
    Component_temp_C     nominal -10 – 70     hard -20 – 65
    Gyro_rate_degs       nominal -0.5 – 0.5   hard 0 – 7
    SEU_counter          nominal 0 – 5        hard 0 – 0
    V_bus                nominal 27.5 – 32.5  hard 26.6 – 29.4

These are not resolved here. Resolving them means deciding real spacecraft
physics, and inventing a number would be worse than surfacing the conflict.
``validate_dictionary()`` reports each one, ``ChannelDefinition.nominal_within_
hard_limits`` exposes it per channel, and ``docs/phase5_channel_conflicts.md``
records the evidence. See that document for the measured consequence: telemetry
the simulator labels NOMINAL already contains two channels the detector calls
anomalous.

Provenance is per-number, not per-file
--------------------------------------
Every value carries a ``Provenance`` saying where it came from, because the
fields differ sharply in how well grounded they are. The limits were already in
the codebase and are preserved exactly. Sampling rates and criticality did not
exist anywhere and are SENTINEL design classifications, not spacecraft
specifications — they say so, rather than borrowing authority they do not have.

Unknown channels
----------------
``resolve_channel()`` never guesses. An unrecognised channel — notably ESA-ADB's
anonymized ``channel_41`` names — comes back with ``subsystem=Subsystem.UNKNOWN``
and ``provenance=Provenance.UNKNOWN``, with whatever bounds the reading itself
carried and nothing more. There is no code path that assigns a subsystem by
pattern-matching a channel name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional

CHANNEL_DICT_VERSION = "1.0.0"
"""Version of the channel dictionary.

Bump the MAJOR component when a channel is removed or a hard limit changes,
because either invalidates stored detection results. MINOR for a new channel or
a new alias, PATCH for descriptions.
"""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — VOCABULARIES
# ═══════════════════════════════════════════════════════════════════════════

class Subsystem(str, Enum):
    """Spacecraft subsystem a channel belongs to.

    UNKNOWN is a member on purpose. An anonymized or unrecognised channel must be
    representable without either dropping it or having a subsystem guessed for
    it, and a caller reading UNKNOWN can tell the difference between "we do not
    know" and "nobody has filled this in yet".
    """

    EPS = "EPS"          # Electrical Power System
    AOCS = "AOCS"        # Attitude and Orbit Control System
    TCS = "TCS"          # Thermal Control System
    OBC = "OBC"          # On-Board Computer
    COMMS = "COMMS"      # Communications
    PYLD = "PYLD"        # Payload
    UNKNOWN = "UNKNOWN"  # Not attributable — never inferred from the name

    @property
    def is_known(self) -> bool:
        return self is not Subsystem.UNKNOWN


#: AOCS and ADCS name the same subsystem. The rest of the repository — the
#: command registry, SubsystemID in api/models.py, safety.py — uses ADCS, and the
#: Phase 5 specification uses AOCS. Rather than rename 81 registry entries and
#: risk breaking the Phase 1 conflict checker, both spellings resolve to one
#: member and this map is the single place the equivalence is stated.
SUBSYSTEM_ALIASES: dict[str, Subsystem] = {
    "ADCS": Subsystem.AOCS,
    "AOCS": Subsystem.AOCS,
    "EPS": Subsystem.EPS,
    "TCS": Subsystem.TCS,
    "OBC": Subsystem.OBC,
    "COMMS": Subsystem.COMMS,
    "PYLD": Subsystem.PYLD,
    "PAYLOAD": Subsystem.PYLD,
}


def resolve_subsystem(name: object) -> Subsystem:
    """Map a subsystem spelling to a member. Unrecognised input is UNKNOWN."""
    if isinstance(name, Subsystem):
        return name
    if name is None:
        return Subsystem.UNKNOWN
    return SUBSYSTEM_ALIASES.get(str(name).strip().upper(), Subsystem.UNKNOWN)


class ValueClass(str, Enum):
    """Whether a channel is continuous or discrete, and which discrete sort.

    This is what decides which detector applies. Phase 2 established the cost of
    getting it wrong: running a Gaussian z-score over a counter or a status code
    produced blind spots on the five channels the system prompt calls most
    diagnostic.
    """

    CONTINUOUS = "CONTINUOUS"
    """Varies smoothly. Statistical and temporal detection are meaningful."""

    COUNTER = "COUNTER"
    """Monotonically non-decreasing event count. Any increase matters; a
    Gaussian test on it does not."""

    STATUS = "STATUS"
    """Enumerated state or fault code. Only set membership matters."""

    FLAG = "FLAG"
    """Boolean."""

    @property
    def is_discrete(self) -> bool:
        return self in (ValueClass.STATUS, ValueClass.FLAG)

    @property
    def statistical_detection_meaningful(self) -> bool:
        return self is ValueClass.CONTINUOUS


class DataType(str, Enum):
    """On-the-wire type of the value."""

    FLOAT = "FLOAT"
    INT = "INT"
    BOOL = "BOOL"
    BITMASK = "BITMASK"
    ENUM = "ENUM"


class Criticality(str, Enum):
    """How consequential losing or misreading this channel is.

    A SENTINEL operational classification, NOT a spacecraft criticality
    assignment from a real FMECA. It is derived by a stated rule so it is
    reproducible rather than a matter of taste:

      CRITICAL  the channel gates a deterministic safety precondition, or its
                value directly triggers safe-mode entry
      HIGH      the channel appears in a documented fault signature as primary
                evidence
      MEDIUM    diagnostic supporting evidence
      LOW       contextual or housekeeping

    ``Provenance.SENTINEL_CLASSIFICATION`` marks every criticality value, so no
    reader mistakes it for vehicle documentation.
    """

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SamplingRate(str, Enum):
    """Expected cadence class.

    Deliberately a class rather than a number in Hz. The repository contains no
    spacecraft sampling specification; what it contains is telemetry at 10–300 s
    offsets. Stating "0.1 Hz" would imply a measured figure that does not exist,
    whereas a band is what is actually known.
    """

    HIGH_RATE = "HIGH_RATE"        # sub-second to ~1 s
    MEDIUM_RATE = "MEDIUM_RATE"    # ~1 s to ~30 s
    LOW_RATE = "LOW_RATE"          # ~30 s to a few minutes
    ON_CHANGE = "ON_CHANGE"        # emitted when the value changes
    UNKNOWN = "UNKNOWN"


class Provenance(str, Enum):
    """Where a channel's numbers came from. Applied per definition."""

    REPO_DETECTOR_TABLE = "REPO_DETECTOR_TABLE"
    """Preserved byte-for-byte from anomaly_detector.SATELLITE_NOMINAL_RANGES,
    which drove every limit check before Phase 5. Carried over unchanged so
    detector behaviour is provably identical."""

    REPO_SIMULATOR_TABLE = "REPO_SIMULATOR_TABLE"
    """Preserved from simulation/fault_simulator.py, which stamps these bounds
    onto generated readings. Carried over unchanged so generated datasets are
    bit-identical."""

    REPO_SCENARIO_DATA = "REPO_SCENARIO_DATA"
    """Read from the bounds a shipped preset scenario already declared for the
    channel. Weaker than the two tables above: those were applied to every dump,
    whereas these appeared in one scenario. Adopted rather than left unattributed,
    because the repository already acted on them."""

    SENTINEL_SAFETY_POLICY = "SENTINEL_SAFETY_POLICY"
    """A threshold the deterministic safety validator enforces, defined by
    SENTINEL. Not a vehicle limit."""

    SENTINEL_CLASSIFICATION = "SENTINEL_CLASSIFICATION"
    """A SENTINEL judgement — criticality, sampling class, value class. Derived
    by a documented rule, not measured and not sourced from a vehicle
    specification."""

    UNKNOWN = "UNKNOWN"
    """Provenance could not be established. Never treated as authoritative."""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — THE DEFINITION
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ChannelDefinition:
    """Everything SENTINEL knows about one telemetry channel."""

    channel_id: str
    display_name: str
    subsystem: Subsystem
    unit: Optional[str]
    datatype: DataType
    value_class: ValueClass

    #: The band a healthy spacecraft sits in. ``(None, None)`` when unspecified.
    nominal_range: tuple[Optional[float], Optional[float]]

    #: The band outside which a limit violation is raised. Compared exactly,
    #: never via a z-score — that conflation was the Phase 2 defect.
    hard_limits: tuple[Optional[float], Optional[float]]

    criticality: Criticality
    sampling_rate: SamplingRate
    description: str
    physical_meaning: str

    #: Where the numbers came from. ``limits_provenance`` is separate because the
    #: limits and the nominal band came from different tables.
    provenance: Provenance
    limits_provenance: Provenance

    #: For STATUS / FLAG / COUNTER: the value(s) that mean healthy. Empty means
    #: no expected-state check applies.
    expected_states: tuple[float, ...] = ()

    #: Counters only increase; a decrease means a reset or a corrupt reading.
    monotonic_non_decreasing: bool = False

    #: Largest plausible change per second, where the declared span gives a
    #: basis. None disables the rate check rather than inventing a threshold.
    max_rate_per_s: Optional[float] = None

    #: Thresholds the DETERMINISTIC SAFETY VALIDATOR enforces on this channel,
    #: as ``(floor, ceiling)``. Distinct from hard_limits: a hard limit says a
    #: reading is out of range, whereas a safety threshold says SENTINEL will
    #: refuse to issue certain commands. They differ numerically and in kind —
    #: SoC_pct has a hard minimum of 20% but the validator refuses power-hungry
    #: commands below 15%, and Component_temp_C has a hard maximum of 65 degC but
    #: the validator permits only thermal remedies above 85 degC.
    #:
    #: Held here so the validator reads its thresholds from the channel
    #: dictionary rather than from constants of its own.
    safety_limits: tuple[Optional[float], Optional[float]] = (None, None)
    safety_limits_provenance: Provenance = Provenance.UNKNOWN

    #: Alternative spellings seen in payloads, prompts and scenario files.
    aliases: tuple[str, ...] = ()

    #: Operator-facing caveat, when one is warranted.
    notes: Optional[str] = None

    # ── derived views ──────────────────────────────────────────────────────

    @property
    def hard_min(self) -> Optional[float]:
        return self.hard_limits[0]

    @property
    def hard_max(self) -> Optional[float]:
        return self.hard_limits[1]

    @property
    def nominal_min(self) -> Optional[float]:
        return self.nominal_range[0]

    @property
    def nominal_max(self) -> Optional[float]:
        return self.nominal_range[1]

    @property
    def is_discrete(self) -> bool:
        return self.value_class.is_discrete

    @property
    def statistical_detection_meaningful(self) -> bool:
        return self.value_class.statistical_detection_meaningful

    @property
    def is_known(self) -> bool:
        """False for a definition synthesized for an unrecognised channel."""
        return self.subsystem.is_known and self.provenance is not Provenance.UNKNOWN

    @property
    def nominal_within_hard_limits(self) -> Optional[bool]:
        """Whether the nominal band sits inside the hard limits.

        None when either band is unspecified — absent data is not agreement.
        False marks one of the five contradictions this dictionary surfaced;
        ``validate_dictionary()`` lists them.
        """
        nlo, nhi = self.nominal_range
        hlo, hhi = self.hard_limits
        if nlo is None or nhi is None or hlo is None or hhi is None:
            return None
        return nlo >= hlo and nhi <= hhi

    @property
    def degenerate_hard_limits(self) -> bool:
        """True when hard_min == hard_max.

        These are the channels a range-derived sigma reduced to zero, making the
        pre-Phase-2 z-score permanently blind to them.
        """
        return (
            self.hard_min is not None
            and self.hard_max is not None
            and self.hard_min == self.hard_max
        )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — THE CHANNELS
# ═══════════════════════════════════════════════════════════════════════════
#
# hard_limits are copied verbatim from anomaly_detector.SATELLITE_NOMINAL_RANGES.
# nominal_range is copied verbatim from simulation/fault_simulator.py.
# Neither is adjusted. A test asserts both correspondences hold, so this table
# cannot silently drift from the behaviour it replaced.
#
# max_rate_per_s values are carried over from Phase 2's detection/channels.py,
# where they were derived as roughly one tenth of the declared span per second.

def _ch(**kw) -> ChannelDefinition:
    return ChannelDefinition(**kw)


_DEFINITIONS: tuple[ChannelDefinition, ...] = (

    # ── EPS ──────────────────────────────────────────────────────────────
    _ch(
        channel_id="V_bat", display_name="Battery Voltage",
        subsystem=Subsystem.EPS, unit="V", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(28.0, 33.0), hard_limits=(28.0, 33.6),
        criticality=Criticality.CRITICAL, sampling_rate=SamplingRate.MEDIUM_RATE,
        description="Main battery terminal voltage.",
        physical_meaning=(
            "Tracks stored energy and battery health. A sustained fall indicates "
            "the arrays are not covering the load; a collapse precedes bus "
            "undervoltage and safe-mode entry."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        max_rate_per_s=0.56, aliases=("BATTERY_VOLTAGE", "V_BAT", "vbat"),
    ),
    _ch(
        channel_id="I_sa", display_name="Solar Array Current",
        subsystem=Subsystem.EPS, unit="A", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(3.5, 6.5), hard_limits=(0.0, 12.0),
        criticality=Criticality.HIGH, sampling_rate=SamplingRate.MEDIUM_RATE,
        description="Current delivered by the solar array.",
        physical_meaning=(
            "Power generation. Near-zero current while sunlit means an array or "
            "regulator fault, not an eclipse — the distinction requires the "
            "orbital context, not this channel alone."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        max_rate_per_s=1.2,
        aliases=("SOLAR_ARRAY_CURRENT", "I_SA", "SOLAR_ARRAY_CURRENT_A"),
        notes=(
            "0 A is inside the hard limits because eclipse is a legitimate "
            "operating state. Distinguishing eclipse from an array fault needs "
            "operating_context.eclipse_fraction, so no limit check can do it."
        ),
    ),
    _ch(
        channel_id="SoC_pct", display_name="Battery State of Charge",
        subsystem=Subsystem.EPS, unit="%", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(60.0, 95.0), hard_limits=(20.0, 100.0),
        criticality=Criticality.CRITICAL, sampling_rate=SamplingRate.LOW_RATE,
        description="Battery state of charge as a percentage of capacity.",
        physical_meaning=(
            "Remaining energy margin. Below the safety floor, power-hungry "
            "recovery actions risk deepening the fault instead of clearing it."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        max_rate_per_s=8.0, aliases=("SOC", "BATTERY_SOC", "soc_pct",
                                     "BATTERY_SOC_PCT"),
        safety_limits=(15.0, None),
        safety_limits_provenance=Provenance.SENTINEL_SAFETY_POLICY,
        notes=(
            "The deterministic safety validator refuses power-hungry commands "
            "below the 15% safety floor above. That floor is a SENTINEL policy, "
            "stricter than the 20% hard limit, and is read from here by "
            "app/validation/conditions.py."
        ),
    ),
    _ch(
        channel_id="V_bus", display_name="Bus Voltage",
        subsystem=Subsystem.EPS, unit="V", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(27.5, 32.5), hard_limits=(26.6, 29.4),
        criticality=Criticality.CRITICAL, sampling_rate=SamplingRate.MEDIUM_RATE,
        description="Regulated main bus voltage supplied to the loads.",
        physical_meaning=(
            "What every subsystem actually receives. Out-of-range bus voltage "
            "trips EPS FDIR directly."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        max_rate_per_s=0.28, aliases=("BUS_VOLTAGE", "V_BUS", "BUS_VOLTAGE_V"),
    ),

    # ── AOCS ─────────────────────────────────────────────────────────────
    _ch(
        channel_id="Gyro_rate_degs", display_name="Angular Velocity",
        subsystem=Subsystem.AOCS, unit="deg/s", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(-0.5, 0.5), hard_limits=(0.0, 7.0),
        criticality=Criticality.CRITICAL, sampling_rate=SamplingRate.HIGH_RATE,
        description="Body angular rate reported by the gyroscope.",
        physical_meaning=(
            "Rotation rate about the measured axis. NaN or a frozen value means "
            "the sensor has failed rather than that the vehicle is still, and "
            "attitude actuation must not proceed on invalid rate data."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        max_rate_per_s=0.7,
        aliases=("GYRO_A_RATE", "GYRO_B_RATE", "gyro_a_rate", "gyro_b_rate",
                 "ANGULAR_VELOCITY", "GYRO_X_RATE",
                 "IMU_A_ANGULAR_RATE_X"),
        notes=(
            "The nominal band is signed while the hard limits are not, so a "
            "legitimate negative rate reads as a limit violation. One of the "
            "five contradictions in docs/phase5_channel_conflicts.md; unresolved "
            "because the fix is a physics decision, not a code decision."
        ),
    ),
    _ch(
        channel_id="Attitude_error_deg", display_name="Attitude Error",
        subsystem=Subsystem.AOCS, unit="deg", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(0.0, 1.0), hard_limits=(0.0, 0.01),
        criticality=Criticality.CRITICAL, sampling_rate=SamplingRate.MEDIUM_RATE,
        description="Angle between commanded and estimated attitude.",
        physical_meaning=(
            "Pointing accuracy. A growing error is the downstream consequence of "
            "an attitude-knowledge failure, so it usually confirms a root cause "
            "found elsewhere rather than being the root cause itself."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        max_rate_per_s=0.001,
        aliases=("ATTITUDE_ERROR", "ATTITUDE_ERROR_DEG",
                 "ATTITUDE_DEVIATION_DEG"),
        notes=(
            "The nominal band is 100x the hard limit, so simulator-generated "
            "nominal telemetry is always in violation. See "
            "docs/phase5_channel_conflicts.md."
        ),
    ),
    _ch(
        channel_id="RW_speed_rpm", display_name="Reaction Wheel Speed",
        subsystem=Subsystem.AOCS, unit="rpm", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(-5000.0, 5000.0), hard_limits=(-6000.0, 6000.0),
        criticality=Criticality.HIGH, sampling_rate=SamplingRate.MEDIUM_RATE,
        description="Reaction wheel rotation rate; sign gives direction.",
        physical_meaning=(
            "Stored angular momentum. Approaching the limit means saturation, "
            "after which the wheel can no longer absorb disturbance torque and "
            "pointing degrades."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        max_rate_per_s=1200.0,
        aliases=("RWA_SPEED_RPM", "REACTION_WHEEL_SPEED", "RW_SPEED"),
    ),
    _ch(
        channel_id="Sun_sensor_angle_deg", display_name="Sun Sensor Angle",
        subsystem=Subsystem.AOCS, unit="deg", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(0.0, 90.0), hard_limits=(0.0, 90.0),
        criticality=Criticality.MEDIUM, sampling_rate=SamplingRate.MEDIUM_RATE,
        description="Angle between the sun vector and the sensor boresight.",
        physical_meaning=(
            "Coarse attitude reference and array illumination geometry. Needed to "
            "tell an array fault from an eclipse."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        max_rate_per_s=9.0, aliases=("SUN_SENSOR_ANGLE", "sun_sensor_angle_deg"),
    ),
    _ch(
        channel_id="Star_tracker_status", display_name="Star Tracker Status",
        subsystem=Subsystem.AOCS, unit="state", datatype=DataType.ENUM,
        value_class=ValueClass.STATUS,
        nominal_range=(0.0, 0.0), hard_limits=(0.0, 0.0),
        criticality=Criticality.HIGH, sampling_rate=SamplingRate.ON_CHANGE,
        description="Star tracker health code. 0 is healthy.",
        physical_meaning=(
            "Fine attitude determination availability. A non-zero code means the "
            "tracker is not delivering a valid attitude solution."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        expected_states=(0.0,),
        aliases=("STAR_TRACKER_STATUS",),
        notes=(
            "Degenerate limits (0, 0) reduced a range-derived sigma to zero, so "
            "the pre-Phase-2 z-score reported 0.0 for every value and could "
            "never flag this channel. Checked by set membership now."
        ),
    ),
    _ch(
        channel_id="SEU_counter", display_name="SEU Counter",
        subsystem=Subsystem.AOCS, unit="count", datatype=DataType.INT,
        value_class=ValueClass.COUNTER,
        nominal_range=(0.0, 5.0), hard_limits=(0.0, 0.0),
        criticality=Criticality.HIGH, sampling_rate=SamplingRate.ON_CHANGE,
        description="Cumulative count of detected single-event upsets.",
        physical_meaning=(
            "Radiation hits on the processor. A step increase immediately before "
            "a sensor fault points to a radiation-induced upset rather than "
            "hardware degradation, which is what separates a software reset from "
            "a unit swap."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        expected_states=(0.0,), monotonic_non_decreasing=True,
        aliases=("SEU_COUNTER", "seu_counter"),
        notes=(
            "The nominal band permits up to 5 while the hard limit is exactly 0, "
            "so simulator nominal data violates it. See "
            "docs/phase5_channel_conflicts.md."
        ),
    ),

    # ── TCS ──────────────────────────────────────────────────────────────
    _ch(
        channel_id="Component_temp_C", display_name="Component Temperature",
        subsystem=Subsystem.TCS, unit="degC", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(-10.0, 70.0), hard_limits=(-20.0, 65.0),
        criticality=Criticality.CRITICAL, sampling_rate=SamplingRate.LOW_RATE,
        description="Temperature of a monitored component.",
        physical_meaning=(
            "Thermal margin. A rising trend with the heater commanded off "
            "indicates a stuck heater or a lost radiator path; sustained "
            "over-temperature causes permanent damage."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        max_rate_per_s=8.5,
        aliases=("COMPONENT_TEMP_C", "TEMP_C", "temperature_c", "temp_c"),
        safety_limits=(None, 85.0),
        safety_limits_provenance=Provenance.SENTINEL_SAFETY_POLICY,
        notes=(
            "The safety ceiling above is the survival limit: past it, the "
            "validator permits only thermal remedies and observations. It is a "
            "SENTINEL policy rather than this hard limit, and is read from here "
            "by app/validation/conditions.py. The nominal band also exceeds the "
            "hard maximum — see docs/phase5_channel_conflicts.md."
        ),
    ),
    _ch(
        channel_id="Panel_temp_C", display_name="Solar Panel Temperature",
        subsystem=Subsystem.TCS, unit="degC", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(None, None), hard_limits=(-40.0, 70.0),
        criticality=Criticality.MEDIUM, sampling_rate=SamplingRate.LOW_RATE,
        description="Solar panel substrate temperature.",
        physical_meaning=(
            "Panel efficiency falls as temperature rises, so a hot panel reduces "
            "array current without any array fault — one of the few ways I_sa can "
            "drop for a thermal reason."
        ),
        provenance=Provenance.REPO_SCENARIO_DATA,
        limits_provenance=Provenance.REPO_SCENARIO_DATA,
        aliases=("PANEL_TEMP_C", "SOLAR_PANEL_TEMP"),
    ),
    _ch(
        channel_id="Battery_temp_C", display_name="Battery Temperature",
        subsystem=Subsystem.TCS, unit="degC", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(None, None), hard_limits=(0.0, 45.0),
        criticality=Criticality.CRITICAL, sampling_rate=SamplingRate.LOW_RATE,
        description="Battery pack temperature.",
        physical_meaning=(
            "Cells lose capacity below freezing and degrade permanently when hot, "
            "so this bounds what the battery can actually deliver regardless of "
            "its state of charge."
        ),
        provenance=Provenance.REPO_SCENARIO_DATA,
        limits_provenance=Provenance.REPO_SCENARIO_DATA,
        aliases=("BATTERY_TEMP_C",),
    ),
    _ch(
        channel_id="Radiator_eff_pct", display_name="Radiator Efficiency",
        subsystem=Subsystem.TCS, unit="%", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(None, None), hard_limits=(60.0, 100.0),
        criticality=Criticality.MEDIUM, sampling_rate=SamplingRate.LOW_RATE,
        description="Radiator heat-rejection efficiency.",
        physical_meaning=(
            "How well waste heat is being shed. Falling efficiency explains a "
            "temperature rise that no heater fault accounts for."
        ),
        provenance=Provenance.REPO_SCENARIO_DATA,
        limits_provenance=Provenance.REPO_SCENARIO_DATA,
        aliases=("RADIATOR_EFF_PCT",),
    ),
    _ch(
        channel_id="Heater_power_W", display_name="Heater Power",
        subsystem=Subsystem.TCS, unit="W", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(0.0, 10.0), hard_limits=(0.0, 50.0),
        criticality=Criticality.MEDIUM, sampling_rate=SamplingRate.LOW_RATE,
        description="Electrical power drawn by the heater circuit.",
        physical_meaning=(
            "Thermal control effort and a load on the power budget. Power drawn "
            "while the enable flag is clear indicates a stuck heater."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        max_rate_per_s=5.0, aliases=("HEATER_POWER_W", "HEATER_POWER"),
    ),
    _ch(
        channel_id="Heater_enable_flag", display_name="Heater Enable Flag",
        subsystem=Subsystem.TCS, unit="flag", datatype=DataType.BOOL,
        value_class=ValueClass.FLAG,
        nominal_range=(0.0, 0.0), hard_limits=(0.0, 1.0),
        criticality=Criticality.MEDIUM, sampling_rate=SamplingRate.ON_CHANGE,
        description="Commanded heater state. 0 off, 1 on.",
        physical_meaning=(
            "What the heater was told to do. Compared against Heater_power_W and "
            "Component_temp_C, it separates a commanded heat-up from a stuck "
            "element."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        expected_states=(0.0, 1.0),
        aliases=("HEATER_ENABLE_FLAG", "HEATER_ENABLE"),
        notes=(
            "Both states are legitimate, so the expected-state set holds 0 and 1 "
            "and this channel is never flagged on state alone. The simulator's "
            "nominal band of (0, 0) reflects its own default, not a constraint."
        ),
    ),

    # ── OBC ──────────────────────────────────────────────────────────────
    _ch(
        channel_id="CPU_load_pct", display_name="CPU Load",
        subsystem=Subsystem.OBC, unit="%", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(10.0, 70.0), hard_limits=(0.0, 70.0),
        criticality=Criticality.HIGH, sampling_rate=SamplingRate.MEDIUM_RATE,
        description="On-board computer processor utilisation.",
        physical_meaning=(
            "Processing headroom. Saturation sustained across samples indicates a "
            "runaway task, which starves the watchdog kick and ends in a reset."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        max_rate_per_s=7.0, aliases=("CPU_LOAD", "CPU_LOAD_PCT", "cpu_load_pct"),
    ),
    _ch(
        channel_id="Memory_usage_MB", display_name="Memory Usage",
        subsystem=Subsystem.OBC, unit="MB", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(50.0, 200.0), hard_limits=(0.0, 500.0),
        criticality=Criticality.MEDIUM, sampling_rate=SamplingRate.LOW_RATE,
        description="Resident memory in use by flight software.",
        physical_meaning=(
            "A monotonic climb is a leak. The trend matters more than the level, "
            "since the level can be legitimately high after a task starts."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        max_rate_per_s=50.0, aliases=("MEMORY_USAGE_MB", "MEMORY_USAGE"),
    ),
    _ch(
        channel_id="Watchdog_counter", display_name="Watchdog Counter",
        subsystem=Subsystem.OBC, unit="count", datatype=DataType.INT,
        value_class=ValueClass.COUNTER,
        nominal_range=(0.0, 200.0), hard_limits=(0.0, 1000.0),
        criticality=Criticality.CRITICAL, sampling_rate=SamplingRate.MEDIUM_RATE,
        description="Watchdog timer tick count since the last kick.",
        physical_meaning=(
            "How close the processor is to an unattended reset. Reaching the "
            "limit means flight software stopped servicing the watchdog."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        monotonic_non_decreasing=True,
        aliases=("WATCHDOG_COUNTER", "WATCHDOG_TIMER", "watchdog_counter"),
        notes=(
            "The 0–1000 span gave a range-derived sigma of 166.7, so an overflow "
            "at 1002 scored z=2.85 and fell under the 3.0 threshold. No "
            "realistic overflow was detectable. Checked by comparison now."
        ),
    ),
    _ch(
        channel_id="Fault_register", display_name="Fault Register",
        subsystem=Subsystem.OBC, unit="bitmask", datatype=DataType.BITMASK,
        value_class=ValueClass.STATUS,
        nominal_range=(0.0, 0.0), hard_limits=(0.0, 0.0),
        criticality=Criticality.CRITICAL, sampling_rate=SamplingRate.ON_CHANGE,
        description="Bitmask of FDIR flags currently asserted.",
        physical_meaning=(
            "Which on-board fault monitors have tripped. Any set bit is a fault "
            "the vehicle itself has already declared, so this is corroboration "
            "from the spacecraft rather than an inference."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        expected_states=(0.0,),
        aliases=("FAULT_REGISTER", "fault_register"),
        notes=(
            "Degenerate limits (0, 0) made the old z-score permanently blind. "
            "Bit semantics are not decoded here; a bitmask is not an ordinal, so "
            "no magnitude comparison is meaningful beyond zero versus non-zero."
        ),
    ),
    _ch(
        channel_id="OBC_temp_C", display_name="OBC Temperature",
        subsystem=Subsystem.OBC, unit="degC", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(10.0, 50.0), hard_limits=(-10.0, 60.0),
        criticality=Criticality.HIGH, sampling_rate=SamplingRate.LOW_RATE,
        description="On-board computer baseplate temperature.",
        physical_meaning=(
            "Processor thermal margin. Over-temperature causes computation "
            "errors before it causes permanent damage, so it can masquerade as a "
            "software fault."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        max_rate_per_s=7.0, aliases=("TEMP_OBC", "OBC_TEMP_C", "OBC_temp"),
        notes=(
            "The LLM prompt stated a nominal upper bound of 50 degC while the "
            "detector used 60 degC. Phase 5 removed the prompt's numbers, so "
            "there is now one place to disagree with."
        ),
    ),
    _ch(
        channel_id="Safe_mode_entry_count", display_name="Safe Mode Entry Count",
        subsystem=Subsystem.OBC, unit="count", datatype=DataType.INT,
        value_class=ValueClass.COUNTER,
        nominal_range=(0.0, 3.0), hard_limits=(0.0, 5.0),
        criticality=Criticality.MEDIUM, sampling_rate=SamplingRate.ON_CHANGE,
        description="Cumulative safe-mode entries for the mission.",
        physical_meaning=(
            "Repeated entries suggest an unresolved recurring fault rather than a "
            "one-off event, which changes the recovery strategy."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        monotonic_non_decreasing=True,
        aliases=("SAFE_MODE_ENTRY_COUNT",),
    ),

    # ── COMMS ────────────────────────────────────────────────────────────
    _ch(
        channel_id="Transponder_lock", display_name="Transponder Lock",
        subsystem=Subsystem.COMMS, unit="flag", datatype=DataType.BOOL,
        value_class=ValueClass.FLAG,
        nominal_range=(1.0, 1.0), hard_limits=(1.0, 1.0),
        criticality=Criticality.CRITICAL, sampling_rate=SamplingRate.ON_CHANGE,
        description="Receiver carrier lock. 1 locked, 0 no lock.",
        physical_meaning=(
            "Whether the ground can command the vehicle at all. Without lock, no "
            "recovery command can be uplinked, so any action that could delay "
            "regaining it must be refused."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        expected_states=(1.0,),
        aliases=("TRANSPONDER_LOCK", "transponder_lock"),
        notes=(
            "Degenerate limits (1, 1) made the old z-score blind to loss of "
            "lock — a value of 0 scored z=0.0. Checked by set membership now. "
            "COMMS_LINK_STATUS is an alias of Link_status, not of this channel: "
            "carrier lock and overall link state are different measurements, and "
            "the link can be down with the carrier still locked."
        ),
    ),
    # The four channels below appear only in preset scenario 6
    # (COMMS_TRANSPONDER_LOSS), which already declared bounds for them. Adopting
    # those bounds is what lets them carry a subsystem and a unit; leaving them
    # out would have meant the repository shipped telemetry its own dictionary
    # could not attribute. No nominal operating band is claimed for them, because
    # no table in the repository ever stated one.
    _ch(
        channel_id="Link_margin_dB", display_name="Link Margin",
        subsystem=Subsystem.COMMS, unit="dB", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(None, None), hard_limits=(3.0, 20.0),
        criticality=Criticality.HIGH, sampling_rate=SamplingRate.MEDIUM_RATE,
        description="Downlink margin above the minimum detectable signal.",
        physical_meaning=(
            "Headroom before the link fails. A negative margin means the ground "
            "station cannot close the link, so commands will not arrive even "
            "though the transmitter is working."
        ),
        provenance=Provenance.REPO_SCENARIO_DATA,
        limits_provenance=Provenance.REPO_SCENARIO_DATA,
        aliases=("LINK_MARGIN_DB", "LINK_MARGIN"),
    ),
    _ch(
        channel_id="Bit_error_rate", display_name="Bit Error Rate",
        subsystem=Subsystem.COMMS, unit="ratio", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(None, None), hard_limits=(0.0, 0.001),
        criticality=Criticality.HIGH, sampling_rate=SamplingRate.MEDIUM_RATE,
        description="Fraction of received bits decoded in error.",
        physical_meaning=(
            "Link quality after error correction. A rising rate corrupts uplinked "
            "commands before the link drops entirely, so a recovery command may "
            "be received wrongly rather than not at all."
        ),
        provenance=Provenance.REPO_SCENARIO_DATA,
        limits_provenance=Provenance.REPO_SCENARIO_DATA,
        aliases=("BIT_ERROR_RATE", "BER"),
    ),
    _ch(
        channel_id="Antenna_pointing_error_deg",
        display_name="Antenna Pointing Error",
        subsystem=Subsystem.COMMS, unit="deg", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(None, None), hard_limits=(0.0, 1.0),
        criticality=Criticality.HIGH, sampling_rate=SamplingRate.MEDIUM_RATE,
        description="Angle between the antenna boresight and the ground station.",
        physical_meaning=(
            "Mispointing explains a weak link without any transponder fault, "
            "which changes the recovery from a transponder swap to an attitude "
            "correction."
        ),
        provenance=Provenance.REPO_SCENARIO_DATA,
        limits_provenance=Provenance.REPO_SCENARIO_DATA,
        aliases=("ANTENNA_POINTING_ERROR_DEG",),
    ),
    _ch(
        channel_id="RF_power_dBm", display_name="Received RF Power",
        subsystem=Subsystem.COMMS, unit="dBm", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(None, None), hard_limits=(-95.0, -70.0),
        criticality=Criticality.HIGH, sampling_rate=SamplingRate.MEDIUM_RATE,
        description="Received radio-frequency power at the transponder input.",
        physical_meaning=(
            "Absolute signal strength. Separates a weak uplink from a healthy "
            "uplink that the receiver is failing to decode."
        ),
        provenance=Provenance.REPO_SCENARIO_DATA,
        limits_provenance=Provenance.REPO_SCENARIO_DATA,
        aliases=("RF_POWER_DBM",),
    ),
    _ch(
        channel_id="Transponder_temp_C", display_name="Transponder Temperature",
        subsystem=Subsystem.COMMS, unit="degC", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(None, None), hard_limits=(-10.0, 50.0),
        criticality=Criticality.MEDIUM, sampling_rate=SamplingRate.LOW_RATE,
        description="Transponder unit temperature.",
        physical_meaning=(
            "Over-temperature degrades transmitter output before the unit fails, "
            "so it can look like a link problem whose cause is thermal."
        ),
        provenance=Provenance.REPO_SCENARIO_DATA,
        limits_provenance=Provenance.REPO_SCENARIO_DATA,
        aliases=("TRANSPONDER_TEMP_C",),
    ),
    _ch(
        channel_id="Link_status", display_name="Link Status",
        subsystem=Subsystem.COMMS, unit="flag", datatype=DataType.BOOL,
        value_class=ValueClass.FLAG,
        nominal_range=(None, None), hard_limits=(1.0, 1.0),
        criticality=Criticality.CRITICAL, sampling_rate=SamplingRate.ON_CHANGE,
        description="Overall ground link state. 1 up, 0 down.",
        physical_meaning=(
            "Whether a usable two-way link exists. Broader than transponder "
            "lock, which reports only carrier acquisition: the link can be down "
            "with the transponder locked if decoding is failing."
        ),
        provenance=Provenance.REPO_SCENARIO_DATA,
        limits_provenance=Provenance.REPO_SCENARIO_DATA,
        expected_states=(1.0,),
        aliases=("LINK_STATUS", "COMMS_LINK_STATUS"),
        notes=(
            "The expected state is derived from preset scenario 6, which ships a "
            "value of 0 labelled CRITICAL during a transponder-loss event; the "
            "scenario itself therefore asserts that 0 is the fault state. Before "
            "Phase 5 this channel was in no dictionary, so it carried no bounds "
            "and the detector reported NOTHING for it — a loss of link went "
            "unflagged in the very scenario built around losing the link. Same "
            "class of blind spot Phase 2 closed for Transponder_lock."
        ),
    ),
    _ch(
        channel_id="SNR_dB", display_name="Link Signal-to-Noise Ratio",
        subsystem=Subsystem.COMMS, unit="dB", datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(10.0, 25.0), hard_limits=(10.0, 40.0),
        criticality=Criticality.HIGH, sampling_rate=SamplingRate.MEDIUM_RATE,
        description="Downlink signal-to-noise ratio.",
        physical_meaning=(
            "Link margin, and the leading indicator of a degrading link. It "
            "falls before lock is lost, so it is where an impending loss of "
            "contact shows up first."
        ),
        provenance=Provenance.REPO_SIMULATOR_TABLE,
        limits_provenance=Provenance.REPO_DETECTOR_TABLE,
        max_rate_per_s=3.0, aliases=("SNR", "SNR_DB", "LINK_SNR"),
    ),
)


CHANNELS: dict[str, ChannelDefinition] = {d.channel_id: d for d in _DEFINITIONS}

if len(CHANNELS) != len(_DEFINITIONS):  # pragma: no cover — guards a bad edit
    raise RuntimeError("Duplicate channel_id in the channel dictionary")


def _build_alias_index() -> dict[str, str]:
    """Alias (lower-cased) -> canonical channel_id.

    A collision raises at import: two channels claiming the same alias would make
    resolution order-dependent, and an alias that silently resolves to the wrong
    channel is worse than no alias.
    """
    index: dict[str, str] = {}
    for definition in _DEFINITIONS:
        for name in (definition.channel_id, *definition.aliases):
            key = name.strip().lower()
            existing = index.get(key)
            if existing is not None and existing != definition.channel_id:
                raise RuntimeError(
                    f"alias {name!r} maps to both {existing!r} and "
                    f"{definition.channel_id!r}"
                )
            index[key] = definition.channel_id
    return index


_ALIAS_INDEX: dict[str, str] = _build_alias_index()


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — LOOKUP
# ═══════════════════════════════════════════════════════════════════════════

def get_channel(name: object) -> Optional[ChannelDefinition]:
    """Return a channel definition by id or alias, or None if unrecognised.

    Returns None rather than a placeholder so a caller has to decide what to do
    about an unknown channel. ``resolve_channel()`` is the variant that always
    returns something.
    """
    if name is None:
        return None
    key = str(name).strip().lower()
    channel_id = _ALIAS_INDEX.get(key)
    return CHANNELS.get(channel_id) if channel_id else None


def is_known_channel(name: object) -> bool:
    return get_channel(name) is not None


def resolve_channel(
    name: object,
    nominal_min: float | None = None,
    nominal_max: float | None = None,
) -> ChannelDefinition:
    """Return a definition for any channel, known or not.

    For an unrecognised channel the result is explicitly marked unknown:

        subsystem            Subsystem.UNKNOWN
        provenance           Provenance.UNKNOWN
        criticality          Criticality.MEDIUM, and nothing is claimed by it
        value_class          CONTINUOUS, the only class that assumes nothing
        hard_limits          whatever bounds the reading carried, or (None, None)
        expected_states      empty — no healthy value is guessed
        max_rate_per_s       None — no rate ceiling is invented

    No subsystem is ever inferred from a channel name. ESA-ADB ships anonymized
    ``channel_41`` identifiers; pattern-matching a name to a subsystem would
    attach a confident label to a channel nobody has identified, and that label
    would then propagate into diagnoses and audit records.
    """
    known = get_channel(name)
    if known is not None:
        return known

    channel_id = "" if name is None else str(name).strip()
    return ChannelDefinition(
        channel_id=channel_id or "UNNAMED_CHANNEL",
        display_name=channel_id or "Unnamed channel",
        subsystem=Subsystem.UNKNOWN,
        unit=None,
        datatype=DataType.FLOAT,
        value_class=ValueClass.CONTINUOUS,
        nominal_range=(None, None),
        hard_limits=(nominal_min, nominal_max),
        criticality=Criticality.MEDIUM,
        sampling_rate=SamplingRate.UNKNOWN,
        description=(
            "Channel not present in the SENTINEL channel dictionary. No "
            "subsystem, unit or expected state is claimed for it."
        ),
        physical_meaning=(
            "Unknown. Any bound applied to this channel came from the reading "
            "itself, not from an engineering definition, so an exceedance is "
            "weaker evidence than one on a declared channel."
        ),
        provenance=Provenance.UNKNOWN,
        limits_provenance=Provenance.UNKNOWN,
        expected_states=(),
        monotonic_non_decreasing=False,
        max_rate_per_s=None,
        notes="Synthesized on demand; not part of the dictionary.",
    )


def subsystem_of(name: object) -> Subsystem:
    """Subsystem for a channel. UNKNOWN when the channel is unrecognised."""
    known = get_channel(name)
    return known.subsystem if known else Subsystem.UNKNOWN


def hard_limits(name: object) -> tuple[Optional[float], Optional[float]]:
    """Hard limits for a channel, or (None, None) when unknown."""
    known = get_channel(name)
    return known.hard_limits if known else (None, None)


def safety_floor(name: object) -> Optional[float]:
    """Validator-enforced floor for a channel, or None if it has none.

    Raises if the channel is unknown, rather than returning None. A caller asking
    for a safety threshold is about to gate a command on it, and silently
    answering "no floor" for a misspelled channel would turn a typo into a
    permitted command.
    """
    known = get_channel(name)
    if known is None:
        raise KeyError(
            f"no channel {name!r} in the dictionary; refusing to report a "
            f"safety threshold for an unknown channel"
        )
    return known.safety_limits[0]


def safety_ceiling(name: object) -> Optional[float]:
    """Validator-enforced ceiling for a channel, or None if it has none.

    Raises on an unknown channel, for the same reason as ``safety_floor()``.
    """
    known = get_channel(name)
    if known is None:
        raise KeyError(
            f"no channel {name!r} in the dictionary; refusing to report a "
            f"safety threshold for an unknown channel"
        )
    return known.safety_limits[1]


def nominal_range(name: object) -> tuple[Optional[float], Optional[float]]:
    """Nominal operating band for a channel, or (None, None) when unknown."""
    known = get_channel(name)
    return known.nominal_range if known else (None, None)


def channel_ids() -> tuple[str, ...]:
    return tuple(sorted(CHANNELS))


def all_channels() -> tuple[ChannelDefinition, ...]:
    return tuple(CHANNELS[cid] for cid in channel_ids())


def channels_for_subsystem(subsystem: object) -> tuple[ChannelDefinition, ...]:
    target = resolve_subsystem(subsystem)
    return tuple(c for c in all_channels() if c.subsystem is target)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — SELF-VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

#: Channels whose nominal band falls outside their own hard limits. Listed
#: explicitly so the checker can separate a KNOWN, documented contradiction from
#: a newly introduced one. Shrinking this list is the goal; a new entry appearing
#: in it without being added here is an error.
KNOWN_NOMINAL_LIMIT_CONFLICTS: frozenset[str] = frozenset({
    "Attitude_error_deg",
    "Component_temp_C",
    "Gyro_rate_degs",
    "SEU_counter",
    "V_bus",
})


def validate_dictionary() -> dict[str, list[str]]:
    """Check the dictionary for internal contradictions.

    Returns ``{"errors": [...], "known_conflicts": [...], "warnings": [...]}``.

    The distinction matters: ``known_conflicts`` are the five pre-existing
    nominal-versus-limits contradictions Phase 5 inherited and documented, so CI
    can require ``errors`` to be empty without either pretending the conflicts
    are fine or blocking every build until spacecraft physics is settled. A NEW
    conflict lands in ``errors``.
    """
    errors: list[str] = []
    known: list[str] = []
    warnings: list[str] = []

    for definition in all_channels():
        cid = definition.channel_id

        hlo, hhi = definition.hard_limits
        if hlo is not None and hhi is not None and hlo > hhi:
            errors.append(f"{cid}: hard_limits inverted ({hlo} > {hhi})")

        nlo, nhi = definition.nominal_range
        if nlo is not None and nhi is not None and nlo > nhi:
            errors.append(f"{cid}: nominal_range inverted ({nlo} > {nhi})")

        contained = definition.nominal_within_hard_limits
        if contained is False:
            message = (
                f"{cid}: nominal_range {definition.nominal_range} is not "
                f"inside hard_limits {definition.hard_limits}"
            )
            if cid in KNOWN_NOMINAL_LIMIT_CONFLICTS:
                known.append(message)
            else:
                errors.append(
                    message + " — new conflict; add a resolution to "
                    "docs/phase5_channel_conflicts.md or correct the numbers"
                )

        if definition.subsystem is Subsystem.UNKNOWN:
            errors.append(f"{cid}: declared channels must have a subsystem")

        if definition.value_class.is_discrete and not definition.expected_states:
            warnings.append(
                f"{cid}: discrete channel with no expected_states, so no "
                f"state check will run on it"
            )

        if (definition.value_class is ValueClass.CONTINUOUS
                and definition.max_rate_per_s is None):
            warnings.append(
                f"{cid}: continuous channel with no max_rate_per_s, so the "
                f"rate-of-change detector will skip it"
            )

        if not definition.description.strip():
            errors.append(f"{cid}: empty description")
        if not definition.physical_meaning.strip():
            errors.append(f"{cid}: empty physical_meaning")

    stale = sorted(
        cid for cid in KNOWN_NOMINAL_LIMIT_CONFLICTS
        if cid not in CHANNELS
        or CHANNELS[cid].nominal_within_hard_limits is not False
    )
    for cid in stale:
        warnings.append(
            f"{cid}: listed in KNOWN_NOMINAL_LIMIT_CONFLICTS but no longer "
            f"conflicts — remove it from the list"
        )

    return {"errors": errors, "known_conflicts": known, "warnings": warnings}


def dictionary_status() -> dict:
    """Summary for the API, tests and status output."""
    by_subsystem: dict[str, list[str]] = {}
    by_class: dict[str, list[str]] = {}
    for definition in all_channels():
        by_subsystem.setdefault(definition.subsystem.value, []).append(
            definition.channel_id)
        by_class.setdefault(definition.value_class.value, []).append(
            definition.channel_id)

    findings = validate_dictionary()
    return {
        "channel_dict_version": CHANNEL_DICT_VERSION,
        "total_channels": len(CHANNELS),
        "channels_per_subsystem": {
            k: sorted(v) for k, v in sorted(by_subsystem.items())
        },
        "channels_per_value_class": {
            k: sorted(v) for k, v in sorted(by_class.items())
        },
        "degenerate_hard_limits": sorted(
            c.channel_id for c in all_channels() if c.degenerate_hard_limits
        ),
        "statistical_detection_disabled_for": sorted(
            c.channel_id for c in all_channels()
            if not c.statistical_detection_meaningful
        ),
        "rate_limit_declared_for": sorted(
            c.channel_id for c in all_channels() if c.max_rate_per_s is not None
        ),
        "criticality_counts": {
            level.value: sum(
                1 for c in all_channels() if c.criticality is level
            )
            for level in Criticality
        },
        "alias_count": len(_ALIAS_INDEX) - len(CHANNELS),
        "validation": {
            "errors": findings["errors"],
            "known_conflict_count": len(findings["known_conflicts"]),
            "known_conflicts": findings["known_conflicts"],
            "warning_count": len(findings["warnings"]),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6 — CLI
# ═══════════════════════════════════════════════════════════════════════════

def _main() -> int:
    """``python3 -m app.ingest.channel_dict`` — print and validate."""
    import json

    status = dictionary_status()
    print(json.dumps(status, indent=2))

    findings = validate_dictionary()
    print()
    print(f"errors          : {len(findings['errors'])}")
    for message in findings["errors"]:
        print(f"  ERROR {message}")
    print(f"known conflicts : {len(findings['known_conflicts'])}")
    for message in findings["known_conflicts"]:
        print(f"  KNOWN {message}")
    print(f"warnings        : {len(findings['warnings'])}")
    for message in findings["warnings"]:
        print(f"  WARN  {message}")

    return 1 if findings["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(_main())
