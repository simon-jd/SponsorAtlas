"""Shared helpers for opt-in live Snowflake runs: key-pair connections and the credit monitor.

Every setting comes from the environment: SNOWFLAKE_ACCOUNT plus, per identity,
SNOWFLAKE_<ROLE>_PRIVATE_KEY_FILE (LOADER, TRANSFORMER, ANALYST, DEPLOY) and, for DEPLOY,
SNOWFLAKE_DEPLOY_USER / SNOWFLAKE_DEPLOY_ROLE. Only the key *path* is passed to the
connector; this module never opens the key file.

    python -m scripts.snowflake_live monitor --label "before item 1" --output credits.jsonl

reads SPONSORATLAS_BOOTSTRAP_RM with the deploy identity (SHOW only, no warehouse) and
appends one JSON line. Exit 3 once USED_CREDITS reaches --stop-at (default 3.0).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

MONITOR = "SPONSORATLAS_BOOTSTRAP_RM"
WAREHOUSE = "SPONSORATLAS_XS"
SERVICE_USERS = {"LOADER": "AIRBYTE_SVC", "TRANSFORMER": "DBT_SVC", "ANALYST": "CUBE_SVC"}
MONITOR_FIELDS = (
    "name", "credit_quota", "used_credits", "remaining_credits", "level", "frequency",
    "start_time", "end_time", "suspend_at", "suspend_immediately_at",
)  # fmt: skip
NUMERIC = {"credit_quota", "used_credits", "remaining_credits"}
STOP_AT = 3.0
LAG_NOTE = (
    "USED_CREDITS from SHOW RESOURCE MONITORS; Snowflake updates it with a lag "
    "(minutes to hours), so per-item deltas are lower bounds until it settles."
)


def secondary_roles_disabled(text) -> bool:
    """True when CURRENT_SECONDARY_ROLES() reports no secondary role.

    Snowflake returns {"roles":"","value":""} for a session without secondary roles
    (observed 2026-10-31 after USE SECONDARY ROLES NONE); "NONE" is accepted too.
    """
    try:
        state = json.loads(text)
    except (TypeError, ValueError):
        return False
    return (
        isinstance(state, dict)
        and state.get("value") in ("", "NONE")
        and state.get("roles") in ("", [])
    )


def connect_options(role: str, *, query_tag: str = "sponsoratlas:live") -> dict:
    key = Path(os.environ[f"SNOWFLAKE_{role}_PRIVATE_KEY_FILE"]).expanduser()
    if not key.is_file():
        raise FileNotFoundError(f"missing private key file for {role}")
    options = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "authenticator": "SNOWFLAKE_JWT",
        "private_key_file": str(key),
        "login_timeout": 30,
        "session_parameters": {"QUERY_TAG": query_tag},
    }
    if role == "DEPLOY":
        # SHOW commands need no warehouse; never resume one with the deploy identity.
        options["user"] = os.environ["SNOWFLAKE_DEPLOY_USER"]
        options["role"] = os.environ["SNOWFLAKE_DEPLOY_ROLE"]
    else:
        options["user"] = SERVICE_USERS[role]
        options["role"] = role
        options["warehouse"] = os.environ.get("SNOWFLAKE_WAREHOUSE", WAREHOUSE)
    return options


def connect(role: str, **kwargs):
    import snowflake.connector

    return snowflake.connector.connect(**connect_options(role, **kwargs))


def read_monitor(cursor) -> dict:
    cursor.execute(f"SHOW RESOURCE MONITORS LIKE '{MONITOR}'")
    columns = [d[0].lower() for d in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    if len(rows) != 1 or rows[0].get("name") != MONITOR:
        raise ValueError(f"expected exactly one {MONITOR}")
    return {
        k: (float(rows[0][k]) if k in NUMERIC and rows[0][k] is not None else rows[0].get(k))
        for k in MONITOR_FIELDS
    }


def record(label: str, monitor: dict, output: Path, *, stop_at: float = STOP_AT) -> dict:
    line = {
        "label": label,
        "read_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        **monitor,
        "stop": monitor["used_credits"] >= stop_at,
        "note": LAG_NOTE,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(line, default=str) + "\n")
    return line


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    monitor = sub.add_parser("monitor")
    monitor.add_argument("--label", required=True)
    monitor.add_argument("--output", type=Path, required=True)
    monitor.add_argument("--stop-at", type=float, default=STOP_AT)
    args = parser.parse_args(argv)
    with connect("DEPLOY", query_tag="sponsoratlas:monitor") as conn:
        line = record(args.label, read_monitor(conn.cursor()), args.output, stop_at=args.stop_at)
    print(json.dumps(line, default=str))
    if line["stop"]:
        print(f"STOP: USED_CREDITS {line['used_credits']} >= {args.stop_at}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
