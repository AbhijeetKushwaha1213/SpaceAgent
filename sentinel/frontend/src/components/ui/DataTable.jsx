/*
 * DataTable — table primitives with a caption (accessibility) and an
 * optional empty state.
 */

import React from "react";

export default function DataTable({ caption, columns, rows, emptyMessage = "NO DATA AVAILABLE", rowClass }) {
  if (!rows || rows.length === 0) {
    return (
      <div className="table-empty" role="status">
        {emptyMessage}
      </div>
    );
  }
  return (
    <div className="table-scroll">
      <table className="data-table">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key} scope="col">
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={row.key ?? index} className={rowClass ? rowClass(row, index) : undefined}>
              {columns.map((col) => (
                <td key={col.key} className={col.cellClass ? col.cellClass(row) : undefined}>
                  {col.render ? col.render(row, index) : row[col.key] ?? "N/A"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}