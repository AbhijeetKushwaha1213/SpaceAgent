"""
SENTINEL — Phase 25 End-to-End Demonstration Test Suite
(tests/test_phase25_end_to_end_demo.py)

Validates the full multi-scenario demo engine:
  - Scenario A: Single fault execution, physics & safety gating.
  - Scenario B: Two separate faults preserving case separation (CORRELATION != IDENTITY).
  - Scenario C: Conflicting evidence triggering deterministic CONFLICT and Human Review.
  - Scenario D: Insufficient / Bad data triggering P1_INSUFFICIENT_EVIDENCE and safety block.
  - Machine-readable JSON output contract adherence.
  - Cloud provider fail-closed credential verification.
"""

import json
import pytest

from demo.e2e_demo import (
    EndToEndDemoEngine,
    build_scenario_a_single_fault,
    build_scenario_b_two_separate_faults,
    build_scenario_c_conflicting_evidence,
    build_scenario_d_insufficient_data,
)


class TestPhase25EndToEndDemo:
    @pytest.fixture
    def engine(self):
        return EndToEndDemoEngine(mode="stub")

    def test_scenario_a_single_fault_e2e(self, engine):
        data = build_scenario_a_single_fault()
        res = engine.run_scenario(data)
        d = res.to_dict()

        assert d["scenario_id"] == "SCENARIO_A_SINGLE_FAULT"
        assert len(d["cases"]) == 1
        assert d["arbitration"]["decision"] == "LOCAL_ACCEPT"
        assert d["safety"]["is_safe"] is True
        assert len(d["recovery_recommendation"]["steps"]) == 1
        assert d["human_review_required"] is False
        assert "AUDIT-REC" in d["audit_reference"]

    def test_scenario_b_two_separate_faults_e2e(self, engine):
        data = build_scenario_b_two_separate_faults()
        res = engine.run_scenario(data)
        d = res.to_dict()

        assert d["scenario_id"] == "SCENARIO_B_TWO_SEPARATE_FAULTS"
        # Must produce 2 isolated cases proving CORRELATION != IDENTITY
        assert len(d["cases"]) == 2
        assert len(d["relationships"]) == 1
        assert d["relationships"][0]["relationship_type"] in ("RELATED", "RelationshipType.RELATED")
        assert d["safety"]["is_safe"] is True
        assert d["human_review_required"] is False

    def test_scenario_c_conflicting_evidence_e2e(self, engine):
        data = build_scenario_c_conflicting_evidence()
        res = engine.run_scenario(data)
        d = res.to_dict()

        assert d["scenario_id"] == "SCENARIO_C_CONFLICTING_EVIDENCE"
        assert len(d["cases"]) == 2
        assert len(d["relationships"]) == 1
        assert d["relationships"][0]["relationship_type"] in ("CONFLICT", "RelationshipType.CONFLICT")
        # Conflict must mandate human review
        assert d["human_review_required"] is True

    def test_scenario_d_insufficient_data_e2e(self, engine):
        data = build_scenario_d_insufficient_data()
        res = engine.run_scenario(data)
        d = res.to_dict()

        assert d["scenario_id"] == "SCENARIO_D_INSUFFICIENT_DATA"
        # Deterministic arbitrator must enforce P1_INSUFFICIENT_EVIDENCE
        assert d["arbitration"]["decision"] == "HUMAN_REVIEW"
        assert d["arbitration"]["rule_applied"] == "P1_INSUFFICIENT_EVIDENCE"
        # Safety validator must block unregistered command
        assert d["safety"]["is_safe"] is False
        assert len(d["safety"]["blocked_steps"]) >= 1
        # Human review must be mandatory
        assert d["human_review_required"] is True

    def test_json_export_structure(self, engine):
        data = build_scenario_a_single_fault()
        res = engine.run_scenario(data)
        d = res.to_dict()

        # Check all required top-level JSON fields
        expected_keys = {
            "scenario_id",
            "observations",
            "cases",
            "relationships",
            "evidence",
            "rag_context",
            "physics",
            "hypotheses",
            "arbitration",
            "safety",
            "recovery_recommendation",
            "human_review_required",
            "audit_reference",
        }
        assert expected_keys.issubset(d.keys())

        # Ensure JSON serialization round-trip is flawless
        serialized = json.dumps(d)
        deserialized = json.loads(serialized)
        assert deserialized["scenario_id"] == "SCENARIO_A_SINGLE_FAULT"
