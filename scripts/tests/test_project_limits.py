import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD_SHARE_LITERALS = re.compile(r"18_000_000_000|18 \* 10\*\*9|18e9")


def test_disk_share_is_the_budgeted_36_gb():
    from scripts.project_limits import DISK_SHARE_BYTES

    assert DISK_SHARE_BYTES == 36 * 10**9


def test_every_live_guard_uses_the_shared_constant():
    from orchestration.sponsoratlas_orchestration import backfill
    from scripts.project_limits import DISK_SHARE_BYTES

    assert backfill.SHARE_BYTES == DISK_SHARE_BYTES
    tracked = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    live = [p for p in tracked if not p.startswith("docs/") and "/tests/" not in p]
    stale = [p for p in live if OLD_SHARE_LITERALS.search((ROOT / p).read_text())]
    assert stale == []


def test_phase4_fact_projection_now_fits(tmp_path, monkeypatch):
    import sys

    from orchestration.sponsoratlas_orchestration import backfill

    staging = tmp_path / "s.duckdb"
    staging.write_bytes(b"x" * 1000)
    step = {
        "name": "fact_inputs",
        "argv": [sys.executable, "-c", "pass"],
        "reserve_staging_factor": 1.6,
        "staging": staging,
    }
    # The 2026-10-28 refusal: 20.72 GB projected against the old 18 GB share.
    monkeypatch.setattr(backfill, "project_bytes", lambda: 20_720_000_000 - 1600)
    result = backfill.run_steps([step], tmp_path / "out.json", logs=tmp_path)
    assert result["status"] == "SUCCEEDED"
