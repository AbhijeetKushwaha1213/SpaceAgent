import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from sentinel/.env (one level above backend/)
_BACKEND_DIR = Path(__file__).resolve().parent.parent
for _env_path in [_BACKEND_DIR / ".env", _BACKEND_DIR.parent / ".env"]:
    if _env_path.is_file():
        load_dotenv(_env_path, override=False)
        break

import asyncio
import json
import logging
import urllib.parse
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.agent.agent import SentinelAgent
from app.api.adapters import canonical_window_dicts, with_canonical_window
from app.audit import (
    AUDIT_SCHEMA_VERSION,
    AuditRecord,
    AuditStatusResponse,
    ChainVerification,
    OperatorDecisionAccepted,
    OperatorDecisionInput,
    RunListResponse,
)
from app.api.scenarios import get_all_scenarios
from app.api.models import (
    API_VERSION,
    CONTRACT_VERSION,
    ContractInfo,
    CrashDumpRequest,
    EvaluationRunRequest,
    EvaluationResultsResponse,
    ScenarioListResponse,
    SovereigntyInfo,
    SystemStatusResponse,
    SSEEvent,
    SSEEventType,
)
from app.detection import (
    AnomalyReport,
    channel_dictionary_status,
    run_detection_on_crash_dump,
)
from app.validation.physics import PhysicsValidationReport

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sentinel.backend")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Sentinel Backend",
    description=(
        "SENTINEL — Autonomous Spacecraft FDIR Agent. "
        "Streams LLM reasoning trace and structured diagnostic output via SSE."
    ),
    version="1.0.0",
)

from app.security.config import SecurityConfig
from app.security.middleware import SecurityMiddleware
from app.security.sanitization import sanitize_telemetry_payload_data

sec_config = SecurityConfig.from_env()

# Register Security Middleware (Correlation ID, Rate Limiter, Payload Size Limit, Auth)
app.add_middleware(SecurityMiddleware, config=sec_config)

# Enable CORS with explicit allowlist (Phase 14 security requirement)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(sec_config.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Correlation-ID"],
)

# Instantiate agent once at startup (lazy-loads Gemini client on first call)
agent = SentinelAgent()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
@app.get("/api/health")
def health_check():
    """Liveness probe — returns 200 OK when server is up."""
    return {"status": "ok"}


@app.get("/scenarios")
@app.get("/api/scenarios")
def get_scenarios():
    """Return the pre-defined crash dump scenarios.

    UNVERSIONED / LEGACY shape: a bare JSON array, exactly as before Phase 3, so
    any existing client keeps working. New clients should use
    ``GET /api/v1/scenarios``, which is validated against
    ``ScenarioListResponse`` and returns canonical telemetry.
    """
    return get_all_scenarios()


# ---------------------------------------------------------------------------
# Phase 3 — versioned API. One authoritative contract.
#
# The unversioned routes above are kept verbatim for backward compatibility.
# Everything under /api/v1 is declared with a response_model, so FastAPI both
# validates the payload on the way out and publishes its schema into the
# exported OpenAPI document that contracts/ is generated from.
# ---------------------------------------------------------------------------

_V1 = f"/api/{API_VERSION}"


@app.get(f"{_V1}/contract", response_model=ContractInfo)
def contract_info():
    """Describe the data contract this backend serves.

    Lets a client — or CI — verify it is talking to a backend matching the
    contract it was generated against, rather than discovering a mismatch as a
    missing field mid-render.
    """
    return ContractInfo()


@app.get(f"{_V1}/health")
def health_check_v1():
    """Liveness probe, versioned."""
    return {
        "status": "ok",
        "contract_version": CONTRACT_VERSION,
        "api_version": API_VERSION,
    }


@app.get("/system/status", response_model=SystemStatusResponse)
@app.get("/api/system/status", response_model=SystemStatusResponse)
@app.get(f"{_V1}/system/status", response_model=SystemStatusResponse)
def get_system_status():
    """Return comprehensive system status, LLM operational mode, and sovereignty details.

    Phase 11: Serves system status, detector status, physics status, RAG status,
    LLM mode (CLOUD | LOCAL | STUB), LLM provider, active model, API version,
    simulation status, and factual sovereignty/privacy information.
    """
    cfg = agent.config
    mode_obj = getattr(cfg, "mode", "cloud")
    mode_val = getattr(mode_obj, "value", str(mode_obj)).lower()

    if mode_val in ("local", "fallback"):
        llm_mode = "LOCAL"
        llm_provider = "local"
        model_name = getattr(cfg, "fallback_model", "phi-3-mini")
        is_local = True
    elif mode_val == "stub":
        llm_mode = "STUB"
        llm_provider = "stub"
        model_name = getattr(cfg, "active_model_name", "stub")
        is_local = True
    else:
        llm_mode = "CLOUD"
        llm_provider = "gemini"
        model_name = getattr(cfg, "active_model_name", "gemini-2.5-flash")
        is_local = False

    return SystemStatusResponse(
        backend_status="ok",
        detector_status="ok",
        physics_model_status="ok",
        rag_status="ok",
        llm_mode=llm_mode,
        llm_provider=llm_provider,
        model=model_name,
        version=CONTRACT_VERSION,
        simulation_live_status="live",
        sovereignty=SovereigntyInfo(
            local_execution=is_local,
            cloud_telemetry_disabled=is_local,
            disclaimer="Factual operational mode indicator. No security or compliance certifications (e.g. FedRAMP/HIPAA) claimed.",
        ),
    )


@app.post("/evaluation/run", response_model=EvaluationResultsResponse)
@app.post(f"{_V1}/evaluation/run", response_model=EvaluationResultsResponse)
def run_evaluation_endpoint(req: EvaluationRunRequest | None = None):
    """Execute reproducible evaluation runner across baseline configurations and dataset splits."""
    from app.evaluation.runner import EvaluationRunner, save_json_results

    split = req.split if req else "HELD_OUT_TEST"
    seed = req.seed if req else 42
    mode = req.mode if req else "stub"

    runner = EvaluationRunner(seed=seed, model_mode=mode)
    results = runner.run_evaluation(split=split)
    save_json_results(results)
    return results


@app.get("/evaluation/results", response_model=EvaluationResultsResponse)
@app.get(f"{_V1}/evaluation/results", response_model=EvaluationResultsResponse)
def get_evaluation_results_endpoint(split: str = "HELD_OUT_TEST"):
    """Return latest machine-readable evaluation results."""
    from app.evaluation.runner import EvaluationRunner, save_json_results
    from pathlib import Path

    results_file = Path(__file__).resolve().parent / "evaluation" / "results" / "evaluation_results.json"
    if results_file.is_file():
        with open(results_file, "r", encoding="utf-8") as f:
            return json.load(f)

    runner = EvaluationRunner(seed=42, model_mode="stub")
    results = runner.run_evaluation(split=split)
    save_json_results(results)
    return results


@app.get(f"{_V1}/scenarios", response_model=ScenarioListResponse)
def get_scenarios_v1():
    """Return the scenario catalogue — THE single source of scenario definitions.

    Differences from the legacy route:

      * validated against ``ScenarioListResponse``, so a scenario missing its
        provenance declaration fails here rather than rendering unattributed in
        the operator's browser
      * ``pre_fault_telemetry_window`` is fully populated through
        ``with_canonical_window()``, so the client gets timing, status, bounds
        and units on every reading and never has to merge two shapes itself
      * carries ``contract_version``

    The deprecated ``pre_fault_telemetry`` array is still present in each
    scenario for backward compatibility, but it is no longer the field a client
    needs to read.
    """
    scenarios = [with_canonical_window(s) for s in get_all_scenarios()]
    return ScenarioListResponse(count=len(scenarios), scenarios=scenarios)


@app.post(f"{_V1}/detect", response_model=AnomalyReport)
def detect_endpoint_v1(crash_dump: CrashDumpRequest):
    """Versioned alias of POST /detect."""
    return detect_endpoint(crash_dump)


@app.get(f"{_V1}/detect/channels")
def detect_channels_endpoint_v1():
    """Versioned alias of GET /detect/channels — the DETECTION view."""
    return channel_dictionary_status()


@app.get(f"{_V1}/channels")
def channel_dictionary_endpoint():
    """The authoritative spacecraft channel dictionary.

    Phase 5. Serves every field for every channel from
    ``app/ingest/channel_dict.py``, so a client never has to retype a unit, a
    subsystem or a limit. Before Phase 5 the same 21 channels were defined three
    times in the backend alone, and 17 of the 21 disagreed.

    Distinct from ``GET /api/v1/detect/channels``, which is the narrower
    detection-facing projection: which test applies to each channel and why.

    ``validation`` is included in the response on purpose. Five channels have a
    nominal operating band that falls outside their own hard limits, inherited
    from the tables this dictionary replaced, and a client reading limits from
    here is entitled to know that rather than having to consult a document.
    """
    from app.ingest.channel_dict import all_channels, dictionary_status

    status = dictionary_status()
    return {
        **status,
        "channels": [
            {
                "channel_id": c.channel_id,
                "display_name": c.display_name,
                "subsystem": c.subsystem.value,
                "unit": c.unit,
                "datatype": c.datatype.value,
                "value_class": c.value_class.value,
                "is_discrete": c.is_discrete,
                "nominal_range": list(c.nominal_range),
                "hard_limits": list(c.hard_limits),
                "safety_limits": list(c.safety_limits),
                "criticality": c.criticality.value,
                "sampling_rate": c.sampling_rate.value,
                "description": c.description,
                "physical_meaning": c.physical_meaning,
                "provenance": c.provenance.value,
                "limits_provenance": c.limits_provenance.value,
                "safety_limits_provenance": c.safety_limits_provenance.value,
                "expected_states": list(c.expected_states),
                "monotonic_non_decreasing": c.monotonic_non_decreasing,
                "max_rate_per_s": c.max_rate_per_s,
                "aliases": list(c.aliases),
                "notes": c.notes,
                "nominal_within_hard_limits": c.nominal_within_hard_limits,
                "degenerate_hard_limits": c.degenerate_hard_limits,
                "statistical_detection_meaningful":
                    c.statistical_detection_meaningful,
            }
            for c in all_channels()
        ],
    }


@app.post(f"{_V1}/physics", response_model=PhysicsValidationReport)
def physics_validation_endpoint(crash_dump: CrashDumpRequest):
    """Validate fault hypotheses against the simplified spacecraft state model.

    Phase 8. Runs the whole deterministic chain for one dump and returns a
    verdict per candidate hypothesis:

        detection -> hypotheses -> state estimate -> residuals -> VALID /
        INVALID / UNCERTAIN

    No language model is consulted at any step, and there is no request field
    through which one could influence a verdict.

    Read the three statuses precisely, because they are not a spectrum:

      INVALID    a declared constraint is violated by DECIDED residual evidence.
                 Grounds to downgrade or reject the hypothesis.
      VALID      nothing violated and at least one constraint corroborated. NOT
                 "confirmed" — the corroborating models carry four assumed
                 parameters.
      UNCERTAIN  nothing decided either way. The ordinary outcome on sparse
                 telemetry, and explicitly NOT a pass.

    ``assumed_parameters`` and ``model_limitations`` are in every response on
    purpose: a verdict shows consistency with those assumptions rather than with
    the spacecraft, and a client reading a verdict is entitled to see them
    without consulting a document.
    """
    from app.validation.physics import validate_crash_dump

    report, _hypotheses, _residuals, _sequence = validate_crash_dump(
        crash_dump.model_dump()
    )
    return report


@app.get(f"{_V1}/physics/constraints")
def physics_constraints_endpoint():
    """The declared physical constraints and what each fault claims.

    Phase 8. Serves the constraint catalogue, its refutation rules, and the
    per-fault claims table, so a client can see WHY a verdict came out as it did
    rather than being handed a status.

    ``faults_without_coverage`` is included deliberately. Four faults have no
    physics checks at all — OBC, COMMS, the control-command anomaly and the
    cascade — and their UNCERTAIN verdicts reflect the models' reach rather than
    the evidence. A client that could not distinguish those from undecided
    evidence would misreport them.
    """
    from app.validation.physics import physics_status

    return physics_status()


@app.get(f"{_V1}/channels/{{channel_id}}")
def channel_lookup_endpoint(channel_id: str):
    """Resolve one channel by id or alias.

    An unrecognised channel returns 200 with ``subsystem`` and ``provenance`` set
    to ``UNKNOWN`` and ``is_known`` false, rather than a 404. The distinction
    matters: a caller looking up a channel from a live payload needs to be told
    "this exists but is unattributed" — which is the honest answer for an
    anonymized ESA-ADB channel — not that its request was malformed. No subsystem
    is ever inferred from the name.
    """
    from app.ingest.channel_dict import get_channel, resolve_channel

    known = get_channel(channel_id)
    definition = known or resolve_channel(channel_id)
    return {
        "requested": channel_id,
        "is_known": definition.is_known,
        "channel_id": definition.channel_id,
        "display_name": definition.display_name,
        "subsystem": definition.subsystem.value,
        "unit": definition.unit,
        "datatype": definition.datatype.value,
        "value_class": definition.value_class.value,
        "nominal_range": list(definition.nominal_range),
        "hard_limits": list(definition.hard_limits),
        "safety_limits": list(definition.safety_limits),
        "criticality": definition.criticality.value,
        "sampling_rate": definition.sampling_rate.value,
        "description": definition.description,
        "physical_meaning": definition.physical_meaning,
        "provenance": definition.provenance.value,
        "expected_states": list(definition.expected_states),
        "max_rate_per_s": definition.max_rate_per_s,
        "notes": definition.notes,
    }


# ---------------------------------------------------------------------------
# Phase 2 — anomaly detection
# ---------------------------------------------------------------------------

@app.post("/detect", response_model=AnomalyReport)
@app.post("/api/detect", response_model=AnomalyReport)
def detect_endpoint(crash_dump: CrashDumpRequest):
    """Run the deterministic detection pipeline and return an AnomalyReport.

    Deliberately synchronous and LLM-free: detection is pure computation, so this
    endpoint needs no API key, makes no network call, and returns in milliseconds.
    It exists so the frontend can display real detector output instead of
    recomputing an approximation client-side.

    The pipeline is:
        hard limits -> discrete states -> statistical -> temporal -> fusion

    Every anomaly carries its detector, score, threshold, severity, evidence and
    provenance — including which baseline source the statistics came from, so a
    finding resting on engineering limits rather than observed data is
    identifiable as weak evidence.
    """
    payload = crash_dump.model_dump(mode="json", exclude_none=True)
    report = run_detection_on_crash_dump(payload)
    logger.info(
        "POST /detect — scenario_id=%s: %d anomaly(ies) on %d/%d channel(s), "
        "max severity %s",
        payload.get("scenario_id"),
        report.anomaly_count,
        report.anomalous_channels,
        report.total_channels,
        report.max_severity.value,
    )
    return report


@app.get("/detect/channels")
@app.get("/api/detect/channels")
def detect_channels_endpoint():
    """Return the channel dictionary the detectors are driven by.

    Lets a client see which channels have declared limits, which are discrete
    states or counters, and which are excluded from statistical detection
    because a Gaussian test on them is meaningless.
    """
    return channel_dictionary_status()


@app.get("/api/analyze")
async def analyze_get_endpoint(preset: str = None, payload: str = None):
    """EventSource endpoint for index.html client.

    Decodes payload, runs the same streaming analysis, and yields events in the format
    expected by index.html (events: telemetry, trace, done).
    """
    try:
        decoded_payload = urllib.parse.unquote(payload) if payload else "{}"
        data = json.loads(decoded_payload)
    except Exception as exc:
        logger.error("Failed to parse GET payload: %s", exc)
        data = {}

    logger.info(
        "GET /api/analyze — preset=%s, payload keys=%s",
        preset,
        list(data.keys()),
    )

    async def event_generator():
        try:
            # 1. Stream the telemetry entries.
            #
            # Phase 3: read the CANONICAL window. The previous
            #   data.get("pre_fault_telemetry_window") or data.get("pre_fault_telemetry")
            # took whichever field was non-empty and discarded the other, so a
            # dump carrying both streamed only the window — and the window omits
            # up to five channels the legacy list holds. The adapter merges them.
            for entry in canonical_window_dicts(data):
                # `value` is None for an unusable reading; value_text preserves
                # what was actually received ("NaN" / "MISSING"). Note the old
                # code used `or`, so a legitimate 0 or False — a transponder
                # lock of 0 being the fault itself — rendered as an empty cell.
                raw = entry.get("value")
                usable = isinstance(raw, (int, float)) and not isinstance(raw, bool)
                if usable:
                    # Unit only ever accompanies a usable number. Attaching one
                    # to a dropout reads as "MISSING deg/s", which implies a
                    # measurement was taken.
                    unit = entry.get("unit")
                    text = f"{raw} {unit}" if unit else str(raw)
                else:
                    text = str(entry.get("value_text") or raw or "MISSING")

                # Status → index.html css class (ok | warn | anomaly).
                #
                # WARNING and LABELLED_ANOMALY are newly mapped to `warn`. They
                # previously fell through to `ok`, so a payload that declared a
                # warning — or an ESA-ADB ground-truth anomaly label — rendered
                # as nominal green. No CSS changed; the class assignment was
                # simply wrong for those two values.
                #
                # UNKNOWN stays on the neutral class deliberately: these are
                # payload-declared statuses, not detector verdicts, so escalating
                # "not stated" to a warning would invent a finding. The
                # authoritative per-channel verdict comes from POST /detect.
                # `status` is passed through so a client can distinguish a
                # declared NOMINAL from an absent one.
                status_val = entry.get("status") or "UNKNOWN"
                cls_val = "ok"
                if status_val in ("ANOMALOUS", "anomaly", "WARNING",
                                  "LABELLED_ANOMALY"):
                    cls_val = "warn"
                elif status_val in ("CRITICAL", "critical"):
                    cls_val = "anomaly"
                elif not usable:
                    # An unusable reading is an observed fact, not an inference,
                    # so it must not render on the nominal class even when the
                    # payload states no status. Scenario 1's T-0s gyro NaN
                    # arrives with status UNKNOWN and used to show green.
                    cls_val = "warn"

                telem_data = {
                    "t": entry.get("timestamp") or "T-0s",
                    "k": entry.get("parameter") or "",
                    "v": text,
                    "cls": cls_val,
                    "status": status_val,
                }
                yield f"event: telemetry\ndata: {json.dumps(telem_data)}\n\n"
                await asyncio.sleep(0.05)

            # 2. Run the main streaming analysis
            for event in agent.analyze_crash_dump_stream(data):
                # Map SSEEvent to index.html trace event
                # index.html trace types: 'thought', 'action', 'observe', 'result', 'alert'
                trace_type = "thought"
                if event.event_type == SSEEventType.THOUGHT:
                    trace_type = "thought"
                elif event.event_type == SSEEventType.ACTION:
                    trace_type = "action"
                elif event.event_type == SSEEventType.OBSERVATION:
                    trace_type = "observe"
                elif event.event_type == SSEEventType.RESULT:
                    trace_type = "result"
                elif event.event_type == SSEEventType.ERROR:
                    trace_type = "alert"
                elif event.event_type == SSEEventType.STATUS:
                    # Pipeline stage events get the 'stage' type so the
                    # frontend renders them distinctly from generic thoughts.
                    trace_type = "stage"

                trace_data = {
                    "type": trace_type,
                    "text": event.data
                }
                yield f"event: trace\ndata: {json.dumps(trace_data)}\n\n"

                # Keep the same delays as the POST endpoint
                if event.event_type in (
                    SSEEventType.THOUGHT,
                    SSEEventType.ACTION,
                    SSEEventType.STATUS,
                ):
                    await asyncio.sleep(0.35)
                elif event.event_type == SSEEventType.OBSERVATION:
                    await asyncio.sleep(0.15)

            # 3. Emit done event
            yield "event: done\ndata: {}\n\n"

        except Exception as exc:
            logger.error("Streaming error in GET /api/analyze: %s", exc, exc_info=True)
            err_data = {"type": "alert", "text": f"Error during analysis: {exc}"}
            yield f"event: trace\ndata: {json.dumps(err_data)}\n\n"
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/analyze")
@app.post("/api/analyze")
async def analyze_endpoint(crash_dump: CrashDumpRequest):
    """Analyze a crash dump and stream the full reasoning trace via SSE.

    The response is a text/event-stream where each event is a JSON-encoded
    SSEEvent object.  Event types:
      - STATUS     : pipeline stage updates
      - THOUGHT    : agent reasoning steps
      - ACTION     : tool/subsystem invocations
      - OBSERVATION: tool results / telemetry returns
      - RESULT     : final SentinelOutput JSON string
      - ERROR      : any exception during streaming
    """
    payload = crash_dump.model_dump(mode="json", exclude_none=True)
    logger.info(
        "POST /analyze — scenario_id=%s fault_type=%s",
        payload.get("scenario_id"),
        payload.get("fault_type"),
    )

    async def event_generator():
        try:
            for event in agent.analyze_crash_dump_stream(payload):
                yield f"data: {event.model_dump_json()}\n\n"

                # Small delays so the UI can render each thought before the next
                if event.event_type in (
                    SSEEventType.THOUGHT,
                    SSEEventType.ACTION,
                    SSEEventType.STATUS,
                ):
                    await asyncio.sleep(0.35)
                elif event.event_type == SSEEventType.OBSERVATION:
                    await asyncio.sleep(0.15)

        except Exception as exc:
            logger.error("Streaming error: %s", exc, exc_info=True)
            err = SSEEvent(
                event_type=SSEEventType.ERROR,
                data=f"Streaming analysis encountered an error: {exc}",
            )
            yield f"data: {err.model_dump_json()}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post(f"{_V1}/analyze")
async def analyze_endpoint_v1(crash_dump: CrashDumpRequest):
    """Analyze a crash dump, streaming the trace and recording an audit run.

    Phase 4. This is the audited entry point. It differs from the unversioned
    ``POST /analyze`` in one way: every invocation opens an audit run and
    persists it, so the investigation is reproducible afterwards through
    ``GET /api/v1/runs/{run_id}``.

    The run id is published twice, because the two consumers need it at
    different moments:

      * response header ``X-Sentinel-Run-Id`` — available before the body starts
        streaming, so a client can record it even if the stream then fails
      * an SSE ``status`` event early in the stream, for a browser client that
        cannot read headers from an EventSource

    The record is persisted in a ``finally`` block, so a run is stored even when
    the stream raises or the client disconnects mid-analysis. A disconnect is
    recorded as ABANDONED rather than COMPLETED — an interrupted investigation
    that looked finished would be worse than no record.

    NOTE ON ACCESS CONTROL: this endpoint is unauthenticated, like the rest of
    this API. Anyone who can reach the port can start a run and read any stored
    record. Audit records contain telemetry, model output and operator names, so
    an authentication layer is required before this is exposed beyond localhost.
    """
    from app.audit import AuditRecorder, RunStatus, get_store

    payload = crash_dump.model_dump(mode="json", exclude_none=True)
    recorder = AuditRecorder.begin(
        payload, origin=f"POST {_V1}/analyze",
    )
    logger.info(
        "POST %s/analyze — run_id=%s scenario_id=%s fault_type=%s provenance=%s",
        _V1, recorder.run_id, payload.get("scenario_id"),
        payload.get("fault_type"), recorder.header.provenance,
    )

    async def event_generator():
        status = RunStatus.COMPLETED
        error: str | None = None
        try:
            # Announce the run id first so it is in the stream even if the
            # pipeline fails on its first stage.
            opened = SSEEvent(
                event_type=SSEEventType.STATUS,
                data=f"Audit run: {recorder.run_id}",
            )
            yield f"data: {opened.model_dump_json()}\n\n"

            for event in agent.analyze_crash_dump_stream(
                payload, recorder=recorder,
            ):
                if event.event_type == SSEEventType.ERROR:
                    status = RunStatus.FAILED
                    error = event.data
                yield f"data: {event.model_dump_json()}\n\n"

                if event.event_type in (
                    SSEEventType.THOUGHT,
                    SSEEventType.ACTION,
                    SSEEventType.STATUS,
                ):
                    await asyncio.sleep(0.35)
                elif event.event_type == SSEEventType.OBSERVATION:
                    await asyncio.sleep(0.15)

        except GeneratorExit:
            # The client went away. Record what was gathered and mark it
            # ABANDONED, then let the exception propagate as asyncio requires.
            recorder.finalize(
                store=get_store(), status=RunStatus.ABANDONED,
                error="client disconnected before the analysis completed",
            )
            raise
        except Exception as exc:
            logger.error("Streaming error: %s", exc, exc_info=True)
            status = RunStatus.FAILED
            error = str(exc)
            err = SSEEvent(
                event_type=SSEEventType.ERROR,
                data=f"Streaming analysis encountered an error: {exc}",
            )
            yield f"data: {err.model_dump_json()}\n\n"
        else:
            record = recorder.finalize(
                store=get_store(), status=status, error=error,
            )
            done = SSEEvent(
                event_type=SSEEventType.STATUS,
                data=(
                    f"Audit run {record.run_id} recorded: "
                    f"{len(record.entries)} entries, seal "
                    f"{record.outcome.final_hash[:16]}"
                ),
            )
            yield f"data: {done.model_dump_json()}\n\n"
            return

        # Reached only on the exception path above.
        recorder.finalize(store=get_store(), status=status, error=error)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "X-Sentinel-Run-Id": recorder.run_id,
            "X-Sentinel-Audit-Schema": AUDIT_SCHEMA_VERSION,
            # EventSource cannot read custom headers cross-origin unless they
            # are explicitly exposed.
            "Access-Control-Expose-Headers":
                "X-Sentinel-Run-Id, X-Sentinel-Audit-Schema",
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# Phase 4 — audit trail read API
#
# Read-only by construction. There is no endpoint that accepts an audit record,
# a stage entry, a status or a hash from a client, so the frontend cannot
# fabricate or amend a record. The single write path is the operator-decision
# endpoint below, which accepts a decision and a rationale and lets the server
# stamp everything else.
# ---------------------------------------------------------------------------

@app.get(f"{_V1}/audit/status", response_model=AuditStatusResponse)
def audit_status():
    """Describe the audit store and the guarantees actually in force.

    ``not_implemented_stages`` is served explicitly so a client cannot read the
    absence of a stage as a check that passed. It is now EMPTY: Phase 7 added
    state estimation and Phase 8 added physics validation, so every stage in the
    enum records a real result.

    An empty list is not a claim that every check succeeds. A stage can still be
    recorded DEGRADED (it ran and could decide nothing) or FAILED, and a physics
    verdict can be UNCERTAIN. What the empty list means is narrower and worth
    stating: no stage is missing from this build. Whether a given run reached a
    conclusion is a per-run question, answered by that run's coverage map.
    """
    from app.audit import Stage, get_store
    from app.audit.store import SQLiteAuditStore

    store = get_store()
    return AuditStatusResponse(
        backend=type(store).__name__,
        location=str(getattr(store, "db_path", "unknown")),
        run_count=store.count_runs(),
        append_only=True,
        enforcement=[
            "no update or delete method exists on the AuditStore interface",
            "database triggers abort UPDATE and DELETE on all audit tables",
            "each entry chains to the previous one by SHA-256; "
            "GET /api/v1/runs/{run_id}/verify recomputes the chain",
            "entry_count and final_hash are derived at read time, so appending "
            "never requires an UPDATE",
        ] if isinstance(store, SQLiteAuditStore) else [
            "no update or delete method exists on the AuditStore interface",
        ],
        stages_recorded=[s.value for s in Stage],
        # Empty as of Phase 8. STATE_ESTIMATION was here until Phase 7 and
        # PHYSICS_VALIDATION until Phase 8; both now record real results, so
        # listing either would understate what runs. A stage that runs but cannot
        # decide anything is recorded DEGRADED on that run rather than declared
        # absent here.
        not_implemented_stages=[],
        last_error=store.last_error,
    )


@app.get(f"{_V1}/runs", response_model=RunListResponse)
def list_runs(
    limit: int = 50,
    offset: int = 0,
    scenario_id: int | None = None,
    provenance: str | None = None,
):
    """List audit runs, newest first.

    ``provenance`` filters on the recorded code, so an auditor can separate real
    investigations from SYNTHETIC and DEMO runs without reading each record.
    """
    from app.audit import get_store

    store = get_store()
    runs = store.list_runs(
        limit=limit, offset=offset, scenario_id=scenario_id,
        provenance=provenance,
    )
    return RunListResponse(
        total=store.count_runs(),
        count=len(runs),
        limit=max(1, min(int(limit), 500)),
        offset=max(0, int(offset)),
        runs=runs,
    )


@app.get(f"{_V1}/runs/{{run_id}}", response_model=AuditRecord)
def get_run(run_id: str):
    """Return one complete audit record.

    The full entry list is returned, not a summary: the entries ARE the record,
    and a caller re-verifying the hash chain needs every one of them.
    """
    from app.audit import get_store

    record = get_store().get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return record


@app.get(f"{_V1}/runs/{{run_id}}/verify", response_model=ChainVerification)
def verify_run(run_id: str):
    """Recompute a run's hash chain from what is on disk.

    Reads the stored rows rather than a cached object, so it detects tampering
    performed outside the application — a doctored backup, or a direct write that
    somehow bypassed the triggers.
    """
    from app.audit import get_store
    from app.audit.store import RunNotFoundError

    try:
        return get_store().verify_chain(run_id)
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")


@app.post(
    f"{_V1}/runs/{{run_id}}/decisions",
    response_model=OperatorDecisionAccepted,
    status_code=201,
)
def record_operator_decision(run_id: str, decision: OperatorDecisionInput):
    """Append an operator decision to an existing run.

    The ONLY client-writable path into the audit trail, and deliberately narrow.
    A client supplies the decision, who made it, why, and optionally which step
    or command it concerns. The server assigns the sequence number, the
    timestamp, the actor (always OPERATOR) and the stage (always
    OPERATOR_DECISION), and the store refuses an OPERATOR actor on any system
    stage. So a client can add an attributed human decision to a run; it cannot
    manufacture a detection result, an LLM output or a safety verdict.

    ``OperatorDecisionInput`` forbids extra fields, so an attempt to smuggle
    additional keys is a 422 rather than silently ignored data.

    NOTE ON IDENTITY: ``operator_id`` is recorded verbatim and is NOT
    authenticated. This endpoint establishes WHAT was decided and WHEN, not WHO
    by any verifiable means. Attribution needs an authentication layer that does
    not exist yet, and the response says so.
    """
    from app.audit import get_store
    from app.audit.store import RunNotFoundError

    store = get_store()
    try:
        entry = store.append_operator_decision(run_id, decision)
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    verification = store.verify_chain(run_id)
    return OperatorDecisionAccepted(
        run_id=run_id,
        seq=entry.seq,
        recorded_at=entry.recorded_at,
        entry_hash=entry.entry_hash,
        chain_valid=verification.valid,
    )
