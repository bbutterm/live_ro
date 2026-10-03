"""Социальная жизнь жителей без LLM (AUT-087–089, 091–095, 102). Правила, тик 1 с.

Конфигурация — brain/world/goals.json раздел "social", персональные переопределения —
поле "social" в brain/personas/<bot>.json; шаблонные реплики — поле "phrases" персоны.

Городской распорядок (режим routine «town», житель дошёл до точки отдыха):
    раз в walk_minutes (10–25 мин) житель переходит к другой точке интереса Пронтеры
    (фонтан, Kafra, вход к Tool Dealer, собор), встаёт (stand), идёт (meet_point) и садится
    по прибытии (sit). Выбор точки — по весам характера (point_weights): Vera чаще у собора
    и Kafra, Arkady у торговцев. Если друг-житель стоит у какой-то точки, житель с шансом
    join_friend_chance (выше при хорошем отношении) идёт к нему — так жители встречаются.
    Точка отдыха распорядка (routine.town) на время прогулки сдвигается на выбранную точку,
    чтобы распорядок не возвращал тело назад; вне режима town исходная точка восстанавливается.
    Не ходит: активный план встречи, арбитр не разрешает (mind.may_move("routine")),
    открыта лавка, отдых кончается раньше чем через rest_end_guard_minutes, ночь.
Встреча в городе (житель видим ≤ near_cells клеток): шёпот с меткой [chat:<тема>:<шаг>].
    Шаг 1 — приветствие (с эмоцией), 2 — ответ на приветствие, 3 — тема по фактам памяти
    (уровень, победы за день, добыча, гибель, усталость, погода мира — weather.py), 4 — прощание или поздравление/
    сочувствие. На шаг 4 не отвечают: не больше max_exchanges (2) обменов подряд.
    Пара говорит не чаще pair_gap_minutes (15 мин); не друзьям — реже, ночью — ещё реже.
    Фразы не повторяются, пока не исчерпаны варианты ключа (история used в kv "social").
    При включённом LLM модуль не говорит шаблонами, а только даёт повод mind.trigger(kind="chat").
Реакции на события (по шаблону, с лимитом react_gap_minutes на вид и жителя):
    мой новый уровень (level_up) — сообщить жителям; уровень жителя вырос (lv в state.players) —
    поздравить; гибель жителя ([party:dead]) — посочувствовать; лечение жителя (support AL_HEAL) —
    поблагодарить. С эмоцией (действие emote, только из allowlist safety.EMOTES).
Отношения по поступкам (AUT-102): время рядом с жителем (в городе или на охоте) от
    together_minutes за сутки — affinity +1, не чаще раза в сутки. Проигнорированное сообщение —
    ничего. Отношение задаёт частоту общения и шанс пойти к другу.
Ссора (society.py, ORG-027): с жителем «в ссоре» не заговаривают первым и не идут к нему на прогулке;
    ответ ему — холодная короткая реплика (тема cold, шаг 4 — без продолжения), кроме сочувствия.
Ночь (night_hours по timezone_offset_hours мира): не гуляют, сидят, говорят в night_factor раз реже.
Реестр тем (ORG-066): register_topic(name, provider, reply) — модули добавляют темы разговора (topics.py:
    питомец, слух, цель недели, новости мира; weather.py — погода; episodes.py — «помнишь?»). Тема шага 3 —
    случайно среди фактов дня и тем реестра, по которым есть факты и фразы (гибель и уровень — первыми);
    на тему собеседника из реестра ответ — фразой <тема>_re (тот же тег темы, шаг +1), а не своим монологом.
Грамматика реплик (ORG-065, grammar.py): фраза персоны по прежней ротации, поверх — шаблоны speech.json с
    альтернативами, род глагола по полу, коды карт с предлогами («на карте X» или имя места ORG-084), словечки
    по характеру, без повтора последних текстов (kv social.said_recent). Выкл.: goals.json grammar.enabled=false.
Все реплики идут через SafetyPolicy как обычный чат (лимиты лички, без повторов за час);
эмоции и переходы — служебные действия (protocol=True), модель их не получает.
"""
import json
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone

from . import weather
from . import grammar as grammar_mod                       # grammar: ORG-065 слой реплик поверх фраз персон
from .world_calendar import PHRASES as CALENDAR_PHRASES   # calendar: фразы тем holiday/birthday по умолчанию

log = logging.getLogger("social")

TAG = re.compile(r"\[chat:([a-z]{3,12}):([1-4])\]")
LAST_STEP = 4                         # 2 обмена: 1-2 и 3-4
FACT_TOPICS = ("death", "level", "hunt", "loot", "tired")
FACT_TOPICS += ("trip",)                    # explore: «где был» — факт экспедиции (ORG-054)
TRIP_PHRASES = ["А я на днях до {trip_map} дошёл(дошла)!", "Был(а) на {trip_map} — интересное место.",
                "Сходил(а) на {trip_map}, теперь знаю дорогу."]   # explore: если в персоне нет phrases.trip
TOPICS = ("hello", "weather", "bye", "congrats", "condolence", "thanks") + FACT_TOPICS
REPLY = {"level": "congrats", "death": "condolence"}       # на что отвечают особой фразой
EMOTE = {"hello": 12, "bye": 12, "congrats": 21, "condolence": 28, "thanks": 15, "level": 2,
         "loot": 18, "weather": 20}                         # тема -> номер эмоции (tables/emotions.txt)
DEFAULTS = {
    "enabled": True,
    "walk_minutes": [10, 25],
    "rest_end_guard_minutes": 5,
    "points": {},
    "point_weights": {},
    "join_friend_chance": 0.4,
    "near_cells": 6,
    "pair_gap_minutes": 15,
    "max_exchanges": 2,
    "reply_delay_seconds": [3, 8],
    "react_gap_minutes": 30,
    "emote_gap_seconds": 120,
    "friend_affinity": 3,
    "together_minutes": 30,
    "together_cap": 6,          # soak: «просто рядом» поднимает отношение не выше этого (остальное — поступки)
    "night_hours": [1, 7],
    "night_factor": 3,
}
MAX_PHRASE = 80


def dist(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


def merged_social(world, persona):
    cfg = dict(DEFAULTS)
    cfg.update((world or {}).get("social") or {})
    own = persona.get("social") or {}
    for key, value in own.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key] = dict(cfg[key], **value)
        else:
            cfg[key] = value
    return cfg


def fields(template):
    return grammar_mod.fields(template)        # grammar: слоты {name}; альтернативы {a|b} — не слоты


class Social:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES, ARGS = "social", "social", "social", True, ("world", "peers"), "world"
    TICK_ORDER = 70
    TAGS, TAG_ORDER = [(TAG, "on_tag")], 80
    ECHO = [("party", r"\[party:dead:", "on_peer_dead", 10)]          # сочувствие
    EVENTS, EVENT_ORDER = {"support": "on_support", "level_up": "on_level_up"}, 20

    def __init__(self, mind, world, clock=None, rng=None):
        self.mind = mind
        self.cfg = merged_social(world, mind.persona)
        self.tz_hours = (world or {}).get("timezone_offset_hours", 0)
        self.tz = timezone(timedelta(hours=self.tz_hours))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.phrases = mind.persona.get("phrases") or {}
        if "trip" not in self.phrases:                                         # explore: тема «где был»
            self.phrases = dict(self.phrases, trip=TRIP_PHRASES)               # explore:
        self.phrases = dict(CALENDAR_PHRASES, **self.phrases)                  # calendar: holiday/birthday (ORG-059)
        self.st = mind.mem.get("social") or {}
        for key in ("pairs", "used", "together", "bonded", "peer_lv", "react", "told", "replies"):
            self.st.setdefault(key, {})
        self.next_walk = None
        self.spot = None                  # имя текущей точки интереса или None (исходная точка отдыха)
        self.sat = False
        self.queue = []                   # отложенные ответы: (когда, кому, тема, шаг)
        self.near_since = {}              # житель -> с какого времени рядом
        self.last_tick = None
        self.last_emote = 0.0
        self.last_save = 0.0
        self.topics = {}                  # реестр тем (ORG-066): имя -> поставщик фактов, ключ ответа, флаги
        self.register_topic("weather", self.weather_facts, fallback=True)   # ORG-085: общая погода мира
        self.st.setdefault("said_recent", [])                                 # grammar: анти-повтор текстов
        self.grammar = self.make_grammar(world)                               # grammar: ORG-065, None — выкл.

    # ---------- грамматика реплик (ORG-065) ----------

    def make_grammar(self, world):                                            # grammar:
        """Слой грамматики (grammar.py) или None: goals.json grammar.enabled, BRAIN_DISABLE=grammar."""
        cfg = dict(grammar_mod.DEFAULTS, **((world or {}).get("grammar") or {}))
        s = getattr(self.mind, "s", None)
        if not cfg.get("enabled", True) or (s is not None and hasattr(s, "feature") and not s.feature("grammar")):
            return None
        return grammar_mod.Grammar(grammar_mod.load_speech(), self.mind.persona, lambda: self.rng, cfg,
                                   sex=self.my_sex, peer_sex=self.peer_sex)

    def my_sex(self):                                                         # grammar: пол по данным игры
        state = getattr(self.mind, "state", None) or {}
        return grammar_mod.sex_of(state.get("sex")) or grammar_mod.sex_of(self.mind.persona.get("sex"))

    def peer_sex(self, name):                                                 # grammar: как в topics.py
        if not name:
            return None
        return grammar_mod.sex_of(((self.mind.mem.get("known_players") or {}).get(name) or {}).get("sex"))

    # ---------- данные ----------

    @property
    def me(self):
        return self.mind.state.get("name") or self.mind.persona["name"]

    def save(self):
        self.mind.mem.set("social", self.st)

    def local(self, now):
        return datetime.fromtimestamp(now, self.tz)

    def today(self, now):
        return self.local(now).strftime("%Y-%m-%d")

    def day_start(self, now):
        d = self.local(now)
        return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def is_night(self, now):
        lo, hi = self.cfg["night_hours"]
        h = self.local(now).hour
        return lo <= h < hi if lo <= hi else (h >= lo or h < hi)

    def affinity(self, peer):
        rel = self.mind.mem.relation(peer)
        return (rel or {}).get("affinity", 0)

    def pair_gap(self, peer, now):
        """Друзья общаются чаще: 15 мин — минимум; нейтральным ×1.5, неприятным ×3; ночью ещё ×night_factor."""
        aff = self.affinity(peer)
        factor = 1.0 if aff >= self.cfg["friend_affinity"] else (3.0 if aff < 0 else 1.5)
        if self.is_night(now):
            factor *= self.cfg["night_factor"]
        mood = getattr(self.mind, "mood", None)
        if mood:
            factor *= mood.talk_factor()              # ORG-064: в плохом настроении реже, в хорошем чаще
        return self.cfg["pair_gap_minutes"] * 60 * factor

    def near_peers(self, state, cells=None):
        cells = cells or self.cfg["near_cells"]
        if state.get("x") is None:
            return []
        out = []
        for p in state.get("players") or []:
            if (isinstance(p, dict) and p.get("name") in self.mind.ctx.peers and p.get("x") is not None
                    and dist(int(state["x"]), int(state["y"]), int(p["x"]), int(p["y"])) <= cells):
                out.append(p["name"])
        return sorted(set(out))

    def quarrel(self, peer):
        """society: в ссоре с жителем (society.py, ORG-027) — не заговаривать первым, не идти к нему, отвечать холодно."""
        society = getattr(self.mind, "society", None)
        return bool(society and society.quarrel(peer))

    def busy(self):
        """AUT-095: не болтать в разгар аварии и боя."""
        life = getattr(self.mind, "life", None)
        return getattr(life, "current", None) in ("DEAD", "ESCAPING", "FIGHTING", "BLOCKED")

    def llm(self):
        s = getattr(self.mind, "s", None)
        return bool(getattr(s, "llm_enabled", False))

    def events_since(self, kind, since):
        rows = self.mind.mem.db.execute("SELECT data FROM events WHERE kind = ? AND ts >= ? ORDER BY id",
                                        (kind, since))
        return [json.loads(r[0]) for r in rows]

    def facts(self, peer=None, now=None):
        """Факты из памяти и состояния игры для подстановки в фразы (только то, что известно)."""
        now = now or self.clock()
        start = self.day_start(now)
        mem, state = self.mind.mem, self.mind.state
        f = {"name": peer or "", "me": self.me}
        if state.get("lv"):
            f["lv"] = state["lv"]
        levels = [e.get("level") for e in self.events_since("level_up", start) if e.get("level")]
        if levels:
            f["lv"] = max(levels)
            f["leveled"] = True
        kills = mem.count_events("kill", start)
        if kills:
            f["kills"] = kills
        loot = mem.count_events("loot", start)
        if loot:
            f["loot"] = loot
        deaths = self.events_since("died", start)
        if deaths:
            f["death_map"] = deaths[-1].get("map") or "охоте"
        r = getattr(self.mind, "routine", None)
        if r and r.st:
            if r.st.get("hunted", 0) >= 3600:
                f["hours"] = int(r.st["hunted"] // 3600)
            try:
                f["map"] = r.hunt_map()
            except (AttributeError, KeyError, TypeError):
                pass
        explorer = getattr(self.mind, "explorer", None)                        # explore: последняя экспедиция за 2 суток
        trip = explorer.last_trip(now - 2 * 86400) if explorer else None       # explore:
        if trip:                                                               # explore:
            f["trip_map"] = trip["map"]                                        # explore:
        cal = getattr(self.mind, "calendar", None)                             # calendar: праздник и день рождения
        if cal and peer:                                                       # calendar: собеседника (ORG-059),
            f.update(cal.topic_facts(peer, now))                               # calendar: раз в день каждому
        if peer:
            heals = [e for e in self.events_since("heal_confirmed", start)
                     if e.get("from") == peer and e.get("to") == self.me]
            if heals:
                f["amount"] = heals[-1].get("amount")
            if self.st["peer_lv"].get(peer):
                f["peer_lv"] = self.st["peer_lv"][peer]
        return f

    def available(self, facts):
        topics = []
        if facts.get("death_map"):
            topics.append("death")
        if facts.get("leveled"):
            topics.append("level")
        if facts.get("kills"):
            topics.append("hunt")
        if facts.get("loot"):
            topics.append("loot")
        if facts.get("hours"):
            topics.append("tired")
        if facts.get("trip_map"):                                              # explore:
            topics.append("trip")                                              # explore:
        topics += [t for t in ("birthday", "holiday") if facts.get(t)]         # calendar: ORG-059
        return topics

    # ---------- реестр тем (ORG-066) ----------

    def register_topic(self, name, provider, reply=None, said=None, chance=1.0, opener=False, fallback=False):
        """Тема разговора: provider(peer, now) -> факты (dict) или None — темы нет.

        reply — ключ фраз ответа собеседника (по умолчанию <name>_re); said(peer, facts, now) — после реплики
        (отметить «уже рассказал»); chance — шанс предложить тему, если она доступна; opener — может заменить
        приветствие на шаге 1 (с тем же шансом); fallback — только когда других тем нет (погода).
        Ключ "_key" в фактах — другой ключ фраз (weather_rain, remember_heal); ключи с "_" в фразы не идут.
        """
        if not re.fullmatch(r"[a-z]{3,12}", name):
            raise ValueError(f"тема {name!r}: нужно [a-z]{{3,12}} (метка [chat:<тема>:<шаг>])")
        self.topics[name] = {"provider": provider, "reply": reply or f"{name}_re", "said": said,
                             "chance": chance, "opener": opener, "fallback": fallback}

    def provide(self, name, peer, now):
        """Факты одной темы реестра; ошибка поставщика — в лог, темы нет."""
        t = self.topics.get(name)
        if not t:
            return None
        try:
            f = t["provider"](peer, now)
        except Exception as e:                       # noqa: BLE001 — чужой модуль не роняет разговор
            log.warning("тема %s: поставщик упал: %s", name, e)
            return None
        return dict(f) if isinstance(f, dict) and f else None

    def topic_facts(self, peer, now):
        """{тема: факты} от всех поставщиков реестра (у кого фактов нет — пропуск)."""
        out = {}
        for name in self.topics:
            f = self.provide(name, peer, now)
            if f is not None:
                out[name] = f
        return out

    def can_say(self, key, facts):
        return any(fields(p) <= set(facts) for p in self.phrases.get(key) or [])

    def registry_topics(self, peer, now, base=None, opener=False):
        """Темы реестра, о которых сейчас есть что сказать (факты есть, фраза с ними подставляется)."""
        out = []
        for name, t in self.topics.items():
            if t["fallback"] or (opener and not t["opener"]):
                continue
            if t["chance"] < 1 and self.rng.random() >= t["chance"]:
                continue
            f = self.provide(name, peer, now)
            if f is None:
                continue
            if self.can_say(f.get("_key", name), dict(base or {}, **f)):
                out.append(name)
        return out

    def weather_facts(self, peer, now):
        """ORG-085: погода — общий детерминированный факт мира (weather.py), а не выдумка жителя."""
        s = getattr(self.mind, "s", None)
        if s is not None and hasattr(s, "feature") and not s.feature("weather"):
            return None
        w = weather.weather(now, self.tz_hours)
        return {"weather": w["label"], "_key": f"weather_{w['kind']}"}

    def choose_topic(self, peer, facts, now):
        """Тема по свежим фактам и реестру; о чём уже говорил сегодня этому жителю — не повторять."""
        day = self.today(now)
        told = self.st["told"].get(peer) or {}
        if told.get("day") != day:
            told = {"day": day, "topics": []}
        fresh = [t for t in self.available(facts) if t not in told["topics"] and self.phrases.get(t)]
        if not (fresh and fresh[0] in ("death", "level")):          # ORG-066: темы из жизни мира
            fresh += [t for t in self.registry_topics(peer, now, facts) if t not in told["topics"] and t not in fresh]
        topic = fresh[0] if fresh and fresh[0] in ("death", "level") else (self.rng.choice(fresh) if fresh else "weather")
        told["topics"] = told["topics"] + [topic]
        self.st["told"][peer] = told
        return topic

    def phrase(self, key, facts):
        """Фраза ключа key с подстановкой фактов; варианты не повторяются, пока не исчерпаны."""
        mood = getattr(self.mind, "mood", None)
        if mood:                                       # ORG-064: hello_good / hunt_bad, если такие фразы есть
            k = mood.phrase_key(key)
            if k != key and self.can_say(k, facts):
                key = k
        options = [p for p in self.phrases.get(key) or [] if fields(p) <= set(facts)]
        if not options:
            return None
        used = self.st["used"].get(key) or []
        fresh = [p for p in options if p not in used] or [p for p in options if p != (used[-1] if used else None)] \
            or options
        choice = self.rng.choice(fresh)
        used = [u for u in used if u != choice] + [choice]
        self.st["used"][key] = used[-max(1, len(self.phrases.get(key) or []) - 1):]
        if self.grammar is not None:                       # grammar: ORG-065 шаблоны, род, карты, словечки
            recent = self.st.setdefault("said_recent", [])
            try:
                text = self.grammar.say(key, choice, facts, recent)
            except Exception as e:                     # noqa: BLE001 — сбой грамматики: прежняя фраза персоны
                log.warning("грамматика: %s — фраза персоны без слоя", e)
                text = None
            if text is not None:
                keep = int(self.grammar.cfg["recent"])
                self.st["said_recent"] = (recent + [text])[-keep:] if keep > 0 else []
                return text[:MAX_PHRASE]
        text = choice.format(**facts)
        return " ".join(text.split())[:MAX_PHRASE]

    # ---------- тик ----------

    async def tick(self):
        now = self.clock()
        state = self.mind.state
        gap = (now - self.last_tick) if self.last_tick else 0
        self.last_tick = now
        if not self.mind.fresh_state or state.get("dead"):
            return
        self.together(now, state, gap)
        await self.watch_peer_levels(now, state)
        await self.flush(now)
        r = getattr(self.mind, "routine", None)
        if not r or not r.in_town_mode:
            self.restore()
            self.next_walk = None
            return
        if not r.st.get("arrived"):
            return
        await self.walk(now, state, r)
        await self.chat(now, state)
        if now - self.last_save >= 60:
            self.last_save = now
            self.save()

    # ---------- городской распорядок ----------

    def interval(self):
        lo, hi = self.cfg["walk_minutes"]
        return self.rng.uniform(lo, hi) * 60

    def restore(self):
        """Вернуть распорядку исходную точку отдыха (центр)."""
        r = getattr(self.mind, "routine", None)
        if r and self.spot is not None:
            r.town = r.cfg["town"]
        self.spot = None
        self.sat = False

    def may_walk(self, r):
        if self.mind.plans.store.active():
            return False
        explorer = getattr(self.mind, "explorer", None)        # explore: в экспедиции по городу не гуляем
        if explorer and explorer.busy():                       # explore:
            return False                                       # explore:
        may_move = getattr(self.mind, "may_move", None)
        if may_move and not may_move("routine")[0]:
            return False
        return not (self.mind.state.get("vend") or {}).get("open")

    def pick_point(self, state):
        """Точка по весам характера; если друг стоит у точки — с шансом идти к нему."""
        points = {k: v for k, v in (self.cfg.get("points") or {}).items()
                  if v.get("map", self.mind.routine.cfg["town"]["map"]) == state.get("map")}
        if not points:
            return None, None
        for p in state.get("players") or []:
            if not isinstance(p, dict) or p.get("name") not in self.mind.ctx.peers or p.get("x") is None:
                continue
            if self.quarrel(p["name"]):                   # society: в ссоре — к нему не идём
                continue
            chance = self.cfg["join_friend_chance"] * (1.5 if self.affinity(p["name"]) >= self.cfg["friend_affinity"]
                                                       else 1.0)
            near = min(points, key=lambda k: dist(points[k]["x"], points[k]["y"], int(p["x"]), int(p["y"])))
            if near != self.spot and self.rng.random() < chance:
                return near, f"там {p['name']}"
        here = self.spot
        if here is None and state.get("x") is not None:   # ещё у исходной точки — та, что ближе
            here = min(points, key=lambda k: dist(points[k]["x"], points[k]["y"], int(state["x"]), int(state["y"])))
        weights = self.cfg.get("point_weights") or {}
        names = [k for k in sorted(points) if k != here] or sorted(points)
        habits = getattr(self.mind, "habits", None)                                    # habits: ORG-068
        w = [max(0.0, float(weights.get(k, 1))) * (habits.point_factor(k) if habits else 1)   # habits:
             for k in names]
        if not any(w):
            w = [1.0] * len(names)
        return self.rng.choices(names, weights=w)[0], "по настроению"

    async def walk(self, now, state, r):
        if self.next_walk is None:
            self.next_walk = now + self.interval()      # отсчёт — с прибытия в город
        if not self.may_walk(r):
            return
        point = (self.cfg["points"].get(self.spot) if self.spot else None) or r.town
        here = state.get("x") is not None and dist(int(state["x"]), int(state["y"]), point["x"], point["y"]) <= 3
        if here and not state.get("sitting") and not self.sat and (self.spot or self.is_night(now)):
            self.sat = True
            await self.send([{"action": "sit"}], "общение: присесть" + (" — ночь" if self.is_night(now) else ""))
            return
        if now < self.next_walk or self.is_night(now):
            return
        guard = self.cfg["rest_end_guard_minutes"] * 60
        if r.st.get("rest_until", 0) != float("inf") and r.st.get("rest_until", 0) - now < guard:
            return                                  # отдых кончается — не начинать прогулку
        name, why = self.pick_point(state)
        self.next_walk = now + self.interval()
        if not name:
            return
        p = self.cfg["points"][name]
        town_map = p.get("map", r.cfg["town"]["map"])
        self.spot, self.sat = name, False
        r.town = {"map": town_map, "x": p["x"], "y": p["y"], "radius": 2}
        r.last_sent = now                         # распорядок не дублирует команду в этот же тик
        await self.send([{"action": "stand"}, {"action": "meet_point", "map": town_map, "x": p["x"], "y": p["y"]}],
                        f"общение: иду {p.get('label', name)} ({why})")
        self.mind.mem.add_event("social_walk", {"point": name, "why": why})

    async def visit(self, map_, x, y, label):
        """Подойти к месту (каталог занятий: «навестить жителя»): как прогулка, но к заданной клетке."""
        r = getattr(self.mind, "routine", None)
        if not r or not r.in_town_mode or not self.may_walk(r):
            return False
        self.spot, self.sat = None, False
        r.town = {"map": map_, "x": int(x), "y": int(y), "radius": 2}
        r.last_sent = self.clock()
        await self.send([{"action": "stand"}, {"action": "meet_point", "map": map_, "x": int(x), "y": int(y)}],
                        f"общение: иду {label}")
        self.next_walk = self.clock() + self.interval()
        return True

    def walk_now(self):
        """Каталог занятий выбрал прогулку — следующая прогулка сразу (ночью и при запретах walk сам откажет)."""
        self.next_walk = self.clock()

    # ---------- разговоры ----------

    async def chat(self, now, state):
        near = self.near_peers(state)
        self.near_since = {p: self.near_since.get(p, now) for p in near}
        if self.busy():
            return
        mood = getattr(self.mind, "mood", None)
        if mood and mood.silent():                         # ORG-064: мрачный — первым не заговаривает (отвечает)
            return
        for peer in near:
            if self.quarrel(peer):                        # society: в ссоре — первым не заговаривать
                continue
            last = self.st["pairs"].get(peer, 0)
            gap = self.pair_gap(peer, now)
            if self.me > peer and (now - self.near_since[peer] < 60 or now - last < gap + 60):
                continue                          # одновременно не начинать: первым заговаривает меньшее имя
            if now - last < gap or any(q[1] == peer for q in self.queue):
                continue
            self.st["pairs"][peer] = now
            if self.llm():
                f = self.facts(peer, now)
                self.mind.ctx.last[f"talk:{peer}"] = now      # общий повод не дублируется peer_smalltalk
                self.mind.trigger(f"{peer} (житель) рядом в городе — можно поговорить; факты дня: "
                                  f"{ {k: v for k, v in f.items() if k not in ('name', 'me')} }",
                                  {"from": peer}, kind="chat")
            else:
                openers = self.registry_topics(peer, now, opener=True)     # ORG-055: «помнишь?» вместо привета
                await self.say(peer, self.rng.choice(openers) if openers else "hello", 1)
            self.save()
            return

    def compose(self, peer, topic, key, now):
        """Текст реплики темы: факты дня + факты темы реестра; key — ключ фраз ответа (<тема>_re) или None.

        Возвращает (текст, факты темы) или (None, None). Фраза темы реестра — из "_key" фактов (если есть такие
        фразы), иначе из ключа темы; без фактов тема реестра не говорится (кроме ответа <тема>_re).
        """
        facts = self.facts(peer, now)
        extra = self.provide(topic, peer, now) if topic in self.topics else None
        if topic in self.topics and extra is None and key is None and not self.topics[topic]["fallback"]:
            return None, None
        facts.update(extra or {})
        keys = [key] if key else ([extra["_key"]] if extra and extra.get("_key") else []) + [topic]
        for k in keys:
            text = self.phrase(k, facts)
            if text is not None:
                return text, extra
        return None, None

    async def say(self, peer, topic, step, now=None, key=None):
        """Шёпот жителю: фраза темы + метка. Обычный чат — лимиты safety действуют.

        key — ключ фраз ответа на тему собеседника (ORG-066: <тема>_re), метка остаётся темой собеседника."""
        now = now or self.clock()
        if self.quarrel(peer) and step == 1:              # society: в ссоре — первым не пишу (и весточек нет)
            return False
        if self.quarrel(peer) and topic not in ("condolence", "thanks"):   # society: холодно, коротко, без продолжения
            text, topic, step = self.mind.society.cold_phrase(peer), "cold", LAST_STEP
            self.st["pairs"][peer] = now
            await self.mind.execute([{"action": "whisper", "to": peer, "text": f"{text} [chat:cold:{step}]"}],
                                    source="social", reason=f"общение: холодно жителю {peer} (ссора)")
            self.mind.mem.add_event("social_said", {"peer": peer, "topic": "cold", "fact": False})
            self.save()
            return True
        text, extra = self.compose(peer, topic, key, now)
        if text is None and topic not in ("hello", "bye", "weather"):
            topic, key = "weather", None
            text, extra = self.compose(peer, topic, None, now)
        if text is None:
            return False
        tag = f"[chat:{topic}:{step}]"
        self.st["pairs"][peer] = now
        await self.mind.execute([{"action": "whisper", "to": peer, "text": f"{text} {tag}"}],
                                source="social", reason=f"общение: {topic} жителю {peer}")
        self.mind.mem.add_event("social_said", {"peer": peer, "topic": topic, "fact": topic in FACT_TOPICS,
                                                "text": text})     # grammar: текст — для метрики повторов
        self.mind.write_decision({"type": "social", "event": "said", "to": peer, "topic": topic, "step": step,
                                  "key": key or (extra or {}).get("_key") or topic})
        said = (self.topics.get(topic) or {}).get("said")
        if said and extra is not None and key is None:
            try:
                said(peer, extra, now)                      # ORG-066: «уже рассказал» — тема не повторится
            except Exception as e:                          # noqa: BLE001
                log.warning("тема %s: отметка упала: %s", topic, e)
        await self.emote(topic, now)
        self.save()
        return True

    async def emote(self, topic, now):
        eid = EMOTE.get(topic)
        if eid is None or now - self.last_emote < self.cfg["emote_gap_seconds"]:
            return
        if self.is_night(now) and topic not in ("condolence", "thanks"):
            return
        self.last_emote = now
        await self.send([{"action": "emote", "id": eid}], f"общение: эмоция {topic}")

    async def send(self, actions, reason):
        await self.mind.execute(actions, source="social", reason=reason, protocol=True)

    async def flush(self, now):
        due = [q for q in self.queue if q[0] <= now]
        self.queue = [q for q in self.queue if q[0] > now]
        for q in due:
            _, peer, topic, step = q[:4]
            key = q[4] if len(q) > 4 else None             # ORG-066: ответ по теме собеседника (<тема>_re)
            if topic is None:
                topic = self.choose_topic(peer, self.facts(peer, now), now)
            await self.say(peer, topic, step, now, key=key)

    async def on_tag(self, sender, text):
        """Реплика жителя с меткой [chat:<тема>:<шаг>] — ответить по правилу (не больше 2 обменов)."""
        m = TAG.search(text or "")
        if not m or sender not in self.mind.ctx.peers:
            return
        topic, step = m.group(1), int(m.group(2))
        now = self.clock()
        self.st["pairs"][sender] = now
        self.mind.mem.add_event("social_heard", {"from": sender, "topic": topic, "step": step})
        self.mind.write_decision({"type": "social", "event": "heard", "from": sender, "topic": topic, "step": step})
        if topic in ("congrats", "condolence", "thanks"):
            self.mind.mem.remember(f"{sender} сказал мне доброе слово: {TAG.sub('', text).strip()[:120]}", 1,
                                   kind="note")
        nxt = step + 1
        if nxt > min(LAST_STEP, 2 * self.cfg["max_exchanges"]) or self.mind.state.get("dead"):
            self.save()
            return
        window = self.cfg["pair_gap_minutes"] * 60
        replies = [t for t in self.st["replies"].get(sender, []) if now - t < window]
        if len(replies) >= self.cfg["max_exchanges"]:
            self.save()
            return                               # защита от петли: не больше 2 ответов за окно
        self.st["replies"][sender] = replies + [now]
        if self.llm():
            self.mind.trigger(f"{sender} (житель) пишет мне: {TAG.sub('', text).strip()[:100]}",
                              {"from": sender, "text": TAG.sub('', text).strip()[:100]}, kind="chat")
            self.save()
            return
        key = None
        if topic in REPLY:
            reply = REPLY[topic]
        elif step % 2 and topic in self.topics and self.phrases.get(self.topics[topic]["reply"]):
            reply, key = topic, self.topics[topic]["reply"]  # ORG-066: на тему собеседника (шаг 1/3) — ответ по ней
        elif nxt >= LAST_STEP:
            reply = "bye"
        elif topic == "hello" and nxt == 2:
            reply = "hello"
        else:
            reply = None                         # тема по фактам — выбирается в момент ответа
        lo, hi = self.cfg["reply_delay_seconds"]
        self.queue.append((now + self.rng.uniform(lo, hi), sender, reply, nxt, key))
        self.save()

    # ---------- реакции на события ----------

    def react_due(self, key, now):
        gap = self.cfg["react_gap_minutes"] * 60
        if now - self.st["react"].get(key, 0) < gap:
            return False
        self.st["react"][key] = now
        return True

    async def on_level_up(self, event):
        """Мой новый уровень — рассказать жителям (без LLM; с LLM повод уже дал gate)."""
        now = self.clock()
        level = event.get("level")
        if not level or self.st.get("level_told") == level or self.llm():
            return
        self.st["level_told"] = level
        await self.emote("level", now)
        for peer in sorted(self.mind.ctx.peers):
            if self.quarrel(peer):                        # society: с ним в ссоре — не хвастаюсь
                continue
            if self.react_due(f"level:{peer}", now):
                await self.say(peer, "level", 3, now)
        self.save()

    async def watch_peer_levels(self, now, state):
        """Уровень жителя вырос (по данным игры о видимых игроках) — поздравить."""
        for p in state.get("players") or []:
            if not isinstance(p, dict) or p.get("name") not in self.mind.ctx.peers or not p.get("lv"):
                continue
            name, lv = p["name"], int(p["lv"])
            old = self.st["peer_lv"].get(name)
            self.st["peer_lv"][name] = lv
            if old and lv > old:
                self.mind.mem.remember(f"{name} дорос до {lv} уровня — видел сам.", 2)
                if not self.quarrel(name) and self.react_due(f"congrats:{name}", now):   # society: в ссоре — молчу
                    if self.llm():
                        self.mind.trigger(f"{name} (житель) достиг {lv} уровня — можно поздравить",
                                          {"from": name}, kind="chat")
                    else:
                        await self.say(name, "congrats", LAST_STEP, now)
                self.save()

    async def on_peer_dead(self, sender):
        """Сигнал [party:dead] от жителя: посочувствовать (факт гибели party.py уже записал)."""
        now = self.clock()
        if sender not in self.mind.ctx.peers or not self.react_due(f"condolence:{sender}", now):
            return
        if self.llm():
            self.mind.trigger(f"{sender} (житель) погиб — можно посочувствовать", {"from": sender}, kind="chat")
            return
        await self.say(sender, "condolence", LAST_STEP, now)

    async def on_support(self, event):
        """Житель вылечил меня (пакет сервера) — поблагодарить, не чаще react_gap."""
        frm, to = event.get("from"), event.get("to")
        if event.get("skill") != "AL_HEAL" or not event.get("amount") or to != self.me or frm == self.me:
            return
        now = self.clock()
        if frm not in self.mind.ctx.peers or not self.react_due(f"thanks:{frm}", now):
            return
        if self.llm():
            self.mind.trigger(f"{frm} (житель) вылечил меня на {event.get('amount')} HP", {"from": frm}, kind="chat")
            return
        await self.say(frm, "thanks", LAST_STEP, now)

    # ---------- отношения по поступкам ----------

    def together(self, now, state, gap):
        """AUT-102: от together_minutes рядом за сутки — affinity +1, не чаще раза в сутки."""
        if not 0 < gap <= 5:
            return
        day = self.today(now)
        tg = self.st["together"]
        if tg.get("day") != day:
            tg.clear()
            tg["day"] = day
        near = set(self.near_peers(state))
        for m in state.get("party_members") or []:
            if (isinstance(m, dict) and m.get("name") in self.mind.ctx.peers and m.get("online")
                    and m.get("map") == state.get("map") and m.get("x") is not None and state.get("x") is not None
                    and dist(int(m["x"]), int(m["y"]), int(state["x"]), int(state["y"])) <= 12):
                near.add(m["name"])
        for peer in near:
            tg[peer] = tg.get(peer, 0) + gap
            if tg[peer] >= self.cfg["together_minutes"] * 60 and self.st["bonded"].get(peer) != day:
                self.st["bonded"][peer] = day
                # soak: жители видятся каждый день — +1 в сутки без предела за 10 суток уводил ВСЕ пары в +10
                # (максимум memory), отношения переставали различаться; выше together_cap растят только поступки
                if (self.mind.mem.relation(peer) or {}).get("affinity", 0) >= self.cfg.get("together_cap", 6):
                    self.mind.mem.touch_relation(peer)          # soak: встреча — та же (остывание не начнётся)
                    self.save()
                    continue
                self.mind.mem.update_relation(peer, 1, "провели время вместе")
                self.mind.mem.remember(f"Сегодня провёл с {peer} больше {self.cfg['together_minutes']} мин рядом.", 2)
                self.mind.mem.add_event("social_together", {"peer": peer, "minutes": int(tg[peer] / 60)})
                self.mind.write_decision({"type": "social", "event": "together", "peer": peer})
                log.info("время вместе с %s: отношение +1", peer)
                self.save()
