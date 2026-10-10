"""Fail-closed UTC reservations; no provider access.

legacy_path shares DailyBudget's .lock and retains every new upper-bound
reservation permanently there (conservative, including after settlement).
All legacy callers MUST use that same lock. Missing/malformed legacy accounting
fails closed; it is never reset here. global_cap(cost, now, reservation_id) may
instead atomically reserve against another authoritative global ledger.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import uuid


class Budget:
    def __init__(self, path, legacy_path=None, *, global_cap=None):
        self.path = Path(path)
        self.legacy_path = Path(legacy_path) if legacy_path is not None else None
        self.global_cap = global_cap
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, rid TEXT, kind TEXT, ts REAL, day TEXT, reserved INTEGER, cost INTEGER, settled INTEGER DEFAULT 0)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=8, isolation_level=None)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _legacy_reserve(self, cost, now):
        path = self.legacy_path
        with path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(path.read_text())
            day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
            if 'days' in state:
                if state['days'] and day < max(state['days']):
                    return False
                record = state['days'].get(day)
                if record is None:
                    return False  # only authoritative legacy owner may open new day
                usage = Decimal(str(record['last_usage']))
            else:
                record = state
                if str(record['day']) != day:
                    return False
                usage = Decimal(str(record['provider_usage']))
            baseline = Decimal(str(record['baseline']))
            reserved = Decimal(str(record['reserved']))
            if not all(x.is_finite() for x in (usage, baseline, reserved)) or not 0 <= baseline <= usage or reserved < 0:
                return False
            amount = Decimal(cost)/1000000
            if usage-baseline+reserved+amount > 2:
                return False
            record['reserved'] = str(reserved+amount)
            if 'days' in state:
                record['reservations'].append({'cost': str(amount), 'ts': now})
            else:
                record['calls'] = int(record.get('calls', 0))+1
            tmp = path.with_suffix('.behavior.tmp')
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(state, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            return True

    def reserve(self, rid, kind, max_tokens, now, *, upper_cost_usd=None):
        if kind not in ('decision', 'chat') or type(max_tokens) is not int or not 1 <= max_tokens <= (192 if kind == 'decision' else 128):
            raise ValueError('invalid reservation')
        now = float(now)
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        cost = 1568+max_tokens*4  # integer microdollars, no floating overspend
        if upper_cost_usd is not None:
            upper = Decimal(str(upper_cost_usd))
            if not upper.is_finite() or upper <= 0:
                raise ValueError('invalid provider upper cost')
            cost = max(cost, int((upper*1000000).to_integral_value(rounding='ROUND_CEILING')))
        identifier = uuid.uuid4().hex
        try:
            with self._db() as db:
                last = db.execute('SELECT MAX(ts) FROM reservations').fetchone()[0]
                if last is not None and now < last:
                    return None
                count = db.execute('SELECT COUNT(*) FROM reservations WHERE rid=? AND kind=? AND ts>?', (rid, kind, now-3600)).fetchone()[0]
                if count >= (6 if kind == 'decision' else 12):
                    return None
                if kind == 'chat' and db.execute("SELECT COUNT(*) FROM reservations WHERE day=? AND kind='chat'", (day,)).fetchone()[0] >= 180:
                    return None
                spent = db.execute('SELECT COALESCE(SUM(cost),0) FROM reservations WHERE day=? AND kind=?', (day, kind)).fetchone()[0]
                total = db.execute('SELECT COALESCE(SUM(cost),0) FROM reservations WHERE day=?', (day,)).fetchone()[0]
                if spent+cost > (1350000 if kind == 'decision' else 550000) or total+cost > 1900000:
                    return None
                if self.legacy_path is not None and not self._legacy_reserve(cost, now):
                    return None
                if self.global_cap is not None and not self.global_cap(Decimal(cost)/1000000, now, identifier):
                    return None
                db.execute('INSERT INTO reservations(id,rid,kind,ts,day,reserved,cost) VALUES(?,?,?,?,?,?,?)', (identifier, rid, kind, now, day, cost, cost))
                return identifier
        except (OSError, ValueError, KeyError, TypeError, ArithmeticError, sqlite3.Error):
            return None

    def settle(self, identifier, usage_cost_or_None):
        if usage_cost_or_None is None:
            return  # unknown usage never releases reservation
        amount = Decimal(str(usage_cost_or_None))
        if not amount.is_finite() or amount < 0:
            raise ValueError('invalid usage cost')
        cost = int((amount*1000000).to_integral_value(rounding='ROUND_CEILING'))
        with self._db() as db:
            row = db.execute('SELECT reserved,settled FROM reservations WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            if row[1]:
                return
            db.execute('UPDATE reservations SET cost=?,settled=1 WHERE id=?', (cost, identifier))
