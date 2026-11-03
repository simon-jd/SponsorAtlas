// Pure server-owned query builders: no user-provided Cube members, SQL, or dimensions.
export type EmployerRow = {
  id: string;
  count: number;
  wage?: number | null;
  year?: number;
  status?: string | null;
};
export function validId(id: string) {
  return /^[A-Za-z0-9_-]{1,80}$/.test(id) && id !== "UNKNOWN";
}
export function parseIds(value: string) {
  const ids = [...new Set(value ? value.split(",") : [])];
  if (ids.length > 3 || ids.some((id) => !validId(id)))
    throw Error("Invalid identifiers");
  return ids;
}
export function searchQuery(q: string) {
  if (q && !validId(q)) throw Error("Invalid identifier");
  const filters = [
    {
      member: "lca_metrics.employer",
      operator: "notEquals",
      values: ["UNKNOWN"],
    },
  ];
  if (q)
    filters.push({
      member: "lca_metrics.employer",
      operator: "contains",
      values: [q],
    });
  return {
    measures: ["lca_metrics.case_count"],
    dimensions: ["lca_metrics.employer"],
    filters,
    order: { "lca_metrics.case_count": "desc", "lca_metrics.employer": "asc" },
    limit: 13,
  };
}
export function detailQuery(id: string) {
  if (!validId(id)) throw Error("Invalid identifier");
  return {
    measures: ["lca_metrics.case_count", "lca_metrics.wage_p50"],
    dimensions: [
      "lca_metrics.employer",
      "lca_metrics.fiscal_year",
      "lca_metrics.employer_history_status",
    ],
    filters: [
      { member: "lca_metrics.employer", operator: "equals", values: [id] },
    ],
    order: {
      "lca_metrics.fiscal_year": "desc",
      "lca_metrics.employer_history_status": "asc",
    },
    limit: 101,
  };
}
export function parseRows(payload: unknown, detail: boolean): EmployerRow[] {
  if (
    !payload ||
    typeof payload !== "object" ||
    !("data" in payload) ||
    !Array.isArray(payload.data) ||
    payload.data.length > (detail ? 100 : 13)
  )
    throw Error("Invalid Cube data");
  const numeric = (v: unknown) => {
    if (!["string", "number"].includes(typeof v) || v === "")
      throw Error("Invalid numeric data");
    const n = Number(v);
    if (!Number.isFinite(n) || n < 0) throw Error("Invalid numeric data");
    return n;
  };
  return payload.data.map((raw) => {
    if (!raw || typeof raw !== "object") throw Error("Invalid row");
    const id = raw["lca_metrics.employer"];
    const count = numeric(raw["lca_metrics.case_count"]);
    if (typeof id !== "string" || !validId(id) || !Number.isSafeInteger(count))
      throw Error("Invalid identity/count");
    if (!detail) return { id, count };
    const year = numeric(raw["lca_metrics.fiscal_year"]);
    const status = raw["lca_metrics.employer_history_status"];
    if (
      !Number.isInteger(year) ||
      year < 1900 ||
      year > 2200 ||
      (status !== null &&
        (typeof status !== "string" ||
          status.length < 1 ||
          status.length > 100))
    )
      throw Error("Invalid period/status");
    const value = raw["lca_metrics.wage_p50"];
    return {
      id,
      count,
      year,
      status,
      wage: value === null ? null : numeric(value),
    };
  });
}
export function unresolvedQuery() {
  return {
    measures: ["lca_metrics.case_count"],
    filters: [
      {
        member: "lca_metrics.employer",
        operator: "equals",
        values: ["UNKNOWN"],
      },
    ],
    limit: 1,
  };
}
export function parseUnresolved(payload: unknown): number {
  if (
    !payload ||
    typeof payload !== "object" ||
    !("data" in payload) ||
    !Array.isArray(payload.data) ||
    payload.data.length !== 1
  )
    throw Error("Invalid aggregate");
  const value = payload.data[0]?.["lca_metrics.case_count"];
  if ((typeof value !== "number" && typeof value !== "string") || value === "")
    throw Error("Invalid count");
  const n = Number(value);
  if (!Number.isSafeInteger(n) || n < 0) throw Error("Invalid count");
  return n;
}
