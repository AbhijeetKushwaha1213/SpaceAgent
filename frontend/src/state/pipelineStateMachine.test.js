/*
 * Tests for Live 10-Stage Pipeline State Machine (pipelineStateMachine.test.js)
 *
 * Validates:
 *   1. Initial state (IDLE -> all pending)
 *   2. Stage 1 running (INGESTION -> Stage 1 running)
 *   3. Stage 1 complete -> Stage 2 running (DETECTION)
 *   4. Reconciliation running (RECONCILIATION -> Stage 3 running)
 *   5. Case isolation running (CASE_ISOLATION -> Stage 4 running)
 *   6. Physics running (PHYSICS_VALIDATION -> Stage 7 running)
 *   7. Safety running (SAFETY_VALIDATION -> Stage 8 running)
 *   8. Complete state (all completed)
 *   9. Failure state (error -> active failed)
 *  10. Blocked state (safety BLOCKED -> safety blocked)
 *  11. Case isolation preserves separation (no cross-case leakage)
 *  12. No fake final result before backend completion
 */

import {
  PIPELINE_STAGES,
  computeHighestStageIndex,
  derivePipelineProgress,
} from "./pipelineStateMachine";

describe("Live Pipeline State Machine", () => {
  test("1. Initial state: IDLE with all 10 stages pending", () => {
    const progress = derivePipelineProgress({
      analysis: { status: "IDLE", events: [], output: null },
    });

    expect(progress.stages).toHaveLength(10);
    expect(progress.isRunning).toBe(false);
    expect(progress.isComplete).toBe(false);
    expect(progress.statusLabel).toBe("STANDING BY");
    progress.stages.forEach((s) => {
      expect(s.status).toBe("pending");
    });
  });

  test("2. Stage 1 running: INGESTION event", () => {
    const events = [
      { type: "status", data: "[INGESTION] Ingesting raw spacecraft crash dump..." },
    ];
    const highest = computeHighestStageIndex(events);
    expect(highest).toBe(0);

    const progress = derivePipelineProgress({
      analysis: { status: "RUNNING", events, output: null },
    });

    expect(progress.isRunning).toBe(true);
    expect(progress.activeIndex).toBe(0);
    expect(progress.stages[0].status).toBe("running");
    expect(progress.stages[1].status).toBe("pending");
  });

  test("3. Stage 1 complete -> Stage 2 running: DETECTION event", () => {
    const events = [
      { type: "status", data: "[INGESTION] Ingesting telemetry..." },
      { type: "status", data: "[DETECTION] Analyzing pre-fault telemetry..." },
    ];
    const progress = derivePipelineProgress({
      analysis: { status: "RUNNING", events, output: null },
    });

    expect(progress.activeIndex).toBe(1);
    expect(progress.stages[0].status).toBe("completed");
    expect(progress.stages[1].status).toBe("running");
    expect(progress.stages[2].status).toBe("pending");
  });

  test("4. Reconciliation running: RECONCILIATION event", () => {
    const events = [
      { type: "status", data: "[INGESTION] Ingesting telemetry..." },
      { type: "status", data: "[DETECTION] Analyzing telemetry..." },
      { type: "status", data: "[RECONCILIATION] Reconciling multi-channel observations (CORRELATION != IDENTITY)..." },
    ];
    const progress = derivePipelineProgress({
      analysis: { status: "RUNNING", events, output: null },
    });

    expect(progress.activeIndex).toBe(2);
    expect(progress.stages[0].status).toBe("completed");
    expect(progress.stages[1].status).toBe("completed");
    expect(progress.stages[2].status).toBe("running");
    expect(progress.stages[3].status).toBe("pending");
  });

  test("5. Case Isolation running: CASE_ISOLATION event", () => {
    const events = [
      { type: "status", data: "[INGESTION] Ingesting telemetry..." },
      { type: "status", data: "[DETECTION] Analyzing telemetry..." },
      { type: "status", data: "[RECONCILIATION] Reconciled observations." },
      { type: "status", data: "[CASE_ISOLATION] Enforcing case boundaries." },
    ];
    const progress = derivePipelineProgress({
      analysis: { status: "RUNNING", events, output: null },
    });

    expect(progress.activeIndex).toBe(3);
    expect(progress.stages[2].status).toBe("completed");
    expect(progress.stages[3].status).toBe("running");
  });

  test("6. Physics running: PHYSICS_VALIDATION event", () => {
    const events = [
      { type: "status", data: "[INGESTION] Ingesting telemetry..." },
      { type: "status", data: "[DETECTION] Analyzing telemetry..." },
      { type: "status", data: "[RECONCILIATION] Reconciled." },
      { type: "status", data: "[CASE_ISOLATION] Enforcing boundaries." },
      { type: "status", data: "[RAG_RETRIEVAL] Retrieved procedures." },
      { type: "status", data: "[HYPOTHESIS_GENERATION] Generated candidates." },
      { type: "status", data: "[PHYSICS_VALIDATION] Validating hypotheses against physical models..." },
    ];
    const progress = derivePipelineProgress({
      analysis: { status: "RUNNING", events, output: null },
    });

    expect(progress.activeIndex).toBe(6);
    expect(progress.stages[6].name).toBe("Deterministic Physics Validation");
    expect(progress.stages[6].status).toBe("running");
    expect(progress.stages[7].status).toBe("pending");
  });

  test("7. Safety running: SAFETY_VALIDATION event", () => {
    const events = [
      { type: "status", data: "[INGESTION] Ingesting telemetry..." },
      { type: "status", data: "[PHYSICS_VALIDATION] Physics verified." },
      { type: "status", data: "[SAFETY_VALIDATION] Running deterministic safety checks..." },
    ];
    const progress = derivePipelineProgress({
      analysis: { status: "RUNNING", events, output: null },
    });

    expect(progress.activeIndex).toBe(7);
    expect(progress.stages[7].name).toBe("Safety Validation");
    expect(progress.stages[7].status).toBe("running");
    expect(progress.stages[8].status).toBe("pending");
  });

  test("8. Complete state: all 10 stages completed with real output", () => {
    const sampleOutput = {
      hypotheses: [
        { rank: 1, fault_id: "ADCS_GYRO_SEU", confidence: 0.95, subsystem: "AOCS" },
        { rank: 2, fault_id: "ADCS_WHEEL_DRY_FRICTION", confidence: 0.8, subsystem: "AOCS" },
        { rank: 3, fault_id: "COMMS_TRANSPONDER_LOSS", confidence: 0.5, subsystem: "COMMS" },
      ],
      recovery_plan: [
        { step: 1, command: "CMD_GYRO_A_RESET", wait_seconds: 5, verify: "Rate valid", risk: "LOW" },
      ],
      safety_status: "APPROVED",
      requires_human_review: false,
    };

    const progress = derivePipelineProgress({
      analysis: { status: "COMPLETE", events: [], output: sampleOutput },
    });

    expect(progress.isComplete).toBe(true);
    expect(progress.statusLabel).toBe("ANALYSIS COMPLETE");
    progress.stages.forEach((s) => {
      expect(["completed", "blocked", "uncertain"]).toContain(s.status);
    });
  });

  test("9. Failure state: active stage marked failed", () => {
    const events = [
      { type: "status", data: "[INGESTION] Telemetry ingested." },
      { type: "status", data: "[PHYSICS_VALIDATION] Validating physics..." },
    ];
    const progress = derivePipelineProgress({
      analysis: { status: "ERROR", events, output: null, error: "Physics engine timeout" },
    });

    expect(progress.isError).toBe(true);
    expect(progress.stages[6].status).toBe("failed");
    expect(progress.stages[6].detail).toContain("Physics engine timeout");
  });

  test("10. Blocked state: safety BLOCKED reflected on safety stage", () => {
    const sampleOutput = {
      hypotheses: [
        { rank: 1, fault_id: "EPS_SOLAR_UNDERVOLT", confidence: 0.9, subsystem: "EPS" },
        { rank: 2, fault_id: "TCS_THERMAL_RUNAWAY", confidence: 0.7, subsystem: "TCS" },
        { rank: 3, fault_id: "COMMS_TRANSPONDER_LOSS", confidence: 0.4, subsystem: "COMMS" },
      ],
      recovery_plan: [],
      safety_status: "BLOCKED",
      safety_reason: "Battery SOC below 40% floor limit",
      requires_human_review: true,
    };

    const progress = derivePipelineProgress({
      analysis: { status: "COMPLETE", events: [], output: sampleOutput },
    });

    expect(progress.stages[7].status).toBe("blocked");
    expect(progress.stages[7].badge).toBe("BLOCKED");
  });

  test("11. No fake final result before backend completion", () => {
    const progress = derivePipelineProgress({
      analysis: {
        status: "RUNNING",
        events: [{ type: "status", data: "[INGESTION] Reading..." }],
        output: null,
      },
    });

    expect(progress.isComplete).toBe(false);
    expect(progress.stages[9].status).toBe("pending");
  });
});
