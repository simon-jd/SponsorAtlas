"""Account defaults as a regression test (live part: RUN_SNOWFLAKE_GRANTS=1, same settings as
test_grants.py).

A new Snowflake trial account grants objects to PUBLIC that Terraform does not create:
SNOWFLAKE_LEARNING_ROLE (USAGE and OPERATE on SNOWFLAKE_LEARNING_WH, which has no resource
monitor) and USAGE on SYSTEM$STREAMLIT_NOTEBOOK_WH. The user revoked both on 2026-10-30
(warehouse/README.md, manual setup). Each service user must see exactly the project warehouse
and hold exactly its own role plus PUBLIC, so a reappearing default grant fails here.
"""

import json

import pytest

from warehouse.tests.test_grants import sessions  # noqa: F401 - shared fixture

WAREHOUSE = "SPONSORATLAS_XS"
SERVICE_USERS = {"LOADER": "AIRBYTE_SVC", "TRANSFORMER": "DBT_SVC", "ANALYST": "CUBE_SVC"}


def visible_warehouses(cursor):
    cursor.execute("SHOW WAREHOUSES")
    name = [d[0].lower() for d in cursor.description].index("name")
    return sorted(row[name] for row in cursor.fetchall())


def available_roles(cursor):
    (text,) = cursor.execute("SELECT CURRENT_AVAILABLE_ROLES()").fetchone()
    return sorted(json.loads(text))


def default_violations(role, warehouses, roles):
    problems = []
    if warehouses != [WAREHOUSE]:
        problems.append(f"{role} sees warehouses {warehouses}, expected only {WAREHOUSE}")
    if roles != sorted({role, "PUBLIC"}):
        problems.append(f"{role} holds roles {roles}, expected only {role} and PUBLIC")
    return problems


def test_default_violations_flag_trial_grants():
    assert default_violations("ANALYST", [WAREHOUSE], ["ANALYST", "PUBLIC"]) == []
    assert default_violations(
        "ANALYST", ["SNOWFLAKE_LEARNING_WH", WAREHOUSE], ["ANALYST", "PUBLIC", "SNOWFLAKE_LEARNING_ROLE"]
    ) == [
        "ANALYST sees warehouses ['SNOWFLAKE_LEARNING_WH', 'SPONSORATLAS_XS'], expected only SPONSORATLAS_XS",
        "ANALYST holds roles ['ANALYST', 'PUBLIC', 'SNOWFLAKE_LEARNING_ROLE'], expected only ANALYST and PUBLIC",
    ]
    assert default_violations("LOADER", [], ["LOADER"]) != []


@pytest.mark.snowflake
@pytest.mark.integration
def test_service_users_see_only_the_project_warehouse_and_their_role(sessions):  # noqa: F811
    observed, problems = {}, []
    for role, user in SERVICE_USERS.items():
        cursor = sessions[role].cursor()
        assert cursor.execute("SELECT CURRENT_USER()").fetchone() == (user,)
        warehouses, roles = visible_warehouses(cursor), available_roles(cursor)
        observed[user] = {"warehouses": warehouses, "available_roles": roles}
        problems += default_violations(role, warehouses, roles)
    print("account defaults:", json.dumps(observed))
    assert problems == []
