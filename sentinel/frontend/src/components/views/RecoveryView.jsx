/*
 * Recovery — three explicitly separated layers:
 *
 *   1. LLM / AI PROPOSAL      the plan the model proposed (recovery_plan +
 *                             blocked_steps, exactly as emitted)
 *   2. SAFETY VALIDATED       the subset that passed the deterministic
 *                             safety validator, with the safety_status
 *   3. OPERATOR APPROVAL      human decision, POSTed to the audit trail
 *                             (POST /api/v1/runs/{run_id}/decisions)
 *
 * Blocked commands are shown in their own dedicated panel and are never
 * hidden, whether or not any plan was approved.
 */

import React, { useMemo, useState } from "react";
import { useSentinel } from "../../state/SentinelContext";
import { blockedCommands, recoveryPlan } from "../../state/selectors";
import Panel from "../ui/Panel";
import StatusBadge from "../ui/StatusBadge";
import ValueCell from "../ui/ValueCell";
import BlockedCommandsPanel from "../BlockedCommandsPanel";

const SAFETY_BADGE = {
  VALIDATED: { status: "VALIDATED", label: "SAFETY VALIDATED" },
  PARTIALLY_BLOCKED: { status: "PARTIALLY_BLOCKED", label: "PARTIALLY BLOCKED" },
  BLOCKED: { status: "BLOCKED", label: "ALL BLOCKED" },
  REQUIRES_HUMAN_REVIEW: { status: "REQUIRES_HUMAN_REVIEW", label: "HUMAN REVIEW REQUIRED" },
  NOT_VALIDATED: { status: "NOT_VALIDATED", label: "NOT VALIDATED" },
};

export default function RecoveryView() {
  const { analysis, selectedScenario, recordDecision, decisionResult } = useSentinel();
  const [operatorId, setOperatorId] = useState("");
  const [rationale, setRationale] = useState("");
  const [lastDecision, setLastDecision] = useState(null);

  const plan = recoveryPlan(analysis);
  const blocked = blockedCommands(analysis);
  const safetyStatus = analysis?.output?.safety_status || null;
  const runId = analysis?.runId || null;

  const proposedSteps = useMemo(() => {
    const steps = plan.map((s) => ({
      step: s.step,
      command: s.command,
      rationale: s.rationale,
      wait_seconds: s.wait_seconds,
      verify: s.verify,
      risk: s.risk,
      approved: true,
    }));
    const blockedAsProposed = blocked.map((b) => ({
      step: b.step,
      command: b.command,
      rationale: b.reason,
      wait_seconds: null,
      verify: null,
      risk: "BLOCKED",
      approved: false,
      violated_constraint: b.violated_constraint,
    }));
    return [...steps, ...blockedAsProposed].sort((a, b) => (a.step ?? 99) - (b.step ?? 99));
  }, [plan, blocked]);

  const canDecide = Boolean(runId) && plan.length > 0;

  const handleDecision = async (step, command, decision) => {
    if (!runId) return;
    const payload = {
      decision,
      operator_id: operatorId.trim() || "unidentified-operator",
      rationale: rationale.trim() || `Operator ${decision.toLowerCase()} step ${step} (${command})`,
      step_number: step,
      command,
    };
    const result = await recordDecision(runId, payload);
    setLastDecision({ step, command, decision, ok: Boolean(result) });
  };

  const hasOutput = Boolean(analysis?.output);
  const safetyMeta = SAFETY_BADGE[safetyStatus] || { status: "NOT_VALIDATED", label: "NOT VALIDATED" };

  return (
    <div className="view-stack">
      <div className="view-heading">
        <h1 className="view-heading__title">Recovery</h1>
        <p className="view-heading__sub">
          Proposed recovery actions, the deterministic safety verdict, and the
          operator approval step. Nothing is dispatched by SENTINEL itself.
        </p>
      </div>

      <Panel id="rv-context" title="Analysis context">
        <dl className="value-grid value-grid--4col">
          <ValueCell label="Run ID" value={runId || null} monospace placeholder="NOT AVAILABLE" />
          <ValueCell label="Safety status" value={safetyStatus || null} monospace placeholder="NOT AVAILABLE" />
          <ValueCell
            label="Human review required"
            value={
              analysis?.output?.requires_human_review !== undefined
                ? String(analysis.output.requires_human_review)
                : null
            }
          />
          <ValueCell
            label="Overall confidence"
            value={
              analysis?.output?.confidence !== undefined
                ? `${(analysis.output.confidence * 100).toFixed(1)}%`
                : null
            }
          />
          <ValueCell
            label="Analysis status"
            value={analysis?.output?.status || null}
            monospace
            placeholder="NOT AVAILABLE"
          />
          <ValueCell
            label="Scenario"
            value={selectedScenario ? `SCENARIO ${selectedScenario.scenario_id}` : null}
            monospace
          />
        </dl>
      </Panel>

      <div className="grid-3 recovery-stages">
        <Panel id="rv-llm" title="1. LLM / AI proposal" className="panel--proposal">
          {!hasOutput ? (
            <p className="muted-text">
              NOT AVAILABLE — no FDIR analysis output recorded. Run FDIR analysis
              to produce a recovery proposal.
            </p>
          ) : proposedSteps.length === 0 ? (
            <p className="muted-text">EMPTY PROPOSAL — no steps proposed by the model</p>
          ) : (
            <ol className="plan-list">
              {proposedSteps.map((s, i) => (
                <li key={`${s.step}-${i}`} className="plan-item plan-item--raw">
                  <span className="mono bold">
                    STEP {s.step ?? "-"}: {s.command}
                  </span>
                  <span className="mono fs-sm">RISK: {s.risk || "N/A"}</span>
                  <p className="fs-sm">{s.rationale || "N/A"}</p>
                  {s.violated_constraint ? (
                    <p className="mono fs-sm muted-text">
                      REFUSED — {s.violated_constraint}
                    </p>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </Panel>

        <Panel id="rv-safe" title="2. Safety validated action">
          <div className="panel-inline-status">
            <StatusBadge status={safetyMeta.status} label={safetyMeta.label} />
          </div>
          {!hasOutput ? (
            <p className="muted-text">
              NOT EVALUATED — no recovery plan has been validated yet.
            </p>
          ) : plan.length === 0 ? (
            <p className="muted-text">
              NO VALIDATED ACTIONS — every proposed step was blocked (or no plan
              survived validation). See the blocked commands panel below.
            </p>
          ) : (
            <ol className="plan-list">
              {plan.map((s) => (
                <li key={s.step} className="plan-item plan-item--validated">
                  <span className="mono bold">
                    STEP {s.step}: {s.command}
                  </span>
                  <span className="mono fs-sm">RISK: {s.risk}</span>
                  <p className="fs-sm">{s.rationale}</p>
                  <p className="mono fs-sm muted-text">
                    WAIT {s.wait_seconds}s — VERIFY: {s.verify}
                  </p>
                </li>
              ))}
            </ol>
          )}
        </Panel>

        <Panel id="rv-operator" title="3. Operator approval">
          {!canDecide ? (
            <div className="operator-blocked">
              <StatusBadge status={runId ? "PENDING" : "UNKNOWN"} label={runId ? "AWAITING RUN" : "NO RUN ID"} />
              <p className="muted-text">
                {!runId
                  ? "Operator decisions are recorded against an audit run. Run FDIR analysis to obtain a run id."
                  : "No validated steps are available to approve."}
              </p>
            </div>
          ) : (
            <div>
              <p className="muted-text fs-sm">
                Decisions are appended to the audit trail via
                POST /api/v1/runs/{runId}/decisions. The server stamps the actor
                OPERATOR, the sequence and the timestamp.
              </p>
              <div className="form-grid">
                <label className="field-label" htmlFor="rv-operator-id">
                  Operator ID
                </label>
                <input
                  id="rv-operator-id"
                  className="field-input"
                  value={operatorId}
                  onChange={(e) => setOperatorId(e.target.value)}
                  placeholder="e.g. FLT-DIR-01"
                />
                <label className="field-label" htmlFor="rv-rationale">
                  Rationale
                </label>
                <input
                  id="rv-rationale"
                  className="field-input"
                  value={rationale}
                  onChange={(e) => setRationale(e.target.value)}
                  placeholder="Required for every recorded decision"
                />
              </div>
              <ol className="plan-list">
                {plan.map((s) => (
                  <li key={s.step} className="plan-item plan-item--operator">
                    <div className="flex-between">
                      <span className="mono bold">
                        STEP {s.step}: {s.command}
                      </span>
                      <StatusBadge status={s.risk} label={`RISK ${s.risk}`} />
                    </div>
                    <div className="btn-row">
                      <button
                        type="button"
                        className="btn btn--sm btn--approve"
                        onClick={() => handleDecision(s.step, s.command, "APPROVED")}
                        disabled={decisionResult?.loading}
                      >
                        APPROVE &amp; RECORD
                      </button>
                      <button
                        type="button"
                        className="btn btn--sm btn--reject"
                        onClick={() => handleDecision(s.step, s.command, "REJECTED")}
                        disabled={decisionResult?.loading}
                      >
                        REJECT
                      </button>
                      <button
                        type="button"
                        className="btn btn--sm"
                        onClick={() => handleDecision(s.step, s.command, "DEFERRED")}
                        disabled={decisionResult?.loading}
                      >
                        DEFER
                      </button>
                    </div>
                  </li>
                ))}
              </ol>
              {lastDecision ? (
                <div className="decision-result" role="status">
                  {lastDecision.ok ? (
                    <>
                      <StatusBadge status="APPROVED" label="RECORDED" />
                      <span className="mono fs-sm">
                        {lastDecision.decision} on step {lastDecision.step} ({lastDecision.command})
                        — entry appended to run {runId}
                      </span>
                    </>
                  ) : (
                    <>
                      <StatusBadge status="FAILED" label="NOT RECORDED" />
                      <span className="mono fs-sm">{decisionResult?.error || "Backend refused the decision"}</span>
                    </>
                  )}
                </div>
              ) : null}
              {decisionResult?.error ? (
                <p className="error-text fs-sm">BACKEND ERROR: {decisionResult.error}</p>
              ) : null}
            </div>
          )}
        </Panel>
      </div>

      <BlockedCommandsPanel blockedSteps={blocked} />
    </div>
  );
}