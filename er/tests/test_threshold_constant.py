"""The deployed ER match threshold lives only in er.settings.THRESHOLD."""

import re
import subprocess
from pathlib import Path

from er.settings import CURVE, THRESHOLD

ROOT = Path(__file__).resolve().parents[2]
# Code and the docs that state the current configuration. Tests, evidence and the dated
# history in docs/dev/evidence/entity-resolution.md are out of scope.
SCANNED = ("er", "scripts", "orchestration", "dbt", "semantic", "governance", "Makefile")
SUFFIXES = {".py", ".sql", ".yml", ".yaml", ".json", ".md", ".sh", ".cjs", ".js", ""}
# A threshold word followed by a number, or a probability compared with a literal.
PATTERNS = [
    re.compile(r"(?i)(threshold|阈值)[\"'`*\s:=（(]{0,6}(0\.\d+|\d/\d)"),
    re.compile(r"(match_probability|\bp)\s*[<>]=?\s*0\.\d+"),
]


def tracked_files():
    out = subprocess.run(
        ["git", "ls-files", "--", *SCANNED], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    for name in out:
        path = Path(name)
        if "tests" in path.parts or "evidence" in path.parts or path.suffix not in SUFFIXES:
            continue
        if path.name.endswith(".lock") or path == Path("er/settings.py"):
            continue
        yield path


def literals(text):
    for number, line in enumerate(text.splitlines(), 1):
        for pattern in PATTERNS:
            for match in pattern.finditer(line):
                yield number, match.group(0)


def test_threshold_is_the_user_decision_of_2026_10_29():
    assert THRESHOLD == 0.875 == 7 / 8
    assert {0.85, 0.875, 0.9} <= set(CURVE)
    assert list(CURVE) == sorted(CURVE)


def test_scanner_catches_threshold_literals():
    samples = ["threshold=0.85", '"threshold": 0.9', "阈值 **0.9**", "（阈值0.9）", "p < 0.875"]
    assert all(list(literals(s)) for s in samples)
    assert not list(literals("threshold=THRESHOLD; recall >= 0.90; precision >= 0.97"))


def test_no_other_threshold_literal_outside_settings():
    found = []
    for path in tracked_files():
        text = (ROOT / path).read_text(errors="ignore")
        found += [f"{path}:{n}: {m}" for n, m in literals(text)]
    assert found == [], "threshold literals must read er.settings.THRESHOLD:\n" + "\n".join(found)
