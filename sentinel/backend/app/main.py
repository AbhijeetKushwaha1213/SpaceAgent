import asyncio
import json
import logging
import urllib.parse
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.agent.agent import SentinelAgent
from app.api.scenarios import get_preset_scenarios
from app.api.models import SSEEvent, SSEEventType, CrashDumpRequest

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

# Enable CORS so the React frontend (port 3000) can reach this server (port 8000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Instantiate agent once at startup (lazy-loads Gemini client on first call)
agent = SentinelAgent()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check():
    """Liveness probe — returns 200 OK when server is up."""
    return {"status": "ok"}


@app.get("/scenarios")
def get_scenarios():
    """Return the pre-defined crash dump scenarios for the demo UI."""
    return get_preset_scenarios()


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
            # 1. Stream the telemetry entries if present in data
            pre_fault_telem = data.get("pre_fault_telemetry_window") or data.get("pre_fault_telemetry")
            if pre_fault_telem:
                for entry in pre_fault_telem:
                    t_val = entry.get("timestamp") or entry.get("t") or "T-0s"
                    k_val = entry.get("parameter") or entry.get("k") or ""
                    v_val = entry.get("value") or entry.get("v") or ""
                    status_val = entry.get("status") or entry.get("cls") or "NOMINAL"

                    # Map status/cls to index.html css classes (anomaly, warn, ok)
                    cls_val = "ok"
                    if status_val in ("ANOMALOUS", "anomaly"):
                        cls_val = "warn"
                    elif status_val in ("CRITICAL", "critical"):
                        cls_val = "anomaly"

                    telem_data = {
                        "t": t_val,
                        "k": k_val,
                        "v": str(v_val),
                        "cls": cls_val
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
                    trace_type = "thought"

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