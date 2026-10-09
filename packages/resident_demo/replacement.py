"""Native replacement wiring; canonical checkpoints and journals are never reset."""
from . import supervisor

import json
import pathlib


def check_state(path):
    state = json.loads(pathlib.Path(path).read_text())
    if ('pending' not in state or 'blocked' not in state or
            state['pending'] is not None or state['blocked'] is not None):
        raise RuntimeError('UNRESOLVED_STATE')
    if (state.get('version') != 1 or state.get('resident') != 'resident_a' or
            state.get('hunt_seconds') != 60 or type(state.get('cycles')) is not int or
            state['cycles'] < 0 or state.get('phase') not in
            ('field','hunt','town','sell','buy','rest','return')):
        raise RuntimeError('STATE_IDENTITY')
    return state


REPO = '/root/ragnarok/repos/live_ro'
STATE = '/root/ragnarok/run/autonomy-20261008/life-state.json'


def build_commands(deadline, now):
    commands = supervisor.native_commands(deadline=deadline)
    commands.append(dict(name='resident_a-life', cwd=REPO, argv=[
        '/usr/bin/python3', '-u', '-m', 'packages.resident_demo.life_cycle', '--execute',
        '--state', STATE, '--socket', '/root/ragnarok/run/Demo/resident_a/actions.sock.control',
        '--duration', '21600', '--deadline', str(deadline), '--hunt-seconds', '60']))
    return commands


def admit(register, stops, deadline, now, stopped):
    import json, math, pathlib
    try:
        authorization = json.loads(pathlib.Path(register).read_text())
    except (OSError, ValueError):
        raise RuntimeError('AUTHORIZATION')
    if authorization.get('authorized') is not True:
        raise RuntimeError('AUTHORIZATION')
    if any(pathlib.Path(p).exists() for p in stops):
        raise RuntimeError('ESTOP')
    cap = authorization.get('max_native_lease_seconds')
    if (type(cap) is not int or not 0 < cap <= 28800 or
            type(deadline) not in (int, float) or not math.isfinite(deadline) or
            not 0 < deadline-now <= min(cap, 21600)):
        raise RuntimeError('DEADLINE')
    if stopped is not None and (type(stopped) not in (int,float) or
            not math.isfinite(stopped) or stopped < 0 or now-stopped < 60):
        raise RuntimeError('BACKOFF')


def require_offline(query_fn=None):
    """Authoritative admission, not a snapshot granting future runtime ownership."""
    from tools.events import query, DEFAULTS
    rows = (query_fn or query)(DEFAULTS,
        'SELECT char_id,name,online FROM ro_residents_main.char '
        'WHERE char_id IN (150000,150001,150002,150003)').strip().splitlines()
    expected = {'150000': 'Tester', '150001': 'ResidentA',
                '150002': 'Mira', '150003': 'Borin'}
    parsed = [row.split('\t') for row in rows]
    if (len(parsed) != len(expected) or
            any(len(row) != 3 for row in parsed) or
            {row[0]: row[1] for row in parsed} != expected or
            any(row[2] not in ('0', '1') for row in parsed)):
        raise RuntimeError('AUTHORITATIVE_IDENTITY')
    if any(row[2] != '0' for row in parsed):
        raise RuntimeError('AUTHORITATIVE_ONLINE')
    return rows


def launch(directory, deadline, root=pathlib.Path('/root/ragnarok/run')):
    import time, os, fcntl, sqlite3, hashlib
    from packages.body_gateway.journal import TERMINAL
    root=pathlib.Path(root);directory=pathlib.Path(directory)
    register=root/'CONTINUOUS_REGISTER.json'
    old=root/'autonomy-20261008'
    stops=[root/'ESTOP',old/'ESTOP',directory/'ESTOP']
    previous_path=root/'NATIVE_CURRENT.json'
    previous=json.loads(previous_path.read_text()) if previous_path.exists() else None
    prior_directory=pathlib.Path(previous['directory']) if previous else old
    identity=json.loads((prior_directory/'identity.json').read_text())
    stopped=identity.get('stopped')
    if stopped is None: raise RuntimeError('PREVIOUS_RUNTIME_UNRECONCILED')
    admit(register,stops,deadline,time.time(),stopped)
    os.umask(0o077)
    with (old/'supervisor.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with (root/'private/night-budget.json.lock').open('a') as writer:
            fcntl.flock(writer,fcntl.LOCK_EX|fcntl.LOCK_NB)
            for child in identity['children']:
                stat=pathlib.Path('/proc')/str(child['pid'])/'stat'
                if stat.exists() and stat.read_text().split(') ',1)[1].split()[19]==child['start_ticks']:
                    raise RuntimeError('OWNED_CHILD_STILL_PRESENT')
            state=old/'life-state.json'
            check_state(state)
            for actor in ('resident_a','resident_b','resident_c'):
                with sqlite3.connect(f'file:{root}/Demo/{actor}/journal.sqlite?mode=ro',uri=True) as db:
                    if any(row[0] not in TERMINAL for row in db.execute('SELECT state FROM actions')):
                        raise RuntimeError('NONTERMINAL_JOURNAL')
            require_offline()
        commands=build_commands(deadline,time.time())
        # Exclusive evidence directory; no rewriting historical launches.
        directory.mkdir(mode=0o700)
        sources=['packages/resident_demo/replacement.py','packages/resident_demo/supervisor.py',
                 'packages/resident_demo/life_cycle.py','packages/body_gateway/service.py',
                 'plugins/residentBody/residentBody.pl']
        controls={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                  for actor in ('resident_a','resident_b','resident_c')
                  for p in (root/'Demo'/actor/'control').glob('*') if p.is_file()}
        record=dict(started=time.time(),deadline=deadline,directory=str(directory),
                    unit=os.environ.get('NATIVE_UNIT'),invocation=os.environ.get('INVOCATION_ID'),
                    mode='native-only-no-paid',paid_calls=0,state=str(state),
                    state_sha256=hashlib.sha256(state.read_bytes()).hexdigest(),controls_sha256=controls,
                    source_sha256={p:hashlib.sha256(pathlib.Path(REPO,p).read_bytes()).hexdigest() for p in sources},
                    commands=commands,backoff_seconds=60,restart_policy='fail-closed; next authorized reconciled launch only')
        for p in [directory/'launch.json',root/'NATIVE_CURRENT.json']:
            temp=p.with_suffix('.tmp')
            with temp.open('w') as f:
                json.dump(record,f,indent=2);f.flush();os.fsync(f.fileno())
            os.replace(temp,p)
            fd=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        guard=lambda: admit(register,stops,deadline,time.time(),None)
        return supervisor.supervise(commands,directory,deadline,authorized_native=True,guard=guard)


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',required=True)
    parser.add_argument('--deadline',required=True,type=float)
    args=parser.parse_args()
    launch(args.directory,args.deadline)
