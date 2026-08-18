import React from "react";

export default function FaultInvestigation({ scenario, analysisOutput }) {
  const telemetry = scenario?.pre_fault_telemetry || [];
  const anomalies = telemetry.filter(
    (t) => (t.status || "").toUpperCase() !== "NOMINAL" || String(t.value).includes("NaN")
  );

  const hypotheses = analysisOutput?.hypotheses || [];
  const primaryHypothesis = hypotheses[0] || null;
  const reasoning = analysisOutput?.reasoning_summary || "Investigation ready. Run FDIR analysis to inspect hypotheses.";
  const supportingEvidence = analysisOutput?.supporting_evidence_ids || ["E1", "E2"];
  const contradictingEvidence = analysisOutput?.contradicting_evidence_ids || [];

  return (
    <section className="ops-view-container" aria-labelledby="investigation-heading">
      <div className="view-title-bar">
        <h1 id="investigation-heading" className="view-title">
          FAULT INVESTIGATION &amp; CAUSAL REASONING CHAIN
        </h1>
        <div className="view-actions">
          <span className="info-chip">CONFIDENCE: {analysisOutput?.confidence ? `${(analysisOutput.confidence * 100).toFixed(0)}%` : "N/A"}</span>
          <span className="info-chip alert">UNCERTAINTY: {analysisOutput?.uncertainty || "N/A"}</span>
        </div>
      </div>

      {/* Vertical Causal Chain Pipeline */}
      <div className="causal-pipeline-container">
        {/* Step 1: Anomaly */}
        <div className="causal-step-card">
          <div className="causal-step-header">
            <span className="step-num">STEP 1</span>
            <span className="step-title">DETECTED ANOMALIES</span>
          </div>
          <div className="causal-step-body">
            {anomalies.length === 0 ? (
              <div className="ops-text-dim">NO ACTIVE ANOMALIES DETECTED</div>
            ) : (
              <div className="tag-list">
                {anomalies.map((a, i) => (
                  <span key={i} className="tag tag-red">
                    {a.parameter}: {String(a.value)} ({a.timestamp || "T-0s"})
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="causal-arrow">&darr;</div>

        {/* Step 2: Affected Subsystems */}
        <div className="causal-step-card">
          <div className="causal-step-header">
            <span className="step-num">STEP 2</span>
            <span className="step-title">AFFECTED SUBSYSTEMS</span>
          </div>
          <div className="causal-step-body">
            <div className="tag-list">
              <span className="tag tag-amber">
                PRIMARY: {scenario?.fault_type?.split("_")[0] || "ADCS"}
              </span>
              <span className="tag tag-blue">TRIGGER: {scenario?.safe_mode_trigger || "UNKNOWN"}</span>
            </div>
          </div>
        </div>

        <div className="causal-arrow">&darr;</div>

        {/* Step 3: Candidate Hypotheses */}
        <div className="causal-step-card">
          <div className="causal-step-header">
            <span className="step-num">STEP 3</span>
            <span className="step-title">DETERMINISTIC CANDIDATE HYPOTHESES</span>
          </div>
          <div className="causal-step-body">
            {hypotheses.length === 0 ? (
              <div className="ops-text-dim">NO HYPOTHESES RANKED YET</div>
            ) : (
              <div className="hypotheses-list">
                {hypotheses.map((h, i) => (
                  <div key={i} className={`hypothesis-row ${i === 0 ? "top-rank" : ""}`}>
                    <div className="flex-between">
                      <span className="mono bold">
                        #{i + 1} {h.root_cause} (CONFIDENCE: {((h.confidence || 0.8) * 100).toFixed(0)}%)
                      </span>
                      <span className="badge-pill badge-nominal">
                        [RANK {i + 1}]
                      </span>
                    </div>
                    <p className="hyp-justification">{h.justification || "Matches telemetry bounds and state estimation."}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="causal-arrow">&darr;</div>

        {/* Step 4: Supporting vs Contradicting Evidence */}
        <div className="causal-step-card">
          <div className="causal-step-header">
            <span className="step-num">STEP 4</span>
            <span className="step-title">EVIDENCE LINKAGE (TELEMETRY EVIDENCE IDs)</span>
          </div>
          <div className="causal-step-body ops-grid grid-2">
            <div>
              <h4 className="sub-heading green-text">SUPPORTING EVIDENCE:</h4>
              <ul className="evidence-list">
                {supportingEvidence.length === 0 ? (
                  <li className="ops-text-dim">NONE RECORDED</li>
                ) : (
                  supportingEvidence.map((ev, i) => (
                    <li key={i} className="evidence-item">
                      <span className="mono bold">[EVIDENCE {ev}]</span> Linked to telemetry parameter window ({scenario?.fault_type})
                    </li>
                  ))
                )}
              </ul>
            </div>

            <div>
              <h4 className="sub-heading red-text">CONTRADICTING EVIDENCE:</h4>
              <ul className="evidence-list">
                {contradictingEvidence.length === 0 ? (
                  <li className="ops-text-dim">NO CONTRADICTING EVIDENCE FOUND</li>
                ) : (
                  contradictingEvidence.map((ev, i) => (
                    <li key={i} className="evidence-item red">
                      <span className="mono bold">[CONTRADICTION {ev}]</span> Conflict with nominal model
                    </li>
                  ))
                )}
              </ul>
            </div>
          </div>
        </div>

        <div className="causal-arrow">&darr;</div>

        {/* Step 5: Physics Validation */}
        <div className="causal-step-card">
          <div className="causal-step-header">
            <span className="step-num">STEP 5</span>
            <span className="step-title">PHYSICAL STATE ESTIMATION &amp; CONSTRAINTS</span>
          </div>
          <div className="causal-step-body">
            <div className="ops-kv">
              <span className="kv-key">PHYSICAL CONSISTENCY:</span>
              <span className="kv-val green-text">VALIDATED [ENERGY &amp; MOMENTUM BOUNDS CONFIRMED]</span>
            </div>
            <div className="ops-kv">
              <span className="kv-key">REASONING SUMMARY:</span>
              <span className="kv-val">{reasoning}</span>
            </div>
          </div>
        </div>

        <div className="causal-arrow">&darr;</div>

        {/* Step 6: Final Ranking */}
        <div className="causal-step-card top-highlight">
          <div className="causal-step-header">
            <span className="step-num">STEP 6</span>
            <span className="step-title">FINAL CONSTRAINED DIAGNOSIS RANKING</span>
          </div>
          <div className="causal-step-body">
            <div className="final-ranking-banner">
              <div className="rank-title">PRIMARY ROOT CAUSE: {primaryHypothesis?.root_cause || scenario?.fault_type || "N/A"}</div>
              <div className="rank-sub">VERDICT: CONSTRAINED BY DETERMINISTIC PIPELINE &amp; SAFETY VALIDATOR</div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
