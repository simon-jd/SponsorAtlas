"""Lossless tabular release readers. Blank records are ignored, malformed records fail."""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from zipfile import ZipFile

MAX_MEMBER_BYTES = 2 * 1024**3


def headers(values):
    result = list(values)
    if not result or any(not isinstance(x, str) or not x.strip() for x in result):
        raise ValueError("Headers must be nonempty strings; select the correct header_row")
    if len(result) != len(set(result)):
        raise ValueError("Duplicate original headers would lose columns")
    return result


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


# ="text" (inner quotes doubled) and nothing else is a constant, not a computation.
STRING_LITERAL_FORMULA = re.compile(r'="((?:[^"]|"")*)"')


def cell_text(cell, number, name, verbatim=frozenset()):
    if cell.data_type != "f":
        return as_text(cell.value)
    if f"{number}:{name}" in verbatim:
        # Reviewed source defect: free text the publisher's tool stored as a formula.
        return cell.value
    literal = STRING_LITERAL_FORMULA.fullmatch(cell.value or "")
    if literal is None:
        raise ValueError(f"Formula in row {number}; cached values cannot be trusted")
    return literal.group(1).replace('""', '"')


@contextmanager
def source_stream(path, config):
    """ZIP members are selected explicitly and read without filesystem extraction."""
    with Path(path).open("rb") as raw:
        if str(path).endswith(".gz"):
            with gzip.GzipFile(fileobj=raw) as stream:
                yield stream
        elif not config.get("zip_member"):
            yield raw
        else:
            with ZipFile(raw) as archive:
                member = archive.getinfo(config["zip_member"])
                if member.is_dir() or member.file_size > MAX_MEMBER_BYTES:
                    raise ValueError("Invalid or oversized ZIP member")
                with archive.open(member) as stream:
                    # openpyxl needs a seekable file, unlike ZipExtFile on some Python versions.
                    if config["format"] == "xlsx":
                        yield io.BytesIO(stream.read(MAX_MEMBER_BYTES + 1))
                    else:
                        yield stream


def iter_rows(path, config):
    """Yield 1-based original data row numbers and unrenamed key/value dictionaries."""
    fmt = config["format"]
    header_row = config.get("header_row", 1)
    if not isinstance(header_row, int) or header_row < 1:
        raise ValueError("header_row must be a positive integer")
    with source_stream(path, config) as binary:
        if fmt == "csv":
            with io.TextIOWrapper(
                binary, encoding=config.get("encoding", "utf-8-sig"), newline=""
            ) as text:
                reader = csv.reader(text, strict=True)
                for _ in range(header_row - 1):
                    next(reader)
                names = headers(next(reader, []))
                for number, row in enumerate(reader, start=1):
                    if not row or all(x == "" for x in row):
                        continue
                    if len(row) != len(names):
                        raise ValueError(
                            f"Row {number} has {len(row)} columns; expected {len(names)}"
                        )
                    yield number, dict(zip(names, row, strict=True))
        elif fmt == "xlsx":
            from openpyxl import load_workbook

            workbook = load_workbook(binary, read_only=True, data_only=False)
            try:
                name = config.get("sheet")
                if name is None and len(workbook.sheetnames) != 1:
                    raise ValueError("Multiple worksheets: configure sheet explicitly")
                worksheet = workbook[name or workbook.sheetnames[0]]
                rows = worksheet.iter_rows(min_row=header_row)
                names = headers([c.value for c in next(rows, [])])
                verbatim = frozenset(config.get("formula_text_cells", []))
                for number, cells in enumerate(rows, start=1):
                    if all(c.value is None for c in cells):
                        continue
                    if len(cells) != len(names):
                        raise ValueError(f"Row {number} has an unexpected column count")
                    yield number, {
                        n: cell_text(c, number, n, verbatim)
                        for n, c in zip(names, cells, strict=True)
                    }
            finally:
                workbook.close()
        elif fmt == "jsonl":
            for number, line in enumerate(binary, start=1):
                if not line.strip():
                    raise ValueError(f"Empty JSONL record at line {number}")
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise TypeError(f"JSONL record {number} is not an object")
                yield number, record
        else:
            raise ValueError(f"Unsupported format {fmt!r}; supported: csv, xlsx, jsonl")
