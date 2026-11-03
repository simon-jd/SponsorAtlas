from datetime import UTC, datetime, timedelta

import pytest

from orchestration.sponsoratlas_orchestration.trial import (
    bounded_snowflake_options,
    trial_remaining,
)


def test_missing_expired_or_oversize_trial_fails_before_dispatch(monkeypatch):
    monkeypatch.setenv("SPONSORATLAS_ORCHESTRATION_MODE", "production")
    now = datetime(2026, 10, 24, tzinfo=UTC)
    with pytest.raises(RuntimeError):
        trial_remaining(960, now=now)
    monkeypatch.setenv("SNOWFLAKE_TRIAL_STARTED_AT", "2026-10-01T00:00:00Z")
    monkeypatch.setenv("SNOWFLAKE_TRIAL_EXPIRES_AT", "2026-10-24T00:00:00Z")
    with pytest.raises(RuntimeError):
        trial_remaining(960, now=now)
    monkeypatch.setenv("SNOWFLAKE_TRIAL_EXPIRES_AT", "2026-11-02T00:00:00Z")
    with pytest.raises(RuntimeError):
        trial_remaining(960, now=now)
    monkeypatch.setenv("SNOWFLAKE_TRIAL_EXPIRES_AT", "2026-10-25T00:00:00Z")
    assert trial_remaining(960, now=now) == 86400 - 420
    with pytest.raises(RuntimeError):
        trial_remaining(960, now=now + timedelta(hours=23, minutes=50))


def test_production_snowflake_cli_options_are_bounded_and_identity_fixed(monkeypatch):
    monkeypatch.setenv("SPONSORATLAS_ORCHESTRATION_MODE", "production")
    # The window is relative to the real clock: a fixed expiry turns this test red once it passes.
    now = datetime.now(UTC).replace(microsecond=0)
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    monkeypatch.setenv("SNOWFLAKE_TRIAL_STARTED_AT", (now - timedelta(days=1)).strftime(stamp))
    monkeypatch.setenv("SNOWFLAKE_TRIAL_EXPIRES_AT", (now + timedelta(days=5)).strftime(stamp))
    options = bounded_snowflake_options(
        {
            "user": "DBT_SVC",
            "role": "TRANSFORMER",
            "authenticator": "SNOWFLAKE_JWT",
            "warehouse": "SPONSORATLAS_XS",
        }
    )
    assert options["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == 240
    assert options["session_parameters"]["STATEMENT_QUEUED_TIMEOUT_IN_SECONDS"] == 60
    assert options["session_parameters"]["ABORT_DETACHED_QUERY"] is True
    assert options["login_timeout"] == 15 and options["network_timeout"] == 30
    with pytest.raises(RuntimeError):
        bounded_snowflake_options({"user": "ACCOUNTADMIN"})


def test_dbt_budget_recomputed_before_spawn_and_late_return_rejected(monkeypatch):
    from orchestration.sponsoratlas_orchestration import dbt_entry

    waits = []

    class Child:
        def wait(self, *, timeout):
            waits.append(timeout)
            return 0

    monkeypatch.setattr(dbt_entry.subprocess, "Popen", lambda *_args, **_kwargs: Child())
    values = iter([1000, 100, 80])
    monkeypatch.setattr(dbt_entry, "trial_remaining", lambda *_args: next(values))
    assert dbt_entry.main(["--version"]) == 0
    assert waits == [40], "Dispatch must use cutoff-now-cleanup, even after a delayed admission"

    def remaining(_minimum=0):
        if waits and len(waits) > 1:
            raise RuntimeError("trial cutoff crossed on late return")
        return 1000

    monkeypatch.setattr(dbt_entry, "trial_remaining", remaining)
    with pytest.raises(RuntimeError, match="late return"):
        dbt_entry.main(["--version"])
