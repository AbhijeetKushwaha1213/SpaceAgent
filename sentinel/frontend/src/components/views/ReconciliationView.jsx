/*
 * Observation Reconciliation / Separation View (ReconciliationView.jsx)
 *
 * Phase 24 — Deterministic multi-channel observation reconciliation.
 *
 * Core Principle: CORRELATION != IDENTITY.
 * Displays isolated Cases and inter-case CaseRelationships with deterministic
 * signal outcomes and human review indicators.
 */

import React, { useState, useEffect } from "react";
import { useSentinel } from "../../state/SentinelContext";
import Panel from "../ui/Panel";
import StatusBadge from "../ui/StatusBadge";
import DataTable from "../ui/DataTable";

export default function ReconciliationView() {
  const { selectedScenario, auditRun } = useSentinel();
  const [reconData, setReconData] = useState(null);
  const [loading, setLoading] = useState(false);

  // Extract reconciliation stage from auditRun if available
  useEffect(() => {
    if (auditRun?.entries) {
      const reconEntry = auditRun.entries.find((e) => e.stage === "reconciliation");
      if (reconEntry?.payload) {
        setReconData(reconEntry.payload);
        return;
      }
    }

    // Otherwise fetch from endpoint or scenario default
    if (selectedScenario?.id) {
      setLoading(true);
      fetch(`/api/v1/scenarios/${selectedScenario.id}`)
        .then((res) => (res.ok ? res.json() : null))
        .then((data) => {
          // If scenario has local reconciliation data or mock state
          setLoading(false);
        })
        .catch(() => setLoading(false));
    }
  }, [selectedScenario, auditRun]);

  const cases = reconData?.cases || [];
  const relationships = reconData?.relationships || [];
  const humanReviewRequired = reconData?.human_review_required ?? false;

  const relationshipCounts = {
    DUPLICATE: 0,
    SAME_CASE: 0,
    RELATED: 0,
    SEPARATE: 0,
    CONFLICT: 0,
    UNCERTAIN: 0,
  };

  for (const r of relationships) {
    if (relationshipCounts[r.relationship_type] !== undefined) {
      relationshipCounts[r.relationship_type] += 1;
    }
  }

  const caseColumns = [
    { key: "case_id", header: "Case ID" },
    {
      key: "subsystems",
      header: "Subsystems",
      render: (c) => (Array.isArray(c.subsystems) ? c.subsystems.join(", ") : c.subsystems || "—"),
    },
    {
      key: "channels",
      header: "Channels",
      render: (c) => (Array.isArray(c.channels) ? c.channels.join(", ") : c.channels || "—"),
    },
    {
      key: "window",
      header: "Onset Window",
      render: (c) =>
        c.window_start_s != null ? `T${c.window_start_s >= 0 ? "+" : ""}${c.window_start_s.toFixed(1)}s` : "—",
    },
    {
      key: "event_ids",
      header: "Member Events",
      render: (c) => (c.event_ids ? c.event_ids.length : 1),
    },
  ];

  const relColumns = [
    { key: "relationship_id", header: "Relationship ID" },
    {
      key: "pair",
      header: "Cases",
      render: (r) => `${r.source_case_id} ↔ ${r.target_case_id}`,
    },
    {
      key: "relationship_type",
      header: "Type",
      render: (r) => <StatusBadge status={r.relationship_type} />,
    },
    {
      key: "propagation_source",
      header: "Propagation Root",
      render: (r) => r.propagation_source_case_id || "None / N/A",
    },
    {
      key: "confidence",
      header: "Deterministic Confidence",
      render: (r) => `${((r.confidence || 0) * 100).toFixed(0)}%`,
    },
  ];

  return (
    <div className="view-stack">
      <div className="view-heading">
        <h1 className="view-heading__title">Observation Reconciliation / Separation</h1>
        <p className="view-heading__sub">
          Deterministic case partitioning across 8 independent signal families.
          Upholds <strong>CORRELATION ≠ IDENTITY</strong>: preserves separation whenever root-cause
          coincidence cannot be deterministically proved.
        </p>
      </div>

      <div className="grid-4">
        <Panel id="recon-cases" title="Isolated Cases">
          <div className="stat-number">{cases.length || 0}</div>
          <p className="muted-text fs-sm">Disjoint fault investigation scopes</p>
        </Panel>

        <Panel id="recon-rel" title="Related Cases">
          <div className="stat-number">{relationshipCounts.RELATED}</div>
          <StatusBadge status="RELATED" />
          <p className="muted-text fs-sm">Physical propagation links</p>
        </Panel>

        <Panel id="recon-conflicts" title="Conflicts Detected">
          <div className="stat-number">{relationshipCounts.CONFLICT}</div>
          <StatusBadge status={relationshipCounts.CONFLICT > 0 ? "CONFLICT" : "NOMINAL"} />
          <p className="muted-text fs-sm">Contradictions preserved for review</p>
        </Panel>

        <Panel id="recon-review" title="Review Authority">
          <div className="stat-number" style={{ fontSize: "1.2rem", marginTop: "0.5rem" }}>
            {humanReviewRequired ? "HUMAN REVIEW REQUIRED" : "AUTOMATED"}
          </div>
          <StatusBadge status={humanReviewRequired ? "CRITICAL" : "OK"} />
          <p className="muted-text fs-sm">Monotone review requirement gate</p>
        </Panel>
      </div>

      <Panel id="recon-case-list" title="Reconciled Cases">
        {cases.length === 0 ? (
          <p className="muted-text">
            No active reconciliation data available. Load a scenario and run diagnosis stream to view cases.
          </p>
        ) : (
          <DataTable columns={caseColumns} data={cases} keyField="case_id" />
        )}
      </Panel>

      <Panel id="recon-rel-list" title="Case Relationships & Propagation Graph">
        {relationships.length === 0 ? (
          <p className="muted-text">No cross-case relationships established.</p>
        ) : (
          <DataTable columns={relColumns} data={relationships} keyField="relationship_id" />
        )}
      </Panel>
    </div>
  );
}
