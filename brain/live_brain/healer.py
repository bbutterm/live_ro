"""Ремесло-роль: лекарь у собора (ORG-069, ТЗ Т-15). Правила без LLM, тик 1 с.

Житель с профессией ветки Acolyte и щедрым характером (generosity ≥ min_generosity) — «лекарь»: иногда (занятие
`healer_post` каталога activities.json) встаёт у собора Пронтеры, вешает вывеску «Лечу у собора» (чат-комната
society, ORG-026) и лечит тех, кто попросил. Остальные жители — «пациенты»: с низким HP в городе просят лекаря
или заходят к нему. Платы нет: лекарь ничего не просит и ничего не продаёт за лечение.

Что известно телу (сверено с upstream): HP чужого игрока клиент не знает — OpenKore видит HP только участников
группы (AI/CoreLogic.pm smartHeal, Misc.pm checkPlayerCondition), а их и так лечит partySkill профиля. Поэтому
лечение — по просьбе:
    житель (знает свой HP)  -> шёпот лекарю «Vera, подлечишь? HP 45% [heal:ask:45]»;
    человек                  -> шёпот или общий чат рядом со словом heal/хил/лечи/вылечи/подлечи.
Слово человека выбирает только «лечить отправителя»: навык (Heal) и цель фиксированы, текст не хранится.
Каст — действие моста skill_on_player {skill, to} (OpenKore `sp <id> <номер игрока>`, Commands.pm cmdUseSkill);
мост сам выходит из чат-комнаты (rAthena clif_parse_skill_toid: chatID) и проверяет видимость и дальность 9 клеток.
Факт — событие support (пакет сервера skilluse, brainBridge onSkillUse) с моим именем источником.

Лекарь: просьба в очередь, если я на посту, цель видна не дальше range клеток, жителю — HP по его словам ниже
heal_below; каст не чаще cast_gap_seconds, одному — не чаще person_gap_seconds (человеку stranger_gap_seconds),
SP ≥ min_sp %, за сутки не больше heals_per_day. После подтверждённого Heal при SP ≥ bless_sp % — Blessing и
Increase AGI тому же (только выученные: state.support_skills), не чаще bless_gap_minutes одному.
Пост: на посту — тихий снимок шины healer_post {map, x, y, until} (затирание); начало смены — событие
healer_post_start, конец — healer_shift {heals, blesses, patients, minutes} (память, шина, летопись).
Пациент: режим town, HP < ask_below, лекарь на посту по шине (снимок не старше POST_FRESH), не в ссоре:
лекарь виден не дальше range — шёпот-просьба (не чаще ask_gap_seconds); не виден и HP < visit_below — прогулка к
посту (social.visit, не чаще visit_gap_minutes). Ответ [heal:no] — пауза no_backoff_minutes. Вылечили у собора —
память и отношение +1 (раз в сутки на лекаря); «спасибо» уже говорит social.py (событие support).
Чаевые: лекарь их не просит; сделки от людей OpenKore принимает только от жителей (dealAuto_names), поэтому
чаевые людей сейчас не принимаются.
Конфигурация — раздел "healer" brain/world/goals.json (необязателен); BRAIN_DISABLE=healer.
"""
import logging
import random
import re
import time

from .safety import CHAT_ROOM_GAP, fit_text

log = logging.getLogger("healer")

TAG = re.compile(r"\[heal:(ask|no)(?::(\d{1,3}))?\]")
# Просьба человека: отдельное слово (не часть другого), латиница или кириллица.
ASK_WORDS = re.compile(r"(?<![\w])(heal|хил|хильни|хилни|лечи|вылечи|подлечи|полечи)(?![\w])", re.I)
HEALER_JOBS = ("Acolyte", "Priest", "Monk", "High Priest", "Champion", "Arch Bishop", "Sura",
               "Acolyte High", "Baby Acolyte", "Baby Priest", "Baby Monk")
BLESS = ("AL_BLESSING", "AL_INCAGI")
POST_FRESH = 600          # снимок поста в шине моложе 10 мин
REQUEST_TTL = 60          # просьба в очереди не дольше минуты
SNAPSHOT_EVERY = 120      # снимок поста в шину
DEFAULTS = {
    "enabled": True,
    "role": "auto",                  # auto | healer | patient
    "min_generosity": 0.6,
    "post": {"map": "prontera", "x": 237, "y": 310, "label": "к собору"},
    "post_minutes": [20, 40],
    "post_cells": 4,                 # «у поста» — не дальше
    "sign": "Лечу у собора",
    "range": 9,                      # Range у Heal/Blessing/Inc AGI (db/pre-re/skill_db.yml)
    "heal_below": 70,
    "min_sp": 30,
    "bless_sp": 60,
    "cast_gap_seconds": 5,
    "person_gap_seconds": 60,
    "stranger_gap_seconds": 120,
    "bless_gap_minutes": 30,
    "heals_per_day": 150,
    "ask_below": 70,
    "visit_below": 60,
    "ask_gap_seconds": 180,
    "visit_gap_minutes": 30,
    "no_backoff_minutes": 30,
}


def dist(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


class Healer:
    # реестр модулей (modules.py, W8): создание, тик, подписки (метка [heal:], support, просьбы людей)
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES, ARGS = "healer", "healer", "healer", True, ("world",), "world"
    TICK_ORDER = 135                         # после society (130): вывеска уже решена в этом тике
    TAGS, TAG_ORDER = [(TAG, "on_tag")], 65  # после guild (60), до party (70)
    EVENTS, EVENT_ORDER = {"support": "on_support", "chat_private": "on_chat", "chat_public": "on_chat"}, 25

    def __init__(self, mind, world=None, clock=None, rng=None):
        self.mind = mind
        cfg = dict(DEFAULTS, **((world or {}).get("healer") or {}))
        if "post" not in ((world or {}).get("healer") or {}):
            church = (((world or {}).get("social") or {}).get("points") or {}).get("church")
            if church:                                          # точка «к собору» social (goals.json)
                cfg["post"] = {k: church[k] for k in ("map", "x", "y", "label") if k in church}
        self.cfg = cfg
        self.post = cfg["post"]
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.st = mind.mem.get("healer") or {}
        self.queue = []              # просьбы: {name, who: resident|stranger, skill, ts}
        self.last_cast = 0.0
        self.last_person = {}        # имя -> время последнего Heal
        self.last_bless = {}         # имя -> время последнего благословения
        self.last_snapshot = 0.0
        self.last_patient_check = 0.0

    # ---------- данные ----------

    @property
    def state(self):
        return self.mind.state

    def me(self):
        return self.state.get("name") or self.mind.persona["name"]

    def save(self):
        self.mind.mem.set("healer", self.st)

    def note(self, kind, text, importance, **data):
        if text:
            self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "healer", "event": kind, "text": text, **data})
        log.info("%s", text or kind)

    def bus(self):
        feed = getattr(self.mind, "world", None)
        return getattr(feed, "bus", None) if feed else None

    def town(self):
        r = getattr(self.mind, "routine", None)
        return bool(r and r.in_town_mode)

    def known(self, skill):
        """Навык выучен (state.support_skills от моста); поля нет (старый мост) — считаем, что да."""
        skills = self.state.get("support_skills")
        return not isinstance(skills, dict) or bool(skills.get(skill))

    def role(self):
        mode = self.cfg.get("role", "auto")
        if mode in ("healer", "patient"):
            return mode
        job = str(self.state.get("job") or self.mind.persona.get("job") or "")
        gen = float(((self.mind.persona.get("traits") or {}).get("generosity", 0.5)))
        if job in HEALER_JOBS and gen >= self.cfg["min_generosity"] and self.known("AL_HEAL"):
            return "healer"
        return "patient"

    def is_healer(self):
        return self.role() == "healer"

    def player(self, name, state=None):
        state = state or self.state
        for p in state.get("players") or []:
            if isinstance(p, dict) and p.get("name") == name and p.get("x") is not None:
                return p
        return None

    def in_range(self, name, state=None):
        state = state or self.state
        p = self.player(name, state)
        return bool(p and state.get("x") is not None
                    and dist(int(p["x"]), int(p["y"]), int(state["x"]), int(state["y"])) <= self.cfg["range"])

    def today(self, now):
        return time.strftime("%Y-%m-%d", time.localtime(now))

    def heals_today(self, now):
        day = self.st.get("day") or {}
        return int(day.get("heals", 0)) if day.get("date") == self.today(now) else 0

    def count_today(self, key, now):
        day = self.st.get("day") or {}
        if day.get("date") != self.today(now):
            day = {"date": self.today(now)}
        day[key] = int(day.get(key, 0)) + 1
        self.st["day"] = day

    # ---------- лекарь: занятие и пост ----------

    def at_post(self, state=None):
        state = state or self.state
        p = self.post
        return (state.get("map") == p["map"] and state.get("x") is not None
                and dist(int(state["x"]), int(state["y"]), int(p["x"]), int(p["y"])) <= self.cfg["post_cells"])

    def activity_on(self):
        act = getattr(self.mind, "activities", None)
        return bool(act and (act.st or {}).get("name") == "healer_post")

    def on_post(self, state=None, now=None):
        now = now if now is not None else self.clock()
        shift = self.st.get("post")
        return bool(self.is_healer() and shift and now < shift["until"] and self.activity_on() and self.town()
                    and self.at_post(state))

    def can_post(self, state):
        """Условие занятия healer_post: я лекарь, в городе поста, SP хватает на работу."""
        if not self.is_healer() or state.get("dead") or state.get("map") != self.post["map"]:
            return False
        sp = state.get("sp_pct")
        return sp is None or sp >= self.cfg["bless_sp"]

    async def start_post(self):
        """Исполнитель занятия: к точке поста, стоять там post_minutes (прогулки social — после смены)."""
        now = self.clock()
        lo, hi = self.cfg["post_minutes"]
        until = now + self.rng.uniform(lo, hi) * 60
        social = getattr(self.mind, "social", None)
        p = self.post
        if not social or not await social.visit(p["map"], p["x"], p["y"], p.get("label", "к собору")):
            self.mind.write_decision({"type": "healer", "event": "post_skip", "why": "не могу идти к посту"})
            return False
        social.next_walk = until                     # стоять у поста до конца смены (как вечерний круг)
        self.st["post"] = {"since": now, "until": until, "heals": 0, "blesses": 0, "patients": [], "opened": False}
        self.save()
        self.mind.write_decision({"type": "healer", "event": "post_start", "until_minutes": round((until - now) / 60, 1),
                                  "post": p})
        return True

    def sign(self, state, now):
        """Заголовок вывески для society.rooms или None: на посту, очередь пуста, после каста прошла минута,
        safety разрешит комнату (CHAT_ROOM_GAP) и недавно комната не срывалась."""
        if not self.on_post(state, now) or self.queue or now - self.last_cast < 60:
            return None
        if now - getattr(self.mind.safety, "room_opened", 0.0) < CHAT_ROOM_GAP:
            return None
        if self.mind.mem.count_events("society_room_failed", time.time() - 1800):
            return None
        return self.cfg["sign"]

    async def post_tick(self, now, state):
        shift = self.st.get("post")
        if not shift:
            return
        if now >= shift["until"] or not self.activity_on() or not self.town() or state.get("dead"):
            await self.end_post(now, "смена окончена" if now >= shift["until"] else "ушла с поста")
            return
        if not self.at_post(state):
            return
        if not shift.get("opened"):
            shift["opened"] = True
            self.save()
            self.note("healer_post_start", f"Встала лечить у собора ({self.post['map']} {self.post['x']},"
                      f"{self.post['y']}).", 2, map=self.post["map"], x=self.post["x"], y=self.post["y"])
        if now - self.last_snapshot >= SNAPSHOT_EVERY:
            self.snapshot(now, True)

    def snapshot(self, now, open_):
        bus = self.bus()
        if not bus:
            return
        self.last_snapshot = now
        shift = self.st.get("post") or {}
        try:
            bus.replace("healer_post", {"map": self.post["map"], "x": self.post["x"], "y": self.post["y"],
                                        "open": open_, "until": shift.get("until", now)}, 1, now=now)
        except Exception as e:                                      # общая БД занята — повторю позже
            log.warning("шина мира: %s", e)

    async def end_post(self, now, why):
        shift = self.st.pop("post", None) or {}
        self.save()
        self.queue = []
        self.snapshot(now, False)
        if not shift.get("opened"):
            self.mind.write_decision({"type": "healer", "event": "post_cancel", "why": why})
            return
        minutes = round((now - shift.get("since", now)) / 60)
        patients = sorted(set(shift.get("patients") or []))
        self.note("healer_shift", f"Лечила у собора {minutes} мин: Heal {shift.get('heals', 0)}, благословений "
                  f"{shift.get('blesses', 0)}, людей {len(patients)}.", 2 if shift.get("heals") else 1,
                  heals=shift.get("heals", 0), blesses=shift.get("blesses", 0), patients=len(patients),
                  minutes=minutes, why=why)

    # ---------- лекарь: просьбы и касты ----------

    def enqueue(self, name, who, skill="AL_HEAL", now=None):
        now = now if now is not None else self.clock()
        if any(q["name"] == name and q["skill"] == skill for q in self.queue):
            return False
        self.queue.append({"name": name, "who": who, "skill": skill, "ts": now})
        return True

    async def on_chat(self, event):
        """Просьба человека (шёпот или общий чат) — только на посту и только словом-просьбой; текст не храним."""
        name = event.get("from")
        if not name or name in self.mind.ctx.peers or name == self.me():
            return
        if not ASK_WORDS.search(str(event.get("text") or "")):
            return
        now = self.clock()
        if not self.on_post(now=now) or not self.in_range(str(name)):
            return
        if now - self.last_person.get(name, 0) < self.cfg["stranger_gap_seconds"]:
            return
        if self.enqueue(str(name), "stranger", now=now):
            self.mind.write_decision({"type": "healer", "event": "request", "from": name, "who": "stranger"})

    async def on_tag(self, sender, text):
        m = TAG.search(text or "")
        if not m:
            return
        kind, hp = m.group(1), m.group(2)
        now = self.clock()
        if kind == "no":                                            # я пациент: лекарь не может — не надоедать
            self.st["backoff_until"] = now + self.cfg["no_backoff_minutes"] * 60
            self.save()
            return
        if not self.is_healer() or not self.on_post(now=now):
            await self.whisper(sender, "Я сейчас не у собора. [heal:no]", "лекарь: не на посту")
            return
        hp = int(hp) if hp is not None else None
        if hp is not None and hp >= self.cfg["heal_below"]:
            await self.whisper(sender, f"{sender}, ты и так бодр. [heal:no]", "лекарь: HP в порядке")
            return
        if not self.in_range(sender):
            await self.whisper(sender, "Подойди ближе к собору. [heal:no]", "лекарь: далеко")
            return
        if self.enqueue(sender, "resident", now=now):
            self.mind.write_decision({"type": "healer", "event": "request", "from": sender, "who": "resident",
                                      "hp": hp})

    async def whisper(self, to, text, reason):
        await self.mind.execute([{"action": "whisper", "to": to, "text": fit_text(text)}], source="healer",
                                reason=reason, protocol=True)

    def why_not(self, q, now, state):
        """Почему эту просьбу сейчас не исполнить (None — можно)."""
        if now - q["ts"] > REQUEST_TTL:
            return "просьба устарела"
        if not self.in_range(q["name"], state):
            return "не вижу рядом"
        if not self.known(q["skill"]):
            return "навык не выучен"
        sp = state.get("sp_pct")
        need = self.cfg["min_sp"] if q["skill"] == "AL_HEAL" else self.cfg["bless_sp"]
        if sp is not None and sp < need:
            return f"SP {sp}% < {need}%"
        if q["skill"] == "AL_HEAL":
            gap = self.cfg["stranger_gap_seconds" if q["who"] == "stranger" else "person_gap_seconds"]
            if now - self.last_person.get(q["name"], 0) < gap:
                return "недавно лечила"
            if self.heals_today(now) >= self.cfg["heals_per_day"]:
                return "на сегодня хватит"
        return None

    async def cast_tick(self, now, state):
        if not self.queue or now - self.last_cast < self.cfg["cast_gap_seconds"]:
            return
        q = self.queue.pop(0)
        why = self.why_not(q, now, state)
        if why:
            self.mind.write_decision({"type": "healer", "event": "skip", "to": q["name"], "skill": q["skill"],
                                      "why": why})
            return
        self.last_cast = now
        if q["skill"] == "AL_HEAL":
            self.last_person[q["name"]] = now
        await self.mind.execute([{"action": "skill_on_player", "skill": q["skill"], "to": q["name"]}],
                                source="healer", reason=f"лекарь: {q['skill']} для {q['name']}", protocol=True)

    def on_support(self, event):
        """Пакет сервера: мой каст (счёт смены, благословение вслед) или меня вылечил лекарь у собора."""
        skill, frm, to = event.get("skill"), event.get("from"), event.get("to")
        me = self.me()
        now = self.clock()
        if frm == me and to and to != me:
            shift = self.st.get("post")
            if not shift or not self.is_healer():
                return                                              # лечение в бою (partySkill) — не смена
            who = "resident" if to in self.mind.ctx.peers else "stranger"
            if skill == "AL_HEAL" and event.get("amount"):
                shift["heals"] = shift.get("heals", 0) + 1
                shift["patients"] = sorted(set(shift.get("patients") or []) | {to})
                self.count_today("heals", now)
                self.save()
                self.mind.mem.add_event("healer_heal", {"to": to, "amount": int(event.get("amount") or 0), "who": who})
                sp = self.state.get("sp_pct")
                if (sp is None or sp >= self.cfg["bless_sp"]) and \
                        now - self.last_bless.get(to, 0) >= self.cfg["bless_gap_minutes"] * 60:
                    queued = [s for s in BLESS if self.known(s) and self.enqueue(to, who, s, now)]
                    if queued:
                        self.last_bless[to] = now
            elif skill in BLESS:
                shift["blesses"] = shift.get("blesses", 0) + 1
                self.save()
                self.mind.mem.add_event("healer_bless", {"to": to, "skill": skill, "who": who})
            return
        if to == me and frm in self.mind.ctx.peers and skill == "AL_HEAL" and event.get("amount"):
            post = self.healer_posts(now).get(frm)
            if not post:
                return                                              # не у собора (бой, группа) — это party.py
            thanked = self.st.setdefault("thanked", {})
            if thanked.get(frm) == self.today(now):
                return
            thanked[frm] = self.today(now)
            self.save()
            self.mind.mem.update_relation(frm, 1, "лечила меня у собора")
            self.note("healer_healed_me", f"{frm} вылечил(а) меня у собора на {event.get('amount')} HP — по пакету "
                      "сервера.", 3, healer=frm, amount=int(event.get("amount") or 0))

    # ---------- пациент ----------

    def healer_posts(self, now):
        """Открытые посты лекарей по шине: {лекарь: данные} (снимок свежий и смена не кончилась)."""
        bus = self.bus()
        if not bus:
            return {}
        try:
            snaps = bus.latest("healer_post", since=now - POST_FRESH)
        except Exception as e:
            log.warning("шина мира: %s", e)
            return {}
        return {bot: s["data"] for bot, s in snaps.items()
                if s["data"].get("open") and float(s["data"].get("until") or 0) > now and bot in self.mind.ctx.peers}

    async def patient_tick(self, now, state):
        if now - self.last_patient_check < 5:
            return
        self.last_patient_check = now
        r = getattr(self.mind, "routine", None)
        hp = state.get("hp_pct")
        if not self.town() or not r.st.get("arrived") or hp is None or hp >= self.cfg["ask_below"]:
            return
        if now < self.st.get("backoff_until", 0) or self.mind.plans.store.active():
            return
        society = getattr(self.mind, "society", None)
        posts = {k: v for k, v in self.healer_posts(now).items() if not (society and society.quarrel(k))}
        if not posts:
            return
        name = sorted(posts)[0]
        post = posts[name]
        if self.in_range(name, state):
            if now - self.st.get("last_ask", 0) < self.cfg["ask_gap_seconds"]:
                return
            self.st["last_ask"] = now
            self.save()
            await self.whisper(name, f"{name}, подлечишь? HP {hp}% [heal:ask:{hp}]", "пациент: прошу лекаря")
            self.mind.mem.add_event("healer_asked", {"healer": name, "hp": hp})
            return
        social = getattr(self.mind, "social", None)
        if hp >= self.cfg["visit_below"] or not social or state.get("map") != post.get("map"):
            return
        if now - self.st.get("last_visit", 0) < self.cfg["visit_gap_minutes"] * 60:
            return
        self.st["last_visit"] = now
        self.save()
        y = int(post["y"]) - 2                                  # рядом с постом, не в ту же клетку
        if await social.visit(post["map"], int(post["x"]), y, f"к лекарю {name} у собора"):
            self.note("healer_visit", None, 1, healer=name, hp=hp)

    # ---------- тик ----------

    async def tick(self):
        if not self.cfg.get("enabled", True) or not self.mind.fresh_state:
            return
        now = self.clock()
        state = self.state
        if state.get("dead"):
            self.queue = []
            return
        if self.is_healer():
            await self.post_tick(now, state)
            await self.cast_tick(now, state)
        elif self.mind.routine is not None:
            await self.patient_tick(now, state)


# Строки летописи (chronicle.LINES дополняется этим словарём).
CHRONICLE_LINES = {
    "healer_post_start": lambda d: f"встал(а) лечить у собора ({d.get('map')} {d.get('x')},{d.get('y')})",
    "healer_shift": lambda d: (f"лечил(а) у собора {d.get('minutes')} мин: Heal {d.get('heals')}, "
                               f"благословений {d.get('blesses')}, людей {d.get('patients')}"),
    "healer_healed_me": lambda d: f"{d.get('healer')} вылечил(а) у собора на {d.get('amount')} HP",
}
