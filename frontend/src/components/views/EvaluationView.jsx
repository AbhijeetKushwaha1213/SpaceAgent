/*
 * Evaluation — actual reproducible benchmark results served by the backend
 * (GET /api/v1/evaluation/results). A pipeline without recorded metrics is
 * shown as NOT EVALUATED; no metric is ever invented client-side.
 */

import React, { useEffect, useState } from "react";
import { useSentinel } from "../../state/SentinelContext";
import { apiGet } from "../../api/client";
import { ENDPOINTS } from "../../api/endpoints";
import Panel from "../ui/Panel";
import ValueCell from "../ui/ValueCell";
import DataTable from "../ui/DataTable";
import StatusBadge from "../ui/StatusBadge";

const PIPELINES = [
  { key: "baseline_1", name: "Baseline 1: Z-score + rules" },
  { key: "baseline_2", name: "Baseline 2: enhanced detector + hypotheses" },
  { key: "baseline_3", name: "Baseline 3: detector + RAG + unconstrained LLM" },
  { key: "sentinel", name: "SENTINEL: detector + physics + RAG + constrained LLM" },
];

export default function EvaluationView() {
  const { backendUrl } = useSentinel();
  const [evaluation, setEvaluation] = useState({ data: null, loading: true, error: null });

  const fetchResults = async () => {
    setEvaluation((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const data = await apiGet(ENDPOINTS.evaluationResults);
      setEvaluation({ data, loading: false, error: null });
    } catch (err) {
      setEvaluation({ data: null, loading: false, error: String(err) });
    }
  };

  useEffect(() => {
    fetchResults();
    // eslint-disable-next-line -- fetchResults is recreated per render; fetch once per backendUrl
  }, [backendUrl]);

  const prov = evaluation.data?.provenance || null;
  const summary = evaluation.data?.summary || null;
  const pipelines = evaluation.data?.pipelines || {};

  const pipelineRows = PIPELINES.map((p) => {
    const data = pipelines[p.key];
    if (!data || typeof data !== "object") {
      return {
        key: p.key,
        name: p.name,
        evaluated: false,
        anomalyF1: "NOT EVALUATED",
        top1: "NOT EVALUATED",
        top3: "NOT EVALUATED",
        brier: "NOT EVALUATED",
        ece: "NOT EVALUATED",
        ragPrecision: "NOT EVALUATED",
        blocking: "NOT EVALUATED",
        latency: "NOT EVALUATED",
      };
    }
    const anom = data.anomaly_detection || {};
    const diag = data.final_diagnosis || {};
    const cal = data.calibration || {};
    const rag = data.rag || {};
    const safe = data.safety || {};
    const sys = data.system_performance || {};
    const num = (v) => (typeof v === "number" ? v : null);
    const pct = (v) => (num(v) !== null ? `${(v * 100).toFixed(1)}%` : "N/A");
    return {
      key: p.key,
      name: p.name,
      evaluated: true,
      anomalyF1: pct(num(anom.f1)),
      top1: pct(num(diag.top1_accuracy)),
      top3: pct(num(diag.top3_accuracy)),
      brier: num(cal.brier_score) !== null ? cal.brier_score.toFixed(4) : "N/A",
      ece:
        num(cal.expected_calibration_error) !== null
          ? cal.expected_calibration_error.toFixed(4)
          : "N/A",
      ragPrecision: pct(num(rag.retrieval_precision)),
      blocking: pct(num(safe.unsafe_command_blocking_rate)),
      latency:
        num(sys.end_to_end_latency_ms) !== null
          ? `${sys.end_to_end_latency_ms.toFixed(1)} ms`
          : "N/A",
    };
  });

  return (
    <div className="view-stack">
      <div className="view-heading">
        <h1 className="view-heading__title">Evaluation</h1>
        <p className="view-heading__sub">
          Reproducible benchmark results across pipeline configurations. Metrics
          come from the backend evaluation runner; unrun pipelines are marked
          NOT EVALUATED.
        </p>
      </div>

      <Panel
        id="ev-meta"
        title="Run provenance"
        actions={
          <button
            type="button"
            className="btn btn--sm"
            onClick={fetchResults}
            disabled={evaluation.loading}
          >
            {evaluation.loading ? "REFRESHING..." : "REFRESH"}
          </button>
        }
      >
        {evaluation.loading ? (
          <p className="muted-text">LOADING EVALUATION RESULTS...</p>
        ) : evaluation.error ? (
          <p className="error-text">BACKEND ERROR: {evaluation.error}</p>
        ) : (
          <dl className="value-grid value-grid--4col">
            <ValueCell label="Dataset version" value={prov?.dataset_version || null} monospace />
            <ValueCell label="Scenario version" value={prov?.scenario_version || null} monospace />
            <ValueCell label="Code version" value={prov?.code_version || null} monospace />
            <ValueCell label="Model" value={prov?.model || null} monospace />
            <ValueCell label="Seed" value={prov?.seed ?? null} monospace />
            <ValueCell label="Split" value={prov?.split || null} monospace />
            <ValueCell label="Scenarios evaluated" value={prov?.scenarios_evaluated ?? null} monospace />
            <ValueCell label="Evaluated at" value={prov?.timestamp || null} monospace />
          </dl>
        )}
      </Panel>

      <Panel id="ev-summary" title="Summary">
        {!evaluation.data ? (
          <p className="muted-text">NOT AVAILABLE — no evaluation results served.</p>
        ) : (
          <dl className="value-grid value-grid--3col">
            <ValueCell label="Total scenarios" value={summary?.total_scenarios ?? null} monospace />
            <ValueCell
              label="Evaluated pipelines"
              value={summary?.evaluated_pipelines?.length ?? null}
              monospace
            />
            <ValueCell
              label="Unrun pipelines"
              value={summary?.unrun_pipelines?.length ?? null}
              monospace
            />
          </dl>
        )}
      </Panel>

      <Panel id="ev-matrix" title="Pipeline comparison">
        {!evaluation.data ? (
          <p className="muted-text">NOT AVAILABLE</p>
        ) : (
          <DataTable
            caption="Comparative evaluation metrics across pipelines"
            emptyMessage="NO PIPELINE METRICS SERVED"
            columns={[
              { key: "name", label: "Pipeline", render: (r) => <span className="mono bold">{r.name}</span> },
              { key: "anomalyF1", label: "Anomaly F1" },
              { key: "top1", label: "Top-1 accuracy" },
              { key: "top3", label: "Top-3 accuracy" },
              { key: "brier", label: "Brier" },
              { key: "ece", label: "ECE" },
              { key: "ragPrecision", label: "RAG precision" },
              { key: "blocking", label: "Blocking rate" },
              { key: "latency", label: "E2E latency" },
              { key: "status", label: "Status", render: (r) =>
                r.evaluated ? <StatusBadge status="OK" label="EVALUATED" /> : <StatusBadge status="NOT_IMPLEMENTED" label="NOT EVALUATED" />
              },
            ]}
            rows={pipelineRows.map((r) => ({ ...r, key: r.key }))}
            rowClass={(r) => (!r.evaluated ? "row--dim" : r.key === "sentinel" ? "row--highlight" : "")}
          />
        )}
      </Panel>

      {evaluation.data?.charts ? (
        <Panel id="ev-charts" title="Charts data">
          <p className="muted-text fs-sm">
            Chart series are served by the backend evaluation runner
            ({Object.keys(evaluation.data.charts).length} chart set(s)).
          </p>
          <details className="details">
            <summary className="mono fs-sm">RAW CHART DATA</summary>
            <pre className="pre-wrap">{JSON.stringify(evaluation.data.charts, null, 2)}</pre>
          </details>
        </Panel>
      ) : null}
    </div>
  );
}