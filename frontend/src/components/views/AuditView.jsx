/*
 * Audit — the investigation timeline, straight from the append-only audit
 * store (GET /api/v1/runs, GET /api/v1/runs/{id}, GET /runs/{id}/verify).
 *
 * The timeline is the entry list itself, in recorded order:
 * ingestion -> detection -> state estimation -> hypotheses -> physics ->
 * RAG -> LLM -> safety -> diagnosis -> operator decision.
 * A stage absent from the record is reported as NOT RUN, never as OK.
 */

import React, { useMemo, useState } from "react";
import { useSentinel } from "../../state/SentinelContext";
import { stageEntries } from "../../state/selectors";
import Panel from "../ui/Panel";
import StatusBadge from "../ui/StatusBadge";
import ValueCell from "../ui/ValueCell";
import AsyncBlock from "../ui/AsyncBlock";

const STAGE_ORDER = [
  "input",
  "detection",
  "state_estimation",
  "hypotheses",
  "physics_validation",
  "rag",
  "llm",
  "safety_validation",
  "diagnosis",
  "operator_decision",
];

const STAGE_LABELS = {
  input: "Ingestion",
  detection: "Detection",
  state_estimation: "State estimation",
  hypotheses: "Hypotheses",
  physics_validation: "Physics validation",
  rag: "RAG retrieval",
  llm: "LLM",
  safety_validation: "Safety validation",
  diagnosis: "Diagnosis",
  operator_decision: "Operator decision",
};

export default function AuditView() {
  const {
    runs,
    selectedRun,
    selectedRunId,
    selectRun,
    verifyRun,
    chainVerification,
  } = useSentinel();

  const [expandedPayload, setExpandedPayload] = useState(null);

  const runList = runs?.data?.runs || [];
  const record = selectedRun?.data;
  const entries = useMemo(() => (record ? stageEntries(record) : []), [record]);

  const coverage = useMemo(() => {
    const map = {};
    for (const entry of entries) {
      map[entry.stage] = entry.status;
    }
    return map;
  }, [entries]);

  return (
    <div className="view-stack">
      <div className="view-heading">
        <h1 className="view-heading__title">Audit</h1>
        <p className="view-heading__sub">
          Append-only investigation records: run timeline, stage outcomes,
          measured durations and SHA-256 chain verification.
        </p>
      </div>

      <Panel id="au-runs" title="Audit runs">
        <AsyncBlock entity={runs}>
          {runList.length === 0 ? (
            <p className="muted-text">
              NO RUNS RECORDED — the audit store is empty. Run FDIR analysis to
              create the first record.
            </p>
          ) : (
            <div className="run-list">
              {runList.map((r) => (
                <button
                  key={r.run_id}
                  type="button"
                  className={`run-row ${selectedRunId === r.run_id ? "run-row--active" : ""}`}
                  onClick={() => selectRun(r.run_id)}
                >
                  <span className="run-row__id mono">{r.run_id}</span>
                  <span className="run-row__meta mono">
                    {r.started_at} · {r.fault_type || "N/A"} · {r.scenario_id ? `SCN ${r.scenario_id}` : "N/A"}
                  </span>
                  <StatusBadge status={r.status} />
                </button>
              ))}
            </div>
          )}
        </AsyncBlock>
      </Panel>

      <Panel id="au-record" title="Run record">
        <AsyncBlock entity={selectedRun}>
          {record ? (
            <div>
              <div className="flex-between">
                <span className="mono bold">{record.run_id}</span>
                <div className="btn-row">
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={() => verifyRun(record.run_id)}
                    disabled={chainVerification?.loading}
                  >
                    VERIFY CHAIN (SHA-256)
                  </button>
                </div>
              </div>
              <dl className="value-grid value-grid--4col">
                <ValueCell label="Started at" value={record.header?.started_at || null} monospace />
                <ValueCell label="Finished at" value={record.outcome?.finished_at || null} monospace />
                <ValueCell label="Status" value={record.outcome?.status || null} monospace />
                <ValueCell label="Provenance" value={record.header?.provenance || null} monospace />
                <ValueCell label="Origin" value={record.header?.origin || null} monospace />
                <ValueCell label="Audit schema" value={record.header?.audit_schema_version || null} monospace />
                <ValueCell label="Total duration" value={record.outcome?.total_duration_ms != null ? `${record.outcome.total_duration_ms.toFixed(1)} ms` : null} monospace />
                <ValueCell label="Input SHA-256" value={record.header?.input_sha256?.slice(0, 24) || null} monospace />
              </dl>

              {chainVerification?.data ? (
                <div className={`chain-verify ${chainVerification.data.valid ? "chain-verify--valid" : "chain-verify--invalid"}`}>
                  <StatusBadge
                    status={chainVerification.data.valid ? "VALID" : "INVALID"}
                    label={chainVerification.data.valid ? "CHAIN VERIFIED" : "CHAIN BROKEN"}
                  />
                  <span className="mono fs-sm">
                    {chainVerification.data.entry_count} ENTRIES · CHECKED {chainVerification.data.checked_at}
                  </span>
                  {chainVerification.data.problems?.length ? (
                    <ul className="plain-list">
                      {chainVerification.data.problems.map((p, i) => (
                        <li key={i} className="error-text fs-sm">{p}</li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : null}
              {chainVerification?.error ? (
                <p className="error-text fs-sm">VERIFY ERROR: {chainVerification.error}</p>
              ) : null}
            </div>
          ) : (
            <p className="muted-text">
              {selectedRun?.loading
                ? "LOADING RUN RECORD..."
                : "NO RUN SELECTED"}
            </p>
          )}
        </AsyncBlock>
      </Panel>

      <Panel id="au-timeline" title="Investigation timeline">
        {!record ? (
          <p className="muted-text">NOT AVAILABLE — select a run to view its timeline.</p>
        ) : entries.length === 0 ? (
          <p className="muted-text">NO ENTRIES RECORDED FOR THIS RUN</p>
        ) : (
          <ol className="timeline">
            {entries.map((entry) => (
              <li key={entry.seq} className="timeline__entry">
                <div className="timeline__head">
                  <span className="timeline__seq mono">#{String(entry.seq).padStart(2, "0")}</span>
                  <span className="timeline__stage mono bold">
                    {STAGE_LABELS[entry.stage] || entry.stage}
                  </span>
                  <StatusBadge status={entry.status} />
                  <span className="timeline__meta mono fs-sm">
                    {entry.duration_ms != null ? `${entry.duration_ms.toFixed(1)} ms` : "NO DURATION"} ·{" "}
                    {entry.recorded_at} · {entry.actor}
                  </span>
                </div>
                <p className="timeline__summary">{entry.summary}</p>
                <div className="timeline__hash mono fs-sm">
                  HASH {entry.entry_hash?.slice(0, 24)}… · PAYLOAD SHA {entry.payload_sha256?.slice(0, 16)}…
                </div>
                {Object.keys(entry.payload || {}).length > 0 ? (
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={() =>
                      setExpandedPayload(
                        expandedPayload === entry.seq ? null : entry.seq
                      )
                    }
                    aria-expanded={expandedPayload === entry.seq}
                  >
                    {expandedPayload === entry.seq ? "HIDE PAYLOAD" : "SHOW PAYLOAD"}
                  </button>
                ) : null}
                {expandedPayload === entry.seq ? (
                  <pre className="pre-wrap payload-pre">{JSON.stringify(entry.payload, null, 2)}</pre>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </Panel>

      <Panel id="au-coverage" title="Stage coverage">
        {!record ? (
          <p className="muted-text">NOT AVAILABLE — select a run first.</p>
        ) : (
          <>
            <table className="data-table data-table--compact">
            <thead>
              <tr>
                <th scope="col">Stage</th>
                {STAGE_ORDER.map((stage) => (
                  <th key={stage} scope="col" title={STAGE_LABELS[stage]}>
                    <span className="mono fs-sm">{STAGE_LABELS[stage]}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row" className="mono fs-sm">Status</th>
                {STAGE_ORDER.map((stage) => {
                  const status = coverage[stage] || "NOT_RUN";
                  return (
                    <td key={stage}>
                      <StatusBadge status={status} label={status} />
                    </td>
                  );
                })}
              </tr>
            </tbody>
          </table>
          <p className="muted-text fs-sm">
            Absent stages are reported as NOT RUN. Statuses are read from the
            append-only record; nothing here is client-side state.
          </p>
          </>
        )}
      </Panel>
    </div>
  );
}