"""Bounded local rehearsal supervisor; owns and reaps its complete Airflow process tree.

Start after exporting explicit demo configuration. Touch .tools/orchestration/STOP
for a graceful stop, or send SIGINT/SIGTERM. Never finds or kills unrelated processes.
"""

import json
import os
import signal
import subprocess
import time

import psutil

from orchestration.sponsoratlas_orchestration.runtime import ROOT


def main():
    work = ROOT / ".tools/orchestration"
    stop = work / "STOP"
    if stop.exists():
        raise RuntimeError("Remove the project STOP marker explicitly before starting a rehearsal")
    stopping = False

    def request_stop(*_args):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    log = (work / "supervised-airflow.log").open("w")
    commands = [
        ["api-server", "--host", "127.0.0.1", "--port", "18780", "--apps", "execution"],
        ["dag-processor"],
        ["scheduler", "--skip-serve-logs"],
    ]
    parent = psutil.Process(os.getpid())
    processes = [
        subprocess.Popen(
            [str(ROOT / "orchestration/run.sh"), "airflow", *args],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        for args in commands
    ]
    known = {p.pid: psutil.Process(p.pid) for p in processes}
    peak, started = 0, time.monotonic()
    samples = []
    try:
        while all(p.poll() is None for p in processes) and not stopping and not stop.exists():
            live = [parent, *parent.children(recursive=True)]
            known.update({p.pid: p for p in live if p.pid != parent.pid})
            rss = 0
            for p in live:
                try:
                    rss += p.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            peak = max(peak, rss)
            samples.append(
                {
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                    "rss_bytes": rss,
                    "processes": len(live),
                }
            )
            if rss > 8_000_000_000 or time.monotonic() - started > 1200:
                raise RuntimeError("Local rehearsal exceeded 8GB RSS or 1200s budget")
            time.sleep(1)
    finally:
        try:
            known.update({p.pid: p for p in parent.children(recursive=True)})
        except psutil.NoSuchProcess:
            pass
        for p in reversed(list(known.values())):
            try:
                if p.is_running():
                    p.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(list(known.values()), timeout=10)
        for p in alive:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(alive, timeout=3)
        remaining = [p.pid for p in alive if p.status() != psutil.STATUS_ZOMBIE]
        result = {
            "peak_tree_rss_bytes": peak,
            "rss_cap_bytes": 8_000_000_000,
            "seconds": time.monotonic() - started,
            "remaining_live_owned_pids": remaining,
            "samples": samples,
        }
        (work / "supervisor-resource.json").write_text(json.dumps(result, indent=2))
        log.close()
        if remaining:
            raise RuntimeError("Project-owned Airflow process cleanup incomplete")


if __name__ == "__main__":
    main()
