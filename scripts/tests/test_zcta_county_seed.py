"""Census2020 ZCTA -> county seed: one county, or one county with >= 90% of the land area."""

import csv
from pathlib import Path

from scripts.build_zcta_county_seed import zcta_rows

ROOT = Path(__file__).resolve().parents[2]


def _part(zcta, county, land):
    return {"GEOID_ZCTA5_20": zcta, "GEOID_COUNTY_20": county, "AREALAND_PART": str(land)}


def test_single_and_dominant_counties_resolve_split_zctas_do_not():
    rows = zcta_rows(
        [
            _part("02139", "25017", 1000),
            # 95% in one county: dominant.
            _part("10001", "36061", 950),
            _part("10001", "36047", 50),
            # 60/40 split: ambiguous, left out.
            _part("75201", "48113", 600),
            _part("75201", "48085", 400),
            # Water-only part in a second county does not count.
            _part("78701", "48453", 500),
            _part("78701", "48491", 0),
            # County parts outside any ZCTA have an empty ZCTA id.
            _part("", "01003", 10),
        ]
    )
    assert [(r[0], r[1], r[2], r[4]) for r in rows] == [
        ("02139", "25", "25017", "ZCTA_UNIQUE"),
        ("10001", "36", "36061", "ZCTA_DOMINANT_90"),
        ("78701", "48", "48453", "ZCTA_UNIQUE"),
    ]
    assert {r[3] for r in rows} == {"Census2020"}
    assert [r[5] for r in rows] == ["1.0000", "0.9500", "1.0000"]


def test_exact_ninety_percent_counts_as_dominant():
    rows = zcta_rows([_part("00601", "72001", 900), _part("00601", "72141", 100)])
    assert [(r[2], r[4]) for r in rows] == [("72001", "ZCTA_DOMINANT_90")]


def test_committed_seed_is_unique_and_matches_county_vintage():
    with (ROOT / "dbt/seeds/zcta_county_fips.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    keys = [r["zcta"] for r in rows]
    assert len(keys) == len(set(keys)) > 25000
    assert all(len(k) == 5 and k.isdigit() for k in keys)
    with (ROOT / "dbt/seeds/county_fips.csv").open() as stream:
        counties = {r["county_fips"] for r in csv.DictReader(stream)}
    assert {r["county_fips"] for r in rows} <= counties
    assert all(r["state_fips"] == r["county_fips"][:2] for r in rows)
    assert {r["match_rule"] for r in rows} == {"ZCTA_UNIQUE", "ZCTA_DOMINANT_90"}
