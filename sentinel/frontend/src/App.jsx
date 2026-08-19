/*
 * SENTINEL — operator console shell.
 *
 * Renders the header/navigation and the active console section. All data
 * flows through SentinelProvider; views are dumb renderers of backend state.
 */

import React, { useState } from "react";
import "./App.css";

import HeaderNav from "./components/HeaderNav";
import MissionOverview from "./components/views/MissionOverview";
import TelemetryView from "./components/views/TelemetryView";
import FaultInvestigationView from "./components/views/FaultInvestigationView";
import PhysicsView from "./components/views/PhysicsView";
import RecoveryView from "./components/views/RecoveryView";
import EvidenceView from "./components/views/EvidenceView";
import AuditView from "./components/views/AuditView";
import EvaluationView from "./components/views/EvaluationView";
import { SentinelProvider } from "./state/SentinelContext";

const TAB_IDS = Object.freeze({
  overview: "overview",
  telemetry: "telemetry",
  investigation: "investigation",
  physics: "physics",
  recovery: "recovery",
  evidence: "evidence",
  audit: "audit",
  evaluation: "evaluation",
});

function isDashboardPath(path) {
  if (typeof path !== "string") return false;
  const p = path.toLowerCase().replace(/\/+$/, "");
  return p === "/dashboard";
}

function Console() {
  const [currentPath, setCurrentPath] = useState(
    typeof window !== "undefined" ? window.location.pathname : "/dashboard"
  );
  const [activeTab, setActiveTab] = useState(TAB_IDS.overview);

  React.useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const handlePopState = () => setCurrentPath(window.location.pathname);
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  if (!isDashboardPath(currentPath)) {
    return (
      <iframe
        src="/public/landing.html"
        title="SENTINEL landing page"
        style={{
          width: "100%",
          height: "100vh",
          border: "none",
          margin: 0,
          padding: 0,
          overflow: "hidden",
        }}
      />
    );
  }

  const navigate = (tabId) => setActiveTab(tabId);

  return (
    <div className="app-shell">
      <HeaderNav activeTab={activeTab} onSelectTab={navigate} />
      <main className="ops-main" id="ops-main">
        <div
          key={activeTab}
          role="tabpanel"
          id={`panel-${activeTab}`}
          aria-labelledby={`tab-${activeTab}`}
        >
          {activeTab === TAB_IDS.overview && <MissionOverview onNavigate={navigate} />}
          {activeTab === TAB_IDS.telemetry && <TelemetryView />}
          {activeTab === TAB_IDS.investigation && (
            <FaultInvestigationView onNavigate={navigate} />
          )}
          {activeTab === TAB_IDS.physics && <PhysicsView />}
          {activeTab === TAB_IDS.recovery && <RecoveryView />}
          {activeTab === TAB_IDS.evidence && <EvidenceView onNavigate={navigate} />}
          {activeTab === TAB_IDS.audit && <AuditView />}
          {activeTab === TAB_IDS.evaluation && <EvaluationView />}
        </div>
      </main>
      <footer className="ops-footer">
        <span className="mono fs-sm">
          SENTINEL — every value on this console is served by the backend API.
          Absent data renders as N/A; nothing is simulated client-side.
        </span>
      </footer>
    </div>
  );
}

export default function App() {
  return (
    <SentinelProvider>
      <Console />
    </SentinelProvider>
  );
}