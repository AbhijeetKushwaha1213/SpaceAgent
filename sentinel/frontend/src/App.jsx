import React, { useState, useEffect, useRef } from "react";
import "./App.css";

const BACKEND_URL = "http://localhost:8000";

// Robust local preset scenarios to fall back on if backend is starting up or unreachable
const LOCAL_PRESET_SCENARIOS = [
  {
    "scenario_id": 1,
    "fault_type": "ADCS_SENSOR_FAULT",
    "fault_register": "0x00000080",
    "pre_fault_telemetry": [
      {"parameter": "Gyro_rate_degs", "value": "NaN", "nominal_min": 0.0, "nominal_max": 7.0},
      {"parameter": "Attitude_error_deg", "value": 7.3, "nominal_min": 0.0, "nominal_max": 0.01},
      {"parameter": "SEU_counter", "value": 3.0, "nominal_min": 0.0, "nominal_max": 0.0},
      {"parameter": "RW_speed_rpm", "value": 4500.0, "nominal_min": -6000.0, "nominal_max": 6000.0},
      {"parameter": "V_bat", "value": 30.2, "nominal_min": 28.0, "nominal_max": 33.6},
      {"parameter": "SoC_pct", "value": 85.0, "nominal_min": 20.0, "nominal_max": 100.0},
      {"parameter": "I_sa", "value": 8.4, "nominal_min": 0.0, "nominal_max": 12.0},
      {"parameter": "OBC_temp_C", "value": 24.5, "nominal_min": -10.0, "nominal_max": 60.0}
    ],
    "event_log": [
      {"timestamp": "T-62s", "source": "OBC_KERNEL", "message": "SEU counter incremented: 3"},
      {"timestamp": "T-60s", "source": "ADCS_MANAGER", "message": "GYRO_A health status: NaN"},
      {"timestamp": "T-30s", "source": "ADCS_ATTITUDE", "message": "Attitude error exceeded threshold (7.3 deg)"},
      {"timestamp": "T-0s", "source": "FDIR_CORE", "message": "Safe Mode entry triggered by ADCS_ERROR"}
    ],
    "hardware_state": {
      "active_gyro": "A",
      "seu_flags": "0x03",
      "watchdog_status": "nominal"
    },
    "operating_context": {
      "eclipse_fraction": 0.0,
      "sun_sensor_angle_deg": 12.5,
      "time_since_contact_s": 1200
    }
  },
  {
    "scenario_id": 2,
    "fault_type": "EPS_POWER_FAULT",
    "fault_register": "0x00000002",
    "pre_fault_telemetry": [
      {"parameter": "I_sa", "value": 0.0, "nominal_min": 0.0, "nominal_max": 12.0},
      {"parameter": "V_bat", "value": 21.8, "nominal_min": 28.0, "nominal_max": 33.6},
      {"parameter": "SoC_pct", "value": 14.2, "nominal_min": 20.0, "nominal_max": 100.0},
      {"parameter": "V_bus", "value": 24.1, "nominal_min": 26.6, "nominal_max": 29.4},
      {"parameter": "Heater_power_W", "value": 15.0, "nominal_min": 0.0, "nominal_max": 50.0},
      {"parameter": "Attitude_error_deg", "value": 0.004, "nominal_min": 0.0, "nominal_max": 0.01},
      {"parameter": "OBC_temp_C", "value": 18.2, "nominal_min": -10.0, "nominal_max": 60.0}
    ],
    "event_log": [
      {"timestamp": "T-300s", "source": "EPS_SENSORS", "message": "Solar Array A Current dropped to 0A (expected: 8.5A)"},
      {"timestamp": "T-180s", "source": "EPS_MANAGER", "message": "State of Charge low (14.2%). Starting load shedding."},
      {"timestamp": "T-120s", "source": "OBC_CORE", "message": "Command issued: Power off PYLD subsystem"},
      {"timestamp": "T-0s", "source": "FDIR_CORE", "message": "Safe Mode entry triggered by EPS_UNDER_VOLT"}
    ],
    "hardware_state": {
      "solar_relay": "open",
      "battery_relays": "closed",
      "shed_status": "active"
    },
    "operating_context": {
      "eclipse_fraction": 0.0,
      "sun_sensor_angle_deg": 42.0,
      "time_since_contact_s": 2400
    }
  },
  {
    "scenario_id": 3,
    "fault_type": "OBC_SOFTWARE_FAULT",
    "fault_register": "0x00000040",
    "pre_fault_telemetry": [
      {"parameter": "CPU_load_pct", "value": 100.0, "nominal_min": 0.0, "nominal_max": 70.0},
      {"parameter": "Memory_usage_MB", "value": 495.0, "nominal_min": 0.0, "nominal_max": 500.0},
      {"parameter": "Watchdog_counter", "value": 1002.0, "nominal_min": 0.0, "nominal_max": 1000.0},
      {"parameter": "V_bat", "value": 31.1, "nominal_min": 28.0, "nominal_max": 33.6},
      {"parameter": "SoC_pct", "value": 90.0, "nominal_min": 20.0, "nominal_max": 100.0},
      {"parameter": "Attitude_error_deg", "value": 0.003, "nominal_min": 0.0, "nominal_max": 0.01}
    ],
    "event_log": [
      {"timestamp": "T-180s", "source": "OBC_MONITOR", "message": "CPU load exceeded 95%"},
      {"timestamp": "T-120s", "source": "OBC_MONITOR", "message": "Memory leak signature detected in thread 'attitude_control'"},
      {"timestamp": "T-10s", "source": "WATCHDOG_TIMER", "message": "Watchdog counter exceeded limit (value=1002)"},
      {"timestamp": "T-0s", "source": "OBC_BOOT", "message": "Watchdog reset triggered. Booting in Safe Mode."}
    ],
    "hardware_state": {
      "watchdog_state": "expired",
      "active_bank": "EEPROM_B",
      "last_reboot_cause": "WATCHDOG_RESET"
    },
    "operating_context": {
      "eclipse_fraction": 0.2,
      "sun_sensor_angle_deg": 15.0,
      "time_since_contact_s": 50
    }
  }
];

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

  const [scenarios, setScenarios] = useState(LOCAL_PRESET_SCENARIOS);
  const [selectedScenarioId, setSelectedScenarioId] = useState(1);
  const [customDump, setCustomDump] = useState("");
  const [isCustomMode, setIsCustomMode] = useState(false);
  
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [logs, setLogs] = useState([]);
  const [result, setResult] = useState(null);
  const [completedSteps, setCompletedSteps] = useState(new Set());
  const [backendStatus, setBackendStatus] = useState("checking"); // "online" | "offline"
  
  const terminalEndRef = useRef(null);

  // Fetch scenarios from backend on mount
  useEffect(() => {
    async function init() {
      try {
        const res = await fetch(`${BACKEND_URL}/scenarios`);
        if (res.ok) {
          const data = await res.json();
          setScenarios(data);
          setBackendStatus("online");
        } else {
          setBackendStatus("offline");
        }
      } catch (err) {
        console.warn("Backend scenarios fetch failed, using local presets.", err);
        setBackendStatus("offline");
      }
    }
    init();
  }, []);

  // Scroll terminal logs to bottom on update
  useEffect(() => {
    if (terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs]);

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

  // Check if a parameter is anomalous
  const isAnomalous = (param) => {
    const val = parseFloat(param.value);
    if (isNaN(val)) return true; // NaN is always anomalous
    return val < param.nominal_min || val > param.nominal_max;
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
      const response = await fetch(`${BACKEND_URL}/analyze`, {
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

  if (currentPath !== "/dashboard") {
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
        title="SENTINEL Landing Page"
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
          <div className="status-indicator">
            <span>Mode:</span>
            <span className="status-badge safe-mode">SAFE_MODE</span>
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
              <select className="custom-select" onChange={handleScenarioChange} value={isCustomMode ? "custom" : selectedScenarioId}>
                {scenarios.map(s => (
                  <option key={s.scenario_id} value={s.scenario_id}>
                    Scenario {s.scenario_id}: {s.fault_type.replace(/_/g, " ")}
                  </option>
                ))}
                <option value="custom">⚙️ Upload Custom Crash Dump JSON</option>
              </select>
              
              <button className="btn-primary" onClick={runAnalysis} disabled={isAnalyzing || (isCustomMode && !customDump)}>
                {isAnalyzing ? "Diagnosing..." : "Run FDIR Analysis"}
              </button>
            </div>

            {isCustomMode && (
              <div style={{ marginTop: "1rem" }}>
                <textarea
                  className="custom-select"
                  style={{ width: "100%", height: "120px", fontFamily: "monospace", fontSize: "0.75rem" }}
                  placeholder='Paste crash dump JSON here (e.g. { "scenario_id": 4, "fault_type": "EPS_POWER_FAULT", ... })'
                  value={customDump}
                  onChange={(e) => setCustomDump(e.target.value)}
                />
              </div>
            )}
          </section>

          {/* TELEMETRY MATRIX */}
          {activeScenario && activeScenario.pre_fault_telemetry && (
            <section className="glass-panel">
              <div className="panel-header">
                <h2><span className="panel-icon">📊</span> Pre-Fault Telemetry Window</h2>
                <span className="telemetry-unit">Z-Score Monitoring Active</span>
              </div>
              
              <div className="telemetry-grid">
                {activeScenario.pre_fault_telemetry.map((param, idx) => {
                  const abnormal = isAnomalous(param);
                  return (
                    <div key={idx} className={`telemetry-card ${abnormal ? "anomalous" : ""}`}>
                      <span className="telemetry-label">{param.parameter}</span>
                      <span className="telemetry-value">
                        {param.value === "NaN" ? "NaN" : Number(param.value).toFixed(1)}
                      </span>
                      <span className="telemetry-range">
                        Range: {param.nominal_min}–{param.nominal_max}
                      </span>
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          {/* STREAMING REASONING CONSOLE */}
          <section className="glass-panel" style={{ flex: 1 }}>
            <div className="panel-header">
              <h2><span className="panel-icon">⌨</span> FDIR Agent Live Thoughts</h2>
              <span className="telemetry-unit">SSE Streaming Active</span>
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
                  {result.requires_human_review ? "HUMAN REVIEW REQ." : "AUTO RECOVERY PERMITTED"}
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
                
                {result.reasoning_summary && (
                  <div className="summary-panel-content">
                    <h3>Diagnostic Reasoning Summary</h3>
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
                <h2><span className="panel-icon">🔧</span> Step-by-Step Recovery Procedures</h2>
                <span className="telemetry-unit">ECSS Standardized Commands</span>
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
