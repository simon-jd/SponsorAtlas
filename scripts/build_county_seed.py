"""Rebuild the Census2020 county alias seed from the pinned official reference file."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from scripts.staging_fixture import ROOT


def county_rows(records):
    aliases = defaultdict(set)
    for row in records:
        code = row["STATEFP"] + row["COUNTYFP"]
        name = re.sub(
            r"\s+", " ", row["COUNTYNAME"].upper().replace(".", "").replace("'", "")
        ).strip()
        aliases[row["STATE"], name].add(code)
        short = re.sub(
            r" (CITY AND BOROUGH|CENSUS AREA|COUNTY|PARISH|BOROUGH|MUNICIPALITY|CITY|MUNICIPIO)$",
            "",
            name,
        )
        aliases[row["STATE"], short].add(code)
    return [
        [state, name, code[:2], code, "Census2020"]
        for (state, name), codes in sorted(aliases.items())
        if len(codes) == 1
        for code in codes
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dbt/seeds/county_fips.csv")
    args = parser.parse_args()
    pinned = json.loads((ROOT / "dbt/source_contracts/geography.json").read_text())
    if hashlib.sha256(args.source.read_bytes()).hexdigest() != pinned["source_sha256"]:
        parser.error("Census reference SHA256 differs from reviewed geography contract")
    with args.source.open() as stream:
        rows = county_rows(csv.DictReader(stream, delimiter="|"))
    with args.output.open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            ["state_code", "county_name", "state_fips", "county_fips", "geography_vintage"]
        )
        writer.writerows(rows)
    print(
        json.dumps(
            {"vintage": "Census2020", "rows": len(rows), "source_sha256": pinned["source_sha256"]}
        )
    )


if __name__ == "__main__":
    main()
