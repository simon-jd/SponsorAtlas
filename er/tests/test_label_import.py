"""er.label_import on synthetic workbooks/CSVs only; nothing is written to er/labels."""

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from er.label_import import COLUMNS, EVIDENCE_FROM_SOURCE_ROWS, convert, main, read_source

ROOT = Path(__file__).resolve().parents[2]
WHEN = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
HEADER = ["pair_id", "left_id", "right_id", "split", "label", "hard_case", "rationale", "evidence"]
RECORDS = [
    {"unique_id": u, "source_system": s, "source_employer_key": k, "eligible": True}
    for u, s, k in [
        ("a", "lca", "ka"),
        ("b", "uscis", "kb"),
        ("c", "lca", "kc"),
        ("d", "lca", "kd"),
    ]
]
ROWS = [
    ["p1", "a", "b", "train", 1, 0, "同一雇主", "row 12 / row 40"],
    ["p2", "c", "d", "holdout", 0, 1, "different EIN", "IRS lookup"],
    ["p3", "a", "c", "train", None, None, None, None],  # not labelled yet
    ["p4", "a", "gone", "holdout", 1, 1, "same", "src"],  # endpoint no longer in the input
]


def workbook(path, rows=ROWS, reviewer="Test Reviewer"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "pairs"
    ws.append(HEADER)
    for r in rows:
        ws.append(r)
    notes = wb.create_sheet("说明")
    notes.append(["说明", "盲标表"])
    notes.append(["标注人", reviewer])
    wb.save(path)
    return path


def csv_file(path, rows=ROWS):
    with path.open("w", newline="", encoding="utf-8-sig") as f:  # Excel's "CSV UTF-8" has a BOM
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows([["" if v is None else v for v in r] for r in rows])
    return path


def test_output_columns_are_exactly_the_pairs_csv_header():
    with (ROOT / "er/labels/pairs.csv").open(newline="") as f:
        assert next(csv.reader(f)) == COLUMNS


def test_workbook_reviewer_comes_from_the_notes_sheet(tmp_path):
    rows, reviewer, meta = read_source(workbook(tmp_path / "w.xlsx"))
    assert reviewer == "Test Reviewer"
    assert meta["format"] == "xlsx" and meta["sheet"] == "pairs"
    assert [r["label"] for r in rows] == ["1", "0", "", "1"]  # Excel numbers -> "1"/"0"


def test_convert_fills_source_keys_skips_empty_and_unmatched(tmp_path):
    rows, reviewer, _ = read_source(workbook(tmp_path / "w.xlsx"))
    out, summary = convert(rows, reviewer, RECORDS, reviewed_at=WHEN)
    assert [(r["left_id"], r["right_id"]) for r in out] == [("a", "b"), ("c", "d")]
    first = out[0]
    assert list(first) == COLUMNS
    assert first["label_method"] == "human" and first["reviewer"] == "Test Reviewer"
    assert first["reviewed_at"] == "2026-10-29T12:00:00+00:00"
    assert (first["left_source_system"], first["left_source_employer_key"]) == ("lca", "ka")
    assert (first["right_source_system"], first["right_source_employer_key"]) == ("uscis", "kb")
    assert first["rationale"] == "同一雇主" and first["split"] == "train"
    assert summary["rows_read"] == 4 and summary["written"] == 2
    assert summary["skipped_empty_label"] == {"count": 1, "pair_ids": ["p3"]}
    assert summary["skipped_unmatched"]["count"] == 1
    assert summary["skipped_unmatched"]["pairs"][0]["pair_id"] == "p4"
    assert summary["skipped_unmatched"]["pairs"][0]["reason"] == "RIGHT_ABSENT"


def test_csv_needs_an_explicit_reviewer_and_keeps_utf8(tmp_path):
    rows, reviewer, meta = read_source(csv_file(tmp_path / "pairs.csv"))
    assert reviewer is None and meta["format"] == "csv"
    with pytest.raises(ValueError, match="reviewer"):
        convert(rows, reviewer, RECORDS, reviewed_at=WHEN)
    out, _ = convert(rows, "CSV Reviewer", RECORDS, reviewed_at=WHEN)
    assert out[0]["rationale"] == "同一雇主" and out[0]["reviewer"] == "CSV Reviewer"


@pytest.mark.parametrize(
    "row, message",
    [
        (["p9", "a", "b", "train", 2, 0, "r", "e"], "label"),
        (["p9", "a", "b", "train", "yes", 0, "r", "e"], "label"),
        (["p9", "a", "b", "train", 1, None, "r", "e"], "hard_case"),
        (["p9", "a", "b", "test", 1, 0, "r", "e"], "split"),
        (["p9", "a", "b", "train", 1, 0, "", "e"], "rationale"),
        (["p9", "a", "b", "train", 1, 0, "r", ""], "evidence"),
        (["p9", "a", "a", "train", 1, 0, "r", "e"], "self"),
    ],
)
def test_invalid_labelled_rows_are_refused(tmp_path, row, message):
    rows, reviewer, _ = read_source(workbook(tmp_path / "w.xlsx", rows=[row]))
    with pytest.raises(ValueError, match=message):
        convert(rows, reviewer, RECORDS, reviewed_at=WHEN)


def test_duplicate_pair_is_refused(tmp_path):
    rows, reviewer, _ = read_source(workbook(tmp_path / "w.xlsx", rows=[ROWS[0], ROWS[0]]))
    with pytest.raises(ValueError, match="duplicate"):
        convert(rows, reviewer, RECORDS, reviewed_at=WHEN)


def test_pairs_must_be_the_frozen_pairs_with_their_split(tmp_path):
    rows, reviewer, _ = read_source(workbook(tmp_path / "w.xlsx", rows=ROWS[:2]))
    frozen = [{"left_id": "b", "right_id": "a", "split": "train"},
              {"left_id": "c", "right_id": "d", "split": "train"}]  # fmt: skip
    with pytest.raises(ValueError, match="split"):
        convert(rows, reviewer, RECORDS, reviewed_at=WHEN, frozen=frozen)
    frozen[1]["split"] = "holdout"
    _, summary = convert(rows, reviewer, RECORDS, reviewed_at=WHEN, frozen=frozen)
    assert summary["frozen_pairs"] == {"checked": 2, "all_frozen": True}
    with pytest.raises(ValueError, match="not in the frozen"):
        convert(rows, reviewer, RECORDS, reviewed_at=WHEN, frozen=frozen[:1])


def test_workbook_reviewer_conflicting_with_the_flag_is_refused(tmp_path):
    source = workbook(tmp_path / "w.xlsx")
    inp = tmp_path / "input"
    inp.mkdir()
    pq.write_table(pa.Table.from_pylist(RECORDS), inp / "records.parquet")
    argv = ["--source", str(source), "--input", str(inp), "--output", str(tmp_path / "o.csv")]
    with pytest.raises(SystemExit, match="reviewer"):
        main([*argv, "--reviewer", "Someone Else"])


def test_cli_writes_csv_and_manifest_and_refuses_to_overwrite_labels(tmp_path):
    source = workbook(tmp_path / "w.xlsx")
    inp = tmp_path / "input"
    inp.mkdir()
    pq.write_table(pa.Table.from_pylist(RECORDS), inp / "records.parquet")
    (inp / "input.json").write_text(json.dumps({"records_sha256": "abc"}))
    out = tmp_path / "pairs.csv"
    main(["--source", str(source), "--input", str(inp), "--output", str(out)])
    with out.open(newline="", encoding="utf-8") as f:
        written = list(csv.DictReader(f))
    assert [r["label"] for r in written] == ["1", "0"]
    manifest = json.loads(out.with_suffix(".manifest.json").read_text())
    assert manifest["reviewer"] == "Test Reviewer"
    assert manifest["reviewed_at_meaning"].startswith("conversion time")
    assert manifest["input_records_sha256"] == "abc"
    assert manifest["skipped_empty_label"]["count"] == 1
    assert manifest["gate_validation"].startswith("NOT_MET")  # 2 pairs < 500
    with pytest.raises(SystemExit, match="already holds labels"):
        main(["--source", str(source), "--input", str(inp), "--output", str(out)])


def test_header_only_output_may_be_filled(tmp_path):
    out = tmp_path / "pairs.csv"
    out.write_text(",".join(COLUMNS) + "\n")
    source = csv_file(tmp_path / "src.csv")
    inp = tmp_path / "input"
    inp.mkdir()
    pq.write_table(pa.Table.from_pylist(RECORDS), inp / "records.parquet")
    main(["--source", str(source), "--input", str(inp), "--output", str(out), "--reviewer", "R"])
    assert len(out.read_text().splitlines()) == 3


# The coordinator's workbook: annotated headers, the source rows shown to the reviewer.
SHIPPED_HEADER = [
    "序号", "split", "左·出现记录", "右·出现记录", "label\n(1/0)", "hard_case\n(1/0)",
    "rationale", "evidence（可选）", "pair_id", "left_id", "right_id",
]  # fmt: skip


def shipped_workbook(path, evidence=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "pairs"
    ws.append(SHIPPED_HEADER)
    ws.append(
        [1, "train", "LCA FY2025_Q3 第12行", "USCIS FY2023 第40行", 1, 0, "同名同邮编", evidence]
        + ["p1", "a", "b"]
    )
    wb.create_sheet("说明").append(["标注人", "Test Reviewer"])
    wb.save(path)
    return path


def test_annotated_headers_of_the_shipped_workbook_are_read(tmp_path):
    rows, _, _ = read_source(shipped_workbook(tmp_path / "w.xlsx", evidence="州登记"))
    assert (rows[0]["label"], rows[0]["hard_case"], rows[0]["evidence"]) == ("1", "0", "州登记")


def test_empty_evidence_may_name_the_source_rows_the_reviewer_saw(tmp_path):
    rows, reviewer, _ = read_source(shipped_workbook(tmp_path / "w.xlsx"))
    with pytest.raises(ValueError, match="empty evidence"):
        convert(rows, reviewer, RECORDS, reviewed_at=WHEN)
    out, summary = convert(
        rows, reviewer, RECORDS, reviewed_at=WHEN, evidence_from_source_rows=True
    )
    assert out[0]["evidence"] == (
        "标注表所列原件行（审阅者未另附外部证据）：左 LCA FY2025_Q3 第12行；右 USCIS FY2023 第40行"
    )
    assert summary["evidence_from_source_rows"] == {
        "count": 1,
        "meaning": EVIDENCE_FROM_SOURCE_ROWS,
    }


def test_source_row_evidence_needs_the_source_row_columns():
    row = dict(zip(HEADER, ["p1", "a", "b", "train", "1", "0", "same", ""]))
    with pytest.raises(ValueError, match="出现记录"):
        convert([row], "R", RECORDS, reviewed_at=WHEN, evidence_from_source_rows=True)
