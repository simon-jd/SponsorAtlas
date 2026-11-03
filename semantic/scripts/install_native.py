"""Install only Cube's required native extension, pinned to the official v1.8.0 asset."""

import hashlib
import json
import platform
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
URL = "https://github.com/cube-js/cube/releases/download/v1.8.0/native-linux-x64-glibc-fallback.tar.gz"
ARCHIVE_SHA = "c8ac69634063d0f375a1952d08cb33ae9e43e69b55be17e414e7eca4cb1929b5"
BINARY_SHA = "66b0029ca31686ba843fe1af06f5e215da67b97595033c09914c275ea9f65a8a"


def install():
    assert platform.system() == "Linux" and platform.machine() == "x86_64", (
        "Pinned native asset is Linux x64 glibc only"
    )
    package = ROOT / "semantic/node_modules/@cubejs-backend/native"
    assert json.loads((package / "package.json").read_text())["version"] == "1.8.0"
    archive = ROOT / ".tools/semantic/native-1.8.0-linux-x64-glibc-fallback.tar.gz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        urllib.request.urlretrieve(URL, archive)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    assert digest == ARCHIVE_SHA, "Official release archive digest mismatch"
    with tarfile.open(archive) as bundle:
        data = bundle.extractfile("native/index.node").read()
    assert hashlib.sha256(data).hexdigest() == BINARY_SHA
    target = package / "native/index.node"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(data)
    target.chmod(0o555)
    evidence = {
        "url": URL,
        "archive_sha256": digest,
        "binary_sha256": BINARY_SHA,
        "bytes": len(data),
        "target": str(target),
    }
    (archive.parent / "native-install.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence))


if __name__ == "__main__":
    install()
