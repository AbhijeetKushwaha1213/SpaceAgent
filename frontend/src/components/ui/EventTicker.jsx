/*
 * EventTicker — the live reasoning/telemetry feed during an FDIR run.
 *
 * Renders the accumulated SSE trace (thought / action / observation / status /
 * error) newest-last, auto-scrolling to the bottom as events arrive. The RESULT
 * event carries the full SentinelOutput JSON and is intentionally excluded —
 * it is rendered by the diagnosis views, not the ticker.
 */

import React, { useEffect, useRef } from "react";

const KIND_LABEL = {
  thought: "THOUGHT",
  action: "ACTION",
  observation: "OBSERV",
  status: "STATUS",
  error: "ERROR",
};

export default function EventTicker({ events, max = 80 }) {
  const endRef = useRef(null);
  const trace = (events || []).filter((e) => e && e.type !== "result");
  const shown = trace.slice(-max);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [trace.length]);

  if (shown.length === 0) {
    return (
      <div className="ticker" role="log" aria-live="polite">
        <div className="ticker__row ticker__row--status">
          <span className="ticker__kind">STATUS</span>
          <span className="ticker__text">Waiting for the first pipeline event…</span>
        </div>
      </div>
    );
  }

  return (
    <div className="ticker" role="log" aria-live="polite" aria-label="Live analysis event trace">
      {shown.map((ev, i) => {
        const kind = String(ev.type || "status").toLowerCase();
        const text =
          typeof ev.data === "object" && ev.data !== null
            ? String(ev.data.data || JSON.stringify(ev.data))
            : String(ev.data ?? "");
        return (
          <div key={i} className={`ticker__row ticker__row--${kind}`}>
            <span className="ticker__kind">{KIND_LABEL[kind] || kind.toUpperCase()}</span>
            <span className="ticker__text mono">{text}</span>
          </div>
        );
      })}
      <div ref={endRef} />
    </div>
  );
}
