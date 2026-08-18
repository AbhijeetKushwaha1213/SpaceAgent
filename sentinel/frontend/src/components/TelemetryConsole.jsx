import React, { useState } from "react";

export default function TelemetryConsole({ scenario }) {
  const telemetry = scenario?.pre_fault_telemetry || [];
  const [selectedSubsystem, setSelectedSubsystem] = useState("ALL");
  const [selectedChannel, setSelectedChannel] = useState(telemetry[0]?.parameter || "");

  // Extract unique parameters & subsystems
  const allParams = Array.from(new Set(telemetry.map((t) => t.parameter)));
  const filteredParams = allParams.filter((p) => {
    if (selectedSubsystem === "ALL") return true;
    if (selectedSubsystem === "ADCS") return p.includes("GYRO") || p.includes("ATTITUDE") || p.includes("RW");
    if (selectedSubsystem === "EPS") return p.includes("SOLAR") || p.includes("BUS") || p.includes("BATTERY");
    if (selectedSubsystem === "OBC") return p.includes("WATCHDOG") || p.includes("CPU") || p.includes("MEMORY");
    if (selectedSubsystem === "TCS") return p.includes("TEMP") || p.includes("HEATER");
    if (selectedSubsystem === "COMMS") return p.includes("TRANSPONDER") || p.includes("SIGNAL");
    return true;
  });

  const activeChannelName = selectedChannel || filteredParams[0] || "";
  const channelData = telemetry.filter((t) => t.parameter === activeChannelName);

  // Bounds
  const nominalMin = channelData[0]?.nominal_min ?? null;
  const nominalMax = channelData[0]?.nominal_max ?? null;
  const unit = channelData[0]?.unit || "";

  // Helper for numeric extraction
  const getNumericVal = (val) => {
    if (typeof val === "number") return val;
    if (val === "NaN" || val === null || val === undefined) return NaN;
    const parsed = parseFloat(val);
    return isNaN(parsed) ? NaN : parsed;
  };

  const validVals = channelData.map((d) => getNumericVal(d.value)).filter((v) => !isNaN(v));
  let minVal = Math.min(...validVals, nominalMin ?? 0);
  let maxVal = Math.max(...validVals, nominalMax ?? 10);
  if (minVal === maxVal) {
    minVal -= 1;
    maxVal += 1;
  }
  const valRange = maxVal - minVal || 1;

  // Generate SVG path coordinates
  const svgWidth = 700;
  const svgHeight = 220;
  const padding = 30;

  const points = channelData.map((d, idx) => {
    const x = padding + (idx / Math.max(channelData.length - 1, 1)) * (svgWidth - 2 * padding);
    const num = getNumericVal(d.value);
    const y = isNaN(num)
      ? svgHeight / 2
      : svgHeight - padding - ((num - minVal) / valRange) * (svgHeight - 2 * padding);
    return { x, y, val: d.value, isNaN: isNaN(num), status: d.status, ts: d.timestamp };
  });

  const pathD = points
    .filter((p) => !p.isNaN)
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`)
    .join(" ");

  const minLimitY = nominalMin !== null ? svgHeight - padding - ((nominalMin - minVal) / valRange) * (svgHeight - 2 * padding) : null;
  const maxLimitY = nominalMax !== null ? svgHeight - padding - ((nominalMax - minVal) / valRange) * (svgHeight - 2 * padding) : null;

  return (
    <section className="ops-view-container" aria-labelledby="telemetry-heading">
      <div className="view-title-bar">
        <h1 id="telemetry-heading" className="view-title">
          TIME-SERIES TELEMETRY CONSOLE &amp; HARD LIMIT ANALYSIS
        </h1>
        <div className="view-actions">
          <span className="info-chip">SCENARIO: {scenario?.scenario_id || "N/A"}</span>
          <span className="info-chip">TOTAL CHANNELS: {allParams.length}</span>
        </div>
      </div>

      {/* Filter Controls */}
      <div className="ops-card ops-toolbar">
        <div className="toolbar-group">
          <label className="ops-label" htmlFor="subsystem-filter">SUBSYSTEM FILTER:</label>
          <select
            id="subsystem-filter"
            className="ops-select"
            value={selectedSubsystem}
            onChange={(e) => {
              setSelectedSubsystem(e.target.value);
              const firstMatch = allParams.find((p) => {
                if (e.target.value === "ALL") return true;
                if (e.target.value === "ADCS") return p.includes("GYRO") || p.includes("ATTITUDE");
                if (e.target.value === "EPS") return p.includes("SOLAR") || p.includes("BUS");
                if (e.target.value === "OBC") return p.includes("WATCHDOG") || p.includes("CPU");
                if (e.target.value === "TCS") return p.includes("TEMP");
                if (e.target.value === "COMMS") return p.includes("TRANSPONDER");
                return true;
              });
              setSelectedChannel(firstMatch || "");
            }}
          >
            <option value="ALL">ALL SUBSYSTEMS</option>
            <option value="ADCS">ADCS (ATTITUDE / GYRO)</option>
            <option value="EPS">EPS (POWER / SOLAR)</option>
            <option value="OBC">OBC (COMPUTER / WATCHDOG)</option>
            <option value="TCS">TCS (THERMAL)</option>
            <option value="COMMS">COMMS (TRANSPONDER)</option>
          </select>
        </div>

        <div className="toolbar-group">
          <label className="ops-label" htmlFor="channel-filter">CHANNEL / PARAMETER:</label>
          <select
            id="channel-filter"
            className="ops-select"
            value={activeChannelName}
            onChange={(e) => setSelectedChannel(e.target.value)}
          >
            {filteredParams.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Plot Panel */}
      <div className="section-block">
        <div className="ops-card">
          <div className="card-header flex-between">
            <span>CHANNEL PLOT: {activeChannelName} ({unit || "UNITLESS"})</span>
            <span className="mono fs-xs">
              MIN LIMIT: {nominalMin ?? "N/A"} | MAX LIMIT: {nominalMax ?? "N/A"}
            </span>
          </div>
          <div className="card-body plot-body">
            {channelData.length === 0 ? (
              <div className="ops-empty-state">NO TELEMETRY POINTS AVAILABLE FOR THIS CHANNEL</div>
            ) : (
              <div className="svg-plot-wrapper">
                <svg viewBox={`0 0 ${svgWidth} ${svgHeight}`} className="telemetry-svg">
                  {/* Grid Lines */}
                  <line x1={padding} y1={padding} x2={svgWidth - padding} y2={padding} stroke="#1E293B" strokeDasharray="3 3" />
                  <line x1={padding} y1={svgHeight / 2} x2={svgWidth - padding} y2={svgHeight / 2} stroke="#1E293B" strokeDasharray="3 3" />
                  <line x1={padding} y1={svgHeight - padding} x2={svgWidth - padding} y2={svgHeight - padding} stroke="#1E293B" strokeDasharray="3 3" />

                  {/* Nominal Limit Lines */}
                  {maxLimitY !== null && (
                    <g>
                      <line x1={padding} y1={maxLimitY} x2={svgWidth - padding} y2={maxLimitY} stroke="#EF4444" strokeDasharray="4 2" strokeWidth="1.5" />
                      <text x={svgWidth - padding + 5} y={maxLimitY + 4} fill="#EF4444" fontSize="9" fontFamily="monospace">HIGH</text>
                    </g>
                  )}
                  {minLimitY !== null && (
                    <g>
                      <line x1={padding} y1={minLimitY} x2={svgWidth - padding} y2={minLimitY} stroke="#EF4444" strokeDasharray="4 2" strokeWidth="1.5" />
                      <text x={svgWidth - padding + 5} y={minLimitY + 4} fill="#EF4444" fontSize="9" fontFamily="monospace">LOW</text>
                    </g>
                  )}

                  {/* Data Path */}
                  {pathD && <path d={pathD} fill="none" stroke="#00E5FF" strokeWidth="2.5" />}

                  {/* Data Points & Anomaly Markers */}
                  {points.map((pt, idx) => (
                    <g key={idx}>
                      <circle
                        cx={pt.x}
                        cy={pt.y}
                        r={pt.isNaN || pt.status !== "NOMINAL" ? 6 : 4}
                        fill={pt.isNaN || pt.status === "CRITICAL" ? "#EF4444" : pt.status === "ANOMALOUS" ? "#F59E0B" : "#00E5FF"}
                        stroke="#040816"
                        strokeWidth="1.5"
                      />
                      {pt.isNaN && (
                        <text x={pt.x - 10} y={pt.y - 10} fill="#EF4444" fontSize="10" fontWeight="bold" fontFamily="monospace">
                          [NaN SENSOR DROP]
                        </text>
                      )}
                    </g>
                  ))}
                </svg>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Telemetry Readings Table */}
      <div className="section-block">
        <h2 className="section-title">RAW TELEMETRY READINGS WINDOW</h2>
        <table className="ops-table" aria-label="Telemetry readings window">
          <thead>
            <tr>
              <th>TIMESTAMP</th>
              <th>PARAMETER</th>
              <th>VALUE</th>
              <th>UNIT</th>
              <th>NOMINAL MIN</th>
              <th>NOMINAL MAX</th>
              <th>STATUS</th>
            </tr>
          </thead>
          <tbody>
            {channelData.map((row, idx) => (
              <tr key={idx} className={row.status === "CRITICAL" ? "row-critical" : row.status === "ANOMALOUS" ? "row-warning" : ""}>
                <td className="mono">{row.timestamp}</td>
                <td className="mono bold">{row.parameter}</td>
                <td className="mono bold">{String(row.value)}</td>
                <td className="mono">{row.unit || "-"}</td>
                <td className="mono">{row.nominal_min ?? "N/A"}</td>
                <td className="mono">{row.nominal_max ?? "N/A"}</td>
                <td>
                  <span className={`badge-pill badge-${(row.status || "NOMINAL").toLowerCase()}`}>
                    [{(row.status || "NOMINAL").toUpperCase()}]
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
