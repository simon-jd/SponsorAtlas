"""Load one UTC day of public job-board observations into Snowflake RAW as AIRBYTE_SVC / LOADER.

A script, not Airbyte. The boards in ingestion/board_api/live_boards.yaml are fetched with
scripts.public_board_probe, archived under .tools/boards/<date>/ and parsed by the same CDK
manifest as the local replay (scripts.load_fact_inputs.replay_board). A complete same-date
archive is reused unless --refetch, so retries and reruns load identical rows. The date then
replaces its rows in RAW.LCA.FACTS_INPUT_JOB_OBSERVATIONS in one transaction.

    python -m scripts.load_board_observations --date YYYY-MM-DD --output manifest.json
        [--refetch] [--dry-run --duckdb PATH]

A board whose fields the job fact reads are mostly missing or of the wrong type counts as failed,
so MIN_BOARDS and the provider-zero refusal apply before anything reaches RAW.

--dry-run never connects to Snowflake; it runs the same replacement on a DuckDB file.
Exit 0: loaded and verified. Exit 2: refused by a safety check, nothing loaded. Otherwise: error.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import shutil
import signal
import sys
import tempfile
import time
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

from er.io import digest, quote
from scripts.canonical_paths import CANONICAL
from scripts.load_fact_inputs import replay_board
from scripts.load_snowflake_raw import JOB_OBSERVATIONS, LIVE_REPLAY_KIND, relative
from scripts.local_duckdb import connect_local
from scripts.public_board_probe import probe

ROOT = Path(__file__).resolve().parents[1]
BOARDS = ROOT / "ingestion/board_api/live_boards.yaml"
ARCHIVE_ROOT = ROOT / ".tools/boards"
INDEX = "archive.json"
COLUMNS = [
    "job_key", "source_provider", "source_board", "source_url", "payload_json",
    "observed_at_utc", "source_sha256", "source_deleted_at", "replay_kind",
]  # fmt: skip
TIMESTAMPS = {"observed_at_utc", "source_deleted_at"}
LOAD_TABLE = f"{JOB_OBSERVATIONS}_LOAD"
# Snowflake lets a COPY read only the target table's own table stage, so this is the temp table's.
STAGE = "@RAW.LCA.%FACTS_INPUT_JOB_OBSERVATIONS_LOAD"
MIN_BOARDS = 10
MIN_PRIOR_SHARE = 0.5
ATTEMPTS = 2
RETRY_SECONDS = 5
BOARD_SECONDS = 60
MIN_FIELD_SHARE = 0.9


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _object_or_null(value) -> bool:
    return value is None or isinstance(value, dict)


def _text_or_null(value) -> bool:
    return value is None or isinstance(value, str)


def _bool_or_null(value) -> bool:
    return value is None or isinstance(value, bool)


# The payload fields dbt/models/marts/fact_job_posting_daily.sql reads, per provider.
FIELD_RULES = {
    "greenhouse": {"title": _text, "absolute_url": _text, "location": _object_or_null},
    "lever": {"text": _text, "hostedUrl": _text, "categories": _object_or_null},
    "ashby": {"title": _text, "jobUrl": _text, "location": _text_or_null, "isListed": _bool_or_null},
}


class Refused(Exception):
    """A safety check failed: nothing is loaded and the CLI exits 2."""

    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse would exit 2, which this CLI's callers read as a safety refusal.
        self.exit(64, f"{self.prog}: error: {message}\n")


def utc_today() -> date:
    return datetime.now(UTC).date()


def load_boards(path: Path = BOARDS) -> list[tuple[str, str]]:
    listed = yaml.safe_load(Path(path).read_text())
    return [(provider, board) for provider, boards in listed.items() for board in boards]


@contextlib.contextmanager
def time_limit(seconds: float):
    """Wall-clock limit for one board; the probe's socket timeout only bounds each read."""

    def expired(_signum, _frame):
        raise TimeoutError(f"board exceeded {seconds}s")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def fetch_board(provider: str, board: str, root: Path) -> dict:
    """One attempt plus one retry; a board that still fails is recorded, not raised."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with time_limit(BOARD_SECONDS):
                pages = probe(provider, board, root)["pages"]
            return {"pages": pages, "http_status": pages[-1]["http_status"], "attempts": attempt}
        except Exception as exc:  # Every failure mode means "this board failed".
            status, error = getattr(exc, "code", None), f"{type(exc).__name__}: {exc}"[:300]
        if attempt < ATTEMPTS:
            time.sleep(RETRY_SECONDS)
    return {"pages": [], "http_status": status, "attempts": ATTEMPTS, "error": error}


def fetch_archive(day: date, directory: Path, boards) -> dict:
    """Fetch sequentially, gzip each page next to the index, and write the index last."""
    raw = directory / "raw"
    entries = []
    for provider, board in boards:
        fetched = fetch_board(provider, board, raw)
        whole = hashlib.sha256()
        pages = []
        for page in fetched["pages"]:
            source = Path(page["path"])
            body = source.read_bytes()
            whole.update(body)
            name = f"{source.name}.gz"
            with gzip.GzipFile(directory / name, "wb", mtime=0) as output:
                output.write(body)
            source.unlink()
            pages.append({**{k: page[k] for k in ("url", "http_status", "observed_at_utc",
                                                  "sha256", "rows")}, "file": name})  # fmt: skip
        entries.append({
            "provider": provider, "board": board, "http_status": fetched["http_status"],
            "attempts": fetched["attempts"], "error": fetched.get("error"),
            "sha256": whole.hexdigest() if pages else None, "pages": pages,
        })  # fmt: skip
    shutil.rmtree(raw, ignore_errors=True)
    index = {"date": str(day), "boards": entries}
    (directory / INDEX).write_text(json.dumps(index, indent=2) + "\n")
    return index


def read_complete_archive(directory: Path) -> dict | None:
    """The index is written last and the directory renamed into place, so it marks completeness."""
    try:
        index = json.loads((directory / INDEX).read_text())
    except FileNotFoundError:
        return None
    files = (directory / page["file"] for entry in index["boards"] for page in entry["pages"])
    return index if all(path.is_file() for path in files) else None


def promote(staging: Path, directory: Path) -> None:
    old = directory.with_name(f".{directory.name}.old")
    shutil.rmtree(old, ignore_errors=True)
    if directory.exists():
        directory.rename(old)
    staging.rename(directory)
    shutil.rmtree(old, ignore_errors=True)


def observation(row: dict) -> dict:
    observed = datetime.fromisoformat(row["observed_at_utc"]).astimezone(UTC).replace(tzinfo=None)
    return {
        **{column: row[column] for column in COLUMNS},
        "observed_at_utc": observed,
        "replay_kind": LIVE_REPLAY_KIND,
    }


def field_shares(provider: str, rows: list[dict]) -> dict[str, float]:
    """Per required field, the share of rows where it has the type the fact expects."""
    payloads = [json.loads(row["payload_json"]) for row in rows]
    if not payloads:
        return {}
    return {
        field: round(sum(valid(payload.get(field)) for payload in payloads) / len(payloads), 4)
        for field, valid in FIELD_RULES[provider].items()
    }


def parse_archive(directory: Path, index: dict) -> tuple[list[dict], list[dict]]:
    """Rows and the per-board summary. A damaged archive raises; a malformed board is recorded."""
    rows, summary = [], []
    with tempfile.TemporaryDirectory(prefix="board-observations-") as work:
        for entry in index["boards"]:
            board = {k: entry[k] for k in ("provider", "board", "http_status", "attempts",
                                           "error", "sha256")}  # fmt: skip
            board |= {"pages": len(entry["pages"]), "rows": 0}
            pages = []
            for page in entry["pages"]:
                path = Path(work) / page["file"].removesuffix(".gz")
                path.write_bytes(gzip.decompress((directory / page["file"]).read_bytes()))
                if digest(path) != page["sha256"]:
                    raise ValueError(f"archive digest mismatch: {page['file']}")
                pages.append({**page, "path": str(path)})
            if pages:
                meta = {
                    "provider": entry["provider"],
                    "board": entry["board"],
                    "rows": sum(page["rows"] for page in pages),
                    "pages": pages,
                }
                try:
                    parsed = [observation(row) for row in replay_board(meta)]
                    board["field_shares"] = field_shares(entry["provider"], parsed)
                except Exception as exc:  # Counted against the board threshold.
                    board["error"] = f"parse: {type(exc).__name__}: {exc}"[:300]
                else:
                    shares = board["field_shares"]
                    if bad := {k: v for k, v in shares.items() if v < MIN_FIELD_SHARE}:
                        board["error"] = f"fields below {MIN_FIELD_SHARE:.0%} valid: {bad}"
                    else:
                        rows.extend(parsed)
                        board["rows"] = len(parsed)
            summary.append(board)
    return rows, summary


def local_refusals(rows: list[dict], summary: list[dict], day: date) -> list[str]:
    reasons = []
    succeeded = [board for board in summary if board["rows"]]
    for provider in sorted({board["provider"] for board in summary}):
        if not any(board["provider"] == provider for board in succeeded):
            reasons.append(f"provider {provider} returned zero rows")
    if len(succeeded) < MIN_BOARDS:
        reasons.append(f"only {len(succeeded)} boards succeeded, need {MIN_BOARDS}")
    if outside := {str(row["observed_at_utc"].date()) for row in rows} - {str(day)}:
        reasons.append(f"observations fall outside {day}: {sorted(outside)}")
    return reasons


def prior_refusals(total: int, prior: dict | None) -> list[str]:
    if prior and total < prior["rows"] * MIN_PRIOR_SHARE:
        return [f"{total} rows is below 50% of the {prior['rows']} stored for {prior['date']}"]
    return []


def column_ddl(timestamp_type: str) -> str:
    return ", ".join(f"{c} {timestamp_type if c in TIMESTAMPS else 'VARCHAR'}" for c in COLUMNS)


def write_parquet(rows: list[dict], path: Path) -> None:
    with connect_local(":memory:") as c:
        c.execute(f"create table load_rows ({column_ddl('TIMESTAMP')})")
        marks = ", ".join("?" for _ in COLUMNS)
        c.executemany(
            f"insert into load_rows values ({marks})",
            [[row[column] for column in COLUMNS] for row in rows],
        )
        c.execute(f"copy load_rows to {quote(path)} (format parquet)")


class SnowflakeTarget:
    table, mark = JOB_OBSERVATIONS, "%s"

    def __init__(self):
        from scripts.snowflake_live import connect

        self.connection = connect("LOADER", query_tag="sponsoratlas:board-observations")

    def execute(self, sql, params=None):
        return self.connection.cursor().execute(sql, params)

    def stage(self, parquet: Path) -> str:
        """Create a session temp table, PUT to its stage and COPY into it. The temp table has no
        COPY history, so a reloaded file is never skipped as already loaded."""
        columns = column_ddl("TIMESTAMP_NTZ")
        self.execute(f"CREATE OR REPLACE TEMPORARY TABLE {LOAD_TABLE} ({columns})")
        self.execute(f"PUT 'file://{parquet}' {STAGE} AUTO_COMPRESS = FALSE OVERWRITE = TRUE")
        self.execute(
            f"COPY INTO {LOAD_TABLE} FROM {STAGE} FILES = ('{parquet.name}')"
            " FILE_FORMAT = (TYPE = PARQUET USE_LOGICAL_TYPE = TRUE)"
            " MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE ON_ERROR = ABORT_STATEMENT PURGE = TRUE"
        )
        return LOAD_TABLE

    def close(self):
        self.connection.close()


class DuckDBTarget:
    table, mark = "FACTS_INPUT.job_observations", "?"

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = connect_local(path)
        self.execute("create schema if not exists FACTS_INPUT")
        self.execute(f"create table if not exists {self.table} ({column_ddl('TIMESTAMP')})")

    def execute(self, sql, params=None):
        return self.connection.execute(sql, params or [])

    def stage(self, parquet: Path) -> str:
        return f"read_parquet({quote(parquet)})"

    def close(self):
        self.connection.close()


def prior_day(target, day: date) -> dict | None:
    """The most recent stored date before this one, so a skipped day cannot disable the check."""
    observed = "CAST(OBSERVED_AT_UTC AS DATE)"
    row = target.execute(
        f"SELECT {observed}, COUNT(*) FROM {target.table}"
        f" WHERE {observed} < CAST({target.mark} AS DATE) GROUP BY 1 ORDER BY 1 DESC LIMIT 1",
        [str(day)],
    ).fetchone()
    return {"date": str(row[0]), "rows": int(row[1])} if row else None


def replace_date(target, source: str, day: date, expected: int) -> int:
    """Delete the UTC date and insert, verifying the count before the transaction commits."""
    names = ", ".join(COLUMNS)
    in_date = f"CAST(OBSERVED_AT_UTC AS DATE) = CAST({target.mark} AS DATE)"
    target.execute("BEGIN")
    try:
        target.execute(f"DELETE FROM {target.table} WHERE {in_date}", [str(day)])
        target.execute(f"INSERT INTO {target.table} ({names}) SELECT {names} FROM {source}")
        count = target.execute(
            f"SELECT COUNT(*) FROM {target.table} WHERE {in_date}", [str(day)]
        ).fetchone()[0]
        if int(count) != expected:
            raise ValueError(f"table holds {count} rows for {day}, manifest says {expected}")
        target.execute("COMMIT")
    except BaseException:
        target.execute("ROLLBACK")
        raise
    return int(count)


def guard_dry_run_database(path: Path) -> None:
    path = path.resolve()
    if any(path == item or path.is_relative_to(item) for item in CANONICAL):
        raise Refused([f"{path} is a canonical database and is read-only here"])


def run(args, manifest: dict) -> None:
    day = args.date
    if args.dry_run:
        guard_dry_run_database(args.duckdb)
    directory = ARCHIVE_ROOT / str(day)
    index = None if args.refetch else read_complete_archive(directory)
    reused = index is not None
    staging = ARCHIVE_ROOT / f".{day}.partial"
    try:
        if reused:
            source = directory
        else:
            if day != utc_today():
                raise Refused([f"cannot fetch {day}: live boards only describe {utc_today()}"])
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            index = fetch_archive(day, staging, load_boards())
            source = staging
        rows, summary = parse_archive(source, index)
        by_provider = {}
        for board in summary:
            by_provider[board["provider"]] = by_provider.get(board["provider"], 0) + board["rows"]
        manifest["boards"] = summary
        manifest["totals"] = {
            "boards": len(summary),
            "boards_succeeded": sum(1 for board in summary if board["rows"]),
            "rows": len(rows),
            "rows_by_provider": by_provider,
        }
        if reasons := local_refusals(rows, summary, day):
            raise Refused(reasons)
        if not reused:
            promote(staging, directory)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    manifest["archive"] = {
        "path": relative(directory),
        "reused": reused,
        "sha256": digest(directory / INDEX),
        "bytes": sum(path.stat().st_size for path in directory.iterdir()),
    }
    target = DuckDBTarget(args.duckdb) if args.dry_run else SnowflakeTarget()
    with contextlib.closing(target):
        manifest["target"] = target.table
        manifest["prior_day"] = prior_day(target, day)
        if reasons := prior_refusals(len(rows), manifest["prior_day"]):
            raise Refused(reasons)
        with tempfile.TemporaryDirectory(prefix="board-observations-") as work:
            parquet = Path(work) / f"board_observations_{day}.parquet"
            write_parquet(rows, parquet)
            manifest["table_count"] = replace_date(target, target.stage(parquet), day, len(rows))


def parse_args(argv=None):
    parser = Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", required=True, type=date.fromisoformat, help="UTC YYYY-MM-DD")
    parser.add_argument("--output", required=True, type=Path, help="manifest JSON path")
    parser.add_argument("--refetch", action="store_true", help="ignore a same-date archive")
    parser.add_argument("--dry-run", action="store_true", help="no Snowflake; requires --duckdb")
    parser.add_argument("--duckdb", type=Path, help="throw-away DuckDB file for --dry-run")
    args = parser.parse_args(argv)
    if args.dry_run != (args.duckdb is not None):
        parser.error("--dry-run and --duckdb go together")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    started = time.monotonic()
    manifest = {
        "date": str(args.date),
        "status": "ERROR",
        "mode": "duckdb-dry-run" if args.dry_run else "snowflake",
        "replay_kind": LIVE_REPLAY_KIND,
        "boards": [],
        "totals": None,
        "archive": None,
        "target": None,
        "prior_day": None,
        "refusals": [],
        "table_count": None,
    }
    code = 1
    try:
        run(args, manifest)
    except Refused as refused:
        manifest["status"], manifest["refusals"], code = "REFUSED", refused.reasons, 2
        print(f"REFUSED: {refused}", file=sys.stderr)
    except Exception as exc:  # Reported in the manifest; the exit code carries the failure.
        manifest["error"] = f"{type(exc).__name__}: {exc}"[:1000]
        print(f"ERROR: {manifest['error']}", file=sys.stderr)
    else:
        manifest["status"], code = "LOADED", 0
    manifest["wall_seconds"] = round(time.monotonic() - started, 1)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n")
    for board in manifest["boards"]:
        print(f"{board['provider']}\t{board['board']}\t{board['http_status']}\t{board['rows']}")
    print(f"{manifest['status']}\t{args.date}\t{manifest['table_count']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
