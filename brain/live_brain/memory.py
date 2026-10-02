"""Постоянная память персонажа: SQLite в $LAB_ROOT/state/<bot>/memory.sqlite."""
import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, text TEXT NOT NULL, importance INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS relations (
    name TEXT PRIMARY KEY, affinity INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
    first_seen REAL NOT NULL, last_seen REAL NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, ok INTEGER NOT NULL, latency REAL,
    prompt_tokens INTEGER, completion_tokens INTEGER, error TEXT);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS llm_calls_ts ON llm_calls(ts);
"""


class Memory:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    # --- события ---
    def add_event(self, kind, data):
        self.db.execute("INSERT INTO events (ts, kind, data) VALUES (?, ?, ?)",
                        (time.time(), kind, json.dumps(data, ensure_ascii=False)))
        self.db.commit()

    def recent_events(self, n=20):
        rows = self.db.execute(
            "SELECT ts, kind, data FROM events WHERE kind != 'state' ORDER BY id DESC LIMIT ?", (n,))
        return [dict(ts=r["ts"], kind=r["kind"], **json.loads(r["data"])) for r in rows][::-1]

    def count_events(self, kind, since):
        return self.db.execute("SELECT COUNT(*) FROM events WHERE kind = ? AND ts >= ?",
                               (kind, since)).fetchone()[0]

    # --- воспоминания ---
    def remember(self, text, importance=2):
        text = str(text).strip()[:300]
        if not text:
            return
        importance = max(1, min(5, int(importance)))
        self.db.execute("INSERT INTO memories (ts, text, importance) VALUES (?, ?, ?)",
                        (time.time(), text, importance))
        self.db.commit()

    def top_memories(self, n=12):
        rows = self.db.execute(
            "SELECT ts, text, importance FROM memories ORDER BY importance DESC, id DESC LIMIT ?", (n,))
        return [dict(r) for r in rows]

    # --- отношения ---
    def touch_relation(self, name):
        now = time.time()
        self.db.execute(
            "INSERT INTO relations (name, first_seen, last_seen) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET last_seen = excluded.last_seen", (name, now, now))
        self.db.commit()

    def update_relation(self, name, delta=0, note=None):
        self.touch_relation(name)
        delta = max(-2, min(2, int(delta)))
        self.db.execute("UPDATE relations SET affinity = MAX(-10, MIN(10, affinity + ?)) WHERE name = ?",
                        (delta, name))
        if note:
            self.db.execute("UPDATE relations SET note = ? WHERE name = ?", (str(note)[:200], name))
        self.db.commit()

    def relation(self, name):
        row = self.db.execute("SELECT name, affinity, note, first_seen, last_seen FROM relations "
                              "WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    # --- состояние ---
    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key, value):
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, json.dumps(value, ensure_ascii=False)))
        self.db.commit()

    # --- бюджет LLM ---
    def log_llm_call(self, ok, latency=None, usage=None, error=None):
        usage = usage or {}
        self.db.execute(
            "INSERT INTO llm_calls (ts, ok, latency, prompt_tokens, completion_tokens, error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), 1 if ok else 0, latency, usage.get("prompt_tokens"),
             usage.get("completion_tokens"), (error or "")[:300] or None))
        self.db.commit()

    def llm_calls_since(self, since):
        return self.db.execute("SELECT COUNT(*) FROM llm_calls WHERE ts >= ?", (since,)).fetchone()[0]
