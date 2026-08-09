#!/usr/bin/env python3
"""
SENTINEL — Worked audit-trail example (scripts/example_audit_run.py)

Phase 4. Runs one complete investigation through the real pipeline, persists the
audit record, and prints exactly what was stored.

    python3 scripts/example_audit_run.py
    python3 scripts/example_audit_run.py --db /tmp/example.sqlite3 --json

What is real here and what is not
---------------------------------
Real: canonical telemetry resolution, the Phase 2 detection pipeline, procedure
retrieval with source attribution, output schema validation, and the Phase 1
safety validator. All of it is the same code the API runs.

Not real: the language model. No API key is available in a clean checkout, so the
agent runs in ``ModelMode.STUB`` and the response below is returned verbatim.
This is why STUB exists: monkeypatching the LLM call while leaving the config on
``base`` would make the audit record claim gemini-2.5-flash produced the output.
In STUB mode the record states ``provider: none_stubbed_response`` and
``inference_performed: false``, so nothing in it misattributes its own origin.

The run is also stamped ``provenance=DEMO``, because it is a rehearsed example
whose outcome is known before it starts.

The stubbed plan deliberately contains one command the registry does not define,
so the stored record demonstrates the property the architecture exists for: the
model asked for something, and the deterministic validator refused it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.agent.agent import AgentConfig, ModelMode, SentinelAgent  # noqa: E402
from app.api.adapters import with_canonical_window                  # noqa: E402
from app.api.provenance import Provenance                           # noqa: E402
from app.api.scenarios import get_all_scenarios                     # noqa: E402
from app.audit import (                                             # noqa: E402
    AuditRecorder,
    OperatorDecisionInput,
    OperatorDecisionType,
    RunStatus,
    SQLiteAuditStore,
    Stage,
    scan_for_secrets,
)

# A plausible diagnosis for preset scenario 1 (ADCS gyro SEU). Step 2 names a
# command that does not exist in the registry, on purpose.
STUB_RESPONSE = json.dumps({
    "hypotheses": [
        {
            "rank": 1,
            "root_cause": "ADCS_GYRO_SEU",
            "affected_component": "GYRO_A",
            "confidence": 0.88,
            "causal_chain": [
                "SEU_counter increments to 3 at T-62s",
                "Gyro_rate_degs returns NaN at T-60s",
                "Attitude_error_deg reaches 7.3 deg at T-30s",
                "FDIR raises ADCS_ERROR and commands safe mode at T-0s",
            ],
        },
        {
            "rank": 2,
            "root_cause": "ADCS_GYRO_HARDWARE_FAILURE",
            "affected_component": "GYRO_A",
            "confidence": 0.08,
            "causal_chain": [
                "Gyro bearing or driver degradation",
                "Rate output becomes invalid without an SEU trigger",
            ],
        },
        {
            "rank": 3,
            "root_cause": "OBC_SENSOR_BUS_FAULT",
            "affected_component": "OBC",
            "confidence": 0.04,
            "causal_chain": [
                "Sensor bus read error",
                "Gyro telemetry dropout without gyro hardware fault",
            ],
        },
    ],
    "recovery_plan": [
        {
            "step": 1,
            "command": "CMD_GYRO_A_DRIVER_RESET",
            "rationale": "Power-cycle the gyro driver to clear the SEU latch-up.",
            "wait_seconds": 30,
            "verify": "Gyro_rate_degs returns a finite value within limits",
            "risk": "LOW",
        },
        {
            "step": 2,
            "command": "CMD_PURGE_SEU_MEMORY_BANK",
            "rationale": "Invented command — exercises the registry check.",
            "wait_seconds": 10,
            "verify": "SEU_counter returns to 0",
            "risk": "MEDIUM",
        },
        {
            "step": 3,
            "command": "CMD_ATTITUDE_RESET",
            "rationale": "Re-establish the attitude reference once rates are valid.",
            "wait_seconds": 60,
            "verify": "Attitude_error_deg below 0.01 deg",
            "risk": "MEDIUM",
        },
    ],
    "confidence": 0.88,
    "requires_human_review": False,
    "reasoning_summary": (
        "The SEU counter incremented before the gyro rate became invalid, and "
        "attitude error grew only afterwards. That ordering favours a "
        "single-event upset in the gyro driver over a mechanical failure, which "
        "would not be preceded by an SEU count."
    ),
}, indent=2)


def run(db_path: str | None) -> tuple[Any, Any]:  # noqa: ANN401 - script
    store = SQLiteAuditStore(db_path=db_path or ":memory:")

    scenario = next(
        s for s in get_all_scenarios() if s.get("scenario_id") == 1
    )
    crash_dump = with_canonical_window(scenario)

    agent = SentinelAgent(AgentConfig(
        mode=ModelMode.STUB,
        stub_response=STUB_RESPONSE,
        stub_label="phase4-worked-example",
    ))

    recorder = AuditRecorder.begin(
        crash_dump,
        origin="scripts/example_audit_run.py",
        # This is a rehearsed walkthrough, not an investigation of simulated
        # telemetry. DEMO records that distinction.
        provenance_override=Provenance.DEMO.value,
    )

    # The STREAMING path is used deliberately: it is what POST /api/v1/analyze
    # runs, and it includes the deterministic detection stage. analyze_with_rag()
    # does not run detection — it expects the caller to supply anomalous
    # parameters — so a record produced through it would honestly but unhelpfully
    # read detection: NOT_RUN.
    events = list(agent.analyze_crash_dump_stream(crash_dump, recorder=recorder))
    record = recorder.finalize(store=store, status=RunStatus.COMPLETED)

    # An operator reviews the recommendation. Recorded through the store, which
    # forces actor=OPERATOR and assigns the sequence number itself.
    store.append_operator_decision(record.run_id, OperatorDecisionInput(
        decision=OperatorDecisionType.APPROVED,
        operator_id="fd.controller.alpha",
        rationale=(
            "Gyro driver reset is reversible and the battery margin is healthy. "
            "Approved for execution at the next ground contact."
        ),
        step_number=1,
        command="CMD_GYRO_A_DRIVER_RESET",
    ))
    store.append_operator_decision(record.run_id, OperatorDecisionInput(
        decision=OperatorDecisionType.DEFERRED,
        operator_id="fd.controller.alpha",
        rationale=(
            "Attitude reset held until the gyro reset has been confirmed good "
            "over a full orbit."
        ),
        step_number=2,
        command="CMD_ATTITUDE_RESET",
    ))

    return store, store.get(record.run_id), events


def show(store, record) -> None:  # noqa: ANN001 - script
    line = "=" * 78

    print(line)
    print("RUN HEADER — what was investigated, and where the data came from")
    print(line)
    h = record.header
    for key, value in h.model_dump().items():
        print(f"  {key:<22}: {value}")

    print()
    print(line)
    print("ENTRY LOG — append-only, hash-chained")
    print(line)
    print(f"{'seq':>4} {'stage':<20} {'status':<16} {'actor':<9} "
          f"{'ms':>8}  summary")
    for e in record.entries:
        ms = f"{e.duration_ms:.1f}" if e.duration_ms is not None else "—"
        print(f"{e.seq:>4} {e.stage.value:<20} {e.status.value:<16} "
              f"{e.actor.value:<9} {ms:>8}  {e.summary[:60]}")

    print()
    print(line)
    # Every stage has an implementation as of Phase 8, so the old "capability
    # absent" annotation would now never fire. What still needs flagging is the
    # narrower and more useful fact: a stage that RAN and could not conclude.
    # Collapsing that into a bare status would let a reader mistake "nothing was
    # decidable from this telemetry" for "checked and fine".
    print("STAGE COVERAGE — a stage can run and still decide nothing")
    print(line)
    for stage, status in record.coverage().items():
        flag = {
            "NOT_IMPLEMENTED": "  <-- capability absent from this build",
            "DEGRADED": "  <-- ran, but reached no conclusion",
            "FAILED": "  <-- attempted and raised",
            "SKIPPED": "  <-- deliberately bypassed",
        }.get(status, "")
        print(f"  {stage:<22} {status}{flag}")

    print()
    print(line)
    print("WHAT EACH STAGE PERSISTED")
    print(line)
    for e in record.entries:
        keys = sorted(e.payload.keys())
        size = len(json.dumps(e.payload, default=str))
        print(f"  {e.stage.value:<20} {size:>7} bytes  keys={keys}")

    print()
    print(line)
    print("PROOF POINTS")
    print(line)
    v = store.verify_chain(record.run_id)
    print(f"  hash chain valid        : {v.valid} ({v.entry_count} entries)")
    print(f"  chain seal              : {v.final_hash}")
    print(f"  input digest            : {h.input_sha256}")
    leaked = scan_for_secrets([e.payload for e in record.entries])
    print(f"  credential patterns     : {leaked or 'none'}")

    llm = record.stage(Stage.LLM).payload
    print(f"  llm provider            : {llm['provider']}")
    print(f"  inference performed     : {llm['inference_performed']}")
    print(f"  api key value recorded  : {llm['api_key_value_recorded']}")
    print(f"  prompt                  : {llm['prompt_version']}"
          f"@{llm['prompt_fingerprint']}")

    safety = record.stage(Stage.SAFETY_VALIDATION).payload
    print(f"  safety status           : {safety['safety_status']}")
    print(f"  approved                : {safety['approved_commands']}")
    for b in safety["blocked_steps"]:
        print(f"  refused                 : {b['command']} "
              f"({b['violated_constraint']}, {b['severity']})")

    decisions = record.operator_decisions()
    print(f"  operator decisions      : {len(decisions)}")
    for d in decisions:
        p = d.payload
        print(f"     seq {d.seq}: {p['decision']} by {p['operator_id']} "
              f"on {p.get('command')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default=None,
        help="SQLite path. Default: in-memory, so the script leaves no file.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print the full record as JSON instead of the readable report",
    )
    args = parser.parse_args()

    store, record, _events = run(args.db)
    if args.json:
        print(record.model_dump_json(indent=2))
    else:
        show(store, record)
    store.close()
    return 0


if __name__ == "__main__":
    from typing import Any  # noqa: E402 - keeps the annotation above valid

    raise SystemExit(main())
