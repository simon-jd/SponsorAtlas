"""Use the already reviewed process-tree monitor, with the Task16 limits."""
import importlib.util
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('reviewed_monitor', root / 'web/scripts/run-monitored.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
os.chdir(root / 'azure/fabric')
Path('evidence').mkdir(exist_ok=True)
os.environ['PYTHONPATH'] = str(root)
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
raise SystemExit(monitor.run_monitor(sys.argv[1], sys.argv[2:], timeout_seconds=180, rss_limit_bytes=2_000_000_000))
