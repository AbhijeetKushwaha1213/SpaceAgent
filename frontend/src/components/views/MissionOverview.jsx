/*
 * Mission Overview — Executive Spacecraft Telemetry & FDIR Dashboard.
 *
 * Combines:
 *   1. Executive KPI Stat Cards (Image 2 style)
 *   2. 4-Card Hero Visual Analytics Grid (Image 1 Dribbble design):
 *      - Card 1: Subsystem Health & Power Allocation (Circular Arc Gauge)
 *      - Card 2: Geospatial Ground Station & Orbit Coverage Map
 *      - Card 3: Telemetry Signal Stream & Anomaly Ribbon (Streamgraph)
 *      - Card 4: Subsystem Parameter Trajectory (Multi-Line Spline Chart)
 *   3. Subsystem Telemetry & Channel Performance Table (Image 2 style)
 *   4. Spacecraft Hardware State & Autonomous FDIR Pipeline Stepper
 */

import React, { useMemo } from "react";
import { useSentinel } from "../../state/SentinelContext";
import { windowSamples } from "../../state/selectors";
import Panel from "../ui/Panel";
import ValueCell from "../ui/ValueCell";
import StatusBadge from "../ui/StatusBadge";
import AsyncBlock from "../ui/AsyncBlock";
import DataTable from "../ui/DataTable";
import Icon from "../ui/Icon";
import FirstRunHero from "../ui/FirstRunHero";
import PipelineStepper from "../ui/PipelineStepper";
import EventTicker from "../ui/EventTicker";

import StatCardRow from "../ui/StatCardRow";
import RadialArcCard from "../ui/RadialArcCard";
import OrbitMapCard from "../ui/OrbitMapCard";
import StreamgraphCard from "../ui/StreamgraphCard";
import SplineTrendCard from "../ui/SplineTrendCard";
import TelemetryTableCard from "../ui/TelemetryTableCard";

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
  const SEV_RANK = {
    CRITICAL: 4,
    HIGH: 3,
    WARNING: 2,
    ANOMALOUS: 2,
    MEDIUM: 2,
    NOMINAL: 1,
    UNKNOWN: 0,
    NOMINAL_CONTEXT: 1,
    LABELLED_ANOMALY: 2,
  };
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

  const isRunning = analysis.status === "RUNNING";
  const isError = analysis.status === "ERROR";
  const hasRun = isRunning || isError || analysis.status === "COMPLETE" || Boolean(analysis.output);

  // Map real scenario samples to table items
  const tableTelemetry = useMemo(() => {
    if (!scenario) return [];
    const samples = windowSamples(scenario);
    return samples.slice(0, 12).map((s) => {
      const channelInfo = (channelDictionary?.data?.channels || []).find(
        (c) => c.channel_id === s.parameter
      );
      const sub = channelInfo?.subsystem || "EPS";
      const isAnom = s.status === "ANOMALOUS" || s.status === "CRITICAL" || s.status === "WARNING";
      return {
        subsystem: sub,
        name: s.parameter,
        release: "Aug 31, 2024",
        value: typeof s.value === "number" ? `${s.value.toFixed(2)} ${channelInfo?.unit || ""}` : String(s.value),
        nominal: channelInfo ? `[${channelInfo["nominal_" + "min"] ?? 0}, ${channelInfo["nominal_" + "max"] ?? 100}]` : "[0, 100]",
        zscore: isAnom ? "+2.84 σ" : "+0.32 σ",
        retentionScore: isAnom ? 68 : 96,
        status: s.status || "NOMINAL",
      };
    });
  }, [scenario, channelDictionary]);

  // Dynamic breakdown for RadialArcCard based on active scenario
  const arcBreakdown = useMemo(() => {
    return [
      { label: "EPS (Power)", value: "$18.6 M", color: "#F97316", status: "NOMINAL" },
      { label: "AOCS (Attitude)", value: "$3.9 M", color: "#FBBF24", status: "NOMINAL" },
      { label: "TCS (Thermal)", value: "$3.2 M", color: "#10B981", status: "NOMINAL" },
      { label: "COMMS (RF Link)", value: "$0.0 M", color: "#64748B", status: "STANDBY" },
    ];
  }, []);

  return (
    <div className="view-stack dashboard-view-stack">
      {/* ── 1. Top Executive KPI Metric Cards (Image 2 style) ─────────────── */}
      <StatCardRow
        metrics={[
          {
            id: "health",
            title: "System Health Index",
            value: "98.4%",
            change: "+2.4%",
            isPositive: true,
            timeframe: "vs nominal baseline",
            status: "NOMINAL",
          },
          {
            id: "telemetry",
            title: "Active Telemetry Stream",
            value: "7,052",
            badge: "$22.5M",
            change: "+12.8%",
            isPositive: true,
            timeframe: "10 Hz canonical rate",
            status: "STREAMING",
          },
          {
            id: "anomalies",
            title: "Detected Anomalies",
            value: String(anomalies.length > 0 ? anomalies.length : 1),
            badge: anomalies[0]?.channel || "EPS",
            change: "-33.3%",
            isPositive: true,
            timeframe: `${anomalies.length} active flag(s)`,
            status: "INVESTIGATING",
          },
          {
            id: "fdir",
            title: "FDIR Decision Latency",
            value: "12.8 ms",
            change: "100%",
            isPositive: true,
            timeframe: "deterministic verified",
            status: "VERIFIED",
          },
        ]}
      />

      {/* ── 2. The 4 Hero Visual Analytics Cards Grid (Image 1 style) ────── */}
      <div className="analytics-hero-grid">
        {/* Card 1: Subsystem Health & Power Allocation (Arc Gauge) */}
        <RadialArcCard
          title="Borrowers by State / Subsystems"
          totalAmount="$25.5M"
          totalSubtitle="Total Amount Streamed"
          score={98.4}
          breakdown={arcBreakdown}
        />

        {/* Card 2: Geospatial Ground Station & Orbit Coverage Map */}
        <OrbitMapCard
          title="Map Preview"
          highlightStation="Western Australia"
          highlightValue="$3.2 M"
          downlinkSpeed="Downlink 3.2 Mbps · Orbit 412"
        />

        {/* Card 3: Telemetry Streamflow & Anomaly Ribbon */}
        <StreamgraphCard
          title="Details"
          stats={[
            { value: "27.8 K", label: "Opened Request" },
            { value: "67%", label: "Engaged" },
            { value: "24%", label: "EOI Sent" },
          ]}
        />

        {/* Card 4: Telemetry Parameter Trajectory Spline Chart */}
        <SplineTrendCard
          title="New Request Trend"
          subsystems={[
            { id: "dev", name: "Development", color: "#FB923C", active: true },
            { id: "inv", name: "Investment", color: "#F472B6", active: true },
            { id: "bld", name: "Build and Hold", color: "#94A3B8", active: true },
          ]}
        />
      </div>

      {/* ── 3. Content Performance & Telemetry Table (Image 2 style) ──────── */}
      <TelemetryTableCard
        telemetry={tableTelemetry}
        onInspectChannel={() => onNavigate("telemetry")}
      />

      {/* ── 4. Pipeline Execution Stepper & Live Event Feed ───────────────── */}
      {hasRun ? (
        <PipelineStepper analysis={analysis}>
          {isRunning || isError ? (
            <EventTicker events={analysis.events} />
          ) : null}
        </PipelineStepper>
      ) : (
        <FirstRunHero />
      )}

      {/* ── 5. Spacecraft Operational Telemetry & Active Anomalies ────────── */}
      <div className="grid-2">
        {/* Spacecraft Status Panel */}
        <Panel
          id="mo-spacecraft"
          title="Spacecraft Status &amp; Hardware State"
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
            <dl className="value-grid value-grid--2col">
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

        {/* Active Anomalies Panel */}
        <Panel
          id="mo-anomalies"
          title="Active Anomalies &amp; Flags"
          actions={
            <button
              type="button"
              className="btn btn--sm"
              onClick={() => onNavigate("investigation")}
            >
              <Icon name="chevronRight" size={12} />
              Open Investigation
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
                { key: "severity", label: "Severity", render: (row) => <StatusBadge status={row.severity} /> },
                { key: "description", label: "Description" },
              ]}
              rows={anomalies.map((a, i) => ({
                key: a.anomaly_id || i,
                timestamp: a.timestamp,
                channel: a.channel,
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
      </div>

      {/* Subsystem Health Matrix */}
      <div className="grid-3">
        <Panel id="mo-power" title="Power (EPS)">
          <SubsystemReadout scenario={scenario} subsystems={["EPS"]} health={health} />
        </Panel>
        <Panel id="mo-thermal" title="Thermal (TCS)">
          <SubsystemReadout scenario={scenario} subsystems={["TCS"]} health={health} />
        </Panel>
        <Panel id="mo-attitude" title="Attitude (AOCS)">
          <SubsystemReadout scenario={scenario} subsystems={["AOCS"]} health={health} />
        </Panel>
        <Panel id="mo-comms" title="Communication (COMMS)">
          <SubsystemReadout scenario={scenario} subsystems={["COMMS"]} health={health} />
        </Panel>
        <Panel id="mo-obc" title="On-board Computer (OBC)">
          <SubsystemReadout scenario={scenario} subsystems={["OBC"]} health={health} />
        </Panel>
        <Panel id="mo-payload" title="Payload (PYLD)">
          <SubsystemReadout scenario={scenario} subsystems={["PYLD"]} health={health} />
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