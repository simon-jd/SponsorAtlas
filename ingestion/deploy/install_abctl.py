#!/usr/bin/env python3
"""Download unmodified pinned upstream abctl into project-local .tools; never install Airbyte."""

import hashlib
import io
import platform
import tarfile
import urllib.request
from pathlib import Path

VERSION = "0.30.4"
CHECKSUMS = {
    "x86_64": ("amd64", "bcd46d55d5e515fc2ccc92cd67d493825640c2977fb2d3dd81a5c965ef545534"),
    "aarch64": ("arm64", "b471cfd849e6ca6749fba76b0e7354a78e6c7b37f5de887efaff1fbec9bdd9d6"),
}


def main():
    if platform.system() != "Linux" or platform.machine() not in CHECKSUMS:
        raise SystemExit("This installer targets Linux amd64/arm64 dedicated VMs")
    architecture, expected = CHECKSUMS[platform.machine()]
    root = Path(__file__).resolve().parents[2]
    target = root / ".tools" / "abctl" / VERSION / "abctl"
    if target.exists():
        raise SystemExit(
            "Pinned abctl path exists; remove only that project-local binary to reinstall"
        )
    url = (
        f"https://github.com/airbytehq/abctl/releases/download/v{VERSION}/"
        f"abctl-v{VERSION}-linux-{architecture}.tar.gz"
    )
    with urllib.request.urlopen(url, timeout=90) as response:
        data = response.read(100 * 1024**2)
    if hashlib.sha256(data).hexdigest() != expected:
        raise SystemExit("abctl SHA256 verification failed")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = [m for m in archive.getmembers() if m.isfile() and Path(m.name).name == "abctl"]
        if len(members) != 1:
            raise SystemExit("Expected exactly one abctl executable")
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.extractfile(members[0]) as binary, target.open("xb") as output:
            output.write(binary.read())
    target.chmod(0o755)
    print(f"Verified unmodified abctl {VERSION} archive SHA256={expected}; binary={target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
