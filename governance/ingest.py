"""External official dbt workflow. No database profiler, samples, or ingestion server."""

import os
from pathlib import Path

import yaml
from metadata.workflow.metadata import MetadataWorkflow

from governance.local_auth import token

ROOT = Path(__file__).resolve().parents[1]


def ingest(service="sponsoratlas_local_synthetic_duckdb", artifacts="dbt-artifacts", include=None):
    os.environ["SA_OM_TOKEN"] = token()
    os.environ["SA_DBT_ARTIFACT_DIR"] = str(ROOT / ".tools/governance" / artifacts)
    config = yaml.safe_load(
        os.path.expandvars((ROOT / "governance/openmetadata/dbt-local.yaml").read_text())
    )
    config["source"]["serviceName"] = service
    if include:
        config["source"]["sourceConfig"]["config"]["tableFilterPattern"] = {"includes": include}
    workflow = MetadataWorkflow.create(config)
    try:
        workflow.execute()
        workflow.raise_from_status()
        workflow.print_status()
    finally:
        workflow.stop()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--service", default="sponsoratlas_local_synthetic_duckdb")
    parser.add_argument("--artifacts", default="dbt-artifacts")
    parser.add_argument("--include", action="append")
    args = parser.parse_args()
    ingest(args.service, args.artifacts, args.include)
