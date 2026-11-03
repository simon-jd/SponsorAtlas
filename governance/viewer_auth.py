"""Narrow local fixture authentication; responses and credentials are never logged."""

import base64
import grp
import json
import math
import os
import pwd
import secrets
import stat
import time
import uuid
from pathlib import Path

import requests

BASE = "http://127.0.0.1:18585/api/v1/"
REVISION = "a695261b19d48411a39502a5c5fc0b453729eb4e"
VIEWER = "sponsoratlas-viewer"
ADMIN = "sponsoratlas-admin"
EMAIL = VIEWER + "@example.invalid"
# The controller separately binds its native approval to the actual checkout.
# This process pins the source-derived root on its first validated FD open.
PROJECT_ROOT = Path(__file__).absolute().parents[1]
_project_anchor = None
FIELDS = "roles,teams,domains,personas,defaultPersona"
VIEWER_PATH = f"users/name/{VIEWER}?include=all&fields={FIELDS}"
IDENTITY_PATH = f"users/loggedInUser?fields={FIELDS}"
PATHS = {"system/version", "system/security/config", "system/settings/emailConfiguration",
         VIEWER_PATH, IDENTITY_PATH, "auth/login", "auth/logout", "users",
         "users/changePassword"}


class Failure(Exception):
    """Only fixed operation codes and HTTP status may cross this boundary."""
    def __init__(self, code, status=None):
        self.code = code if isinstance(code, str) and code.replace("_", "").isupper() else "INTERNAL"
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        super().__init__(self.code + (f":{self.status}" if self.status else ""))


def require(condition, code):
    if not condition:
        raise Failure(code)


def canonical_uuid(value):
    try:
        require(isinstance(value, str) and str(uuid.UUID(value)) == value, "IDENTITY")
        return value
    except (ValueError, AttributeError):
        raise Failure("IDENTITY") from None


def snapshot(value, admin=False):
    require(isinstance(value, dict), "IDENTITY")
    name = ADMIN if admin else VIEWER
    require(value.get("name") == name and value.get("email") == name + "@example.invalid",
            "IDENTITY")
    require(value.get("isAdmin") is admin and value.get("isBot") is False
            and value.get("deleted") is False and value.get("allowImpersonation", False) is False,
            "IDENTITY")
    result = {key: value.get(key) for key in
              ("name", "email", "isAdmin", "isBot", "deleted", "isEmailVerified")}
    result.update(id=canonical_uuid(value.get("id")), allowImpersonation=False)
    require(value.get("isEmailVerified") is None or type(value["isEmailVerified"]) is bool, "IDENTITY")
    for key in ("roles", "inheritedRoles", "teams", "domains", "personas", "inheritedPersonas"):
        refs = value.get(key)  # OM omits null/empty relationship fields.
        refs = [] if refs is None else refs
        require(isinstance(refs, list) and len(refs) <= 100, "ASSIGNMENTS")
        expected_type = {"roles": "role", "inheritedRoles": "role", "teams": "team",
                         "domains": "domain", "personas": "persona", "inheritedPersonas": "persona"}[key]
        normalized = []
        for ref in refs:
            require(isinstance(ref, dict) and ref.get("type") == expected_type,
                    "ASSIGNMENTS")
            normalized.append((canonical_uuid(ref.get("id")), ref["type"]))
        require(len(set(normalized)) == len(normalized), "ASSIGNMENTS")
        result[key] = sorted(normalized)
    persona = value.get("defaultPersona")
    require(persona is None or isinstance(persona, dict) and persona.get("type") == "persona", "ASSIGNMENTS")
    result["defaultPersona"] = canonical_uuid(persona.get("id")) if persona is not None else None
    return result


def credentials(raw):
    try:
        data = json.loads(raw)
        require(isinstance(data, dict) and set(data) == {"email", "password"}, "CREDENTIAL_SHAPE")
        password = data["password"]
        require(data["email"] == EMAIL and isinstance(password, str)
                and 32 <= len(password) <= 128 and password.isascii()
                and all(33 <= ord(c) <= 126 for c in password), "CREDENTIAL_SHAPE")
        return password
    except (ValueError, TypeError, UnicodeError):
        raise Failure("CREDENTIAL_SHAPE") from None


def credential_bytes(password):
    raw = json.dumps({"email": EMAIL, "password": password}).encode()
    credentials(raw)
    return raw


def private_primary_group():
    """Local NSS evidence only; this does not claim OS or directory isolation."""
    try:
        uid, gid = os.getuid(), os.getgid()
        account = pwd.getpwuid(uid)
        group = grp.getgrgid(gid)
        members = [entry.pw_uid for entry in pwd.getpwall() if entry.pw_gid == gid]
        require(account.pw_gid == gid and group.gr_gid == gid and members == [uid]
                and set(group.gr_mem) <= {account.pw_name}, "DIRECTORY_GROUP")
    except (KeyError, OSError):
        raise Failure("DIRECTORY_GROUP") from None


def directory_component(info, inside, anchor=False):
    global _project_anchor
    mode = stat.S_IMODE(info.st_mode)
    require(stat.S_ISDIR(info.st_mode) and not mode & 0o7000, "DIRECTORY_UNSAFE")
    if not inside:
        # Readonly external prefixes may have namespace-mapped owners.
        require(not mode & 0o022, "DIRECTORY_UNSAFE")
        return
    require(info.st_uid == os.getuid() and info.st_gid == os.getgid()
            and not mode & 0o002, "DIRECTORY_UNSAFE")
    if anchor:
        identity = (info.st_dev, info.st_ino, info.st_uid, info.st_gid)
        require(_project_anchor is None or identity == _project_anchor, "DIRECTORY_ANCHOR")
    if mode & 0o020:
        require(mode == 0o775, "DIRECTORY_UNSAFE")
        private_primary_group()
    if anchor:
        _project_anchor = identity


def directory_fd(path):
    """Open an anchored project path without following any component symlink."""
    path = Path(path).absolute()
    require(".." not in path.parts and path.is_relative_to(PROJECT_ROOT), "FILE_PATH")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        directory_component(os.fstat(fd), inside=False)
        for index, part in enumerate(path.parts[1:], start=1):
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
            anchor = index == len(PROJECT_ROOT.parts) - 1
            directory_component(os.fstat(fd), inside=index >= len(PROJECT_ROOT.parts) - 1,
                                anchor=anchor)
        return fd
    except BaseException:
        os.close(fd)
        raise


class Directory:
    """Small dirfd-based durable store. New task directories refuse reentry."""
    def __init__(self, path, create=False, private=True):
        self.path = Path(path).absolute()
        self.fd = None
        try:
            if create:
                parent = directory_fd(self.path.parent)
                try:
                    os.mkdir(self.path.name, mode=0o700, dir_fd=parent)
                    os.fsync(parent)
                finally:
                    os.close(parent)
            self.fd = directory_fd(self.path)
            info = os.fstat(self.fd)
            require(info.st_uid == os.getuid() and info.st_gid == os.getgid() and
                    (not private or stat.S_IMODE(info.st_mode) == 0o700), "DIRECTORY_MODE")
            if create:
                os.fsync(self.fd)
        except (OSError, Failure):
            self.close()
            raise Failure("FILE_SAFETY") from None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    @staticmethod
    def name(name):
        require(isinstance(name, str) and name not in ("", ".", "..")
                and "/" not in name and "\0" not in name, "FILE_PATH")

    def exists(self, name):
        self.name(name)
        try:
            os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def read(self, name, limit=4096):
        self.name(name)
        fd = None
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and info.st_gid == os.getgid()
                    and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
                    and info.st_size <= limit, "FILE_SAFETY")
            raw = os.read(fd, limit + 1)
            require(len(raw) <= limit and len(raw) == info.st_size, "FILE_SIZE")
            return raw
        except OSError:
            raise Failure("FILE_SAFETY") from None
        finally:
            if fd is not None:
                os.close(fd)

    def write(self, name, raw, replace=False):
        self.name(name)
        require(isinstance(raw, bytes) and len(raw) <= 65536, "FILE_SIZE")
        temporary = ".pending-" + secrets.token_hex(12)
        fd = None
        try:
            if self.exists(name):
                require(replace, "FILE_EXISTS")
                self.read(name, 65536)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=self.fd)
            with os.fdopen(fd, "wb") as stream:
                fd = None
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            else:
                os.link(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd,
                        follow_symlinks=False)
                os.unlink(temporary, dir_fd=self.fd)
            os.fsync(self.fd)
        except OSError:
            raise Failure("FILE_DURABILITY") from None
        finally:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass


class Client:
    def __init__(self, token=None, session=None, deadline=None):
        self.session = session if session is not None else requests.Session()
        self.session.trust_env = False
        self.admin = token is not None
        if token is not None:
            self.session.headers["Authorization"] = "Bearer " + token
        self.deadline = deadline if deadline is not None else time.monotonic() + 90

    def close(self):
        self.session.cookies.clear()
        self.session.headers.pop("Authorization", None)
        self.session.close()

    def request(self, method, path, payload=None, expected=(200,), json_response=True):
        require(path in PATHS and method in {"GET", "POST", "PUT"}, "REQUEST_PATH")
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "DEADLINE")
        response = None
        try:
            kwargs = {"timeout": (min(3, remaining), min(10, remaining)),
                      "allow_redirects": False, "stream": True}
            if payload is not None:
                kwargs["json"] = payload
            response = self.session.request(method, BASE + path, **kwargs)
            status_code = response.status_code
            if status_code not in expected:
                raise Failure("HTTP_STATUS", status_code)
            if status_code in (401, 404) or not json_response:
                return status_code, None
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=8192):
                size += len(chunk)
                require(size <= 131072, "RESPONSE_SIZE")
                require(time.monotonic() < self.deadline, "DEADLINE")
                chunks.append(chunk)
            return status_code, json.loads(b"".join(chunks))
        except (requests.RequestException, ValueError, UnicodeError):
            raise Failure("TRANSPORT_OR_SCHEMA") from None
        finally:
            if response is not None:
                response.close()

    def preflight(self, *, check_identity=True):
        require(type(check_identity) is bool, "IDENTITY_CHECK")
        _, version = self.request("GET", "system/version")
        require(isinstance(version, dict) and version.get("version") == "2.0.6"
                and version.get("revision") == REVISION, "SERVER_VERSION")
        _, security = self.request("GET", "system/security/config")
        require(isinstance(security, dict), "PROVIDER")
        config = security.get("authenticationConfiguration", {})
        authorizer = security.get("authorizerConfiguration", {})
        require(isinstance(config, dict) and isinstance(authorizer, dict), "PROVIDER")
        require(config.get("provider") in {"basic", "openmetadata"}
                and config.get("forceSecureSessionCookie") is False
                and authorizer.get("useRolesFromProvider") is False, "PROVIDER")
        # Require explicit policy, rather than relying on server fallback defaults.
        for field, minimum in (("sessionExpiry", 3600), ("maxActiveSessionsPerUser", 1)):
            value = config.get(field)
            require(type(value) is int and minimum <= value <= 2147483647, "SESSION_POLICY")
        _, smtp = self.request("GET", "system/settings/emailConfiguration")
        require(isinstance(smtp, dict) and smtp.get("config_type") == "emailConfiguration"
                and isinstance(smtp.get("config_value"), dict)
                and smtp["config_value"].get("enableSmtpServer") is False, "SMTP")
        if check_identity:
            _, admin = self.request("GET", IDENTITY_PATH)
            snapshot(admin, admin=True)

    def viewer(self, missing=False):
        code, data = self.request("GET", VIEWER_PATH, expected=(200, 404) if missing else (200,))
        return None if code == 404 else snapshot(data)

    def login(self, password, expected):
        if self.admin:
            viewer = Client(deadline=self.deadline)
            try:
                return viewer.login(password, expected)
            finally:
                viewer.close()
        success = False
        try:
            code, data = self.request("POST", "auth/login", expected=(200, 401), payload={
                "email": EMAIL, "password": base64.b64encode(password.encode("utf-8")).decode("ascii")})
            if code == 401:
                return code
            success = True  # Even malformed success may have created a server session.
            require(isinstance(data, dict) and data.get("tokenType") == "Bearer"
                    and isinstance(data.get("accessToken"), str) and 0 < len(data["accessToken"]) <= 16384
                    and type(data.get("expiryDuration")) in (int, float)
                    and math.isfinite(data["expiryDuration"]) and data["expiryDuration"] > 0
                    and data.get("refreshToken") is None, "LOGIN_SCHEMA")
            cookies = [c for c in self.session.cookies if c.name == "OM_SESSION"]
            require(len(cookies) == 1 and cookies[0].domain == "127.0.0.1"
                    and cookies[0].path == "/" and not cookies[0].secure
                    and len(cookies[0].value) == 43 and cookies[0].value.isascii()
                    and cookies[0].value.replace("_", "").replace("-", "").isalnum()
                    and cookies[0].has_nonstandard_attr("HttpOnly")
                    and cookies[0].get_nonstandard_attr("SameSite") == "Lax",
                    "SESSION_COOKIE")
            self.session.headers["Authorization"] = "Bearer " + data["accessToken"]
            _, identity = self.request("GET", IDENTITY_PATH)
            require(snapshot(identity) == expected, "IDENTITY_PARITY")
            return 200
        finally:
            try:
                if success or any(c.name == "OM_SESSION" for c in self.session.cookies):
                    self.request("POST", "auth/logout", json_response=False)
            finally:
                self.session.headers.pop("Authorization", None)
                self.session.cookies.clear()

    def change_password(self, password, username=VIEWER):
        require(username in (VIEWER, ADMIN), "IDENTITY")
        self.request("PUT", "users/changePassword", payload={"username": username,
                     "newPassword": password, "confirmPassword": password, "requestType": "USER"},
                     json_response=False)


def ensure_viewer(api, runtime):
    """Future setup only: create once, or verify existing credentials without repair."""
    api.preflight()
    with Directory(runtime, private=False) as directory:
        existing = directory.exists("ui-credentials.json")
        if existing:
            password = credentials(directory.read("ui-credentials.json"))
            identity = api.viewer()
            require(api.login(password, identity) == 200, "EXPLICIT_REPAIR_REQUIRED")
            return
        require(api.viewer(missing=True) is None, "EXPLICIT_REPAIR_REQUIRED")
        with Directory(Path(runtime) / "viewer-initialize-pending", create=True) as pending:
            password = secrets.token_urlsafe(30) + "aA1!"
            raw = credential_bytes(password)
            pending.write("candidate-credentials.json", raw)
            _, data = api.request("POST", "users", expected=(201,), payload={
                "name": VIEWER, "email": EMAIL, "displayName": "SponsorAtlas local evidence viewer",
                "isAdmin": False, "isBot": False, "createPasswordType": "ADMIN_CREATE",
                "password": password, "confirmPassword": password})
            identity = snapshot(data)
            require(api.login(password, identity) == 200, "CREATE_LOGIN_FAILED")
            directory.write("ui-credentials.json", raw)
            pending.write("result.json", b'{"credential_published":true}')
