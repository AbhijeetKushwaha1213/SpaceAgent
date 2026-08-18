import React from "react";

export default function MissionOverview({
  scenario,
  analysisOutput,
  systemStatus,
  isAnalyzing,
  onNavigateToTab,
}) {
  const telemetry = scenario?.pre_fault_telemetry || [];
  const faultType = scenario?.fault_type || "N/A";
  const incidentId = scenario?.incident_id || "N/A";
  const trigger = scenario?.safe_mode_trigger || "NOT AVAILABLE";
  const register = scenario?.fault_register || "0x00000000";

  // Subsystem status calculation from real telemetry
  const subsystems = [
    { name: "ADCS", params: ["GYRO_A_RATE", "GYRO_B_RATE", "ATTITUDE_ERROR", "RW_SPEED"] },
    { name: "EPS", params: ["SOLAR_ARRAY_CURRENT", "BUS_VOLTAGE", "BATTERY_SOC", "BATTERY_TEMP"] },
    { name: "OBC", params: ["WATCHDOG_COUNTER", "CPU_LOAD", "OBC_TEMP", "MEMORY_USAGE"] },
    { name: "TCS", params: ["OBC_TEMP", "BATTERY_TEMP", "HEATER_STATE_A"] },
    { name: "COMMS", params: ["TRANSPONDER_LOCK", "COMMS_SIGNAL_DBM"] },
    { name: "PYLD", params: ["PAYLOAD_STATUS", "PAYLOAD_POWER"] },
  ];

  const getSubsystemStatus = (sub) => {
    const matched = telemetry.filter((t) => sub.params.includes(t.parameter));
    if (matched.length === 0) return { status: "NOMINAL", label: "NOMINAL [NO ANOMALIES]" };
    const hasCritical = matched.some((t) => (t.status || "").toUpperCase().includes("CRITICAL") || String(t.value).includes("NaN"));
    const hasAnom = matched.some((t) => (t.status || "").toUpperCase().includes("ANOMALOUS") || (t.status || "").toUpperCase().includes("WARNING"));
    if (hasCritical) return { status: "CRITICAL", label: "CRITICAL ANOMALY DETECTED" };
    if (hasAnom) return { status: "WARNING", label: "DEGRADED / ANOMALOUS" };
    return { status: "NOMINAL", label: "NOMINAL" };
  };

  const anomalies = telemetry.filter((t) =>
    (t.status || "").toUpperCase() !== "NOMINAL" || String(t.value).includes("NaN")
  );

  return (
    <section className="ops-view-container" aria-labelledby="overview-heading">
      <div className="view-title-bar">
        <h1 id="overview-heading" className="view-title">
          MISSION OVERVIEW &amp; SUBSYSTEM TELEMETRY STATE
        </h1>
        <div className="view-actions">
          <span className="info-chip">INCIDENT ID: {incidentId}</span>
          <span className="info-chip alert">TRIGGER: {trigger}</span>
        </div>
      </div>

      {/* Top Telemetry Summary Grid */}
      <div className="ops-grid grid-4">
        <div className="ops-card">
          <div className="card-header">SPACECRAFT MODE</div>
          <div className="card-body stat-block">
            <div className="stat-value red">SAFE MODE</div>
            <div className="stat-sub">TRIGGER REG: {register}</div>
          </div>
        </div>

        <div className="ops-card">
          <div className="card-header">PRIMARY FAULT CLASS</div>
          <div className="card-body stat-block">
            <div className="stat-value amber">{faultType}</div>
            <div className="stat-sub">DETECTION CONTEXT: {scenario?.source_note || "CRASH DUMP"}</div>
          </div>
        </div>

        <div className="ops-card">
          <div className="card-header">ACTIVE ANOMALIES</div>
          <div className="card-body stat-block">
            <div className="stat-value red">{anomalies.length} CHANNELS</div>
            <div className="stat-sub">TOTAL MONITORED: {telemetry.length}</div>
          </div>
        </div>

        <div className="ops-card">
          <div className="card-header">AI SOVEREIGNTY / MODE</div>
          <div className="card-body stat-block">
            <div className={`stat-value ${systemStatus?.llm_mode === "LOCAL" ? "cyan" : "amber"}`}>
              {systemStatus?.llm_mode || "STUB"} AI
            </div>
            <div className="stat-sub">MODEL: {systemStatus?.model || "STUB"}</div>
          </div>
        </div>
      </div>

      {/* Subsystems Health Grid */}
      <div className="section-block">
        <h2 className="section-title">SUBSYSTEM HEALTH ASSESSMENT</h2>
        <div className="ops-grid grid-3">
          {subsystems.map((sub) => {
            const res = getSubsystemStatus(sub);
            return (
              <div key={sub.name} className={`ops-card status-border-${res.status.toLowerCase()}`}>
                <div className="card-header flex-between">
                  <span>SUBSYSTEM: {sub.name}</span>
                  <span className={`badge-pill badge-${res.status.toLowerCase()}`}>
                    [{res.status}]
                  </span>
                </div>
                <div className="card-body">
                  <div className="ops-kv">
                    <span className="kv-key">OPERATIONAL STATE:</span>
                    <span className="kv-val">{res.label}</span>
                  </div>
                  <div className="ops-kv">
                    <span className="kv-key">MONITORED CHANNELS:</span>
                    <span className="kv-val">{sub.params.join(", ")}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Active Anomalies Table */}
      <div className="section-block">
        <div className="flex-between">
          <h2 className="section-title">ACTIVE DETECTED ANOMALIES</h2>
          <button
            className="ops-btn ops-btn-sm"
            onClick={() => onNavigateToTab("investigation")}
          >
            [VIEW FAULT INVESTIGATION &rarr;]
          </button>
        </div>

        {anomalies.length === 0 ? (
          <div className="ops-empty-state">NO ANOMALIES DETECTED IN CURRENT TELEMETRY WINDOW</div>
        ) : (
          <table className="ops-table" aria-label="Active anomalies list">
            <thead>
              <tr>
                <th>TIMESTAMP</th>
                <th>PARAMETER / CHANNEL</th>
                <th>OBSERVED VALUE</th>
                <th>NOMINAL RANGE</th>
                <th>SEVERITY</th>
                <th>ACTION</th>
              </tr>
            </thead>
            <tbody>
              {anomalies.map((row, idx) => (
                <tr key={idx} className={row.status === "CRITICAL" ? "row-critical" : "row-warning"}>
                  <td className="mono">{row.timestamp || "T-0s"}</td>
                  <td className="mono bold">{row.parameter}</td>
                  <td className="mono red bold">{String(row.value)} {row.unit || ""}</td>
                  <td className="mono">{row.nominal_min ?? "N/A"} to {row.nominal_max ?? "N/A"} {row.unit || ""}</td>
                  <td>
                    <span className={`badge-pill badge-${(row.status || "ANOMALOUS").toLowerCase()}`}>
                      [{(row.status || "ANOMALOUS").toUpperCase()}]
                    </span>
                  </td>
                  <td>
                    <button
                      className="ops-btn ops-btn-xs"
                      onClick={() => onNavigateToTab("telemetry")}
                    >
                      [PLOT CHANNEL]
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* FDIR Pipeline Status */}
      <div className="section-block">
        <h2 className="section-title">FDIR PIPELINE EXECUTION STATE</h2>
        <div className="ops-grid grid-4">
          <div className="ops-card">
            <div className="card-header">1. ANOMALY DETECTION</div>
            <div className="card-body">
              <span className="badge-pill badge-nominal">[ACTIVE - {telemetry.length} ROWS]</span>
            </div>
          </div>
          <div className="ops-card">
            <div className="card-header">2. PHYSICS &amp; ESTIMATION</div>
            <div className="card-body">
              <span className="badge-pill badge-nominal">[VALIDATED]</span>
            </div>
          </div>
          <div className="ops-card">
            <div className="card-header">3. RAG PROCEDURE RETRIEVAL</div>
            <div className="card-body">
              <span className="badge-pill badge-nominal">[ECSS LIBRARY LOADED]</span>
            </div>
          </div>
          <div className="ops-card">
            <div className="card-header">4. CONSTRAINED LLM RANKING</div>
            <div className="card-body">
              <span className={`badge-pill badge-${analysisOutput ? "nominal" : "warning"}`}>
                [{analysisOutput ? "COMPLETED" : "READY"}]
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
