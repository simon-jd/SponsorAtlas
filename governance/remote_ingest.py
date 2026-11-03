"""Explicitly authorized Snowflake metadata + real Snowflake dbt artifacts only."""

import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def run():
    if os.getenv("RUN_SNOWFLAKE_METADATA") != "1":
        raise SystemExit("OPEN: set RUN_SNOWFLAKE_METADATA=1 only after trial/cost authorization")
    required = [
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_METADATA_USER",
        "SNOWFLAKE_METADATA_ROLE",
        "SNOWFLAKE_PRIVATE_KEY_FILE",
        "SNOWFLAKE_DBT_ARTIFACT_DIR",
        "OPENMETADATA_URL",
        "OPENMETADATA_JWT_TOKEN",
    ]
    if missing := [k for k in required if not os.getenv(k)]:
        raise ValueError("Missing settings: " + ", ".join(missing))
    artifacts = Path(os.environ["SNOWFLAKE_DBT_ARTIFACT_DIR"]).resolve()
    os.environ["SNOWFLAKE_DBT_ARTIFACT_DIR"] = str(artifacts)
    documents = {
        name: json.loads((artifacts / f"{name}.json").read_text())
        for name in ("manifest", "catalog", "run_results")
    }
    if documents["manifest"]["metadata"].get("adapter_type") != "snowflake":
        raise ValueError("Reject non-Snowflake manifest; local artifacts cannot close this gate")
    results = documents["run_results"].get("results", [])
    if (
        not results
        or documents["run_results"].get("args", {}).get("target") != "snowflake"
        or any(row["status"] not in ("success", "pass") for row in results)
    ):
        raise ValueError("Require successful executed Snowflake dbt results")
    import snowflake.connector
    from metadata.workflow.metadata import MetadataWorkflow

    key = Path(os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"]).expanduser()
    options = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_METADATA_USER"],
        "role": os.environ["SNOWFLAKE_METADATA_ROLE"],
        "authenticator": "SNOWFLAKE_JWT",
        "private_key_file": str(key),
        "warehouse": "SPONSORATLAS_XS",
        "login_timeout": 30,
        "session_parameters": {
            "STATEMENT_TIMEOUT_IN_SECONDS": 60,
            "QUERY_TAG": "sponsoratlas:task8:metadata-preflight",
        },
    }
    passphrase = os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
    if passphrase:
        options["private_key_file_pwd"] = passphrase
    with snowflake.connector.connect(**options) as connection:
        cursor = connection.cursor().execute("SHOW WAREHOUSES LIKE 'SPONSORATLAS_XS'")
        warehouse = dict(zip([c[0] for c in cursor.description], cursor.fetchone()))
        assert warehouse["size"] == "X-Small" and int(warehouse["auto_suspend"]) == 60
    # The exact declared dbt owner must exist before enrichment. No personal owner is guessed.
    import requests

    response = requests.put(
        os.environ["OPENMETADATA_URL"].rstrip("/") + "/v1/teams",
        headers={"Authorization": "Bearer " + os.environ["OPENMETADATA_JWT_TOKEN"]},
        json={
            "name": "sponsoratlas-governance",
            "teamType": "Group",
            "description": "SponsorAtlas project metadata ownership",
        },
        timeout=30,
    )
    response.raise_for_status()
    for file in ("snowflake.yaml", "dbt-snowflake.yaml"):
        config = yaml.safe_load(
            os.path.expandvars((ROOT / "governance/openmetadata" / file).read_text())
        )
        if file == "snowflake.yaml":
            connection_config = config["source"]["serviceConnection"]["config"]
            connection_config["privateKey"] = key.read_text()
            if passphrase:
                connection_config["snowflakePrivatekeyPassphrase"] = passphrase
        else:
            # The dbt parser needs the dialect, but never persists the source key in OM.
            config["source"]["serviceConnection"] = {"config": connection_config}
        workflow = MetadataWorkflow.create(config)
        try:
            workflow.execute()
            workflow.raise_from_status()
        finally:
            workflow.stop()


if __name__ == "__main__":
    run()
