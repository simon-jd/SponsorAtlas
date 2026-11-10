"""Native dbt merge, enforced-contract, grain and FK probes in isolated Task6 fixtures."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import yaml

from er.io import quote
from scripts.build_bls_references import ROOT
from scripts.canonical_paths import FACTS_DB
from scripts.local_duckdb import connect_local

FACTS = ["fact_lca_case", "fact_job_posting_daily", "fact_wage_benchmark"]


def summarize(rows):
    # Sort fixed-size row digests, not hundreds of MB of payload strings.
    digests = [
        hashlib.sha256(json.dumps(row, default=str, sort_keys=True).encode()).digest()
        for row in rows
    ]
    return hashlib.sha256(b"".join(sorted(digests))).hexdigest()


def contents(database):
    with connect_local(database) as c:
        result = {}
        for table in FACTS:
            cursor = c.execute(f"select * from MARTS.{table}")

            def rows(cursor=cursor):
                while batch := cursor.fetchmany(1000):
                    yield from batch

            hashes, count = [], 0
            for row in rows():
                count += 1
                hashes.append(hashlib.sha256(json.dumps(row, default=str).encode()).digest())
            result[table] = {
                "rows": count,
                "sha256": hashlib.sha256(b"".join(sorted(hashes))).hexdigest(),
            }
        return result


def run_dbt(database, output, label, args, *, project=None, expected=None):
    project = project or ROOT / "dbt"
    target = output / "target"
    env = dict(
        os.environ,
        SPONSORATLAS_DUCKDB_PATH=str(database),
        DBT_SEND_ANONYMOUS_USAGE_STATS="false",
        DBT_TARGET_PATH=str(target),
        DBT_LOG_PATH=str(output / "logs"),
        NO_COLOR="1",
    )
    cmd = [
        str(ROOT / ".venv/bin/dbt"),
        "--debug",
        "--warn-error",
        *args,
        "--project-dir",
        str(project),
        "--profiles-dir",
        str(ROOT / "dbt"),
    ]
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    log = proc.stdout + proc.stderr
    output.mkdir(parents=True, exist_ok=True)
    (output / (label + ".log")).write_text(log)
    result = json.loads((target / "run_results.json").read_text())["results"]
    record = {
        "label": label,
        "command": cmd,
        "exit_code": proc.returncode,
        "results": [
            {k: r.get(k) for k in ["unique_id", "status", "failures", "message"]} for r in result
        ],
    }
    (output / (label + ".json")).write_text(json.dumps(record, indent=2))
    if expected:
        failures = [r for r in result if r["status"] in ["fail", "error"]]
        if (
            proc.returncode == 0
            or not failures
            or not any(
                expected.lower() in (r["unique_id"] + " " + r.get("message", "")).lower()
                for r in failures
            )
        ):
            raise AssertionError(f"{label}: wrong failure, inspect log")
    elif proc.returncode:
        raise AssertionError(f"{label}: dbt failed, inspect log")
    return record


def prepare_fixture(actual, database):
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists():
        raise FileExistsError(database)
    with connect_local(database) as c:
        c.execute("attach " + quote(actual) + " as actual (read_only)")
        for schema in ["MARTS", "INTERMEDIATE", "FACTS_INPUT", "DIMENSIONS_INPUT", "STAGING"]:
            c.execute("create schema " + schema)
        for table in ["dim_employer", "dim_occupation", "dim_geography", "dim_date"]:
            c.execute(f"create table MARTS.{table} as select * from actual.MARTS.{table}")
        c.execute(
            "create table FACTS_INPUT.lca as select * from actual.FACTS_INPUT.lca order by case_number limit 2"
        )
        c.execute(
            "create table DIMENSIONS_INPUT.lca_attributes as select a.* from actual.DIMENSIONS_INPUT.lca_attributes a join FACTS_INPUT.lca l using(source_sha256,source_row_number)"
        )
        c.execute(
            "create table DIMENSIONS_INPUT.employer_sources as select * from actual.DIMENSIONS_INPUT.employer_sources"
        )
        c.execute(
            "create table STAGING.file_column_map as select * from actual.STAGING.file_column_map"
        )
        c.execute(
            "create table STAGING.stg_bls as select * from actual.STAGING.stg_bls where industry_group='cross-industry' and ownership_code='1235' and naics='000000' and area_type='1' and area_code='99' and soc_code in ('00-0000','13-1020','15-1252','21-1018')"
        )
        c.execute(
            "create table FACTS_INPUT.job_observations as select * from actual.FACTS_INPUT.job_observations order by job_key limit 2"
        )
        case, decision, source_key = c.execute(
            "select case_number,decision_date,source_employer_key from FACTS_INPUT.lca order by case_number limit 1"
        ).fetchone()
        # Explicitly synthetic capture-time intervals: exact midnight boundary and a later intraday split.
        c.execute(
            "insert into MARTS.dim_employer select * replace('fixture-old' as employer_key,'fixture-e' as employer_id,cast(? as timestamp)-interval '10 days' as valid_from,cast(? as timestamp) as valid_to,false as is_current) from MARTS.dim_employer limit 1",
            [decision, decision],
        )
        c.execute(
            "insert into MARTS.dim_employer select * replace('fixture-new' as employer_key,'fixture-e' as employer_id,cast(? as timestamp) as valid_from,cast(null as timestamp) as valid_to,true as is_current) from MARTS.dim_employer limit 1",
            [decision],
        )
        c.execute(
            "update DIMENSIONS_INPUT.employer_sources set employer_id='fixture-e' where source_system='lca' and source_employer_key=?",
            [source_key],
        )
        c.execute("update FACTS_INPUT.job_observations set replay_kind='SYNTHETIC_FACT_PROBE'")
    return case, decision


def verify_job_relationships(fixture, output, project):
    """Explicit relational fixture; this does not assert real board identity resolution."""
    runs = []
    with connect_local(fixture) as c:
        c.execute(
            "create or replace table MARTS.before_job_fk_probe as select * from MARTS.fact_job_posting_daily"
        )
        c.execute(
            "update MARTS.fact_job_posting_daily set employer_id='fixture-e',employer_key='fixture-new',occupation_key=(select occupation_key from MARTS.dim_occupation where soc_code='15-1252'),geography_key=(select geography_key from MARTS.dim_geography where area_type='1' and area_code='99'),employer_mapping_status='SYNTHETIC_RELATIONSHIP_PROBE',occupation_mapping_status='SYNTHETIC_RELATIONSHIP_PROBE',geography_mapping_status='SYNTHETIC_RELATIONSHIP_PROBE'"
        )
        c.execute(
            "create or replace table MARTS.valid_job_fk_probe as select * from MARTS.fact_job_posting_daily"
        )
    runs.append(
        run_dbt(
            fixture,
            output,
            "job-valid-fks",
            ["test", "--select", "fact_job_posting_daily"],
            project=project,
        )
    )
    for field in [
        "employer_id",
        "employer_key",
        "occupation_key",
        "geography_key",
        "observation_date_key",
    ]:
        with connect_local(fixture) as c:
            value = "99999999" if field.endswith("date_key") else "'INVALID_FIXTURE_FK'"
            c.execute(f"update MARTS.fact_job_posting_daily set {field}={value}")
        runs.append(
            run_dbt(
                fixture,
                output,
                "reject-job-fk-" + field,
                ["test", "--select", "fact_job_posting_daily"],
                project=project,
                expected="relationships_fact_job_posting_daily_" + field,
            )
        )
        with connect_local(fixture) as c:
            c.execute(
                "create or replace table MARTS.fact_job_posting_daily as select * from MARTS.valid_job_fk_probe"
            )
    with connect_local(fixture) as c:
        c.execute(
            "create or replace table MARTS.fact_job_posting_daily as select * from MARTS.before_job_fk_probe"
        )
    runs.append(
        run_dbt(
            fixture,
            output,
            "job-fks-restored",
            ["test", "--select", "fact_job_posting_daily"],
            project=project,
        )
    )
    return runs


def verify_unlisted(fixture, output, project):
    """An explicitly unlisted provider record remains in the daily fact."""
    with connect_local(fixture) as c:
        c.execute(
            "create or replace table FACTS_INPUT.before_listing_probe as select * from FACTS_INPUT.job_observations"
        )
        key = c.execute(
            "select max(job_key) from FACTS_INPUT.job_observations where source_provider='ashby'"
        ).fetchone()[0]
        c.execute(
            "update FACTS_INPUT.job_observations set payload_json=json_merge_patch(payload_json,'{\"isListed\":false}') where job_key=?",
            [key],
        )
    runs = [
        run_dbt(
            fixture,
            output,
            "unlisted-false-retained",
            ["build", "--select", "fact_job_posting_daily"],
            project=project,
        )
    ]
    with connect_local(fixture) as c:
        values = c.execute(
            "select is_listed,is_active from MARTS.fact_job_posting_daily where posting_id=?", [key]
        ).fetchall()
        assert values and all(v == (False, False) for v in values)
        c.execute(
            "create or replace table FACTS_INPUT.job_observations as select * from FACTS_INPUT.before_listing_probe"
        )
    runs.append(
        run_dbt(
            fixture,
            output,
            "unlisted-restored",
            ["build", "--select", "fact_job_posting_daily"],
            project=project,
        )
    )
    return runs


def verify(actual, output):
    actual, output = Path(actual).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    runs = []
    initial = contents(actual)
    for i in range(2):
        runs.append(
            run_dbt(actual, output, f"actual-merge-{i + 1}", ["build", "--select", "tag:facts"])
        )
        assert contents(actual) == initial, "actual rerun changed count or content"
    actual_logs = "".join((output / f"actual-merge-{i + 1}.log").read_text() for i in range(2))
    # Two runs of the two merge facts; fact_job_posting_daily replaces dates with delete+insert.
    if actual_logs.lower().count("merge into") < 4:
        raise AssertionError("native MERGE SQL missing")
    fixture = output / "fixture.duckdb"
    case, decision = prepare_fixture(actual, fixture)
    project = output / "project"
    shutil.copytree(
        ROOT / "dbt", project, ignore=shutil.ignore_patterns("target", "logs", "evidence")
    )

    def dbt(label, args, expected=None):
        r = run_dbt(fixture, output, label, args, project=project, expected=expected)
        runs.append(r)
        return r

    dbt("fixture-initial", ["build", "--select", "int_dimension_mapping", "tag:facts"])
    with connect_local(fixture) as c:
        assert (
            c.execute(
                "select employer_key from MARTS.fact_lca_case where case_number=?", [case]
            ).fetchone()[0]
            == "fixture-new"
        )
        old_wage = c.execute(
            "select annual_wage from MARTS.fact_lca_case where case_number=?", [case]
        ).fetchone()[0]
        # Late newer decision inserts an occurrence and updates existing case; a new case inserts.
        c.execute(
            "insert into FACTS_INPUT.lca select * replace(decision_date+interval '1 day' as decision_date,'Denied' as case_status,annual_wage+100 as annual_wage,900001::bigint as source_row_number) from FACTS_INPUT.lca where case_number=?",
            [case],
        )
        c.execute(
            "insert into FACTS_INPUT.lca select * replace('SYNTHETIC_NEW_CASE' as case_number,900002::bigint as source_row_number) from FACTS_INPUT.lca where case_number=? and source_row_number<>900001",
            [case],
        )
        c.execute(
            "insert into FACTS_INPUT.job_observations select * replace(observed_at_utc+interval '1 hour' as observed_at_utc,replace(payload_json,'Engineer','Revised Engineer') as payload_json,cast('2026-10-24 03:00:00' as timestamp) as source_deleted_at) from FACTS_INPUT.job_observations limit 1"
        )
        c.execute(
            "insert into FACTS_INPUT.job_observations select * replace(observed_at_utc+interval '1 day' as observed_at_utc) from FACTS_INPUT.job_observations where source_deleted_at is null limit 1"
        )
        c.execute(
            "insert into FACTS_INPUT.job_observations select * replace('postgres_cdc:fixture:deleted' as job_key,'postgres_cdc' as source_provider,'fixture' as source_board,'{\"title\":\"CDC fixture\",\"company_name\":\"Unresolved demo\"}' as payload_json,cast('2026-10-24 03:00:00' as timestamp) as source_deleted_at) from FACTS_INPUT.job_observations limit 1"
        )
        # Propagate only fixture occurrence mapping fields for the two new source occurrences.
        cols = [r[0] for r in c.execute("describe DIMENSIONS_INPUT.lca_attributes").fetchall()]
        c.execute(
            "insert into DIMENSIONS_INPUT.lca_attributes select "
            + ",".join(cols)
            + " from FACTS_INPUT.lca where source_row_number in (900001,900002)"
        )
        c.execute(
            "update STAGING.stg_bls set annual_median_wage=annual_median_wage+10,annual_median_wage_raw=cast(annual_median_wage+10 as varchar) where soc_code='15-1252'"
        )
        c.execute(
            "insert into STAGING.stg_bls select * replace(2026 as survey_year,2026 as fiscal_year) from STAGING.stg_bls where soc_code='15-1252'"
        )
    dbt("fixture-merge-change", ["build", "--select", "int_dimension_mapping", "tag:facts"])
    with connect_local(fixture) as c:
        assert c.execute(
            "select case_status,annual_wage from MARTS.fact_lca_case where case_number=?", [case]
        ).fetchone() == ("Denied", old_wage + 100)
        assert c.execute("select count(*) from MARTS.fact_lca_case").fetchone()[0] == 3
        assert c.execute("select count(*) from MARTS.fact_job_posting_daily").fetchone()[0] == 4
        assert (
            c.execute(
                "select count(*) from MARTS.fact_job_posting_daily where not is_active"
            ).fetchone()[0]
            == 2
        )
        assert c.execute("select count(*) from MARTS.fact_wage_benchmark").fetchone()[0] == 5
        # Append a genuinely older case record after the newer one has merged.
        c.execute(
            "insert into FACTS_INPUT.lca select * replace(decision_date-interval '100 days' as decision_date,'OLD_LATE' as case_status,900003::bigint as source_row_number) from FACTS_INPUT.lca where source_row_number=900001"
        )
        cols = [r[0] for r in c.execute("describe DIMENSIONS_INPUT.lca_attributes").fetchall()]
        c.execute(
            "insert into DIMENSIONS_INPUT.lca_attributes select "
            + ",".join(cols)
            + " from FACTS_INPUT.lca where source_row_number=900003"
        )
    dbt("fixture-late-old", ["build", "--select", "int_dimension_mapping", "tag:facts"])
    changed = contents(fixture)
    with connect_local(fixture) as c:
        assert (
            c.execute(
                "select case_status from MARTS.fact_lca_case where case_number=?", [case]
            ).fetchone()[0]
            == "Denied"
        )
    for i in range(2):
        dbt(f"fixture-idempotent-{i + 1}", ["build", "--select", "tag:facts"])
        assert contents(fixture) == changed
    # dbt must reject the actual compiled SQL type against the enforced YAML contract.
    contract = project / "models/marts/fact_lca_case.yml"
    original = contract.read_text()
    data = yaml.safe_load(original)
    next(c for c in data["models"][0]["columns"] if c["name"] == "annual_wage")["data_type"] = (
        "varchar"
    )
    contract.write_text(yaml.safe_dump(data, sort_keys=False))
    dbt("reject-contract-type", ["build", "--select", "fact_lca_case"], expected="contract")
    contract.write_text(original)
    # SQL data tests must fail against concrete invalid rows, never missing-node/credential errors.
    with connect_local(fixture) as c:
        c.execute("create table MARTS.pristine_lca as select * from MARTS.fact_lca_case")
        c.execute("create table MARTS.pristine_jobs as select * from MARTS.fact_job_posting_daily")
        c.execute("create table MARTS.pristine_wage as select * from MARTS.fact_wage_benchmark")
    for name, short in zip(FACTS, ["lca", "jobs", "wage"]):
        with connect_local(fixture) as c:
            c.execute(f"insert into MARTS.{name} select * from MARTS.{name} limit 1")
        dbt(
            "reject-grain-" + short,
            ["test", "--select", name, "facts_grain"],
            expected="unique" if short == "lca" else "facts_grain",
        )
        with connect_local(fixture) as c:
            c.execute(
                f"create or replace table MARTS.{name} as select * from MARTS.pristine_{short}"
            )
    for field in ["employer_key", "occupation_key", "geography_key", "decision_date_key"]:
        with connect_local(fixture) as c:
            value = "99999999" if field.endswith("date_key") else "'INVALID_FIXTURE_FK'"
            c.execute(f"update MARTS.fact_lca_case set {field}={value} where case_number=?", [case])
        dbt(
            "reject-fk-" + field,
            ["test", "--select", "fact_lca_case"],
            expected="relationships_fact_lca_case_" + field,
        )
        with connect_local(fixture) as c:
            c.execute(
                "create or replace table MARTS.fact_lca_case as select * from MARTS.pristine_lca"
            )
    with connect_local(fixture) as c:
        c.execute("create table STAGING.pristine_bls as select * from STAGING.stg_bls")
        c.execute(
            'update STAGING.stg_bls set payload_json=json_merge_patch(payload_json,\'{"UNEXPECTED_NEW_COLUMN":"x"}\')'
        )
    dbt(
        "reject-unknown-bls-column",
        ["test", "--select", "facts_bls_headers"],
        expected="facts_bls_headers",
    )
    with connect_local(fixture) as c:
        c.execute("create or replace table STAGING.stg_bls as select * from STAGING.pristine_bls")
    with connect_local(fixture) as c:
        c.execute(
            "update STAGING.stg_bls set annual_median_wage_raw=cast(annual_median_wage+1 as varchar) where soc_code='13-1020' and occupation_group='broad'"
        )
    dbt(
        "reject-conflicting-oews",
        ["build", "--select", "int_bls_benchmark_candidates", "facts_bls_conflicts"],
        expected="facts_bls_conflicts",
    )
    with connect_local(fixture) as c:
        c.execute(
            "update STAGING.stg_bls set annual_median_wage_raw=(select annual_median_wage_raw from STAGING.stg_bls where soc_code='13-1020' and occupation_group='detailed') where soc_code='13-1020' and occupation_group='broad'"
        )
        # Date-only event straddles an intraday version boundary: never invent midnight precision.
        c.execute(
            "update MARTS.dim_employer set valid_to=cast(? as timestamp)+interval '12 hours',is_current=false where employer_key='fixture-new'",
            [decision + timedelta(days=1)],
        )
        c.execute(
            "insert into MARTS.dim_employer select * replace('fixture-intraday' as employer_key,valid_to as valid_from,cast(null as timestamp) as valid_to,true as is_current) from MARTS.dim_employer where employer_key='fixture-new'"
        )
    dbt("fixture-date-ambiguity", ["build", "--select", "tag:facts"])
    with connect_local(fixture) as c:
        assert c.execute(
            "select employer_key,employer_history_status from MARTS.fact_lca_case where case_number=?",
            [case],
        ).fetchone() == (None, "DATE_PRECISION_OR_INTERVAL_UNKNOWN")
    runs.extend(verify_job_relationships(fixture, output, project))
    runs.extend(verify_unlisted(fixture, output, project))
    evidence = {
        "actual": initial,
        "actual_idempotent": contents(actual),
        "fixture": contents(fixture),
        "scope": "Actual official LCA/BLS and public jobs versus explicitly synthetic probes",
        "runs": runs,
        "native_merge_statements": actual_logs.lower().count("merge into"),
    }
    (output / "evidence.json").write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    return evidence


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--actual", type=Path, default=FACTS_DB)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    e = verify(a.actual, a.output)
    print(
        json.dumps(
            {
                "runs": len(e["runs"]),
                "native_merge_statements": e["native_merge_statements"],
                "actual": e["actual"],
                "fixture": e["fixture"],
            },
            indent=2,
        )
    )
