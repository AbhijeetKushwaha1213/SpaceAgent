"""
SENTINEL — Case Construction and Indexing (app/reconciliation/cases.py)

Phase 24.  Deterministic Case model builders and cross-case indexers.

A Case groups observation events attributed to one underlying fault scenario.
This module provides deterministic constructors and indexing structures
so that downstream stages (isolation, RAG, evidence assembly) can look up
case boundaries without leaking evidence across cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from app.reconciliation.config import (
    RECONCILIATION_CONFIG_VERSION,
    RECONCILIATION_ENGINE_VERSION,
)
from app.reconciliation.contract import (
    Case,
    ObservationEvent,
    make_case_id,
)


def build_case_from_events(
    events: Iterable[ObservationEvent],
    scenario_id: str = "",
    config_version: str = RECONCILIATION_CONFIG_VERSION,
    engine_version: str = RECONCILIATION_ENGINE_VERSION,
    reasons: tuple[str, ...] = (),
    merged_from: tuple[str, ...] = (),
) -> Case:
    """Build a deterministic Case instance from a cluster of ObservationEvents."""
    event_list = list(events)
    if not event_list:
        raise ValueError("Cannot build a Case from an empty event cluster.")

    event_ids = tuple(sorted(e.event_id for e in event_list))
    case_id = make_case_id(event_ids, scenario_id)

    channels = tuple(sorted(set(e.channel for e in event_list if e.channel)))
    subsystems = tuple(sorted(set(e.subsystem for e in event_list if e.subsystem and e.subsystem != "UNKNOWN")))
    if not subsystems:
        subsystems = ("UNKNOWN",)

    start_candidates = [e.first_seen_s for e in event_list if e.first_seen_s is not None]
    end_candidates = [e.last_seen_s for e in event_list if e.last_seen_s is not None]

    window_start_s = min(start_candidates) if start_candidates else None
    window_end_s = max(end_candidates) if end_candidates else window_start_s

    all_defects: list[str] = []
    for e in event_list:
        for d in e.defects:
            if d not in all_defects:
                all_defects.append(d)

    return Case(
        case_id=case_id,
        event_ids=event_ids,
        channels=channels,
        subsystems=subsystems,
        window_start_s=window_start_s,
        window_end_s=window_end_s,
        scenario_id=str(scenario_id or ""),
        config_version=config_version,
        engine_version=engine_version,
        reasons=tuple(reasons) or (f"Derived from {len(event_ids)} observation event(s).",),
        merged_from=tuple(sorted(merged_from)),
        defects=tuple(all_defects),
    )


@dataclass(frozen=True)
class CaseEvidenceIndex:
    """Read-only index mapping case_id to its scoped evidence, events, and channels."""

    case_id: str
    event_ids: tuple[str, ...]
    channels: tuple[str, ...]
    subsystems: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    @classmethod
    def from_case(
        cls,
        case: Case,
        evidence_refs: tuple[tuple[str, str], ...] = (),
    ) -> CaseEvidenceIndex:
        """Construct an index from a Case and global (event_id, evidence_id) references."""
        case_events = set(case.event_ids)
        scoped_evidence: list[str] = []
        for ev_id, evid_id in evidence_refs:
            if ev_id in case_events and evid_id not in scoped_evidence:
                scoped_evidence.append(evid_id)

        return cls(
            case_id=case.case_id,
            event_ids=case.event_ids,
            channels=case.channels,
            subsystems=case.subsystems,
            evidence_ids=tuple(scoped_evidence),
        )
