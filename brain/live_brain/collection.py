"""Коллекции жителя: карты монстров и трофеи (ORG-074). Правила без LLM, только факты памяти и тела.

Источник — события памяти, которые пишет мост тела: kill {monster} и loot {item: имя, amount} (OpenKore
item_gathered). Модуль читает их по курсору (kv collection.cursor), как шина мира (world_bus.Feed.pump), —
поэтому mind.on_event не меняется. Первый запуск историю не объявляет: альбом заполняется картами из state.items,
трофеи — видами из уже записанных побед, молча.

Добыча (drop) — только loot не позже DROP_SEC после своей победы (kill), и в окне TRADE_SEC нет сделки, подарка,
письма или покупки: OpenKore зовёт item_gathered на любое добавление в рюкзак (склад, NPC, сделка), а ложное
«нашёл» хуже молчания. Остальное (карта со склада, от жителя) — в альбом молча, без хвастовства.

    Карта   — предмет типа Card из prices.json (по имени предмета). Альбом — kv collection.album {id: {ts, name, src}}.
              Новая карта (drop) → событие card_found {id, name, first, n} (первая в жизни — шина мира важность 5,
              иначе 3), летопись «нашёл(шла) карту Poring Card (первая карта!)», воспоминание; тема разговора
              «card» в реестре social.register_topic: хвастается каждому жителю один раз в brag_days.
              Карты альбома не продаются: economy.for_sale видит рюкзак через sellable() — одна копия каждой
              карты альбома остаётся, дубликаты можно продать.
    Трофей  — первая победа над видом монстра (trophy_first {monster}: летопись, без шины) и первая добыча
              редкости (trophy_rare {id, name}: тип из rare_types или цена NPC ≥ rare_sell; шина важность 3).

Метрики (ORG-046, report): «карт в альбоме», «трофеев за период». Выключатель: BRAIN_DISABLE=collection или
goals.json "collection": {"enabled": false}.
"""
import json
import logging
import random
import time

from .prices import Prices

log = logging.getLogger("collection")

DEFAULTS = {"enabled": True, "tick_seconds": 10, "rare_sell": 1000, "rare_types": ["Weapon", "Armor"],
            "brag_days": 7, "brag_chance": 0.7}
DROP_SEC = 60                     # loot не позже минуты после своей победы — добыча с монстра
TRADE_SEC = 120                   # сделка/подарок/письмо/покупка рядом по времени — не добыча
TRADE_KINDS = ("deal_complete", "gift_received", "trade_bought", "mail_got", "mail_taken", "buy_result")
BATCH = 500
NAME_MAX = 24
PHRASES = {   # ≤ 60 символов без метки при имени карты до 24 символов
    "card": ["Смотри, у меня новая карта — {card}!",
             "В альбоме прибавилось: {card}. Уже {cards}!",
             "Мне выпала {card}. Берегу её!"],
    "card_first": ["Моя первая карта — {card}! Начинаю альбом.",
                   "Первая карта в жизни: {card}!"],
    "card_re": ["Ого, вот это удача!", "Здорово! Береги её.", "Повезло! Мне бы такую."],
}


class Collection:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "collection", "collection", "collection", True, "world"
    TICK_ORDER = 220

    def __init__(self, mind, world=None, clock=None, rng=None, prices=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("collection") or {}))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.prices = prices or Prices.load()
        self.st = mind.mem.get("collection") or {}
        for key in ("album", "trophies", "rare", "told"):
            self.st.setdefault(key, {})
        self.next_tick = 0.0
        self._names = None
        social = getattr(mind, "social", None)
        if social is not None and hasattr(social, "register_topic"):
            for key, pool in PHRASES.items():
                social.phrases.setdefault(key, pool)
            social.register_topic("card", self.facts, said=self.said, chance=self.cfg["brag_chance"])

    # ---------- данные ----------

    def save(self):
        self.mind.mem.set("collection", self.st)

    def names(self):
        """Имя предмета -> ID (prices.json); у карт — приоритет (имя карты уникально, кроме дублей базы)."""
        if self._names is None:
            self._names = {}
            for iid, it in sorted(self.prices.data.items(), key=lambda kv: (kv[1].get("type") != "Card", kv[0])):
                if isinstance(it, dict) and it.get("name"):
                    self._names.setdefault(str(it["name"]).lower(), iid)
        return self._names

    def item_id(self, name):
        return self.names().get(str(name or "").strip().lower())

    def is_rare(self, iid):
        it = self.prices.item(iid) or {}
        if not it or it.get("type") == "Card":
            return False
        return it.get("type") in self.cfg["rare_types"] or int(it.get("sell") or 0) >= self.cfg["rare_sell"]

    def album(self):
        return self.st["album"]

    def sellable(self, items):
        """Рюкзак для продажи (economy.for_sale): одна копия каждой карты альбома остаётся, дубликаты — можно."""
        out = dict(items or {})
        for iid in self.album():
            if iid in out:
                out[iid] = max(0, int(out[iid] or 0) - 1)
        return out

    # ---------- такт ----------

    def tick(self):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        mem = self.mind.mem
        if self.st.get("cursor") is None:
            self.seed()
            return
        rows = mem.db.execute("SELECT id, ts, kind, data FROM events WHERE id > ? AND kind IN ('kill', 'loot') "
                              "ORDER BY id LIMIT ?", (self.st["cursor"], BATCH)).fetchall()
        changed = self.inventory()
        ripe = now - TRADE_SEC                         # сделка приходит после предметов — ждать окно целиком
        rows = rows[:next((i for i, r in enumerate(rows) if r[1] > ripe), len(rows))]
        if rows:
            last_kill = self.st.get("last_kill", 0)
            for _id, ts, kind, data in rows:
                d = _loads(data)
                if kind == "kill":
                    last_kill = ts
                    self.on_kill(ts, d.get("monster"))
                else:
                    self.on_loot(ts, d.get("item"), last_kill)
            self.st["cursor"] = rows[-1][0]
            self.st["last_kill"] = last_kill
            changed = True
        if changed:
            self.save()

    def seed(self):
        """Первый запуск: курсор на конец, альбом — карты рюкзака, трофеи — виды прошлых побед (молча)."""
        mem = self.mind.mem
        self.st["cursor"] = mem.db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        for (data,) in mem.db.execute("SELECT data FROM events WHERE kind = 'kill'"):
            m = _loads(data).get("monster")
            if m:
                self.st["trophies"].setdefault(str(m), 0)
        self.inventory()
        self.save()

    def inventory(self):
        """Карты в state.items, которых нет в альбоме (склад, сделка, старые) — в альбом молча."""
        changed = False
        for iid, n in ((self.mind.state or {}).get("items") or {}).items():
            iid = str(iid)
            if int(n or 0) > 0 and iid not in self.album() and self.prices.is_card(iid):
                self.album()[iid] = {"ts": self.clock(), "name": self.prices.name(iid), "src": "inventory"}
                changed = True
        return changed

    def dropped(self, ts, last_kill):
        if not last_kill or not 0 <= ts - last_kill <= DROP_SEC:
            return False
        n = self.mind.mem.db.execute(
            f"SELECT COUNT(*) FROM events WHERE kind IN ({', '.join('?' * len(TRADE_KINDS))}) AND ts >= ? AND ts <= ?",
            (*TRADE_KINDS, ts - TRADE_SEC, ts + TRADE_SEC)).fetchone()[0]
        return n == 0

    def on_kill(self, ts, monster):
        if not monster or str(monster) in self.st["trophies"]:
            return
        self.st["trophies"][str(monster)] = ts
        self.mind.mem.add_event("trophy_first", {"monster": str(monster)})
        self.mind.write_decision({"type": "collection", "event": "trophy_first", "monster": str(monster)})

    def on_loot(self, ts, name, last_kill):
        iid = self.item_id(name)
        if not iid:
            return
        if self.prices.is_card(iid):
            if iid in self.album():
                return
            drop = self.dropped(ts, last_kill)
            self.album()[iid] = {"ts": ts, "name": self.prices.name(iid), "src": "drop" if drop else "inventory"}
            if drop:
                self.card_found(ts, iid)
        elif self.is_rare(iid) and iid not in self.st["rare"]:
            if not self.dropped(ts, last_kill):
                return
            self.st["rare"][iid] = ts
            nm = self.prices.name(iid)
            self.mind.mem.add_event("trophy_rare", {"id": iid, "name": nm})
            self.mind.mem.remember(f"Добыл(а) редкость: {nm} (по данным игры).", 3)
            self.mind.write_decision({"type": "collection", "event": "trophy_rare", "id": iid, "name": nm})
            log.info("редкий трофей: %s", nm)

    def card_found(self, ts, iid):
        nm = self.prices.name(iid)
        found = [i for i, c in self.album().items() if c.get("src") == "drop"]
        first = len(found) == 1
        n = len(self.album())
        self.mind.mem.add_event("card_found", {"id": iid, "name": nm, "first": first, "n": n})
        self.mind.mem.remember(f"Нашёл(шла) карту {nm}" + (" — первая карта в жизни!" if first else "")
                               + f" В альбоме карт: {n}.", 5 if first else 3)
        self.mind.write_decision({"type": "collection", "event": "card_found", "id": iid, "name": nm,
                                  "first": first, "n": n})
        log.info("новая карта: %s%s", nm, " (первая)" if first else "")

    # ---------- тема разговора (ORG-066) ----------

    def facts(self, peer, now):
        """Свежая найденная карта, о которой этому жителю ещё не говорил -> {card, cards}."""
        told = set(self.st["told"].get(peer) or [])
        fresh = [(c["ts"], iid, c) for iid, c in self.album().items()
                 if c.get("src") == "drop" and now - c.get("ts", 0) <= self.cfg["brag_days"] * 86400
                 and iid not in told]
        if not fresh:
            return None
        _, iid, c = max(fresh)
        drops = sum(1 for x in self.album().values() if x.get("src") == "drop")
        out = {"card": str(c.get("name") or iid)[:NAME_MAX], "cards": len(self.album()), "_card": iid}
        if drops == 1:
            out["_key"] = "card_first"
        return out

    def said(self, peer, facts, now):
        iid = (facts or {}).get("_card")
        if iid:
            told = [i for i in (self.st["told"].get(peer) or []) if i in self.album()] + [iid]
            self.st["told"][peer] = told[-50:]
            self.save()

    def summary(self):
        alb = self.album()
        if not alb:
            return None
        last = max(alb.values(), key=lambda c: c.get("ts", 0))
        return {"карт": len(alb), "последняя": last.get("name"), "трофеев": len(self.st["trophies"]) + len(self.st["rare"])}


def _loads(text):
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def album_size(memory):
    """Для метрик (report, дашборд): число карт в альбоме из kv collection."""
    return len(((memory.get("collection") or {}).get("album") or {}))


CHRONICLE_LINES = {
    "card_found": lambda d: f"нашёл(шла) карту {d.get('name')}" + (" — первая карта!" if d.get("first") else
                                                                   f" (в альбоме {d.get('n')})"),
    "trophy_rare": lambda d: f"добыл(а) редкость: {d.get('name')}",
    "trophy_first": lambda d: f"первая победа над {d.get('monster')}",
}
