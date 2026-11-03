"""Load minimal reviewed staging/ER relations, enforcing batch and registry continuity."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from er.io import digest, quote
from er.lineage import validate_input
from er.registry import load_registry, rebuild_registry
from scripts.canonical_paths import ER_INPUT, ER_MODEL, ER_PREDICTION, STAGING_DB
from scripts.local_duckdb import connect_local

ROOT = Path(__file__).resolve().parents[1]
INPUT, MODEL, PREDICTION = ER_INPUT, ER_MODEL, ER_PREDICTION
RAW = STAGING_DB


def _require(c, sql, message):
    if c.execute(sql).fetchone()[0]:
        raise ValueError(message)


def load(
    database, *, input_dir=INPUT, model_dir=MODEL, prediction_dir=PREDICTION, source_database=RAW
):
    database, input_dir, model_dir, prediction_dir, source_database = map(
        Path, (database, input_dir, model_dir, prediction_dir, source_database)
    )
    if database.resolve() == source_database.resolve() or any(
        database.resolve().is_relative_to(ROOT / ".tools" / area) for area in ["er", "staging"]
    ):
        raise ValueError("refusing to modify reviewed source database")
    prediction = json.loads((prediction_dir / "prediction.json").read_text())
    training = json.loads((model_dir / "training.json").read_text())
    metadata = validate_input(input_dir, training=training, prediction=prediction)
    if training.get("input_kind") != metadata.get("kind") or prediction.get(
        "input_kind"
    ) != metadata.get("kind"):
        raise ValueError("input/model/prediction kind mismatch")
    for path, expected in [
        (model_dir / "model.json", training["model_sha256"]),
        (prediction_dir / "int_employer_xref.parquet", prediction["xref_sha256"]),
        (prediction_dir / "registry.json", prediction["registry_sha256"]),
    ]:
        if digest(path) != expected:
            raise ValueError(f"artifact digest mismatch: {path.name}")
    if prediction["model_sha256"] != training["model_sha256"]:
        raise ValueError("model lineage mismatch")
    registry = load_registry(prediction_dir / "registry.json")
    payload = registry["payload"]
    if (
        payload["input_records_sha256"] != metadata["records_sha256"]
        or payload["input_occurrences_sha256"] != metadata["occurrences_sha256"]
    ):
        raise ValueError("registry input lineage mismatch")
    # Validate entirely in bounded memory before writing to the destination.
    with connect_local(":memory:") as c:
        for table, path in [
            ("records", input_dir / "records.parquet"),
            ("occurrences", input_dir / "occurrences.parquet"),
            ("xref", prediction_dir / "int_employer_xref.parquet"),
        ]:
            c.execute(f"create table {table} as select * from read_parquet(?)", [str(path)])
        for table in ["records", "xref"]:
            _require(
                c,
                f"select count(*) from (select source_system,source_employer_key from {table} group by all having count(*)<>1 or source_system is null or source_employer_key is null or trim(source_system)='' or trim(source_employer_key)='')",
                "duplicate or missing source key",
            )
            _require(
                c,
                f"select count(*) from (select unique_id from {table} group by all having count(*)<>1 or unique_id is null)",
                "duplicate or missing unique ID",
            )
        _require(
            c,
            "select count(*) from records r full join xref x using(unique_id) where r.unique_id is null or x.unique_id is null or r.source_system is distinct from x.source_system or r.source_employer_key is distinct from x.source_employer_key or r.source_name is distinct from x.source_name",
            "xref/source-key membership mismatch",
        )
        _require(
            c,
            "select count(*) from occurrences o left join records r using(unique_id) where r.unique_id is null or o.source_system is distinct from r.source_system or o.source_employer_key is distinct from r.source_employer_key",
            "occurrence/source-key membership mismatch",
        )
        _require(
            c,
            "select count(*) from records r where not exists (select 1 from occurrences o where o.unique_id=r.unique_id)",
            "source record missing occurrence",
        )
        records = [
            dict(zip([d[0] for d in c.description], row))
            for row in c.execute("select * from records").fetchall()
        ]
        deployed = dict(c.execute("select unique_id,employer_id from xref").fetchall())
        # Rebuild published identities from deployed components and the immutable parent.
        # This validates continuity; human quality/scored-cluster acceptance remains Task4 OPEN.
        anchors = {}
        for uid, employer in deployed.items():
            if employer is not None:
                anchors[employer] = min(uid, anchors.get(employer, uid))
        membership = {uid: anchors[emp] if emp else uid for uid, emp in deployed.items()}
        canonical, rebuilt, published = rebuild_registry(
            records, membership, metadata, prediction, prediction_dir
        )
        if canonical != deployed or rebuilt != published:
            raise ValueError("xref registry provenance mismatch")
        _require(
            c,
            "select count(*) from (select source_system,source_release,source_sha256,source_row_number from occurrences group by all having count(*)<>1)",
            "duplicate source occurrence",
        )
        if (
            c.execute("select count(*) from occurrences").fetchone()[0] != metadata["rows"]
            or len(records) != metadata["source_records"]
        ):
            raise ValueError("manifest source counts mismatch")
        c.execute("attach " + quote(source_database) + " as original (read_only)")
        c.execute(
            "create table stage_occurrences as select source_system,source_employer_key,source_release,source_sha256,source_row_number,fiscal_year from original.STAGING.stg_lca union all select source_system,source_employer_key,source_release,source_sha256,source_row_number,fiscal_year from original.STAGING.stg_uscis where released"
        )
        _require(
            c,
            "select count(*) from ((select source_system,source_employer_key,source_release,source_sha256,source_row_number from stage_occurrences except all select source_system,source_employer_key,source_release,source_sha256,source_row_number from occurrences) union all (select source_system,source_employer_key,source_release,source_sha256,source_row_number from occurrences except all select source_system,source_employer_key,source_release,source_sha256,source_row_number from stage_occurrences))",
            "staging/ER occurrence lineage mismatch",
        )
        originals = sorted(
            (r["source_system"], r["source_release"], r["source_sha256"], r["rows"])
            for r in metadata["originals"]
        )
        observed = sorted(
            c.execute(
                "select source_system,source_release,source_sha256,count(*) from occurrences group by all"
            ).fetchall()
        )
        if originals != observed:
            raise ValueError("original release counts mismatch")
        c.execute(
            "create table employer_sources as select x.*, true as is_source_active, o.fiscal_year as source_fiscal_year, o.source_release as source_observation_period from xref x join (select source_system,source_employer_key,max(fiscal_year) as fiscal_year,max(source_release) as source_release from stage_occurrences group by all) o using(source_system,source_employer_key)"
        )
        c.execute(
            "create table lca_attributes as select source_system,source_employer_key,source_release,source_sha256,source_row_number,fiscal_year,decision_date,soc_code_raw,soc_code,worksite_state,worksite_county,state_fips,county_fips,geography_vintage,geography_status from original.STAGING.stg_lca"
        )
        lineage = {
            "input_kind": metadata["kind"],
            "namespace": payload["namespace"],
            "registry_sha256": prediction["registry_sha256"],
            "registry_previous_sha256": prediction["registry_previous_sha256"],
            "input_records_sha256": metadata["records_sha256"],
            "input_occurrences_sha256": metadata["occurrences_sha256"],
            "model_sha256": prediction["model_sha256"],
            "xref_sha256": prediction["xref_sha256"],
            "source_database": str(source_database.resolve()),
            "loaded_at_utc": datetime.now(UTC).isoformat(),
            "acceptance_status": prediction["acceptance_status"],
        }
        existed = database.exists()
        database.parent.mkdir(parents=True, exist_ok=True)
        try:
            with connect_local(database) as out:
                out.execute("begin")
                if existed:
                    tables = {
                        r[0]
                        for r in out.execute(
                            "select table_name from information_schema.tables where table_schema='DIMENSIONS_INPUT'"
                        ).fetchall()
                    }
                    if "lineage" not in tables:
                        raise ValueError(
                            "existing database lacks dimension lineage; explicit migration required"
                        )
                    old = json.loads(
                        out.execute(
                            "select manifest_json from DIMENSIONS_INPUT.lineage"
                        ).fetchone()[0]
                    )
                    if old["namespace"] != lineage["namespace"] or (
                        old["registry_sha256"] != lineage["registry_sha256"]
                        and old["registry_sha256"] != lineage["registry_previous_sha256"]
                    ):
                        raise ValueError(
                            "registry continuity mismatch: only same batch/direct inherited child accepted"
                        )
                    # Even a valid registry envelope may not rewrite a previously assigned ID.
                    previous = dict(
                        out.execute(
                            "select unique_id,employer_id from DIMENSIONS_INPUT.employer_sources"
                        ).fetchall()
                    )
                    if any(
                        emp is not None and deployed.get(uid, emp) != emp
                        for uid, emp in previous.items()
                    ):
                        raise ValueError(
                            "existing source identity changed; explicit migration required"
                        )
                out.execute("create schema if not exists DIMENSIONS_INPUT")
                for table in ["employer_sources", "lca_attributes"]:
                    arrow = c.sql("select * from " + table).to_arrow_table()
                    out.register("validated_input", arrow)
                    if table == "employer_sources" and existed:
                        out.execute(
                            "create or replace temp table next_sources as select * from validated_input"
                        )
                        out.execute(
                            "insert into next_sources select old.* replace(false as is_source_active) from DIMENSIONS_INPUT.employer_sources old where not exists (select 1 from next_sources n where n.unique_id=old.unique_id)"
                        )
                        out.execute(
                            "create or replace table DIMENSIONS_INPUT.employer_sources as select * from next_sources"
                        )
                    else:
                        out.execute(
                            "create or replace table DIMENSIONS_INPUT."
                            + table
                            + " as select * from validated_input"
                        )
                    out.unregister("validated_input")
                out.execute(
                    "create or replace table DIMENSIONS_INPUT.lineage as select ? as manifest_json",
                    [json.dumps(lineage)],
                )
                out.execute("commit")
        except Exception:
            if not existed:
                database.unlink(missing_ok=True)
            raise
    return lineage


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, default=INPUT)
    parser.add_argument("--model-dir", type=Path, default=MODEL)
    parser.add_argument("--prediction-dir", type=Path, default=PREDICTION)
    parser.add_argument("--source-database", type=Path, default=RAW)
    args = parser.parse_args()
    print(
        json.dumps(
            load(
                args.database,
                input_dir=args.input_dir,
                model_dir=args.model_dir,
                prediction_dir=args.prediction_dir,
                source_database=args.source_database,
            ),
            indent=2,
        )
    )
