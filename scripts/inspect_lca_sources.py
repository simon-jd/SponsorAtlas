"""Independently reconcile archived JSONL with nonempty worksheet XML rows, not dimensions."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def inspect(sidecar: Path) -> dict:
    meta = json.loads(sidecar.read_text())
    with ZipFile(meta["original_path"]) as zipped:
        if zipped.testzip() is not None:
            raise ValueError("ZIP integrity failure")
        # Verified workbooks each contain exactly one sheet. Refuse other layouts.
        sheets = [
            n
            for n in zipped.namelist()
            if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
        ]
        if len(sheets) != 1:
            raise ValueError("Expected verified single-sheet workbook")
        count, first, last, empty = 0, None, None, 0
        with zipped.open(sheets[0]) as stream:
            for _, row in ET.iterparse(stream, events=["end"]):
                if row.tag != NS + "row":
                    continue
                number = int(row.attrib["r"])
                if number <= meta["reader"]["header_row"]:
                    row.clear()
                    continue
                # Cell formatting alone is empty. Numeric zero, boolean zero and
                # shared-string index zero have <v> text and count as real data.
                populated = any(
                    (cell.find(NS + "v") is not None and cell.find(NS + "v").text is not None)
                    or cell.find(NS + "is") is not None
                    or cell.find(NS + "f") is not None
                    for cell in row.findall(NS + "c")
                )
                if populated:
                    count += 1
                    first = first or number
                    last = number
                else:
                    empty += 1
                row.clear()
    json_count = 0
    last_logical = None
    with Path(meta["path"]).open() as stream:
        for line in stream:
            record = json.loads(line)
            json_count += 1
            last_logical = record["source_row_number"]
    if count != json_count or count != meta["source_rows"]:
        raise ValueError(
            f"Independent counts disagree: XML={count}, JSONL={json_count}, metadata={meta['source_rows']}"
        )
    return {
        "release": meta["release"],
        "source_sha256": meta["sha256"],
        "xml_nonempty_data_rows": count,
        "xml_explicit_empty_rows": empty,
        "xml_first_data_row": first,
        "xml_last_data_row": last,
        "jsonl_rows": json_count,
        "jsonl_last_source_row_number": last_logical,
        "workbook_zip_integrity": "PASS",
        "coverage": "all nonempty rows of the downloaded official workbook; not an inference of full fiscal-year history",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = [inspect(p) for p in sorted(Path("data/raw/lca").glob("*/*.provenance.json"))]
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
