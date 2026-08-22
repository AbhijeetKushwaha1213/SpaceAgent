/*
 * SENTINEL shared data context.
 *
 * One provider fetches and caches every piece of backend state the console
 * renders. Views read from this context; they never fetch, derive, or invent
 * values themselves. Anything the backend did not supply renders as N/A.
 *
 * State shape:
 *   entities.<name> = { data, loading, error }
 *   selection    = { scenarioId, runId }
 *   analysis     = { status, output, runId, events }
 *   focus        = { channel, timestamp }  (evidence -> telemetry jump)
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { apiGet, apiPost, resolveBackendUrl, streamAnalysis } from "../api/client";
import { ENDPOINTS, runDecisions, runVerify } from "../api/endpoints";

const SentinelContext = createContext(null);

export const ANALYSIS_STATUS = Object.freeze({
  IDLE: "IDLE",
  RUNNING: "RUNNING",
  COMPLETE: "COMPLETE",
  ERROR: "ERROR",
});

function emptyEntity() {
  return { data: null, loading: false, error: null };
}

export function SentinelProvider({ children }) {
  const backendUrl = useMemo(() => resolveBackendUrl(), []);

  // ── scenarios ──────────────────────────────────────────────────────────
  const [scenarios, setScenarios] = useState(emptyEntity);
  const [selectedScenarioId, setSelectedScenarioId] = useState(null);

  // ── system + dictionary + static catalogues ────────────────────────────
  const [systemStatus, setSystemStatus] = useState(emptyEntity);
  const [channelDictionary, setChannelDictionary] = useState(emptyEntity);
  const [physicsConstraints, setPhysicsConstraints] = useState(emptyEntity);
  const [auditStatus, setAuditStatus] = useState(emptyEntity);

  // ── per-scenario analysis artifacts (deterministic backend endpoints) ──
  const [detection, setDetection] = useState(emptyEntity);
  const [physicsReport, setPhysicsReport] = useState(emptyEntity);

  // ── audit runs ─────────────────────────────────────────────────────────
  const [runs, setRuns] = useState(emptyEntity);
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [selectedRun, setSelectedRun] = useState(emptyEntity);
  const [chainVerification, setChainVerification] = useState(emptyEntity);
  const [decisionResult, setDecisionResult] = useState(emptyEntity);

  // ── streaming analysis ─────────────────────────────────────────────────
  const [analysis, setAnalysis] = useState({
    status: ANALYSIS_STATUS.IDLE,
    output: null,
    runId: null,
    events: [],
  });

  // ── cross-view navigation ──────────────────────────────────────────────
  const [focus, setFocus] = useState(null); // { channel, timestamp }

  const abortRef = useRef(null);

  // ── fetch scenario catalogue + static state on mount ───────────────────
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const data = await apiGet(ENDPOINTS.scenarios);
        if (cancelled) return;
        setScenarios({ data, loading: false, error: null });
        const list = data.scenarios || [];
        if (list.length > 0) {
          setSelectedScenarioId(String(list[0].scenario_id));
        }
      } catch (err) {
        if (!cancelled) {
          setScenarios({ data: null, loading: false, error: String(err) });
        }
      }
    })();
    (async () => {
      try {
        const data = await apiGet(ENDPOINTS.systemStatus);
        if (!cancelled) setSystemStatus({ data, loading: false, error: null });
      } catch (err) {
        if (!cancelled) {
          setSystemStatus({ data: null, loading: false, error: String(err) });
        }
      }
    })();
    (async () => {
      try {
        const data = await apiGet(ENDPOINTS.channels);
        if (!cancelled) setChannelDictionary({ data, loading: false, error: null });
      } catch (err) {
        if (!cancelled) {
          setChannelDictionary({ data: null, loading: false, error: String(err) });
        }
      }
    })();
    (async () => {
      try {
        const data = await apiGet(ENDPOINTS.physicsConstraints);
        if (!cancelled) {
          setPhysicsConstraints({ data, loading: false, error: null });
        }
      } catch (err) {
        if (!cancelled) {
          setPhysicsConstraints({ data: null, loading: false, error: String(err) });
        }
      }
    })();
    (async () => {
      try {
        const data = await apiGet(ENDPOINTS.auditStatus);
        if (!cancelled) setAuditStatus({ data, loading: false, error: null });
      } catch (err) {
        if (!cancelled) {
          setAuditStatus({ data: null, loading: false, error: String(err) });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // ── fetch one run detail ───────────────────────────────────────────────
  const selectRun = useCallback(async (runId) => {
    if (!runId) return;
    setSelectedRunId(runId);
    setSelectedRun({ data: null, loading: true, error: null });
    try {
      const data = await apiGet(`/api/v1/runs/${encodeURIComponent(runId)}`);
      setSelectedRun({ data, loading: false, error: null });
    } catch (err) {
      setSelectedRun({ data: null, loading: false, error: String(err) });
    }
  }, []);

  // ── fetch audit run list ───────────────────────────────────────────────
  const refreshRuns = useCallback(async () => {
    setRuns((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const data = await apiGet(`${ENDPOINTS.runs}?limit=100`);
      setRuns({ data, loading: false, error: null });
      if (data?.runs?.length > 0 && !selectedRunId) {
        selectRun(data.runs[0].run_id);
      }
    } catch (err) {
      setRuns({ data: null, loading: false, error: String(err) });
    }
  }, [selectRun, selectedRunId]);

  useEffect(() => {
    refreshRuns();
  }, [refreshRuns]);

  const verifyRun = useCallback(async (runId) => {
    setChainVerification({ data: null, loading: true, error: null });
    try {
      const data = await apiGet(runVerify(runId));
      setChainVerification({ data, loading: false, error: null });
    } catch (err) {
      setChainVerification({ data: null, loading: false, error: String(err) });
    }
  }, []);

  const recordDecision = useCallback(async (runId, decisionPayload) => {
    setDecisionResult({ data: null, loading: true, error: null });
    try {
      const data = await apiPost(runDecisions(runId), decisionPayload);
      setDecisionResult({ data, loading: false, error: null });
      await refreshRuns();
      if (selectedRunId === runId) {
        const detail = await apiGet(`/api/v1/runs/${encodeURIComponent(runId)}`);
        setSelectedRun({ data: detail, loading: false, error: null });
      }
      return data;
    } catch (err) {
      setDecisionResult({ data: null, loading: false, error: String(err) });
      return null;
    }
  }, [refreshRuns, selectedRunId]);

  // ── selected scenario ──────────────────────────────────────────────────
  const selectedScenario = useMemo(() => {
    const list = scenarios.data?.scenarios || [];
    return (
      list.find((s) => String(s.scenario_id) === String(selectedScenarioId)) ||
      list[0] ||
      null
    );
  }, [scenarios, selectedScenarioId]);

  const selectScenario = useCallback((id) => {
    setSelectedScenarioId(String(id));
    setAnalysis({ status: ANALYSIS_STATUS.IDLE, output: null, runId: null, events: [] });
    setSelectedRunId(null);
    setSelectedRun(emptyEntity());
    setDecisionResult(emptyEntity());
    setChainVerification(emptyEntity());
  }, []);

  // ── deterministic per-scenario analysis (detect + physics) ─────────────
  useEffect(() => {
    if (!selectedScenario) return;
    let cancelled = false;
    const payload = selectedScenario;

    setDetection({ data: null, loading: true, error: null });
    setPhysicsReport({ data: null, loading: true, error: null });

    (async () => {
      try {
        const data = await apiPost(ENDPOINTS.detect, payload);
        if (!cancelled) setDetection({ data, loading: false, error: null });
      } catch (err) {
        if (!cancelled) {
          setDetection({ data: null, loading: false, error: String(err) });
        }
      }
    })();
    (async () => {
      try {
        const data = await apiPost(ENDPOINTS.physics, payload);
        if (!cancelled) setPhysicsReport({ data, loading: false, error: null });
      } catch (err) {
        if (!cancelled) {
          setPhysicsReport({ data: null, loading: false, error: String(err) });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedScenario]);

  // ── streaming FDIR analysis ────────────────────────────────────────────
  const runAnalysis = useCallback(async () => {
    if (!selectedScenario) return;
    if (analysis.status === ANALYSIS_STATUS.RUNNING) return;

    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }

    setAnalysis({
      status: ANALYSIS_STATUS.RUNNING,
      output: null,
      runId: null,
      events: [],
    });

    try {
      await streamAnalysis(selectedScenario, {
        onRunId: (runId) => {
          setAnalysis((prev) => ({ ...prev, runId }));
        },
        onEvent: (eventType, data) => {
          setAnalysis((prev) => {
            const next = {
              ...prev,
              events: [...prev.events.slice(-400), { type: eventType, data }],
            };
            if (eventType === "result" && data) {
              // The backend serializes the result payload as a JSON string
              // inside the SSE event ("data" is the SSE event's data field).
              let output = data;
              if (typeof data === "string") {
                try {
                  output = JSON.parse(data);
                } catch (e) {
                  output = null;
                }
              }
              if (output && typeof output === "object") {
                next.output = output;
              }
            }
            return next;
          });
        },
      });
      setAnalysis((prev) => ({ ...prev, status: ANALYSIS_STATUS.COMPLETE }));
      if (analysis.runId || selectedScenario) {
        refreshRuns();
      }
    } catch (err) {
      if (err?.name === "AbortError") return;
      setAnalysis((prev) => ({
        ...prev,
        status: ANALYSIS_STATUS.ERROR,
        error: String(err),
      }));
    }
  }, [selectedScenario, analysis.status, refreshRuns]);

  const focusTelemetry = useCallback((channel, timestamp) => {
    setFocus({ channel, timestamp: timestamp || null });
  }, []);

  const clearTelemetryFocus = useCallback(() => setFocus(null), []);

  const value = useMemo(
    () => ({
      backendUrl,
      scenarios,
      systemStatus,
      channelDictionary,
      physicsConstraints,
      auditStatus,
      detection,
      physicsReport,
      runs,
      selectedRun,
      selectedRunId,
      chainVerification,
      decisionResult,
      selectedScenario,
      selectedScenarioId,
      analysis,
      focus,
      selectScenario,
      runAnalysis,
      refreshRuns,
      selectRun,
      verifyRun,
      recordDecision,
      focusTelemetry,
      clearTelemetryFocus,
    }),
    [
      backendUrl,
      scenarios,
      systemStatus,
      channelDictionary,
      physicsConstraints,
      auditStatus,
      detection,
      physicsReport,
      runs,
      selectedRun,
      selectedRunId,
      chainVerification,
      decisionResult,
      selectedScenario,
      selectedScenarioId,
      analysis,
      focus,
      selectScenario,
      runAnalysis,
      refreshRuns,
      selectRun,
      verifyRun,
      recordDecision,
      focusTelemetry,
      clearTelemetryFocus,
    ]
  );

  return <SentinelContext.Provider value={value}>{children}</SentinelContext.Provider>;
}

export function useSentinel() {
  const ctx = useContext(SentinelContext);
  if (!ctx) {
    throw new Error("useSentinel must be used inside <SentinelProvider>");
  }
  return ctx;
}
