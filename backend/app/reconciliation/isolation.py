"""
SENTINEL — Cross-Case Evidence Isolation (app/reconciliation/isolation.py)

Phase 24.  Enforces case-level evidence boundaries.

A Case is the boundary of relevance for downstream evidence assembly,
physics validation, RAG context, and LLM reasoning. Evidence belonging to
Case A must not enter Case B's evidence bundle unless an explicit, sanctioned
CaseRelationship permits referencing it.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from app.reconciliation.contract import (
    Case,
    ReconciliationResult,
    RelationshipType,
)


class CrossCaseLeakageError(ValueError):
    """Raised when evidence from an unrelated case is detected inside a case bundle."""
    pass


class CaseIsolationBoundary:
    """Deterministic isolation boundary gating evidence and context per case."""

    @staticmethod
    def isolate_evidence_for_case(
        case_id: str,
        result: ReconciliationResult,
        evidence_items: Iterable[Any],
        allow_related: bool = True,
    ) -> tuple[Any, ...]:
        """Filter an evidence sequence so only in-case (and sanctioned related) items remain.

        Args:
            case_id: Target Case identifier.
            result: The complete ReconciliationResult.
            evidence_items: Objects with an `anomaly_ids`, `channel`, or `event_id` attribute.
            allow_related: If True, allows evidence from cases with an explicit RELATED relationship.

        Returns:
            Tuple of evidence items safe for this case.
        """
        target_case = result.case(case_id)
        if target_case is None:
            return ()

        in_scope_channels = set(target_case.channels)
        in_scope_events = set(target_case.event_ids)

        if allow_related:
            related_ids = set(result.related_case_ids(case_id))
            for rel_id in related_ids:
                rel_case = result.case(rel_id)
                if rel_case:
                    in_scope_channels.update(rel_case.channels)
                    in_scope_events.update(rel_case.event_ids)

        filtered: list[Any] = []
        for item in evidence_items:
            if item is None or isinstance(item, (str, int, float, bool)):
                continue

            item_ch = getattr(item, "channel", None)
            item_ev = getattr(item, "event_id", None)
            item_an = getattr(item, "anomaly_ids", None)

            # Fail-closed: Must have at least one identifiable attribute
            if item_ch is None and item_ev is None and item_an is None:
                continue

            # Check by channel if item has channel
            if item_ch is not None and item_ch not in in_scope_channels:
                continue

            # Check by event_id if item has event_id
            if item_ev is not None and item_ev not in in_scope_events:
                continue

            filtered.append(item)

        return tuple(filtered)

    @staticmethod
    def assert_no_cross_case_leakage(
        case_id: str,
        result: ReconciliationResult,
        bundle_evidence: Iterable[Any],
    ) -> None:
        """Assert that no forbidden cross-case evidence exists in the bundle.

        Raises:
            CrossCaseLeakageError: If any evidence item belongs strictly to a SEPARATE case.
        """
        target_case = result.case(case_id)
        if target_case is None:
            raise CrossCaseLeakageError(f"Case '{case_id}' not found in reconciliation result.")

        # Find all cases that are strictly SEPARATE from target_case
        separate_case_ids: set[str] = set()
        for r in result.relationships_for(case_id):
            if r.relationship_type is RelationshipType.SEPARATE:
                other = r.other_case(case_id)
                if other:
                    separate_case_ids.add(other)

        forbidden_channels: set[str] = set()
        for sep_id in separate_case_ids:
            sep_case = result.case(sep_id)
            if sep_case:
                # Channels unique to the separate case
                for ch in sep_case.channels:
                    if ch not in target_case.channels:
                        forbidden_channels.add(ch)

        for item in bundle_evidence:
            item_ch = getattr(item, "channel", None)
            if item_ch and item_ch in forbidden_channels:
                raise CrossCaseLeakageError(
                    f"Evidence item referencing channel '{item_ch}' belongs to separate "
                    f"case ({separate_case_ids}) and leaked into bundle for '{case_id}'."
                )
