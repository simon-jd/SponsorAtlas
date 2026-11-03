// Map projection limits, the only copy (the Python exporter parses this file).
// Sized 2026-10-28 from the full-year projection: 517,991 frequency rows, 103,036,467 bytes,
// at most 12,701 H3 cells per scope (all years, resolution 7). Measured server cost of that
// projection: 1.43 GB peak RSS, 0.4–0.85 s per uncached all-years scope
// (docs/evidence/task14-fullyear-map/). Exact wages, no rounding.
export const VIZ_LIMITS = {
  maxFrequencyRows: 600_000,
  maxFrequencyBytes: 120_000_000,
  maxCells: 16_000
} as const;
