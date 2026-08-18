/*
 * Inline SVG icon set. No emoji on operational surfaces; these glyphs are
 * monochrome and inherit currentColor.
 */

import React from "react";

const PATHS = {
  check: <path d="M4 12l5 5 11-11" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />,
  cross: <path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />,
  warn: (
    <React.Fragment>
      <path d="M12 3L2 20h20L12 3z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="square" />
      <path d="M12 9v5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />
      <path d="M12 17.5v.01" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />
    </React.Fragment>
  ),
  unknown: (
    <React.Fragment>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M9.5 9.5a2.6 2.6 0 115 1.2c-.8.7-2.5 1.4-2.5 3.1" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />
      <path d="M12 17.3v.01" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />
    </React.Fragment>
  ),
  block: (
    <React.Fragment>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M5 5l14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />
    </React.Fragment>
  ),
  chevronDown: <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />,
  chevronRight: <path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />,
  link: (
    <React.Fragment>
      <path d="M10 14a5 5 0 007.07 0l2.83-2.83a5 5 0 00-7.07-7.07L11 5.93" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />
      <path d="M14 10a5 5 0 00-7.07 0L4.1 12.83a5 5 0 007.07 7.07L13 18.07" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />
    </React.Fragment>
  ),
  search: (
    <React.Fragment>
      <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M16 16l5 5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />
    </React.Fragment>
  ),
  refresh: (
    <React.Fragment>
      <path d="M20 12a8 8 0 11-2.34-5.66" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />
      <path d="M20 3v4h-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />
    </React.Fragment>
  ),
  close: <path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />,
  record: <rect x="5" y="5" width="14" height="14" fill="currentColor" />,
  shield: (
    <React.Fragment>
      <path d="M12 3l8 3v6c0 4.5-3.2 7.6-8 9-4.8-1.4-8-4.5-8-9V6l8-3z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="square" />
      <path d="M9 12l2 2 4-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />
    </React.Fragment>
  ),
  clock: (
    <React.Fragment>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M12 7v5l3 2" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" />
    </React.Fragment>
  ),
};

export default function Icon({ name, size = 14, className = "", label = null }) {
  const glyph = PATHS[name] || PATHS.unknown;
  return (
    <svg
      className={`icon ${className}`.trim()}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      focusable="false"
      aria-hidden={label ? "false" : "true"}
      role={label ? "img" : undefined}
      aria-label={label || undefined}
    >
      {glyph}
    </svg>
  );
}
