"""Export the public Tableau snapshot from Snowflake or, for development, from local DuckDB.

--source snowflake connects as CUBE_SVC/ANALYST on SPONSORATLAS_XS, SELECT only. --source duckdb
reads the canonical marts read-only and costs no credits; its manifest kind is LOCAL_DUCKDB_DEV,
which reconciliation and publish builds refuse. Every public CSV carries snapshot_kind and
snapshot_id, so a sheet built from it can be tied to this manifest.

The source oracle runs first, so a mismatch costs no extract. It must match the fixed values in
projection.json. The written CSV files must then match the source oracle, including the total
posting values, which grow with each daily load. --source snowflake refuses while the orchestration
STOP marker exists.

employer_names_private.csv holds source employer names. It stays in the snapshot directory, and
build_workbook.py never packages it. --names most-filed works offline from that file and needs a
reviewed override JSON, because bi/dashboard-spec.md forbids source names in public projections
and showing them is the user's decision.
"""
import argparse
import csv
import datetime as dt
import decimal
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = Path(__file__).resolve().parent
PROJECTION_PATH = PUBLIC / "projection.json"
OUT_ROOT = ROOT / ".tools/bi-public"
CANONICAL_DUCKDB = ROOT / ".tools/facts/fy2025-full.duckdb"
STOP_MARKER = ROOT / ".tools/orchestration/STOP"

KIND_ACTUAL = "ACTUAL_SNOWFLAKE_PUBLIC_MARTS"
KIND_DEV = "LOCAL_DUCKDB_DEV"
# Cube's Parquet export shares KIND_ACTUAL (semantic/scripts/export_snowflake.py), so kind alone
# does not identify a manifest from this script.
MANIFEST_SCHEMA = "sponsoratlas-bi-public-snapshot/1"
SHORT_ID_LENGTH = 16  # "emp_" plus 12 hex digits; collisions refuse the export.
FETCH_ROWS = 50_000
TRIAL_MARGIN_SECONDS = 420  # Same seven-minute cutoff as the Cube and orchestration guards.
MIN_NAMED_CASES = 50
TOP_REVIEWED = 25
NAME_LABEL = "most-filed DOL name; grouping unvalidated"
NON_PERSON_TOKENS = {
    "ACADEMY", "AG", "AMERICA", "AMERICAN", "ASSOCIATES", "BANK", "CAPITAL", "CENTER", "CENTRE", "CLINIC",
    "CO", "COLLEGE", "COMPANY", "CONSULTING", "CORP", "CORPORATION", "DEPARTMENT", "ENGINEERING", "FOUNDATION",
    "GROUP", "GMBH", "HEALTH", "HEALTHCARE", "HOLDINGS", "HOSPITAL", "INC", "INCORPORATED", "INSTITUTE",
    "INTERNATIONAL", "LABS", "LABORATORIES", "LLC", "LLP", "LP", "LTD", "LIMITED", "MEDICAL", "NETWORK",
    "NETWORKS", "PARTNERS", "PC", "PLC", "PLLC", "SA", "SCHOOL", "SCHOOLS", "SERVICES", "SOFTWARE", "SOLUTIONS",
    "SYSTEM", "SYSTEMS", "TECH", "TECHNOLOGIES", "TECHNOLOGY", "TRUST", "UNIVERSITY", "USA",
}  # fmt: skip

PUBLIC_FILES = ("lca.csv", "postings.csv", "wages.csv", "benchmark.csv")
PRIVATE_NAMES = "employer_names_private.csv"
NAMED_LCA = "lca_most_filed.csv"
NAMES_RECORD = "names_applied.json"
SNAPSHOT_COLUMNS = ("snapshot_kind", "snapshot_id")
LCA_COLUMNS = ("case_id", "fiscal_year", "received_month", "soc", "soc_title", "metro_cbsa", "metro_title",
               "state", "is_certified", "annual_wage", "employer_id", "data_role", *SNAPSHOT_COLUMNS)  # fmt: skip


class Refused(RuntimeError):
    """The snapshot failed a check; nothing is published."""


def load_projection(path=PROJECTION_PATH):
    return json.loads(Path(path).read_text())


def sql_for(projection, name, schema):
    template = projection["queries"][name]
    return (template.replace("{s}", schema)
            .replace("{min_cases}", str(int(projection["benchmark_min_lca_cases"])))
            .replace("{top_metros}", str(int(projection["benchmark_top_metros"])))
            .replace("{survey_year}", str(int(projection["oews_survey_year"]))))  # fmt: skip


def format_cell(value):
    """Render a warehouse value as CSV text without losing precision."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, decimal.Decimal):
        return format(value, "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise Refused("Non-finite number in export")
        return repr(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def short_employer_id(full, seen):
    """Readable stable id; the mapping back to the full id stays in the private file."""
    if full == "UNKNOWN":
        return full
    short = full[:SHORT_ID_LENGTH]
    if seen.setdefault(short, full) != full:
        raise Refused("Short employer id collision")
    return short


def posting_key(posting_id):
    """posting_id embeds the board URL and its employer slug, so only a hash goes public."""
    return hashlib.sha256(posting_id.encode()).hexdigest()[:16]


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rows_of(cursor):
    columns = [item[0].lower() for item in cursor.description]
    while True:
        chunk = cursor.fetchmany(FETCH_ROWS)
        if not chunk:
            return
        for row in chunk:
            yield dict(zip(columns, row))


def stamped(rows, snapshot):
    for row in rows:
        yield {**row, **snapshot}


def write_csv(path, header, rows):
    count = 0
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow([format_cell(row[column]) for column in header])
            count += 1
    return count


def lca_rows(cursor, pattern, seen):
    for row in rows_of(cursor):
        title = row.pop("job_title") or ""
        row["data_role"] = 1 if pattern.search(title) else 0
        row["employer_id"] = short_employer_id(row["employer_id"], seen)
        yield row


def posting_rows(cursor, seen):
    for row in rows_of(cursor):
        row["posting_key"] = posting_key(row.pop("posting_id"))
        row["employer_id"] = short_employer_id(row["employer_id"], seen)
        yield row


def name_rows(cursor, seen):
    for row in rows_of(cursor):
        row["employer_id_full"] = row["employer_id"]
        row["employer_id"] = short_employer_id(row["employer_id"], seen)
        yield row


def oracle_from_source(run, projection, schema):
    cases, distinct, certified, wage_p75 = run(sql_for(projection, "oracle_lca", schema)).fetchone()
    if int(cases) != int(distinct):
        raise Refused("fact_lca_case is not unique on case_number")
    posting_rows_count, active, archived, archived_active = run(sql_for(projection, "oracle_postings", schema)).fetchone()
    estimates, median = run(sql_for(projection, "oracle_oews", schema)).fetchone()
    return {"case_count": int(cases), "posting_rows": int(posting_rows_count),
            "certified_rate": int(certified or 0) / int(cases) if cases else None,
            "wage_p75": None if wage_p75 is None else float(wage_p75),
            "active_posting_days": int(active or 0),
            "archive_posting_rows": int(archived or 0),
            "archive_active_posting_days": int(archived_active or 0),
            "oews_median": float(median) if int(estimates) == 1 and median is not None else None}  # fmt: skip


def oracle_from_csv(directory):
    """Recompute from the written files with an in-memory engine; no warehouse is involved."""
    import duckdb

    def table(name):
        path = str(Path(directory) / name).replace("'", "''")
        return f"read_csv('{path}',header=true,all_varchar=true,delim=',',quote='\"')"

    con = duckdb.connect(config={"threads": 2, "memory_limit": "1GB"})
    try:
        cases, distinct, certified, wage_p75 = con.execute(
            "select count(*),count(distinct case_id),sum(cast(is_certified as integer)),"
            f"quantile_cont(cast(annual_wage as double),0.75) from {table('lca.csv')}"
        ).fetchone()
        if cases != distinct:
            raise Refused("lca.csv is not unique on case_id")
        posting_rows_count, active, archived, archived_active = con.execute(
            "select count(*),sum(cast(is_active as integer)),"
            "sum(case when replay_kind='LOCAL_ARCHIVE_REPLAY' then 1 else 0 end),"
            "sum(case when replay_kind='LOCAL_ARCHIVE_REPLAY' then cast(is_active as integer) else 0 end)"
            f" from {table('postings.csv')}"
        ).fetchone()
        estimates, median = con.execute(
            "select count(*),max(cast(annual_median_wage as double)) from "
            f"{table('wages.csv')} where soc='15-1252' and area='1:99' and survey_year='2025'"
        ).fetchone()
    finally:
        con.close()
    return {"case_count": int(cases), "posting_rows": int(posting_rows_count),
            "certified_rate": int(certified or 0) / cases if cases else None,
            "wage_p75": None if wage_p75 is None else float(wage_p75),
            "active_posting_days": int(active or 0),
            "archive_posting_rows": int(archived or 0),
            "archive_active_posting_days": int(archived_active or 0),
            "oews_median": float(median) if estimates == 1 and median is not None else None}  # fmt: skip


def check_oracle(found, expected, tolerance, label):
    failures = []
    for metric, target in expected.items():
        value = found.get(metric)
        if value is None or abs(float(value) - float(target)) > tolerance:
            failures.append(f"{metric}: expected {target!r}, found {value!r}")
    if failures:
        raise Refused(f"Oracle mismatch ({label}): " + "; ".join(failures))


def check_trial(projection, now=None):
    now = now or dt.datetime.now(dt.UTC)
    end = dt.datetime.strptime(projection["trial_end_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
    if (end - now).total_seconds() <= TRIAL_MARGIN_SECONDS:
        raise Refused("Snowflake trial window has ended; export refused")


def connect_duckdb(path):
    import duckdb

    return duckdb.connect(str(path), read_only=True, config={"threads": 2, "memory_limit": "2GB"})


def connect_snowflake():
    """Only the key path reaches the connector; this process never opens the key file."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.snowflake_live import connect_options

    key_file = os.environ.get("SPONSORATLAS_CUBE_PRIVATE_KEY_FILE")
    if not key_file or not os.environ.get("SNOWFLAKE_ACCOUNT"):
        raise Refused("SNOWFLAKE_ACCOUNT and SPONSORATLAS_CUBE_PRIVATE_KEY_FILE are required")
    os.environ["SNOWFLAKE_ANALYST_PRIVATE_KEY_FILE"] = key_file
    os.environ.pop("SNOWFLAKE_WAREHOUSE", None)  # Always the helper's SPONSORATLAS_XS default.
    options = connect_options("ANALYST", query_tag="sponsoratlas:bi-public-export")
    options["session_parameters"].update(
        {"STATEMENT_TIMEOUT_IN_SECONDS": 240, "STATEMENT_QUEUED_TIMEOUT_IN_SECONDS": 60,
         "ABORT_DETACHED_QUERY": True}  # fmt: skip
    )
    passphrase = os.environ.get("SPONSORATLAS_CUBE_PRIVATE_KEY_PASSPHRASE")
    if passphrase:
        options["private_key_file_pwd"] = passphrase
    import snowflake.connector

    return snowflake.connector.connect(**options)


def snowflake_identity(run):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.snowflake_live import secondary_roles_disabled

    run("USE SECONDARY ROLES NONE")
    user, role, warehouse, secondary = run(
        "SELECT CURRENT_USER(),CURRENT_ROLE(),CURRENT_WAREHOUSE(),CURRENT_SECONDARY_ROLES()"
    ).fetchone()
    if (user, role, warehouse) != ("CUBE_SVC", "ANALYST", "SPONSORATLAS_XS"):
        raise Refused("Unexpected Snowflake session identity")
    if not secondary_roles_disabled(secondary):
        raise Refused("Secondary roles must be disabled")
    return {"user": user, "role": role, "warehouse": warehouse}


def export(source, *, out_root=OUT_ROOT, duckdb_path=CANONICAL_DUCKDB, projection=None, now=None):
    projection = projection or load_projection()
    now = now or dt.datetime.now(dt.UTC)
    if source == "snowflake":
        if STOP_MARKER.exists():
            raise Refused(f"STOP marker {STOP_MARKER} exists: Snowflake spend is stopped; export refused")
        check_trial(projection, now)
        kind, schema = KIND_ACTUAL, "ANALYTICS.MARTS"
        connection = connect_snowflake()
        cursor = connection.cursor()
        run = cursor.execute
        identity = snowflake_identity(run)
    elif source == "duckdb":
        kind, schema = KIND_DEV, "MARTS"
        connection = connect_duckdb(duckdb_path)
        run = connection.execute
        identity = {"user": "local", "role": "read-only file", "warehouse": None,
                    "database": Path(duckdb_path).name}  # fmt: skip
    else:
        raise Refused(f"Unknown source {source!r}")

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    destination = Path(out_root) / stamp
    stage = Path(out_root) / f".{stamp}-pending"
    if destination.exists() or stage.exists():
        raise Refused("Snapshot directories are never overwritten")
    stage.mkdir(parents=True, mode=0o700)
    snapshot = {"snapshot_kind": kind, "snapshot_id": f"{stamp}-{secrets.token_hex(4)}"}
    try:
        expected, tolerance = projection["oracle"], projection["tolerance"]
        source_oracle = oracle_from_source(run, projection, schema)
        check_oracle(source_oracle, expected, tolerance, "source marts")
        pattern = re.compile(projection["data_role"]["regex"], re.IGNORECASE)
        seen = {}
        queries = {name: sql_for(projection, name, schema) for name in projection["queries"]}
        counts = {
            "lca.csv": write_csv(stage / "lca.csv", LCA_COLUMNS, stamped(lca_rows(run(queries["lca"]), pattern, seen), snapshot)),
            "postings.csv": write_csv(
                stage / "postings.csv",
                ("posting_key", "observation_date", "is_active", "employer_id", "soc", "metro_cbsa",
                 "employer_mapping_status", "replay_kind", *SNAPSHOT_COLUMNS),  # fmt: skip
                stamped(posting_rows(run(queries["postings"]), seen), snapshot),
            ),
            "wages.csv": write_csv(
                stage / "wages.csv",
                ("soc", "soc_title", "area", "survey_year", "annual_median_wage", "median_raw", "wage_status", *SNAPSHOT_COLUMNS),
                stamped(rows_of(run(queries["wages"])), snapshot),
            ),
            "benchmark.csv": write_csv(
                stage / "benchmark.csv",
                ("soc", "soc_title", "soc_label", "metro_cbsa", "metro_title", "lca_cases", "metro_top25",
                 "lca_p50", "lca_p75", "lca_p90", "oews_median", "oews_status", "oews_survey_year", *SNAPSHOT_COLUMNS),  # fmt: skip
                stamped(rows_of(run(queries["benchmark"])), snapshot),
            ),
        }
        private = stage / PRIVATE_NAMES
        private.touch(mode=0o600)
        counts[PRIVATE_NAMES] = write_csv(
            private, ("employer_id", "employer_id_full", "source_employer_name", "cases"),
            name_rows(run(queries["employer_names"]), seen),
        )  # fmt: skip
        releases = [dict(row) for row in rows_of(run(queries["releases"]))]
        for row in releases:
            row["row_count"] = int(row["row_count"])
        csv_oracle = oracle_from_csv(stage)
        check_oracle(csv_oracle, source_oracle, tolerance, "written CSV files")
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "kind": kind,
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_time_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "stamp": stamp,
            "identity": identity,
            "files": {
                name: {"rows": rows, "sha256": sha256_file(stage / name), "bytes": (stage / name).stat().st_size,
                       "private": name == PRIVATE_NAMES}  # fmt: skip
                for name, rows in counts.items()
            },
            "queries": queries,
            "releases": releases,
            "oracle_expected": expected,
            "oracle_source": source_oracle,
            "oracle_csv": csv_oracle,
            "data_role": projection["data_role"],
            "projection_sha256": sha256_file(PROJECTION_PATH),
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        connection.close()
    return destination, manifest


def employers_from_private(snapshot):
    """Per employer: total cases and the most-filed source name, ties broken alphabetically."""
    totals, names = {}, {}
    with (Path(snapshot) / PRIVATE_NAMES).open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            employer, cases = row["employer_id"], int(row["cases"])
            totals[employer] = totals.get(employer, 0) + cases
            current = names.get(employer)
            candidate = (-cases, row["source_employer_name"])
            if current is None or candidate < current:
                names[employer] = candidate
    return {employer: {"cases": total, "name": names[employer][1]} for employer, total in totals.items()}


def looks_person_like(name):
    tokens = re.findall(r"[A-Z]+", name.upper())
    return 2 <= len(tokens) <= 4 and not NON_PERSON_TOKENS.intersection(tokens)


def review_scope(snapshot):
    employers = employers_from_private(snapshot)
    eligible = {key: value for key, value in employers.items()
                if key != "UNKNOWN" and value["cases"] >= MIN_NAMED_CASES}  # fmt: skip
    ranked = sorted(eligible, key=lambda key: (-eligible[key]["cases"], key))
    top = ranked[:TOP_REVIEWED]
    flagged = [key for key in ranked if looks_person_like(eligible[key]["name"])]
    return eligible, top, flagged


def write_review_template(snapshot, path):
    """Private by construction: it lists source names for a human to review."""
    eligible, top, flagged = review_scope(snapshot)
    entry = lambda key: {"employer_id": key, "most_filed_name": eligible[key]["name"],
                         "cases": eligible[key]["cases"], "label": eligible[key]["name"]}  # fmt: skip
    template = {"reviewed_by": "", "reviewed_at": "", "top_25": [entry(key) for key in top],
                "person_like": [{**entry(key), "decision": "other"} for key in flagged]}  # fmt: skip
    target = Path(path)
    target.touch(mode=0o600)
    target.write_text(json.dumps(template, indent=2, ensure_ascii=False) + "\n")
    return len(top), len(flagged)


def resolve_labels(snapshot, review_path):
    """Refuse unless the review covers the current top 25 and every flagged person-like name."""
    if not review_path or not Path(review_path).is_file():
        raise Refused("--names most-filed requires a reviewed override JSON (--review)")
    review = json.loads(Path(review_path).read_text())
    if not review.get("reviewed_by") or not review.get("reviewed_at"):
        raise Refused("Review file must state reviewed_by and reviewed_at")
    eligible, top, flagged = review_scope(snapshot)
    top_entries = {item["employer_id"]: item for item in review.get("top_25", [])}
    if set(top_entries) != set(top):
        raise Refused(f"Review must list exactly the current top {TOP_REVIEWED} employers")
    person_entries = {item["employer_id"]: item for item in review.get("person_like", [])}
    missing = [key for key in flagged if key not in person_entries]
    if missing:
        raise Refused(f"Review is missing {len(missing)} flagged person-like employers")
    labels = {key: value["name"] for key, value in eligible.items()}
    for key, item in top_entries.items():
        if not str(item.get("label", "")).strip():
            raise Refused("Every reviewed top-25 entry needs a label")
        labels[key] = item["label"].strip()
    for key, item in person_entries.items():
        decision = item.get("decision")
        if decision == "other":
            labels.pop(key, None)
        elif decision == "label" and str(item.get("label", "")).strip():
            labels[key] = item["label"].strip()
        else:
            raise Refused("Each person-like entry needs decision 'other', or 'label' with a label")
    return labels


def apply_names(snapshot, review_path):
    """Write lca_most_filed.csv with employer_label; lca.csv and the manifest stay untouched."""
    snapshot = Path(snapshot)
    labels = resolve_labels(snapshot, review_path)
    target = snapshot / NAMED_LCA
    rows, named_rows = 0, 0
    with (snapshot / "lca.csv").open(newline="", encoding="utf-8") as source, target.open(
        "w", newline="", encoding="utf-8"
    ) as sink:
        reader = csv.DictReader(source)
        writer = csv.DictWriter(sink, [*reader.fieldnames, "employer_label"], lineterminator="\n")
        writer.writeheader()
        for row in reader:
            label = labels.get(row["employer_id"])
            named_rows += label is not None
            row["employer_label"] = label if label is not None else "Other"
            writer.writerow(row)
            rows += 1
    record = {"mode": "most-filed", "label_note": NAME_LABEL, "min_cases": MIN_NAMED_CASES,
              "named_employers": len(labels), "named_rows": named_rows, "rows": rows,
              "file": NAMED_LCA, "sha256": sha256_file(target),
              "review_sha256": sha256_file(review_path)}  # fmt: skip
    (snapshot / NAMES_RECORD).write_text(json.dumps(record, indent=2) + "\n")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=("snowflake", "duckdb"))
    parser.add_argument("--names", choices=("ids", "most-filed"), default="ids")
    parser.add_argument("--snapshot", type=Path, help="Existing snapshot, for the offline --names step")
    parser.add_argument("--review", type=Path, help="Reviewed override JSON for --names most-filed")
    parser.add_argument("--write-review-template", type=Path, help="Write a private template for the reviewer")
    parser.add_argument("--duckdb-path", type=Path, default=CANONICAL_DUCKDB)
    parser.add_argument("--out-root", type=Path, default=OUT_ROOT)
    args = parser.parse_args(argv)
    try:
        if args.names == "most-filed":
            if not args.snapshot or args.source:
                parser.error("--names most-filed is offline: pass --snapshot and no --source")
            if args.write_review_template:
                top, flagged = write_review_template(args.snapshot, args.write_review_template)
                print(json.dumps({"review_template": str(args.write_review_template), "top": top, "flagged": flagged}))
                return 0
            record = apply_names(args.snapshot, args.review)
            print(json.dumps({key: record[key] for key in ("mode", "named_employers", "named_rows", "rows")}))
            return 0
        if not args.source:
            parser.error("--source is required")
        destination, manifest = export(args.source, out_root=args.out_root, duckdb_path=args.duckdb_path)
    except Refused as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 -- connector errors may echo connection options.
        print(f"Export failed ({type(error).__name__}); no snapshot published", file=sys.stderr)
        return 1
    print(json.dumps({"kind": manifest["kind"], "snapshot": str(destination),
                      "rows": {name: item["rows"] for name, item in manifest["files"].items()},
                      "oracle_csv": manifest["oracle_csv"]}, indent=2))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
