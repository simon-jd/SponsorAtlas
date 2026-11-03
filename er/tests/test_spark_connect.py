"""LOCAL PROTOCOL/SQL CONTRACT ONLY. This does NOT launch Spark or Databricks."""

import shutil
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("pyspark")
from er.io import TOOLS
from er.settings import settings
from er.spark_connect import SparkConnectAPI


class Catalog:
    def __init__(self):
        self.objects = set()
        self.drops = []

    def tableExists(self, name):
        return name in self.objects

    def dropTempView(self, name):
        self.objects.discard(name)
        self.drops.append(name)


class Frame:
    def __init__(self, spark):
        self.spark = spark
        self.write = self
        self.columns = ["unique_id", "name_norm"]

    def format(self, x):
        assert x == "delta"
        return self

    def mode(self, x):
        self.mode_value = x
        return self

    def saveAsTable(self, name):
        self.spark.saved.append((name, self.mode_value))
        self.spark.catalog.objects.add(name)

    def createOrReplaceTempView(self, name):
        self.spark.catalog.objects.add(name)


class Session:
    # Any accidental classic Spark/JVM access fails immediately in these protocol tests.
    @property
    def sparkContext(self):
        raise AssertionError("JVM/classic Spark is forbidden")

    @property
    def _jsc(self):
        raise AssertionError("private JVM is forbidden")

    def __init__(self):
        self.catalog = Catalog()
        self.saved = []
        self.queries = []

    def table(self, name):
        return Frame(self)

    def createDataFrame(self, value):
        self.created_input = value
        return Frame(self)

    def sql(self, query):
        self.queries.append(query)
        return Frame(self)


@pytest.fixture
def backend():
    root = TOOLS / "er-tests"
    root.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(dir=root))
    spark = Session()
    db = SparkConnectAPI(
        spark_session=spark, catalog="analytics", database="er_scratch", workspace_dir=directory
    )
    yield spark, db
    db.close()
    shutil.rmtree(directory)


def test_native_delta_and_only_own_objects(backend):
    spark, db = backend
    data = db.register(Frame(spark), table_name="own_input")
    assert data.physical_name == "own_input"
    result = db._cleanup_for_execute_sql(Frame(spark), "__splink__temp", "__splink__temp_hash")
    assert result.physical_name == "__splink__temp_hash"
    assert spark.saved == [("`analytics`.`er_scratch`.`__splink__temp_hash`", "error")]
    with pytest.raises(ValueError, match="not owned"):
        db.delete_table_from_database("unrelated")
    spark.catalog.objects.add("external")
    with pytest.raises(ValueError, match="outside this backend"):
        db._cleanup_for_execute_sql(Frame(spark), "external", "external")
    db.delete_table_from_database("__splink__temp_hash")
    assert spark.queries == ["DROP TABLE IF EXISTS `analytics`.`er_scratch`.`__splink__temp_hash`"]
    db.register([{"u_count": 1.0, "m_count": 0.0}], table_name="own_counts")
    assert spark.created_input.to_pylist() == [{"u_count": 1.0, "m_count": 0.0}]


def test_splink_shared_settings_compile_to_native_spark_sql(backend):
    _, db = backend
    compiled = settings().create_settings_dict("spark")
    sql = " ".join(
        level["sql_condition"]
        for comp in compiled["comparisons"]
        for level in comp["comparison_levels"]
    )
    assert "levenshtein" in sql.lower()
    assert not any(function in sql.lower() for function in ("jaro", "damerau", "jaccard"))
    generated = db._setup_for_execute_sql(
        "SELECT CAST(0.5 AS DOUBLE) AS probability", "__splink__x"
    )
    assert "0.5D" in generated
    assert db.duckdb_con.execute("select current_setting('threads')").fetchall()[0][0] == 1
