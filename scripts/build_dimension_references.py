"""Replay pinned official classification files; immutable originals and deterministic CSVs."""

import argparse
import csv
import io
import json
import re
import shutil
import zipfile
from pathlib import Path

import openpyxl

from er.io import digest

SOURCES = {
    "soc_structure_2018.xlsx": (
        "SOC2018",
        "ade08af40923266f3a854842e888ca3e93c15b26a147c20a2b12a61f4c4f4077",
        "https://www2.census.gov/programs-surveys/demo/guidance/industry-occupation/soc_structure_2018.xlsx",
    ),
    "list1_2023.xlsx": (
        "CBSA2023-07",
        "952c4b1e78acbb54e6ec9412434b7602fedacbf021736351a63c181bdb753629",
        "https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/2023/delineation-files/list1_2023.xlsx",
    ),
    "2023_Gaz_counties_national.zip": (
        "Census2023",
        "919df59ba90759cce85468c0337e898e4b39c08eaffce86ddd88fa41f1f7f0c8",
        "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_counties_national.zip",
    ),
}


def write_csv(path, header, rows):
    with path.open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def build(source_dir, archive_dir, seed_dir):
    source_dir, archive_dir, seed_dir = map(Path, (source_dir, archive_dir, seed_dir))
    originals = {}
    source_paths = {}
    metadata = []
    # Validate every original before generating anything.
    for name, (vintage, sha, url) in SOURCES.items():
        archived_name = f"{Path(name).stem}_{vintage}_{sha}{Path(name).suffix}"
        path = source_dir / name
        if not path.exists():
            path = source_dir / vintage / sha / archived_name
        source_paths[name] = path
        if digest(path) != sha:
            raise ValueError(f"source digest mismatch: {name}")
        archived = archive_dir / vintage / sha / archived_name
        if archived.exists() and digest(archived) != sha:
            raise ValueError(f"archive digest mismatch: {name}")
        originals[name] = archived
        metadata.append(
            {
                "official_filename": name,
                "archived_filename": archived_name,
                "vintage": vintage,
                "sha256": sha,
                "url": url,
                "bytes": path.stat().st_size,
            }
        )
    for name, archived in originals.items():
        archived.parent.mkdir(parents=True, exist_ok=True)
        if not archived.exists():
            with archived.open("xb") as dst, source_paths[name].open("rb") as src:
                shutil.copyfileobj(src, dst)
    seed_dir.mkdir(parents=True, exist_ok=True)
    workbook = openpyxl.load_workbook(
        originals["soc_structure_2018.xlsx"], read_only=True, data_only=True
    )
    sheet = workbook["2018 Structure"]
    if tuple(next(sheet.iter_rows(min_row=8, max_row=8, values_only=True)))[:4] != (
        "Major Group",
        "Minor Group",
        "Broad Group",
        "Detailed Occupation",
    ):
        raise ValueError("SOC header changed")
    hierarchy = [None] * 4
    soc = []
    for row in sheet.iter_rows(min_row=9, values_only=True):
        populated = [i for i in range(4) if row[i] is not None]
        if not populated:
            continue
        if len(populated) != 1 or not row[4]:
            raise ValueError("unexpected SOC hierarchy row")
        level = populated[0]
        code = str(row[level]).strip()
        if not re.fullmatch(r"\d{2}-\d{4}", code) or any(x is None for x in hierarchy[:level]):
            raise ValueError("invalid SOC parent/code")
        hierarchy[level:] = [code] + [None] * (3 - level)
        soc.append(
            (
                code,
                row[4],
                ["major", "minor", "broad", "detailed"][level],
                hierarchy[0],
                hierarchy[1],
                hierarchy[2],
                hierarchy[level - 1] if level else None,
                "SOC2018",
            )
        )
    workbook.close()
    if [sum(r[2] == level for r in soc) for level in ["major", "minor", "broad", "detailed"]] != [
        23,
        98,
        459,
        867,
    ] or len({r[0] for r in soc}) != len(soc):
        raise ValueError("SOC reference counts/uniqueness changed")
    write_csv(
        seed_dir / "ref_soc_2018.csv",
        [
            "soc_code",
            "soc_title",
            "soc_level",
            "major_code",
            "minor_code",
            "broad_code",
            "parent_code",
            "soc_vintage",
        ],
        soc,
    )
    workbook = openpyxl.load_workbook(originals["list1_2023.xlsx"], read_only=True, data_only=True)
    sheet = workbook["List 1"]
    header = list(next(sheet.iter_rows(min_row=3, max_row=3, values_only=True)))
    if header != [
        "CBSA Code",
        "Metropolitan Division Code",
        "CSA Code",
        "CBSA Title",
        "Metropolitan/Micropolitan Statistical Area",
        "Metropolitan Division Title",
        "CSA Title",
        "County/County Equivalent",
        "State Name",
        "FIPS State Code",
        "FIPS County Code",
        "Central/Outlying County",
    ]:
        raise ValueError("CBSA header changed")
    cbsa = []
    for row in sheet.iter_rows(min_row=4, values_only=True):
        if re.fullmatch(r"\d{5}", str(row[0] or "")):
            state, county = str(row[9]), str(row[10])
            if not re.fullmatch(r"\d{2}", state) or not re.fullmatch(r"\d{3}", county):
                raise ValueError("invalid county FIPS")
            cbsa.append((state + county, row[0], row[3], row[4], "CBSA2023-07"))
        elif any(row) and not str(row[0] or "").startswith(("Source:", "Note:")):
            raise ValueError("unexpected CBSA footer")
    workbook.close()
    if len(cbsa) != 1915 or len({r[0] for r in cbsa}) != 1915 or len({r[1] for r in cbsa}) != 935:
        raise ValueError("CBSA counts changed")
    with zipfile.ZipFile(originals["2023_Gaz_counties_national.zip"]) as z:
        if z.namelist() != ["2023_Gaz_counties_national.txt"] or z.testzip():
            raise ValueError("unexpected gazetteer ZIP")
        reader = csv.DictReader(
            io.StringIO(z.read(z.namelist()[0]).decode("utf-8-sig")), delimiter="\t"
        )
        reader.fieldnames = [h.strip() for h in reader.fieldnames]
        if reader.fieldnames != [
            "USPS",
            "GEOID",
            "ANSICODE",
            "NAME",
            "ALAND",
            "AWATER",
            "ALAND_SQMI",
            "AWATER_SQMI",
            "INTPTLAT",
            "INTPTLONG",
        ]:
            raise ValueError("gazetteer header changed")
        counties = [
            (
                r["GEOID"],
                r["USPS"],
                r["NAME"],
                r["INTPTLAT"].strip(),
                r["INTPTLONG"].strip(),
                "Census2023",
            )
            for r in reader
        ]
    if (
        len(counties) != 3222
        or len({r[0] for r in counties}) != 3222
        or not {r[0] for r in cbsa} <= {r[0] for r in counties}
    ):
        raise ValueError("county coverage changed")
    write_csv(
        seed_dir / "ref_county_2023.csv",
        [
            "county_fips",
            "state_code",
            "county_name",
            "county_internal_latitude",
            "county_internal_longitude",
            "county_reference_vintage",
        ],
        counties,
    )
    write_csv(
        seed_dir / "ref_county_cbsa_2023.csv",
        ["county_fips", "cbsa_code", "cbsa_title", "cbsa_type", "cbsa_vintage"],
        cbsa,
    )
    result = {
        "generator_version": 2,
        "counts": {"soc": len(soc), "county_2023": len(counties), "county_cbsa_2023": len(cbsa)},
        "sources": metadata,
        "seeds": {
            name: digest(seed_dir / name)
            for name in ["ref_county_2023.csv", "ref_county_cbsa_2023.csv", "ref_soc_2018.csv"]
        },
    }
    (seed_dir / "dimension_references.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=Path("data/reference"))
    parser.add_argument("--archive", type=Path, default=Path("data/reference"))
    parser.add_argument("--seeds", type=Path, default=Path("dbt/seeds"))
    args = parser.parse_args()
    print(json.dumps(build(args.sources, args.archive, args.seeds), indent=2))
