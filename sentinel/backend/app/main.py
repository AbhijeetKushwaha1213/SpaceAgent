import asyncio
import logging
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