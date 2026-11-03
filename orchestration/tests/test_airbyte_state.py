"""Durable ordering and actual advisory connection exclusion, without crash claims."""

import json
import os
from pathlib import Path

import pytest


def test_state_commit_fsync_order_and_connection_exclusion(tmp_path, monkeypatch):
    from orchestration.sponsoratlas_orchestration import airbyte_state as state

    events, descriptors = [], {}
    original_open, original_fsync, original_replace, original_mkdir = (
        os.open,
        os.fsync,
        os.replace,
        Path.mkdir,
    )

    def open_(path, flags, mode=0o777):
        fd = original_open(path, flags, mode)
        descriptors[fd] = str(path)
        events.append(("open", str(path), flags, mode))
        return fd

    def fsync(fd):
        events.append(("fsync", descriptors[fd]))
        return original_fsync(fd)

    def replace(src, dest):
        events.append(("replace", str(src), str(dest)))
        return original_replace(src, dest)

    def mkdir(path, *args, **kwargs):
        events.append(("mkdir", str(path)))
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(state.os, "open", open_)
    monkeypatch.setattr(state.os, "fsync", fsync)
    monkeypatch.setattr(state.os, "replace", replace)
    monkeypatch.setattr(state.Path, "mkdir", mkdir)
    directory = tmp_path / "new" / "state"
    with state.AirbyteState(directory, "https://airbyte.invalid", "connection", "run") as store:
        with (
            pytest.raises(RuntimeError, match="connection.*owned"),
            state.AirbyteState(directory, "https://airbyte.invalid", "connection", "other"),
        ):
            pytest.fail("Concurrent owner acquired connection")
        store.commit({"run_id": "run", "status": "trigger_pending"})
        assert directory.stat().st_mode & 0o777 == 0o700
        for path in directory.glob("*.json"):
            assert path.stat().st_mode & 0o777 == 0o600
            assert json.loads(path.read_text())["status"] == "trigger_pending"
        for index, event in enumerate(events):
            if event[0] == "replace":
                _, src, dest = event
                assert ("fsync", src) in events[:index]
                next_replace = next(
                    (i for i in range(index + 1, len(events)) if events[i][0] == "replace"),
                    len(events),
                )
                assert ("fsync", str(Path(dest).parent)) in events[index + 1 : next_replace]
                opened = next(e for e in events[:index] if e[0] == "open" and e[1] == src)
                assert opened[3] == 0o600
        assert sum(e[0] == "replace" for e in events) == 2
        for path in [tmp_path / "new", directory]:
            index = events.index(("mkdir", str(path)))
            assert ("fsync", str(path.parent)) in events[index + 1 :]


@pytest.mark.parametrize("pause_at_ancestor", [False, True])
def test_observer_commits_while_creator_stops_before_parent_fsync(
    tmp_path, monkeypatch, pause_at_ancestor
):
    from orchestration.sponsoratlas_orchestration import airbyte_state as state

    parent, directory = tmp_path / "new", tmp_path / "new" / "state"
    pause_at = parent if pause_at_ancestor else directory
    original_mkdir, original_sync = Path.mkdir, state.sync_directory
    events, dispatch_barriers = [], []
    worker = {"name": "A", "paused": False}

    class CreatorStopped(Exception):
        pass

    def sync(path):
        original_sync(path)
        events.append((worker["name"], str(path)))

    def mkdir(path, *args, **kwargs):
        original_mkdir(path, *args, **kwargs)
        if path == pause_at and not worker["paused"]:
            worker.update(name="B", paused=True)
            # A's mkdir is visible, but A never returns to its parent-fsync call.
            with state.AirbyteState(
                directory, "https://airbyte.invalid", "connection", "B"
            ) as store:
                store.commit({"run_id": "B", "status": "trigger_pending"})
                dispatch_barriers.extend(events)
            raise CreatorStopped

    monkeypatch.setattr(state.Path, "mkdir", mkdir)
    monkeypatch.setattr(state, "sync_directory", sync)
    with pytest.raises(CreatorStopped):
        state.ensure_directory(directory)
    assert worker["paused"]
    for needed in [directory, parent, tmp_path]:
        assert ("B", str(needed)) in dispatch_barriers, (
            "Dispatching observer must finish its own directory-chain barrier; "
            f"creator never resumes: {dispatch_barriers}"
        )
