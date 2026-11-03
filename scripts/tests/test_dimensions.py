"""Task5 reference, identity-bound loader and real snapshot acceptance."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_official_reference_replay_and_hash_rejection(tmp_path, monkeypatch):
    from er.io import digest
    from scripts.build_dimension_references import build

    # A fresh working directory has no .tools cache; use only committed original bytes.
    monkeypatch.chdir(tmp_path)
    sources = ROOT / "data/reference"
    result = build(sources, tmp_path / "archive", tmp_path / "seeds")
    assert result["counts"] == {"soc": 1447, "county_2023": 3222, "county_cbsa_2023": 1915}
    expected_originals = {
        "soc_structure_2018.xlsx",
        "list1_2023.xlsx",
        "2023_Gaz_counties_national.zip",
    }
    assert {s["official_filename"] for s in result["sources"]} == expected_originals
    for source in result["sources"]:
        basename = source["archived_filename"]
        assert source["vintage"] in basename and source["sha256"] in basename
        assert "FY" not in basename  # These reference vintages are not fiscal years.
        assert Path(basename).name == basename
        archived = tmp_path / "archive" / source["vintage"] / source["sha256"] / basename
        assert digest(archived) == source["sha256"]
        assert archived.stat().st_size == source["bytes"]
    again = build(tmp_path / "archive", tmp_path / "archive", tmp_path / "seeds")
    assert result == again
    soc = next(s for s in result["sources"] if s["official_filename"] == "soc_structure_2018.xlsx")
    original = tmp_path / "archive" / soc["vintage"] / soc["sha256"] / soc["archived_filename"]
    original.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="archive digest"):
        build(sources, tmp_path / "archive", tmp_path / "seeds")
    with pytest.raises(ValueError, match="source digest"):
        build(tmp_path / "archive", tmp_path / "another-archive", tmp_path / "another-seeds")


def test_loader_rejects_mixed_batch_before_output(tmp_path):
    from scripts.canonical_paths import ER_PREDICTION
    from scripts.load_dimension_inputs import load

    pred = tmp_path / "prediction"
    pred.mkdir()
    meta = json.loads((ER_PREDICTION / "prediction.json").read_text())
    meta["input_records_sha256"] = "0" * 64
    (pred / "prediction.json").write_text(json.dumps(meta))
    output = tmp_path / "bad.duckdb"
    with pytest.raises(ValueError, match="lineage"):
        load(output, prediction_dir=pred)
    assert not output.exists()


def test_snapshot_invariant_sql_has_pairwise_overlap_and_invalid_ranges():
    # A query actually executed on malformed intervals, independent of dbt rendering.
    from scripts.local_duckdb import connect_local

    sql = (
        (ROOT / "dbt/tests/scd2_no_overlap.sql")
        .read_text()
        .replace("{{ ref('dim_employer') }}", "dim_employer")
    )
    with connect_local(":memory:") as c:
        c.execute(
            "create table dim_employer(employer_id varchar, employer_key varchar, valid_from timestamp, valid_to timestamp, is_current boolean)"
        )
        c.execute(
            "insert into dim_employer values ('e','a','2024-01-01','2024-05-01',false),('e','b','2024-02-01','2024-03-01',false),('e','c','2024-04-01',NULL,true)"
        )
        assert c.execute(
            sql
        ).fetchall()  # Long first interval overlaps nonadjacent current interval.
        for rows in [
            "('e','a','2024-01-01',NULL,true),('e','b','2024-02-01',NULL,true)",
            "('e','a','2024-01-01','2024-01-01',false),('e','b','2024-01-01',NULL,true)",
            "('e','a','2024-02-01','2024-01-01',false),('e','b','2024-03-01',NULL,true)",
            "('e','a','2024-01-01','2024-02-01',false)",
            "('e','a','2024-01-01',NULL,false)",
        ]:
            c.execute("delete from dim_employer")
            c.execute("insert into dim_employer values " + rows)
            assert c.execute(sql).fetchall()
        c.execute("delete from dim_employer")
        c.execute(
            "insert into dim_employer values ('e','a','2024-01-01','2024-02-01',false),('e','b','2024-02-01',NULL,true)"
        )
        assert not c.execute(sql).fetchall()


def _batch(root, previous=None):
    """Tiny transparent fixture, with the same public registry/lineage contracts."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from er.io import digest
    from er.registry import registry_metadata, resolve_registry, save_registry
    from scripts.local_duckdb import connect_local

    root.mkdir()
    inp, model, pred = [root / x for x in ["input", "model", "prediction"]]
    for p in [inp, model, pred]:
        p.mkdir()
    rec = {
        "unique_id": "b",
        "source_system": "lca",
        "source_employer_key": "k",
        "source_name": "Fixture employer",
        "state": "CA",
        "country": "US",
        "city": "Example",
        "postcode": "90001",
        "eligible": True,
    }
    occ = {k: rec[k] for k in ["unique_id", "source_system", "source_employer_key", "source_name"]}
    occ.update(source_release="FY2025_Q4", source_sha256="a" * 64, source_row_number=1)
    pq.write_table(pa.Table.from_pylist([rec]), inp / "records.parquet")
    pq.write_table(pa.Table.from_pylist([occ]), inp / "occurrences.parquet")
    metadata = {
        "kind": "SYNTHETIC_LOADER_FIXTURE",
        "rows": 1,
        "source_records": 1,
        "records_sha256": digest(inp / "records.parquet"),
        "occurrences_sha256": digest(inp / "occurrences.parquet"),
        "originals": [
            {
                "source_system": "lca",
                "source_release": "FY2025_Q4",
                "source_sha256": "a" * 64,
                "rows": 1,
            }
        ],
    }
    (inp / "input.json").write_text(json.dumps(metadata))
    canonical, registry = resolve_registry(
        [rec], {"b": "b"}, metadata, initialize=previous is None, previous=previous
    )
    save_registry(pred / "registry.json", registry)
    xref = {
        **rec,
        "employer_id": canonical["b"],
        "match_probability": 1.0,
        "issues": "",
        "resolution_status": "SINGLETON",
        "match_method": "SINGLETON",
    }
    xref.pop("eligible")
    pq.write_table(pa.Table.from_pylist([xref]), pred / "int_employer_xref.parquet")
    (model / "model.json").write_text("{}")
    common = {
        "input_records_sha256": metadata["records_sha256"],
        "input_occurrences_sha256": metadata["occurrences_sha256"],
        "model_sha256": digest(model / "model.json"),
        "input_kind": metadata["kind"],
    }
    (model / "training.json").write_text(json.dumps(common))
    (pred / "prediction.json").write_text(
        json.dumps(
            {
                **common,
                "xref_sha256": digest(pred / "int_employer_xref.parquet"),
                "acceptance_status": "SYNTHETIC_ONLY",
                **registry_metadata(pred / "registry.json", previous),
            }
        )
    )
    raw = root / "raw.duckdb"
    with connect_local(raw) as c:
        c.execute("create schema STAGING")
        c.execute(
            "create table STAGING.stg_lca as select 'lca' as source_system, 'k' as source_employer_key,'FY2025_Q4' as source_release,? as source_sha256,1::bigint as source_row_number,2025 as fiscal_year,date '2025-09-01' as decision_date,'15-1252' as soc_code_raw,'15-1252' as soc_code,'CA' as worksite_state,'LOS ANGELES' as worksite_county,'06' as state_fips,'06037' as county_fips,'Census2020' as geography_vintage,'MATCHED' as geography_status",
            ["a" * 64],
        )
        c.execute(
            "create table STAGING.stg_uscis as select *, true as released from STAGING.stg_lca limit 0"
        )
    return {"input_dir": inp, "model_dir": model, "prediction_dir": pred, "source_database": raw}


def test_loader_valid_same_batch_child_and_drift_rejection(tmp_path):
    from scripts.load_dimension_inputs import load
    from scripts.local_duckdb import connect_local

    first = _batch(tmp_path / "first")
    db = tmp_path / "dimensions.duckdb"
    load(db, **first)
    load(db, **first)  # Idempotent reload is allowed.
    with connect_local(db) as c:
        c.execute("create schema SNAPSHOTS")
        c.execute("create table SNAPSHOTS.keep_me as select 1 as x")
    second = _batch(tmp_path / "second", first["prediction_dir"] / "registry.json")
    load(db, **second)
    for bad in [first, _batch(tmp_path / "reinitialized")]:
        with pytest.raises(ValueError, match="continuity"):
            load(db, **bad)
    with connect_local(db) as c:
        assert (
            c.execute("select count(*) from DIMENSIONS_INPUT.employer_sources").fetchone()[0] == 1
        )
        assert c.execute("select x from SNAPSHOTS.keep_me").fetchone() == (1,)


@pytest.mark.parametrize(
    "problem",
    [
        "duplicate_key",
        "missing_key",
        "missing_xref",
        "registry_disagreement",
        "occurrence_key",
        "stage_key",
    ],
)
def test_loader_rejects_invalid_source_contracts_before_creation(tmp_path, problem):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from er.io import digest
    from scripts.load_dimension_inputs import load
    from scripts.local_duckdb import connect_local

    batch = _batch(tmp_path / "batch")
    pred, inp = batch["prediction_dir"], batch["input_dir"]
    meta = json.loads((pred / "prediction.json").read_text())
    if problem in ["duplicate_key", "missing_key", "missing_xref", "registry_disagreement"]:
        path = pred / "int_employer_xref.parquet"
        table = pq.read_table(path)
        rows = table.to_pylist()
        if problem == "duplicate_key":
            rows += [{**rows[0], "unique_id": "z"}]
        elif problem == "missing_key":
            rows[0]["source_employer_key"] = None
        elif problem == "registry_disagreement":
            rows[0]["employer_id"] = "emp_wrong"
        else:
            rows = []
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
        meta["xref_sha256"] = digest(path)
        (pred / "prediction.json").write_text(json.dumps(meta))
    elif problem == "occurrence_key":
        path = inp / "occurrences.parquet"
        rows = pq.read_table(path).to_pylist()
        rows[0]["source_employer_key"] = "wrong"
        pq.write_table(pa.Table.from_pylist(rows), path)
        # Keep manifests stale: fail the cryptographic lineage check before SQL.
    else:
        with connect_local(batch["source_database"]) as c:
            c.execute("update STAGING.stg_lca set source_employer_key='wrong'")
    output = tmp_path / "bad.duckdb"
    with pytest.raises(ValueError):
        load(output, **batch)
    assert not output.exists()


def test_loader_rejects_untracked_database_without_modifying_it(tmp_path):
    from scripts.load_dimension_inputs import load
    from scripts.local_duckdb import connect_local

    batch = _batch(tmp_path / "batch")
    db = tmp_path / "untracked.duckdb"
    with connect_local(db) as c:
        c.execute("create table unrelated as select 42 as value")
    with pytest.raises(ValueError, match="lineage"):
        load(db, **batch)
    with connect_local(db) as c:
        assert c.execute("select value from unrelated").fetchone() == (42,)
        assert c.execute(
            "select count(*) from information_schema.schemata where schema_name='DIMENSIONS_INPUT'"
        ).fetchone() == (0,)


def test_loader_rejects_model_batch_mismatch_and_protected_paths(tmp_path):
    from scripts.load_dimension_inputs import load

    batch = _batch(tmp_path / "batch")
    with pytest.raises(ValueError, match="refusing"):
        load(batch["source_database"], **batch)
    meta_path = batch["model_dir"] / "training.json"
    meta = json.loads(meta_path.read_text())
    meta["input_kind"] = "REAL_WHEN_IT_IS_NOT"
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="kind"):
        load(tmp_path / "bad.duckdb", **batch)


def test_loader_retains_retracted_identity_as_inactive(tmp_path):
    import pyarrow.parquet as pq

    from er.io import digest
    from er.registry import registry_metadata, resolve_registry, save_registry
    from scripts.load_dimension_inputs import load
    from scripts.local_duckdb import connect_local

    first = _batch(tmp_path / "first")
    db = tmp_path / "dimensions.duckdb"
    load(db, **first)
    child = _batch(tmp_path / "child", first["prediction_dir"] / "registry.json")
    inp, pred, model = child["input_dir"], child["prediction_dir"], child["model_dir"]
    for path in [
        inp / "records.parquet",
        inp / "occurrences.parquet",
        pred / "int_employer_xref.parquet",
    ]:
        table = pq.read_table(path)
        pq.write_table(table.slice(0, 0), path)
    metadata = json.loads((inp / "input.json").read_text())
    metadata.update(
        rows=0,
        source_records=0,
        originals=[],
        records_sha256=digest(inp / "records.parquet"),
        occurrences_sha256=digest(inp / "occurrences.parquet"),
    )
    (inp / "input.json").write_text(json.dumps(metadata))
    _, registry = resolve_registry(
        [], {}, metadata, previous=first["prediction_dir"] / "registry.json"
    )
    (pred / "registry.json").unlink()
    save_registry(pred / "registry.json", registry)
    for path in [model / "training.json", pred / "prediction.json"]:
        meta = json.loads(path.read_text())
        meta.update(
            input_records_sha256=metadata["records_sha256"],
            input_occurrences_sha256=metadata["occurrences_sha256"],
        )
        if path.parent == pred:
            meta.update(
                xref_sha256=digest(pred / "int_employer_xref.parquet"),
                **registry_metadata(
                    pred / "registry.json", first["prediction_dir"] / "registry.json"
                ),
            )
        path.write_text(json.dumps(meta))
    with connect_local(child["source_database"]) as c:
        c.execute("delete from STAGING.stg_lca")
    load(db, **child)
    load(db, **child)
    with connect_local(db) as c:
        assert c.execute(
            "select employer_id,is_source_active from DIMENSIONS_INPUT.employer_sources"
        ).fetchall() == [("emp_b", False)]
        assert c.execute("select count(*) from DIMENSIONS_INPUT.lca_attributes").fetchone() == (0,)


def test_loader_rejects_record_without_occurrence(tmp_path):
    import pyarrow.parquet as pq

    from er.io import digest
    from er.registry import registry_metadata, resolve_registry, save_registry
    from scripts.load_dimension_inputs import load
    from scripts.local_duckdb import connect_local

    batch = _batch(tmp_path / "batch")
    inp, pred, model = batch["input_dir"], batch["prediction_dir"], batch["model_dir"]
    occurrence_path = inp / "occurrences.parquet"
    pq.write_table(pq.read_table(occurrence_path).slice(0, 0), occurrence_path)
    metadata = json.loads((inp / "input.json").read_text())
    metadata.update(rows=0, originals=[], occurrences_sha256=digest(occurrence_path))
    (inp / "input.json").write_text(json.dumps(metadata))
    records = pq.read_table(inp / "records.parquet").to_pylist()
    _, registry = resolve_registry(records, {"b": "b"}, metadata, initialize=True)
    (pred / "registry.json").unlink()
    save_registry(pred / "registry.json", registry)
    for path in [model / "training.json", pred / "prediction.json"]:
        meta = json.loads(path.read_text())
        meta["input_occurrences_sha256"] = metadata["occurrences_sha256"]
        if path.parent == pred:
            meta.update(**registry_metadata(pred / "registry.json"))
        path.write_text(json.dumps(meta))
    with connect_local(batch["source_database"]) as c:
        c.execute("delete from STAGING.stg_lca")
    with pytest.raises(ValueError, match="missing occurrence"):
        load(tmp_path / "bad.duckdb", **batch)
