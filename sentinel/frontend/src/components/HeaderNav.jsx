/*
 * Header + primary navigation.
 *
 * Every value displayed here comes from the backend:
 *   - simulation/live state, LLM mode, model, version: GET /api/v1/system/status
 *   - scenario catalogue: GET /api/v1/scenarios
 *   - run id: X-Sentinel-Run-Id header from POST /api/v1/analyze
 *
 * The tab list follows the ARIA tablist pattern with roving tabindex so the
 * nav is fully keyboard-operable.
 */

import React, { useRef } from "react";
import { useSentinel } from "../state/SentinelContext";
import { PROVENANCE_LABELS, normalizeProvenance } from "../generated/contract";
import Icon from "./ui/Icon";

export const NAV_TABS = [
  { id: "overview", label: "Mission Overview" },
  { id: "telemetry", label: "Telemetry" },
  { id: "investigation", label: "Fault Investigation" },
  { id: "physics", label: "Physics / State" },
  { id: "recovery", label: "Recovery" },
  { id: "evidence", label: "Evidence" },
  { id: "audit", label: "Audit" },
  { id: "evaluation", label: "Evaluation" },
];

function provenanceLabel(scenario) {
  if (!scenario) return "PROVENANCE UNKNOWN";
  const code = normalizeProvenance(scenario.provenance || scenario.source_type);
  return PROVENANCE_LABELS[code] || "PROVENANCE UNKNOWN";
}

export default function HeaderNav({ activeTab, onSelectTab }) {
  const {
    scenarios,
    systemStatus,
    selectedScenario,
    selectedScenarioId,
    selectScenario,
    analysis,
    runAnalysis,
  } = useSentinel();

  const tabRefs = useRef({});

  const llmMode = systemStatus?.data?.llm_mode || "N/A";
  const llmProvider = systemStatus?.data?.llm_provider || "N/A";
  const model = systemStatus?.data?.model || "N/A";
  const version = systemStatus?.data?.version || "N/A";
  const simLive = systemStatus?.data?.simulation_live_status || "N/A";
  const sovereignty = systemStatus?.data?.sovereignty || null;
  const isAnalyzing = analysis.status === "RUNNING";

  // Explicit sovereign-mode indicator: LOCAL AI / CLOUD AI, straight from the
  // backend status. STUB reports itself as such — nothing here is inferred.
  const aiModeLabel = llmMode === "N/A" ? "AI MODE N/A" : `${llmMode} AI`;
  const aiDetail = sovereignty?.cloud_telemetry_disabled
    ? `${llmProvider} · ${model} · CLOUD TELEMETRY DISABLED`
    : `${llmProvider} · ${model}`;

  const onTabKeyDown = (event, index) => {
    const count = NAV_TABS.length;
    let next = null;
    if (event.key === "ArrowRight") next = (index + 1) % count;
    if (event.key === "ArrowLeft") next = (index - 1 + count) % count;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = count - 1;
    if (next === null) return;
    event.preventDefault();
    const tab = NAV_TABS[next];
    onSelectTab(tab.id);
    tabRefs.current[tab.id]?.focus();
  };

  return (
    <header className="ops-header" role="banner">
      <div className="ops-header__top">
        <div className="brand">
          <span className="brand__name">SENTINEL</span>
          <span className="brand__sub">SPACECRAFT FDIR &amp; RECOVERY CONSOLE</span>
          <span className="brand__version">CONTRACT {version}</span>
        </div>

        <div className="ops-header__controls">
          <label className="field-label" htmlFor="scenario-select">
            Crash dump scenario
          </label>
          <select
            id="scenario-select"
            className="field-select"
            value={selectedScenarioId || ""}
            onChange={(e) => selectScenario(e.target.value)}
            disabled={isAnalyzing}
          >
            {scenarios.loading ? (
              <option value="">Loading scenarios...</option>
            ) : scenarios.error || !scenarios.data ? (
              <option value="">Scenarios unavailable</option>
            ) : (
              (scenarios.data.scenarios || []).map((sc) => (
                <option key={sc.scenario_id} value={sc.scenario_id}>
                  SCENARIO {sc.scenario_id}: {sc.fault_type || "N/A"} — {provenanceLabel(sc)}
                </option>
              ))
            )}
          </select>

          <button
            type="button"
            className="btn btn--primary"
            onClick={runAnalysis}
            disabled={isAnalyzing || !selectedScenario}
          >
            {isAnalyzing ? "ANALYSIS RUNNING" : "RUN FDIR ANALYSIS"}
          </button>
        </div>

        <div className="ops-header__status">
          <div className="sys-pill">
            <span className="sys-pill__label">SOURCE</span>
            <span className="sys-pill__value">{String(simLive).toUpperCase()}</span>
          </div>
          <div className={`sys-pill sys-pill--ai ${llmMode === "LOCAL" ? "sys-pill--local" : ""}`}>
            <span className="sys-pill__label">AI ENGINE</span>
            <span className="sys-pill__value">{aiModeLabel}</span>
            <span className="sys-pill__detail">{aiDetail}</span>
          </div>
          <div className="sys-pill sys-pill--run">
            <span className="sys-pill__label">RUN ID</span>
            <span className="sys-pill__value">
              {analysis.runId || (analysis.output ? "COMPLETE" : "N/A")}
            </span>
          </div>
        </div>
      </div>

      <nav className="ops-tabs" aria-label="Primary mission control navigation">
        <div className="ops-tabs__list" role="tablist" aria-label="Console sections">
          {NAV_TABS.map((tab, index) => (
            <button
              key={tab.id}
              ref={(node) => {
                tabRefs.current[tab.id] = node;
              }}
              type="button"
              role="tab"
              id={`tab-${tab.id}`}
              aria-selected={activeTab === tab.id}
              aria-controls={`panel-${tab.id}`}
              tabIndex={activeTab === tab.id ? 0 : -1}
              className={`ops-tab ${activeTab === tab.id ? "ops-tab--active" : ""}`}
              onClick={() => onSelectTab(tab.id)}
              onKeyDown={(e) => onTabKeyDown(e, index)}
            >
              <span className="ops-tab__index">{index + 1}</span>
              {tab.label}
            </button>
          ))}
        </div>
      </nav>
    </header>
  );
}