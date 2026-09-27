"""
Tests for Phase 24 Reconciliation 6-Scenario Demo (tests/test_phase24_reconciliation_demo.py).

Verifies all 6 demo scenarios:
  1. DUPLICATE observations -> single merged Case, 0 relationships
  2. SAME_CASE multi-channel -> single merged Case, 0 relationships
  3. RELATED propagation -> 2 separate Cases, 1 RELATED relationship
  4. SEPARATE independent -> 2 separate Cases, 1 SEPARATE relationship
  5. CONFLICT observations -> 2 separate Cases, 1 CONFLICT relationship, human review required
  6. UNCERTAIN ambiguous -> 2 separate Cases, 1 UNCERTAIN relationship, human review required
"""

import pytest

from app.reconciliation.contract import RelationshipType
from demo.reconciliation_demo import (
    run_all_scenarios,
    scenario_conflicting_observations,
    scenario_duplicate_observations,
    scenario_related_propagation_cases,
    scenario_same_case_corroboration,
    scenario_separate_independent_cases,
    scenario_uncertain_ambiguous_evidence,
)


class TestReconciliationDemoScenarios:
    def test_scenario_1_duplicate_merging(self):
        res = scenario_duplicate_observations()
        assert res.case_count == 1
        assert len(res.merges_performed) == 1
        assert len(res.relationships) == 0
        assert res.human_review_required is False

    def test_scenario_2_same_case_multi_channel(self):
        res = scenario_same_case_corroboration()
        assert res.case_count == 1
        assert len(res.merges_performed) == 1
        assert len(res.relationships) == 0
        assert res.human_review_required is False

    def test_scenario_3_related_propagation(self):
        res = scenario_related_propagation_cases()
        assert res.case_count == 2
        assert len(res.relationships) == 1
        rel = res.relationships[0]
        assert rel.relationship_type == RelationshipType.RELATED
        assert rel.propagation_source_case_id == res.case_for_event("EVT-AOCS-RW")
        assert res.human_review_required is False

    def test_scenario_4_separate_independent(self):
        res = scenario_separate_independent_cases()
        assert res.case_count == 2
        assert len(res.relationships) == 1
        rel = res.relationships[0]
        assert rel.relationship_type == RelationshipType.SEPARATE
        assert res.human_review_required is False

    def test_scenario_5_conflicting_observations(self):
        res = scenario_conflicting_observations()
        assert res.case_count == 2
        assert len(res.relationships) == 1
        rel = res.relationships[0]
        assert rel.relationship_type == RelationshipType.CONFLICT
        assert res.human_review_required is True

    def test_scenario_6_uncertain_ambiguous(self):
        res = scenario_uncertain_ambiguous_evidence()
        assert res.case_count == 2
        assert len(res.relationships) == 1
        rel = res.relationships[0]
        assert rel.relationship_type == RelationshipType.UNCERTAIN
        assert res.human_review_required is True

    def test_run_all_scenarios_summary(self):
        summaries = run_all_scenarios()
        assert len(summaries) == 6
        for s in summaries:
            assert "title" in s
            assert "case_count" in s
            assert "relationship_summary" in s
            assert isinstance(s["human_review_required"], bool)
