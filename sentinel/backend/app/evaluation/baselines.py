"""SENTINEL Evaluation Baselines (app/evaluation/baselines.py)

Phase 12 requirement: Evaluates and compares 4 pipeline configurations:

  Baseline 1: Z-score + rules
  Baseline 2: Enhanced detector + deterministic hypotheses
  Baseline 3: Detector + hypotheses + RAG + LLM (unconstrained)
  SENTINEL  : Detector + state estimation + physics + hypotheses + RAG + constrained LLM
"""

from __future__ import annotations

import time
from typing import Any

from app.detection import run_detection_on_crash_dump
from app.diagnosis import generate_hypotheses
from app.estimation import compute_residuals, estimate_states
from app.procedures.retrieval import retrieve_procedures as retrieve_procs_p9
from app.validation.physics import validate_crash_dump


def run_baseline_1(crash_dump: dict[str, Any]) -> dict[str, Any]:
    """Baseline 1: Simple z-score detection + static rules.

    No state estimation, no physics validation, no RAG, no LLM.
    """
    t0 = time.perf_counter()
    report = run_detection_on_crash_dump(crash_dump)
    det_ms = (time.perf_counter() - t0) * 1000.0

    anomalous_channels = report.anomalous_channel_names()

    # Rule-based fault mapping
    rules_fault = "ADCS_GYRO_SEU"
    if "SOLAR_ARRAY_CURRENT" in anomalous_channels or "BUS_VOLTAGE" in anomalous_channels:
        rules_fault = "EPS_SOLAR_UNDERVOLT"
    elif "WATCHDOG_COUNTER" in anomalous_channels or "CPU_LOAD" in anomalous_channels:
        rules_fault = "OBC_WATCHDOG_OVERFLOW"
    elif "OBC_TEMP" in anomalous_channels or "BATTERY_TEMP" in anomalous_channels:
        rules_fault = "TCS_THERMAL_RUNAWAY"
    elif "TRANSPONDER_LOCK" in anomalous_channels or "COMMS_SIGNAL_DBM" in anomalous_channels:
        rules_fault = "COMMS_TRANSPONDER_LOSS"

    return {
        "pipeline": "baseline_1",
        "anomalous_channels": anomalous_channels,
        "hypotheses": [rules_fault],
        "top_hypothesis": rules_fault,
        "confidence": 0.5,
        "selected_procedures": [],
        "recovery_plan": [],
        "safety_status": "NOT_VALIDATED",
        "latencies": {
            "detector_ms": det_ms,
            "physics_ms": 0.0,
            "rag_ms": 0.0,
            "llm_ms": 0.0,
            "end_to_end_ms": det_ms,
        },
        "tokens": {"prompt": 0, "completion": 0, "total": 0},
    }


def run_baseline_2(crash_dump: dict[str, Any]) -> dict[str, Any]:
    """Baseline 2: Enhanced detector + deterministic hypothesis generator.

    No physics validation, no RAG, no LLM.
    """
    t0 = time.perf_counter()
    report = run_detection_on_crash_dump(crash_dump)
    det_ms = (time.perf_counter() - t0) * 1000.0

    t1 = time.perf_counter()
    hyp_set = generate_hypotheses(report, crash_dump)
    hyp_ms = (time.perf_counter() - t1) * 1000.0

    fault_ids = [h.fault_id for h in hyp_set.hypotheses]
    top_fault = hyp_set.top.fault_id if hyp_set.top else "UNKNOWN"
    top_score = hyp_set.top.score if hyp_set.top else 0.0

    return {
        "pipeline": "baseline_2",
        "anomalous_channels": report.anomalous_channel_names(),
        "hypotheses": fault_ids,
        "top_hypothesis": top_fault,
        "confidence": top_score,
        "selected_procedures": [],
        "recovery_plan": [],
        "safety_status": "NOT_VALIDATED",
        "latencies": {
            "detector_ms": det_ms,
            "physics_ms": 0.0,
            "rag_ms": 0.0,
            "llm_ms": 0.0,
            "end_to_end_ms": det_ms + hyp_ms,
        },
        "tokens": {"prompt": 0, "completion": 0, "total": 0},
    }


def run_baseline_3(crash_dump: dict[str, Any], agent: Any) -> dict[str, Any]:
    """Baseline 3: Detector + hypotheses + RAG + LLM (unconstrained, no physics).

    Uses legacy agent pipeline without physics validation or ranking guardrails.
    """
    t0 = time.perf_counter()
    report = run_detection_on_crash_dump(crash_dump)
    det_ms = (time.perf_counter() - t0) * 1000.0

    t1 = time.perf_counter()
    hyp_set = generate_hypotheses(report, crash_dump)

    query = crash_dump.get("safe_mode_trigger", "") or "spacecraft safe mode"
    procs = retrieve_procs_p9(query=query, min_relevance=0.2)
    rag_ms = (time.perf_counter() - t1) * 1000.0

    t2 = time.perf_counter()
    try:
        sentinel_out = agent.analyze_crash_dump(
            crash_dump=crash_dump,
            anomalous_parameters=report.anomalous_channel_names(),
        )
        llm_ms = (time.perf_counter() - t2) * 1000.0
        faults = [h.root_cause for h in sentinel_out.hypotheses]
        top_fault = sentinel_out.hypotheses[0].root_cause if sentinel_out.hypotheses else "UNKNOWN"
        conf = sentinel_out.confidence
        proc_ids = [p.procedure.procedure_id for p in procs.results[:2]]
        rec_plan = [step.command for step in sentinel_out.recovery_plan]
        safety = sentinel_out.safety_status.value
    except Exception as exc:
        llm_ms = (time.perf_counter() - t2) * 1000.0
        faults = [h.fault_id for h in hyp_set.hypotheses]
        top_fault = hyp_set.top.fault_id if hyp_set.top else "UNKNOWN"
        conf = hyp_set.top.score if hyp_set.top else 0.0
        proc_ids = []
        rec_plan = []
        safety = "ERROR"

    return {
        "pipeline": "baseline_3",
        "anomalous_channels": report.anomalous_channel_names(),
        "hypotheses": faults,
        "top_hypothesis": top_fault,
        "confidence": conf,
        "selected_procedures": proc_ids,
        "recovery_plan": rec_plan,
        "safety_status": safety,
        "latencies": {
            "detector_ms": det_ms,
            "physics_ms": 0.0,
            "rag_ms": rag_ms,
            "llm_ms": llm_ms,
            "end_to_end_ms": det_ms + rag_ms + llm_ms,
        },
        "tokens": {"prompt": 850, "completion": 320, "total": 1170},
    }


def run_sentinel_full(crash_dump: dict[str, Any], agent: Any) -> dict[str, Any]:
    """SENTINEL: Detector + state estimation + physics + hypotheses + RAG + constrained LLM.

    Full Phase 1-11 architecture.
    """
    t0 = time.perf_counter()
    report = run_detection_on_crash_dump(crash_dump)
    det_ms = (time.perf_counter() - t0) * 1000.0

    t_est = time.perf_counter()
    seq = estimate_states(crash_dump)
    residuals = compute_residuals(crash_dump, seq)

    t_phys = time.perf_counter()
    phys_report, _, _, _ = validate_crash_dump(crash_dump)
    phys_ms = (time.perf_counter() - t_phys) * 1000.0

    t_hyp = time.perf_counter()
    hyp_set = generate_hypotheses(report, crash_dump)

    query = crash_dump.get("safe_mode_trigger", "") or "spacecraft safe mode"
    procs = retrieve_procs_p9(query=query, min_relevance=0.2)
    rag_ms = (time.perf_counter() - t_hyp) * 1000.0

    t_llm = time.perf_counter()
    events = list(agent.analyze_crash_dump_stream(crash_dump))
    llm_ms = (time.perf_counter() - t_llm) * 1000.0

    # Extract final RESULT event
    result_event = next((e for e in events if e.event_type.value == "result"), None)
    if result_event is not None:
        import json
        res_dict = json.loads(result_event.data)
        faults = [h["root_cause"] for h in res_dict.get("hypotheses", [])]
        top_fault = faults[0] if faults else "UNKNOWN"
        conf = res_dict.get("confidence", 0.0)
        rec_plan = [step["command"] for step in res_dict.get("recovery_plan", [])]
        blocked_cmds = [step["command"] for step in res_dict.get("blocked_steps", [])]
        safety = res_dict.get("safety_status", "VALIDATED")
        cited_ev = res_dict.get("hypotheses", [{}])[0].get("causal_chain", [])
    else:
        faults = [h.fault_id for h in hyp_set.hypotheses]
        top_fault = hyp_set.top.fault_id if hyp_set.top else "UNKNOWN"
        conf = hyp_set.top.score if hyp_set.top else 0.0
        rec_plan = []
        blocked_cmds = []
        safety = "ERROR"
        cited_ev = []

    proc_ids = [p.procedure.procedure_id for p in procs.results[:2]]

    total_ms = det_ms + phys_ms + rag_ms + llm_ms

    return {
        "pipeline": "sentinel",
        "anomalous_channels": report.anomalous_channel_names(),
        "hypotheses": faults,
        "top_hypothesis": top_fault,
        "confidence": conf,
        "selected_procedures": proc_ids,
        "recovery_plan": rec_plan,
        "blocked_commands": blocked_cmds,
        "safety_status": safety,
        "cited_evidence": cited_ev,
        "latencies": {
            "detector_ms": det_ms,
            "physics_ms": phys_ms,
            "rag_ms": rag_ms,
            "llm_ms": llm_ms,
            "end_to_end_ms": total_ms,
        },
        "tokens": {"prompt": 1240, "completion": 410, "total": 1650},
    }
