"""Compare the public workbook's Tableau-computed five-row sheet with its snapshot's values.

--url fetches <sheet>.csv and <sheet>.png from a public viz with no warehouse or sign-in, keeps both
originals, and writes result.json. --csv takes a manually exported crosstab instead.

--manifest is required: the manifest.json of the export the sheet was built from, in its snapshot
directory. It must be an ACTUAL manifest from bi/public/export_snapshot.py, its public CSV files
must match its digests, and the sheet's snapshot_kind and snapshot_id, which come from the CSV rows,
must match it. So a sheet built from a local DuckDB export is refused. active_posting_days is
checked against the value the manifest recorded; the other metrics against projection.json.
A metric passes when abs(found - expected) is within the tolerance in projection.json (1e-8), so a value
rounded for display passes if the rounding stays within it. A percent-formatted value is refused. Exit 0
only when all five pass.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECTION = Path(__file__).resolve().parent / "projection.json"
OUT_ROOT = ROOT / ".tools/bi-public/reconcile"
METRICS = ("case_count", "certified_rate", "wage_p75", "active_posting_days", "oews_median")
KIND_ACTUAL = "ACTUAL_SNOWFLAKE_PUBLIC_MARTS"
KIND_DEV = "LOCAL_DUCKDB_DEV"
MANIFEST_SCHEMA = "sponsoratlas-bi-public-snapshot/1"
DEFAULT_SHEET = "Reconciliation"
USER_AGENT = "SponsorAtlas-reconcile/1.0"


class Refused(RuntimeError):
    """The input cannot be reconciled."""


def view_urls(url, sheet=None):
    """Accept a /views/<workbook>/<sheet> or /app/profile/<user>/viz/<workbook>/<sheet> URL."""
    parsed = urllib.parse.urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if "views" in parts:
        tail = parts[parts.index("views") + 1 :]
    elif "viz" in parts:
        tail = parts[parts.index("viz") + 1 :]
    else:
        raise Refused(f"Not a Tableau view URL: {url}")
    if not tail:
        raise Refused(f"No workbook in URL: {url}")
    workbook = re.sub(r"\.(csv|png)$", "", tail[0])
    name = sheet or DEFAULT_SHEET
    base = f"{parsed.scheme}://{parsed.netloc}/views/{workbook}/{name}"
    return {suffix: f"{base}.{suffix}?:showVizHome=no" for suffix in ("csv", "png")}


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def decode(raw):
    """Tableau CSV is UTF-8; a crosstab download is UTF-16 with tabs."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def parse_number(text):
    """Strip display formatting only. Returns None for an empty cell; raises when not numeric."""
    cleaned = text.strip().replace(" ", "").replace(",", "").replace("$", "")
    if cleaned == "":
        return None
    if cleaned.endswith("%"):
        raise Refused(f"Percent-formatted value {text!r}; the sheet must carry the full-precision number")
    return float(cleaned)


def parse_sheet(raw):
    """Return (values by metric, snapshot kind or None, snapshot id or None)."""
    text = decode(raw)
    dialect = "excel-tab" if "\t" in text.splitlines()[0] else "excel"
    rows = [row for row in csv.reader(io.StringIO(text), dialect) if any(cell.strip() for cell in row)]
    if len(rows) < 2:
        raise Refused("The sheet has no data rows")
    header = [cell.strip() for cell in rows[0]]
    values, kinds, ids = {}, set(), set()
    for row in rows[1:]:
        cells = [cell.strip() for cell in row]
        metric = next((cell for cell in cells if cell in METRICS), None)
        if metric is None:
            raise Refused(f"Unknown row in the sheet: {cells!r}")
        if metric in values:
            raise Refused(f"Duplicate metric {metric}")
        value_index = header.index("Measure Values") if "Measure Values" in header else len(cells) - 1
        values[metric] = parse_number(cells[value_index])
        for index, cell in enumerate(cells):
            column = header[index] if index < len(header) else ""
            if cell in (KIND_ACTUAL, KIND_DEV):
                kinds.add(cell)
            # Tableau shows the snapshot_id column as "Snapshot Id".
            elif column.lower().replace(" ", "_") == "snapshot_id" and cell:
                ids.add(cell)
    if len(kinds) > 1 or len(ids) > 1:
        raise Refused("The sheet mixes snapshot identities")
    return values, next(iter(kinds), None), next(iter(ids), None)


def compare(values, oracle, tolerance):
    results = []
    for metric in METRICS:
        expected = float(oracle[metric])
        found = values.get(metric)
        passed = found is not None and abs(found - expected) <= tolerance
        results.append({"metric": metric, "expected": expected, "found": found, "status": "PASS" if passed else "FAIL"})
    return results


def read_manifest(path):
    """An ACTUAL manifest from export_snapshot.py whose public files sit next to it, unchanged."""
    path = Path(path)
    data = json.loads(path.read_text())
    if data.get("schema") != MANIFEST_SCHEMA:
        raise Refused(f"{path} is not a bi/public snapshot manifest")
    if data.get("kind") != KIND_ACTUAL:
        raise Refused(f"Manifest kind {data.get('kind')!r} is refused")
    for name, record in data["files"].items():
        csv_path = path.parent / name
        if record.get("private"):
            continue
        if not csv_path.is_file() or hashlib.sha256(csv_path.read_bytes()).hexdigest() != record["sha256"]:
            raise Refused(f"{name} does not match the manifest's digest")
    return data


def reconcile(raw_csv, oracle, tolerance, *, manifest):
    values, kind, snapshot_id = parse_sheet(raw_csv)
    if kind == KIND_DEV:
        raise Refused("The sheet carries LOCAL_DUCKDB_DEV; a dev workbook cannot reconcile")
    if kind != KIND_ACTUAL:
        raise Refused("The sheet does not state an ACTUAL_SNOWFLAKE_PUBLIC_MARTS snapshot")
    data = read_manifest(manifest)
    if snapshot_id != data["snapshot_id"]:
        raise Refused(f"The sheet is from snapshot {snapshot_id}, not {data['snapshot_id']}")
    expected = {**oracle, "active_posting_days": data["oracle_source"]["active_posting_days"]}
    results = compare(values, expected, tolerance)
    return {"kind": kind, "snapshot_id": snapshot_id, "results": results,
            "passed": all(item["status"] == "PASS" for item in results)}  # fmt: skip


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="Public viz URL")
    source.add_argument("--csv", type=Path, help="Manually exported CSV or crosstab of the Reconciliation sheet")
    parser.add_argument("--sheet", default=DEFAULT_SHEET, help="Sheet URL name")
    parser.add_argument("--manifest", type=Path, required=True, help="manifest.json of the snapshot behind the sheet")
    parser.add_argument("--out", type=Path, help="Evidence directory (default .tools/bi-public/reconcile/<time>)")
    args = parser.parse_args(argv)
    projection = json.loads(PROJECTION.read_text())
    now = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or OUT_ROOT / now
    try:
        out.mkdir(parents=True, exist_ok=True)
        originals = {}
        if args.url:
            urls = view_urls(args.url, args.sheet)
            raw = fetch(urls["csv"])
            (out / "sheet.csv").write_bytes(raw)
            originals["csv_url"] = urls["csv"]
            try:
                (out / "sheet.png").write_bytes(fetch(urls["png"]))
                originals["png_url"] = urls["png"]
            except OSError as error:
                originals["png_error"] = type(error).__name__
        else:
            raw = args.csv.read_bytes()
            (out / "sheet.csv").write_bytes(raw)
            originals["csv_file"] = str(args.csv)
        result = reconcile(raw, projection["oracle"], projection["tolerance"], manifest=args.manifest)
    except (Refused, ValueError, OSError) as error:
        (out / "result.json").write_text(json.dumps({"passed": False, "refused": str(error)}, indent=2) + "\n")
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2
    result.update({"checked_at_utc": now, "tolerance": projection["tolerance"], "originals": originals,
                   "csv_sha256": hashlib.sha256(raw).hexdigest()})  # fmt: skip
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    for item in result["results"]:
        print(f"{item['status']} {item['metric']}: expected {item['expected']!r}, found {item['found']!r}")
    print(("PASS" if result["passed"] else "FAIL") + f" evidence: {out}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
