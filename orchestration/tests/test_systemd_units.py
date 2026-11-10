"""The deployment's systemd --user units and scripts. Nothing here touches the real user manager:
scripts run against a throw-away HOME and project, with systemctl and run.sh replaced by stubs."""

import configparser
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import airflow
import pytest
import yaml

from orchestration.sponsoratlas_orchestration import runtime

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT / "orchestration/systemd"
PREFIX = "%h/Desktop/SponsorAtlas"
RUN = f"{PREFIX}/orchestration/run.sh airflow"
SERVICES = ["api-server", "dag-processor", "scheduler"]
SERVICE_UNITS = [f"sponsoratlas-airflow-{name}.service" for name in SERVICES]
ALL_UNITS = [*SERVICE_UNITS, "sponsoratlas-airflow-end.timer"]
UNIT_FILES = [*SERVICE_UNITS, "sponsoratlas-airflow-end.service", "sponsoratlas-airflow-end.timer"]
SECRET_NAME = re.compile(r"SECRET|PASSWORD|TOKEN|PASSPHRASE")


def unit(name):
    parser = configparser.ConfigParser(strict=False, interpolation=None, delimiters=("=",))
    parser.optionxform = str
    parser.read(SYSTEMD / name)
    return parser


def template():
    lines = (SYSTEMD / "airflow.env.template").read_text().splitlines()
    return dict(ln.split("=", 1) for ln in lines if ln and not ln.startswith("#"))


@pytest.mark.parametrize("name", SERVICE_UNITS)
def test_each_service_has_the_deployment_contract(name):
    parsed = unit(name)
    assert parsed["Unit"]["ConditionPathExists"] == f"!{PREFIX}/.tools/orchestration/STOP"
    service = parsed["Service"]
    assert service["Restart"] == "on-failure"
    assert service["MemoryMax"] == "4G"
    assert service["EnvironmentFile"] == "%h/.config/sponsoratlas/airflow.env"
    assert service["ExecStart"].startswith(RUN + " ")
    assert parsed["Install"]["WantedBy"] == "default.target"


def test_nothing_listens_beyond_loopback():
    api = unit("sponsoratlas-airflow-api-server.service")["Service"]["ExecStart"]
    assert api == f"{RUN} api-server --host 127.0.0.1 --port 18780 --apps execution"
    assert "AIRFLOW__API__PORT:-18780" in (ROOT / "orchestration/run.sh").read_text()
    # The scheduler would otherwise serve logs on every interface.
    scheduler = unit("sponsoratlas-airflow-scheduler.service")["Service"]["ExecStart"]
    assert scheduler == f"{RUN} scheduler --skip-serve-logs"
    assert unit("sponsoratlas-airflow-dag-processor.service")["Service"]["ExecStart"] == (
        f"{RUN} dag-processor"
    )


def test_end_timer_fires_before_the_trial_ends_and_catches_up_after_downtime():
    parsed = unit("sponsoratlas-airflow-end.timer")
    assert parsed["Timer"]["OnCalendar"] == "2026-11-23 00:30:00 UTC"
    assert parsed["Timer"]["Persistent"] == "true"
    assert parsed["Timer"]["Unit"] == "sponsoratlas-airflow-end.service"
    assert parsed["Install"]["WantedBy"] == "timers.target"
    assert template()["SNOWFLAKE_TRIAL_EXPIRES_AT"] == "2026-11-24T00:00:00Z"


def test_end_service_still_runs_when_stop_exists():
    parsed = unit("sponsoratlas-airflow-end.service")
    assert parsed["Service"]["Type"] == "oneshot"
    assert parsed["Service"]["ExecStart"].endswith(
        "orchestration/systemd/sponsoratlas-airflow-end.sh"
    )
    assert not parsed.has_option("Unit", "ConditionPathExists")


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="needs systemd-analyze")
def test_units_pass_systemd_analyze_verify():
    done = subprocess.run(
        ["systemd-analyze", "--user", "verify", *(str(SYSTEMD / f) for f in UNIT_FILES)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    # Other installed units can warn about themselves; ours may not.
    assert "sponsoratlas" not in done.stdout + done.stderr


def test_template_holds_no_secret():
    values = template()
    text = (SYSTEMD / "airflow.env.template").read_text()
    assert "PRIVATE KEY-----" not in text
    assert {k: v for k, v in values.items() if SECRET_NAME.search(k)} == {
        "CUBEJS_API_SECRET": "@GENERATED_BY_INSTALL@"
    }


def test_template_configures_the_snowflake_deployment():
    values = template()
    assert values["DBT_TARGET"] == "snowflake"
    assert values["SPONSORATLAS_ORCHESTRATION_MODE"] == "production"
    assert values["SNOWFLAKE_ACCOUNT"] == "@SNOWFLAKE_ACCOUNT@"
    keys = "@HOME@/.snowflake-keys/sponsoratlas"
    assert values["SNOWFLAKE_PRIVATE_KEY_FILE"] == f"{keys}/DBT_SVC.p8"
    assert values["SPONSORATLAS_CUBE_PRIVATE_KEY_FILE"] == f"{keys}/CUBE_SVC.p8"
    assert values["SNOWFLAKE_LOADER_PRIVATE_KEY_FILE"] == f"{keys}/AIRBYTE_SVC.p8"
    assert values["SNOWFLAKE_COST_OBSERVER_PRIVATE_KEY_FILE"] == f"{keys}/COST_OBSERVER_SVC.p8"
    assert values["SNOWFLAKE_TRIAL_STARTED_AT"] == "2026-10-25T07:29:00Z"
    assert values["SEMANTIC_SOURCE"] == "snowflake"
    assert values["SEMANTIC_SNOWFLAKE_OPT_IN"] == "1"
    assert values["SEMANTIC_WORK"] == "@PROJECT@/.tools/semantic/snowflake-refresh"
    assert values["SPONSORATLAS_ALERT_TRANSPORT"] == "local_log"


def test_parse_interval_is_a_real_setting_for_the_deployed_units_only():
    assert template()["AIRFLOW__DAG_PROCESSOR__MIN_FILE_PROCESS_INTERVAL"] == "3600"
    config = yaml.safe_load(
        (Path(airflow.__file__).parent / "config_templates/config.yml").read_text()
    )
    assert "min_file_process_interval" in config["dag_processor"]["options"]
    assert "MIN_FILE_PROCESS_INTERVAL" not in (ROOT / "orchestration/run.sh").read_text()


class Sandbox:
    """A HOME with the checkout at the path the units name, and stubs that log every call."""

    def __init__(self, base):
        self.home = base / "home"
        self.project = self.home / "Desktop/SponsorAtlas"
        self.systemd = self.project / "orchestration/systemd"
        shutil.copytree(SYSTEMD, self.systemd)
        self.stop = self.project / ".tools/orchestration/STOP"
        self.stop.parent.mkdir(parents=True)
        self.stop.touch()
        self.env_file = self.home / ".config/sponsoratlas/airflow.env"
        self.log = base / "calls.log"
        self.bin = base / "bin"
        self.bin.mkdir()
        stub = """#!/bin/sh
state=stop=no
[ -e "$STUB_STOP" ] && state=stop=yes
printf '%s %s\\n' "{name} $*" "$state" >> "$STUB_LOG"
case "$*" in
  *export_runs*) exit "${{STUB_EXPORT_EXIT:-0}}" ;;
  *notify*) exit "${{STUB_ALERT_EXIT:-0}}" ;;
esac
"""
        for path, name in [
            (self.bin / "systemctl", "systemctl"),
            (self.project / "orchestration/run.sh", "run.sh"),
            (self.project / "orchestration/bin/dbt", "dbt"),
        ]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(stub.format(name=name))
            path.chmod(0o755)

    def script(self, name, *args, **env):
        return subprocess.run(
            [str(self.systemd / name), *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env={
                **os.environ,
                "HOME": str(self.home),
                "PATH": f"{self.bin}:{os.environ['PATH']}",
                "STUB_LOG": str(self.log),
                "STUB_STOP": str(self.stop),
                "SNOWFLAKE_ACCOUNT": "example-account",
                **env,
            },
        )

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def secret(self):
        return re.search(r"^CUBEJS_API_SECRET=(.*)$", self.env_file.read_text(), re.M).group(1)


@pytest.fixture
def box(tmp_path):
    return Sandbox(tmp_path)


def test_scripts_are_executable():
    for name in ["install.sh", "rollback.sh", "sponsoratlas-airflow-end.sh"]:
        assert (SYSTEMD / name).stat().st_mode & stat.S_IXUSR, name


def test_install_enables_nothing_and_keeps_stop(box):
    done = box.script("install.sh")
    assert done.returncode == 0, done.stderr
    assert box.calls() == [
        "systemctl --user daemon-reload stop=yes",
        "run.sh airflow db migrate stop=yes",
    ]
    assert box.stop.exists()
    installed = sorted(p.name for p in (box.home / ".config/systemd/user").iterdir())
    assert installed == sorted(UNIT_FILES)
    assert "Next steps" in done.stdout


def test_install_creates_an_owner_only_env_file_with_a_generated_secret(box):
    done = box.script("install.sh")
    assert stat.S_IMODE(box.env_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(box.env_file.parent.stat().st_mode) == 0o700
    text = box.env_file.read_text()
    assert "@" not in text
    assert f"SNOWFLAKE_PRIVATE_KEY_FILE={box.home}/.snowflake-keys/sponsoratlas/DBT_SVC.p8" in text
    assert f"SEMANTIC_WORK={box.project}/.tools/semantic/snowflake-refresh" in text
    assert "SNOWFLAKE_ACCOUNT=example-account" in text
    secret = box.secret()
    assert re.fullmatch(r"[0-9a-f]{64}", secret)
    assert secret not in done.stdout + done.stderr
    assert not list(box.env_file.parent.glob("airflow.env.*"))


def test_install_needs_the_account_to_create_the_env_file(box):
    done = box.script("install.sh", SNOWFLAKE_ACCOUNT="")
    assert done.returncode != 0
    assert "SNOWFLAKE_ACCOUNT" in done.stderr
    assert not box.env_file.exists()


def test_install_rerun_keeps_the_env_file_and_its_secret(box):
    box.script("install.sh")
    secret = box.secret()
    with box.env_file.open("a") as stream:
        stream.write("OPERATOR_NOTE=kept\n")
    box.env_file.chmod(0o640)
    assert box.script("install.sh").returncode == 0
    assert box.secret() == secret
    assert "OPERATOR_NOTE=kept" in box.env_file.read_text()
    assert stat.S_IMODE(box.env_file.stat().st_mode) == 0o600


def test_install_fills_an_absent_secret_only(box):
    box.env_file.parent.mkdir(parents=True)
    box.env_file.write_text("DBT_TARGET=snowflake\n")
    assert box.script("install.sh").returncode == 0
    assert re.fullmatch(r"[0-9a-f]{64}", box.secret())
    assert "DBT_TARGET=snowflake" in box.env_file.read_text()


def test_install_enables_and_unpauses_only_on_explicit_flags(box):
    assert box.script("install.sh", "--enable").returncode == 0
    calls = box.calls()
    assert "systemctl --user enable --now sponsoratlas-airflow-end.timer stop=yes" in calls
    assert f"systemctl --user enable --now {' '.join(SERVICE_UNITS)} stop=yes" in calls
    assert not [c for c in calls if "unpause" in c]
    assert box.stop.exists()
    box.log.unlink()
    done = box.script("install.sh", "--unpause", "a", "--unpause", "b")
    assert done.returncode == 0, done.stderr
    calls = box.calls()
    assert "run.sh airflow dags unpause --yes a stop=yes" in calls
    assert "run.sh airflow dags unpause --yes b stop=yes" in calls
    assert not [c for c in calls if " enable " in c]


def test_a_new_deployment_gets_its_own_final_alert(box):
    assert box.script("sponsoratlas-airflow-end.sh").returncode == 0
    marker = box.stop.parent / "END_ALERT_SENT"
    assert marker.exists()
    assert box.script("install.sh").returncode == 0
    assert marker.exists()
    assert box.script("install.sh", "--enable").returncode == 0
    assert not marker.exists()


def test_install_refuses_a_checkout_the_units_do_not_name(box, tmp_path):
    other = tmp_path / "elsewhere"
    box.project.rename(tmp_path / "moved")
    (other / "Desktop").mkdir(parents=True)
    (tmp_path / "moved").rename(other / "Desktop/Other")
    done = subprocess.run(
        [str(other / "Desktop/Other/orchestration/systemd/install.sh")],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HOME": str(box.home), "PATH": f"{box.bin}:{os.environ['PATH']}"},
    )
    assert done.returncode == 1
    assert "this checkout is" in done.stderr
    assert box.calls() == []


def test_end_dry_run_prints_every_action_and_does_none(box):
    box.stop.unlink()
    done = box.script("sponsoratlas-airflow-end.sh", "--dry-run")
    assert done.returncode == 0, done.stderr
    for action in ["export_runs.py", "notify", f"touch {box.stop}", *ALL_UNITS]:
        assert action in done.stdout, action
    assert box.calls() == []
    assert not box.stop.exists()
    assert not (box.stop.parent / "END_ALERT_SENT").exists()


def test_end_exports_then_alerts_then_stops_then_disables(box):
    box.stop.unlink()
    done = box.script("sponsoratlas-airflow-end.sh")
    assert done.returncode == 0, done.stderr
    calls = box.calls()
    assert "export_runs.py" in calls[0]
    assert "notify" in calls[1]
    assert calls[0].endswith("stop=no")
    assert calls[1].endswith("stop=no")
    assert calls[2:] == [f"systemctl --user disable --now {u} stop=yes" for u in ALL_UNITS]
    assert box.stop.exists()


def test_end_is_safe_to_rerun(box):
    assert box.script("sponsoratlas-airflow-end.sh").returncode == 0
    box.log.unlink()
    done = box.script("sponsoratlas-airflow-end.sh")
    assert done.returncode == 0, done.stderr
    calls = box.calls()
    assert not [c for c in calls if "notify" in c]
    assert [c for c in calls if "disable" in c] == [
        f"systemctl --user disable --now {u} stop=yes" for u in ALL_UNITS
    ]


@pytest.mark.parametrize("failing", ["STUB_EXPORT_EXIT", "STUB_ALERT_EXIT"])
def test_end_still_shuts_down_when_export_or_alert_fails(box, failing):
    box.stop.unlink()
    done = box.script("sponsoratlas-airflow-end.sh", **{failing: "7"})
    assert done.returncode == 1
    assert "FAILED with exit 7" in done.stderr
    assert box.stop.exists()
    assert len([c for c in box.calls() if "disable --now" in c]) == 4


def test_end_retries_only_an_alert_that_failed(box):
    box.script("sponsoratlas-airflow-end.sh", STUB_ALERT_EXIT="7")
    box.log.unlink()
    assert box.script("sponsoratlas-airflow-end.sh").returncode == 0
    assert len([c for c in box.calls() if "notify" in c]) == 1


def test_end_alert_matches_the_real_notify(monkeypatch, tmp_path):
    source = (SYSTEMD / "sponsoratlas-airflow-end.sh").read_text()
    code = re.search(r"^alert='(.*)'$", source, re.M).group(1)
    log = tmp_path / "alerts.jsonl"
    monkeypatch.setattr(runtime, "ALERT_LOG", log)
    monkeypatch.setenv("SPONSORATLAS_ALERT_TRANSPORT", "local_log")
    exec(code, {})  # noqa: S102 -- the script's own constant
    assert '"event": "deployment_end"' in log.read_text()


def test_rollback_stops_everything_and_only_prints_the_snowflake_cleanup(box):
    box.stop.unlink()
    done = box.script("rollback.sh")
    assert done.returncode == 0, done.stderr
    assert box.stop.exists()
    calls = box.calls()
    assert sorted(c.rsplit(" ", 1)[0] for c in calls) == sorted(
        f"systemctl --user disable --now {u}" for u in ALL_UNITS
    )
    assert "sponsoratlas-airflow-end.timer" in calls[0]
    assert (
        "DELETE FROM RAW.LCA.FACTS_INPUT_JOB_OBSERVATIONS WHERE replay_kind = 'LIVE_BOARD_FETCH';"
        in done.stdout
    )
    assert "--full-refresh --select fact_job_posting_daily+" in done.stdout
    # Printed, never run: neither dbt nor Airflow was started.
    assert not [c for c in calls if c.startswith(("dbt", "run.sh"))]
