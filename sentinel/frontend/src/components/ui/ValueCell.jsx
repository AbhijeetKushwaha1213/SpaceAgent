/*
 * ValueCell — a labelled readout. When the backend supplied no value the
 * caller decides the placeholder (N/A, NOT AVAILABLE, NOT EVALUATED).
 */

import React from "react";

export default function ValueCell({ label, value, placeholder = "N/A", monospace = false, testId }) {
  const hasValue = value !== null && value !== undefined && value !== "";
  return (
    <div className="value-cell" data-testid={testId}>
      <dt className="value-cell__label">{label}</dt>
      <dd className={`value-cell__value ${monospace ? "value-cell__value--mono" : ""}`}>
        {hasValue ? value : placeholder}
      </dd>
    </div>
  );
}