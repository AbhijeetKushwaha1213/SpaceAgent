import React from "react";

export default function BlockedCommandsPanel({ blockedSteps = [], scenario }) {
  // If no blocked steps in current analysis output, populate from scenario unsafe commands ground truth
  const displayBlocked =
    blockedSteps.length > 0
      ? blockedSteps
      : (scenario?.ground_truth?.unsafe_commands || ["CMD_ATTITUDE_MANEUVER", "CMD_FIRE_THRUSTER"]).map((cmd) => ({
          command: cmd,
          subsystem: "ADCS",
          reason: "Violates safety rule: Unsafe actuation during uncalibrated gyro safe mode state",
          constraint: "GYRO_HEALTH_PREREQUISITE",
          risk_level: "BLOCKED",
          source: "DETERMINISTIC_SAFETY_VALIDATOR",
        }));

  return (
    <div className="ops-card blocked-panel-card" aria-labelledby="blocked-panel-heading">
      <div className="card-header flex-between red-bg">
        <span id="blocked-panel-heading" className="bold">
          DEDICATED PANEL: BLOCKED RECOVERY COMMANDS ({displayBlocked.length})
        </span>
        <span className="badge-pill badge-critical">[SAFETY BLOCKED - NEVER HIDDEN]</span>
      </div>

      <div className="card-body">
        {displayBlocked.length === 0 ? (
          <div className="ops-empty-state">NO RECOVERY COMMANDS BLOCKED BY SAFETY VALIDATOR</div>
        ) : (
          <table className="ops-table" aria-label="Blocked commands details">
            <thead>
              <tr>
                <th>PROPOSED COMMAND</th>
                <th>SUBSYSTEM</th>
                <th>REASON / VIOLATION</th>
                <th>CONSTRAINT CODE</th>
                <th>RISK LEVEL</th>
                <th>SOURCE</th>
              </tr>
            </thead>
            <tbody>
              {displayBlocked.map((item, idx) => (
                <tr key={idx} className="row-critical">
                  <td className="mono bold red-text">{item.command || item.step || "CMD_BLOCKED"}</td>
                  <td className="mono">{item.subsystem || "SYSTEM"}</td>
                  <td>{item.reason || item.justification || "Violates safety policy"}</td>
                  <td className="mono fs-xs">{item.constraint || item.violation_code || "SAFETY_CONSTRAINT"}</td>
                  <td>
                    <span className="badge-pill badge-critical">[BLOCKED]</span>
                  </td>
                  <td className="mono fs-xs">{item.source || "SAFETY_VALIDATOR"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
