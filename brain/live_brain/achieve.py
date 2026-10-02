"""Достижения сервера как коллекция (ORG-080, ТЗ Т-31). Правила без LLM, только данные сервера.

Источник — пакеты rAthena (feature.achievement: on, conf/battle/feature.conf:81): 0A23 ZC_ALL_ACH_LIST при входе
(только если у персонажа есть хоть одна запись, clif.cpp:21831) и 0A24 ZC_ACH_UPDATE. OpenKore (serverType
kRO_RagexeRE_2018_06_20e -> kRO/Sakexe_0.pm:640-642) разбирает их побайтно так же, как пишет rAthena
(Receive.pm:9800-9842); мост шлёт мозгу только выполненные: события achievement {id, at, reward, points, rank, title}
и achievement_list {points, rank, done: [[id, at, reward], ...]}. В игре не проверено.

kv achieve: {done: {id: at}, unrewarded: [id], points, rank, synced, told: {житель: [id]}, compared: {житель: день},
claimed: {id: ts}}.
- Первый список после установки модуля — молча (synced): история не объявляется.
- Новое выполненное (обновление или следующий список): воспоминание «Получил(а) достижение «X» — по данным сервера»,
  событие памяти achievement_done {id, name, group, score} -> шина «achievement» (важность 3, world_bus.PUBLISH) и
  летопись; тема разговора achieve (ORG-066) — каждому жителю один раз за brag_days.
- Сравнение с соперником недели (rivalry.py): тихий снимок шины achieve_known {n, points} (затирание); если у меня
  больше, чем у соперника, — тема «У меня 5 достижений, у тебя 3!» раз в день; метрика соперничества «по
  достижениям» (rivalry.py, # achieve:).
- Название — brain/world/achievements.json (scripts/gen_achievements.py из db/re/achievement_db.yml), иначе title из
  таблицы OpenKore (tables/achievement_list.txt), иначе «№ id».
- Награда: goals.json achieve.claim_rewards (по умолчанию false) — действие achieve_reward {id} для выполненного и
  ещё не полученного, не чаще раза в claim_gap_seconds.
Если пакеты не приходят, модуль молчит: собственных «достижений» по памяти он не выдумывает (личные вехи ведут
коллекция ORG-074, бестиарий ORG-077, мечта ORG-081).
Выключатель: BRAIN_DISABLE=achieve или goals.json "achieve": {"enabled": false}.
"""
import json
import logging
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .world_bus import WorldBus

log = logging.getLogger("achieve")

TABLE = Path(__file__).resolve().parents[1] / "world" / "achievements.json"
DEFAULTS = {"enabled": True, "brag_days": 7, "brag_chance": 0.6, "snapshot_minutes": 10, "claim_rewards": False,
            "claim_gap_seconds": 60, "tick_seconds": 10}
NAME_MAX = 30
PHRASES = {   # ≤ 60 символов без метки
    "achieve": ["Получил(а) достижение «{ach}»!", "У меня новое достижение: «{ach}»!"],
    "achieve_lead": ["У меня {mine} достижений, у тебя {theirs}!", "{mine} достижений против твоих {theirs}!"],
    "achieve_re": ["Поздравляю!", "Здорово! Мне бы такое.", "Ого, молодец!"],
}
_TABLE_CACHE = None


def load_table(path=TABLE):
    global _TABLE_CACHE
    if path == TABLE and _TABLE_CACHE is not None:
        return _TABLE_CACHE
    try:
        data = (json.loads(Path(path).read_text(encoding="utf-8")) or {}).get("achievements") or {}
    except (OSError, ValueError):
        data = {}
    if path == TABLE:
        _TABLE_CACHE = data
    return data


class Achieve:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "achieve", "achieve", "achieve", True, "world"
    TICK_ORDER = 227                          # после bestiary (225)
    EVENTS = {"achievement": {"call": "on_update", "own": True},
              "achievement_list": {"call": "on_list", "own": True},
              "achievement_reward": {"call": "on_reward", "own": True}}
    EVENT_ORDER = 80
    PROMPT = [("достижения", "summary", 237)]  # после бестиария (235)

    def __init__(self, mind, world=None, clock=None, rng=None, table=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("achieve") or {}))
        self.tz = timezone(timedelta(hours=(world or {}).get("timezone_offset_hours", 0)))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.table = table if table is not None else load_table()
        self.st = mind.mem.get("achieve") or {}
        for key, val in (("done", {}), ("unrewarded", []), ("told", {}), ("compared", {}), ("claimed", {}),
                         ("news", [])):
            self.st.setdefault(key, val)
        self.next_tick = 0.0
        self.next_snap = 0.0
        self.snap_sent = None
        self.last_claim = 0.0
        social = getattr(mind, "social", None)
        if social is not None and hasattr(social, "register_topic"):
            for key, pool in PHRASES.items():
                social.phrases.setdefault(key, pool)
            social.register_topic("achieve", self.facts, said=self.said, chance=self.cfg["brag_chance"])

    # ---------- данные ----------

    def save(self):
        self.mind.mem.set("achieve", self.st)

    def bus(self):
        bus = getattr(getattr(self.mind, "world", None), "bus", None)
        return bus if isinstance(bus, WorldBus) else None          # только настоящая шина мира (world_bus.Feed)

    def day(self, now):
        return datetime.fromtimestamp(now, self.tz).strftime("%Y-%m-%d")

    def info(self, aid, title=None):
        row = self.table.get(str(aid)) or {}
        name = row.get("name") or (str(title).strip() if title else "") or f"№ {aid}"
        return {"id": int(aid), "name": name, "group": row.get("group"), "score": int(row.get("score") or 0)}

    def count(self):
        return len(self.st["done"])

    def head(self, event):
        for key in ("points", "rank"):
            if isinstance(event.get(key), int):
                self.st[key] = event[key]

    def mark_reward(self, aid, reward):
        un = set(self.st["unrewarded"])
        if reward:
            un.discard(aid)
        else:
            un.add(aid)
        self.st["unrewarded"] = sorted(un)[-200:]

    # ---------- события моста ----------

    def on_list(self, event):
        """Список при входе: первый после установки — молча; дальше новое — объявить."""
        self.head(event)
        fresh = []
        for row in event.get("done") or []:
            if not (isinstance(row, list) and row and isinstance(row[0], int)):
                continue
            aid, at = row[0], (row[1] if len(row) > 1 and isinstance(row[1], int) else 0)
            self.mark_reward(aid, len(row) > 2 and bool(row[2]))
            if str(aid) not in self.st["done"]:
                self.st["done"][str(aid)] = at or int(self.clock())
                fresh.append(aid)
        if not self.st.get("synced"):
            self.st["synced"] = True
            self.save()
            if fresh:
                self.mind.write_decision({"type": "achieve", "event": "synced", "n": len(fresh)})
            return
        self.save()
        for aid in fresh:
            self.announce(aid)

    def on_update(self, event):
        """Одно выполненное достижение (мост шлёт только выполненные)."""
        self.head(event)
        aid = event.get("id")
        if not isinstance(aid, int) or aid <= 0:
            return
        self.mark_reward(aid, bool(event.get("reward")))
        if str(aid) in self.st["done"]:
            self.save()
            return
        self.st["done"][str(aid)] = event.get("at") if isinstance(event.get("at"), int) and event["at"] else int(self.clock())
        self.save()
        self.announce(aid, event.get("title"))

    def on_reward(self, event):
        aid = event.get("id")
        if isinstance(aid, int) and event.get("ok"):
            self.mark_reward(aid, True)
            self.save()
            self.mind.write_decision({"type": "achieve", "event": "reward", "id": aid})

    def announce(self, aid, title=None):
        info = self.info(aid, title)
        now = self.clock()
        self.st["news"] = (self.st["news"] + [{"id": aid, "name": info["name"], "ts": now}])[-20:]
        self.save()
        self.mind.mem.add_event("achievement_done", {"id": aid, "name": info["name"], "group": info["group"],
                                                     "score": info["score"]})
        text = f"Получил(а) достижение «{info['name']}» — по данным сервера."
        self.mind.mem.remember(text, 3)
        self.mind.write_decision({"type": "achieve", "event": "done", **info})
        log.info("%s", text)

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        try:
            self.snapshot(now)
        except sqlite3.Error as e:
            log.warning("шина мира: %s — повторю позже", e)
        await self.claim(now)

    def snapshot(self, now):
        """Тихий снимок «сколько у меня достижений» (затирание): при изменении, не чаще snapshot_minutes."""
        bus = self.bus()
        if not bus or now < self.next_snap or not self.st.get("synced") and not self.st["done"]:
            return
        data = {"n": self.count(), "points": self.st.get("points")}
        if data == self.snap_sent:
            return
        self.next_snap = now + self.cfg["snapshot_minutes"] * 60
        bus.replace("achieve_known", data, 1, now=now)
        self.snap_sent = data

    async def claim(self, now):
        if not self.cfg["claim_rewards"] or not self.st["unrewarded"] or now - self.last_claim < self.cfg["claim_gap_seconds"]:
            return
        state = self.mind.state
        if state.get("dead") or not getattr(self.mind, "fresh_state", True):
            return
        aid = self.st["unrewarded"][0]
        self.last_claim = now
        self.st["claimed"][str(aid)] = now
        self.save()
        await self.mind.execute([{"action": "achieve_reward", "id": aid}], source="achieve",
                                reason=f"достижения: забрать награду за {self.info(aid)['name']}", protocol=True)

    # ---------- сравнение и тема разговора (ORG-066) ----------

    def others(self):
        bus = self.bus()
        if not bus:
            return {}
        try:
            return {b: r["data"] for b, r in bus.latest("achieve_known").items() if b in self.mind.ctx.peers}
        except sqlite3.Error:
            return {}

    def rival(self):
        r = getattr(self.mind, "rivalry", None)
        return (r.st or {}).get("rival") if r else None

    def facts(self, peer, now):
        """Своё свежее достижение, о котором этому жителю ещё не говорил; иначе — счёт против соперника."""
        told = set(self.st["told"].get(peer) or [])
        for rec in reversed(self.st["news"]):
            if rec["id"] in told or now - rec.get("ts", 0) > self.cfg["brag_days"] * 86400:
                continue
            return {"ach": str(rec["name"])[:NAME_MAX], "_id": rec["id"]}
        if peer == self.rival() and self.st["compared"].get(peer) != self.day(now):
            theirs = (self.others().get(peer) or {}).get("n")
            if isinstance(theirs, int) and self.count() > theirs:
                return {"mine": self.count(), "theirs": theirs, "_key": "achieve_lead", "_cmp": True}
        return None

    def said(self, peer, facts, now):
        facts = facts or {}
        if facts.get("_id") is not None:
            self.st["told"][peer] = ((self.st["told"].get(peer) or []) + [facts["_id"]])[-50:]
        if facts.get("_cmp"):
            self.st["compared"][peer] = self.day(now)
        self.save()

    def summary(self):
        if not self.st["done"]:
            return None
        out = {"достижений": self.count()}
        if isinstance(self.st.get("points"), int):
            out["очки"] = self.st["points"]
        if self.st["news"]:
            out["последнее"] = self.st["news"][-1]["name"]
        return out


CHRONICLE_LINES = {"achievement_done": lambda d: f"получил(а) достижение «{d.get('name')}»"}


def count(memory):
    """Для соперничества и метрик: число выполненных достижений жителя (kv achieve)."""
    return len(((memory.get("achieve") or {}).get("done") or {}))
