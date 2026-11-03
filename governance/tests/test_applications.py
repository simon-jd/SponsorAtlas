"""Native dbt execution, synthetic only; these tests make no Snowflake security claim."""

import os
import subprocess
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[2]


def run_dbt(path, *args):
    env = {**os.environ, "SPONSORATLAS_DUCKDB_PATH": str(path), "DBT_TARGET": "local"}
    return subprocess.run(
        [
            str(ROOT / ".venv/bin/dbt"),
            *args,
            "--project-dir",
            str(ROOT / "dbt"),
            "--profiles-dir",
            str(ROOT / "dbt"),
            "--target-path",
            str(path.parent / "target"),
            "--log-path",
            str(path.parent / "logs"),
            "--vars",
            "{enable_private_applications: true, approved_application_owner_roles: [SPONSORATLAS_OWNER_SYNTHETIC, SPONSORATLAS_OWNER_OTHER]}",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.fixture
def private_db(tmp_path):
    from governance.fixture import create_fixture

    path = tmp_path / "synthetic_applications.duckdb"
    create_fixture(path)
    return path


def test_application_grain_lifecycle_and_rebuild(private_db):
    for flags in [[], [], ["--full-refresh"]]:
        result = run_dbt(private_db, "build", "--select", "tag:private_applications", *flags)
        assert result.returncode == 0, result.stdout + result.stderr
        with duckdb.connect(str(private_db), read_only=True) as db:
            rows = db.execute(
                "select application_id, event_id, contact_email from MARTS.fact_my_applications order by 1"
            ).fetchall()
        assert rows == [
            ("synthetic-app-1", "synthetic-e2", "recruiter@example.invalid"),
            ("synthetic-app-2", "synthetic-e3", None),
        ]
    # A physical CDC event deletion removes that event, never an unrelated application.
    with duckdb.connect(str(private_db)) as db:
        db.execute(
            "update RAW_JOBS.CDC_APPLICATION_EVENTS set _ab_cdc_deleted_at=timestamp '2026-01-05' where id='synthetic-e2'"
        )
    result = run_dbt(private_db, "build", "--select", "tag:private_applications")
    assert result.returncode == 0, result.stdout + result.stderr
    with duckdb.connect(str(private_db), read_only=True) as db:
        assert db.execute(
            "select event_id from MARTS.fact_my_applications where application_id='synthetic-app-1'"
        ).fetchone() == ("synthetic-e1",)


@pytest.mark.parametrize(
    "change",
    [
        "application_id=null",
        "owner_role=null",
        "owner_role='ANALYST'",
        "contract_version=1",
        "owner_role='SPONSORATLAS_OWNER_UNKNOWN'",
    ],
)
def test_unknown_contract_owner_or_grain_fails(private_db, change):
    with duckdb.connect(str(private_db)) as db:
        db.execute(f"update RAW_JOBS.CDC_APPLICATION_EVENTS set {change} where id='synthetic-e1'")
    result = run_dbt(private_db, "build", "--select", "tag:private_applications")
    assert result.returncode != 0
    assert "private_application_source_contract" in result.stdout
