"""Наставничество новичков (ORG-057, ТЗ Т-22). Правила без LLM.

Новичок — житель, который недавно родился (roster.json born моложе newborn_days), Novice или ещё низкого уровня
(< seek_level), и уровень ещё не выпускной (< graduate_level). Наставник — житель не новичок, уровень которого выше
уровня новичка на level_gap, щедрый (generosity ≥ min_generosity), не в ссоре, без другого ученика.

Уровни чужих жителей мозгу неизвестны, поэтому новичок о себе сообщает сам:
    шина:  mentee_seek {lv, job, job_lv, born}  — тихий снимок (затирание), пока наставника нет;
    шёпот: [mentor:lv:<lv>:<job_lv>:<job>]      — ученик наставнику при согласии, новом уровне, смене профессии.
Видимый рядом житель с уровнем из state.players тоже кандидат (без шины).

Протокол (шёпот жителю, метка в конце):
    [mentor:offer]            наставник -> новичок        [mentor:ok] / [mentor:no]   ответ
    [mentor:tip:<тема>]       совет по фактам (hunt — атлас по уровню и профессии ученика, kafra, potion — магазин
                              Red Potion, job — путь профессии из progression.json); ученик запоминает совет
    [mentor:gift:<id>:<n>]    наставник предлагает зелья; ученику не хватает (< gift_below) — обычная просьба
                              economy [need:] именно наставнику (economy.ask(to=...)), «получил» — по сделке сервера
    [mentor:cheer]            поздравление с вехой (milestones, смена профессии)
    [mentor:grad] / [mentor:end]   выпуск (уровень ученика ≥ graduate_level) / конец опеки (срок term_days)
Охота вместе — группа party/crew (карта по слабейшему, crew.group_choice): ученик в подтверждённой группе на моей
карте во время охоты — событие mentor_hunt (раз в сутки). Пары вне одной группы модуль не переставляет.

Связь — kv mentor обоих (role mentor|mentee, peer, since, …) и отношение (+1 при согласии, +2 при выпуске).
События: mentor_start / mentor_graduated / mentor_end (шина мира и летопись), mentor_found, mentor_tip,
mentor_gift_offer, mentor_cheer, mentor_hunt, mentor_done (ученик). Новичков нет — модуль спит (ничего не шлёт).
Выключатель: BRAIN_DISABLE=mentor или goals.json "mentor": {"enabled": false}.
"""
import json
import logging
import re
import time
from datetime import datetime, timezone

from . import progression
from .world_bus import WorldBus

log = logging.getLogger("mentor")

TAG = re.compile(r"\[mentor:(offer|ok|no|lv|tip|gift|cheer|grad|end)((?::[A-Za-z0-9_]{1,16}){0,3})\]")
DEFAULTS = {"enabled": True, "tick_seconds": 10, "newborn_days": 14, "seek_level": 20, "graduate_level": 25,
            "term_days": 10, "level_gap": 15, "min_generosity": 0.5, "offer_gap_hours": 12, "answer_seconds": 180,
            "tip_gap_minutes": 30, "tip_band": 5, "gift_gap_hours": 24, "gift_item": "501", "gift_below": 5,
            "seek_minutes": 30, "seek_fresh_minutes": 90, "milestones": [10, 15, 20], "town": "prontera"}
HOSTILE = -3
NAME_MAX = 23


def roster_born(name, world_dir=None):
    """Время рождения жителя (UTC-полночь поля born в brain/world/roster.json) или None."""
    world = progression.WORLD if world_dir is None else world_dir
    try:
        res = json.loads((world / "roster.json").read_text(encoding="utf-8")).get("residents") or {}
    except (OSError, ValueError):
        return None
    for r in res.values():
        if isinstance(r, dict) and (r.get("name") or "").lower() == (name or "").lower() and r.get("born"):
            try:
                return datetime.strptime(r["born"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
            except (TypeError, ValueError):
                return None
    return None


class Mentor:
    # реестр модулей (modules.py, W8): создание, тик, метка, промпт
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES, ARGS = "mentor", "mentor", "mentor", True, ("world", "peers"), "world"
    TICK_ORDER = 95                          # после crew (90): группа и карта уже решены в этом тике
    WARMUP = 60                 # warmup: необязательная инициатива — через 60–120 с после пробуждения (modules.py)
    TICK_EVERY = 10             # perf: реестр не зовёт tick до next_tick (modules.py)
    TAGS, TAG_ORDER = [(TAG, "on_tag")], 55  # после crew [crew:] (50), до guild (60)
    PROMPT = [("наставничество", "summary", 215)]

    def __init__(self, mind, world=None, clock=None, atlas=None, prog=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("mentor") or {}))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self._atlas = atlas
        self._prog = prog
        self.st = mind.mem.get("mentor") or {}
        for key, val in (("offers", {}), ("history", []), ("tips", {}), ("cheered", [])):
            self.st.setdefault(key, val)
        self.born_ts = roster_born(self.me())
        self.next_tick = 0.0
        self.last_seek = 0.0

    # ---------- данные ----------

    @property
    def state(self):
        return self.mind.state or {}

    def me(self):
        return (self.mind.state or {}).get("name") or self.mind.persona["name"]

    def atlas(self):
        if self._atlas is None:
            from . import atlas
            self._atlas = atlas.default()
        return self._atlas

    def prog(self):
        if self._prog is None:
            self._prog = progression.load()
        return self._prog

    def save(self):
        self.mind.mem.set("mentor", self.st)

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "mentor", "event": kind, "text": text, **data})
        log.info("%s", text)

    def bus(self):
        bus = getattr(getattr(self.mind, "world", None), "bus", None)
        return bus if isinstance(bus, WorldBus) else None          # только настоящая шина мира (world_bus.Feed)

    def trait(self, name):
        return (getattr(getattr(self.mind, "needs", None), "t", None) or {}).get(name, 0.5)

    def friendly(self, peer):
        society = getattr(self.mind, "society", None)
        if society and society.quarrel(peer):
            return False
        return (self.mind.mem.relation(peer) or {}).get("affinity", 0) > HOSTILE

    async def whisper(self, to, text, tag, reason):
        await self.mind.execute([{"action": "whisper", "to": to, "text": f"{text} {tag}".strip()}],
                                source="mentor", reason=f"наставничество: {reason}", protocol=True)

    @property
    def role(self):
        return self.st.get("role")

    @property
    def peer(self):
        return self.st.get("peer")

    # ---------- роли ----------

    def lv(self):
        try:
            return int(self.state.get("lv") or 0)
        except (TypeError, ValueError):
            return 0

    def newbie(self, now=None):
        """Я новичок: недавно родился (born), Novice или уровень < seek_level; выпускной уровень — уже нет."""
        lv = self.lv()
        if not lv or lv >= self.cfg["graduate_level"]:
            return False
        now = now or self.clock()
        if self.born_ts and 0 <= now - self.born_ts <= self.cfg["newborn_days"] * 86400:
            return True
        return self.state.get("job") == "Novice" or lv < self.cfg["seek_level"]

    def can_mentor(self):
        return (not self.role and not self.newbie() and self.lv() > 0
                and self.trait("generosity") >= self.cfg["min_generosity"])

    def candidates(self, now):
        """Новички без наставника: снимки mentee_seek в шине и видимые жители (уровень из state.players)."""
        out = {}
        bus = self.bus()
        if bus:
            try:
                rows = bus.latest("mentee_seek", now - self.cfg["seek_fresh_minutes"] * 60)
            except Exception as e:                                  # общая БД занята — повтор позже
                log.warning("шина мира: %s", e)
                rows = {}
            for name, r in rows.items():
                lv = (r.get("data") or {}).get("lv")
                if isinstance(lv, int) and lv > 0:
                    out[name] = lv
        for p in self.state.get("players") or []:
            if isinstance(p, dict) and p.get("name") in self.mind.ctx.peers and isinstance(p.get("lv"), int) \
                    and 0 < p["lv"] < self.cfg["seek_level"]:
                out.setdefault(p["name"], p["lv"])
        mine = self.lv()
        return sorted(((n, lv) for n, lv in out.items()
                       if n in self.mind.ctx.peers and n != self.me() and mine >= lv + self.cfg["level_gap"]
                       and self.friendly(n)), key=lambda x: (x[1], x[0]))

    # ---------- советы по фактам ----------

    def tips(self):
        """[(тема, метка ступени, текст)] — советы для уровня и профессии ученика (только данные атласа/прогрессии)."""
        lv, job, job_lv = self.st.get("lv"), self.st.get("job") or "Novice", self.st.get("job_lv")
        if not lv:
            return []
        out = []
        town = self.cfg["town"]
        try:
            from .atlas import archetype
            a = self.atlas()
            maps = a.suitable_maps(int(lv), archetype(job), around=town, top=1)
            if maps:
                hmap = maps[0]["map"]
                mob = self.common_mob(a, hmap)
                out.append(("hunt", str(int(lv) // self.cfg["tip_band"]),
                            f"Начни с {hmap}" + (f": там {mob}." if mob else ".")))
            shop = a.nearest_shop(int(self.cfg["gift_item"]), town)
            if shop:
                item = (a.items.get(str(self.cfg["gift_item"])) or {}).get("name") or "Red Potion"
                out.append(("potion", "1", f"{item} — у {shop['npc']} в {shop['map']}, {shop['price']}z."))
            kafra = a.nearest_kafra(town)
            if kafra:
                out.append(("kafra", "1", f"Kafra в {kafra['map']} у {kafra['x']},{kafra['y']} — сохраняйся."))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            log.warning("атлас недоступен: %s", e)
        job_tip = self.job_tip(job, job_lv)
        if job_tip:
            out.append(("job", job, job_tip))
        return out

    @staticmethod
    def common_mob(a, hmap):
        mobs = [(c, a.monsters[i]["name"]) for i, c in ((a.maps.get(hmap) or {}).get("monsters") or {}).items()
                if i in a.monsters and a.monsters[i].get("class") != "Boss"]
        return max(mobs)[1] if mobs else None

    def job_tip(self, job, job_lv):
        try:
            data = self.prog()
        except (OSError, ValueError):
            return None
        target = progression.target_job(self.peer) if job == "Novice" else None
        path = progression.path_for({"job": job}, data, target)
        info = (data.get("paths") or {}).get(path or "") or {}
        need = (info.get("requirements") or {}).get("job_lv")
        npcs = list((info.get("npcs") or {}).values())
        if not info or not need or not npcs:
            return None
        npc = str(npcs[0].get("name") or "").split("#")[0]
        return f"С job {need} иди к {npc} ({npcs[0].get('map')}): станешь {info.get('to')}."

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        if now < self.next_tick or not getattr(self.mind, "fresh_state", True) or self.state.get("dead"):
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        if self.role and now - self.st.get("since", now) >= self.cfg["term_days"] * 86400:
            await self.end("срок", notify=True)
            return
        if self.role == "mentee":
            await self.mentee_tick(now)
        elif self.role == "mentor":
            await self.mentor_tick(now)
        elif self.newbie(now):
            self.seek(now)
        elif self.can_mentor():
            await self.offer(now)

    def seek(self, now):
        bus = self.bus()
        if not bus or now - self.last_seek < self.cfg["seek_minutes"] * 60:
            return
        self.last_seek = now
        s = self.state
        try:
            bus.replace("mentee_seek", {"lv": self.lv(), "job": s.get("job"), "job_lv": s.get("job_lv"),
                                        "born": bool(self.born_ts)}, 1, now=now)
        except Exception as e:
            log.warning("шина мира: %s", e)

    def unseek(self):
        bus = self.bus()
        if bus:
            try:
                bus.db.execute("DELETE FROM world_events WHERE bot = ? AND kind = 'mentee_seek'", (bus.bot,))
            except Exception as e:
                log.warning("шина мира: %s", e)

    async def offer(self, now):
        pend = self.st.get("pending")
        if pend:
            if now - pend["ts"] < self.cfg["answer_seconds"]:
                return
            self.st["pending"] = None
            self.save()
        for name, lv in self.candidates(now):
            if now - self.st["offers"].get(name, 0) < self.cfg["offer_gap_hours"] * 3600:
                continue
            self.st["offers"][name] = now
            self.st["pending"] = {"peer": name, "lv": lv, "ts": now}
            self.save()
            self.mind.write_decision({"type": "mentor", "event": "offer", "peer": name, "lv": lv})
            await self.whisper(name, f"{name}, давай помогу освоиться?", "[mentor:offer]", f"беру под крыло {name}")
            return

    async def report(self, force=False):
        """Ученик: свой уровень и профессия наставнику — при изменении (или force)."""
        s = self.state
        cur = [self.lv(), int(s.get("job_lv") or 0), str(s.get("job") or "Novice")]
        if not cur[0] or (cur == self.st.get("reported") and not force):
            return
        self.st["reported"] = cur
        self.save()
        job = re.sub(r"[^A-Za-z0-9_]", "", cur[2])[:16] or "Novice"
        await self.whisper(self.peer, "", f"[mentor:lv:{cur[0]}:{cur[1]}:{job}]", f"мой уровень {cur[0]}")

    async def mentee_tick(self, now):
        await self.report()

    async def mentor_tick(self, now):
        if now - self.st.get("last_tip", 0) >= self.cfg["tip_gap_minutes"] * 60:
            for topic, mark, text in self.tips():
                if self.st["tips"].get(topic) == mark:
                    continue
                att = getattr(self.mind, "attention", None)            # attention: ORG-109 бюджет инициатив
                if att is not None and not att.may("mentor", self.peer, now):   # attention: совет позже
                    break                                              # attention:
                if att is not None:                                    # attention:
                    att.spend("mentor", self.peer, now)                # attention:
                self.st["tips"][topic] = mark
                self.st["last_tip"] = now
                self.save()
                self.note("mentor_tip_given", f"Посоветовал(а) {self.peer}: {text}", 1, peer=self.peer, topic=topic)
                await self.whisper(self.peer, text, f"[mentor:tip:{topic}]", f"совет {topic}")
                return
        await self.maybe_gift(now)
        self.joint_hunt(now)

    async def maybe_gift(self, now):
        econ = getattr(self.mind, "economy", None)
        item = str(self.cfg["gift_item"])
        rule = (econ.share.get(item) if econ else None) or {}
        if not rule or now - self.st.get("gift_ts", 0) < self.cfg["gift_gap_hours"] * 3600:
            return
        n = int(rule.get("ask") or 0)
        if n <= 0 or econ.have(item) < int(rule.get("keep") or 0) + n:
            return
        self.st["gift_ts"] = now
        self.save()
        self.note("mentor_gift_offer", f"Предложил(а) {self.peer} {n} {econ.item_name(item)}.", 1,
                  peer=self.peer, item=item, amount=n)
        await self.whisper(self.peer, "Зелья нужны? Поделюсь.", f"[mentor:gift:{item}:{n}]", "подарок ученику")

    def joint_hunt(self, now):
        party, r = getattr(self.mind, "party", None), getattr(self.mind, "routine", None)
        if not party or not party.st.get("confirmed") or not r or (r.st or {}).get("mode") != "hunt":
            return
        mate = party.member(self.peer)
        here = self.state.get("map")
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        if not mate or not mate.get("online") or mate.get("map") != here or self.st.get("hunt_day") == day:
            return
        self.st["hunt_day"] = day
        self.st["hunts"] = self.st.get("hunts", 0) + 1
        self.save()
        self.note("mentor_hunt", f"Охочусь вместе с учеником {self.peer} на {here}.", 2, peer=self.peer, map=here)

    # ---------- метки ----------

    async def on_tag(self, sender, text):
        m = TAG.search(text or "")
        if not m:
            return
        kind, args = m.group(1), [a for a in m.group(2).split(":") if a]
        now = self.clock()
        if kind == "offer":
            await self.on_offer(sender, now)
        elif kind in ("ok", "no"):
            pend = self.st.get("pending") or {}
            if pend.get("peer") != sender or self.role:
                return
            self.st["pending"] = None
            if kind == "ok":
                self.st.update(role="mentor", peer=sender, since=now, lv=pend.get("lv"), job=None, job_lv=None,
                               tips={}, cheered=[], last_tip=0, gift_ts=0, hunts=0)
                self.mind.mem.update_relation(sender, 1, "мой ученик")
                self.note("mentor_start", f"Взял(а) под крыло {sender}.", 3, mentee=sender, lv=pend.get("lv"))
            self.save()
        elif self.peer != sender or not self.role:
            return
        elif kind == "lv" and self.role == "mentor" and len(args) == 3 and args[0].isdigit():
            await self.on_level(int(args[0]), int(args[1]) if args[1].isdigit() else None, args[2], now)
        elif kind == "tip" and self.role == "mentee":
            advice = TAG.sub("", text).strip()[:80]
            self.note("mentor_tip", f"{sender} советует: {advice}", 2, peer=sender, topic=args[0] if args else None)
        elif kind == "gift" and self.role == "mentee" and len(args) == 2:
            await self.on_gift(sender, args[0], int(args[1]) if args[1].isdigit() else 0)
        elif kind == "cheer" and self.role == "mentee":
            self.note("mentor_cheer", f"{sender} поздравил(а) меня.", 1, peer=sender)
        elif kind == "grad" and self.role == "mentee":
            self.mind.mem.update_relation(sender, 2, "мой наставник")
            self.finish("выпуск", f"Выпустился(лась) у наставника {sender}.", "mentor_done", 4)
        elif kind == "end":
            self.finish("срок", f"Опека с {sender} закончилась.", "mentor_end", 2)

    async def on_offer(self, sender, now):
        if self.role or not self.newbie(now) or not self.friendly(sender):
            await self.whisper(sender, "Спасибо, справлюсь сам(а).", "[mentor:no]", "отказ от наставника")
            return
        self.st.update(role="mentee", peer=sender, since=now, reported=None)
        self.save()
        self.unseek()
        self.mind.mem.update_relation(sender, 1, "мой наставник")
        self.note("mentor_found", f"{sender} взял(а) меня под крыло.", 3, mentor=sender)
        await self.whisper(sender, "С радостью!", "[mentor:ok]", f"наставник {sender}")
        await self.report(force=True)

    async def on_level(self, lv, job_lv, job, now):
        prev_job, prev_lv = self.st.get("job"), self.st.get("lv") or 0
        self.st.update(lv=lv, job_lv=job_lv, job=job)
        self.save()
        if lv >= self.cfg["graduate_level"]:
            await self.graduate(now)
            return
        cheer = None
        if prev_job and job != prev_job:
            cheer = f"Ты теперь {job}! Горжусь."
        else:
            hit = [x for x in self.cfg["milestones"] if prev_lv < x <= lv and x not in self.st["cheered"]]
            if hit:
                self.st["cheered"].append(max(hit))
                cheer = f"Уже {lv} уровень! Так держать."
        if cheer:
            att = getattr(self.mind, "attention", None)                # attention: ORG-109 бюджет инициатив
            if att is not None and not att.may("mentor", self.peer, now):   # attention: промолчал(а)
                self.save()                                            # attention:
                return                                                 # attention:
            if att is not None:                                        # attention:
                att.spend("mentor", self.peer, now)                    # attention:
            self.save()
            self.note("mentor_cheer_sent", f"Поздравил(а) {self.peer}: {cheer}", 1, peer=self.peer, lv=lv, job=job)
            await self.whisper(self.peer, cheer, "[mentor:cheer]", "поздравление ученику")

    async def on_gift(self, sender, item, n):
        econ = getattr(self.mind, "economy", None)
        if not econ or n <= 0 or econ.have(item) >= self.cfg["gift_below"] or econ.req:
            self.mind.write_decision({"type": "mentor", "event": "gift_skip", "peer": sender, "item": item,
                                      "why": "запаса хватает" if econ and econ.have(item) >= self.cfg["gift_below"]
                                      else "нет economy или уже прошу"})
            return
        why = await econ.ask(item, n, force=True, remote=True, to=sender)
        self.mind.write_decision({"type": "mentor", "event": "gift_ask", "peer": sender, "item": item, "amount": n,
                                  "why": why})

    async def graduate(self, now):
        mentee, days = self.peer, round((now - self.st.get("since", now)) / 86400, 1)
        self.mind.mem.update_relation(mentee, 2, "мой выпускник")
        await self.whisper(mentee, f"{mentee}, ты уже {self.st.get('lv')}! Ты справишься сам(а).", "[mentor:grad]",
                           "выпуск ученика")
        self.finish("выпуск", f"Мой ученик {mentee} вырос до {self.st.get('lv')} — выпуск!", "mentor_graduated", 4,
                    mentee=mentee, lv=self.st.get("lv"), days=days)

    async def end(self, why, notify=False):
        peer = self.peer
        if notify and peer:
            await self.whisper(peer, "Опека закончилась, удачи!", "[mentor:end]", "конец опеки")
        self.finish(why, f"Опека с {peer} закончилась ({why}).", "mentor_end", 2)

    def finish(self, why, text, kind, importance, **data):
        peer, role = self.peer, self.role
        self.st["history"] = (self.st["history"] + [{"peer": peer, "role": role, "since": self.st.get("since"),
                                                     "end": self.clock(), "why": why}])[-20:]
        for key in ("role", "peer", "since", "lv", "job", "job_lv", "reported", "pending"):
            self.st[key] = None
        self.save()
        self.note(kind, text, importance, **dict({"peer": peer, "role": role, "why": why}, **data))

    def summary(self):
        if not self.role:
            return None
        out = {"роль": "наставник" if self.role == "mentor" else "ученик", "с_кем": self.peer}
        if self.role == "mentor" and self.st.get("lv"):
            out["уровень_ученика"] = self.st["lv"]
        return out


CHRONICLE_LINES = {
    "mentor_start": lambda d: f"взял(а) под крыло {d.get('mentee')}",
    "mentor_found": lambda d: f"нашёл(шла) наставника: {d.get('mentor')}",
    "mentor_graduated": lambda d: f"выпуск ученика {d.get('mentee')} ({d.get('lv')} ур.)",
    "mentor_done": lambda d: f"выпустился(лась) у наставника {d.get('peer')}",
    "mentor_end": lambda d: f"опека с {d.get('peer')} закончилась ({d.get('why')})",
    "mentor_hunt": lambda d: f"охотится с учеником {d.get('peer')} на {d.get('map')}",
}
