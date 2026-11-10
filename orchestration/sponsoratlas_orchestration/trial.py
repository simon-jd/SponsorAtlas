"""One trial admission rule before external work; mirrors Cube's 30-day/7-minute guard."""

import os
import re
from datetime import UTC, datetime
from pathlib import Path

STOP = Path(__file__).resolve().parents[2] / ".tools/orchestration/STOP"


def refuse_when_stopped():
    """Preflight admits a run once; a STOP written later must end its remaining paid work."""
    if os.environ.get("SPONSORATLAS_ORCHESTRATION_MODE") == "production" and STOP.exists():
        raise RuntimeError("STOP marker present; external work refused")


def trial_seconds_left(*, now=None):
    """Seconds to the configured trial end, without the cleanup margin of trial_remaining."""
    mode = os.environ.get("SPONSORATLAS_ORCHESTRATION_MODE")
    if mode == "synthetic_http":
        if os.environ.get("DBT_TARGET") != "local":
            raise RuntimeError("Synthetic mode must use the local dbt target")
        return float("inf")
    if mode != "production":
        raise RuntimeError("Explicit production or synthetic_http execution mode required")

    def timestamp(key):
        value = os.environ.get(key, "")
        if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
            raise RuntimeError(f"Explicit UTC trial timestamp required: {key}")
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            raise RuntimeError("Invalid UTC trial timestamp") from None

    start, end = timestamp("SNOWFLAKE_TRIAL_STARTED_AT"), timestamp("SNOWFLAKE_TRIAL_EXPIRES_AT")
    now = now or datetime.now(UTC)
    if not 0 < (end - start).total_seconds() <= 30 * 86400 or now < start:
        raise RuntimeError("Snowflake trial must be active and at most 30 days")
    return (end - now).total_seconds()


def trial_remaining(minimum_seconds=0, *, now=None):
    remaining = trial_seconds_left(now=now) - 420
    if remaining <= minimum_seconds:
        raise RuntimeError(
            "Insufficient trial window for bounded work and cleanup; dispatch refused"
        )
    return remaining


def bounded_snowflake_options(options):
    trial_remaining(330)
    for name, value in {
        "user": "DBT_SVC",
        "role": "TRANSFORMER",
        "authenticator": "SNOWFLAKE_JWT",
        "warehouse": "SPONSORATLAS_XS",
    }.items():
        if options.get(name) != value:
            raise RuntimeError("Unexpected dbt Snowflake service identity")
    if options.get("password"):
        raise RuntimeError("Password authentication forbidden")
    result = {
        **options,
        "login_timeout": 15,
        # Above the 240 s statement limit: a shorter socket timeout would cancel statements first.
        "network_timeout": 300,
        "client_session_keep_alive": False,
    }
    result["session_parameters"] = {
        **options.get("session_parameters", {}),
        "STATEMENT_TIMEOUT_IN_SECONDS": 240,
        "STATEMENT_QUEUED_TIMEOUT_IN_SECONDS": 60,
        "ABORT_DETACHED_QUERY": True,
    }
    return result
