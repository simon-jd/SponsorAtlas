"""Account-wide metered credits from SNOWFLAKE.ACCOUNT_USAGE, printed as one JSON object.

Connects as COST_OBSERVER_SVC on SPONSORATLAS_XS (SNOWFLAKE_ACCOUNT and
SNOWFLAKE_COST_OBSERVER_PRIVATE_KEY_FILE from the environment; only the key path is passed on).
The total covers every project on the account, including SPCS. Exit 2 and a failed status when
it cannot be read; only the error type is reported.

    python -m scripts.snowflake_metering
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

SOURCE = "SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY"
SQL = f"SELECT COALESCE(SUM(CREDITS_USED), 0), MAX(USAGE_DATE) FROM {SOURCE}"
IDENTITY_SQL = "SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_WAREHOUSE()"
LAG_NOTE = (
    "ACCOUNT_USAGE lags by up to hours, so this total can be lower than the credits "
    "actually consumed so far."
)


def connect_options() -> dict:
    return {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": "COST_OBSERVER_SVC",
        "role": "COST_OBSERVER",
        "warehouse": "SPONSORATLAS_XS",
        "authenticator": "SNOWFLAKE_JWT",
        "private_key_file": str(
            Path(os.environ["SNOWFLAKE_COST_OBSERVER_PRIVATE_KEY_FILE"]).expanduser()
        ),
        "login_timeout": 15,
        "network_timeout": 120,
        "session_parameters": {
            "QUERY_TAG": "sponsoratlas:metering",
            "STATEMENT_TIMEOUT_IN_SECONDS": 90,
        },
    }


def read_total(connect) -> dict:
    connection = connect(**connect_options())
    try:
        cursor = connection.cursor()
        cursor.execute(SQL)
        total, latest = cursor.fetchone()
        cursor.execute(IDENTITY_SQL)
        account, user, warehouse = cursor.fetchone()
    finally:
        connection.close()
    return {
        "status": "ok",
        "credits_used_total": float(total),
        "latest_usage_date": str(latest) if latest else None,
        "identity": {"account": account, "user": user, "warehouse": warehouse},
        "queried_at": datetime.now(UTC).isoformat(),
        "source": SOURCE,
        "scope": "whole account, all services and projects including SPCS",
        "lag_note": LAG_NOTE,
    }


def main() -> int:
    try:
        import snowflake.connector

        result = read_total(snowflake.connector.connect)
    except Exception as exc:  # noqa: BLE001 -- report the type only, never connection details
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
