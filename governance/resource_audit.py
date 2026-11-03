"""Conservative project allocation, including external Docker storage; no global cleanup."""

import datetime
import json
import os
import subprocess
from pathlib import Path

from scripts.project_limits import DISK_SHARE_BYTES

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ["postgres:16-alpine", "eclipse-temurin:21-jre-jammy"]


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def audit():
    workspace_bytes = 0
    unreadable = []
    for parent, _, files in os.walk(ROOT, onerror=lambda e: unreadable.append(e.filename)):
        for file in files:
            try:
                workspace_bytes += (Path(parent) / file).stat().st_size
            except OSError:
                unreadable.append(str(Path(parent) / file))
    images = {name: json.loads(command("docker", "image", "inspect", name))[0] for name in IMAGES}
    image_bytes = sum(image["Size"] for image in images.values())
    ids = command(
        "docker",
        "ps",
        "-aq",
        "--filter",
        "label=com.docker.compose.project=sponsoratlas-governance",
    ).split()
    containers = [json.loads(command("docker", "inspect", "--size", i))[0] for i in ids]
    pg_data = int(
        command(
            "docker",
            "exec",
            "sponsoratlas-governance-postgres-1",
            "du",
            "-sb",
            "/var/lib/postgresql/data",
        ).split()[0]
    )
    memory = {}
    for c in containers:
        if c["State"]["Running"]:
            memory[c["Name"]] = {
                "cap_bytes": c["HostConfig"]["Memory"],
                "peak_bytes": int(
                    command("docker", "exec", c["Id"], "cat", "/sys/fs/cgroup/memory.peak")
                ),
            }
    # Double image sizes covers both content blobs and extracted snapshots, even when
    # Engine's Size already accounts for both. PG bind data is unreadable on host.
    # No image builds or named volumes in this project. Reserve max 30 MB Docker logs.
    total = (
        workspace_bytes
        + 2 * image_bytes
        + pg_data
        + sum(c.get("SizeRw", 0) for c in containers)
        + 30_000_000
    )
    result = {
        "time_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "workspace_path_bytes_including_file_symlink_targets": workspace_bytes,
        "docker_image_reported_bytes": image_bytes,
        "docker_image_ids": {n: i["Id"] for n, i in images.items()},
        "postgres_data_bytes_added": pg_data,
        "docker_log_reserve_bytes": 30_000_000,
        "project_build_cache_bytes": 0,
        "project_named_volume_bytes": 0,
        "container_writable_bytes": sum(c.get("SizeRw", 0) for c in containers),
        "conservative_total_bytes": total,
        "cap_bytes": DISK_SHARE_BYTES,
        "container_memory": memory,
        "unreadable_host_paths": unreadable,
    }
    assert total < DISK_SHARE_BYTES, result
    assert sum(m["cap_bytes"] for m in memory.values()) <= 7_000_000_000
    (ROOT / ".tools/governance/evidence/resource-final-running.json").write_text(
        json.dumps(result, indent=2)
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("unreadable_host_paths", "docker_image_ids")
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    audit()
