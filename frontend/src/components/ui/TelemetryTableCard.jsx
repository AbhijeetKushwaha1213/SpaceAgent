/*
 * TelemetryTableCard.jsx — Subsystem Telemetry & Content Performance Table.
 *
 * Recreates the Ekonomi Table from Image 2:
 *   - Search input ("Search for product / parameter...")
 *   - Filter pill controls
 *   - Clean multi-column data table
 *   - Status scores, retention badges, and action triggers
 */

import React, { useState, useMemo } from "react";
import Icon from "./Icon";
import StatusBadge from "./StatusBadge";

export default function TelemetryTableCard({
  telemetry = [],
  onInspectChannel,
}) {
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedSubsystem, setSelectedSubsystem] = useState("ALL");

  // Fallback realistic telemetry if empty
  const defaultItems = useMemo(
    () => [
      {
        subsystem: "AOCS",
        name: "GYRO_RATE_ROLL",
        release: "Aug 31, 2024",
        value: "0.042 deg/s",
        nominal: "[-0.05, 0.05]",
        zscore: "+0.84 σ",
        retentionScore: 92,
        status: "NOMINAL",
      },
      {
        subsystem: "EPS",
        name: "BATTERY_SOC_PCT",
        release: "Aug 31, 2024",
        value: "84.2 %",
        nominal: "[20.0, 100.0]",
        zscore: "-0.12 σ",
        retentionScore: 98,
        status: "NOMINAL",
      },
      {
        subsystem: "EPS",
        name: "BUS_VOLTAGE_MAIN",
        release: "Aug 31, 2024",
        value: "27.4 V",
        nominal: "[26.0, 32.0]",
        zscore: "-1.92 σ",
        retentionScore: 78,
        status: "WARNING",
      },
      {
        subsystem: "TCS",
        name: "PAYLOAD_TEMP_SENSOR",
        release: "Aug 31, 2024",
        value: "38.6 °C",
        nominal: "[-10.0, 45.0]",
        zscore: "+0.45 σ",
        retentionScore: 95,
        status: "NOMINAL",
      },
      {
        subsystem: "OBC",
        name: "CPU_CYCLES_UTIL",
        release: "Aug 31, 2024",
        value: "41.2 %",
        nominal: "[0.0, 85.0]",
        zscore: "+0.21 σ",
        retentionScore: 99,
        status: "NOMINAL",
      },
      {
        subsystem: "COMMS",
        name: "TRANSPONDER_SNR_DB",
        release: "Aug 31, 2024",
        value: "14.8 dB",
        nominal: "[8.0, 24.0]",
        zscore: "+0.15 σ",
        retentionScore: 94,
        status: "NOMINAL",
      },
    ],
    []
  );

  const items = telemetry && telemetry.length > 0 ? telemetry : defaultItems;

  const filteredItems = useMemo(() => {
    return items.filter((row) => {
      const matchesSearch =
        (row.name || "").toLowerCase().includes(searchTerm.toLowerCase()) ||
        (row.subsystem || "").toLowerCase().includes(searchTerm.toLowerCase());
      const matchesSub =
        selectedSubsystem === "ALL" || row.subsystem === selectedSubsystem;
      return matchesSearch && matchesSub;
    });
  }, [items, searchTerm, selectedSubsystem]);

  const subsystems = ["ALL", "EPS", "AOCS", "TCS", "OBC", "COMMS"];

  return (
    <div className="card analytics-card table-card">
      <div className="table-card__header">
        <div className="table-card__title-group">
          <h3 className="analytics-card__title">Telemetry Stream &amp; Channel Status</h3>
          <span className="table-card__subtitle">Real-time parameters verified across canonical window</span>
        </div>

        <div className="table-card__controls">
          {/* Search Input */}
          <div className="table-search-wrap">
            <Icon name="search" size={14} className="search-icon" />
            <input
              type="text"
              className="table-search-input"
              placeholder="Search parameters..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              aria-label="Search parameters"
            />
          </div>

          {/* Subsystem Filter Pills */}
          <div className="table-filter-pills">
            {subsystems.map((sub) => (
              <button
                key={sub}
                type="button"
                className={`table-filter-pill ${selectedSubsystem === sub ? "table-filter-pill--active" : ""}`}
                onClick={() => setSelectedSubsystem(sub)}
              >
                {sub}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="table-card__body">
        <div className="table-responsive">
          <table className="ekonomi-table">
            <thead>
              <tr>
                <th>Subsystem</th>
                <th>Channel Parameter</th>
                <th>Observed Value</th>
                <th>Nominal Range</th>
                <th>Z-Score</th>
                <th>Integrity</th>
                <th>Status</th>
                <th className="text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {filteredItems.map((row, idx) => (
                <tr key={idx} className="ekonomi-row">
                  <td>
                    <span className="subsystem-pill">{row.subsystem}</span>
                  </td>
                  <td className="mono fw-semibold">{row.name}</td>
                  <td className="mono">{row.value}</td>
                  <td className="mono text-muted">{row.nominal}</td>
                  <td className="mono">{row.zscore}</td>
                  <td>
                    <span
                      className={`score-badge ${
                        (row.retentionScore || 90) >= 90
                          ? "score-badge--high"
                          : (row.retentionScore || 90) >= 75
                          ? "score-badge--med"
                          : "score-badge--low"
                      }`}
                    >
                      {row.retentionScore || 90}
                    </span>
                  </td>
                  <td>
                    <StatusBadge status={row.status || "NOMINAL"} />
                  </td>
                  <td className="text-right">
                    <button
                      type="button"
                      className="btn btn--sm btn--subtle"
                      onClick={() => onInspectChannel && onInspectChannel(row.name)}
                    >
                      Inspect
                    </button>
                  </td>
                </tr>
              ))}
              {filteredItems.length === 0 && (
                <tr>
                  <td colSpan={8} className="text-center text-muted p-4">
                    No matching telemetry parameters found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
