"""A real dbt local build: 25 mutations must fail data tests, one city-fill and two
ZIP-fill cases stay green and resolve the county, then the build restores green."""

import json
from pathlib import Path

import pytest

pytest.importorskip("duckdb", reason="install the locked staging extra to execute real dbt gates")
from scripts.staging_checks import verify


def test_real_dbt_build_and_mutation_gates(tmp_path):
    evidence = verify(tmp_path / "staging")
    assert evidence["dataset"] == "SYNTHETIC_ONLY"
    assert len(evidence["runs"]) == 32
    assert evidence["runs"][-1]["exit_code"] == 0


def test_official_lca_header_coverage_is_explicit():
    import csv

    root = Path(__file__).resolve().parents[2]
    official = json.loads((root / "dbt/source_contracts/lca_headers.json").read_text())
    with (root / "dbt/seeds/lca_column_map.csv").open() as file:
        mapping = list(csv.DictReader(file))
    for year, release in official.items():
        rows = [r for r in mapping if r["fiscal_year"] == year]
        # The map is per fiscal year: it must cover the header of every release in that year.
        headers = set(release["headers"]).union(
            *(extra["headers"] for extra in release.get("other_releases", {}).values())
        )
        assert {r["source_column"] for r in rows} == headers
        assert len(rows) == len(headers)
        assert all(r["reason"] and r["disposition"] in {"mapped", "excluded"} for r in rows)


def _loader_archives(directory):
    """Tiny synthetic envelopes test loader invariants, never real source acceptance."""
    import hashlib

    archives = []
    for source, release in [
        ("LCA", "FY2024_Q4"),
        ("LCA", "FY2025_Q1"),
        ("LCA", "FY2025_Q2"),
        ("LCA", "FY2025_Q3"),
        ("LCA", "FY2025_Q4"),
        ("USCIS", "FY2022"),
        ("USCIS", "FY2023"),
    ]:
        sha =hashlib.sha256(f"SYNTHETIC_LOADER_TEST:{source}:{release}".encode()).hexdigest()
        path = directory / f"{source}_{release}.jsonl"
        row = {
            "source_release": release,
            "source_sha256": sha,
            "source_url": "https://example.invalid/SYNTHETIC_LOADER_TEST",
            "source_row_number": 1,
            "payload_json": '{"dataset":"SYNTHETIC_LOADER_TEST"}',
        }
        path.write_text(json.dumps(row) + "\n")
        archives.append(
            {
                "source": source,
                "release": release,
                "sha256": sha,
                "source_rows": 1,
                "path": str(path),
            }
        )
    return archives


def test_loader_rejects_duplicate_revision_before_database_creation(tmp_path):
    from scripts.load_staging_files import load

    archives = _loader_archives(tmp_path)
    archives[-1] = dict(archives[0], sha256="a" * 64)
    database = tmp_path / "invalid.duckdb"
    with pytest.raises(ValueError, match="exactly once"):
        load(database, archives)
    assert not database.exists()


@pytest.mark.parametrize("problem", ["missing", "bad_hash"])
def test_loader_rejects_invalid_metadata_before_database_creation(tmp_path, problem):
    from scripts.load_staging_files import load

    archives = _loader_archives(tmp_path)
    if problem == "missing":
        archives.pop()
    else:
        archives[0]["sha256"] = "not-an-original-sha256"
    database = tmp_path / "invalid.duckdb"
    with pytest.raises(ValueError):
        load(database, archives)
    assert not database.exists()


def test_loader_covers_exactly_the_expected_releases_without_synthetic_official_rows(tmp_path):
    from scripts.load_staging_files import load
    from scripts.local_duckdb import connect_local

    archives = _loader_archives(tmp_path)
    database = tmp_path / "complete.duckdb"
    result = load(database, archives)
    assert {(s["source"], s["release"]) for s in result["sources"]} == {
        (m["source"], m["release"]) for m in archives
    }
    assert len(result["sources"]) == 7
    with connect_local(database) as conn:
        for meta in archives:
            table = f"RAW_{meta['source']}.{meta['source']}_{meta['release']}"
            assert conn.execute(f"select count(*) from {table}").fetchone()[0] == 1
            assert (
                conn.execute(f"select source_sha256 from {table}").fetchone()[0] == meta["sha256"]
            )


def test_alaska_composite_suffix_aliases_are_unambiguous():
    import csv

    root = Path(__file__).resolve().parents[2]
    with (root / "dbt/seeds/county_fips.csv").open() as file:
        rows = list(csv.DictReader(file))
    keys = [(r["state_code"], r["county_name"]) for r in rows]
    assert len(keys) == len(set(keys))
    mapping = {r["county_name"]: r["county_fips"] for r in rows if r["state_code"] == "AK"}
    for name, code in [
        ("JUNEAU", "02110"),
        ("SITKA", "02220"),
        ("WRANGELL", "02275"),
        ("YAKUTAT", "02282"),
    ]:
        assert mapping[name] == code
        assert mapping[f"{name} CITY AND BOROUGH"] == code
        assert f"{name} CITY AND" not in mapping


def test_python_connection_limits_match_actual_dbt_profile(tmp_path):
    import duckdb
    import yaml

    from scripts.local_duckdb import connect_local

    root = Path(__file__).resolve().parents[2]
    settings = yaml.safe_load((root / "dbt/profiles.yml").read_text())["sponsoratlas"]["outputs"][
        "local"
    ]["settings"]
    query = "select name,value from duckdb_settings() where name in ('memory_limit','threads','max_temp_directory_size','preserve_insertion_order') order by name"
    with duckdb.connect(":memory:", config=settings) as expected:
        expected_limits = expected.execute(query).fetchall()
    with connect_local(tmp_path / "limits.duckdb") as actual:
        actual_limits = actual.execute(query).fetchall()
    assert actual_limits == expected_limits
    assert dict(actual_limits)["threads"] == "1"
    assert dict(actual_limits)["preserve_insertion_order"] == "false"


def test_loader_cli_rejects_duplicate_revision_without_output(tmp_path):
    import subprocess
    import sys
    from tempfile import TemporaryDirectory

    root = Path(__file__).resolve().parents[2]
    with TemporaryDirectory(prefix="fix-r1-loader-", dir=root / ".tools/staging") as directory:
        work = Path(directory)
        archives = _loader_archives(work)
        archives[-1] = {**archives[0], "sha256": "a" * 64}
        archive_root = work / "archives"
        for index, meta in enumerate(archives):
            folder = archive_root / meta["source"] / meta["release"]
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{index}.provenance.json").write_text(json.dumps(meta))
        database, output = work / "bad.duckdb", work / "result.json"
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.load_staging_files",
                "--database",
                str(database),
                "--output",
                str(output),
                "--archive-root",
                str(archive_root),
            ],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        assert proc.returncode != 0
        assert "exactly once" in proc.stderr
        assert "COMPLETE_OFFICIAL" not in proc.stdout
        assert not database.exists() and not output.exists()


def test_loader_reads_only_lca_and_uscis_sidecars(tmp_path):
    from scripts.load_staging_files import official_archives

    for source, release in [("lca", "FY2025_Q1"), ("uscis", "FY2023"), ("bls", "FY2025")]:
        directory = tmp_path / source / release
        directory.mkdir(parents=True)
        (directory / f"{release}.provenance.json").write_text(
            json.dumps({"source": source.upper(), "release": release})
        )
    assert sorted(a["source"] for a in official_archives(tmp_path)) == ["LCA", "USCIS"]


def test_official_loader_starts_with_empty_official_tables(tmp_path):
    from scripts.local_duckdb import connect_local
    from scripts.staging_fixture import create_fixture

    database = tmp_path / "empty-official.duckdb"
    create_fixture(database, include_lca_uscis_fixtures=False)
    with connect_local(database) as conn:
        for source, release in [
            ("LCA", "FY2024_Q4"),
            ("LCA", "FY2025_Q1"),
            ("LCA", "FY2025_Q2"),
            ("LCA", "FY2025_Q3"),
            ("LCA", "FY2025_Q4"),
            ("USCIS", "FY2022"),
            ("USCIS", "FY2023"),
        ]:
            assert (
                conn.execute(f"select count(*) from RAW_{source}.{source}_{release}").fetchone()[0]
                == 0
            )
        assert conn.execute("select count(*) from RAW_BLS.BLS_FY2024").fetchone()[0] == 1


def test_county_generator_keeps_composite_suffix_and_real_ambiguity():
    from scripts.build_county_seed import county_rows

    records = [
        {
            "STATE": "AK",
            "STATEFP": "02",
            "COUNTYFP": "110",
            "COUNTYNAME": "Juneau City and Borough",
        },
        {"STATE": "AK", "STATEFP": "02", "COUNTYFP": "220", "COUNTYNAME": "Sitka City and Borough"},
        {
            "STATE": "AK",
            "STATEFP": "02",
            "COUNTYFP": "275",
            "COUNTYNAME": "Wrangell City and Borough",
        },
        {
            "STATE": "AK",
            "STATEFP": "02",
            "COUNTYFP": "282",
            "COUNTYNAME": "Yakutat City and Borough",
        },
        {"STATE": "VA", "STATEFP": "51", "COUNTYFP": "059", "COUNTYNAME": "Fairfax County"},
        {"STATE": "VA", "STATEFP": "51", "COUNTYFP": "600", "COUNTYNAME": "Fairfax city"},
    ]
    rows = county_rows(records)
    mapping = {(r[0], r[1]): r[3] for r in rows}
    for name, code in [
        ("JUNEAU", "02110"),
        ("SITKA", "02220"),
        ("WRANGELL", "02275"),
        ("YAKUTAT", "02282"),
    ]:
        assert mapping["AK", name] == mapping["AK", name + " CITY AND BOROUGH"] == code
    assert ("VA", "FAIRFAX") not in mapping
    assert mapping["VA", "FAIRFAX COUNTY"] == "51059"
    assert mapping["VA", "FAIRFAX CITY"] == "51600"
    assert len(rows) == len(mapping)
