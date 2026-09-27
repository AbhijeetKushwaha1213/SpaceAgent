"""
SENTINEL Phase 9 — Engineering Procedure RAG Tests

Run:  cd sentinel/backend && python -m pytest tests/test_phase9_procedures.py -v

Tests cover all 13 Phase 9 requirements:
  1.  Procedure model — all required fields present and typed correctly
  2.  Command consistency — every command_id in procedures exists in COMMAND_REGISTRY
  3.  Citation stability — citation IDs are deterministic and unique
  4.  Citation preservation — citations survive retrieval round-trip
  5.  Correct source labeling — FALLBACK_KB procedures never labeled ECSS
  6.  Irrelevant retrieval — unrelated queries return INSUFFICIENT_EVIDENCE
  7.  Fallback retrieval — KB-sourced procedures labeled FALLBACK_KB
  8.  Subsystem filtering — filter narrows results correctly
  9.  Fault filtering — exact fault_class match
  10. Relevance threshold — low-relevance results excluded
  11. Retrieval evaluation — precision/recall/relevance computed correctly
  12. Command registry consistency — no procedure step references unregistered command
  13. Provenance chain validation — citation chain is valid for every procedure
"""

import os
import sys

import pytest

# Ensure backend/ root is on sys.path for standalone execution
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════════════════════════════════════════════════════════════════
# IMPORTS — verify the package is importable
# ═══════════════════════════════════════════════════════════════════════════

from app.api.models import RiskLevel, SubsystemID
from app.procedures.models import (
    Citation,
    ProcedureDefinition,
    ProcedureStep,
    RetrievalEvaluation,
    RetrievalResponse,
    RetrievalResult,
    SourceType,
)
from app.procedures.library import (
    CITATION_BY_PROCEDURE,
    CITATION_REGISTRY,
    PROCEDURE_BY_FAULT,
    PROCEDURE_LIBRARY,
)
from app.procedures.retrieval import retrieve_procedures
from app.procedures.citations import (
    format_citation,
    get_citation,
    get_citations_for_source,
    validate_citation_chain,
)
from app.procedures.evaluation import evaluate_retrieval
from app.validation.command_registry import COMMAND_REGISTRY


# ═══════════════════════════════════════════════════════════════════════════
# EXPECTED CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

EXPECTED_FAULT_CLASSES = [
    "ADCS_GYRO_SEU",
    "EPS_SOLAR_UNDERVOLT",
    "OBC_WATCHDOG_OVERFLOW",
    "TCS_THERMAL_RUNAWAY",
    "COMMS_TRANSPONDER_LOSS",
    "MULTI_CASCADE",
]

EXPECTED_PROCEDURE_IDS = [
    "PROC-ADCS-SEU-001",
    "PROC-EPS-UNDERVOLT-001",
    "PROC-OBC-WATCHDOG-001",
    "PROC-TCS-THERMAL-001",
    "PROC-COMMS-TRANSPONDER-001",
    "PROC-MULTI-CASCADE-001",
]


# ═══════════════════════════════════════════════════════════════════════════
# TEST 1: Procedure model — all required fields present
# ═══════════════════════════════════════════════════════════════════════════

class TestProcedureModel:
    """Test that every procedure has all 13 required fields (Phase 9 spec)."""

    def test_library_has_6_procedures(self):
        assert len(PROCEDURE_LIBRARY) == 6

    def test_all_fault_classes_present(self):
        for fc in EXPECTED_FAULT_CLASSES:
            assert fc in PROCEDURE_BY_FAULT, f"Missing fault class: {fc}"

    def test_all_procedure_ids_present(self):
        for pid in EXPECTED_PROCEDURE_IDS:
            assert pid in PROCEDURE_LIBRARY, f"Missing procedure_id: {pid}"

    @pytest.mark.parametrize("proc_id", EXPECTED_PROCEDURE_IDS)
    def test_required_fields_present(self, proc_id: str):
        proc = PROCEDURE_LIBRARY[proc_id]
        # All 13 required fields from Phase 9 spec
        assert proc.procedure_id, "procedure_id is empty"
        assert proc.title, "title is empty"
        assert isinstance(proc.subsystem, SubsystemID)
        assert proc.fault_class, "fault_class is empty"
        assert len(proc.steps) >= 1, "steps is empty"
        assert len(proc.preconditions) >= 1, "preconditions is empty"
        assert len(proc.postconditions) >= 1, "postconditions is empty"
        assert isinstance(proc.risk, RiskLevel)
        assert proc.source, "source is empty"
        assert proc.source_version, "source_version is empty"
        assert proc.section is not None, "section is None"
        assert proc.clause is not None, "clause is None"
        assert proc.provenance, "provenance is empty"

    @pytest.mark.parametrize("proc_id", EXPECTED_PROCEDURE_IDS)
    def test_steps_are_typed(self, proc_id: str):
        proc = PROCEDURE_LIBRARY[proc_id]
        for step in proc.steps:
            assert isinstance(step, ProcedureStep)
            assert step.step_number >= 1
            assert step.command_id.startswith("CMD_")
            assert step.description
            assert step.wait_seconds >= 0
            assert step.verification
            assert isinstance(step.risk, RiskLevel)

    @pytest.mark.parametrize("proc_id", EXPECTED_PROCEDURE_IDS)
    def test_command_ids_property(self, proc_id: str):
        proc = PROCEDURE_LIBRARY[proc_id]
        cmd_ids = proc.command_ids
        assert isinstance(cmd_ids, tuple)
        assert len(cmd_ids) == len(proc.steps)
        for cid in cmd_ids:
            assert cid.startswith("CMD_")

    @pytest.mark.parametrize("proc_id", EXPECTED_PROCEDURE_IDS)
    def test_frozen_dataclass(self, proc_id: str):
        """Procedure definitions must be immutable."""
        proc = PROCEDURE_LIBRARY[proc_id]
        with pytest.raises(AttributeError):
            proc.title = "MUTATED"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2: Command consistency — every command_id in COMMAND_REGISTRY
# ═══════════════════════════════════════════════════════════════════════════

class TestCommandConsistency:
    """Phase 9 requirement 4: commands must reference the command registry."""

    @pytest.mark.parametrize("proc_id", EXPECTED_PROCEDURE_IDS)
    def test_all_step_commands_in_registry(self, proc_id: str):
        proc = PROCEDURE_LIBRARY[proc_id]
        for step in proc.steps:
            assert step.command_id in COMMAND_REGISTRY, (
                f"{proc_id} step {step.step_number}: "
                f"'{step.command_id}' not in COMMAND_REGISTRY"
            )

    def test_no_invented_commands(self):
        """No procedure step may invent a command the registry doesn't define."""
        all_cmd_ids = set()
        for proc in PROCEDURE_LIBRARY.values():
            for step in proc.steps:
                all_cmd_ids.add(step.command_id)

        unregistered = all_cmd_ids - set(COMMAND_REGISTRY.keys())
        assert not unregistered, (
            f"Unregistered commands in procedure library: {unregistered}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3: Citation stability — IDs are deterministic and unique
# ═══════════════════════════════════════════════════════════════════════════

class TestCitationStability:
    """Phase 9 requirement 9: stable citation IDs."""

    def test_every_procedure_has_citation(self):
        for proc_id in EXPECTED_PROCEDURE_IDS:
            assert proc_id in CITATION_BY_PROCEDURE, (
                f"No citation for {proc_id}"
            )

    def test_citation_ids_are_deterministic(self):
        """Citation IDs follow the CIT-{procedure_id} pattern."""
        for proc_id, citation in CITATION_BY_PROCEDURE.items():
            expected_cit_id = f"CIT-{proc_id}"
            assert citation.citation_id == expected_cit_id, (
                f"Expected {expected_cit_id}, got {citation.citation_id}"
            )

    def test_citation_ids_unique(self):
        cit_ids = [c.citation_id for c in CITATION_REGISTRY.values()]
        assert len(cit_ids) == len(set(cit_ids)), "Duplicate citation IDs"

    def test_citation_fields_non_empty(self):
        for citation in CITATION_REGISTRY.values():
            assert citation.citation_id
            assert citation.procedure_id
            assert citation.source
            assert citation.source_version
            assert citation.section is not None
            assert citation.clause is not None
            assert citation.provenance

    def test_get_citation_works(self):
        cit = get_citation("PROC-ADCS-SEU-001")
        assert cit is not None
        assert cit.citation_id == "CIT-PROC-ADCS-SEU-001"

    def test_get_citation_missing(self):
        assert get_citation("PROC-DOES-NOT-EXIST") is None

    def test_format_citation(self):
        cit = get_citation("PROC-ADCS-SEU-001")
        assert cit is not None
        formatted = format_citation(cit)
        assert "CIT-PROC-ADCS-SEU-001" in formatted
        assert "Provenance:" in formatted

    def test_get_citations_for_source(self):
        fb_cits = get_citations_for_source("FALLBACK_KB")
        assert len(fb_cits) == 6  # All 6 procedures are FALLBACK_KB

    def test_get_citations_for_nonexistent_source(self):
        cits = get_citations_for_source("DOES_NOT_EXIST")
        assert len(cits) == 0


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4: Citation preservation — citations survive retrieval round-trip
# ═══════════════════════════════════════════════════════════════════════════

class TestCitationPreservation:
    """Phase 9 requirement 10: citations preserved through retrieval."""

    def test_retrieval_includes_citations(self):
        response = retrieve_procedures(
            fault_cues=["GYRO_A_RATE", "SEU_COUNTER"],
        )
        for result in response.results:
            assert result.citation is not None
            assert result.citation.citation_id.startswith("CIT-")
            assert result.citation.procedure_id == result.procedure.procedure_id

    def test_citation_matches_procedure(self):
        response = retrieve_procedures(
            fault_filter="ADCS_GYRO_SEU",
        )
        assert len(response.results) >= 1
        result = response.results[0]
        assert result.citation.procedure_id == result.procedure.procedure_id
        assert result.citation.source == result.procedure.source

    @pytest.mark.parametrize("proc_id", EXPECTED_PROCEDURE_IDS)
    def test_provenance_chain_valid(self, proc_id: str):
        """Phase 9 requirement: full provenance chain validation."""
        errors = validate_citation_chain(proc_id)
        assert errors == [], (
            f"Provenance chain errors for {proc_id}: {errors}"
        )

    def test_provenance_chain_missing_procedure(self):
        errors = validate_citation_chain("PROC-DOES-NOT-EXIST")
        assert len(errors) >= 1
        assert "not found" in errors[0].lower()


# ═══════════════════════════════════════════════════════════════════════════
# TEST 5: Correct source labeling (Phase 9 rule 6)
# ═══════════════════════════════════════════════════════════════════════════

class TestCorrectSourceLabeling:
    """Phase 9 rule 6: never label something ECSS unless it actually came
    from an ECSS source. All current procedures are FALLBACK_KB."""

    @pytest.mark.parametrize("proc_id", EXPECTED_PROCEDURE_IDS)
    def test_fallback_kb_labeled_correctly(self, proc_id: str):
        proc = PROCEDURE_LIBRARY[proc_id]
        assert proc.source_type == SourceType.FALLBACK_KB, (
            f"{proc_id} has source_type={proc.source_type} but "
            f"source='{proc.source}' — should be FALLBACK_KB"
        )

    def test_retrieval_never_returns_ecss_for_fallback(self):
        """Retrieval of FALLBACK_KB procedures must not claim ECSS."""
        response = retrieve_procedures(
            fault_cues=["GYRO_A_RATE", "SEU_COUNTER"],
        )
        for result in response.results:
            if result.procedure.source == "FALLBACK_KB":
                assert result.source_type == SourceType.FALLBACK_KB

    def test_overall_source_type_is_fallback(self):
        """When all results are FALLBACK_KB, overall must be FALLBACK_KB."""
        response = retrieve_procedures(
            fault_cues=["GYRO_A_RATE", "SEU_COUNTER"],
        )
        if response.results:
            assert response.source_type == SourceType.FALLBACK_KB


# ═══════════════════════════════════════════════════════════════════════════
# TEST 6: Irrelevant retrieval — INSUFFICIENT_EVIDENCE (Phase 9 rule 8)
# ═══════════════════════════════════════════════════════════════════════════

class TestIrrelevantRetrieval:
    """Phase 9 rule 8: if retrieval is insufficient, return
    INSUFFICIENT_EVIDENCE. Do not force irrelevant documents."""

    def test_completely_unrelated_query(self):
        """A query about cooking should return INSUFFICIENT_EVIDENCE."""
        response = retrieve_procedures(
            query="How to make chocolate cake with vanilla frosting",
            min_relevance=0.3,
        )
        assert response.source_type == SourceType.INSUFFICIENT_EVIDENCE
        assert len(response.results) == 0

    def test_high_threshold_filters_weak_matches(self):
        """A very high threshold should exclude weak matches."""
        response = retrieve_procedures(
            query="some vague problem",
            min_relevance=0.99,
        )
        assert response.source_type == SourceType.INSUFFICIENT_EVIDENCE
        assert len(response.results) == 0

    def test_query_metadata_explains_insufficient(self):
        """The query_metadata should explain why INSUFFICIENT_EVIDENCE."""
        response = retrieve_procedures(
            query="alien invasion protocol",
            min_relevance=0.3,
        )
        assert response.source_type == SourceType.INSUFFICIENT_EVIDENCE
        assert "reason" in response.query_metadata


# ═══════════════════════════════════════════════════════════════════════════
# TEST 7: Fallback retrieval — explicit FALLBACK_KB labeling (rule 7)
# ═══════════════════════════════════════════════════════════════════════════

class TestFallbackRetrieval:
    """Phase 9 rule 7: if fallback KB is used, return source_type=FALLBACK_KB."""

    def test_source_type_is_fallback_kb(self):
        response = retrieve_procedures(
            query="ADCS gyroscope SEU",
            fault_cues=[
                "GYRO_A_RATE", "SEU_COUNTER", "ADCS",
                "attitude_error", "gyro", "radiation",
            ],
        )
        assert response.source_type == SourceType.FALLBACK_KB

    def test_each_result_has_fallback_source_type(self):
        response = retrieve_procedures(
            query="ADCS gyroscope SEU",
            fault_cues=[
                "GYRO_A_RATE", "SEU_COUNTER", "ADCS",
                "attitude_error", "gyro", "radiation",
            ],
        )
        for result in response.results:
            assert result.source_type == SourceType.FALLBACK_KB


# ═══════════════════════════════════════════════════════════════════════════
# TEST 8: Subsystem filtering
# ═══════════════════════════════════════════════════════════════════════════

class TestSubsystemFiltering:
    """Phase 9 requirement 5: subsystem filtering."""

    def test_filter_adcs_only(self):
        response = retrieve_procedures(
            query="gyroscope fault",
            subsystem_filter=SubsystemID.ADCS,
        )
        for result in response.results:
            assert result.procedure.subsystem == SubsystemID.ADCS

    def test_filter_eps_only(self):
        response = retrieve_procedures(
            query="solar power fault",
            subsystem_filter=SubsystemID.EPS,
        )
        for result in response.results:
            assert result.procedure.subsystem == SubsystemID.EPS

    def test_filter_nonexistent_subsystem(self):
        """PYLD has no procedures — should return INSUFFICIENT_EVIDENCE."""
        response = retrieve_procedures(
            query="payload issue",
            subsystem_filter=SubsystemID.PYLD,
        )
        assert response.source_type == SourceType.INSUFFICIENT_EVIDENCE
        assert len(response.results) == 0


# ═══════════════════════════════════════════════════════════════════════════
# TEST 9: Fault filtering — exact fault class match
# ═══════════════════════════════════════════════════════════════════════════

class TestFaultFiltering:
    """Phase 9 requirement 5: fault filtering."""

    @pytest.mark.parametrize("fault_class", EXPECTED_FAULT_CLASSES)
    def test_exact_fault_filter(self, fault_class: str):
        response = retrieve_procedures(
            fault_filter=fault_class,
        )
        assert len(response.results) == 1
        assert response.results[0].procedure.fault_class == fault_class

    def test_nonexistent_fault_filter(self):
        response = retrieve_procedures(
            fault_filter="DOES_NOT_EXIST",
        )
        assert response.source_type == SourceType.INSUFFICIENT_EVIDENCE


# ═══════════════════════════════════════════════════════════════════════════
# TEST 10: Relevance threshold
# ═══════════════════════════════════════════════════════════════════════════

class TestRelevanceThreshold:
    """Phase 9 requirement 5: minimum relevance threshold."""

    def test_zero_threshold_returns_all(self):
        response = retrieve_procedures(
            query="spacecraft fault",
            min_relevance=0.0,
        )
        # With zero threshold, all candidates should be returned (up to top_k)
        assert len(response.results) > 0

    def test_high_threshold_filters_weak_results(self):
        response = retrieve_procedures(
            query="some problem",
            min_relevance=0.95,
        )
        for result in response.results:
            assert result.relevance_score >= 0.95

    def test_scores_are_bounded(self):
        response = retrieve_procedures(
            fault_cues=["GYRO_A_RATE", "SEU_COUNTER"],
            min_relevance=0.0,
        )
        for result in response.results:
            assert 0.0 <= result.relevance_score <= 1.0

    def test_results_ordered_by_relevance(self):
        response = retrieve_procedures(
            fault_cues=["GYRO_A_RATE", "SEU_COUNTER"],
            min_relevance=0.0,
        )
        scores = [r.relevance_score for r in response.results]
        assert scores == sorted(scores, reverse=True)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 11: Retrieval evaluation — precision/recall/relevance
# ═══════════════════════════════════════════════════════════════════════════

class TestRetrievalEvaluation:
    """Phase 9 requirement 12: precision, recall, relevance metrics."""

    def test_perfect_retrieval(self):
        """Exact fault filter should give precision=1.0, recall=1.0."""
        response = retrieve_procedures(
            fault_filter="ADCS_GYRO_SEU",
        )
        evaluation = evaluate_retrieval(
            query="ADCS_GYRO_SEU",
            expected_fault_class="ADCS_GYRO_SEU",
            results=response.results,
        )
        assert evaluation.precision == 1.0
        assert evaluation.recall == 1.0
        assert evaluation.relevance > 0.0

    def test_irrelevant_retrieval_evaluation(self):
        """Wrong fault class should give precision=0.0."""
        response = retrieve_procedures(
            fault_filter="ADCS_GYRO_SEU",
        )
        evaluation = evaluate_retrieval(
            query="thermal runaway",
            expected_fault_class="TCS_THERMAL_RUNAWAY",
            results=response.results,
        )
        assert evaluation.precision == 0.0

    def test_empty_results_evaluation(self):
        evaluation = evaluate_retrieval(
            query="nothing",
            expected_fault_class="ADCS_GYRO_SEU",
            results=[],
        )
        assert evaluation.precision == 0.0
        assert evaluation.recall == 0.0
        assert evaluation.relevance == 0.0

    def test_evaluation_fields_present(self):
        response = retrieve_procedures(
            fault_cues=["GYRO_A_RATE"],
            min_relevance=0.0,
        )
        evaluation = evaluate_retrieval(
            query="gyro",
            expected_fault_class="ADCS_GYRO_SEU",
            results=response.results,
        )
        assert isinstance(evaluation, RetrievalEvaluation)
        assert 0.0 <= evaluation.precision <= 1.0
        assert 0.0 <= evaluation.recall <= 1.0
        assert 0.0 <= evaluation.relevance <= 1.0
        assert evaluation.expected_fault_class == "ADCS_GYRO_SEU"
        assert isinstance(evaluation.returned_fault_classes, tuple)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 12: Retrieval result quality — real fault scenarios
# ═══════════════════════════════════════════════════════════════════════════

class TestRetrievalQuality:
    """Verify retrieval returns the correct procedure for real fault scenarios."""

    def test_adcs_seu_retrieval(self):
        response = retrieve_procedures(
            query="ADCS gyroscope SEU cosmic ray",
            fault_cues=["GYRO_A_RATE", "SEU_COUNTER", "ATTITUDE_ERROR"],
        )
        assert len(response.results) >= 1
        top = response.results[0]
        assert top.procedure.fault_class == "ADCS_GYRO_SEU"

    def test_eps_undervolt_retrieval(self):
        response = retrieve_procedures(
            query="EPS solar array power loss",
            fault_cues=["I_sa", "V_bat", "SoC", "solar"],
        )
        assert len(response.results) >= 1
        top = response.results[0]
        assert top.procedure.fault_class == "EPS_SOLAR_UNDERVOLT"

    def test_obc_watchdog_retrieval(self):
        response = retrieve_procedures(
            query="OBC watchdog overflow CPU stuck",
            fault_cues=["CPU_LOAD", "WATCHDOG_COUNTER", "OBC"],
        )
        assert len(response.results) >= 1
        top = response.results[0]
        assert top.procedure.fault_class == "OBC_WATCHDOG_OVERFLOW"

    def test_tcs_thermal_retrieval(self):
        response = retrieve_procedures(
            query="TCS thermal runaway heater stuck",
            fault_cues=["TEMP_OBC", "HEATER_ZONE", "thermal"],
        )
        assert len(response.results) >= 1
        top = response.results[0]
        assert top.procedure.fault_class == "TCS_THERMAL_RUNAWAY"

    def test_comms_transponder_retrieval(self):
        response = retrieve_procedures(
            query="COMMS transponder loss of signal",
            fault_cues=["TRANSPONDER_LOCK", "SNR", "COMMS"],
        )
        assert len(response.results) >= 1
        top = response.results[0]
        assert top.procedure.fault_class == "COMMS_TRANSPONDER_LOSS"

    def test_multi_cascade_retrieval(self):
        response = retrieve_procedures(
            query="multi subsystem cascade failure",
            fault_cues=["cascade", "multiple", "downstream"],
        )
        assert len(response.results) >= 1
        top = response.results[0]
        assert top.procedure.fault_class == "MULTI_CASCADE"


# ═══════════════════════════════════════════════════════════════════════════
# TEST 13: Source type enum values
# ═══════════════════════════════════════════════════════════════════════════

class TestSourceType:
    """Verify SourceType enum has the required values."""

    def test_ecss_value(self):
        assert SourceType.ECSS.value == "ECSS"

    def test_fallback_kb_value(self):
        assert SourceType.FALLBACK_KB.value == "FALLBACK_KB"

    def test_insufficient_evidence_value(self):
        assert SourceType.INSUFFICIENT_EVIDENCE.value == "INSUFFICIENT_EVIDENCE"


# ═══════════════════════════════════════════════════════════════════════════
# TEST 14: Top-k limiting
# ═══════════════════════════════════════════════════════════════════════════

class TestTopK:
    """Verify top_k parameter limits results."""

    def test_top_k_1(self):
        response = retrieve_procedures(
            fault_cues=["GYRO_A_RATE"],
            min_relevance=0.0,
            top_k=1,
        )
        assert len(response.results) <= 1

    def test_top_k_default(self):
        response = retrieve_procedures(
            fault_cues=["GYRO_A_RATE"],
            min_relevance=0.0,
        )
        assert len(response.results) <= 3  # default top_k=3

    def test_top_k_larger_than_library(self):
        response = retrieve_procedures(
            fault_cues=["GYRO_A_RATE"],
            min_relevance=0.0,
            top_k=100,
        )
        assert len(response.results) <= 6  # library has 6 procedures


# ═══════════════════════════════════════════════════════════════════════════
# TEST 15: Query metadata
# ═══════════════════════════════════════════════════════════════════════════

class TestQueryMetadata:
    """Verify retrieval response includes diagnostic query metadata."""

    def test_metadata_present(self):
        response = retrieve_procedures(
            query="test query",
            fault_cues=["TEST_CUE"],
        )
        meta = response.query_metadata
        assert "query" in meta
        assert "fault_cues" in meta
        assert "min_relevance" in meta
        assert "top_k" in meta

    def test_metadata_reflects_filters(self):
        response = retrieve_procedures(
            subsystem_filter=SubsystemID.ADCS,
            fault_filter="ADCS_GYRO_SEU",
        )
        meta = response.query_metadata
        assert meta["subsystem_filter"] == "ADCS"
        assert meta["fault_filter"] == "ADCS_GYRO_SEU"
