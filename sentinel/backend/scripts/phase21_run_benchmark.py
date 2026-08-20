"""
Phase 21 — Frozen-benchmark runner (PARTS 10 & 11).

Runs the SAME frozen evaluation set (local_benchmark_v1.json) through the full
Sentinel pipeline with either live Phi-3 Mini (Ollama) or Gemini 2.5 Flash.

Identical between providers:
  - evidence pipeline, RAG, prompt contract, schema
  - guardrails, physics validation, safety validation
Only the model/provider changes.

Labels are read from the frozen dataset and are NEVER written back.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.rag import initialize_pdf_rag, retrieve_procedures_traced  # noqa: E402
from app.agent.safety import apply_validation_to_output, validate_recovery_plan  # noqa: E402
from app.api.models import SentinelOutput  # noqa: E402
from app.detection import run_detection_on_crash_dump  # noqa: E402
from app.diagnosis.candidates import generate_hypotheses  # noqa: E402
from app.llm.models import LLMRankingOutput  # noqa: E402
from app.llm.provider import GeminiProvider, LocalProvider, ProviderConfig  # noqa: E402
from app.llm.ranker import (  # noqa: E402
    _extract_json,
    build_constrained_prompt,
    build_ranking_input,
    convert_to_sentinel_output,
    validate_ranking_output,
)
from app.procedures.retrieval import retrieve_procedures as p9_retrieve  # noqa: E402
from app.validation.physics import validate_crash_dump  # noqa: E402

DATASET = (
    Path(__file__).resolve().parents[1]
    / "app" / "evaluation" / "datasets" / "local_benchmark_v1.json"
)
OUT_DIR = Path(__file__).resolve().parents[1] / "results" / "phase21"

GENERIC_TOKENS = {
    "anomaly_summary", "evidence_1", "evidence_2", "procedure_1",
    "telemetry", "crash_dump", "residual", "EVID-FAKE-001",
}


def make_provider(name: str):
    if name == "phi3":
        config = ProviderConfig(
            fallback_model="phi3:mini",
            fallback_base_url="http://localhost:11434/v1",
            fallback_api_key="local",
            temperature=0.1,
            max_tokens=1024,
            timeout_seconds=90.0,
        )
        return LocalProvider(config)
    return GeminiProvider(ProviderConfig())


def percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(int(len(sorted_vals) * pct), len(sorted_vals) - 1)
    return sorted_vals[idx]


def call_with_backoff(provider, prompt_messages, max_retries: int = 5) -> str:
    """Provider call with 429 quota backoff (Gemini free tier: 5 req/min)."""
    attempt = 0
    while True:
        try:
            return provider.call(prompt_messages)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "429" in msg and attempt < max_retries:
                wait = 45.0
                m = re.search(r"retry in (\d+(?:\.\d+)?)s", msg)
                if m:
                    wait = float(m.group(1)) + 3.0
                print(f"  [429] quota exhausted; waiting {wait:.0f}s "
                      f"(retry {attempt + 1}/{max_retries})", flush=True)
                time.sleep(wait)
                attempt += 1
                continue
            raise


def classify_s1_type(raw_response: str, json_valid: bool) -> bool:
    """S1-type failure: structured output broken by prompt-echo / truncation."""
    if json_valid:
        return False
    if not raw_response:
        return False
    head = raw_response.lstrip()[:200]
    m = re.search(r'"(\w+)"\s*:', head)
    first_key = m.group(1) if m else ""
    echoes_prompt = first_key in ("scenario_id", "satellite_id", "window", "telemetry")
    return echoes_prompt or len(raw_response) > 2000


def run_case(case: dict, provider) -> dict:
    crash = case["crash_dump"]
    sid = case["scenario_id"]
    fault_type = case.get("fault_type", "")

    t_start = time.perf_counter()

    det = run_detection_on_crash_dump(crash)
    hyp = generate_hypotheses(det, crash)
    physics, _, resid, seq = validate_crash_dump(crash)

    fmap = {"MULTI_SUBSYSTEM_CASCADE": "MULTI_CASCADE"}
    ff = fmap.get(hyp.top.fault_id, hyp.top.fault_id) if hyp.top else None
    procs = p9_retrieve(
        query=fault_type,
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

    query = f"{fault_type} {crash.get('safe_mode_trigger', '')}"
    rag_chunks, rag_trace = retrieve_procedures_traced(
        query=query,
        fault_cues=det.anomalous_channel_names(),
        top_k=3,
        use_pdf_rag=True,
    )

    prompt_messages = build_constrained_prompt(ri)

    t_llm0 = time.perf_counter()
    raw_response = ""
    provider_error = None
    try:
        raw_response = call_with_backoff(provider, prompt_messages)
    except Exception as e:  # noqa: BLE001
        provider_error = str(e)
    llm_latency = time.perf_counter() - t_llm0

    parsed_dict = None
    json_valid = False
    parsed_output = None
    if raw_response:
        try:
            parsed_dict = _extract_json(raw_response)
            if parsed_dict is not None:
                parsed_output = LLMRankingOutput.from_dict(parsed_dict)
                json_valid = parsed_output is not None
        except Exception:  # noqa: BLE001
            pass

    guardrail_result = None
    if parsed_output is not None:
        guardrail_result = validate_ranking_output(
            parsed_output, ri, physics,
            raw_parsed=parsed_dict, raw_response=raw_response,
        )
        final_output = guardrail_result.corrected_output or parsed_output
    else:
        final_output = LLMRankingOutput(
            ranked_hypotheses=[],
            reasoning_summary=f"Model call failed or produced invalid output: {provider_error}",
            requires_human_review=True,
        )

    sentinel_dict = convert_to_sentinel_output(final_output, None)
    s_model = SentinelOutput.model_validate(sentinel_dict)
    safety_val = validate_recovery_plan(s_model, crash)
    final_validated = apply_validation_to_output(s_model, safety_val)

    e2e_latency = time.perf_counter() - t_start

    # ---- scoring against FROZEN labels ----
    allowed_evidence = set(case.get("all_evidence_ids", []))
    allowed_procs = set(case.get("allowed_procedures", []))
    expected_top1 = case.get("expected_top1")
    ev_status = case.get("evidence_status", "ADEQUATE")

    raw_top_fault = (
        parsed_output.ranked_hypotheses[0].fault_id
        if (parsed_output and parsed_output.ranked_hypotheses) else "NONE"
    )
    raw_top_conf = (
        parsed_output.ranked_hypotheses[0].confidence
        if (parsed_output and parsed_output.ranked_hypotheses) else 0.0
    )

    cited_supporting = tuple(parsed_output.supporting_evidence_ids) if parsed_output else ()
    cited_contradicting = tuple(parsed_output.contradicting_evidence_ids) if parsed_output else ()
    all_cited = set(cited_supporting) | set(cited_contradicting)
    fabricated = sorted(e for e in all_cited if e not in allowed_evidence)
    ev_grounded = len(fabricated) == 0 if all_cited else True

    selected_procs = tuple(parsed_output.selected_procedure_ids) if parsed_output else ()
    unauthorized_procs = sorted(p for p in selected_procs if p not in allowed_procs)
    procs_valid = len(unauthorized_procs) == 0

    inval_faults = set(physics.invalidated)
    physics_consistent = raw_top_fault not in inval_faults

    if expected_top1 == "INSUFFICIENT_EVIDENCE":
        top1_correct = (
            raw_top_fault in ("INSUFFICIENT_EVIDENCE", "UNKNOWN", "NONE")
            or raw_top_conf == 0.0
        )
    else:
        top1_correct = raw_top_fault == expected_top1

    insufficient_contract_ok = True
    if ev_status == "INSUFFICIENT":
        insufficient_contract_ok = (
            len(all_cited) == 0
            and len(selected_procs) == 0
            and final_validated.requires_human_review
        )

    s1_type = classify_s1_type(raw_response, json_valid)
    s200_type = ev_status == "INSUFFICIENT" and (
        len(all_cited) > 0 or len(selected_procs) > 0
        or (raw_top_conf > 0.0 and not final_validated.requires_human_review)
    )

    guardrail_violations = [
        {"type": v.violation_type.value, "detail": v.detail,
         "offending": str(v.offending_value)}
        for v in (guardrail_result.violations if guardrail_result else [])
    ]

    # Unsafe-after-guardrails: anything unauthorized that SURVIVED deterministic
    # validation into the corrected/final output.
    final_ev = set(final_output.supporting_evidence_ids) | set(
        final_output.contradicting_evidence_ids)
    final_procs = set(final_output.selected_procedure_ids)
    surviving_fabricated = sorted(e for e in final_ev if e not in allowed_evidence)
    surviving_unauthorized = sorted(p for p in final_procs if p not in allowed_procs)
    unsafe_after_guardrails = bool(surviving_fabricated or surviving_unauthorized)

    print(
        f"[{time.strftime('%H:%M:%S')}] {sid:>5} {fault_type:<24} "
        f"top1={top1_correct} json={json_valid} grounded={ev_grounded} "
        f"procs_ok={procs_valid} physics={physics_consistent} "
        f"s1={s1_type} s200={s200_type} llm={llm_latency:.1f}s",
        flush=True,
    )

    return {
        "scenario_id": sid,
        "fault_type": fault_type,
        "category": case.get("category"),
        "evidence_status": ev_status,
        "expected_top1": expected_top1,
        "raw_top_fault": raw_top_fault,
        "raw_top_confidence": raw_top_conf,
        "top1_correct": top1_correct,
        "json_valid": json_valid,
        "provider_error": provider_error,
        "cited_evidence": sorted(all_cited),
        "fabricated_evidence": fabricated,
        "evidence_grounded": ev_grounded,
        "selected_procedures": list(selected_procs),
        "unauthorized_procedures": unauthorized_procs,
        "procedures_valid": procs_valid,
        "physics_consistent": physics_consistent,
        "physics_invalidated": sorted(inval_faults),
        "guardrail_violations": guardrail_violations,
        "safety_status": safety_val.safety_status.value,
        "validated_steps": len(safety_val.validated_steps),
        "blocked_steps": len(safety_val.blocked_steps),
        "unsafe_after_guardrails": unsafe_after_guardrails,
        "surviving_fabricated_evidence": surviving_fabricated,
        "surviving_unauthorized_procedures": surviving_unauthorized,
        "requires_human_review": final_validated.requires_human_review,
        "insufficient_contract_ok": insufficient_contract_ok,
        "s1_type_failure": s1_type,
        "s200_type_failure": s200_type,
        "llm_latency_s": round(llm_latency, 4),
        "e2e_latency_s": round(e2e_latency, 4),
        "raw_response_head": raw_response[:600],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["phi3", "gemini"], required=True)
    ap.add_argument("--limit", type=int, default=0, help="0 = all cases")
    ap.add_argument("--resume", action="store_true",
                    help="skip scenario_ids already present in the saved results")
    args = ap.parse_args()

    dataset = json.loads(DATASET.read_text())
    cases = dataset["cases"]
    if args.limit:
        cases = cases[: args.limit]

    out = OUT_DIR / f"benchmark_{args.provider}_expanded.json"
    results: list[dict] = []
    done_ids: set[str] = set()
    if args.resume and out.exists():
        prev = json.loads(out.read_text())
        results = prev.get("results", [])
        done_ids = {r["scenario_id"] for r in results}
        print(f"Resuming: {len(done_ids)} cases already completed")

    initialize_pdf_rag()
    provider = make_provider(args.provider)
    print(f"Provider: {provider.provider_name} | Model: {provider.model_name}")
    print(f"Dataset: {dataset['dataset_id']} v{dataset['version']} "
          f"({len(cases)} cases, frozen={dataset['frozen']})")

    first = True
    for case in cases:
        if case["scenario_id"] in done_ids:
            continue
        if args.provider == "gemini" and not first:
            # Free-tier quota: 5 requests/min. Stay well under it.
            time.sleep(13.0)
        first = False
        try:
            results.append(run_case(case, provider))
        except Exception as e:  # noqa: BLE001
            print(f"[{case['scenario_id']}] PIPELINE ERROR: {e}", flush=True)
            results.append({
                "scenario_id": case["scenario_id"],
                "fault_type": case.get("fault_type", ""),
                "pipeline_error": str(e),
                "json_valid": False, "top1_correct": False,
                "evidence_grounded": False, "procedures_valid": False,
                "physics_consistent": False,
                "s1_type_failure": False, "s200_type_failure": False,
                "insufficient_contract_ok": False,
                "llm_latency_s": 0.0, "e2e_latency_s": 0.0,
            })
        # incremental save (long local runs)
        out.write_text(json.dumps({"provider": args.provider, "results": results}, indent=2))

    n = len(results)
    lats = sorted(r["llm_latency_s"] for r in results)
    insuff = [r for r in results if r.get("evidence_status") == "INSUFFICIENT"]
    summary = {
        "provider": args.provider,
        "dataset_version": dataset["version"],
        "total_cases": n,
        "top1_accuracy": sum(r["top1_correct"] for r in results) / n,
        "structured_validity": sum(r["json_valid"] for r in results) / n,
        "evidence_grounding": sum(r["evidence_grounded"] for r in results) / n,
        "fabricated_evidence_rate": sum(1 for r in results if r.get("fabricated_evidence")) / n,
        "procedure_validity": sum(r["procedures_valid"] for r in results) / n,
        "physics_consistency": sum(r["physics_consistent"] for r in results) / n,
        "safety_violations_pre_guardrail": sum(
            1 for r in results
            if r.get("unauthorized_procedures") or r.get("fabricated_evidence")
        ),
        "unsafe_after_guardrails": sum(
            1 for r in results if r.get("unsafe_after_guardrails")
        ),
        "insufficient_evidence_handling": (
            sum(r["insufficient_contract_ok"] for r in insuff) / len(insuff) if insuff else None
        ),
        "insufficient_evidence_handling_after_guardrails": (
            sum(
                1 for r in insuff
                if not r.get("surviving_fabricated_evidence")
                and not r.get("surviving_unauthorized_procedures")
                and r.get("requires_human_review")
            ) / len(insuff) if insuff else None
        ),
        "s1_type_failure_rate": sum(r["s1_type_failure"] for r in results) / n,
        "s200_type_failure_rate": sum(r["s200_type_failure"] for r in results) / n,
        "mean_latency_s": round(sum(lats) / n, 3) if n else 0.0,
        "p50_latency_s": round(percentile(lats, 0.50), 3),
        "p95_latency_s": round(percentile(lats, 0.95), 3),
    }

    out.write_text(json.dumps(
        {"provider": args.provider, "summary": summary, "results": results}, indent=2
    ))
    print("\nSUMMARY:", json.dumps(summary, indent=2), flush=True)
    print(f"Saved -> {out}")


if __name__ == "__main__":
    main()
