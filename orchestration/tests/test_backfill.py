import json
import sys
from pathlib import Path

import pytest

from orchestration.sponsoratlas_orchestration import backfill
from orchestration.sponsoratlas_orchestration.backfill import (
    backfill_steps,
    fiscal_year_readiness,
    labelled_paths,
    labelled_steps,
    run_one,
    run_steps,
    steps_from,
)
from orchestration.sponsoratlas_orchestration.runtime import ROOT


def test_existing_quarter_archive_cannot_be_a_full_fiscal_year():
    result = fiscal_year_readiness(2024)
    assert result["status"] == "BLOCKED"
    assert result["available_releases"] == ["FY2024_Q4"]
    assert result["missing_releases"] == ["FY2024_Q1", "FY2024_Q2", "FY2024_Q3"]
    assert result["source_date_coverage"] == ["2024-07-01", "2024-09-30"]
    assert result["raw_rows"] == 120897
    assert result["requested_interval"] == ["2023-10-01", "2024-10-01"]
    assert result["mutations_started"] is False


def test_fy2025_has_four_archived_registered_quarters():
    result = fiscal_year_readiness(2025)
    assert result["status"] == "READY"
    assert result["available_releases"] == [f"FY2025_Q{q}" for q in (1, 2, 3, 4)]
    assert result["missing_releases"] == []
    assert result["raw_rows"] == 107414 + 132133 + 238425 + 118580
    assert result["source_date_coverage"] == ["2024-10-01", "2025-09-30"]


def test_fiscal_year_must_be_bounded():
    with pytest.raises(ValueError):
        fiscal_year_readiness(1999)


def test_steps_cover_the_pipeline_in_order():
    names = [s["name"] for s in backfill_steps(2025)]
    assert names == [
        "load_raw",
        "dbt_seed",
        "dbt_staging",
        "er_export",
        "er_train",
        "er_predict",
        "dimension_inputs",
        "dbt_dimensions",
        "fact_inputs",
        "dbt_facts",
    ]
    # Only the staging build may finish with failed data tests (known T3 limits);
    # every other step must exit 0.
    assert [s["name"] for s in backfill_steps(2025) if s.get("gate_failures_allowed")] == [
        "dbt_staging"
    ]


def test_steps_from_a_named_step_keep_everything_downstream():
    steps = steps_from(backfill_steps(2025), "er_export")
    assert [s["name"] for s in steps] == [
        "er_export",
        "er_train",
        "er_predict",
        "dimension_inputs",
        "dbt_dimensions",
        "fact_inputs",
        "dbt_facts",
    ]
    assert steps_from(backfill_steps(2025), None) == backfill_steps(2025)
    with pytest.raises(ValueError):
        steps_from(backfill_steps(2025), "er_typo")


def test_run_steps_times_each_step_and_stops_at_first_failure(tmp_path):
    ok = {"name": "ok", "argv": [sys.executable, "-c", "pass"]}
    bad = {"name": "bad", "argv": [sys.executable, "-c", "raise SystemExit(3)"]}
    never = {"name": "never", "argv": [sys.executable, "-c", "pass"]}
    result = run_steps([ok, bad, never], tmp_path / "out.json", logs=tmp_path)
    assert result["status"] == "FAILED"
    assert [(s["name"], s["exit_code"]) for s in result["steps"]] == [("ok", 0), ("bad", 3)]
    assert all(s["seconds"] >= 0 for s in result["steps"])
    assert json.loads((tmp_path / "out.json").read_text()) == result


def test_gate_failures_continue_only_when_every_model_succeeded(tmp_path):
    results = tmp_path / "run_results.json"

    def step(statuses):
        results.write_text(
            json.dumps(
                {
                    "results": [
                        {"unique_id": uid, "status": status} for uid, status in statuses.items()
                    ]
                }
            )
        )
        return {
            "name": "dbt_staging",
            "argv": [sys.executable, "-c", "raise SystemExit(1)"],
            "gate_failures_allowed": True,
            "run_results": results,
        }

    after = {"name": "after", "argv": [sys.executable, "-c", "pass"]}
    passed = step({"model.sponsoratlas.stg_lca": "success", "test.sponsoratlas.limits": "fail"})
    result = run_steps([passed, after], tmp_path / "a.json", logs=tmp_path)
    assert result["status"] == "COMPLETED_WITH_FAILED_GATES"
    assert result["steps"][0]["failed_tests"] == ["test.sponsoratlas.limits"]
    assert [s["name"] for s in result["steps"]] == ["dbt_staging", "after"]
    broken = step({"model.sponsoratlas.stg_lca": "error", "test.sponsoratlas.limits": "fail"})
    result = run_steps([broken, after], tmp_path / "b.json", logs=tmp_path)
    assert result["status"] == "FAILED"
    assert [s["name"] for s in result["steps"]] == ["dbt_staging"]


def test_backfill_dag_runs_each_step_once_in_order():
    import importlib

    dag = importlib.import_module("orchestration.dags.sponsoratlas_backfill").dag
    assert dag.dag_id == "sponsoratlas_backfill"
    assert dag.schedule is None
    assert dag.max_active_runs == 1
    order = [t.task_id for t in dag.topological_sort()]
    assert order == [s["name"] for s in backfill_steps(2025)]
    # Steps refuse to overwrite their destination, so an automatic retry cannot help.
    assert all(t.retries == 0 for t in dag.tasks)
    assert dag.on_failure_callback is not None


def test_run_steps_creates_the_step_work_directory(tmp_path):
    work = tmp_path / "work" / "nested"
    step = {"name": "w", "argv": [sys.executable, "-c", "pass"], "work": work}
    run_steps([step], tmp_path / "out.json", logs=tmp_path / "logs")
    assert work.is_dir()


def test_existing_destination_is_reconciled_instead_of_reloaded(tmp_path):
    destination = tmp_path / "db.duckdb"
    marker = tmp_path / "ran"
    step = {
        "name": "load_raw",
        "argv": [sys.executable, "-c", "raise SystemExit(9)"],
        "destination": destination,
        "resume_argv": [sys.executable, "-c", f"open({str(marker)!r}, 'w')"],
    }
    assert run_steps([step], tmp_path / "a.json", logs=tmp_path)["status"] == "FAILED"
    destination.write_bytes(b"")
    result = run_steps([step], tmp_path / "b.json", logs=tmp_path)
    assert result["status"] == "SUCCEEDED"
    assert result["steps"][0]["resumed"] is True
    assert marker.exists()


def test_load_step_resumes_with_a_count_reconciliation():
    load = backfill_steps(2025)[0]
    assert "scripts.reconcile_counts" in load["resume_argv"]
    assert "--duckdb" in load["resume_argv"]


def test_backfill_writes_new_databases_not_the_q4_only_ones():
    from scripts.canonical_paths import SUPERSEDED

    argv = " ".join(" ".join(s["argv"]) for s in backfill_steps(2025))
    for old in SUPERSEDED:
        assert str(old) not in argv
    assert "fy2025-full.duckdb" in argv


def test_disk_guard_refuses_a_step_that_would_cross_the_share(tmp_path, monkeypatch):
    from orchestration.sponsoratlas_orchestration import backfill

    staging = tmp_path / "s.duckdb"
    staging.write_bytes(b"x" * 1000)
    step = {
        "name": "fact_inputs",
        "argv": [sys.executable, "-c", "pass"],
        "reserve_staging_factor": 1.6,
        "staging": staging,
    }
    monkeypatch.setattr(backfill, "project_bytes", lambda: 10_000)
    monkeypatch.setattr(backfill, "SHARE_BYTES", 11_000)
    result = run_steps([step], tmp_path / "out.json", logs=tmp_path)
    assert result["status"] == "BLOCKED_DISK_SHARE"
    assert result["steps"][0]["exit_code"] is None
    assert result["steps"][0]["projected_bytes"] == 11_600
    monkeypatch.setattr(backfill, "SHARE_BYTES", 12_000)
    assert run_steps([step], tmp_path / "out.json", logs=tmp_path)["status"] == "SUCCEEDED"


def test_er_predict_inherits_the_archived_registry_unless_reset(tmp_path):
    archive = tmp_path / "not-created"
    argv = next(
        s for s in backfill_steps(2025, registry_archive=archive) if s["name"] == "er_predict"
    )["argv"]
    assert argv[1:3] == ["-m", "er.registry_archive"]
    assert argv[argv.index("--archive") + 1] == str(archive)
    assert "--initialize-registry" not in argv
    assert not archive.exists()  # building the steps does no I/O (DAG parsing)
    reset = next(
        s for s in backfill_steps(2025, initialize_registry=True) if s["name"] == "er_predict"
    )
    assert reset["argv"][-1] == "--initialize-registry"
    # The archive outlives the backfill directory, which is moved aside before a rebuild.
    default = next(s for s in backfill_steps(2025) if s["name"] == "er_predict")["argv"]
    output = Path(default[default.index("--output") + 1])
    assert not Path(default[default.index("--archive") + 1]).is_relative_to(output.parent)


@pytest.fixture
def archive(tmp_path):
    """A registry archive shaped like .tools/er/registry-archive-fy2025; steps only read it."""
    root = tmp_path / "archive"
    root.mkdir()
    (root / "abc.json").write_text("{}")
    (root / "LATEST").write_text("abc.json\n")
    return root


def written_paths(step):
    """Every project path a step names, except its executable and the registry it reads."""
    values = [*step["argv"][1:], *step.get("resume_argv", [])[1:], *step.get("env", {}).values()]
    values += [step[k] for k in ("work", "destination", "manifest", "run_results") if k in step]
    argv = step["argv"]
    if "--previous-registry" in argv:
        values.remove(argv[argv.index("--previous-registry") + 1])
    return [Path(v).resolve() for v in map(str, values) if v.startswith(str(ROOT))]


def test_labelled_run_never_resolves_to_a_canonical_path(archive):
    from scripts.canonical_paths import CANONICAL

    forbidden = [
        *CANONICAL,
        ROOT / ".tools/er",
        ROOT / ".tools/orchestration/backfill-fy2025",
    ]
    allowed = [
        ROOT / ".tools/backfill-runs/verify-1",
        *(ROOT / ".tools" / area / "backfill-runs/verify-1" for area in
          ("staging", "dimensions", "facts")),
    ]  # fmt: skip
    paths = [p for s in labelled_steps(2025, "verify-1", archive=archive) for p in written_paths(s)]
    assert paths
    for path in paths:
        assert not any(path == f or path.is_relative_to(f) for f in forbidden), path
        assert any(path.is_relative_to(a) for a in allowed), path


def test_labelled_databases_pass_the_loader_location_guards():
    paths = labelled_paths(2025, "verify-1")
    tools = ROOT / ".tools"
    assert paths["staging"].is_relative_to(tools / "staging")
    assert paths["dimensions"].is_relative_to(tools / "dimensions")
    assert paths["facts"].is_relative_to(tools / "facts")
    assert paths["er"].is_relative_to(tools)


@pytest.mark.parametrize("label", ["", "../../facts", "a/b", "/tmp/x", ".", "A", "1a", 20261106])
def test_a_label_cannot_escape_or_silently_mean_canonical(label):
    with pytest.raises(ValueError):
        labelled_paths(2025, label)


def test_labelled_er_predict_inherits_read_only_and_leaves_the_archive_alone(archive):
    before = {p.name: p.read_bytes() for p in archive.iterdir()}
    steps = labelled_steps(2025, "verify-1", archive=archive)
    argv = next(s for s in steps if s["name"] == "er_predict")["argv"]
    # er.predict has no archive argument and never publishes; er.registry_archive does.
    assert argv[1:3] == ["-m", "er.predict"]
    assert argv[argv.index("--previous-registry") + 1] == str(archive / "abc.json")
    assert "--archive" not in argv
    assert "--initialize-registry" not in argv
    assert not any("er.registry_archive" in " ".join(s["argv"]) for s in steps)
    assert {p.name: p.read_bytes() for p in archive.iterdir()} == before


def test_labelled_run_without_a_registry_fails_before_any_step(tmp_path):
    with pytest.raises(FileNotFoundError):
        labelled_steps(2025, "verify-1", archive=tmp_path / "empty")


def test_unlabelled_er_predict_still_publishes_to_the_archive():
    argv = next(s for s in backfill_steps(2025) if s["name"] == "er_predict")["argv"]
    assert argv[1:3] == ["-m", "er.registry_archive"]
    assert argv[argv.index("--archive") + 1] == str(ROOT / ".tools/er/registry-archive-fy2025")


@pytest.mark.parametrize(
    "build",
    [lambda a: backfill_steps(2025), lambda a: labelled_steps(2025, "v", archive=a)],
    ids=["unlabelled", "labelled"],
)
def test_every_dbt_step_overrides_a_snowflake_environment(build, archive, monkeypatch, tmp_path):
    # profiles.yml reads DBT_TARGET; the deployed environment sets it to snowflake.
    monkeypatch.setenv("DBT_TARGET", "snowflake")
    steps = [s for s in build(archive) if s["argv"][0] == backfill.DBT]
    assert len(steps) == 4
    seen = tmp_path / "target"
    write = f"import os; open({str(seen)!r}, 'a').write(os.environ['DBT_TARGET'] + ' ')"
    probes = [
        {"name": s["name"], "argv": [sys.executable, "-c", write], "env": s["env"]} for s in steps
    ]
    assert run_steps(probes, tmp_path / "out.json", logs=tmp_path)["status"] == "SUCCEEDED"
    assert seen.read_text().split() == ["local"] * 4


def capture_run_steps(monkeypatch):
    calls = []

    def fake(steps, output, *, logs):
        calls.append({"steps": steps, "output": Path(output), "logs": Path(logs)})
        return {"status": "SUCCEEDED", "steps": []}

    monkeypatch.setattr(backfill, "run_steps", fake)
    return calls


def test_run_one_with_a_label_uses_labelled_steps_and_its_own_evidence(monkeypatch, tmp_path):
    calls = capture_run_steps(monkeypatch)
    made = []
    monkeypatch.setattr(
        backfill,
        "labelled_paths",
        lambda year, label: {k: tmp_path / k / "db" for k in ("staging", "dimensions", "facts")},
    )
    monkeypatch.setattr(
        backfill, "labelled_steps", lambda year, label: made.append(label) or [{"name": "dbt_seed"}]
    )
    assert run_one(2025, "dbt_seed", "/ignored", label="verify-1") == "SUCCEEDED"
    assert made == ["verify-1"]
    evidence = ROOT / "docs/evidence/task09-backfill-airflow-verify-1"
    assert calls[0]["output"] == evidence / "dbt_seed.json"
    assert calls[0]["logs"] == evidence / "logs"
    assert (tmp_path / "facts").is_dir()


def test_run_one_on_canonical_paths_needs_the_explicit_flag(monkeypatch, tmp_path):
    calls = capture_run_steps(monkeypatch)
    run_one(2025, "dbt_seed", tmp_path, label=None, overwrite_canonical=True)
    assert calls[0]["steps"] == [s for s in backfill_steps(2025) if s["name"] == "dbt_seed"]
    assert calls[0]["output"] == tmp_path / "dbt_seed.json"


@pytest.mark.parametrize("flag", [False, None, "true", "True", 1])
def test_run_one_without_a_label_refuses_before_any_step(monkeypatch, tmp_path, flag):
    calls = capture_run_steps(monkeypatch)
    with pytest.raises(ValueError, match="label"):
        run_one(2025, "dbt_seed", tmp_path, label=None, overwrite_canonical=flag)
    assert calls == [] and not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("conf", "label", "overwrite"),
    [
        ({}, None, False),
        ({"fiscal_year": 2025}, None, False),
        ({"fiscal_year": 2025, "Label": "typo"}, None, False),
        ({"fiscal_year": 2025, "overwrite_canonical": "yes"}, None, "yes"),
        ({"fiscal_year": 2025, "overwrite_canonical": True}, None, True),
        ({"fiscal_year": 2025, "label": "verify-20261106"}, "verify-20261106", False),
    ],
)
def test_dag_renders_the_conf_into_run_one(conf, label, overwrite):
    import importlib
    from types import SimpleNamespace

    dag = importlib.import_module("orchestration.dags.sponsoratlas_backfill").dag
    assert dag.schedule is None
    task = dag.get_task("load_raw")
    rendered = task.render_template(
        task.op_kwargs, {"dag_run": SimpleNamespace(conf=conf)}, jinja_env=dag.get_template_env()
    )
    assert rendered["label"] == label
    assert rendered["year"] == 2025
    assert rendered["overwrite_canonical"] == overwrite
    if label is None and overwrite is not True:
        with pytest.raises(ValueError, match="label"):
            run_one(rendered["year"], "load_raw", "/ignored", label, rendered["overwrite_canonical"])
