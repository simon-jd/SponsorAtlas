"""Opt-in live Free Edition entry: actual remote Splink Spark u/EM/predict.

This module is not executed by local contract tests. Input is the traceable,
normalized real LCA/USCIS export copied into the project's Unity Catalog volume.
Graph constraints run on the bounded Python driver after remote Spark scoring.
"""

import csv
import json
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from splink import Linker

from er.evaluate import validate_labels
from er.io import digest, dump
from er.lineage import validate_input
from er.predict import build_xref, constrained_clusters
from er.registry import registry_metadata, registry_request, resolve_registry, save_registry
from er.settings import (
    ID_BLOCK,
    MAX_CANDIDATES,
    MAX_RECORDS,
    MAX_U_PAIRS,
    NAME_BLOCK,
    SEED,
    THRESHOLD,
    settings,
)
from er.spark_connect import SparkConnectAPI, identifier, workspace_directory

XREF_SCHEMA = "source_system string, source_name string, employer_id string, match_probability double, source_employer_key string, state string, country string, city string, postcode string, unique_id string, issues string, resolution_status string, match_method string"


def upper_bound(spark, table):
    total = 0
    for cols, condition in [
        ("first_token,state", "first_token IS NOT NULL AND state IS NOT NULL"),
        ("strong_id", "strong_id IS NOT NULL"),
    ]:
        total += int(
            spark.sql(
                f"SELECT coalesce(sum(n*(n-1)/2),0) AS n FROM (SELECT count(*) n FROM {table} WHERE {condition} GROUP BY {cols})"
            ).collect()[0][0]
        )
    if total < 1:
        raise ValueError("no valid blocking pairs")
    if total > MAX_CANDIDATES:
        raise ValueError("Spark candidate budget exceeded; no unbounded scoring")
    return total


def run_free(
    spark,
    *,
    input_dir,
    output_dir,
    catalog="analytics",
    scratch_schema="er_scratch",
    labels=None,
    initialize_registry=False,
    previous_registry=None,
):
    """Requires existing catalog/schemas and bundled Databricks Connect. No provisioning."""
    previous_digest = registry_request(initialize_registry, previous_registry)
    inp = workspace_directory(input_dir)
    metadata = validate_input(inp)
    out = Path(output_dir)
    if out.exists():
        raise ValueError("refusing to overwrite a previous Spark run")
    out = workspace_directory(out)
    catalog = identifier(catalog)
    scratch_schema = identifier(scratch_schema)
    input_hash = metadata["records_sha256"]
    if metadata["kind"] != "real-staging-lca-uscis":
        raise ValueError("live acceptance requires real employer staging")
    if pq.ParquetFile(inp / "records.parquet").metadata.num_rows > MAX_RECORDS:
        raise ValueError("driver graph record budget exceeded")
    rows = pq.read_table(inp / "records.parquet").to_pylist()
    if len({r["unique_id"] for r in rows}) != len(rows):
        raise ValueError("duplicate source identities")
    excluded = set()
    labels_hash = None
    if labels:
        with open(labels, newline="") as f:
            reviewed = list(csv.DictReader(f))
        validate_labels(reviewed)
        excluded = {
            r[k] for r in reviewed if r["split"] == "holdout" for k in ("left_id", "right_id")
        }
        labels_hash = digest(labels)
    # This path only uses Spark SQL/DataFrame/Delta; server resources are managed
    # by Free Edition. Unsupported memory/JVM Spark conf is never set.
    spark.conf.set("spark.databricks.execution.timeout", 1800)
    spark.conf.set("spark.sql.shuffle.partitions", 4)
    original = spark.read.parquet(str(inp / "records.parquet"))
    training = original.filter("eligible")
    if excluded:
        from pyspark.sql import functions as F

        training = training.filter(~F.col("unique_id").isin(sorted(excluded)))
    training_ids = sorted(
        r["unique_id"] for r in rows if r["eligible"] and r["unique_id"] not in excluded
    )
    if len(training_ids) < 2:
        raise ValueError("at least two eligible training records required")
    (out / "training_ids.txt").write_text("\n".join(training_ids) + "\n")
    db = SparkConnectAPI(
        spark_session=spark,
        catalog=catalog,
        database=scratch_schema,
        workspace_dir=out / "training-helper",
    )
    try:
        source = db.register(training, table_name="er_" + db.id + "_train")
        bound = upper_bound(spark, source.physical_name)
        linker = Linker(source, settings())
        linker.training.estimate_u_using_random_sampling(max_pairs=MAX_U_PAIRS, seed=SEED)
        sessions = []
        for rule in (NAME_BLOCK, ID_BLOCK):
            n = int(
                spark.sql(
                    f"SELECT count(*) FROM {source.physical_name} l JOIN {source.physical_name} r ON l.unique_id<r.unique_id AND ({rule})"
                ).collect()[0][0]
            )
            if n:
                linker.training.estimate_parameters_using_expectation_maximisation(rule)
                sessions.append({"rule": rule, "pairs": n})
        linker.misc.save_model_to_json(str(out / "model.json"), overwrite=False)
        saved = json.loads((out / "model.json").read_text())
        untrained = [
            {"comparison": c["output_column_name"], "level": l["label_for_charts"], "parameter": p}
            for c in saved["comparisons"]
            for l in c["comparison_levels"]
            if not l.get("is_null_level")
            for p in ("m_probability", "u_probability")
            if p not in l
        ]
        model_meta = {
            "kind": "splink-spark-connect-u-em",
            "input_kind": metadata["kind"],
            "splink_version": "5.0.0",
            "seed": SEED,
            "records": len(training_ids),
            "excluded_holdout_records": len(excluded),
            "labels_sha256": labels_hash,
            "acceptance_eligible": bool(labels_hash) and not untrained,
            "untrained_parameters": untrained,
            "input_records_sha256": input_hash,
            "input_occurrences_sha256": metadata["occurrences_sha256"],
            "model_sha256": digest(out / "model.json"),
            "training_ids_sha256": digest(out / "training_ids.txt"),
            "em_sessions": sessions,
            "candidate_upper_bound": bound,
            "spark_version": spark.version,
            "backend_adapter": "SponsorAtlas.SparkConnectAPI-v1",
        }
        validate_input(inp, training=model_meta)
        dump(out / "training.json", model_meta)
    finally:
        db.close()
    validate_input(inp, training=model_meta)
    db = SparkConnectAPI(
        spark_session=spark,
        catalog=catalog,
        database=scratch_schema,
        workspace_dir=out / "prediction-helper",
    )
    try:
        source = db.register(original.filter("eligible"), table_name="er_" + db.id + "_predict")
        bound = upper_bound(spark, source.physical_name)
        linker = Linker(source, str(out / "model.json"))
        prediction = linker.inference.predict(threshold_match_probability=0.0)
        scored = (
            prediction.as_spark_dataframe()
            .selectExpr("unique_id_l AS left_id", "unique_id_r AS right_id", "match_probability")
            .toArrow()
        )
        if scored.num_rows > MAX_CANDIDATES:
            raise ValueError("scored pair budget exceeded")
        pq.write_table(scored, out / "scores.parquet", compression="zstd")
        edges = [(r["left_id"], r["right_id"], r["match_probability"]) for r in scored.to_pylist()]
        members, decisions = constrained_clusters(rows, edges, THRESHOLD)
        validate_input(inp, training=model_meta)
        if previous_registry and digest(previous_registry) != previous_digest:
            raise ValueError("previous registry changed during Spark scoring")
        canonical, registry = resolve_registry(
            rows, members, metadata, initialize=initialize_registry, previous=previous_registry
        )
        save_registry(out / "registry.json", registry)
        identity_metadata = registry_metadata(out / "registry.json", previous_registry)
        xref = build_xref(rows, canonical, decisions)
        pq.write_table(
            pa.Table.from_pylist(xref), out / "int_employer_xref.parquet", compression="zstd"
        )
        pq.write_table(
            pa.Table.from_pylist(decisions), out / "pair_decisions.parquet", compression="zstd"
        )
        # Explicit canonical target, fail if it exists: an operator must review
        # a later replacement. Scratch cleanup cannot touch this output table.
        target = f"`{catalog}`.`intermediate`.`int_employer_xref`"
        validate_input(inp, training=model_meta)
        spark.createDataFrame(xref, schema=XREF_SCHEMA).write.format("delta").mode(
            "error"
        ).saveAsTable(target)
        summary = {
            "kind": "splink-spark-connect-predict",
            "input_kind": metadata["kind"],
            "threshold": THRESHOLD,
            "source_records": len(rows),
            "candidates": scored.num_rows,
            "candidate_upper_bound": bound,
            "statuses": dict(Counter(r["resolution_status"] for r in xref)),
            "decisions": dict(Counter(d["reason"] for d in decisions)),
            "input_records_sha256": input_hash,
            "input_occurrences_sha256": metadata["occurrences_sha256"],
            "model_sha256": digest(out / "model.json"),
            "scores_sha256": digest(out / "scores.parquet"),
            "xref_sha256": digest(out / "int_employer_xref.parquet"),
            "target": target,
            "acceptance_status": "HUMAN_HOLDOUT_EVALUATION_REQUIRED",
            "spark_version": spark.version,
            "driver_record_limit": MAX_RECORDS,
            "pair_limit": MAX_CANDIDATES,
            **identity_metadata,
        }
        dump(out / "prediction.json", summary)
        return summary
    finally:
        db.close()
