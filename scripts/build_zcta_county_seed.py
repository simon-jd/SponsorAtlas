"""Build the Census2020 ZCTA -> county seed used to fill LCA worksite county FIPS from ZIP.

A ZCTA is kept when all of its land lies in one county (ZCTA_UNIQUE), or when one county
holds at least 90% of its land area (ZCTA_DOMINANT_90). ZCTAs split more evenly are left
out, so those rows fall through to the city rule or stay unresolved. Parts with zero land
area (water only) do not count. ZCTAs approximate USPS ZIP codes; they are not identical.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from scripts.staging_fixture import ROOT

CONTRACT = ROOT / "dbt/source_contracts/zcta_county.json"
DOMINANT_SHARE = 0.9


def zcta_rows(records) -> list[list[str]]:
    land = defaultdict(lambda: defaultdict(int))
    for row in records:
        zcta = row["GEOID_ZCTA5_20"]
        if zcta and int(row["AREALAND_PART"]) > 0:
            land[zcta][row["GEOID_COUNTY_20"]] += int(row["AREALAND_PART"])
    rows = []
    for zcta, parts in sorted(land.items()):
        county, area = max(parts.items(), key=lambda item: item[1])
        share = area / sum(parts.values())
        if len(parts) == 1:
            rule = "ZCTA_UNIQUE"
        elif share >= DOMINANT_SHARE:
            rule = "ZCTA_DOMINANT_90"
        else:
            continue
        rows.append([zcta, county[:2], county, "Census2020", rule, f"{share:.4f}"])
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dbt/seeds/zcta_county_fips.csv")
    args = parser.parse_args()
    pinned = json.loads(CONTRACT.read_text())
    if hashlib.sha256(args.source.read_bytes()).hexdigest() != pinned["source_sha256"]:
        parser.error("Census ZCTA relationship file SHA256 differs from the pinned contract")
    with args.source.open(encoding="utf-8-sig") as stream:
        rows = zcta_rows(csv.DictReader(stream, delimiter="|"))
    with args.output.open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            ["zcta", "state_fips", "county_fips", "geography_vintage", "match_rule", "land_share"]
        )
        writer.writerows(rows)
    by_rule = defaultdict(int)
    for row in rows:
        by_rule[row[4]] += 1
    print(json.dumps({"vintage": "Census2020", "rows": len(rows), "by_rule": by_rule}))


if __name__ == "__main__":
    main()
