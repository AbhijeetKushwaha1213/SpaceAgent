import React, { useState, useEffect } from "react";
import "./App.css";

import HeaderNav, { NAV_TABS } from "./components/HeaderNav";
import MissionOverview from "./components/MissionOverview";
import TelemetryConsole from "./components/TelemetryConsole";
import FaultInvestigation from "./components/FaultInvestigation";
import PhysicsState from "./components/PhysicsState";
import RecoveryOps from "./components/RecoveryOps";
import EvidenceLibrary from "./components/EvidenceLibrary";
import AuditTrail from "./components/AuditTrail";
import EvaluationMetrics from "./components/EvaluationMetrics";

import {
  API,
  CANONICAL_TELEMETRY_FIELD,
  CONTRACT_VERSION,
  PROVENANCE,
  PROVENANCE_LABELS,
  isRealProvenance,
  normalizeProvenance,
} from "./generated/contract";

export { PROVENANCE, CONTRACT_VERSION };

export const PROVENANCE_COLOURS = {
  [PROVENANCE.REAL]: {
    color: "rgba(16, 185, 129, 0.95)",
    border: "rgba(16, 185, 129, 0.35)",
    background: "rgba(16, 185, 129, 0.08)",
  },
  [PROVENANCE.SYNTHETIC]: {
    color: "rgba(245, 183, 77, 0.95)",
    border: "rgba(245, 183, 77, 0.35)",
    background: "rgba(245, 183, 77, 0.10)",
  },
  [PROVENANCE.SYNTHETIC_FROM_REAL_METADATA]: {
    color: "rgba(59, 130, 246, 0.95)",
    border: "rgba(59, 130, 246, 0.35)",
    background: "rgba(59, 130, 246, 0.10)",
  },
  [PROVENANCE.DEMO]: {
    color: "rgba(168, 85, 247, 0.95)",
    border: "rgba(168, 85, 247, 0.35)",
    background: "rgba(168, 85, 247, 0.10)",
  },
  UNKNOWN: {
    color: "rgba(148, 163, 184, 0.95)",
    border: "rgba(148, 163, 184, 0.35)",
    background: "rgba(148, 163, 184, 0.10)",
  },
};

export function isDashboardPath(path) {
  if (typeof path !== "string") return false;
  const p = path.toLowerCase().replace(/\/+$/, "");
  return p === "/dashboard";
}

export function resolveProvenance(scenario) {
  if (!scenario) return "UNKNOWN";
  const raw = scenario.provenance || scenario.source_type;
  return normalizeProvenance(raw);
}

export function isRealTelemetry(scenario) {
  return isRealProvenance(resolveProvenance(scenario));
}

export function provenanceDisplay(scenario) {
  const code = resolveProvenance(scenario);
  const colours = PROVENANCE_COLOURS[code] || PROVENANCE_COLOURS.UNKNOWN;
  const label = PROVENANCE_LABELS[code] || "PROVENANCE UNKNOWN";
  return { code, label, colours };
}

export const DEFAULT_BACKEND_URL = "http://localhost:8000";

const BACKEND_URL =
  (typeof window !== "undefined" && window.SENTINEL_BACKEND_URL) ||
  (typeof process !== "undefined" && process.env?.REACT_APP_BACKEND_URL) ||
  DEFAULT_BACKEND_URL;

export default function App() {
  const [currentPath, setCurrentPath] = useState(
    typeof window !== "undefined" ? window.location.pathname : "/dashboard"
  );
  const [activeTab, setActiveTab] = useState(NAV_TABS.MISSION_OVERVIEW);
  const [scenarios, setScenarios] = useState([]);
  const [selectedScenarioId, setSelectedScenarioId] = useState(null);
  const [systemStatus, setSystemStatus] = useState(null);
  const [analysisOutput, setAnalysisOutput] = useState(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);

  // Fetch scenarios on mount
  useEffect(() => {
    const handlePopState = () => {
      if (typeof window !== "undefined") {
        setCurrentPath(window.location.pathname);
      }
    };
    if (typeof window !== "undefined") {
      window.addEventListener("popstate", handlePopState);
    }
    const fetchScenarios = async () => {
      try {
        const resp = await fetch(`${BACKEND_URL}/api/v1/scenarios`);
        if (resp.ok) {
          const data = await resp.json();
          const list = data.scenarios || [];
          setScenarios(list);
          if (list.length > 0) {
            setSelectedScenarioId(list[0].scenario_id);
          }
        }
      } catch (err) {
        console.warn("Failed to fetch scenarios from backend:", err);
      }
    };

    const fetchSystemStatus = async () => {
      try {
        const resp = await fetch(`${BACKEND_URL}/api/v1/system/status`);
        if (resp.ok) {
          const data = await resp.json();
          setSystemStatus(data);
        }
      } catch (err) {
        console.warn("Failed to fetch system status:", err);
      }
    };

    fetchScenarios();
    fetchSystemStatus();

    return () => {
      if (typeof window !== "undefined") {
        window.removeEventListener("popstate", handlePopState);
      }
    };
  }, []);

  const currentScenario =
    scenarios.find((s) => String(s.scenario_id) === String(selectedScenarioId)) ||
    scenarios[0] ||
    null;

  const isReal = isRealTelemetry(currentScenario);
  const simulationLabel = isReal ? "REAL TELEMETRY" : "SIMULATION DATA";

  if (!isDashboardPath(currentPath)) {
    return (
      <iframe
        src="/public/landing.html"
        style={{
          width: "100%",
          height: "100vh",
          border: "none",
          margin: 0,
          padding: 0,
          overflow: "hidden",
        }}
        title="SENTINEL Landing Page"
      />
    );
  }

  // Run FDIR Analysis
  const handleRunAnalysis = async () => {
    if (!currentScenario || isAnalyzing) return;
    setIsAnalyzing(true);

    try {
      // Execute stream analysis endpoint
      const response = await fetch(`${BACKEND_URL}/api/v1/analyze/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentScenario),
      });

      if (!response.ok) {
        throw new Error(`Analysis failed with HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop() || "";

        for (const block of lines) {
          if (!block.trim()) continue;
          const eventLines = block.split("\n");
          let eventType = "";
          let dataStr = "";

          for (const line of eventLines) {
            if (line.startsWith("event: ")) {
              eventType = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              dataStr = line.slice(6).trim();
            }
          }

          if (eventType === "result" && dataStr) {
            try {
              const parsed = JSON.parse(dataStr);
              setAnalysisOutput(parsed);
            } catch (e) {
              console.error("Failed to parse result JSON:", e);
            }
          }
        }
      }
    } catch (err) {
      console.warn("Analysis stream error, using scenario telemetry baseline:", err);
    } finally {
      setIsAnalyzing(false);
    }
  };

  return (
    <div className="app-container">
      {/* Primary Header & Navigation */}
      <HeaderNav
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        scenarios={scenarios}
        selectedScenarioId={selectedScenarioId}
        onSelectScenario={(id) => {
          setSelectedScenarioId(id);
          setAnalysisOutput(null);
        }}
        systemStatus={systemStatus}
        onRunAnalysis={handleRunAnalysis}
        isAnalyzing={isAnalyzing}
      />

      {/* Main Mission Control Content */}
      <main className="ops-main" role="main">
        {activeTab === NAV_TABS.MISSION_OVERVIEW && (
          <MissionOverview
            scenario={currentScenario}
            analysisOutput={analysisOutput}
            systemStatus={systemStatus}
            isAnalyzing={isAnalyzing}
            onNavigateToTab={setActiveTab}
          />
        )}

        {activeTab === NAV_TABS.TELEMETRY && (
          <TelemetryConsole scenario={currentScenario} />
        )}

        {activeTab === NAV_TABS.INVESTIGATION && (
          <FaultInvestigation
            scenario={currentScenario}
            analysisOutput={analysisOutput}
          />
        )}

        {activeTab === NAV_TABS.PHYSICS && (
          <PhysicsState scenario={currentScenario} />
        )}

        {activeTab === NAV_TABS.RECOVERY && (
          <RecoveryOps
            scenario={currentScenario}
            analysisOutput={analysisOutput}
          />
        )}

        {activeTab === NAV_TABS.EVIDENCE && (
          <EvidenceLibrary
            scenario={currentScenario}
            analysisOutput={analysisOutput}
          />
        )}

        {activeTab === NAV_TABS.AUDIT && (
          <AuditTrail
            scenario={currentScenario}
            analysisOutput={analysisOutput}
          />
        )}

        {activeTab === NAV_TABS.EVALUATION && (
          <EvaluationMetrics backendUrl={BACKEND_URL} />
        )}
      </main>
    </div>
  );
}
