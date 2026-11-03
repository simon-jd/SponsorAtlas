"""Real, disposable, opt-in key-pair policy acceptance. No account or role creation."""

import os
import re
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
IDENTITIES = ("ADMIN", "TRANSFORMER", "OWNER_ANALYST", "NON_OWNER", "OWNER_PII", "PII_NON_OWNER")


@contextmanager
def policy_sessions():
    if os.getenv("RUN_SNOWFLAKE_POLICIES") != "1":
        pytest.skip(
            "OPEN: RUN_SNOWFLAKE_POLICIES=1 requires authorized Enterprise trial and dedicated identities"
        )
    required = ["SNOWFLAKE_TEST_ACCOUNT", "SNOWFLAKE_POLICY_OWNER_ROLE"] + [
        f"SNOWFLAKE_POLICY_{identity}_{field}"
        for identity in IDENTITIES
        for field in ("USER", "ROLE", "PRIVATE_KEY_FILE")
    ]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        pytest.fail("Opted in but missing settings: " + ", ".join(missing))
    users = [os.environ[f"SNOWFLAKE_POLICY_{i}_USER"].upper() for i in IDENTITIES]
    assert len(set(users)) == len(users), (
        "Use six distinct users; never repurpose CUBE_SVC as owner"
    )
    assert "CUBE_SVC" not in [
        os.environ["SNOWFLAKE_POLICY_OWNER_ANALYST_USER"].upper(),
        os.environ["SNOWFLAKE_POLICY_OWNER_PII_USER"].upper(),
    ]
    owner = os.environ["SNOWFLAKE_POLICY_OWNER_ROLE"]
    assert re.fullmatch(r"SPONSORATLAS_OWNER_[A-Z0-9_]+", owner)
    import jinja2
    import snowflake.connector

    with ExitStack() as stack:
        sessions = {}
        actual_users = set()
        for identity in IDENTITIES:
            prefix = f"SNOWFLAKE_POLICY_{identity}_"
            key = Path(os.environ[prefix + "PRIVATE_KEY_FILE"]).expanduser()
            assert key.is_file()
            options = {
                "account": os.environ["SNOWFLAKE_TEST_ACCOUNT"],
                "user": os.environ[prefix + "USER"],
                "role": os.environ[prefix + "ROLE"],
                "private_key_file": str(key),
                "authenticator": "SNOWFLAKE_JWT",
                "warehouse": "SPONSORATLAS_XS",
                "login_timeout": 30,
                "session_parameters": {
                    "QUERY_TAG": "sponsoratlas:task8:policies",
                    "STATEMENT_TIMEOUT_IN_SECONDS": 60,
                },
            }
            if os.getenv(prefix + "PRIVATE_KEY_PASSPHRASE"):
                options["private_key_file_pwd"] = os.environ[prefix + "PRIVATE_KEY_PASSPHRASE"]
            conn = stack.enter_context(snowflake.connector.connect(**options))
            conn.cursor().execute(
                "USE SECONDARY ROLES ALL"
                if identity in ("OWNER_ANALYST", "OWNER_PII", "PII_NON_OWNER")
                else "USE SECONDARY ROLES NONE"
            )
            sessions[identity] = conn
            role, user, owns, pii = (
                conn.cursor()
                .execute(
                    "SELECT CURRENT_ROLE(), CURRENT_USER(), IS_ROLE_IN_SESSION(%s), IS_ROLE_IN_SESSION('PII_READER')",
                    (owner,),
                )
                .fetchone()
            )
            actual_users.add(user)
            assert role == options["role"] and user.upper() == options["user"].upper()
            if identity not in ("ADMIN",):
                assert bool(owns) == identity.startswith("OWNER_")
                assert bool(pii) == (identity in ("OWNER_PII", "PII_NON_OWNER"))
            if identity in ("OWNER_ANALYST", "NON_OWNER"):
                assert role == "ANALYST"
            if identity == "TRANSFORMER":
                assert role == "TRANSFORMER"
        assert len(actual_users) == len(IDENTITIES), "Actual authenticated users must be distinct"
        wh = sessions["ADMIN"].cursor().execute("SHOW WAREHOUSES LIKE 'SPONSORATLAS_XS'")
        values = dict(zip([d[0] for d in wh.description], wh.fetchone()))
        assert values["size"] == "X-Small" and int(values["auto_suspend"]) == 60
        module = (
            jinja2.Environment()
            .from_string(
                (ROOT / "dbt/macros/private_applications.sql")
                .read_text()
                .split("{% materialization")[0]
            )
            .make_module()
        )
        table = "ANALYTICS.MARTS.SA_POLICY_TEST_" + uuid4().hex.upper()
        query = (
            "select 'synthetic-live-1', '"
            + owner
            + "', 'synthetic-job', 'synthetic-event', 'submitted', current_timestamp()::timestamp_ntz, 'synthetic@example.invalid', 1, current_timestamp()::timestamp_ntz, current_timestamp()::timestamp_ntz"
        )
        creator = sessions["TRANSFORMER"]
        created = False
        try:
            # Same macro as dbt; both first creation and refresh are protected before visibility.
            for replacing in (False, True):
                creator.cursor().execute(module.private_application_ddl(table, query, replacing))
                created = True
                assert creator.cursor().execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
                assert sessions["OWNER_ANALYST"].cursor().execute(
                    f"SELECT contact_email FROM {table}"
                ).fetchall() == [("***MASKED***",)]
                assert sessions["NON_OWNER"].cursor().execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone() == (0,)
            yield sessions, table
        finally:
            if created:
                creator.cursor().execute(f"DROP TABLE {table}")
