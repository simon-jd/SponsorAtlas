"""Opt-in CDC acceptance probe for dedicated demo_schema tables; never run on production.

The whole insert/update/delete sequence for both tables must finish within 900s.
This does real mutations only with --run and an explicitly provided test DSN.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

from scripts.reconcile_counts import snowflake_connection


class Deadline:
    """One absolute clock shared by polling, transport, and database statements."""

    def __init__(self, budget=900, monotonic=time.monotonic):
        if not math.isfinite(budget) or budget <= 0:
            raise ValueError("CDC budget must be positive and finite")
        self.budget = budget
        self.monotonic = monotonic
        self.started = monotonic()

    def elapsed(self):
        elapsed = self.monotonic() - self.started
        if elapsed > self.budget:
            raise TimeoutError(f"CDC exceeded {self.budget}s total acceptance deadline")
        return elapsed

    def remaining(self, cap=None):
        remaining = self.budget - self.elapsed()
        if remaining <= 0:
            raise TimeoutError("CDC deadline exhausted; no further external call allowed")
        return min(remaining, cap) if cap is not None else remaining

    def seconds(self, cap=30):
        # Snowflake timeout=0 disables its timer: never round a subsecond budget to0.
        seconds = int(self.remaining(cap))
        if seconds < 1:
            raise TimeoutError("Insufficient CDC budget for an integer-second SQL timeout")
        return seconds

    def call(self, operation, *args, **kwargs):
        self.remaining()
        try:
            return operation(*args, **kwargs)
        finally:
            self.elapsed()


def probe(
    mutate,
    fetch,
    trigger,
    *,
    job_done=lambda job: True,
    monotonic=time.monotonic,
    wait=time.sleep,
    budget=900,
    deadline=None,
):
    deadline = deadline if deadline is not None else Deadline(budget, monotonic)
    evidence = []
    for phase in ["insert", "update", "delete"]:
        phase_start = deadline.elapsed()
        deadline.call(mutate, phase)
        mutated_at = datetime.now(UTC).isoformat()
        job = deadline.call(trigger)
        while True:
            rows = deadline.call(fetch)
            observed = all(
                row is not None
                and (
                    row[1] is not None if phase == "delete" else row[0] == phase and row[1] is None
                )
                for row in rows
            )
            if len(rows) == 2 and observed and deadline.call(job_done, job):
                # The job lookup itself can cross the limit even when it returns True.
                elapsed = deadline.elapsed()
                evidence.append(
                    {
                        "phase": phase,
                        "source_committed_at_utc": mutated_at,
                        "observed_at_utc": datetime.now(UTC).isoformat(),
                        "lag_seconds": elapsed - phase_start,
                        "job": job,
                    }
                )
                break
            deadline.call(wait, deadline.remaining(5))
    # Check and use the same final clock sample. PASS can never report >budget.
    elapsed = deadline.elapsed()
    return {
        "status": "PASS",
        "deadline_seconds": deadline.budget,
        "total_seconds": elapsed,
        "operations": evidence,
    }


class AirbyteJobs:
    def __init__(self, deadline):
        self.deadline = deadline
        self.url = os.environ["AIRBYTE_API_URL"].rstrip("/")
        parsed = urllib.parse.urlsplit(self.url)
        if (
            (
                parsed.scheme != "https"
                and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"})
            )
            or parsed.username
            or parsed.password
        ):
            raise ValueError("API must use HTTPS or localhost SSH tunnel")

    def request(self, path, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method="POST" if body is not None else "GET",
        )
        with self.deadline.call(
            urllib.request.urlopen, request, timeout=self.deadline.remaining(30)
        ) as response:
            payload = self.deadline.call(json.load, response)
        self.deadline.elapsed()
        return payload

    def token(self):
        # Official API recommends a fresh short-lived application token per request.
        return self.request(
            "/applications/token",
            {
                "client_id": os.environ["AIRBYTE_CLIENT_ID"],
                "client_secret": os.environ["AIRBYTE_CLIENT_SECRET"],
                "grant-type": "client_credentials",
            },
        )["access_token"]

    def trigger(self):
        job = self.request(
            "/jobs",
            {"connectionId": os.environ["AIRBYTE_CDC_CONNECTION_ID"], "jobType": "sync"},
            self.token(),
        )
        return {"job_id": job["jobId"]}

    def done(self, job):
        current = self.request("/jobs/" + str(job["job_id"]), token=self.token())
        status = current["status"]
        if status in {"failed", "cancelled", "incomplete"}:
            raise RuntimeError("Airbyte sync job did not succeed")
        return status == "succeeded"


def postgres_execute(cursor, query, params, deadline):
    """Apply a remaining-budget server timeout before each mutation statement."""
    milliseconds = int(deadline.remaining(30) * 1000)
    if milliseconds < 1:
        raise TimeoutError("Insufficient CDC budget for PostgreSQL statement timeout")
    deadline.call(
        cursor.execute, "SELECT set_config('statement_timeout', %s, true)", (f"{milliseconds}ms",)
    )
    deadline.call(cursor.execute, query, params)


def postgres_mutate(connection, identifier, phase, deadline):
    with connection.transaction(), connection.cursor() as cursor:
        deadline.elapsed()
        if phase == "insert":
            statements = [
                (
                    "INSERT INTO public.job_postings(id,title,company_name) VALUES(%s,%s,%s)",
                    (identifier, phase, "SponsorAtlas synthetic CDC acceptance"),
                ),
                (
                    "INSERT INTO public.application_events(id,job_id,event_type,application_id,owner_role,contract_version) VALUES(%s,%s,%s,%s,%s,2)",
                    (
                        identifier,
                        identifier,
                        phase,
                        "application-" + identifier,
                        "SPONSORATLAS_OWNER_SYNTHETIC",
                    ),
                ),
            ]
        elif phase == "update":
            statements = [
                (
                    "UPDATE public.job_postings SET title=%s,updated_at=now() WHERE id=%s",
                    (phase, identifier),
                ),
                (
                    "UPDATE public.application_events SET event_type=%s,event_at=now() WHERE id=%s",
                    (phase, identifier),
                ),
            ]
        else:
            statements = [
                ("DELETE FROM public.application_events WHERE id=%s", (identifier,)),
                ("DELETE FROM public.job_postings WHERE id=%s", (identifier,)),
            ]
        for sql, params in statements:
            postgres_execute(cursor, sql, params, deadline)
        # Refresh the bound for COMMIT as well. Query/lock work cannot disable it.
        milliseconds = int(deadline.remaining(30) * 1000)
        if milliseconds < 1:
            raise TimeoutError("Insufficient CDC budget for PostgreSQL COMMIT")
        deadline.call(
            cursor.execute,
            "SELECT set_config('statement_timeout', %s, true)",
            (f"{milliseconds}ms",),
        )
    deadline.elapsed()


def snowflake_fetch(connection, identifier, deadline):
    rows = []
    with connection.cursor() as cursor:
        for table, field in [
            ("RAW.JOBS.CDC_JOB_POSTINGS", "TITLE"),
            ("RAW.JOBS.CDC_APPLICATION_EVENTS", "EVENT_TYPE"),
        ]:
            deadline.call(
                cursor.execute,
                "SELECT IDENTIFIER(%s), _AB_CDC_DELETED_AT FROM IDENTIFIER(%s) WHERE ID=%s",
                (field, table, identifier),
                timeout=deadline.seconds(),
            )
            rows.append(deadline.call(cursor.fetchone))
    deadline.elapsed()
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(".tools/ingestion/cdc-live.json"))
    args = parser.parse_args(argv)
    if not args.run:
        print(
            "NOT RUN: requires --run, dedicated demo_schema database, key-pair Snowflake, Airbyte API"
        )
        return 2
    sf = pg = None
    evidence = {"status": "FAIL", "synthetic_test_rows": True}
    result = 2
    try:
        import psycopg

        pg = psycopg.connect(
            os.environ["SPONSORATLAS_CDC_TEST_DSN"],
            autocommit=True,
            connect_timeout=30,
            options="-c statement_timeout=30000 -c lock_timeout=30000",
        )
        sf = snowflake_connection()
        deadline = Deadline()
        jobs = AirbyteJobs(deadline)
        identifier = "sponsoratlas_probe_" + uuid.uuid4().hex
        evidence["probe_id"] = identifier

        evidence.update(
            probe(
                lambda phase: postgres_mutate(pg, identifier, phase, deadline),
                lambda: snowflake_fetch(sf, identifier, deadline),
                jobs.trigger,
                job_done=jobs.done,
                deadline=deadline,
            )
        )
        result = 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary returns documented error exit code
        evidence["error_type"] = type(exc).__name__
        # Do not log DSNs, tokens or driver exception messages.
        print(f"CDC acceptance failed: {type(exc).__name__}", file=sys.stderr)
    finally:
        if pg is not None:
            pg.close()
        if sf is not None:
            sf.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
