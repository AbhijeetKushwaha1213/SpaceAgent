/*
 * OrbitMapCard.jsx — Geospatial Satellite Ground Track & Station Coverage Map.
 *
 * Recreates the Dribbble Hero Card 2:
 *   - Stylized world continent geometry with gradient heat density
 *   - Floating interactive telemetry tag ("$3.2 M Western Australia")
 *   - Zoom controls (+ / -)
 *   - Coverage thresholds legend (< 60%, < 40%, < 20%)
 */

import React, { useState } from "react";
import Icon from "./Icon";

export default function OrbitMapCard({
  title = "Map Preview",
  highlightStation = "Western Australia",
  highlightValue = "$3.2 M",
  downlinkSpeed = "3.2 Mbps · Orbit 412",
}) {
  const [zoomLevel, setZoomLevel] = useState(1);
  const [isStationHovered, setIsStationHovered] = useState(false);

  const handleZoomIn = () => setZoomLevel((z) => Math.min(1.4, z + 0.1));
  const handleZoomOut = () => setZoomLevel((z) => Math.max(0.8, z - 0.1));

  return (
    <div className="card analytics-card map-card">
      <div className="analytics-card__header">
        <div className="analytics-card__title-group">
          <h3 className="analytics-card__title">{title}</h3>
        </div>
        <div className="analytics-card__actions">
          <button type="button" className="btn-icon" aria-label="Layout view" title="Grid layout">
            <Icon name="layers" size={14} />
          </button>
          <button type="button" className="btn-icon" aria-label="Map options" title="More options">
            <Icon name="moreVertical" size={14} />
          </button>
        </div>
      </div>

      <div className="map-card__viewport">
        {/* Floating Zoom Controls */}
        <div className="map-zoom-controls">
          <button
            type="button"
            className="map-zoom-btn"
            onClick={handleZoomIn}
            aria-label="Zoom in map"
            title="Zoom in"
          >
            <Icon name="zoomIn" size={14} />
          </button>
          <button
            type="button"
            className="map-zoom-btn"
            onClick={handleZoomOut}
            aria-label="Zoom out map"
            title="Zoom out"
          >
            <Icon name="zoomOut" size={14} />
          </button>
        </div>

        {/* Map Vector Graphic */}
        <div
          className="map-svg-wrap"
          style={{ transform: `scale(${zoomLevel})`, transition: "transform 0.25s ease-out" }}
        >
          <svg
            className="map-vector"
            viewBox="0 0 540 280"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <defs>
              {/* Heatmap Gradients */}
              <radialGradient id="heatWest" cx="38%" cy="46%" r="35%">
                <stop offset="0%" stopColor="#EF4444" stopOpacity="0.85" />
                <stop offset="35%" stopColor="#F59E0B" stopOpacity="0.75" />
                <stop offset="65%" stopColor="#84CC16" stopOpacity="0.6" />
                <stop offset="100%" stopColor="#10B981" stopOpacity="0" />
              </radialGradient>
              <linearGradient id="orbitTrackGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stopColor="#00E5FF" stopOpacity="0.1" />
                <stop offset="50%" stopColor="#00E5FF" stopOpacity="0.8" />
                <stop offset="100%" stopColor="#5B7CFA" stopOpacity="0.2" />
              </linearGradient>
            </defs>

            {/* Subtle Longitude/Latitude Grid */}
            <g className="map-grid-lines" stroke="var(--border)" strokeWidth="0.6" strokeDasharray="3 4">
              <line x1="30" y1="70" x2="510" y2="70" />
              <line x1="30" y1="140" x2="510" y2="140" />
              <line x1="30" y1="210" x2="510" y2="210" />
              <line x1="120" y1="20" x2="120" y2="260" />
              <line x1="240" y1="20" x2="240" y2="260" />
              <line x1="360" y1="20" x2="360" y2="260" />
            </g>

            {/* Stylized Australia / Global Landmass Contours */}
            {/* Main Landmass (Australia Shape) */}
            <path
              className="map-continent map-continent--base"
              d="M 120 70 C 135 60, 160 55, 185 65 C 220 50, 240 70, 255 90 C 270 100, 280 125, 275 145 C 265 170, 250 190, 220 200 C 190 205, 175 195, 160 205 C 145 200, 130 185, 125 160 C 115 140, 110 100, 120 70 Z"
              fill="var(--bg-surface-3)"
              stroke="var(--border-strong)"
              strokeWidth="1.2"
            />
            {/* Secondary Regional Outlines */}
            <path
              className="map-continent"
              d="M 285 105 C 295 95, 310 100, 315 115 C 310 130, 295 135, 285 125 Z"
              fill="var(--bg-surface-3)"
              stroke="var(--border-strong)"
              strokeWidth="1"
            />
            <path
              className="map-continent"
              d="M 230 215 C 240 210, 255 215, 250 225 C 240 230, 230 225, 230 215 Z"
              fill="var(--bg-surface-3)"
              stroke="var(--border-strong)"
              strokeWidth="0.8"
            />

            {/* Glowing Telemetry Heat Map Layer */}
            <path
              className="map-heatmap"
              d="M 122 75 C 135 65, 160 60, 180 70 C 185 110, 175 140, 155 175 C 135 180, 125 150, 122 110 Z"
              fill="url(#heatWest)"
            />

            {/* Orbit Ground Track Line */}
            <path
              d="M 50 190 Q 170 80, 300 120 T 510 170"
              fill="none"
              stroke="url(#orbitTrackGrad)"
              strokeWidth="2.2"
              strokeDasharray="5 3"
            />

            {/* Satellite Current Position Marker */}
            <g transform="translate(195, 108)">
              <circle cx="0" cy="0" r="10" fill="rgba(0, 229, 255, 0.25)" className="ping-circle" />
              <circle cx="0" cy="0" r="4.5" fill="#00E5FF" />
            </g>
          </svg>

          {/* Floating High-Tech Station Badge (matching Image 1) */}
          <div
            className={`map-floating-badge ${isStationHovered ? "map-floating-badge--hovered" : ""}`}
            style={{ top: "34%", left: "33%" }}
            onMouseEnter={() => setIsStationHovered(true)}
            onMouseLeave={() => setIsStationHovered(false)}
          >
            <div className="map-badge-glow" />
            <div className="map-badge-content">
              <span className="map-badge-val">{highlightValue}</span>
              <span className="map-badge-label">{highlightStation}</span>
              {isStationHovered && (
                <span className="map-badge-extra">{downlinkSpeed}</span>
              )}
            </div>
          </div>
        </div>

        {/* Legend */}
        <div className="map-legend">
          <div className="map-legend-item">
            <span className="map-legend-dot" style={{ backgroundColor: "#EF4444" }} />
            <span className="map-legend-text">&lt; 60%</span>
          </div>
          <div className="map-legend-item">
            <span className="map-legend-dot" style={{ backgroundColor: "#F59E0B" }} />
            <span className="map-legend-text">&lt; 40%</span>
          </div>
          <div className="map-legend-item">
            <span className="map-legend-dot" style={{ backgroundColor: "#10B981" }} />
            <span className="map-legend-text">&lt; 20%</span>
          </div>
        </div>
      </div>
    </div>
  );
}
