"""Real Splink execution against an explicitly synthetic small fixture."""

import shutil
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from er.io import TOOLS, connect, digest, dump
from er.predict import predict
from er.tests.fixtures.synthetic import records
from er.train import candidate_upper_bound, load_input, train


@pytest.fixture
def sandbox():
    root = TOOLS / "er-tests"
    root.mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(dir=root))
    yield path
    shutil.rmtree(path)


def test_actual_splink_u_em_predict_and_lineage(sandbox):
    inp = sandbox / "input"
    inp.mkdir()
    rows = records()
    pq.write_table(pa.Table.from_pylist(rows), inp / "records.parquet")
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"unique_id": r["unique_id"], "source_sha256": "SYNTHETIC", "source_row_number": i}
                for i, r in enumerate(rows)
            ]
        ),
        inp / "occurrences.parquet",
    )
    dump(
        inp / "input.json",
        {
            "kind": "SYNTHETIC_TEST_ONLY",
            "records_sha256": digest(inp / "records.parquet"),
            "occurrences_sha256": digest(inp / "occurrences.parquet"),
        },
    )
    model = train(inp, sandbox / "model")
    assert model["records"] == 96 and len(model["em_sessions"]) == 2
    assert model["seed"] == 42 and not model["acceptance_eligible"]
    actual = predict(inp, sandbox / "model", sandbox / "prediction", initialize_registry=True)
    assert actual["candidates"] > 0 and actual["source_records"] == 96
    c = connect(sandbox / "prediction" / "analytics.duckdb")
    assert (
        c.execute("select count(*) from analytics.intermediate.int_employer_xref").fetchall()[0][0]
        == 96
    )
    assert (
        c.execute("select count(*) from intermediate.employer_source_occurrences").fetchall()[0][0]
        == 96
    )
    c.close()
    assert actual["limits"]["threads"] == "1"
    assert actual["input_occurrences_sha256"] == model["input_occurrences_sha256"]
    repeated = predict(
        inp,
        sandbox / "model",
        sandbox / "prediction2",
        previous_registry=sandbox / "prediction" / "registry.json",
    )
    assert repeated["registry_mode"] == "INHERIT"
    first = pq.read_table(sandbox / "prediction" / "int_employer_xref.parquet").to_pylist()
    second = pq.read_table(sandbox / "prediction2" / "int_employer_xref.parquet").to_pylist()
    assert first == second


@pytest.mark.parametrize("count", [0, 1])
def test_empty_or_single_input_rejected(sandbox, count):
    rows = records()[:count]
    table = pa.Table.from_pylist(rows) if rows else pa.Table.from_pylist(records()).slice(0, 0)
    pq.write_table(table, sandbox / "records.parquet")
    c = connect(sandbox / "test.duckdb")
    with pytest.raises(ValueError, match="at least two"):
        load_input(c, sandbox / "records.parquet")
    c.close()


def test_no_blocking_or_excessive_candidates_fail(sandbox):
    c = connect(sandbox / "test.duckdb")
    c.execute(
        "create table input_records as select 'one' as first_token, 'CA' as state, NULL::VARCHAR as strong_id,"
        " 'one' as name_norm, NULL::VARCHAR as tax_last4"
    )
    with pytest.raises(ValueError, match="no valid blocking"):
        candidate_upper_bound(c)
    c.execute("insert into input_records select 'one','CA',NULL,'one',NULL from range(4000)")
    with pytest.raises(ValueError, match="exceeds"):
        candidate_upper_bound(c)
    c.close()


def test_fixture_cannot_be_exported_as_real_originals():
    from er.export import verify_originals

    with pytest.raises(ValueError, match="not fixtures"):
        verify_originals(
            [{"source_system": "lca", "source_release": "SYNTHETIC", "source_sha256": "fixture"}]
        )
