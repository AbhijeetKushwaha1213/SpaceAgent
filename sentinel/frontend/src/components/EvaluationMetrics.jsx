import React, { useState, useEffect } from "react";

export default function EvaluationMetrics({ backendUrl }) {
  const [evalData, setEvalData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchResults = async () => {
    setLoading(true);
    setError(null);
    try {
      const url = `${backendUrl || "http://localhost:8000"}/api/v1/evaluation/results?split=HELD_OUT_TEST`;
      const resp = await fetch(url);
      if (!resp.ok) {
        throw new Error(`Evaluation API returned HTTP ${resp.status}`);
      }
      const data = await resp.json();
      setEvalData(data);
    } catch (err) {
      console.warn("Failed to fetch evaluation results from backend:", err);
      setError(err.message || "Failed to load evaluation metrics");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchResults();
  }, [backendUrl]);

  const provenance = evalData?.provenance || null;
  const pipelines = evalData?.pipelines || {};

  const pipelineList = [
    { key: "baseline_1", name: "BASELINE 1: Z-SCORE + RULES" },
    { key: "baseline_2", name: "BASELINE 2: ENHANCED DETECTOR + HYPOTHESES" },
    { key: "baseline_3", name: "BASELINE 3: DETECTOR + RAG + UNCONSTRAINED LLM" },
    { key: "sentinel", name: "SENTINEL: DETECTOR + PHYSICS + RAG + CONSTRAINED LLM" },
  ];

  return (
    <section className="ops-view-container" aria-labelledby="evaluation-heading">
      <div className="view-title-bar">
        <h1 id="evaluation-heading" className="view-title">
          REPRODUCIBLE EVALUATION BENCHMARK DASHBOARD (PHASE 12)
        </h1>
        <div className="view-actions">
          <button className="ops-btn ops-btn-sm" onClick={fetchResults} disabled={loading}>
            [REFRESH BENCHMARKS]
          </button>
          <span className="info-chip">SPLIT: {provenance?.split || "HELD_OUT_TEST"}</span>
        </div>
      </div>

      {/* Provenance Metadata Card */}
      <div className="ops-card mb-20">
        <div className="card-header flex-between">
          <span>EVALUATION RUN PROVENANCE METADATA</span>
          <span className="badge-pill badge-nominal">
            [{provenance ? `DATASET ${provenance.dataset_version}` : "LOADING METADATA"}]
          </span>
        </div>
        <div className="card-body ops-grid grid-4">
          <div className="ops-kv">
            <span className="kv-key">DATASET VERSION:</span>
            <span className="kv-val mono">{provenance?.dataset_version || "N/A"}</span>
          </div>
          <div className="ops-kv">
            <span className="kv-key">CODE VERSION:</span>
            <span className="kv-val mono">{provenance?.code_version || "1.0.0"}</span>
          </div>
          <div className="ops-kv">
            <span className="kv-key">EVALUATION MODEL:</span>
            <span className="kv-val mono">{provenance?.model || "N/A"}</span>
          </div>
          <div className="ops-kv">
            <span className="kv-key">RANDOM SEED / TIME:</span>
            <span className="kv-val mono">{provenance?.seed ?? 42} ({provenance?.timestamp ? new Date(provenance.timestamp).toLocaleTimeString() : "N/A"})</span>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="ops-empty-state">LOADING ACTUAL EVALUATION BENCHMARK METRICS FROM BACKEND...</div>
      ) : error ? (
        <div className="ops-card alert-bg">
          <div className="card-header red-text bold">EVALUATION BENCHMARK ERROR</div>
          <div className="card-body">
            <p className="red-text">{error}</p>
            <p className="ops-text-dim mt-5">Ensure SENTINEL FastAPI backend is running on {backendUrl || "http://localhost:8000"}.</p>
          </div>
        </div>
      ) : (
        <div className="section-block">
          <h2 className="section-title">4-PIPELINE COMPARATIVE METRICS MATRIX</h2>
          <table className="ops-table" aria-label="Comparative evaluation metrics table">
            <thead>
              <tr>
                <th>PIPELINE CONFIGURATION</th>
                <th>ANOMALY F1 SCORE</th>
                <th>TOP-1 ACCURACY</th>
                <th>TOP-3 ACCURACY</th>
                <th>BRIER SCORE</th>
                <th>ECE (CALIBRATION)</th>
                <th>RAG PRECISION</th>
                <th>SAFETY BLOCKING</th>
                <th>E2E LATENCY (MS)</th>
              </tr>
            </thead>
            <tbody>
              {pipelineList.map((p) => {
                const data = pipelines[p.key];
                const isUnrun = !data || data === "NOT EVALUATED";

                if (isUnrun) {
                  return (
                    <tr key={p.key} className="row-dim">
                      <td className="bold mono">{p.name}</td>
                      <td colSpan="8" className="mono red-text center-text">NOT EVALUATED</td>
                    </tr>
                  );
                }

                const anom = data.anomaly_detection || {};
                const diag = data.final_diagnosis || {};
                const cal = data.calibration || {};
                const rag = data.rag || {};
                const safe = data.safety || {};
                const sysPerf = data.system_performance || {};

                return (
                  <tr key={p.key} className={p.key === "sentinel" ? "row-highlight" : ""}>
                    <td className="bold mono">{p.name}</td>
                    <td className="mono bold">{anom.f1 !== undefined ? (anom.f1 * 100).toFixed(1) + "%" : "N/A"}</td>
                    <td className="mono bold green-text">{diag.top1_accuracy !== undefined ? (diag.top1_accuracy * 100).toFixed(1) + "%" : "N/A"}</td>
                    <td className="mono bold">{diag.top3_accuracy !== undefined ? (diag.top3_accuracy * 100).toFixed(1) + "%" : "N/A"}</td>
                    <td className="mono">{cal.brier_score !== undefined ? cal.brier_score.toFixed(4) : "N/A"}</td>
                    <td className="mono">{cal.expected_calibration_error !== undefined ? cal.expected_calibration_error.toFixed(4) : "N/A"}</td>
                    <td className="mono">{rag.retrieval_precision !== undefined ? (rag.retrieval_precision * 100).toFixed(1) + "%" : "N/A"}</td>
                    <td className="mono bold">{safe.unsafe_command_blocking_rate !== undefined ? (safe.unsafe_command_blocking_rate * 100).toFixed(0) + "%" : "N/A"}</td>
                    <td className="mono">{sysPerf.end_to_end_latency_ms !== undefined ? sysPerf.end_to_end_latency_ms.toFixed(1) + " ms" : "N/A"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
