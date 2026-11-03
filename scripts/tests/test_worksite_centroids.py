"""Worksite centroids: ZCTA internal points per state with land, unique place internal points."""

import csv
from pathlib import Path

from scripts.build_worksite_centroids import place_centroids, zcta_centroids

ROOT = Path(__file__).resolve().parents[2]


def test_zcta_centroid_is_listed_for_every_state_with_land():
    gazetteer = [
        {"GEOID": "02139", "INTPTLAT": "42.364", "INTPTLONG": "-71.104"},
        {"GEOID": "99999", "INTPTLAT": "1", "INTPTLONG": "1"},  # no land part: dropped
    ]
    parts = [
        {"GEOID_ZCTA5_20": "02139", "GEOID_COUNTY_20": "25017", "AREALAND_PART": "100"},
        {"GEOID_ZCTA5_20": "02139", "GEOID_COUNTY_20": "33011", "AREALAND_PART": "5"},
        {"GEOID_ZCTA5_20": "02139", "GEOID_COUNTY_20": "44001", "AREALAND_PART": "0"},
        {"GEOID_ZCTA5_20": "", "GEOID_COUNTY_20": "25017", "AREALAND_PART": "9"},
    ]
    states = {"25": "MA", "33": "NH", "44": "RI"}
    assert zcta_centroids(gazetteer, parts, states) == [
        ["ZCTA", "MA", "02139", "42.364", "-71.104"],
        ["ZCTA", "NH", "02139", "42.364", "-71.104"],
    ]


def test_place_centroid_needs_a_unique_name_or_one_incorporated_place():
    rows = [
        {"USPS": "CA", "NAME": "Mountain View city", "LSAD": "25", "INTPTLAT": "37.4", "INTPTLONG": "-122.1"},
        {"USPS": "CA", "NAME": "Mountain View CDP", "LSAD": "57", "INTPTLAT": "37.9", "INTPTLONG": "-122.0"},
        {"USPS": "CA", "NAME": "Ashland CDP", "LSAD": "57", "INTPTLAT": "1", "INTPTLONG": "1"},
        {"USPS": "CA", "NAME": "Ashland CDP", "LSAD": "57", "INTPTLAT": "2", "INTPTLONG": "2"},
        {"USPS": "MA", "NAME": "Boston city", "LSAD": "25", "INTPTLAT": "42.33", "INTPTLONG": "-71.02"},
    ]
    assert place_centroids(rows) == [
        ["PLACE", "CA", "MOUNTAIN VIEW", "37.4", "-122.1"],
        ["PLACE", "MA", "BOSTON", "42.33", "-71.02"],
    ]


def test_committed_centroids_are_unique_and_inside_the_us():
    with (ROOT / "data/reference/worksite_centroids_2020.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    keys = [(r["kind"], r["state_code"], r["key"]) for r in rows]
    assert len(keys) == len(set(keys))
    kinds = {k for k, _, _ in keys}
    assert kinds == {"ZCTA", "PLACE"}
    assert sum(k == "ZCTA" for k, _, _ in keys) > 30000
    assert sum(k == "PLACE" for k, _, _ in keys) > 25000
    for r in rows:
        lat, lon = float(r["latitude"]), float(r["longitude"])
        assert -15 <= lat <= 72 and (-180 <= lon <= -64 or 144 <= lon <= 180), r
