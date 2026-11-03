"""Build Census2020 worksite centroids for the map: ZCTA and place internal points.

ZCTA rows: the Gazetteer internal point, listed once for every state in which the ZCTA has
land (from the ZCTA-county relationship file), so a worksite ZIP is only placed when it
belongs to the worksite state. PLACE rows: the Gazetteer internal point keyed like the
place -> county seed (type suffix removed); a name kept only when it is unique in the
state, or when the only other places of that name are CDPs (one incorporated place).
Internal points are Census representative points, not worksite addresses.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path

from scripts.build_place_county_seed import place_key
from scripts.staging_fixture import ROOT

CONTRACT = ROOT / "data/reference/worksite_centroids_2020.json"
CDP_LSAD = "57"


def zcta_centroids(gazetteer, parts, state_codes: dict[str, str]) -> list[list[str]]:
    states = defaultdict(set)
    for row in parts:
        if row["GEOID_ZCTA5_20"] and int(row["AREALAND_PART"]) > 0:
            states[row["GEOID_ZCTA5_20"]].add(state_codes[row["GEOID_COUNTY_20"][:2]])
    rows = []
    for row in gazetteer:
        for state in sorted(states.get(row["GEOID"], ())):
            rows.append(["ZCTA", state, row["GEOID"], row["INTPTLAT"], row["INTPTLONG"]])
    return sorted(rows)


def place_centroids(gazetteer) -> list[list[str]]:
    places = defaultdict(list)
    for row in gazetteer:
        places[row["USPS"], place_key(row["NAME"])].append(row)
    rows = []
    for (state, name), members in sorted(places.items()):
        if len(members) > 1:
            members = [m for m in members if m["LSAD"] != CDP_LSAD]
        if name and len(members) == 1:
            (m,) = members
            rows.append(["PLACE", state, name, m["INTPTLAT"], m["INTPTLONG"]])
    return rows


def _zip_rows(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as archive:
        (member,) = archive.infolist()
        text = io.TextIOWrapper(archive.open(member), encoding="latin-1")
        reader = csv.reader(text, delimiter="\t")
        header = [h.strip() for h in next(reader)]
        return [dict(zip(header, (v.strip() for v in values), strict=True)) for values in reader]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zcta-gazetteer", type=Path, required=True)
    parser.add_argument("--place-gazetteer", type=Path, required=True)
    parser.add_argument("--relationship", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/reference/worksite_centroids_2020.csv"
    )
    args = parser.parse_args()
    pinned = json.loads(CONTRACT.read_text())["sources"]
    for key in ("zcta_gazetteer", "place_gazetteer", "relationship"):
        path = getattr(args, key)
        if hashlib.sha256(path.read_bytes()).hexdigest() != pinned[key]["sha256"]:
            parser.error(f"{key}: SHA256 differs from the pinned contract")
    with (ROOT / "dbt/seeds/state_fips.csv").open() as stream:
        state_codes = {r["state_fips"]: r["state_code"] for r in csv.DictReader(stream)}
    with args.relationship.open(encoding="utf-8-sig") as stream:
        parts = list(csv.DictReader(stream, delimiter="|"))
    rows = zcta_centroids(_zip_rows(args.zcta_gazetteer), parts, state_codes)
    rows += place_centroids(_zip_rows(args.place_gazetteer))
    with args.output.open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["kind", "state_code", "key", "latitude", "longitude"])
        writer.writerows(rows)
    counts = defaultdict(int)
    for row in rows:
        counts[row[0]] += 1
    print(json.dumps({"vintage": "Census2020", "rows": dict(counts)}))


if __name__ == "__main__":
    main()
