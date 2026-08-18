/*
 * Evidence — retrieved sources, citations and recorded evidence from the
 * audit trail. Every item is served by the backend audit store
 * (GET /api/v1/runs/{run_id}) or the RAG retrieval recorded in it.
 *
 * Field mapping (document / source / version / section-clause / citation /
 * relevance):
 *   - document:      retrieved source identifier
 *   - source:        retrieval backend + source kind
 *   - version:       source document version (pdf metadata carries none)
 *   - section/clause: page (pdf_rag) or N/A
 *   - citation:      content SHA-256 recorded in the audit entry
 *   - relevance:     retrieval distance or fallback match score
 */

import React, { useMemo } from "react";
import { useSentinel } from "../../state/SentinelContext";
import { stageSummary } from "../../state/selectors";
import Panel from "../ui/Panel";
import StatusBadge from "../ui/StatusBadge";
import ValueCell from "../ui/ValueCell";
import DataTable from "../ui/DataTable";
import Icon from "../ui/Icon";

export default function EvidenceView({ onNavigate }) {
  const {
    runs,
    selectedRun,
    selectedRunId,
    selectRun,
    analysis,
    focusTelemetry,
  } = useSentinel();

  const runList = runs?.data?.runs || [];
  const record = selectedRun?.data;
  const rag = record ? stageSummary(record, "rag") : null;
  const inputStage = record ? stageSummary(record, "input") : null;
  const llmStage = record ? stageSummary(record, "llm") : null;
  const ragPayload = rag?.payload || {};
  const sources = ragPayload.sources || [];

  const coverage = inputStage?.payload?.telemetry_coverage || null;

  const latestCompletedRun = useMemo(() => {
    if (runList.length === 0) return null;
    return (
      runList.find((r) => r.status === "COMPLETED") ||
      runList[0]
    );
  }, [runList]);

  const activeRunId = selectedRunId || analysis?.runId || latestCompletedRun?.run_id || null;

  const loadActiveRun = () => {
    if (activeRunId && activeRunId !== selectedRunId) {
      selectRun(activeRunId);
    }
  };

  const jumpToEvidence = (channel, timestamp) => {
    focusTelemetry(channel, timestamp);
    onNavigate("telemetry");
  };

  return (
    <div className="view-stack">
      <div className="view-heading">
        <h1 className="view-heading__title">Evidence</h1>
        <p className="view-heading__sub">
          Retrieved procedure sources, citations and recorded telemetry evidence
          for the selected audit run. Nothing here is synthesised by the
          frontend.
        </p>
      </div>

      <Panel id="ev-run" title="Evidence run selection">
        <div className="toolbar">
          <label className="field-label" htmlFor="ev-run-select">
            Audit run
          </label>
          <select
            id="ev-run-select"
            className="field-select"
            value={activeRunId || ""}
            onChange={(e) => {
              if (e.target.value) selectRun(e.target.value);
            }}
          >
            {runList.length === 0 ? (
              <option value="">NO RUNS RECORDED</option>
            ) : (
              runList.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {r.run_id} — {r.fault_type || "N/A"} [{r.status}]
                </option>
              ))
            )}
          </select>
          {activeRunId && activeRunId !== selectedRunId ? (
            <button type="button" className="btn btn--sm" onClick={loadActiveRun}>
              Load run {activeRunId}
            </button>
          ) : null}
          {runList.length === 0 ? (
            <button
              type="button"
              className="btn btn--sm"
              onClick={() => onNavigate("overview")}
            >
              Run FDIR analysis to create evidence
            </button>
          ) : null}
        </div>
      </Panel>

      <Panel id="ev-rag" title="Retrieved sources (RAG)">
        {!selectedRun?.data && selectedRun?.loading ? (
          <p className="muted-text">LOADING RUN RECORD...</p>
        ) : selectedRun?.error ? (
          <p className="error-text">BACKEND ERROR: {selectedRun.error}</p>
        ) : !rag ? (
          <p className="muted-text">
            NOT AVAILABLE — no RAG retrieval recorded for the selected run.
            Retrieval evidence is written to the audit record when an FDIR
            analysis runs.
          </p>
        ) : (
          <div>
            <dl className="value-grid value-grid--4col">
              <ValueCell label="Retrieval backend" value={ragPayload.backend || null} monospace />
              <ValueCell label="Query" value={ragPayload.query || null} placeholder="NOT AVAILABLE" />
              <ValueCell label="Snippet count" value={ragPayload.snippet_count ?? null} monospace />
              <ValueCell label="Top-K requested" value={ragPayload.top_k ?? null} monospace />
            </dl>
            <DataTable
              caption="Retrieved evidence sources with citations"
              emptyMessage="NO SOURCES RECORDED"
              columns={[
                { key: "document", label: "Document", render: (r) => <span className="mono bold">{r.document}</span> },
                { key: "source", label: "Source", render: (r) => <span className="mono">{r.source}</span> },
                { key: "version", label: "Version", render: (r) => <span className="mono">{r.version}</span> },
                { key: "section", label: "Section / clause", render: (r) => <span className="mono">{r.section}</span> },
                { key: "citation", label: "Citation (content hash)", render: (r) => <span className="mono fs-sm" title={r.citation}>{r.citation}</span> },
                { key: "relevance", label: "Relevance", render: (r) => <span className="mono">{r.relevance}</span> },
              ]}
              rows={sources.map((s, i) => ({
                key: `${s.identifier}-${i}`,
                document: s.identifier || s.title || "N/A",
                source: `${s.source_kind || "N/A"} / ${ragPayload.backend || "N/A"}`,
                version: "NOT AVAILABLE",
                section: s.source_kind === "pdf_rag" ? `PAGE ${s.page ?? "?"}` : "N/A",
                citation: s.content_sha256 || "N/A",
                relevance:
                  s.source_kind === "pdf_rag"
                    ? s.distance !== null && s.distance !== undefined
                      ? `dist ${Number(s.distance).toFixed(3)}`
                      : "N/A"
                    : s.match_score !== null && s.match_score !== undefined
                    ? Number(s.match_score).toFixed(2)
                    : "N/A",
                matchedCues: s.matched_cues || [],
              }))}
            />
            {ragPayload.snippet_hashes?.length ? (
              <p className="muted-text fs-sm">
                {ragPayload.snippet_hashes.length} snippet content hash(es) recorded; snippet text
                is not stored in the audit record.
              </p>
            ) : null}
          </div>
        )}
      </Panel>

      <Panel id="ev-telemetry" title="Telemetry evidence in record">
        {!inputStage ? (
          <p className="muted-text">NOT AVAILABLE — no input stage recorded for this run</p>
        ) : (
          <div>
            <dl className="value-grid value-grid--4col">
              <ValueCell
                label="Readings recorded"
                value={inputStage?.payload?.telemetry?.length ?? null}
                monospace
              />
              <ValueCell
                label="Canonical channels"
                value={coverage?.canonical_channels?.length ?? null}
                monospace
              />
              <ValueCell
                label="Run provenance"
                value={inputStage?.payload?.run_provenance || null}
                monospace
              />
              <ValueCell
                label="Payload declares"
                value={inputStage?.payload?.declared_provenance || null}
                monospace
              />
            </dl>
            {coverage?.coverage ? (
              <details className="details">
                <summary className="mono fs-sm">PER-CHANNEL COVERAGE ({Object.keys(coverage.coverage).length})</summary>
                <ul className="plain-list">
                  {Object.entries(coverage.coverage).map(([ch, cov]) => (
                    <li key={ch} className="mono fs-sm">
                      <button
                        type="button"
                        className="link-button"
                        onClick={() => jumpToEvidence(ch, null)}
                      >
                        <Icon name="link" size={10} />
                        {ch}
                      </button>{" "}
                      — {JSON.stringify(cov)}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </div>
        )}
      </Panel>

      <Panel id="ev-llm" title="Model evidence">
        {!llmStage ? (
          <p className="muted-text">
            NOT AVAILABLE — no LLM stage recorded for this run (stub mode
            records a stub identity; see the audit record).
          </p>
        ) : (
          <div>
            <dl className="value-grid value-grid--4col">
              <ValueCell label="Provider" value={llmStage?.payload?.provider || null} monospace />
              <ValueCell label="Model" value={llmStage?.payload?.model || null} monospace />
              <ValueCell label="Mode" value={llmStage?.payload?.mode || llmStage?.payload?.llm_mode || null} monospace />
              <ValueCell label="Inference performed" value={llmStage?.payload?.inference_performed !== undefined ? String(llmStage.payload.inference_performed) : null} />
              <ValueCell label="Prompt version" value={llmStage?.payload?.prompt_version || null} monospace />
              <ValueCell label="Attempts" value={llmStage?.payload?.attempts ?? null} monospace />
              <ValueCell label="Response count" value={llmStage?.payload?.response_count ?? null} monospace />
              <ValueCell label="Stub claim" value={llmStage?.payload?.claim || null} placeholder="NOT AVAILABLE" />
            </dl>
            {llmStage?.payload?.raw_responses?.length ? (
              <details className="details">
                <summary className="mono fs-sm">
                  RAW MODEL RESPONSES ({llmStage.payload.raw_responses.length})
                </summary>
                {llmStage.payload.raw_responses.map((r, i) => (
                  <pre key={i} className="pre-wrap fs-sm">
                    {r.text ?? JSON.stringify(r)}
                  </pre>
                ))}
              </details>
            ) : null}
          </div>
        )}
      </Panel>

      <Panel id="ev-status" title="Run status">
        {record ? (
          <div className="flex-between">
            <StatusBadge status={record.outcome?.status || "UNKNOWN"} />
            <span className="mono fs-sm">
              {record.outcome?.entry_count ?? 0} ENTRIES · FINAL HASH {record.outcome?.final_hash?.slice(0, 16) || "N/A"}…
            </span>
          </div>
        ) : (
          <p className="muted-text">NOT AVAILABLE — no run record loaded.</p>
        )}
      </Panel>
    </div>
  );
}