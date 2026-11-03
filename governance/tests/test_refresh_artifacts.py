import json

import pytest

from governance import catalog, refresh_artifacts
from scripts.canonical_paths import FACTS_DB, STAGING_DB


def test_plan_matches_the_catalog_services_and_the_backfill_builds():
    plan = {p["service"]: p for p in refresh_artifacts.plan()}
    for service, (artifacts, physical) in catalog.SERVICES.items():
        if service == "sponsoratlas_local_synthetic_duckdb":
            assert service not in plan
            continue
        assert plan[service]["artifacts"] == catalog.RUNTIME / artifacts
        assert plan[service]["database"] == physical
    assert {p["database"] for p in plan.values()} == {STAGING_DB, FACTS_DB}
    assert all("backfill-fy2025" in str(p["build"]) for p in plan.values())


def test_generate_copies_build_artifacts_and_refuses_an_existing_directory(tmp_path, monkeypatch):
    build = tmp_path / "build"
    build.mkdir()
    for name in ("manifest.json", "run_results.json"):
        (build / name).write_text(json.dumps({"name": name}))
    calls = []
    monkeypatch.setattr(refresh_artifacts, "docs_generate", lambda **kw: calls.append(kw))
    item = {"service": "s", "artifacts": tmp_path / "out", "database": tmp_path / "db", "build": build}
    provenance = refresh_artifacts.generate(item)
    assert (tmp_path / "out/manifest.json").read_text() == (build / "manifest.json").read_text()
    assert calls == [{"database": tmp_path / "db", "target": tmp_path / "out"}]
    assert set(provenance["copied"]) == {"manifest.json", "run_results.json"}
    with pytest.raises(FileExistsError):
        refresh_artifacts.generate(item)
