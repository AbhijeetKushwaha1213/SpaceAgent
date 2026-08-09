"""run_evaluation.py

SENTINEL evaluation runner — supports two modes:

  --mode file   Score pre-generated candidate JSONL files through the
                evaluator. No live LLM calls. Good for checkpoint comparison.

  --mode live   Generate held-out synthetic crash dumps using
                SatelliteFaultSimulator, run SentinelAgent.analyze_with_rag()
                against them, and score the results. Requires GEMINI_API_KEY.

Ablation configs (--config):
  full          Full pipeline: anomaly detector + PDF RAG + safety validator
  no-rag        Fallback KB only; no PDF RAG
  no-safety     Skip deterministic safety validation (uses skip_safety=True)
  base-model    Minimal system prompt; no domain-specific instructions

Output
------
  sentinel/backend/results/<candidate>_results.json      per-candidate
  sentinel/backend/results/comparison_summary.json       ranked leaderboard

Usage examples
--------------
  # File-mode (default): score JSONL candidate files
  cd sentinel/backend
  python -m app.analytics.run_evaluation --mode file --candidates-dir data/

  # Live-mode: run live agent on 6 held-out dumps (requires GEMINI_API_KEY)
  cd sentinel/backend
  python -m app.analytics.run_evaluation --mode live --n-samples 6

  # Ablation: live mode without safety
  cd sentinel/backend
  python -m app.analytics.run_evaluation --mode live --config no-safety

Imports
-------
Always run from the sentinel/backend/ root so `app.*` imports resolve:
  cd sentinel/backend && python -m app.analytics.run_evaluation ...
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Path bootstrap — allow both:
#   python -m app.analytics.run_evaluation  (from sentinel/backend)
#   python run_evaluation.py               (from analytics/ directly)
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

# Load .env so GEMINI_API_KEY is available for live evaluation
try:
    from dotenv import load_dotenv
    for _env_path in [
        os.path.join(_BACKEND_ROOT, ".env"),
        os.path.join(_BACKEND_ROOT, "..", ".env"),
    ]:
        if os.path.isfile(_env_path):
            load_dotenv(_env_path, override=False)
            break
except ImportError:
    pass  # dotenv is optional for file-mode evaluation

from app.analytics.evaluator import (      # noqa: E402
    SentinelEvaluator,
    GROUND_TRUTH_REGISTRY,
    DEMO_FAULT_TYPES,
    evaluate_response,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default output directory (relative to sentinel/backend/)
DEFAULT_OUTPUT_DIR = os.path.join(_BACKEND_ROOT, "results")

# JSONL files to exclude from auto-discovery (training data, splits)
AUTO_DISCOVERY_EXCLUDE: set[str] = {
    "sentinel_training.jsonl",
    "train.jsonl",
    "valid.jsonl",
    "test_output.jsonl",
}

# Canonical fault types used for live evaluation held-out set
_CANONICAL_FAULT_TYPES: list[str] = list(GROUND_TRUTH_REGISTRY.keys())

# Fault-type regex for extracting labels from message content
_FAULT_TYPE_RE = re.compile(
    r"\b(EPS_SOLAR_UNDERVOLT|ADCS_GYRO_SEU|OBC_WATCHDOG_OVERFLOW"
    r"|TCS_THERMAL_RUNAWAY|COMMS_TRANSPONDER_LOSS|MULTI_CASCADE)\b"
)

# Fault-register → fault-type mapping (fallback when no label field present)
_FAULT_REGISTER_MAP: dict[str, str] = {
    "0x00000002": "EPS_SOLAR_UNDERVOLT",
    "0x00000042": "ADCS_GYRO_SEU",
    "0x00000040": "OBC_WATCHDOG_OVERFLOW",
    "0x00000010": "TCS_THERMAL_RUNAWAY",
    "0x00000008": "COMMS_TRANSPONDER_LOSS",
    "0x00000046": "MULTI_CASCADE",
}

# Candidate registry (edit to add registered checkpoint files)
CANDIDATE_FILES: list[dict[str, Any]] = [
    # {
    #   "name": "base_gemini",
    #   "path": "data/base_gemini_responses.jsonl",
    #   "latency_col": "latency_ms",
    # },
]

# Ablation config → (use_pdf_rag, skip_safety, system_prompt_override_key)
_ABLATION_CONFIGS: dict[str, dict[str, Any]] = {
    "full": {
        "use_pdf_rag": True,
        "skip_safety": False,
        "system_prompt_override": None,
        "description": "Full pipeline: anomaly detector + PDF RAG + safety validator",
    },
    "no-rag": {
        "use_pdf_rag": False,
        "skip_safety": False,
        "system_prompt_override": None,
        "description": "Fallback KB only, no PDF RAG",
    },
    "no-safety": {
        "use_pdf_rag": True,
        "skip_safety": True,
        "system_prompt_override": None,
        "description": "Full RAG, safety validator bypassed",
    },
    "base-model": {
        "use_pdf_rag": False,
        "skip_safety": False,
        "system_prompt_override": (
            "You are a spacecraft fault diagnosis assistant. "
            "Analyze the crash dump and respond with JSON."
        ),
        "description": "Minimal system prompt, no domain-specific instructions, fallback KB",
    },
}


# ---------------------------------------------------------------------------
# JSONL parsing helpers (file mode)
# ---------------------------------------------------------------------------

def _extract_fault_type(record: dict) -> Optional[str]:
    """Try to determine the ground-truth fault type from a JSONL record."""
    # 1. Explicit field
    for key in ("true_fault_type", "fault_type", "label", "ground_truth"):
        if key in record and record[key] in GROUND_TRUTH_REGISTRY:
            return record[key]

    # 2. Messages list (training / chat format)
    for msg in record.get("messages", []):
        content = msg.get("content", "")
        if msg.get("role") == "assistant":
            try:
                parsed = json.loads(content)
                hyps = parsed.get("hypotheses", [])
                rank1 = next((h for h in hyps if h.get("rank") == 1), None)
                if rank1 and rank1.get("root_cause") in GROUND_TRUTH_REGISTRY:
                    return rank1["root_cause"]
            except (json.JSONDecodeError, TypeError):
                pass
        if msg.get("role") == "user":
            fr_match = re.search(r"Fault Register:\s*(0x[0-9A-Fa-f]+)", content)
            if fr_match:
                ft = _FAULT_REGISTER_MAP.get(fr_match.group(1).lower())
                if ft:
                    return ft
            ft_match = _FAULT_TYPE_RE.search(content)
            if ft_match:
                return ft_match.group(1)

    # 3. Direct fault_register field
    fr = record.get("fault_register", "")
    if fr in _FAULT_REGISTER_MAP:
        return _FAULT_REGISTER_MAP[fr]

    return None


def _extract_response(record: dict) -> Optional[str]:
    """Extract the raw model response string from a JSONL record."""
    for key in ("response", "assistant_response", "output", "completion"):
        if key in record:
            val = record[key]
            return val if isinstance(val, str) else json.dumps(val)

    for msg in record.get("messages", []):
        if msg.get("role") == "assistant":
            return msg.get("content", "")

    return None


def _extract_latency(record: dict, latency_col: str = "latency_ms") -> Optional[float]:
    """Try to read latency_ms from the record."""
    val = record.get(latency_col) or record.get("latency_ms") or record.get("latency")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _sanitise_name(path: str) -> str:
    """Turn a file path into a safe candidate name."""
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[^A-Za-z0-9_\-]", "_", stem)
    stem = re.sub(r"_(responses?|outputs?|results?)$", "", stem, flags=re.IGNORECASE)
    return stem


# ---------------------------------------------------------------------------
# Candidate discovery (file mode)
# ---------------------------------------------------------------------------

def discover_candidates(candidates_dir: str = ".") -> list[dict[str, Any]]:
    """Return the merged list of registered + auto-discovered JSONL candidates."""
    known_paths = {c["path"] for c in CANDIDATE_FILES}
    auto: list[dict[str, Any]] = []

    if not os.path.isdir(candidates_dir):
        return CANDIDATE_FILES

    for fname in sorted(os.listdir(candidates_dir)):
        if not fname.endswith(".jsonl"):
            continue
        if fname in AUTO_DISCOVERY_EXCLUDE:
            continue
        full = os.path.join(candidates_dir, fname)
        if full in known_paths or fname in known_paths:
            continue
        auto.append({
            "name": _sanitise_name(fname),
            "path": full,
            "latency_col": "latency_ms",
        })

    return CANDIDATE_FILES + auto


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------

def _compute_safety_alignment(
    response_str: str,
    crash_dump: dict,
) -> Optional[float]:
    """Compute safety alignment score for one response.

    Safety alignment = fraction of recovery steps that pass the deterministic
    whitelist and constraint checks in safety.py.

    Returns None if the response cannot be parsed (never returns a fake number).
    Only called when safety.py is available.
    """
    try:
        from app.agent.safety import validate_recovery_plan
        from app.api.models import SafetyStatus, SentinelOutput

        parsed = json.loads(response_str)
        output = SentinelOutput.model_validate(parsed)
        result = validate_recovery_plan(output, crash_dump)

        total = len(result.validated_steps) + len(result.blocked_steps)
        if total == 0:
            # Phase 1: an empty plan is no longer necessarily "safe". A plan can
            # legitimately be empty when safety_status is BLOCKED, i.e. every
            # step was rejected — the worst possible alignment, not the best.
            # Proposing nothing at all also cannot be scored as perfect.
            if output.safety_status is SafetyStatus.BLOCKED:
                return 0.0
            return None  # nothing was proposed; no alignment to measure
        return len(result.validated_steps) / total

    except Exception:
        return None


# ---------------------------------------------------------------------------
# FILE MODE
# ---------------------------------------------------------------------------

def evaluate_candidate_file(
    candidate: dict[str, Any],
    output_dir: str,
    verbose: bool = False,
) -> Optional[dict[str, Any]]:
    """Score one JSONL candidate file through the evaluator.

    Returns the report dict, or None if the file could not be read.
    """
    name        = candidate["name"]
    path        = candidate["path"]
    latency_col = candidate.get("latency_col", "latency_ms")

    if not os.path.exists(path):
        print(f"  [SKIP] {name}: file not found — {path}")
        return None

    evaluator = SentinelEvaluator(name)
    safety_scores: list[float] = []
    n_skipped  = 0
    n_total    = 0

    with open(path, "r", encoding="utf-8") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            n_total += 1

            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                if verbose:
                    print(f"    Line {line_no}: JSON parse error — {exc}")
                n_skipped += 1
                continue

            fault_type = _extract_fault_type(record)
            response   = _extract_response(record)
            latency    = _extract_latency(record, latency_col)

            if fault_type is None:
                if verbose:
                    print(f"    Line {line_no}: could not determine fault type — skipped")
                n_skipped += 1
                continue

            if response is None:
                if verbose:
                    print(f"    Line {line_no}: could not extract response — treated as malformed")
                response = ""

            evaluator.add(response, fault_type, latency_ms=latency)

            # Optional safety alignment (only when response is valid JSON)
            if response:
                crash_ctx = {
                    k: v for k, v in record.items()
                    if k not in ("messages", "response", "latency_ms", "true_fault_type")
                }
                sa = _compute_safety_alignment(response, crash_ctx)
                if sa is not None:
                    safety_scores.append(sa)

    if not evaluator._scores:
        print(f"  [WARN] {name}: no scorable responses found in {path}")
        return None

    report = evaluator.report()
    report["source_file"]         = path
    report["lines_total"]         = n_total
    report["lines_skipped"]       = n_skipped
    report["evaluation_mode"]     = "file"

    # Safety alignment — only if we computed real numbers
    if safety_scores:
        report["safety_alignment"] = round(sum(safety_scores) / len(safety_scores), 4)
    # (Never write a placeholder if safety_scores is empty)

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{name}_results.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    _print_summary(report, out_path, verbose)
    return report


def _run_self_eval(output_dir: str, verbose: bool) -> Optional[dict]:
    """Score sentinel_training.jsonl against itself as a smoke test.

    This gives a ~perfect baseline (ground-truth responses evaluated against
    ground-truth labels) to confirm the evaluator pipeline is working correctly.
    The score will be high by design — it is NOT an honest held-out accuracy.
    """
    # Try both relative and absolute paths
    training_candidates = [
        os.path.join(_BACKEND_ROOT, "data", "sentinel_training.jsonl"),
        "sentinel_training.jsonl",
        os.path.join("data", "sentinel_training.jsonl"),
    ]
    training_file = next((p for p in training_candidates if os.path.exists(p)), None)
    if training_file is None:
        return None

    print(f"\nRunning self-evaluation on {training_file} ...")
    print("  NOTE: Training-set self-eval measures evaluator correctness, not model accuracy.")
    candidate = {
        "name":        "ground_truth_baseline",
        "path":        training_file,
        "latency_col": "latency_ms",
    }
    return evaluate_candidate_file(candidate, output_dir, verbose=verbose)


# ---------------------------------------------------------------------------
# LIVE MODE
# ---------------------------------------------------------------------------

def run_live_evaluation(
    output_dir: str,
    config_name: str = "full",
    n_samples: int = 6,
    seed: int = 42,
    verbose: bool = False,
) -> Optional[dict[str, Any]]:
    """Run the live agent on held-out synthetic crash dumps and score it.

    Generates one crash dump per canonical fault type (n_samples controls
    total; minimum 6 to cover all 6 fault types).

    Requires GEMINI_API_KEY in the environment.  If the key is missing,
    this function prints an honest warning and returns None.

    Args:
        output_dir: Directory to write result JSON.
        config_name: Ablation config key (see _ABLATION_CONFIGS).
        n_samples: Number of held-out crash dumps to generate.
            At least 6 to cover all canonical fault types.
        seed: RNG seed for the fault simulator (held-out seed ≠ training seed).
        verbose: Print per-fault breakdown.

    Returns:
        Report dict, or None if the evaluation could not be run.
    """
    import os

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(
            "\n[LIVE MODE] GEMINI_API_KEY not found in environment.\n"
            "  Live evaluation requires the API key.\n"
            "  Set it in sentinel/.env or export GEMINI_API_KEY=<key> and retry.\n"
            "  This code path is fully implemented — only the key is missing."
        )
        return None

    config = _ABLATION_CONFIGS.get(config_name, _ABLATION_CONFIGS["full"])
    candidate_name = f"live_{config_name}"
    print(f"\n[LIVE MODE] Config: {config_name} — {config['description']}")
    print(f"  Generating {max(n_samples, 6)} held-out crash dumps (seed={seed}) ...")

    try:
        from simulation.fault_simulator import SatelliteFaultSimulator
        from app.agent.agent import SentinelAgent, AgentConfig
    except ImportError as exc:
        print(f"  [ERROR] Import failed: {exc}")
        return None

    sim   = SatelliteFaultSimulator(seed=seed)
    agent = SentinelAgent(AgentConfig())

    evaluator = SentinelEvaluator(candidate_name)
    safety_scores: list[float] = []
    n_total   = max(n_samples, 6)
    n_failed  = 0

    # Round-robin over canonical fault types
    fault_cycle = _CANONICAL_FAULT_TYPES * (n_total // len(_CANONICAL_FAULT_TYPES) + 1)
    fault_cycle = fault_cycle[:n_total]

    for i, fault_type in enumerate(fault_cycle):
        crash_dump = sim.generate_crash_dump(fault_type, scenario_id=1000 + i)

        if verbose:
            print(f"  [{i+1}/{n_total}] Running {fault_type} ...", end="", flush=True)

        t0 = time.time()
        try:
            result = agent.analyze_with_rag(
                crash_dump=crash_dump,
                use_pdf_rag=config["use_pdf_rag"],
                skip_safety=config["skip_safety"],
                system_prompt_override=config["system_prompt_override"],
            )
            latency_ms = (time.time() - t0) * 1000

            response_str = result.model_dump_json()
            evaluator.add(response_str, fault_type, latency_ms=latency_ms)

            # Safety alignment (only if safety was applied)
            if not config["skip_safety"]:
                sa = _compute_safety_alignment(response_str, crash_dump)
                if sa is not None:
                    safety_scores.append(sa)

            if verbose:
                h1 = result.hypotheses[0] if result.hypotheses else None
                correct = h1 and h1.root_cause == fault_type
                print(f" {'✓' if correct else '✗'}  conf={result.confidence:.2f}  "
                      f"rank1={h1.root_cause if h1 else 'N/A'}")

        except Exception as exc:
            n_failed += 1
            if verbose:
                print(f" ERROR: {exc}")
            else:
                print(f"  [{i+1}/{n_total}] {fault_type}: FAILED — {exc}")
            # Count as malformed response for fair scoring
            evaluator.add("", fault_type, latency_ms=None)

    if not evaluator._scores:
        print(f"  [WARN] No responses scored for live evaluation ({n_failed} failed).")
        return None

    report = evaluator.report()
    report["evaluation_mode"]  = "live"
    report["ablation_config"]  = config_name
    report["config_description"] = config["description"]
    report["n_failed"]         = n_failed
    report["simulator_seed"]   = seed

    if safety_scores:
        report["safety_alignment"] = round(sum(safety_scores) / len(safety_scores), 4)
    # (No placeholder if safety_scores is empty)

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{candidate_name}_results.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    _print_summary(report, out_path, verbose)
    return report


# ---------------------------------------------------------------------------
# Shared output helpers
# ---------------------------------------------------------------------------

def _print_summary(report: dict, out_path: str, verbose: bool) -> None:
    """Print a one-line (or verbose) summary for a completed evaluation."""
    c    = report["candidate"]
    n    = report["n_samples"]
    acc  = report["fault_class_accuracy"]
    cal  = report["confidence_calibration"]
    rec  = report["recovery_plan_adequacy"]
    jvr  = report["json_validity_rate"]
    mfr  = report["retry_malformed_rate"]
    lat  = report.get("mean_latency_ms")
    demo = report["demo_scenario_success_rate"]
    rhr  = report["requires_human_review_correct"]
    sa   = report.get("safety_alignment")  # None if not computed

    lat_str = f"{lat:.1f}ms" if lat is not None else "N/A"
    sa_str  = f"{sa:.3f}" if sa is not None else "N/A"

    print(f"  [{c}]  n={n}  acc={acc:.3f}  cal={cal:.3f}  "
          f"rec={rec:.3f}  jvr={jvr:.3f}  mfr={mfr:.3f}  "
          f"rhr={rhr:.3f}  demo={demo:.3f}  lat={lat_str}  safety_align={sa_str}")
    print(f"    → {out_path}")

    if verbose:
        print("    Per-fault breakdown:")
        for ft, ft_stats in report.get("per_fault_type", {}).items():
            print(f"      {ft:<25s}  acc={ft_stats['fault_class_accuracy']:.2f}  "
                  f"rec={ft_stats['recovery_plan_adequacy']:.2f}  "
                  f"demo={ft_stats['demo_scenario_pass_rate']:.2f}")


def write_comparison_summary(
    reports: list[dict[str, Any]],
    output_dir: str,
) -> None:
    """Write a ranked comparison of all candidates to comparison_summary.json."""
    if not reports:
        return

    ranked = sorted(
        reports,
        key=lambda r: (r["fault_class_accuracy"], r["demo_scenario_success_rate"]),
        reverse=True,
    )

    summary = {
        "total_candidates": len(ranked),
        "ranking": [
            {
                "rank":                          i + 1,
                "candidate":                     r["candidate"],
                "evaluation_mode":               r.get("evaluation_mode", "unknown"),
                "ablation_config":               r.get("ablation_config"),
                "n_samples":                     r["n_samples"],
                "fault_class_accuracy":          r["fault_class_accuracy"],
                "confidence_calibration":        r["confidence_calibration"],
                "requires_human_review_correct": r["requires_human_review_correct"],
                "recovery_plan_adequacy":        r["recovery_plan_adequacy"],
                "json_validity_rate":            r["json_validity_rate"],
                "retry_malformed_rate":          r["retry_malformed_rate"],
                "mean_latency_ms":               r.get("mean_latency_ms"),
                "demo_scenario_success_rate":    r["demo_scenario_success_rate"],
                "safety_alignment":              r.get("safety_alignment"),
            }
            for i, r in enumerate(ranked)
        ],
    }

    out_path = os.path.join(output_dir, "comparison_summary.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"\nComparison summary written to {out_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "SENTINEL evaluation runner.\n"
            "File mode: score pre-generated JSONL candidate files.\n"
            "Live mode: run the live agent on held-out synthetic crash dumps."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=["file", "live"],
        default="file",
        help=(
            "Evaluation mode: 'file' scores candidate JSONL files; "
            "'live' runs the live agent (requires GEMINI_API_KEY). "
            "Default: file"
        ),
    )
    parser.add_argument(
        "--config",
        choices=list(_ABLATION_CONFIGS.keys()),
        default="full",
        help=(
            "Ablation config for live mode: "
            "full (default), no-rag, no-safety, base-model. "
            "Ignored in file mode."
        ),
    )
    parser.add_argument(
        "--candidates-dir",
        default=os.path.join(_BACKEND_ROOT, "data"),
        help="Directory to scan for candidate JSONL files (file mode only).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to write result JSONs. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--n-samples",
        type=int,
        default=6,
        help="Number of held-out crash dumps for live evaluation (min 6). Default: 6",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed for held-out crash dump generation (live mode). Default: 42",
    )
    parser.add_argument(
        "--self-eval",
        action="store_true",
        default=True,
        help=(
            "Score sentinel_training.jsonl as a ground-truth smoke test (file mode). "
            "Default: on"
        ),
    )
    parser.add_argument(
        "--no-self-eval",
        dest="self_eval",
        action="store_false",
        help="Skip the ground-truth self-evaluation (file mode).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print per-fault-type breakdowns.",
    )
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    reports: list[dict] = []

    if args.mode == "live":
        print(f"\n=== SENTINEL Live Evaluation (config={args.config}) ===")
        r = run_live_evaluation(
            output_dir=args.output_dir,
            config_name=args.config,
            n_samples=args.n_samples,
            seed=args.seed,
            verbose=args.verbose,
        )
        if r:
            reports.append(r)

    else:  # file mode
        print(f"\n=== SENTINEL File Evaluation ===")
        print(f"Scanning candidates in: {args.candidates_dir}")

        # Ground-truth self-evaluation first (smoke test)
        if args.self_eval:
            r = _run_self_eval(args.output_dir, args.verbose)
            if r:
                reports.append(r)

        # Registered + auto-discovered candidates
        candidates = discover_candidates(args.candidates_dir)
        if candidates:
            print(f"\nEvaluating {len(candidates)} candidate file(s) ...")
            for cand in candidates:
                r = evaluate_candidate_file(cand, args.output_dir, verbose=args.verbose)
                if r:
                    reports.append(r)
        else:
            if not args.self_eval:
                print(
                    "No candidate JSONL files found. "
                    "Add entries to CANDIDATE_FILES in run_evaluation.py, "
                    "or drop response JSONL files into the candidates directory."
                )

    if reports:
        write_comparison_summary(reports, args.output_dir)
        print(f"\n{len(reports)} result file(s) written to {args.output_dir}/")
    else:
        print("\nNo results to write.")
        sys.exit(1)


if __name__ == "__main__":
    main()
