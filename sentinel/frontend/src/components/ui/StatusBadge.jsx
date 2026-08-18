/*
 * Status badge. Status is NEVER conveyed by colour alone: every badge pairs
 * a glyph with a text label, and the colour is a redundant cue.
 */

import React from "react";
import Icon from "./Icon";

const STATUS_META = {
  // positive / ok
  OK: { icon: "check", cls: "ok" },
  VALID: { icon: "check", cls: "ok" },
  VALIDATED: { icon: "check", cls: "ok" },
  COMPLETED: { icon: "check", cls: "ok" },
  NOMINAL: { icon: "check", cls: "ok" },
  CONSISTENT: { icon: "check", cls: "ok" },
  PASS: { icon: "check", cls: "ok" },
  APPROVED: { icon: "check", cls: "ok" },
  EXECUTED: { icon: "check", cls: "ok" },
  ACKNOWLEDGED: { icon: "check", cls: "ok" },
  LOW: { icon: "check", cls: "ok" },
  ACCEPTED: { icon: "check", cls: "ok" },
  // warning / degraded
  WARNING: { icon: "warn", cls: "warn" },
  ANOMALOUS: { icon: "warn", cls: "warn" },
  LABELLED_ANOMALY: { icon: "warn", cls: "warn" },
  UNCERTAIN: { icon: "warn", cls: "warn" },
  DEGRADED: { icon: "warn", cls: "warn" },
  PARTIALLY_BLOCKED: { icon: "warn", cls: "warn" },
  REQUIRES_HUMAN_REVIEW: { icon: "warn", cls: "warn" },
  MEDIUM: { icon: "warn", cls: "warn" },
  PENDING: { icon: "clock", cls: "warn" },
  IN_PROGRESS: { icon: "clock", cls: "warn" },
  RUNNING: { icon: "clock", cls: "warn" },
  NOT_VALIDATED: { icon: "unknown", cls: "warn" },
  HIGH: { icon: "warn", cls: "warn" },
  MODIFIED: { icon: "warn", cls: "warn" },
  DEFERRED: { icon: "clock", cls: "warn" },
  SKIPPED: { icon: "unknown", cls: "warn" },
  NOT_RUN: { icon: "unknown", cls: "warn" },
  // critical / failure / blocked
  CRITICAL: { icon: "cross", cls: "crit" },
  INVALID: { icon: "cross", cls: "crit" },
  INCONSISTENT: { icon: "cross", cls: "crit" },
  FAILED: { icon: "cross", cls: "crit" },
  ERROR: { icon: "cross", cls: "crit" },
  BLOCKED: { icon: "block", cls: "crit" },
  REJECTED: { icon: "cross", cls: "crit" },
  ABANDONED: { icon: "cross", cls: "crit" },
  // neutral / not stated
  UNKNOWN: { icon: "unknown", cls: "neutral" },
  INFO: { icon: "unknown", cls: "neutral" },
  NOT_IMPLEMENTED: { icon: "unknown", cls: "neutral" },
  NOMINAL_CONTEXT: { icon: "unknown", cls: "neutral" },
  IDLE: { icon: "unknown", cls: "neutral" },
  UNKNOWN_MARK: { icon: "unknown", cls: "neutral" },
};

export default function StatusBadge({ status, label, title }) {
  const key = String(status || "UNKNOWN").toUpperCase();
  const meta = STATUS_META[key] || STATUS_META.UNKNOWN;
  const text = label || key;
  return (
    <span
      className={`status-badge status-badge--${meta.cls}`}
      title={title || `${text} (also indicated by the glyph, not colour alone)`}
    >
      <Icon name={meta.icon} size={11} />
      <span className="status-badge__label">{text}</span>
    </span>
  );
}