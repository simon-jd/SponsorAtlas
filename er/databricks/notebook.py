# Databricks notebook source
# ruff: noqa: F821  # Databricks injects dbutils and spark into notebook cells.
# MAGIC %pip install splink==5.0.0 pyarrow==26.0.0

# COMMAND ----------
# Keep Databricks' bundled Connect client; do NOT install local pyspark-client here.
dbutils.library.restartPython()

# COMMAND ----------
dbutils.widgets.text("project_root", "/Workspace/Shared/SponsorAtlas")
dbutils.widgets.text("input_dir", "")
dbutils.widgets.text("output_dir", "")
dbutils.widgets.text("catalog", "analytics")
dbutils.widgets.text("scratch_schema", "er_scratch")
dbutils.widgets.text("labels", "")
dbutils.widgets.text("initialize_registry", "false")
dbutils.widgets.text("previous_registry", "")
import sys

sys.path.insert(0, dbutils.widgets.get("project_root"))
from er.databricks_run import run_free

result = run_free(
    spark,
    input_dir=dbutils.widgets.get("input_dir"),
    output_dir=dbutils.widgets.get("output_dir"),
    catalog=dbutils.widgets.get("catalog"),
    scratch_schema=dbutils.widgets.get("scratch_schema"),
    labels=dbutils.widgets.get("labels") or None,
    initialize_registry=dbutils.widgets.get("initialize_registry").lower() == "true",
    previous_registry=dbutils.widgets.get("previous_registry") or None,
)
print(result)
