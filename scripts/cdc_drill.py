"""Local CDC drill: Postgres (wal_level=logical, pgoutput slot) -> DuckDB RAW_JOBS.

A replicator thread peeks the slot every --interval seconds, applies the changes, and
only then advances the slot (at-least-once; the upsert is idempotent). The drill seeds
synthetic rows, makes one insert, one update and one delete per table, and records how
long each change took from Postgres commit to RAW. Then it reconciles source and RAW
counts. All data is synthetic.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from scripts.cdc_pgoutput import (
    TABLES,
    Decoder,
    apply_changes,
    create_raw_tables,
    lsn_int,
    raw_current_counts,
)
from scripts.local_duckdb import connect_local
from scripts.reconcile_counts import reconcile_cdc

SLOT = "sponsoratlas_airbyte"
PUBLICATION = "sponsoratlas_publication"
TARGET_SECONDS = 15 * 60
OWNER = "SPONSORATLAS_OWNER_DRILL"


class Replicator(threading.Thread):
    def __init__(self, dsn, raw, interval):
        super().__init__(daemon=True)
        self.dsn, self.raw, self.interval = dsn, raw, interval
        self.stop = threading.Event()
        self.cycles = []
        self.error = None

    def sync_once(self, pg, duck, decoder):
        started = time.monotonic()
        rows = pg.execute(
            "SELECT lsn::text, data FROM pg_logical_slot_peek_binary_changes"
            "(%s, NULL, NULL, 'proto_version', '1', 'publication_names', %s)",
            (SLOT, PUBLICATION),
        ).fetchall()
        applied = 0
        if rows:
            changes = list(decoder.feed_all(rows))
            duck.execute("BEGIN")
            applied = apply_changes(duck, changes, datetime.now(UTC))
            duck.execute("COMMIT")
            last = max(rows, key=lambda r: lsn_int(r[0]))[0]
            pg.execute("SELECT pg_replication_slot_advance(%s, %s::pg_lsn)", (SLOT, last))
        self.cycles.append(
            {
                "at": datetime.now(UTC).isoformat(),
                "messages": len(rows),
                "applied": applied,
                "seconds": round(time.monotonic() - started, 3),
            }
        )

    def run(self):
        try:
            with psycopg.connect(self.dsn, autocommit=True) as pg, connect_local(self.raw) as duck:
                decoder = Decoder()
                while not self.stop.is_set():
                    self.sync_once(pg, duck, decoder)
                    self.stop.wait(self.interval)
        except Exception as exc:  # noqa: BLE001 - surfaced by the drill
            self.error = repr(exc)


def raw_row(raw, table, key):
    with connect_local(raw) as duck:
        return duck.execute(
            f"SELECT _AB_CDC_UPDATED_AT, _AB_CDC_DELETED_AT, _AIRBYTE_EXTRACTED_AT, _AB_CDC_LSN"
            f" FROM RAW_JOBS.{TABLES[table][0]} WHERE id = ?",
            [key],
        ).fetchone()


def seed(pg, postings, events):
    with pg.transaction():
        for i in range(postings):
            pg.execute(
                "INSERT INTO job_postings (id, title, company_name) VALUES (%s, %s, %s)",
                (f"seed-{i:04d}", f"SYNTHETIC role {i}", f"SYNTHETIC Employer {i % 17}"),
            )
        for i in range(events):
            pg.execute(
                "INSERT INTO application_events (id, application_id, owner_role, job_id,"
                " event_type) VALUES (%s, %s, %s, %s, %s)",
                (f"seed-ev-{i:04d}", f"app-{i:04d}", OWNER, f"seed-{i:04d}", "applied"),
            )


def mutations(pg):
    """One insert, update and delete per table, each in its own transaction."""
    steps = [
        ("job_postings", "insert", "drill-new",
         "INSERT INTO job_postings (id, title, company_name)"
         " VALUES ('drill-new', 'SYNTHETIC drill role', 'SYNTHETIC Employer 0')"),
        ("job_postings", "update", "seed-0001",
         "UPDATE job_postings SET title = 'SYNTHETIC renamed', updated_at = now()"
         " WHERE id = 'seed-0001'"),
        ("job_postings", "delete", "seed-0002", "DELETE FROM job_postings WHERE id = 'seed-0002'"),
        ("application_events", "insert", "drill-ev",
         "INSERT INTO application_events (id, application_id, owner_role, job_id, event_type)"
         f" VALUES ('drill-ev', 'app-drill', '{OWNER}', 'drill-new', 'applied')"),
        ("application_events", "update", "seed-ev-0001",
         "UPDATE application_events SET event_type = 'interview' WHERE id = 'seed-ev-0001'"),
        ("application_events", "delete", "seed-ev-0002",
         "DELETE FROM application_events WHERE id = 'seed-ev-0002'"),
    ]
    done = []
    for table, op, key, sql in steps:
        with pg.transaction():
            pg.execute(sql)
        done.append({"table": table, "op": op, "id": key, "committed_at": datetime.now(UTC)})
    return done


def visible(raw, step):
    row = raw_row(raw, step["table"], step["id"])
    if row is None:
        return None
    cdc_at, deleted_at, extracted_at, lsn = row
    if cdc_at is None or cdc_at < step["committed_at"].replace(microsecond=0):
        return None
    if step["op"] == "delete" and deleted_at is None:
        return None
    return {"cdc_commit_at": cdc_at, "extracted_at": extracted_at, "lsn": lsn}


def drill(dsn, raw, interval, postings, events, timeout):
    raw = Path(raw)
    if raw.exists():
        raise SystemExit(f"{raw} exists; the drill writes a fresh RAW database")
    raw.parent.mkdir(parents=True, exist_ok=True)
    with connect_local(raw) as duck:
        create_raw_tables(duck)
    started = datetime.now(UTC)
    with psycopg.connect(dsn, autocommit=True) as pg:
        server = pg.execute(
            "SELECT version(), current_setting('wal_level'), plugin, slot_type"
            " FROM pg_replication_slots WHERE slot_name = %s",
            (SLOT,),
        ).fetchone()
        seed(pg, postings, events)
        replicator = Replicator(dsn, raw, interval)
        replicator.start()
        deadline = time.monotonic() + timeout
        while raw_current_counts_safe(raw) != {"job_postings": postings, "application_events": events}:
            if replicator.error or time.monotonic() > deadline:
                raise SystemExit(f"initial sync failed: {replicator.error}")
            time.sleep(1)
        steps = mutations(pg)
        results = []
        for step in steps:
            seen = None
            while seen is None:
                if replicator.error or time.monotonic() > deadline:
                    break
                seen = visible(raw, step)
                if seen is None:
                    time.sleep(0.5)
            latency = (
                (seen["extracted_at"] - seen["cdc_commit_at"]).total_seconds() if seen else None
            )
            results.append(
                {
                    "table": step["table"],
                    "op": step["op"],
                    "id": step["id"],
                    "committed_at": step["committed_at"].isoformat(),
                    "cdc_commit_at": seen["cdc_commit_at"].isoformat() if seen else None,
                    "raw_extracted_at": seen["extracted_at"].isoformat() if seen else None,
                    "lsn": seen["lsn"] if seen else None,
                    "commit_to_raw_seconds": latency,
                    "within_15_min": latency is not None and latency <= TARGET_SECONDS,
                }
            )
        time.sleep(interval + 1)  # one more cycle so the slot is fully drained
        replicator.stop.set()
        replicator.join(timeout=interval + 30)
        source_counts = {
            t: pg.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES
        }
        slot = pg.execute(
            "SELECT confirmed_flush_lsn::text,"
            " pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint"
            " FROM pg_replication_slots WHERE slot_name = %s",
            (SLOT,),
        ).fetchone()
    with connect_local(raw) as duck:
        raw_counts = raw_current_counts(duck)
        tombstones = {
            t: duck.execute(
                f"SELECT count(*) FROM RAW_JOBS.{r} WHERE _AB_CDC_DELETED_AT IS NOT NULL"
            ).fetchone()[0]
            for t, (r, _) in TABLES.items()
        }
    reconciliation = reconcile_cdc(source_counts, raw_counts)
    return {
        "dataset": "SYNTHETIC_CDC_DRILL",
        "started_at": started.isoformat(),
        "postgres": {"version": server[0], "wal_level": server[1], "plugin": server[2],
                     "slot_type": server[3]},
        "poll_interval_seconds": interval,
        "seed_rows": {"job_postings": postings, "application_events": events},
        "changes": results,
        "max_commit_to_raw_seconds": max(
            (r["commit_to_raw_seconds"] for r in results if r["commit_to_raw_seconds"] is not None),
            default=None,
        ),
        "all_within_15_min": all(r["within_15_min"] for r in results),
        "tombstones": tombstones,
        "reconciliation": reconciliation,
        "reconciled": all(r["status"] == "MATCH" for r in reconciliation),
        "slot_after": {"confirmed_flush_lsn": slot[0], "retained_wal_bytes": slot[1]},
        "replicator_cycles": len(replicator.cycles),
        "replicator_error": replicator.error,
    }


def raw_current_counts_safe(raw):
    with connect_local(raw) as duck:
        return raw_current_counts(duck)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dsn", required=True)
    p.add_argument("--raw", required=True)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--interval", type=float, default=30)
    p.add_argument("--postings", type=int, default=200)
    p.add_argument("--events", type=int, default=80)
    p.add_argument("--timeout", type=float, default=TARGET_SECONDS)
    a = p.parse_args()
    report = drill(a.dsn, a.raw, a.interval, a.postings, a.events, a.timeout)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({k: report[k] for k in
                      ["max_commit_to_raw_seconds", "all_within_15_min", "reconciled"]}))
    raise SystemExit(0 if report["all_within_15_min"] and report["reconciled"] else 1)


if __name__ == "__main__":
    main()
