/*
 * StreamgraphCard.jsx — Telemetry Signal Window & Streamflow Ribbon.
 *
 * Recreates the Dribbble Hero Card 3:
 *   - Top summary stats: 27.8K Ingested, 67% Nominal, 24% Flagged
 *   - Multi-gradient fluid bezier ribbon / streamgraph
 *   - Visual marker pins at inflection points (8%, 32%, 60%, 4%, 21%, 12%)
 */

import React, { useState } from "react";
import Icon from "./Icon";

export default function StreamgraphCard({
  title = "Details",
  stats = [
    { value: "27.8 K", label: "Opened Request" },
    { value: "67%", label: "Engaged" },
    { value: "24%", label: "EOI Sent" },
  ],
}) {
  const [activePin, setActivePin] = useState(null);

  const pins = [
    { id: "p1", x: "12%", y: "22%", label: "8%", detail: "Initial Ingest" },
    { id: "p2", x: "42%", y: "28%", label: "32%", detail: "Attitude Transition" },
    { id: "p3", x: "88%", y: "26%", label: "60%", detail: "Peak Signal" },
    { id: "p4", x: "14%", y: "78%", label: "4%", detail: "Thermal Baseline" },
    { id: "p5", x: "40%", y: "82%", label: "21%", detail: "EKF Convergence" },
    { id: "p6", x: "85%", y: "78%", label: "12%", detail: "Final Handshake" },
  ];

  return (
    <div className="card analytics-card stream-card">
      <div className="analytics-card__header">
        <div className="analytics-card__title-group">
          <h3 className="analytics-card__title">{title}</h3>
        </div>
        <div className="analytics-card__actions">
          <button type="button" className="btn-icon" aria-label="Card options" title="More options">
            <Icon name="moreVertical" size={14} />
          </button>
        </div>
      </div>

      {/* Top Details Stat Row */}
      <div className="stream-stats-row">
        {stats.map((s, idx) => (
          <div key={idx} className="stream-stat-item">
            <span className="stream-stat-val">{s.value}</span>
            <span className="stream-stat-label">{s.label}</span>
          </div>
        ))}
      </div>

      {/* Fluid Streamgraph Visualization */}
      <div className="stream-chart-viewport">
        <svg
          className="stream-svg"
          viewBox="0 0 540 180"
          preserveAspectRatio="none"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
        >
          <defs>
            {/* Upper green/lime stream gradient */}
            <linearGradient id="streamGreen" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#10B981" stopOpacity="0.8" />
              <stop offset="35%" stopColor="#84CC16" stopOpacity="0.85" />
              <stop offset="70%" stopColor="#F59E0B" stopOpacity="0.75" />
              <stop offset="100%" stopColor="#F97316" stopOpacity="0.65" />
            </linearGradient>

            {/* Core orange/amber energetic stream */}
            <linearGradient id="streamAmber" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#84CC16" stopOpacity="0.4" />
              <stop offset="30%" stopColor="#F59E0B" stopOpacity="0.95" />
              <stop offset="65%" stopColor="#EF4444" stopOpacity="0.9" />
              <stop offset="100%" stopColor="#FB7185" stopOpacity="0.5" />
            </linearGradient>

            {/* Lower soft ambient glow layer */}
            <linearGradient id="streamLower" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#10B981" stopOpacity="0.15" />
              <stop offset="45%" stopColor="#F59E0B" stopOpacity="0.25" />
              <stop offset="100%" stopColor="#F97316" stopOpacity="0.15" />
            </linearGradient>
          </defs>

          {/* Background Ambient Wave */}
          <path
            d="M 0 100 C 90 70, 160 115, 230 90 C 310 60, 420 85, 540 70 L 540 145 C 420 155, 310 135, 230 140 C 160 145, 90 120, 0 130 Z"
            fill="url(#streamLower)"
          />

          {/* Primary Flowing Ribbon (Green to Orange Fluid Shape) */}
          <path
            d="M 0 95 C 75 75, 140 110, 210 88 C 285 62, 380 78, 540 65 L 540 125 C 380 135, 285 120, 210 135 C 140 145, 75 115, 0 120 Z"
            fill="url(#streamGreen)"
          />

          {/* Core Density River (Orange/Red Intense Core) */}
          <path
            d="M 120 102 C 170 95, 220 78, 290 85 C 360 92, 430 80, 540 78 L 540 118 C 430 115, 360 122, 290 118 C 220 115, 170 110, 120 108 Z"
            fill="url(#streamAmber)"
          />
        </svg>

        {/* Floating Percentage Inflection Pins */}
        <div className="stream-pins-layer">
          {pins.map((pin) => (
            <div
              key={pin.id}
              className={`stream-pin ${activePin === pin.id ? "stream-pin--active" : ""}`}
              style={{ left: pin.x, top: pin.y }}
              onMouseEnter={() => setActivePin(pin.id)}
              onMouseLeave={() => setActivePin(null)}
            >
              <div className="stream-pin-pill">{pin.label}</div>
              {activePin === pin.id && (
                <div className="stream-pin-tooltip">{pin.detail}</div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
