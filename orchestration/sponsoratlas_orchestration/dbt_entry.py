"""Existing native dbt CLI in its existing environment, with orchestration-only bounds."""

import os
import signal
import subprocess
import sys
from pathlib import Path

from orchestration.sponsoratlas_orchestration.trial import (
    bounded_snowflake_options,
    trial_remaining,
)

ROOT = Path(__file__).resolve().parents[2]


def worker(arguments):
    if os.environ.get("SPONSORATLAS_ORCHESTRATION_MODE") == "production":
        import snowflake.connector

        connect = snowflake.connector.connect

        def guarded_connect(**options):
            return connect(**bounded_snowflake_options(options))

        snowflake.connector.connect = guarded_connect
    from dbt.cli.main import cli

    cli(args=arguments)


def main(arguments):
    trial_remaining(960)  # 840s native process +25s signal cleanup, before the 7min server margin
    budget = min(
        840,
        float(os.environ.get("SPONSORATLAS_DBT_PROCESS_TIMEOUT", "840")),
        trial_remaining(0) - 60,
    )
    if budget < 1:
        raise RuntimeError("No safe native dbt subprocess window remains")
    process = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), "-m", __package__ + ".dbt_entry", "--worker", *arguments],
        start_new_session=True,
    )
    try:
        result = process.wait(timeout=budget)
        trial_remaining(0)  # A late return must never be reported as a successful trial run.
        return result
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        for sig, wait in [(signal.SIGINT, 10), (signal.SIGTERM, 10), (signal.SIGKILL, 5)]:
            if process.poll() is not None:
                break
            os.killpg(process.pid, sig)
            try:
                process.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                pass
        raise RuntimeError("Native dbt exceeded orchestration subprocess budget") from None


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        worker(sys.argv[2:])
    else:
        raise SystemExit(main(sys.argv[1:]))
