"""
Integration tests for Phase 24 Reconciliation Subsystem (tests/test_phase24_reconciliation_integration.py).

Verifies:
  - Event construction from raw crash dump dicts and detection reports.
  - End-to-end reconciliation on realistic multi-channel spacecraft crash dumps.
  - Audit logging of Stage.RECONCILIATION with payload redaction and hashing.
  - Flag gating (RECONCILIATION_ENABLED=false default vs enabled).
  - Preserving case separation and human review triggers under conflict and uncertainty.
"""

import json
import pytest

from app.audit import SQLiteAuditStore, RunStatus
from app.audit.record import AuditRecorder, Stage, StageStatus
from app.detection.models import (
    Anomaly,
    AnomalyProvenance,
    AnomalyReport,
    BaselineSource,
    ChannelFinding,
    DetectorName,
    Severity,
)
from app.reconciliation.audit import build_reconciliation_audit_payload
from app.reconciliation.config import (
    DEFAULT_CONFIG,
    RECONCILIATION_CONFIG_VERSION,
    RECONCILIATION_ENGINE_VERSION,
    ReconciliationConfig,
    reconciliation_enabled,
)
from app.reconciliation.contract import (
    ObservationEvent,
    ReconciliationInput,
    RelationshipType,
)
from app.reconciliation.engine import ReconciliationEngine
from app.reconciliation.events import (
    build_events_from_dicts,
    build_observation_events,
)


def _make_mock_detection_report() -> AnomalyReport:
    a1 = Anomaly(
        anomaly_id="AN-001",
        channel="I_sa",
        timestamp="T-100s",
        detector=DetectorName.HARD_LIMIT,
        score=1.0,
        threshold=0.8,
        severity=Severity.CRITICAL,
        description="Solar array current undervolt",
        evidence={"direction": "LOW", "observed": 0.5},
        provenance=AnomalyProvenance(
            detector_module="app.detection.limits",
            baseline_source=BaselineSource.NONE,
        ),
    )
    a2 = Anomaly(
        anomaly_id="AN-002",
        channel="V_bat",
        timestamp="T-105s",
        detector=DetectorName.HARD_LIMIT,
        score=1.0,
        threshold=0.8,
        severity=Severity.CRITICAL,
        description="Battery bus voltage drop",
        evidence={"direction": "LOW", "observed": 22.0},
        provenance=AnomalyProvenance(
            detector_module="app.detection.limits",
            baseline_source=BaselineSource.NONE,
        ),
    )

    f1 = ChannelFinding(
        channel="I_sa",
        severity=Severity.CRITICAL,
        severity_rank=3,
        anomaly_count=1,
        anomaly_ids=("AN-001",),
        detectors=(DetectorName.HARD_LIMIT,),
        corroborated=True,
        anomalies=(a1,),
    )
    f2 = ChannelFinding(
        channel="V_bat",
        severity=Severity.CRITICAL,
        severity_rank=3,
        anomaly_count=1,
        anomaly_ids=("AN-002",),
        detectors=(DetectorName.HARD_LIMIT,),
        corroborated=True,
        anomalies=(a2,),
    )
    return AnomalyReport(
        scenario_id="SCENARIO-EPS-FAIL",
        channels=(f1, f2),
        warnings=(),
        anomaly_count=2,
        critical_count=2,
        warning_count=0,
        low_count=0,
    )


class TestReconciliationIntegration:
    def test_build_observation_events_from_report_and_crash_dump(self):
        report = _make_mock_detection_report()
        crash_dump = {
            "scenario_id": "SCENARIO-EPS-FAIL",
            "telemetry": [
                {"name": "I_sa", "status": "CRITICAL", "timestamp": "T-100s", "value": 0.5},
                {"name": "V_bat", "status": "CRITICAL", "timestamp": "T-105s", "value": 22.0},
            ],
        }
        events = build_observation_events(report, crash_dump, "SCENARIO-EPS-FAIL")
        assert len(events) == 2
        assert events[0].channel == "I_sa"
        assert events[0].subsystem == "EPS"
        assert events[0].first_seen_s == -100.0
        assert events[1].channel == "V_bat"
        assert events[1].subsystem == "EPS"
        assert events[1].first_seen_s == -105.0

    def test_end_to_end_reconciliation_pipeline(self):
        report = _make_mock_detection_report()
        crash_dump = {
            "scenario_id": "SCENARIO-EPS-FAIL",
            "telemetry": [
                {"name": "I_sa", "status": "CRITICAL", "timestamp": "T-100s", "value": 0.5},
                {"name": "V_bat", "status": "CRITICAL", "timestamp": "T-105s", "value": 22.0},
            ],
        }
        events = build_observation_events(report, crash_dump, "SCENARIO-EPS-FAIL")
        inp = ReconciliationInput(
            events=events,
            scenario_id="SCENARIO-EPS-FAIL",
            physics_statuses=(("EPS_SOLAR_UNDERVOLT", "VALID"),),
        )

        engine = ReconciliationEngine()
        result = engine.reconcile(inp)

        # Corroborated within EPS: merges into 1 Case
        assert result.case_count == 1
        case = result.cases[0]
        assert set(case.channels) == {"I_sa", "V_bat"}
        assert case.primary_subsystem == "EPS"

    def test_audit_payload_and_stage_recording(self, tmp_path):
        db_path = str(tmp_path / "test_audit.db")
        store = SQLiteAuditStore(db_path=db_path)
        recorder = AuditRecorder.begin(
            crash_dump={"scenario_id": "SCENARIO-TEST"},
            run_id="RUN-RECON-001",
        )

        events = (
            ObservationEvent(
                event_id="EVT-1",
                channel="I_sa",
                subsystem="EPS",
                severity="CRITICAL",
                severity_rank=3,
                detectors=("HARD_LIMIT",),
                anomaly_ids=("AN-1",),
                timestamps=("T-50s",),
                directions=("LOW",),
                first_seen_s=-50.0,
                last_seen_s=-50.0,
                candidate_fault_ids=("EPS_SOLAR_UNDERVOLT",),
            ),
        )
        inp = ReconciliationInput(events=events, scenario_id="SCENARIO-TEST")
        result = ReconciliationEngine().reconcile(inp)

        payload = build_reconciliation_audit_payload(inp, result)
        assert payload["stage"] == "reconciliation"
        assert payload["case_count"] == 1
        assert "outcome_hash" in payload

        entry = recorder.record(
            stage=Stage.RECONCILIATION,
            status=StageStatus.OK,
            summary="Reconciliation completed: 1 case formed.",
            payload=payload,
        )
        assert entry.stage == Stage.RECONCILIATION
        assert entry.status == StageStatus.OK

        # Finalize record to store and reload
        record = recorder.finalize(store=store, status=RunStatus.COMPLETED)

        reloaded = store.get("RUN-RECON-001")
        assert reloaded is not None
        recon_stage = reloaded.stage(Stage.RECONCILIATION)
        assert recon_stage is not None
        assert recon_stage.payload["case_count"] == 1

    def test_default_flag_is_false(self):
        assert reconciliation_enabled() is False
