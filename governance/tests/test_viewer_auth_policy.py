"""Invented session-policy preflight evidence; never actual server evidence."""

import json
import socket
from unittest.mock import Mock

import pytest

from governance import viewer_auth as auth
from governance import viewer_auth_runner as runner
from governance.tests.test_viewer_auth import PASSWORD, Response, client, config, projection, user


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", Mock(side_effect=AssertionError("NETWORK")))


def policy_client(security, complete=False):
    responses = [Response(body={"version": "2.0.6", "revision": auth.REVISION}),
                 Response(body=security)]
    if complete:
        responses.extend([
            Response(body={"config_type": "emailConfiguration",
                           "config_value": {"enableSmtpServer": False}}),
            Response(body=user(admin=True)),
        ])
    return client(responses)


BAD_POLICY = [
    pytest.param(field, kind, id=f"{label}-{kind}")
    for field, label in (("sessionExpiry", "expiry"), ("maxActiveSessionsPerUser", "limit"))
    for kind in ("missing", "null", "bool", "string", "float", "object", "under-min", "overflow")
]


@pytest.mark.parametrize("field,kind", BAD_POLICY)
def test_session_policy_rejects_before_repair(tmp_path, field, kind):
    security = config()
    value = {"null": None, "bool": True, "string": "604800", "float": 604800.0,
             "object": {}, "under-min": 3599 if field == "sessionExpiry" else 0,
             "overflow": 2147483648}
    if kind == "missing":
        del security["authenticationConfiguration"][field]
    else:
        security["authenticationConfiguration"][field] = value[kind]
    api, wire = policy_client(security)
    inspect = Mock(side_effect=AssertionError("DATABASE"))
    with auth.Directory(tmp_path) as directory:
        with pytest.raises(auth.Failure, match="^SESSION_POLICY$"):
            runner.repair(api, PASSWORD, auth.credential_bytes(PASSWORD), directory, inspect)
        result = runner.failure_result(directory, "SESSION_POLICY")
    assert result["session_policy_validated"] is False
    assert result["mutation_attempted"] is False
    assert [call[0] for call in wire.calls] == ["GET", "GET"]
    inspect.assert_not_called()


@pytest.mark.parametrize("expiry,limit", [
    pytest.param(3600, 1, id="minimum"),
    pytest.param(604800, 5, id="default-sized"),
    pytest.param(2147483647, 2147483647, id="integer-max"),
])
def test_session_policy_accepts_explicit_bounds(expiry, limit):
    security = config()
    security["authenticationConfiguration"].update(sessionExpiry=expiry, maxActiveSessionsPerUser=limit)
    original = json.dumps(security, sort_keys=True)
    api, wire = policy_client(security, complete=True)
    api.preflight()
    assert [call[0] for call in wire.calls] == ["GET"] * 4
    assert json.dumps(security, sort_keys=True) == original


def test_legacy_only_policy_is_rejected():
    security = config()
    del security["authenticationConfiguration"]["sessionExpiry"]
    security["authenticationConfiguration"]["oidcConfiguration"] = {"sessionExpiry": 604800}
    api, wire = policy_client(security)
    with pytest.raises(auth.Failure, match="^SESSION_POLICY$"):
        api.preflight()
    assert len(wire.calls) == 2


def test_initializer_blocks_on_session_policy(tmp_path, monkeypatch):
    security = config()
    del security["authenticationConfiguration"]["maxActiveSessionsPerUser"]
    api, wire = policy_client(security)
    storage = Mock(side_effect=AssertionError("CREDENTIAL_READ"))
    monkeypatch.setattr(auth, "Directory", storage)
    with pytest.raises(auth.Failure, match="^SESSION_POLICY$"):
        auth.ensure_viewer(api, tmp_path)
    storage.assert_not_called()
    assert [call[0] for call in wire.calls] == ["GET", "GET"]


def test_policy_fact_is_retained_without_values_on_failure(tmp_path, monkeypatch):
    api, wire = policy_client(config(), complete=True)
    monkeypatch.setattr(api, "viewer", lambda: auth.snapshot(user()))
    monkeypatch.setattr(api, "login", lambda *_: 401)
    change = Mock(side_effect=auth.Failure("TRANSPORT"))
    monkeypatch.setattr(api, "change_password", change)
    with auth.Directory(tmp_path) as directory:
        with pytest.raises(auth.Failure, match="^TRANSPORT$"):
            runner.repair(api, PASSWORD, auth.credential_bytes(PASSWORD), directory,
                          lambda: runner.parse_projection(json.dumps(projection()).encode()))
        result = runner.failure_result(directory, "TRANSPORT")
    assert result["session_policy_validated"] is True
    assert result["mutation_attempted"] is True and result["mutation_ambiguous"] is True
    assert change.call_count == 1 and len(wire.calls) == 4
    public = json.dumps(result)
    assert "sessionExpiry" not in public and "maxActiveSessionsPerUser" not in public
    assert "604800" not in public and PASSWORD not in public
