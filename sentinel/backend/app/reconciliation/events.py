"""
SENTINEL — Observation Event Projection (app/reconciliation/events.py)

Phase 24.  Turns the existing detection output into the minimal comparable unit
the separation logic reasons over.

This module creates NO new identity system and duplicates NO existing one. It
projects, by reference:

    AnomalyReport.channels[i]  (a ChannelFinding, already fused per channel)
        -> ObservationEvent

One event per anomalous channel. That granularity is the point of the
specification's motivating example: a reaction-wheel fault surfaces on
``RW1_speed``, ``RW1_torque`` AND ``attitude_error``, while a gyro fault
surfaces on ``gyro_rate`` AND ``attitude_error``. If the unit of comparison were
the whole report, the shared ``attitude_error`` channel would be invisible; if it
were the individual anomaly, the same channel flagged by three detectors would
look like three separate observations. The channel finding is the level at which
detection has already resolved that ambiguity.

What is NOT copied
------------------
``Anomaly.evidence`` holds observed readings — ``{'value': 1002.0,
'limit_max': 1000.0, 'exceeded_by': 2.0}``. None of it is copied into an event.
The only thing read out of it is ``evidence["direction"]``, which is the
detector's own classification (``HIGH``/``LOW``/``INVALID``/``STATE_CHANGE``),
already surfaced by ``AnomalyReport.legacy_dict()``. A direction says a channel
went high; it does not say how high. Everything else is referenced by id.

Timing
------
``relative_time_s`` is parsed by ``app.api.adapters`` — once, at
canonicalization, "rather than in each consumer", as that module's docstring
puts it. This module therefore builds its offset -> seconds map FROM the
canonical window when a crash dump is available, and only falls back to the
adapter's own parser for offsets the window does not contain. It does not
implement a third parser.

A malformed or absent offset is recorded as a DEFECT on the event, not silently
coerced to 0.0. Collapsing unparseable offsets to a single instant would make
two unrelated observations look simultaneous — which is precisely the error this
whole layer exists to prevent.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from app.reconciliation.contract import ObservationEvent, make_event_id

#: Direction classifications the detectors emit (app/detection/limits.py,
#: statistical.py, temporal.py). Listed so the contradiction signal knows which
#: pairs are genuinely opposed rather than merely different.
DIRECTION_HIGH = "HIGH"
DIRECTION_LOW = "LOW"
DIRECTION_INVALID = "INVALID"
DIRECTION_STATE_CHANGE = "STATE_CHANGE"
DIRECTION_UNKNOWN = "UNKNOWN"

#: Direction pairs that are mutually contradictory on the SAME channel. A
#: channel cannot be simultaneously above and below its bound.
OPPOSED_DIRECTIONS: frozenset[frozenset[str]] = frozenset({
    frozenset({DIRECTION_HIGH, DIRECTION_LOW}),
})


def _offset_seconds_map(crash_dump: dict[str, Any] | None) -> dict[str, float]:
    """Map each telemetry offset string to its parsed seconds.

    Built from the canonical window, which is where the repository parses
    offsets. Returns an empty map when no dump is supplied or canonicalization
    fails; callers then fall back to the adapter's parser per offset.
    """
    if not crash_dump:
        return {}
    try:
        from app.api.adapters import canonical_window

        out: dict[str, float] = {}
        for entry in canonical_window(crash_dump):
            if entry.relative_time_s is not None:
                out.setdefault(entry.timestamp, float(entry.relative_time_s))
        return out
    except Exception:  # pragma: no cover — adapter is in-tree
        return {}


def _parse_offset(offset: str, table: dict[str, float]) -> Optional[float]:
    """Seconds for one offset string, or None when it cannot be established.

    Prefers the canonical window's already-parsed value; falls back to the
    adapter's parser so there is exactly one parsing implementation in the
    repository. Never returns 0.0 as a stand-in for "unparseable".
    """
    if offset in table:
        return table[offset]
    try:
        from app.api.adapters import _parse_seconds

        return _parse_seconds(offset)
    except Exception:  # pragma: no cover — adapter is in-tree
        return None


def _resolve_subsystem(channel: str) -> str:
    """Subsystem name for a channel, or ``UNKNOWN``.

    Reuses ``app.ingest.channel_dict``, which already owns the alias table
    (ADCS -> AOCS, PAYLOAD -> PYLD, …). No second vocabulary is introduced.
    """
    try:
        from app.ingest.channel_dict import subsystem_of

        return subsystem_of(channel).value
    except Exception:  # pragma: no cover — channel dictionary is in-tree
        return "UNKNOWN"


def _candidate_fault_ids_by_channel(
    hypothesis_set: Any,
) -> dict[str, tuple[str, ...]]:
    """Map channel -> deterministic candidate fault ids that reference it.

    Read from the EXISTING hypothesis set (``app.diagnosis.candidates``). This
    layer does not generate hypotheses and does not score them; it only needs to
    know which candidate faults each observed channel participates in, so the
    hypothesis-compatibility signal has something deterministic to compare.
    """
    if hypothesis_set is None:
        return {}
    out: dict[str, list[str]] = {}
    try:
        for hypothesis in getattr(hypothesis_set, "hypotheses", ()) or ():
            fault_id = str(getattr(hypothesis, "fault_id", "") or "")
            if not fault_id:
                continue
            for channel in getattr(hypothesis, "affected_channels", ()) or ():
                out.setdefault(str(channel), [])
                if fault_id not in out[str(channel)]:
                    out[str(channel)].append(fault_id)
    except Exception:  # pragma: no cover — defensive on a foreign shape
        return {}
    return {k: tuple(v) for k, v in out.items()}


def build_observation_events(
    detection_report: Any,
    crash_dump: dict[str, Any] | None = None,
    hypothesis_set: Any = None,
    scenario_id: str = "",
) -> tuple[ObservationEvent, ...]:
    """Project a detection report into comparable observation events.

    Args:
        detection_report: An ``app.detection.models.AnomalyReport``. ``None`` or
            a report with no anomalous channels yields an empty tuple — the
            correct answer when there is nothing to reconcile.
        crash_dump: The canonicalized crash dump, used only to resolve offset
            strings to seconds. Optional.
        hypothesis_set: An ``app.diagnosis.candidates.HypothesisSet``, used only
            to attach candidate fault ids by reference. Optional.
        scenario_id: Run/scenario identity, folded into every event id so two
            scenarios never collide.

    Returns:
        Events in deterministic order: by descending severity rank, then channel
        name. Same report in, same tuple out, byte for byte.

    Never raises. A malformed finding produces an event carrying a ``defect``
    rather than an exception or a silent omission — the separation logic must be
    able to say "I could not evaluate this", which it cannot do for an
    observation that was dropped.
    """
    if detection_report is None:
        return ()

    findings = list(getattr(detection_report, "channels", ()) or ())
    if not findings:
        return ()

    offsets = _offset_seconds_map(crash_dump)
    faults_by_channel = _candidate_fault_ids_by_channel(hypothesis_set)
    scenario = str(scenario_id or "")

    events: list[ObservationEvent] = []

    for finding in findings:
        channel = str(getattr(finding, "channel", "") or "").strip()
        if not channel:
            # A finding with no channel cannot be attributed to a subsystem, a
            # case, or an operator. Recording it as an event with an empty
            # channel would let it match everything.
            continue

        defects: list[str] = []

        anomalies = list(getattr(finding, "anomalies", ()) or ())
        anomaly_ids = tuple(
            str(getattr(a, "anomaly_id", "") or "")
            for a in anomalies
            if getattr(a, "anomaly_id", None)
        )
        if not anomaly_ids:
            defects.append(
                "no anomaly_id on any finding for this channel; provenance to "
                "detection cannot be established"
            )

        detectors: list[str] = []
        for detector in getattr(finding, "detectors", ()) or ():
            name = getattr(detector, "value", None) or str(detector)
            if name not in detectors:
                detectors.append(name)

        timestamps: list[str] = []
        directions: list[str] = []
        for anomaly in anomalies:
            stamp = str(getattr(anomaly, "timestamp", "") or "").strip()
            if stamp and stamp not in timestamps:
                timestamps.append(stamp)
            evidence = getattr(anomaly, "evidence", None)
            direction = DIRECTION_UNKNOWN
            if isinstance(evidence, dict):
                direction = str(
                    evidence.get("direction", DIRECTION_UNKNOWN)
                    or DIRECTION_UNKNOWN
                ).upper()
            if direction not in directions:
                directions.append(direction)

        if not timestamps:
            defects.append(
                "no timestamp on any finding for this channel; temporal "
                "signals cannot be evaluated"
            )

        seconds = [
            s for s in (_parse_offset(t, offsets) for t in timestamps)
            if s is not None
        ]
        if timestamps and not seconds:
            defects.append(
                f"none of {len(timestamps)} timestamp offset(s) could be parsed "
                f"to seconds; temporal signals cannot be evaluated"
            )

        severity_obj = getattr(finding, "severity", None)
        severity = str(getattr(severity_obj, "value", None) or severity_obj or "")
        try:
            from app.detection.models import severity_rank

            rank = severity_rank(severity) if severity else 0
        except Exception:  # pragma: no cover — detection is in-tree
            rank = 0

        subsystem = _resolve_subsystem(channel)
        if subsystem == "UNKNOWN":
            defects.append(
                f"channel '{channel}' is not in the channel dictionary, so its "
                f"subsystem is unknown and subsystem/physical signals cannot be "
                f"evaluated"
            )

        events.append(ObservationEvent(
            event_id=make_event_id(channel, anomaly_ids, scenario),
            channel=channel,
            subsystem=subsystem,
            severity=severity,
            severity_rank=rank,
            detectors=tuple(detectors),
            anomaly_ids=anomaly_ids,
            timestamps=tuple(timestamps),
            directions=tuple(directions),
            first_seen_s=min(seconds) if seconds else None,
            last_seen_s=max(seconds) if seconds else None,
            candidate_fault_ids=faults_by_channel.get(channel, ()),
            corroborated=bool(getattr(finding, "corroborated", False)),
            scenario_id=scenario,
            source_ref="app.detection.models.AnomalyReport.channels",
            defects=tuple(defects),
        ))

    # Deterministic ordering. Detection already sorts by severity, but relying
    # on an upstream sort for reproducibility here would make this module's
    # output depend on a detail it does not own.
    events.sort(key=lambda e: (-e.severity_rank, e.channel))
    return tuple(events)


def build_events_from_dicts(
    rows: Iterable[dict[str, Any]],
    scenario_id: str = "",
) -> tuple[ObservationEvent, ...]:
    """Project explicitly-described observations into events.

    Used by the deterministic demo dataset and by tests, so a scenario can state
    "these are the two observations" without having to synthesise telemetry that
    happens to make the detectors fire in the required pattern. The DECISION is
    still computed by the engine — only the INPUT is declared.

    Recognised keys: ``channel`` (required), ``detectors``, ``severity``,
    ``timestamps``, ``directions``, ``anomaly_ids``, ``first_seen_s``,
    ``last_seen_s``, ``candidate_fault_ids``, ``corroborated``, ``subsystem``.
    Unknown keys are ignored rather than rejected.
    """
    scenario = str(scenario_id or "")
    events: list[ObservationEvent] = []

    for row in rows or ():
        if not isinstance(row, dict):
            continue
        channel = str(row.get("channel") or "").strip()
        if not channel:
            continue

        defects: list[str] = []
        timestamps = tuple(str(t) for t in (row.get("timestamps") or ()))
        seconds = [
            s for s in (_parse_offset(t, {}) for t in timestamps) if s is not None
        ]

        first = row.get("first_seen_s")
        last = row.get("last_seen_s")
        first_s = float(first) if isinstance(first, (int, float)) else (
            min(seconds) if seconds else None
        )
        last_s = float(last) if isinstance(last, (int, float)) else (
            max(seconds) if seconds else first_s
        )
        if first_s is None:
            defects.append(
                "no parseable timestamp or explicit first_seen_s; temporal "
                "signals cannot be evaluated"
            )

        subsystem = str(row.get("subsystem") or "").strip().upper()
        if not subsystem:
            subsystem = _resolve_subsystem(channel)
        if subsystem == "UNKNOWN":
            defects.append(
                f"channel '{channel}' has no known subsystem; subsystem and "
                f"physical signals cannot be evaluated"
            )

        severity = str(row.get("severity") or "MEDIUM").upper()
        try:
            from app.detection.models import severity_rank

            rank = severity_rank(severity)
        except Exception:  # pragma: no cover — detection is in-tree
            rank = 0

        anomaly_ids = tuple(str(a) for a in (row.get("anomaly_ids") or ()))
        if not anomaly_ids:
            # Derive a deterministic reference from the declared identity so the
            # event id is still content-addressed and reproducible.
            anomaly_ids = tuple(
                f"AN-DECL-{channel}-{t}" for t in (timestamps or ("T-0s",))
            )

        events.append(ObservationEvent(
            event_id=make_event_id(channel, anomaly_ids, scenario),
            channel=channel,
            subsystem=subsystem,
            severity=severity,
            severity_rank=rank,
            detectors=tuple(str(d).upper() for d in (row.get("detectors") or ())),
            anomaly_ids=anomaly_ids,
            timestamps=timestamps,
            directions=tuple(
                str(d).upper() for d in (row.get("directions") or ())
            ) or (DIRECTION_UNKNOWN,),
            first_seen_s=first_s,
            last_seen_s=last_s,
            candidate_fault_ids=tuple(
                str(f) for f in (row.get("candidate_fault_ids") or ())
            ),
            corroborated=bool(row.get("corroborated", False)),
            scenario_id=scenario,
            source_ref="declared observation (demo/test fixture)",
            defects=tuple(defects),
        ))

    events.sort(key=lambda e: (-e.severity_rank, e.channel))
    return tuple(events)
