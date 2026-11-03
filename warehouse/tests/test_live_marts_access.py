"""Opt-in checks on the real dbt tables (RUN_SNOWFLAKE_GRANTS=1, same settings as
test_grants.py): CUBE_SVC reads MARTS but not RAW/STAGING/INTERMEDIATE, where the
governance policies are attached, and whether they take effect for ANALYST without
PII_READER. Probe tables are UUID-named, synthetic and dropped in `finally`.
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from warehouse.tests.test_grants import assert_denied, sessions  # noqa: F401 - shared fixture

pytestmark = [pytest.mark.snowflake, pytest.mark.integration]
ROOT = Path(__file__).resolve().parents[2]
MARTS = ["DIM_DATE", "DIM_EMPLOYER", "DIM_GEOGRAPHY", "DIM_OCCUPATION",
         "FACT_LCA_CASE", "FACT_JOB_POSTING_DAILY", "FACT_WAGE_BENCHMARK"]  # fmt: skip
DENIED = ["RAW.LCA.LCA_FY2025_Q1", "RAW.LCA.DIMENSIONS_INPUT_EMPLOYER_SOURCES",
          "ANALYTICS.STAGING.STG_LCA", "ANALYTICS.INTERMEDIATE.INT_LCA_CASE_CANDIDATES",
          "ANALYTICS.INTERMEDIATE.SNAP_EMPLOYER"]  # fmt: skip
POLICIES = ["ANALYTICS.MARTS.APPLICATION_OWNER_V1", "ANALYTICS.MARTS.CONTACT_EMAIL_V1"]


def references(cursor, policy):
    rows = cursor.execute(
        "SELECT POLICY_KIND, REF_DATABASE_NAME, REF_SCHEMA_NAME, REF_ENTITY_NAME, REF_COLUMN_NAME"
        " FROM TABLE(ANALYTICS.INFORMATION_SCHEMA.POLICY_REFERENCES(POLICY_NAME => %s))",
        (policy,),
    ).fetchall()
    return [list(r) for r in rows]


def test_cube_reads_every_mart_and_nothing_upstream(sessions):  # noqa: F811
    analyst = sessions["ANALYST"]
    counts = {
        t: analyst.cursor().execute(f"SELECT COUNT(*) FROM ANALYTICS.MARTS.{t}").fetchone()[0]
        for t in MARTS
    }
    print("CUBE_SVC MARTS counts:", json.dumps(counts))
    assert all(n > 0 for n in counts.values())
    for relation in DENIED:
        # Positive control: the relation exists and dbt can read it.
        assert sessions["TRANSFORMER"].cursor().execute(
            f"SELECT COUNT(*) FROM {relation}"
        ).fetchone()[0] > 0
        assert_denied(analyst, f"SELECT COUNT(*) FROM {relation}", allowed_errnos={2003, 3001})
    print("CUBE_SVC denied:", DENIED)


def test_policy_references_on_the_real_build(sessions):  # noqa: F811
    cursor = sessions["TRANSFORMER"].cursor()
    found = {p: references(cursor, p) for p in POLICIES}
    print("POLICY_REFERENCES before probes:", json.dumps(found))
    # The only consumer in code is fact_my_applications (disabled unless
    # enable_private_applications=true); the default build attaches nothing.
    assert all(not refs or all(r[3] == "FACT_MY_APPLICATIONS" for r in refs) for refs in found.values())


def test_policies_take_effect_for_analyst_without_pii_reader(sessions):  # noqa: F811
    import jinja2

    module = jinja2.Environment().from_string(
        (ROOT / "dbt/macros/private_applications.sql").read_text().split("{% materialization")[0]
    ).make_module()
    suffix = uuid4().hex.upper()
    protected = f"ANALYTICS.MARTS.SA_POLICY_PROBE_{suffix}"
    masked = f"ANALYTICS.MARTS.SA_MASK_PROBE_{suffix}"
    query = (
        "select 'synthetic-probe-1', 'SPONSORATLAS_OWNER_PROBE_NONE', 'synthetic-job',"
        " 'synthetic-event', 'submitted', current_timestamp()::timestamp_ntz,"
        " 'synthetic@example.invalid', 1, current_timestamp()::timestamp_ntz,"
        " current_timestamp()::timestamp_ntz"
    )
    dbt, analyst = sessions["TRANSFORMER"], sessions["ANALYST"]
    created = []
    try:
        # The dbt macro's single CREATE binds RAP, mask and tags (needs TRANSFORMER APPLY).
        dbt.cursor().execute(module.private_application_ddl(protected, query, False))
        created.append(protected)
        dbt.cursor().execute(
            f"CREATE TRANSIENT TABLE {masked} (contact_email VARCHAR WITH MASKING POLICY"
            " ANALYTICS.MARTS.CONTACT_EMAIL_V1) AS SELECT 'synthetic@example.invalid'"
        )
        created.append(masked)
        refs = {p: references(dbt.cursor(), p) for p in POLICIES}
        print("POLICY_REFERENCES with probes:", json.dumps(refs))
        assert any(r[3] == protected.split(".")[2] for r in refs[POLICIES[0]])
        assert any(r[3] == protected.split(".")[2] and r[4] == "CONTACT_EMAIL"
                   for r in refs[POLICIES[1]])  # fmt: skip
        # No owner role is active for anyone: the row access policy hides the row.
        for name, conn in (("CUBE_SVC/ANALYST", analyst), ("DBT_SVC/TRANSFORMER", dbt)):
            n = conn.cursor().execute(f"SELECT COUNT(*) FROM {protected}").fetchone()[0]
            print(f"{name} rows in RAP probe:", n)
            assert n == 0
        # Mask-only probe: ANALYST without PII_READER gets the masked value.
        value = analyst.cursor().execute(f"SELECT contact_email FROM {masked}").fetchall()
        print("CUBE_SVC/ANALYST email in mask probe:", value)
        assert value == [("***MASKED***",)]
        assert analyst.cursor().execute("SELECT IS_ROLE_IN_SESSION('PII_READER')").fetchone() == (
            False,
        )
    finally:
        for table in reversed(created):
            dbt.cursor().execute(f"DROP TABLE IF EXISTS {table}")
