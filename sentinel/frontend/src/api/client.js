/*
 * SENTINEL frontend API client.
 *
 * Every request goes to the real backend contract. There is no mock data
 * anywhere in this file: an endpoint that fails surfaces an error state, and
 * an absent field renders as N/A downstream.
 */

const DEFAULT_BACKEND_URL = "http://localhost:8000";

export function resolveBackendUrl() {
  if (typeof window !== "undefined" && window.SENTINEL_BACKEND_URL) {
    return window.SENTINEL_BACKEND_URL.replace(/\/+$/, "");
  }
  if (typeof process !== "undefined" && process.env?.REACT_APP_BACKEND_URL) {
    return process.env.REACT_APP_BACKEND_URL.replace(/\/+$/, "");
  }
  return DEFAULT_BACKEND_URL;
}

export async function apiGet(path) {
  const resp = await fetch(`${resolveBackendUrl()}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status} on GET ${path}`);
  }
  return resp.json();
}

export async function apiPost(path, body) {
  const resp = await fetch(`${resolveBackendUrl()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status} on POST ${path}`);
  }
  return resp.json();
}

/*
 * Consume the SSE stream from POST /api/v1/analyze.
 *
 * `onEvent(eventType, data)` is called for each parsed event. `onRunId` is
 * called with the run id read from the X-Sentinel-Run-Id response header,
 * which is available before the body starts streaming.
 */
export async function streamAnalysis(payload, { onEvent, onRunId }) {
  const resp = await fetch(`${resolveBackendUrl()}/api/v1/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    throw new Error(`Analysis stream failed with HTTP ${resp.status}`);
  }
  const runId = resp.headers.get("X-Sentinel-Run-Id");
  if (runId && onRunId) onRunId(runId);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      const trimmed = block.trim();
      if (!trimmed) continue;
      const lines = trimmed.split("\n");
      let eventType = "";
      const dataLines = [];
      for (const line of lines) {
        if (line.startsWith("event:")) {
          eventType = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trim());
        }
      }
      const dataStr = dataLines.join("\n");
      if (dataStr) {
        let parsed = null;
        try {
          parsed = JSON.parse(dataStr);
        } catch (e) {
          parsed = dataStr;
        }
        // POST /api/v1/analyze emits plain "data: {...}" lines whose payload
        // carries its own event_type (no "event:" prefix in the wire format).
        const inferred = !eventType && parsed && typeof parsed === "object"
          ? parsed.event_type
          : "";
        onEvent(inferred || eventType || "message", parsed);
      }
    }
  }
}
