"""pgoutput (protocol v1) decoding and Airbyte-style RAW application, without a server."""

import struct
from datetime import UTC, datetime

from scripts.cdc_pgoutput import Decoder, apply_changes, create_raw_tables, raw_current_counts
from scripts.local_duckdb import connect_local

PG_EPOCH_US = 946684800 * 1_000_000


def cstr(s):
    return s.encode() + b"\0"


def tuple_data(values):
    out = struct.pack(">h", len(values))
    for v in values:
        if v is None:
            out += b"n"
        else:
            raw = v.encode()
            out += b"t" + struct.pack(">i", len(raw)) + raw
    return out


def relation(oid, name, columns):
    out = b"R" + struct.pack(">i", oid) + cstr("public") + cstr(name) + b"f"
    out += struct.pack(">h", len(columns))
    for col in columns:
        out += b"\1" + cstr(col) + struct.pack(">ii", 25, -1)
    return out


def begin(lsn, ts):
    micros = int(ts.timestamp() * 1_000_000) - PG_EPOCH_US
    return b"B" + struct.pack(">qqi", lsn, micros, 7)


def commit(lsn, ts):
    micros = int(ts.timestamp() * 1_000_000) - PG_EPOCH_US
    return b"C" + b"\0" + struct.pack(">qqq", lsn, lsn + 8, micros)


COLS = ["id", "title", "company_name", "updated_at"]
TS = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)
T0, T1 = "2026-10-27 11:00:00+00", "2026-10-27 11:30:00+00"


def messages():
    return [
        (0x10, begin(0x20, TS)),
        (0x10, relation(16384, "job_postings", COLS)),
        (0x11, b"I" + struct.pack(">i", 16384) + b"N" + tuple_data(["j1", "Eng", "Acme", T0])),
        (0x12, b"I" + struct.pack(">i", 16384) + b"N" + tuple_data(["j2", "Ops", "Acme", T0])),
        (0x20, commit(0x20, TS)),
        (0x30, begin(0x40, TS)),
        (
            0x31,
            b"U"
            + struct.pack(">i", 16384)
            + b"O"
            + tuple_data(["j1", "Eng", "Acme", T0])
            + b"N"
            + tuple_data(["j1", "Senior Eng", "Acme", T1]),
        ),
        (0x32, b"D" + struct.pack(">i", 16384) + b"O" + tuple_data(["j2", "Ops", "Acme", T0])),
        (0x40, commit(0x40, TS)),
    ]


def test_decoder_yields_row_changes_with_commit_time():
    changes = list(Decoder().feed_all(messages()))
    assert [(c["op"], c["table"], c["row"]["id"]) for c in changes] == [
        ("insert", "job_postings", "j1"),
        ("insert", "job_postings", "j2"),
        ("update", "job_postings", "j1"),
        ("delete", "job_postings", "j2"),
    ]
    assert changes[2]["row"]["title"] == "Senior Eng"
    assert changes[3]["row"]["title"] == "Ops"  # REPLICA IDENTITY FULL old tuple
    assert all(c["commit_time"] == TS for c in changes)
    assert [c["lsn"] for c in changes] == ["0/11", "0/12", "0/31", "0/32"]


def test_null_values_decode_as_none():
    msgs = [
        (1, begin(2, TS)),
        (1, relation(5, "job_postings", COLS)),
        (1, b"I" + struct.pack(">i", 5) + b"N" + tuple_data(["j3", "x", "y", None])),
        (2, commit(2, TS)),
    ]
    (change,) = Decoder().feed_all(msgs)
    assert change["row"]["updated_at"] is None


def test_apply_upserts_and_keeps_tombstones(tmp_path):
    with connect_local(tmp_path / "raw.duckdb") as conn:
        create_raw_tables(conn)
        extracted = datetime(2026, 10, 27, 12, 0, 5, tzinfo=UTC)
        apply_changes(conn, Decoder().feed_all(messages()), extracted)
        rows = conn.execute(
            "select id, title, _ab_cdc_lsn, _ab_cdc_deleted_at is not null"
            " from RAW_JOBS.CDC_JOB_POSTINGS order by id"
        ).fetchall()
        assert rows == [("j1", "Senior Eng", "0/31", False), ("j2", "Ops", "0/32", True)]
        assert raw_current_counts(conn) == {"job_postings": 1, "application_events": 0}
