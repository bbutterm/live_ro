"""Night-owned native components only; no paid calls or automatic retries.

Lifetime lock, private durable identity, owned process groups, fail-closed exits.
Systemd cgroup is the second shutdown boundary if this controller is killed.
"""
import argparse
import fcntl
import json
import os
import pathlib
import signal
import subprocess
import time
import uuid
from .budget import DEADLINE


def supervise(commands, directory, deadline, authorized_native=False, guard=None):
    if guard is not None: guard()
    if authorized_native:
        import math
        if type(deadline) not in (int,float) or not math.isfinite(deadline) or not 0 < deadline-time.time() <= 8*3600:
            raise RuntimeError('DEADLINE')
    else:
        deadline = min(deadline, DEADLINE)
    if time.time() >= deadline:
        raise RuntimeError('DEADLINE')
    directory = pathlib.Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (directory / 'supervisor.lock').open('a')
    os.chmod(lock.name, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        raise RuntimeError('CONTROLLER_ALREADY_RUNNING')
    children = []
    state = {'run': str(uuid.uuid4()), 'pid': os.getpid(), 'start': time.time(),
             'deadline': deadline, 'mode': 'native-only-no-paid', 'children': [], 'reason': 'RUNNING'}
    def save():
        temp = directory / 'identity.tmp'
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, directory / 'identity.json')
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    stopping = []
    previous = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.signal(sig, lambda *_: stopping.append(True))
    try:
        save()
        for cmd in commands:
            if guard is not None: guard()
            if time.time() >= deadline or stopping or (directory / 'ESTOP').exists():
                break
            log = (directory / (cmd['name'] + '.log')).open('ab', buffering=0)
            os.chmod(log.name, 0o600)
            try:
                p = subprocess.Popen(cmd['argv'], cwd=cmd['cwd'],
                    env=dict(os.environ, **cmd.get('env', {})), stdin=subprocess.DEVNULL,
                    stdout=log, stderr=log, start_new_session=True)
            finally: log.close()
            children.append(p)
            state['children'].append({'name': cmd['name'], 'pid': p.pid, 'argv': cmd['argv'],
                'cwd': cmd['cwd'], 'start_ticks': pathlib.Path(f'/proc/{p.pid}/stat').read_text().split()[21]})
            save()
        while time.time() < deadline and not stopping and not (directory / 'ESTOP').exists():
            if guard is not None: guard()
            # Observe exit without reaping: retain PID ownership until groups are stopped.
            exited = [p for p in children if os.waitid(os.P_PID, p.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None]
            if exited:
                state['reason'] = 'CHILD_EXIT_NO_RETRY'
                break
            time.sleep(0.1)
        if state['reason'] == 'RUNNING':
            state['reason'] = 'DEADLINE' if time.time() >= deadline else 'SIGNAL_OR_ESTOP'
    except BaseException:
        state['reason'] = 'CONTROLLER_FAILURE'
        raise
    finally:
        # Only unreaped children we started: their PIDs cannot have been reused.
        for p in children:
            try: os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError: pass
        end = time.monotonic() + 5
        for p in children:
            try: p.wait(timeout=max(0.01, end - time.monotonic()))
            except subprocess.TimeoutExpired:
                try: os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                p.wait(timeout=2)
        state['stopped'] = time.time()
        for row, p in zip(state['children'], children): row['exit'] = p.returncode
        save()
        for sig, handler in previous.items(): signal.signal(sig, handler)
        lock.close()
    return state


def native_commands(deadline=DEADLINE):
    base = pathlib.Path('/root/ragnarok/run/Demo')
    repo = '/root/ragnarok/repos/live_ro'
    kore = '/root/ragnarok/repos/openkore'
    if not pathlib.Path(kore, 'openkore.pl').is_file(): raise RuntimeError('OPENKORE_MISSING')
    duration = max(1, int(deadline - time.time()))
    commands = []
    for rid, cid, name in [('resident_a', 150001, 'ResidentA'), ('resident_b', 150002, 'Mira'), ('resident_c', 150003, 'Borin')]:
        d = base / rid
        if not (d / 'control/config.txt').is_file(): raise RuntimeError('CONTROL_MISSING')
        sock = d / 'actions.sock'
        # Stale endpoints require explicit inspection, never unlink a live socket here.
        if sock.exists() or pathlib.Path(str(sock) + '.control').exists(): raise RuntimeError('SOCKET_EXISTS')
        commands.append({'name': rid + '-gateway', 'cwd': repo, 'argv': ['/usr/bin/python3', '-m',
            'packages.body_gateway.service', '--socket', str(sock), '--store', str(d / 'journal.sqlite'),
            '--resident', rid, '--char-id', str(cid), '--char-name', name, '--duration', str(duration)]})
    for rid in ('resident_a', 'resident_b', 'resident_c'):
        d = base / rid
        commands.append({'name': rid + '-body', 'cwd': kore,
            'env': {'TERM': 'dumb', 'RESIDENT_CHAT_UTF8': '1', 'RESIDENT_SOCKET': str(d / 'actions.sock'),
                    'RESIDENT_ID': rid, 'RESIDENT_RUN_DIR': str(d)},
            'argv': ['/usr/bin/perl', 'openkore.pl', '--control=' + str(d / 'control'),
                '--tables=' + str(d / 'tables') + ':tables', '--fields=' + str(d / 'fields'),
                '--plugins=' + repo + '/plugins', '--logs=/root/ragnarok/logs/Demo-' + rid[-1], '--ai=auto']})
    return commands


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--native-only', action='store_true', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if time.time() >= DEADLINE: raise RuntimeError('DEADLINE')
    supervise(native_commands(), '/root/ragnarok/run/night-20261008', DEADLINE)

if __name__ == '__main__': main()
