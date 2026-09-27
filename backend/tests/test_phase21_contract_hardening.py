"""SENTINEL Phase 21 — Contract hardening regression tests.

Covers the Phase 21 failure analysis findings:

1. Guardrails must reject fabricated IDs even when the valid sets are EMPTY.
   (Phase 20 S200: ``guardrail_violations == []`` although the model cited
   ``anomaly_summary`` as evidence and selected ``procedure_1``.)
2. The deterministic ``evidence_status`` contract (ADEQUATE / PARTIAL /
   INSUFFICIENT / CONTRADICTORY) is computed from pipeline outputs and
   enforced by guardrails independently of prompt compliance.
3. The constrained prompt carries the machine-readable evidence state and the
   explicit insufficient-evidence instructions.
4. ``LLMRankingOutput.from_dict`` deduplicates ID lists and parses all seven
   contract fields exactly once (Part 1 output-model audit).
5. Evidence-ID and procedure-ID constraint matrix (Parts 6 and 7):
   valid / nonexistent / empty / duplicated / cross-scenario / malformed.

These tests never weaken a Phase 10-17 safety assertion; they only pin the
hardened behaviour.
"""

from __future__ import annotations

import json

from app.llm.models import (
    EvidenceStatus,
    HypothesisContext,
    LLMRankingInput,
    LLMRankingOutput,
    RankedHypothesis,
    ViolationType,
)
from app.llm.ranker import (
    build_constrained_prompt,
    compute_evidence_status,
    validate_ranking_output,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _hyp(
    fault_id: str = "ADCS_GYRO_SEU",
    supporting: tuple[str, ...] = ("EVID-AAAAAAAAAAAA",),
    contradicting: tuple[str, ...] = (),
    undetermined: tuple[str, ...] = (),
) -> HypothesisContext:
    return HypothesisContext(
        hypothesis_id=f"HYP-{fault_id}",
        fault_id=fault_id,
        fault_name=fault_id,
        subsystem="ADCS",
        deterministic_rank=1,
        deterministic_score=0.9,
        supporting_evidence=supporting,
        contradicting_evidence=contradicting,
        undetermined_evidence=undetermined,
    )


def _input(
    hypotheses: tuple[HypothesisContext, ...] = (),
    valid_fault_ids: tuple[str, ...] = (),
    valid_procedure_ids: tuple[str, ...] = (),
    evidence_status: str = EvidenceStatus.INSUFFICIENT.value,
) -> LLMRankingInput:
    return LLMRankingInput(
        hypotheses=hypotheses,
        valid_fault_ids=valid_fault_ids,
        valid_procedure_ids=valid_procedure_ids,
        evidence_status=evidence_status,
        scenario_id="T",
    )


def _output(
    fault_ids: tuple[str, ...] = (),
    supporting: tuple[str, ...] = (),
    contradicting: tuple[str, ...] = (),
    procedures: tuple[str, ...] = (),
    confidence: float = 0.8,
    requires_human_review: bool = True,
) -> LLMRankingOutput:
    return LLMRankingOutput(
        ranked_hypotheses=tuple(
            RankedHypothesis(fault_id=f, rank=i + 1, confidence=confidence)
            for i, f in enumerate(fault_ids)
        ),
        reasoning_summary="test",
        supporting_evidence_ids=supporting,
        contradicting_evidence_ids=contradicting,
        selected_procedure_ids=procedures,
        uncertainty="test",
        requires_human_review=requires_human_review,
    )


# ---------------------------------------------------------------------------
# Part 6 — evidence-ID constraint matrix
# ---------------------------------------------------------------------------

class TestEvidenceIdConstraints:
    def test_valid_evidence_id_accepted(self):
        ri = _input(
            hypotheses=(_hyp(),),
            valid_fault_ids=("ADCS_GYRO_SEU",),
            evidence_status=EvidenceStatus.ADEQUATE.value,
        )
        out = _output(
            fault_ids=("ADCS_GYRO_SEU",),
            supporting=("EVID-AAAAAAAAAAAA",),
        )
        result = validate_ranking_output(out, ri)
        assert result.is_valid
        assert ViolationType.NONEXISTENT_EVIDENCE not in result.violation_types

    def test_nonexistent_evidence_id_rejected(self):
        ri = _input(
            hypotheses=(_hyp(),),
            valid_fault_ids=("ADCS_GYRO_SEU",),
            evidence_status=EvidenceStatus.ADEQUATE.value,
        )
        out = _output(
            fault_ids=("ADCS_GYRO_SEU",),
            supporting=("EVID-FAKE-001",),
        )
        result = validate_ranking_output(out, ri)
        assert not result.is_valid
        assert ViolationType.NONEXISTENT_EVIDENCE in result.violation_types
        assert (
            "EVID-FAKE-001"
            not in result.corrected_output.supporting_evidence_ids
        )

    def test_empty_evidence_lists_accepted(self):
        ri = _input(
            hypotheses=(_hyp(),),
            valid_fault_ids=("ADCS_GYRO_SEU",),
            evidence_status=EvidenceStatus.ADEQUATE.value,
        )
        out = _output(fault_ids=("ADCS_GYRO_SEU",))
        result = validate_ranking_output(out, ri)
        assert result.is_valid

    def test_duplicated_evidence_deduplicated(self):
        parsed = {
            "ranked_hypotheses": [],
            "supporting_evidence_ids": [
                "EVID-AAAAAAAAAAAA", "EVID-AAAAAAAAAAAA",
            ],
            "contradicting_evidence_ids": [],
            "selected_procedure_ids": ["PROC-1", "PROC-1"],
            "uncertainty": "",
            "requires_human_review": True,
        }
        out = LLMRankingOutput.from_dict(parsed)
        assert out.supporting_evidence_ids == ("EVID-AAAAAAAAAAAA",)
        assert out.selected_procedure_ids == ("PROC-1",)

    def test_evidence_from_another_scenario_rejected(self):
        # Well-formed EVID pattern, but not part of THIS scenario's input.
        ri = _input(
            hypotheses=(_hyp(),),
            valid_fault_ids=("ADCS_GYRO_SEU",),
            evidence_status=EvidenceStatus.ADEQUATE.value,
        )
        out = _output(
            fault_ids=("ADCS_GYRO_SEU",),
            supporting=("EVID-6189b7b78f62",),  # belongs to scenario S1
        )
        result = validate_ranking_output(out, ri)
        assert ViolationType.NONEXISTENT_EVIDENCE in result.violation_types

    def test_malformed_evidence_id_rejected(self):
        ri = _input(
            hypotheses=(_hyp(),),
            valid_fault_ids=("ADCS_GYRO_SEU",),
            evidence_status=EvidenceStatus.ADEQUATE.value,
        )
        out = _output(
            fault_ids=("ADCS_GYRO_SEU",),
            contradicting=("not-an-evidence-id !!!",),
        )
        result = validate_ranking_output(out, ri)
        assert ViolationType.NONEXISTENT_EVIDENCE in result.violation_types

    def test_fabricated_evidence_rejected_when_valid_set_empty(self):
        """S200 regression: no evidence exists, so ANY citation is fabricated.

        Phase 20 shipped with the evidence check gated on a non-empty valid
        set, which let ``anomaly_summary`` / ``anomaly_count`` pass as
        "validated evidence" on scenario 200.
        """
        ri = _input()  # no hypotheses, no evidence at all
        out = _output(
            supporting=("anomaly_summary", "anomaly_count"),
        )
        result = validate_ranking_output(out, ri)
        assert not result.is_valid
        assert ViolationType.NONEXISTENT_EVIDENCE in result.violation_types
        assert result.corrected_output.supporting_evidence_ids == ()


# ---------------------------------------------------------------------------
# Part 7 — procedure constraint matrix
# ---------------------------------------------------------------------------

class TestProcedureConstraints:
    def test_valid_retrieved_procedure_accepted(self):
        ri = _input(
            hypotheses=(_hyp(),),
            valid_fault_ids=("ADCS_GYRO_SEU",),
            valid_procedure_ids=("PROC-ADCS-SEU-001",),
            evidence_status=EvidenceStatus.ADEQUATE.value,
        )
        out = _output(
            fault_ids=("ADCS_GYRO_SEU",),
            procedures=("PROC-ADCS-SEU-001",),
        )
        result = validate_ranking_output(out, ri)
        assert result.is_valid

    def test_nonexistent_procedure_rejected(self):
        ri = _input(
            hypotheses=(_hyp(),),
            valid_fault_ids=("ADCS_GYRO_SEU",),
            valid_procedure_ids=("PROC-ADCS-SEU-001",),
            evidence_status=EvidenceStatus.ADEQUATE.value,
        )
        out = _output(
            fault_ids=("ADCS_GYRO_SEU",),
            procedures=("PROC-DOES-NOT-EXIST",),
        )
        result = validate_ranking_output(out, ri)
        assert ViolationType.INVALID_PROCEDURE in result.violation_types
        assert (
            "PROC-DOES-NOT-EXIST"
            not in result.corrected_output.selected_procedure_ids
        )

    def test_procedure_from_another_scenario_rejected(self):
        # A real library procedure that was NOT retrieved for this scenario
        # is not authorized here.
        ri = _input(
            hypotheses=(_hyp(),),
            valid_fault_ids=("ADCS_GYRO_SEU",),
            valid_procedure_ids=("PROC-ADCS-SEU-001",),
            evidence_status=EvidenceStatus.ADEQUATE.value,
        )
        out = _output(
            fault_ids=("ADCS_GYRO_SEU",),
            procedures=("PROC-TCS-THERMAL-001",),
        )
        result = validate_ranking_output(out, ri)
        assert ViolationType.INVALID_PROCEDURE in result.violation_types

    def test_empty_procedure_list_accepted(self):
        ri = _input(
            hypotheses=(_hyp(),),
            valid_fault_ids=("ADCS_GYRO_SEU",),
            valid_procedure_ids=("PROC-ADCS-SEU-001",),
            evidence_status=EvidenceStatus.ADEQUATE.value,
        )
        out = _output(fault_ids=("ADCS_GYRO_SEU",), procedures=())
        result = validate_ranking_output(out, ri)
        assert result.is_valid

    def test_duplicate_procedure_deduplicated(self):
        parsed = {
            "ranked_hypotheses": [],
            "supporting_evidence_ids": [],
            "contradicting_evidence_ids": [],
            "selected_procedure_ids": [
                "PROC-ADCS-SEU-001", "PROC-ADCS-SEU-001",
            ],
            "uncertainty": "",
            "requires_human_review": True,
        }
        out = LLMRankingOutput.from_dict(parsed)
        assert out.selected_procedure_ids == ("PROC-ADCS-SEU-001",)

    def test_fabricated_procedure_rejected_when_valid_set_empty(self):
        """S200 regression: nothing was retrieved, so ``procedure_1`` must
        be rejected even though the valid set is empty."""
        ri = _input()
        out = _output(procedures=("procedure_1", "procedure_2"))
        result = validate_ranking_output(out, ri)
        assert not result.is_valid
        assert ViolationType.INVALID_PROCEDURE in result.violation_types
        assert result.corrected_output.selected_procedure_ids == ()


# ---------------------------------------------------------------------------
# Part 5 — evidence_status computation and enforcement
# ---------------------------------------------------------------------------

class TestEvidenceStatus:
    def test_no_hypotheses_is_insufficient(self):
        assert compute_evidence_status((), "ADEQUATE_FOR_PHYSICS") == "INSUFFICIENT"

    def test_no_supporting_evidence_is_insufficient(self):
        assert compute_evidence_status(
            (_hyp(supporting=()),), "ADEQUATE_FOR_PHYSICS",
        ) == "INSUFFICIENT"

    def test_adequate_window_with_supporting_evidence(self):
        assert compute_evidence_status(
            (_hyp(),), "ADEQUATE_FOR_PHYSICS",
        ) == "ADEQUATE"

    def test_degraded_window_is_partial(self):
        assert compute_evidence_status(
            (_hyp(),), "MISSING_REQUIRED_CHANNELS",
        ) == "PARTIAL"
        assert compute_evidence_status((_hyp(),), "UNDER_SAMPLED") == "PARTIAL"
        assert compute_evidence_status(
            (_hyp(),), "INVALID_TIMESTAMPS",
        ) == "PARTIAL"

    def test_contradictory_evidence_is_contradictory(self):
        assert compute_evidence_status(
            (_hyp(contradicting=("EVID-BBBBBBBBBBBB",)),),
            "ADEQUATE_FOR_PHYSICS",
        ) == "CONTRADICTORY"

    def test_contradictory_window_is_contradictory(self):
        assert compute_evidence_status(
            (_hyp(),), "CONTRADICTORY_DATA",
        ) == "CONTRADICTORY"

    def test_insufficient_default_on_ranking_input(self):
        ri = LLMRankingInput()
        assert ri.evidence_status == "INSUFFICIENT"

    def test_insufficient_contract_enforced_on_hallucination(self):
        """The exact S200 output shape must be neutralized deterministically:
        fabricated faults removed, confidence zeroed, evidence and procedures
        emptied, human review forced — even if every ID were 'valid'."""
        ri = _input(
            hypotheses=(_hyp(),),
            valid_fault_ids=("ADCS_GYRO_SEU",),
            valid_procedure_ids=("PROC-X",),
            evidence_status="INSUFFICIENT",
        )
        out = _output(
            fault_ids=("ADCS_GYRO_SEU",),
            supporting=("EVID-AAAAAAAAAAAA",),
            procedures=("PROC-X",),
            confidence=0.85,
            requires_human_review=False,
        )
        result = validate_ranking_output(out, ri)
        assert not result.is_valid
        types = result.violation_types
        assert ViolationType.INSUFFICIENT_EVIDENCE_CLAIM in types
        corrected = result.corrected_output
        assert corrected.supporting_evidence_ids == ()
        assert corrected.contradicting_evidence_ids == ()
        assert corrected.selected_procedure_ids == ()
        assert all(h.confidence == 0.0 for h in corrected.ranked_hypotheses)
        assert corrected.requires_human_review is True

    def test_fabricated_fault_rejected_when_fault_set_empty(self):
        """S200 regression: with no deterministic hypotheses, ``fault_1``
        must not survive as a validated diagnosis."""
        ri = _input()
        out = _output(fault_ids=("fault_1", "fault_2", "fault_3"))
        result = validate_ranking_output(out, ri)
        assert ViolationType.UNSUPPORTED_HYPOTHESIS in result.violation_types
        assert result.corrected_output.ranked_hypotheses == ()

    def test_evidence_status_in_prompt_dict(self):
        ri = _input(evidence_status="INSUFFICIENT")
        assert ri.as_prompt_dict()["evidence_status"] == "INSUFFICIENT"


# ---------------------------------------------------------------------------
# Prompt contract
# ---------------------------------------------------------------------------

class TestPromptContract:
    def test_prompt_carries_evidence_status_and_insufficient_rules(self):
        ri = _input(
            valid_fault_ids=("ADCS_GYRO_SEU",),
            valid_procedure_ids=("PROC-ADCS-SEU-001",),
            evidence_status="INSUFFICIENT",
        )
        messages = build_constrained_prompt(ri)
        system = messages[0]["content"]
        assert "EVIDENCE STATUS: INSUFFICIENT" in system
        assert "NEVER invent fault IDs, evidence IDs or procedure IDs" in system
        assert "requires_human_review must be true" in system
        # evidence grounding instruction (Part 4 minimal prompt fix)
        assert (
            "supporting_evidence_ids and contradicting_evidence_ids MUST come"
            " from the evidence IDs present in the input" in system
        )

    def test_user_prompt_contains_evidence_status_field(self):
        ri = _input(evidence_status="PARTIAL")
        messages = build_constrained_prompt(ri)
        payload = json.loads(messages[1]["content"])
        assert payload["evidence_status"] == "PARTIAL"


# ---------------------------------------------------------------------------
# Part 1 — output model parsing audit
# ---------------------------------------------------------------------------

class TestOutputModelParsing:
    def test_all_seven_fields_parsed_exactly_once(self):
        parsed = {
            "ranked_hypotheses": [
                {
                    "fault_id": "ADCS_GYRO_SEU",
                    "rank": 1,
                    "confidence": 0.9,
                    "justification": "j",
                    "affected_component": "GYRO_A",
                    "causal_chain": ["a", "b"],
                },
            ],
            "reasoning_summary": "summary",
            "supporting_evidence_ids": ["EVID-AAAAAAAAAAAA"],
            "contradicting_evidence_ids": ["EVID-BBBBBBBBBBBB"],
            "selected_procedure_ids": ["PROC-ADCS-SEU-001"],
            "uncertainty": "u",
            "requires_human_review": False,
        }
        out = LLMRankingOutput.from_dict(parsed)
        assert len(out.ranked_hypotheses) == 1
        assert out.reasoning_summary == "summary"
        assert out.supporting_evidence_ids == ("EVID-AAAAAAAAAAAA",)
        assert out.contradicting_evidence_ids == ("EVID-BBBBBBBBBBBB",)
        assert out.selected_procedure_ids == ("PROC-ADCS-SEU-001",)
        assert out.uncertainty == "u"
        assert out.requires_human_review is False

    def test_missing_fields_default_to_safe_values(self):
        out = LLMRankingOutput.from_dict({})
        assert out.ranked_hypotheses == ()
        assert out.supporting_evidence_ids == ()
        assert out.contradicting_evidence_ids == ()
        assert out.selected_procedure_ids == ()
        # fail-safe default: human review required
        assert out.requires_human_review is True

    def test_confidence_clamped(self):
        parsed = {
            "ranked_hypotheses": [
                {"fault_id": "F", "rank": 1, "confidence": 1.7},
                {"fault_id": "G", "rank": 2, "confidence": -0.4},
            ],
        }
        out = LLMRankingOutput.from_dict(parsed)
        assert out.ranked_hypotheses[0].confidence == 1.0
        assert out.ranked_hypotheses[1].confidence == 0.0


# ---------------------------------------------------------------------------
# Part 11 regression — confidence-monotonicity crash (cases 1004/1031)
# ---------------------------------------------------------------------------


class TestConfidenceMonotonicityRegression:
    """convert_to_sentinel_output must never emit a hypothesis list whose
    confidence increases with rank; SentinelOutput rejects that shape and
    the whole pipeline used to crash on valid model output."""

    def test_non_monotonic_confidences_are_reordered(self):
        from app.api.models import SentinelOutput
        from app.llm.ranker import convert_to_sentinel_output

        out = LLMRankingOutput(
            ranked_hypotheses=(
                RankedHypothesis(fault_id="ADCS_GYRO_SEU", rank=1, confidence=0.24),
                RankedHypothesis(fault_id="TCS_THERMAL_RUNAWAY", rank=2, confidence=0.41),
                RankedHypothesis(fault_id="OBC_WATCHDOG_OVERFLOW", rank=3, confidence=0.29),
            ),
            reasoning_summary="reasoning summary text",
        )
        sentinel_dict = convert_to_sentinel_output(out, None)
        # Must not raise — this crashed on expanded-benchmark cases 1004/1031.
        model = SentinelOutput.model_validate(sentinel_dict)
        confs = [h.confidence for h in model.hypotheses]
        assert confs == sorted(confs, reverse=True)
        assert model.hypotheses[0].root_cause == "TCS_THERMAL_RUNAWAY"

    def test_padding_never_exceeds_lowest_real_confidence(self):
        from app.api.models import SentinelOutput
        from app.llm.ranker import convert_to_sentinel_output

        out = LLMRankingOutput(
            ranked_hypotheses=(
                RankedHypothesis(fault_id="ADCS_GYRO_SEU", rank=1, confidence=0.0),
            ),
            reasoning_summary="reasoning summary text",
            requires_human_review=True,
        )
        sentinel_dict = convert_to_sentinel_output(out, None)
        model = SentinelOutput.model_validate(sentinel_dict)
        confs = [h.confidence for h in model.hypotheses]
        assert confs == sorted(confs, reverse=True)
        assert all(c <= 0.0 for c in confs[1:])
