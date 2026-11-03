"""Canonical local databases: the FY2025 full-fiscal-year backfill (`sponsoratlas_backfill`,
finished 2026-10-28). Every reader (Cube export, map export, OpenMetadata, checks) uses these."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGING_DB = ROOT / ".tools/staging/fy2025-full.duckdb"
DIMENSIONS_DB = ROOT / ".tools/dimensions/fy2025-full.duckdb"
FACTS_DB = ROOT / ".tools/facts/fy2025-full.duckdb"
ER_DIR = ROOT / ".tools/er/fy2025-backfill"
ER_INPUT, ER_MODEL, ER_PREDICTION = ER_DIR / "input", ER_DIR / "model", ER_DIR / "prediction"
# Map projection exported from FACTS_DB + STAGING_DB; the site reads it.
VIZ_WORKSITE = ROOT / ".tools/viz/worksite-fy2025"
CANONICAL = (STAGING_DB, DIMENSIONS_DB, FACTS_DB, ER_DIR)

# FY2025 Q4-only predecessors. No code reads them. The user approved deletion on
# 2026-10-28 and the files were removed; do not recreate them as the live path.
SUPERSEDED = (
    ROOT / ".tools/staging/actual-files.duckdb",
    ROOT / ".tools/dimensions/actual.duckdb",
    ROOT / ".tools/facts/actual.duckdb",
    ROOT / ".tools/er/real-input-v2",
    ROOT / ".tools/er/fix-r1-model",
    ROOT / ".tools/er/fix-r1-next",
    ROOT / ".tools/er/fix-r1-initial",
    ROOT / ".tools/viz/worksite",
)
