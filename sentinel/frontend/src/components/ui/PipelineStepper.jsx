/*
 * PipelineStepper — live FDIR pipeline progress & stage pacing.
 *
 * Phase 25/26 hardening.
 *
 * Exposes the 10 real pipeline stages. No faked progress, no arbitrary timers.
 * Tracks incoming SSE events and stage transitions in real time.
 *
 * Authority boundaries:
 *   - PHYSICS = BINDING AUTHORITY
 *   - SAFETY = BINDING AUTHORITY
 *   - LLM = ASSISTIVE ONLY
 *   - RECONCILIATION = DETERMINISTIC (CORRELATION != IDENTITY)
 */

import React from "react";
import Icon from "./Icon";
import { PIPELINE_STAGES, computeHighestStageIndex } from "../../state/pipelineStateMachine";
import { useSentinel } from "../../state/SentinelContext";

export default function PipelineStepper({ analysis: propAnalysis, children }) {
  const { analysis: ctxAnalysis, pipelineProgress } = useSentinel();
  const analysis = propAnalysis || ctxAnalysis;

  const status = analysis?.status || "IDLE";
  const events = analysis?.events || [];
  const output = analysis?.output || null;
  const isRunning = status === "RUNNING";
  const isError = status === "ERROR";
  const isComplete = status === "COMPLETE" || Boolean(output);

  const stages = pipelineProgress?.stages || PIPELINE_STAGES.map((s) => ({ ...s, status: "pending" }));
  const activeIndex = pipelineProgress?.activeIndex ?? computeHighestStageIndex(events);
  const activeStage = activeIndex >= 0 && activeIndex < stages.length ? stages[activeIndex] : null;

  const completedCount = isComplete
    ? stages.length
    : stages.filter((s) => s.status === "completed" || s.status === "blocked").length;

  const pulseClass = isError
    ? "pipeline__pulse--crit"
    : isComplete
    ? "pipeline__pulse--ok"
    : isRunning
    ? "pipeline__pulse--live"
    : "";

  const statusTitle = isError
    ? "EXECUTION HALTED"
    : isComplete
    ? "FDIR ANALYSIS COMPLETE"
    : isRunning
    ? `PROCESSING STAGE 0${(activeIndex + 1)}: ${activeStage?.name?.toUpperCase()}`
    : "STANDING BY";

  return (
    <section className="pipeline" aria-label="FDIR pipeline live execution progress">
      <div className="pipeline__bar">
        <h2 className="pipeline__title">
          <span className={`pipeline__pulse ${pulseClass}`} aria-hidden="true" />
          <span>FDIR Pipeline · {statusTitle}</span>
        </h2>
        <span className="pipeline__count mono">
          {completedCount}/{stages.length} STAGES
        </span>
      </div>

      {/* 10-Stage Horizontal Track */}
      <ol className="pipeline__track" style={{ display: "grid", gridTemplateColumns: `repeat(${stages.length}, 1fr)`, gap: "0.25rem" }}>
        {stages.map((stage, idx) => {
          const st = stage.status;
          const isCurrent = idx === activeIndex && isRunning;
          const isDone = st === "completed";
          const isBlocked = st === "blocked";
          const isFailed = st === "failed";
          const isUncertain = st === "uncertain";

          const nodeClass = isCurrent
            ? "pstep--active"
            : isDone
            ? "pstep--done"
            : isBlocked || isFailed
            ? "pstep--failed"
            : isUncertain
            ? "pstep--uncertain"
            : "pstep--idle";

          return (
            <li
              key={stage.id}
              className={`pstep ${nodeClass}`}
              title={`${stage.name} (${stage.authority})`}
              style={{ padding: "0.4rem 0.2rem" }}
            >
              <span className="pstep__node">
                {isDone ? (
                  <Icon name="check" size={12} />
                ) : isFailed ? (
                  <Icon name="cross" size={12} />
                ) : isBlocked ? (
                  <Icon name="shield" size={12} />
                ) : (
                  stage.num
                )}
              </span>
              <span className="pstep__label fs-xs" style={{ fontSize: "0.72rem" }}>
                {stage.shortLabel}
              </span>
            </li>
          );
        })}
      </ol>

      {/* Live Stage Hero / Announcement Banner */}
      <div
        className="pipeline-hero"
        style={{
          marginTop: "0.75rem",
          padding: "0.85rem 1rem",
          background: isRunning
            ? "linear-gradient(90deg, rgba(59,130,246,0.12) 0%, rgba(30,41,59,0.5) 100%)"
            : isComplete
            ? "linear-gradient(90deg, rgba(16,185,129,0.12) 0%, rgba(30,41,59,0.5) 100%)"
            : "rgba(15,23,42,0.6)",
          border: isRunning
            ? "1px solid rgba(59,130,246,0.3)"
            : isComplete
            ? "1px solid rgba(16,185,129,0.3)"
            : "1px solid rgba(255,255,255,0.08)",
          borderRadius: "6px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.4rem", flexWrap: "wrap", gap: "0.5rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <span
              style={{
                fontSize: "0.75rem",
                fontWeight: "700",
                letterSpacing: "0.05em",
                color: isRunning ? "#60a5fa" : isComplete ? "#34d399" : "#94a3b8",
                textTransform: "uppercase",
              }}
            >
              {isRunning ? `CURRENT STAGE 0${activeIndex + 1}` : isComplete ? "FINAL VERDICT" : "PIPELINE STATUS"}
            </span>
            <span style={{ fontSize: "0.95rem", fontWeight: "700", color: "#f8fafc" }}>
              {activeStage?.name || (isComplete ? "Diagnosis Completed" : "Ready for Telemetry Ingestion")}
            </span>
          </div>

          {activeStage && (
            <span
              className="mono"
              style={{
                fontSize: "0.72rem",
                padding: "0.2rem 0.5rem",
                borderRadius: "4px",
                fontWeight: "600",
                background:
                  activeStage.authorityTone === "danger"
                    ? "rgba(239,68,68,0.2)"
                    : activeStage.authorityTone === "purple"
                    ? "rgba(168,85,247,0.2)"
                    : activeStage.authorityTone === "warning"
                    ? "rgba(245,158,11,0.2)"
                    : "rgba(59,130,246,0.2)",
                color:
                  activeStage.authorityTone === "danger"
                    ? "#f87171"
                    : activeStage.authorityTone === "purple"
                    ? "#c084fc"
                    : activeStage.authorityTone === "warning"
                    ? "#fbbf24"
                    : "#93c5fd",
                border: "1px solid rgba(255,255,255,0.1)",
              }}
            >
              {activeStage.authority}
            </span>
          )}
        </div>

        <p
          style={{
            margin: 0,
            fontSize: "0.85rem",
            lineHeight: "1.4",
            color: "rgba(255,255,255,0.85)",
            fontFamily: isRunning ? "monospace" : "inherit",
          }}
        >
          {activeStage?.detail || (isComplete ? "All deterministic validation and safety checks complete." : "Select a crash dump scenario and start FDIR analysis to inspect the full reasoning trace.")}
        </p>
      </div>

      {children}
    </section>
  );
}
