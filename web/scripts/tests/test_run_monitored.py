"""Harmless, real subprocess lifecycle coverage; no browser or service startup."""
import ctypes
import json
import os
import resource
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

MONITOR = Path(__file__).resolve().parents[1] / "run-monitored.py"
CACHE = MONITOR.parents[1] / ".cache"
FIXTURE = r'''
import json, os, pathlib, signal, subprocess, sys, time
path=pathlib.Path(sys.argv[1]); mode=sys.argv[2]
if mode == 'ignore': signal.signal(signal.SIGTERM, signal.SIG_IGN)
child_code="import pathlib,signal,sys,time; " + ("signal.signal(signal.SIGTERM,signal.SIG_IGN); " if mode=='ignore' else "") + "pathlib.Path(sys.argv[1]).write_text('ready'); time.sleep(60)"
child=subprocess.Popen([sys.executable,'-c',child_code,str(path)+'.child'],start_new_session=mode=='detached-leader-exit')
while not pathlib.Path(str(path)+'.child').exists():time.sleep(.01)
path.write_text(json.dumps({'leader':os.getpid(),'child':child.pid,'pgid':os.getpgrp()}))
print('fixture-ready',flush=True)
if mode in ('leader-exit','detached-leader-exit'):sys.exit(0)
time.sleep(60)
'''


class MonitorLifecycle(unittest.TestCase):
    outcomes = []

    @classmethod
    def tearDownClass(cls):
        if destination := os.environ.get("MONITOR_LIFECYCLE_EVIDENCE"):
            Path(destination).write_text(json.dumps({
                "cases": cls.outcomes,
                "harnessMaxRssBytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                "remainingTestDirectories": list(map(str, CACHE.glob("monitor-lifecycle-*"))),
            }, indent=2) + "\n")

    def retain(self, result):
        self.outcomes.append({"test": self.id(), "result": result})

    @classmethod
    def setUpClass(cls):
        # Reap any orphaned fixture children even when testing the broken RED version.
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), 'Cannot establish test subreaper')
        CACHE.mkdir(exist_ok=True)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='monitor-lifecycle-', dir=CACHE)
        self.work = Path(self.directory.name)
        (self.work / 'evidence').mkdir()
        self.monitors = []
        self.groups = []

    def tearDown(self):
        # Cleanup is deliberately independent of the monitor under test.
        for process in self.monitors:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=2)
        for group in self.groups:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    pid, _ = os.waitpid(-group, os.WNOHANG)
                except ChildProcessError:
                    break
                if pid == 0:
                    time.sleep(.01)
        self.directory.cleanup()

    def launch(self, mode='wait', budget=None):
        marker = self.work / 'pids.json'
        command = [sys.executable, '-c', FIXTURE, str(marker), mode]
        if budget:
            loader = (
                'import importlib.util,sys; '
                f's=importlib.util.spec_from_file_location("monitor",{str(MONITOR)!r}); '
                'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                f'sys.exit(m.run_monitor("case", {command!r}, {budget}))'
            )
            argv = [sys.executable, '-c', loader]
        else:
            argv = [sys.executable, str(MONITOR), 'case', *command]
        process = subprocess.Popen(argv, cwd=self.work, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.monitors.append(process)
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(f'Monitor exited before fixture readiness: {process.communicate()}')
            time.sleep(.01)
        self.assertTrue(marker.exists(), 'Fixture readiness deadline')
        ids = json.loads(marker.read_text())
        self.groups.append(ids['pgid'])
        if mode == 'detached-leader-exit':
            self.groups.append(ids['child'])
        return process, ids

    def finish(self, process, ids, expected_exit):
        output, errors = process.communicate(timeout=5)
        self.assertEqual(process.returncode, expected_exit, (output, errors))
        for pid in (ids['leader'], ids['child']):
            self.assertFalse(Path(f'/proc/{pid}').exists(), f'Owned PID {pid} remains, including zombies')
        result = json.loads((self.work / 'evidence/case-resources.json').read_text())
        self.assertEqual(result['exitCode'], expected_exit)
        self.assertTrue(result['cleanup']['completed'], result)
        self.assertEqual(result['cleanup']['remainingPids'], [])
        self.assertEqual(json.loads(output), result)
        self.retain(result)
        return result

    def test_sigint_cleans_descendants_and_records_outcome(self):
        process, ids = self.launch()
        process.send_signal(signal.SIGINT)
        result = self.finish(process, ids, 130)
        self.assertEqual(result['terminationReason'], 'signal:SIGINT')
        self.assertTrue(result['cleanup']['termSent'])

    def test_sigterm_cleans_descendants_and_records_outcome(self):
        process, ids = self.launch()
        process.send_signal(signal.SIGTERM)
        result = self.finish(process, ids, 143)
        self.assertEqual(result['terminationReason'], 'signal:SIGTERM')

    def test_exited_leader_does_not_leave_live_descendant(self):
        process, ids = self.launch('leader-exit')
        result = self.finish(process, ids, 0)
        self.assertEqual(result['commandExitCode'], 0)
        self.assertEqual(result['terminationReason'], 'command-exit')
        self.assertTrue(result['cleanup']['termSent'])

    def test_detached_child_is_reaped_after_leader_exit(self):
        process, ids = self.launch('detached-leader-exit')
        result = self.finish(process, ids, 0)
        self.assertEqual(result['terminationReason'], 'command-exit')
        self.assertTrue(result['cleanup']['termSent'])

    def test_ignored_term_escalates_to_bounded_kill(self):
        process, ids = self.launch('ignore')
        start = time.monotonic()
        process.send_signal(signal.SIGTERM)
        result = self.finish(process, ids, 143)
        self.assertLess(time.monotonic() - start, 4)
        self.assertTrue(result['cleanup']['killSent'])

    def test_timeout_also_bounds_ignored_term(self):
        process, ids = self.launch('ignore', 'timeout_seconds=0.3, term_grace_seconds=0.15, kill_grace_seconds=0.5')
        result = self.finish(process, ids, 124)
        self.assertEqual(result['terminationReason'], 'timeout')
        self.assertTrue(result['cleanup']['killSent'])

    def test_normal_return_and_output_are_preserved(self):
        process = subprocess.Popen([sys.executable, str(MONITOR), 'case', sys.executable,
                                    '-c', 'print("normal-output"); raise SystemExit(7)'],
                                   cwd=self.work, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        self.monitors.append(process)
        output, errors = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 7, errors)
        result = json.loads(output)
        self.assertEqual(result['exitCode'], 7)
        self.assertEqual((self.work / 'evidence/case.log').read_text(), 'normal-output\n')
        self.assertTrue(result['cleanup']['completed'])
        self.assertFalse(result['cleanup']['killSent'])
        self.retain(result)


    def test_spawn_error_still_records_final_outcome(self):
        process = subprocess.Popen([sys.executable, str(MONITOR), 'case',
                                    str(self.work / 'nonexistent-command')],
                                   cwd=self.work, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        self.monitors.append(process)
        output, errors = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 1, errors)
        result = json.loads(output)
        self.assertEqual(result['terminationReason'], 'monitor-error')
        self.assertIn('FileNotFoundError', result['error'])
        self.assertTrue(result['cleanup']['completed'])
        self.assertIsNone(result['commandExitCode'])
        self.assertEqual(result, json.loads((self.work / 'evidence/case-resources.json').read_text()))
        self.retain(result)

    def test_rss_limit_records_bounded_cleanup(self):
        code = (
            'import importlib.util,sys; '
            f's=importlib.util.spec_from_file_location("monitor",{str(MONITOR)!r}); '
            'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
            'sys.exit(m.run_monitor("case",[sys.executable,"-c","import time; time.sleep(60)"],rss_limit_bytes=1))'
        )
        process = subprocess.Popen([sys.executable, '-c', code], cwd=self.work,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        self.monitors.append(process)
        output, errors = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 125, errors)
        result = json.loads(output)
        self.assertEqual(result['terminationReason'], 'rss-limit')
        self.assertTrue(result['cleanup']['completed'])
        for pid in result['observedPids']:
            self.assertFalse(Path(f'/proc/{pid}').exists(), f'Budgeted PID {pid} remains')
        self.retain(result)

if __name__ == '__main__':
    unittest.main(verbosity=2)
