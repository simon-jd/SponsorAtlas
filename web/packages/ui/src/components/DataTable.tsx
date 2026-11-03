"use client";
import { useState, type ReactNode } from "react";
export interface DataColumn<Row> {
  key: string;
  header: string;
  render: (row: Row) => ReactNode;
  compare?: (a: Row, b: Row) => number;
}
export interface DataTableProps<Row> {
  caption: string;
  columns: readonly DataColumn<Row>[];
  rows: readonly Row[];
  rowKey: (row: Row) => string;
  emptyMessage: string;
}
/** Native table: Tab visits sortable header buttons; Enter/Space sorts. No grid role. */
export function DataTable<Row>({
  caption,
  columns,
  rows,
  rowKey,
  emptyMessage,
}: DataTableProps<Row>) {
  const [sort, setSort] = useState<{
    key: string;
    direction: "ascending" | "descending";
  } | null>(null);
  const compare = columns.find((column) => column.key === sort?.key)?.compare;
  const ordered =
    compare && sort
      ? [...rows].sort(
          (a, b) => compare(a, b) * (sort.direction === "ascending" ? 1 : -1),
        )
      : rows;
  return (
    <div
      className="sa-table-scroll"
      tabIndex={0}
      role="region"
      aria-label={caption}
    >
      <table className="sa-table">
        <caption>{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                aria-sort={
                  column.compare
                    ? sort?.key === column.key
                      ? sort.direction
                      : "none"
                    : undefined
                }
              >
                {column.compare ? (
                  <button
                    type="button"
                    onClick={() =>
                      setSort({
                        key: column.key,
                        direction:
                          sort?.key === column.key &&
                          sort.direction === "ascending"
                            ? "descending"
                            : "ascending",
                      })
                    }
                  >
                    {column.header}
                    <span aria-hidden="true">
                      {" "}
                      {sort?.key === column.key
                        ? sort.direction === "ascending"
                          ? "↑"
                          : "↓"
                        : "↕"}
                    </span>
                  </button>
                ) : (
                  column.header
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ordered.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((column) => (
                <td key={column.key}>{column.render(row)}</td>
              ))}
            </tr>
          ))}
          {!rows.length && (
            <tr>
              <td colSpan={Math.max(columns.length, 1)}>{emptyMessage}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
