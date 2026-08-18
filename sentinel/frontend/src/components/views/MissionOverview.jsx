/*
 * Mission Overview — real backend state only.
 *
 * Sources:
 *   - spacecraft identity/status: GET /api/v1/scenarios (selected scenario)
 *   - subsystem health: telemetry statuses in the canonical window, grouped
 *     by the channel dictionary's subsystem attribution
 *   - active anomalies: POST /api/v1/detect
 *   - FDIR + AI state: GET /api/v1/system/status, GET /api/v1/audit/status
 */

import React from "react";
import { useSentinel } from "../../state/SentinelContext";
import { windowSamples } from "../../state/selectors";
import Panel from "../ui/Panel";
import ValueCell from "../ui/ValueCell";
import StatusBadge from "../ui/StatusBadge";
import AsyncBlock from "../ui/AsyncBlock";
import DataTable from "../ui/DataTable";
import Icon from "../ui/Icon";

const SUBSYSTEM_ORDER = ["EPS", "AOCS", "OBC", "TCS", "COMMS", "PYLD", "UNKNOWN"];

function subsystemHealth(scenario, channelDictionary) {
  const samples = windowSamples(scenario);
  const bySub = {};
  for (const sample of samples) {
    const sub = channelDictionary
      ? (
          (channelDictionary.data?.channels || []).find(
            (c) => c.channel_id === sample.parameter
          )?.subsystem || "UNKNOWN"
        )
      : "UNKNOWN";
    bySub[sub] = bySub[sub] || { samples: [] };
    bySub[sub].samples.push(sample);
  }
  const SEV_RANK = { CRITICAL: 4, HIGH: 3, WARNING: 2, ANOMALOUS: 2, MEDIUM: 2, NOMINAL: 1, UNKNOWN: 0, NOMINAL_CONTEXT: 1, LABELLED_ANOMALY: 2 };
  const out = [];
  for (const [sub, group] of Object.entries(bySub)) {
    let worst = "NOMINAL";
    let worstRank = SEV_RANK.NOMINAL;
    let criticalCount = 0;
    for (const s of group.samples) {
      const st = String(s.status || "UNKNOWN").toUpperCase();
      const rank = SEV_RANK[st] ?? SEV_RANK.UNKNOWN;
      if (rank > worstRank) {
        worstRank = rank;
        worst = st;
      }
      if (st === "CRITICAL") criticalCount += 1;
    }
    const subName = sub === "UNKNOWN" ? "UNKNOWN / UNATTRIBUTED" : sub;
    out.push({
      subsystem: sub,
      displayName: subName,
      status: worst,
      criticalCount,
      channelCount: group.samples.length,
      channels: Array.from(new Set(group.samples.map((s) => s.parameter))).join(", "),
    });
  }
  return out.sort(
    (a, b) =>
      SUBSYSTEM_ORDER.indexOf(a.subsystem) - SUBSYSTEM_ORDER.indexOf(b.subsystem)
  );
}

export default function MissionOverview({ onNavigate }) {
  const {
    selectedScenario: scenario,
    systemStatus,
    detection,
    auditStatus,
    analysis,
    channelDictionary,
  } = useSentinel();

  const anomalies = detection?.data?.anomalies || [];
  const health = scenario ? subsystemHealth(scenario, channelDictionary) : [];
  
  const llmMode = systemStatus?.data?.llm_mode || null;
  const fdirStage = auditStatus?.data || null;

  const analysisStatusLabel =
    analysis.status === "RUNNING"
      ? "RUNNING"
      : analysis.status === "COMPLETE"
      ? "COMPLETE"
      : analysis.status === "ERROR"
      ? "ERROR"
      : analysis.output
      ? "COMPLETE"
      : "NOT RUN";

  return (
    <div className="view-stack">
      <div className="view-heading">
        <h1 className="view-heading__title">Mission Overview</h1>
        <p className="view-heading__sub">
          Current spacecraft, telemetry and FDIR pipeline state. Every value is
          served by the SENTINEL backend; absent data renders as N/A.
        </p>
      </div>

      <Panel
        id="mo-spacecraft"
        title="Spacecraft status"
        actions={
          scenario ? (
            <StatusBadge
              status={scenario.provenance}
              label={scenario.source_type || scenario.provenance}
            />
          ) : null
        }
      >
        <AsyncBlock entity={{ loading: !scenario, data: scenario ? {} : null, error: null }}>
          <dl className="value-grid">
            <ValueCell label="Scenario ID" value={scenario?.scenario_id} monospace />
            <ValueCell label="Incident ID" value={scenario?.incident_id} monospace />
            <ValueCell label="Fault class" value={scenario?.fault_type} />
            <ValueCell label="Fault register" value={scenario?.fault_register} monospace />
            <ValueCell label="Safe mode trigger" value={scenario?.safe_mode_trigger} />
            <ValueCell label="Source note" value={scenario?.source_note} placeholder="NOT AVAILABLE" />
            <ValueCell
              label="Telecommand context"
              value={scenario?.telecommand_context ? `${scenario.telecommand_context.telecommand} (${scenario.telecommand_context.gap_classification})` : null}
            />
            <ValueCell
              label="Hardware state"
              value={scenario?.hardware_state ? JSON.stringify(scenario.hardware_state) : null}
              monospace
            />
          </dl>
        </AsyncBlock>
      </Panel>

      <div className="grid-3">
        <Panel id="mo-power" title="Power">
          <SubsystemReadout
            scenario={scenario}
            subsystems={["EPS"]}
            health={health}
          />
        </Panel>
        <Panel id="mo-thermal" title="Thermal">
          <SubsystemReadout
            scenario={scenario}
            subsystems={["TCS"]}
            health={health}
          />
        </Panel>
        <Panel id="mo-attitude" title="Attitude">
          <SubsystemReadout
            scenario={scenario}
            subsystems={["AOCS"]}
            health={health}
          />
        </Panel>
        <Panel id="mo-comms" title="Communication">
          <SubsystemReadout
            scenario={scenario}
            subsystems={["COMMS"]}
            health={health}
          />
        </Panel>
        <Panel id="mo-obc" title="On-board computer">
          <SubsystemReadout
            scenario={scenario}
            subsystems={["OBC"]}
            health={health}
          />
        </Panel>
        <Panel id="mo-payload" title="Payload">
          <SubsystemReadout
            scenario={scenario}
            subsystems={["PYLD"]}
            health={health}
          />
        </Panel>
      </div>

      <Panel
        id="mo-anomalies"
        title="Active anomalies"
        actions={
          <button
            type="button"
            className="btn btn--sm"
            onClick={() => onNavigate("investigation")}
          >
            <Icon name="chevronRight" size={12} />
            Open fault investigation
          </button>
        }
      >
        <AsyncBlock entity={detection}>
          <DataTable
            caption="Anomalies detected by the SENTINEL deterministic pipeline"
            emptyMessage="NO ANOMALIES DETECTED"
            columns={[
              { key: "timestamp", label: "Timestamp" },
              { key: "channel", label: "Channel" },
              { key: "detector", label: "Detector" },
              { key: "score", label: "Score" },
              { key: "threshold", label: "Threshold" },
              { key: "severity", label: "Severity", render: (row) => <StatusBadge status={row.severity} /> },
              { key: "description", label: "Description" },
            ]}
            rows={anomalies.map((a, i) => ({
              key: a.anomaly_id || i,
              timestamp: a.timestamp,
              channel: a.channel,
              detector: a.detector,
              score: a.score !== null && a.score !== undefined ? a.score : "N/A",
              threshold: a.threshold !== null && a.threshold !== undefined ? a.threshold : "N/A",
              severity: a.severity,
              description: a.description,
            }))}
            rowClass={(row) =>
              row.severity === "CRITICAL"
                ? "row--critical"
                : row.severity === "HIGH" || row.severity === "MEDIUM"
                ? "row--warning"
                : ""
            }
          />
        </AsyncBlock>
      </Panel>

      <div className="grid-2">
        <Panel id="mo-fdir" title="FDIR pipeline state">
          <dl className="value-grid value-grid--2col">
            <ValueCell
              label="Analysis status"
              value={analysisStatusLabel}
              monospace
            />
            <ValueCell
              label="Audit runs recorded"
              value={fdirStage ? fdirStage.run_count : null}
              monospace
            />
            <ValueCell
              label="Audit store"
              value={fdirStage ? fdirStage.backend : null}
              monospace
            />
            <ValueCell
              label="Audit append-only"
              value={fdirStage ? String(fdirStage.append_only) : null}
              monospace
            />
            <ValueCell
              label="Stages not implemented"
              value={
                fdirStage && fdirStage.not_implemented_stages
                  ? (fdirStage.not_implemented_stages.length
                      ? fdirStage.not_implemented_stages.join(", ")
                      : "NONE")
                  : null
              }
            />
            <ValueCell
              label="Detector status"
              value={systemStatus?.data?.detector_status || null}
              monospace
            />
            <ValueCell
              label="Physics model status"
              value={systemStatus?.data?.physics_model_status || null}
              monospace
            />
            <ValueCell
              label="RAG status"
              value={systemStatus?.data?.rag_status || null}
              monospace
            />
          </dl>
        </Panel>

        <Panel id="mo-ai" title="AI engine">
          <AsyncBlock entity={systemStatus}>
            <dl className="value-grid">
              <ValueCell label="LLM mode" value={llmMode} monospace />
              <ValueCell label="Provider" value={systemStatus?.data?.llm_provider || null} monospace />
              <ValueCell label="Model" value={systemStatus?.data?.model || null} monospace />
              <ValueCell
                label="Simulation / live state"
                value={systemStatus?.data?.simulation_live_status || null}
                monospace
              />
              <ValueCell
                label="Local execution"
                value={
                  systemStatus?.data?.sovereignty
                    ? String(systemStatus.data.sovereignty.local_execution)
                    : null
                }
              />
              <ValueCell
                label="Cloud telemetry disabled"
                value={
                  systemStatus?.data?.sovereignty
                    ? String(systemStatus.data.sovereignty.cloud_telemetry_disabled)
                    : null
                }
              />
              <ValueCell
                label="Sovereignty disclaimer"
                value={systemStatus?.data?.sovereignty?.disclaimer || null}
                placeholder="NOT AVAILABLE"
              />
            </dl>
          </AsyncBlock>
        </Panel>
      </div>
    </div>
  );
}

function SubsystemReadout({ scenario, subsystems, health }) {
  const entries = health.filter((h) => subsystems.includes(h.subsystem));
  if (!scenario || entries.length === 0) {
    return (
      <p className="muted-text">
        NO TELEMETRY IN WINDOW FOR THIS SUBSYSTEM — status unavailable
      </p>
    );
  }
  return (
    <div className="subsystem-readout">
      {entries.map((entry) => (
        <div key={entry.subsystem} className="subsystem-row">
          <div className="subsystem-row__head">
            <StatusBadge status={entry.status} />
            <span className="mono muted-text">{entry.channelCount} SAMPLE(S)</span>
          </div>
          <p className="mono fs-sm">{entry.channels}</p>
        </div>
      ))}
    </div>
  );
}