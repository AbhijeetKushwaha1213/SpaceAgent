/*
 * Fault Investigation — the causal chain rendered from real backend stages:
 *
 *   ANOMALY (POST /api/v1/detect)
 *     -> AFFECTED SUBSYSTEM (channel dictionary attribution)
 *     -> CANDIDATE HYPOTHESES (physics validator's deterministic set)
 *     -> SUPPORTING / CONTRADICTING EVIDENCE (state-estimation residuals)
 *     -> PHYSICS VALIDATION (VALDID / INVALID / UNCERTAIN verdicts)
 *     -> FINAL RANKING (constrained LLM hypotheses, when a run exists)
 *
 * Every evidence item links to its telemetry channel and timestamp and jumps
 * to the Telemetry view.
 */

import React from "react";
import { useSentinel } from "../../state/SentinelContext";
import {
  channelById,
  fmtPct,
} from "../../state/selectors";
import Panel from "../ui/Panel";
import StatusBadge from "../ui/StatusBadge";
import AsyncBlock from "../ui/AsyncBlock";
import ValueCell from "../ui/ValueCell";
import Icon from "../ui/Icon";

const PHYSICS_PRESENTATION_ORDER = {
  VALID: 0,
  UNCERTAIN: 1,
  INVALID: 2,
};

export default function FaultInvestigationView({ onNavigate }) {
  const {
    selectedScenario: scenario,
    detection,
    physicsReport,
    analysis,
    channelDictionary,
    focusTelemetry,
  } = useSentinel();

  const anomalies = detection?.data?.anomalies || [];
  const verdicts = physicsReport?.data?.verdicts || [];
  const llmHypotheses = analysis?.output?.hypotheses || [];
  const reasoning = analysis?.output?.reasoning_summary || null;

  const anomalousChannels = Array.from(new Set(anomalies.map((a) => a.channel)));

  const affectedSubsystems = Array.from(
    new Set(
      anomalousChannels.map((c) => {
        const ch = channelById(channelDictionary, c);
        return ch ? ch.subsystem : "UNKNOWN";
      })
    )
  );

  const sortedVerdicts = [...verdicts].sort(
    (a, b) =>
      (PHYSICS_PRESENTATION_ORDER[a.validation_status] ?? 9) -
      (PHYSICS_PRESENTATION_ORDER[b.validation_status] ?? 9)
  );

  const verdictForFault = (faultId) =>
    verdicts.find((v) => v.fault_id === faultId || v.hypothesis_id === faultId);

  const evidenceForVerdict = (verdict) => {
    const out = [];
    for (const res of verdict.supporting_residuals || []) {
      out.push({
        channel: res.channel,
        timestamp: res.from_timestamp,
        status: res.status,
        observed: res.observed,
        predicted: res.predicted,
        residual: res.residual,
        unit: res.unit,
        equation: res.equation,
      });
    }
    return out;
  };

  const jumpToEvidence = (channel, timestamp) => {
    focusTelemetry(channel, timestamp);
    onNavigate("telemetry");
  };

  const chainStep = (step, title, children, tone = "neutral") => (
    <li className={`chain-step chain-step--${tone}`}>
      <span className="chain-step__num mono">{step}</span>
      <div className="chain-step__body">
        <h3 className="chain-step__title">{title}</h3>
        {children}
      </div>
    </li>
  );

  return (
    <div className="view-stack">
      <div className="view-heading">
        <h1 className="view-heading__title">Fault Investigation</h1>
        <p className="view-heading__sub">
          Causal chain: detected anomaly to final ranked diagnosis, with the
          evidence each step rests on.
        </p>
      </div>

      <ol className="chain">
        {chainStep(
          "01",
          "Detected anomalies",
          <AsyncBlock entity={detection}>
            {anomalies.length === 0 ? (
              <p className="muted-text">NO ANOMALIES DETECTED IN THIS WINDOW</p>
            ) : (
              <table className="data-table data-table--compact">
                <thead>
                  <tr>
                    <th scope="col">Channel</th>
                    <th scope="col">Timestamp</th>
                    <th scope="col">Detector</th>
                    <th scope="col">Severity</th>
                    <th scope="col">Description</th>
                    <th scope="col">Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {anomalies.map((a, i) => (
                    <tr key={a.anomaly_id || i} className={a.severity === "CRITICAL" ? "row--critical" : ""}>
                      <td className="mono">{a.channel}</td>
                      <td className="mono">{a.timestamp}</td>
                      <td className="mono">{a.detector}</td>
                      <td>
                        <StatusBadge status={a.severity} />
                      </td>
                      <td>{a.description}</td>
                      <td>
                        <button
                          type="button"
                          className="btn btn--sm btn--link"
                          onClick={() => jumpToEvidence(a.channel, a.timestamp)}
                        >
                          <Icon name="link" size={11} />
                          Open telemetry
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </AsyncBlock>,
          anomalies.some((a) => a.severity === "CRITICAL") ? "critical" : "neutral"
        )}

        {chainStep(
          "02",
          "Affected subsystems",
          <AsyncBlock entity={{ loading: !scenario, data: scenario ? {} : null, error: null }}>
            {affectedSubsystems.length === 0 ? (
              <p className="muted-text">
                NOT DETERMINED — no anomalies to attribute. Subsystem attribution
                comes from the channel dictionary.
              </p>
            ) : (
              <div className="tag-row">
                {affectedSubsystems.map((sub) => (
                  <span key={sub} className="tag">
                    {sub}
                  </span>
                ))}
              </div>
            )}
          </AsyncBlock>
        )}

        {chainStep(
          "03",
          "Candidate hypotheses",
          <AsyncBlock entity={physicsReport} loadingText="GENERATING DETERMINISTIC HYPOTHESIS SET...">
            {sortedVerdicts.length === 0 ? (
              <p className="muted-text">NO CANDIDATE HYPOTHESES GENERATED</p>
            ) : (
              <div className="hypothesis-list">
                {sortedVerdicts.map((verdict) => {
                  const llmMatch = llmHypotheses.find((h) => h.root_cause === verdict.fault_id);
                  return (
                    <div key={verdict.hypothesis_id} className="hypothesis-card">
                      <div className="hypothesis-card__head">
                        <span className="mono bold">{verdict.fault_name || verdict.fault_id || verdict.hypothesis_id}</span>
                        {llmMatch ? (
                          <StatusBadge status="NOMINAL" label={`LLM RANK ${llmMatch.rank} · ${fmtPct(llmMatch.confidence)}`} />
                        ) : (
                          <StatusBadge status="UNKNOWN" label="NOT LLM-RANKED" />
                        )}
                      </div>
                      <p className="hypothesis-card__sub mono fs-sm">
                        {verdict.subsystem ? `SUBSYSTEM ${verdict.subsystem}` : "SUBSYSTEM N/A"} · {verdict.hypothesis_id}
                      </p>
                      {verdict.explanation ? <p>{verdict.explanation}</p> : null}
                    </div>
                  );
                })}
              </div>
            )}
          </AsyncBlock>
        )}

        {chainStep(
          "04",
          "Supporting and contradicting evidence",
          <AsyncBlock entity={physicsReport}>
            {sortedVerdicts.length === 0 ? (
              <p className="muted-text">NO EVIDENCE RECORDED — no hypotheses examined</p>
            ) : (
              <div className="evidence-grid">
                {sortedVerdicts.map((verdict) => {
                  const evidence = evidenceForVerdict(verdict);
                  if (evidence.length === 0) {
                    return (
                      <div key={verdict.hypothesis_id} className="evidence-card">
                        <h4 className="mono fs-sm">{verdict.fault_id}</h4>
                        <p className="muted-text">NO SUPPORTING RESIDUALS RECORDED</p>
                      </div>
                    );
                  }
                  return (
                    <div key={verdict.hypothesis_id} className="evidence-card">
                      <h4 className="mono fs-sm">{verdict.fault_id}</h4>
                      <ul className="evidence-list">
                        {evidence.map((e, i) => (
                          <li key={i} className="evidence-item">
                            <span className="mono">{e.channel} @ {e.timestamp}</span>
                            <span className="mono fs-sm muted-text">
                              obs {e.observed ?? "N/A"} / pred {e.predicted ?? "N/A"} / res {e.residual ?? "N/A"} {e.unit}
                            </span>
                            <button
                              type="button"
                              className="btn btn--sm btn--link"
                              onClick={() => jumpToEvidence(e.channel, e.timestamp)}
                            >
                              <Icon name="link" size={11} />
                              Open telemetry
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  );
                })}
              </div>
            )}
          </AsyncBlock>
        )}

        {chainStep(
          "05",
          "Physics validation",
          <AsyncBlock entity={physicsReport}>
            {sortedVerdicts.length === 0 ? (
              <p className="muted-text">NO PHYSICS VERDICTS AVAILABLE</p>
            ) : (
              <table className="data-table data-table--compact">
                <thead>
                  <tr>
                    <th scope="col">Hypothesis</th>
                    <th scope="col">Verdict</th>
                    <th scope="col">Refuted by</th>
                    <th scope="col">Corroborated by</th>
                    <th scope="col">Verdict basis</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedVerdicts.map((v) => (
                    <tr key={v.hypothesis_id} className={v.validation_status === "INVALID" ? "row--critical" : v.validation_status === "UNCERTAIN" ? "row--warning" : ""}>
                      <td className="mono">{v.fault_id || v.hypothesis_id}</td>
                      <td>
                        <StatusBadge status={v.validation_status} />
                      </td>
                      <td className="mono fs-sm">
                        {v.refuted_by?.length ? v.refuted_by.join(", ") : "NONE"}
                      </td>
                      <td className="mono fs-sm">
                        {v.corroborated_constraints?.length ? v.corroborated_constraints.join(", ") : "NONE"}
                      </td>
                      <td className="fs-sm">
                        {v.has_physics_coverage === false
                          ? `NO PHYSICS COVERAGE — ${v.claims_rationale || "verdict UNCERTAIN by construction"}`
                          : v.explanation || v.verdict_basis}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {physicsReport?.data ? (
              <div className="mt-10">
                <ValueCell
                  label="Physics model version"
                  value={physicsReport.data.model_version || null}
                  monospace
                />
                <ValueCell
                  label="Deterministic"
                  value={physicsReport.data.deterministic !== undefined ? String(physicsReport.data.deterministic) : null}
                  monospace
                />
                <ValueCell
                  label="Assumed parameters"
                  value={
                    physicsReport.data.assumed_parameters?.length
                      ? physicsReport.data.assumed_parameters.length
                      : null
                  }
                />
              </div>
            ) : null}
          </AsyncBlock>
        )}

        {chainStep(
          "06",
          "Final ranking",
          analysis?.output
            ? llmHypotheses.length > 0
              ? (() => {
                  const top = llmHypotheses.find((h) => h.rank === 1);
                  return (
                    <div>
                      <div className="final-ranking">
                        <span className="final-ranking__label">PRIMARY ROOT CAUSE</span>
                        <span className="final-ranking__value mono">
                          {top ? `${top.root_cause} (${fmtPct(top.confidence)})` : "N/A"}
                        </span>
                        <StatusBadge
                          status={analysis.output.requires_human_review ? "REQUIRES_HUMAN_REVIEW" : "VALIDATED"}
                          label={analysis.output.requires_human_review ? "HUMAN REVIEW REQUIRED" : "NO REVIEW REQUIRED"}
                        />
                      </div>
                      <ol className="ranked-hypotheses">
                        {[...llmHypotheses]
                          .sort((a, b) => a.rank - b.rank)
                          .map((h) => {
                            const verdict = verdictForFault(h.root_cause);
                            return (
                              <li key={h.rank} className="ranked-hypothesis">
                                <div className="ranked-hypothesis__head">
                                  <span className="mono bold">
                                    RANK {h.rank}: {h.root_cause}
                                  </span>
                                  <span className="mono fs-sm">
                                    {fmtPct(h.confidence)} confidence
                                  </span>
                                  {verdict ? <StatusBadge status={verdict.validation_status} label={`PHYSICS ${verdict.validation_status}`} /> : null}
                                </div>
                                <p className="fs-sm muted-text">
                                  {h.affected_component ? `AFFECTED: ${h.affected_component}` : "AFFECTED: N/A"}
                                </p>
                                {h.causal_chain?.length ? (
                                  <ol className="causal-chain">
                                    {h.causal_chain.map((link, i) => (
                                      <li key={i} className="mono fs-sm">
                                        {link}
                                      </li>
                                    ))}
                                  </ol>
                                ) : null}
                              </li>
                            );
                          })}
                      </ol>
                      {reasoning ? (
                        <p className="reasoning-summary">
                          <strong>Reasoning summary:</strong> {reasoning}
                        </p>
                      ) : null}
                    </div>
                  );
                })()
              : <p className="muted-text">NO RANKED HYPOTHESES IN ANALYSIS OUTPUT</p>
            : (
              <div>
                <p className="muted-text">
                  NO ANALYSIS RUN COMPLETED — deterministic candidate set and physics
                  verdicts above are the only ranking available. Run FDIR analysis to
                  produce the constrained LLM ranking.
                </p>
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={() => onNavigate("overview")}
                >
                  Run analysis from Mission Overview
                </button>
              </div>
            )
        )}
      </ol>
    </div>
  );
}