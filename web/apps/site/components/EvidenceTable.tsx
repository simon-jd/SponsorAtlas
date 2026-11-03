"use client";
import { DataTable, type DataColumn } from "ui";
import type { EmployerRow } from "../lib/data";
import { formatCurrency, formatNumber, type Locale } from "../lib/format";
export function EvidenceTable({
  rows,
  locale,
  labels,
}: {
  rows: EmployerRow[];
  locale: Locale;
  labels: Record<string, string>;
}) {
  const columns: DataColumn<EmployerRow>[] = [
    {
      key: "id",
      header: labels.employer,
      render: (r) => <span className="identifier">{r.id}</span>,
    },
    { key: "year", header: labels.year, render: (r) => String(r.year) },
    {
      key: "status",
      header: labels.history,
      render: (r) => r.status ?? labels.historyMissing,
    },
    {
      key: "count",
      header: labels.applications,
      render: (r) => formatNumber(r.count, locale),
      compare: (a, b) => a.count - b.count,
    },
    {
      key: "wage",
      header: labels.wage,
      render: (r) => formatCurrency(r.wage ?? null, locale),
    },
  ];
  return (
    <DataTable
      caption={labels.caption}
      columns={columns}
      rows={rows}
      rowKey={(r) => JSON.stringify([r.id, r.year, r.status])}
      emptyMessage={labels.empty}
    />
  );
}
