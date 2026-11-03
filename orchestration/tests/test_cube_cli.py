"""Real CLI persistence and gateway restart, checked after all native connections close."""

import json
import os
import re
import select
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen

import jwt

ROOT = Path(__file__).resolve().parents[2]
NODE = ROOT / ".tools/node-runtime/node-v24.22.1-linux-x64/bin/node"


def physical_tables(work):
    code = """import duckdb,json,sys
with duckdb.connect(sys.argv[1],read_only=True,config={'threads':1,'memory_limit':'128MB'}) as c:
 names=c.execute("select table_name from information_schema.tables where table_schema='semantic_preagg' and table_type='BASE TABLE' order by 1").fetchall()
 print(json.dumps([{'table':n,'rows':c.execute('SELECT * FROM semantic_preagg."'+n.replace('"','""')+'" ORDER BY ALL').fetchall()} for (n,) in names],default=str))
"""
    result = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-c", code, str(work / "scratch/cube.duckdb")],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_real_refresh_cli_persists_and_gateway_reuses_same_revision(tmp_path):
    original = ROOT / ".tools/semantic/fixture"
    manifest = json.loads((original / "export-evidence.json").read_text())
    for item in manifest["exports"]:
        name = Path(item["path"]).name
        shutil.copyfile(original / name, tmp_path / name)
        (tmp_path / name).chmod(0o444)
        item["path"] = str(tmp_path / name)
    (tmp_path / "export-evidence.json").write_text(json.dumps(manifest))
    secret = "synthetic-cli-test-secret-with-32-bytes-minimum"
    env = {
        **os.environ,
        "SEMANTIC_SOURCE": "parquet",
        "SEMANTIC_WORK": str(tmp_path),
        "CUBEJS_API_SECRET": secret,
        # bind.cjs refuses port 0, so reserve a free unprivileged port up front.
        "PORT": str(free_port()),
    }
    command = [str(NODE), "--max-old-space-size=768", "scripts/refresh.cjs"]
    first = subprocess.run(
        command,
        cwd=ROOT / "semantic",
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert first.returncode == 0, first.stderr
    before = physical_tables(tmp_path)
    assert len(before) == 2, (
        "REST usedPreAggregations is insufficient: both closed-file tables must persist"
    )
    ready = (tmp_path / "refresh-state.json").read_bytes()
    child = subprocess.Popen(
        [str(NODE), "--max-old-space-size=768", "server.cjs"],
        cwd=ROOT / "semantic",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        port = None
        output = []
        while time.monotonic() < deadline and child.poll() is None:
            if select.select([child.stdout], [], [], 0.1)[0]:
                line = child.stdout.readline()
                output.append(line)
                match = re.search(r"REST listening on 127\.0\.0\.1:(\d+)", line)
                if match:
                    port = int(match[1])
                    break
        assert port, "Gateway did not start:\n" + "".join(output)
        blocked = subprocess.run(
            command,
            cwd=ROOT / "semantic",
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert blocked.returncode != 0
        assert (tmp_path / "refresh-state.json").read_bytes() == ready
        token = jwt.encode({"sub": "synthetic-restart-test", "exp": int(time.time()) + 60}, secret)
        request = Request(
            f"http://127.0.0.1:{port}/cubejs-api/v1/load",
            method="POST",
            data=json.dumps(
                {"query": {"measures": ["postings.count"]}, "cache": "must-revalidate"}
            ).encode(),
            headers={"Authorization": token, "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=10) as response:
            result = json.load(response)
        assert result["data"][0]["postings.count"] == "4"
    finally:
        child.terminate()
        try:
            child.wait(timeout=7)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)
    after = physical_tables(tmp_path)
    assert child.returncode == 0, "Gateway must finish its awaited SIGTERM shutdown without KILL"
    assert after == before, "Same ready revision must reuse exact closed-file tables and rows"
    assert (tmp_path / "refresh-state.json").read_bytes() == ready
    assert not (tmp_path / "work-owner.json").exists()
    # Tracked evidence is refreshed only on request, so a normal test run leaves the tree clean.
    if os.environ.get("SPONSORATLAS_WRITE_EVIDENCE") != "1":
        return
    (ROOT / "orchestration/evidence/cube-cli-persistence.json").write_text(
        json.dumps(
            {
                "kind": "SYNTHETIC_REAL_CLI_AND_GATEWAY_RESTART_NOT_SNOWFLAKE",
                "ready_revision": json.loads(ready)["revision"],
                "tables_after_cli_closed": before,
                "tables_after_gateway_closed": after,
                "gateway_data": result["data"],
                "active_gateway_refresh_refused": True,
                "cli_exit_code_after_awaited_shutdown": first.returncode,
                "gateway_exit_code_after_sigterm_awaited_shutdown": child.returncode,
                "closed_file_rows_and_ready_revision_reused": True,
            },
            indent=2,
        )
    )
