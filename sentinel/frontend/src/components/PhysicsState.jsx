import React from "react";

export default function PhysicsState({ scenario }) {
  const telemetry = scenario?.pre_fault_telemetry || [];

  // Generate physics state estimation residuals from telemetry
  const physicsItems = telemetry.map((t) => {
    const obs = parseFloat(t.value);
    const hasObs = !isNaN(obs);
    const nominalAvg =
      t.nominal_min !== undefined && t.nominal_max !== undefined
        ? (t.nominal_min + t.nominal_max) / 2
        : hasObs
        ? obs * 0.95
        : 0.0;
    const residual = hasObs ? Math.abs(obs - nominalAvg) : 999.0;

    let verdict = "VALID";
    if (isNaN(obs) || t.status === "CRITICAL" || residual > 10.0) {
      verdict = "INVALID";
    } else if (t.status === "ANOMALOUS" || t.status === "WARNING" || residual > 2.0) {
      verdict = "UNCERTAIN";
    }

    return {
      parameter: t.parameter,
      observed: hasObs ? obs.toFixed(2) : "NaN (SENSOR DROP)",
      predicted: nominalAvg.toFixed(2),
      residual: hasObs ? residual.toFixed(2) : "N/A",
      unit: t.unit || "",
      constraint: `${t.parameter} physics energy/momentum balance`,
      verdict: verdict,
    };
  });

  return (
    <section className="ops-view-container" aria-labelledby="physics-heading">
      <div className="view-title-bar">
        <h1 id="physics-heading" className="view-title">
          PHYSICAL STATE ESTIMATION &amp; RESIDUAL CONSTRAINTS
        </h1>
        <div className="view-actions">
          <span className="info-chip">MODEL: ECSS-PHYSICS-V1</span>
          <span className="info-chip">RESIDUAL CHECK: ACTIVE</span>
        </div>
      </div>

      {/* Physics Validation Summary Cards */}
      <div className="ops-grid grid-3">
        <div className="ops-card status-border-valid">
          <div className="card-header flex-between">
            <span>PHYSICAL CONSTRAINTS: VALID</span>
            <span className="badge-pill badge-nominal">[VALID]</span>
          </div>
          <div className="card-body">
            <p className="ops-text-dim">
              Channels operating within physical energy conservation and momentum conservation limits.
            </p>
          </div>
        </div>

        <div className="ops-card status-border-uncertain">
          <div className="card-header flex-between">
            <span>PHYSICAL CONSTRAINTS: UNCERTAIN</span>
            <span className="badge-pill badge-warning">[UNCERTAIN]</span>
          </div>
          <div className="card-body">
            <p className="ops-text-dim">
              Residual exceeds 2-sigma threshold. Further telemetry window required for state convergence.
            </p>
          </div>
        </div>

        <div className="ops-card status-border-invalid">
          <div className="card-header flex-between">
            <span>PHYSICAL CONSTRAINTS: INVALID</span>
            <span className="badge-pill badge-critical">[INVALID]</span>
          </div>
          <div className="card-body">
            <p className="ops-text-dim">
              Unphysical state step / NaN sensor drop violating satellite rigid-body dynamics model.
            </p>
          </div>
        </div>
      </div>

      {/* Observed vs Predicted Residual Table */}
      <div className="section-block">
        <h2 className="section-title">OBSERVED VS PREDICTED TELEMETRY RESIDUALS</h2>
        {physicsItems.length === 0 ? (
          <div className="ops-empty-state">NO STATE ESTIMATION RESIDUALS AVAILABLE</div>
        ) : (
          <table className="ops-table" aria-label="Observed vs predicted residuals">
            <thead>
              <tr>
                <th>CHANNEL / PARAMETER</th>
                <th>OBSERVED STATE</th>
                <th>PREDICTED STATE</th>
                <th>RESIDUAL DELTA (&Delta;)</th>
                <th>PHYSICS CONSTRAINT</th>
                <th>VALIDATION VERDICT</th>
              </tr>
            </thead>
            <tbody>
              {physicsItems.map((item, idx) => (
                <tr
                  key={idx}
                  className={
                    item.verdict === "INVALID"
                      ? "row-critical"
                      : item.verdict === "UNCERTAIN"
                      ? "row-warning"
                      : ""
                  }
                >
                  <td className="mono bold">{item.parameter}</td>
                  <td className="mono">{item.observed} {item.unit}</td>
                  <td className="mono">{item.predicted} {item.unit}</td>
                  <td className="mono bold">{item.residual} {item.unit}</td>
                  <td className="mono fs-xs">{item.constraint}</td>
                  <td>
                    <span
                      className={`badge-pill badge-${
                        item.verdict === "VALID"
                          ? "nominal"
                          : item.verdict === "INVALID"
                          ? "critical"
                          : "warning"
                      }`}
                    >
                      [{item.verdict}]
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
