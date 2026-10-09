"""Night-wide conservative ledger. Reservations are never refunded, even on crash.

Provider usage PLUS all local reservations intentionally double-counts reflected
charges. This sacrifices capacity rather than risk spending above the shared cap.
"""
import fcntl
import json
import os
import pathlib
import time
import urllib.request
from decimal import Decimal


def upper_cost(pricing, messages, max_tokens):
    try:
        # UTF-8 bytes bound text token count; large framing margin + 2x reserve.
        input_bound = len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) + 4096
        prompt, completion = Decimal(pricing['prompt']), Decimal(pricing['completion'])
        cache_read = Decimal(str(pricing.get('input_cache_read', '0')))
        extras = [Decimal(str(v)) for k, v in pricing.items()
                  if k not in ('prompt', 'completion', 'input_cache_read')]
        if (input_bound > 32768 or max_tokens != 350
                or not cache_read.is_finite() or not 0 <= cache_read <= prompt
                or not prompt.is_finite() or not completion.is_finite()
                or prompt <= 0 or completion <= 0
                or any(not v.is_finite() or v != 0 for v in extras)):
            raise ValueError('unbounded pricing/input')
        return (input_bound * prompt + max_tokens * completion) * 2
    except Exception as e:
        raise BudgetStop('UNKNOWN_COST') from e


def provider_snapshot(cfg, key):
    if cfg['base_url'].rstrip('/') != 'https://openrouter.ai/api/v1':
        raise BudgetStop('UNAPPROVED_ENDPOINT')
    def get(route):
        req = urllib.request.Request(cfg['base_url'].rstrip('/') + route,
                                     headers={'Authorization': 'Bearer ' + key})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)['data']
    try:
        usage = get('/key')['usage']
        if cfg.get('provider_only'):
            endpoints = get('/models/' + cfg['model'] + '/endpoints')['endpoints']
            model = next(e for e in endpoints if e['provider_name'] == cfg['provider_only'])
        else:
            model = next(m for m in get('/models') if m['id'] == cfg['model'])
        return usage, model['pricing']
    except Exception as e:
        raise BudgetStop('PROVIDER_ACCOUNTING_UNAVAILABLE') from e


def guarded_completion(client, guard, cfg, key, messages, *, response_schema=None):
    request = {}
    extra: dict = {'reasoning': {'enabled': False}}
    if response_schema is not None:
        if len(json.dumps(response_schema).encode('utf-8')) > 2048:
            raise BudgetStop('UNBOUNDED_RESPONSE_SCHEMA')
        request['response_format'] = {'type': 'json_schema', 'json_schema': {
            'name': 'resident_decision', 'strict': True, 'schema': response_schema}}
        extra['provider'] = {'only': ['DeepInfra'], 'allow_fallbacks': False, 'require_parameters': True}
    routing_cfg = dict(cfg, provider_only='DeepInfra') if response_schema is not None else cfg
    usage, pricing = provider_snapshot(routing_cfg, key)
    guard.reserve(usage, upper_cost(pricing, messages, 350), now=time.time())
    # Reservation is fsynced before request, never refunded on timeout/error.
    try:
        return client.chat.completions.create(
            model=cfg['model'], messages=messages, max_tokens=350, timeout=45,
            extra_body=extra, **request)
    except Exception as e:
        raise BudgetStop('PAID_CALL_FAILED_NO_RETRY') from e

BASELINE = '0.012724245'
LIMIT = '2'
DEADLINE = 1791432000  # 2026-10-08T04:00:00Z; tested against UTC datetime

class BudgetStop(RuntimeError):
    pass

class Budget:
    @staticmethod
    def initialize(path, key_id):
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as e:
            raise BudgetStop('LEDGER_ALREADY_EXISTS') from e
        with os.fdopen(fd, 'w') as f:
            json.dump({'baseline': BASELINE, 'limit': LIMIT, 'key_id': key_id,
                       'last_usage': BASELINE, 'reservations': []}, f)
            f.flush()
            os.fsync(f.fileno())

    def __init__(self, path, key_id):
        self.path = pathlib.Path(path)
        self.key_id = key_id
        self.lock = None

    def __enter__(self):
        self.lock = open(str(self.path) + '.lock', 'a')
        os.chmod(self.lock.name, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.state = json.loads(self.path.read_text())
            if (self.state['baseline'] != BASELINE or self.state['limit'] != LIMIT
                    or self.state['key_id'] != self.key_id
                    or self.path.stat().st_mode & 0o077):
                raise ValueError('identity or permissions')
        except Exception as e:
            self.lock.close()
            self.lock = None
            raise BudgetStop('LEDGER_UNAVAILABLE') from e
        return self

    def __exit__(self, *_):
        self.lock.close()
        self.lock = None

    def reserve(self, usage, upper_cost, now):
        try:
            usage, cost = Decimal(str(usage)), Decimal(str(upper_cost))
            if self.lock is None:
                raise ValueError('lock required')
            previous = Decimal(self.state['last_usage'])
            reservations = [Decimal(x['upper_cost']) for x in self.state['reservations']]
            if not previous.is_finite() or any(not x.is_finite() or x <= 0 for x in reservations):
                raise ValueError('invalid ledger')
            spent = sum(reservations, Decimal(0))
            if (not usage.is_finite() or not cost.is_finite() or cost <= 0
                    or usage < previous or previous < Decimal(BASELINE)
                    or now + 60 >= DEADLINE  # leave time for 45s paid request to terminate
                    or usage - Decimal(BASELINE) + spent + cost > Decimal(LIMIT)):
                raise ValueError('budget, deadline or unknown usage')
        except Exception as e:
            raise BudgetStop('CALL_NOT_AUTHORIZED') from e
        self.state['last_usage'] = str(usage)
        self.state['reservations'].append({'upper_cost': str(cost), 'ts': now})
        temp = self.path.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(self.state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self.path)
        fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return len(self.state['reservations'])
