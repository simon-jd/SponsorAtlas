"""Offline security contracts for the declared Snowflake grant graph."""

import json
from pathlib import Path

import hcl2
import pytest

TERRAFORM = Path(__file__).parents[1] / "terraform"


def normalize(value):
    """python-hcl2 8 preserves literal quotes; support both declared 7/8 releases."""
    if isinstance(value, dict):
        return {normalize(k): normalize(v) for k, v in value.items() if k != "__is_block__"}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, str) and value.startswith('"') and value.endswith('"'):
        return json.loads(value)
    return value


@pytest.fixture(scope="module")
def config():
    paths = sorted(TERRAFORM.glob("*.tf"))
    assert paths, "Snowflake Terraform implementation is missing"
    result = {"resource": {}, "locals": {}, "provider": [], "terraform": []}
    for path in paths:
        with path.open() as handle:
            parsed = normalize(hcl2.load(handle))
        for block in parsed.get("resource", []):
            for kind, resources in block.items():
                for name, body in resources.items():
                    result["resource"][kind, name] = body
        for block in parsed.get("locals", []):
            result["locals"].update(block)
        result["provider"].extend(parsed.get("provider", []))
        result["terraform"].extend(parsed.get("terraform", []))
    return result


def test_only_expected_resources_and_no_escalation(config):
    resources = config["resource"]
    assert set(resources) == {
        ("snowflake_database", "data"),
        ("snowflake_row_access_policy", "application_owner"),
        ("snowflake_masking_policy", "contact_email"),
        ("snowflake_tag", "pii"),
        ("snowflake_tag", "sensitivity"),
        ("snowflake_grant_privileges_to_account_role", "governance_binding"),
        ("snowflake_schema", "data"),
        ("snowflake_warehouse", "compute"),
        ("snowflake_account_role", "functional"),
        ("snowflake_account_role", "application_owner"),
        ("snowflake_grant_privileges_to_account_role", "warehouse_usage"),
        ("snowflake_grant_privileges_to_account_role", "database_usage"),
        ("snowflake_grant_privileges_to_account_role", "schema_access"),
        ("snowflake_grant_privileges_to_account_role", "existing_objects"),
        ("snowflake_grant_privileges_to_account_role", "future_objects"),
        ("snowflake_service_user", "service"),
        ("snowflake_grant_account_role", "service"),
        ("snowflake_authentication_policy", "key_pair_only"),
        ("snowflake_user_authentication_policy_attachment", "service"),
    }
    for (kind, _), body in resources.items():
        if kind == "snowflake_grant_privileges_to_account_role":
            assert not body.get("all_privileges")
            assert not body.get("with_grant_option")
            assert not body.get("on_account")
        if kind == "snowflake_grant_account_role":
            assert "parent_role_name" not in body
            assert body["user_name"] == "${snowflake_service_user.service[each.key].name}"


def test_schema_and_database_inventory(config):
    schemas = config["locals"]["schemas"]
    assert {(v["database"], v["name"]) for v in schemas.values()} == {
        ("RAW", name) for name in ("LCA", "USCIS", "BLS", "JOBS")
    } | {("ANALYTICS", name) for name in ("STAGING", "INTERMEDIATE", "MARTS")}
    database = config["resource"]["snowflake_database", "data"]
    assert database["for_each"] == '${toset(["RAW", "ANALYTICS"])}'
    assert database["name"] == "${each.key}"
    schema = config["resource"]["snowflake_schema", "data"]
    assert schema["for_each"] == "${local.schemas}"
    assert schema["database"] == "${snowflake_database.data[each.value.database].name}"
    assert schema["with_managed_access"] == "true"


def test_grant_matrix_is_connected_to_resources(config):
    resources = config["resource"]
    role = resources["snowflake_account_role", "functional"]
    assert role["for_each"] == "${toset(local.roles)}"
    assert role["name"] == "${each.key}"
    for name, matrix in (("database_usage", "database_access"), ("schema_access", "schema_access")):
        grant = resources["snowflake_grant_privileges_to_account_role", name]
        assert grant["for_each"] == "${local." + matrix + "}"
        assert (
            grant["account_role_name"]
            == "${snowflake_account_role.functional[each.value.role].name}"
        )
    database = resources["snowflake_grant_privileges_to_account_role", "database_usage"]
    assert database["privileges"] == ["USAGE"]
    assert database["on_account_object"] == [
        {
            "object_type": "DATABASE",
            "object_name": "${snowflake_database.data[each.value.database].name}",
        }
    ]
    schema = resources["snowflake_grant_privileges_to_account_role", "schema_access"]
    assert schema["privileges"] == "${each.value.privileges}"
    assert schema["on_schema"] == [
        {"schema_name": "${snowflake_schema.data[each.value.schema].fully_qualified_name}"}
    ]
    warehouse = resources["snowflake_grant_privileges_to_account_role", "warehouse_usage"]
    assert warehouse["for_each"] == '${toset(["LOADER", "TRANSFORMER", "ANALYST"])}'
    assert warehouse["account_role_name"] == "${snowflake_account_role.functional[each.key].name}"
    assert warehouse["privileges"] == ["USAGE"]
    assert warehouse["on_account_object"] == [
        {"object_type": "WAREHOUSE", "object_name": "${snowflake_warehouse.compute.name}"}
    ]


def test_official_provider_is_exactly_pinned(config):
    required = config["terraform"][0]["required_providers"][0]["snowflake"]
    assert required == {"source": "snowflakedb/snowflake", "version": "= 2.22.0"}
    with (TERRAFORM / ".terraform.lock.hcl").open() as handle:
        lock = normalize(hcl2.load(handle))
    pinned = lock["provider"][0]["registry.terraform.io/snowflakedb/snowflake"]
    assert pinned["version"] == "2.22.0"
    assert any(value.startswith("h1:") for value in pinned["hashes"])


def test_role_security_matrix(config):
    local = config["locals"]
    assert set(local["roles"]) == {"LOADER", "TRANSFORMER", "ANALYST", "PII_READER"}
    schemas = local["schemas"]
    expected_db = {
        ("LOADER", "RAW"),
        ("TRANSFORMER", "RAW"),
        ("TRANSFORMER", "ANALYTICS"),
        ("ANALYST", "ANALYTICS"),
    }
    assert {(v["role"], v["database"]) for v in local["database_access"].values()} == expected_db
    seen = set()
    for grant in local["schema_access"].values():
        role, key = grant["role"], grant["schema"]
        db, name = schemas[key]["database"], schemas[key]["name"]
        seen.add((role, db, name))
        expected = {"USAGE"}
        if role == "LOADER" and db == "RAW":
            expected |= {"CREATE TABLE", "CREATE STAGE", "CREATE FILE FORMAT"}
        elif role == "TRANSFORMER" and db == "ANALYTICS":
            expected |= {"CREATE TABLE", "CREATE VIEW"}
        assert set(grant["privileges"]) == expected
    raw = {("RAW", s) for s in ("LCA", "USCIS", "BLS", "JOBS")}
    analytics = {("ANALYTICS", s) for s in ("STAGING", "INTERMEDIATE", "MARTS")}
    assert seen == {(r, db, s) for r in ("LOADER", "TRANSFORMER") for db, s in raw} | {
        ("TRANSFORMER", db, s) for db, s in analytics
    } | {("ANALYST", "ANALYTICS", "MARTS")}
    actual = set()
    for grant in local["object_access"].values():
        role, key, kind = grant["role"], grant["schema"], grant["kind"]
        db, name = schemas[key]["database"], schemas[key]["name"]
        privileges = set(grant["privileges"])
        expected = {"SELECT"}
        if kind == "TABLES" and (
            (role == "LOADER" and db == "RAW") or (role == "TRANSFORMER" and db == "ANALYTICS")
        ):
            expected |= {"INSERT", "UPDATE", "DELETE", "TRUNCATE"}
        assert privileges == expected
        actual.add((role, db, name, kind))
    expected = {("LOADER", db, s, "TABLES") for db, s in raw}
    expected |= {
        ("TRANSFORMER", db, s, k) for db, s in raw | analytics for k in ("TABLES", "VIEWS")
    }
    expected |= {("ANALYST", "ANALYTICS", "MARTS", k) for k in ("TABLES", "VIEWS")}
    assert actual == expected
    for name, mode in (("existing_objects", "all"), ("future_objects", "future")):
        grant = config["resource"]["snowflake_grant_privileges_to_account_role", name]
        assert grant["for_each"] == "${local.object_access}"
        assert (
            grant["account_role_name"]
            == "${snowflake_account_role.functional[each.value.role].name}"
        )
        assert grant["privileges"] == "${each.value.privileges}"
        target = grant["on_schema_object"][0][mode][0]
        assert (
            target["in_schema"]
            == "${snowflake_schema.data[each.value.schema].fully_qualified_name}"
        )
        assert target["object_type_plural"] == "${each.value.kind}"


def test_key_pair_only_service_and_provider(config):
    assert config["locals"]["service_users"] == {
        "AIRBYTE_SVC": "LOADER",
        "DBT_SVC": "TRANSFORMER",
        "CUBE_SVC": "ANALYST",
    }
    service = config["resource"]["snowflake_service_user", "service"]
    assert service["for_each"] == "${local.service_users}"
    assert service["rsa_public_key"] == "${var.service_public_keys[each.key]}"
    assert service["default_role"] == "${snowflake_account_role.functional[each.value].name}"
    assert service["default_secondary_roles_option"] == "NONE"
    assert service["disabled"] == "${local.native_users_disabled}"
    assert not {"password", "default_workload_identity"} & service.keys()
    policy = config["resource"]["snowflake_authentication_policy", "key_pair_only"]
    assert policy["authentication_methods"] == ["KEYPAIR"]
    attachment = config["resource"]["snowflake_user_authentication_policy_attachment", "service"]
    assert attachment["for_each"] == "${local.service_policy_users}"
    assert (
        config["locals"]["service_policy_users"]
        == '${var.native_phase == "CREATE_DISABLED" ? {} : local.service_users}'
    )
    assert attachment["lifecycle"] == [{"prevent_destroy": True}]
    assert attachment["user_name"] == "${snowflake_service_user.service[each.key].name}"
    assert (
        attachment["authentication_policy_name"]
        == "${snowflake_authentication_policy.key_pair_only.fully_qualified_name}"
    )
    provider = config["provider"][0]["snowflake"]
    assert provider["authenticator"] == "SNOWFLAKE_JWT"
    assert provider["warehouse"] == "${local.native_provider_warehouse}"
    assert not {"password", "token"} & provider.keys()


def test_warehouse_cost_contract(config):
    warehouse = config["resource"]["snowflake_warehouse", "compute"]
    assert warehouse["warehouse_size"] == "XSMALL"
    assert warehouse["auto_suspend"] == 60
    assert warehouse["auto_resume"] == "true"
    assert warehouse["initially_suspended"] is True
    assert warehouse["resource_monitor"] == "${local.native_resource_monitor}"
    assert warehouse["min_cluster_count"] == warehouse["max_cluster_count"] == 1
