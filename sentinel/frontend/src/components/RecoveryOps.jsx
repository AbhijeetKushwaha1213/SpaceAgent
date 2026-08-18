import React, { useState } from "react";
import BlockedCommandsPanel from "./BlockedCommandsPanel";

export default function RecoveryOps({ scenario, analysisOutput, onApproveStep }) {
  const [operatorDecisions, setOperatorDecisions] = useState({});

  const recoveryPlan = analysisOutput?.recovery_plan || [
    { step_number: 1, command: "CMD_GYRO_A_RESET", subsystem: "ADCS", rationale: "Reset primary gyro registers.", risk_level: "LOW" },
    { step_number: 2, command: "CMD_ATTITUDE_HOLD", subsystem: "ADCS", rationale: "Maintain sun-pointing attitude mode.", risk_level: "LOW" },
  ];

  const blockedSteps = analysisOutput?.blocked_steps || [];
  const safetyStatus = analysisOutput?.safety_status || "VALIDATED";

  const handleDecision = (stepNum, decision) => {
    setOperatorDecisions((prev) => ({
      ...prev,
      [stepNum]: decision,
    }));
    if (onApproveStep) {
      onApproveStep(stepNum, decision);
    }
  };

  return (
    <section className="ops-view-container" aria-labelledby="recovery-heading">
      <div className="view-title-bar">
        <h1 id="recovery-heading" className="view-title">
          RECOVERY PROCEDURE DISPATCH &amp; SAFETY VALIDATION CONSOLE
        </h1>
        <div className="view-actions">
          <span className="info-chip">SAFETY STATUS: {safetyStatus}</span>
          <span className="info-chip">RECOVERY STEPS: {recoveryPlan.length}</span>
        </div>
      </div>

      {/* 3-Stage Pipeline Column Grid */}
      <div className="ops-grid grid-3">
        {/* Stage 1: LLM Proposal */}
        <div className="ops-card">
          <div className="card-header flex-between">
            <span>STAGE 1: LLM / AI PROPOSAL</span>
            <span className="badge-pill badge-warning">[RAW PROPOSAL]</span>
          </div>
          <div className="card-body">
            <p className="ops-text-dim mb-10">
              Unvalidated sequence proposed by ranking engine. Must pass deterministic safety filter.
            </p>
            <ul className="pipeline-list">
              {recoveryPlan.map((step) => (
                <li key={step.step_number} className="pipeline-item">
                  <span className="mono bold">STEP {step.step_number}:</span> {step.command} ({step.subsystem})
                </li>
              ))}
            </ul>
          </div>
        </div>

        {/* Stage 2: Safety Validated Action */}
        <div className="ops-card">
          <div className="card-header flex-between">
            <span>STAGE 2: SAFETY VALIDATED ACTION</span>
            <span className={`badge-pill badge-${safetyStatus === "VALIDATED" ? "nominal" : "critical"}`}>
              [{safetyStatus}]
            </span>
          </div>
          <div className="card-body">
            <p className="ops-text-dim mb-10">
              Filtered against hardware safety rules, thermal bounds, battery floor (15%), and comms lock constraints.
            </p>
            <ul className="pipeline-list">
              {recoveryPlan.map((step) => (
                <li key={step.step_number} className="pipeline-item green-border">
                  <span className="mono bold green-text">[PASSED] STEP {step.step_number}:</span> {step.command}
                  <span className="mono fs-xs block mt-2">RISK: {step.risk_level || "LOW"}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>

        {/* Stage 3: Operator Approval */}
        <div className="ops-card">
          <div className="card-header flex-between">
            <span>STAGE 3: OPERATOR APPROVAL</span>
            <span className="badge-pill badge-nominal">[HUMAN IN THE LOOP]</span>
          </div>
          <div className="card-body">
            <p className="ops-text-dim mb-10">
              Flight operator authorization required prior to telecommand uplink.
            </p>
            <div className="operator-actions-list">
              {recoveryPlan.map((step) => {
                const dec = operatorDecisions[step.step_number] || "PENDING";
                return (
                  <div key={step.step_number} className="operator-action-row">
                    <div className="flex-between">
                      <span className="mono bold">STEP {step.step_number}: {step.command}</span>
                      <span className={`badge-pill badge-${dec === "APPROVED" ? "nominal" : dec === "REJECTED" ? "critical" : "warning"}`}>
                        [{dec}]
                      </span>
                    </div>

                    <div className="btn-group mt-5">
                      <button
                        className="ops-btn ops-btn-xs ops-btn-success"
                        onClick={() => handleDecision(step.step_number, "APPROVED")}
                        disabled={dec === "APPROVED"}
                        aria-label={`Approve command ${step.command}`}
                      >
                        [APPROVE &amp; UPLINK]
                      </button>
                      <button
                        className="ops-btn ops-btn-xs ops-btn-danger"
                        onClick={() => handleDecision(step.step_number, "REJECTED")}
                        disabled={dec === "REJECTED"}
                        aria-label={`Reject command ${step.command}`}
                      >
                        [REJECT]
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {/* Dedicated Blocked Commands Panel */}
      <div className="section-block mt-20">
        <BlockedCommandsPanel blockedSteps={blockedSteps} scenario={scenario} />
      </div>
    </section>
  );
}
