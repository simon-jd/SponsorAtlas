"""Task10 synthetic account-usage fixtures; no Snowflake account required."""

import pytest


def test_report_ranks_by_credits():
    from scripts.query_profile_report import build_report

    history = [
        {"QUERY_ID": str(i), "TOTAL_ELAPSED_TIME": i * 10, "PARTITIONS_SCANNED": i,
         "PARTITIONS_TOTAL": 100, "CREDITS_USED_CLOUD_SERVICES": 100 - i}
        for i in range(25)
    ]
    attribution = [{"QUERY_ID": str(i), "CREDITS_ATTRIBUTED_COMPUTE": i / 100,
                    "CREDITS_USED_QUERY_ACCELERATION": None} for i in range(25)]
    report = build_report(history, attribution, synthetic=True)
    assert report["kind"] == "SYNTHETIC_QUERY_HISTORY"
    assert [r["query_id"] for r in report["queries"]] == [str(i) for i in range(24, 4, -1)]
    assert report["queries"][0]["credits_attributed_compute"] == .24
    assert report["queries"][0]["elapsed_ms"] == 240


def test_missing_attribution_is_unknown_and_real_zero_is_zero():
    from scripts.query_profile_report import build_report

    report = build_report([{"QUERY_ID": "missing"}, {"QUERY_ID": "zero"}],
                          [{"QUERY_ID": "zero", "CREDITS_ATTRIBUTED_COMPUTE": 0}],
                          synthetic=True)
    assert [r["query_id"] for r in report["queries"]] == ["zero", "missing"]
    assert report["queries"][1]["credits_attributed_compute"] is None
    assert report["queries"][1]["attribution_status"] == "MISSING_DELAY_OR_SHORT_QUERY"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "bad"])
def test_invalid_credits_rejected(value):
    from scripts.query_profile_report import build_report

    with pytest.raises(ValueError):
        build_report([{"QUERY_ID": "bad"}],
                     [{"QUERY_ID": "bad", "CREDITS_ATTRIBUTED_COMPUTE": value}],
                     synthetic=True)


def test_private_sql_is_never_in_report():
    from scripts.query_profile_report import build_report

    report = build_report([{"QUERY_ID": "a", "QUERY_TEXT": "secret", "USER_NAME": "person"}],
                          [], synthetic=True)
    assert "secret" not in str(report) and "person" not in str(report)


def test_fixture_cli_has_no_connection_path(tmp_path, monkeypatch):
    import json
    import sys

    from scripts import query_profile_report as p

    source, output = tmp_path / "input.json", tmp_path / "out.json"
    source.write_text(json.dumps({"kind": "SYNTHETIC_QUERY_HISTORY", "QUERY_HISTORY": [],
                                  "QUERY_ATTRIBUTION_HISTORY": []}))
    monkeypatch.setattr(p, "connect", lambda: pytest.fail("fixture connected remotely"))
    monkeypatch.setattr(sys, "argv", ["profile", "fixture", str(source), str(output)])
    p.main()
    assert json.loads(output.read_text())["kind"] == "SYNTHETIC_QUERY_HISTORY"


def test_synthetic_or_infinite_trial_cannot_admit_remote():
    from scripts.query_profile_report import admission

    for env in [{}, {"SPONSORATLAS_COST_MODE": "synthetic_http"},
                {"SPONSORATLAS_COST_MODE": "production", "SPONSORATLAS_COST_OPT_IN": "YES",
                 "SNOWFLAKE_TRIAL_STARTED_AT": "-inf", "SNOWFLAKE_TRIAL_EXPIRES_AT": "inf"}]:
        with pytest.raises(ValueError):
            admission(env)


def test_bounded_session_rejects_late_result_but_can_cleanup():
    from scripts.query_profile_report import BoundedSession

    now = [0]

    class Cursor:
        sfqid = "actual-id"
        description = (("N",),)

        def execute(self, sql, params, timeout):
            assert timeout == (10 if sql == "SELECT 1" else 30)
            now[0] += 11

        def fetchall(self):
            return [(1,)]

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

    session = BoundedSession(Connection(), seconds=10, clock=lambda: now[0])
    with pytest.raises(TimeoutError, match="Late"):
        session.run("SELECT 1")
    assert session.query_ids == ["actual-id"]
    with pytest.raises(TimeoutError, match="Absolute"):
        session.run("SELECT 1")
    assert session.run("cleanup", cleanup=True) == [{"N": 1}]


def test_restore_attempts_all_actions_after_ambiguous_failure():
    from scripts.query_profile_report import restore_experiment

    class Session:
        session_id = 123

        def __init__(self):
            self.calls = []
            self.query_ids = []

        def run(self, sql, params=None, cleanup=False):
            assert cleanup
            self.calls.append(sql)
            self.query_ids.append("q-" + str(len(self.calls)))
            if "SUSPEND RECLUSTER" in sql:
                raise TimeoutError("ambiguous transport")
            if sql.startswith("SHOW SCHEMAS") and not any("DROP SCHEMA" in s for s in self.calls):
                return [{"NAME": "TASK10_TEST", "COMMENT": "Task10:" + "a" * 32}]
            if sql.startswith("SHOW TABLES"):
                return [{"NAME": "FACT_LCA_CASE", "CLUSTER_BY": "LINEAR(fiscal_year,worksite_state)"}]
            if sql.startswith("SHOW WAREHOUSES"):
                return [{"NAME": "SPONSORATLAS_XS", "SIZE": "X-Small", "AUTO_SUSPEND": 60,
                         "STATE": "SUSPENDED", "MIN_CLUSTER_COUNT": 1, "MAX_CLUSTER_COUNT": 1}]
            return []

    session = Session()
    report = {"mutation_protocol": 1, "originating_session": 123, "mutations": []}
    assert restore_experiment(session, "ANALYTICS.TASK10_TEST", "a" * 32,
                              report=report)["status"] == "OPEN"
    assert any("DROP SCHEMA" in sql for sql in session.calls)
    assert any("WAREHOUSE_SIZE='XSMALL'" in sql for sql in session.calls)
    assert session.calls[0].endswith("ABORT ALL QUERIES")


def test_experiment_lost_id_failure_is_journaled_and_requires_recovery(monkeypatch):
    from scripts import query_profile_report as p

    snapshots = []
    monkeypatch.setenv("SPONSORATLAS_COST_MAX_CREDITS", "1")
    monkeypatch.setenv("SPONSORATLAS_CLUSTERING_MAX_CREDITS", ".1")

    class Session:
        session_id = 123

        def __init__(self):
            self.query_ids = []

        def run(self, sql, params=None, cleanup=False):
            if sql.startswith("CREATE TRANSIENT"):
                raise TimeoutError("ambiguous clone response with no provider ID")
            self.query_ids.append("q-" + str(len(self.query_ids)))
            if sql.startswith("SHOW WAREHOUSES"):
                return [{"NAME": "SPONSORATLAS_XS", "SIZE": "X-Small", "AUTO_SUSPEND": 60,
                         "STATE": "SUSPENDED", "MIN_CLUSTER_COUNT": 1, "MAX_CLUSTER_COUNT": 1,
                         "RESOURCE_MONITOR": "TASK10_MONITOR"}]
            if sql.startswith("SHOW RESOURCE"):
                return [{"NAME": "TASK10_MONITOR", "LEVEL": "WAREHOUSE", "CREDIT_QUOTA": 1,
                         "SUSPEND_IMMEDIATELY_AT": "100%", "USED_CREDITS": 0}]
            return []

    with pytest.raises(TimeoutError):
        p.experiment(Session(), "TEST", 2025, "CA", checkpoint=lambda r: snapshots.append(
            __import__("copy").deepcopy(r)))
    assert snapshots[0]["status"] == "RUNNING"
    assert snapshots[-1]["status"] == "RECOVERY_REQUIRED"
    assert snapshots[-1]["restoration"]["status"] == "OPEN"
    assert snapshots[-1]["mutations"][-1]["query_id"] is None


def test_unsafe_lab_identifier_rejected_before_sql():
    from scripts.query_profile_report import dashboard_sql, restore_experiment

    with pytest.raises(ValueError):
        dashboard_sql("ANALYTICS.MARTS")
    with pytest.raises(ValueError):
        restore_experiment(None, "ANALYTICS.TASK10_X;DROP SCHEMA MARTS", "a" * 32)


def test_successful_experiment_runs_all_phases_and_clones_only_public_tables(monkeypatch):
    from scripts import query_profile_report as p

    monkeypatch.setenv("SPONSORATLAS_COST_MAX_CREDITS", "1")
    monkeypatch.setenv("SPONSORATLAS_CLUSTERING_MAX_CREDITS", ".1")
    cubes = []

    def cube(schema, run, phase, cached, remaining):
        cubes.append((phase, cached))
        return {"result_sha256": "same-seven-metrics", "query_ids": ["cube-" + phase]}

    monkeypatch.setattr(p, "cube_refresh", cube)

    class Session:
        deadline = 1000
        session_id = 123

        def __init__(self):
            self.query_ids, self.sql = [], []
            self.size = "X-Small"

        def clock(self):
            return 0

        def run(self, sql, params=None, cleanup=False):
            self.sql.append(sql)
            self.query_ids.append("q-" + str(len(self.sql)))
            if "SET WAREHOUSE_SIZE='SMALL'" in sql:
                self.size = "Small"
            if "SET WAREHOUSE_SIZE='XSMALL'" in sql:
                self.size = "X-Small"
            if sql.startswith("SHOW WAREHOUSES"):
                return [{"NAME": "SPONSORATLAS_XS", "SIZE": self.size, "AUTO_SUSPEND": 60,
                         "STATE": "SUSPENDED", "MIN_CLUSTER_COUNT": 1, "MAX_CLUSTER_COUNT": 1,
                         "RESOURCE_MONITOR": "TASK10_MONITOR"}]
            if sql.startswith("SHOW RESOURCE"):
                return [{"NAME": "TASK10_MONITOR", "LEVEL": "WAREHOUSE", "CREDIT_QUOTA": 1,
                         "SUSPEND_IMMEDIATELY_AT": "100%", "USED_CREDITS": 0}]
            if "AS CASE_COUNT" in sql:
                return [{"CASE_COUNT": 2}]
            if "AS BENCHMARK_ROWS" in sql:
                return [{"BENCHMARK_ROWS": 1}]
            if "HASH_AGG" in sql:
                return [{"N": 2, "H": 123}]
            if "AS POSTINGS" in sql:
                return [{"POSTINGS": 4}]
            return []

    session = Session()
    result = p.experiment(session, "TEST", 2025, "CA", checkpoint=lambda r: None)
    assert result["status"] == "MEASURED_ATTRIBUTION_PENDING"
    assert result["restoration"]["status"] == "VERIFIED"
    assert cubes == [("baseline", False), ("clustered", False), ("small", False),
                     ("cache_prime", True), ("cache_repeat", True)]
    clones = [sql for sql in session.sql if " CLONE " in sql]
    assert len(clones) == 6 and all(" CLONE ANALYTICS.MARTS." in sql for sql in clones)
    assert all(sample["credits"] is None for sample in result["samples"])


def test_reconciliation_keeps_missing_query_credits_unknown():
    from scripts.query_profile_report import reconcile_experiment

    class Session:
        def run(self, sql, params=None):
            if "QUERY_HISTORY q" in sql:
                assert "q.warehouse_name=%s" in sql and "q.query_id IN" in sql
                return [{"QUERY_ID": "a", "ATTRIBUTION_QUERY_ID": "a",
                         "CREDITS_ATTRIBUTED_COMPUTE": .1}]
            return []

    evidence = {"kind": "ACTUAL_SNOWFLAKE_SOURCE_REFRESH_EXPERIMENT",
                "schema": "ANALYTICS.TASK10_TEST", "samples": [
                    {"phase": "baseline", "query_tag": "SponsorAtlas_Task10_TEST_baseline",
                     "query_ids": ["a", "missing"], "cube": {
                         "query_tag": "SponsorAtlas_Task10_TEST_baseline_cube", "query_ids": ["a"]}}]}
    result = reconcile_experiment(Session(), evidence, "2026-10-24T00:00:00Z",
                                  "2026-10-24T01:00:00Z")
    assert result["samples"][0]["credits"] is None
    assert result["samples"][0]["cube"]["credits"] == .1


def test_remote_requires_real_finite_trial_dedicated_identity_and_owner_only_rsa(tmp_path):
    from datetime import UTC, datetime

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from scripts.query_profile_report import admission

    key = tmp_path / "cost-key.pem"
    key.write_bytes(rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    key.chmod(0o600)
    env = {"SPONSORATLAS_COST_MODE": "production", "SPONSORATLAS_COST_OPT_IN": "YES",
           "SNOWFLAKE_TRIAL_STARTED_AT": "2026-10-01T00:00:00Z",
           "SNOWFLAKE_TRIAL_EXPIRES_AT": "2026-10-30T00:00:00Z",
           "SPONSORATLAS_TRIAL_REMAINING_CREDITS": "10", "SPONSORATLAS_COST_MAX_CREDITS": "1",
           "SNOWFLAKE_ACCOUNT": "example-account", "SNOWFLAKE_COST_USER": "COST_OBSERVER_SVC",
           "SNOWFLAKE_COST_ROLE": "COST_OBSERVER", "SNOWFLAKE_COST_PRIVATE_KEY_FILE": str(key)}
    now = datetime(2026, 10, 24, tzinfo=UTC)
    assert admission(env, now=now)["authenticator"] == "SNOWFLAKE_JWT"
    for change in [{"SNOWFLAKE_COST_USER": "CUBE_SVC"}, {"SNOWFLAKE_PASSWORD": "forbidden"},
                   {"SNOWFLAKE_TRIAL_EXPIRES_AT": "2026-10-24T00:05:00Z"},
                   {"SPONSORATLAS_COST_MAX_CREDITS": "nan"}]:
        with pytest.raises(ValueError):
            admission({**env, **change}, now=now)
    key.chmod(0o644)
    with pytest.raises(ValueError, match="Owner-only"):
        admission(env, now=now)


def test_changed_namespace_owner_is_not_deleted():
    from scripts.query_profile_report import restore_experiment

    class Session:
        def __init__(self):
            self.sql = []

        def run(self, sql, params=None, cleanup=False):
            self.sql.append(sql)
            if sql.startswith("SHOW SCHEMAS"):
                return [{"NAME": "TASK10_TEST", "COMMENT": "belongs to another owner"}]
            if sql.startswith("SHOW WAREHOUSES"):
                return [{"NAME": "SPONSORATLAS_XS", "SIZE": "X-Small", "AUTO_SUSPEND": 60,
                         "STATE": "SUSPENDED", "MIN_CLUSTER_COUNT": 1, "MAX_CLUSTER_COUNT": 1}]
            return []

    session = Session()
    assert restore_experiment(session, "ANALYTICS.TASK10_TEST", "a" * 32)["status"] == "OPEN"
    assert not any(sql.startswith(("DROP", "ALTER TABLE")) for sql in session.sql)


def test_unresolved_durable_journal_blocks_new_remote_dispatch(tmp_path, monkeypatch):
    import argparse
    import json

    from scripts import query_profile_report as p

    monkeypatch.setattr(p, "ROOT", tmp_path)
    directory = tmp_path / ".tools/task10"
    p.durable_directory(directory)
    p.durable_checkpoint(directory / "remote-pending.json", {"status": "RUNNING"})
    monkeypatch.setattr(p, "connect", lambda **kwargs: pytest.fail("connected across pending barrier"))
    with pytest.raises(RuntimeError, match="unresolved"):
        p.remote_experiment(argparse.Namespace(mode="experiment"))
    assert json.loads((directory / "remote-pending.json").read_text())["status"] == "RUNNING"


def test_experiment_reads_snowflakes_suspend_immediately_at_column(monkeypatch):
    """SHOW RESOURCE MONITORS returns suspend_immediately_at; a row without it must be refused."""
    from scripts import query_profile_report as p

    monkeypatch.setenv("SPONSORATLAS_COST_MAX_CREDITS", "1")
    monkeypatch.setenv("SPONSORATLAS_CLUSTERING_MAX_CREDITS", ".1")

    class Session:
        session_id = 123

        def run(self, sql, params=None, cleanup=False):
            if sql.startswith("SHOW WAREHOUSES"):
                return [{"NAME": "SPONSORATLAS_XS", "SIZE": "X-Small", "AUTO_SUSPEND": 60,
                         "STATE": "SUSPENDED", "MIN_CLUSTER_COUNT": 1, "MAX_CLUSTER_COUNT": 1,
                         "RESOURCE_MONITOR": "TASK10_MONITOR"}]
            if sql.startswith("SHOW RESOURCE"):
                return [{"NAME": "TASK10_MONITOR", "LEVEL": "WAREHOUSE", "CREDIT_QUOTA": 1,
                         "SUSPEND_IMMEDIATE_AT": "100%", "USED_CREDITS": 0}]
            return []

    with pytest.raises(ValueError, match="immediate suspension"):
        p.experiment(Session(), "TEST", 2025, "CA", checkpoint=lambda r: None)
