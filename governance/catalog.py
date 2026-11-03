"""Register actual dbt catalog schemas only; never query/upload samples or run profiler."""

import json
import os
from pathlib import Path

import requests

from scripts.canonical_paths import FACTS_DB, STAGING_DB

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".tools/governance"
SERVICES = {
    "sponsoratlas_local_synthetic_duckdb": ("dbt-artifacts", "synthetic_applications.duckdb"),
    # Full-fiscal-year artifacts (2026-10-28); the Q4-only ones stay in their old dirs.
    "sponsoratlas_local_public_facts_duckdb": ("public-facts-artifacts-fy2025", FACTS_DB),
    "sponsoratlas_local_public_staging_duckdb": ("public-staging-artifacts-fy2025", STAGING_DB),
}


class Catalog:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["Authorization"] = "Bearer " + os.environ["SA_OM_TOKEN"]
        self.base = "http://127.0.0.1:18585/api/v1/"

    def call(self, method, path, **kwargs):
        result = self.session.request(method, self.base + path, timeout=60, **kwargs)
        if not result.ok:
            raise RuntimeError(f"{method} {path}: {result.status_code} {result.text[:1500]}")
        try:
            return result.json() if result.content else None
        except requests.exceptions.JSONDecodeError:
            return result.text

    def put(self, path, data):
        return self.call("PUT", path, json=data)


def datatype(native):
    v = native.upper()
    if v.startswith(("VARCHAR", "TEXT", "CHAR")):
        return "STRING"
    if v.startswith(("DECIMAL", "NUMERIC")):
        return "DECIMAL"
    if v.startswith("TIMESTAMP"):
        return "TIMESTAMP"
    if v == "DATE":
        return "DATE"
    if v == "BOOLEAN":
        return "BOOLEAN"
    if v in ("FLOAT", "DOUBLE", "REAL"):
        return "DOUBLE"
    if "INT" in v:
        return "BIGINT"
    if v == "JSON":
        return "JSON"
    return "STRING"


def register():
    api = Catalog()
    team = api.put(
        "teams",
        {
            "name": "sponsoratlas-governance",
            "displayName": "SponsorAtlas Governance",
            "teamType": "Group",
            "description": "Local project metadata ownership; not a guessed personal data owner.",
        },
    )
    inventory = {}
    for name, (artifacts, physical) in SERVICES.items():
        catalog = json.loads((RUNTIME / artifacts / "catalog.json").read_text())
        api.put(
            "services/databaseServices",
            {
                "name": name,
                "serviceType": "CustomDatabase",
                "description": "Actual local DuckDB file "
                + str((RUNTIME / physical).resolve().relative_to(ROOT))
                + ". Artifact-only catalog; no Snowflake connection, samples or profiler.",
                "connection": {"config": {"type": "CustomDatabase"}},
            },
        )
        seen = {}
        for node in [*catalog["sources"].values(), *catalog["nodes"].values()]:
            meta = node["metadata"]
            # Catalog from dbt docs describes only relations actually discovered in this DB.
            database = api.put("databases", {"name": meta["database"], "service": name})
            schema = api.put(
                "databaseSchemas",
                {"name": meta["schema"], "database": database["fullyQualifiedName"]},
            )
            columns = [
                {
                    "name": c["name"],
                    "dataType": datatype(c["type"]),
                    "dataTypeDisplay": c["type"],
                    "ordinalPosition": c["index"],
                }
                for c in node["columns"].values()
            ]
            if not columns:
                raise ValueError("Empty physical schema: " + node["unique_id"])
            table = api.put(
                "tables",
                {
                    "name": meta["name"],
                    "databaseSchema": schema["fullyQualifiedName"],
                    "columns": columns,
                    "owners": [{"id": team["id"], "type": "team"}],
                },
            )
            seen[table["fullyQualifiedName"]] = {
                "id": table["id"],
                "columns": [c["name"] for c in table["columns"]],
            }
        inventory[name] = seen
    (RUNTIME / "evidence/catalog-inventory.json").write_text(json.dumps(inventory, indent=2))
    print({name: len(tables) for name, tables in inventory.items()})


if __name__ == "__main__":
    from governance.local_auth import token

    os.environ["SA_OM_TOKEN"] = token()
    register()
