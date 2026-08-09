import React, { useState, useEffect, useRef } from "react";
import "./App.css";
// ─── GENERATED DATA CONTRACT ──────────────────────────────────────
// Phase 3. Versioned API paths and every closed vocabulary come from
// src/generated/contract.js, which is generated from the backend Pydantic
// models by sentinel/backend/scripts/export_contracts.py. Do not retype these
// literals here: hand-typed copies are what let the frontend and backend drift.
import {
  API,
  CANONICAL_TELEMETRY_FIELD,
  CONTRACT_VERSION,
  PROVENANCE,
  PROVENANCE_LABELS,
  isRealProvenance,
  normalizeProvenance,
} from "./generated/contract";

export { PROVENANCE, CONTRACT_VERSION };

// ─── BACKEND URL — SINGLE SOURCE OF TRUTH ─────────────────────────
// Resolution order:
//   1. window.SENTINEL_BACKEND_URL, written by scripts/generate-config.js into
//      config.js from REACT_APP_BACKEND_URL in sentinel/frontend/.env
//   2. process.env.REACT_APP_BACKEND_URL, inlined at build time by CRA
//   3. DEFAULT_BACKEND_URL below
// DEFAULT_BACKEND_URL must match scripts/generate-config.js, index.html,
// public/landing.html and .env.example. Do not introduce a different port.
export const DEFAULT_BACKEND_URL = "http://localhost:8000";

const BACKEND_URL =
  (typeof window !== "undefined" && window.SENTINEL_BACKEND_URL) ||
  process.env.REACT_APP_BACKEND_URL ||
  DEFAULT_BACKEND_URL;

// ─── PROVENANCE PRESENTATION ──────────────────────────────────────
// The vocabulary and the operator-facing labels are generated from
// backend/app/api/provenance.py. Only the COLOURS are a frontend concern, so
// only the colours are defined here. A scenario is shown as REAL solely when
// its numeric telemetry came from the source dataset — real identifiers or real
// anomaly-class metadata are not sufficient.
const PROVENANCE_COLOURS = {
  [PROVENANCE.REAL]: {
    color: "rgba(16, 185, 129, 0.95)",
    border: "rgba(16, 185, 129, 0.35)",
    background: "rgba(16, 185, 129, 0.08)",
  },
  [PROVENANCE.SYNTHETIC]: {
    color: "rgba(245, 183, 77, 0.95)",
    border: "rgba(245, 183, 77, 0.35)",
    background: "rgba(245, 183, 77, 0.10)",
  },
  [PROVENANCE.SYNTHETIC_FROM_REAL_METADATA]: {
    color: "rgba(245, 183, 77, 0.95)",
    border: "rgba(245, 183, 77, 0.35)",
    background: "rgba(245, 183, 77, 0.10)",
  },
  [PROVENANCE.UNKNOWN]: {
    color: "rgba(148, 163, 184, 0.95)",
    border: "rgba(148, 163, 184, 0.35)",
    background: "rgba(148, 163, 184, 0.08)",
  },
};

// Resolve a scenario's provenance. Anything unrecognised — including a missing
// field — resolves to UNKNOWN so a typo can never render as REAL.
export function resolveProvenance(scenario) {
  return normalizeProvenance(scenario && scenario.provenance);
}

export function provenanceDisplay(scenario) {
  const code = resolveProvenance(scenario);
  return { label: PROVENANCE_LABELS[code], ...PROVENANCE_COLOURS[code] };
}

// True only when the numeric telemetry itself is real.
export function isRealTelemetry(scenario) {
  return isRealProvenance(scenario && scenario.provenance);
}

// ─── CANONICAL TELEMETRY ACCESS ───────────────────────────────────
// Phase 3. Read the canonical window and nothing else. The frontend used to
// render `pre_fault_telemetry`, the deprecated shape, which carries bounds but
// no timing and no status — so the panel could not show WHEN a channel went bad
// and had to be merged mentally with what the detector reported separately.
// GET /api/v1/scenarios serves the canonical field fully populated.
export function canonicalWindow(scenario) {
  if (!scenario) return [];
  const rows = scenario[CANONICAL_TELEMETRY_FIELD];
  return Array.isArray(rows) ? rows : [];
}

// Latest reading per channel, for a one-card-per-channel summary. The window is
// a time series; showing every sample would repeat channels, and showing the
// first would show the oldest — which is how the safety layer's condition
// extractors came to read a stale healthy value over a live dropout.
export function latestPerChannel(scenario) {
  const latest = new Map();
  canonicalWindow(scenario).forEach((row, index) => {
    if (!row || !row.parameter) return;
    const t = typeof row.relative_time_s === "number" ? row.relative_time_s : null;
    const previous = latest.get(row.parameter);
    if (
      !previous ||
      (t !== null && (previous.t === null || t > previous.t)) ||
      (t === null && previous.t === null && index > previous.index)
    ) {
      latest.set(row.parameter, { row, t, index });
    }
  });
  return Array.from(latest.values()).map((e) => e.row);
}

// Format a reading for display. `value` is null for an unusable sample and
// `value_text` preserves what actually arrived ("NaN" / "MISSING"), so a dropout
// stays visible instead of rendering as a blank or as 0.
export function formatReading(row) {
  if (!row) return "—";
  if (row.value === null || row.value === undefined) {
    return row.value_text || "MISSING";
  }
  const magnitude = Math.abs(row.value);
  const digits = magnitude !== 0 && magnitude < 0.01 ? 4 : magnitude < 1 ? 3 : 1;
  const text = Number(row.value).toFixed(digits);
  return row.unit ? `${text} ${row.unit}` : text;
}

export function formatRange(row) {
  if (!row) return "Range: not specified";
  const lo = row.nominal_min;
  const hi = row.nominal_max;
  if (lo === null || lo === undefined || hi === null || hi === undefined) {
    return "Range: not specified";
  }
  return `Range: ${lo}–${hi}`;
}

// ─── ROUTING ──────────────────────────────────────────────────────
// Normalize the path before comparing. A strict `!== "/dashboard"` check meant
// "/dashboard/" (with a trailing slash) fell through to the landing iframe,
// which — because no /landing.html exists at the served root — resolved via the
// catch-all to index.html and embedded the demo page inside the dashboard.
export function isDashboardPath(pathname) {
  if (typeof pathname !== "string") return false;
  const normalized = pathname.replace(/\/+$/, "").toLowerCase();
  return normalized === "/dashboard";
}

// Scenario definitions live ONLY in the backend.
//
// Phase 3 removed a 188-line copy of the scenario catalogue that lived here.
// It was described as a fallback "used ONLY as analysis input when the backend
// is unreachable", but in practice it was a second source of truth: the local
// copy and app/api/scenarios.py had already diverged (the local copy carried
// only the deprecated pre_fault_telemetry array, no provenance notes and no
// telemetry window), so the dashboard could show one thing and the backend
// analyse another.
//
// There is now no offline fallback by design. If the catalogue cannot be
// fetched, the UI says so — see the scenariosState === "unavailable" branch.
// Showing stale local scenarios while the backend is down is exactly the
// failure mode Phase 0 set out to remove.

function App() {
  const [currentPath, setCurrentPath] = useState(window.location.pathname);

  useEffect(() => {
    const handlePopState = () => {
      setCurrentPath(window.location.pathname);
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  // Handle scroll restoration & reset scroll to top when changing views
  useEffect(() => {
    if ('scrollRestoration' in window.history) {
      window.history.scrollRestoration = 'manual';
    }
    window.scrollTo(0, 0);
  }, [currentPath]);

  // Phase 3: the catalogue starts EMPTY and is only ever filled from
  // GET /api/v1/scenarios. There is no local copy to fall back to.
  const [scenarios, setScenarios] = useState([]);
  const [scenariosState, setScenariosState] = useState("loading"); // loading|ready|unavailable
  const [scenariosError, setScenariosError] = useState(null);
  const [contractMismatch, setContractMismatch] = useState(null);
  const [selectedScenarioId, setSelectedScenarioId] = useState(null);
  const [customDump, setCustomDump] = useState("");
  const [isCustomMode, setIsCustomMode] = useState(false);
  
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [logs, setLogs] = useState([]);
  const [result, setResult] = useState(null);
  const [completedSteps, setCompletedSteps] = useState(new Set());
  const [backendStatus, setBackendStatus] = useState("checking"); // "online" | "offline"

  // Phase 2: the backend AnomalyReport for the selected scenario.
  // null means "no detection result available" — never "nothing is anomalous".
  const [detectionReport, setDetectionReport] = useState(null);
  const [detectionState, setDetectionState] = useState("idle"); // idle|loading|ready|unavailable
  
  const terminalEndRef = useRef(null);

  // Fetch the scenario catalogue from the versioned endpoint on mount.
  //
  // Phase 3: this is the ONLY place scenario definitions enter the frontend.
  // The response is the ScenarioListResponse envelope, which carries the
  // contract version — checked here so a frontend built against one contract
  // talking to a backend serving another reports that plainly instead of
  // failing later as an undefined field in a render.
  useEffect(() => {
    let cancelled = false;
    async function init() {
      setScenariosState("loading");
      try {
        const res = await fetch(`${BACKEND_URL}${API.scenarios}`);
        if (!res.ok) {
          throw new Error(`GET ${API.scenarios} returned ${res.status}`);
        }
        const payload = await res.json();
        const list = Array.isArray(payload.scenarios) ? payload.scenarios : [];
        if (cancelled) return;

        if (payload.contract_version && payload.contract_version !== CONTRACT_VERSION) {
          setContractMismatch({
            frontend: CONTRACT_VERSION,
            backend: payload.contract_version,
          });
        } else {
          setContractMismatch(null);
        }

        setScenarios(list);
        setScenariosState(list.length ? "ready" : "unavailable");
        setScenariosError(list.length ? null : "The backend returned an empty catalogue.");
        setSelectedScenarioId((current) => {
          if (current !== null && list.some((s) => s.scenario_id === current)) {
            return current;
          }
          return list.length ? list[0].scenario_id : null;
        });
        setBackendStatus("online");
      } catch (err) {
        if (cancelled) return;
        // No silent fallback. An empty catalogue plus an explicit message is
        // honest; stale local scenarios shown as if live are not.
        console.warn("Scenario catalogue fetch failed.", err);
        setScenarios([]);
        setScenariosState("unavailable");
        setScenariosError(err.message || String(err));
        setBackendStatus("offline");
      }
    }
    init();
    return () => {
      cancelled = true;
    };
  }, []);

  // Scroll terminal logs to bottom on update
  useEffect(() => {
    if (terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs]);

  // Phase 2: re-run backend detection whenever the selected payload changes.
  // Detection is pure computation on the backend — no LLM, no API key — so it
  // is cheap enough to run on selection rather than only on analysis.
  useEffect(() => {
    const dump = isCustomMode
      ? (() => { try { return JSON.parse(customDump); } catch { return null; } })()
      : scenarios.find(s => s.scenario_id === selectedScenarioId) || scenarios[0];
    fetchDetection(dump);
  }, [scenarios, selectedScenarioId, isCustomMode, customDump]);

  // Handle scenario selector change
  const handleScenarioChange = (e) => {
    const val = e.target.value;
    if (val === "custom") {
      setIsCustomMode(true);
    } else {
      setIsCustomMode(false);
      setSelectedScenarioId(parseInt(val, 10));
    }
  };

  // Get active scenario object
  const getActiveScenario = () => {
    if (isCustomMode) {
      try {
        return JSON.parse(customDump);
      } catch (e) {
        return null;
      }
    }
    return scenarios.find(s => s.scenario_id === selectedScenarioId) || scenarios[0];
  };

  // Phase 2: anomaly status comes from the backend detection pipeline, not from
  // a client-side approximation. This used to be a local min/max range check
  // (labelled "Z-Score Monitoring Active" until Phase 0 corrected the label),
  // which meant the UI and the backend could disagree about what was anomalous.
  //
  // detectionReport is the AnomalyReport from POST /detect. Until it arrives,
  // or if the backend is unreachable, no anomaly claim is made at all — the
  // cards render as UNKNOWN rather than guessing.
  const channelSeverity = (paramName) => {
    if (!detectionReport || !Array.isArray(detectionReport.channels)) return null;
    const finding = detectionReport.channels.find(c => c.channel === paramName);
    return finding ? finding.severity : "NOMINAL";
  };

  const channelDetectors = (paramName) => {
    if (!detectionReport || !Array.isArray(detectionReport.channels)) return [];
    const finding = detectionReport.channels.find(c => c.channel === paramName);
    return finding ? finding.detectors : [];
  };

  // Ask the backend to run detection on the selected scenario.
  const fetchDetection = async (dump) => {
    if (!dump) {
      setDetectionReport(null);
      return;
    }
    setDetectionState("loading");
    try {
      const res = await fetch(`${BACKEND_URL}${API.detect}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(dump),
      });
      if (!res.ok) throw new Error(`Detection returned status ${res.status}`);
      setDetectionReport(await res.json());
      setDetectionState("ready");
    } catch (err) {
      console.warn("Detection request failed; no anomaly claim will be shown.", err);
      setDetectionReport(null);
      setDetectionState("unavailable");
    }
  };

  // Run the FDIR diagnostic streaming analysis
  const runAnalysis = async () => {
    const dump = getActiveScenario();
    if (!dump) {
      alert("Invalid custom crash dump JSON structure.");
      return;
    }

    setIsAnalyzing(true);
    setLogs([]);
    setResult(null);
    setCompletedSteps(new Set());

    // Add initial log
    setLogs([{ type: "status", text: "Connecting to Sentinel FDIR telemetry stream..." }]);

    try {
      const response = await fetch(`${BACKEND_URL}${API.analyze}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(dump)
      });

      if (!response.ok) {
        throw new Error(`Server returned status ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop(); // Keep remaining incomplete block

        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed.startsWith("data: ")) {
            const dataStr = trimmed.substring(6).trim();
            if (!dataStr) continue;

            try {
              const event = JSON.parse(dataStr);
              handleIncomingEvent(event);
            } catch (err) {
              console.error("JSON parsing error on event chunk:", err, dataStr);
            }
          }
        }
      }
    } catch (err) {
      setLogs(prev => [...prev, {
        type: "error",
        text: `COMMUNICATION LOSS: Failed to complete diagnosis. ${err.message}`
      }]);
    } finally {
      setIsAnalyzing(false);
    }
  };

  // Process a single SSE event from the stream
  const handleIncomingEvent = (event) => {
    const { event_type, data, step_number } = event;
    const prefix = step_number ? `[STEP ${step_number}] ` : "";

    switch (event_type) {
      case "status":
        setLogs(prev => [...prev, { type: "status", text: `${prefix}${data}` }]);
        break;
      case "thought":
        setLogs(prev => [...prev, { type: "thought", text: `${prefix}🧠 Agent: "${data}"` }]);
        break;
      case "action":
        setLogs(prev => [...prev, { type: "action", text: `${prefix}⚙️ Executing Action: ${data}` }]);
        break;
      case "observation":
        setLogs(prev => [...prev, { type: "observation", text: `${prefix}📊 Telemetry Return:\n${data}` }]);
        break;
      case "error":
        setLogs(prev => [...prev, { type: "error", text: `⚠️ CRASH ENCOUNTERED: ${data}` }]);
        break;
      case "result":
        setLogs(prev => [...prev, { type: "status", text: "✅ Diagnosis complete. Formatting response." }]);
        try {
          const parsedResult = typeof data === "string" ? JSON.parse(data) : data;
          setResult(parsedResult);
        } catch (e) {
          console.error("Result parsing failed", e);
        }
        break;
      default:
        setLogs(prev => [...prev, { type: "status", text: data }]);
    }
  };

  // Toggle checklist status of recovery steps
  const toggleStep = (stepIdx) => {
    setCompletedSteps(prev => {
      const next = new Set(prev);
      if (next.has(stepIdx)) {
        next.delete(stepIdx);
      } else {
        next.add(stepIdx);
      }
      return next;
    });
  };

  const activeScenario = getActiveScenario();

  if (!isDashboardPath(currentPath)) {
    return (
      <iframe
        src="/landing.html"
        style={{
          width: "100%",
          height: "100vh",
          border: "none",
          margin: 0,
          padding: 0,
          overflow: "hidden",
          display: "block",
          backgroundColor: "#040816"
        }}
        title="SENTINEL Landing Page (demonstration)"
      />
    );
  }

  return (
    <div className="dashboard-container">
      {/* HEADER */}
      <header className="dashboard-header">
        <div className="header-brand">
          <div className="brand-logo"></div>
          <div className="brand-text">
            <h1>SENTINEL</h1>
            <span>Autonomous Spacecraft FDIR Agent</span>
          </div>
        </div>
        
        <div className="header-status-group">
          <div className="status-indicator">
            <span>Link:</span>
            <span className={`status-dot ${backendStatus === "online" ? "pulsing" : ""}`} 
                  style={{ backgroundColor: backendStatus === "online" ? "#10b981" : "#ef4444" }}>
            </span>
            <span style={{ color: backendStatus === "online" ? "#10b981" : "#ef4444" }}>
              {backendStatus === "online" ? "Online" : "Offline"}
            </span>
          </div>
          {/* Phase 0: this was a hardcoded "SAFE_MODE" badge that reflected no
              spacecraft state. It now shows the provenance of the loaded
              scenario, which IS backend-derived. */}
          <div className="status-indicator">
            <span>Data:</span>
            <span
              className="status-badge"
              style={{
                color: provenanceDisplay(activeScenario).color,
                border: `1px solid ${provenanceDisplay(activeScenario).border}`,
                backgroundColor: provenanceDisplay(activeScenario).background,
              }}
            >
              {provenanceDisplay(activeScenario).label}
            </span>
          </div>
          <a href="/" style={{
            textDecoration: "none",
            backgroundColor: "rgba(0, 229, 255, 0.1)",
            color: "#00E5FF",
            border: "1px solid rgba(0, 229, 255, 0.3)",
            cursor: "pointer",
            fontWeight: "bold",
            padding: "4px 8px",
            fontSize: "10px",
            letterSpacing: "0.08em",
            fontFamily: "monospace",
            marginLeft: "12px",
            borderRadius: "3px",
            display: "inline-flex",
            alignItems: "center",
            transition: "all 0.2s ease"
          }}
          onMouseOver={(e) => {
            e.currentTarget.style.backgroundColor = "rgba(0, 229, 255, 0.2)";
            e.currentTarget.style.boxShadow = "0 0 8px rgba(0, 229, 255, 0.4)";
          }}
          onMouseOut={(e) => {
            e.currentTarget.style.backgroundColor = "rgba(0, 229, 255, 0.1)";
            e.currentTarget.style.boxShadow = "none";
          }}>
            🛰️ MISSION CONTROL
          </a>
        </div>
      </header>

      {/* WORKSPACE GRID */}
      <main className="dashboard-grid">
        {/* LEFT COLUMN: CONTROL & TELEMETRY */}
        <div className="column">
          {/* CONTROL BOX */}
          <section className="glass-panel">
            <div className="panel-header">
              <h2><span className="panel-icon">⚏</span> Telemetry Ingestion</h2>
              <span className="telemetry-unit">Select Scenario to Begin</span>
            </div>
            
            <div className="scenario-select-wrapper">
              <select
                className="custom-select"
                onChange={handleScenarioChange}
                value={isCustomMode ? "custom" : (selectedScenarioId ?? "")}
              >
                {/* Phase 3: options come only from GET /api/v1/scenarios.
                    When the catalogue is unavailable the list is empty and the
                    notice below explains why, rather than the selector quietly
                    offering stale locally-embedded scenarios. */}
                {scenariosState === "loading" && (
                  <option value="">Loading scenario catalogue…</option>
                )}
                {scenariosState === "unavailable" && (
                  <option value="">No scenarios — backend unavailable</option>
                )}
                {scenarios.map(s => (
                  <option key={s.scenario_id} value={s.scenario_id}>
                    Scenario {s.scenario_id}: {(s.fault_type || "UNSPECIFIED FAULT").replace(/_/g, " ")} [{provenanceDisplay(s).label}]
                  </option>
                ))}
                <option value="custom">⚙️ Upload Custom Crash Dump JSON</option>
              </select>
              
              <button
                className="btn-primary"
                onClick={runAnalysis}
                disabled={
                  isAnalyzing ||
                  (isCustomMode && !customDump) ||
                  (!isCustomMode && scenariosState !== "ready")
                }
              >
                {isAnalyzing ? "Diagnosing..." : "Run FDIR Analysis"}
              </button>
            </div>

            {/* Catalogue unavailable notice. Phase 3 removed the local copy of
                the scenarios, so this is now a real state rather than one
                masked by a silent fallback. */}
            {scenariosState === "unavailable" && (
              <div style={{
                marginTop: "0.5rem",
                padding: "0.4rem 0.6rem",
                borderRadius: "4px",
                border: "1px solid rgba(239, 68, 68, 0.3)",
                background: "rgba(239, 68, 68, 0.08)",
                color: "var(--text-muted)",
                fontSize: "0.68rem",
                lineHeight: 1.5,
              }}>
                Scenario catalogue unavailable — the backend at {BACKEND_URL} could
                not be reached{scenariosError ? ` (${scenariosError})` : ""}. Scenario
                definitions live only in the backend, so none are shown. Start the
                backend and reload; you can still paste a custom crash dump below.
              </div>
            )}

            {contractMismatch && (
              <div style={{
                marginTop: "0.5rem",
                padding: "0.4rem 0.6rem",
                borderRadius: "4px",
                border: "1px solid rgba(245, 183, 77, 0.35)",
                background: "rgba(245, 183, 77, 0.10)",
                color: "rgba(245, 183, 77, 0.95)",
                fontSize: "0.68rem",
                lineHeight: 1.5,
              }}>
                CONTRACT VERSION MISMATCH — this dashboard was generated against
                contract {contractMismatch.frontend}; the backend serves{" "}
                {contractMismatch.backend}. Fields may be missing or renamed.
                Regenerate with{" "}
                <code>python3 sentinel/backend/scripts/export_contracts.py</code>.
              </div>
            )}

            {/* Provenance badge.
                Previously this decided "REAL ESA TELEMETRY" by testing whether
                source_type merely CONTAINED the string "ESA", which labelled
                metadata-derived synthetic payloads as real. It now renders the
                scenario's declared provenance code, and unrecognised values
                resolve to PROVENANCE UNKNOWN rather than to REAL. */}
            {!isCustomMode && activeScenario && (() => {
              const prov = provenanceDisplay(activeScenario);
              const badgeColor = prov.color;
              const bgColor = prov.background;
              const borderColor = prov.border;
              return (
                <div style={{ marginTop: "0.5rem" }}>
                  <span style={{
                    display: "inline-block",
                    padding: "0.15rem 0.5rem",
                    borderRadius: "3px",
                    fontSize: "0.65rem",
                    fontWeight: "700",
                    letterSpacing: "0.05em",
                    color: badgeColor,
                    border: `1px solid ${borderColor}`,
                    background: bgColor,
                    marginBottom: "0.3rem",
                  }}>
                    {prov.label}
                  </span>
                  {!isRealTelemetry(activeScenario) && (
                    <span style={{
                      display: "inline-block",
                      marginLeft: "0.4rem",
                      padding: "0.15rem 0.5rem",
                      borderRadius: "3px",
                      fontSize: "0.65rem",
                      fontWeight: "700",
                      letterSpacing: "0.08em",
                      color: "#1A1200",
                      background: "#F59E0B",
                    }}>
                      SIMULATION
                    </span>
                  )}
                  {activeScenario.source_note && (
                    <div style={{
                      padding: "0.4rem 0.6rem",
                      background: bgColor,
                      border: `1px solid ${borderColor}`,
                      borderRadius: "4px",
                      fontSize: "0.7rem",
                      color: "var(--text-muted)",
                      fontStyle: "italic",
                      marginTop: "0.25rem",
                    }}>
                      ℹ️ {activeScenario.source_note}
                    </div>
                  )}
                </div>
              );
            })()}

            {isCustomMode && (
              <div style={{ marginTop: "1rem" }}>
                <textarea
                  className="custom-select"
                  style={{ width: "100%", height: "120px", fontFamily: "monospace", fontSize: "0.75rem" }}
                  placeholder='Paste crash dump JSON here (e.g. { "scenario_id": 4, "fault_type": "EPS_SOLAR_UNDERVOLT", ... })'
                  value={customDump}
                  onChange={(e) => setCustomDump(e.target.value)}
                />
              </div>
            )}
          </section>

          {/* TELEMETRY MATRIX */}
          {activeScenario && canonicalWindow(activeScenario).length > 0 && (
            <section className="glass-panel">
              <div className="panel-header">
                <h2><span className="panel-icon">📊</span> Pre-Fault Telemetry Window</h2>
                {/* Phase 2: anomaly status is the backend's, not the client's. */}
                <span className="telemetry-unit">
                  {detectionState === "ready"
                    ? `Backend detection: ${detectionReport.anomaly_count} finding(s) on ${detectionReport.anomalous_channels}/${detectionReport.total_channels} channel(s)`
                    : detectionState === "loading"
                    ? "Running backend detection…"
                    : "Detection unavailable — no anomaly status shown"}
                </span>
              </div>

              {detectionState === "unavailable" && (
                <div style={{
                  padding: "0.4rem 0.6rem",
                  marginBottom: "0.5rem",
                  borderRadius: "4px",
                  border: "1px solid rgba(148, 163, 184, 0.3)",
                  background: "rgba(148, 163, 184, 0.08)",
                  color: "var(--text-muted)",
                  fontSize: "0.68rem",
                }}>
                  The backend detection pipeline could not be reached. Anomaly status is
                  shown as UNKNOWN rather than computed in the browser — a client-side
                  approximation would not match what the backend actually detects.
                </div>
              )}

              {/* Phase 3: rendered from the CANONICAL window. One card per
                  channel showing its most recent sample — the window is a time
                  series, so the previous per-row render over the deprecated
                  array had neither timing nor status to show. An unusable
                  reading now displays its preserved text ("NaN" / "MISSING")
                  instead of NaN-from-Number() or a blank. */}
              <div className="telemetry-grid">
                {latestPerChannel(activeScenario).map((param, idx) => {
                  const severity = channelSeverity(param.parameter);
                  const detectors = channelDetectors(param.parameter);
                  const abnormal =
                    severity !== null && severity !== "NOMINAL";
                  const unknown = severity === null;
                  return (
                    <div
                      key={param.parameter || idx}
                      className={`telemetry-card ${abnormal ? "anomalous" : ""}`}
                      title={
                        abnormal
                          ? `${severity} — detected by ${detectors.join(", ")}`
                          : unknown
                          ? "No detection result available"
                          : "No anomaly detected on this channel"
                      }
                    >
                      <span className="telemetry-label">{param.parameter}</span>
                      <span className="telemetry-value">
                        {formatReading(param)}
                      </span>
                      <span className="telemetry-range">
                        {formatRange(param)}
                      </span>
                      <span className="telemetry-range" style={{
                        color: abnormal
                          ? "var(--color-rose, #ef4444)"
                          : "var(--text-muted)",
                        letterSpacing: "0.04em",
                      }}>
                        {param.timestamp ? `${param.timestamp} · ` : ""}
                        {unknown ? "UNKNOWN" : abnormal ? severity : "NOMINAL"}
                      </span>
                    </div>
                  );
                })}
              </div>

              {detectionState === "ready" && detectionReport.warnings.length > 0 && (
                <div style={{
                  marginTop: "0.6rem",
                  padding: "0.4rem 0.6rem",
                  borderRadius: "4px",
                  border: "1px solid rgba(245, 183, 77, 0.25)",
                  background: "rgba(245, 183, 77, 0.08)",
                  color: "rgba(245, 183, 77, 0.95)",
                  fontSize: "0.66rem",
                  lineHeight: 1.5,
                }}>
                  {detectionReport.warnings.map((w, i) => (
                    <div key={i}>· {w}</div>
                  ))}
                </div>
              )}
            </section>
          )}

          {/* STREAMING REASONING CONSOLE */}
          <section className="glass-panel" style={{ flex: 1 }}>
            <div className="panel-header">
              <h2><span className="panel-icon">⌨</span> FDIR Agent Live Thoughts</h2>
              {/* Phase 0: was a decorative "SSE Streaming Active" label that
                  was shown regardless of whether a stream was open. */}
              <span className="telemetry-unit">
                {isAnalyzing ? "Streaming from backend" : backendStatus === "online" ? "Backend reachable — idle" : "Backend offline"}
              </span>
            </div>
            
            <div className="console-terminal">
              {logs.length === 0 ? (
                <div style={{ color: "var(--text-muted)", fontStyle: "italic" }}>
                  Awaiting telemetry ingestion command...
                </div>
              ) : (
                logs.map((log, idx) => (
                  <div key={idx} className="console-row">
                    <span className={`console-content ${log.type}`}>
                      {log.type === "status" && <span className="console-prefix">&gt;</span>}
                      {log.text}
                    </span>
                  </div>
                ))
              )}
              {isAnalyzing && <span className="console-cursor"></span>}
              <div ref={terminalEndRef} />
            </div>
          </section>
        </div>

        {/* RIGHT COLUMN: DIAGNOSTIC HYPOTHESES & RECOVERY PLAN */}
        <div className="column">
          {/* DIAGNOSIS HYPOTHESES */}
          <section className="glass-panel" style={{ flex: result ? "none" : 1 }}>
            <div className="panel-header">
              <h2><span className="panel-icon">⚕</span> Multi-Hypothesis Analysis</h2>
              {result && (
                <span className="status-badge" style={{ 
                  backgroundColor: result.requires_human_review ? "rgba(239, 68, 68, 0.15)" : "rgba(16, 185, 129, 0.15)",
                  color: result.requires_human_review ? "var(--color-rose)" : "var(--color-emerald)",
                  border: result.requires_human_review ? "1px solid var(--color-rose)" : "1px solid var(--color-emerald)"
                }}>
                  {/* Phase 0: the false branch previously read "AUTO RECOVERY
                      PERMITTED". SENTINEL never executes or uplinks a command,
                      so no recovery is ever auto-permitted. */}
                  {result.requires_human_review ? "HUMAN REVIEW REQUIRED" : "OPERATOR APPROVAL REQUIRED"}
                </span>
              )}
            </div>

            {!result ? (
              <div className="empty-state">
                <div className="empty-icon">⚕</div>
                <h3>No Diagnosis Generated</h3>
                <p>Run FDIR analysis on a scenario to generate a multi-hypothesis diagnostic table.</p>
              </div>
            ) : (
              <div className="hypotheses-container">
                {result.hypotheses?.map((hypo, idx) => (
                  <div key={idx} className={`hypothesis-row ${hypo.rank === 1 ? "rank-1" : ""}`}>
                    <div className="hypothesis-rank">
                      <span className="rank-num">{hypo.rank}</span>
                      <span className="rank-lbl">Rank</span>
                    </div>
                    <div className="hypothesis-body">
                      <div className="hypo-info">
                        <h3>{hypo.root_cause.replace(/_/g, " ")}</h3>
                        <span className="hypo-comp">Affected Component: <strong>{hypo.affected_component}</strong></span>
                      </div>
                      <div className="hypo-confidence">
                        <div className="conf-bar-wrapper">
                          <div className="conf-bar" style={{ width: `${hypo.confidence * 100}%` }}></div>
                        </div>
                        <span className="conf-text">{(hypo.confidence * 100).toFixed(0)}%</span>
                      </div>
                      
                      {hypo.causal_chain && hypo.causal_chain.length > 0 && (
                        <div className="causal-timeline">
                          <span className="timeline-title">Telemetry Causal Propagation Chain</span>
                          <div className="timeline-steps">
                            {hypo.causal_chain.map((cstep, sidx) => (
                              <React.Fragment key={sidx}>
                                {sidx > 0 && <span className="timeline-arrow">➔</span>}
                                <span className="timeline-node">{cstep}</span>
                              </React.Fragment>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
                
                {/* Phase 0: make explicit that these are LLM-generated
                    hypotheses, not a validated diagnosis. */}
                <div style={{
                  marginTop: "0.6rem",
                  padding: "0.4rem 0.6rem",
                  borderRadius: "4px",
                  border: "1px solid rgba(245, 183, 77, 0.25)",
                  background: "rgba(245, 183, 77, 0.08)",
                  color: "rgba(245, 183, 77, 0.95)",
                  fontSize: "0.68rem",
                  letterSpacing: "0.04em",
                }}>
                  LLM-generated hypotheses, ranked by the model. Not a validated diagnosis —
                  no physics or state-estimation check has been applied.
                </div>

                {result.reasoning_summary && (
                  <div className="summary-panel-content">
                    <h3>Reasoning Summary (model-generated)</h3>
                    <p>{result.reasoning_summary}</p>
                  </div>
                )}
              </div>
            )}
          </section>

          {/* RECOVERY PLAN */}
          {result && result.recovery_plan && (
            <section className="glass-panel" style={{ flex: 1 }}>
              <div className="panel-header">
                <h2><span className="panel-icon">🔧</span> Proposed Recovery Steps</h2>
                {/* Phase 0: previously read "ECSS Standardized Commands". The
                    commands are LLM-proposed and filtered by the deterministic
                    safety whitelist; they are not ECSS-standardised commands
                    and carry no compliance assertion. */}
                <span className="telemetry-unit">LLM-proposed · safety-filtered · operator approval required</span>
              </div>

              {result.requires_human_review && (
                <div className="human-review-banner">
                  <span className="review-icon">⚠</span>
                  <div className="review-message">
                    <h4>Ground station authorization required</h4>
                    <p>Safety parameters indicate high risk levels or lower overall confidence. Ground command approval is required prior to execution.</p>
                  </div>
                </div>
              )}

              <div className="recovery-steps-list">
                {result.recovery_plan.map((step, idx) => {
                  const completed = completedSteps.has(idx);
                  return (
                    <div key={idx} className={`recovery-step-card ${completed ? "completed" : ""}`}>
                      <div className="step-checkbox-wrapper">
                        <input
                          type="checkbox"
                          className="step-checkbox"
                          checked={completed}
                          onChange={() => toggleStep(idx)}
                          aria-label={`Mark step ${idx + 1} as completed`}
                        />
                      </div>
                      <div className="step-details">
                        <div className="step-header-row">
                          <span className="step-cmd">{idx + 1}. {step.command}</span>
                          <div className="step-badge-group">
                            <span className="badge wait-seconds">Wait: {step.wait_seconds}s</span>
                            <span className={`badge risk-${step.risk.toLowerCase()}`}>Risk: {step.risk}</span>
                          </div>
                        </div>
                        <p className="step-rationale">{step.rationale}</p>
                        <div className="step-verify">
                          <span className="step-verify-label">Verify Target:</span>
                          <span>{step.verify}</span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          )}
        </div>
      </main>
    </div>
  );
}

export default App;
