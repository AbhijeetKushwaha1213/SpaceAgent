/*
 * Panel — a labelled section container. Heading level is configurable so
 * views can keep a correct document outline.
 */

import React from "react";

export default function Panel({
  title,
  id,
  level = 2,
  actions = null,
  className = "",
  status = null,
  children,
}) {
  const HeadingTag = `h${Math.min(Math.max(level, 1), 6)}`;
  return (
    <section className={`panel ${className}`.trim()} aria-labelledby={id}>
      <header className="panel__header">
        <HeadingTag id={id} className="panel__title">
          {title}
        </HeadingTag>
        {status}
        {actions ? <div className="panel__actions">{actions}</div> : null}
      </header>
      <div className="panel__body">{children}</div>
    </section>
  );
}