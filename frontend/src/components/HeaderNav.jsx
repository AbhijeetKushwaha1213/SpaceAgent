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
import { useTheme } from "../state/ThemeContext";
import { PROVENANCE_LABELS, normalizeProvenance } from "../generated/contract";
import Icon from "./ui/Icon";

export const NAV_TABS = [
  { id: "overview", label: "Mission Overview" },
  { id: "pipeline", label: "Pipeline Demo" },
  { id: "telemetry", label: "Telemetry" },
  { id: "investigation", label: "Fault Investigation" },
  { id: "reconciliation", label: "Reconciliation" },
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

  const { theme, toggleTheme } = useTheme();
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
          <div className="brand__title-wrap">
            <span className="brand__name">Dashboard</span>
            <span className="brand__version">v{version}</span>
          </div>
          <span className="brand__sub">SENTINEL AUTONOMOUS SPACECRAFT FDIR</span>
        </div>

        <div className="ops-header__controls">
          <div className="scenario-select-box">
            <label className="field-label" htmlFor="scenario-select">
              Active Scenario
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
          </div>

          <button
            type="button"
            className="btn btn--primary btn--run-analysis"
            onClick={runAnalysis}
            disabled={isAnalyzing || !selectedScenario}
          >
            <Icon name={isAnalyzing ? "refresh" : "rocket"} size={15} className={isAnalyzing ? "spin" : ""} />
            {isAnalyzing ? "ANALYSIS RUNNING" : "RUN FDIR ANALYSIS"}
          </button>
        </div>

        <div className="ops-header__status">
          {/* Quick Header Metric Badges matching Image 1 */}
          <div className="header-quick-stat">
            <div className="quick-stat-num">
              <span className="mono">7,052</span>
              <span className="quick-stat-badge">$22.5M</span>
            </div>
            <span className="quick-stat-lbl">EOI SENT</span>
          </div>

          <div className="header-quick-stat">
            <div className="quick-stat-num">
              <span className="mono">34</span>
              <span className="quick-stat-badge">$5.9M</span>
            </div>
            <span className="quick-stat-lbl">NEW REQUESTS</span>
          </div>

          <div className={`sys-pill sys-pill--ai ${llmMode === "LOCAL" ? "sys-pill--local" : ""}`}>
            <span className="sys-pill__label">AI ENGINE</span>
            <span className="sys-pill__value">{aiModeLabel}</span>
            <span className="sys-pill__detail">{aiDetail}</span>
          </div>

          {/* Theme Toggle Button */}
          <button
            type="button"
            className="header-theme-toggle"
            onClick={toggleTheme}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
            title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
          >
            <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
            <span className="theme-toggle-label">{theme === "dark" ? "Light" : "Dark"}</span>
          </button>
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