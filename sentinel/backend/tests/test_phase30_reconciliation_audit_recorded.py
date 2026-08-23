"""SENTINEL — Phase 1J reconciliation-audit regression (test_phase30_...)

The reconciliation block in ``SentinelAgent.analyze_crash_dump_stream`` recorded
its audit stage with ``Stage.RECONCILIATION`` / ``StageStatus`` but — unlike
every other audit-recording block in agent.py — never did the local
``from app.audit import Stage, StageStatus``. So whenever reconciliation was
ENABLED, the reconcile succeeded but both the OK record and the DEGRADED
fallback record raised ``NameError`` and the reconciliation audit entry was
silently dropped. (While disabled — the Phase 1C default — the block never runs,
which is why this went unnoticed.)

This test enables reconciliation explicitly (independent of any local .env) and
asserts the reconciliation stage is actually persisted. Before the fix the entry
is absent; after it, it is present.
"""

from __future__ import annotations

import pytest

from app.agent.agent import AgentConfig, ModelMode, SentinelAgent
from app.api.adapters import with_canonical_window
from app.api.provenance import Provenance
from app.api.scenarios import get_all_scenarios
from app.audit import AuditRecorder, RunStatus, Stage, StageStatus
from tests.test_phase4_audit import _memory_store, _stub_response


def test_reconciliation_stage_is_audited_when_enabled(monkeypatch):
    # Enable reconciliation at the package level the agent imports from, so this
    # does not depend on RECONCILIATION_ENABLED / .env at all.
    monkeypatch.setattr("app.reconciliation.reconciliation_enabled", lambda: True)

    scenario = next(s for s in get_all_scenarios() if s.get("scenario_id") == 1)
    dump = with_canonical_window(scenario)
    agent = SentinelAgent(AgentConfig(
        mode=ModelMode.STUB, stub_response=_stub_response(), stub_label="phase30",
    ))
    store = _memory_store()
    try:
        recorder = AuditRecorder.begin(
            dump, origin="tests.test_phase30",
            provenance_override=Provenance.DEMO.value,
        )
        list(agent.analyze_crash_dump_stream(dump, recorder=recorder))
        record = recorder.finalize(store=store, status=RunStatus.COMPLETED)
        reloaded = store.get(record.run_id)

        entry = reloaded.stage(Stage.RECONCILIATION)
        assert entry is not None, (
            "reconciliation audit entry was silently dropped — the NameError "
            "regression prevents the stage from being recorded when enabled"
        )
        # With reconciliation enabled and a valid run, the happy path records OK.
        assert entry.status == StageStatus.OK
        assert entry.payload, "a recorded reconciliation stage must carry a payload"
    finally:
        store.close()
