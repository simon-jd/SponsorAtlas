"""Build the Census2020 place -> county seed used to fill LCA worksite county FIPS from city.

A (state, place name) pair is kept when every Census2020 place with that name in the state
lies in one county (PLACE_UNIQUE), or when the only other places of that name are CDPs and
the single incorporated place lies in one county (INCORPORATED_OVER_CDP). Places that span
counties (New York, Atlanta, Houston, ...) and names shared by incorporated places in
different counties are left out, so those rows stay unresolved.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from scripts.staging_fixture import ROOT

CONTRACT = ROOT / "dbt/source_contracts/place_county.json"


# Every type suffix that occurs in national_place_by_county2020.txt, longest first.
PLACE_TYPES = (
    "city and borough",
    "consolidated government",
    "metropolitan government",
    "unified government",
    "metro government",
    "metro township",
    "urban county",
    "zona urbana",
    "municipality",
    "corporation",
    "comunidad",
    "borough",
    "village",
    "city",
    "town",
    "CDP",
)
_SUFFIX = re.compile(r"\s+(" + "|".join(PLACE_TYPES) + r")$")


def place_key(name: str) -> str:
    """Census place name without its type suffix, normalized like the dbt county_key macro."""
    name = re.sub(r"\s+", " ", name).strip()
    name = _SUFFIX.sub("", re.sub(r"\s*\([^)]*\)$", "", name))
    return re.sub(r"\s+", " ", name.upper().replace(".", "").replace("'", "")).strip()


def place_rows(records) -> list[list[str]]:
    counties = defaultdict(set)
    incorporated = defaultdict(set)
    for row in records:
        key = row["STATE"], place_key(row["PLACENAME"])
        code = row["STATEFP"] + row["COUNTYFP"]
        counties[key].add(code)
        if row["TYPE"] == "INCORPORATED PLACE":
            incorporated[key].add(code)
    rows = []
    for (state, name), codes in sorted(counties.items()):
        if not name:
            continue
        if len(codes) == 1:
            rule = "PLACE_UNIQUE"
        elif len(incorporated[state, name]) == 1:
            # The only competitors are CDPs; worksite cities normally name the municipality.
            rule, codes = "INCORPORATED_OVER_CDP", incorporated[state, name]
        else:
            continue
        (code,) = codes
        rows.append([state, name, code[:2], code, "Census2020", rule])
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dbt/seeds/place_county_fips.csv")
    args = parser.parse_args()
    pinned = json.loads(CONTRACT.read_text())
    if hashlib.sha256(args.source.read_bytes()).hexdigest() != pinned["source_sha256"]:
        parser.error("Census place reference SHA256 differs from the pinned contract")
    with args.source.open(encoding="utf-8") as stream:
        rows = place_rows(csv.DictReader(stream, delimiter="|"))
    with args.output.open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "state_code",
                "place_name",
                "state_fips",
                "county_fips",
                "geography_vintage",
                "match_rule",
            ]
        )
        writer.writerows(rows)
    print(json.dumps({"vintage": "Census2020", "rows": len(rows)}))


if __name__ == "__main__":
    main()
