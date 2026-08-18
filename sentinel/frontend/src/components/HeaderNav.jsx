import React from "react";

export const NAV_TABS = {
  MISSION_OVERVIEW: "overview",
  TELEMETRY: "telemetry",
  INVESTIGATION: "investigation",
  PHYSICS: "physics",
  RECOVERY: "recovery",
  EVIDENCE: "evidence",
  AUDIT: "audit",
  EVALUATION: "evaluation",
};

export default function HeaderNav({
  activeTab,
  onSelectTab,
  scenarios = [],
  selectedScenarioId,
  onSelectScenario,
  systemStatus,
  onRunAnalysis,
  isAnalyzing,
}) {
  const llmMode = (systemStatus?.llm_mode || "STUB").toUpperCase();
  const llmProvider = systemStatus?.llm_provider || "stub";
  const modelName = systemStatus?.model || "stub-model";
  const isLocal = llmMode === "LOCAL";
  const isCloud = llmMode === "CLOUD";

  return (
    <header className="ops-header" role="banner">
      <div className="ops-header-top">
        <div className="brand-zone">
          <span className="brand-logo">SENTINEL</span>
          <span className="brand-subtitle">SPACECRAFT FDIR &amp; RECOVERY CONSOLE</span>
          <span className="version-badge">v1.0.0</span>
        </div>

        <div className="scenario-selector-zone">
          <label htmlFor="scenario-select" className="ops-label">
            CRASH DUMP SCENARIO:
          </label>
          <select
            id="scenario-select"
            className="ops-select"
            value={selectedScenarioId || ""}
            onChange={(e) => onSelectScenario(e.target.value)}
            disabled={isAnalyzing}
            aria-label="Select fault scenario"
          >
            {scenarios.length === 0 ? (
              <option value="">LOADING SCENARIOS...</option>
            ) : (
              scenarios.map((sc) => (
                <option key={sc.scenario_id} value={sc.scenario_id}>
                  [{sc.provenance || "SYNTHETIC"}] SCENARIO {sc.scenario_id}: {sc.fault_type} ({sc.safe_mode_trigger || "SAFE MODE"})
                </option>
              ))
            )}
          </select>

          <button
            className="ops-btn ops-btn-primary"
            onClick={onRunAnalysis}
            disabled={isAnalyzing}
            aria-label="Execute FDIR analysis pipeline"
          >
            {isAnalyzing ? "[ANALYZING...]" : "[RUN FDIR ANALYSIS]"}
          </button>
        </div>

        <div className="system-status-zone" aria-label="System status telemetry">
          <div className="status-pill">
            <span className="status-dot green"></span>
            <span className="status-key">SOURCE:</span>
            <span className="status-val">LIVE SIMULATION</span>
          </div>

          <div className={`status-pill ${isLocal ? "cyan" : isCloud ? "amber" : "gray"}`}>
            <span className={`status-dot ${isLocal ? "cyan" : isCloud ? "amber" : "gray"}`}></span>
            <span className="status-key">ENGINE:</span>
            <span className="status-val">{llmMode} AI ({modelName})</span>
          </div>
        </div>
      </div>

      <nav className="ops-nav" role="navigation" aria-label="Primary Mission Control Navigation">
        <button
          className={`nav-tab ${activeTab === NAV_TABS.MISSION_OVERVIEW ? "active" : ""}`}
          onClick={() => onSelectTab(NAV_TABS.MISSION_OVERVIEW)}
          aria-current={activeTab === NAV_TABS.MISSION_OVERVIEW ? "page" : undefined}
        >
          1. MISSION OVERVIEW
        </button>
        <button
          className={`nav-tab ${activeTab === NAV_TABS.TELEMETRY ? "active" : ""}`}
          onClick={() => onSelectTab(NAV_TABS.TELEMETRY)}
          aria-current={activeTab === NAV_TABS.TELEMETRY ? "page" : undefined}
        >
          2. TELEMETRY
        </button>
        <button
          className={`nav-tab ${activeTab === NAV_TABS.INVESTIGATION ? "active" : ""}`}
          onClick={() => onSelectTab(NAV_TABS.INVESTIGATION)}
          aria-current={activeTab === NAV_TABS.INVESTIGATION ? "page" : undefined}
        >
          3. FAULT INVESTIGATION
        </button>
        <button
          className={`nav-tab ${activeTab === NAV_TABS.PHYSICS ? "active" : ""}`}
          onClick={() => onSelectTab(NAV_TABS.PHYSICS)}
          aria-current={activeTab === NAV_TABS.PHYSICS ? "page" : undefined}
        >
          4. PHYSICS / STATE
        </button>
        <button
          className={`nav-tab ${activeTab === NAV_TABS.RECOVERY ? "active" : ""}`}
          onClick={() => onSelectTab(NAV_TABS.RECOVERY)}
          aria-current={activeTab === NAV_TABS.RECOVERY ? "page" : undefined}
        >
          5. RECOVERY
        </button>
        <button
          className={`nav-tab ${activeTab === NAV_TABS.EVIDENCE ? "active" : ""}`}
          onClick={() => onSelectTab(NAV_TABS.EVIDENCE)}
          aria-current={activeTab === NAV_TABS.EVIDENCE ? "page" : undefined}
        >
          6. EVIDENCE
        </button>
        <button
          className={`nav-tab ${activeTab === NAV_TABS.AUDIT ? "active" : ""}`}
          onClick={() => onSelectTab(NAV_TABS.AUDIT)}
          aria-current={activeTab === NAV_TABS.AUDIT ? "page" : undefined}
        >
          7. AUDIT
        </button>
        <button
          className={`nav-tab ${activeTab === NAV_TABS.EVALUATION ? "active" : ""}`}
          onClick={() => onSelectTab(NAV_TABS.EVALUATION)}
          aria-current={activeTab === NAV_TABS.EVALUATION ? "page" : undefined}
        >
          8. EVALUATION
        </button>
      </nav>
    </header>
  );
}
