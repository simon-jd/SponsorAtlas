"""Decode PostgreSQL pgoutput (protocol v1) and apply it to local RAW_JOBS tables.

The RAW tables follow the Airbyte deduped-CDC shape the staging models read: one row per
primary key, `_AB_CDC_*` metadata, and deletes kept as tombstones (`_AB_CDC_DELETED_AT`).
"""

from __future__ import annotations

import struct
import uuid
from datetime import UTC, datetime, timedelta

PG_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
META = [
    ("_AB_CDC_LSN", "VARCHAR"),
    ("_AB_CDC_UPDATED_AT", "TIMESTAMPTZ"),
    ("_AB_CDC_DELETED_AT", "TIMESTAMPTZ"),
    ("_AB_CDC_CURSOR", "BIGINT"),
    ("_AIRBYTE_RAW_ID", "VARCHAR"),
    ("_AIRBYTE_EXTRACTED_AT", "TIMESTAMPTZ"),
    ("_AIRBYTE_META", "JSON"),
    ("_AIRBYTE_GENERATION_ID", "BIGINT"),
]
# Source tables of ingestion/postgres/demo_schema.sql -> RAW_JOBS table and column types.
TABLES = {
    "job_postings": (
        "CDC_JOB_POSTINGS",
        [
            ("id", "VARCHAR"),
            ("title", "VARCHAR"),
            ("company_name", "VARCHAR"),
            ("updated_at", "TIMESTAMPTZ"),
        ],
    ),
    "application_events": (
        "CDC_APPLICATION_EVENTS",
        [
            ("id", "VARCHAR"),
            ("application_id", "VARCHAR"),
            ("owner_role", "VARCHAR"),
            ("contact_email", "VARCHAR"),
            ("contract_version", "INTEGER"),
            ("job_id", "VARCHAR"),
            ("event_type", "VARCHAR"),
            ("event_at", "TIMESTAMPTZ"),
        ],
    ),
}


def lsn_text(lsn):
    if isinstance(lsn, str):
        return lsn
    return f"{lsn >> 32:X}/{lsn & 0xFFFFFFFF:X}"


def lsn_int(lsn):
    if isinstance(lsn, int):
        return lsn
    high, low = lsn.split("/")
    return (int(high, 16) << 32) | int(low, 16)


class _Reader:
    def __init__(self, data):
        self.data, self.pos = bytes(data), 0

    def take(self, fmt):
        (value,) = struct.unpack_from(">" + fmt, self.data, self.pos)
        self.pos += struct.calcsize(fmt)
        return value

    def byte(self):
        value = self.data[self.pos : self.pos + 1]
        self.pos += 1
        return value

    def cstr(self):
        end = self.data.index(b"\0", self.pos)
        value = self.data[self.pos : end].decode()
        self.pos = end + 1
        return value

    def tuple(self, columns):
        values = {}
        for name in columns[: self.take("h")]:
            kind = self.byte()
            if kind == b"n":
                values[name] = None
            elif kind == b"t":
                size = self.take("i")
                values[name] = self.data[self.pos : self.pos + size].decode()
                self.pos += size
            else:  # b"u": unchanged TOAST value; not produced by these narrow tables
                raise ValueError("unchanged TOAST column in pgoutput tuple")
        return values


class Decoder:
    """Stateful: Relation messages define column names for later row messages."""

    def __init__(self):
        self.relations = {}
        self.commit_time = None

    def feed(self, lsn, data):
        r = _Reader(data)
        kind = r.byte()
        if kind == b"B":
            r.take("q")
            self.commit_time = PG_EPOCH + timedelta(microseconds=r.take("q"))
        elif kind == b"R":
            oid = r.take("i")
            r.cstr()  # namespace
            name = r.cstr()
            r.byte()  # replica identity
            columns = []
            for _ in range(r.take("h")):
                r.byte()
                columns.append(r.cstr())
                r.take("i")
                r.take("i")
            self.relations[oid] = (name, columns)
        elif kind in (b"I", b"U", b"D"):
            name, columns = self.relations[r.take("i")]
            marker = r.byte()
            row = r.tuple(columns)
            if kind == b"U" and marker in (b"K", b"O"):
                r.byte()  # b"N"
                row = r.tuple(columns)
            op = {b"I": "insert", b"U": "update", b"D": "delete"}[kind]
            return {
                "op": op,
                "table": name,
                "row": row,
                "lsn": lsn_text(lsn),
                "commit_time": self.commit_time,
            }
        return None

    def feed_all(self, messages):
        for lsn, data in messages:
            change = self.feed(lsn, data)
            if change:
                yield change


def create_raw_tables(conn):
    conn.execute("CREATE SCHEMA IF NOT EXISTS RAW_JOBS")
    for raw, columns in TABLES.values():
        body = ", ".join(f"{n} {t}" for n, t in columns + META)
        conn.execute(f"CREATE TABLE IF NOT EXISTS RAW_JOBS.{raw} ({body}, PRIMARY KEY (id))")


def apply_changes(conn, changes, extracted_at=None):
    """Upsert each change; deletes keep the old row with _AB_CDC_DELETED_AT set."""
    applied = 0
    for change in changes:
        raw, columns = TABLES[change["table"]]
        when = change["commit_time"]
        values = [change["row"].get(n) for n, _ in columns] + [
            change["lsn"],
            when,
            when if change["op"] == "delete" else None,
            lsn_int(change["lsn"]),
            str(uuid.uuid4()),
            extracted_at or datetime.now(UTC),
            "{}",
            0,
        ]
        names = [n for n, _ in columns + META]
        conn.execute(
            f"INSERT OR REPLACE INTO RAW_JOBS.{raw} ({', '.join(names)}) "
            f"VALUES ({', '.join('?' for _ in names)})",
            values,
        )
        applied += 1
    return applied


def raw_current_counts(conn):
    return {
        source: conn.execute(
            f"SELECT count(*) FROM RAW_JOBS.{raw} WHERE _AB_CDC_DELETED_AT IS NULL"
        ).fetchone()[0]
        for source, (raw, _) in TABLES.items()
    }
