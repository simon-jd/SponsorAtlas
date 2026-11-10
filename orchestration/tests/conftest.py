import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("AIRFLOW_HOME", str(ROOT / ".tools/orchestration/test-airflow"))
os.environ["AIRFLOW__COSMOS__ENABLE_CACHE"] = "False"
os.environ["AIRFLOW__COSMOS__ENABLE_DAG_VERSIONING"] = "False"
os.environ["AIRFLOW__OPENLINEAGE__NAMESPACE"] = "sponsoratlas"
os.environ["DBT_TARGET"] = "local"


@pytest.fixture(autouse=True)
def absent_stop_marker(monkeypatch, tmp_path):
    """The real marker exists until go-live, so no test may read it."""
    from orchestration.sponsoratlas_orchestration import trial

    monkeypatch.setattr(trial, "STOP", tmp_path / "absent-stop-marker")
