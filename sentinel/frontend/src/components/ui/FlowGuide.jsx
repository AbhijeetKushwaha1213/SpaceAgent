/*
 * FlowGuide — a slim breadcrumb of the primary operator journey.
 *
 * The full tab bar exposes every console section; this strip highlights the
 * core recovery flow (Overview → Investigation → Physics → Recovery) so the
 * operator always knows where they are in that path. Clicking a step navigates
 * via the same handler the tabs use.
 */

import React from "react";

const FLOW_STEPS = [
  { id: "overview", label: "Overview" },
  { id: "investigation", label: "Investigate" },
  { id: "physics", label: "Physics" },
  { id: "recovery", label: "Recovery" },
];

export default function FlowGuide({ activeTab, onSelectTab }) {
  const activeIndex = FLOW_STEPS.findIndex((s) => s.id === activeTab);

  return (
    <nav className="flow-guide" aria-label="Recovery workflow">
      {FLOW_STEPS.map((step, i) => {
        const isActive = step.id === activeTab;
        // "done" is a position cue within the linear flow, not a claim that the
        // stage's analysis succeeded — it marks steps before the current one.
        const isBefore = activeIndex > -1 && i < activeIndex;
        const cls = isActive
          ? "flow-guide__step flow-guide__step--active"
          : isBefore
          ? "flow-guide__step flow-guide__step--done"
          : "flow-guide__step";
        return (
          <React.Fragment key={step.id}>
            {i > 0 ? (
              <span className="flow-guide__sep" aria-hidden="true">
                ›
              </span>
            ) : null}
            <button
              type="button"
              className={cls}
              aria-current={isActive ? "step" : undefined}
              onClick={() => onSelectTab(step.id)}
            >
              {step.label}
            </button>
          </React.Fragment>
        );
      })}
    </nav>
  );
}
