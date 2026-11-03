"""Convert the returned human-label workbook into er/labels/pairs.csv (er/labels/README.md).

Source: `SponsorAtlas-ER-人工标注.xlsx` (sheet `pairs`: pair_id, left_id, right_id, split,
label, hard_case, rationale, evidence; sheet `说明`: the reviewer's name next to 标注人), or
the `pairs` sheet saved as UTF-8 CSV together with --reviewer. Every row becomes
label_method=human with that reviewer; reviewed_at is the conversion time (the workbook
has no per-row review time) and the manifest says so. Rows with an empty label, and rows
whose endpoint is no longer in the ER input, are skipped and counted. Source system and
key come from the ER input records. Invalid labelled rows stop the conversion; nothing
is written then. An output that already holds label rows is never overwritten.
"""

import argparse
import csv
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from er.evaluate import pair_key, validate_labels
from er.io import digest
from er.label_alignment import align, read_pairs, read_records

# The header of er/labels/pairs.csv.
COLUMNS = [
    "left_id", "right_id", "left_source_system", "left_source_employer_key",
    "right_source_system", "right_source_employer_key", "label", "hard_case",
    "label_method", "reviewer", "reviewed_at", "evidence", "rationale", "split",
]  # fmt: skip
SOURCE_COLUMNS = [
    "pair_id", "left_id", "right_id", "split", "label", "hard_case", "rationale", "evidence",
]  # fmt: skip
SHEET, NOTES, REVIEWER_LABEL = "pairs", "说明", "标注人"
REVIEWED_AT_MEANING = (
    "conversion time of this import (UTC); the workbook records no per-pair review time"
)
# Columns of the coordinator's workbook that show the reviewer where each record occurs.
SOURCE_ROW_COLUMNS = ("左·出现记录", "右·出现记录")
EVIDENCE_FROM_SOURCE_ROWS = (
    "evidence was empty in the workbook; it names the official source rows the workbook "
    "showed the reviewer (左·出现记录 / 右·出现记录), not external evidence"
)


def _text(value):
    """Cell value as text; Excel stores 1/0 as int or float."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _xlsx(path):
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if SHEET not in wb.sheetnames:
            raise ValueError(f"workbook has no sheet {SHEET!r}")
        cells = [[_text(v) for v in row] for row in wb[SHEET].iter_rows(values_only=True)]
        reviewer = None
        if NOTES in wb.sheetnames:
            for row in wb[NOTES].iter_rows(values_only=True):
                values = [_text(v) for v in row]
                for i, value in enumerate(values):
                    if value.rstrip("：:") == REVIEWER_LABEL:
                        reviewer = next((v for v in values[i + 1 :] if v), None)
                if reviewer:
                    break
    finally:
        wb.close()
    return cells, reviewer


def read_source(path):
    """(rows as dicts of text, reviewer or None, source metadata)."""
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        cells, reviewer, fmt = *_xlsx(path), "xlsx"
    elif path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as f:
            cells, reviewer, fmt = [[c.strip() for c in r] for r in csv.reader(f)], None, "csv"
    else:
        raise ValueError("source must be the .xlsx workbook or its pairs sheet as .csv")
    cells = [r for r in cells if any(r)]
    if not cells:
        raise ValueError("empty pairs sheet")
    # "label\n(1/0)" or "evidence（可选）" in the coordinator's workbook: keep the leading name.
    header = [re.split(r"[\s(（]", h, maxsplit=1)[0] for h in cells[0]]
    named = [h for h in header if h]
    if len(set(named)) != len(named):
        raise ValueError(f"pairs sheet has duplicate columns {header}")
    missing = [c for c in SOURCE_COLUMNS if c not in header]
    if missing:
        raise ValueError(f"pairs sheet lacks columns {missing}")
    rows = [dict(zip(header, r + [""] * (len(header) - len(r)))) for r in cells[1:]]
    meta = {
        "path": str(path.resolve()),
        "sha256": digest(path),
        "format": fmt,
        "sheet": SHEET if fmt == "xlsx" else None,
    }
    return rows, reviewer, meta


def _check(row):
    pid = row.get("pair_id") or f"{row['left_id']}|{row['right_id']}"
    if row["label"] not in {"0", "1"}:
        raise ValueError(f"pair {pid}: label must be 1 or 0, got {row['label']!r}")
    if row["hard_case"] not in {"0", "1"}:
        raise ValueError(f"pair {pid}: hard_case must be 1 or 0, got {row['hard_case']!r}")
    if row["split"] not in {"train", "holdout"}:
        raise ValueError(f"pair {pid}: split must be train or holdout, got {row['split']!r}")
    for field in ("left_id", "right_id", "rationale", "evidence"):
        if not row[field]:
            raise ValueError(f"pair {pid}: empty {field}")
    if row["left_id"] == row["right_id"]:
        raise ValueError(f"pair {pid}: self pair")


def _source_row_evidence(row):
    left, right = (row.get(c, "") for c in SOURCE_ROW_COLUMNS)
    if not (left and right):
        pid = row.get("pair_id") or f"{row['left_id']}|{row['right_id']}"
        raise ValueError(f"pair {pid}: empty evidence and no {'/'.join(SOURCE_ROW_COLUMNS)}")
    return f"标注表所列原件行（审阅者未另附外部证据）：左 {left}；右 {right}"


def convert(
    rows, reviewer, records, *, reviewed_at, frozen=None, evidence_from_source_rows=False
):
    """Return (pairs.csv rows, summary). Raises ValueError on any invalid labelled row.

    With evidence_from_source_rows, an empty evidence cell becomes the source rows the
    workbook showed the reviewer, and the summary counts and explains those rows.
    """
    if not reviewer or not reviewer.strip():
        raise ValueError(f"no reviewer: none next to {REVIEWER_LABEL} and no --reviewer given")
    summary = {"rows_read": len(rows)}
    if frozen is not None:
        splits = {pair_key(p["left_id"], p["right_id"]): p["split"] for p in frozen}
        for row in rows:
            key = pair_key(row["left_id"], row["right_id"])
            if key not in splits:
                raise ValueError(f"pair {row.get('pair_id')}: not in the frozen pair list")
            if splits[key] != row["split"]:
                raise ValueError(f"pair {row.get('pair_id')}: split differs from the frozen split")
        summary["frozen_pairs"] = {"checked": len(rows), "all_frozen": True}
    labelled, empty, seen, filled = [], [], set(), 0
    for row in rows:
        if row["label"] == "":
            empty.append(row.get("pair_id"))
            continue
        if evidence_from_source_rows and not row["evidence"]:
            row = {**row, "evidence": _source_row_evidence(row)}
            filled += 1
        _check(row)
        key = pair_key(row["left_id"], row["right_id"])
        if key in seen:
            raise ValueError(f"pair {row.get('pair_id')}: duplicate pair")
        seen.add(key)
        labelled.append(row)
    alignment = align(labelled, records)
    unmatched = {u["index"]: u for u in alignment["unmatched"]}
    by_id = {r["unique_id"]: r for r in records}
    stamp = reviewed_at.astimezone(UTC).isoformat()
    out = []
    for i, row in enumerate(labelled):
        if i in unmatched:
            continue
        left, right = by_id[row["left_id"]], by_id[row["right_id"]]
        out.append(
            {
                "left_id": row["left_id"],
                "right_id": row["right_id"],
                "left_source_system": left["source_system"],
                "left_source_employer_key": left["source_employer_key"],
                "right_source_system": right["source_system"],
                "right_source_employer_key": right["source_employer_key"],
                "label": row["label"],
                "hard_case": row["hard_case"],
                "label_method": "human",
                "reviewer": reviewer.strip(),
                "reviewed_at": stamp,
                "evidence": row["evidence"],
                "rationale": row["rationale"],
                "split": row["split"],
            }
        )
    summary["written"] = len(out)
    if evidence_from_source_rows:
        summary["evidence_from_source_rows"] = {
            "count": filled,
            "meaning": EVIDENCE_FROM_SOURCE_ROWS,
        }
    summary["skipped_empty_label"] = {"count": len(empty), "pair_ids": empty}
    summary["skipped_unmatched"] = {
        "count": len(unmatched),
        "pairs": [
            {"pair_id": labelled[i].get("pair_id"), "reason": u["reason"],
             "left_id": u["left_id"], "right_id": u["right_id"]}
            for i, u in sorted(unmatched.items())
        ],
    }  # fmt: skip
    return out, summary


def _holds_labels(path):
    if not path.exists():
        return False
    with path.open(newline="", encoding="utf-8") as f:
        return sum(1 for row in csv.reader(f) if any(row)) > 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, type=Path, help=".xlsx workbook or UTF-8 .csv")
    p.add_argument("--input", required=True, type=Path, help="current ER input directory")
    p.add_argument("--output", required=True, type=Path, help="pairs.csv to write")
    p.add_argument("--reviewer", help="required for CSV; must match the workbook if given")
    p.add_argument("--frozen", type=Path, help="frozen pair list (left_id,right_id,split)")
    p.add_argument(
        "--evidence-from-source-rows",
        action="store_true",
        help="fill an empty evidence cell with the workbook's 出现记录 source rows (counted)",
    )
    a = p.parse_args(argv)
    if _holds_labels(a.output):
        raise SystemExit(f"{a.output} already holds labels; refusing to overwrite")
    try:
        rows, sheet_reviewer, source = read_source(a.source)
        if a.reviewer and sheet_reviewer and a.reviewer.strip() != sheet_reviewer:
            raise ValueError(f"--reviewer differs from the workbook reviewer {sheet_reviewer!r}")
        frozen = read_pairs(a.frozen) if a.frozen else None
        now = datetime.now(UTC)
        out, summary = convert(
            rows,
            sheet_reviewer or a.reviewer,
            read_records(a.input),
            reviewed_at=now,
            frozen=frozen,
            evidence_from_source_rows=a.evidence_from_source_rows,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, COLUMNS)
        writer.writeheader()
        writer.writerows(out)
    try:
        validate_labels(out)
        gate = "MET"
    except ValueError as exc:
        gate = f"NOT_MET: {exc}"
    input_json = a.input / "input.json"
    manifest = {
        "source": source,
        "reviewer": out[0]["reviewer"] if out else (sheet_reviewer or a.reviewer),
        "reviewed_at": now.isoformat(),
        "reviewed_at_meaning": REVIEWED_AT_MEANING,
        "input": str(a.input.resolve()),
        "input_records_sha256": json.loads(input_json.read_text()).get("records_sha256")
        if input_json.exists()
        else None,
        **summary,
        "gate_validation": gate,
        "output": str(a.output.resolve()),
        "output_sha256": digest(a.output),
    }
    a.output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps({k: v for k, v in manifest.items() if k != "skipped_unmatched"},
                     ensure_ascii=False, indent=1))  # fmt: skip


if __name__ == "__main__":
    main()
