/*
 * TimeSeriesChart — real telemetry time-series rendered as SVG.
 *
 * Renders:
 *   - observed series (line + sample points; unusable readings are gaps)
 *   - nominal band (shaded rect) and hard limits (lines) from the channel
 *     dictionary / window bounds
 *   - anomaly markers from the backend detection report
 *   - model predictions (state-estimation residuals) as dashed markers
 *   - a selectable time range via the `range` prop
 *
 * Pure function of its props; all numbers come from backend payloads.
 */

import React, { useMemo } from "react";

const W = 720;
const H = 260;
const PAD_L = 46;
const PAD_R = 14;
const PAD_T = 16;
const PAD_B = 34;

function isFiniteNum(v) {
  return typeof v === "number" && Number.isFinite(v);
}

export default function TimeSeriesChart({
  samples = [],
  predictions = [],
  anomalies = [],
  unit = "",
  nominalMin = null,
  nominalMax = null,
  hardMin = null,
  hardMax = null,
  range = null,
  focusTimestamp = null,
  onSelectSample = null,
  height = 260,
}) {
  const scale = useMemo(() => {
    const ts = samples.map((s) => s.t);
    const ps = predictions.map((p) => p.t);
    const allT = ts.concat(ps);
    let tMin = range && isFiniteNum(range.min) ? range.min : Math.min(...allT, 0);
    let tMax = range && isFiniteNum(range.max) ? range.max : Math.max(...allT, 1);
    if (tMax - tMin < 1e-6) tMax = tMin + 1;

    const values = samples.map((s) => s.numericValue).filter(isFiniteNum);
    const predVals = predictions
      .map((p) => [p.predicted, p.observed])
      .flat()
      .filter(isFiniteNum);
    const limits = [nominalMin, nominalMax, hardMin, hardMax].filter(isFiniteNum);
    const allV = values.concat(predVals, limits);
    let vMin = allV.length ? Math.min(...allV) : 0;
    let vMax = allV.length ? Math.max(...allV) : 1;
    if (vMax - vMin < 1e-9) {
      vMin -= 1;
      vMax += 1;
    }
    const pad = (vMax - vMin) * 0.08 || 1;
    vMin -= pad;
    vMax += pad;

    const x = (t) => PAD_L + ((t - tMin) / (tMax - tMin)) * (W - PAD_L - PAD_R);
    const y = (v) => PAD_T + (1 - (v - vMin) / (vMax - vMin)) * (H - PAD_T - PAD_B);
    return { tMin, tMax, vMin, vMax, x, y };
  }, [samples, predictions, nominalMin, nominalMax, hardMin, hardMax, range]);

  const linePath = useMemo(() => {
    const usable = samples.filter((s) => s.numericValue !== null);
    return usable
      .map((s, i) => `${i === 0 ? "M" : "L"} ${scale.x(s.t).toFixed(1)} ${scale.y(s.numericValue).toFixed(1)}`)
      .join(" ");
  }, [samples, scale]);

  const yTicks = useMemo(() => {
    const ticks = [];
    const count = 5;
    for (let i = 0; i <= count; i += 1) {
      const v = scale.vMin + (i / count) * (scale.vMax - scale.vMin);
      ticks.push(v);
    }
    return ticks;
  }, [scale]);

  const xTicks = useMemo(() => {
    const ticks = [];
    const count = 6;
    for (let i = 0; i <= count; i += 1) {
      const t = scale.tMin + (i / count) * (scale.tMax - scale.tMin);
      ticks.push(t);
    }
    return ticks;
  }, [scale]);

  const labelForT = (t) => {
    const sample = [...samples]
      .filter((s) => s.t === t)
      .sort((a, b) => (a.xIndex || 0) - (b.xIndex || 0))[0];
    return sample ? sample.timestamp || String(t) : `t=${t}`;
  };

  const anomalyAt = (s) =>
    (anomalies || []).find(
      (a) =>
        (a.timestamp === s.timestamp || (isFiniteNum(a.t) && a.t === s.t))
    );

  const focusX = focusTimestamp
    ? scale.x(
        samples.find((s) => s.timestamp === focusTimestamp)?.t ??
          samples[0]?.t
      )
    : null;

  const anomaliesByT = useMemo(() => {
    const map = new Map();
    for (const a of anomalies || []) {
      const key = isFiniteNum(a.t) ? a.t : a.timestamp;
      map.set(key, (map.get(key) || []).concat(a));
    }
    return map;
  }, [anomalies]);

  const anomalyAtT = (t) => {
    const key = isFiniteNum(t) ? t : null;
    if (key !== null && anomaliesByT.has(key)) return anomaliesByT.get(key)[0];
    const sample = samples.find((s) => s.t === t);
    return sample ? anomalyAt(sample) : null;
  };

  return (
    <div className="chart">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ height }}
        className="chart__svg"
        role="img"
        aria-label={`Time-series chart. ${unit ? `Unit: ${unit}.` : ""}`}
      >
        <g className="chart__grid">
          {yTicks.map((v) => (
            <g key={`y${v}`}>
              <line x1={PAD_L} x2={W - PAD_R} y1={scale.y(v)} y2={scale.y(v)} className="chart__gridline" />
              <text x={PAD_L - 6} y={scale.y(v) + 3} className="chart__ylabel" textAnchor="end">
                {v >= 1e4 || v <= -1e4 ? v.toExponential(1) : Number(v.toFixed(2)).toString()}
              </text>
            </g>
          ))}
          {xTicks.map((t) => (
            <g key={`x${t}`}>
              <line x1={scale.x(t)} x2={scale.x(t)} y1={PAD_T} y2={H - PAD_B} className="chart__gridline chart__gridline--x" />
              <text x={scale.x(t)} y={H - PAD_B + 16} className="chart__xlabel" textAnchor="middle">
                {labelForT(t)}
              </text>
            </g>
          ))}
        </g>

        {/* nominal band */}
        {isFiniteNum(nominalMin) && isFiniteNum(nominalMax) ? (
          <rect
            x={PAD_L}
            y={scale.y(nominalMax)}
            width={W - PAD_L - PAD_R}
            height={Math.max(scale.y(nominalMin) - scale.y(nominalMax), 0)}
            className="chart__band"
          />
        ) : null}

        {/* hard limits */}
        {isFiniteNum(hardMax) ? (
          <g>
            <line x1={PAD_L} x2={W - PAD_R} y1={scale.y(hardMax)} y2={scale.y(hardMax)} className="chart__hardlimit" />
            <text x={W - PAD_R} y={scale.y(hardMax) - 4} className="chart__limitlabel" textAnchor="end">
              HARD MAX {hardMax} {unit}
            </text>
          </g>
        ) : null}
        {isFiniteNum(hardMin) ? (
          <g>
            <line x1={PAD_L} x2={W - PAD_R} y1={scale.y(hardMin)} y2={scale.y(hardMin)} className="chart__hardlimit" />
            <text x={W - PAD_R} y={scale.y(hardMin) + 12} className="chart__limitlabel" textAnchor="end">
              HARD MIN {hardMin} {unit}
            </text>
          </g>
        ) : null}
        {isFiniteNum(nominalMax) && !isFiniteNum(hardMax) ? (
          <text x={W - PAD_R} y={scale.y(nominalMax) - 4} className="chart__limitlabel" textAnchor="end">
            NOMINAL MAX {nominalMax} {unit}
          </text>
        ) : null}
        {isFiniteNum(nominalMin) && !isFiniteNum(hardMin) ? (
          <text x={W - PAD_R} y={scale.y(nominalMin) + 12} className="chart__limitlabel" textAnchor="end">
            NOMINAL MIN {nominalMin} {unit}
          </text>
        ) : null}

        {/* observed series */}
        {linePath ? <path d={linePath} className="chart__series" /> : null}
        {samples.map((s, i) => {
          const hasValue = s.numericValue !== null;
          const anomaly = anomalyAtT(s.t);
          const isFocus = focusTimestamp && s.timestamp === focusTimestamp;
          return hasValue ? (
            <g key={`pt${i}`}>
              <circle
                cx={scale.x(s.t)}
                cy={scale.y(s.numericValue)}
                r={isFocus ? 5 : 3.2}
                className={
                  anomaly
                    ? `chart__point chart__point--anomaly chart__point--${(anomaly.severity || "ANOMALOUS").toLowerCase()}`
                    : `chart__point chart__point--${(s.status || "UNKNOWN").toLowerCase()}`
                }
              />
              {anomaly ? (
                <g className="chart__anomaly-marker">
                  <line x1={scale.x(s.t) - 4} y1={scale.y(s.numericValue) - 4} x2={scale.x(s.t) + 4} y2={scale.y(s.numericValue) + 4} />
                  <line x1={scale.x(s.t) + 4} y1={scale.y(s.numericValue) - 4} x2={scale.x(s.t) - 4} y2={scale.y(s.numericValue) + 4} />
                </g>
              ) : null}
              {onSelectSample ? (
                <circle
                  cx={scale.x(s.t)}
                  cy={scale.y(s.numericValue)}
                  r={7}
                  className="chart__hit"
                  onClick={() => onSelectSample(s)}
                />
              ) : null}
            </g>
          ) : null;
        })}

        {/* prediction overlay from state-estimation residuals */}
        {predictions.length > 0 ? (
          <g>
            {predictions.map((p, i) => {
              const py =
                isFiniteNum(p.predicted) && isFiniteNum(p.t) ? scale.y(p.predicted) : null;
              const px = isFiniteNum(p.t) ? scale.x(p.t) : null;
              if (py === null || px === null) return null;
              const isInRange = isFiniteNum(p.observed);
              const oy = isInRange ? scale.y(p.observed) : null;
              return (
                <g key={`pred${i}`} className="chart__prediction">
                  {oy !== null ? (
                    <line x1={px} y1={oy} x2={px} y2={py} className="chart__prediction-residual" />
                  ) : null}
                  <circle cx={px} cy={py} r={4.5} className="chart__prediction-point" />
                  <text x={px + 6} y={py + 4} className="chart__prediction-label">
                    P
                  </text>
                </g>
              );
            })}
          </g>
        ) : null}

        {/* focus cursor */}
        {focusX !== null ? (
          <line x1={focusX} x2={focusX} y1={PAD_T} y2={H - PAD_B} className="chart__focus-line" />
        ) : null}
      </svg>
      <div className="chart__legend">
        <span className="chart__legend-item chart__legend-item--series">OBSERVED</span>
        {predictions.length > 0 ? (
          <span className="chart__legend-item chart__legend-item--prediction">MODEL PREDICTION</span>
        ) : null}
        {anomalies.length > 0 ? (
          <span className="chart__legend-item chart__legend-item--anomaly">ANOMALY FLAG</span>
        ) : null}
        <span className="chart__legend-item chart__legend-item--band">NOMINAL BAND</span>
      </div>
    </div>
  );
}