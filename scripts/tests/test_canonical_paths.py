import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
Q4_ONLY = re.compile(
    r"staging/actual-files\.duckdb|dimensions/actual\.duckdb|facts/actual\.duckdb"
    r"|er/(real-input-v2|fix-r1-model|fix-r1-next)|viz/worksite(?!-fy2025)|: .worksite.\)"
)
CODE = (".py", ".mjs", ".cjs", ".ts", ".sh", "Makefile")


def test_canonical_databases_are_the_full_fiscal_year_backfill():
    from orchestration.sponsoratlas_orchestration.backfill import backfill_steps
    from scripts import canonical_paths as c

    steps = {s["name"]: s for s in backfill_steps(2025)}
    assert c.STAGING_DB == steps["load_raw"]["destination"]
    assert str(c.DIMENSIONS_DB) in steps["dimension_inputs"]["argv"]
    assert str(c.FACTS_DB) in steps["fact_inputs"]["argv"]
    assert str(c.ER_PREDICTION) in steps["er_predict"]["argv"]
    assert all("fy2025-full" in p.name or "fy2025-backfill" in str(p) for p in c.CANONICAL)
    assert not set(c.CANONICAL) & set(c.SUPERSEDED)
    assert [p for p in c.SUPERSEDED if p.exists()] == []


def test_no_live_code_reads_a_superseded_database():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    live = [
        p for p in tracked
        if p.endswith(CODE) and not p.startswith("docs/") and "/evidence/" not in p
        and p not in {"scripts/canonical_paths.py", "scripts/tests/test_canonical_paths.py"}
    ]  # fmt: skip
    stale = [p for p in live if Q4_ONLY.search((ROOT / p).read_text())]
    assert stale == []


def test_consumers_default_to_the_canonical_databases():
    from governance import bridge, catalog
    from scripts import canonical_paths as c
    from scripts import load_dimension_inputs, load_fact_inputs
    from semantic.scripts import export_worksite_coordinates as worksite

    assert load_fact_inputs.STAGING == load_dimension_inputs.RAW == worksite.STAGING == c.STAGING_DB
    assert load_fact_inputs.DIMENSIONS == c.DIMENSIONS_DB
    assert worksite.FACTS == c.FACTS_DB
    assert (load_dimension_inputs.INPUT, load_dimension_inputs.MODEL) == (c.ER_INPUT, c.ER_MODEL)
    assert load_dimension_inputs.PREDICTION == c.ER_PREDICTION
    assert (bridge.SOURCE_PATH, bridge.TARGET_PATH) == (c.STAGING_DB, c.FACTS_DB)
    physical = {catalog.RUNTIME / p for _, p in catalog.SERVICES.values()}
    assert {c.STAGING_DB, c.FACTS_DB} <= {p.resolve() for p in physical}
