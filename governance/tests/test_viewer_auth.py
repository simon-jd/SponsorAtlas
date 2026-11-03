"""Invented offline auth contracts; never evidence of an actual server login."""

import base64
import json
import os
import socket
from unittest.mock import Mock

import pytest
import requests

from governance import viewer_auth as auth
from governance import viewer_auth_runner as runner

PASSWORD = "fictional-private-password-only-for-tests-aA1!"
VIEWER_ID = "00000000-0000-4000-8000-000000000001"
ADMIN_ID = "00000000-0000-4000-8000-000000000002"
ROLE_ID = "00000000-0000-4000-8000-000000000003"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", Mock(side_effect=AssertionError("NETWORK")))


def user(admin=False):
    name = auth.ADMIN if admin else auth.VIEWER
    return {"id": ADMIN_ID if admin else VIEWER_ID, "name": name, "email": name + "@example.invalid",
            "isAdmin": admin, "isBot": False, "deleted": False, "allowImpersonation": False,
            "isEmailVerified": True, "roles": [], "inheritedRoles": [], "teams": [], "domains": [],
            "personas": [], "inheritedPersonas": [], "defaultPersona": None}


class Response:
    def __init__(self, status=200, body=None, raw=None):
        self.status_code = status
        self.body = raw if raw is not None else json.dumps(body).encode()
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        self.closed = True


class Wire:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self.calls = []
        self.trust_env = True

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs, dict(self.headers)))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        if url.endswith("auth/login") and response.status_code == 200:
            self.cookies.set("OM_SESSION", "x" * 43, domain="127.0.0.1", path="/",
                             rest={"HttpOnly": None, "SameSite": "Lax"})
        if url.endswith("auth/logout"):
            self.cookies.clear()
        return response

    def close(self):
        pass


def client(responses):
    wire = Wire(responses)
    return auth.Client(session=wire), wire


def tokens():
    return {"accessToken": "fictional-session-token", "tokenType": "Bearer",
            "expiryDuration": 9999999999999, "refreshToken": None}


def projection(present=False, xmin="11"):
    return {"matches": 1, "checks": [{"id": VIEWER_ID, "name_matches": True, "email_matches": True,
            "is_admin": False, "is_bot": False, "deleted": False, "mechanism_present": present,
            "basic_type": present, "password_present": present, "auth_field_existed": present,
            "original_auth_absent_or_null": not present, "row_version": xmin}]}


def test_real_prepared_request_encoding_and_plain_password_separation():
    api, wire = client([Response(401), Response(body="ok")])
    assert api.login(PASSWORD, auth.snapshot(user())) == 401
    api.change_password(PASSWORD)
    login = requests.Request("POST", wire.calls[0][1], json=wire.calls[0][2]["json"]).prepare()
    change = requests.Request("PUT", wire.calls[1][1], json=wire.calls[1][2]["json"]).prepare()
    assert json.loads(login.body) == {"email": auth.EMAIL,
                                    "password": base64.b64encode(PASSWORD.encode()).decode()}
    assert json.loads(change.body) == {"username": auth.VIEWER, "newPassword": PASSWORD,
                                      "confirmPassword": PASSWORD, "requestType": "USER"}
    assert wire.calls[0][1] == auth.BASE + "auth/login"
    assert wire.calls[0][2]["allow_redirects"] is False
    assert wire.trust_env is False


def test_session_identity_and_logout_do_not_use_admin_token():
    api, wire = client([Response(body=tokens()), Response(body=user()), Response(body={})])
    assert api.login(PASSWORD, auth.snapshot(user())) == 200
    assert "Authorization" not in wire.calls[0][3]
    assert wire.calls[1][3]["Authorization"] == "Bearer fictional-session-token"
    assert wire.calls[2][1] == auth.BASE + "auth/logout"
    assert not wire.cookies and "Authorization" not in wire.headers


def test_admin_client_uses_a_separate_viewer_transport(monkeypatch):
    original = Wire([])
    viewer = Wire([Response(body=tokens()), Response(body=user()), Response(body={})])
    monkeypatch.setattr(auth.requests, "Session", lambda: viewer)
    api = auth.Client(token="fictional-admin-only", session=original)
    assert api.login(PASSWORD, auth.snapshot(user())) == 200
    assert not original.calls
    assert all("fictional-admin-only" not in str(c[3]) for c in viewer.calls)


@pytest.mark.parametrize("key,value", [("roles", False), ("teams", ""),
                                      ("defaultPersona", {}), ("isEmailVerified", "yes")])
def test_malformed_assignment_shape_fails(key, value):
    with pytest.raises(auth.Failure):
        auth.snapshot(user() | {key: value})


@pytest.mark.parametrize("changes", [{"email": "other@example.invalid"}, {"isAdmin": True},
                                   {"isBot": True}, {"deleted": True}, {"id": "not-a-uuid"},
                                   {"allowImpersonation": True}])
def test_wrong_identity_fails(changes):
    data = user() | changes
    with pytest.raises(auth.Failure):
        auth.snapshot(data)


def test_assignments_order_independent_but_drift_rejected():
    before = user()
    before["roles"] = [{"id": ROLE_ID, "type": "role"}]
    expected = auth.snapshot(before)
    assert expected != auth.snapshot(user())
    api, wire = client([Response(body=tokens()), Response(body=user()), Response(body={})])
    with pytest.raises(auth.Failure, match="IDENTITY_PARITY"):
        api.login(PASSWORD, expected)
    assert len(wire.calls) == 3 and not wire.cookies


@pytest.mark.parametrize("response", [Response(302, raw=PASSWORD.encode()),
                                     Response(500, raw=PASSWORD.encode()),
                                     Response(raw=PASSWORD.encode()),
                                     Response(raw=b"x" * 131073),
                                     requests.RequestException(PASSWORD)])
def test_error_secrecy_bounds_and_no_retry(response, capsys):
    api, wire = client([response])
    with pytest.raises(auth.Failure) as error:
        api.request("GET", "system/version")
    assert PASSWORD not in str(error.value)
    assert len(wire.calls) == 1
    assert PASSWORD not in repr(capsys.readouterr())


@pytest.mark.parametrize("bad", [{"tokenType": "Other"}, {"accessToken": ""},
                                {"refreshToken": "secret-refresh"}, {"expiryDuration": True}])
def test_malformed_success_logs_out_without_claiming_identity(bad):
    api, wire = client([Response(body=tokens() | bad), Response(body={})])
    with pytest.raises(auth.Failure):
        api.login(PASSWORD, auth.snapshot(user()))
    assert wire.calls[-1][1].endswith("auth/logout")
    assert not wire.cookies


def config():
    return {"authenticationConfiguration": {"provider": "basic", "forceSecureSessionCookie": False,
            "maxActiveSessionsPerUser": 5, "sessionExpiry": 604800},
            "authorizerConfiguration": {"useRolesFromProvider": False}}


@pytest.mark.parametrize("setting,value", [("provider", "ldap"),
                                          ("forceSecureSessionCookie", True)])
def test_provider_and_cookie_mismatch_prevent_user_calls(setting, value):
    security = config()
    security["authenticationConfiguration"][setting] = value
    api, wire = client([Response(body={"version": "2.0.6", "revision": auth.REVISION}),
                        Response(body=security)])
    with pytest.raises(auth.Failure):
        api.preflight()
    assert len(wire.calls) == 2


@pytest.mark.parametrize("role_sync,smtp", [(True, False), (False, True)])
def test_role_sync_and_smtp_prevent_mutation(role_sync, smtp):
    security = config()
    security["authorizerConfiguration"]["useRolesFromProvider"] = role_sync
    api, wire = client([Response(body={"version": "2.0.6", "revision": auth.REVISION}),
                        Response(body=security),
                        Response(body={"config_type": "emailConfiguration",
                                       "config_value": {"enableSmtpServer": smtp}})])
    with pytest.raises(auth.Failure):
        api.preflight()
    assert all(call[0] == "GET" for call in wire.calls)


def test_secure_file_modes_durability_and_exclusive_once(tmp_path, monkeypatch):
    events = []
    real_sync = os.fsync
    def sync(fd):
        events.append(os.readlink(f"/proc/self/fd/{fd}"))
        real_sync(fd)
    monkeypatch.setattr(os, "fsync", sync)
    location = tmp_path / "private"
    with auth.Directory(location, create=True) as directory:
        directory.write("credential.json", auth.credential_bytes(PASSWORD))
        assert directory.read("credential.json") == auth.credential_bytes(PASSWORD)
        assert (location.stat().st_mode & 0o777) == 0o700
        assert (location / "credential.json").stat().st_mode & 0o777 == 0o600
        with pytest.raises(auth.Failure, match="^FILE_EXISTS$"):
            directory.write("credential.json", b"replacement")
    assert events[0] == str(tmp_path)
    assert events[-1] == str(location)
    assert any(".pending-" in p for p in events)
    with pytest.raises(auth.Failure, match="^FILE_SAFETY$"):
        auth.Directory(location, create=True)


def test_fsync_failure_never_publishes_credential(tmp_path, monkeypatch):
    with auth.Directory(tmp_path / "private", create=True) as directory:
        monkeypatch.setattr(os, "fsync", Mock(side_effect=OSError("synthetic")))
        with pytest.raises(auth.Failure, match="^FILE_DURABILITY$"):
            directory.write("credentials.json", auth.credential_bytes(PASSWORD))
        assert not (directory.path / "credentials.json").exists()


def test_new_directory_parent_sync_failure_refuses_reentry(tmp_path, monkeypatch):
    location = tmp_path / "private"
    monkeypatch.setattr(os, "fsync", Mock(side_effect=OSError("synthetic")))
    with pytest.raises(auth.Failure, match="^FILE_SAFETY$"):
        auth.Directory(location, create=True)
    assert location.exists() and not list(location.iterdir())
    with pytest.raises(auth.Failure, match="^FILE_SAFETY$"):
        auth.Directory(location, create=True)


@pytest.mark.parametrize("kind", ["symlink", "mode", "hardlink", "oversize"])
def test_unsafe_credential_files_refused(tmp_path, kind):
    with auth.Directory(tmp_path / "private", create=True) as directory:
        file = directory.path / "credentials.json"
        if kind == "symlink":
            file.symlink_to(tmp_path / "missing")
        else:
            directory.write("credentials.json", auth.credential_bytes(PASSWORD))
            if kind == "mode":
                file.chmod(0o644)
            elif kind == "hardlink":
                os.link(file, directory.path / "second")
            else:
                file.write_bytes(b"x" * 4097)
        with pytest.raises(auth.Failure, match="^FILE_SAFETY$"):
            directory.read("credentials.json")


def test_symlink_parent_refused(tmp_path):
    (tmp_path / "real").mkdir(mode=0o700)
    # Prove the parent and real leaf pass before rejecting the symlink itself.
    with auth.Directory(tmp_path / "real"):
        pass
    (tmp_path / "link").symlink_to(tmp_path / "real")
    with pytest.raises(auth.Failure, match="^FILE_SAFETY$"):
        auth.Directory(tmp_path / "link")


@pytest.mark.parametrize("data", [{"email": "wrong", "password": PASSWORD},
                                {"email": auth.EMAIL, "password": "short"},
                                {"email": auth.EMAIL, "password": PASSWORD, "token": "no"}])
def test_credentials_schema_fails_without_value_in_error(data):
    with pytest.raises(auth.Failure) as error:
        auth.credentials(json.dumps(data).encode())
    assert PASSWORD not in str(error.value)


class FixtureAPI:
    def __init__(self, baseline=401, drift=False, ambiguous=False):
        self.events = []
        self.baseline = baseline
        self.drift = drift
        self.ambiguous = ambiguous
        self.changed = False

    def preflight(self):
        self.events.append("preflight")

    def viewer(self, missing=False):
        data = user()
        if self.changed and self.drift:
            data["roles"] = [{"id": ROLE_ID, "type": "role"}]
        return auth.snapshot(data)

    def login(self, password, expected):
        assert password == PASSWORD
        self.events.append("login")
        return 200 if self.changed else self.baseline

    def change_password(self, password):
        assert password == PASSWORD
        self.events.append("change")
        self.changed = True
        if self.ambiguous:
            raise auth.Failure("TRANSPORT")


def repair(tmp_path, api, before=None, after=None):
    states = iter([before or projection(), after or projection(True, "12")])
    with auth.Directory(tmp_path / "attempt", create=True) as directory:
        return runner.repair(api, PASSWORD, auth.credential_bytes(PASSWORD), directory,
                             lambda: runner.parse_projection(json.dumps(next(states)).encode()))


def test_null_repair_uses_one_baseline_change_and_post_login(tmp_path):
    api = FixtureAPI()
    result = repair(tmp_path, api)
    assert api.events == ["preflight", "login", "change", "login"]
    assert result["api_gate"] == "PASS" and result["role_team_domain_parity"] is True
    assert result["cause"] == "MISSING_BASIC_MECHANISM"
    assert result["session_policy_validated"] is True
    saved = json.loads((tmp_path / "attempt/auth-recovery.json").read_text())
    assert saved["row_version_after"] == "12" and saved["original_value"] is None
    assert PASSWORD not in json.dumps(result) and "token" not in json.dumps(saved)


def test_existing_basic_failure_never_rotates(tmp_path):
    api = FixtureAPI()
    with pytest.raises(auth.Failure, match="EXISTING_AUTH_UNRESOLVED"):
        repair(tmp_path, api, projection(True))
    assert api.events == ["preflight", "login"]


def test_existing_healthy_no_mutation(tmp_path):
    api = FixtureAPI(baseline=200)
    result = repair(tmp_path, api, projection(True))
    assert result["api_gate"] == "PASS_NO_REPAIR"
    assert "change" not in api.events


@pytest.mark.parametrize("option", ["drift", "ambiguous"])
def test_failed_repair_is_not_retried_or_rolled_back(tmp_path, option):
    api = FixtureAPI(**{option: True})
    code = "IDENTITY_PARITY" if option == "drift" else "TRANSPORT"
    with pytest.raises(auth.Failure, match=f"^{code}$"):
        repair(tmp_path, api)
    assert api.events.count("change") == 1
    assert api.events.count("login") == 1
    state = json.loads((tmp_path / "attempt/state.json").read_text())
    assert state["mutation_attempted"] is True


@pytest.mark.parametrize("field,value,code", [
    pytest.param("is_admin", True, "DB_IDENTITY", id="admin"),
    pytest.param("deleted", True, "DB_IDENTITY", id="deleted"),
    pytest.param("row_version", "11'; SQL", "ROW_VERSION", id="xmin"),
    pytest.param("id", ADMIN_ID, "DB_IDENTITY", id="identity"),
    pytest.param("password_present", True, "AUTH_STATE", id="password-state"),
])
def test_projection_wrong_or_inconsistent_state_no_mutation(tmp_path, field, value, code):
    before = projection()
    before["checks"][0][field] = value
    api = FixtureAPI()
    with pytest.raises(auth.Failure, match=f"^{code}$"):
        repair(tmp_path, api, before)
    assert "change" not in api.events


def test_recovery_sql_only_field_cas_no_native_executor():
    receipt = {"viewer_id": VIEWER_ID, "auth_field_existed": False, "original_value": None,
               "row_version_before": "11", "row_version_after": "12"}
    sql = runner.recovery_sql(receipt)
    assert "xmin = '12'::xid" in sql and "GET DIAGNOSTICS" in sql
    assert "affected <> 1" in sql and "RAISE EXCEPTION 'CAS_MISMATCH'" in sql
    assert "json::jsonb - 'authenticationMechanism'" in sql
    assert "DELETE FROM" not in sql and PASSWORD not in sql
    receipt["auth_field_existed"] = True
    assert "jsonb_set" in runner.recovery_sql(receipt)
    receipt["original_value"] = {"password": "must-not-backup"}
    with pytest.raises(auth.Failure):
        runner.recovery_sql(receipt)


def test_native_cli_requires_explicit_approval_without_loading_secrets(monkeypatch):
    monkeypatch.setattr(runner, "run_native", Mock(side_effect=AssertionError("LIVE")))
    assert runner.main([]) == 2


def test_future_creation_posts_then_checks_before_credential_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda _: PASSWORD[:-4])
    admin, wire = client([Response(404), Response(201, body=user())])
    login = Mock(return_value=200)
    monkeypatch.setattr(admin, "preflight", lambda: None)
    monkeypatch.setattr(admin, "login", login)
    auth.ensure_viewer(admin, tmp_path)
    assert [call[0] for call in wire.calls] == ["GET", "POST"]
    body = wire.calls[1][2]["json"]
    assert body["isAdmin"] is False and body["isBot"] is False
    assert body["createPasswordType"] == "ADMIN_CREATE"
    assert auth.credentials((tmp_path / "ui-credentials.json").read_bytes()) == PASSWORD
    assert login.call_count == 1


def test_failed_creation_login_keeps_only_private_pending_value(tmp_path, monkeypatch):
    admin, _ = client([Response(404), Response(201, body=user())])
    monkeypatch.setattr(admin, "preflight", lambda: None)
    monkeypatch.setattr(admin, "login", lambda *_: 401)
    with pytest.raises(auth.Failure, match="CREATE_LOGIN_FAILED"):
        auth.ensure_viewer(admin, tmp_path)
    assert not (tmp_path / "ui-credentials.json").exists()
    assert (tmp_path / "viewer-initialize-pending/candidate-credentials.json").exists()


def test_existing_credentials_are_verified_and_failed_login_does_not_register(tmp_path, monkeypatch):
    from governance import initialize_catalog
    with auth.Directory(tmp_path, private=False) as directory:
        directory.write("ui-credentials.json", auth.credential_bytes(PASSWORD))
        directory.write("admin-credential.json", b"{}")
    api = FixtureAPI()
    monkeypatch.setattr(initialize_catalog, "RUNTIME", tmp_path)
    monkeypatch.setattr(initialize_catalog, "token", lambda: "fictional-admin-token")
    monkeypatch.setattr(initialize_catalog, "Client", lambda **_: api)
    monkeypatch.setattr(initialize_catalog, "register", Mock())
    api.close = lambda: None
    with pytest.raises(auth.Failure, match="^EXPLICIT_REPAIR_REQUIRED$"):
        initialize_catalog.initialize()
    initialize_catalog.register.assert_not_called()
    assert "login" in api.events
