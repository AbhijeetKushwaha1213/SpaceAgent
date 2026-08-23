"""
SENTINEL — Phase 26 Reconciliation API Endpoint Tests
(tests/test_phase26_reconciliation_endpoint.py)

Verifies POST /api/v1/reconciliation end-to-end against the *real* deterministic
Phase 24 engine — the same engine the FDIR pipeline and the Phase 24/25 tests
exercise. There is no second engine and no client-side reconciliation logic:
the endpoint is a pure projection sibling of /detect and /physics.

Coverage map (spec §S15):
  - flag OFF is the default; a disabled result is explicit, never a silent zero (§3, §14)
  - flag parsing (true/1/yes enable; everything else disables)
  - per-scenario case counts on real web scenarios
  - endpoint output == independently-invoked engine output (§4, §6)
  - no audit side-effect (parity with /detect, /physics) (§13)
  - all documented envelope keys present (§5)
  - no raw telemetry value leaks in cases[] (§S16)
  - merge_permitted matches RelationshipType semantics (DUPLICATE/SAME_CASE only)
  - relationship_type serialized as a plain enum value (frontend depends on it)
  - RELATED carries no physics authority; physics_validation == "pending" (§11)
"""

from __future__ import annotations

import json
import os

# Auth is tested elsewhere; run these against dev mode so no API key is required.
os.environ.setdefault("SECURE_DEV_MODE", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.models import CrashDumpRequest
from app.api.scenarios import get_all_scenarios
from app.detection import run_detection_on_crash_dump
from app.reconciliation import (
    RECONCILIATION_CONFIG_VERSION,
    RECONCILIATION_ENGINE_VERSION,
    ReconciliationEngine,
    ReconciliationInput,
    RelationshipType,
    build_observation_events,
)
from app.security.sanitization import sanitize_telemetry_payload_data

RECON_PATH = "/api/v1/reconciliation"

# Empirical ground truth on the real web scenarios (production path:
# sanitize -> detect -> build_observation_events -> reconcile). These are the
# same numbers the engine yields directly (asserted by
# test_endpoint_matches_direct_engine); the constants document intent.
EXPECTED_CASE_COUNTS = {"1": 1, "3": 3, "5": 2, "6": 3, "4": 0}

_ENABLED_ENV_VALUES = ["true", "1", "yes", "TRUE", "Yes", "  true  "]
_DISABLED_ENV_VALUES = ["false", "0", "no", "", "off", "enabled"]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def scenarios() -> dict:
    """All web scenarios keyed by string scenario_id (each dict is a dump payload)."""
    return {str(s["scenario_id"]): s for s in get_all_scenarios()}


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("RECONCILIATION_ENABLED", "true")


def _disable(monkeypatch) -> None:
    # Explicitly remove so a value leaked by another test (e.g. the run_e2e
    # flag, which mutates os.environ globally) cannot make "disabled" pass falsely.
    monkeypatch.delenv("RECONCILIATION_ENABLED", raising=False)


# ── §3 / §14 — flag OFF is the default, and disabled != silent zero ──────────
def test_flag_off_by_default_returns_disabled_envelope(client, scenarios, monkeypatch):
    _disable(monkeypatch)
    # scenario 5 WOULD produce 2 cases if enabled — proving the flag gates execution.
    resp = client.post(RECON_PATH, json=scenarios["5"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["reconciliation_enabled"] is False
    assert body["executed"] is False
    assert body["total_cases"] == 0
    assert body["cases"] == []
    assert body["relationships"] == []
    assert body["physics_validation"] == "not_applicable"


def test_disabled_result_is_explicit_not_silent_zero(client, scenarios, monkeypatch):
    _disable(monkeypatch)
    body = client.post(RECON_PATH, json=scenarios["5"]).json()
    # An explicit disabled signal the UI can distinguish from "engine ran, found nothing".
    assert body["executed"] is False
    assert body["flag_name"] == "RECONCILIATION_ENABLED"
    assert "note" in body and "RECONCILIATION_ENABLED" in body["note"]
    assert "authority_note" in body and "CORRELATION != IDENTITY" in body["authority_note"]


def test_disabled_does_not_run_engine(client, scenarios, monkeypatch):
    _disable(monkeypatch)
    body = client.post(RECON_PATH, json=scenarios["3"]).json()  # 3 cases when enabled
    assert body["total_cases"] == 0
    assert body["related_relationships"] == 0
    assert body["human_review_required"] is False


@pytest.mark.parametrize("value", _ENABLED_ENV_VALUES)
def test_flag_values_that_enable(client, scenarios, monkeypatch, value):
    monkeypatch.setenv("RECONCILIATION_ENABLED", value)
    body = client.post(RECON_PATH, json=scenarios["5"]).json()
    assert body["reconciliation_enabled"] is True
    assert body["executed"] is True


@pytest.mark.parametrize("value", _DISABLED_ENV_VALUES)
def test_flag_values_that_stay_disabled(client, scenarios, monkeypatch, value):
    monkeypatch.setenv("RECONCILIATION_ENABLED", value)
    body = client.post(RECON_PATH, json=scenarios["5"]).json()
    assert body["reconciliation_enabled"] is False
    assert body["executed"] is False


# ── per-scenario case counts on real web scenarios ───────────────────────────
def test_scenario5_two_cases_one_related(client, scenarios, monkeypatch):
    _enable(monkeypatch)
    body = client.post(RECON_PATH, json=scenarios["5"]).json()
    assert body["total_cases"] == 2
    assert body["related_relationships"] == 1
    assert body["conflicts_detected"] == 0
    assert body["human_review_required"] is False
    rels = body["relationships"]
    assert len(rels) == 1
    assert rels[0]["relationship_type"] == "RELATED"
    # CORRELATION != IDENTITY: a RELATED link must NOT permit a merge.
    assert rels[0]["merge_permitted"] is False


def test_scenario1_single_isolated_case(client, scenarios, monkeypatch):
    _enable(monkeypatch)
    body = client.post(RECON_PATH, json=scenarios["1"]).json()
    assert body["total_cases"] == 1
    assert body["related_relationships"] == 0
    # A single case has no cross-case relationships to establish.
    assert body["relationships"] == []


def test_scenario4_executed_zero_is_not_disabled(client, scenarios, monkeypatch):
    _enable(monkeypatch)
    body = client.post(RECON_PATH, json=scenarios["4"]).json()
    # The engine RAN (enabled + executed) and legitimately found nothing to
    # reconcile — a state the UI must distinguish from the disabled state.
    assert body["reconciliation_enabled"] is True
    assert body["executed"] is True
    assert body["total_cases"] == 0
    assert body["physics_validation"] == "pending"


@pytest.mark.parametrize("sid", sorted(EXPECTED_CASE_COUNTS))
def test_expected_case_counts(client, scenarios, monkeypatch, sid):
    _enable(monkeypatch)
    body = client.post(RECON_PATH, json=scenarios[sid]).json()
    assert body["total_cases"] == EXPECTED_CASE_COUNTS[sid]


# ── §4 / §6 — the endpoint runs the SAME engine, no second implementation ────
@pytest.mark.parametrize("sid", ["1", "3", "5", "6"])
def test_endpoint_matches_direct_engine(client, scenarios, monkeypatch, sid):
    _enable(monkeypatch)
    scn = scenarios[sid]

    # Reproduce the endpoint's exact preprocessing, then invoke the engine directly.
    payload = sanitize_telemetry_payload_data(
        CrashDumpRequest(**scn).model_dump(mode="json", exclude_none=True)
    )
    scenario_id = str(payload.get("scenario_id") or "")
    report = run_detection_on_crash_dump(payload)
    events = build_observation_events(report, crash_dump=payload, scenario_id=scenario_id)
    direct = ReconciliationEngine().reconcile(
        ReconciliationInput(events=tuple(events), scenario_id=scenario_id)
    )

    body = client.post(RECON_PATH, json=scn).json()

    assert body["total_cases"] == direct.case_count
    endpoint_types = sorted(r["relationship_type"] for r in body["relationships"])
    direct_types = sorted(r.relationship_type.value for r in direct.relationships)
    assert endpoint_types == direct_types
    assert body["human_review_required"] == direct.human_review_required


# ── §13 — pure projection: no audit side-effect (parity with /detect) ────────
def test_no_audit_side_effect(client, scenarios, monkeypatch):
    _enable(monkeypatch)
    before = client.get("/api/v1/audit/status").json()["run_count"]
    for _ in range(3):
        client.post(RECON_PATH, json=scenarios["5"])
    after = client.get("/api/v1/audit/status").json()["run_count"]
    assert after == before  # reconciliation projection writes no audit record


# ── §5 — the envelope carries every documented field ─────────────────────────
def test_required_keys_present_when_enabled(client, scenarios, monkeypatch):
    _enable(monkeypatch)
    body = client.post(RECON_PATH, json=scenarios["5"]).json()
    required = {
        "reconciliation_enabled", "executed", "scenario_id", "flag_name",
        "config_version", "engine_version", "total_cases", "isolated_cases",
        "related_relationships", "separate_relationships", "conflicts_detected",
        "uncertain_relationships", "merges_performed", "human_review_required",
        "cases", "relationships", "reasons", "warnings", "authority_note",
        "physics_validation",
    }
    assert required.issubset(body.keys())
    assert body["config_version"] == RECONCILIATION_CONFIG_VERSION
    assert body["engine_version"] == RECONCILIATION_ENGINE_VERSION


# ── §S16 — no raw telemetry value leaks through the reconciliation projection ─
def test_no_raw_value_leak_in_cases(client, scenarios, monkeypatch):
    _enable(monkeypatch)
    body = client.post(RECON_PATH, json=scenarios["5"]).json()
    for case in body["cases"]:
        # Cases describe scope (channels/subsystems/windows), never raw readings.
        assert "value" not in case
    # Any numeric "value" belongs only to derived signal metrics, never top-level.
    for rel in body["relationships"]:
        assert "value" not in rel


def test_no_raw_telemetry_window_leaks(client, scenarios, monkeypatch):
    _enable(monkeypatch)
    blob = json.dumps(client.post(RECON_PATH, json=scenarios["5"]).json())
    # The raw input telemetry arrays must not survive into the reconciliation view.
    assert "pre_fault_telemetry_window" not in blob
    assert "raw_text_head" not in blob


# ── merge semantics & clean serialization the frontend depends on ────────────
@pytest.mark.parametrize("sid", ["1", "3", "5", "6"])
def test_merge_permitted_matches_relationship_type(client, scenarios, monkeypatch, sid):
    _enable(monkeypatch)
    body = client.post(RECON_PATH, json=scenarios[sid]).json()
    for rel in body["relationships"]:
        rt = RelationshipType(rel["relationship_type"])
        assert rel["merge_permitted"] == rt.merge_permitted
        if rel["merge_permitted"]:
            assert rt in (RelationshipType.DUPLICATE, RelationshipType.SAME_CASE)


@pytest.mark.parametrize("sid", ["1", "3", "5", "6"])
def test_relationship_type_serialized_as_plain_value(client, scenarios, monkeypatch, sid):
    _enable(monkeypatch)
    body = client.post(RECON_PATH, json=scenarios[sid]).json()
    valid = {t.value for t in RelationshipType}
    for rel in body["relationships"]:
        # Over HTTP the type is a bare value ("RELATED"), never "RelationshipType.RELATED";
        # StatusBadge in the frontend keys off exactly these values.
        assert rel["relationship_type"] in valid
        assert not rel["relationship_type"].startswith("RelationshipType")


# ── §11 — reconciliation asserts NO physics authority ────────────────────────
def test_related_has_no_physics_authority(client, scenarios, monkeypatch):
    _enable(monkeypatch)
    body = client.post(RECON_PATH, json=scenarios["5"]).json()
    assert body["physics_validation"] == "pending"
    for rel in body["relationships"]:
        if rel["relationship_type"] == "RELATED":
            # A deterministic RELATED link claims no physical proof of propagation.
            assert rel.get("physics_support") == []
