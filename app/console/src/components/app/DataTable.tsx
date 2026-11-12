"use client";

// Sortable, filterable data table (DM Assistant table pattern) used by every
// list screen. Columns declare how to render and how to sort; the table owns
// sort state, an optional text filter, and keyboard-reachable row actions.

import { ReactNode, useMemo, useState } from "react";
import { cx, EmptyState } from "./primitives";

export interface Column<T> {
  key: string;
  header: ReactNode;
  render?: (row: T) => ReactNode;
  sort?: (row: T) => string | number | null | undefined;
  num?: boolean;
  width?: string | number;
}

export function DataTable<T>({ rows, columns, rowKey, onRowClick, filterPlaceholder, filterText, empty, initialSort, dense }: {
  rows: T[]; columns: Column<T>[]; rowKey: (r: T, i: number) => string;
  onRowClick?: (r: T) => void; filterPlaceholder?: string; filterText?: (r: T) => string;
  empty?: ReactNode; initialSort?: { key: string; dir: "asc" | "desc" }; dense?: boolean }) {
  const [sort, setSort] = useState(initialSort || null);
  const [q, setQ] = useState("");

  const shown = useMemo(() => {
    let out = rows;
    if (q && filterText) {
      const needle = q.toLowerCase();
      out = out.filter((r) => filterText(r).toLowerCase().includes(needle));
    }
    if (sort) {
      const col = columns.find((c) => c.key === sort.key);
      if (col?.sort) {
        const get = col.sort;
        out = [...out].sort((a, b) => {
          const x = get(a), y = get(b);
          if (x == null && y == null) return 0;
          if (x == null) return 1;
          if (y == null) return -1;
          const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
          return sort.dir === "asc" ? c : -c;
        });
      }
    }
    return out;
  }, [rows, q, filterText, sort, columns]);

  const toggle = (key: string) =>
    setSort((s) => (s?.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }));

  return (
    <div>
      {filterText && (
        <input className="input mb-3 max-w-xs" placeholder={filterPlaceholder || "Filter…"} value={q}
               onChange={(e) => setQ(e.target.value)} aria-label="Filter rows" />
      )}
      <div className="overflow-x-auto">
        <table className="data-table" style={dense ? { fontSize: ".78rem" } : undefined}>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c.key} scope="col" className={cx(c.sort && "sortable", c.num && "num")} style={{ width: c.width }}
                    onClick={c.sort ? () => toggle(c.key) : undefined}
                    aria-sort={sort?.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}>
                  {c.header}{sort?.key === c.key ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={rowKey(r, i)} className={cx(onRowClick && "click")}
                  onClick={onRowClick ? () => onRowClick(r) : undefined}
                  tabIndex={onRowClick ? 0 : undefined}
                  onKeyDown={onRowClick ? (e) => e.key === "Enter" && onRowClick(r) : undefined}>
                {columns.map((c) => (
                  <td key={c.key} className={cx(c.num && "num")}>
                    {c.render ? c.render(r) : String((r as any)[c.key] ?? "—")}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {shown.length === 0 && <EmptyState>{empty || "No rows."}</EmptyState>}
    </div>
  );
}
