"""Verify the existing Python loader's real cross-file copy, then publish column edges."""

import hashlib
import json
import os
from pathlib import Path

from scripts.canonical_paths import FACTS_DB, STAGING_DB

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".tools/governance/evidence/loader-bridge.json"
COPY_SQL = "create table FACTS_INPUT.lca as select * from original.STAGING.stg_lca"
SOURCE_PATH, TARGET_PATH = STAGING_DB, FACTS_DB
SOURCE = f"sponsoratlas_local_public_staging_duckdb.{STAGING_DB.stem}.STAGING.stg_lca"
TARGET = f"sponsoratlas_local_public_facts_duckdb.{FACTS_DB.stem}.FACTS_INPUT.lca"


def verify():
    import duckdb

    code = ROOT / "scripts/load_fact_inputs.py"
    assert COPY_SQL in code.read_text()
    source_path, target_path = SOURCE_PATH, TARGET_PATH
    proof = {
        "sql": COPY_SQL,
        "loader_sha256": hashlib.file_digest(code.open("rb"), "sha256").hexdigest(),
        "from": SOURCE,
        "to": TARGET,
        "source_kind": "LOCAL_ARCHIVE_REPLAY, not Snowflake",
        "files": [],
    }
    schemas, summaries = [], []
    for path, table in [(source_path, "STAGING.stg_lca"), (target_path, "FACTS_INPUT.lca")]:
        with duckdb.connect(
            str(path),
            read_only=True,
            config={"memory_limit": "512MB", "threads": 1, "max_temp_directory_size": "128MB"},
        ) as db:
            schema = [(r[0], r[1]) for r in db.execute("DESCRIBE " + table).fetchall()]
            columns = ",".join('"' + name.replace('"', '""') + '"' for name, _ in schema)
            result = db.execute(
                f"SELECT count(*), bit_xor(hash({columns})) FROM {table}"
            ).fetchone()
            schemas.append(schema)
            summaries.append(result)
        proof["files"].append(
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest(),
                "rows": result[0],
                "row_hash_xor": result[1],
            }
        )
    assert schemas[0] == schemas[1], "Loader copy schema drift"
    assert summaries[0] == summaries[1], "Loader copy content drift"
    proof["columns"] = [name for name, _ in schemas[0]]
    proof["column_types"] = dict(schemas[0])
    EVIDENCE.write_text(json.dumps(proof, indent=2))
    print(
        "Verified loader copy:",
        len(proof["columns"]),
        "columns;",
        summaries[0][0],
        "rows; equal row hashes",
    )


def publish():
    from governance.catalog import Catalog
    from governance.local_auth import token

    os.environ["SA_OM_TOKEN"] = token()
    api = Catalog()
    proof = json.loads(EVIDENCE.read_text())
    # Publication is bound to the checked loader and physical source bytes.
    assert (
        hashlib.file_digest((ROOT / "scripts/load_fact_inputs.py").open("rb"), "sha256").hexdigest()
        == proof["loader_sha256"]
    )
    for file in proof["files"]:
        assert (
            hashlib.file_digest((ROOT / file["path"]).open("rb"), "sha256").hexdigest()
            == file["sha256"]
        )
    source = api.call("GET", "tables/name/" + SOURCE)
    target = api.call("GET", "tables/name/" + TARGET)
    assert [c["name"] for c in source["columns"]] == proof["columns"]
    assert [c["name"] for c in target["columns"]] == proof["columns"]
    service = api.put(
        "services/pipelineServices",
        {
            "name": "sponsoratlas_local_python",
            "serviceType": "CustomPipeline",
            "description": "Existing local Python loaders; verified metadata publication, not a new execution.",
        },
    )
    pipeline = api.put(
        "pipelines",
        {
            "name": "load_fact_inputs_lca",
            "service": service["fullyQualifiedName"],
            "description": f"scripts/load_fact_inputs.py copies {STAGING_DB.stem}.STAGING.stg_lca into {FACTS_DB.stem}.FACTS_INPUT.lca. Verified schema, source SHA and row hash; local DuckDB replay only.",
        },
    )
    columns = [
        {"fromColumns": [SOURCE + "." + c], "toColumn": TARGET + "." + c, "function": "identity"}
        for c in proof["columns"]
    ]
    api.put(
        "lineage",
        {
            "edge": {
                "fromEntity": {"id": source["id"], "type": "table"},
                "toEntity": {"id": target["id"], "type": "table"},
                "lineageDetails": {
                    "sqlQuery": COPY_SQL,
                    "columnsLineage": columns,
                    "source": "PipelineLineage",
                    "pipeline": {"id": pipeline["id"], "type": "pipeline"},
                    "description": "Cross-file local Python CTAS verified against schema and content hashes. Not inferred dbt ref, not Snowflake.",
                },
            }
        },
    )
    print("Published verified pipeline bridge:", len(columns), "column mappings")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["verify", "publish"])
    globals()[parser.parse_args().action]()
