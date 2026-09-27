/*
 * Telemetry — real time-series plots of the canonical pre-fault window.
 *
 * Controls:
 *   - channel selection (channels present in the window)
 *   - subsystem filter (channel dictionary attribution; unknown stays UNKNOWN)
 *   - time range (trim the plotted window by relative time)
 *   - anomaly markers from POST /api/v1/detect
 *   - thresholds from the channel dictionary (nominal band, hard limits)
 *   - model predictions from the physics/state-estimation residuals
 *
 * Evidence links: when the investigation view jumps here with a
 * { channel, timestamp } focus, the channel is preselected and the sample
 * is highlighted.
 */

import React, { useEffect, useMemo, useState } from "react";
import { useSentinel } from "../../state/SentinelContext";
import { anomaliesForChannel, residualsForChannel, windowSamples, channelById } from "../../state/selectors";
import Panel from "../ui/Panel";
import AsyncBlock from "../ui/AsyncBlock";
import DataTable from "../ui/DataTable";
import TimeSeriesChart from "../ui/TimeSeriesChart";
import StatusBadge from "../ui/StatusBadge";
import ValueCell from "../ui/ValueCell";

export default function TelemetryView() {
  const {
    selectedScenario: scenario,
    channelDictionary,
    detection,
    physicsReport,
    focus,
    clearTelemetryFocus,
  } = useSentinel();

  const allSamples = useMemo(() => (scenario ? windowSamples(scenario) : []), [scenario]);

  const channels = useMemo(() => {
    const seen = new Map();
    for (const sample of allSamples) {
      if (!seen.has(sample.parameter)) seen.set(sample.parameter, sample);
    }
    return Array.from(seen.values()).sort((a, b) =>
      a.parameter.localeCompare(b.parameter)
    );
  }, [allSamples]);

  const [selectedChannel, setSelectedChannel] = useState(null);
  const [subsystemFilter, setSubsystemFilter] = useState("ALL");
  const [range, setRange] = useState(null);
  const [focusedSample, setFocusedSample] = useState(null);

  // Honour evidence-link focus (channel + timestamp jump).
  useEffect(() => {
    if (focus?.channel) {
      const exists = channels.some((c) => c.parameter === focus.channel);
      if (exists) {
        setSelectedChannel(focus.channel);
        if (focus.timestamp) {
          setFocusedSample({ timestamp: focus.timestamp });
        }
      }
      clearTelemetryFocus();
    }
    // eslint-disable-next-line -- focus is consumed once when set
  }, [focus?.channel, focus?.timestamp]);

  useEffect(() => {
    if (channels.length > 0) {
      setSelectedChannel((prev) =>
        prev && channels.some((c) => c.parameter === prev) ? prev : channels[0].parameter
      );
    }
  }, [channels]);

  const subsystems = useMemo(() => {
    const set = new Set();
    for (const c of channels) {
      const ch = channelById(channelDictionary, c.parameter);
      set.add(ch ? ch.subsystem : "UNKNOWN");
    }
    return ["ALL", ...Array.from(set).sort()];
  }, [channels, channelDictionary]);

  const filteredChannels = useMemo(() => {
    if (subsystemFilter === "ALL") return channels;
    return channels.filter((c) => {
      const ch = channelById(channelDictionary, c.parameter);
      return (ch ? ch.subsystem : "UNKNOWN") === subsystemFilter;
    });
  }, [channels, subsystemFilter, channelDictionary]);

  const activeChannel = selectedChannel || filteredChannels[0]?.parameter || null;
  const samples = useMemo(
    () => (activeChannel ? allSamples.filter((s) => s.parameter === activeChannel) : []),
    [allSamples, activeChannel]
  );

  const dict = channelById(channelDictionary, activeChannel);
  const unit = dict?.unit || samples[0]?.unit || "";
  const nominalMin = dict?.nominal_range?.[0] ?? null;
  const nominalMax = dict?.nominal_range?.[1] ?? null;
  const hardMin = dict?.hard_limits?.[0] ?? null;
  const hardMax = dict?.hard_limits?.[1] ?? null;

  const anomalies = activeChannel ? anomaliesForChannel(detection, activeChannel) : [];
  const predictions = useMemo(() => {
    if (!activeChannel) return [];
    const tByTimestamp = new Map();
    for (const s of allSamples) {
      if (!tByTimestamp.has(s.timestamp)) tByTimestamp.set(s.timestamp, s.t);
    }
    return residualsForChannel(physicsReport, activeChannel)
      .map((r) => ({
        t: tByTimestamp.get(r.from_timestamp) ?? null,
        predicted: r.predicted,
        observed: r.observed,
        residual: r.residual,
      }))
      .filter((p) => p.t !== null);
  }, [physicsReport, activeChannel, allSamples]);

  const sampleTimes = useMemo(
    () => samples.map((s) => s.t).filter((t) => typeof t === "number"),
    [samples]
  );
  const tMin = sampleTimes.length ? Math.min(...sampleTimes) : null;
  const tMax = sampleTimes.length ? Math.max(...sampleTimes) : null;

  const rangeOptions = useMemo(() => {
    if (tMin === null || tMax === null) return [];
    const opts = [{ label: "FULL WINDOW", value: null }];
    for (let i = 0; i < 4; i += 1) {
      const start = tMin + ((tMax - tMin) * i) / 4;
      opts.push({ label: `FROM t=${Number(start.toFixed(1))}`, value: start });
    }
    return opts;
  }, [tMin, tMax]);

  const focusTimestamp = focusedSample?.timestamp || null;

  return (
    <div className="view-stack">
      <div className="view-heading">
        <h1 className="view-heading__title">Telemetry</h1>
        <p className="view-heading__sub">
          Pre-fault telemetry window from the scenario catalogue, plotted with
          channel dictionary thresholds and detector findings.
        </p>
      </div>

      <div className="telemetry-layout">
        <Panel id="tl-channels" title="Channels" className="telemetry-layout__side">
          <label className="field-label" htmlFor="tl-subsystem">
            Subsystem filter
          </label>
          <select
            id="tl-subsystem"
            className="field-select field-select--block"
            value={subsystemFilter}
            onChange={(e) => setSubsystemFilter(e.target.value)}
          >
            {subsystems.map((s) => (
              <option key={s} value={s}>
                {s === "ALL" ? "ALL SUBSYSTEMS" : s}
              </option>
            ))}
          </select>

          <div className="channel-list" role="listbox" aria-label="Telemetry channels" aria-orientation="vertical">
            {filteredChannels.map((c) => {
              const ch = channelById(channelDictionary, c.parameter);
              const sub = ch ? ch.subsystem : "UNKNOWN";
              const count = allSamples.filter((s) => s.parameter === c.parameter).length;
              const isActive = activeChannel === c.parameter;
              return (
                <button
                  key={c.parameter}
                  type="button"
                  role="option"
                  aria-selected={isActive}
                  className={`channel-item ${isActive ? "channel-item--active" : ""}`}
                  onClick={() => setSelectedChannel(c.parameter)}
                >
                  <span className="channel-item__name mono">{c.parameter}</span>
                  <span className="channel-item__meta">
                    {sub} · {count} sample{count === 1 ? "" : "s"}
                  </span>
                </button>
              );
            })}
            {filteredChannels.length === 0 ? (
              <p className="muted-text">NO CHANNELS IN WINDOW FOR THIS FILTER</p>
            ) : null}
          </div>
        </Panel>

        <div className="telemetry-layout__main">
          <Panel
            id="tl-plot"
            title={activeChannel ? `Channel: ${activeChannel}` : "Channel"}
            status={activeChannel ? <StatusBadge status={dict?.criticality || "UNKNOWN"} label={`CRITICALITY ${dict?.criticality || "UNKNOWN"}`} /> : null}
            actions={
              <div className="toolbar">
                <label className="field-label" htmlFor="tl-range">
                  Time range
                </label>
                <select
                  id="tl-range"
                  className="field-select"
                  value={range ?? ""}
                  onChange={(e) => {
                    const v = e.target.value;
                    setRange(v === "" ? null : Number(v));
                  }}
                >
                  {rangeOptions.map((opt) => (
                    <option key={opt.label} value={opt.value ?? ""}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
            }
          >
            <AsyncBlock entity={detection} loadingText="RUNNING DETECTION PIPELINE...">
              {samples.length === 0 ? (
                <p className="muted-text">NO SAMPLES AVAILABLE FOR THIS CHANNEL</p>
              ) : (
                <TimeSeriesChart
                  samples={samples}
                  predictions={predictions}
                  anomalies={anomalies}
                  unit={unit}
                  nominalMin={nominalMin}
                  nominalMax={nominalMax}
                  hardMin={hardMin}
                  hardMax={hardMax}
                  range={range ? { min: range, max: tMax } : null}
                  focusTimestamp={focusTimestamp}
                  onSelectSample={(s) => setFocusedSample({ timestamp: s.timestamp })}
                />
              )}
            </AsyncBlock>

            <dl className="value-grid value-grid--4col">
              <ValueCell label="Unit" value={unit || null} monospace />
              <ValueCell label="Nominal range" value={nominalMin !== null && nominalMax !== null ? `${nominalMin} .. ${nominalMax}` : null} monospace />
              <ValueCell label="Hard limits" value={hardMin !== null || hardMax !== null ? `${hardMin ?? "N/A"} .. ${hardMax ?? "N/A"}` : null} monospace />
              <ValueCell label="Subsystem" value={dict?.subsystem || "UNKNOWN"} />
              <ValueCell label="Sampling rate" value={dict?.sampling_rate || null} />
              <ValueCell label="Value class" value={dict?.value_class || null} />
              <ValueCell label="Criticality" value={dict?.criticality || null} />
              <ValueCell label="Channel provenance" value={dict?.provenance || null} placeholder="NOT AVAILABLE" />
            </dl>
          </Panel>

          <Panel id="tl-readings" title="Raw readings">
            <DataTable
              caption={`Readings for channel ${activeChannel || ""}`}
              emptyMessage="NO READINGS AVAILABLE"
              columns={[
                { key: "timestamp", label: "Timestamp" },
                { key: "value", label: "Value" },
                { key: "status", label: "Status", render: (row) => <StatusBadge status={row.status} /> },
                { key: "nominalMin", label: "Nominal min" },
                { key: "nominalMax", label: "Nominal max" },
                { key: "detected", label: "Detector findings", render: (row) => (row.detected ? <StatusBadge status="ANOMALOUS" label="ANOMALOUS" /> : "NONE") },
              ]}
              rows={samples.map((s, i) => ({
                key: i,
                timestamp: s.timestamp,
                value: s.displayValue + (unit ? ` ${unit}` : ""),
                status: s.status,
                nominalMin: s.nominal_min ?? "N/A",
                nominalMax: s.nominal_max ?? "N/A",
                detected: anomalies.some((a) => a.timestamp === s.timestamp),
              }))}
              rowClass={(row) => (row.status === "CRITICAL" ? "row--critical" : row.status === "ANOMALOUS" || row.status === "WARNING" ? "row--warning" : "")}
            />
          </Panel>
        </div>
      </div>
    </div>
  );
}