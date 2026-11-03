"""Read-only public map projection placed at worksite ZCTA/place internal points (Census2020).

Every LCA fact row is joined back to its staging source row (same file SHA and row number)
for the worksite state, ZIP and city. Coordinates: the worksite ZCTA's internal point when
that ZCTA has land in the worksite state, else the worksite place's internal point, else the
county internal point already in dim_geography. Each frequency row names which one it used.
Writes .tools/viz/worksite-fy2025/{frequencies,manifest}.json once; refuses to overwrite.
"""

import csv
import hashlib
import json
import re
import threading
from collections import Counter
from pathlib import Path

import duckdb

from scripts.canonical_paths import FACTS_DB, STAGING_DB, VIZ_WORKSITE

ROOT = Path(__file__).resolve().parents[2]
FACTS = FACTS_DB
STAGING = STAGING_DB
CENTROIDS = ROOT / "data/reference/worksite_centroids_2020.csv"
WORK = VIZ_WORKSITE
KIND = "WORKSITE_ZCTA_OR_PLACE_INTERNAL_POINT"
LIMITS = ROOT / "web/apps/site/lib/viz-limits.ts"


def site_limits(path: Path = LIMITS) -> dict:
    """The site's map limits (the one copy lives in the TypeScript module the server reads)."""
    found = re.findall(r"^\s+(\w+): ([\d_]+),?$", path.read_text(), re.MULTILINE)
    return {name: int(value) for name, value in found}


_limits = site_limits()
MAX_ROWS, MAX_BYTES = _limits["maxFrequencyRows"], _limits["maxFrequencyBytes"]
COLUMNS = [
    "employer", "fiscal_year", "employer_history_status", "latitude", "longitude",
    "county_reference_vintage", "coordinate_reference_vintage", "coordinate_semantics",
    "county_mapping_status", "annual_wage", "count",
]  # fmt: skip
QUERY = """SELECT f.employer_id, f.fiscal_year, f.employer_history_status,
 g.county_internal_latitude, g.county_internal_longitude, g.county_reference_vintage,
 g.coordinate_reference_vintage, g.coordinate_semantics, f.county_mapping_status,
 f.annual_wage, s.worksite_state, s.worksite_zip5, s.worksite_city
FROM MARTS.fact_lca_case f
LEFT JOIN MARTS.dim_geography g ON f.geography_key = g.geography_key
LEFT JOIN staging.STAGING.stg_lca s
 ON s.source_sha256 = f.source_sha256 AND s.source_row_number = f.source_row_number"""
CHECKS = [
    "SELECT count(*), count(DISTINCT case_number) FROM MARTS.fact_lca_case",
    "SELECT count(*) FROM (" + QUERY + ")",
    "SELECT count(*) FROM (" + QUERY + ") WHERE worksite_state IS NULL",
]


def load_centroids(path: Path = CENTROIDS) -> dict:
    with path.open() as stream:
        return {
            (r["kind"], r["state_code"], r["key"]): (float(r["latitude"]), float(r["longitude"]))
            for r in csv.DictReader(stream)
        }


def locate(row: tuple, centroids: dict) -> tuple:
    """Return (latitude, longitude, coordinate vintage, semantics) for one fact row."""
    lat, lon, coord_vintage, semantics = row[3], row[4], row[6], row[7]
    state, zip5, city = row[10], row[11], row[12]
    for kind, key in (("ZCTA", zip5), ("PLACE", city)):
        point = centroids.get((kind, state, key)) if key else None
        if point:
            return (*point, "Census2020", f"{kind}_INTERNAL_POINT")
    lat = None if lat is None else float(lat)
    lon = None if lon is None else float(lon)
    return lat, lon, coord_vintage, semantics


def frequencies(rows, centroids: dict) -> Counter:
    groups = Counter()
    for row in rows:
        lat, lon, coord_vintage, semantics = locate(row, centroids)
        wage = None if row[9] is None else float(row[9])
        groups[
            (row[0], row[1], row[2], lat, lon, row[5], coord_vintage, semantics, row[8], wage)
        ] += 1
    return groups


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    target = WORK / "frequencies.json"
    if target.exists():
        raise RuntimeError("Immutable projection already exists")
    paths = [FACTS, STAGING, CENTROIDS]

    def stats():
        return {str(p): {"size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns} for p in paths}

    before = stats()
    con = duckdb.connect(
        str(FACTS),
        read_only=True,
        config={
            "memory_limit": "1GB",
            "threads": 1,
            "max_temp_directory_size": "256MB",
            "temp_directory": str(WORK / "export-tmp"),
        },
    )
    con.execute(f"ATTACH '{STAGING}' AS staging (READ_ONLY)")
    timer = threading.Timer(300, con.interrupt)
    timer.start()
    try:
        checks = [list(con.execute(q).fetchone()) for q in CHECKS]
        assert checks[0][0] == checks[0][1] == checks[1][0], checks
        assert checks[2][0] == 0, "fact rows without a staging source row"
        groups = frequencies(con.execute(QUERY).fetchall(), load_centroids())
    finally:
        timer.cancel()
        con.close()
    total = sum(groups.values())
    assert total == checks[0][0]
    rows = [[*key, n] for key, n in sorted(groups.items(), key=lambda kv: repr(kv[0]))]
    with target.open("x") as stream:
        stream.write('{"columns":' + json.dumps(COLUMNS) + ',"rows":')
        stream.write(json.dumps(rows, separators=(",", ":"), allow_nan=False) + "}")
    if target.stat().st_size > MAX_BYTES or len(rows) > MAX_ROWS:
        target.unlink()
        raise RuntimeError(f"Projection cap: {len(rows)} rows")
    after = stats()
    assert before == after
    by_semantics = Counter()
    for key, n in groups.items():
        by_semantics[key[7] if key[3] is not None else "UNMAPPED"] += n
    public = json.loads((ROOT / ".tools/semantic/actual/export-evidence.json").read_text())
    snapshot = next(e["sha256"] for e in public["exports"] if e["path"].endswith("/lca.parquet"))
    manifest = {
        "kind": KIND,
        "sourceSnapshot": snapshot,
        "sha256": hashlib.file_digest(target.open("rb"), "sha256").hexdigest(),
        "bytes": target.stat().st_size,
        "rows": total,
        "frequencyRows": len(rows),
        "nullWages": sum(n for k, n in groups.items() if k[9] is None),
        "unmapped": by_semantics["UNMAPPED"],
        "bySemantics": dict(sorted(by_semantics.items())),
        "checks": checks,
        "sourceProtection": {"unchanged": before == after},
        "coordinateCaveat": "Census2020 ZCTA/place internal points, county internal point as "
        "fallback; representative points, not worksite addresses",
    }
    (WORK / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    target.chmod(0o444)
    (WORK / "manifest.json").chmod(0o444)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
