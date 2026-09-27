"""
Phase 21 — Minimal structured-output contract experiment (PART 3).

Controlled experiment OUTSIDE production behavior. Asks phi3:mini to produce
progressively more complex structured output and measures where reliability
breaks:

  A. minimal schema (empty lists, 6 fields, no reasoning)
  B. schema + one hypothesis
  C. schema + evidence IDs
  D. schema + causal chain
  E. full Sentinel production prompt (scenario S1)
  F. S1 production prompt WITHOUT response_format=json_object (control for
     Ollama response-format behavior — Part 2 cause D)

Levels A-D run 3 trials each; E and F run 1 trial each (E is additionally
covered by the S1 reproduction, which failed identically twice).

Production code is NOT modified by this experiment. Levels A-D use a reduced
schema exactly as specified by Phase 21:

  {
    "ranked_hypotheses": [],
    "supporting_evidence_ids": [],
    "contradicting_evidence_ids": [],
    "selected_procedure_ids": [],
    "uncertainty": "",
    "requires_human_review": true
  }
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE_URL = "http://localhost:11434/v1"
MODEL = "phi3:mini"
TEMPERATURE = 0.1
OUT_DIR = Path(__file__).resolve().parents[1] / "results" / "phase21"

MINIMAL_SCHEMA = {
    "ranked_hypotheses": [],
    "supporting_evidence_ids": [],
    "contradicting_evidence_ids": [],
    "selected_procedure_ids": [],
    "uncertainty": "",
    "requires_human_review": True,
}

MINIMAL_SYSTEM = (
    "You are SENTINEL, a constrained spacecraft fault diagnosis assistant. "
    "Return ONLY a JSON object. Do not output any text outside the JSON object."
)


def call_raw(messages: list[dict], max_tokens: int, use_json_mode: bool = True) -> dict:
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": TEMPERATURE,
        "max_tokens": max_tokens,
    }
    if use_json_mode:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer local"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=900) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    body["_wall_seconds"] = round(time.perf_counter() - t0, 3)
    return body


def extract_json(text: str) -> dict | None:
    """Mirror of the production _extract_json tolerance rules."""
    text = text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def analyse_trial(content: str, expected_keys: set[str]) -> dict:
    parsed = extract_json(content)
    first_key = None
    m = re.search(r'"(\w+)"\s*:', content)
    if m:
        first_key = m.group(1)
    schema_ok = False
    if parsed is not None:
        schema_ok = expected_keys.issubset(parsed.keys())
    return {
        "json_valid": parsed is not None,
        "schema_complete": schema_ok,
        "first_key": first_key,
        "response_chars": len(content),
        "parsed": parsed,
    }


def run_level(name: str, messages: list[dict], expected_keys: set[str],
              trials: int, max_tokens: int, use_json_mode: bool = True) -> dict:
    results = []
    for t in range(trials):
        print(f"  [{name}] trial {t + 1}/{trials} ...", flush=True)
        try:
            body = call_raw(messages, max_tokens=max_tokens, use_json_mode=use_json_mode)
            content = body["choices"][0]["message"]["content"]
            usage = body.get("usage", {})
            analysis = analyse_trial(content, expected_keys)
            results.append({
                "trial": t + 1,
                "json_valid": analysis["json_valid"],
                "schema_complete": analysis["schema_complete"],
                "first_key": analysis["first_key"],
                "finish_reason": body["choices"][0].get("finish_reason"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "wall_seconds": body["_wall_seconds"],
                "response_chars": analysis["response_chars"],
                "parsed": analysis["parsed"],
                "raw": content,
            })
        except Exception as exc:
            results.append({"trial": t + 1, "error": str(exc)})
    ok = sum(1 for r in results if r.get("schema_complete"))
    valid = sum(1 for r in results if r.get("json_valid"))
    return {
        "level": name,
        "trials": trials,
        "json_valid_rate": valid / trials if trials else 0.0,
        "schema_complete_rate": ok / trials if trials else 0.0,
        "results": results,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict = {"model": MODEL, "temperature": TEMPERATURE, "levels": {}}
    minimal_keys = set(MINIMAL_SCHEMA.keys())

    # ── Level A: minimal schema, empty lists ─────────────────────────────
    msgs_a = [
        {"role": "system", "content": MINIMAL_SYSTEM},
        {"role": "user", "content": (
            "Return ONLY this exact JSON object with no modifications:\n"
            + json.dumps(MINIMAL_SCHEMA, indent=2)
        )},
    ]
    report["levels"]["A_minimal_schema"] = run_level(
        "A", msgs_a, minimal_keys, trials=3, max_tokens=512)

    # ── Level B: schema + one hypothesis ─────────────────────────────────
    ctx_b = {
        "hypotheses": [{
            "fault_id": "ADCS_GYRO_SEU",
            "deterministic_rank": 1,
            "deterministic_score": 0.96,
            "supporting_evidence": [],
            "contradicting_evidence": [],
        }],
        "valid_fault_ids": ["ADCS_GYRO_SEU"],
    }
    msgs_b = [
        {"role": "system", "content": MINIMAL_SYSTEM + (
            " Output JSON with fields: ranked_hypotheses, "
            "supporting_evidence_ids, contradicting_evidence_ids, "
            "selected_procedure_ids, uncertainty, requires_human_review. "
            "ranked_hypotheses entries have fault_id, rank, confidence."
        )},
        {"role": "user", "content": (
            "Rank the provided hypothesis.\n" + json.dumps(ctx_b, indent=2)
        )},
    ]
    report["levels"]["B_one_hypothesis"] = run_level(
        "B", msgs_b, minimal_keys, trials=3, max_tokens=512)

    # ── Level C: schema + evidence IDs ───────────────────────────────────
    ctx_c = json.loads(json.dumps(ctx_b))
    ctx_c["hypotheses"][0]["supporting_evidence"] = [
        "EVID-429070d6c1d9", "EVID-5796888f538d",
    ]
    msgs_c = [
        {"role": "system", "content": MINIMAL_SYSTEM + (
            " Output JSON with fields: ranked_hypotheses, "
            "supporting_evidence_ids, contradicting_evidence_ids, "
            "selected_procedure_ids, uncertainty, requires_human_review. "
            "Evidence IDs must come from the input only."
        )},
        {"role": "user", "content": (
            "Rank the hypothesis and list the supporting evidence IDs.\n"
            + json.dumps(ctx_c, indent=2)
        )},
    ]
    report["levels"]["C_evidence_ids"] = run_level(
        "C", msgs_c, minimal_keys, trials=3, max_tokens=512)

    # ── Level D: schema + causal chain ───────────────────────────────────
    ctx_d = json.loads(json.dumps(ctx_c))
    ctx_d["hypotheses"][0]["causal_chain"] = [
        "SEU bitflip corrupts gyro register",
        "Gyro rate reads NaN",
        "Attitude error grows beyond threshold",
        "ADCS triggers safe mode",
    ]
    msgs_d = [
        {"role": "system", "content": MINIMAL_SYSTEM + (
            " Output JSON with fields: ranked_hypotheses, "
            "supporting_evidence_ids, contradicting_evidence_ids, "
            "selected_procedure_ids, uncertainty, requires_human_review. "
            "Each ranked hypothesis must include a causal_chain array."
        )},
        {"role": "user", "content": (
            "Rank the hypothesis, cite evidence IDs, and include the causal chain.\n"
            + json.dumps(ctx_d, indent=2)
        )},
    ]
    report["levels"]["D_causal_chain"] = run_level(
        "D", msgs_d, minimal_keys, trials=3, max_tokens=512)

    # ── Level E: full production prompt (S1) ─────────────────────────────
    from app.agent.rag import initialize_pdf_rag
    from app.api.scenarios import get_all_scenarios
    from app.detection import run_detection_on_crash_dump
    from app.diagnosis.candidates import generate_hypotheses
    from app.llm.ranker import build_constrained_prompt, build_ranking_input
    from app.procedures.retrieval import retrieve_procedures as p9_retrieve
    from app.validation.physics import validate_crash_dump

    initialize_pdf_rag()
    scenarios = {str(s["scenario_id"]): s for s in get_all_scenarios()}
    crash = scenarios["1"]
    det = run_detection_on_crash_dump(crash)
    hyp = generate_hypotheses(det, crash)
    physics, _, resid, seq = validate_crash_dump(crash)
    procs = p9_retrieve(
        query=crash.get("fault_type", ""),
        fault_cues=det.anomalous_channel_names() or None,
        fault_filter=hyp.top.fault_id if hyp.top else None,
        min_relevance=0.2,
    )
    ri = build_ranking_input(
        crash_dump=crash, anomaly_report=det, hypothesis_set=hyp,
        physics_report=physics, residual_report=resid,
        state_sequence=seq, procedure_results=procs,
    )
    msgs_e = build_constrained_prompt(ri)
    full_keys = minimal_keys | {"reasoning_summary"}
    report["levels"]["E_full_production_prompt"] = run_level(
        "E", msgs_e, full_keys, trials=1, max_tokens=1024)

    # ── Level F: control — same prompt, NO response_format ───────────────
    report["levels"]["F_no_response_format_control"] = run_level(
        "F", msgs_e, full_keys, trials=1, max_tokens=1024, use_json_mode=False)

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("STRUCTURED OUTPUT RELIABILITY BY COMPLEXITY LEVEL")
    print("=" * 70)
    for name, lvl in report["levels"].items():
        print(
            f"  {name:35s} json_valid={lvl['json_valid_rate']:.2f} "
            f"schema_ok={lvl['schema_complete_rate']:.2f} "
            f"(n={lvl['trials']})"
        )

    # strip bulky raw fields from the saved report except truncations
    for lvl in report["levels"].values():
        for r in lvl["results"]:
            if "raw" in r:
                r["raw_head"] = r["raw"][:300]
                r["raw_tail"] = r["raw"][-300:]
                del r["raw"]

    with open(OUT_DIR / "structured_output_experiment.json", "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nSaved to {OUT_DIR / 'structured_output_experiment.json'}")


if __name__ == "__main__":
    main()
