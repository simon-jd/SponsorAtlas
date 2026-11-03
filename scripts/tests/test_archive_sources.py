"""Synthetic release fixtures exercise archival, conversion, and corruption failures."""

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from openpyxl import Workbook

from scripts.archive_sources import archive_release, official_url, terraform_files
from scripts.source_files import iter_rows


def release(**kwargs):
    return {
        "id": "lca_fy2024_q4",
        "source": "LCA",
        "release": "FY2024_Q4",
        "format": "csv",
        "url": "https://www.dol.gov/example.csv",
        **kwargs,
    }


def test_archive_sha_provenance_and_unmapped_headers(tmp_path):
    source = tmp_path / "synthetic.csv"
    source.write_text("Employer Name,New_Unmapped_Column\nACME,0000123\n", encoding="utf-8")
    original_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    meta = archive_release(release(), source, tmp_path / "archive")
    assert meta["sha256"] == original_sha
    assert f"FY2024_Q4__{original_sha}" in meta["original_path"]
    assert meta["source_rows"] == 1
    record = json.loads(Path(meta["path"]).read_text())
    assert record["source_sha256"] == original_sha
    assert record["source_row_number"] == 1
    assert json.loads(record["payload_json"]) == {
        "Employer Name": "ACME",
        "New_Unmapped_Column": "0000123",
    }
    assert archive_release(release(), source, tmp_path / "archive") == meta
    config = terraform_files([meta], "https://archive.example/data", tmp_path / "archive")
    assert config["file_releases"]["lca_fy2024_q4"]["stream"] == "LCA_FY2024_Q4"
    assert original_sha in config["file_releases"]["lca_fy2024_q4"]["url"]


def test_gzip_derived_jsonl_is_deterministic_and_readable(tmp_path):
    import duckdb

    source = tmp_path / "synthetic.csv"
    source.write_text("CASE_NUMBER,H-1B_DEPENDENT\nI-1,No\nI-2,Yes\n", encoding="utf-8")
    meta = archive_release(release(derived_compression="gzip"), source, tmp_path / "a")
    assert meta["path"].endswith(".jsonl.gz")
    assert meta["derived_compression"] == "gzip"
    again = archive_release(release(derived_compression="gzip"), source, tmp_path / "b")
    # mtime/filename are not written into the gzip header, so the derived SHA is stable.
    assert again["derived_sha256"] == meta["derived_sha256"]
    rows = list(iter_rows(meta["path"], {"format": "jsonl"}))
    assert [n for n, _ in rows] == [1, 2]
    assert json.loads(rows[1][1]["payload_json"]) == {"CASE_NUMBER": "I-2", "H-1B_DEPENDENT": "Yes"}
    count = duckdb.sql(
        f"select count(*) from read_json('{meta['path']}', format='newline_delimited')"
    ).fetchall()[0][0]
    assert count == 2


def test_cli_archives_an_already_downloaded_file(tmp_path):
    from scripts.archive_sources import main

    source = tmp_path / "LCA_Disclosure_Data_FY2025_Q1.csv"
    source.write_text("A\nx\n")
    releases = tmp_path / "releases.json"
    releases.write_text(json.dumps({"releases": [release(derived_compression="gzip")]}))
    registry = tmp_path / "registry.json"
    code = main(
        [
            str(releases),
            "--archive-root",
            str(tmp_path / "raw"),
            "--registry",
            str(registry),
            "--local-file",
            f"lca_fy2024_q4={source}",
        ]
    )
    assert code == 0
    [meta] = json.loads(registry.read_text())
    assert meta["source_rows"] == 1
    assert meta["http"]["fetched_by"] == "curl -fsSL"
    assert source.exists()


@pytest.mark.parametrize("text", ["A,A\nx,y\n", "A,\nx,y\n", "A,B\nx\n", "A,B\nx,y,z\n"])
def test_csv_ambiguous_columns_fail(tmp_path, text):
    p = tmp_path / "bad.csv"
    p.write_text(text)
    with pytest.raises(ValueError):
        list(iter_rows(p, {"format": "csv"}))


def test_zip_xlsx_selection_preserves_zeroes_and_dates(tmp_path):
    p = tmp_path / "input.xlsx"
    workbook = Workbook()
    workbook.active.title = "Data"
    workbook.active.append(["Tax ID", "Amount"])
    workbook.active.append(["0012", 123])
    workbook.save(p)
    z = tmp_path / "release.zip"
    with ZipFile(z, "w") as archive:
        archive.write(p, "all_data.xlsx")
    meta = archive_release(
        release(format="xlsx", zip_member="all_data.xlsx", sheet="Data"), z, tmp_path / "archive"
    )
    payload = json.loads(json.loads(Path(meta["path"]).read_text())["payload_json"])
    assert payload == {"Tax ID": "0012", "Amount": "123"}
    assert meta["original_path"].endswith(".zip")


def test_xlsx_formula_rejected(tmp_path):
    p = tmp_path / "input.xlsx"
    workbook = Workbook()
    workbook.active.append(["Amount"])
    workbook.active.append(["=1+1"])
    workbook.save(p)
    with pytest.raises(ValueError, match="Formula"):
        list(iter_rows(p, {"format": "xlsx"}))


def test_xlsx_string_literal_formula_is_its_text(tmp_path):
    # DOL FY2025 Q2 writes text cells as ="..." so Excel keeps leading zeros.
    p = tmp_path / "input.xlsx"
    workbook = Workbook()
    workbook.active.append(["ZIP", "Firm", "Empty"])
    workbook.active.append(['="02139"', '="Fragomen, ""Del"" Rey"', '=""'])
    workbook.save(p)
    assert list(iter_rows(p, {"format": "xlsx"})) == [
        (1, {"ZIP": "02139", "Firm": 'Fragomen, "Del" Rey', "Empty": ""})
    ]


@pytest.mark.parametrize("formula", ['="a"&"b"', '=A1', '="a"+1', '=" unterminated'])
def test_xlsx_computed_formula_still_rejected(tmp_path, formula):
    p = tmp_path / "input.xlsx"
    workbook = Workbook()
    workbook.active.append(["Amount"])
    workbook.active.append([formula])
    workbook.save(p)
    with pytest.raises(ValueError, match="Formula"):
        list(iter_rows(p, {"format": "xlsx"}))


def test_listed_formula_cell_keeps_its_stored_text_and_others_still_fail(tmp_path):
    p = tmp_path / "input.xlsx"
    workbook = Workbook()
    workbook.active.append(["Publisher", "Other"])
    workbook.active.append(["=- AON, Radford _xlnm.Database", "x"])
    workbook.active.append(["ok", "=A1"])
    workbook.save(p)
    config = {"format": "xlsx", "formula_text_cells": ["1:Publisher"]}
    rows = iter_rows(p, config)
    assert next(rows) == (1, {"Publisher": "=- AON, Radford _xlnm.Database", "Other": "x"})
    with pytest.raises(ValueError, match="Formula in row 2"):
        next(rows)


def test_sha_pin_and_archive_corruption_fail(tmp_path):
    p = tmp_path / "input.csv"
    p.write_text("A\nx\n")
    with pytest.raises(ValueError, match="SHA256"):
        archive_release(release(expected_sha256="a" * 64), p, tmp_path / "archive")
    meta = archive_release(release(), p, tmp_path / "archive")
    Path(meta["original_path"]).write_text("A\ny\n")
    with pytest.raises(ValueError, match="Immutable"):
        archive_release(release(), p, tmp_path / "archive")


@pytest.mark.parametrize(
    "url",
    ["http://www.dol.gov/a.csv", "https://mirror.example/a.csv", "https://www.dol.gov.evil/a.csv"],
)
def test_requires_official_origin(url):
    with pytest.raises(ValueError):
        official_url("LCA", url)


def test_failed_download_preserves_previous_registry_and_tfvars(tmp_path, monkeypatch):
    from scripts import archive_sources

    release_file = tmp_path / "sources.json"
    release_file.write_text(json.dumps({"releases": [release()]}))
    registry = tmp_path / "registry.json"
    registry.write_text('[{"id":"previous"}]')
    tfvars = tmp_path / "files.tfvars.json"
    tfvars.write_text("unchanged")

    def fail(*args):
        raise OSError("synthetic network failure")

    monkeypatch.setattr(archive_sources, "download_release", fail)
    assert (
        archive_sources.main(
            [
                str(release_file),
                "--registry",
                str(registry),
                "--tfvars",
                str(tfvars),
                "--archive-root",
                str(tmp_path / "archive"),
                "--base-url",
                "https://archive.example",
            ]
        )
        == 2
    )
    assert registry.read_text() == '[{"id":"previous"}]'
    assert tfvars.read_text() == "unchanged"


@pytest.fixture
def archive_cli_pair(tmp_path, monkeypatch):
    """One real local archive conversion plus a previous registry/config pair; no HTTP."""
    from scripts import archive_sources

    archive_root = tmp_path / "archive"
    release_file = tmp_path / "releases.json"
    release_file.write_text(json.dumps({"releases": [release()]}))
    registry = tmp_path / "registry.json"
    registry.write_text("[]\n")
    tfvars = tmp_path / "files.tfvars.json"
    tfvars.write_text('{"file_releases":{"old_fixture":{}}}\n')
    previous = {registry: registry.read_bytes(), tfvars: tfvars.read_bytes()}

    def download(*args):
        source = tmp_path / "download.csv"
        source.write_text("Name\nArtificial fixture\n")
        return source, {"fixture": True}

    monkeypatch.setattr(archive_sources, "download_release", download)
    arguments = [
        str(release_file),
        "--registry",
        str(registry),
        "--tfvars",
        str(tfvars),
        "--archive-root",
        str(archive_root),
        "--base-url",
        "https://archive.example",
    ]
    return archive_sources, arguments, registry, tfvars, previous


def test_tfvars_generation_failure_preserves_previous_complete_pair(archive_cli_pair, monkeypatch):
    module, args, _registry, _tfvars, previous = archive_cli_pair
    original = module.terraform_files

    def fail_with_nonempty_registry(items, *args):
        if items:
            raise ValueError("synthetic tfvars generation failure")
        return original(items, *args)

    monkeypatch.setattr(module, "terraform_files", fail_with_nonempty_registry)
    assert module.main(args) == 2
    assert {path: path.read_bytes() for path in previous} == previous


def test_tfvars_write_failure_preserves_previous_complete_pair(archive_cli_pair, monkeypatch):
    module, args, _registry, tfvars, previous = archive_cli_pair
    original = Path.write_text

    def fail_tfvars_write(path, *args, **kwargs):
        if path == tfvars or path.name.startswith(f".{tfvars.name}.pending-"):
            raise OSError("synthetic config disk-write failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_tfvars_write)
    assert module.main(args) == 2
    assert {path: path.read_bytes() for path in previous} == previous


def test_second_publish_failure_restores_previous_complete_pair(archive_cli_pair, monkeypatch):
    module, args, registry, tfvars, previous = archive_cli_pair
    original = module.os.replace
    failed = [False]

    def fail_second_publish(source, destination):
        if Path(destination) == tfvars and ".pending-" in Path(source).name and not failed[0]:
            failed[0] = True
            raise OSError("synthetic second publish failure")
        return original(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_second_publish)
    assert module.main(args) == 2
    assert failed[0], "The regression must exercise a failure after the registry publication"
    assert {path: path.read_bytes() for path in previous} == previous
    assert not list(registry.parent.glob(".*.pending-*"))
    assert not list(registry.parent.glob(".*.previous-*"))


def test_complete_registry_tfvars_pair_publishes(archive_cli_pair):
    module, args, registry, tfvars, _previous = archive_cli_pair
    assert module.main(args) == 0
    metadata = json.loads(registry.read_text())
    configuration = json.loads(tfvars.read_text())["file_releases"]
    assert list(configuration) == [metadata[0]["id"]]
    assert configuration[metadata[0]["id"]]["stream"] == metadata[0]["table"].split(".")[-1]
    assert metadata[0]["sha256"] in configuration[metadata[0]["id"]]["url"]


def test_second_publish_failure_on_first_install_leaves_no_partial_pair(
    archive_cli_pair, monkeypatch
):
    module, args, registry, tfvars, _previous = archive_cli_pair
    registry.unlink()
    tfvars.unlink()
    original = module.os.replace

    def fail_second_publish(source, destination):
        if Path(destination) == tfvars and ".pending-" in Path(source).name:
            raise OSError("synthetic second publish failure")
        return original(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_second_publish)
    assert module.main(args) == 2
    assert not registry.exists()
    assert not tfvars.exists()
