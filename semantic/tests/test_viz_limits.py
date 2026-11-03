"""One set of map projection limits, sized from the measured full-year projection (phase 6).

Measured 2026-10-28 on the full-year facts (docs/evidence/task14-fullyear-map/): 517,991 frequency
rows, 103,036,467 bytes, at most 12,701 H3 cells in one scope (all years, resolution 7).
"""

import re
from pathlib import Path

from semantic.scripts import export_worksite_coordinates as exporter

ROOT = Path(__file__).resolve().parents[2]
LIMITS_TS = ROOT / "web/apps/site/lib/viz-limits.ts"
READERS = [
    "web/apps/site/lib/viz-source.ts",
    "web/apps/site/lib/viz-data.ts",
    "web/apps/site/deploy/package-swa.mjs",
    "web/apps/site/deploy/stage.mjs",
    "semantic/scripts/export_worksite_coordinates.py",
]
FULL_YEAR = {"maxFrequencyRows": 517_991, "maxFrequencyBytes": 103_036_467, "maxCells": 12_701}


def test_exporter_reads_the_shared_limits():
    limits = exporter.site_limits()
    assert set(limits) == set(FULL_YEAR)
    assert (exporter.MAX_ROWS, exporter.MAX_BYTES) == (
        limits["maxFrequencyRows"],
        limits["maxFrequencyBytes"],
    )


def test_the_measured_full_year_projection_fits_with_bounded_headroom():
    limits = exporter.site_limits()
    for name, measured in FULL_YEAR.items():
        # Room for a re-export of the same year, not for a silent second year.
        assert measured <= limits[name] <= 1.3 * measured, (name, limits[name])


def test_no_reader_keeps_its_own_copy_of_a_limit():
    old = re.compile(r"45_?000_?000|300_?000\b|cells\.(length|size)\s*>\s*10_?000")
    for path in READERS:
        assert not old.search((ROOT / path).read_text()), path
    for path in READERS[:4]:
        assert "viz-limits" in (ROOT / path).read_text(), path
