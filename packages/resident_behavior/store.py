"""Transactional stdlib SQLite state/inbox/outbox; claimed actions require reconciliation."""
import json
import sqlite3
from .reducer import timestamp


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class BehaviorStore:
    def __init__(self, path):
        self.connection = sqlite3.connect(str(path), timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA synchronous=FULL')
        self.connection.executescript('''
        CREATE TABLE IF NOT EXISTS residents(rid TEXT PRIMARY KEY, seq INTEGER NOT NULL DEFAULT 0,
          snapshot TEXT, snapshot_at REAL, needs_full INTEGER NOT NULL DEFAULT 0, mental TEXT);
        CREATE TABLE IF NOT EXISTS events(rid TEXT NOT NULL, event_id TEXT NOT NULL, time REAL NOT NULL,
          payload TEXT NOT NULL, PRIMARY KEY(rid,event_id));
        CREATE TABLE IF NOT EXISTS outbox(action_id TEXT PRIMARY KEY, rid TEXT NOT NULL,
          payload TEXT NOT NULL, status TEXT NOT NULL, outcome TEXT);
        CREATE TABLE IF NOT EXISTS chat_intents(rid TEXT NOT NULL, intent_id TEXT NOT NULL,
          payload TEXT NOT NULL, receipt TEXT, PRIMARY KEY(rid,intent_id));
        CREATE TABLE IF NOT EXISTS summaries(rid TEXT NOT NULL, day REAL NOT NULL, text TEXT NOT NULL,
          PRIMARY KEY(rid,day));
        ''')
        columns = {row['name'] for row in self.connection.execute('PRAGMA table_info(summaries)')}
        if 'source_ids' not in columns:
            self.connection.execute("ALTER TABLE summaries ADD COLUMN source_ids TEXT NOT NULL DEFAULT '[]'")
        if 'entries' not in columns:
            self.connection.execute("ALTER TABLE summaries ADD COLUMN entries TEXT NOT NULL DEFAULT '[]'")
        self.connection.commit()

    def events(self, rid):
        rows = self.connection.execute('SELECT payload FROM events WHERE rid=? ORDER BY time,event_id', (rid,))
        return sorted((json.loads(row[0]) for row in rows),
                      key=lambda event: (timestamp(event['server_time']), event['resident_id'], event['seq'], event['event_id']))

    def summarize_day(self, rid, day, *, max_chars=480):
        """Deterministic factual labels, never an LLM inference or a claim of healing."""
        with self.connection:
            return self._summarize_day(rid, day, max_chars=max_chars)

    def _summarize_day(self, rid, day, *, max_chars=480):
        if not 1 <= max_chars <= 480:
            raise ValueError('summary must be between 1 and 480 characters')
        start = timestamp(day)//86400*86400
        old = self.connection.execute('SELECT * FROM summaries WHERE rid=? AND day=?', (rid, start)).fetchone()
        records = [event for event in self.events(rid) if start <= timestamp(event['server_time']) < start+86400]
        priority = {'died': 0, 'meet_agreed': 1, 'meet_done': 1, 'meet_missed': 1,
                    'level_up': 2, 'pickup': 2, 'sold': 2, 'bought': 2, 'heard': 3}
        records.sort(key=lambda event: (priority.get(event['type'], 4), timestamp(event['server_time']), event['seq'], event['event_id']))
        # A partially pruned day no longer has all of its raw evidence. Keep
        # already selected labels and their real provenance, never reconstruct
        # deleted events from IDs or replace them with only the surviving tail.
        labels = self._summary_entries(old) if old else []
        known = {source for entry in labels for source in entry['source_ids']}
        labels.extend({'text': event['type']+':'+event['event_id'], 'source_ids': [event['event_id']]}
                      for event in records if event['event_id'] not in known)
        labels.sort(key=lambda entry: priority.get(entry['text'].split(':', 1)[0], 4))
        text, sources, selected = self._bounded_entries(labels, max_chars)
        # No nested connection context: prune owns the outer transaction.
        self.connection.execute('INSERT OR REPLACE INTO summaries(rid,day,text,source_ids,entries) VALUES (?,?,?,?,?)',
                                (rid, start, text, _json(sources), _json(selected)))
        return {'rid': rid, 'day': start, 'text': text, 'source_ids': sources}

    @staticmethod
    def _summary_entries(row):
        entries = json.loads(row['entries'])
        if entries:
            return entries
        # Legacy summaries did not preserve label boundaries. Treat them as a
        # single evidence block, rather than fabricating labels from source IDs.
        sources = json.loads(row['source_ids'])
        return [{'text': row['text'], 'source_ids': sources}] if row['text'] and sources else []

    @staticmethod
    def _bounded_entries(entries, max_chars):
        text, sources, selected = '', [], []
        for entry in entries:
            if not entry['source_ids'] or all(source in sources for source in entry['source_ids']):
                continue
            addition = ('; ' if text else '')+entry['text']
            if len(text)+len(addition) <= max_chars:
                text += addition
                sources.extend(source for source in entry['source_ids'] if source not in sources)
                selected.append(entry)
        return text, sources, selected

    def prompt_memory(self, rid, *, max_chars=600, now=None):
        """Bounded factual evidence only; never decision reasons or inferred Heal.

        source_ids identify only the complete entries actually present in text.
        Call prune separately; this reader does not delete data or commit state.
        """
        if type(max_chars) is not int or not 1 <= max_chars <= 600:
            raise ValueError('prompt memory must be between 1 and 600 characters')
        now = timestamp(now) if now is not None else None
        priority = {'died': 0, 'meet_agreed': 1, 'meet_done': 1, 'meet_missed': 1,
                    'level_up': 2, 'pickup': 2, 'sold': 2, 'bought': 2, 'heard': 3}
        entries = []
        for row in self.connection.execute('SELECT * FROM summaries WHERE rid=? ORDER BY day DESC', (rid,)):
            if now is None or now-30*86400 <= row['day'] <= now:
                entries.extend(self._summary_entries(row))
        for event in reversed(self.events(rid)):
            at = timestamp(event['server_time'])
            if now is None or now-7*86400 <= at <= now:
                entries.append({'text': event['type']+':'+event['event_id'], 'source_ids': [event['event_id']]})
        entries.sort(key=lambda entry: priority.get(entry['text'].split(':', 1)[0], 4))
        text, sources, _ = self._bounded_entries(entries, max_chars)
        return {'text': text, 'source_ids': sources}

    def get_summaries(self, rid):
        rows = self.connection.execute('SELECT * FROM summaries WHERE rid=? ORDER BY day', (rid,))
        return [{'rid': rid, 'day': row['day'], 'text': row['text'], 'source_ids': json.loads(row['source_ids'])} for row in rows]

    def prune(self, now):
        """Preserve daily evidence before expiring seven-day events and 30-day summaries."""
        now = timestamp(now)
        with self.connection:
            days = self.connection.execute('SELECT DISTINCT rid,CAST(time/86400 AS INTEGER)*86400 AS day FROM events WHERE time<?',
                                           (now-7*86400,)).fetchall()
            for row in days:
                self._summarize_day(row['rid'], row['day'])
            events = self.connection.execute('DELETE FROM events WHERE time<?', (now-7*86400,)).rowcount
            summaries = self.connection.execute('DELETE FROM summaries WHERE day<?', (now-30*86400,)).rowcount
        return {'events_deleted': events, 'summaries_deleted': summaries}

    def claim_chat_intent(self, rid, intent):
        """Durable at-most-once claim before any model/native I/O; NULL is unknown."""
        payload = _json(intent)
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            row = self.connection.execute('SELECT payload,receipt FROM chat_intents WHERE rid=? AND intent_id=?',
                                          (rid,intent['id'])).fetchone()
            if row:
                if row['payload'] != payload:
                    raise ValueError('chat intent conflict')
                return False, json.loads(row['receipt']) if row['receipt'] else None
            self.connection.execute('INSERT INTO chat_intents VALUES (?,?,?,NULL)',(rid,intent['id'],payload))
            return True, None

    def finish_chat_intent(self, rid, intent_id, receipt):
        with self.connection:
            self.connection.execute('UPDATE chat_intents SET receipt=? WHERE rid=? AND intent_id=? AND receipt IS NULL',
                                    (_json(receipt),rid,intent_id))

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _resident(self, rid):
        self.connection.execute('INSERT OR IGNORE INTO residents(rid) VALUES (?)', (rid,))
        return self.connection.execute('SELECT * FROM residents WHERE rid=?', (rid,)).fetchone()

    def ingest_snapshot(self, snapshot, *, full=False):
        rid, seq = snapshot['resident_id'], snapshot['seq']
        now = timestamp(snapshot['server_time'])
        payload = _json(snapshot)
        with self.connection:
            previous = self._resident(rid)
            if seq <= previous['seq']:
                return {'status': 'duplicate' if seq == previous['seq'] else 'stale', 'seq': previous['seq']}
            gap = bool(previous['seq'] and seq > previous['seq']+1)
            needed = 0 if full else int(gap or previous['needs_full'])
            self.connection.execute('UPDATE residents SET seq=?,snapshot=?,snapshot_at=?,needs_full=? WHERE rid=?',
                                    (seq, payload, now, needed, rid))
            return {'status': 'gap' if gap and not full else 'accepted', 'seq': seq}

    def apply_snapshot(self, snapshot, mental_fn, *, full=True):
        """Validate/ingest/reduce/save in one locked SQL transaction.

        mental_fn(accepted_snapshot, previous_mental) must be pure (no body,
        network, store writes or commits). Identical telemetry may advance the
        caller's mental clock on every tick; a seq cannot identify two facts.
        Exceptions roll back BOTH observations and mental state. Selection and
        the optional action outbox remain a later save_state transaction.
        """
        from .contracts import ContractError, validate_telemetry
        accepted = validate_telemetry(snapshot)
        rid, seq = accepted['resident_id'], accepted['seq']
        now, payload = timestamp(accepted['server_time']), _json(accepted)
        if self.connection.in_transaction:
            raise RuntimeError('apply_snapshot requires its own transaction')
        with self.connection:
            # Lock before reading: another writer must not replace the previous
            # mental state between validation of seq and reducer application.
            self.connection.execute('BEGIN IMMEDIATE')
            row = self._resident(rid)
            old = json.loads(row['snapshot']) if row['snapshot'] else None
            same = old is not None and seq == old['seq']
            if same and payload != _json(old):
                raise ContractError('conflict', 'seq')
            if not same:
                if seq < row['seq'] or (old is not None and seq <= old['seq']):
                    raise ContractError('stale_state', 'seq')
                if old is not None and now < timestamp(old['server_time']):
                    raise ContractError('stale_state', 'server_time')
                gap = bool(row['seq'] and seq > row['seq']+1)
                needed = 0 if full else int(gap or row['needs_full'])
                self.connection.execute('UPDATE residents SET seq=max(seq,?),snapshot=?,snapshot_at=?,needs_full=? WHERE rid=?',
                                        (seq, payload, now, needed, rid))
            elif full:
                self.connection.execute('UPDATE residents SET needs_full=0 WHERE rid=?', (rid,))
            previous = json.loads(row['mental']) if row['mental'] else None
            mental = mental_fn(accepted, previous)
            if _json(accepted) != payload:
                raise ValueError('mental callback mutated authoritative snapshot')
            if type(mental) is not dict or mental.get('resident_id', rid) != rid:
                raise ValueError('mental callback must return resident mental dict')
            if 'current_action' in mental and mental['current_action'] != accepted['current_action']:
                raise ValueError('mental current_action differs from authoritative snapshot')
            if 'latestSnapshot' in mental and mental['latestSnapshot'] != accepted:
                raise ValueError('mental latestSnapshot differs from authoritative snapshot')
            serialized = _json(mental)
            self.connection.execute('UPDATE residents SET mental=? WHERE rid=?', (serialized, rid))
            return json.loads(serialized)

    def ingest_event(self, event):
        rid, eid = event['resident_id'], event['event_id']
        now, payload = timestamp(event['server_time']), _json(event)
        with self.connection:
            previous = self._resident(rid)
            inserted = self.connection.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?)', (rid, eid, now, payload)).rowcount
            if inserted:
                seq = event['seq']
                gap = bool(previous['seq'] and seq > previous['seq']+1)
                self.connection.execute('UPDATE residents SET seq=max(seq,?),needs_full=max(needs_full,?) WHERE rid=?', (seq, int(gap), rid))
            return bool(inserted)

    def get_snapshot(self, rid):
        row = self.connection.execute('SELECT snapshot FROM residents WHERE rid=?', (rid,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def get_state(self, rid):
        row = self.connection.execute('SELECT mental FROM residents WHERE rid=?', (rid,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def save_state(self, rid, state, *, action=None):
        """Persist state and optional command in ONE transaction; action IDs never requeue."""
        mental = _json(state)
        payload = _json(action) if action is not None else None
        with self.connection:
            self._resident(rid)
            inserted = True
            if action is not None:
                if action['resident_id'] != rid:
                    raise ValueError('action resident mismatch')
                old = self.connection.execute('SELECT payload FROM outbox WHERE action_id=?', (action['action_id'],)).fetchone()
                if old and old[0] != payload:
                    raise ValueError('action_id reused with different command')
                inserted = bool(self.connection.execute('INSERT OR IGNORE INTO outbox VALUES (?,?,?,\'pending\',NULL)', (action['action_id'], rid, payload)).rowcount)
            self.connection.execute('UPDATE residents SET mental=? WHERE rid=?', (mental, rid))
            return inserted

    def claim_action(self, action_id):
        """Claim before any I/O. Crash leaves reconcile, NEVER automatically resend."""
        with self.connection:
            return bool(self.connection.execute("UPDATE outbox SET status='reconcile' WHERE action_id=? AND status='pending'", (action_id,)).rowcount)

    def get_action(self, action_id):
        row = self.connection.execute('SELECT * FROM outbox WHERE action_id=?', (action_id,)).fetchone()
        if not row:
            return None
        return dict(json.loads(row['payload']), status=row['status'], outcome=json.loads(row['outcome']) if row['outcome'] else None)

    def pending_actions(self):
        rows = self.connection.execute("SELECT action_id FROM outbox WHERE status IN ('pending','reconcile') ORDER BY action_id").fetchall()
        return [self.get_action(row[0]) for row in rows]

    def record_outcome(self, action_id, outcome):
        with self.connection:
            changed = self.connection.execute("UPDATE outbox SET status=?,outcome=? WHERE action_id=?", ('terminal' if outcome.get('result') in ('done', 'failed', 'timeout') else 'reconcile', _json(outcome), action_id)).rowcount
            if not changed:
                raise KeyError(action_id)

    def reconciliation(self, rid, now):
        row = self.connection.execute('SELECT seq,snapshot_at,needs_full FROM residents WHERE rid=?', (rid,)).fetchone()
        return {'seq': row['seq'] if row else 0, 'stale': not row or row['snapshot_at'] is None or timestamp(now)-row['snapshot_at'] > 10,
                'needs_full_snapshot': not row or bool(row['needs_full'])}
