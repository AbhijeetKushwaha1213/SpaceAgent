/*
 * GENERATED FILE — DO NOT EDIT BY HAND.
 * Source of truth: sentinel/backend/app/api/models.py
 * Regenerate:      python3 sentinel/backend/scripts/export_contracts.py
 * Verify:          python3 sentinel/backend/scripts/export_contracts.py --check
 *
 * Runtime constants shared by the SENTINEL frontend. Every vocabulary
 * here is derived from a backend enum, so a value the frontend compares
 * against cannot outlive the value the backend emits.
 */

export const CONTRACT_VERSION = "1.0.0";
export const API_VERSION = "v1";

// Versioned API paths. The frontend must not build these by hand.
export const API = {
  contract: "/api/v1/contract",
  health: "/api/v1/health",
  scenarios: "/api/v1/scenarios",
  detect: "/api/v1/detect",
  detectChannels: "/api/v1/detect/channels",
  analyze: "/api/v1/analyze",
};

// The canonical telemetry representation. Read this field, not the
// deprecated one — see app/api/adapters.py for why they were merged.
export const CANONICAL_TELEMETRY_FIELD = "pre_fault_telemetry_window";
export const DEPRECATED_TELEMETRY_FIELDS = ["pre_fault_telemetry"];

// Phase 4 audit trail. runs() and runVerify() build the paths so a
// client never concatenates a run id into a URL by hand.
export const AUDIT_API = {
  status: "/api/v1/audit/status",
  runs: "/api/v1/runs",
};

export function runPath(runId) {
  return `${AUDIT_API.runs}/${encodeURIComponent(runId)}`;
}

export function runVerifyPath(runId) {
  return `${runPath(runId)}/verify`;
}

export function runDecisionsPath(runId) {
  return `${runPath(runId)}/decisions`;
}

// Header carrying the run id on a POST /api/v1/analyze response. It is
// readable before the SSE body starts, so a client can record the run
// even if the stream then fails.
export const RUN_ID_HEADER = "X-Sentinel-Run-Id";

// Stages this build records as NOT_IMPLEMENTED on every run. A client
// must not read their absence as a check that passed.
//
// EMPTY as of Phase 8. state_estimation left the list in Phase 7, which
// added the simplified attitude, power and thermal models;
// physics_validation left it in Phase 8, which validates hypotheses
// against those models. Every stage in the enum now records a result.
//
// An empty list does NOT mean every check succeeds. A stage can be
// recorded DEGRADED (it ran and decided nothing) or FAILED, and a
// physics verdict can be UNCERTAIN. Read the per-run coverage map for
// what a given run actually concluded.
export const NOT_IMPLEMENTED_STAGES = Object.freeze([]);

// Phase 5 channel dictionary. Channel units, subsystems and limits are
// served from the backend; the frontend must not retype any of them.
export const CHANNEL_API = {
  channels: "/api/v1/channels",
  detectionView: "/api/v1/detect/channels",
};

export function channelPath(channelId) {
  return `${CHANNEL_API.channels}/${encodeURIComponent(channelId)}`;
}

// Subsystem shown for a channel the dictionary cannot attribute. Never
// inferred from the channel name.
export const UNKNOWN_SUBSYSTEM = "UNKNOWN";

export const ACTOR = Object.freeze({
  SYSTEM: "SYSTEM",
  OPERATOR: "OPERATOR",
});

export const ANALYSIS_STATUS = Object.freeze({
  complete: "complete",
  partial: "partial",
  timeout: "timeout",
  error: "error",
});

export const AUDIT_STAGE = Object.freeze({
  input: "input",
  detection: "detection",
  state_estimation: "state_estimation",
  rag: "rag",
  routing: "routing",
  llm: "llm",
  external_transmission: "external_transmission",
  hypotheses: "hypotheses",
  physics_validation: "physics_validation",
  safety_validation: "safety_validation",
  diagnosis: "diagnosis",
  operator_decision: "operator_decision",
});

export const BASELINE_SOURCE = Object.freeze({
  OBSERVED_PROVIDED: "OBSERVED_PROVIDED",
  OBSERVED_WINDOW: "OBSERVED_WINDOW",
  RANGE_DERIVED: "RANGE_DERIVED",
  NONE: "NONE",
});

export const BLOCK_SEVERITY = Object.freeze({
  CRITICAL: "CRITICAL",
  HIGH: "HIGH",
  MEDIUM: "MEDIUM",
  LOW: "LOW",
});

export const CHANNEL_PROVENANCE = Object.freeze({
  REPO_DETECTOR_TABLE: "REPO_DETECTOR_TABLE",
  REPO_SIMULATOR_TABLE: "REPO_SIMULATOR_TABLE",
  REPO_SCENARIO_DATA: "REPO_SCENARIO_DATA",
  SENTINEL_SAFETY_POLICY: "SENTINEL_SAFETY_POLICY",
  SENTINEL_CLASSIFICATION: "SENTINEL_CLASSIFICATION",
  UNKNOWN: "UNKNOWN",
});

export const CHECK_FAMILY = Object.freeze({
  PHYSICAL_CONSISTENCY: "PHYSICAL_CONSISTENCY",
  TELEMETRY_CONSISTENCY: "TELEMETRY_CONSISTENCY",
  STATE_TRANSITION_CONSISTENCY: "STATE_TRANSITION_CONSISTENCY",
  ACTUATOR_FEASIBILITY: "ACTUATOR_FEASIBILITY",
  SENSOR_CONSISTENCY: "SENSOR_CONSISTENCY",
  ENERGY_CONSISTENCY: "ENERGY_CONSISTENCY",
  THERMAL_CONSISTENCY: "THERMAL_CONSISTENCY",
});

export const CHECK_OUTCOME = Object.freeze({
  PASS: "PASS",
  FAIL: "FAIL",
  NOT_APPLICABLE: "NOT_APPLICABLE",
  INDETERMINATE: "INDETERMINATE",
});

export const CONFIDENCE = Object.freeze({
  HIGH: "HIGH",
  MEDIUM: "MEDIUM",
  LOW: "LOW",
});

export const CRITICALITY = Object.freeze({
  CRITICAL: "CRITICAL",
  HIGH: "HIGH",
  MEDIUM: "MEDIUM",
  LOW: "LOW",
});

export const DATA_TYPE = Object.freeze({
  FLOAT: "FLOAT",
  INT: "INT",
  BOOL: "BOOL",
  BITMASK: "BITMASK",
  ENUM: "ENUM",
});

export const DETECTOR_NAME = Object.freeze({
  HARD_LIMIT: "HARD_LIMIT",
  DISCRETE_STATE: "DISCRETE_STATE",
  COUNTER: "COUNTER",
  DATA_QUALITY: "DATA_QUALITY",
  ZSCORE: "ZSCORE",
  ROBUST_ZSCORE: "ROBUST_ZSCORE",
  RATE_OF_CHANGE: "RATE_OF_CHANGE",
  TREND: "TREND",
  PERSISTENCE: "PERSISTENCE",
  SUDDEN_CHANGE: "SUDDEN_CHANGE",
});

export const OPERATOR_DECISION_TYPE = Object.freeze({
  ACKNOWLEDGED: "ACKNOWLEDGED",
  APPROVED: "APPROVED",
  REJECTED: "REJECTED",
  MODIFIED: "MODIFIED",
  ESCALATED: "ESCALATED",
  EXECUTED: "EXECUTED",
  DEFERRED: "DEFERRED",
  COMMENT: "COMMENT",
});

export const PHYSICS_STATUS = Object.freeze({
  VALID: "VALID",
  INVALID: "INVALID",
  UNCERTAIN: "UNCERTAIN",
});

export const PROVENANCE = Object.freeze({
  REAL: "REAL",
  SYNTHETIC: "SYNTHETIC",
  SYNTHETIC_FROM_REAL_METADATA: "SYNTHETIC_FROM_REAL_METADATA",
  DEMO: "DEMO",
  UNKNOWN: "UNKNOWN",
});

export const RISK_LEVEL = Object.freeze({
  LOW: "LOW",
  MEDIUM: "MEDIUM",
  HIGH: "HIGH",
  BLOCKED: "BLOCKED",
});

export const RUN_STATUS = Object.freeze({
  IN_PROGRESS: "IN_PROGRESS",
  COMPLETED: "COMPLETED",
  FAILED: "FAILED",
  ABANDONED: "ABANDONED",
});

export const SSEEVENT_TYPE = Object.freeze({
  thought: "thought",
  action: "action",
  observation: "observation",
  result: "result",
  error: "error",
  status: "status",
});

export const SAFETY_STATUS = Object.freeze({
  NOT_VALIDATED: "NOT_VALIDATED",
  VALIDATED: "VALIDATED",
  PARTIALLY_BLOCKED: "PARTIALLY_BLOCKED",
  BLOCKED: "BLOCKED",
  REQUIRES_HUMAN_REVIEW: "REQUIRES_HUMAN_REVIEW",
});

export const SAMPLING_RATE = Object.freeze({
  HIGH_RATE: "HIGH_RATE",
  MEDIUM_RATE: "MEDIUM_RATE",
  LOW_RATE: "LOW_RATE",
  ON_CHANGE: "ON_CHANGE",
  UNKNOWN: "UNKNOWN",
});

export const SEVERITY = Object.freeze({
  CRITICAL: "CRITICAL",
  HIGH: "HIGH",
  MEDIUM: "MEDIUM",
  LOW: "LOW",
  INFO: "INFO",
});

export const STAGE_STATUS = Object.freeze({
  OK: "OK",
  DEGRADED: "DEGRADED",
  FAILED: "FAILED",
  SKIPPED: "SKIPPED",
  NOT_RUN: "NOT_RUN",
  NOT_IMPLEMENTED: "NOT_IMPLEMENTED",
});

export const SUBSYSTEM = Object.freeze({
  EPS: "EPS",
  AOCS: "AOCS",
  TCS: "TCS",
  OBC: "OBC",
  COMMS: "COMMS",
  PYLD: "PYLD",
  UNKNOWN: "UNKNOWN",
});

export const SUBSYSTEM_ID = Object.freeze({
  ADCS: "ADCS",
  EPS: "EPS",
  OBC: "OBC",
  TCS: "TCS",
  COMMS: "COMMS",
  PYLD: "PYLD",
  SYSTEM: "SYSTEM",
});

export const TELEMETRY_STATUS = Object.freeze({
  NOMINAL: "NOMINAL",
  WARNING: "WARNING",
  ANOMALOUS: "ANOMALOUS",
  CRITICAL: "CRITICAL",
  NOMINAL_CONTEXT: "NOMINAL_CONTEXT",
  LABELLED_ANOMALY: "LABELLED_ANOMALY",
  UNKNOWN: "UNKNOWN",
});

export const VALUE_CLASS = Object.freeze({
  CONTINUOUS: "CONTINUOUS",
  COUNTER: "COUNTER",
  STATUS: "STATUS",
  FLAG: "FLAG",
});

// Provenance code -> operator-facing label, derived from
// app/api/provenance.py. A code missing from this map resolves to
// UNKNOWN rather than to REAL.
export const PROVENANCE_LABELS = Object.freeze({
  DEMO: "DEMO / REPLAY",
  REAL: "REAL ESA TELEMETRY",
  SYNTHETIC: "SYNTHETIC DATA",
  SYNTHETIC_FROM_REAL_METADATA: "SYNTHETIC FROM REAL METADATA",
  UNKNOWN: "PROVENANCE UNKNOWN",
});

export function normalizeProvenance(code) {
  return Object.prototype.hasOwnProperty.call(PROVENANCE_LABELS, code)
    ? code
    : PROVENANCE.UNKNOWN;
}

// True only when the numeric telemetry itself came from a mission
// dataset. Real identifiers or real anomaly labels are not sufficient.
export function isRealProvenance(code) {
  return normalizeProvenance(code) === PROVENANCE.REAL;
}
