"""
SENTINEL — Phase 26 run_e2e --reconciliation Flag Tests
(tests/test_phase26_run_e2e_reconciliation_flag.py)

Covers the one-command judge-facing demo activation path (spec §S9, §S10):

    python -m demo.run_e2e --scenario B --reconciliation

The flag must (a) activate reconciliation for THIS process only (never change
the production default), (b) print the deterministic CASE 00N separation
spotlight with the CORRELATION != IDENTITY framing, and (c) leave every
pre-existing invocation (--scenario A/B/C/D/ALL, --json) working unchanged.

The canonical CONFLICT + human-review story lives in demo scenario C; it is
asserted here at the demo-engine layer (the web presets do not produce a
CONFLICT, so this is where State E is exercised end-to-end).
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("SECURE_DEV_MODE", "1")

import pytest

from demo import run_e2e
from demo.e2e_demo import (
    EndToEndDemoEngine,
    build_scenario_b_two_separate_faults,
    build_scenario_c_conflicting_evidence,
)
from app.reconciliation import RelationshipType


@pytest.fixture(autouse=True)
def restore_recon_env():
    """run_e2e.main() mutates os.environ['RECONCILIATION_ENABLED'] globally.

    Snapshot and restore it so activation cannot leak into other tests and,
    critically, so the production default (unset) is preserved after the run.
    """
    sentinel = object()
    prior = os.environ.get("RECONCILIATION_ENABLED", sentinel)
    try:
        yield
    finally:
        if prior is sentinel:
            os.environ.pop("RECONCILIATION_ENABLED", None)
        else:
            os.environ["RECONCILIATION_ENABLED"] = prior


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", ["run_e2e", *argv])
    run_e2e.main()


# ── §S9 / §S10 — the --reconciliation flag activates & narrates separation ───
def test_reconciliation_flag_activates_and_prints_focus(monkeypatch, capsys):
    _run_cli(monkeypatch, ["--scenario", "B", "--reconciliation"])
    out = capsys.readouterr().out

    # Explicit per-process activation is announced and actually set.
    assert "RECONCILIATION EXPLICITLY ACTIVATED" in out
    assert os.environ.get("RECONCILIATION_ENABLED") == "true"

    # The deterministic separation spotlight and its framing are printed.
    assert "DETERMINISTIC CASE SEPARATION" in out
    assert "CORRELATION != IDENTITY" in out
    # Scenario B is two separate faults -> two stable case labels.
    assert "CASE 001" in out
    assert "CASE 002" in out
    # RELATED must be narrated as physics-validation-pending, not physical proof.
    assert "physics" in out.lower() and "PENDING" in out


def test_flag_absent_does_not_activate(monkeypatch, capsys):
    _run_cli(monkeypatch, ["--scenario", "B"])
    out = capsys.readouterr().out
    assert "RECONCILIATION EXPLICITLY ACTIVATED" not in out
    assert "DETERMINISTIC CASE SEPARATION" not in out
    # The production default is untouched when the flag is not passed.
    assert os.environ.get("RECONCILIATION_ENABLED") is None


# ── §S10 State E — canonical CONFLICT + human review (demo scenario C) ────────
def test_scenario_c_conflict_raises_human_review():
    engine = EndToEndDemoEngine(mode="stub")
    d = engine.run_scenario(build_scenario_c_conflicting_evidence()).to_dict()

    # Robust to either "CONFLICT" or an enum-repr "RelationshipType.CONFLICT".
    has_conflict = any(
        RelationshipType(str(r["relationship_type"]).split(".")[-1])
        == RelationshipType.CONFLICT
        for r in d["relationships"]
    )
    assert has_conflict, "scenario C must yield a CONFLICT relationship"
    # Reconciliation does not resolve a conflict; it raises the review gate.
    assert d["human_review_required"] is True


def test_scenario_b_two_separate_related_not_merged():
    engine = EndToEndDemoEngine(mode="stub")
    d = engine.run_scenario(build_scenario_b_two_separate_faults()).to_dict()
    assert len(d["cases"]) == 2
    # Correlation != identity: B's cases are RELATED but never merged.
    for r in d["relationships"]:
        rt = RelationshipType(str(r["relationship_type"]).split(".")[-1])
        assert rt != RelationshipType.SAME_CASE
        assert rt != RelationshipType.DUPLICATE


# ── §S9 regression — pre-existing invocations still work unchanged ────────────
def test_json_reconciliation_still_valid_json(monkeypatch, capsys):
    _run_cli(monkeypatch, ["--scenario", "B", "--json", "--reconciliation"])
    out = capsys.readouterr().out
    payload = json.loads(out)  # must be pure, parseable JSON
    assert payload["scenario_id"]
    assert len(payload["cases"]) == 2


def test_plain_json_unchanged_by_phase26(monkeypatch, capsys):
    _run_cli(monkeypatch, ["--scenario", "A", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["scenario_id"]
    # No activation banner or focus text bleeds into the existing JSON path.
    assert "RECONCILIATION EXPLICITLY ACTIVATED" not in out


def test_scenario_all_runs_without_error(monkeypatch, capsys):
    _run_cli(monkeypatch, ["--scenario", "ALL"])
    out = capsys.readouterr().out
    # Existing 4-scenario narrative is intact.
    for label in ("SCENARIO:", "TELEMETRY & OBSERVATION RECONCILIATION"):
        assert label in out
