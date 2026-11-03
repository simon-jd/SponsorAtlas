"""Bound a standalone Linux verification command and record catchable exits.

Only one command is launched. Subreaper adoption preserves ownership of orphaned
and detached descendants; pidfds ensure signals target those identities only.
This is trusted-command lifecycle management, not a security sandbox. SIGKILL
of the monitor and kernel-uninterruptible tasks cannot be cleaned synchronously;
incomplete bounded cleanup is reported as failure.
"""
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def process_record(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()
    return {'state': fields[0], 'parent': int(fields[1]), 'identity': fields[19],
            'rss': int(fields[21]) * os.sysconf('SC_PAGE_SIZE')}


def process_records():
    records = {}
    for item in Path('/proc').iterdir():
        if not item.name.isdigit():
            continue
        try:
            records[int(item.name)] = process_record(int(item.name))
        except (OSError, ValueError, IndexError):
            continue
    return records


def owned_records(leader):
    # This standalone monitor launches only one command. Its other direct
    # children are descendants adopted by the subreaper, including setsid()
    # children whose original parents exited between samples.
    records = process_records()
    owned = {leader}
    while True:
        descendants = {pid for pid, record in records.items()
                       if record['parent'] in owned or record['parent'] == os.getpid()}
        expanded = owned | descendants
        if expanded == owned:
            break
        owned = expanded
    return {pid: records[pid] for pid in owned if pid in records}


def signal_identity(pid, record, sig):
    # pidfds pin the target identity, so a recycled PID never receives a signal.
    try:
        descriptor = os.pidfd_open(pid)
    except ProcessLookupError:
        return False
    try:
        current = process_record(pid)
        if current['identity'] != record['identity']:
            return False
        signal.pidfd_send_signal(descriptor, sig)
        return True
    except (ProcessLookupError, FileNotFoundError):
        return False
    finally:
        os.close(descriptor)


def enable_subreaper():
    """Adopt/reap grandchildren after their parent exits, even under weak PID1."""
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'Cannot read child-subreaper state')
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'Cannot enable child subreaper')
    return libc, previous.value


def reap_adopted(leader):
    # Preserve the direct leader's wait status for subprocess.Popen.wait().
    for pid in owned_records(leader):
        if pid == leader:
            continue
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass  # Still owned by its original parent, not yet adopted.


def terminate_group(process, term_grace, kill_grace):
    outcome = {'termSent': False, 'killSent': False, 'completed': False,
               'remainingPids': [], 'reapedLeader': False, 'targetedPids': []}
    targeted = set()

    def live_members():
        reap_adopted(process.pid)
        return {pid: record for pid, record in owned_records(process.pid).items()
                if record['state'] != 'Z'}

    def stop_phase(sig, seconds):
        sent = set()
        deadline = time.monotonic() + seconds
        while members := live_members():
            for pid, record in members.items():
                identity = (pid, record['identity'])
                if identity not in sent:
                    if signal_identity(pid, record, sig):
                        outcome['termSent' if sig == signal.SIGTERM else 'killSent'] = True
                        targeted.add(pid)
                    sent.add(identity)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(.02, remaining))
        return True

    # Re-scan during each phase to cover descendants adopted as parents exit.
    if not stop_phase(signal.SIGTERM, term_grace):
        stop_phase(signal.SIGKILL, kill_grace)
    # Every wait is bounded, including the pathological uninterruptible case.
    try:
        process.wait(timeout=kill_grace)
        outcome['reapedLeader'] = True
    except subprocess.TimeoutExpired:
        pass
    reap_adopted(process.pid)
    outcome['targetedPids'] = sorted(targeted)
    outcome['remainingPids'] = sorted(owned_records(process.pid))
    outcome['completed'] = outcome['reapedLeader'] and not outcome['remainingPids']
    return outcome


def run_monitor(label, command, *, timeout_seconds=180, rss_limit_bytes=6_000_000_000,
                term_grace_seconds=1, kill_grace_seconds=1):
    start = time.monotonic()
    process = None
    peak = 0
    profiles, seen = set(), set()
    received_signal = None
    exit_code, reason, error = 1, 'monitor-error', None
    cleanup = {'termSent': False, 'killSent': False, 'completed': True,
               'remainingPids': [], 'reapedLeader': False}
    previous_handlers = {}
    subreaper = None

    def request_stop(signum, _frame):
        nonlocal received_signal
        # Do not throw during spawn or cleanup. Repeated signals never bypass
        # the bounded finally path or replace the first interruption reason.
        if received_signal is None:
            received_signal = signum

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[sig] = signal.signal(sig, request_stop)
        subreaper = enable_subreaper()
        with Path(f'evidence/{label}.log').open('w') as log:
            if received_signal is None:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
            while process is not None:
                records = owned_records(process.pid)
                seen.update(records)
                peak = max(peak, sum(record['rss'] for record in records.values()))
                for pid in records:
                    try:
                        for arg in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0'):
                            if arg.startswith(b'--user-data-dir='):
                                profiles.add(arg.decode().split('=', 1)[1])
                    except OSError:
                        pass
                if received_signal is not None:
                    break
                # Detect exit without reaping: cleanup still owns the leader wait status.
                if os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT):
                    reason = 'command-exit'
                    break
                if peak > rss_limit_bytes:
                    exit_code, reason = 125, 'rss-limit'
                    break
                if time.monotonic() - start > timeout_seconds:
                    exit_code, reason = 124, 'timeout'
                    break
                time.sleep(.05)
    except BaseException as exception:
        error = f'{type(exception).__name__}: {exception}'
    finally:
        if process is not None:
            try:
                cleanup = terminate_group(process, term_grace_seconds, kill_grace_seconds)
            except Exception as exception:
                cleanup['completed'] = False
                cleanup['remainingPids'] = sorted(owned_records(process.pid))
                error = f'Cleanup {type(exception).__name__}: {exception}'
        if received_signal is not None:
            exit_code = 128 + received_signal
            reason = f'signal:{signal.Signals(received_signal).name}'
        elif reason == 'command-exit' and process is not None:
            code = process.returncode
            exit_code = code if code is not None and code >= 0 else 128 - (code or 0)
        if not cleanup['completed']:
            exit_code = 1
            reason = 'cleanup-incomplete'
        result = {
            'command': command, 'exitCode': exit_code,
            'commandExitCode': process.returncode if process else None,
            'terminationReason': reason,
            'interruptedBy': signal.Signals(received_signal).name if received_signal else None,
            'error': error, 'cleanup': cleanup,
            'sampledPeakTreeRssBytes': peak, 'samplingSeconds': .05,
            'elapsedSeconds': round(time.monotonic() - start, 3),
            'isolatedProfiles': sorted(profiles), 'observedPids': sorted(seen),
            'limits': {'rssBytes': rss_limit_bytes, 'timeoutSeconds': timeout_seconds,
                       'termGraceSeconds': term_grace_seconds,
                       'killGraceSeconds': kill_grace_seconds},
        }
        # Evidence is attempted even after spawn errors or interrupted sampling.
        try:
            Path(f'evidence/{label}-resources.json').write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps(result))
        finally:
            if subreaper is not None:
                subreaper[0].prctl(36, subreaper[1], 0, 0, 0)
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
    return exit_code


if __name__ == '__main__':
    if len(sys.argv) < 3:
        raise SystemExit('usage: run-monitored.py LABEL COMMAND [ARGS...]')
    raise SystemExit(run_monitor(sys.argv[1], sys.argv[2:]))
