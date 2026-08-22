/*
 * GENERATED FILE — DO NOT EDIT BY HAND.
 * Source of truth: sentinel/backend/app/api/models.py
 * Regenerate:      python3 sentinel/backend/scripts/export_contracts.py
 * Verify:          python3 sentinel/backend/scripts/export_contracts.py --check
 */

export declare const CONTRACT_VERSION: "1.0.0";
export declare const API_VERSION: "v1";

export declare const API: Readonly<{
  contract: string;
  health: string;
  scenarios: string;
  detect: string;
  detectChannels: string;
  analyze: string;
}>;

export declare const CANONICAL_TELEMETRY_FIELD: "pre_fault_telemetry_window";
export declare const DEPRECATED_TELEMETRY_FIELDS: readonly string[];

export declare const AUDIT_API: Readonly<{
  status: string;
  runs: string;
}>;
export declare function runPath(runId: string): string;
export declare function runVerifyPath(runId: string): string;
export declare function runDecisionsPath(runId: string): string;
export declare const RUN_ID_HEADER: "X-Sentinel-Run-Id";
export declare const NOT_IMPLEMENTED_STAGES: readonly string[];

export declare const CHANNEL_API: Readonly<{
  channels: string;
  detectionView: string;
}>;
export declare function channelPath(channelId: string): string;
export declare const UNKNOWN_SUBSYSTEM: "UNKNOWN";

export type Actor = "SYSTEM" | "OPERATOR";
export declare const ACTOR: Readonly<Record<Actor, Actor>>;

export type AnalysisStatus = "complete" | "partial" | "timeout" | "error";
export declare const ANALYSIS_STATUS: Readonly<Record<AnalysisStatus, AnalysisStatus>>;

export type AuditStage = "input" | "detection" | "state_estimation" | "rag" | "routing" | "llm" | "external_transmission" | "hypotheses" | "physics_validation" | "safety_validation" | "diagnosis" | "operator_decision";
export declare const AUDIT_STAGE: Readonly<Record<AuditStage, AuditStage>>;

export type BaselineSource = "OBSERVED_PROVIDED" | "OBSERVED_WINDOW" | "RANGE_DERIVED" | "NONE";
export declare const BASELINE_SOURCE: Readonly<Record<BaselineSource, BaselineSource>>;

export type BlockSeverity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export declare const BLOCK_SEVERITY: Readonly<Record<BlockSeverity, BlockSeverity>>;

export type ChannelProvenance = "REPO_DETECTOR_TABLE" | "REPO_SIMULATOR_TABLE" | "REPO_SCENARIO_DATA" | "SENTINEL_SAFETY_POLICY" | "SENTINEL_CLASSIFICATION" | "UNKNOWN";
export declare const CHANNEL_PROVENANCE: Readonly<Record<ChannelProvenance, ChannelProvenance>>;

export type CheckFamily = "PHYSICAL_CONSISTENCY" | "TELEMETRY_CONSISTENCY" | "STATE_TRANSITION_CONSISTENCY" | "ACTUATOR_FEASIBILITY" | "SENSOR_CONSISTENCY" | "ENERGY_CONSISTENCY" | "THERMAL_CONSISTENCY";
export declare const CHECK_FAMILY: Readonly<Record<CheckFamily, CheckFamily>>;

export type CheckOutcome = "PASS" | "FAIL" | "NOT_APPLICABLE" | "INDETERMINATE";
export declare const CHECK_OUTCOME: Readonly<Record<CheckOutcome, CheckOutcome>>;

export type Confidence = "HIGH" | "MEDIUM" | "LOW";
export declare const CONFIDENCE: Readonly<Record<Confidence, Confidence>>;

export type Criticality = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export declare const CRITICALITY: Readonly<Record<Criticality, Criticality>>;

export type DataType = "FLOAT" | "INT" | "BOOL" | "BITMASK" | "ENUM";
export declare const DATA_TYPE: Readonly<Record<DataType, DataType>>;

export type DetectorName = "HARD_LIMIT" | "DISCRETE_STATE" | "COUNTER" | "DATA_QUALITY" | "ZSCORE" | "ROBUST_ZSCORE" | "RATE_OF_CHANGE" | "TREND" | "PERSISTENCE" | "SUDDEN_CHANGE";
export declare const DETECTOR_NAME: Readonly<Record<DetectorName, DetectorName>>;

export type OperatorDecisionType = "ACKNOWLEDGED" | "APPROVED" | "REJECTED" | "MODIFIED" | "ESCALATED" | "EXECUTED" | "DEFERRED" | "COMMENT";
export declare const OPERATOR_DECISION_TYPE: Readonly<Record<OperatorDecisionType, OperatorDecisionType>>;

export type PhysicsStatus = "VALID" | "INVALID" | "UNCERTAIN";
export declare const PHYSICS_STATUS: Readonly<Record<PhysicsStatus, PhysicsStatus>>;

export type Provenance = "REAL" | "SYNTHETIC" | "SYNTHETIC_FROM_REAL_METADATA" | "DEMO" | "UNKNOWN";
export declare const PROVENANCE: Readonly<Record<Provenance, Provenance>>;

export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "BLOCKED";
export declare const RISK_LEVEL: Readonly<Record<RiskLevel, RiskLevel>>;

export type RunStatus = "IN_PROGRESS" | "COMPLETED" | "FAILED" | "ABANDONED";
export declare const RUN_STATUS: Readonly<Record<RunStatus, RunStatus>>;

export type SSEEventType = "thought" | "action" | "observation" | "result" | "error" | "status";
export declare const SSEEVENT_TYPE: Readonly<Record<SSEEventType, SSEEventType>>;

export type SafetyStatus = "NOT_VALIDATED" | "VALIDATED" | "PARTIALLY_BLOCKED" | "BLOCKED" | "REQUIRES_HUMAN_REVIEW";
export declare const SAFETY_STATUS: Readonly<Record<SafetyStatus, SafetyStatus>>;

export type SamplingRate = "HIGH_RATE" | "MEDIUM_RATE" | "LOW_RATE" | "ON_CHANGE" | "UNKNOWN";
export declare const SAMPLING_RATE: Readonly<Record<SamplingRate, SamplingRate>>;

export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "INFO";
export declare const SEVERITY: Readonly<Record<Severity, Severity>>;

export type StageStatus = "OK" | "DEGRADED" | "FAILED" | "SKIPPED" | "NOT_RUN" | "NOT_IMPLEMENTED";
export declare const STAGE_STATUS: Readonly<Record<StageStatus, StageStatus>>;

export type Subsystem = "EPS" | "AOCS" | "TCS" | "OBC" | "COMMS" | "PYLD" | "UNKNOWN";
export declare const SUBSYSTEM: Readonly<Record<Subsystem, Subsystem>>;

export type SubsystemID = "ADCS" | "EPS" | "OBC" | "TCS" | "COMMS" | "PYLD" | "SYSTEM";
export declare const SUBSYSTEM_ID: Readonly<Record<SubsystemID, SubsystemID>>;

export type TelemetryStatus = "NOMINAL" | "WARNING" | "ANOMALOUS" | "CRITICAL" | "NOMINAL_CONTEXT" | "LABELLED_ANOMALY" | "UNKNOWN";
export declare const TELEMETRY_STATUS: Readonly<Record<TelemetryStatus, TelemetryStatus>>;

export type ValueClass = "CONTINUOUS" | "COUNTER" | "STATUS" | "FLAG";
export declare const VALUE_CLASS: Readonly<Record<ValueClass, ValueClass>>;

export declare const PROVENANCE_LABELS: Readonly<Record<Provenance, string>>;
export declare function normalizeProvenance(code: unknown): Provenance;
export declare function isRealProvenance(code: unknown): boolean;

/** Canonical telemetry reading — one channel at one time step. */
export interface TelemetryEntry {
  timestamp: string;
  parameter: string;
  relative_time_s: number | null;
  value: number | null;
  value_text: string | null;
  unit: string | null;
  status: TelemetryStatus;
  anomalous: boolean | null;
  nominal_min: number | null;
  nominal_max: number | null;
  baseline_mean: number | null;
  baseline_std: number | null;
}

export interface Scenario {
  scenario_id: number | null;
  fault_type: string | null;
  provenance: Provenance;
  source_type: string;
  source_note: string | null;
  pre_fault_telemetry_window: TelemetryEntry[] | null;
  [key: string]: unknown;
}

export interface ScenarioListResponse {
  contract_version: string;
  api_version: string;
  count: number;
  scenarios: Scenario[];
}
