"""Reconciliation parsing and refusals. No network and no warehouse."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

PUBLIC = Path(__file__).resolve().parents[1] / "public"
sys.path.insert(0, str(PUBLIC))
import reconcile_public as rc  # noqa: E402

ORACLE = json.loads((PUBLIC / "projection.json").read_text())["oracle"]
TOLERANCE = json.loads((PUBLIC / "projection.json").read_text())["tolerance"]
SNAPSHOT_ID = "20261106T090000Z-1a2b3c4d"
ACTIVE = 4993
GOOD = {"case_count": "713,467", "certified_rate": "0.9777789302098064", "wage_p75": "156,383.6300000000",
        "active_posting_days": "4,993", "oews_median": "$135,980.0000000000"}  # fmt: skip


def sheet(values=GOOD, kind=rc.KIND_ACTUAL, snapshot=SNAPSHOT_ID, header="snapshot_id"):
    lines = [f"snapshot_kind,{header},Measure Names,Measure Values"]
    lines += [f'{kind},{snapshot},{metric},"{value}"' for metric, value in values.items()]
    return ("\n".join(lines) + "\n").encode()


def make_manifest(directory, **changes):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "lca.csv").write_text("case_id\nI-1\n")
    (directory / "employer_names_private.csv").write_text("kept out of the check\n")
    manifest = {
        "schema": rc.MANIFEST_SCHEMA,
        "kind": rc.KIND_ACTUAL,
        "snapshot_id": SNAPSHOT_ID,
        "oracle_source": {"active_posting_days": ACTIVE},
        "files": {
            "lca.csv": {"sha256": hashlib.sha256(b"case_id\nI-1\n").hexdigest(), "private": False},
            "employer_names_private.csv": {"sha256": "not-checked", "private": True},
        },
        **changes,
    }
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


@pytest.fixture
def manifest(tmp_path):
    return make_manifest(tmp_path / "snapshot")


def check(raw, manifest):
    return rc.reconcile(raw, ORACLE, TOLERANCE, manifest=manifest)


def test_formatted_values_pass(manifest):
    result = check(sheet(), manifest)
    assert result["passed"] and result["snapshot_id"] == SNAPSHOT_ID
    assert [item["status"] for item in result["results"]] == ["PASS"] * 5
    active = next(item for item in result["results"] if item["metric"] == "active_posting_days")
    assert active["expected"] == ACTIVE


def test_utf16_tab_crosstab_passes(manifest):
    text = "snapshot_kind\tsnapshot_id\tMeasure Names\tMeasure Values\n"
    text += "".join(f"{rc.KIND_ACTUAL}\t{SNAPSHOT_ID}\t{metric}\t{value}\n" for metric, value in GOOD.items())
    assert check(text.encode("utf-16"), manifest)["passed"]


def test_utf8_bom_and_tableau_caption_pass(manifest):
    assert check(b"\xef\xbb\xbf" + sheet(), manifest)["passed"]
    assert check(sheet(header="Snapshot Id"), manifest)["passed"]


@pytest.mark.parametrize(
    "metric, value",
    [("case_count", "713,468"), ("certified_rate", "0.9777790302098064"), ("wage_p75", "156,383.6300001000"),
     ("active_posting_days", "86"), ("oews_median", "135,981.0000000000")],
)  # fmt: skip
def test_mismatch_fails_that_metric_only(manifest, metric, value):
    result = check(sheet({**GOOD, metric: value}), manifest)
    assert not result["passed"]
    assert [item["metric"] for item in result["results"] if item["status"] == "FAIL"] == [metric]


def test_tolerance_boundary(manifest):
    assert check(sheet({**GOOD, "wage_p75": "156383.630000005"}), manifest)["passed"]
    assert not check(sheet({**GOOD, "wage_p75": "156383.63000002"}), manifest)["passed"]


def test_rounded_display_precision_fails(manifest):
    assert not check(sheet({**GOOD, "certified_rate": "0.98"}), manifest)["passed"]


def test_percent_format_is_refused(manifest):
    with pytest.raises(rc.Refused, match="Percent"):
        check(sheet({**GOOD, "certified_rate": "97.78%"}), manifest)


def test_missing_metric_fails_and_empty_value_fails(manifest):
    partial = {key: value for key, value in GOOD.items() if key != "oews_median"}
    assert not check(sheet(partial), manifest)["passed"]
    assert not check(sheet({**GOOD, "oews_median": ""}), manifest)["passed"]


def test_dev_kind_is_refused_even_when_numbers_match(manifest):
    with pytest.raises(rc.Refused, match="LOCAL_DUCKDB_DEV"):
        check(sheet(kind=rc.KIND_DEV), manifest)


def test_missing_kind_is_refused(manifest):
    raw = "Measure Names,Measure Values\n" + "".join(f'{metric},"{value}"\n' for metric, value in GOOD.items())
    with pytest.raises(rc.Refused, match="ACTUAL"):
        check(raw.encode(), manifest)


def test_duplicate_and_unknown_rows_are_refused(manifest):
    raw = sheet() + f"{rc.KIND_ACTUAL},{SNAPSHOT_ID},case_count,1\n".encode()
    with pytest.raises(rc.Refused, match="Duplicate"):
        check(raw, manifest)
    raw = sheet() + f"{rc.KIND_ACTUAL},{SNAPSHOT_ID},mystery,1\n".encode()
    with pytest.raises(rc.Refused, match="Unknown"):
        check(raw, manifest)


def test_a_sheet_with_a_typed_actual_label_is_refused_against_a_dev_manifest(tmp_path):
    dev = make_manifest(tmp_path / "dev", kind=rc.KIND_DEV)
    with pytest.raises(rc.Refused, match="Manifest kind"):
        check(sheet(), dev)


def test_a_sheet_with_no_snapshot_id_is_refused(manifest):
    raw = "snapshot_kind,Measure Names,Measure Values\n" + "".join(
        f'{rc.KIND_ACTUAL},{metric},"{value}"\n' for metric, value in GOOD.items()
    )
    with pytest.raises(rc.Refused, match="snapshot None"):
        check(raw.encode(), manifest)


def test_a_sheet_from_another_export_is_refused(manifest):
    with pytest.raises(rc.Refused, match="not " + SNAPSHOT_ID):
        check(sheet(snapshot="20261106T035802Z-deadbeef"), manifest)


def test_the_cube_parquet_manifest_is_refused_although_it_shares_the_kind(tmp_path):
    cube = tmp_path / "export-evidence.json"
    cube.write_text(json.dumps({"kind": rc.KIND_ACTUAL, "files": {}}))
    with pytest.raises(rc.Refused, match="not a bi/public snapshot manifest"):
        check(sheet(), cube)


def test_a_changed_or_missing_public_file_is_refused(manifest):
    (manifest.parent / "lca.csv").write_text("case_id\nI-2\n")
    with pytest.raises(rc.Refused, match="lca.csv"):
        check(sheet(), manifest)
    (manifest.parent / "lca.csv").unlink()
    with pytest.raises(rc.Refused, match="lca.csv"):
        check(sheet(), manifest)


def test_the_private_file_is_not_needed_next_to_the_manifest(manifest):
    (manifest.parent / "employer_names_private.csv").unlink()
    assert check(sheet(), manifest)["passed"]


def test_manifest_is_required(tmp_path, capsys):
    path = tmp_path / "export.csv"
    path.write_bytes(sheet())
    with pytest.raises(SystemExit) as stopped:
        rc.main(["--csv", str(path), "--out", str(tmp_path / "out")])
    assert stopped.value.code == 2
    assert "--manifest" in capsys.readouterr().err


@pytest.mark.parametrize(
    "url",
    ["https://public.tableau.com/views/SponsorAtlasPublic/Dashboard?:language=en-US&:display_count=n",
     "https://public.tableau.com/app/profile/some.user/viz/SponsorAtlasPublic/Dashboard",
     "https://public.tableau.com/views/SponsorAtlasPublic/Reconciliation.csv"],
)  # fmt: skip
def test_view_urls_target_the_five_row_sheet(url):
    urls = rc.view_urls(url)
    assert urls["csv"] == "https://public.tableau.com/views/SponsorAtlasPublic/Reconciliation.csv?:showVizHome=no"
    assert urls["png"] == "https://public.tableau.com/views/SponsorAtlasPublic/Reconciliation.png?:showVizHome=no"


def test_non_view_url_is_refused():
    with pytest.raises(rc.Refused):
        rc.view_urls("https://public.tableau.com/app/discover")


def test_cli_url_keeps_originals_and_writes_result(tmp_path, monkeypatch, capsys, manifest):
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return sheet() if ".csv" in url else b"\x89PNG"

    monkeypatch.setattr(rc, "fetch", fake_fetch)
    out = tmp_path / "out"
    code = rc.main(["--url", "https://public.tableau.com/views/W/S", "--manifest", str(manifest), "--out", str(out)])
    assert code == 0 and len(fetched) == 2
    assert (out / "sheet.csv").read_bytes() == sheet()
    assert (out / "sheet.png").read_bytes() == b"\x89PNG"
    result = json.loads((out / "result.json").read_text())
    assert result["passed"] and result["originals"]["csv_url"] == fetched[0]
    assert result["snapshot_id"] == SNAPSHOT_ID
    lines = capsys.readouterr().out.splitlines()
    assert sum(line.startswith("PASS ") for line in lines[:-1]) == 5 and lines[-1].startswith("PASS evidence")


def test_cli_csv_mismatch_prints_fail_and_exits_1(tmp_path, capsys, manifest):
    path = tmp_path / "export.csv"
    path.write_bytes(sheet({**GOOD, "case_count": "1"}))
    assert rc.main(["--csv", str(path), "--manifest", str(manifest), "--out", str(tmp_path / "out")]) == 1
    output = capsys.readouterr().out
    assert "FAIL case_count" in output and "PASS wage_p75" in output


def test_cli_dev_csv_refused_exit_2(tmp_path, capsys, manifest):
    path = tmp_path / "export.csv"
    path.write_bytes(sheet(kind=rc.KIND_DEV))
    assert rc.main(["--csv", str(path), "--manifest", str(manifest), "--out", str(tmp_path / "out")]) == 2
    assert not json.loads((tmp_path / "out/result.json").read_text())["passed"]
    assert "REFUSED" in capsys.readouterr().err
