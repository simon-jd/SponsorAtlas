"""Native dbt source freshness on an isolated database: no fixture can bypass the gate."""

import json
import os
import subprocess

import pytest

from orchestration.sponsoratlas_orchestration import runtime

ROOT = runtime.ROOT
COLUMNS = (
    "job_key varchar,source_provider varchar,source_board varchar,source_url varchar,"
    "payload_json varchar,observed_at_utc timestamp,source_sha256 varchar,"
    "source_deleted_at timestamp,replay_kind varchar"
)


def run_duckdb(database, sql):
    code = "import duckdb,sys\nwith duckdb.connect(sys.argv[1]) as c:\n c.execute(sys.argv[2])"
    subprocess.run([str(ROOT / ".venv/bin/python"), "-c", code, str(database), sql], check=True)


@pytest.fixture
def gate(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "ARTIFACTS_ROOT", tmp_path)
    monkeypatch.setenv("SPONSORATLAS_ORCHESTRATION_MODE", "synthetic_http")
    monkeypatch.setenv("DBT_TARGET", "local")
    monkeypatch.setenv("SPONSORATLAS_DUCKDB_PATH", str(tmp_path / "freshness.duckdb"))
    return tmp_path / "freshness.duckdb"


def test_native_24h_gate_on_job_observations(gate):
    run_duckdb(
        gate,
        f"create schema FACTS_INPUT;create table FACTS_INPUT.job_observations({COLUMNS});"
        "insert into FACTS_INPUT.job_observations(job_key,observed_at_utc) "
        "values('a',timezone('UTC',current_timestamp)-interval '25 hours')",
    )
    with pytest.raises(subprocess.CalledProcessError):
        runtime.freshness(run_id="stale")
    run_duckdb(
        gate,
        "update FACTS_INPUT.job_observations set observed_at_utc=timezone('UTC',current_timestamp)",
    )
    runtime.freshness(run_id="fresh")


def test_gate_selects_only_job_observations_with_vars(monkeypatch, gate, tmp_path):
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        target = _kwargs["env"]["DBT_TARGET_PATH"]
        os.makedirs(target)
        with open(target + "/sources.json", "w") as handle:
            json.dump(
                {"results": [{"unique_id": runtime.OBSERVATION_SOURCE_ID, "status": "pass"}]},
                handle,
            )

    monkeypatch.setattr(runtime.subprocess, "run", run)
    runtime.freshness(run_id="selection")
    command = commands[0]
    assert command[command.index("--select") + 1] == "source:facts_input.job_observations"
    assert not any("source:jobs" in part for part in command)
    assert json.loads(command[command.index("--vars") + 1]) == {
        "enable_private_applications": False,
        "snapshot_schema": "INTERMEDIATE",
    }


@pytest.mark.parametrize(
    "results",
    [[], [{"unique_id": runtime.OBSERVATION_SOURCE_ID, "status": "warn"}]],
)
def test_nothing_to_do_or_unproven_is_never_a_pass(monkeypatch, gate, results):
    """dbt exits 0 with empty results for a source that declares no freshness."""

    def run(_command, **kwargs):
        target = kwargs["env"]["DBT_TARGET_PATH"]
        os.makedirs(target)
        with open(target + "/sources.json", "w") as handle:
            json.dump({"results": results}, handle)

    monkeypatch.setattr(runtime.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="not proven fresh"):
        runtime.freshness(run_id="unproven-" + str(len(results)))
