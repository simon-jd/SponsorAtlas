"""Immutable, bounded local artifacts; no HOME cache, no RAW copies."""

import hashlib
import json
from pathlib import Path

from scripts.local_duckdb import connect_local

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".tools"


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_dir(path, *, create=False):
    path = Path(path).resolve()
    if not path.is_relative_to(TOOLS.resolve()):
        raise ValueError("ER artifacts must remain inside project .tools")
    if create:
        path.mkdir(parents=True, exist_ok=False)
    return path


def connect(path):
    path = Path(path).resolve()
    if not path.is_relative_to(TOOLS.resolve()):
        raise ValueError("ER databases must remain inside project .tools")
    c = connect_local(path)
    temp = path.parent / "spill"
    temp.mkdir(exist_ok=True)
    c.execute("SET temp_directory = " + quote(temp))
    return c
