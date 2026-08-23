/*
 * FirstRunHero — guided empty state shown before an FDIR run.
 *
 * Replaces a screen of N/A with a clear orientation and a single primary
 * action. Every value shown (scenario id, fault type, provenance) is backend
 * state from the selected scenario; the CTA calls the same runAnalysis() the
 * header button does. Nothing here is simulated.
 */

import React from "react";
import { useSentinel } from "../../state/SentinelContext";
import { PROVENANCE_LABELS, normalizeProvenance } from "../../generated/contract";
import Icon from "./Icon";

const FLOW = [
  {
    n: "01",
    title: "Detect",
    desc: "Deterministic detectors flag out-of-nominal telemetry in the pre-fault window.",
  },
  {
    n: "02",
    title: "Investigate",
    desc: "Hypotheses are generated and ranked against propagation and evidence.",
  },
  {
    n: "03",
    title: "Validate physics",
    desc: "Each hypothesis is checked against simplified attitude, power and thermal models.",
  },
  {
    n: "04",
    title: "Recover",
    desc: "A safety-validated recovery plan is proposed for operator decision.",
  },
];

export default function FirstRunHero() {
  const { selectedScenario, runAnalysis, analysis } = useSentinel();
  const isRunning = analysis?.status === "RUNNING";
  const provenance = selectedScenario
    ? PROVENANCE_LABELS[
        normalizeProvenance(selectedScenario.provenance || selectedScenario.source_type)
      ] || "PROVENANCE UNKNOWN"
    : null;

  return (
    <section className="hero" aria-label="Start an FDIR analysis">
      <span className="hero__eyebrow">
        <Icon name="shield" size={12} />
        Ready for analysis
      </span>

      <h2 className="hero__title">
        Autonomous <em>fault diagnosis &amp; recovery</em> for the loaded incident
      </h2>

      <p className="hero__lede">
        {selectedScenario ? (
          <>
            Scenario <strong>{selectedScenario.scenario_id}</strong>
            {selectedScenario.fault_type ? (
              <> — {selectedScenario.fault_type}</>
            ) : null}{" "}
            is loaded{provenance ? <> ({provenance})</> : null}. Telemetry and
            detected anomalies are shown below. Run the FDIR pipeline to generate
            ranked hypotheses, physics validation and a recovery plan.
          </>
        ) : (
          <>Select a crash-dump scenario from the header to begin. Every value on
          this console is served by the SENTINEL backend.</>
        )}
      </p>

      <div className="hero__cta">
        <button
          type="button"
          className="btn btn--primary"
          onClick={runAnalysis}
          disabled={isRunning || !selectedScenario}
        >
          <Icon name="shield" size={13} />
          {isRunning ? "ANALYSIS RUNNING" : "RUN FDIR ANALYSIS"}
        </button>
        <span className="hero__hint">
          Deterministic detection &amp; physics run on load — no analysis has been
          streamed yet.
        </span>
      </div>

      <div className="hero__steps">
        {FLOW.map((step) => (
          <div key={step.n} className="hero-step">
            <span className="hero-step__num">{step.n}</span>
            <h3 className="hero-step__title">{step.title}</h3>
            <p className="hero-step__desc">{step.desc}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
