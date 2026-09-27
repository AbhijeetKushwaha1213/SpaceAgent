/*
 * Pure selector helpers over backend payloads.
 *
 * These functions only read fields the backend emits. When a field is absent
 * they return null / "N/A" rather than a fabricated value.
 */

import { CANONICAL_TELEMETRY_FIELD } from "../generated/contract";

// ── channel dictionary ───────────────────────────────────────────────────

export function channelById(channelDictionary, channelId) {
  const list = channelDictionary?.data?.channels || [];
  if (!channelId) return null;
  const exact = list.find((c) => c.channel_id === channelId);
  if (exact) return exact;
  return (
    list.find((c) => (c.aliases || []).includes(channelId)) || null
  );
}

export function subsystemForChannel(channelDictionary, channelId) {
  const ch = channelById(channelDictionary, channelId);
  return ch ? ch.subsystem : "UNKNOWN";
}

// ── canonical telemetry window ───────────────────────────────────────────

export function canonicalWindow(scenario) {
  return scenario?.[CANONICAL_TELEMETRY_FIELD] || [];
}

/*
 * Normalize a window into a flat list of samples with a numeric x position:
 *   relative_time_s when present, otherwise the sample index (negative,
 *   counting back from the last sample) so ordering is still meaningful.
 */
export function windowSamples(scenario) {
  const window = canonicalWindow(scenario);
  const last = window.length - 1;
  return window.map((entry, index) => {
    const t =
      typeof entry.relative_time_s === "number"
        ? entry.relative_time_s
        : index - last;
    return {
      ...entry,
      t,
      xIndex: index,
      numericValue:
        typeof entry.value === "number" ? entry.value : null,
      displayValue:
        entry.value_text || (entry.value === null ? "MISSING" : String(entry.value)),
    };
  });
}

export function channelsInWindow(scenario) {
  const seen = new Map();
  for (const sample of windowSamples(scenario)) {
    if (!seen.has(sample.parameter)) {
      seen.set(sample.parameter, sample);
    }
  }
  return Array.from(seen.values());
}

// ── subsystem grouping (only for known channels; unknown stays UNKNOWN) ──

export function subsystemCounts(window, channelDictionary) {
  const counts = {};
  for (const entry of window) {
    const sub = subsystemForChannel(channelDictionary, entry.parameter);
    counts[sub] = (counts[sub] || 0) + 1;
  }
  return counts;
}

// ── detection report helpers ─────────────────────────────────────────────

export function anomaliesForChannel(detection, channelId) {
  const list = detection?.data?.anomalies || [];
  return list.filter((a) => a.channel === channelId);
}

export function maxAnomalySeverity(detection) {
  const report = detection?.data;
  return report?.max_severity || null;
}

// ── physics helpers ──────────────────────────────────────────────────────

export function residualsForChannel(physicsReport, channelId) {
  const verdicts = physicsReport?.data?.verdicts || [];
  const out = [];
  for (const verdict of verdicts) {
    for (const residual of verdict.supporting_residuals || []) {
      if (residual.channel === channelId) {
        out.push({ ...residual, hypothesis_id: verdict.hypothesis_id });
      }
    }
  }
  return out;
}

// ── analysis output (SentinelOutput from SSE result or loaded audit run) ─

export function hypotheses(analysis, selectedRun) {
  if (analysis?.output?.hypotheses?.length > 0) return analysis.output.hypotheses;
  const diag = stageSummary(selectedRun?.data, "diagnosis");
  return diag?.payload?.sentinel_output?.hypotheses || [];
}

export function recoveryPlan(analysis, selectedRun) {
  if (analysis?.output?.recovery_plan?.length > 0) return analysis.output.recovery_plan;
  const diag = stageSummary(selectedRun?.data, "diagnosis");
  return (
    diag?.payload?.sentinel_output?.recovery_plan ||
    diag?.payload?.recommended_actions ||
    []
  );
}

export function blockedCommands(analysis, selectedRun) {
  if (analysis?.output?.blocked_steps?.length > 0) return analysis.output.blocked_steps;
  const diag = stageSummary(selectedRun?.data, "diagnosis");
  return diag?.payload?.sentinel_output?.blocked_steps || [];
}

export function reasoningSummary(analysis, selectedRun) {
  if (analysis?.output?.reasoning_summary) return analysis.output.reasoning_summary;
  const diag = stageSummary(selectedRun?.data, "diagnosis");
  return diag?.payload?.sentinel_output?.reasoning_summary || null;
}

export function safetyStatus(analysis, selectedRun) {
  if (analysis?.output?.safety_status) return analysis.output.safety_status;
  const diag = stageSummary(selectedRun?.data, "diagnosis");
  return (
    diag?.payload?.sentinel_output?.safety_status ||
    diag?.payload?.safety_status ||
    null
  );
}

// ── audit helpers ────────────────────────────────────────────────────────

export function stageEntries(record) {
  return record?.entries || [];
}

export function stageSummary(record, stageName) {
  const entries = stageEntries(record);
  const matches = entries.filter((e) => e.stage === stageName);
  return matches.length > 0 ? matches[matches.length - 1] : null;
}

// ── generic display helpers ──────────────────────────────────────────────

export function fmt(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "N/A";
  if (typeof value === "number") {
    if (Number.isNaN(value) || !Number.isFinite(value)) return "N/A";
    return Number(value.toFixed(digits)).toString();
  }
  return String(value);
}

export function fmtPct(value, digits = 1) {
  if (typeof value !== "number" || Number.isNaN(value)) return "N/A";
  return `${(value * 100).toFixed(digits)}%`;
}
