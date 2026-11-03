"""Conservative project disk accounting, reusing the stopped governance PG charge."""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from orchestration.sponsoratlas_orchestration.runtime import ROOT
from scripts.project_limits import DISK_SHARE_BYTES


def audit():
    baseline = json.loads((ROOT / "governance/evidence/resource-final-running.json").read_text())
    latest_path = ROOT / ".tools/resource-audit-task9-allocated-20261024.json"
    latest = json.loads(latest_path.read_text()) if latest_path.exists() else {}
    pg_charge = max(
        baseline["postgres_data_bytes_added"], latest.get("postgres_counted_bytes", 0), 111_742_976
    )
    errors, workspace = [], 0
    for parent, _, files in os.walk(ROOT, onerror=lambda e: errors.append(e.filename)):
        for name in files:
            try:
                workspace += (Path(parent) / name).stat().st_size
            except OSError as e:
                errors.append(str(e.filename))
    du = subprocess.run(["du", "-sB1", str(ROOT)], capture_output=True, text=True, check=False)
    allocated = int(du.stdout.split()[0])
    counted = max(workspace, allocated)
    images = {}
    for name in ["postgres:16-alpine", "eclipse-temurin:21-jre-jammy"]:
        info = json.loads(subprocess.check_output(["docker", "image", "inspect", name], text=True))[
            0
        ]
        images[name] = {"id": info["Id"], "bytes": info["Size"]}
    ids = subprocess.check_output(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project=sponsoratlas-governance",
        ],
        text=True,
    ).split()
    containers = [
        json.loads(subprocess.check_output(["docker", "inspect", "--size", i], text=True))[0]
        for i in ids
    ]
    if any(c["State"]["Running"] for c in containers):
        raise RuntimeError(
            "Governance services must stay stopped during this bounded orchestration audit"
        )
    # No new image/volume/download cache was introduced by orchestration. Count content
    # blobs and extracted layers twice as in the independently reviewed baseline.
    external = (
        2 * sum(i["bytes"] for i in images.values())
        + pg_charge
        + sum(c.get("SizeRw", 0) for c in containers)
        + 30_000_000
    )
    result = {
        "time_utc": datetime.now(UTC).isoformat(),
        "workspace_path_bytes": workspace,
        "workspace_allocated_bytes_excluding_pg": allocated,
        "workspace_counted_bytes": counted,
        "workspace_counting_rule": "max(path including symlinks, allocated du) plus PG separately",
        "docker_images": images,
        "docker_image_multiplier": 2,
        "stopped_governance_pg_data_charge_bytes": pg_charge,
        "docker_writable_bytes": sum(c.get("SizeRw", 0) for c in containers),
        "docker_logs_reserved_bytes": 30_000_000,
        "orchestration_new_images_or_volumes_bytes": 0,
        "conservative_total_bytes": counted + external,
        "cap_bytes": DISK_SHARE_BYTES,
        "walk_errors": errors,
    }
    assert result["conservative_total_bytes"] < result["cap_bytes"]
    return result


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
