"""Offline tests for the live-Snowflake helpers: fake cursors, no account access."""

import json

import pytest

from scripts import snowflake_live


class Cursor:
    def __init__(self, rows, columns):
        self.rows, self.description, self.sql = rows, [(c,) for c in columns], []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        return self

    def fetchall(self):
        return self.rows


MONITOR_COLUMNS = [
    "name", "credit_quota", "used_credits", "remaining_credits", "level", "frequency",
    "start_time", "end_time", "notify_at", "suspend_at", "suspend_immediately_at", "owner",
]  # fmt: skip


def monitor_row(used="0.07"):
    return [
        "SPONSORATLAS_BOOTSTRAP_RM", "5.00", used, "4.93", "WAREHOUSE", "NEVER",
        "2026-10-30 10:00:00.000 -0700", "2026-11-23 16:00:00.000 -0800", None, "80%", "100%",
        "SPONSORATLAS_DEPLOY",
    ]  # fmt: skip


def test_read_monitor_uses_show_only_and_parses_numbers():
    cursor = Cursor([monitor_row()], MONITOR_COLUMNS)
    row = snowflake_live.read_monitor(cursor)
    assert cursor.sql == [("SHOW RESOURCE MONITORS LIKE 'SPONSORATLAS_BOOTSTRAP_RM'", None)]
    assert row["used_credits"] == 0.07 and row["credit_quota"] == 5.0
    assert row["suspend_at"] == "80%" and "owner" not in row


def test_read_monitor_rejects_missing_or_ambiguous_monitor():
    with pytest.raises(ValueError, match="exactly one"):
        snowflake_live.read_monitor(Cursor([], MONITOR_COLUMNS))
    with pytest.raises(ValueError, match="exactly one"):
        snowflake_live.read_monitor(Cursor([monitor_row(), monitor_row()], MONITOR_COLUMNS))


def test_record_appends_labelled_lines_and_flags_the_stop_threshold(tmp_path):
    out = tmp_path / "credits.jsonl"
    first = snowflake_live.record("before item 1", {"used_credits": 0.07}, out)
    second = snowflake_live.record("after item 2", {"used_credits": 3.1}, out)
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert [line["label"] for line in lines] == ["before item 1", "after item 2"]
    assert first["stop"] is False and second["stop"] is True
    assert "lag" in lines[0]["note"]


def test_connect_options_pass_the_key_path_and_never_read_the_key(tmp_path, monkeypatch):
    key = tmp_path / "k.p8"
    key.write_text("not read")
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "example-account")
    monkeypatch.setenv("SNOWFLAKE_LOADER_PRIVATE_KEY_FILE", str(key))
    options = snowflake_live.connect_options("LOADER")
    assert options["user"] == "AIRBYTE_SVC" and options["role"] == "LOADER"
    assert options["private_key_file"] == str(key)
    assert options["authenticator"] == "SNOWFLAKE_JWT"
    assert options["warehouse"] == "SPONSORATLAS_XS"
    assert "password" not in options and "private_key" not in options


def test_deploy_options_have_no_warehouse(tmp_path, monkeypatch):
    key = tmp_path / "k.p8"
    key.write_text("")
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "example-account")
    monkeypatch.setenv("SNOWFLAKE_DEPLOY_USER", "DEPLOY_USER")
    monkeypatch.setenv("SNOWFLAKE_DEPLOY_ROLE", "DEPLOY_ROLE")
    monkeypatch.setenv("SNOWFLAKE_DEPLOY_PRIVATE_KEY_FILE", str(key))
    options = snowflake_live.connect_options("DEPLOY")
    assert options["user"] == "DEPLOY_USER" and options["role"] == "DEPLOY_ROLE"
    assert "warehouse" not in options


def test_connect_options_refuse_missing_settings(monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    with pytest.raises(KeyError):
        snowflake_live.connect_options("ANALYST")
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "example-account")
    monkeypatch.setenv("SNOWFLAKE_ANALYST_PRIVATE_KEY_FILE", "/nonexistent/key.p8")
    with pytest.raises(FileNotFoundError):
        snowflake_live.connect_options("ANALYST")


def test_secondary_roles_disabled_accepts_the_live_format():
    # 2026-10-31: CUBE_SVC with DEFAULT_SECONDARY_ROLES=() after USE SECONDARY ROLES NONE
    # returned {"roles":"","value":""}; "NONE" is accepted as well.
    assert snowflake_live.secondary_roles_disabled('{"roles":"","value":""}')
    assert snowflake_live.secondary_roles_disabled('{"roles":"","value":"NONE"}')
    for text in [
        '{"roles":"PII_READER","value":""}', '{"roles":"","value":"ALL"}',
        '{"roles":["ANALYST"],"value":"NONE"}', '{"value":""}', '{}', "[]", "not json", None,
    ]:  # fmt: skip
        assert not snowflake_live.secondary_roles_disabled(text), text
