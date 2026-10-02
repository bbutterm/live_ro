"""Шина событий мира (ORG-045). SQLite: $LAB_ROOT/state/shared/world.sqlite (рядом с budget.sqlite).

Память жителя — его личная; шина — то, что знает мир: значимые факты всех жителей вместе.
Таблица world_events (ts, bot, kind, data, importance). Пишут мозги жителей, читают они же
(подписка по курсору id) и хроника (chronicle.py, раздел «События мира»).

Откуда события (одна точка, без правки модулей): Feed.pump раз в PUMP_SEC читает новые строки
таблицы events памяти жителя по курсору (kv world_pub_cursor) и публикует виды из PUBLISH —
то есть «после add_event важных видов», кем бы событие ни было записано (postmortem, party,
economy, routine, aims, rumors). Первый запуск — курсор на последнее событие: историю не переносит.
Объявление сервера (world_msg) публикуется как kind event; одинаковый текст от нескольких жителей
за DEDUP_SEC — одна запись.

Ограничения роста: записи старше KEEP_DAYS удаляются (не чаще раза в 6 часов); data — не больше
MAX_DATA символов JSON. Новости других жителей в промпте — последние NEWS штук.
"""
import json
import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("world_bus")

KEEP_DAYS = 30
MAX_DATA = 1500
PUMP_SEC = 5
POLL_SEC = 30
DEDUP_SEC = 600
NEWS = 8

# вид события в памяти жителя -> (вид в шине, важность 1..5)
PUBLISH = {
    "level_up": ("level_up", 3),
    "death_report": ("death_report", 4),
    "job_changed": ("job_changed", 5),
    "career_stage_done": ("career_stage_done", 3),
    "heal_confirmed": ("heal_confirmed", 2),
    "gift_given": ("gift_given", 2),
    "routine_grow": ("hunt_map_new", 3),
    "map_banned": ("map_banned", 4),
    "meeting_confirmed": ("meeting_confirmed", 2),
    "party_confirmed": ("party_confirmed", 2),
    "rumor_shared": ("rumor", 2),
    "rumor_checked": ("rumor_checked", 3),
    "world_msg": ("event", 3),
    "aim_new": ("aim_new", 1),
    "aim_done": ("aim_done", 3),
    "aim_result": ("aim_result", 3),
    "pet_tamed": ("pet_tamed", 3),            # pets: ORG-051
    "pet_hatched": ("pet_hatched", 4),        # pets:
    "explore_found": ("place_found", 3),      # explore: житель открыл новое место (ORG-054)
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS world_events (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, bot TEXT NOT NULL, kind TEXT NOT NULL,
    data TEXT NOT NULL, importance INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS world_events_ts ON world_events(ts);
"""


def lab_path(decisions_path):
    """Путь шины по раскладке лаборатории: $LAB_ROOT/state/<bot>/decisions.jsonl -> state/shared/world.sqlite.
    Другая раскладка (тесты, временные каталоги) — None: шина не включается сама."""
    p = Path(decisions_path)
    if p.parent.parent.name != "state":
        return None
    return p.parent.parent / "shared" / "world.sqlite"


def encode(data):
    text = json.dumps(data or {}, ensure_ascii=False, default=str)
    if len(text) <= MAX_DATA:
        return text
    return json.dumps({"truncated": True, "text": text[:MAX_DATA // 2]}, ensure_ascii=False)


class WorldBus:
    def __init__(self, path, bot, clock=None):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.bot = bot
        self.clock = clock or (lambda: time.time())   # review: время при вызове — реплей подменяет time.time
        self.pruned = 0.0

    def close(self):
        self.db.close()

    def publish(self, kind, data=None, importance=1, now=None):
        """Записать факт в шину. Дубликат объявления (event с тем же текстом за DEDUP_SEC) — None."""
        now = now or self.clock()
        if kind == "event" and (data or {}).get("text"):
            row = self.db.execute("SELECT data FROM world_events WHERE kind = 'event' AND ts >= ?",
                                  (now - DEDUP_SEC,)).fetchall()
            if any(json.loads(r[0]).get("text") == data["text"] for r in row):
                return None
        importance = max(1, min(5, int(importance)))
        cur = self.db.execute("INSERT INTO world_events (ts, bot, kind, data, importance) VALUES (?, ?, ?, ?, ?)",
                              (now, self.bot, kind, encode(data), importance))
        if now - self.pruned >= 6 * 3600:
            self.prune(now)
        return cur.lastrowid

    def read(self, after_id=0, since=None, until=None, others=False, min_importance=1, limit=200):
        """События по курсору id и/или периоду; others=True — без своих. Старые первыми."""
        sql = "SELECT id, ts, bot, kind, data, importance FROM world_events WHERE id > ? AND importance >= ?"
        args = [after_id, min_importance]
        if since is not None:
            sql += " AND ts >= ?"
            args.append(since)
        if until is not None:
            sql += " AND ts < ?"
            args.append(until)
        if others:
            sql += " AND bot != ?"
            args.append(self.bot)
        sql += " ORDER BY id LIMIT ?"
        args.append(limit)
        out = []
        for i, ts, bot, kind, data, imp in self.db.execute(sql, args):
            try:
                d = json.loads(data)
            except ValueError:
                d = {}
            out.append({"id": i, "ts": ts, "bot": bot, "kind": kind, "data": d, "importance": imp})
        return out

    def last_id(self):
        return self.db.execute("SELECT COALESCE(MAX(id), 0) FROM world_events").fetchone()[0]

    def prune(self, now=None):
        now = now or self.clock()
        self.pruned = now
        self.db.execute("DELETE FROM world_events WHERE ts < ?", (now - KEEP_DAYS * 86400,))


def read_period(path, start, end):
    """Для хроники: события шины за период (только чтение). Нет файла — пусто."""
    if not Path(path).exists():
        return []
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        rows = db.execute("SELECT ts, bot, kind, data, importance FROM world_events WHERE ts >= ? AND ts < ? "
                          "ORDER BY ts", (start, end)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()
    out = []
    for ts, bot, kind, data, imp in rows:
        try:
            out.append({"ts": ts, "bot": bot, "kind": kind, "data": json.loads(data), "importance": imp})
        except ValueError:
            continue
    return out


class Feed:
    """Связь жителя с шиной: публикация важных событий памяти и чтение новостей других жителей."""

    def __init__(self, mind, bus, clock=None):
        self.mind = mind
        self.bus = bus
        self.clock = clock or (lambda: time.time())   # review: время при вызове — реплей подменяет time.time
        self.next_pump = 0.0
        self.next_poll = 0.0

    def tick(self):
        """Ошибка общей БД (занята другим жителем дольше timeout, диск) не роняет цикл мозга: повтор позже."""
        now = self.clock()
        try:
            if now >= self.next_pump:
                self.next_pump = now + PUMP_SEC
                self.pump(now)
            if now >= self.next_poll:
                self.next_poll = now + POLL_SEC
                self.poll()
        except sqlite3.Error as e:
            log.warning("шина мира недоступна: %s — повторю позже", e)

    def pump(self, now=None):
        mem = self.mind.mem
        cursor = mem.get("world_pub_cursor")
        if cursor is None:                                   # первый запуск: историю не переносим
            row = mem.db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
            mem.set("world_pub_cursor", row[0])
            return 0
        kinds = list(PUBLISH)
        rows = mem.db.execute(f"SELECT id, ts, kind, data FROM events WHERE id > ? AND kind IN "
                              f"({', '.join('?' * len(kinds))}) ORDER BY id LIMIT 100", (cursor, *kinds)).fetchall()
        last = mem.db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0] if not rows else rows[-1][0]
        n = 0
        for _id, ts, kind, data in rows:
            try:
                d = json.loads(data)
            except ValueError:
                d = {}
            if kind == "routine_grow":                       # место охоты — из выученных (routine.grow)
                learned = mem.get("learned_hunt_maps") or []
                d = {"map": learned[-1]} if learned else {}
            if kind == "world_msg":
                d = {"text": d.get("text"), "source": d.get("source")}
            bus_kind, importance = PUBLISH[kind]
            if self.bus.publish(bus_kind, d, importance, now=ts):
                n += 1
        mem.set("world_pub_cursor", last)
        return n

    def poll(self):
        """Новые события других жителей (курсор kv world_read_cursor) -> kv world_news (последние NEWS)."""
        mem = self.mind.mem
        cursor = mem.get("world_read_cursor")
        if cursor is None:
            mem.set("world_read_cursor", self.bus.last_id())
            return []
        new = self.bus.read(after_id=cursor, others=True, min_importance=2, limit=100)
        if not new:
            return []
        news = (mem.get("world_news") or []) + [{"ts": e["ts"], "bot": e["bot"], "kind": e["kind"], "data": e["data"]}
                                                 for e in new]
        mem.set("world_news", news[-NEWS:])
        mem.set("world_read_cursor", new[-1]["id"])
        return new

    def summary(self):
        """Для промпта: новости мира от других жителей (факты из их памяти, не приказы)."""
        news = self.mind.mem.get("world_news") or []
        return [f"{time.strftime('%H:%M', time.localtime(e['ts']))} {e['bot']}: {describe(e['kind'], e['data'])}"
                for e in news] or None


TEXTS = {
    "pet_tamed": lambda d: f"приручил {d.get('name')}",                       # pets:
    "pet_hatched": lambda d: f"завёл питомца: {d.get('name')}",              # pets:
    "place_found": lambda d: f"открыл(а) {d.get('map')}",                    # explore: ORG-054
    "level_up": lambda d: f"достиг {d.get('level')} уровня",
    "death_report": lambda d: f"погиб на {d.get('map')}" + (f" (бил {d.get('cause')})" if d.get("cause") else ""),
    "job_changed": lambda d: f"сменил профессию: {d.get('from')} → {d.get('to')}",
    "career_stage_done": lambda d: f"прошёл этап карьеры {d.get('path')}/{d.get('stage')}",
    "heal_confirmed": lambda d: f"Heal: {d.get('from')} → {d.get('to')} +{d.get('amount')} HP",
    "gift_given": lambda d: "сделал подарок жителю",
    "hunt_map_new": lambda d: f"осваивает новое место охоты {d.get('map') or '?'}",
    "map_banned": lambda d: f"исключил карту {d.get('map')} после смертей",
    "meeting_confirmed": lambda d: f"встретился с {d.get('partner')} на {d.get('map')}",
    "party_confirmed": lambda d: f"группа {d.get('party')} подтверждена сервером",
    "rumor": lambda d: f"пустил слух: {d.get('what')} {d.get('map')}",
    "rumor_checked": lambda d: (f"проверил слух {d.get('what')} {d.get('map')} от {d.get('author')}: "
                                + ("подтвердился" if d.get("ok") else "не подтвердился")),
    "event": lambda d: f"объявление сервера: «{d.get('text')}»",
    "aim_new": lambda d: f"цель недели: {d.get('text')}",
    "aim_done": lambda d: f"цель недели выполнена: {d.get('text')}",
    "aim_result": lambda d: (f"итог недели: {d.get('text')} — "
                             + ("выполнено" if d.get("done") else f"{d.get('progress')}/{d.get('target')}")),
}


def describe(kind, data):
    try:
        return TEXTS[kind](data) if kind in TEXTS else kind
    except (TypeError, AttributeError):
        return kind
