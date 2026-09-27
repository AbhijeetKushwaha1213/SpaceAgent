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
  grid: (
    <React.Fragment>
      <rect x="3" y="3" width="7" height="7" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
      <rect x="14" y="3" width="7" height="7" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
      <rect x="14" y="14" width="7" height="7" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
      <rect x="3" y="14" width="7" height="7" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
    </React.Fragment>
  ),
  rocket: (
    <React.Fragment>
      <path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 00-2.91-.09z" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M12 15l-3-3a22 22 0 012-3.95A12.88 12.88 0 0122 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 01-4 2z" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M9 12l2 2" fill="none" stroke="currentColor" strokeWidth="2" />
    </React.Fragment>
  ),
  activity: (
    <path d="M22 12h-4l-3 9L9 3l-3 9H2" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  ),
  nodes: (
    <React.Fragment>
      <circle cx="18" cy="5" r="3" fill="none" stroke="currentColor" strokeWidth="2" />
      <circle cx="6" cy="12" r="3" fill="none" stroke="currentColor" strokeWidth="2" />
      <circle cx="18" cy="19" r="3" fill="none" stroke="currentColor" strokeWidth="2" />
      <line x1="8.59" y1="13.51" x2="15.42" y2="17.49" stroke="currentColor" strokeWidth="2" />
      <line x1="15.41" y1="6.51" x2="8.59" y2="10.49" stroke="currentColor" strokeWidth="2" />
    </React.Fragment>
  ),
  orbit: (
    <React.Fragment>
      <circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" strokeWidth="2" />
      <ellipse cx="12" cy="12" rx="9" ry="4" transform="rotate(30 12 12)" fill="none" stroke="currentColor" strokeWidth="2" />
      <ellipse cx="12" cy="12" rx="9" ry="4" transform="rotate(-30 12 12)" fill="none" stroke="currentColor" strokeWidth="2" />
    </React.Fragment>
  ),
  tools: (
    <React.Fragment>
      <path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </React.Fragment>
  ),
  book: (
    <React.Fragment>
      <path d="M4 19.5A2.5 2.5 0 016.5 17H20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </React.Fragment>
  ),
  fingerprint: (
    <React.Fragment>
      <path d="M12 10a2 2 0 00-2 2c0 1.02-.1 2.51-.26 3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M14 13.12c0 2.38 0 6.38-1 8.88" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M2 12a10 10 0 0118-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M20 12c0 1.5-.5 3-1 4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M6 12a6 6 0 0110.82-3.6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </React.Fragment>
  ),
  chart: (
    <React.Fragment>
      <line x1="18" y1="20" x2="18" y2="10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <line x1="12" y1="20" x2="12" y2="4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <line x1="6" y1="20" x2="6" y2="14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </React.Fragment>
  ),
  sun: (
    <React.Fragment>
      <circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </React.Fragment>
  ),
  moon: (
    <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  ),
  map: (
    <React.Fragment>
      <polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <line x1="8" y1="2" x2="8" y2="18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <line x1="16" y1="6" x2="16" y2="22" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </React.Fragment>
  ),
  zoomIn: (
    <React.Fragment>
      <circle cx="11" cy="11" r="8" fill="none" stroke="currentColor" strokeWidth="2" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <line x1="11" y1="8" x2="11" y2="14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <line x1="8" y1="11" x2="14" y2="11" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </React.Fragment>
  ),
  zoomOut: (
    <React.Fragment>
      <circle cx="11" cy="11" r="8" fill="none" stroke="currentColor" strokeWidth="2" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <line x1="8" y1="11" x2="14" y2="11" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </React.Fragment>
  ),
  moreVertical: (
    <React.Fragment>
      <circle cx="12" cy="12" r="1.5" fill="currentColor" />
      <circle cx="12" cy="5" r="1.5" fill="currentColor" />
      <circle cx="12" cy="19" r="1.5" fill="currentColor" />
    </React.Fragment>
  ),
  moreHorizontal: (
    <React.Fragment>
      <circle cx="12" cy="12" r="1.5" fill="currentColor" />
      <circle cx="5" cy="12" r="1.5" fill="currentColor" />
      <circle cx="19" cy="12" r="1.5" fill="currentColor" />
    </React.Fragment>
  ),
  bell: (
    <React.Fragment>
      <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h24s-3-2-3-9" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13.73 21a2 2 0 01-3.46 0" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </React.Fragment>
  ),
  filter: (
    <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  ),
  user: (
    <React.Fragment>
      <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="12" cy="7" r="4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </React.Fragment>
  ),
  trendingUp: (
    <React.Fragment>
      <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <polyline points="17 6 23 6 23 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </React.Fragment>
  ),
  trendingDown: (
    <React.Fragment>
      <polyline points="23 18 13.5 8.5 8.5 13.5 1 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <polyline points="17 18 23 18 23 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </React.Fragment>
  ),
  layers: (
    <React.Fragment>
      <polygon points="12 2 2 7 12 12 22 7 12 2" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <polyline points="2 17 12 22 22 17" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <polyline points="2 12 12 17 22 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
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
