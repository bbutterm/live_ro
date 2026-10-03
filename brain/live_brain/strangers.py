"""Незнакомцы (ORG-063): жители замечают людей-игроков, узнают знакомых в лицо, коротко отвечают на шёпот.

Правила без LLM, тик 1 с. Источник — state.players тела (brainBridge nearbyPlayers: до 10 игроков в зоне
видимости, поля name, job, sex, lv, x, y) минус жители (ctx.peers) минус я.

Встреча: игрок впервые за meet_gap_minutes (30) виден не дальше meet_cells (10) клеток. Учёт —
kv strangers {"people": {имя: {seen, first, last, maps, hours, noted, emoted, replied}}, "replies": [...],
"emotes": [...]}; людей не больше max_people (100), лишние вытесняются по last (давно не виденные).
Знакомый в лицо: встреч ≥ familiar_meetings (3) в разные часы — один раз воспоминание note
«Часто вижу <имя> на <карта>.».

Эмоция wav (12) на встречу: только днём (day_hours по timezone_offset_hours мира), только в городе (towns +
город распорядка), не чаще раза в emote_person_minutes (30) одному человеку и не больше emotes_per_hour (4) всего;
сверху — лимит эмоций safety (EMOTE_LIMIT/10 мин).

Шёпот человека (не жителя) при BRAIN_LLM=off: mind.py по флагу gate (GateResult.stranger) зовёт on_whisper.
Ответ — только шаблон persona.phrases.stranger_hello (первый ответ незнакомцу) / stranger_reply (знакомому —
вариант с {name}) / stranger_busy (бой, мало HP); подстановки только наши: {me}, {name}, {map}, {hunt}.
Лимиты: одному — не чаще раза в reply_person_minutes (10), всем — не больше replies_per_hour (3); сверху
лимиты лички safety. Без меток, ≤ safety.MAX_TEXT символов. Шёпот с машинной меткой жителей ([offer:..],
[need:..]) — подделка протокола: без ответа. Если настроен быстрый gate (BRAIN_GATE=jev) — шаблон не
нужен, решает JEV, как раньше.

Безопасность: текст человека НЕ разбирается и НЕ исполняется — никаких сделок, движения, группы, передач по
словам; модуль отправляет только whisper (ответ) и emote. Текст не пишется в память и в журнал событий
(mind.py убирает поле text у события chat_private незнакомца при BRAIN_LLM=off); в память — только
«<имя> писал мне.». При включённом LLM модуль на шёпот не отвечает: прежний путь gate → LLM.
Конфигурация — раздел "strangers" brain/world/goals.json (необязателен); выключатель BRAIN_DISABLE=strangers.
"""
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone
from string import Formatter

from .safety import MAX_TEXT, fit_text
from .world_calendar import is_quiet                                   # hush: ORG-110

log = logging.getLogger("strangers")

DEFAULTS = {
    "enabled": True,
    "meet_cells": 10,
    "meet_gap_minutes": 30,
    "max_people": 100,
    "familiar_meetings": 3,
    "emote": 12,                         # wav (safety.EMOTES)
    "emote_person_minutes": 30,
    "emotes_per_hour": 4,
    "day_hours": [8, 23],                # местное время мира: [с, до)
    "towns": ["prontera", "izlude", "geffen", "payon", "morocc", "alberta", "aldebaran"],
    "reply_person_minutes": 10,
    "replies_per_hour": 3,
    "busy_hp_pct": 40,
}
FIELDS = {"me", "name", "map", "hunt"}
# Машинные метки жителей ([offer:..], [need:..], [chat:..]) от чужого — попытка говорить протоколом: молчим.
SPOOF = re.compile(r"\[[a-z]{3,12}:[^\[\]]{0,40}\]")
FALLBACK = {"stranger_hello": ["Привет. Я {me}."], "stranger_reply": ["Привет."],
            "stranger_busy": ["Занят, потом."]}


def dist(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


def fields(template):
    return {f for _, f, _, _ in Formatter().parse(template) if f}


def clean_name(name):
    """Имя игрока от сервера — только как подпись: без скобок меток, фигурных скобок и управляющих символов."""
    return "".join(c for c in str(name) if c.isprintable() and c not in "[]{}\"")[:23].strip()


class Strangers:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "strangers", "strangers", "strangers", True, "world"
    TICK_ORDER = 140            # private() и on_whisper() — явно в mind (стык с памятью и gate)

    def __init__(self, mind, world=None, cfg=None, clock=None, rng=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("strangers") or {}), **(cfg or {}))
        self.tz = timezone(timedelta(hours=(world or {}).get("timezone_offset_hours", 0)))
        town = (((world or {}).get("routine") or {}).get("town") or {}).get("map")
        self.towns = set(self.cfg["towns"]) | ({town} if town else set())
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.st = mind.mem.get("strangers") or {}
        for key, empty in (("people", {}), ("replies", []), ("emotes", [])):
            self.st.setdefault(key, empty)
        self.visible = {}                 # имя -> когда последний раз видел рядом (между сохранениями)
        self.last_save = 0.0

    # ---------- данные ----------

    @property
    def me(self):
        return self.mind.state.get("name") or self.mind.persona["name"]

    def save(self, now):
        self.last_save = now
        self.mind.mem.set("strangers", self.st)

    def is_stranger(self, name):
        return bool(name) and name not in self.mind.ctx.peers and name != self.me \
            and name != self.mind.persona["name"]

    def private(self, event):
        """Шёпот незнакомца при выключенном LLM: его текст не храним (mind.py убирает поле text из события)."""
        return (event.get("kind") == "chat_private" and not self.mind.s.llm_enabled
                and self.is_stranger(str(event.get("from") or "")))

    def familiar(self, name):
        p = self.st["people"].get(name) or {}
        return p.get("seen", 0) >= self.cfg["familiar_meetings"] and len(p.get("hours") or []) >= \
            self.cfg["familiar_meetings"]

    def is_day(self, now):
        lo, hi = self.cfg["day_hours"]
        h = datetime.fromtimestamp(now, self.tz).hour
        return lo <= h < hi if lo <= hi else (h >= lo or h < hi)

    def same_day(self, ts, now):                                        # hush: ORG-110
        return bool(ts) and (datetime.fromtimestamp(ts, self.tz).date() == datetime.fromtimestamp(now, self.tz).date())

    def decision(self, event, **data):
        self.mind.write_decision({"type": "strangers", "event": event, **data})

    # ---------- встречи ----------

    async def tick(self):
        now = self.clock()
        state = self.mind.state
        if not self.mind.fresh_state or state.get("dead") or state.get("x") is None:
            return
        met = []
        for pl in state.get("players") or []:
            if not isinstance(pl, dict) or pl.get("x") is None or pl.get("y") is None:
                continue
            name = clean_name(pl.get("name") or "")
            if not name or name != pl.get("name") or not self.is_stranger(name):
                continue
            try:
                d = dist(int(pl["x"]), int(pl["y"]), int(state["x"]), int(state["y"]))
            except (TypeError, ValueError):
                continue
            if d > self.cfg["meet_cells"]:
                continue
            p = self.st["people"].get(name)
            last = max(self.visible.get(name, 0), (p or {}).get("last", 0))
            self.visible[name] = now
            if now - last >= self.cfg["meet_gap_minutes"] * 60:
                met.append(name)
        for name in met:
            await self.meet(name, now, state)
        if met or (self.visible and now - self.last_save >= 60):
            for name, t in self.visible.items():
                if name in self.st["people"]:
                    self.st["people"][name]["last"] = max(t, self.st["people"][name].get("last", 0))
            self.visible = {n: t for n, t in self.visible.items() if now - t < 3600}
            self.evict()
            self.save(now)

    async def meet(self, name, now, state):
        where = state.get("map") or "?"
        p = self.st["people"].setdefault(name, {"seen": 0, "first": now, "maps": [], "hours": []})
        p["seen"] = p.get("seen", 0) + 1
        p["last"] = now
        p["maps"] = ([m for m in p.get("maps", []) if m != where] + [where])[-5:]
        hour = int(now // 3600)
        if hour not in p.get("hours", []):
            p["hours"] = (p.get("hours", []) + [hour])[-5:]
        self.mind.mem.add_event("stranger_met", {"name": name, "map": where, "seen": p["seen"]})
        self.decision("met", name=name, map=where, seen=p["seen"])
        if self.familiar(name) and not p.get("noted"):
            p["noted"] = True
            self.mind.mem.remember(f"Часто вижу {name} на {where}.", 2, kind="note")
            self.mind.mem.add_event("stranger_familiar", {"name": name, "map": where})
            self.decision("familiar", name=name, map=where)
        await self.wave(name, p, now, state)

    async def wave(self, name, p, now, state):
        if not self.is_day(now) or state.get("map") not in self.towns:
            return
        if now - p.get("emoted", 0) < self.cfg["emote_person_minutes"] * 60:
            return
        if is_quiet(self.mind, now) and self.same_day(p.get("emoted", 0), now):   # hush: тихий день — машу раз в день
            return                                                               # hush:
        self.st["emotes"] = [t for t in self.st["emotes"] if now - t < 3600]
        if len(self.st["emotes"]) >= self.cfg["emotes_per_hour"]:
            return
        p["emoted"] = now
        self.st["emotes"].append(now)
        await self.mind.execute([{"action": "emote", "emotion": self.cfg["emote"]}], source="strangers",
                                reason=f"незнакомцы: машу {name}", protocol=True)

    def evict(self):
        people = self.st["people"]
        if len(people) > self.cfg["max_people"]:
            keep = sorted(people.items(), key=lambda kv: kv[1].get("last", 0))[-self.cfg["max_people"]:]
            self.st["people"] = dict(keep)

    # ---------- шёпот ----------

    def phrase(self, key, facts, with_name):
        options = (self.mind.persona.get("phrases") or {}).get(key) or FALLBACK[key]
        options = [o for o in options if fields(o) <= FIELDS] or FALLBACK[key]
        named = [o for o in options if "name" in fields(o)]
        plain = [o for o in options if "name" not in fields(o)]
        pool = (named if with_name and named else plain) or options
        text = self.rng.choice(pool).format(**facts)
        return fit_text(text.replace("[", "(").replace("]", ")"), MAX_TEXT)

    def busy(self):
        state = self.mind.state
        hp = state.get("hp_pct")
        return state.get("activity") == "attack" or (isinstance(hp, (int, float)) and hp < self.cfg["busy_hp_pct"])

    async def on_whisper(self, name, text=None):
        """Ответ на шёпот незнакомца — только при выключенном LLM. Текст человека не разбирается: ни команд,
        ни действий по нему, ни записи; проверяется только одно — нет ли в нём машинных меток жителей (тогда
        молчим). Возвращает True, если ответ отправлен в тело."""
        if self.mind.s.llm_enabled or not self.is_stranger(name):
            return False
        now = self.clock()
        state = self.mind.state
        who = clean_name(name)
        p = self.st["people"].setdefault(name, {"seen": 0, "first": now, "last": 0, "maps": [], "hours": []})
        if now - p.get("wrote", 0) >= 3600:
            self.mind.mem.remember(f"{who} писал мне.", 1)
        p["wrote"] = now
        self.mind.mem.add_event("stranger_whisper", {"from": name})
        why = None
        self.st["replies"] = [t for t in self.st["replies"] if now - t < 3600]
        if state.get("dead"):
            why = "мёртв"
        elif who != name:
            why = "странное имя"
        elif SPOOF.search(str(text or "")):
            why = "метка жителей от чужого — молчу"
        elif now - p.get("replied", 0) < self.cfg["reply_person_minutes"] * 60:
            why = f"уже отвечал {name} меньше {self.cfg['reply_person_minutes']} мин назад"
        elif len(self.st["replies"]) >= self.cfg["replies_per_hour"]:
            why = f"лимит ответов незнакомцам {self.cfg['replies_per_hour']}/ч"
        if why:
            self.decision("whisper_skip", name=name, why=why)
            self.evict()
            self.save(now)
            return False
        facts = {"me": self.me, "name": who, "map": state.get("map") or "городе",
                 "hunt": state.get("lock_map") or (self.mind.persona.get("hunt_maps") or ["поле"])[0]}
        if self.busy():
            key = "stranger_busy"
        elif p.get("replied") or self.familiar(name):
            key = "stranger_reply"
        else:
            key = "stranger_hello"
        text = self.phrase(key, facts, with_name=self.familiar(name))
        p["replied"] = now
        self.st["replies"].append(now)
        self.evict()
        self.save(now)
        await self.mind.execute([{"action": "whisper", "to": name, "text": text}], source="strangers",
                                reason=f"незнакомцы: ответ {name} ({key})")
        return True
