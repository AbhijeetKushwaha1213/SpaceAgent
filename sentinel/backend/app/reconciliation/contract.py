"""
SENTINEL — Reconciliation Contract (app/reconciliation/contract.py)

Phase 24 Step 1.  The separation layer's LANGUAGE, not its behavior.

This module defines the immutable data contracts the reconciliation engine
speaks. It contains NO decision logic:

    - it does not compare observations
    - it does not decide identity
    - it does not merge cases
    - it does not call any model, retriever, or provider
    - it cannot authorize commands, override physics, or override safety

Authority boundaries encoded structurally (specification §6, §10, §18, §19)
--------------------------------------------------------------------------
* ``ReconciliationInput`` has NO field through which model output could enter.
  There is no ``raw_text``, no ``model_confidence``, no ``llm_case_id``, no
  ``reasoning``. The engine therefore cannot consult them even by accident —
  the same technique ``BranchResult`` uses to make human-review monotonicity
  unexpressible rather than merely discouraged.

* ``ObservationEvent`` carries channel NAMES, detector NAMES, a severity, a
  direction CLASSIFICATION and timestamps. It carries no telemetry values. A
  pair of events is therefore comparable — and describable to an operator —
  without transporting raw readings anywhere.

* Physics appears only as ``physics_support``: a tuple of read-only reference
  strings of the form ``"<fault_id>:<PhysicsStatus>"``. There is no field a
  verdict could be written back through.

* ``RelationshipType.merge_permitted`` is a property, not stored state. Only
  DUPLICATE and SAME_CASE return True, so "keep separate under uncertainty"
  (§13) is a property of the type system rather than of callers behaving well.

Identifiers follow the repository-wide convention — content-derived, truncated
hash, zero randomness — established by ``Anomaly.make_id`` (``AN-…``),
``candidates._evidence_id`` (``EVID-…``) and ``HYP-…``:

    EVT-<sha256[:12]>      one observation event
    CASE-<sha256[:12]>     one case (identity derived from its member events)
    REL-<sha256[:12]>      one relationship record
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════
# ID CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════

_ID_DIGEST_LEN = 12


def _digest(material: str) -> str:
    """Truncated SHA-256 of the identity material.

    A content hash rather than a counter or a UUID: reconciling the same
    observations twice must produce byte-identical ids, which is what makes
    replay, diffing and idempotency testable.
    """
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:_ID_DIGEST_LEN]


def make_event_id(
    channel: str,
    anomaly_ids: tuple[str, ...] | list[str],
    scenario_id: str = "",
) -> str:
    """Stable id for one observation event.

    Anomaly ids are sorted before hashing so detector execution order cannot
    change the id.
    """
    material = "|".join([
        "EVT",
        str(channel),
        ",".join(sorted(str(a) for a in anomaly_ids)),
        str(scenario_id or ""),
    ])
    return f"EVT-{_digest(material)}"


def make_case_id(
    event_ids: tuple[str, ...] | list[str],
    scenario_id: str = "",
) -> str:
    """Stable id for a case, derived from its member events.

    Deriving identity from MEMBERSHIP rather than from a counter has a
    consequence worth stating plainly: merging two cases produces a third id
    rather than absorbing one into the other. That is intentional. A case id
    names a specific set of observations, so a case whose membership changed is
    a different case, and the audit trail shows the old ids in
    ``Case.merged_from`` instead of silently redefining what an id meant.
    """
    material = "|".join([
        "CASE",
        ",".join(sorted(str(e) for e in event_ids)),
        str(scenario_id or ""),
    ])
    return f"CASE-{_digest(material)}"


def make_relationship_id(
    source_case_id: str,
    target_case_id: str,
    relationship_type: "RelationshipType | str",
) -> str:
    """Stable id for a relationship record.

    The case-id pair is sorted, so ``(A, B)`` and ``(B, A)`` produce the same
    id. This is what makes duplicate and circular records structurally
    impossible rather than something the caller must remember to avoid (§12).
    """
    kind = (
        relationship_type.value
        if isinstance(relationship_type, RelationshipType)
        else str(relationship_type)
    )
    low, high = sorted([str(source_case_id), str(target_case_id)])
    return f"REL-{_digest('|'.join(['REL', low, high, kind]))}"


# ═══════════════════════════════════════════════════════════════════════════
# RELATIONSHIP TAXONOMY (§2)
# ═══════════════════════════════════════════════════════════════════════════

class RelationshipType(str, Enum):
    """How two observations (or two cases) relate to one another.

    The six classes are exhaustive and mutually exclusive: the engine returns
    exactly one per pair.

    DUPLICATE   The same observation recorded twice. Identical deterministic
                signature — same channels, same detectors, same timestamps,
                same states. Merging loses nothing.
    SAME_CASE   Different observations of ONE underlying fault. Requires
                corroboration across independent signal families.
    RELATED     Genuinely different faults with a physical relationship — one
                plausibly propagated into the other. They stay SEPARATE cases
                linked by a relationship record. Correlation is not identity.
    SEPARATE    No relationship the deterministic signals can find.
    CONFLICT    The observations contradict one another (opposing directions on
                a shared channel, or physics validating one while invalidating
                the other). BOTH sources are preserved; neither is discarded.
    UNCERTAIN   The signals do not resolve the question. This is the default,
                and it keeps the cases separate.
    """

    DUPLICATE = "DUPLICATE"
    SAME_CASE = "SAME_CASE"
    RELATED = "RELATED"
    SEPARATE = "SEPARATE"
    CONFLICT = "CONFLICT"
    UNCERTAIN = "UNCERTAIN"

    @property
    def merge_permitted(self) -> bool:
        """Whether this classification permits collapsing two cases into one.

        Only DUPLICATE and SAME_CASE. RELATED, SEPARATE, CONFLICT and UNCERTAIN
        all keep the cases apart, so §13's "default to keep separate" is
        enforced by the type rather than by every call site remembering it.
        """
        return self in (RelationshipType.DUPLICATE, RelationshipType.SAME_CASE)

    @property
    def is_unresolved(self) -> bool:
        """True when the pair was not resolved and an operator should look."""
        return self in (RelationshipType.UNCERTAIN, RelationshipType.CONFLICT)


# ═══════════════════════════════════════════════════════════════════════════
# SIGNALS (§8)
# ═══════════════════════════════════════════════════════════════════════════

class ReconciliationSignal(str, Enum):
    """The independent signals evaluated for every pair.

    "Independent" means each reads a different property of the observations, so
    agreement between them is corroboration rather than the same fact counted
    twice. None of them reads model output.

    Nine members covering the eight required families: TELEMETRY_SIMILARITY is
    split into CHANNEL_RELATIONSHIP (which channels) and
    SIGNAL_PATTERN_SIMILARITY (how they behaved), because a pair can share every
    channel while behaving oppositely, and collapsing the two would hide that.
    """

    TEMPORAL_PROXIMITY = "temporal_proximity"
    """Onset separation in seconds, from the canonical window's parsed
    ``relative_time_s``."""

    SUBSYSTEM_RELATIONSHIP = "subsystem_relationship"
    """Same subsystem / propagation-adjacent / unrelated, via
    ``channel_dict.resolve_subsystem``."""

    CHANNEL_RELATIONSHIP = "channel_relationship"
    """Set algebra over channel names: shared count and Jaccard index."""

    SIGNAL_PATTERN_SIMILARITY = "signal_pattern_similarity"
    """Exact comparison of the (detector set, severity rank, direction)
    pattern. Deterministic set/tuple equality — never a vector distance."""

    PHYSICAL_RELATIONSHIP = "physical_relationship"
    """Whether the declared propagation graph explains one observation from the
    other, and whether the observed onset gap is compatible with the declared
    delay class. This consults an AUTHORITY, not a similarity metric."""

    HYPOTHESIS_COMPATIBILITY = "hypothesis_compatibility"
    """Overlap and mutual exclusion between the two observations' deterministic
    candidate fault ids."""

    DUPLICATE_SIGNATURE = "duplicate_signature"
    """Exact equality of the full deterministic signature."""

    CONTRADICTION_INDICATOR = "contradiction_indicator"
    """Opposing directions on a shared channel, or a physics verdict that
    validates one observation's fault while invalidating the other's."""

    DATA_QUALITY = "data_quality"
    """Whether the pair was evaluable at all: defects recorded during
    projection, absent timing, unrecognised channels.

    Reported as its own signal rather than folded into the others because
    "three signals supported identity out of eight evaluated" and "three
    supported it out of four we could even look at" are different claims, and
    an operator is entitled to see which one they are being shown.

    This signal never supports identity. On degraded input it returns
    NOT_EVALUABLE, never OPPOSES: missing data is not evidence that two
    observations are separate, and the engine must reach UNCERTAIN (which keeps
    the cases apart without asserting anything) rather than SEPARATE (which
    asserts they are unrelated)."""


class SignalVerdict(str, Enum):
    """What one signal concluded.

    SUPPORTS_IDENTITY  counts toward SAME_CASE / DUPLICATE
    SUPPORTS_RELATION  counts toward RELATED but NOT toward identity
    NEUTRAL            evaluated, and says nothing either way
    OPPOSES            evaluated, and argues against identity
    CONTRADICTS        the two observations are mutually inconsistent
    NOT_EVALUABLE      required input was missing or malformed; deliberately
                       distinct from NEUTRAL, because "we could not tell" and
                       "we looked and it does not matter" are different facts
                       and collapsing them would overstate what was checked
    """

    SUPPORTS_IDENTITY = "SUPPORTS_IDENTITY"
    SUPPORTS_RELATION = "SUPPORTS_RELATION"
    NEUTRAL = "NEUTRAL"
    OPPOSES = "OPPOSES"
    CONTRADICTS = "CONTRADICTS"
    NOT_EVALUABLE = "NOT_EVALUABLE"


@dataclass(frozen=True)
class SignalOutcome:
    """One signal's evaluation of one pair.

    ``explanation`` is written for an operator, not for a log grep: it states
    the observed value and the threshold it was compared against, so the
    decision can be disagreed with.
    """

    signal: ReconciliationSignal
    verdict: SignalVerdict
    value: Optional[float] = None
    threshold_name: str = ""
    threshold_used: Optional[float] = None
    explanation: str = ""

    @property
    def supports_identity(self) -> bool:
        return self.verdict is SignalVerdict.SUPPORTS_IDENTITY

    @property
    def supports_relation(self) -> bool:
        return self.verdict in (
            SignalVerdict.SUPPORTS_IDENTITY,
            SignalVerdict.SUPPORTS_RELATION,
        )

    @property
    def opposes(self) -> bool:
        return self.verdict is SignalVerdict.OPPOSES

    @property
    def contradicts(self) -> bool:
        return self.verdict is SignalVerdict.CONTRADICTS

    def as_dict(self) -> dict[str, object]:
        """Audit/API projection. No raw telemetry, by construction."""
        return {
            "signal": self.signal.value,
            "verdict": self.verdict.value,
            "value": self.value,
            "threshold_name": self.threshold_name,
            "threshold_used": self.threshold_used,
            "explanation": self.explanation,
        }


# ═══════════════════════════════════════════════════════════════════════════
# OBSERVATION EVENT (§5) — the minimal comparable unit
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ObservationEvent:
    """One observed anomaly cluster, projected BY REFERENCE from detection.

    What this deliberately does NOT carry
    -------------------------------------
    No telemetry values. No ``Anomaly.evidence`` dict (which holds observed
    readings such as ``{'value': 1002.0, 'limit_max': 1000.0}``). No baseline
    statistics. Two events are compared on structure — which channels, which
    detectors, which severity, which direction, when — so the comparison never
    requires transporting readings, and an event is safe to summarise into a
    prompt or an API response without a redaction pass.

    ``directions`` holds the detector's own direction CLASSIFICATION
    (``HIGH``/``LOW``/``INVALID``/``STATE_CHANGE``/…), read from
    ``Anomaly.evidence["direction"]``. That is a detector output, not a
    reading: it says a channel went high, not how high.

    Provenance (§4): ``anomaly_ids`` references the detection findings,
    ``channel`` names the source telemetry channel, ``timestamps`` and
    ``first_seen_s``/``last_seen_s`` give the window, ``subsystem`` gives the
    scope, and ``scenario_id`` ties back to the run. Nothing is copied that
    could be referenced.
    """

    event_id: str
    channel: str
    subsystem: str
    severity: str
    severity_rank: int = 0
    detectors: tuple[str, ...] = ()
    anomaly_ids: tuple[str, ...] = ()
    timestamps: tuple[str, ...] = ()
    directions: tuple[str, ...] = ()
    first_seen_s: Optional[float] = None
    last_seen_s: Optional[float] = None
    candidate_fault_ids: tuple[str, ...] = ()
    corroborated: bool = False
    scenario_id: str = ""
    #: Free-text provenance note, e.g. which report the event was projected
    #: from. Never model output.
    source_ref: str = ""
    #: Set when the projection could not establish a required property, e.g. a
    #: malformed or absent timestamp. Signals read this and return
    #: NOT_EVALUABLE rather than guessing.
    defects: tuple[str, ...] = ()

    @property
    def has_timing(self) -> bool:
        return self.first_seen_s is not None

    @property
    def has_known_subsystem(self) -> bool:
        return bool(self.subsystem) and self.subsystem != "UNKNOWN"

    def signature(self) -> tuple:
        """The deterministic signature used for DUPLICATE detection.

        Exact-equality material only. Sorted so detector or sample ordering
        cannot make two identical observations look different.
        """
        return (
            self.channel,
            tuple(sorted(self.detectors)),
            tuple(sorted(self.timestamps)),
            tuple(sorted(self.directions)),
            self.severity,
        )

    def as_dict(self) -> dict[str, object]:
        """Audit/API projection."""
        return {
            "event_id": self.event_id,
            "channel": self.channel,
            "subsystem": self.subsystem,
            "severity": self.severity,
            "detectors": list(self.detectors),
            "anomaly_ids": list(self.anomaly_ids),
            "timestamps": list(self.timestamps),
            "directions": list(self.directions),
            "first_seen_s": self.first_seen_s,
            "last_seen_s": self.last_seen_s,
            "candidate_fault_ids": list(self.candidate_fault_ids),
            "corroborated": self.corroborated,
            "scenario_id": self.scenario_id,
            "source_ref": self.source_ref,
            "defects": list(self.defects),
        }


# ═══════════════════════════════════════════════════════════════════════════
# CASE (§4)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Case:
    """A set of observations attributed to one underlying fault scenario.

    A case is the scope for everything downstream: its evidence, its
    hypotheses, its physics verdicts, its retrieved procedures and its recovery
    plan. Nothing from another case may enter it without an explicit
    relationship reference (§11).
    """

    case_id: str
    event_ids: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    subsystems: tuple[str, ...] = ()
    window_start_s: Optional[float] = None
    window_end_s: Optional[float] = None
    scenario_id: str = ""
    config_version: str = ""
    engine_version: str = ""
    #: Deterministic reasons this case exists with this membership.
    reasons: tuple[str, ...] = ()
    #: Case ids this case replaced, when a justified merge occurred. Empty for
    #: a case that was never merged.
    merged_from: tuple[str, ...] = ()
    #: Set when any member event carried a defect, so an operator can see the
    #: case rests on partly malformed input.
    defects: tuple[str, ...] = ()

    @property
    def primary_subsystem(self) -> str:
        """The single subsystem, or ``MULTI`` when the case spans several.

        Reuses ``propagation.MULTI`` rather than inventing a second sentinel.
        """
        from app.diagnosis.propagation import MULTI

        known = tuple(s for s in self.subsystems if s and s != "UNKNOWN")
        if len(set(known)) == 1:
            return known[0]
        if not known:
            return "UNKNOWN"
        return MULTI

    @property
    def event_count(self) -> int:
        return len(self.event_ids)

    def as_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "event_ids": list(self.event_ids),
            "channels": list(self.channels),
            "subsystems": list(self.subsystems),
            "primary_subsystem": self.primary_subsystem,
            "window_start_s": self.window_start_s,
            "window_end_s": self.window_end_s,
            "scenario_id": self.scenario_id,
            "config_version": self.config_version,
            "engine_version": self.engine_version,
            "reasons": list(self.reasons),
            "merged_from": list(self.merged_from),
            "defects": list(self.defects),
        }


# ═══════════════════════════════════════════════════════════════════════════
# CASE RELATIONSHIP (§12)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CaseRelationship:
    """One deterministic relationship between two cases.

    Normalized representation (§12): ``source_case_id`` is always the
    lexicographically smaller of the pair, so ``(A, B)`` and ``(B, A)`` produce
    one record with one id. Causal DIRECTION, where the propagation graph
    establishes it, is carried separately in ``propagation_source_case_id`` —
    normalizing the pair must not silently discard which one came first.

    ``confidence`` is present only because §12 permits it *if deterministic*.
    It is the fraction of evaluable signals that supported the decision. It is
    NOT a probability, NOT calibrated, and NOT a model estimate — the same
    caveat the hypothesis scorer carries.
    """

    relationship_id: str
    source_case_id: str
    target_case_id: str
    relationship_type: RelationshipType
    deterministic_reasons: tuple[str, ...] = ()
    signals: tuple[SignalOutcome, ...] = ()
    config_version: str = ""
    engine_version: str = ""
    #: Observation-time window the decision was made over, in seconds relative
    #: to the fault. Not a wall-clock timestamp: the pipeline reasons in
    #: relative offsets and inventing wall-clock time here would be fabrication.
    event_window_s: tuple[Optional[float], Optional[float]] = (None, None)
    #: Read-only physics references, ``"<fault_id>:<PhysicsStatus>"``. No
    #: verdict can be written back through this field.
    physics_support: tuple[str, ...] = ()
    #: Evidence ids the relationship was justified against, by reference.
    evidence_references: tuple[str, ...] = ()
    #: Source event ids on each side, for provenance.
    source_event_ids: tuple[str, ...] = ()
    target_event_ids: tuple[str, ...] = ()
    #: Which case the propagation graph places upstream, when it establishes a
    #: direction. None when the relationship is symmetric or undirected.
    propagation_source_case_id: Optional[str] = None
    confidence: Optional[float] = None

    @property
    def merge_permitted(self) -> bool:
        """Delegates to the type. There is no per-record override."""
        return self.relationship_type.merge_permitted

    @property
    def case_pair(self) -> tuple[str, str]:
        return (self.source_case_id, self.target_case_id)

    def references_case(self, case_id: str) -> bool:
        return case_id in (self.source_case_id, self.target_case_id)

    def other_case(self, case_id: str) -> Optional[str]:
        """The far side of the relationship, or None if ``case_id`` is absent."""
        if case_id == self.source_case_id:
            return self.target_case_id
        if case_id == self.target_case_id:
            return self.source_case_id
        return None

    def as_dict(self) -> dict[str, object]:
        return {
            "relationship_id": self.relationship_id,
            "source_case_id": self.source_case_id,
            "target_case_id": self.target_case_id,
            "relationship_type": self.relationship_type.value,
            "merge_permitted": self.merge_permitted,
            "deterministic_reasons": list(self.deterministic_reasons),
            "signals": [s.as_dict() for s in self.signals],
            "config_version": self.config_version,
            "engine_version": self.engine_version,
            "event_window_s": list(self.event_window_s),
            "physics_support": list(self.physics_support),
            "evidence_references": list(self.evidence_references),
            "source_event_ids": list(self.source_event_ids),
            "target_event_ids": list(self.target_event_ids),
            "propagation_source_case_id": self.propagation_source_case_id,
            "confidence": self.confidence,
            "confidence_caveat": (
                "Fraction of evaluable deterministic signals that supported the "
                "decision. Not a probability, not calibrated, not a model "
                "estimate."
            ),
        }


# ═══════════════════════════════════════════════════════════════════════════
# ENGINE INPUT (§6) — note what CANNOT be expressed here
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ReconciliationInput:
    """Everything the engine is allowed to look at.

    There is deliberately no field for raw model text, model confidence, model
    reasoning, or a model-supplied case id. The engine cannot consult them
    because the contract gives it nowhere to receive them from (§6).

    ``physics_statuses`` is a tuple of ``(fault_id, PhysicsStatus_value)`` pairs
    — a tuple rather than a dict so the input stays hashable and the audit
    payload stays order-stable.
    """

    events: tuple[ObservationEvent, ...] = ()
    scenario_id: str = ""
    physics_statuses: tuple[tuple[str, str], ...] = ()
    #: ``(fault_id_a, fault_id_b)`` pairs the fault model declares mutually
    #: exclusive. Populated from the deterministic diagnosis layer.
    mutually_exclusive_faults: tuple[tuple[str, str], ...] = ()
    #: Evidence ids by event, ``(event_id, evidence_id)`` pairs. References
    #: only — the evidence items themselves stay owned by the diagnosis layer.
    evidence_refs: tuple[tuple[str, str], ...] = ()

    def physics_status_for(self, fault_id: str) -> Optional[str]:
        for fid, status in self.physics_statuses:
            if fid == fault_id:
                return status
        return None

    def evidence_ids_for(self, event_id: str) -> tuple[str, ...]:
        return tuple(e for ev, e in self.evidence_refs if ev == event_id)


# ═══════════════════════════════════════════════════════════════════════════
# ENGINE OUTPUT
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ReconciliationResult:
    """The separation layer's complete, explainable output.

    ``human_review_required`` is raised — never cleared — when the layer could
    not resolve something an operator should see (CONFLICT, UNCERTAIN between
    events that share a channel, malformed input). It is combined into the
    pipeline's flag through ``combine_human_review()`` only, so monotonicity is
    preserved (§19).
    """

    cases: tuple[Case, ...] = ()
    relationships: tuple[CaseRelationship, ...] = ()
    #: ``(event_id, case_id)`` assignment pairs, sorted by event_id.
    event_assignments: tuple[tuple[str, str], ...] = ()
    config_version: str = ""
    engine_version: str = ""
    human_review_required: bool = False
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    merges_performed: tuple[tuple[str, str], ...] = field(default=())

    def case_for_event(self, event_id: str) -> Optional[str]:
        for ev, case_id in self.event_assignments:
            if ev == event_id:
                return case_id
        return None

    def case(self, case_id: str) -> Optional[Case]:
        for c in self.cases:
            if c.case_id == case_id:
                return c
        return None

    def relationships_for(self, case_id: str) -> tuple[CaseRelationship, ...]:
        return tuple(r for r in self.relationships if r.references_case(case_id))

    def related_case_ids(self, case_id: str) -> tuple[str, ...]:
        """Cases explicitly linked to ``case_id`` by a relationship record.

        This is the ONLY sanctioned basis for letting one case's evidence be
        referenced while reasoning about another (§11). SEPARATE relationships
        are excluded: recording that two cases are unrelated must not become a
        channel for sharing their evidence.
        """
        out: list[str] = []
        for r in self.relationships:
            if r.relationship_type is RelationshipType.SEPARATE:
                continue
            other = r.other_case(case_id)
            if other is not None and other not in out:
                out.append(other)
        return tuple(out)

    @property
    def case_count(self) -> int:
        return len(self.cases)

    def as_dict(self) -> dict[str, object]:
        return {
            "cases": [c.as_dict() for c in self.cases],
            "relationships": [r.as_dict() for r in self.relationships],
            "event_assignments": [list(p) for p in self.event_assignments],
            "config_version": self.config_version,
            "engine_version": self.engine_version,
            "human_review_required": self.human_review_required,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "merges_performed": [list(p) for p in self.merges_performed],
            "case_count": self.case_count,
        }
