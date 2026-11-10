"""Export, oracle and names checks on a throw-away DuckDB fixture. No warehouse is used."""
import csv
import datetime as dt
import decimal
import json
import sys
from pathlib import Path

import duckdb
import pytest

PUBLIC = Path(__file__).resolve().parents[1] / "public"
sys.path.insert(0, str(PUBLIC))
import export_snapshot as ex  # noqa: E402

FIXED_NOW = dt.datetime(2026, 11, 6, 3, 0, 0, tzinfo=dt.UTC)
PRIVATE_NAME = "Secret Source Employer Inc"


def make_marts(path):
    con = duckdb.connect(str(path))
    con.execute("create schema MARTS")
    con.execute("create table MARTS.dim_occupation(occupation_key varchar, soc_code varchar, soc_title varchar)")
    con.execute("insert into MARTS.dim_occupation values('o1','15-1252','Software Developers'),('o2','15-2051','Data Scientists')")
    con.execute("create table MARTS.dim_geography(geography_key varchar, cbsa_code varchar, cbsa_title varchar, cbsa_type varchar)")
    con.execute("""insert into MARTS.dim_geography values
      ('g1','35620','New York-Newark-Jersey City, NY-NJ','Metropolitan Statistical Area'),
      ('g2','99999','Tiny Town, ZZ','Micropolitan Statistical Area')""")
    con.execute("""create table MARTS.fact_lca_case(case_number varchar, is_certified boolean, job_title varchar,
      source_employer_name varchar, employer_id varchar, occupation_key varchar, geography_key varchar, soc_code varchar,
      received_date date, annual_wage decimal(24,2), fiscal_year integer, worksite_state varchar, source_release varchar)""")
    full = "emp_" + "a" * 64
    other = "emp_" + "b" * 64
    rows = []
    for index in range(12):
        rows.append((f"I-200-{index:05d}", index != 0, "Senior Data Engineer" if index % 2 else "Physical Therapist",
                     PRIVATE_NAME, full, "o1", "g1", "15-1252", dt.date(2025, 3, 1),
                     decimal.Decimal(100000 + index * 1000), 2025, "NY", "FY2025_Q2"))  # fmt: skip
    rows.append(("I-200-99999", True, "Nurse", "Micro Person", other, "o2", "g2", "15-2051", dt.date(2024, 8, 1),
                 decimal.Decimal("50000.25"), 2024, "ZZ", "FY2024_Q4"))  # fmt: skip
    rows.append(("I-200-99998", True, "ETL Developer", None, None, None, None, None, None,
                 decimal.Decimal("70000.75"), 2024, "NY", "FY2024_Q4"))  # fmt: skip
    con.executemany("insert into MARTS.fact_lca_case values(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("""create table MARTS.fact_job_posting_daily(posting_id varchar, observation_date date, is_active boolean,
      employer_id varchar, occupation_key varchar, geography_key varchar, employer_mapping_status varchar, replay_kind varchar)""")
    con.execute("""insert into MARTS.fact_job_posting_daily values
      ('ashby:Acme:https://jobs.example/Acme/1', date '2026-10-24', true, null, null, null, 'UNRESOLVED', 'LOCAL_ARCHIVE_REPLAY'),
      ('ashby:Acme:https://jobs.example/Acme/2', date '2026-10-24', false, null, null, null, 'UNRESOLVED', 'LOCAL_ARCHIVE_REPLAY')""")
    con.execute("""create table MARTS.fact_wage_benchmark(soc_code varchar, area_type varchar, area_code varchar, survey_year integer,
      occupation_title varchar, annual_median_wage decimal(18,2), annual_median_wage_raw varchar, wage_status varchar, source_release varchar)""")
    con.execute("""insert into MARTS.fact_wage_benchmark values
      ('15-1252','1','99',2025,'Software Developers',135980.00,'135980','REPORTED','FY2025'),
      ('15-2051','1','99',2025,'Data Scientists',null,'#','SUPPRESSED_OR_TOP_CODED','FY2025'),
      ('15-1252','4','35620',2025,'Software Developers',150000.00,'150000','REPORTED','FY2025')""")
    con.close()


@pytest.fixture(autouse=True)
def absent_stop_marker(monkeypatch, tmp_path):
    """The real marker exists until go-live, so no test may read it."""
    monkeypatch.setattr(ex, "STOP_MARKER", tmp_path / "absent-stop-marker")


def fixture_oracle(marts):
    """The same formulas as the production oracle, read from the fixture itself."""
    con = duckdb.connect(str(marts), read_only=True)
    cases, certified, wage = con.execute(
        "select count(*), sum(case when is_certified then 1 else 0 end), quantile_cont(annual_wage::double,0.75) from MARTS.fact_lca_case"
    ).fetchone()
    con.close()
    return {"case_count": cases, "certified_rate": certified / cases, "wage_p75": wage, "oews_median": 135980.0,
            "archive_posting_rows": 2, "archive_active_posting_days": 1}  # fmt: skip


@pytest.fixture
def marts(tmp_path):
    path = tmp_path / "fixture.duckdb"
    make_marts(path)
    return path


@pytest.fixture
def projection(marts):
    data = ex.load_projection()
    data["oracle"] = fixture_oracle(marts)
    return data


def export_fixture(marts, projection, tmp_path, now=FIXED_NOW):
    return ex.export("duckdb", out_root=tmp_path / "out", duckdb_path=marts, projection=projection, now=now)


def read(path):
    with Path(path).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_dev_export_kind_manifest_and_columns(marts, projection, tmp_path):
    destination, manifest = export_fixture(marts, projection, tmp_path)
    assert manifest["kind"] == ex.KIND_DEV
    assert manifest["schema"] == ex.MANIFEST_SCHEMA
    assert manifest["snapshot_time_utc"] == "2026-11-06T03:00:00Z"
    assert destination.name == "20261106T030000Z"
    for name in (*ex.PUBLIC_FILES, ex.PRIVATE_NAMES):
        record = manifest["files"][name]
        assert record["sha256"] == ex.sha256_file(destination / name)
        assert record["rows"] == len(read(destination / name))
    assert "fact_lca_case" in manifest["queries"]["lca"]
    assert manifest["files"][ex.PRIVATE_NAMES]["private"] is True
    assert (destination / ex.PRIVATE_NAMES).stat().st_mode & 0o077 == 0
    assert tuple(read(destination / "lca.csv")[0]) == ex.LCA_COLUMNS


def test_every_public_file_row_carries_this_exports_kind_and_id(marts, projection, tmp_path):
    destination, manifest = export_fixture(marts, projection, tmp_path)
    assert manifest["snapshot_id"].startswith("20261106T030000Z-")
    for name in ex.PUBLIC_FILES:
        rows = read(destination / name)
        assert rows
        assert {(r["snapshot_kind"], r["snapshot_id"]) for r in rows} == {(ex.KIND_DEV, manifest["snapshot_id"])}
    assert "snapshot_id" not in read(destination / ex.PRIVATE_NAMES)[0]


def test_public_files_carry_no_source_names_titles_or_board_slugs(marts, projection, tmp_path):
    destination, _ = export_fixture(marts, projection, tmp_path)
    for name in ex.PUBLIC_FILES:
        text = (destination / name).read_text()
        assert PRIVATE_NAME not in text and "Micro Person" not in text
        assert "Senior Data Engineer" not in text and "Acme" not in text
    assert PRIVATE_NAME in (destination / ex.PRIVATE_NAMES).read_text()


def test_data_role_flag_and_unknowns_stay_available(marts, projection, tmp_path):
    destination, _ = export_fixture(marts, projection, tmp_path)
    rows = {row["case_id"]: row for row in read(destination / "lca.csv")}
    assert rows["I-200-00001"]["data_role"] == "1"
    assert rows["I-200-00000"]["data_role"] == "0"
    assert rows["I-200-99998"]["data_role"] == "1"
    assert rows["I-200-99998"]["employer_id"] == "UNKNOWN"
    assert rows["I-200-99998"]["soc"] == "UNKNOWN"
    assert rows["I-200-99998"]["received_month"] == "UNKNOWN"
    assert rows["I-200-99999"]["metro_cbsa"] == "UNKNOWN", "Only metropolitan CBSA may be called metro"
    assert rows["I-200-00001"]["received_month"] == "2025-03"
    assert rows["I-200-00001"]["employer_id"] == "emp_aaaaaaaaaaaa"


@pytest.mark.parametrize(
    "title, expected",
    [("Data Engineer", True), ("Sr. Data Scientist II", True), ("BUSINESS INTELLIGENCE ANALYST", True),
     ("ETL / Teradata Developer", True), ("Staff ML Engineer", True), ("Analytics Engineer (00077703)", True),
     ("Data Center Infrastructure Engineer", False), ("Database Administrator", False),
     ("Clinical Data Manager", False), ("Data Entry Clerk", False), ("Software Engineer", False),
     ("Metallurgical Engineer", False)],
)  # fmt: skip
def test_data_role_regex(title, expected):
    import re

    pattern = re.compile(ex.load_projection()["data_role"]["regex"], re.IGNORECASE)
    assert bool(pattern.search(title)) is expected


def test_benchmark_is_metro_only_with_status_and_no_fanout(marts, projection, tmp_path):
    destination, _ = export_fixture(marts, projection, tmp_path)
    rows = read(destination / "benchmark.csv")
    assert len(rows) == 1
    row = rows[0]
    assert (row["soc"], row["metro_cbsa"], row["lca_cases"]) == ("15-1252", "35620", "12")
    assert row["oews_median"] == "150000.00" and row["oews_status"] == "REPORTED"
    assert float(row["lca_p50"]) == pytest.approx(105500.0, abs=1e-8)


def test_float_formatting_keeps_full_precision():
    assert ex.format_cell(decimal.Decimal("156383.63")) == "156383.63"
    assert ex.format_cell(decimal.Decimal("135980.00")) == "135980.00"
    assert ex.format_cell(decimal.Decimal("1E+5")) == "100000"
    assert ex.format_cell(0.9777789302098064) == "0.9777789302098064"
    assert float(ex.format_cell(1 / 3)) == 1 / 3
    assert ex.format_cell(None) == ""
    assert ex.format_cell(True) == "1"
    assert ex.format_cell(dt.date(2026, 10, 24)) == "2026-10-24"
    with pytest.raises(ex.Refused):
        ex.format_cell(float("nan"))


@pytest.mark.parametrize("metric", ["case_count", "certified_rate", "wage_p75", "oews_median", "archive_posting_rows", "archive_active_posting_days"])
def test_oracle_mismatch_refuses_before_any_extract_and_publishes_nothing(marts, projection, tmp_path, monkeypatch, metric):
    def no_extract(*_args):
        raise AssertionError("The oracle must refuse before a CSV is written")

    monkeypatch.setattr(ex, "write_csv", no_extract)
    projection["oracle"][metric] = float(projection["oracle"][metric]) + 1e-6
    with pytest.raises(ex.Refused, match=metric):
        export_fixture(marts, projection, tmp_path)
    assert not list((tmp_path / "out").iterdir())


def add_live_postings(marts, active=2, inactive=1):
    con = duckdb.connect(str(marts))
    for index in range(active + inactive):
        con.execute(
            "insert into MARTS.fact_job_posting_daily values(?,date '2026-11-06',?,null,null,null,'UNRESOLVED','LIVE_BOARD_FETCH')",
            [f"lever:x:{index}", index < active],
        )
    con.close()


def test_live_rows_do_not_break_the_export_and_are_recorded(marts, projection, tmp_path):
    add_live_postings(marts)
    destination, manifest = export_fixture(marts, projection, tmp_path)
    for oracle in (manifest["oracle_source"], manifest["oracle_csv"]):
        assert oracle["posting_rows"] == 5 and oracle["active_posting_days"] == 3
        assert oracle["archive_posting_rows"] == 2 and oracle["archive_active_posting_days"] == 1
    assert len(read(destination / "postings.csv")) == 5
    assert {"fact": "POSTINGS", "release": "LIVE_BOARD_FETCH 2026-11-06", "row_count": 3} in manifest["releases"]


def test_a_changed_archive_row_still_refuses_with_live_rows_present(marts, projection, tmp_path):
    add_live_postings(marts)
    con = duckdb.connect(str(marts))
    con.execute("delete from MARTS.fact_job_posting_daily where posting_id like 'ashby:Acme:%/2'")
    con.close()
    with pytest.raises(ex.Refused, match="archive_posting_rows"):
        export_fixture(marts, projection, tmp_path)


def test_csv_files_must_match_the_source_totals(marts, projection, tmp_path, monkeypatch):
    real = ex.oracle_from_csv
    monkeypatch.setattr(ex, "oracle_from_csv", lambda stage: {**real(stage), "posting_rows": 99})
    with pytest.raises(ex.Refused, match="written CSV files.*posting_rows"):
        export_fixture(marts, projection, tmp_path)
    assert not list((tmp_path / "out").iterdir())


def test_snowflake_export_is_refused_while_stop_exists(projection, tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError("must not connect")

    monkeypatch.setattr(ex, "connect_snowflake", forbidden)
    ex.STOP_MARKER.write_text("")
    with pytest.raises(ex.Refused, match="STOP"):
        ex.export("snowflake", out_root=tmp_path / "out", projection=projection, now=FIXED_NOW)
    assert not (tmp_path / "out").exists()


def test_local_export_ignores_stop_because_it_spends_nothing(marts, projection, tmp_path):
    ex.STOP_MARKER.write_text("")
    assert export_fixture(marts, projection, tmp_path)[0].is_dir()


def test_oracle_within_tolerance_passes_and_null_fails():
    ex.check_oracle({"a": 1.000000001}, {"a": 1.0}, 1e-8, "x")
    with pytest.raises(ex.Refused):
        ex.check_oracle({"a": 1.00000002}, {"a": 1.0}, 1e-8, "x")
    with pytest.raises(ex.Refused):
        ex.check_oracle({"a": None}, {"a": 1.0}, 1e-8, "x")


def test_csv_oracle_catches_a_corrupted_file(marts, projection, tmp_path):
    destination, manifest = export_fixture(marts, projection, tmp_path)
    assert ex.oracle_from_csv(destination) == manifest["oracle_csv"]
    lines = (destination / "lca.csv").read_text().splitlines(keepends=True)
    (destination / "lca.csv").write_text("".join(lines[:-1]))
    with pytest.raises(ex.Refused, match="case_count"):
        ex.check_oracle(ex.oracle_from_csv(destination), projection["oracle"], 1e-8, "csv")


def test_snapshot_directories_are_never_overwritten(marts, projection, tmp_path):
    export_fixture(marts, projection, tmp_path)
    with pytest.raises(ex.Refused):
        export_fixture(marts, projection, tmp_path)


class FakeSnowflake:
    """Mock connector backed by the fixture: it records SQL and never touches a network."""

    def __init__(self, marts, identity=("CUBE_SVC", "ANALYST", "SPONSORATLAS_XS", '{"roles":"","value":""}')):
        self.con = duckdb.connect(str(marts), read_only=True)
        self.identity = identity
        self.sql = []
        self.closed = False
        self.result = None

    def cursor(self):
        return self

    def execute(self, sql):
        self.sql.append(sql)
        if sql.startswith("USE SECONDARY"):
            return self
        if sql.startswith("SELECT CURRENT_USER"):
            self.result = [self.identity]
            return self
        self.result = None
        self.con.execute(sql.replace("ANALYTICS.MARTS", "MARTS"))
        return self

    @property
    def description(self):
        return [(item[0].upper(),) for item in self.con.description]

    def fetchone(self):
        return self.result[0] if self.result is not None else self.con.fetchone()

    def fetchmany(self, size):
        return self.con.fetchmany(size)

    def close(self):
        self.closed = True
        self.con.close()


def test_snowflake_source_is_mocked_and_records_identity(marts, projection, tmp_path, monkeypatch):
    fake = FakeSnowflake(marts)
    monkeypatch.setattr(ex, "connect_snowflake", lambda: fake)
    destination, manifest = ex.export("snowflake", out_root=tmp_path / "out", projection=projection, now=FIXED_NOW)
    assert manifest["kind"] == ex.KIND_ACTUAL
    assert {row["snapshot_kind"] for row in read(destination / "lca.csv")} == {ex.KIND_ACTUAL}
    assert manifest["identity"] == {"user": "CUBE_SVC", "role": "ANALYST", "warehouse": "SPONSORATLAS_XS"}
    assert "ANALYTICS.MARTS.fact_lca_case" in manifest["queries"]["lca"]
    assert fake.closed and destination.is_dir()
    statements = [sql.lstrip().lower() for sql in fake.sql]
    assert all(sql.startswith(("use secondary", "select", "with")) for sql in statements), "SELECT only"


def test_snowflake_wrong_identity_refused(marts, projection, tmp_path, monkeypatch):
    fake = FakeSnowflake(marts, identity=("OTHER", "ANALYST", "SPONSORATLAS_XS", '{"roles":"","value":""}'))
    monkeypatch.setattr(ex, "connect_snowflake", lambda: fake)
    with pytest.raises(ex.Refused, match="identity"):
        ex.export("snowflake", out_root=tmp_path / "out", projection=projection, now=FIXED_NOW)


def test_snowflake_refused_after_trial_without_connecting(projection, tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError("must not connect")

    monkeypatch.setattr(ex, "connect_snowflake", forbidden)
    late = dt.datetime(2026, 11, 23, 23, 55, tzinfo=dt.UTC)
    with pytest.raises(ex.Refused, match="trial"):
        ex.export("snowflake", out_root=tmp_path / "out", projection=projection, now=late)


def test_connection_helper_receives_only_the_key_path(tmp_path, monkeypatch):
    key = tmp_path / "CUBE_SVC.p8"
    key.write_text("not a real key")
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "EXAMPLE-ACCOUNT")
    monkeypatch.setenv("SPONSORATLAS_CUBE_PRIVATE_KEY_FILE", str(key))
    monkeypatch.delenv("SPONSORATLAS_CUBE_PRIVATE_KEY_PASSPHRASE", raising=False)
    captured = {}
    import snowflake.connector

    monkeypatch.setattr(snowflake.connector, "connect", lambda **options: captured.update(options) or "connection")
    assert ex.connect_snowflake() == "connection"
    assert captured["user"] == "CUBE_SVC" and captured["role"] == "ANALYST"
    assert captured["warehouse"] == "SPONSORATLAS_XS"
    assert captured["private_key_file"] == str(key)
    assert "not a real key" not in json.dumps({k: str(v) for k, v in captured.items()})


def many_employers_snapshot(tmp_path):
    """A snapshot with only the private file and lca.csv: 26 companies, one person-like name, one small."""
    snapshot = tmp_path / "snap"
    snapshot.mkdir()
    names = [("emp_person", "John Q Smith", 60), ("emp_small", "Tiny Inc", 49)]
    names += [(f"emp_{index:02d}", f"Company {index:02d} LLC", 200 - index) for index in range(26)]
    with (snapshot / ex.PRIVATE_NAMES).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("employer_id", "employer_id_full", "source_employer_name", "cases"))
        for employer, name, cases in names:
            writer.writerow((employer, employer, name, cases))
        writer.writerow(("emp_00", "emp_00", "Company 00 Variant", 3))
    with (snapshot / "lca.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("case_id", "employer_id"))
        for index, employer in enumerate(("emp_00", "emp_25", "emp_person", "emp_small")):
            writer.writerow((f"c{index}", employer))
    return snapshot


def reviewed(snapshot, path, **changes):
    ex.write_review_template(snapshot, path)
    review = json.loads(path.read_text())
    review.update(reviewed_by="reviewer", reviewed_at="2026-11-06", **changes)
    path.write_text(json.dumps(review))
    return review


def test_names_refused_without_a_review_file(tmp_path):
    snapshot = many_employers_snapshot(tmp_path)
    with pytest.raises(ex.Refused, match="review"):
        ex.apply_names(snapshot, None)
    with pytest.raises(ex.Refused, match="review"):
        ex.apply_names(snapshot, tmp_path / "absent.json")
    assert not (snapshot / ex.NAMED_LCA).exists()


def test_names_refused_for_an_unsigned_or_incomplete_review(tmp_path):
    snapshot = many_employers_snapshot(tmp_path)
    path = tmp_path / "review.json"
    ex.write_review_template(snapshot, path)
    with pytest.raises(ex.Refused, match="reviewed_by"):
        ex.apply_names(snapshot, path)
    review = reviewed(snapshot, path)
    review["top_25"] = review["top_25"][:24]
    path.write_text(json.dumps(review))
    with pytest.raises(ex.Refused, match="top 25"):
        ex.apply_names(snapshot, path)
    review = reviewed(snapshot, path, person_like=[])
    with pytest.raises(ex.Refused, match="person-like"):
        ex.apply_names(snapshot, path)


def test_names_apply_labels_others_and_review_decisions(tmp_path):
    snapshot = many_employers_snapshot(tmp_path)
    path = tmp_path / "review.json"
    review = reviewed(snapshot, path)
    review["top_25"][0]["label"] = "Reviewed Company Zero"
    path.write_text(json.dumps(review))
    record = ex.apply_names(snapshot, path)
    rows = {row["employer_id"]: row["employer_label"] for row in read(snapshot / ex.NAMED_LCA)}
    assert rows["emp_00"] == "Reviewed Company Zero"
    assert rows["emp_25"] == "Company 25 LLC"
    assert rows["emp_person"] == "Other", "A flagged person-like name stays hidden by decision"
    assert rows["emp_small"] == "Other", "Under 50 cases"
    assert record["label_note"] == ex.NAME_LABEL
    assert record["sha256"] == ex.sha256_file(snapshot / ex.NAMED_LCA)


def test_person_like_detection():
    assert ex.looks_person_like("John Q Smith")
    assert not ex.looks_person_like("Company 00 LLC")
    assert not ex.looks_person_like("AMAZON")
