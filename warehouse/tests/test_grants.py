"""Real grant tests: opt-in creates small disposable nonproduction fixtures.

No password/token authentication, no Terraform apply, and no production data access.
See warehouse/README.md for every required environment variable and authorization.
"""

import os
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.snowflake, pytest.mark.integration]


@pytest.fixture(scope="module")
def sessions():
    if os.getenv("RUN_SNOWFLAKE_GRANTS") != "1":
        pytest.skip("live Snowflake: set RUN_SNOWFLAKE_GRANTS=1 with key-pair credentials")
    required = [
        "SNOWFLAKE_TEST_ACCOUNT",
        "SNOWFLAKE_TEST_ADMIN_USER",
        "SNOWFLAKE_TEST_ADMIN_ROLE",
    ] + [
        f"SNOWFLAKE_TEST_{role}_PRIVATE_KEY_FILE"
        for role in ("ADMIN", "LOADER", "TRANSFORMER", "ANALYST")
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.skip("missing key-pair test settings: " + ", ".join(missing))
    key_paths = {
        role: Path(os.environ[f"SNOWFLAKE_TEST_{role}_PRIVATE_KEY_FILE"]).expanduser()
        for role in ("ADMIN", "LOADER", "TRANSFORMER", "ANALYST")
    }
    for role, path in key_paths.items():
        if not path.is_file():
            pytest.skip(f"missing private key file for {role}")
    connector = pytest.importorskip("snowflake.connector")
    defaults = {"LOADER": "AIRBYTE_SVC", "TRANSFORMER": "DBT_SVC", "ANALYST": "CUBE_SVC"}
    with ExitStack() as stack:
        result = {}
        for role in ("ADMIN", "LOADER", "TRANSFORMER", "ANALYST"):
            prefix = f"SNOWFLAKE_TEST_{role}_"
            options = {
                "account": os.environ["SNOWFLAKE_TEST_ACCOUNT"],
                "user": os.getenv(prefix + "USER", defaults.get(role)),
                "role": os.environ[prefix + "ROLE"] if role == "ADMIN" else role,
                "warehouse": os.getenv("SNOWFLAKE_TEST_WAREHOUSE", "SPONSORATLAS_XS"),
                "authenticator": "SNOWFLAKE_JWT",
                "private_key_file": str(key_paths[role]),
                "session_parameters": {
                    "QUERY_TAG": "sponsoratlas:task1:grants",
                    "STATEMENT_TIMEOUT_IN_SECONDS": 60,
                },
                "login_timeout": 30,
            }
            passphrase = os.getenv(prefix + "PRIVATE_KEY_PASSPHRASE")
            if passphrase:
                options["private_key_file_pwd"] = passphrase
            conn = stack.enter_context(connector.connect(**options))
            # Do not let inherited secondary roles hide isolation failures.
            conn.cursor().execute("USE SECONDARY ROLES NONE")
            assert conn.cursor().execute("SELECT CURRENT_ROLE()").fetchone() == (options["role"],)
            result[role] = conn
        yield result


@pytest.fixture(scope="module")
def objects(sessions):
    admin = sessions["ADMIN"]
    suffix = uuid4().hex.upper()
    tables = [f"RAW.{schema}.SA_GRANT_TEST_{suffix}" for schema in ("LCA", "USCIS", "BLS", "JOBS")]
    marts = f"ANALYTICS.MARTS.SA_GRANT_TEST_{suffix}"
    view = f"ANALYTICS.MARTS.SA_GRANT_VIEW_{suffix}"
    created = []
    try:
        for table in tables + [marts]:
            admin.cursor().execute(f"CREATE TRANSIENT TABLE {table} (ID INTEGER)")
            created.append(("TABLE", table))
            admin.cursor().execute(f"INSERT INTO {table} VALUES (1)")
            assert admin.cursor().execute(f"SELECT ID FROM {table}").fetchall() == [(1,)]
        admin.cursor().execute(f"CREATE VIEW {view} AS SELECT ID FROM {marts}")
        created.append(("VIEW", view))
        yield tables, marts, view
    finally:
        for kind, name in reversed(created):
            admin.cursor().execute(f"DROP {kind} IF EXISTS {name}")


def assert_denied(connection, sql, *, allowed_errnos):
    from snowflake.connector.errors import ProgrammingError

    with pytest.raises(ProgrammingError) as caught:
        connection.cursor().execute(sql)
    assert caught.value.errno in allowed_errnos, (
        f"expected authorization denial, got errno {caught.value.errno}, "
        f"SQLSTATE {caught.value.sqlstate}"
    )


def test_analyst_cannot_read_raw(sessions, objects):
    tables, _, _ = objects
    for table in tables:
        # Positive control proves the fixture exists and RAW SELECT works for dbt.
        assert sessions["TRANSFORMER"].cursor().execute(f"SELECT ID FROM {table}").fetchall() == [
            (1,)
        ]
        assert_denied(sessions["ANALYST"], f"SELECT ID FROM {table}", allowed_errnos={2003, 3001})


def test_analyst_reads_marts(sessions, objects):
    _, marts, view = objects
    for target in (marts, view):
        assert sessions["ANALYST"].cursor().execute(f"SELECT ID FROM {target}").fetchall() == [(1,)]
    assert_denied(sessions["ANALYST"], f"INSERT INTO {marts} VALUES (2)", allowed_errnos={3001})


def test_transformer_cannot_write_raw(sessions, objects):
    tables, _, _ = objects
    for table in tables:
        assert sessions["TRANSFORMER"].cursor().execute(f"SELECT ID FROM {table}").fetchall() == [
            (1,)
        ]
        assert_denied(
            sessions["TRANSFORMER"], f"INSERT INTO {table} VALUES (2)", allowed_errnos={3001}
        )
        # LOADER can write the same object; rollback leaves other tests deterministic.
        loader = sessions["LOADER"]
        loader.cursor().execute("BEGIN")
        try:
            loader.cursor().execute(f"INSERT INTO {table} VALUES (2)")
        finally:
            loader.cursor().execute("ROLLBACK")
