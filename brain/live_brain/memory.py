"""Постоянная память персонажа: SQLite в $LAB_ROOT/state/<bot>/memory.sqlite.

Версия схемы (AUT-107): kv schema_version; перед миграцией — копия файла <имя>.bak-v<N>
(sqlite backup API), личность и воспоминания не теряются. v2 — memories.kind; v3 (perf) — частичный индекс
events(kind, ts) для count_events (без него «события вида за сутки/неделю» — проход всего диапазона ts).
Виды воспоминаний (AUT-097): fact — подтверждено игрой/правилом, thought — мысль модели
(не проверена), note — прочее. Модель видит вид и не путает намерение с фактом.
Рост ограничен (AUT-100): рутинные события старше 14 дней, прочие старше 90 (кроме итогов:
дневник, разбор смерти, уровни, встречи), воспоминаний не больше MAX_MEMORIES (уходят
наименее важные и старые), журнал вызовов моделей — 90 дней.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 3                    # perf: v3 — частичный индекс events_kind_ts для count_events (docs/PERF.md)
# perf: условие частичного индекса. SQLite берёт частичный индекс, только если в WHERE запроса есть этот же терм, —
# поэтому индекс видит лишь count_events (терм всегда истинен: kind NOT NULL), а планы остальных запросов
# (курсоры «id > ? AND kind IN», выборки по ts без ORDER BY) и порядок их строк не меняются.
KIND_TS_WHERE = "length(kind) >= 0"
MAX_MEMORIES = 2000
KEEP_EVENTS = ("diary", "death_report", "level_up", "meeting_confirmed", "party_confirmed", "map_banned")
KEEP_EVENTS += ("society_quarrel", "society_reconciled", "job_changed", "pet_hatched", "gift_received")  # gossip: W6
RELATION_LOG = 40                     # gossip: W5 — записей истории на пару (kv relation_log, prune не чистит)
RELATION_LOG_NAMES = 200              # review3: имён в relation_log не больше (модель пишет отношения к любым игрокам)
KINDS = ("fact", "thought", "note")
_MISSING = object()                   # perf: «ключа нет» в кэше kv/relations

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
        # perf: кэш такта (docs/PERF.md) — внутри with mem.tick(): kv и relations читаются из БД один раз,
        # запись идёт сквозь кэш; коммиты копятся и делаются одним COMMIT на выходе из такта.
        self._scope = 0
        self._dirty = False
        self._kv = {}
        self._rel = {}
        self._same = {}                       # perf: set_changed — последнее записанное значение ключа (текст)
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
        if version < 3:                                                      # perf: только индекс, данные те же
            self.db.execute(f"CREATE INDEX IF NOT EXISTS events_kind_ts ON events(kind, ts) WHERE {KIND_TS_WHERE}")
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
        if self._dirty:                       # perf: отложенный коммит такта не теряется
            self.db.commit()
        self.db.close()

    # perf: такт мозга (Mind.step / состояние тела) — кэш kv/relations и один коммит на такт
    @contextmanager
    def tick(self):
        if not self._scope:
            self._kv.clear()                  # между тактами память могли править мимо методов (тесты, SQL)
            self._rel.clear()
        self._scope += 1
        try:
            yield self
        finally:
            self._scope -= 1
            if not self._scope:
                self._kv.clear()
                self._rel.clear()
                self.flush()

    def flush(self):
        """perf: зафиксировать отложенные записи (вне такта коммит сразу — как раньше)."""
        if self._dirty:
            self._dirty = False
            self.db.commit()

    def _commit(self):
        if self._scope:
            self._dirty = True
        else:
            self.db.commit()

    # --- события ---
    def add_event(self, kind, data):
        self.db.execute("INSERT INTO events (ts, kind, data) VALUES (?, ?, ?)",
                        (time.time(), kind, json.dumps(data, ensure_ascii=False)))
        self._commit()

    def recent_events(self, n=20):
        rows = self.db.execute(
            "SELECT ts, kind, data FROM events WHERE kind != 'state' ORDER BY id DESC LIMIT ?", (n,))
        out = []
        for r in rows:
            # review3: данные с ключами kind/ts (gossip_heard, habit_formed, explore_found) роняли dict(**data) —
            # и вместе с ним промпт (поле «последние_события»); такие ключи данных — с суффиксом «_», не затирают
            row = {"ts": r["ts"], "kind": r["kind"]}
            try:
                data = json.loads(r["data"])
            except ValueError:
                data = None
            for k, v in (data.items() if isinstance(data, dict) else [("data", data)]):
                row[k + "_" if k in ("ts", "kind") else k] = v
            out.append(row)
        return out[::-1]

    def count_events(self, kind, since):
        return self.db.execute(f"SELECT COUNT(*) FROM events WHERE kind = ? AND ts >= ? AND {KIND_TS_WHERE}",  # perf:
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
        self._commit()

    def top_memories(self, n=12):
        rows = self.db.execute(
            "SELECT ts, text, importance, kind FROM memories ORDER BY importance DESC, id DESC LIMIT ?", (n,))
        return [dict(r) for r in rows]

    # --- отношения ---
    def touch_relation(self, name):
        self._rel.pop(name, None)                                            # perf:
        now = time.time()
        self.db.execute(
            "INSERT INTO relations (name, first_seen, last_seen) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET last_seen = excluded.last_seen", (name, now, now))
        self._commit()

    def update_relation(self, name, delta=0, note=None):
        self.touch_relation(name)
        delta = max(-2, min(2, int(delta)))
        self._rel.pop(name, None)                                            # perf:
        self.db.execute("UPDATE relations SET affinity = MAX(-10, MIN(10, affinity + ?)) WHERE name = ?",
                        (delta, name))
        if note:
            self.db.execute("UPDATE relations SET note = ? WHERE name = ?", (str(note)[:200], name))
        self._commit()
        if delta or note:                                                    # gossip: W5 — причина не теряется
            self.log_relation(name, delta, note)                             # gossip:

    # gossip: W5 — история пары: каждое изменение отношения с причиной; остывание без касания last_seen
    def log_relation(self, name, delta, note):
        log = self.get("relation_log") or {}
        rows = log.get(name) or []
        rel = self.relation(name) or {}
        rows.append({"ts": round(time.time(), 1), "delta": int(delta), "note": str(note or "")[:120],
                     "affinity": rel.get("affinity", 0)})
        log.pop(name, None)                                                  # review3: свежая пара — в конец,
        log[name] = rows[-RELATION_LOG:]
        for old in list(log)[:max(0, len(log) - RELATION_LOG_NAMES)]:        # review3: давние пары уходят
            del log[old]
        self.set("relation_log", log)

    def relation_log(self, name):
        return list((self.get("relation_log") or {}).get(name) or [])

    def adjust_affinity(self, name, delta, why):
        """Сдвиг отношения без «встречи» (last_seen не трогается): остывание, первое впечатление."""
        if not self.relation(name):
            return
        self._rel.pop(name, None)                                            # perf:
        self.db.execute("UPDATE relations SET affinity = MAX(-10, MIN(10, affinity + ?)) WHERE name = ?",
                        (max(-2, min(2, int(delta))), name))
        self._commit()
        self.log_relation(name, delta, why)

    def relation(self, name):
        if self._scope:                                                      # perf: кэш такта
            hit = self._rel.get(name, _MISSING)
            if hit is not _MISSING:
                return dict(hit) if hit else None
        row = self.db.execute("SELECT name, affinity, note, first_seen, last_seen FROM relations "
                              "WHERE name = ?", (name,)).fetchone()
        if self._scope:                                                      # perf:
            self._rel[name] = dict(row) if row else None
        return dict(row) if row else None

    # --- состояние ---
    def get(self, key, default=None):
        if self._scope:                                                      # perf: кэш такта (текст JSON —
            raw = self._kv.get(key, _MISSING)                                # perf: каждый get даёт свежий объект)
            if raw is _MISSING:
                row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
                raw = self._kv[key] = row["value"] if row else None
            return json.loads(raw) if raw is not None else default
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key, value):
        raw = json.dumps(value, ensure_ascii=False)
        if self._scope and self._kv.get(key) == raw:                         # perf: то же значение — без записи
            return
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, raw))
        self._same.pop(key, None)                                            # perf:
        if self._scope:                                                      # perf:
            self._kv[key] = raw
        self._commit()

    def set_changed(self, key, value):
        """perf: set() только если значение отличается от записанного этим же методом в прошлый раз
        (снимок состояния тела раз в секунду: стоящее тело даёт тот же снимок — без записи и коммита)."""
        raw = json.dumps(value, ensure_ascii=False)
        if self._same.get(key) == raw:
            return
        self.set(key, value)
        self._same[key] = raw

    # --- бюджет LLM ---
    def log_llm_call(self, ok, latency=None, usage=None, error=None, provider="openrouter"):
        usage = usage or {}
        self.db.execute(
            "INSERT INTO llm_calls (ts, ok, latency, prompt_tokens, completion_tokens, error, provider, cost) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), 1 if ok else 0, latency, usage.get("prompt_tokens"),
             usage.get("completion_tokens"), (error or "")[:300] or None, provider,
             usage.get("cost") if isinstance(usage.get("cost"), (int, float)) else None))
        self._commit()

    def cost_since(self, since, provider="openrouter"):
        return self.db.execute("SELECT COALESCE(SUM(cost), 0) FROM llm_calls WHERE ts >= ? AND provider = ?",
                               (since, provider)).fetchone()[0]

    def llm_calls_since(self, since, provider="openrouter"):
        return self.db.execute("SELECT COUNT(*) FROM llm_calls WHERE ts >= ? AND provider = ?",
                               (since, provider)).fetchone()[0]
