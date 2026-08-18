/*
 * AsyncBlock — uniform loading / error / unavailable presentation for any
 * backend entity. Every view routes its data through this so states are
 * consistent: a loading entity shows a status line, a failed fetch shows the
 * error, a null payload shows "NOT AVAILABLE".
 */

import React from "react";

export default function AsyncBlock({ entity, loadingText = "LOADING...", children }) {
  if (entity?.loading) {
    return (
      <div className="async-state" role="status">
        <span className="async-state__spinner" aria-hidden="true" />
        <span>{loadingText}</span>
      </div>
    );
  }
  if (entity?.error) {
    return (
      <div className="async-state async-state--error" role="alert">
        <strong>BACKEND ERROR</strong>
        <span>{entity.error}</span>
      </div>
    );
  }
  if (!entity?.data) {
    return (
      <div className="async-state" role="status">
        <span>NOT AVAILABLE</span>
      </div>
    );
  }
  return children;
}