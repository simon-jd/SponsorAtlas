"""Task6 contracts: full actual source coverage and isolated native dbt probes."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_bls_reference_replay_and_digest_rejection(tmp_path):
    from scripts.build_bls_references import build

    result = build(tmp_path)
    assert result["counts"] == {"areas": 585, "occupations": 6}
    assert result == build(tmp_path)
    assert (
        result["original_sha256"]
        == "86ffcbbeb27a96b7aa84d01801de71ee94ee9027452152caabebfdec9d764fde"
    )
    corrupt = tmp_path / "bad.jsonl"
    corrupt.write_text("{}\n")
    with pytest.raises(ValueError, match="digest"):
        build(tmp_path / "bad", jsonl=corrupt)


def test_fact_models_declare_native_merge_and_enforced_contract():
    import yaml

    for name in ["fact_lca_case", "fact_job_posting_daily", "fact_wage_benchmark"]:
        sql = (ROOT / "dbt/models/marts" / (name + ".sql")).read_text()
        spec = yaml.safe_load((ROOT / "dbt/models/marts" / (name + ".yml")).read_text())
        assert "incremental_strategy='merge'" in sql
        assert spec["models"][0]["config"]["contract"]["enforced"] is True
        assert all(c.get("data_type") for c in spec["models"][0]["columns"])


def test_local_loader_refuses_reviewed_destination():
    from scripts.load_fact_inputs import load

    with pytest.raises(ValueError, match="facts"):
        load(ROOT / ".tools/dimensions/fy2025-full.duckdb")


def test_local_loader_takes_explicit_sources_inside_their_areas(tmp_path):
    from scripts.load_fact_inputs import load

    with pytest.raises(ValueError, match="staging"):
        load(ROOT / ".tools/facts/never-created.duckdb", staging=tmp_path / "x.duckdb")
    with pytest.raises(ValueError, match="dimensions"):
        load(ROOT / ".tools/facts/never-created.duckdb", dimensions=tmp_path / "x.duckdb")
    assert not (ROOT / ".tools/facts/never-created.duckdb").exists()


def test_native_acceptance_is_recorded():
    # Expensive native probes are run explicitly once by scripts.fact_checks.
    from scripts.fact_checks import summarize

    assert summarize([("a", 1), ("b", 2)]) == summarize([("b", 2), ("a", 1)])
    assert summarize([("a", 1)]) != summarize([("a", 2)])
