"""Постоянная память персонажа: SQLite в $LAB_ROOT/state/<bot>/memory.sqlite.

Версия схемы (AUT-107): kv schema_version; перед миграцией — копия файла <имя>.bak-v<N>
(sqlite backup API), личность и воспоминания не теряются.
Виды воспоминаний (AUT-097): fact — подтверждено игрой/правилом, thought — мысль модели
(не проверена), note — прочее. Модель видит вид и не путает намерение с фактом.
Рост ограничен (AUT-100): рутинные события старше 14 дней, прочие старше 90 (кроме итогов:
дневник, разбор смерти, уровни, встречи), воспоминаний не больше MAX_MEMORIES (уходят
наименее важные и старые), журнал вызовов моделей — 90 дней.
"""
import json
import sqlite3
import time
from pathlib import Path

SCHEMA_VERSION = 2
MAX_MEMORIES = 2000
KEEP_EVENTS = ("diary", "death_report", "level_up", "meeting_confirmed", "party_confirmed", "map_banned")
KINDS = ("fact", "thought", "note")

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
    prompt_tokens INTEGER, completion_tokens INTEGER, error TEXT,
    provider TEXT NOT NULL DEFAULT 'openrouter');
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS llm_calls_ts ON llm_calls(ts);
"""


class Memory:
    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        if str(path) != ":memory:":
            # ORG D7: WAL + synchronous NORMAL — запись без fsync на каждый коммит, чтение не блокирует запись.
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.migrate()
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(llm_calls)")}
        if "provider" not in cols:   # файл памяти из версии до JEV
            self.db.execute("ALTER TABLE llm_calls ADD COLUMN provider TEXT NOT NULL DEFAULT 'openrouter'")
        if "cost" not in cols:       # стоимость по usage.cost (OpenRouter), USD
            self.db.execute("ALTER TABLE llm_calls ADD COLUMN cost REAL")
        self.prune()

    def migrate(self):
        row = self.db.execute("SELECT value FROM kv WHERE key = 'schema_version'").fetchone()
        version = json.loads(row["value"]) if row else 1
        if version >= SCHEMA_VERSION:
            return
        has_data = self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] or \
            self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if has_data and str(self.path) != ":memory:":
            backup = self.path.with_name(f"{self.path.name}.bak-v{version}")
            dst = sqlite3.connect(str(backup))
            self.db.backup(dst)
            dst.close()
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(memories)")}
        if "kind" not in cols:
            self.db.execute("ALTER TABLE memories ADD COLUMN kind TEXT NOT NULL DEFAULT 'note'")
        self.db.execute("INSERT INTO kv (key, value) VALUES ('schema_version', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (json.dumps(SCHEMA_VERSION),))
        self.db.commit()

    def prune(self, now=None):
        now = now or time.time()
        self.db.execute("DELETE FROM events WHERE kind IN ('attack', 'kill', 'loot') AND ts < ?", (now - 14 * 86400,))
        self.db.execute(f"DELETE FROM events WHERE kind NOT IN ({', '.join('?' * len(KEEP_EVENTS))}) AND ts < ?",
                        (*KEEP_EVENTS, now - 90 * 86400))
        self.db.execute("DELETE FROM llm_calls WHERE ts < ?", (now - 90 * 86400,))
        extra = self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] - MAX_MEMORIES
        if extra > 0:
            self.db.execute("DELETE FROM memories WHERE id IN (SELECT id FROM memories "
                            "ORDER BY importance ASC, id ASC LIMIT ?)", (extra,))
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
    def remember(self, text, importance=2, kind="fact"):
        """По умолчанию fact: воспоминания пишут правила по данным игры; мысли модели — kind=thought."""
        text = str(text).strip()[:300]
        if not text:
            return
        importance = max(1, min(5, int(importance)))
        kind = kind if kind in KINDS else "note"
        self.db.execute("INSERT INTO memories (ts, text, importance, kind) VALUES (?, ?, ?, ?)",
                        (time.time(), text, importance, kind))
        self.db.commit()

    def top_memories(self, n=12):
        rows = self.db.execute(
            "SELECT ts, text, importance, kind FROM memories ORDER BY importance DESC, id DESC LIMIT ?", (n,))
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
    def log_llm_call(self, ok, latency=None, usage=None, error=None, provider="openrouter"):
        usage = usage or {}
        self.db.execute(
            "INSERT INTO llm_calls (ts, ok, latency, prompt_tokens, completion_tokens, error, provider, cost) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), 1 if ok else 0, latency, usage.get("prompt_tokens"),
             usage.get("completion_tokens"), (error or "")[:300] or None, provider,
             usage.get("cost") if isinstance(usage.get("cost"), (int, float)) else None))
        self.db.commit()

    def cost_since(self, since, provider="openrouter"):
        return self.db.execute("SELECT COALESCE(SUM(cost), 0) FROM llm_calls WHERE ts >= ? AND provider = ?",
                               (since, provider)).fetchone()[0]

    def llm_calls_since(self, since, provider="openrouter"):
        return self.db.execute("SELECT COUNT(*) FROM llm_calls WHERE ts >= ? AND provider = ?",
                               (since, provider)).fetchone()[0]
