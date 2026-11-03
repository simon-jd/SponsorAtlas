"""Local DDL checks plus explicitly opted-in real Snowflake behavior (never simulated)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_policy_ddl_has_no_unprotected_creation_or_transformer_bypass():
    import jinja2

    text = (ROOT / "dbt/macros/private_applications.sql").read_text()
    # Exercise the actual reusable SQL macro, including replacement and first creation.
    macro_text = text.split("{% materialization")[0]
    module = jinja2.Environment().from_string(macro_text).make_module()
    for replace in (False, True):
        sql = module.private_application_ddl("ANALYTICS.MARTS.PROBE", "select 1", replace)
        assert sql.lower().count("create") == 1
        assert "WITH ROW ACCESS POLICY ANALYTICS.MARTS.APPLICATION_OWNER_V1 ON (owner_role)" in sql
        assert "WITH MASKING POLICY ANALYTICS.MARTS.CONTACT_EMAIL_V1" in sql
        assert "WITH TAG" in sql
        assert ("COPY GRANTS" in sql) == replace
        assert sql.index("ROW ACCESS POLICY") < sql.index("as (")
    policies = (ROOT / "warehouse/terraform/policies.tf").read_text()
    assert "IS_ROLE_IN_SESSION(owner_role)" in policies
    assert "IS_ROLE_IN_SESSION('PII_READER')" in policies
    assert "TRANSFORMER" not in policies
    assert policies.count("prevent_destroy = true") >= 2


@pytest.fixture(scope="module")
def live():
    from governance.snowflake_acceptance import policy_sessions

    with policy_sessions() as context:
        yield context


@pytest.mark.snowflake
@pytest.mark.integration
def test_analyst_sees_masked_email(live):
    sessions, table = live
    rows = (
        sessions["OWNER_ANALYST"]
        .cursor()
        .execute(f"SELECT application_id, contact_email FROM {table}")
        .fetchall()
    )
    assert rows == [("synthetic-live-1", "***MASKED***")]


@pytest.mark.snowflake
@pytest.mark.integration
def test_non_owner_sees_zero_application_rows(live):
    sessions, table = live
    for identity in ("NON_OWNER", "PII_NON_OWNER", "TRANSFORMER"):
        assert sessions[identity].cursor().execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (
            0,
        )


@pytest.mark.snowflake
@pytest.mark.integration
def test_owner_pii_reader_sees_plain_email(live):
    sessions, table = live
    assert sessions["OWNER_PII"].cursor().execute(
        f"SELECT contact_email FROM {table}"
    ).fetchall() == [("synthetic@example.invalid",)]


def test_apply_grants_are_object_scoped_and_owner_roles_have_no_service_grants():
    import hcl2

    from warehouse.tests.test_terraform_contracts import normalize

    with (ROOT / "warehouse/terraform/roles.tf").open() as handle:
        roles = normalize(hcl2.load(handle))
    resources = {
        (kind, name): value
        for block in roles["resource"]
        for kind, values in block.items()
        for name, value in values.items()
    }
    apply = resources["snowflake_grant_privileges_to_account_role", "governance_binding"]
    assert apply["privileges"] == ["APPLY"]
    assert apply["account_role_name"] == '${snowflake_account_role.functional["TRANSFORMER"].name}'
    assert apply["on_schema_object"] == [
        {"object_type": "${each.value.kind}", "object_name": "${each.value.name}"}
    ]
    assert not any(k[0] == "snowflake_grant_account_role" for k in resources)
    owner = resources["snowflake_account_role", "application_owner"]
    assert owner["for_each"] == "${var.application_owner_roles}"
