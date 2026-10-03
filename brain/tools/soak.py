#!/usr/bin/env python3
"""soak: долгий прогон мира — 3–4 настоящих Mind на общей шине мира, простой мир-симулятор, 7–14 игровых суток.

Ищет баги, которые видны только за много дней и при нескольких жителях (docs/SOAK.md): рост памяти, рост времени
тика, спам шёпотов и реплик, повторы фраз, «застревания» в одном режиме, утечка или бесконечный рост зени,
отношения, ушедшие в ±максимум, застывшие мечты, смерти в цикле, исключения модулей.

Жители: Arkady (bot01) и Vera (bot02) — персоны brain/personas; Bram (Merchant) и Ilsa (Archer) — синтетические,
персоны из bots/templates/<шаблон>/persona.json. Все — жители друг для друга (peers), шина мира — одна world.sqlite.
Модули и их настройки — как в коде и brain/world/goals.json (ничего не включается и не выключается).

Мир (World) исполняет команды мозга упрощённо, как мост и плагины OpenKore:
  движение — hunt/explore/meet_point/follow/unstuck: переход на другую карту TRAVEL с, по карте — WALK с;
  охота — на полевой карте, совпадающей с lockMap: attack/kill (монстры карты из atlas.json), лут, опыт, уровни
  (level_up), урон, зелья, смерть с вероятностью (died -> возрождение у точки сохранения);
  шёпот жителю — chat_private адресату через 1 с и delivery отправителю (офлайн-адресат — delivery ok=false);
  say — chat_public жителям рядом; give / offer_sell+offer_buy — сделка (give_result, buy_result, deal_complete);
  service — продажа лута NPC (источник зени) и докупка зелий (сток зени); лавка — продажи посторонним;
  почта RODEX и банк — как плагин economy и мост; job_change — итог этапа через QUEST с; sleep — relog: тело
  офлайн seconds, затем hello и снова state; на каждое действие — ack.
Зени мира учитываются бухгалтерией: карманы + банк + почта в пути == начальные + источники − стоки.

Часы ускорены: один такт мира = dt игровых секунд (каждый житель: on_message входящих + step). dt подбирается
калибровкой (--budget минут на весь прогон): после каждого игрового часа — по фактической скорости.

Запуск:  cd brain && python3 tools/soak.py [--days 7] [--residents 4] [--budget 18] [--seed 1] [--out DIR]
         python3 tools/soak.py --days 1 --residents 2 --dt 5      # быстрый прогон (smoke: tests/test_soak.py)
Итог — таблица по дням (stdout, --markdown для docs/SOAK.md) и список находок (exit 1, если есть нарушения).
"""
import argparse
import asyncio
import json
import logging
import math
import os
import random
import re
import sys
import tempfile
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

BRAIN = Path(__file__).resolve().parents[1]
REPO = BRAIN.parent
if str(BRAIN) not in sys.path:
    sys.path.insert(0, str(BRAIN))

from live_brain import replay                     # noqa: E402
from live_brain.config import Settings            # noqa: E402
from live_brain.gate import RuleGate              # noqa: E402
from live_brain.memory import Memory              # noqa: E402
from live_brain.mind import Mind                  # noqa: E402
from live_brain.routine import load_world         # noqa: E402
from live_brain.world_bus import WorldBus         # noqa: E402

WORLD = load_world(BRAIN / "world" / "goals.json")
TZ = timezone(timedelta(hours=WORLD.get("timezone_offset_hours", 0)))
ATLAS = json.loads((BRAIN / "world" / "atlas.json").read_text(encoding="utf-8"))

RESIDENTS = (
    {"name": "Arkady", "bot": "bot01", "persona": BRAIN / "personas" / "bot01.json",
     "body": {"job": "Swordsman", "lv": 41, "job_lv": 20, "sex": "Male", "zeny": 40000,
              "items": {"501": 30, "909": 40, "4001": 1, "602": 2}}},
    {"name": "Vera", "bot": "bot02", "persona": BRAIN / "personas" / "bot02.json",
     "body": {"job": "Acolyte", "lv": 38, "job_lv": 25, "sex": "Female", "zeny": 25000, "items": {"501": 30},
              "support_skills": {"AL_HEAL": 10, "AL_BLESSING": 5, "AL_INCAGI": 3}}},
    {"name": "Bram", "bot": "bot03", "persona": REPO / "bots" / "templates" / "merchant" / "persona.json",
     "body": {"job": "Merchant", "lv": 33, "job_lv": 30, "sex": "Male", "zeny": 60000,
              "items": {"501": 20, "909": 15, "914": 10}, "vend": {"can": 1, "open": 0}}},
    {"name": "Ilsa", "bot": "bot04", "persona": REPO / "bots" / "templates" / "archer" / "persona.json",
     "body": {"job": "Archer", "lv": 35, "job_lv": 28, "sex": "Female", "zeny": 20000,
              "items": {"501": 25, "1750": 500, "1019": 5}}},
)

# лут полей (id -> цена продажи NPC); карта Poring Card — в альбом, не продаётся
LOOT = {"909": 3, "914": 3, "705": 10, "949": 5, "1019": 10, "7065": 8}
CARD, POTION, POTION_PRICE, WING = "4001", "501", 50, "602"
SAVE = ("prontera", 116, 73)
# такт мира не крупнее: модули считают промежутки между тиками (social.together — gap <= 5 с, распорядок —
# MAX_TICK_GAP); при dt 8 «время вместе» не копилось вовсе и отношения Vera стояли на 0 все 12 суток
MAX_DT = 4
TOWNS = {"prontera", "izlude", "geffen", "payon", "morocc", "alberta", "aldebaran"}


def map_monsters(m):
    mons = ((ATLAS.get("maps") or {}).get(m) or {}).get("monsters") or {}
    out = [((ATLAS["monsters"].get(k) or {}).get("name"), n) for k, n in mons.items()]
    out = [(name, n) for name, n in out if name]
    return out or [("Poring", 10), ("Lunatic", 5), ("Fabre", 5)]


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class Ledger:
    """Бухгалтерия зени мира: всё, что появилось и исчезло не между жителями."""

    def __init__(self):
        self.minted = Counter()                  # источник -> зени (продажа NPC, лавка посторонним)
        self.burned = Counter()                  # сток -> зени (зелья, сбор почты)


class Body:
    def __init__(self, name, spec, t, rng):
        self.name = name
        self.rng = rng
        b = spec["body"]
        self.s = {"type": "state", "name": name, "job": b["job"], "lv": b["lv"], "job_lv": b["job_lv"],
                  "sex": b["sex"], "sp_pct": 90, "zeny": b["zeny"], "ai": "auto", "map": "prontera",
                  "x": 150 + rng.randint(-5, 5), "y": 180 + rng.randint(-5, 5), "hp_pct": 100, "dead": False,
                  "weight_pct": 10, "items": dict(b["items"]), "lock_map": "prontera", "lock_x": None,
                  "lock_y": None, "activity": "idle", "players": [], "friends": [], "party": None,
                  "party_members": [], "vend": dict(b.get("vend") or {"can": 0, "open": 0}), "sitting": False,
                  "follow": None, "chat_room": None, "dir": 0,
                  "pet": {"has": True, "running": False, "items": {}, "eggs": [], "near": {}}}
        if b.get("support_skills"):
            self.s["support_skills"] = dict(b["support_skills"])
        self.exp = 0                              # побед до уровня
        self.jexp = 0
        self.go = None                            # (когда прибуду, карта, x, y)
        self.quest = None                         # (когда, path, stage, id)
        self.dead_until = None
        self.offline_until = None                 # сон (relog)
        self.inbox = []
        self.bank = 0
        self.kills = 0
        self.deaths = []
        self.hunt_seconds = 0

    @property
    def online(self):
        return self.offline_until is None


class World:
    TRAVEL, WALK, QUEST, RESPAWN = 90, 20, 25, 20
    KILL_EVERY = 30                                # с на победу
    P_DEATH = 0.0015                               # на победу (≈ 1 смерть на 11 ч охоты)

    def __init__(self, specs, t0, seed=1):
        self.t = t0
        self.rng = random.Random(seed)
        self.bodies = {s["name"]: Body(s["name"], s, t0, random.Random(seed * 100 + i)) for i, s in enumerate(specs)}
        self.ledger = Ledger()
        self.start_zeny = sum(b.s["zeny"] for b in self.bodies.values())
        self.trades = {}                           # (продавец, покупатель, предмет) -> {offer_sell, offer_buy, since}
        self.mail = defaultdict(list)              # адресат -> [{mail_id, from, title, zeny, item, amount, read}]
        self.mail_seq = 0
        self.parties = {}                          # имя группы -> [члены]
        self.chat = Counter()                      # (житель, action) -> счётчик
        self.lines = defaultdict(list)             # житель -> [(t, action, text)]
        self.action_log = defaultdict(list)        # житель -> [(t, action, slim state)] для replay.invariants

    # ---------- сообщения мозгу ----------

    def send(self, _who, _delay=0, **msg):
        self.bodies[_who].inbox.append(dict(msg, ts=self.t + _delay))

    def event(self, _who, _delay=0, **kw):
        self.send(_who, _delay, type="event", **kw)

    def take(self, name, t):
        b = self.bodies[name]
        due = [m for m in b.inbox if m["ts"] <= t]
        if due:
            b.inbox = [m for m in b.inbox if m["ts"] > t]
        return sorted(due, key=lambda m: m["ts"])

    def near(self, a, b, cells):
        sa, sb = self.bodies[a].s, self.bodies[b].s
        return (self.bodies[a].online and self.bodies[b].online and sa["map"] == sb["map"]
                and max(abs(sa["x"] - sb["x"]), abs(sa["y"] - sb["y"])) <= cells)

    def zeny_total(self):
        pockets = sum(b.s["zeny"] for b in self.bodies.values())
        banks = sum(b.bank for b in self.bodies.values())
        posted = sum(int(m.get("zeny") or 0) for box in self.mail.values() for m in box if not m.get("taken"))
        return pockets + banks + posted

    def zeny_expected(self):
        return self.start_zeny + sum(self.ledger.minted.values()) - sum(self.ledger.burned.values())

    # ---------- команды мозга ----------

    def apply(self, who, a):
        b = self.bodies[who]
        s = b.s
        kind = a.get("action")
        self.action_log[who].append((self.t, dict(a), {"dead": s["dead"], "hp_pct": s["hp_pct"], "map": s["map"]}))
        ok, err = True, None
        other = a.get("to") or a.get("from")
        if not b.online:
            ok, err = False, "не в игре"
        elif kind in replay.MOVES + ("meet_point", "sleep", "shop_open", "skill_on_player"):
            s["chat_room"] = None                                # brainBridge: chat leave перед движением
        if not ok:
            pass
        elif kind in ("hunt", "explore", "set_hunt_map"):
            s.update(lock_map=a["map"], lock_x=a.get("x"), lock_y=a.get("y"), sitting=False, follow=None)
        elif kind == "meet_point":
            s.update(lock_map=a["map"], lock_x=a.get("x"), lock_y=a.get("y"), sitting=False, follow=None)
        elif kind == "clear_point":
            s.update(lock_x=None, lock_y=None)
        elif kind == "follow":
            s["follow"] = a.get("to")
        elif kind == "unfollow":
            s["follow"] = None
        elif kind == "sit":
            s["sitting"] = True
        elif kind == "stand":
            s["sitting"] = False
        elif kind == "unstuck":
            s["x"] += self.rng.randint(-8, 8)
            s["y"] += self.rng.randint(-8, 8)
        elif kind == "pause":
            s["paused"] = True
        elif kind == "resume":
            s["paused"] = False
        elif kind == "chat_room":
            s["chat_room"] = a.get("title") if a.get("op") == "open" else None
        elif kind in ("whisper", "say", "party_say", "guild_say"):
            self.chat[(who, kind)] += 1
            self.lines[who].append((self.t, kind, str(a.get("text", ""))))
            if kind == "whisper":
                if other in self.bodies and self.bodies[other].online:
                    self.event(other, 1, kind="chat_private", **{"from": who}, text=a["text"])
                    self.send(who, 1, type="delivery", id=a.get("id"), action="whisper", to=other, ok=True)
                elif other in self.bodies:
                    self.send(who, 2, type="delivery", id=a.get("id"), action="whisper", to=other, ok=False,
                              reason="адресат не в игре")
            elif kind == "say":
                for o in self.bodies:
                    if o != who and self.near(who, o, 14):
                        self.event(o, 1, kind="chat_public", **{"from": who}, text=a["text"])
                self.send(who, 1, type="delivery", id=a.get("id"), action="say", ok=True)
            elif kind == "party_say" and s.get("party"):
                for o in self.parties.get(s["party"], []):
                    if o != who and self.bodies[o].online:
                        self.event(o, 1, kind="chat_party", **{"from": who}, text=a["text"])
        elif kind == "emote" or kind == "look_at":
            pass
        elif kind == "party_create":
            name = a.get("name") or f"LR_{who}"
            self.leave_party(who)
            self.parties[name] = [who]
            s["party"] = name
        elif kind == "party_invite":
            pname = s.get("party")
            if pname and other in self.bodies and self.bodies[other].online and pname.startswith("LR_"):
                self.leave_party(other)
                self.parties[pname].append(other)
                self.bodies[other].s["party"] = pname
                self.event(other, 1, kind="party_joined_auto", party=pname)
        elif kind == "party_accept":
            pass
        elif kind == "party_leave":
            self.leave_party(who)
        elif kind == "friend_request":
            if other in self.bodies and other not in s["friends"]:
                s["friends"].append(other)
                self.bodies[other].s["friends"].append(who)
        elif kind == "service":
            self.service(who)
        elif kind == "shop_open":
            if s["vend"].get("can"):
                s["vend"]["open"] = 1
                s["sitting"] = False
        elif kind == "shop_close":
            s["vend"]["open"] = 0
        elif kind == "offer_shop":
            s["shop"] = list(a.get("items") or [])
        elif kind == "give":
            self.give(who, a)
        elif kind in ("offer_sell", "offer_buy"):
            self.offer(who, a)
        elif kind == "mail_send":
            self.mail_send(who, a)
        elif kind == "mail_check":
            self.mail_check(who)
        elif kind == "mail_take":
            self.mail_take(who, a)
        elif kind in ("bank_check", "bank_deposit", "bank_withdraw"):
            self.bank_op(who, kind, a)
        elif kind == "job_change":
            b.quest = (self.t + self.QUEST, a.get("path"), a.get("stage"), a.get("id"))
            s["job_change"] = {"running": True, "stage": a.get("stage")}
            s.update(lock_map=None, lock_x=None, lock_y=None)
        elif kind == "skill_on_player":
            t = self.bodies.get(other)
            if t and self.near(who, other, 9) and not t.s["dead"] and s["sp_pct"] >= 10:
                s["sp_pct"] -= 5
                amount = None
                if a.get("skill") == "AL_HEAL":
                    amount = min(30, 100 - t.s["hp_pct"]) * 10 or 1
                    t.s["hp_pct"] = min(100, t.s["hp_pct"] + 30)
                for n in (who, other):
                    self.event(n, 1, kind="support", skill=a.get("skill"), **{"from": who}, to=other, amount=amount)
        elif kind == "arrowcraft":
            item = str(a.get("item"))
            if s["items"].get(item, 0) > 0:
                s["items"][item] -= 1
                s["items"]["1750"] = s["items"].get("1750", 0) + 40
                self.event(who, 1, kind="arrowcraft_result", item=a.get("item"), ok=True)
            else:
                self.event(who, 1, kind="arrowcraft_result", item=a.get("item"), ok=False, reason="нет предмета")
        elif kind == "sleep":
            sec = max(600, min(43200, int(a.get("seconds") or 0)))
            b.offline_until = self.t + sec
            s["sitting"] = False                               # группа rAthena переживает выход: участник офлайн
            for key in [k for k in self.trades if who in k[:2]]:
                del self.trades[key]                           # relog обрывает сделку
        elif kind in ("pet_tame", "pet_hatch", "pet_setup", "craft_setup", "achieve_reward", "guild_expect",
                      "refine", "spar", "spar_stop", "guild_create", "guild_invite"):
            pass                                                   # выключенные по умолчанию или без следствий
        else:
            ok, err = False, f"неизвестное действие {kind}"
        if a.get("id") is not None:
            self.send(who, 0, type="ack", id=a["id"], ok=ok, command=kind, error=err)

    def leave_party(self, who):
        s = self.bodies[who].s
        p = s.get("party")
        if p and p in self.parties:
            self.parties[p] = [m for m in self.parties[p] if m != who]
            if not self.parties[p]:
                del self.parties[p]
        s["party"] = None
        s["party_members"] = []

    def service(self, who):
        s = self.bodies[who].s
        got = 0
        for item, price in LOOT.items():
            n = s["items"].get(item, 0)
            if n and item not in ("1019",) and self.keep(who, item):
                continue
            if n:
                got += n * price
                s["items"][item] = 0
        s["zeny"] += got
        self.ledger.minted["npc_sold"] += got
        if got:
            self.event(who, 2, kind="npc_sold", zeny=got)
        need = max(0, 30 - s["items"].get(POTION, 0))
        cost = need * POTION_PRICE
        if need and s["zeny"] >= cost:
            s["zeny"] -= cost
            s["items"][POTION] = s["items"].get(POTION, 0) + need
            self.ledger.burned["potions"] += cost
        self.weigh(who)

    def keep(self, who, item):
        return item == "1019" and self.bodies[who].s["job"] == "Archer"     # материал стрел

    def weigh(self, who):
        s = self.bodies[who].s
        n = sum(v for k, v in s["items"].items() if k in LOOT)
        s["weight_pct"] = min(95, 10 + n // 6)

    def give(self, who, a):
        to, item, n = a.get("to"), str(a.get("item")), int(a.get("amount") or 0)
        s = self.bodies[who].s
        ok = to in self.bodies and self.near(who, to, 14) and n > 0
        if ok and item in ("z", "zeny"):
            ok = s["zeny"] >= n
        elif ok:
            ok = s["items"].get(item, 0) >= n
        reason = None if ok else "житель не рядом или нечего дать"
        if ok:
            o = self.bodies[to].s
            if item in ("z", "zeny"):
                s["zeny"] -= n
                o["zeny"] += n
            else:
                s["items"][item] -= n
                o["items"][item] = o["items"].get(item, 0) + n
            self.event(to, self.WALK, kind="deal_complete", **{"with": who}, gave=False)
            self.event(who, self.WALK, kind="deal_complete", **{"with": to}, gave=True)
        self.event(who, self.WALK, kind="give_result", id=a.get("id"), to=to, item=a.get("item"), amount=n, ok=ok,
                   reason=reason)

    def offer(self, who, a):
        other = a.get("to") or a.get("from")
        if other not in self.bodies:
            return
        seller, buyer = (who, other) if a["action"] == "offer_sell" else (other, who)
        key = (seller, buyer, str(a.get("item")))
        tr = self.trades.setdefault(key, {"since": self.t})
        tr[a["action"]] = dict(a)
        if "offer_sell" not in tr or "offer_buy" not in tr:
            return
        del self.trades[key]
        sb, bb = self.bodies[seller].s, self.bodies[buyer].s
        item, n, price = key[2], int(tr["offer_sell"]["amount"]), int(tr["offer_sell"]["price"])
        ok = self.near(seller, buyer, 14) and sb["items"].get(item, 0) >= n and bb["zeny"] >= price
        if ok:
            sb["items"][item] -= n
            bb["items"][item] = bb["items"].get(item, 0) + n
            sb["zeny"] += price
            bb["zeny"] -= price
        self.event(seller, self.WALK, kind="give_result", id=tr["offer_sell"].get("id"), to=buyer, item=int(item),
                   amount=n, price=price, paid=price if ok else 0, ok=ok, reason=None if ok else "далеко или нет денег")
        self.event(buyer, self.WALK, kind="buy_result", id=tr["offer_buy"].get("id"), **{"from": seller},
                   item=int(item), amount=n, price=price, ok=ok, reason=None if ok else "сделка не состоялась")

    def expire_trades(self):
        for key, tr in list(self.trades.items()):
            if self.t - tr["since"] < 120:
                continue
            del self.trades[key]                                       # вторая сторона не пришла: плагин сдаётся
            seller, buyer, item = key
            if "offer_sell" in tr:
                a = tr["offer_sell"]
                self.event(seller, 0, kind="give_result", id=a.get("id"), to=buyer, item=int(item),
                           amount=a.get("amount"), price=a.get("price"), paid=0, ok=False,
                           reason="покупатель не пришёл")
            if "offer_buy" in tr:
                a = tr["offer_buy"]
                self.event(buyer, 0, kind="buy_result", id=a.get("id"), **{"from": seller}, item=int(item),
                           amount=a.get("amount"), price=a.get("price"), ok=False, reason="продавец не пришёл")

    def mail_send(self, who, a):
        s = self.bodies[who].s
        to, zeny = a.get("to"), int(a.get("zeny") or 0)
        item, n = (str(a["item"]) if a.get("item") else None), int(a.get("amount") or 0)
        fee = 100 + zeny // 50                                         # сбор RODEX (сток)
        ok = to in self.bodies and s["zeny"] >= zeny + fee and (not item or s["items"].get(item, 0) >= n)
        if ok:
            s["zeny"] -= zeny + fee
            self.ledger.burned["mail_fee"] += fee
            if item:
                s["items"][item] -= n
            self.mail_seq += 1
            self.mail[to].append({"mail_id": self.mail_seq, "from": who, "title": a.get("title"), "zeny": zeny,
                                  "item": item, "amount": n, "seen": False, "taken": False})
        self.event(who, 2, kind="mail_result", id=a.get("id"), to=to, title=a.get("title"), zeny=zeny,
                   **({"item": item, "amount": n} if item else {}), ok=ok, reason=None if ok else "нет денег")

    def mail_check(self, who):
        for m in self.mail[who]:
            if not m["seen"] and not m["taken"]:
                m["seen"] = True
                self.event(who, 2, kind="mail_received", mail_id=m["mail_id"], **{"from": m["from"]},
                           title=m["title"], attach=bool(m["zeny"] or m["item"]))

    def mail_take(self, who, a):
        s = self.bodies[who].s
        mid = int(a.get("mail_id") or 0)
        m = next((m for m in self.mail[who] if m["mail_id"] == mid and not m["taken"]), None)
        if m:
            m["taken"] = True
            s["zeny"] += m["zeny"]
            if m["item"]:
                s["items"][m["item"]] = s["items"].get(m["item"], 0) + m["amount"]
            self.mail[who] = [x for x in self.mail[who] if not x["taken"]]
        self.event(who, 2, kind="mail_taken", id=a.get("id"), mail_id=mid, **{"from": (m or {}).get("from")},
                   zeny=(m or {}).get("zeny", 0), items=[[m["item"], m["amount"]]] if m and m["item"] else [],
                   ok=bool(m), reason=None if m else "нет письма")

    def bank_op(self, who, kind, a):
        b = self.bodies[who]
        if kind == "bank_check":
            self.event(who, 1, kind="bank_balance", vault=b.bank)
            return
        z = int(a.get("zeny") or 0)
        op = "deposit" if kind == "bank_deposit" else "withdraw"
        ok = z > 0 and (b.s["zeny"] >= z if op == "deposit" else b.bank >= z)
        if ok:
            b.s["zeny"] += -z if op == "deposit" else z
            b.bank += z if op == "deposit" else -z
        self.event(who, 1, kind="bank_result", op=op, ok=ok, reason=0 if ok else 1, vault=b.bank, zeny=b.s["zeny"])

    # ---------- мир за такт ----------

    def tick(self, t, dt):
        self.t = t
        self.expire_trades()
        for name, b in self.bodies.items():
            s = b.s
            if b.offline_until is not None:
                if t < b.offline_until:
                    continue
                b.offline_until = None                     # relog: вход в игру у точки сохранения? — там же, где вышел
                s.update(activity="idle", sitting=False)
                self.send(name, 0, type="hello", char=name)
                self.event(name, 0, kind="in_game")
                self.mail_check(name)
            if b.dead_until and t >= b.dead_until:
                b.dead_until = None
                s.update(dead=False, hp_pct=5, map=SAVE[0], x=SAVE[1], y=SAVE[2], activity="idle", sitting=False)
                b.go = None
            if b.quest and t >= b.quest[0]:
                _, path, stage, qid = b.quest
                b.quest = None
                s["job_change"] = {"running": False}
                s.update(lock_map=s["map"], lock_x=None, lock_y=None)
                self.event(name, 0, kind="job_change_result", id=qid, path=path, stage=stage, step=2, ok=True,
                           reason="ok")
            if not s["dead"] and not b.quest:
                self.move(name, b, t)
                self.hunt(name, b, t, dt)
            self.weigh(name)
            if s["vend"].get("open") and self.rng.random() < dt / 1800:
                self.vend_sale(name)
        for name, b in self.bodies.items():
            if not b.online:
                continue
            s = b.s
            s["players"] = [{"name": o, "x": self.bodies[o].s["x"], "y": self.bodies[o].s["y"],
                             "job": self.bodies[o].s["job"], "lv": self.bodies[o].s["lv"],
                             "sex": self.bodies[o].s["sex"]}
                            for o in self.bodies if o != name and self.near(name, o, 14)]
            p = s.get("party")
            s["party_members"] = [{"name": m, "online": self.bodies[m].online, "hp_pct": self.bodies[m].s["hp_pct"],
                                   "map": self.bodies[m].s["map"], "x": self.bodies[m].s["x"],
                                   "y": self.bodies[m].s["y"], "leader": m == self.parties[p][0],
                                   "lv": self.bodies[m].s["lv"]}
                                  for m in self.parties.get(p, [])] if p else []
            friends = [{"name": f, "online": self.bodies[f].online} for f in s["friends"]]   # как state плагина
            self.send(name, 0, **dict(s, items=dict(s["items"]), vend=dict(s["vend"]), players=list(s["players"]),
                                      friends=friends))

    def target(self, b):
        s = b.s
        f = s.get("follow")
        if f in self.bodies and self.bodies[f].online and not self.bodies[f].s["dead"]:
            fs = self.bodies[f].s
            return fs["map"], fs["x"] + 1, fs["y"] + 1
        if not s.get("lock_map"):
            return None
        if s.get("lock_x") is not None:
            return s["lock_map"], int(s["lock_x"]), int(s["lock_y"])
        if s["map"] == s["lock_map"]:
            return None
        return s["lock_map"], 100 + self.rng.randint(-20, 20), 100 + self.rng.randint(-20, 20)

    def move(self, name, b, t):
        s = b.s
        if b.go and t >= b.go[0]:
            _, m, x, y = b.go
            s.update(map=m, x=x, y=y)
            b.go = None
        tgt = self.target(b)
        if tgt and not b.go:
            m, x, y = tgt
            if s["map"] != m or max(abs(s["x"] - x), abs(s["y"] - y)) > 3:
                travel = self.TRAVEL if m in TOWNS or m.startswith("prt_") else self.TRAVEL * 3
                b.go = (t + (self.WALK if s["map"] == m else travel), m, x, y)
                s["sitting"] = False

    def hunt(self, name, b, t, dt):
        s = b.s
        field = s["map"] not in TOWNS and s["map"] == s.get("lock_map") and not b.go and not s.get("sitting")
        hunting = field and not s.get("paused") and s.get("lock_x") is None
        if not hunting:
            s["activity"] = "route" if b.go else "idle"
            regen = dt * (0.6 if s.get("sitting") else 0.2)             # % в секунду: сидя втрое быстрее
            b.regen = getattr(b, "regen", 0.0) + regen                       # дробная часть копится, в state — целые
            whole = int(b.regen)
            b.regen -= whole
            s["hp_pct"] = min(100, s["hp_pct"] + whole)
            s["sp_pct"] = min(100, s["sp_pct"] + whole)
            return
        b.hunt_seconds += dt
        s["activity"] = "attack"
        rng = b.rng
        b.regen = getattr(b, "regen", 0.0) + dt * 0.1                    # между боями HP понемногу растёт
        whole = int(b.regen)
        b.regen -= whole
        s["hp_pct"] = min(100, s["hp_pct"] + whole)
        if rng.random() >= dt / self.KILL_EVERY:
            return
        mons = map_monsters(s["map"])
        monster = rng.choices([m for m, _ in mons], [n for _, n in mons])[0]
        self.event(name, 0, kind="attack", monster=monster, map=s["map"], hp_pct=s["hp_pct"])
        s["hp_pct"] = max(0, s["hp_pct"] - rng.randint(0, 9))
        s["x"] = max(10, min(390, s["x"] + rng.randint(-4, 4)))
        s["y"] = max(10, min(390, s["y"] + rng.randint(-4, 4)))
        if s["hp_pct"] < 45 and s["items"].get(POTION, 0) > 0:
            s["items"][POTION] -= 1
            s["hp_pct"] = min(100, s["hp_pct"] + 25)
        if s["hp_pct"] <= 0 or rng.random() < self.P_DEATH:
            self.die(name, b)
            return
        b.kills += 1
        self.event(name, 1, kind="kill", monster=monster, map=s["map"])
        if rng.random() < 0.4:
            item = rng.choice(list(LOOT))
            s["items"][item] = s["items"].get(item, 0) + 1
            self.event(name, 1, kind="loot", item=item, amount=1)
        if rng.random() < 0.002:
            s["items"][CARD] = s["items"].get(CARD, 0) + 1
            self.event(name, 1, kind="loot", item=CARD, amount=1)
        b.exp += 1
        if b.exp >= 40 + s["lv"] * 4:
            b.exp = 0
            s["lv"] += 1
            self.event(name, 1, kind="level_up", level=s["lv"])
        b.jexp += 1
        if b.jexp >= 30 + s["job_lv"] * 3 and s["job_lv"] < 50:
            b.jexp = 0
            s["job_lv"] += 1

    def die(self, name, b):
        s = b.s
        s.update(dead=True, hp_pct=0, activity="dead", sitting=False)
        b.go = None
        b.dead_until = self.t + self.RESPAWN
        b.deaths.append(self.t)
        self.event(name, 0, kind="died", map=s["map"])

    def vend_sale(self, name):
        s = self.bodies[name].s
        shop = [i for i in (s.get("shop") or []) if s["items"].get(str(i.get("id")), 0) > 0]
        if not shop:
            return
        it = self.rng.choice(shop)
        item, price = str(it["id"]), int(it.get("price") or 0)
        s["items"][item] -= 1
        s["zeny"] += price
        self.ledger.minted["vend_sold"] += price
        self.event(name, 1, kind="vend_sold", item=int(item), amount=1, zeny=price)


# ---------------------------------------------------------------- наблюдение


class LogTrap(logging.Handler):
    """Предупреждения и ошибки модулей (logging) — счётчик по логгеру и шаблону сообщения."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.counts = Counter()
        self.samples = {}

    def emit(self, record):
        key = (record.levelname, record.name, str(record.msg)[:80])
        self.counts[key] += 1
        if key not in self.samples:
            try:
                self.samples[key] = record.getMessage()[:300]
            except Exception:
                self.samples[key] = str(record.msg)[:300]


TAGS = re.compile(r"\s*\[[a-z]{2,12}:[^\]]*\]")


def human(text):
    """Реплика без служебных меток протокола ([party:town:], [chat:hello:1]): её видит и читает человек."""
    return TAGS.sub("", text or "").strip()


def db_size(db):
    return db.execute("PRAGMA page_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0]


def kv_stats(db):
    rows = db.execute("SELECT key, length(value) FROM kv").fetchall()
    return len(rows), sum(r[1] for r in rows), sorted(((r[1], r[0]) for r in rows), reverse=True)[:5]


def file_size(p):
    return sum(os.path.getsize(x) for x in (str(p), f"{p}-wal") if os.path.exists(x))


class Day:
    """Метрики одного жителя за игровые сутки."""

    def __init__(self):
        self.tick_ms = []
        self.modes = Counter()                      # режим распорядка -> секунд
        self.max_streak = (0, None)                 # (секунд, режим) — самый долгий непрерывный режим
        self.deaths = 0


class Soak:
    def __init__(self, days=7, residents=4, seed=1, budget=18.0, dt=None, out=None, start=None, quiet=False):
        self.days = days
        self.specs = RESIDENTS[:residents]
        self.seed = seed
        self.budget = budget * 60
        self.fixed_dt = dt
        self.dt = dt or 2
        self.quiet = quiet
        self.tmp = None if out else tempfile.TemporaryDirectory(prefix="soak-")
        self.root = Path(out or self.tmp.name)
        self.root.mkdir(parents=True, exist_ok=True)
        self.t0 = (start or datetime(2025, 3, 3, 8, 0, tzinfo=TZ)).timestamp()
        self.clock = Clock()
        self.clock.t = self.t0
        self.trap = LogTrap()
        self.errors = Counter()                     # (житель, где, тип, строка) -> число
        self.error_samples = {}
        self.rows = []                              # [(день, житель, {метрика: значение})]
        self.world_rows = []                        # [(день, {метрика})]
        self.findings = []
        self.dts = []
        self.mod_ms = {}                            # житель -> Counter(модуль -> мс за текущие сутки)

    # ---------- построение ----------

    def build(self):
        names = {s["name"] for s in self.specs}
        self.world = World(self.specs, self.t0, self.seed)
        self.minds, self.mems, self.buses = {}, {}, {}
        for i, spec in enumerate(self.specs):
            name = spec["name"]
            d = self.root / spec["bot"]
            d.mkdir(exist_ok=True)
            persona = json.loads(Path(spec["persona"]).read_text(encoding="utf-8"))
            persona.setdefault("sex", spec["body"]["sex"])
            persona.setdefault("job", spec["body"]["job"])
            mem = Memory(d / "memory.sqlite")
            bus = WorldBus(self.root / "world.sqlite", name)

            def make_send(n):
                async def send(a):
                    self.seq += 1
                    self.world.apply(n, dict(a, id=self.seq))
                    return self.seq
                return send
            mind = Mind(Settings.from_env({}), persona, mem, make_send(name), d / "decisions.jsonl", RuleGate(),
                        peers=names, world=WORLD, world_bus_db=bus)
            for attr in ("routine", "activities"):
                m = getattr(mind, attr, None)
                if m is not None and hasattr(m, "rng"):
                    m.rng = random.Random(self.seed * 10 + i)
            orig = mind.write_decision                        # decisions.jsonl: ts — часы стены, game — часы мира

            def write(record, _orig=orig, _clock=self.clock):
                return _orig(dict(record, game=round(_clock.t - self.t0)))
            mind.write_decision = write
            self.minds[name], self.mems[name], self.buses[name] = mind, mem, bus
            self.wrap_modules(name, mind)
        self.seq = 0
        for name in self.minds:
            self.world.send(name, 0, type="hello", char=name)
            self.world.event(name, 0, kind="in_game")

    def wrap_modules(self, name, mind):
        """Время tick каждого модуля реестра за сутки (рост — искать здесь)."""
        acc = self.mod_ms.setdefault(name, Counter())
        for attr in mind.registry.ticks:
            module = getattr(mind, attr, None)
            if module is None:
                continue
            orig = module.tick

            def tick(*a, _orig=orig, _attr=attr, **kw):
                t = time.perf_counter()
                r = _orig(*a, **kw)
                if asyncio.iscoroutine(r):
                    return self._timed(r, _attr, t, acc)
                acc[_attr] += (time.perf_counter() - t) * 1000
                return r
            module.tick = tick

    @staticmethod
    async def _timed(coro, attr, t, acc):
        try:
            return await coro
        finally:
            acc[attr] += (time.perf_counter() - t) * 1000

    # ---------- прогон ----------

    async def guarded(self, name, where, coro):
        try:
            await coro
        except Exception as e:                                    # исключение модуля — находка, мир живёт дальше
            tb = traceback.extract_tb(e.__traceback__)
            frame = next((f for f in reversed(tb) if "live_brain" in f.filename), tb[-1])
            key = (name, where, type(e).__name__, f"{Path(frame.filename).name}:{frame.lineno}")
            self.errors[key] += 1
            self.error_samples.setdefault(key, f"{e!r} — {frame.line}")

    async def loop(self):
        end = self.t0 + self.days * 86400
        day_no = 0
        day = {n: Day() for n in self.minds}
        streak = {n: [None, self.t0] for n in self.minds}
        wall0 = time.perf_counter()
        hour_wall, hour_t = wall0, self.clock.t
        next_hour = self.t0 + 3600
        while self.clock.t < end:
            t = self.clock.t
            self.world.tick(t, self.dt)
            for name, mind in self.minds.items():
                body = self.world.bodies[name]
                msgs = self.world.take(name, t)
                for m in msgs:
                    await self.guarded(name, "on_message", mind.on_message(m))
                if not body.online or not mind.state:
                    mode = "offline"
                else:
                    t1 = time.perf_counter()
                    await self.guarded(name, "step", mind.step())
                    day[name].tick_ms.append((time.perf_counter() - t1) * 1000)
                    mode = (mind.routine.st.get("mode") if mind.routine else None) or "?"
                dd = day[name]
                dd.modes[mode] += self.dt
                if streak[name][0] != mode:
                    streak[name] = [mode, t]
                run = t - streak[name][1]
                if run > dd.max_streak[0]:
                    dd.max_streak = (run, mode)
            self.clock.t += self.dt
            if self.clock.t >= next_hour:
                next_hour += 3600
                self.calibrate(hour_wall, hour_t, end)
                hour_wall, hour_t = time.perf_counter(), self.clock.t
            if self.clock.t >= self.t0 + (day_no + 1) * 86400:
                self.close_day(day_no, day)
                day_no += 1
                day = {n: Day() for n in self.minds}
                if not self.quiet:
                    print(f"  сутки {day_no}/{self.days}: {time.perf_counter() - wall0:.0f} с, dt {self.dt}",
                          file=sys.stderr, flush=True)
        if any(d.tick_ms for d in day.values()) and self.clock.t > self.t0 + day_no * 86400 + 3600:
            self.close_day(day_no, day)
        self.wall = time.perf_counter() - wall0

    def calibrate(self, hour_wall, hour_t, end):
        """dt по фактической скорости последнего часа: остаток игрового времени — в остаток бюджета."""
        self.dts.append(self.dt)
        if self.fixed_dt:
            return
        spent = time.perf_counter() - self.started
        left = max(60.0, self.budget - spent)
        # с стены на игровую секунду при dt=1 (цена такта почти не зависит от dt)
        per_game_s = (time.perf_counter() - hour_wall) / max(1.0, self.clock.t - hour_t) * self.dt
        need = per_game_s * (end - self.clock.t) * 1.15
        self.dt = max(1, min(MAX_DT, math.ceil(need / left)))

    # ---------- метрики дня ----------

    def close_day(self, n, day):
        start, stop = self.t0 + n * 86400, self.t0 + (n + 1) * 86400
        w = self.world
        for name, mind in self.minds.items():
            mem = self.mems[name]
            dd = day[name]
            ms = sorted(dd.tick_ms) or [0]
            kv_n, kv_b, kv_top = kv_stats(mem.db)
            ev_n = mem.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            ev_day = mem.db.execute("SELECT COUNT(*) FROM events WHERE ts >= ? AND ts < ?", (start, stop)).fetchone()[0]
            top_kinds = mem.db.execute("SELECT kind, COUNT(*) c FROM events WHERE ts >= ? AND ts < ? GROUP BY kind "
                                       "ORDER BY c DESC LIMIT 3", (start, stop)).fetchall()
            memories = mem.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            rels = dict(mem.db.execute("SELECT name, affinity FROM relations").fetchall())
            peer_aff = {p: rels.get(p) for p in self.minds if p != name}
            lines = [(t, k, x) for t, k, x in w.lines[name] if start <= t < stop]
            per_hour = Counter(int((t - start) // 3600) for t, k, x in lines if k == "whisper" and human(x))
            say_hour = Counter(int((t - start) // 3600) for t, k, _ in lines if k in ("say", "party_say"))
            texts = Counter(human(x) for _, k, x in lines if k in ("say", "whisper", "party_say") and human(x))
            protocol = sum(1 for _, k, x in lines if k == "whisper" and not human(x))
            spoken = sum(texts.values())
            repeats = spoken - len(texts)
            sent = [x for x in w.action_log[name] if start <= x[0] < stop]
            calls = mem.db.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
            bad = replay.invariants(sent, llm_calls=calls)
            deaths = [x for x in w.bodies[name].deaths if start <= x < stop]
            loops = sum(1 for i in range(len(deaths) - 2) if deaths[i + 2] - deaths[i] < 2 * 3600)
            dst = mem.get("dream") or {}
            dream = dst.get("kind")
            dream_key = (dream, dst.get("stage"), len(dst.get("history") or []))
            aims = mem.get("aims") or {}
            aim_ids = [(a.get("kind"), bool(a.get("done"))) for a in (aims.get("items") or []) if isinstance(a, dict)]
            body = w.bodies[name]
            steps = len(dd.tick_ms) or 1
            mods = self.mod_ms.get(name, Counter())
            row = {
                "mod_ms": {k: round(v / steps, 4) for k, v in mods.most_common(8)},
                "mem_kb": db_size(mem.db) // 1024, "wal_kb": file_size(mem.path) // 1024,
                "events": ev_n, "events_day": ev_day,
                "top_kinds": ",".join(f"{k}:{c}" for k, c in top_kinds), "kv": kv_n, "kv_kb": kv_b // 1024,
                "kv_top": ",".join(f"{k}:{b // 1024}k" for b, k in kv_top[:3]), "memories": memories,
                "relations": len(rels), "aff": peer_aff,
                "decisions_kb": file_size(self.root / self._bot(name) / "decisions.jsonl") // 1024,
                "tick_mean": sum(ms) / len(ms), "tick_p95": ms[int(0.95 * (len(ms) - 1))], "tick_max": ms[-1],
                "whispers": sum(per_hour.values()), "whisper_max_h": max(per_hour.values() or [0]),
                "says": sum(say_hour.values()), "say_max_h": max(say_hour.values() or [0]),
                "spoken": spoken, "protocol": protocol, "repeat_pct": round(100 * repeats / spoken) if spoken else 0,
                "top_line": texts.most_common(1)[0] if texts else None,
                "modes": {k: round(v / 3600, 1) for k, v in dd.modes.items()},
                "streak_h": round(dd.max_streak[0] / 3600, 1), "streak_mode": dd.max_streak[1],
                "zeny": body.s["zeny"], "bank": body.bank, "lv": body.s["lv"], "kills": body.kills,
                "deaths": len(deaths), "death_loops": loops, "dream": dream, "dream_key": dream_key, "aims": aim_ids,
                "invariants": bad[:5], "invariants_n": len(bad), "actions": len(sent),
                "acts": Counter(a.get("action") for _, a, _ in sent).most_common(4),
                "sent_backlog": len(mind.sent), "reasons": len(mind.reasons),
            }
            self.rows.append((n + 1, name, row))
            mods.clear()
        bus = self.buses[next(iter(self.buses))].db
        self.world_rows.append((n + 1, {
            "world_kb": db_size(bus) // 1024,
            "world_events": bus.execute("SELECT COUNT(*) FROM world_events").fetchone()[0],
            "zeny_total": w.zeny_total(), "zeny_expected": w.zeny_expected(),
            "minted": dict(w.ledger.minted), "burned": dict(w.ledger.burned),
            "inbox_backlog": sum(len(b.inbox) for b in w.bodies.values()), "trades_open": len(w.trades),
            "mail_waiting": sum(len(v) for v in w.mail.values()),
        }))

    def _bot(self, name):
        return next(s["bot"] for s in self.specs if s["name"] == name)

    # ---------- выводы ----------

    STUCK_HOURS = {"hunt": 4, "town": 15, "offline": 12, "sleep": 12}     # дольше подряд — «застрял»

    def analyze(self, whisper_cap=30, say_cap=30, repeat_cap=60):
        f = self.findings
        by = defaultdict(list)
        for d, name, r in self.rows:
            by[name].append((d, r))
        for name, rows in by.items():
            for d, r in rows:
                if r["invariants_n"]:
                    f.append(f"{name} сутки {d}: replay.invariants — {r['invariants_n']}: {r['invariants'][:2]}")
                if r["whisper_max_h"] > whisper_cap:
                    f.append(f"{name} сутки {d}: {r['whisper_max_h']} шёпотов за час (порог {whisper_cap})")
                if r["say_max_h"] > say_cap:
                    f.append(f"{name} сутки {d}: {r['say_max_h']} реплик в чат за час (порог {say_cap})")
                if r["spoken"] >= 20 and r["repeat_pct"] > repeat_cap:
                    f.append(f"{name} сутки {d}: повторы реплик {r['repeat_pct']} % (порог {repeat_cap}), "
                             f"чаще всего {r['top_line']}")
                if r["streak_h"] > self.STUCK_HOURS.get(r["streak_mode"], 4):
                    f.append(f"{name} сутки {d}: {r['streak_h']} ч подряд в режиме {r['streak_mode']}")
                if r["death_loops"]:
                    f.append(f"{name} сутки {d}: смерти в цикле (3 за 2 ч) — {r['deaths']} смертей за сутки")
                if r["sent_backlog"] > 200:
                    f.append(f"{name} сутки {d}: mind.sent копится — {r['sent_backlog']} действий без ack")
                vals = [v for v in r["aff"].values() if v is not None]
                if len(vals) >= 2 and all(abs(v) >= 10 for v in vals):
                    f.append(f"{name} сутки {d}: все отношения в ±максимуме {r['aff']}")
            if len(rows) >= 4:
                first, last = rows[1][1], rows[-1][1]
                days = rows[-1][0] - rows[1][0]
                if last["tick_mean"] > 2 * max(0.3, first["tick_mean"]):
                    f.append(f"{name}: время тика растёт {first['tick_mean']:.2f} -> {last['tick_mean']:.2f} мс")
                if last["kv_kb"] > first["kv_kb"] * 1.5 + 64:
                    f.append(f"{name}: kv растёт {first['kv_kb']} -> {last['kv_kb']} КБ за {days} сут "
                             f"({last['kv_top']})")
                if last["kv"] > first["kv"] + 40 * days:
                    f.append(f"{name}: число ключей kv растёт {first['kv']} -> {last['kv']}")
                dreams = {tuple(r["dream_key"]) for _, r in rows}
                aims = {tuple(map(tuple, r["aims"])) for _, r in rows}
                if len(rows) >= 7 and len(dreams) <= 1 and len(aims) <= 1:
                    f.append(f"{name}: мечта и недельные цели не двигались {len(rows)} сут ({dreams})")
        for d, r in self.world_rows:
            if r["zeny_total"] != r["zeny_expected"]:
                f.append(f"мир сутки {d}: зени {r['zeny_total']} != ожидаемых {r['zeny_expected']} "
                         f"(разница {r['zeny_total'] - r['zeny_expected']})")
            if r["inbox_backlog"] > 500:
                f.append(f"мир сутки {d}: в очередях тел {r['inbox_backlog']} сообщений")
        for (name, where, typ, at), n in self.errors.items():
            f.append(f"{name}: исключение {typ} в {where} ({at}) ×{n}: {self.error_samples[(name, where, typ, at)]}")
        for (lvl, logger, msg), n in self.trap.counts.items():
            if lvl in ("ERROR", "CRITICAL"):
                f.append(f"лог {lvl} {logger}: ×{n} {self.trap.samples[(lvl, logger, msg)]}")
        return f

    def table(self, markdown=False):
        out = []
        sep = "|" if markdown else " "
        head = ["сут", "житель", "lv", "mem КБ", "событий", "kv", "kv КБ", "тик мс", "p95", "шёп/сут", "шёп/ч max*",
                "чат/сут", "метки/сут", "повт %", "дольше всего", "зени+банк", "смертей", "мечта", "отношения"]
        if markdown:
            out.append("| " + " | ".join(head) + " |")
            out.append("|" + "---|" * len(head))
        else:
            out.append(" ".join(f"{h:>9}" for h in head))
        for d, name, r in self.rows:
            aff = ",".join(f"{k[0]}{v:+d}" for k, v in r["aff"].items() if v is not None) or "—"
            cells = [d, name, r["lv"], r["mem_kb"], r["events"], r["kv"], r["kv_kb"], f"{r['tick_mean']:.2f}",
                     f"{r['tick_p95']:.2f}", r["whispers"], r["whisper_max_h"],
                     r["says"], r["protocol"], r["repeat_pct"],
                     f"{r['streak_mode']} {r['streak_h']}ч", r["zeny"] + r["bank"], r["deaths"], r["dream"] or "—", aff]
            out.append(("| " + " | ".join(map(str, cells)) + " |") if markdown
                       else " ".join(f"{str(c):>9}" for c in cells))
        out.append("")
        whead = ["сут", "world КБ", "событий шины", "зени мира", "ожидалось", "продано NPC", "зелья", "почта"]
        if markdown:
            out.append("| " + " | ".join(whead) + " |")
            out.append("|" + "---|" * len(whead))
        for d, r in self.world_rows:
            cells = [d, r["world_kb"], r["world_events"], r["zeny_total"], r["zeny_expected"],
                     r["minted"].get("npc_sold", 0), r["burned"].get("potions", 0), r["burned"].get("mail_fee", 0)]
            out.append(("| " + " | ".join(map(str, cells)) + " |") if markdown
                       else " ".join(f"{str(c):>12}" for c in cells))
        return "\n".join(out)

    def run(self):
        self.started = time.perf_counter()
        root_log = logging.getLogger()
        old_level = root_log.level
        root_log.setLevel(logging.WARNING)
        root_log.addHandler(self.trap)
        quiet = [h for h in root_log.handlers if h is not self.trap]
        for h in quiet:
            root_log.removeHandler(h)
        try:
            with mock.patch("time.time", self.clock):
                self.build()
                asyncio.run(self.loop())
                for m in self.mems.values():
                    m.close()
                for b in self.buses.values():
                    b.close()
        finally:
            root_log.removeHandler(self.trap)
            for h in quiet:
                root_log.addHandler(h)
            root_log.setLevel(old_level)
        self.analyze()
        return self

    def cleanup(self):
        if self.tmp:
            self.tmp.cleanup()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--residents", type=int, default=4, choices=(2, 3, 4))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--budget", type=float, default=18, help="минут стены на весь прогон (калибровка dt)")
    ap.add_argument("--dt", type=int, help="игровых секунд на такт (без калибровки)")
    ap.add_argument("--out", help="каталог для памяти жителей (по умолчанию временный)")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--json", help="записать строки метрик в JSON-файл")
    args = ap.parse_args()
    s = Soak(days=args.days, residents=args.residents, seed=args.seed, budget=args.budget, dt=args.dt, out=args.out)
    s.run()
    print(f"soak: {args.days} сут, жителей {len(s.specs)}, стена {s.wall / 60:.1f} мин, dt {sorted(set(s.dts))}")
    print(s.table(markdown=args.markdown))
    print()
    warn = Counter()
    for (lvl, logger, msg), n in s.trap.counts.items():
        warn[f"{lvl} {logger}: {s.trap.samples[(lvl, logger, msg)][:140]}"] += n
    if warn:
        print("Предупреждения логов (топ-15):")
        for k, n in warn.most_common(15):
            print(f"  ×{n} {k}")
        print()
    print("Находки:" if s.findings else "Находок нет.")
    for x in s.findings:
        print("  -", x)
    if args.json:
        Path(args.json).write_text(json.dumps({"rows": s.rows, "world": s.world_rows, "findings": s.findings,
                                               "dts": s.dts}, ensure_ascii=False, default=str, indent=1))
    s.cleanup()
    return 1 if s.findings else 0


if __name__ == "__main__":
    sys.exit(main())
