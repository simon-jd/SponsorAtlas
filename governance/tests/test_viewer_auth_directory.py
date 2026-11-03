"""Invented directory trust boundaries; no private data or native services."""

import json
import os
import socket
import stat
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from governance import viewer_auth as auth
from governance import viewer_auth_runner as runner
from governance.tests.test_viewer_auth import PASSWORD, FixtureAPI, projection


def entry(mode=0o775, uid=None, gid=None, dev=None, ino=None):
    return SimpleNamespace(
        st_mode=stat.S_IFDIR | mode,
        st_uid=os.getuid() if uid is None else uid,
        st_gid=os.getgid() if gid is None else gid,
        st_dev=101 if dev is None else dev,
        st_ino=202 if ino is None else ino,
    )


@pytest.fixture(autouse=True)
def invented_nss_and_no_network(monkeypatch):
    monkeypatch.setattr(auth, "_project_anchor", None)
    account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(), pw_name="fixture")
    group = SimpleNamespace(gr_gid=os.getgid(), gr_mem=[])
    monkeypatch.setattr(auth.pwd, "getpwuid", lambda _: account)
    monkeypatch.setattr(auth.pwd, "getpwall", lambda: [account])
    monkeypatch.setattr(auth.grp, "getgrgid", lambda _: group)
    monkeypatch.setattr(socket.socket, "connect", Mock(side_effect=AssertionError("NETWORK")))
    return account, group


def test_readonly_mapped_prefix_is_allowed():
    auth.directory_component(entry(mode=0o755, uid=65534, gid=65534), inside=False)


def test_singleton_primary_group_project_0775_is_allowed():
    auth.directory_component(entry(), inside=True, anchor=True)


@pytest.mark.parametrize("case", [
    "external-group-write", "external-world-write", "inside-world-write",
    "inside-wrong-uid", "inside-wrong-gid", "inside-group-mode",
    "special-mode", "anchor-device", "anchor-inode",
])
def test_unsafe_directory_metadata_is_rejected(case, monkeypatch):
    monkeypatch.setattr(auth, "_project_anchor", (101, 202, os.getuid(), os.getgid()))
    info, inside, anchor = entry(), True, False
    code = "DIRECTORY_UNSAFE"
    if case == "external-group-write":
        inside = False
    elif case == "external-world-write":
        info, inside = entry(mode=0o757), False
    elif case == "inside-world-write":
        info = entry(mode=0o777)
    elif case == "inside-wrong-uid":
        info = entry(uid=os.getuid() + 1)
    elif case == "inside-wrong-gid":
        info = entry(gid=os.getgid() + 1)
    elif case == "inside-group-mode":
        info = entry(mode=0o770)
    elif case == "special-mode":
        info = entry(mode=0o2775)
    elif case == "anchor-device":
        info, anchor, code = entry(dev=-1), True, "DIRECTORY_ANCHOR"
    elif case == "anchor-inode":
        info, anchor, code = entry(ino=-1), True, "DIRECTORY_ANCHOR"
    with pytest.raises(auth.Failure, match=f"^{code}$"):
        auth.directory_component(info, inside=inside, anchor=anchor)


@pytest.mark.parametrize("case", [
    "extra-primary", "extra-explicit", "missing-primary", "wrong-account-gid", "lookup-failure",
])
def test_nonprivate_primary_group_is_rejected(case, invented_nss_and_no_network, monkeypatch):
    account, group = invented_nss_and_no_network
    if case == "extra-primary":
        other = SimpleNamespace(pw_uid=os.getuid() + 1, pw_gid=os.getgid())
        monkeypatch.setattr(auth.pwd, "getpwall", lambda: [account, other])
    elif case == "extra-explicit":
        group.gr_mem = ["other"]
    elif case == "missing-primary":
        monkeypatch.setattr(auth.pwd, "getpwall", list)
    elif case == "wrong-account-gid":
        account.pw_gid += 1
    else:
        monkeypatch.setattr(auth.grp, "getgrgid", Mock(side_effect=KeyError("invented")))
    with pytest.raises(auth.Failure, match="^DIRECTORY_GROUP$"):
        auth.directory_component(entry(), inside=True)


def test_walk_checks_every_component_with_nofollow(tmp_path, monkeypatch):
    real_open, opened = os.open, []
    def tracked_open(path, flags, *args, **kwargs):
        opened.append((path, flags))
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", tracked_open)
    fd = auth.directory_fd(tmp_path)
    os.close(fd)
    assert [path for path, _ in opened] == ["/", *tmp_path.parts[1:]]
    assert all(flags & os.O_NOFOLLOW and flags & os.O_DIRECTORY for _, flags in opened)


def test_walk_rejects_anchor_drift_before_private_creation(tmp_path, monkeypatch):
    with auth.Directory(tmp_path):
        pass
    monkeypatch.setattr(auth, "_project_anchor", (-1, -1, os.getuid(), os.getgid()))
    with pytest.raises(auth.Failure, match="^FILE_SAFETY$"):
        auth.Directory(tmp_path / "not-created", create=True)
    assert not (tmp_path / "not-created").exists()


def test_walk_rechecks_nss_on_each_open(tmp_path, monkeypatch):
    with auth.Directory(tmp_path):
        pass
    monkeypatch.setattr(auth.grp, "getgrgid", lambda _: SimpleNamespace(
        gr_gid=os.getgid(), gr_mem=["new-other-member"]))
    with pytest.raises(auth.Failure, match="^FILE_SAFETY$"):
        auth.Directory(tmp_path)


def test_outside_project_refused_before_open(monkeypatch):
    opener = Mock(side_effect=AssertionError("SHOULD_NOT_OPEN"))
    monkeypatch.setattr(os, "open", opener)
    with pytest.raises(auth.Failure, match="^FILE_PATH$"):
        auth.directory_fd("/tmp/invented-outside-project")
    opener.assert_not_called()


def test_private_directory_still_requires_0700(tmp_path):
    location = tmp_path / "public-mode"
    location.mkdir(mode=0o755)
    with auth.Directory(location, private=False):
        pass
    with pytest.raises(auth.Failure, match="^FILE_SAFETY$"):
        auth.Directory(location)


def test_private_file_wrong_gid_is_rejected(tmp_path, monkeypatch):
    with auth.Directory(tmp_path) as directory:
        directory.write("invented.json", b"{}")
        real_stat = os.fstat
        def changed_gid(fd):
            info = real_stat(fd)
            if stat.S_ISREG(info.st_mode):
                return SimpleNamespace(st_mode=info.st_mode, st_uid=info.st_uid,
                                       st_gid=os.getgid() + 1, st_nlink=1, st_size=2)
            return info
        monkeypatch.setattr(os, "fstat", changed_gid)
        with pytest.raises(auth.Failure, match="^FILE_SAFETY$"):
            directory.read("invented.json")


@pytest.mark.parametrize("case,code,succeeded,ambiguous", [
    pytest.param("ambiguous", "TRANSPORT", False, True, id="mutation-timeout"),
    pytest.param("drift", "IDENTITY_PARITY", True, False, id="postcheck-failure"),
])
def test_public_failure_keeps_durable_mutation_facts(
        tmp_path, monkeypatch, case, code, succeeded, ambiguous):
    # Replace every live boundary before exercising the entry point with invented data.
    api = FixtureAPI(**{case: True})
    api.close = Mock()
    runtime, public = tmp_path / "runtime", tmp_path / "public"
    runtime.mkdir(mode=0o700)
    public.mkdir(mode=0o700)
    with auth.Directory(runtime) as directory:
        directory.write("ui-credentials.json", auth.credential_bytes(PASSWORD))
    monkeypatch.setattr(runner, "RUNTIME", runtime)
    monkeypatch.setattr(runner, "PRIVATE", runtime / "once")
    monkeypatch.setattr(runner, "PUBLIC", public)
    monkeypatch.setattr(runner, "source_binding", lambda: "invented-source-binding")
    monkeypatch.setattr(runner, "Client", lambda **_: api)
    monkeypatch.setattr(runner, "inspect_database", lambda: runner.parse_projection(
        json.dumps(projection()).encode()))
    monkeypatch.setitem(sys.modules, "governance.local_auth", SimpleNamespace(
        token=lambda: "invented-admin-token"))
    assert runner.run_native(runner.time.time() + 30, "invented-source-binding") == 1
    result = json.loads((public / "actual-once1.json").read_text())
    assert result["api_gate"] == "OPEN" and result["failure_code"] == code
    assert result["mutation_attempted"] is True
    assert result["mutation_succeeded"] is succeeded
    assert result["mutation_ambiguous"] is ambiguous
    assert result["baseline_status_code"] == 401
    assert PASSWORD not in json.dumps(result) and "invented-admin-token" not in json.dumps(result)
    assert api.events.count("change") == 1 and api.close.call_count == 1


def test_unreadable_state_does_not_claim_no_mutation():
    directory = SimpleNamespace(read=Mock(side_effect=auth.Failure("FILE_SAFETY")))
    result = runner.failure_result(directory, "INTERNAL")
    assert all(result[key] is None for key in
               ("mutation_attempted", "mutation_succeeded", "mutation_ambiguous"))
