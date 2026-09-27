/*
 * Blocked commands — dedicated, always-visible panel. Blocked commands are
 * never hidden and never collapsed away: the operator sees what was
 * proposed, why it was refused, the constraint violated, the severity and
 * the source of the refusal. Data comes from SentinelOutput.blocked_steps.
 */

import React from "react";
import StatusBadge from "./ui/StatusBadge";
import DataTable from "./ui/DataTable";

export default function BlockedCommandsPanel({ blockedSteps = [] }) {
  return (
    <section className="panel panel--blocked" aria-labelledby="blocked-heading">
      <header className="panel__header">
        <h2 id="blocked-heading" className="panel__title">Blocked commands</h2>
        <StatusBadge status="BLOCKED" label={`${blockedSteps.length} BLOCKED`} />
      </header>
      <div className="panel__body">
        <p className="muted-text fs-sm">
          Commands refused by the deterministic safety validator. These actions
          were proposed but must not be uplinked. They remain visible here; a
          blocked command is never hidden from the operator.
        </p>
        <DataTable
          caption="Safety-blocked recovery commands"
          emptyMessage="NO COMMANDS BLOCKED BY THE SAFETY VALIDATOR"
          columns={[
            { key: "command", label: "Command", render: (r) => <span className="mono bold">{r.command}</span> },
            { key: "subsystem", label: "Subsystem", render: (r) => <span className="mono">{r.subsystem || "N/A"}</span> },
            { key: "reason", label: "Reason" },
            { key: "constraint", label: "Constraint", render: (r) => <span className="mono">{r.constraint}</span> },
            { key: "severity", label: "Severity", render: (r) => <StatusBadge status={r.severity} /> },
            { key: "source", label: "Source", render: (r) => <span className="mono">{r.source}</span> },
          ]}
          rows={blockedSteps.map((b, i) => ({
            key: `${b.command}-${i}`,
            command: b.command,
            subsystem: b.subsystem,
            reason: b.reason,
            constraint: b.violated_constraint || "N/A",
            severity: b.severity || "UNKNOWN",
            source: "SAFETY VALIDATOR (POST /api/v1/analyze)",
          }))}
          rowClass={() => "row--critical"}
        />
      </div>
    </section>
  );
}