"""
SENTINEL — Subsystem Propagation (app/diagnosis/propagation.py)

Phase 6. Declares which subsystem faults can cause which others, so a downstream
symptom is not mistaken for a root cause.

The problem this solves
-----------------------
Detector evidence is unordered by causation. A dump showing an attitude anomaly
AND a power anomaly is equally consistent with:

    an attitude fault that off-pointed the arrays   (AOCS is the root cause)
    a power fault that browned out the ADCS         (EPS is the root cause)
    two unrelated faults                            (no propagation)

Nothing in an ``AnomalyReport`` distinguishes these. The prose in prompts.py told
the model to "identify the INITIATING fault, not just the most recent symptom",
which is correct advice and completely unenforceable — there was no structure that
could say which faults could initiate which.

What this module does and does not claim
---------------------------------------
It provides a directed graph of PLAUSIBLE causal directions between subsystems,
each edge carrying the physical mechanism. It answers "could an EPS fault produce
this TCS symptom" with a mechanism, and "which of these anomalies is upstream of
the others".

It does NOT prove causation. Two things prevent that honestly:

  * Timing. Establishing that A preceded B needs anomaly onset times, which
    ``relative_time_s`` gives us, but a shared onset is equally consistent with a
    common cause. ``onset_ordering()`` reports what the timestamps show and
    labels a simultaneous onset as undetermined rather than picking a winner.
  * Physics. Confirming that an off-pointed array actually would produce the
    observed current drop needs a power and geometry model. There is none — see
    the NOT_IMPLEMENTED physics stage in the Phase 4 audit records.

So this is a plausibility filter, not an inference engine. It narrows and ranks;
it does not conclude.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

#: Subsystem used for a fault spanning several.
MULTI = "MULTI"


@dataclass(frozen=True)
class PropagationEdge:
    """A plausible causal direction from one subsystem to another."""

    source: str
    target: str
    mechanism: str
    """The physical route by which the source fault reaches the target. Required:
    an edge without a stated mechanism is an assertion the reader cannot check."""

    strength: float = 0.5
    """How readily the fault propagates, 0..1. Used to weight a root-cause
    preference, not as a probability — nothing here is calibrated, and it is not
    presented as such."""

    typical_delay: str = "seconds to minutes"
    """Rough time for the effect to appear, for reading alongside onset times."""


# ═══════════════════════════════════════════════════════════════════════════
# THE PROPAGATION GRAPH
# ═══════════════════════════════════════════════════════════════════════════
#
# Edges are the mechanisms already described in the repository's own causal
# chains: the MULTI_CASCADE prose ("gyro fault -> ADCS tumbles -> solar panels
# lose sun pointing -> I_sa drops -> battery drains -> EPS fault") plus the
# channel physical_meaning text from the Phase 5 channel dictionary.
#
# AOCS is the most connected source on purpose. Attitude determines array
# illumination, radiator view factors and antenna pointing, so an attitude fault
# has a physical route into EPS, TCS and COMMS. That asymmetry is what lets the
# engine prefer an attitude root cause over three coincident downstream faults.

PROPAGATION_EDGES: tuple[PropagationEdge, ...] = (
    # ── AOCS is upstream of almost everything ────────────────────────────
    PropagationEdge(
        source="AOCS", target="EPS", strength=0.8,
        mechanism=(
            "Loss of attitude control off-points the solar arrays, so array "
            "current falls and the battery discharges."
        ),
        typical_delay="minutes",
    ),
    PropagationEdge(
        source="AOCS", target="TCS", strength=0.6,
        mechanism=(
            "Loss of attitude control changes which faces see the sun and which "
            "see deep space, so radiator view factors and heat loads shift."
        ),
        typical_delay="tens of minutes",
    ),
    PropagationEdge(
        source="AOCS", target="COMMS", strength=0.7,
        mechanism=(
            "Loss of attitude control mispoints the antenna, so link margin "
            "falls and lock can be lost with the transponder healthy."
        ),
        typical_delay="minutes",
    ),

    # ── EPS is upstream of everything that needs power ───────────────────
    PropagationEdge(
        source="EPS", target="AOCS", strength=0.6,
        mechanism=(
            "Undervoltage browns out or sheds the ADCS, so actuators lose torque "
            "authority and sensors drop out."
        ),
        typical_delay="seconds",
    ),
    PropagationEdge(
        source="EPS", target="OBC", strength=0.6,
        mechanism=(
            "Bus undervoltage resets or brownouts the processor, which presents "
            "as a software fault."
        ),
        typical_delay="seconds",
    ),
    PropagationEdge(
        source="EPS", target="COMMS", strength=0.6,
        mechanism=(
            "Insufficient bus power reduces transmitter output or sheds the "
            "transponder entirely."
        ),
        typical_delay="seconds",
    ),
    PropagationEdge(
        source="EPS", target="TCS", strength=0.5,
        mechanism=(
            "Load shedding disables heaters, so temperatures fall out of range "
            "in the cold case."
        ),
        typical_delay="tens of minutes",
    ),

    # ── TCS reaches anything temperature-sensitive ───────────────────────
    PropagationEdge(
        source="TCS", target="EPS", strength=0.6,
        mechanism=(
            "Battery over- or under-temperature reduces available capacity, and "
            "panel over-temperature reduces array output."
        ),
        typical_delay="tens of minutes",
    ),
    PropagationEdge(
        source="TCS", target="OBC", strength=0.5,
        mechanism=(
            "Processor over-temperature causes computation errors before it "
            "causes damage, so a thermal fault can present as a software fault."
        ),
        typical_delay="minutes",
    ),
    PropagationEdge(
        source="TCS", target="COMMS", strength=0.5,
        mechanism=(
            "Transponder over-temperature degrades transmitter output, so a "
            "thermal fault presents as a link problem."
        ),
        typical_delay="minutes",
    ),
    PropagationEdge(
        source="TCS", target="AOCS", strength=0.4,
        mechanism=(
            "Sensor over-temperature drives gyro bias drift and can blind or "
            "disable a star tracker."
        ),
        typical_delay="tens of minutes",
    ),

    # ── OBC commands everything, so its faults reach everything ──────────
    PropagationEdge(
        source="OBC", target="AOCS", strength=0.7,
        mechanism=(
            "The control loop runs on the OBC. A saturated or reset processor "
            "stops closing it, so attitude drifts."
        ),
        typical_delay="seconds",
    ),
    PropagationEdge(
        source="OBC", target="TCS", strength=0.5,
        mechanism=(
            "Thermostatic control runs on the OBC, so a software fault can leave "
            "a heater commanded on or off."
        ),
        typical_delay="minutes",
    ),
    PropagationEdge(
        source="OBC", target="EPS", strength=0.4,
        mechanism=(
            "Power mode management runs on the OBC, so a software fault can "
            "leave loads enabled that should have been shed."
        ),
        typical_delay="minutes",
    ),
    PropagationEdge(
        source="OBC", target="COMMS", strength=0.5,
        mechanism=(
            "Command and telemetry handling runs on the OBC, so a software fault "
            "stops the downlink even with a healthy transponder."
        ),
        typical_delay="seconds",
    ),

    # ── COMMS is mostly a sink ───────────────────────────────────────────
    # A comms fault stops the GROUND from acting; it does not damage the
    # spacecraft. The one real onward effect is that autonomy takes over.
    PropagationEdge(
        source="COMMS", target="OBC", strength=0.3,
        mechanism=(
            "Prolonged loss of contact triggers onboard autonomy rules, which "
            "change OBC behaviour and can themselves command safe mode."
        ),
        typical_delay="hours",
    ),
)


_BY_SOURCE: dict[str, tuple[PropagationEdge, ...]] = {}
_BY_TARGET: dict[str, tuple[PropagationEdge, ...]] = {}
for _edge in PROPAGATION_EDGES:
    _BY_SOURCE[_edge.source] = _BY_SOURCE.get(_edge.source, ()) + (_edge,)
    _BY_TARGET[_edge.target] = _BY_TARGET.get(_edge.target, ()) + (_edge,)


# ═══════════════════════════════════════════════════════════════════════════
# QUERIES
# ═══════════════════════════════════════════════════════════════════════════

def _norm(subsystem: object) -> str:
    """Normalise a subsystem name through the channel dictionary.

    ADCS and AOCS name the same subsystem, and the repository uses both.
    """
    if subsystem is None:
        return "UNKNOWN"
    text = str(subsystem).strip().upper()
    if text == MULTI:
        return MULTI
    from app.ingest.channel_dict import resolve_subsystem

    return resolve_subsystem(text).value


def downstream_subsystems(subsystem: object) -> tuple[str, ...]:
    """Subsystems a fault here could plausibly affect. Direct edges only."""
    return tuple(sorted({e.target for e in _BY_SOURCE.get(_norm(subsystem), ())}))


def upstream_subsystems(subsystem: object) -> tuple[str, ...]:
    """Subsystems whose faults could plausibly have produced a symptom here."""
    return tuple(sorted({e.source for e in _BY_TARGET.get(_norm(subsystem), ())}))


def get_edge(source: object, target: object) -> Optional[PropagationEdge]:
    """The edge from source to target, or None if propagation is not declared."""
    s, t = _norm(source), _norm(target)
    for edge in _BY_SOURCE.get(s, ()):
        if edge.target == t:
            return edge
    return None


def is_plausible_propagation(source: object, target: object) -> bool:
    """True when a fault in source could plausibly produce a symptom in target.

    A subsystem always explains its own symptoms, so source == target is True.
    """
    if _norm(source) == _norm(target):
        return True
    return get_edge(source, target) is not None


def explain_path(source: object, target: object) -> Optional[str]:
    """The stated mechanism for an edge, for the operator-facing rationale."""
    if _norm(source) == _norm(target):
        return f"Symptom is within the {_norm(source)} subsystem itself."
    edge = get_edge(source, target)
    return edge.mechanism if edge else None


def explained_subsystems(
    source: object,
    observed: Iterable[object],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split observed subsystems into those a source fault explains and those not.

    Returns ``(explained, unexplained)``. A candidate leaving subsystems
    unexplained is a weaker root-cause hypothesis than one accounting for
    everything seen, and ``candidates.py`` uses exactly that.
    """
    explained: list[str] = []
    unexplained: list[str] = []
    for subsystem in {_norm(s) for s in observed}:
        if subsystem == "UNKNOWN":
            # An unattributed channel cannot count for or against a hypothesis.
            continue
        if is_plausible_propagation(source, subsystem):
            explained.append(subsystem)
        else:
            unexplained.append(subsystem)
    return tuple(sorted(explained)), tuple(sorted(unexplained))


def onset_ordering(
    onsets: dict[str, Optional[float]],
) -> dict[str, object]:
    """Report what anomaly onset times show about ordering.

    Args:
        onsets: subsystem -> earliest onset in seconds relative to the event
            (negative is earlier), or None when no time is available.

    Returns a dict with ``earliest``, ``ordered`` and ``determined``.

    ``determined`` is False when times are missing or tied. A tie is genuinely
    undetermined: simultaneous onset is as consistent with a common cause as with
    instant propagation, and reporting the first subsystem alphabetically as the
    root cause would be inventing an ordering the data does not contain.
    """
    timed = {k: v for k, v in onsets.items()
             if isinstance(v, (int, float)) and _norm(k) != "UNKNOWN"}
    if not timed:
        return {"earliest": None, "ordered": [], "determined": False,
                "reason": "no parseable onset times"}

    ordered = sorted(timed.items(), key=lambda kv: kv[1])
    earliest_time = ordered[0][1]
    tied = [k for k, v in ordered if v == earliest_time]

    if len(tied) > 1:
        return {
            "earliest": None,
            "ordered": [k for k, _ in ordered],
            "determined": False,
            "reason": (
                f"{len(tied)} subsystems share the earliest onset "
                f"({', '.join(sorted(tied))}); simultaneous onset does not "
                f"establish a direction"
            ),
        }

    if len(timed) == 1:
        return {
            "earliest": ordered[0][0],
            "ordered": [ordered[0][0]],
            "determined": False,
            "reason": "only one subsystem has an onset time, so there is "
                      "nothing to order it against",
        }

    return {
        "earliest": ordered[0][0],
        "ordered": [k for k, _ in ordered],
        "determined": True,
        "reason": f"{ordered[0][0]} anomaly onset precedes the others",
    }


def propagation_status() -> dict:
    """Summary for the API, tests and status output."""
    subsystems = sorted({e.source for e in PROPAGATION_EDGES}
                        | {e.target for e in PROPAGATION_EDGES})
    return {
        "edge_count": len(PROPAGATION_EDGES),
        "subsystems": subsystems,
        "downstream": {s: list(downstream_subsystems(s)) for s in subsystems},
        "upstream": {s: list(upstream_subsystems(s)) for s in subsystems},
        "edges": [
            {
                "source": e.source,
                "target": e.target,
                "strength": e.strength,
                "typical_delay": e.typical_delay,
                "mechanism": e.mechanism,
            }
            for e in PROPAGATION_EDGES
        ],
        "claim": (
            "Plausible causal directions with stated mechanisms. This narrows "
            "and ranks candidates; it does not prove causation. Confirming a "
            "propagation would need a physics model, which this build does not "
            "have."
        ),
    }


def validate_propagation() -> dict[str, list[str]]:
    """Check the graph for defects that would silently distort ranking."""
    errors: list[str] = []
    warnings: list[str] = []

    seen: set[tuple[str, str]] = set()
    for edge in PROPAGATION_EDGES:
        key = (edge.source, edge.target)
        if key in seen:
            errors.append(f"duplicate edge {edge.source} -> {edge.target}")
        seen.add(key)

        if edge.source == edge.target:
            errors.append(
                f"self-edge {edge.source} -> {edge.target}; a subsystem "
                f"explaining its own symptoms is handled without an edge"
            )
        if not 0.0 <= edge.strength <= 1.0:
            errors.append(
                f"{edge.source} -> {edge.target}: strength {edge.strength} "
                f"outside 0..1"
            )
        if len(edge.mechanism.strip()) < 25:
            errors.append(
                f"{edge.source} -> {edge.target}: mechanism too short to be "
                f"reviewable"
            )

    from app.ingest.channel_dict import resolve_subsystem

    for edge in PROPAGATION_EDGES:
        for role, name in (("source", edge.source), ("target", edge.target)):
            if not resolve_subsystem(name).is_known:
                errors.append(
                    f"{edge.source} -> {edge.target}: {role} {name!r} is not a "
                    f"known subsystem"
                )

    # Every subsystem that owns a fault should appear in the graph, otherwise its
    # candidates can never be compared for propagation.
    from app.diagnosis.fault_dictionary import all_faults

    fault_subsystems = {d.subsystem for d in all_faults()} - {MULTI}
    graph_subsystems = {e.source for e in PROPAGATION_EDGES} | {
        e.target for e in PROPAGATION_EDGES}
    for subsystem in sorted(fault_subsystems - graph_subsystems):
        warnings.append(
            f"{subsystem} has faults defined but no propagation edges, so its "
            f"candidates cannot be compared for propagation"
        )

    return {"errors": errors, "warnings": warnings}


def _main() -> int:
    """``python3 -m app.diagnosis.propagation`` — print and validate."""
    import json

    print(json.dumps(propagation_status(), indent=2))
    findings = validate_propagation()
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
