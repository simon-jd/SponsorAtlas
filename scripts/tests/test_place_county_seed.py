"""Census2020 place -> county seed: only names that resolve to one county within a state."""

import csv
from pathlib import Path

from scripts.build_place_county_seed import place_key, place_rows

ROOT = Path(__file__).resolve().parents[2]


def _record(state, statefp, countyfp, place, kind=None):
    if kind is None:
        kind = "CENSUS DESIGNATED PLACE" if place.endswith("CDP") else "INCORPORATED PLACE"
    return {
        "STATE": state,
        "STATEFP": statefp,
        "COUNTYFP": countyfp,
        "PLACENAME": place,
        "TYPE": kind,
    }


def test_place_key_strips_census_type_suffixes():
    assert place_key("Boston city") == "BOSTON"
    assert place_key("Urban Honolulu CDP") == "URBAN HONOLULU"
    assert place_key("Nashville-Davidson metropolitan government (balance)") == (
        "NASHVILLE-DAVIDSON"
    )
    assert place_key("Louisville/Jefferson County metro government (balance)") == (
        "LOUISVILLE/JEFFERSON COUNTY"
    )
    assert place_key("St. Mary's city") == "ST MARYS"
    assert place_key("  Coeur d'Alene   city ") == "COEUR DALENE"


def test_multi_county_and_duplicate_names_are_not_resolved():
    rows = place_rows(
        [
            _record("MA", "25", "025", "Boston city"),
            # One place that spans two counties.
            _record("GA", "13", "121", "Atlanta city"),
            _record("GA", "13", "089", "Atlanta city"),
            # Two different places with the same name in different counties.
            _record("WI", "55", "079", "Franklin city"),
            _record("WI", "55", "009", "Franklin village"),
            # Same name twice in one county (city plus CDP) stays unique.
            _record("VA", "51", "600", "Fairfax city"),
            _record("TX", "48", "201", "Spring CDP"),
            _record("TX", "48", "201", "Spring CDP"),
        ]
    )
    mapping = {(r[0], r[1]): r[3] for r in rows}
    assert mapping == {
        ("MA", "BOSTON"): "25025",
        ("VA", "FAIRFAX"): "51600",
        ("TX", "SPRING"): "48201",
    }
    assert all(r[2] == r[3][:2] and r[4] == "Census2020" for r in rows)
    assert {r[5] for r in rows} == {"PLACE_UNIQUE"}


def test_single_incorporated_place_wins_over_same_name_cdps_only():
    rows = place_rows(
        [
            _record("CA", "06", "085", "Mountain View city"),
            _record("CA", "06", "013", "Mountain View CDP"),
            # Two CDPs and no incorporated place: still ambiguous.
            _record("CA", "06", "001", "Ashland CDP"),
            _record("CA", "06", "007", "Ashland CDP"),
            # An incorporated place that itself spans two counties: still ambiguous.
            _record("TX", "48", "085", "Plano city"),
            _record("TX", "48", "121", "Plano city"),
            _record("TX", "48", "999", "Plano CDP"),
        ]
    )
    assert [(r[0], r[1], r[3], r[5]) for r in rows] == [
        ("CA", "MOUNTAIN VIEW", "06085", "INCORPORATED_OVER_CDP"),
    ]


def test_committed_seed_is_unique_and_matches_county_vintage():
    with (ROOT / "dbt/seeds/place_county_fips.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    keys = [(r["state_code"], r["place_name"]) for r in rows]
    assert len(keys) == len(set(keys)) > 20000
    with (ROOT / "dbt/seeds/county_fips.csv").open() as stream:
        counties = {r["county_fips"] for r in csv.DictReader(stream)}
    assert {r["county_fips"] for r in rows} <= counties
    assert {r["geography_vintage"] for r in rows} == {"Census2020"}
    assert {r["match_rule"] for r in rows} == {"PLACE_UNIQUE", "INCORPORATED_OVER_CDP"}
