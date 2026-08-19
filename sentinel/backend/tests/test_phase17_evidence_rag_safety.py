"""
Phase 17 — Deterministic Contract, RAG Repair, and Safety Boundary Tests.

Tests:
1. PDF RAG: readable chunk classification, metadata preservation, query relevance.
2. Evidence contract: stable evidence IDs, quantitative residuals, window adequacy.
3. Procedure ID restriction: only retrieved procedures exposed to LLM.
4. Safety H1: CMD_SAFE_MODE_EXIT blocked when SoC < 15.0%.
5. Certainty guardrail: telemetry values like "CPU at 100%" allowed, certainty claims flagged.
"""

import json
import pytest

from app.agent.rag import (
    classify_chunk_text,
    initialize_pdf_rag,
    retrieve_procedures_traced,
    _rag_status,
)
from app.api.scenarios import get_all_scenarios
from app.detection import run_detection_on_crash_dump
from app.diagnosis.candidates import generate_hypotheses
from app.validation.physics import validate_crash_dump
from app.procedures.retrieval import retrieve_procedures as p9_retrieve
from app.llm.ranker import (
    build_ranking_input,
    build_constrained_prompt,
    validate_ranking_output,
    convert_to_sentinel_output,
)
from app.llm.models import (
    LLMRankingInput,
    LLMRankingOutput,
    RankedHypothesis,
    ViolationType,
)
from app.agent.safety import validate_recovery_plan, apply_validation_to_output
from app.api.models import SentinelOutput, RecoveryStep
from app.validation.command_registry import get_command, COMMAND_REGISTRY

SCENARIOS = {str(s["scenario_id"]): s for s in get_all_scenarios()}


def _pipeline(sid: str):
    crash = SCENARIOS[sid]
    det = run_detection_on_crash_dump(crash)
    hyp = generate_hypotheses(det, crash)
    physics, _, resid, seq = validate_crash_dump(crash)
    fmap = {"MULTI_SUBSYSTEM_CASCADE": "MULTI_CASCADE"}
    ff = fmap.get(hyp.top.fault_id, hyp.top.fault_id) if hyp.top else None
    procs = p9_retrieve(
        query=crash.get("fault_type", ""),
        fault_cues=det.anomalous_channel_names() or None,
        fault_filter=ff,
        min_relevance=0.2,
    )
    ri = build_ranking_input(
        crash_dump=crash,
        anomaly_report=det,
        hypothesis_set=hyp,
        physics_report=physics,
        residual_report=resid,
        state_sequence=seq,
        procedure_results=procs,
    )
    return crash, ri, physics


# ═══════════════════════════════════════════════════════════════════════════
# PART 1 — PDF / RAG Legibility and Retrieval Tests
# ═══════════════════════════════════════════════════════════════════════════

def test_classify_chunk_text_readable():
    readable = (
        "ECSS-E-ST-70-11C Rev.1 15 October 2025: When a GNSS receiver is used "
        "to generate the on-board time reference, an on-board GNSS/OBT correlation "
        "mechanism shall be implemented with periodic telemetry."
    )
    assert classify_chunk_text(readable) == "READABLE"


def test_classify_chunk_text_empty():
    assert classify_chunk_text("") == "EMPTY"
    assert classify_chunk_text("   short   ") == "EMPTY"


def test_classify_chunk_text_garbled():
    garbled_pdf = "%PDF-1.4 %âãÏÓ 1 0 obj <</Type /Catalog /Pages 2 0 R>>"
    assert classify_chunk_text(garbled_pdf) == "GARBLED"
    garbled_control = "abc\x00\x01\x02\x03\x04\x05\x06\x07\x08defghijklmn"
    assert classify_chunk_text(garbled_control) == "GARBLED"


def test_pdf_rag_initialization_and_readable_retrieval():
    ok = initialize_pdf_rag()
    assert ok is True
    assert _rag_status.pdf_count == 2
    assert _rag_status.chunk_count > 0

    results, trace = retrieve_procedures_traced(
        "safe mode recovery GNSS time correlation",
        ["GYRO_A_RATE"],
        top_k=3,
        use_pdf_rag=True,
    )
    assert len(results) > 0
    # Verify every retrieved chunk is classified as READABLE
    for chunk in results:
        assert classify_chunk_text(chunk) == "READABLE"
        assert not chunk.startswith("%PDF-")


# ═══════════════════════════════════════════════════════════════════════════
# PART 2 — Evidence Contract Tests (G1, G2, G3)
# ═══════════════════════════════════════════════════════════════════════════

def test_evidence_ids_in_candidates_and_prompt():
    _, ri, _ = _pipeline("1")
    # G1 check: supporting evidence has stable non-empty IDs
    top_hyp = ri.hypotheses[0]
    assert len(top_hyp.supporting_evidence) > 0
    for eid in top_hyp.supporting_evidence:
        assert isinstance(eid, str)
        assert eid.startswith("EVID-")

    prompt_dict = ri.as_prompt_dict()
    h_prompt = prompt_dict["hypotheses"][0]
    assert len(h_prompt["supporting_evidence"]) > 0
    assert all(eid.startswith("EVID-") for eid in h_prompt["supporting_evidence"])


def test_quantitative_residuals_in_prompt():
    # G2 check: quantitative residuals are in prompt dictionary
    _, ri, _ = _pipeline("2")
    prompt_dict = ri.as_prompt_dict()
    assert "spacecraft_state" in prompt_dict
    state = prompt_dict["spacecraft_state"]
    assert "residuals" in state
    assert len(state["residuals"]) > 0
    for r in state["residuals"]:
        assert "channel" in r
        assert "observed" in r
        assert "predicted" in r
        assert "residual" in r
        assert "tolerance" in r
        assert "status" in r


def test_window_adequacy_in_prompt():
    # G3 check: window adequacy reaches the LLM
    _, ri, _ = _pipeline("1")
    prompt_dict = ri.as_prompt_dict()
    state = prompt_dict["spacecraft_state"]
    assert "window_adequacy" in state
    wa = state["window_adequacy"]
    assert "status" in wa
    assert "sample_count" in wa
    assert "required_sample_count" in wa
    assert "channels_checked" in wa
    assert "reason" in wa
    assert wa["status"] in (
        "ADEQUATE_FOR_PHYSICS",
        "UNDER_SAMPLED",
        "MISSING_REQUIRED_CHANNELS",
        "INVALID_TIMESTAMPS",
        "CONTRADICTORY_DATA",
    )


# ═══════════════════════════════════════════════════════════════════════════
# PART 3 — Restrict Procedure IDs Tests
# ═══════════════════════════════════════════════════════════════════════════

def test_valid_procedure_ids_restricted_to_retrieved():
    _, ri, _ = _pipeline("1")
    # Only the retrieved procedure for Scenario 1 should be exposed
    assert len(ri.valid_procedure_ids) == 1
    assert ri.valid_procedure_ids[0] == "PROC-ADCS-SEU-001"
    # Full library has 6 procedures, but only 1 was retrieved
    prompt_dict = ri.as_prompt_dict()
    assert prompt_dict["valid_procedure_ids"] == ["PROC-ADCS-SEU-001"]


def test_unretrieved_procedure_rejected_by_guardrail():
    crash, ri, physics = _pipeline("1")
    # PROC-EPS-SOLAR-001 is a real library procedure, but was NOT retrieved for scenario 1
    raw = json.dumps({
        "ranked_hypotheses": [{
            "fault_id": "ADCS_GYRO_SEU",
            "rank": 1,
            "confidence": 0.85,
            "justification": "gyro failure",
            "affected_component": "GYRO",
            "causal_chain": [],
        }],
        "reasoning_summary": "SEU fault",
        "supporting_evidence_ids": [],
        "contradicting_evidence_ids": [],
        "selected_procedure_ids": ["PROC-EPS-SOLAR-001"],  # not in ri.valid_procedure_ids!
        "uncertainty": "",
        "requires_human_review": False,
    })
    parsed = json.loads(raw)
    out = LLMRankingOutput.from_dict(parsed)
    gr = validate_ranking_output(out, ri, physics, raw_parsed=parsed, raw_response=raw)
    assert not gr.is_valid
    assert any(v.violation_type == ViolationType.INVALID_PROCEDURE for v in gr.violations)
    # The unretrieved procedure should be stripped from corrected output
    assert "PROC-EPS-SOLAR-001" not in gr.corrected_output.selected_procedure_ids


# ═══════════════════════════════════════════════════════════════════════════
# PART 4 — Safety Gap H1: CMD_SAFE_MODE_EXIT Battery Floor Tests
# ═══════════════════════════════════════════════════════════════════════════

def _dummy_hypotheses():
    from app.api.models import Hypothesis
    return [
        Hypothesis(rank=1, root_cause="ADCS_GYRO_SEU", affected_component="GYRO_A", confidence=0.8, causal_chain=["Gyro SEU", "Safe Mode"]),
        Hypothesis(rank=2, root_cause="OBC_WATCHDOG_OVERFLOW", affected_component="OBC_A", confidence=0.15, causal_chain=["Watchdog", "Reboot"]),
        Hypothesis(rank=3, root_cause="MULTI_CASCADE", affected_component="MULTI", confidence=0.05, causal_chain=["Cascade", "Safe Mode"]),
    ]


def test_safe_mode_exit_blocked_when_soc_below_floor():
    # SoC = 14.2% (< 15% floor)
    crash = {
        "scenario_id": "test_soc_low",
        "pre_fault_telemetry_window": [
            {"parameter": "SoC_pct", "value": 14.2, "relative_time_s": -10.0},
        ],
    }
    sentinel_output = SentinelOutput(
        hypotheses=_dummy_hypotheses(),
        recovery_plan=[
            RecoveryStep(
                step=1,
                command="CMD_SAFE_MODE_EXIT",
                description="Exit safe mode",
                rationale="Recovery completed",
                risk="LOW",
                wait_seconds=10,
                verify="normal_mode_flag is true",
            )
        ],
        confidence=0.9,
        requires_human_review=False,
        reasoning_summary="Test recovery",
    )
    val = validate_recovery_plan(sentinel_output, crash)
    assert len(val.blocked_steps) == 1
    assert val.blocked_steps[0].original_step.command == "CMD_SAFE_MODE_EXIT"
    assert val.blocked_steps[0].violation_code == "BATTERY_FLOOR"
    assert val.safety_status.value == "BLOCKED"


def test_safe_mode_exit_allowed_when_soc_above_floor():
    # SoC = 50.0% (> 15% floor)
    crash = {
        "scenario_id": "test_soc_ok",
        "pre_fault_telemetry_window": [
            {"parameter": "SoC_pct", "value": 50.0, "relative_time_s": -10.0},
        ],
    }
    sentinel_output = SentinelOutput(
        hypotheses=_dummy_hypotheses(),
        recovery_plan=[
            RecoveryStep(
                step=1,
                command="CMD_SAFE_MODE_EXIT",
                description="Exit safe mode",
                rationale="Recovery completed",
                risk="LOW",
                wait_seconds=10,
                verify="normal_mode_flag is true",
            )
        ],
        confidence=0.9,
        requires_human_review=False,
        reasoning_summary="Test recovery",
    )
    val = validate_recovery_plan(sentinel_output, crash)
    assert len(val.blocked_steps) == 0
    assert len(val.validated_steps) == 1
    assert val.safety_status.value == "VALIDATED"


def test_safe_mode_exit_allowed_when_soc_exactly_at_floor():
    # SoC = 15.0% (exactly at floor)
    crash = {
        "scenario_id": "test_soc_boundary",
        "pre_fault_telemetry_window": [
            {"parameter": "SoC_pct", "value": 15.0, "relative_time_s": -10.0},
        ],
    }
    sentinel_output = SentinelOutput(
        hypotheses=_dummy_hypotheses(),
        recovery_plan=[
            RecoveryStep(
                step=1,
                command="CMD_SAFE_MODE_EXIT",
                description="Exit safe mode",
                rationale="Recovery completed",
                risk="LOW",
                wait_seconds=10,
                verify="normal_mode_flag is true",
            )
        ],
        confidence=0.9,
        requires_human_review=False,
        reasoning_summary="Test recovery",
    )
    val = validate_recovery_plan(sentinel_output, crash)
    assert len(val.blocked_steps) == 0
    assert len(val.validated_steps) == 1


def test_safe_mode_exit_thermal_constraint_still_blocks():
    # SoC is good (80%) but temperature is 95C (> 85C survival limit)
    crash = {
        "scenario_id": "test_thermal_high",
        "pre_fault_telemetry_window": [
            {"parameter": "SoC_pct", "value": 80.0, "relative_time_s": -10.0},
            {"parameter": "Component_temp_C", "value": 95.0, "relative_time_s": -10.0},
        ],
    }
    sentinel_output = SentinelOutput(
        hypotheses=_dummy_hypotheses(),
        recovery_plan=[
            RecoveryStep(
                step=1,
                command="CMD_SAFE_MODE_EXIT",
                description="Exit safe mode",
                rationale="Recovery completed",
                risk="LOW",
                wait_seconds=10,
                verify="normal_mode_flag is true",
            )
        ],
        confidence=0.9,
        requires_human_review=False,
        reasoning_summary="Test recovery",
    )
    val = validate_recovery_plan(sentinel_output, crash)
    assert len(val.blocked_steps) == 1
    assert val.blocked_steps[0].original_step.command == "CMD_SAFE_MODE_EXIT"
    assert val.blocked_steps[0].violation_code == "THERMAL_SURVIVAL"


# ═══════════════════════════════════════════════════════════════════════════
# PART 5 — Certainty Guardrail Review Tests
# ═══════════════════════════════════════════════════════════════════════════

def test_certainty_guardrail_allows_telemetry_percentages():
    _, ri, physics = _pipeline("3")
    # "CPU load at 100%" is a telemetry observation, not a certainty claim
    raw = json.dumps({
        "ranked_hypotheses": [{
            "fault_id": "OBC_WATCHDOG_OVERFLOW",
            "rank": 1,
            "confidence": 0.85,
            "justification": "CPU load at 100% caused thread starvation and watchdog reset.",
            "affected_component": "OBC",
            "causal_chain": ["CPU load at 100%"],
        }],
        "reasoning_summary": "CPU utilization reached 100% prior to watchdog overflow.",
        "supporting_evidence_ids": [],
        "contradicting_evidence_ids": [],
        "selected_procedure_ids": ["PROC-OBC-WATCHDOG-001"],
        "uncertainty": "",
        "requires_human_review": False,
    })
    parsed = json.loads(raw)
    out = LLMRankingOutput.from_dict(parsed)
    gr = validate_ranking_output(out, ri, physics, raw_parsed=parsed, raw_response=raw)
    certainty_violations = [v for v in gr.violations if v.violation_type == ViolationType.UNSUPPORTED_CERTAINTY]
    assert len(certainty_violations) == 0


def test_certainty_guardrail_flags_unsupported_certainty_claim():
    _, ri, physics = _pipeline("3")
    # "100% certain" is an explicit certainty claim
    raw = json.dumps({
        "ranked_hypotheses": [{
            "fault_id": "OBC_WATCHDOG_OVERFLOW",
            "rank": 1,
            "confidence": 0.85,
            "justification": "I am 100% certain this is an OBC watchdog overflow.",
            "affected_component": "OBC",
            "causal_chain": [],
        }],
        "reasoning_summary": "Diagnosed with 100% confidence.",
        "supporting_evidence_ids": [],
        "contradicting_evidence_ids": [],
        "selected_procedure_ids": ["PROC-OBC-WATCHDOG-001"],
        "uncertainty": "",
        "requires_human_review": False,
    })
    parsed = json.loads(raw)
    out = LLMRankingOutput.from_dict(parsed)
    gr = validate_ranking_output(out, ri, physics, raw_parsed=parsed, raw_response=raw)
    assert not gr.is_valid
    certainty_violations = [v for v in gr.violations if v.violation_type == ViolationType.UNSUPPORTED_CERTAINTY]
    assert len(certainty_violations) > 0
    assert gr.corrected_output.requires_human_review is True
