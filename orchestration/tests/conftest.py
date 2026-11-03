import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("AIRFLOW_HOME", str(ROOT / ".tools/orchestration/test-airflow"))
os.environ["AIRFLOW__COSMOS__ENABLE_CACHE"] = "False"
os.environ["AIRFLOW__COSMOS__ENABLE_DAG_VERSIONING"] = "False"
os.environ["AIRFLOW__OPENLINEAGE__NAMESPACE"] = "sponsoratlas"
os.environ["DBT_TARGET"] = "local"
