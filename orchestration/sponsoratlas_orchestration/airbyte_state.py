"""Connection-scoped exclusion and fsync-committed Airbyte state, plus per-run audit."""

import fcntl
import hashlib
import json
import os
import uuid
from pathlib import Path

TERMINAL = {"succeeded", "cancelled", "failed", "incomplete"}


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_directory(path):
    path = Path(path).absolute()
    chain = [path, *path.parents]
    for directory in reversed(chain):
        if not directory.exists():
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                pass
        if not directory.is_dir() or directory.is_symlink():
            raise RuntimeError("Airbyte state requires a trusted directory")
    # Visibility does not prove another creator synced this entry. Every observer
    # establishes its own barrier, child through parent up to the filesystem root,
    # including ancestors that may also have been created by a paused worker.
    for directory in chain:
        sync_directory(directory)


def atomic_json(path, value):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".pending")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(value, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


class AirbyteState:
    def __init__(self, directory, endpoint, connection, run_id):
        self.directory = Path(directory).resolve()
        self.endpoint, self.connection, self.run_id = endpoint, connection, run_id
        self.identity = hashlib.sha256((endpoint + "\0" + connection).encode()).hexdigest()
        self.active = self.directory / ("connection-" + self.identity + ".json")
        self.fd = None

    def audit_path(self, run_id):
        key = hashlib.sha256(
            (self.endpoint + "\0" + self.connection + "\0" + run_id).encode()
        ).hexdigest()
        return self.directory / (key + ".json")

    def __enter__(self):
        ensure_directory(self.directory)
        self.fd = os.open(
            self.directory / (self.identity + ".lock"),
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.fsync(self.fd)
            sync_directory(self.directory)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            raise RuntimeError("Airbyte connection is owned by another worker") from None
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def __exit__(self, *_args):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)
        self.fd = None

    def read(self, path):
        # No credentials are stored. Refuse symbolic state files instead of following them.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as source:
            state = json.load(source)
        if not isinstance(state.get("run_id"), str) or state.get("status") not in TERMINAL | {
            "trigger_pending",
            "submitted",
            "cancel_unconfirmed",
        }:
            raise RuntimeError("Invalid Airbyte durable state; reconciliation required")
        return state

    def load(self):
        if self.active.exists():
            active = self.read(self.active)
        else:
            # Adopt pre-connection-index audit files conservatively. Their filename
            # binds the endpoint as well as connection/run, unlike connection_id alone.
            legacy = []
            paths = list(self.directory.glob("*.json"))
            if len(paths) > 10000:
                raise RuntimeError("Airbyte audit inventory requires manual reconciliation")
            for path in paths:
                if path.name.startswith("connection-"):
                    continue
                candidate = self.read(path)
                if self.audit_path(candidate["run_id"]) == path:
                    if candidate.get("connection_id") != self.connection:
                        raise RuntimeError(
                            "Airbyte legacy identity is inconsistent; reconcile state"
                        )
                    if candidate["status"] not in TERMINAL:
                        legacy.append(candidate)
            if len(legacy) > 1:
                raise RuntimeError("Airbyte connection has multiple unresolved legacy jobs")
            active = legacy[0] if legacy else None
            if active:
                self.commit(active)
        if active and active["run_id"] != self.run_id and active["status"] not in TERMINAL:
            raise RuntimeError(
                "Airbyte connection has an unresolved previous run; reconcile before new POST"
            )
        if active and active["run_id"] == self.run_id:
            return active
        audit = self.audit_path(self.run_id)
        if audit.exists():
            old = self.read(audit)
            if old["status"] not in TERMINAL:
                raise RuntimeError(
                    "Airbyte connection audit is unresolved; reconciliation required"
                )
            return old
        return None

    def commit(self, state):
        if self.fd is None:
            raise RuntimeError("Airbyte state commit requires connection ownership")
        # Authority first: a crash before the audit commit leaves a blocking active
        # record. Never dispatch until BOTH commits and parent-directory fsync finish.
        atomic_json(self.active, state)
        atomic_json(self.audit_path(state["run_id"]), state)
