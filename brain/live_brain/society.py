"""Социальная ткань: эмоции на события, чат-комнаты как вывески, ссоры и примирения (ORG-022, 026, 027).

Правила без LLM, тик 1 с. Источник фактов — события памяти жителя (таблица events), которые уже пишут
другие модули по данным игры (экономика, планы, группа, социальная жизнь) и тело (chat_private,
party_refused). Модуль читает их по возрастанию id (st.last_id), ничего не придумывает и не трогает
чужие модули: решение — в decisions.jsonl (type society), память — remember/add_event (хроника).
Конфигурация — раздел "society" brain/world/goals.json (необязателен), значения по умолчанию — DEFAULTS.

Эмоции (ORG-022): действие emote (только номера из safety.EMOTES) на события:
    meeting_confirmed — wav (12); trade_sold/trade_bought — heh/ok (18/33); gift_received — thx (15);
    gift_given — ok (33); гибель жителя рядом (party_member_dead + он виден) — sob (28);
    ссора — «...» (9); примирение — sry (17). Поздравление и приветствие при разговоре уже делает
    social.py (congrats — no1, hello — wav), здесь не дублируется.
    Эмоцию видно только рядом: житель — в state.players (или участник группы) не дальше emote_cells.
    Лимиты: общий промежуток emote_gap_seconds вместе с social.py (social.last_emote), одна и та же эмоция
    одному жителю — не чаще emote_same_minutes; ночью — только sob/thx/sry; safety.EMOTE_LIMIT/10 мин сверху.
    Поза «посидеть вместе» — bonds.py: тело остаётся у точки встречи и садится само (sitAuto_idle, без
    команды sit — она ставит sitAuto_forcedBySitCommand и блокирует AI, ORG-001).

Чат-комнаты как вывески (ORG-026): в городе (режим town, дошёл до отдыха, карта города, день) раз в
    room_every_minutes с шансом room_chance житель открывает комнату (chat_room open → OpenKore
    `chat create "<title>" <limit> 1`) с заголовком по занятию/желанию: «Продаю <лот>» (economy.for_sale),
    «Ищу группу на <карта>» (нет группы), «Куплю <предмет>» (economy.wishlist), иначе «Отдыхаю».
    Заголовок ≤ 36 символов и ≤ 36 байт UTF-8 (rAthena CHATROOM_TITLE_SIZE 36+1, serverEncoding UTF-8), без '#' и '"'.
    Открыта — только по данным тела (state.chat_room = заголовок комнаты, где я сейчас). Нет за ROOM_CONFIRM с —
    не открылась (например, рядом NPC: rAthena npc_isnear) — запись society_room_failed и пауза.
    Закрыть (chat_room close → `chat leave`): срок вышел, отдых кончается (room_min_rest_minutes), режим не town,
    план встречи, арбитр не даёт тело, лавка, вес ≥ 48 %, скоро прогулка, ночь. В комнате сервер не даёт
    ходить и атаковать (pc_cant_act: chatID), поэтому мост сам делает `chat leave` перед любым действием,
    двигающим тело, и когда AI OpenKore начинает route/move/attack/... (brainBridge chatGuard).

Ссоры и примирения (ORG-027), только по фактам памяти:
    отношение −1 (не больше drops_per_day раз в сутки на жителя): отказ на мою просьбу [need:..:no]
    (кроме «занят»: мёртв, иду на встречу, уже передаю, стою с лавкой); сорванная встреча — житель согласился,
    а потом не пришёл или отменил (plan_failed: «не дождался», «отказался или отменил» после согласия);
    отказ вступить в группу (party_refused — пакет сервера party_invite_result). Недоплата trade_debt уже
    даёт −1 в economy.py — здесь только проверка порога.
    affinity ≤ quarrel_at (−2) после такого события — «в ссоре»: party.py не зовёт в группу (HOSTILE −3 —
    уже без ссоры), social.py не заговаривает первым, не идёт к нему гулять, отвечает холодно и коротко
    (тема cold, шаг 4 — без продолжения).
    Примирение: остыл (cool_days после последнего конфликта) И был повод после ссоры — подарок/лечение/сделка
    от него или мне, его извинение [chat:sorry:4]; или forget_days без конфликтов и встреча рядом.
    Тогда шаг к нулю (до +2), реплика-извинение [chat:sorry:4] (обычный чат — лимиты safety), эмоция sry.
    LLM только окрашивает: отношение и «в_ссоре» видны в промпте; правила работают при BRAIN_LLM=off.
"""
import json
import logging
import random
import re
import time

log = logging.getLogger("society")

DEFAULTS = {
    "enabled": True,
    "emote_gap_seconds": 120,
    "emote_same_minutes": 10,
    "emote_cells": 14,
    "room_every_minutes": [40, 90],
    "room_chance": 0.5,
    "room_minutes": [10, 25],
    "room_limit": 5,
    "room_min_rest_minutes": 8,
    "room_sign_chance": 0.7,           # вывеска о деле (продаю/ищу/куплю), иначе «Отдыхаю»
    "quarrel_at": -2,
    "drops_per_day": 2,
    "cool_days": 2,
    "forget_days": 7,
}
# событие -> номер эмоции (safety.EMOTES / tables/emotions.txt)
EMOTE_ON = {"meeting": 12, "trade_sold": 18, "trade_bought": 33, "gift_received": 15, "gift_given": 33,
            "friend_dead": 28, "quarrel": 9, "reconciled": 17}
NIGHT_OK = ("friend_dead", "gift_received", "reconciled")
JOYFUL = ("meeting", "trade_sold", "gift_received", "reconciled")    # talk: ORG-064 шанс от настроения
WATCH = ("meeting_confirmed", "trade_sold", "trade_bought", "trade_debt", "gift_received", "gift_given",
         "party_member_dead", "plan_failed", "chat_private", "party_refused", "heal_confirmed", "heal_given")
NEED_NO = re.compile(r"\[need:[a-z0-9]{4,8}:no\]")
NEED_WHY = re.compile(r"не могу:\s*(.+?)\.\s*\[need:")
SORRY_TAG = re.compile(r"\[chat:sorry:\d\]")
BUSY = ("я мёртв", "иду на встречу", "уже передаю", "стою с лавкой", "ты не житель")
ROOM_CONFIRM = 60              # комната должна появиться в state за это время
ROOM_FAIL_BACKOFF = 1800
TITLE_MAX = 36                 # символов и байт UTF-8 (rAthena CHATROOM_TITLE_SIZE = 36 + 1)
MOVING_ACTIVITIES = ("stroll", "socialize", "service", "hunt_early")
COLD = ["Угу.", "Ладно.", "Некогда.", "Ну-ну.", "Хм. Потом.", "Как скажешь."]
SORRY = ["{name}, прости за прошлое. Мир?", "Не держи зла, {name}. Погорячился.",
         "{name}, давай забудем ссору.", "Был неправ, {name}. Мир?"]


def dist(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


def fit_title(text, limit=TITLE_MAX):
    """Заголовок комнаты: без '#'/'"'/управляющих, ≤ limit символов и ≤ limit байт UTF-8, режем по слову."""
    text = " ".join("".join(c for c in str(text) if c.isprintable() and c not in '#"').split())
    if len(text) <= limit and len(text.encode("utf-8")) <= limit:
        return text
    out = ""
    for word in text.split(" "):
        cand = f"{out} {word}".strip()
        if len(cand) > limit or len(cand.encode("utf-8")) > limit:
            break
        out = cand
    if not out:                                   # одно длинное слово — посимвольно
        for c in text:
            if len((out + c).encode("utf-8")) > limit or len(out) + 1 > limit:
                break
            out += c
    return out


class Society:
    def __init__(self, mind, world=None, clock=None, rng=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("society") or {}))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.st = mind.mem.get("society") or {}
        for key in ("quarrel", "drops", "emoted"):
            self.st.setdefault(key, {})
        if self.st.get("last_id") is None:           # история до запуска модуля — не повод для эмоций
            row = mind.mem.db.execute("SELECT MAX(id) FROM events").fetchone()
            self.st["last_id"] = (row[0] if row else None) or 0
        self.room = self.st.get("room")              # открытая (или открываемая) комната: title, since, until, confirmed
        self.next_room = None
        self.last_emote = 0.0

    # ---------- данные ----------

    @property
    def me(self):
        return self.mind.state.get("name") or self.mind.persona["name"]

    def save(self):
        self.st["room"] = self.room
        self.mind.mem.set("society", self.st)

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "society", "event": kind, "text": text, **data})
        log.info("%s", text)

    def affinity(self, peer):
        return (self.mind.mem.relation(peer) or {}).get("affinity", 0)

    def quarrel(self, peer):
        """В ссоре ли я с жителем (для party, social, activity)."""
        return peer in self.st["quarrel"]

    def summary(self):
        return {p: {"причина": q.get("cause"), "с": q.get("since")} for p, q in self.st["quarrel"].items()} or None

    def social(self):
        return getattr(self.mind, "social", None)

    def is_night(self, now):
        s = self.social()
        return bool(s and s.is_night(now))

    def near(self, peer, cells=None):
        """Житель виден рядом (или участник группы на той же карте не дальше cells)."""
        cells = cells or self.cfg["emote_cells"]
        state = self.mind.state
        if state.get("x") is None:
            return False
        for p in list(state.get("players") or []) + [m for m in state.get("party_members") or []
                                                      if isinstance(m, dict) and m.get("map") == state.get("map")]:
            if (isinstance(p, dict) and p.get("name") == peer and p.get("x") is not None
                    and dist(int(p["x"]), int(p["y"]), int(state["x"]), int(state["y"])) <= cells):
                return True
        return False

    def online(self, peer):
        return self.near(peer, 30) or any(isinstance(f, dict) and f.get("name") == peer and f.get("online")
                                          for f in self.mind.state.get("friends") or [])

    # ---------- тик ----------

    async def tick(self):
        now = self.clock()
        state = self.mind.state
        if not self.mind.fresh_state:
            return
        await self.read_events(now)
        await self.rooms(now, state)
        if not state.get("dead"):
            await self.reconcile_due(now)

    async def read_events(self, now):
        rows = self.mind.mem.db.execute(
            f"SELECT id, kind, data FROM events WHERE id > ? AND kind IN ({', '.join('?' * len(WATCH))}) "
            "ORDER BY id LIMIT 200", (self.st["last_id"], *WATCH)).fetchall()
        if not rows:
            return
        for rid, kind, data in rows:
            self.st["last_id"] = rid
            try:
                d = json.loads(data)
            except (TypeError, ValueError):
                continue
            await self.on_fact(kind, d, now)
        self.save()

    async def on_fact(self, kind, d, now):
        peers = self.mind.ctx.peers
        if kind == "meeting_confirmed" and d.get("partner") in peers:
            await self.emote(d["partner"], "meeting", now)
            self.occasion(d["partner"], "встретились", now)
        elif kind in ("trade_sold", "trade_bought", "gift_received", "gift_given") and d.get("peer") in peers:
            await self.emote(d["peer"], kind, now)
            self.occasion(d["peer"], {"trade_sold": "сторговались", "trade_bought": "сторговались",
                                      "gift_received": "подарок от него", "gift_given": "мой подарок"}[kind], now)
        elif kind == "heal_confirmed" and d.get("from") in peers and d.get("to") == self.me:
            self.occasion(d["from"], "вылечил меня в бою", now)
        elif kind == "heal_given" and d.get("to") in peers:
            self.occasion(d["to"], "я его вылечил", now)
        elif kind == "party_member_dead" and d.get("who") in peers:
            if self.near(d["who"]):
                await self.emote(d["who"], "friend_dead", now)
        elif kind == "trade_debt" and d.get("peer") in peers:
            self.count_drop(d["peer"], now)                  # −1 уже поставил economy.py
            await self.check_quarrel(d["peer"], "недоплатил за товар", now)
        elif kind == "plan_failed" and d.get("partner") in peers:
            why = self.meeting_fault(d)
            if why:
                await self.drop(d["partner"], why, now)
        elif kind == "party_refused" and d.get("name") in peers:
            await self.drop(d["name"], "отказался вступить в группу", now)
        elif kind == "chat_private" and d.get("from") in peers:
            text = str(d.get("text") or "")
            if NEED_NO.search(text) and self.asked_recently(d["from"], now):
                m = NEED_WHY.search(text)
                why = m.group(1).strip() if m else ""
                if not any(b in why for b in BUSY):
                    await self.drop(d["from"], "отказал в просьбе" + (f" ({why[:40]})" if why else ""), now)
            elif SORRY_TAG.search(text):
                self.occasion(d["from"], "извинился", now)

    def asked_recently(self, peer, now):
        """Была моя просьба этому жителю за последние 10 мин, ещё без ответа: отказ [need:..:no] — на неё.
        Одна просьба — один отказ (st.ask_seen — id уже отвеченной просьбы)."""
        rows = self.mind.mem.db.execute("SELECT id, data FROM events WHERE kind = 'gift_asked' AND ts >= ? AND id > ? "
                                        "ORDER BY id", (time.time() - 600, self.st.get("ask_seen", 0))).fetchall()
        for rid, data in rows:
            if json.loads(data).get("peer") == peer:
                self.st["ask_seen"] = rid
                return True
        return False

    def meeting_fault(self, d):
        """Сорванная встреча по вине жителя: согласился, а потом не пришёл или отменил. Иначе None."""
        plans = getattr(self.mind, "plans", None)
        plan = plans.store.get(d.get("plan")) if plans and d.get("plan") else None
        if not plan:
            return None
        result = str(plan.get("result") or "")
        notes = " ".join(str(h[1]) for h in json.loads(plan.get("history") or "[]") if len(h) > 1)
        agreed = "согласился" in notes or "принято" in notes
        if "не дождался" in result:
            return "не пришёл на встречу"
        if "отказался или отменил" in result and agreed:
            return "отменил встречу после согласия"
        return None

    # ---------- эмоции (ORG-022) ----------

    async def emote(self, peer, on, now):
        eid = EMOTE_ON.get(on)
        if eid is None or not self.near(peer):
            return False
        mood = getattr(self.mind, "mood", None)                                     # talk: ORG-064
        chance = mood.emote_chance() if mood and on in JOYFUL else 1.0               # talk: хмурый радуется реже
        if chance < 1 and self.rng.random() >= chance:                              # talk:
            return False                                                            # talk:
        if self.is_night(now) and on not in NIGHT_OK:
            return False
        social = self.social()
        last = max(self.last_emote, getattr(social, "last_emote", 0) or 0)
        if now - last < self.cfg["emote_gap_seconds"]:
            return False
        key = f"{eid}:{peer}"
        if now - self.st["emoted"].get(key, 0) < self.cfg["emote_same_minutes"] * 60:
            return False
        self.last_emote = now
        if social is not None:
            social.last_emote = now                     # общий промежуток с social.py
        self.st["emoted"] = {k: t for k, t in self.st["emoted"].items() if now - t < 3600}
        self.st["emoted"][key] = now
        self.mind.mem.add_event("society_emote", {"peer": peer, "on": on, "id": eid})
        await self.mind.execute([{"action": "emote", "id": eid}], source="society",
                                reason=f"общество: эмоция на {on} ({peer})", protocol=True)
        return True

    # ---------- ссоры и примирения (ORG-027) ----------

    def drops_today(self, peer, now):
        day = int(now // 86400)
        rec = self.st["drops"].get(peer) or {}
        return rec.get("n", 0) if rec.get("day") == day else 0

    def count_drop(self, peer, now):
        day = int(now // 86400)
        self.st["drops"][peer] = {"day": day, "n": self.drops_today(peer, now) + 1}

    async def drop(self, peer, cause, now):
        if self.drops_today(peer, now) >= self.cfg["drops_per_day"]:
            self.mind.write_decision({"type": "society", "event": "drop_capped", "peer": peer, "cause": cause})
            return
        self.count_drop(peer, now)
        self.mind.mem.update_relation(peer, -1, cause)
        self.note("society_relation_drop", f"{peer}: {cause} — отношение −1 (стало {self.affinity(peer)}).", 2,
                  peer=peer, cause=cause, affinity=self.affinity(peer))
        await self.check_quarrel(peer, cause, now)

    async def check_quarrel(self, peer, cause, now):
        aff = self.affinity(peer)
        q = self.st["quarrel"].get(peer)
        if q:
            q["last"] = now                              # новый конфликт — остывание заново, старый повод не в счёт
            q.pop("occasion", None)
            self.save()
            return
        if aff > self.cfg["quarrel_at"]:
            return
        self.st["quarrel"][peer] = {"since": now, "last": now, "cause": cause}
        self.save()
        self.note("society_quarrel", f"Поссорился с {peer}: {cause}. Пока не зову и не заговариваю первым.", 3,
                  peer=peer, cause=cause, affinity=aff)
        await self.emote(peer, "quarrel", now)

    def occasion(self, peer, what, now):
        """Повод помириться (после последнего конфликта)."""
        q = self.st["quarrel"].get(peer)
        if q and now >= q.get("last", 0):
            q["occasion"] = what
            self.mind.write_decision({"type": "society", "event": "occasion", "peer": peer, "what": what})

    async def reconcile_due(self, now):
        cool, forget = self.cfg["cool_days"] * 86400, self.cfg["forget_days"] * 86400
        for peer in sorted(self.st["quarrel"]):
            q = self.st["quarrel"][peer]
            quiet = now - q.get("last", now)
            if quiet >= forget and self.near(peer) and not q.get("occasion"):
                q["occasion"] = "давно не ссорились, встретились"
            if quiet >= cool and q.get("occasion") and self.online(peer):
                await self.reconcile(peer, q, now)

    async def reconcile(self, peer, q, now):
        aff = self.affinity(peer)
        step = min(2, -aff) if aff < 0 else 0
        self.mind.mem.update_relation(peer, step, f"помирились: {q['occasion']}")
        del self.st["quarrel"][peer]
        self.save()
        self.note("society_reconciled", f"Помирился с {peer} ({q['occasion']}); отношение {aff} → {aff + step}.", 3,
                  peer=peer, occasion=q["occasion"], affinity=aff + step, days=round((now - q["since"]) / 86400, 1))
        text = self.phrase("sorry", SORRY, peer)
        await self.mind.execute([{"action": "whisper", "to": peer, "text": f"{text} [chat:sorry:4]"}],
                                source="society", reason=f"общество: извинение {peer}")
        await self.emote(peer, "reconciled", now)

    def phrase(self, key, default, peer):
        options = (self.mind.persona.get("phrases") or {}).get(key) or default
        try:
            return self.rng.choice(options).format(name=peer, me=self.me)
        except (KeyError, IndexError, ValueError):
            return self.rng.choice(default).format(name=peer)

    def cold_phrase(self, peer):
        """Холодная короткая реплика жителю, с которым в ссоре (social.say)."""
        return self.phrase("cold", COLD, peer)

    # ---------- чат-комнаты (ORG-026) ----------

    def town_map(self):
        r = getattr(self.mind, "routine", None)
        return ((getattr(r, "cfg", None) or {}).get("town") or {}).get("map")

    def close_reason(self, now, state):
        """Почему комнату надо закрыть (или нельзя открыть); None — можно держать."""
        r = getattr(self.mind, "routine", None)
        if state.get("dead"):
            return "погиб"
        if not r or not r.in_town_mode or not r.st.get("arrived"):
            return "ухожу из города"
        if state.get("map") != self.town_map():
            return "не в городе"
        if self.is_night(now):
            return "ночь"
        if self.mind.plans.store.active():
            return "иду на встречу"
        may_move = getattr(self.mind, "may_move", None)
        if may_move and not may_move("routine")[0]:
            return "телом занята другая задача"
        if (state.get("vend") or {}).get("open"):
            return "открыта лавка"
        if (state.get("weight_pct") or 0) >= 48:
            return "пора продать лут"
        rest_until = r.st.get("rest_until", 0)
        if rest_until != float("inf") and rest_until - now < self.cfg["room_min_rest_minutes"] * 60:
            return "скоро на охоту"
        act = getattr(self.mind, "activities", None)
        if act and (act.st or {}).get("name") in MOVING_ACTIVITIES and not (act.st or {}).get("proved"):
            return "занятие — идти"
        social = self.social()
        if social and social.next_walk and social.next_walk - now < 60:
            return "пора гулять"
        return None

    def title(self, state):
        """Заголовок по занятию/желанию: продаю, ищу группу, куплю — или «Отдыхаю»."""
        econ = getattr(self.mind, "economy", None)
        r = getattr(self.mind, "routine", None)
        signs = []
        if econ:
            try:
                lots = econ.for_sale(state)
                if lots:
                    signs.append(f"Продаю {econ.prices.name(lots[0][0])}")
                want = [i for i in econ.wishlist(state) if econ.prices.item(i)]
                if want:
                    signs.append(f"Куплю {econ.prices.name(sorted(want)[0])}")
            except (AttributeError, KeyError, TypeError, ValueError):
                pass
        if not state.get("party") and r:
            try:
                signs.append(f"Ищу группу на {r.hunt_map()}")
            except (AttributeError, KeyError, TypeError):
                pass
        if signs and self.rng.random() < self.cfg["room_sign_chance"]:
            return fit_title(self.rng.choice(signs))
        return "Отдыхаю"

    async def rooms(self, now, state):
        here = state.get("chat_room")                     # заголовок комнаты, где я сейчас (по данным тела)
        if self.room and not self.room.get("confirmed"):
            if here:
                self.room["confirmed"] = True
                self.save()
                self.note("society_room_opened", f"Открыл чат-комнату «{here}» — по данным сервера.", 1, title=here)
            elif now - self.room["since"] > ROOM_CONFIRM:
                self.note("society_room_failed", f"Чат-комната «{self.room['title']}» не открылась.", 1,
                          title=self.room["title"])
                self.room = None
                self.next_room = now + ROOM_FAIL_BACKOFF
                self.save()
            return
        if self.room and not here:
            self.note("society_room_closed", f"Чат-комната «{self.room['title']}» закрыта (перед движением).", 1,
                      title=self.room["title"], why="тело")
            self.room = None
            self.save()
            return
        why = self.close_reason(now, state)
        if here:
            if why or not self.room or now >= self.room["until"]:
                why = why or ("посидел достаточно" if self.room else "комната не моя или открыта до перезапуска")
                await self.mind.execute([{"action": "chat_room", "op": "close"}], source="society",
                                        reason=f"общество: закрыть комнату — {why}", protocol=True)
                if self.room:
                    self.note("society_room_closed", f"Закрыл чат-комнату «{self.room['title']}»: {why}.", 1,
                              title=self.room["title"], why=why)
                self.room = None
                self.next_room = now + self.interval()
                self.save()
            return
        if self.next_room is None:
            self.next_room = now + self.interval()      # отсчёт — с запуска / прибытия
            return
        if why or now < self.next_room:
            return
        self.next_room = now + self.interval()
        if self.rng.random() >= self.cfg["room_chance"]:
            return
        lo, hi = self.cfg["room_minutes"]
        stay = self.rng.uniform(lo, hi) * 60
        social = self.social()
        if social and social.next_walk:
            stay = min(stay, social.next_walk - now - 60)
        if stay < 5 * 60:
            return                                        # прогулка слишком скоро — не стоит открывать
        title = self.title(state)
        self.room = {"title": title, "since": now, "until": now + stay, "confirmed": False}
        self.save()
        self.mind.write_decision({"type": "society", "event": "room_open", "title": title,
                                  "minutes": round(stay / 60, 1)})
        await self.mind.execute([{"action": "chat_room", "op": "open", "title": title,
                                  "limit": self.cfg["room_limit"]}],
                                source="society", reason=f"общество: вывеска «{title}»", protocol=True)

    def interval(self):
        lo, hi = self.cfg["room_every_minutes"]
        return self.rng.uniform(lo, hi) * 60


# Строки хроники (chronicle.LINES дополняется этим словарём).
CHRONICLE_LINES = {
    "society_relation_drop": lambda d: f"отношение к {d.get('peer')} −1: {d.get('cause')}",
    "society_quarrel": lambda d: f"поссорился с {d.get('peer')}: {d.get('cause')}",
    "society_reconciled": lambda d: f"помирился с {d.get('peer')} ({d.get('occasion')})",
    "society_room_opened": lambda d: f"открыл чат-комнату «{d.get('title')}»",
}
