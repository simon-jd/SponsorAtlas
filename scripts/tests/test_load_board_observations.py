"""Offline tests for the daily board load: a fake public internet with the real probe and CDK
parser, throw-away DuckDB files and a fake Snowflake connection. Nothing reaches a warehouse."""

import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest
from snowflake.connector.errors import ProgrammingError

from scripts import load_board_observations as loader
from scripts import load_snowflake_raw as raw
from scripts import public_board_probe, snowflake_live
from scripts import snowflake_compare as compare
from scripts.canonical_paths import FACTS_DB

ROOT = Path(__file__).resolve().parents[2]
DAY1, DAY2 = date(2026, 11, 6), date(2026, 11, 7)
LEVER = ["palantir", "zoox", "spotify"]
BODY = "UNIQUE-RAW-PAYLOAD-BODY"


class FakeBoards:
    """The three public APIs in their documented shapes, answering by board token."""

    def __init__(self):
        self.sizes = {board: 3 for _, board in loader.load_boards()}
        self.down = {}
        self.edits = {}
        self.calls = []

    def body(self, url):
        parts, query = url.path.split("/"), urllib.parse.parse_qs(url.query)
        if url.netloc.startswith("boards-api"):
            board, n = parts[3], self.sizes[parts[3]]
            jobs = [
                {"id": 1000 + i, "title": f"Engineer {i}", "company_name": board,
                 "absolute_url": f"https://example.invalid/{board}/{i}",
                 "location": {"name": "Remote"}, "content": BODY}
                for i in range(n)
            ]  # fmt: skip
            return board, {"jobs": jobs, "meta": {"total": n}}
        if url.netloc.startswith("api.lever"):
            board, n = parts[-1], self.sizes[parts[-1]]
            skip, limit = int(query["skip"][0]), int(query["limit"][0])
            return board, [
                {"id": f"lv-{i}", "text": f"Engineer {i}", "descriptionPlain": BODY,
                 "hostedUrl": f"https://example.invalid/{board}/{i}",
                 "categories": {"location": "Remote"}}
                for i in range(skip, min(n, skip + limit))
            ]  # fmt: skip
        board, n = parts[-1], self.sizes[parts[-1]]
        jobs = [
            {"title": f"Engineer {i}", "jobUrl": f"https://example.invalid/{board}/{i}",
             "location": "Remote", "isListed": True, "descriptionPlain": BODY}
            for i in range(n)
        ]  # fmt: skip
        return board, {"jobs": jobs}

    def urlopen(self, request, timeout=None):
        url = urllib.parse.urlsplit(request.full_url)
        self.calls.append(request.full_url)
        board, payload = self.body(url)
        for index, record in enumerate(payload if isinstance(payload, list) else payload["jobs"]):
            if board in self.edits:
                self.edits[board](index, record)
        if board in self.down:
            raise urllib.error.HTTPError(request.full_url, self.down[board], "down", None, None)
        response = io.BytesIO(json.dumps(payload).encode())
        response.url, response.status = request.full_url, 200
        return response


class World:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp, self.monkeypatch = tmp_path, monkeypatch
        self.boards = FakeBoards()
        self.database = tmp_path / "facts.duckdb"
        self.archive = tmp_path / "boards"

    def freeze(self, today, observed=None):
        moment = datetime(*(observed or today).timetuple()[:3], 8, 0, tzinfo=UTC)

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return moment

        self.monkeypatch.setattr(public_board_probe, "datetime", Frozen)
        self.monkeypatch.setattr(loader, "utc_today", lambda: today)

    def cli(self, day, *extra, today=None, observed=None, dry_run=True):
        self.freeze(today or day, observed)
        output = self.tmp / "manifest.json"
        target = ["--dry-run", "--duckdb", str(self.database)] if dry_run else []
        code = loader.main(["--date", str(day), "--output", str(output), *target, *extra])
        return code, json.loads(output.read_text())

    def rows(self, day=None):
        with duckdb.connect(str(self.database), read_only=True) as c:
            where = f"where cast(observed_at_utc as date)='{day}'" if day else ""
            return c.execute(
                f"select * from FACTS_INPUT.job_observations {where} order by all"
            ).fetchall()


@pytest.fixture
def world(tmp_path, monkeypatch):
    def no_snowflake(*args, **kwargs):
        raise AssertionError("no Snowflake connection is allowed here")

    world = World(tmp_path, monkeypatch)
    monkeypatch.setattr(urllib.request, "urlopen", world.boards.urlopen)
    monkeypatch.setattr(snowflake_live, "connect", no_snowflake)
    monkeypatch.setattr(loader, "ARCHIVE_ROOT", world.archive)
    monkeypatch.setattr(loader, "RETRY_SECONDS", 0)
    return world


def test_every_provider_parses_into_the_nine_fact_input_columns(world):
    world.boards.sizes["palantir"] = 101  # Lever pages at 100, so this needs a second page.
    code, manifest = world.cli(DAY1)
    assert code == 0 and manifest["status"] == "LOADED"
    assert manifest["table_count"] == manifest["totals"]["rows"] == 12 * 3 + 101
    assert manifest["totals"]["rows_by_provider"] == {
        "greenhouse": 21, "lever": 107, "ashby": 9,
    }  # fmt: skip
    boards = {b["board"]: b for b in manifest["boards"]}
    assert {b["http_status"] for b in boards.values()} == {200}
    assert boards["palantir"]["pages"] == 2 and boards["palantir"]["rows"] == 101
    with duckdb.connect(str(world.database), read_only=True) as c:
        columns = [r[0] for r in c.execute("describe FACTS_INPUT.job_observations").fetchall()]
        assert columns == loader.COLUMNS
        keys = {
            provider: key
            for provider, key in c.execute(
                "select source_provider,min(job_key) from FACTS_INPUT.job_observations group by 1"
            ).fetchall()
        }
        assert keys["greenhouse"] == "greenhouse:airbnb:1000"
        assert keys["lever"] == "lever:palantir:lv-0"
        assert keys["ashby"] == "ashby:notion:https://example.invalid/notion/0"
        assert c.execute(
            "select distinct replay_kind,cast(observed_at_utc as date),source_deleted_at"
            " from FACTS_INPUT.job_observations"
        ).fetchall() == [("LIVE_BOARD_FETCH", DAY1, None)]
        assert c.execute(
            "select count(*) from FACTS_INPUT.job_observations where source_sha256 is null"
            " or source_url not like 'https://%' or job_key<>source_provider||':'||source_board"
            "||':'||(case when source_provider='ashby' then json_extract_string(payload_json,"
            "'$.jobUrl') else json_extract_string(payload_json,'$.id') end)"
        ).fetchone() == (0,)


def test_manifest_records_each_board_digest_without_raw_payloads(world):
    world.boards.sizes["palantir"] = 101
    _, manifest = world.cli(DAY1)
    index = json.loads((world.archive / str(DAY1) / loader.INDEX).read_text())
    entry = next(b for b in index["boards"] if b["board"] == "palantir")
    whole = hashlib.sha256()
    for page in entry["pages"]:
        whole.update(gzip.decompress((world.archive / str(DAY1) / page["file"]).read_bytes()))
    board = next(b for b in manifest["boards"] if b["board"] == "palantir")
    assert board["sha256"] == whole.hexdigest()
    assert manifest["archive"]["bytes"] > 0 and not manifest["archive"]["reused"]
    assert manifest["wall_seconds"] >= 0 and manifest["date"] == str(DAY1)
    assert BODY not in json.dumps(manifest)
    assert {p.suffix for p in (world.archive / str(DAY1)).iterdir()} == {".gz", ".json"}


def test_refuses_when_a_provider_has_zero_rows(world):
    for board in LEVER:
        world.boards.sizes[board] = 0
    code, manifest = world.cli(DAY1)
    assert code == 2 and manifest["status"] == "REFUSED"
    assert manifest["refusals"] == ["provider lever returned zero rows"]
    assert manifest["table_count"] is None
    empty = next(b for b in manifest["boards"] if b["board"] == "palantir")
    assert empty["error"] is None and empty["field_shares"] == {}
    assert not world.database.exists() and not list(world.archive.iterdir())


def test_refuses_when_fewer_than_ten_boards_succeed_after_one_retry(world):
    for board in ["stripe", "airbnb", "zoox", "notion"]:
        world.boards.down[board] = 503
    code, manifest = world.cli(DAY1)
    assert code == 2 and "only 9 boards succeeded" in manifest["refusals"][0]
    failed = next(b for b in manifest["boards"] if b["board"] == "stripe")
    assert failed["http_status"] == 503 and failed["attempts"] == 2 and failed["rows"] == 0
    assert "HTTPError" in failed["error"]
    assert sum("stripe" in url for url in world.boards.calls) == 2
    assert not world.database.exists() and not list(world.archive.iterdir())


def test_loads_when_ten_boards_succeed_and_records_the_failures(world):
    for board in ["stripe", "airbnb", "zoox"]:
        world.boards.down[board] = 500
    code, manifest = world.cli(DAY1)
    assert code == 0 and manifest["totals"]["boards_succeeded"] == 10
    assert manifest["table_count"] == 30
    assert sum(b["http_status"] == 500 for b in manifest["boards"]) == 3


def rename_title(_index, record):
    record["headline"] = record.pop("title")


def location_as_text(_index, record):
    record["location"] = "Menlo Park, CA; Washington, DC"


def test_a_renamed_title_fails_every_board_and_refuses_the_provider(world):
    for board in world.boards.sizes:
        if board in {name for provider, name in loader.load_boards() if provider == "greenhouse"}:
            world.boards.edits[board] = rename_title
    code, manifest = world.cli(DAY1)
    assert code == 2 and manifest["refusals"] == [
        "provider greenhouse returned zero rows",
        "only 6 boards succeeded, need 10",
    ]
    failed = next(b for b in manifest["boards"] if b["board"] == "stripe")
    assert failed["rows"] == 0 and failed["field_shares"]["title"] == 0.0
    assert "title" in failed["error"] and failed["http_status"] == 200
    assert not world.database.exists() and not list(world.archive.iterdir())


def test_a_malformed_nested_field_fails_that_board_and_keeps_the_others(world):
    world.boards.edits["stripe"] = location_as_text
    code, manifest = world.cli(DAY1)
    assert code == 0 and manifest["totals"]["boards_succeeded"] == 12
    assert manifest["table_count"] == 36
    failed = next(b for b in manifest["boards"] if b["board"] == "stripe")
    assert failed["rows"] == 0 and "location" in failed["error"]
    good = next(b for b in manifest["boards"] if b["board"] == "airbnb")
    assert good["error"] is None and set(good["field_shares"].values()) == {1.0}
    assert not [r for r in world.rows(DAY1) if r[2] == "stripe"]


@pytest.mark.parametrize(
    ("board", "field", "value"),
    [
        ("palantir", "text", None),
        ("zoox", "hostedUrl", 7),
        ("spotify", "categories", "Remote"),
        ("notion", "title", None),
        ("notion", "location", {"name": "Remote"}),
        ("notion", "isListed", "maybe"),
        ("airbnb", "absolute_url", ""),
    ],
)
def test_each_provider_field_rule_fails_the_board(world, board, field, value):
    world.boards.edits[board] = lambda _index, record: record.update({field: value})
    code, manifest = world.cli(DAY1)
    assert code == 0 and manifest["totals"]["boards_succeeded"] == 12
    assert next(b for b in manifest["boards"] if b["board"] == board)["rows"] == 0


def test_a_board_at_the_field_threshold_stays_and_below_it_fails(world):
    world.boards.sizes["stripe"] = 10
    world.boards.edits["stripe"] = lambda i, r: r.update(title=None) if i == 0 else None
    code, manifest = world.cli(DAY1)
    board = next(b for b in manifest["boards"] if b["board"] == "stripe")
    assert code == 0 and board["rows"] == 10 and board["field_shares"]["title"] == 0.9
    world.boards.edits["stripe"] = lambda i, r: r.update(title=None) if i < 2 else None
    code, manifest = world.cli(DAY1, "--refetch")
    board = next(b for b in manifest["boards"] if b["board"] == "stripe")
    assert code == 0 and board["rows"] == 0 and board["field_shares"]["title"] == 0.8


def test_refuses_a_total_below_half_of_the_prior_day_and_keeps_that_day(world):
    assert world.cli(DAY1)[0] == 0
    before = world.rows(DAY1)
    world.boards.sizes = {board: 1 for board in world.boards.sizes}
    code, manifest = world.cli(DAY2)
    assert code == 2 and "below 50%" in manifest["refusals"][0]
    assert manifest["prior_day"] == {"date": str(DAY1), "rows": 39}
    assert world.rows(DAY1) == before and world.rows(DAY2) == []
    assert manifest["table_count"] is None


def test_exactly_half_of_the_prior_day_is_not_below_half(world):
    world.boards.sizes = {board: 2 for board in world.boards.sizes}
    assert world.cli(DAY1)[0] == 0
    world.boards.sizes = {board: 1 for board in world.boards.sizes}
    code, manifest = world.cli(DAY2)
    assert code == 0 and manifest["table_count"] == 13


def test_prior_day_is_the_most_recent_stored_date_not_only_yesterday(world):
    assert world.cli(DAY1)[0] == 0
    world.boards.sizes = {board: 1 for board in world.boards.sizes}
    code, manifest = world.cli(date(2026, 11, 20))
    assert code == 2 and manifest["prior_day"]["date"] == str(DAY1)


def test_refuses_to_fetch_any_date_but_today_because_rows_would_be_mislabelled(world):
    code, manifest = world.cli(DAY1, today=DAY2)
    assert code == 2 and "live boards only describe" in manifest["refusals"][0]
    assert world.boards.calls == []
    assert world.cli(DAY1, "--refetch", today=DAY2)[0] == 2
    assert world.boards.calls == []


def test_refuses_observations_outside_the_requested_date(world):
    code, manifest = world.cli(DAY1, observed=DAY2)
    assert code == 2 and "fall outside" in manifest["refusals"][0]
    assert not list(world.archive.iterdir())


def test_refuses_a_canonical_database_as_the_dry_run_target(world):
    output = world.tmp / "manifest.json"
    world.freeze(DAY1)
    code = loader.main(
        ["--date", str(DAY1), "--output", str(output), "--dry-run", "--duckdb", str(FACTS_DB)]
    )
    assert code == 2 and world.boards.calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["--date", "not-a-date", "--output", "x"],
        ["--date", "2026-11-06"],
        ["--date", "2026-11-06", "--output", "x", "--dry-run"],
        ["--date", "2026-11-06", "--output", "x", "--duckdb", "y"],
    ],
)
def test_usage_errors_do_not_look_like_a_safety_refusal(arguments, capsys):
    with pytest.raises(SystemExit) as stopped:
        loader.main(arguments)
    assert stopped.value.code not in (0, 2)


def test_rerun_reuses_the_archive_without_touching_the_network(world):
    code, first = world.cli(DAY1)
    assert code == 0 and not first["archive"]["reused"]
    before, calls = world.rows(DAY1), len(world.boards.calls)
    world.boards.sizes["stripe"] = 2  # The live board changed; a retry must not see that.
    code, second = world.cli(DAY1)
    assert code == 0 and second["archive"]["reused"]
    assert len(world.boards.calls) == calls and world.rows(DAY1) == before
    assert second["archive"]["sha256"] == first["archive"]["sha256"]
    assert second["table_count"] == first["table_count"]


def test_a_retry_after_midnight_still_reuses_its_archive(world):
    assert world.cli(DAY1)[0] == 0
    calls = len(world.boards.calls)
    code, manifest = world.cli(DAY1, today=DAY2)
    assert code == 0 and manifest["archive"]["reused"] and len(world.boards.calls) == calls


def test_refetch_replaces_the_date_exactly_and_leaves_other_dates_alone(world):
    assert world.cli(DAY1)[0] == 0
    day1 = world.rows(DAY1)
    assert world.cli(DAY2)[0] == 0
    world.boards.sizes["stripe"] = 2
    code, manifest = world.cli(DAY2, "--refetch")
    assert code == 0 and not manifest["archive"]["reused"]
    assert manifest["table_count"] == 38 and len(world.rows(DAY2)) == 38
    assert not [r for r in world.rows(DAY2) if r[0] == "greenhouse:stripe:1002"]
    assert world.rows(DAY1) == day1


def test_a_refused_refetch_keeps_the_good_archive_and_rows(world):
    code, first = world.cli(DAY1)
    assert code == 0
    before = world.rows(DAY1)
    for board in LEVER:
        world.boards.sizes[board] = 0
    code, _ = world.cli(DAY1, "--refetch")
    assert code == 2 and world.rows(DAY1) == before
    code, again = world.cli(DAY1)
    assert code == 0 and again["archive"]["reused"]
    assert again["archive"]["sha256"] == first["archive"]["sha256"]


def test_an_incomplete_archive_is_fetched_again_and_a_damaged_one_fails(world):
    assert world.cli(DAY1)[0] == 0
    directory = world.archive / str(DAY1)
    (directory / loader.INDEX).unlink()
    code, manifest = world.cli(DAY1)
    assert code == 0 and not manifest["archive"]["reused"]
    damaged = next(directory.glob("*.gz"))
    damaged.write_bytes(gzip.compress(b"{}"))
    code, manifest = world.cli(DAY1)
    assert code == 1 and manifest["status"] == "ERROR"
    assert "digest mismatch" in manifest["error"]


class FakeSnowflake:
    def __init__(self, prior=None, count=None):
        self.prior, self.count, self.log, self.staged, self.closed = prior, count, [], None, False

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        self.log.append(sql)
        self.result = None
        if sql.startswith("PUT"):
            path = re.match(r"PUT 'file://([^']+)'", sql)[1]
            with duckdb.connect(":memory:") as c:
                self.staged = c.execute(f"describe select * from read_parquet('{path}')").fetchall()
                self.staged_rows = c.execute(f"select count(*) from read_parquet('{path}')").fetchone()[0]  # fmt: skip
        elif "GROUP BY 1" in sql:
            self.result = self.prior
        elif sql.startswith("SELECT COUNT(*)"):
            self.result = (self.staged_rows if self.count is None else self.count,)
        return self

    def fetchone(self):
        return self.result

    def close(self):
        self.closed = True

    def verbs(self):
        return [" ".join(sql.split()[:2]) if sql.startswith("CREATE") else sql.split()[0]
                for sql in self.log]  # fmt: skip


def use_snowflake(world, fake):
    identities = []

    def connect(role, **kwargs):
        identities.append((role, kwargs))
        return fake

    world.monkeypatch.setattr(snowflake_live, "connect", connect)
    return identities


def test_snowflake_load_stages_then_replaces_the_date_in_one_transaction(world):
    fake = FakeSnowflake()
    identities = use_snowflake(world, fake)
    code, manifest = world.cli(DAY1, dry_run=False)
    assert code == 0 and manifest["table_count"] == 39 and fake.closed
    assert manifest["target"] == "RAW.LCA.FACTS_INPUT_JOB_OBSERVATIONS"
    assert identities[0][0] == "LOADER"
    assert fake.verbs() == [
        "SELECT", "CREATE OR", "PUT", "COPY", "BEGIN", "DELETE", "INSERT", "SELECT", "COMMIT",
    ]  # fmt: skip
    temp, put, copy = fake.log[1:4]
    # Snowflake error 001023: COPY may only read the target table's own table stage.
    assert "@RAW.LCA.%FACTS_INPUT_JOB_OBSERVATIONS_LOAD " in put and "OVERWRITE = TRUE" in put
    assert "FROM @RAW.LCA.%FACTS_INPUT_JOB_OBSERVATIONS_LOAD " in copy
    assert "TEMPORARY TABLE RAW.LCA.FACTS_INPUT_JOB_OBSERVATIONS_LOAD" in temp
    assert "OBSERVED_AT_UTC" in temp.upper() and "TIMESTAMP_NTZ" in temp
    assert f"FILES = ('board_observations_{DAY1}.parquet')" in copy
    assert "COPY INTO RAW.LCA.FACTS_INPUT_JOB_OBSERVATIONS_LOAD" in copy
    delete, insert = fake.log[5:7]
    assert "CAST(OBSERVED_AT_UTC AS DATE) = CAST(%s AS DATE)" in delete
    assert insert.endswith("FROM RAW.LCA.FACTS_INPUT_JOB_OBSERVATIONS_LOAD")
    types = {name: kind for name, kind, *_ in fake.staged}
    assert list(types) == loader.COLUMNS
    assert types["observed_at_utc"] == "TIMESTAMP" and types["source_deleted_at"] == "TIMESTAMP"
    assert types["payload_json"] == "VARCHAR"


def test_snowflake_count_mismatch_rolls_back_and_fails(world):
    fake = FakeSnowflake(count=38)
    use_snowflake(world, fake)
    code, manifest = world.cli(DAY1, dry_run=False)
    assert code == 1 and manifest["status"] == "ERROR" and manifest["table_count"] is None
    assert "ROLLBACK" in fake.log and "COMMIT" not in fake.log
    assert "manifest says 39" in manifest["error"]


def test_snowflake_prior_refusal_writes_nothing(world):
    fake = FakeSnowflake(prior=(DAY1, 1000))
    use_snowflake(world, fake)
    code, manifest = world.cli(DAY2, dry_run=False)
    assert code == 2 and manifest["prior_day"] == {"date": str(DAY1), "rows": 1000}
    assert fake.verbs() == ["SELECT"] and fake.closed


def test_local_refusals_never_open_a_snowflake_connection(world):
    for board in LEVER:
        world.boards.sizes[board] = 0
    code, _ = world.cli(DAY1, dry_run=False)  # The fixture's connect raises if it is reached.
    assert code == 2


class ReplaceGuardConnection:
    def __init__(self, live=0, error=None):
        self.live, self.error, self.sql = live, error, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if self.error:
            raise self.error
        return self

    def fetchone(self):
        return (self.live,)


def run_raw_main(monkeypatch, tmp_path, connection, table, *flags):
    loaded = []

    def load_item(_connection, item, *, replace=False):
        loaded.append(item["table"])
        return {"table": item["table"], "status": "MATCH", "expected_rows": 1, "snowflake_rows": 1}

    monkeypatch.setattr(raw, "plan", lambda *a, **k: [{"table": table, "expected_rows": 1}])
    monkeypatch.setattr(raw, "load_item", load_item)
    monkeypatch.setattr(snowflake_live, "connect", lambda *a, **k: connection)
    return raw.main(["--output", str(tmp_path / "load.json"), *flags]), loaded


def test_replace_refuses_job_observations_once_live_rows_exist(monkeypatch, tmp_path, capsys):
    code, loaded = run_raw_main(
        monkeypatch, tmp_path, ReplaceGuardConnection(live=5), raw.JOB_OBSERVATIONS, "--replace"
    )
    assert code == 2 and loaded == []
    assert "would overwrite 5 LIVE_BOARD_FETCH rows" in capsys.readouterr().err


def test_replace_of_job_observations_is_allowed_before_any_live_rows(monkeypatch, tmp_path):
    code, loaded = run_raw_main(
        monkeypatch, tmp_path, ReplaceGuardConnection(), raw.JOB_OBSERVATIONS, "--replace"
    )
    assert code == 0 and loaded == [raw.JOB_OBSERVATIONS]


def test_replace_guard_treats_a_missing_table_as_empty_and_raises_anything_else(
    monkeypatch, tmp_path
):
    missing = ReplaceGuardConnection(error=ProgrammingError(msg="does not exist", errno=2003))
    code, loaded = run_raw_main(monkeypatch, tmp_path, missing, raw.JOB_OBSERVATIONS, "--replace")
    assert code == 0 and loaded == [raw.JOB_OBSERVATIONS]
    other = ReplaceGuardConnection(error=ProgrammingError(msg="other", errno=1))
    with pytest.raises(ProgrammingError):
        run_raw_main(monkeypatch, tmp_path, other, raw.JOB_OBSERVATIONS, "--replace")


def test_replace_guard_leaves_other_tables_and_plain_loads_alone(monkeypatch, tmp_path):
    connection = ReplaceGuardConnection(live=5)
    code, loaded = run_raw_main(
        monkeypatch, tmp_path, connection, "RAW.LCA.FACTS_INPUT_LCA", "--replace"
    )
    assert code == 0 and loaded == ["RAW.LCA.FACTS_INPUT_LCA"] and connection.sql == []
    code, loaded = run_raw_main(monkeypatch, tmp_path, connection, raw.JOB_OBSERVATIONS)
    assert code == 0 and loaded == [raw.JOB_OBSERVATIONS] and connection.sql == []


class CompareConnection:
    """Answers every relation and metric read; only the posting count is made to differ."""

    def __init__(self, live):
        self.live, self.sql = live, ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        self.sql = sql
        return self

    def fetchone(self):
        if self.sql == compare.LIVE_FACT_ROWS:
            return (self.live,)
        if "CURRENT_USER" in self.sql:
            return ("CUBE_SVC", "ANALYST")
        if self.sql == "postings":
            return (4907,)
        if self.sql == "lca":
            return (6, 3, 6, 0.5, 30, 40, 76)
        if self.sql == "wages":
            return (1, 80)
        if "fact_job_posting_daily" in self.sql:
            return (4993, *self.fingerprint(self.sql, 1))
        return (1, *self.fingerprint(self.sql, 0))

    @staticmethod
    def fingerprint(sql, offset):
        return [ord("x") + offset] * sql.count("MD5")

    def fetchall(self):
        return [("E1", 1)]


def run_compare(monkeypatch, tmp_path, live):
    database = tmp_path / "local.duckdb"
    with duckdb.connect(str(database)) as c:
        c.execute("create schema if not exists MARTS")
        for table in ["fact_job_posting_daily", "dim_date"]:
            c.execute(f"create or replace table MARTS.{table} as select 'x' as a")
    for name in ["STAGING_DB", "DIMENSIONS_DB", "FACTS_DB"]:
        monkeypatch.setattr(compare, name, database)
    monkeypatch.setattr(compare, "RELATIONS", {
        "fact_job_posting_daily": (database, "MARTS", "ANALYTICS.MARTS.fact_job_posting_daily"),
        "dim_date": (database, "MARTS", "ANALYTICS.MARTS.dim_date"),
    })  # fmt: skip
    local = {"lca": "select 6,3,6,0.5,30,40,76", "postings": "select 86", "wages": "select 1,80"}
    remote = {"lca": "lca", "postings": "postings", "wages": "wages"}
    monkeypatch.setattr(compare, "metric_queries", lambda s: local if s == "MARTS" else remote)
    monkeypatch.setattr(compare, "group_query", lambda s: "select 'E1',1")
    monkeypatch.setattr(
        compare, "fingerprint_query",
        lambda relation, columns, *, snowflake: (
            f"select count(*), {', '.join('120' for _ in columns)} from {relation}"
            if not snowflake else f"{relation} {'MD5 ' * len(columns)}"
        ),
    )  # fmt: skip
    monkeypatch.setattr(snowflake_live, "connect", lambda *a, **k: CompareConnection(live))
    output = tmp_path / "compare.json"
    assert compare.main(["--output", str(output)]) == 0
    return json.loads(output.read_text())


def test_compare_marks_job_postings_expected_diverged_once_live_rows_exist(monkeypatch, tmp_path):
    result = run_compare(monkeypatch, tmp_path, live=4907)
    assert result["live_job_posting_rows"] == 4907
    assert result["relations"]["fact_job_posting_daily"]["status"] == "EXPECTED_DIVERGED"
    assert result["relations"]["dim_date"]["status"] == "SAME"
    assert result["metrics"]["active_posting_days"]["status"] == "EXPECTED_DIVERGED"
    assert result["metrics"]["case_count"]["status"] == "EQUAL"


def test_compare_keeps_job_posting_differences_as_failures_without_live_rows(monkeypatch, tmp_path):
    result = run_compare(monkeypatch, tmp_path, live=0)
    assert result["relations"]["fact_job_posting_daily"]["status"] == "DIFF"
    assert result["metrics"]["active_posting_days"]["status"] == "DIFF"


def test_only_job_postings_can_be_marked_expected_diverged():
    assert compare.expected_divergence("fact_lca_case", "DIFF", 10) == "DIFF"
    assert compare.expected_divergence("fact_job_posting_daily", "SAME", 10) == "SAME"
    assert compare.metric_status("case_count", False, 10) == "DIFF"
    assert compare.metric_status("active_posting_days", True, 10) == "EQUAL"


def dbt(tmp_path, database, *arguments):
    env = {
        **os.environ,
        "DBT_TARGET": "local",
        "SPONSORATLAS_DUCKDB_PATH": str(database),
        "DBT_TARGET_PATH": str(tmp_path / "target"),
        "DBT_LOG_PATH": str(tmp_path / "logs"),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    }
    return subprocess.run(
        [str(ROOT / "dbt/run.sh"), "--warn-error", *arguments],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip


def fact(database, day=None):
    with duckdb.connect(str(database), read_only=True) as c:
        where = f"where observation_date='{day}'" if day else ""
        return c.execute(f"select * from MARTS.fact_job_posting_daily {where} order by all").fetchall()  # fmt: skip


def build_fact(world):
    selection = ["--select", "dim_date", "fact_job_posting_daily"]
    run = dbt(world.tmp, world.database, "run", *selection)
    assert run.returncode == 0, run.stdout + run.stderr
    # Contract columns, FKs and the accounting gate; facts_grain also needs the other facts.
    check = dbt(world.tmp, world.database, "test", "--select", "fact_job_posting_daily",
                "--exclude", "facts_grain")  # fmt: skip
    assert check.returncode == 0, check.stdout + check.stderr
    assert "facts_job_observation_accounting" in check.stdout


def make_fact_parents(database):
    with duckdb.connect(str(database)) as c:
        c.execute("create schema MARTS")
        c.execute("create table MARTS.dim_employer(employer_id varchar,employer_key varchar)")
        c.execute("create table MARTS.dim_occupation(occupation_key varchar)")
        c.execute("create table MARTS.dim_geography(geography_key varchar)")


def test_one_malformed_row_reads_as_null_and_does_not_wedge_later_builds(world):
    def one_bad_location(index, record):
        if index == 0:
            location_as_text(index, record)

    def one_bad_listing(index, record):
        if index == 0:
            record["isListed"] = "maybe"

    world.boards.sizes |= {"stripe": 20, "notion": 20}
    world.boards.edits |= {"stripe": one_bad_location, "notion": one_bad_listing}
    assert world.cli(DAY1)[0] == 0
    make_fact_parents(world.database)
    build_fact(world)
    with duckdb.connect(str(world.database), read_only=True) as c:
        assert c.execute(
            "select count(*),count(location_raw) from MARTS.fact_job_posting_daily"
            " where source_board='stripe'"
        ).fetchone() == (20, 19)
        assert c.execute(
            "select count(*),count(is_listed),sum(is_active::int) from MARTS.fact_job_posting_daily"
            " where source_board='notion'"
        ).fetchone() == (20, 19, 20)
    # The poisoned date stays inside the lookback window; the next build still succeeds.
    world.boards.edits = {}
    assert world.cli(DAY2)[0] == 0
    build_fact(world)
    with duckdb.connect(str(world.database), read_only=True) as c:
        assert c.execute(
            "select count(distinct observation_date) from MARTS.fact_job_posting_daily"
        ).fetchone() == (2,)


def test_the_job_fields_gate_fails_only_when_the_latest_date_lost_a_provider(world):
    assert world.cli(DAY1)[0] == 0
    make_fact_parents(world.database)
    build_fact(world)
    assert world.cli(DAY2)[0] == 0
    build_fact(world)
    gate = ["test", "--select", "facts_job_fields_present"]

    def blank(column, day):
        with duckdb.connect(str(world.database)) as c:
            c.execute(
                f"update MARTS.fact_job_posting_daily set {column}=null"
                f" where source_provider='greenhouse' and observation_date='{day}'"
            )

    blank("job_title", DAY1)
    passed = dbt(world.tmp, world.database, *gate)
    assert passed.returncode == 0, passed.stdout + passed.stderr
    blank("job_url", DAY2)
    failed = dbt(world.tmp, world.database, *gate)
    assert failed.returncode != 0 and "facts_job_fields_present" in failed.stdout


def test_two_date_dbt_build_grows_by_one_date_and_a_rerun_changes_nothing(world):
    assert world.cli(DAY1)[0] == 0
    make_fact_parents(world.database)
    build_fact(world)
    day1 = fact(world.database)
    assert len(day1) == 39

    assert world.cli(DAY2)[0] == 0
    build_fact(world)
    assert len(fact(world.database)) == 78
    assert fact(world.database, DAY1) == day1
    with duckdb.connect(str(world.database), read_only=True) as c:
        assert c.execute(
            "select observation_date,count(*),count(job_title),count(job_url),count(location_raw)"
            " from MARTS.fact_job_posting_daily group by 1 order by 1"
        ).fetchall() == [(DAY1, 39, 39, 39, 39), (DAY2, 39, 39, 39, 39)]
        assert (
            c.execute(
                "select source_provider,count(*) from MARTS.fact_job_posting_daily where not is_active"
                " or job_title is null or job_url is null group by 1"
            ).fetchall()
            == []
        )

    before = fact(world.database)
    assert world.cli(DAY2)[0] == 0
    build_fact(world)
    assert fact(world.database) == before

    # A refetch that drops a posting leaves no stale fact row, and the other date is untouched.
    world.boards.sizes["stripe"] = 2
    assert world.cli(DAY2, "--refetch")[0] == 0
    build_fact(world)
    day2 = fact(world.database, DAY2)
    assert len(day2) == 38 and fact(world.database, DAY1) == day1
    assert not [row for row in day2 if row[0] == "greenhouse:stripe:1002"]
    with duckdb.connect(str(world.database), read_only=True) as c:
        assert c.execute(
            "select count(*),count(distinct (posting_id,observation_date))"
            " from MARTS.fact_job_posting_daily"
        ).fetchone() == (77, 77)

    # The accounting gate reports a stale row rather than passing over it.
    with duckdb.connect(str(world.database)) as c:
        c.execute(
            "insert into MARTS.fact_job_posting_daily select * replace('stale' as posting_id)"
            f" from MARTS.fact_job_posting_daily where observation_date='{DAY2}' limit 1"
        )
    stale = dbt(world.tmp, world.database, "test", "--select", "facts_job_observation_accounting")
    assert stale.returncode != 0 and "facts_job_observation_accounting" in stale.stdout


def test_source_freshness_errors_after_24_hours_on_duckdb(tmp_path):
    database = tmp_path / "freshness.duckdb"
    with duckdb.connect(str(database)) as c:
        c.execute("create schema FACTS_INPUT")
        c.execute(
            "create table FACTS_INPUT.job_observations as select"
            " timezone('UTC',current_timestamp)-interval '25 hours' as observed_at_utc"
        )
    selection = ["source", "freshness", "--select", "source:facts_input.job_observations"]
    stale = dbt(tmp_path, database, *selection)
    assert stale.returncode != 0, stale.stdout + stale.stderr
    results = json.loads((tmp_path / "target/sources.json").read_text())["results"]
    assert [r["status"] for r in results] == ["error"]
    with duckdb.connect(str(database)) as c:
        c.execute(
            "update FACTS_INPUT.job_observations set observed_at_utc=timezone('UTC',current_timestamp)"
        )
    fresh = dbt(tmp_path, database, *selection)
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr


def test_snowflake_target_parses_the_source_model_and_gate_without_any_account(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SNOWFLAKE_")}
    env |= {
        "DBT_TARGET": "snowflake",
        "DBT_TARGET_PATH": str(tmp_path / "target"),
        "DBT_LOG_PATH": str(tmp_path / "logs"),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    }
    parsed = subprocess.run(
        [str(ROOT / "dbt/run.sh"), "--warn-error", "parse"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert parsed.returncode == 0, parsed.stdout + parsed.stderr
    manifest = json.loads((tmp_path / "target/manifest.json").read_text())
    source = manifest["sources"]["source.sponsoratlas.facts_input.job_observations"]
    assert (source["database"], source["schema"], source["identifier"]) == (
        "RAW", "LCA", "FACTS_INPUT_JOB_OBSERVATIONS",
    )  # fmt: skip
    assert source["loaded_at_field"] == "observed_at_utc"
    assert source["freshness"]["error_after"] == {"count": 24, "period": "hour"}
    model = manifest["nodes"]["model.sponsoratlas.fact_job_posting_daily"]
    assert model["config"]["incremental_strategy"] == "delete+insert"
    assert model["config"]["unique_key"] == "observation_date"
    assert model["contract"]["enforced"] is True
    gate = manifest["nodes"]["test.sponsoratlas.facts_job_observation_accounting"]
    assert gate["tags"] == ["facts"]
    assert manifest["nodes"]["test.sponsoratlas.facts_job_fields_present"]["tags"] == ["facts"]
