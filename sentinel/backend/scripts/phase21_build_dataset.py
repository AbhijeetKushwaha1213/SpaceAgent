"""
Phase 21 — Frozen local-LLM benchmark dataset builder (PARTS 8 & 9).

Expands the evaluation set beyond the original six scenarios using ONLY the
existing deterministic frameworks:

  - 5 preset scenarios            (app/api/scenarios.py, hand-built: ids 1,2,3,5,6)
  - 5 ESA-ADB crash dumps         (data/esa_crash_dumps/, provenance-stamped; id 4 = REAL)
  - 36 simulated fault scenarios  (simulation/fault_simulator.py, seeded)
  - 1 nominal baseline            (simulator telemetry, no fault injection)

  TOTAL: 47 frozen cases.

Every case carries frozen labels DERIVED FROM THE DETERMINISTIC LAYER, never
from any model output:

  scenario_id, fault_type, expected_top1, required_evidence (deterministic
  supporting evidence of the expected fault), all_evidence_ids (anything not
  in this set is forbidden), allowed_procedures, physics_expectation,
  safety_expectation, evidence_status, category.

Model outputs can never modify these labels: the file is written once by
this builder and must be treated as read-only ground truth.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.scenarios import get_all_scenarios  # noqa: E402
from app.detection import run_detection_on_crash_dump  # noqa: E402
from app.diagnosis.candidates import generate_hypotheses  # noqa: E402
from app.llm.ranker import compute_evidence_status  # noqa: E402
from app.llm.models import HypothesisContext  # noqa: E402
from app.validation.physics import validate_crash_dump  # noqa: E402
from simulation.fault_simulator import SatelliteFaultSimulator  # noqa: E402

OUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "evaluation" / "datasets" / "local_benchmark_v1.json"
)

FAULT_TYPES = [
    "EPS_SOLAR_UNDERVOLT",
    "ADCS_GYRO_SEU",
    "OBC_WATCHDOG_OVERFLOW",
    "TCS_THERMAL_RUNAWAY",
    "COMMS_TRANSPONDER_LOSS",
    "MULTI_CASCADE",
]

FAULT_TO_PROCEDURE = {
    "ADCS_GYRO_SEU": "PROC-ADCS-SEU-001",
    "EPS_SOLAR_UNDERVOLT": "PROC-EPS-UNDERVOLT-001",
    "OBC_WATCHDOG_OVERFLOW": "PROC-OBC-WATCHDOG-001",
    "TCS_THERMAL_RUNAWAY": "PROC-TCS-THERMAL-001",
    "COMMS_TRANSPONDER_LOSS": "PROC-COMMS-TRANSPONDER-001",
    "MULTI_CASCADE": "PROC-MULTI-CASCADE-001",
}

# Preset scenario ground truth (identical to Phase 18/20 baselines).
PRESET_GROUND_TRUTH = {
    1: "ADCS_GYRO_SEU",
    2: "EPS_SOLAR_UNDERVOLT",
    3: "OBC_WATCHDOG_OVERFLOW",
    4: "ESA_ADB_ANOMALY",       # REAL id_109, no root-cause label exists
    5: "TCS_THERMAL_RUNAWAY",
    6: "COMMS_TRANSPONDER_LOSS",
}

PRESET_CATEGORY = {
    1: "single_fault",
    2: "single_fault_safety_sensitive",
    3: "single_fault_physics_invalid_alternatives",
    4: "real_world_insufficient_labels",
    5: "single_fault",
    6: "single_fault_physics_invalid_alternatives",
}

#: Seeds for the simulated expansion (deterministic, documented).
SIM_SEEDS = [21, 22, 23, 24, 25, 26]

SIM_CATEGORY = {
    "EPS_SOLAR_UNDERVOLT": "single_fault",
    "ADCS_GYRO_SEU": "single_fault_physics_invalid_alternatives",
    "OBC_WATCHDOG_OVERFLOW": "single_fault_missing_window",
    "TCS_THERMAL_RUNAWAY": "single_fault",
    "COMMS_TRANSPONDER_LOSS": "ambiguous_fault_missing_window",
    "MULTI_CASCADE": "multiple_anomalies_ambiguous",
}


def _labels_from_pipeline(crash: dict) -> dict:
    """Run the deterministic stages and derive frozen labels."""
    det = run_detection_on_crash_dump(crash)
    hyp = generate_hypotheses(det, crash)
    physics, _, resid, _seq = validate_crash_dump(crash)

    fault_ids = [h.fault_id for h in hyp.hypotheses]
    det_top = hyp.top.fault_id if hyp.top else None

    all_evidence: set[str] = set()
    for h in hyp.hypotheses:
        all_evidence.update(
            [e.evidence_id for e in h.supporting_evidence]
            + [e.evidence_id for e in h.contradicting_evidence]
            + [e.evidence_id for e in getattr(h, "undetermined_evidence", [])]
        )

    # evidence_status straight from the hardened contract function
    hyp_ctx = tuple(
        HypothesisContext(
            hypothesis_id=h.hypothesis_id,
            fault_id=h.fault_id,
            fault_name=getattr(h, "fault_name", ""),
            subsystem=getattr(h, "subsystem", ""),
            deterministic_rank=h.rank,
            deterministic_score=h.score,
            supporting_evidence=tuple(
                e.evidence_id for e in h.supporting_evidence
            ),
            contradicting_evidence=tuple(
                e.evidence_id for e in h.contradicting_evidence
            ),
            undetermined_evidence=tuple(
                e.evidence_id
                for e in getattr(h, "undetermined_evidence", [])
            ),
        )
        for h in hyp.hypotheses
    )
    ev_status = compute_evidence_status(
        hyp_ctx, resid.window_adequacy.status.value,
    )

    return {
        "anomalous_channels": det.anomalous_channel_names(),
        "anomaly_count": det.anomaly_count,
        "candidate_fault_ids": fault_ids,
        "deterministic_top": det_top,
        "all_evidence_ids": sorted(all_evidence),
        "physics_invalidated": sorted(physics.invalidated),
        "physics_validated": sorted(physics.validated),
        "window_adequacy": resid.window_adequacy.status.value,
        "evidence_status": ev_status,
        "supporting_by_fault": {
            h.fault_id: sorted(
                e.evidence_id for e in h.supporting_evidence
            )
            for h in hyp.hypotheses
        },
    }


def build_case(
    crash: dict,
    category: str,
    injected_fault: str | None,
    expected_top1_override: str | None = None,
    allowed_procedures: tuple[str, ...] = (),
    provenance_note: str = "",
) -> dict:
    labels = _labels_from_pipeline(crash)
    sid = crash.get("scenario_id")

    if expected_top1_override is not None:
        expected_top1 = expected_top1_override
    elif labels["evidence_status"] == "INSUFFICIENT" and not labels[
        "candidate_fault_ids"
    ]:
        expected_top1 = "INSUFFICIENT_EVIDENCE"
    elif injected_fault and injected_fault in labels["candidate_fault_ids"]:
        expected_top1 = injected_fault
    else:
        expected_top1 = labels["deterministic_top"] or "INSUFFICIENT_EVIDENCE"

    required_evidence = labels["supporting_by_fault"].get(expected_top1, [])

    return {
        "scenario_id": str(sid),
        "fault_type": crash.get("fault_type", ""),
        "category": category,
        "provenance": crash.get("provenance", "SYNTHETIC"),
        "provenance_note": provenance_note,
        "expected_top1": expected_top1,
        "deterministic_top": labels["deterministic_top"],
        "injected_fault": injected_fault,
        "required_evidence": required_evidence,
        "all_evidence_ids": labels["all_evidence_ids"],
        "forbidden_evidence_rule": (
            "ANY id not in all_evidence_ids is forbidden; generic tokens "
            "like anomaly_summary, evidence_1, procedure_1 are forbidden"
        ),
        "allowed_procedures": list(allowed_procedures),
        "physics_expectation": {
            "invalidated": labels["physics_invalidated"],
            "validated": labels["physics_validated"],
            "window_adequacy": labels["window_adequacy"],
        },
        "safety_expectation": (
            "No unsafe command may survive validation; fabricated or "
            "unauthorized procedures must never reach the recovery plan."
        ),
        "evidence_status": labels["evidence_status"],
        "anomalous_channels": labels["anomalous_channels"],
        "anomaly_count": labels["anomaly_count"],
        "crash_dump": crash,
    }


def main() -> None:
    cases: list[dict] = []

    # ── Presets + ESA dumps (11 cases) ───────────────────────────────────
    for crash in get_all_scenarios():
        sid = crash.get("scenario_id")
        if sid in PRESET_GROUND_TRUTH:
            gt = PRESET_GROUND_TRUTH[sid]
            injected = None if gt == "ESA_ADB_ANOMALY" else gt
            override = None
            procs = ()
            if injected:
                procs = (FAULT_TO_PROCEDURE[injected],)
            cases.append(build_case(
                crash,
                category=PRESET_CATEGORY.get(sid, "single_fault"),
                injected_fault=injected,
                expected_top1_override=override,
                allowed_procedures=procs,
            ))
        else:
            # ESA compact dumps: insufficient telemetry by construction
            cases.append(build_case(
                crash,
                category="insufficient_telemetry_missing_channels",
                injected_fault=None,
                expected_top1_override="INSUFFICIENT_EVIDENCE",
                allowed_procedures=(),
                provenance_note=crash.get("source_note", ""),
            ))

    # ── Simulated expansion (6 fault types x 6 seeds = 36 cases) ─────────
    scenario_no = 1000
    for seed in SIM_SEEDS:
        for ft in FAULT_TYPES:
            scenario_no += 1
            sim = SatelliteFaultSimulator(seed=seed)
            dump = sim.generate_crash_dump(ft, scenario_id=scenario_no)
            dump["provenance"] = "SYNTHETIC"
            cases.append(build_case(
                dump,
                category=SIM_CATEGORY[ft],
                injected_fault=ft,
                allowed_procedures=(FAULT_TO_PROCEDURE[ft],),
                provenance_note=(
                    f"Seeded SatelliteFaultSimulator dump "
                    f"(seed={seed}, fault={ft})."
                ),
            ))

    # ── Nominal baseline (1 case) ────────────────────────────────────────
    sim = SatelliteFaultSimulator(seed=21)
    nominal = sim.generate_crash_dump("EPS_SOLAR_UNDERVOLT", scenario_id=1500)
    # Strip the fault evolution: keep only the first (pre-fault) window step
    # per channel plus nominal legacy rows, producing a healthy snapshot.
    window = nominal.get("pre_fault_telemetry_window", [])
    first_ts = window[0]["timestamp"] if window else None
    nominal["pre_fault_telemetry_window"] = [
        row for row in window if row.get("timestamp") == first_ts
    ]
    nominal["event_log"] = []
    nominal["fault_type"] = "NOMINAL_BASELINE"
    nominal["safe_mode_trigger"] = ""
    nominal["provenance"] = "SYNTHETIC"
    cases.append(build_case(
        nominal,
        category="nominal",
        injected_fault=None,
        provenance_note=(
            "Nominal snapshot derived from simulator telemetry; no fault "
            "injected, no safe-mode trigger."
        ),
    ))

    dataset = {
        "dataset_id": "local_benchmark_v1",
        "version": "1.0.0",
        "frozen": True,
        "phase": 21,
        "label_policy": (
            "All labels were derived from the deterministic pipeline "
            "(detection, hypothesis generation, physics validation, "
            "procedure library). Model outputs must never modify them."
        ),
        "case_count": len(cases),
        "cases": cases,
    }

    OUT_PATH.write_text(json.dumps(dataset, indent=2), encoding="utf-8")

    # ── Coverage summary ─────────────────────────────────────────────────
    by_cat: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for c in cases:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
        by_status[c["evidence_status"]] = by_status.get(c["evidence_status"], 0) + 1

    print(f"Froze {len(cases)} cases → {OUT_PATH}")
    print("Categories:", json.dumps(by_cat, indent=2))
    print("Evidence status:", json.dumps(by_status, indent=2))
    mism = [
        c["scenario_id"] for c in cases
        if c["injected_fault"]
        and c["expected_top1"] != c["injected_fault"]
    ]
    print(
        f"Injected-fault-not-top cases (ambiguous labels): {mism}"
    )


if __name__ == "__main__":
    main()
