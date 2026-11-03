"""Replay small area/OEWS-extension references from the verified May2025 archive."""

import argparse
import csv
import json
from pathlib import Path

from er.io import digest

ROOT = Path(__file__).resolve().parents[1]
SHA = "86ffcbbeb27a96b7aa84d01801de71ee94ee9027452152caabebfdec9d764fde"
JSON_SHA = "56ea8a7aa33e1913c332e7d28d9f06742a408e746fb33c9a3fb5f383ced31dfa"
JSONL = ROOT / f"data/raw/bls/FY2025/BLS_FY2025__{SHA}.jsonl"
SPECIAL_MAJORS = {
    "00-0000": None,
    "21-1018": "21-0000",
    "25-2052": "25-0000",
    "25-9045": "25-0000",
    "51-2028": "51-0000",
    "53-1047": "53-0000",
}
VINTAGE = "OEWS_May2025"


def build(seed_dir, *, jsonl=JSONL):
    if digest(jsonl) != JSON_SHA:
        raise ValueError("BLS JSONL digest mismatch")
    areas, occupations, majors = {}, {}, set()
    with Path(jsonl).open() as source:
        for line in source:
            envelope = json.loads(line)
            if envelope["source_sha256"] != SHA or envelope["source_release"] != "FY2025":
                raise ValueError("BLS source provenance mismatch")
            row = json.loads(envelope["payload_json"])
            area = (row["AREA_TYPE"], row["AREA"])
            value = (row["AREA_TITLE"], row["PRIM_STATE"])
            if area in areas and areas[area][:2] != value:
                raise ValueError("area title/state conflict")
            areas.setdefault(area, (*value, envelope["source_row_number"]))
            if row["O_GROUP"] == "major":
                majors.add(row["OCC_CODE"])
            if row["OCC_CODE"] in SPECIAL_MAJORS:
                value = (row["OCC_TITLE"], row["O_GROUP"])
                old = occupations.setdefault(
                    row["OCC_CODE"], (*value, envelope["source_row_number"])
                )
                if old[:2] != value:
                    raise ValueError("OEWS occupation conflict")
    if (
        set(occupations) != set(SPECIAL_MAJORS)
        or not set(SPECIAL_MAJORS.values()) - {None} <= majors
    ):
        raise ValueError("missing verified occupation/major")
    seed_dir = Path(seed_dir)
    seed_dir.mkdir(parents=True, exist_ok=True)

    def write(name, header, rows):
        with (seed_dir / name).open("w", newline="") as output:
            writer = csv.writer(output, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows)

    write(
        "ref_bls_areas_2025.csv",
        [
            "area_type",
            "area_code",
            "area_title",
            "state_raw",
            "source_row_number",
            "geography_vintage",
            "source_sha256",
        ],
        [(t, a, *v, VINTAGE, SHA) for (t, a), v in sorted(areas.items())],
    )
    write(
        "ref_oews_2025.csv",
        [
            "soc_code",
            "soc_title",
            "soc_level",
            "source_row_number",
            "soc_vintage",
            "major_code",
            "classification_type",
            "source_sha256",
        ],
        [
            (
                code,
                *v,
                VINTAGE,
                SPECIAL_MAJORS[code],
                "AGGREGATE_TOTAL" if code == "00-0000" else "OEWS_SPECIFIC",
                SHA,
            )
            for code, v in sorted(occupations.items())
        ],
    )
    result = {
        "original_sha256": SHA,
        "jsonl_sha256": JSON_SHA,
        "reader": "archive JSONL payload_json",
        "survey_reference": "May2025",
        "counts": {"areas": len(areas), "occupations": len(occupations)},
    }
    (seed_dir / "bls_references.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed-dir", type=Path, default=ROOT / "dbt/seeds")
    print(json.dumps(build(p.parse_args().seed_dir), indent=2))
