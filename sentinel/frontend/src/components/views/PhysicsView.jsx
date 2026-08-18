/*
 * Physics / State — observed vs predicted vs residual, per hypothesis,
 * with constraint catalogue and validation verdicts from the backend.
 *
 * Verdict semantics (from the backend contract):
 *   VALID      not refuted and corroborated at least once — NOT "confirmed"
 *   INVALID    a declared constraint violated by DECIDED residual evidence
 *   UNCERTAIN  nothing decided either way — explicitly NOT a pass
 */

import React from "react";
import { useSentinel } from "../../state/SentinelContext";
import Panel from "../ui/Panel";
import AsyncBlock from "../ui/AsyncBlock";
import StatusBadge from "../ui/StatusBadge";
import ValueCell from "../ui/ValueCell";
import DataTable from "../ui/DataTable";

export default function PhysicsView() {
  const { physicsReport, physicsConstraints, selectedScenario: scenario } = useSentinel();

  const report = physicsReport?.data;
  const verdicts = report?.verdicts || [];
  const constraintsData = physicsConstraints?.data;

  const statusCounts = { VALID: 0, INVALID: 0, UNCERTAIN: 0 };
  for (const v of verdicts) {
    if (statusCounts[v.validation_status] !== undefined) {
      statusCounts[v.validation_status] += 1;
    }
  }

  return (
    <div className="view-stack">
      <div className="view-heading">
        <h1 className="view-heading__title">Physics / State</h1>
        <p className="view-heading__sub">
          Simplified spacecraft state models: observed telemetry versus model
          prediction, residuals, and the deterministic validation verdict for
          each candidate hypothesis.
        </p>
      </div>

      <div className="grid-3">
        <Panel id="ph-valid" title="Valid hypotheses">
          <div className="stat-number">{statusCounts.VALID}</div>
          <StatusBadge status="VALID" />
          <p className="muted-text fs-sm">
            Not refuted and corroborated at least once. Not confirmation.
          </p>
        </Panel>
        <Panel id="ph-invalid" title="Invalid hypotheses">
          <div className="stat-number">{statusCounts.INVALID}</div>
          <StatusBadge status="INVALID" />
          <p className="muted-text fs-sm">
            A declared constraint was violated by decided residual evidence.
          </p>
        </Panel>
        <Panel id="ph-uncertain" title="Uncertain hypotheses">
          <div className="stat-number">{statusCounts.UNCERTAIN}</div>
          <StatusBadge status="UNCERTAIN" />
          <p className="muted-text fs-sm">
            Nothing decided either way. Explicitly not a pass.
          </p>
        </Panel>
      </div>

      <Panel id="ph-model" title="Validation model context">
        <AsyncBlock entity={physicsReport}>
          <dl className="value-grid value-grid--4col">
            <ValueCell label="Model version" value={report?.model_version || null} monospace />
            <ValueCell label="Constraint set version" value={report?.constraint_set_version || null} monospace />
            <ValueCell label="Hypotheses examined" value={report?.hypotheses_examined ?? null} monospace />
            <ValueCell label="Deterministic" value={report?.deterministic !== undefined ? String(report.deterministic) : null} monospace />
            <ValueCell label="Uses LLM" value={report?.uses_llm !== undefined ? String(report.uses_llm) : null} monospace />
            <ValueCell label="Flight qualified" value={report?.flight_qualified !== undefined ? String(report.flight_qualified) : null} monospace />
            <ValueCell label="Assumed parameters" value={report?.assumed_parameters?.length ?? null} monospace />
            <ValueCell label="Model limitations" value={report?.model_limitations?.length ?? null} monospace />
          </dl>
          {report?.assumed_parameters?.length ? (
            <details className="details">
              <summary className="mono fs-sm">ASSUMED PARAMETERS ({report.assumed_parameters.length})</summary>
              <ul className="plain-list">
                {report.assumed_parameters.map((p, i) => (
                  <li key={i} className="mono fs-sm">
                    {JSON.stringify(p)}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          {report?.model_limitations?.length ? (
            <details className="details">
              <summary className="mono fs-sm">MODEL LIMITATIONS ({report.model_limitations.length})</summary>
              <ul className="plain-list">
                {report.model_limitations.map((l, i) => (
                  <li key={i} className="fs-sm">{l}</li>
                ))}
              </ul>
            </details>
          ) : null}
          {report?.claim ? <p className="claim-note">{report.claim}</p> : null}
          {report?.summary ? (
            <p className="muted-text fs-sm">{report.summary}</p>
          ) : null}
        </AsyncBlock>
      </Panel>

      <Panel id="ph-verdicts" title="Hypothesis validation verdicts">
        <AsyncBlock entity={physicsReport}>
          {verdicts.length === 0 ? (
            <p className="muted-text">NO VERDICTS AVAILABLE</p>
          ) : (
            <DataTable
              caption="Physics validation verdicts per candidate hypothesis"
              emptyMessage="NO VERDICTS"
              columns={[
                { key: "hypothesis", label: "Hypothesis", render: (r) => <span className="mono bold">{r.hypothesis}</span> },
                { key: "verdict", label: "Verdict", render: (r) => <StatusBadge status={r.verdict} /> },
                { key: "refutedBy", label: "Refuted by" },
                { key: "corroborated", label: "Corroborated" },
                { key: "indeterminate", label: "Indeterminate" },
                { key: "applicable", label: "Applicable constraints" },
              ]}
              rows={verdicts.map((v) => ({
                key: v.hypothesis_id,
                hypothesis: v.fault_id || v.hypothesis_id,
                verdict: v.validation_status,
                refutedBy: v.refuted_by?.length ? v.refuted_by.join(", ") : "NONE",
                corroborated: v.corroborated_constraints?.length ? v.corroborated_constraints.join(", ") : "NONE",
                indeterminate: v.indeterminate_constraints?.length ? v.indeterminate_constraints.join(", ") : "NONE",
                applicable: v.applicable_constraints?.length ? v.applicable_constraints.join(", ") : "N/A",
              }))}
              rowClass={(r) =>
                r.verdict === "INVALID"
                  ? "row--critical"
                  : r.verdict === "UNCERTAIN"
                  ? "row--warning"
                  : ""
              }
            />
          )}
        </AsyncBlock>
      </Panel>

      <Panel id="ph-residuals" title="Observed vs predicted residuals">
        <AsyncBlock entity={physicsReport}>
          {verdicts.length === 0 ? (
            <p className="muted-text">NO RESIDUALS AVAILABLE</p>
          ) : (
            <div className="verdict-stack">
              {verdicts.map((verdict) => {
                const residuals = verdict.supporting_residuals || [];
                return (
                  <details key={verdict.hypothesis_id} className="details verdict-details" open={verdict.validation_status === "INVALID"}>
                    <summary className="mono fs-sm">
                      <StatusBadge status={verdict.validation_status} />{" "}
                      {verdict.fault_id || verdict.hypothesis_id} — {residuals.length} residual(s)
                    </summary>
                    {residuals.length === 0 ? (
                      <p className="muted-text fs-sm">NO SUPPORTING RESIDUALS RECORDED</p>
                    ) : (
                      <DataTable
                        caption={`Residuals for ${verdict.fault_id || verdict.hypothesis_id}`}
                        emptyMessage="NO RESIDUALS"
                        columns={[
                          { key: "channel", label: "Channel", render: (r) => <span className="mono">{r.channel}</span> },
                          { key: "from", label: "From" },
                          { key: "to", label: "To" },
                          { key: "observed", label: "Observed", render: (r) => <span className="mono">{r.observed} {r.unit}</span> },
                          { key: "predicted", label: "Predicted", render: (r) => <span className="mono">{r.predicted} {r.unit}</span> },
                          { key: "residual", label: "Residual", render: (r) => <span className="mono">{r.residual} {r.unit}</span> },
                          { key: "tolerance", label: "Tolerance", render: (r) => <span className="mono">{r.tolerance} {r.unit}</span> },
                          { key: "status", label: "Status", render: (r) => <StatusBadge status={r.status} /> },
                          { key: "equation", label: "Model equation", render: (r) => <span className="mono fs-sm">{r.equation || "N/A"}</span> },
                        ]}
                        rows={residuals.map((res, i) => ({
                          key: `${verdict.hypothesis_id}-${i}`,
                          channel: res.channel,
                          from: res.from_timestamp,
                          to: res.to_timestamp,
                          observed: res.observed !== null && res.observed !== undefined ? res.observed : "N/A",
                          predicted: res.predicted !== null && res.predicted !== undefined ? res.predicted : "N/A",
                          residual: res.residual !== null && res.residual !== undefined ? res.residual : "N/A",
                          tolerance: res.tolerance !== null && res.tolerance !== undefined ? res.tolerance : "N/A",
                          status: res.status,
                          equation: res.equation,
                          unit: res.unit,
                        }))}
                        rowClass={(r) =>
                          r.status === "INCONSISTENT"
                            ? "row--critical"
                            : r.status === "UNDECIDABLE"
                            ? "row--warning"
                            : ""
                        }
                      />
                    )}
                    {verdict.caveats?.length ? (
                      <ul className="plain-list">
                        {verdict.caveats.map((c, i) => (
                          <li key={i} className="fs-sm">{c}</li>
                        ))}
                      </ul>
                    ) : null}
                  </details>
                );
              })}
            </div>
          )}
        </AsyncBlock>
      </Panel>

      <Panel id="ph-constraints" title="Constraint catalogue">
        <AsyncBlock entity={physicsConstraints}>
          <p className="claim-note">{constraintsData?.status_rule || "NOT AVAILABLE"}</p>
          {constraintsData?.faults_without_coverage?.length ? (
            <p className="muted-text fs-sm">
              NO PHYSICS COVERAGE: {constraintsData.faults_without_coverage.join(", ")}
            </p>
          ) : null}
          <DataTable
            caption="Declared physical constraints and their refutation rules"
            emptyMessage="NO CONSTRAINTS AVAILABLE"
            columns={[
              { key: "constraint_id", label: "Constraint", render: (r) => <span className="mono bold">{r.constraint_id}</span> },
              { key: "family", label: "Family", render: (r) => <span className="mono">{r.family}</span> },
              { key: "statement", label: "Statement" },
              { key: "refutation_rule", label: "Refutation rule" },
            ]}
            rows={(constraintsData?.constraints || []).map((c, i) => ({
              key: c.constraint_id || i,
              constraint_id: c.constraint_id,
              family: c.family,
              statement: c.statement,
              refutation_rule: c.refutation_rule,
            }))}
          />
        </AsyncBlock>
      </Panel>

      {!scenario ? (
        <p className="muted-text">NO SCENARIO SELECTED — select a crash dump scenario to validate.</p>
      ) : null}
    </div>
  );
}