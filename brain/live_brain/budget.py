"""Общий бюджет моделей на всех жителей (AUT-109). SQLite: $LAB_ROOT/state/shared/budget.sqlite.

Лимиты на бота (BRAIN_DAILY_LIMIT, BRAIN_DAILY_USD_LIMIT, JEV_DAILY_LIMIT) не защищают от того,
что новый житель умножит расходы. Здесь — сумма по всем жителям за 24 часа:
    BRAIN_GLOBAL_DAILY_LIMIT (600) / BRAIN_GLOBAL_DAILY_USD_LIMIT ($2) для openrouter,
    JEV_GLOBAL_DAILY_LIMIT (4000) для jev.
Резерв ДО вызова (BEGIN IMMEDIATE — процессы мозгов не проходят лимит одновременно), стоимость
дописывается после ответа (settle). Ответ без usage.cost — стоимость неизвестна, считается 0,
но вызов учтён в числе запросов.
"""
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, bot TEXT NOT NULL, provider TEXT NOT NULL,
    cost REAL NOT NULL DEFAULT 0, settled INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS calls_ts ON calls(ts);
"""


class SharedBudget:
    def __init__(self, path, bot):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=10, isolation_level=None)
        self.db.executescript(SCHEMA)
        self.bot = bot

    def close(self):
        self.db.close()

    def reserve(self, provider, max_calls, max_usd=None, now=None):
        """(id, None) — можно звать модель; (None, причина) — общий лимит исчерпан."""
        now = now or time.time()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            n, cost = self.db.execute("SELECT COUNT(*), COALESCE(SUM(cost), 0) FROM calls WHERE provider = ? AND ts >= ?",
                                      (provider, now - 86400)).fetchone()
            if max_calls and n >= max_calls:
                self.db.execute("ROLLBACK")
                return None, f"общий лимит {provider}: {n}/{max_calls} вызовов за сутки на всех жителей"
            if max_usd and cost >= max_usd:
                self.db.execute("ROLLBACK")
                return None, f"общий денежный лимит {provider}: ${cost:.2f}/${max_usd:.2f} за сутки на всех жителей"
            cur = self.db.execute("INSERT INTO calls (ts, bot, provider) VALUES (?, ?, ?)", (now, self.bot, provider))
            self.db.execute("COMMIT")
            return cur.lastrowid, None
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def settle(self, call_id, cost):
        if call_id is None:
            return
        cost = cost if isinstance(cost, (int, float)) else 0.0
        self.db.execute("UPDATE calls SET cost = ?, settled = 1 WHERE id = ?", (cost, call_id))

    def summary(self, now=None):
        now = now or time.time()
        rows = self.db.execute("SELECT provider, bot, COUNT(*), COALESCE(SUM(cost), 0) FROM calls "
                               "WHERE ts >= ? GROUP BY provider, bot", (now - 86400,)).fetchall()
        return [{"provider": p, "bot": b, "calls": n, "cost": c} for p, b, n, c in rows]
