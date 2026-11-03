"""Opt-in REAL LOCAL Spark Connect+Delta execution on SYNTHETIC records.

This never constitutes Databricks Free Edition or human quality acceptance.
Parent supplies an isolated Connect server; this test never provisions services.
"""

import math
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from er.io import TOOLS, dump
from er.settings import ID_BLOCK, NAME_BLOCK, SEED, settings
from er.tests.fixtures.synthetic import records

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("SPONSORATLAS_SPARK_CONNECT_TEST") != "1",
        reason="opt-in isolated local Spark Connect+Delta server required; not Databricks",
    ),
]


def test_real_local_spark_connect_u_em_predict_and_cleanup():
    from pyspark.sql import SparkSession
    from splink import Linker

    from er.spark_connect import SparkConnectAPI, identifier

    remote = os.environ["SPONSORATLAS_SPARK_REMOTE"]
    catalog = identifier(os.environ.get("SPONSORATLAS_SPARK_CATALOG", "spark_catalog"))
    database = "er_test_" + uuid.uuid4().hex[:12]
    fq = f"`{catalog}`.`{database}`"
    root = TOOLS / "er-tests"
    root.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=root))
    spark = SparkSession.builder.remote(remote).getOrCreate()
    db = None
    created = False
    sentinel = False
    try:
        spark.sql(f"CREATE DATABASE {fq}")
        created = True
        spark.sql(f"CREATE TABLE {fq}.keep_me USING DELTA AS SELECT 1 AS preserved")
        sentinel = True
        db = SparkConnectAPI(
            spark_session=spark, catalog=catalog, database=database, workspace_dir=work
        )
        schema = "unique_id string, source_system string, source_employer_key string, source_name string, state string, country string, city string, postcode string, tax_id string, name_norm string, first_token string, strong_id string, tax_last4 string, issues string, eligible boolean"
        source = db.register(
            spark.createDataFrame(records(), schema=schema), table_name="er_" + db.id + "_input"
        )
        linker = Linker(source, settings())
        linker.training.estimate_u_using_random_sampling(
            max_pairs=10_000, seed=SEED, num_chunks=1, min_count_per_level=None
        )
        for rule in (NAME_BLOCK, ID_BLOCK):
            linker.training.estimate_parameters_using_expectation_maximisation(rule)
        scores = linker.inference.predict(threshold_match_probability=0.0)
        scored = (
            scores.as_spark_dataframe()
            .select("unique_id_l", "unique_id_r", "match_probability")
            .collect()
        )
        assert scored and all(
            math.isfinite(r.match_probability) and 0 <= r.match_probability <= 1 for r in scored
        )
        owned = list(db._owned_delta)
        assert owned
        db.close()
        db = None
        assert spark.catalog.tableExists(f"{fq}.keep_me")
        assert all(not spark.catalog.tableExists(f"{fq}.`{name}`") for name in owned)
        dump(
            TOOLS / "er" / "spark-connect-integration.json",
            {
                "status": "PASS",
                "scope": "REAL_LOCAL_SPARK_CONNECT_DELTA_SYNTHETIC_INPUT_NOT_DATABRICKS",
                "spark_version": spark.version,
                "records": len(records()),
                "predicted_pairs": len(scored),
                "em_sessions": 2,
                "seed": SEED,
                "owned_tables_cleaned": len(owned),
                "sentinel_preserved": True,
            },
        )
    finally:
        if db is not None:
            db.close()
        if sentinel:
            spark.sql(f"DROP TABLE {fq}.keep_me")
        if created:
            spark.sql(f"DROP DATABASE {fq} RESTRICT")
        spark.stop()
        shutil.rmtree(work)
