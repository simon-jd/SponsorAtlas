"""Destructive Azure controls tested through an executable fake CLI; no cloud calls."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts/azure_reaper.sh"
SUB = "11111111-2222-3333-4444-555555555555"
GROUP = "rg-sponsoratlas-lab"
GROUP_ID = f"/subscriptions/{SUB}/resourceGroups/{GROUP}"
RESOURCE_ID = f"{GROUP_ID}/providers/Microsoft.Storage/storageAccounts/example"
FLAGS = ["--subscription", SUB, "--output", "json", "--only-show-errors"]
ACCOUNT = ["account", "show", "--output", "json", "--only-show-errors"]
EXISTS = ["group", "exists", "--name", GROUP, *FLAGS]
LIST = ["resource", "list", "--resource-group", GROUP, *FLAGS]
SHOW = ["resource", "show", "--ids", RESOURCE_ID, *FLAGS]
DELETE = ["resource", "delete", "--ids", RESOURCE_ID, *FLAGS]
GROUP_SHOW = ["group", "show", "--name", GROUP, *FLAGS]
GROUP_DELETE = ["group", "delete", "--name", GROUP, "--yes", *FLAGS]


def resource(**updates):
    value = {
        "id": RESOURCE_ID,
        "tags": {
            "project": "sponsoratlas",
            "owner": "codex-sponsoratlas",
            "ttl": "2000-01-01T00:00:00Z",
        },
    }
    value.update(updates)
    return value


def reply(argv, value=None, **updates):
    return {"argv": argv, "stdout": json.dumps(value), **updates}


def initial(exists=True, **account):
    return [reply(ACCOUNT, {"id": SUB, "state": "Enabled", **account}), reply(EXISTS, exists)]


@pytest.fixture
def run_cli(tmp_path):
    fake = tmp_path / "az"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "assert os.environ['AZURE_EXTENSION_DIR'] == os.environ['AZURE_CONFIG_DIR'] + '/extensions'\n"
        "assert os.environ['AZURE_CORE_COLLECT_TELEMETRY'] == 'false'\n"
        "assert os.environ.get('HOME') == os.environ.get('FAKE_AZ_EXPECTED_HOME')\n"
        "journal = pathlib.Path(os.environ['FAKE_AZ_JOURNAL'])\n"
        "calls = json.loads(journal.read_text()) if journal.exists() else []\n"
        "steps = json.loads(pathlib.Path(os.environ['FAKE_AZ_STEPS']).read_text())\n"
        "calls.append(sys.argv[1:]); journal.write_text(json.dumps(calls))\n"
        "step = steps[len(calls)-1]\n"
        "if step['argv'] != sys.argv[1:]:\n"
        " print('unexpected argv', file=sys.stderr); sys.exit(91)\n"
        "print(step.get('stdout', ''))\n"
        "print(step.get('stderr', ''), file=sys.stderr)\n"
        "sys.exit(step.get('code', 0))\n"
    )
    fake.chmod(0o700)
    steps_path, journal = tmp_path / "steps.json", tmp_path / "journal.json"

    def run(steps, *args, env_extra=None):
        steps_path.write_text(json.dumps(steps))
        journal.unlink(missing_ok=True)
        env = {
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "AZURE_CONFIG_DIR": str(ROOT / ".tools/azure"),
            "FAKE_AZ_JOURNAL": str(journal),
            "FAKE_AZ_STEPS": str(steps_path),
            "FAKE_AZ_EXPECTED_HOME": os.environ.get("HOME", ""),
            **(env_extra or {}),
        }
        result = subprocess.run(
            ["bash", str(WRAPPER), *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            cwd=tmp_path,
        )
        calls = json.loads(journal.read_text()) if journal.exists() else []
        return result, calls

    return run


def test_missing_group_is_idempotent_in_both_modes(run_cli):
    for mode in [[], ["--apply"], ["--down", "--apply"]]:
        result, calls = run_cli(initial(False), *mode)
        assert result.returncode == 0, result.stderr
        assert "group_missing" in result.stdout
        assert calls == [ACCOUNT, EXISTS]


def test_default_dry_run_revalidates_without_deleting(run_cli):
    steps = initial() + [reply(LIST, [resource()]), reply(SHOW, resource())]
    result, calls = run_cli(steps)
    assert result.returncode == 0, result.stderr
    assert '"event": "would_delete"' in result.stdout
    assert calls == [s["argv"] for s in steps]


def test_apply_deletes_only_revalidated_resource(run_cli):
    steps = initial() + [reply(LIST, [resource()]), reply(SHOW, resource()), reply(DELETE)]
    result, calls = run_cli(steps, "--apply")
    assert result.returncode == 0, result.stderr
    assert '"event": "deleted"' in result.stdout
    assert calls == [s["argv"] for s in steps]


@pytest.mark.parametrize(
    "candidate,reason",
    [
        ({}, "invalid_resource_id"),
        (resource(id=RESOURCE_ID.replace(SUB, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")), "scope"),
        (resource(id=RESOURCE_ID.replace(GROUP, "rg-mlinfra-westus")), "scope"),
        (resource(id=RESOURCE_ID.replace(GROUP, GROUP + "-extra")), "scope"),
        (resource(id=RESOURCE_ID + "/../escape"), "invalid_resource_id"),
        (resource(id=RESOURCE_ID + "?x=y"), "invalid_resource_id"),
        (resource(tags=None), "tags"),
        (resource(tags={"project": "other"}), "project"),
        (resource(tags={"project": "sponsoratlas", "owner": "other"}), "owner"),
        (resource(tags={"project": "sponsoratlas", "owner": "codex-sponsoratlas"}), "ttl"),
        (resource(tags={**resource()["tags"], "ttl": "not-a-date"}), "ttl"),
        (resource(tags={**resource()["tags"], "ttl": "2000-01-01"}), "ttl"),
        (resource(tags={**resource()["tags"], "ttl": "2000-01-01T00:00:00"}), "ttl"),
        (resource(tags={**resource()["tags"], "ttl": "2000-01-01T00:00:00+01:00"}), "ttl"),
        (resource(tags={**resource()["tags"], "ttl": "2000-02-31T00:00:00Z"}), "ttl"),
        (resource(tags={**resource()["tags"], "ttl": "2999-01-01T00:00:00Z"}), "not_expired"),
    ],
)
def test_reaper_skips_ineligible_resources(run_cli, candidate, reason):
    steps = initial() + [reply(LIST, [candidate])]
    result, calls = run_cli(steps, "--apply")
    assert result.returncode == 0, result.stderr
    assert reason in result.stdout
    assert calls == [s["argv"] for s in steps]


@pytest.mark.parametrize(
    "changed",
    [
        resource(tags={**resource()["tags"], "owner": "other"}),
        resource(tags={**resource()["tags"], "ttl": "2999-01-01T00:00:00Z"}),
        resource(id=RESOURCE_ID + "different"),
        resource(id=RESOURCE_ID.replace(GROUP, "rg-mlinfra-westus")),
    ],
)
def test_revalidation_blocks_changed_identity_tags_or_ttl(run_cli, changed):
    steps = initial() + [reply(LIST, [resource()]), reply(SHOW, changed)]
    result, calls = run_cli(steps, "--apply")
    assert result.returncode == 0, result.stderr
    assert "revalidation" in result.stdout
    assert calls == [s["argv"] for s in steps]


def test_utc_offset_is_valid_and_arguments_remain_separate(run_cli):
    rid = RESOURCE_ID + " space (safe)"
    candidate = resource(id=rid, tags={**resource()["tags"], "ttl": "2000-01-01T00:00:00+00:00"})
    show = ["resource", "show", "--ids", rid, *FLAGS]
    delete = ["resource", "delete", "--ids", rid, *FLAGS]
    steps = initial() + [reply(LIST, [candidate]), reply(show, candidate), reply(delete)]
    result, calls = run_cli(steps, "--apply")
    assert result.returncode == 0, result.stderr
    assert calls[-1] == delete


@pytest.mark.parametrize("bad_account", [{"id": "bad"}, {"state": "Disabled"}])
def test_invalid_account_stops_before_scope_calls(run_cli, bad_account):
    result, calls = run_cli(initial(**bad_account), "--apply")
    assert result.returncode != 0
    assert calls == [ACCOUNT]


@pytest.mark.parametrize(
    "step",
    [
        reply(EXISTS, stdout="not-json"),
        reply(EXISTS, "false"),
        reply(EXISTS, code=2, stderr="SECRET CLI diagnostic"),
    ],
)
def test_cli_json_and_schema_errors_are_nonzero_without_secret_echo(run_cli, step):
    result, calls = run_cli([initial()[0], step], "--apply")
    assert result.returncode != 0
    assert "error" in result.stderr.lower()
    assert "SECRET" not in result.stderr + result.stdout
    assert calls == [ACCOUNT, EXISTS]


@pytest.mark.parametrize("failure_at", ["list", "show", "delete"])
def test_resource_command_failures_propagate(run_cli, failure_at):
    steps = initial() + [reply(LIST, [resource()]), reply(SHOW, resource()), reply(DELETE)]
    index = {"list": 2, "show": 3, "delete": 4}[failure_at]
    steps[index] = {**steps[index], "code": 3}
    result, calls = run_cli(steps[: index + 1], "--apply")
    assert result.returncode != 0
    assert "error" in result.stderr.lower()
    assert calls == [s["argv"] for s in steps[: index + 1]]


def test_one_resource_failure_does_not_hide_later_skips(run_cli):
    steps = initial() + [reply(LIST, [resource(), resource(tags={})]), reply(SHOW, code=3)]
    result, _ = run_cli(steps, "--apply")
    assert result.returncode != 0
    assert '"event": "skipped"' in result.stdout


def test_down_revalidates_group_and_deletes_fixed_scope(run_cli):
    group = resource(id=GROUP_ID)
    steps = initial() + [reply(GROUP_SHOW, group), reply(GROUP_SHOW, group), reply(GROUP_DELETE)]
    result, calls = run_cli(steps, "--down", "--apply")
    assert result.returncode == 0, result.stderr
    assert '"event": "group_deleted"' in result.stdout
    assert calls == [s["argv"] for s in steps]


@pytest.mark.parametrize(
    "bad_group",
    [
        resource(id=GROUP_ID, tags={}),
        resource(id=GROUP_ID, tags={**resource()["tags"], "owner": "other"}),
        resource(id=GROUP_ID.replace(GROUP, "rg-mlinfra-westus")),
    ],
)
def test_down_refuses_wrong_group_identity_or_tags(run_cli, bad_group):
    steps = initial() + [reply(GROUP_SHOW, bad_group)]
    result, calls = run_cli(steps, "--down", "--apply")
    assert result.returncode != 0
    assert calls == [s["argv"] for s in steps]


def test_down_rejects_group_tag_change(run_cli):
    steps = initial() + [
        reply(GROUP_SHOW, resource(id=GROUP_ID)),
        reply(GROUP_SHOW, resource(id=GROUP_ID, tags={})),
    ]
    result, calls = run_cli(steps, "--down", "--apply")
    assert result.returncode != 0
    assert calls == [s["argv"] for s in steps]


def test_down_dry_run_never_deletes(run_cli):
    steps = initial() + [reply(GROUP_SHOW, resource(id=GROUP_ID))]
    result, calls = run_cli(steps, "--down")
    assert result.returncode == 0, result.stderr
    assert "would_delete_group" in result.stdout
    assert calls == [s["argv"] for s in steps]


def test_cannot_override_group_or_read_external_config(run_cli):
    for args, env in [
        (["--group", "rg-mlinfra-westus"], {}),
        ([], {"AZURE_CONFIG_DIR": "/tmp/another-project/.azure"}),
    ]:
        result, calls = run_cli([], *args, env_extra=env)
        assert result.returncode != 0
        assert calls == []


@pytest.mark.parametrize("bad_value", [{}, None, "not-a-list"])
def test_reaper_rejects_invalid_list_schema(run_cli, bad_value):
    steps = initial() + [reply(LIST, bad_value)]
    result, calls = run_cli(steps, "--apply")
    assert result.returncode != 0
    assert calls == [s["argv"] for s in steps]


@pytest.mark.parametrize("bad_step", [reply(SHOW, stdout="not-json"), reply(SHOW, [])])
def test_reaper_never_deletes_invalid_show_json_or_schema(run_cli, bad_step):
    steps = initial() + [reply(LIST, [resource()]), bad_step]
    result, calls = run_cli(steps, "--apply")
    assert result.returncode != 0
    assert calls == [s["argv"] for s in steps]


@pytest.mark.parametrize(
    "bad_step",
    [
        reply(GROUP_SHOW, stdout="not-json"),
        reply(GROUP_SHOW, None),
        reply(GROUP_SHOW, code=3),
    ],
)
def test_down_errors_are_nonzero_and_never_delete(run_cli, bad_step):
    steps = initial() + [bad_step]
    result, calls = run_cli(steps, "--down", "--apply")
    assert result.returncode != 0
    assert calls == [s["argv"] for s in steps]


def test_group_delete_error_is_nonzero(run_cli):
    group = resource(id=GROUP_ID)
    steps = initial() + [
        reply(GROUP_SHOW, group),
        reply(GROUP_SHOW, group),
        reply(GROUP_DELETE, code=3),
    ]
    result, calls = run_cli(steps, "--down", "--apply")
    assert result.returncode != 0
    assert "group_deleted" not in result.stdout
    assert calls == [s["argv"] for s in steps]


def test_make_default_does_not_run_cleanup():
    result = subprocess.run(
        ["make", "-n"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--apply" not in result.stdout
    assert "delete" not in result.stdout
    assert "azure_reaper.sh" not in result.stdout


@pytest.mark.parametrize(
    "target,expected",
    [
        ("azure-reap", "./scripts/azure_reaper.sh --apply"),
        ("azure-down", "./scripts/azure_reaper.sh --down --apply"),
    ],
)
def test_explicit_make_cleanup_commands_remain_available(target, expected):
    result = subprocess.run(
        # --no-print-directory: under an outer `make test` the child would
        # otherwise add "Entering directory" lines to stdout.
        ["make", "--no-print-directory", "-n", target],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
