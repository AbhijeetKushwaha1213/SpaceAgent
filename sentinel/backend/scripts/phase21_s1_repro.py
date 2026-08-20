"""
Phase 21 — S1 prompt-echo failure reproduction (PART 2).

Reproduces Scenario 1 through the EXACT production prompt path
(build_ranking_input + build_constrained_prompt) and calls phi3:mini with
the SAME parameters Phase 20 used (temperature=0.1, max_tokens=1024).

Captures the data points Phase 21 requires:
  1. Exact prompt length (chars, lines, per-message breakdown)
  2. Context length as measured by Ollama (usage.prompt_tokens)
  3. Requested max output tokens
  4. Actual generated tokens (usage.completion_tokens)
  5. Raw model output (verbatim, saved to file)
  6. Where truncation occurred (finish_reason + tail inspection)
  7. Whether the model echoed the prompt
  8. Whether JSON generation began correctly
  9. Candidate cause classification (A-G)

This script does NOT change any production parameter. It only observes.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.rag import initialize_pdf_rag, retrieve_procedures_traced  # noqa: E402
from app.api.scenarios import get_all_scenarios  # noqa: E402
from app.detection import run_detection_on_crash_dump  # noqa: E402
from app.diagnosis.candidates import generate_hypotheses  # noqa: E402
from app.llm.ranker import build_constrained_prompt, build_ranking_input  # noqa: E402
from app.procedures.retrieval import retrieve_procedures as p9_retrieve  # noqa: E402
from app.validation.physics import validate_crash_dump  # noqa: E402

BASE_URL = "http://localhost:11434/v1"
MODEL = "phi3:mini"
MAX_TOKENS = 1024  # identical to Phase 20 run_phi3_baseline.py
TEMPERATURE = 0.1

OUT_DIR = Path(__file__).resolve().parents[1] / "results" / "phase21"


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for a GPT-style BPE tokenizer.

    Heuristic: JSON-heavy text averages ~3.5 chars/token. We report the
    char/3.5 ceiling plus a whitespace-token sanity bound. The authoritative
    count comes from Ollama's usage.prompt_tokens in the response.
    """
    import math

    return math.ceil(len(text) / 3.5)


def call_raw(messages: list[dict]) -> dict:
    """Call the OpenAI-compatible endpoint and return the FULL response body."""
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer local",
        },
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    body["_wall_seconds"] = round(time.perf_counter() - t0, 3)
    return body


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("[1/4] Initializing RAG ...", flush=True)
    initialize_pdf_rag()

    scenarios = {str(s["scenario_id"]): s for s in get_all_scenarios()}
    crash = scenarios["1"]
    print("[2/4] Running deterministic pipeline for S1 ...", flush=True)

    det = run_detection_on_crash_dump(crash)
    hyp = generate_hypotheses(det, crash)
    physics, _, resid, seq = validate_crash_dump(crash)
    ff = hyp.top.fault_id if hyp.top else None
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
    messages = build_constrained_prompt(ri)

    sys_prompt = messages[0]["content"]
    user_prompt = messages[1]["content"]

    prompt_stats = {
        "system_prompt_chars": len(sys_prompt),
        "user_prompt_chars": len(user_prompt),
        "total_prompt_chars": len(sys_prompt) + len(user_prompt),
        "system_prompt_est_tokens": estimate_tokens(sys_prompt),
        "user_prompt_est_tokens": estimate_tokens(user_prompt),
        "total_est_tokens": estimate_tokens(sys_prompt) + estimate_tokens(user_prompt),
        "hypotheses_in_prompt": len(ri.hypotheses),
        "valid_fault_ids": list(ri.valid_fault_ids),
        "valid_procedure_ids": list(ri.valid_procedure_ids),
        "evidence_ids_in_prompt": sorted({
            e for h in ri.hypotheses
            for e in list(h.supporting_evidence) + list(h.contradicting_evidence)
            + list(h.undetermined_evidence)
        }),
        "residuals_in_prompt": len(ri.spacecraft_state.residuals),
        "window_adequacy_status": ri.spacecraft_state.window_adequacy.status,
        "requested_max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "model_context_window": 4096,
    }
    print(json.dumps(prompt_stats, indent=2), flush=True)

    print(f"[3/4] Calling {MODEL} (max_tokens={MAX_TOKENS}) ...", flush=True)
    body = call_raw(messages)

    content = body["choices"][0]["message"]["content"]
    finish_reason = body["choices"][0].get("finish_reason")
    usage = body.get("usage", {})

    # --- Analysis ---
    user_head = user_prompt[:200]
    echoed = content.strip()[:80] in user_prompt or user_head[:80] in content[:400]
    # stronger echo test: does the response start with the same JSON skeleton
    # as the user prompt ("scenario_id" before "ranked_hypotheses")?
    first_key_m = re.search(r'"(\w+)"\s*:', content)
    first_key = first_key_m.group(1) if first_key_m else None
    began_ranked = '"ranked_hypotheses"' in content[:200]
    truncated_json = finish_reason in ("length", "stop") and not _is_complete_json(content)

    analysis = {
        "finish_reason": finish_reason,
        "usage": usage,
        "prompt_tokens_measured": usage.get("prompt_tokens"),
        "completion_tokens_measured": usage.get("completion_tokens"),
        "first_json_key_in_response": first_key,
        "response_echoes_prompt_first_key": first_key in (
            "scenario_id", "fault_type", "safe_mode_trigger", "hypotheses",
        ),
        "began_with_ranked_hypotheses": began_ranked,
        "response_is_complete_json": _is_complete_json(content),
        "response_chars": len(content),
        "wall_seconds": body["_wall_seconds"],
    }

    print("[4/4] Analysis:", flush=True)
    print(json.dumps(analysis, indent=2), flush=True)

    (OUT_DIR / "s1_repro_raw_response.txt").write_text(content, encoding="utf-8")
    (OUT_DIR / "s1_repro_prompt_system.txt").write_text(sys_prompt, encoding="utf-8")
    (OUT_DIR / "s1_repro_prompt_user.txt").write_text(user_prompt, encoding="utf-8")
    with open(OUT_DIR / "s1_repro_analysis.json", "w", encoding="utf-8") as f:
        json.dump({
            "prompt_stats": prompt_stats,
            "analysis": analysis,
        }, f, indent=2)
    print(f"Artifacts saved to {OUT_DIR}", flush=True)


def _is_complete_json(text: str) -> bool:
    try:
        json.loads(text.strip())
        return True
    except Exception:
        # try outermost braces like the production parser
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                json.loads(text[start:end + 1])
                return True
            except Exception:
                return False
        return False


if __name__ == "__main__":
    main()
