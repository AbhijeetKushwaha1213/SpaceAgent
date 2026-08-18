/*
 * Endpoint catalog for the SENTINEL operator console.
 *
 * Paths mirror sentinel/backend/app/main.py. Anything served under /api/v1
 * is validated against a Pydantic response_model; the frontend consumes
 * exactly those payloads and nothing else.
 *
 * Versioned paths already declared in the generated contract are imported
 * from src/generated/contract.js so they cannot drift.
 */

import {
  API as CONTRACT_API,
  AUDIT_API,
  CHANNEL_API,
  runDecisionsPath,
  runPath,
  runVerifyPath,
} from "../generated/contract";

export const ENDPOINTS = Object.freeze({
  health: CONTRACT_API.health,
  contract: CONTRACT_API.contract,
  scenarios: CONTRACT_API.scenarios,
  systemStatus: "/api/v1/system/status",
  channels: CHANNEL_API.channels,
  detectChannels: CHANNEL_API.detectionView,
  detect: CONTRACT_API.detect,
  physics: "/api/v1/physics",
  physicsConstraints: "/api/v1/physics/constraints",
  analyze: CONTRACT_API.analyze,
  auditStatus: AUDIT_API.status,
  runs: AUDIT_API.runs,
  evaluationResults: "/api/v1/evaluation/results",
});

export function runDetailPath(runId) {
  return runPath(runId);
}

export function runVerify(runId) {
  return runVerifyPath(runId);
}

export function runDecisions(runId) {
  return runDecisionsPath(runId);
}
