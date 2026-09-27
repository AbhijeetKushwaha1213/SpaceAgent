/*
 * RadialArcCard.jsx — Subsystem Health & Power Allocation (Arc Gauge).
 *
 * Recreates the Dribbble Hero Card 1:
 *   - Semi-circular radial gauge arc with glowing gradient
 *   - Big bold center figure ($25.5M / 98.4% Integrity)
 *   - Breakdown list on the right with color-coded dot badges
 */

import React, { useState } from "react";
import Icon from "./Icon";

export default function RadialArcCard({
  title = "Subsystems by State",
  totalAmount = "$25.5M",
  totalSubtitle = "Total Telemetry Volume",
  score = 98.4,
  breakdown = [
    { label: "EPS (Power)", value: "$18.6 M", color: "#F97316", dotClass: "dot-orange", status: "NOMINAL" },
    { label: "AOCS (Attitude)", value: "$3.9 M", color: "#FBBF24", dotClass: "dot-yellow", status: "NOMINAL" },
    { label: "TCS (Thermal)", value: "$3.2 M", color: "#10B981", dotClass: "dot-green", status: "NOMINAL" },
    { label: "COMMS (RF Link)", value: "$0.0 M", color: "#64748B", dotClass: "dot-grey", status: "STANDBY" },
  ],
}) {
  const [activeItem, setActiveItem] = useState(null);

  // SVG Arc calculation for smooth semi-circle
  // Radius = 90, Center = (110, 110)
  const radius = 80;
  const strokeWidth = 14;
  const circumference = 2 * Math.PI * radius;
  const halfCircumference = circumference / 2;
  // Score between 0 and 100
  const progressOffset = halfCircumference - (Math.min(100, Math.max(0, score)) / 100) * halfCircumference;

  return (
    <div className="card analytics-card radial-card">
      <div className="analytics-card__header">
        <div className="analytics-card__title-group">
          <h3 className="analytics-card__title">{title}</h3>
        </div>
        <div className="analytics-card__actions">
          <button type="button" className="btn-icon" aria-label="Toggle card layout" title="Grid layout">
            <Icon name="layers" size={14} />
          </button>
          <button type="button" className="btn-icon" aria-label="Card options" title="More options">
            <Icon name="moreVertical" size={14} />
          </button>
        </div>
      </div>

      <div className="radial-card__body">
        {/* Radial Arc Gauge Container */}
        <div className="radial-gauge-container">
          <svg className="radial-gauge-svg" viewBox="0 0 220 135">
            <defs>
              <linearGradient id="radialGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="#10B981" />
                <stop offset="50%" stopColor="#F59E0B" />
                <stop offset="100%" stopColor="#EF4444" />
              </linearGradient>
              <linearGradient id="trackGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="var(--border)" stopOpacity="0.4" />
                <stop offset="100%" stopColor="var(--border)" stopOpacity="0.8" />
              </linearGradient>
              <filter id="glowEffect" x="-20%" y="-20%" width="140%" height="140%">
                <feGaussianBlur stdDeviation="3" result="blur" />
                <feComposite in="SourceGraphic" in2="blur" operator="over" />
              </filter>
            </defs>

            {/* Background Track Arc */}
            <path
              d="M 25 115 A 85 85 0 0 1 195 115"
              fill="none"
              stroke="url(#trackGradient)"
              strokeWidth={strokeWidth}
              strokeLinecap="round"
            />

            {/* Glowing Active Arc */}
            <path
              d="M 25 115 A 85 85 0 0 1 195 115"
              fill="none"
              stroke="url(#radialGradient)"
              strokeWidth={strokeWidth}
              strokeDasharray={267}
              strokeDashoffset={progressOffset}
              strokeLinecap="round"
              filter="url(#glowEffect)"
              className="gauge-active-path"
            />
          </svg>

          {/* Centered Figure */}
          <div className="radial-gauge-center">
            <span className="radial-gauge-value">{totalAmount}</span>
            <span className="radial-gauge-sub">{totalSubtitle}</span>
          </div>
        </div>

        {/* Legend Breakdown List */}
        <div className="radial-breakdown-list">
          {breakdown.map((item, idx) => (
            <div
              key={idx}
              className={`radial-breakdown-item ${activeItem === idx ? "radial-breakdown-item--active" : ""}`}
              onMouseEnter={() => setActiveItem(idx)}
              onMouseLeave={() => setActiveItem(null)}
            >
              <div className="radial-breakdown-left">
                <span className="radial-dot" style={{ backgroundColor: item.color }} />
                <span className="radial-label">{item.label}</span>
              </div>
              <div className="radial-breakdown-right">
                <span className="radial-amount mono">{item.value}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
