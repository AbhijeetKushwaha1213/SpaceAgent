"""
SENTINEL — Reconciliation Configuration (app/reconciliation/config.py)

Phase 24.  The feature flag and every threshold the separation logic uses.

Why the thresholds live HERE and nowhere else
---------------------------------------------
A number buried in a comparison inside an engine is not reviewable. An engineer
asked "why did Sentinel decide these two observations were the same fault?"
needs to be able to read the answer, disagree with it, and change it. So every
threshold is:

  * named,
  * given an explicit unit,
  * documented as an ENGINEERING ASSUMPTION rather than a measured constant,
  * versioned via ``RECONCILIATION_CONFIG_VERSION`` so an audit record says
    which numbers produced a decision,
  * and pinned by boundary tests at ``<``, ``==`` and ``>``.

NONE of these numbers is a physical constant. None was fitted to labelled data,
because this repository has no labelled multi-fault correlation dataset. They are
defaults chosen to be CONSERVATIVE: where a threshold could push a pair toward
"same case" or toward "keep separate", it is set so that the pair stays separate.
Claiming more precision than that would be fabrication.

The flag
--------
``reconciliation_enabled()`` mirrors ``app.llm.router_contract.router_enabled()``
exactly — same accepted literals, same default-off semantics. It reads the
environment on every call rather than caching, so a test can toggle it with
``unittest.mock.patch.dict(os.environ, ...)`` without module reloads. That is the
established convention in this repository (ROUTER_ENABLED, SECURE_DEV_MODE); no
new configuration mechanism is introduced and no ``.env`` file is modified.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: Version of the threshold set below. Recorded in every relationship record and
#: every audit payload, so a decision can be replayed against the exact numbers
#: that produced it. Bump when any value in ReconciliationConfig changes.
RECONCILIATION_CONFIG_VERSION = "1.0.0"

#: Version of the decision RULES (the priority-ordered ladder in engine.py).
#: Separate from the config version because rules and numbers change
#: independently.
RECONCILIATION_ENGINE_VERSION = "1.0.0"


def reconciliation_enabled() -> bool:
    """Whether the reconciliation/separation layer participates in the pipeline.

    Default is False. While this returns False the existing pipeline must
    behave exactly as it did before Phase 24: no extra SSE events, no extra
    audit stage, no change to the LLM bundle, no change to RAG results.

    Only the literal values ``true``/``1``/``yes`` (case-insensitive) enable it;
    every other value — including an absent variable — keeps it disabled.
    """
    raw = os.environ.get("RECONCILIATION_ENABLED", "").lower().strip()
    return raw in ("true", "1", "yes")


@dataclass(frozen=True)
class ReconciliationConfig:
    """Every threshold the separation logic consults.

    Frozen so a decision cannot be produced against thresholds that changed
    halfway through a run.
    """

    # ── Signal 1: temporal proximity ────────────────────────────────────────
    temporal_same_case_window_s: float = 30.0
    """ENGINEERING ASSUMPTION (seconds). Two observations whose onsets fall
    within this window are temporally close enough that identity is *possible*.
    It is NOT sufficient on its own — see IDENTITY_MIN_SUPPORTING_SIGNALS. The
    specification explicitly forbids a bare "within 60 seconds = same case"
    rule, and this threshold cannot express one: temporal proximity contributes
    a single vote out of eight.

    Chosen at 30 s because the fastest propagation delay declared in
    ``app.diagnosis.propagation.PROPAGATION_EDGES`` is "seconds" (EPS -> AOCS,
    EPS -> OBC). A window shorter than the fastest declared mechanism would
    reject genuine same-fault pairs; a much longer one would sweep in
    independent faults."""

    temporal_related_window_s: float = 300.0
    """ENGINEERING ASSUMPTION (seconds). Outside the same-case window but within
    this one, a pair may still be RELATED if a propagation path explains it.
    300 s covers the slowest declared delay class ("minutes") with margin.
    Beyond this, temporal proximity stops being evidence of anything."""

    # ── Signal 3: telemetry / channel relationship ──────────────────────────
    channel_overlap_min_jaccard: float = 0.50
    """ENGINEERING ASSUMPTION (dimensionless, 0..1). Jaccard index of the two
    channel sets above which the channel signal supports identity. Set at 0.50
    so a majority of the observed channels must coincide. This is set algebra
    over channel NAMES — never a vector embedding, never a learned metric."""

    channel_shared_min_count: int = 1
    """ENGINEERING ASSUMPTION (count). At least this many channels must be
    common before the channel signal is anything other than NEUTRAL. Sharing a
    channel is a hint, not identity — the specification's motivating example is
    two unrelated faults that both surface ``attitude_error``."""

    # ── Signal 4: deterministic signal-pattern similarity ───────────────────
    pattern_similarity_min: float = 0.75
    """ENGINEERING ASSUMPTION (dimensionless, 0..1). Similarity of the
    (detector set, severity rank, direction) pattern above which the pattern
    signal supports identity. Computed by exact set/tuple comparison, NOT by
    embedding distance: the specification forbids pure vector similarity as an
    identity mechanism."""

    # ── Signal 5: physical relationship ─────────────────────────────────────
    propagation_min_strength: float = 0.50
    """ENGINEERING ASSUMPTION (dimensionless, 0..1). Minimum declared edge
    strength in the propagation graph for the physical signal to support a
    relationship. 0.50 matches ``PropagationEdge.strength``'s own default, so
    this layer inherits the propagation model's notion of a weak edge rather
    than inventing a second one."""

    # ── Decision ladder ────────────────────────────────────────────────────
    identity_min_supporting_signals: int = 3
    """ENGINEERING ASSUMPTION (count). How many INDEPENDENT signals must support
    identity before SAME_CASE may be returned. Three is the smallest number that
    forces corroboration across signal families (time, structure, physics)
    rather than letting one strong signal decide alone. Fewer than this and the
    pair stays UNCERTAIN, which keeps the cases separate."""

    require_physics_non_opposition: bool = True
    """When True (the only supported setting) a SAME_CASE decision is refused if
    the physical-relationship signal OPPOSES it. Physics is an authority here,
    not a vote to be outweighed."""


#: The default configuration. Callers may pass their own instance; the pipeline
#: uses this one.
DEFAULT_CONFIG = ReconciliationConfig()


def config_status() -> dict[str, object]:
    """Self-describing status, for the API surface and the audit record.

    Mirrors the ``*_status()`` convention used by ``app.diagnosis.propagation``,
    ``app.validation.physics`` and ``app.diagnosis.candidates``.
    """
    return {
        "enabled": reconciliation_enabled(),
        "config_version": RECONCILIATION_CONFIG_VERSION,
        "engine_version": RECONCILIATION_ENGINE_VERSION,
        "flag_name": "RECONCILIATION_ENABLED",
        "thresholds": {
            "temporal_same_case_window_s": DEFAULT_CONFIG.temporal_same_case_window_s,
            "temporal_related_window_s": DEFAULT_CONFIG.temporal_related_window_s,
            "channel_overlap_min_jaccard": DEFAULT_CONFIG.channel_overlap_min_jaccard,
            "channel_shared_min_count": DEFAULT_CONFIG.channel_shared_min_count,
            "pattern_similarity_min": DEFAULT_CONFIG.pattern_similarity_min,
            "propagation_min_strength": DEFAULT_CONFIG.propagation_min_strength,
            "identity_min_supporting_signals": (
                DEFAULT_CONFIG.identity_min_supporting_signals
            ),
        },
        "threshold_provenance": (
            "Every threshold is a reviewable engineering assumption, not a "
            "measured physical constant. None was fitted to labelled data; this "
            "repository has no labelled multi-fault correlation dataset. Where a "
            "value could bias a pair toward identity or toward separation, it is "
            "set to keep the pair separate."
        ),
        "authority": (
            "Reconciliation classifies relationships between observations. It "
            "does not validate physics, authorize commands, approve recovery "
            "plans, or clear human review."
        ),
    }
