"""Version-pinned Splink 5 backend adapter for native Spark Connect SQL/Delta.

This is a project backend extension, NOT a claim of Databricks live validation.
No SparkContext, JVM, RDD, JAR, environment spoofing or installed-library patch.
"""

import re
from pathlib import Path

import splink
from splink.backends.spark import SparkAPI
from splink.internals.database_api import DatabaseAPI
from splink.internals.misc import to_pyarrow_if_list_tuple_or_dict

from scripts.local_duckdb import connect_local


def identifier(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("catalog/schema/table must be simple explicit identifiers")
    return value


def workspace_directory(path):
    path = Path(path)
    if not path.is_absolute() or ".tools" not in path.parts:
        raise ValueError("Spark client artifacts require an absolute project .tools directory")
    path.mkdir(parents=True, exist_ok=True)
    return path


class SparkConnectAPI(SparkAPI):
    """Reuse Splink Spark SQL dialect/dataframe with a Connect-only lifecycle.

    DatabaseAPI is Splink's documented-in-source backend subclass contract.
    SparkAPI.__init__ is deliberately not used: it enables classic JVM JAR UDFs.
    Every intermediate plan materialises to a uniquely owned Delta table plus a
    session view, avoiding checkpoint/persist and private Spark APIs entirely.
    """

    def __init__(self, *, spark_session, catalog, database, workspace_dir):
        if splink.__version__ != "5.0.0":
            raise RuntimeError("SparkConnectAPI requires reviewed Splink 5.0.0")
        DatabaseAPI.__init__(self)
        # Base DatabaseAPI owns a helper DuckDB connection. Bound that too.
        self._ddb_con.close()
        directory = workspace_directory(workspace_dir)
        self._ddb_con = connect_local(directory / "spark-client-helper.duckdb")
        spill = directory / "spill"
        spill.mkdir(exist_ok=True)
        self._ddb_con.execute("SET temp_directory = '" + str(spill).replace("'", "''") + "'")
        self.spark = spark_session
        self.catalog = identifier(catalog)
        self.database = identifier(database)
        self.splink_data_store = f"`{self.catalog}`.`{self.database}`"
        self.break_lineage_method = "delta_lake_table"
        self.repartition_after_blocking = False
        self.num_partitions_on_repartition = 4
        self._owned_delta = set()
        self._owned_views = set()

    def _qualified(self, name):
        return self.splink_data_store + ".`" + identifier(name) + "`"

    def table_exists_in_database(self, name):
        identifier(name)
        return self.spark.catalog.tableExists(name) or self.spark.catalog.tableExists(
            self._qualified(name)
        )

    def _table_registration(self, input, table_name):
        identifier(table_name)
        if self.table_exists_in_database(table_name) and table_name not in self._owned_views:
            raise ValueError("refusing to replace an existing session or catalog object")
        if not hasattr(input, "createOrReplaceTempView"):
            # Splink's u/EM also register small Python aggregate-count tables.
            input = self.spark.createDataFrame(to_pyarrow_if_list_tuple_or_dict(input))
        input.createOrReplaceTempView(table_name)
        self._owned_views.add(table_name)

    def _cleanup_for_execute_sql(self, table, templated_name, physical_name):
        identifier(physical_name)
        if self.table_exists_in_database(physical_name) and physical_name not in self._owned_delta:
            raise ValueError("refusing to replace an object outside this backend run")
        mode = "overwrite" if physical_name in self._owned_delta else "error"
        table.write.format("delta").mode(mode).saveAsTable(self._qualified(physical_name))
        self._owned_delta.add(physical_name)
        materialized = self.spark.table(self._qualified(physical_name))
        materialized.createOrReplaceTempView(physical_name)
        self._owned_views.add(physical_name)
        return self.table_to_splink_dataframe(templated_name, physical_name)

    def delete_table_from_database(self, name):
        identifier(name)
        if name not in self._owned_delta and name not in self._owned_views:
            raise ValueError("refusing to delete an object not owned by this backend run")
        if name in self._owned_views:
            self.spark.catalog.dropTempView(name)
            self._owned_views.remove(name)
        if name in self._owned_delta:
            self.spark.sql("DROP TABLE IF EXISTS " + self._qualified(name))
            self._owned_delta.remove(name)

    def close(self):
        for name in sorted(self._owned_delta | self._owned_views):
            self.delete_table_from_database(name)
        self._ddb_con.close()
