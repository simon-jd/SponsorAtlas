"""Verify inherited source fingerprints plus this task's source and Parquet exports."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def signature(path):
    stat = path.stat()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"sha256": digest, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def check():
    inherited = json.loads((ROOT / ".tools/facts/load-evidence.json").read_text())[
        "protection_after"
    ]
    for name, expected in inherited.items():
        assert signature(ROOT / name) == expected, f"Inherited source changed: {name}"
    exports = []
    for kind in ("fixture", "actual"):
        evidence = json.loads((ROOT / f".tools/semantic/{kind}/export-evidence.json").read_text())
        sources = evidence["exports"] + ([evidence["source_before"]] if kind == "actual" else [])
        for expected in sources:
            actual = signature(Path(expected["path"]))
            assert actual == {
                "sha256": expected["sha256"],
                "bytes": expected["size"],
                "mtime_ns": expected["mtime_ns"],
            }, expected["path"]
            exports.append({"path": expected["path"], **actual})
    mechanism = ROOT / ".tools/semantic/readonly-external/evidence.json"
    mechanism_verified = 0
    if mechanism.exists():
        probe = json.loads(mechanism.read_text())
        current = signature(Path(probe["readonly_source"]))
        expected = probe["before"]
        assert probe["before"] == probe["after"]
        assert current["sha256"] == expected["source"] and current["bytes"] == expected["size"]
        assert str(current["mtime_ns"]) == expected["mtime_ns"]
        assert probe["driver_connections_released"]
        mechanism_verified = 1
    output = {
        "readonly_mechanism_source_verified": mechanism_verified,
        "inherited_sources_unchanged": len(inherited),
        "new_sources_exports_unchanged": len(exports),
        "verified": exports,
    }
    (ROOT / ".tools/semantic/protection-final.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({k: v for k, v in output.items() if k != "verified"}))


if __name__ == "__main__":
    check()
