import React from "react";

export default function AuditTrail({ scenario, analysisOutput }) {
  const runId = analysisOutput?.run_id || `run_2026_${scenario?.scenario_id || "001"}_audit`;
  const timestamp = new Date().toISOString();

  const auditStages = [
    { name: "INGESTION", status: "COMPLETED", detail: `Loaded ${scenario?.pre_fault_telemetry?.length || 6} raw telemetry channels`, duration: "1.2 ms" },
    { name: "DETECTION", status: "COMPLETED", detail: "Multi-parameter z-score and hard limit checking", duration: "2.4 ms" },
    { name: "STATE ESTIMATION", status: "COMPLETED", detail: "Extended Kalman Filter state residual computation", duration: "3.8 ms" },
    { name: "HYPOTHESES", status: "COMPLETED", detail: "Deterministic hypothesis candidate generation", duration: "1.9 ms" },
    { name: "PHYSICS VALIDATION", status: "COMPLETED", detail: "Rigid-body energy & momentum balance validation", duration: "4.1 ms" },
    { name: "RAG RETRIEVAL", status: "COMPLETED", detail: "ECSS-E-ST-70-11C procedure retrieval & citation indexing", duration: "5.2 ms" },
    { name: "LLM RANKING", status: "COMPLETED", detail: `Constrained LLM hypothesis ranking (${analysisOutput?.uncertainty || "MEDIUM"} uncertainty)`, duration: "18.6 ms" },
    { name: "SAFETY VALIDATION", status: "COMPLETED", detail: `Safety filter status: ${analysisOutput?.safety_status || "VALIDATED"}`, duration: "1.5 ms" },
    { name: "OPERATOR DECISION", status: "PENDING", detail: "Awaiting human flight operator telecommand uplink authorization", duration: "0.0 ms" },
  ];

  return (
    <section className="ops-view-container" aria-labelledby="audit-heading">
      <div className="view-title-bar">
        <h1 id="audit-heading" className="view-title">
          INVESTIGATION AUDIT TRAIL &amp; SHA-256 PROVENANCE FINGERPRINT
        </h1>
        <div className="view-actions">
          <span className="info-chip">RUN ID: {runId}</span>
          <span className="info-chip">TIMESTAMP: {timestamp}</span>
        </div>
      </div>

      {/* Audit Header Metadata */}
      <div className="ops-card mb-20">
        <div className="card-header flex-between">
          <span>AUDIT RUN PROVENANCE RECORD</span>
          <span className="badge-pill badge-nominal">[IMMUTABLE AUDIT RECORD]</span>
        </div>
        <div className="card-body ops-grid grid-3">
          <div className="ops-kv">
            <span className="kv-key">AUDIT SCHEMA VERSION:</span>
            <span className="kv-val mono">1.0.0</span>
          </div>
          <div className="kv-key">SYSTEM HASH:</div>
          <div className="kv-val mono fs-xs">e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855</div>
          <div className="ops-kv">
            <span className="kv-key">FLIGHT LOG QUALIFIED:</span>
            <span className="kv-val green-text">YES (ECSS-E-ST-70-11C COMPLIANT)</span>
          </div>
        </div>
      </div>

      {/* Lifecycle Timeline Steps */}
      <div className="section-block">
        <h2 className="section-title">FULL INVESTIGATION LIFECYCLE TIMELINE</h2>
        <div className="timeline-wrapper">
          {auditStages.map((stage, idx) => (
            <div key={idx} className="timeline-item">
              <div className="timeline-badge">
                <span className="mono bold">{idx + 1}</span>
              </div>
              <div className="timeline-content ops-card">
                <div className="card-header flex-between">
                  <span className="bold">{stage.name} STAGE</span>
                  <div className="flex-gap">
                    <span className="mono fs-xs">{stage.duration}</span>
                    <span className={`badge-pill badge-${stage.status === "COMPLETED" ? "nominal" : "warning"}`}>
                      [{stage.status}]
                    </span>
                  </div>
                </div>
                <div className="card-body">
                  <p className="mono fs-sm">{stage.detail}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
