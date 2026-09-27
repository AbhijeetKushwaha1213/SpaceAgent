/*
 * SplineTrendCard.jsx — Telemetry Parameter Trajectory & Trend Spline.
 *
 * Recreates the Dribbble Hero Card 4:
 *   - Clean multi-curve spline chart (smooth bezier trajectories)
 *   - Vertical hover crosshair cursor with tracking pill ("37 +1.2%")
 *   - Category legend pills (Development, Investment, Build and Hold)
 *   - Clean grid lines and numeric y-axis scale
 */

import React, { useState } from "react";
import Icon from "./Icon";

export default function SplineTrendCard({
  title = "New Request Trend",
  subsystems = [
    { id: "dev", name: "Development", color: "#F97316", active: true },
    { id: "inv", name: "Investment", color: "#EC4899", active: true },
    { id: "bld", name: "Build and Hold", color: "#94A3B8", active: true },
  ],
}) {
  const [hoverX, setHoverX] = useState(310); // initial default position
  const [activeCursorVal, setActiveCursorVal] = useState({ val: 37, delta: "+1.2%" });

  const handleMouseMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = Math.max(60, Math.min(rect.width - 40, e.clientX - rect.left));
    setHoverX(x);
    // calculate a dynamic value based on x position
    const ratio = (x - 60) / (rect.width - 100);
    const val = Math.round(20 + ratio * 55);
    const delta = (ratio * 2.8 - 0.4).toFixed(1);
    setActiveCursorVal({ val, delta: `${delta >= 0 ? "+" : ""}${delta}%` });
  };

  return (
    <div className="card analytics-card trend-card">
      <div className="analytics-card__header">
        <div className="analytics-card__title-group">
          <h3 className="analytics-card__title">{title}</h3>
        </div>
        <div className="analytics-card__actions">
          <button type="button" className="btn-icon" aria-label="Trend options" title="More options">
            <Icon name="moreVertical" size={14} />
          </button>
        </div>
      </div>

      <div className="trend-card__body">
        {/* Interactive Chart Area */}
        <div className="trend-chart-container" onMouseMove={handleMouseMove}>
          <svg className="trend-svg" viewBox="0 0 540 220" preserveAspectRatio="none">
            <defs>
              {/* Spline line gradients */}
              <linearGradient id="gradOrange" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="#FB923C" />
                <stop offset="100%" stopColor="#EA580C" />
              </linearGradient>
              <linearGradient id="gradPink" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="#F472B6" />
                <stop offset="100%" stopColor="#DB2777" />
              </linearGradient>
              <linearGradient id="gradGrey" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="#94A3B8" />
                <stop offset="100%" stopColor="#64748B" />
              </linearGradient>
            </defs>

            {/* Y-Axis Grid Lines & Labels */}
            <g className="trend-grid-lines" stroke="var(--border)" strokeWidth="0.8">
              <line x1="50" y1="25" x2="520" y2="25" />
              <line x1="50" y1="65" x2="520" y2="65" />
              <line x1="50" y1="105" x2="520" y2="105" />
              <line x1="50" y1="145" x2="520" y2="145" />
              <line x1="50" y1="185" x2="520" y2="185" />
            </g>

            {/* Y-Axis Scale Numbers */}
            <g className="trend-axis-text" fill="var(--text-dim)" fontSize="10" fontFamily="var(--mono)">
              <text x="36" y="29" textAnchor="end">100</text>
              <text x="36" y="69" textAnchor="end">80</text>
              <text x="36" y="109" textAnchor="end">60</text>
              <text x="36" y="149" textAnchor="end">40</text>
              <text x="36" y="189" textAnchor="end">20</text>
            </g>

            {/* Curve 1: Development (Orange - S-Curve rising to top) */}
            <path
              d="M 50 160 C 180 160, 270 145, 340 90 C 390 50, 440 40, 520 38"
              fill="none"
              stroke="url(#gradOrange)"
              strokeWidth="2.8"
              strokeLinecap="round"
            />

            {/* Curve 2: Investment (Pink - S-Curve in middle) */}
            <path
              d="M 50 175 C 180 175, 270 160, 340 120 C 390 90, 440 85, 520 82"
              fill="none"
              stroke="url(#gradPink)"
              strokeWidth="2.4"
              strokeLinecap="round"
            />

            {/* Curve 3: Build and Hold (Grey - Base Curve) */}
            <path
              d="M 50 190 C 180 190, 280 185, 340 155 C 390 135, 450 130, 520 128"
              fill="none"
              stroke="url(#gradGrey)"
              strokeWidth="2"
              strokeLinecap="round"
            />

            {/* Vertical Tracker Cursor Line */}
            <g className="trend-cursor-group" transform={`translate(${hoverX}, 0)`}>
              <line
                x1="0"
                y1="25"
                x2="0"
                y2="195"
                stroke="var(--text-primary)"
                strokeWidth="1.2"
                strokeDasharray="3 3"
              />
              {/* Intersect Dots */}
              <circle cx="0" cy="115" r="4.5" fill="#10B981" stroke="#FFFFFF" strokeWidth="1.5" />
            </g>
          </svg>

          {/* Interactive Floating Pill Badge (matching Image 1) */}
          <div
            className="trend-cursor-pill"
            style={{ left: `${hoverX}px`, top: "45%" }}
          >
            <span className="trend-cursor-num">{activeCursorVal.val}</span>
            <span className="trend-cursor-delta">{activeCursorVal.delta}</span>
          </div>

          {/* Category Pill Labels (matching Image 1 right tags) */}
          <div className="trend-series-pills">
            <div className="trend-pill trend-pill--orange">Development</div>
            <div className="trend-pill trend-pill--pink">Investment</div>
            <div className="trend-pill trend-pill--grey">Build and Hold</div>
          </div>
        </div>
      </div>
    </div>
  );
}
