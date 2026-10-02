"""Мини-босс группой (ORG-079): Vocal на prt_fild07/prt_fild04, Eclipse на prt_fild02. Правила без LLM.

ПО УМОЛЧАНИЮ ВЫКЛЮЧЕНО (goals.json "boss": {"enabled": false}): мини-босс опасен, в игре не проверено.

Данные (upstream rAthena): npc/re/mobs/fields/prontera.txt — Vocal (1088) на prt_fild04 (:63) и prt_fild07 (:88),
Eclipse (1093) на prt_fild02 (:41), по одному, респаун 1800000,1200000 → 30–50 мин. db/re/mob_db.yml: Vocal ур. 18,
HP 3317 (в renewal класс Normal, агрессивный); Eclipse ур. 31, HP 625, Class Boss. Уровень/HP — из атласа.

Тело (upstream OpenKore): целиться в монстра по имени нечем — «a <номер>» (Commands.pm cmdAttack) берёт номер из
monstersList, а мозг монстров не видит. Профили: attackAuto 2, mon_control.txt Vocal/Eclipse не перечисляет →
attack_auto 1 (Misc.pm mon_control), оба агрессивны — тело бьёт их, встретив. Поэтому поход — довести группу до
карты (экспедиция explore.py, действие explore → lockMap), охотиться там до респауна и ждать факта kill с именем
босса (хук target_died → событие моста kill {monster}).

Протокол (шёпот жителю, метка ≤ 78 символов):
    лидер группы (party.py, crew активна), в городе, днём:     [boss:ask:<Mob>:<карта>]   участникам рядом;
    участник — согласие по характеру и состоянию:              [boss:yes:<ур>:<hp>:<зелья>:<хил 0|1>]
                                                               или [boss:no:<причина>];
    лидер через answer_seconds оценивает команду (assess):     [boss:go:<Mob>:<карта>] согласившимся + экспедиция;
                                                               иначе «видели повод — обошли» (решение, память);
    победа у участника:                                        [boss:won:<Mob>] лидеру;
    лидер — группе:                                            [boss:done:won].
Оценка (assess): группа ≥ min_group (2); Σ уровней ≥ level_sum_factor (2) × уровень босса; уровень каждого ≥ уровень
    босса − level_gap; HP каждого ≥ min_hp (80); в группе есть хилер (Acolyte и потомки) или у каждого зелий
    ≥ min_potions (10); риск карты для слабейшего (atlas.danger_for без самого босса) < max_risk.
Согласие (willing): смелость ≥ min_bravery, иначе отказ «страшно»; после поражения (бан) — нет; «день осторожности»
    режиссёра (ORG-086) — порог смелости выше на 0.2; повод режиссёра boss_call — лидер предлагает охотнее.
Поход: explorer.start (стоянка search_minutes — покрывает респаун 30–50 мин), лидер не зовёт [explore:trip:]
    (explore.lead_group — строка boss:). Отступление — средствами экспедиции: тревога survival/danger/escape, смерть,
    HP < abort_hp → возврат в город.
Итог — только по фактам: kill с именем босса на карте похода → boss_killed (память, летопись), трофей
    collection.boss_trophy, шина boss_victory (важность 5, одна запись на группу), фраза в чат группы; смерть
    в походе → boss_failed (шина 3) и бан босса на fail_ban_hours; вернулись без победы → boss_missed.
Лимиты: не больше max_per_day походов в сутки, между походами gap_hours.
Выключатель: goals.json "boss": {"enabled": true} включает; BRAIN_DISABLE=boss выключает; нужны party, crew, explore.
"""
import json
import logging
import random
import re
import time

log = logging.getLogger("boss")

TAG = re.compile(r"\[boss:(ask|yes|no|go|won|done)((?::[A-Za-z0-9_]{1,16}){0,4})\]")
DEFAULTS = {
    "enabled": False,
    "targets": [{"mob": "Vocal", "id": 1088, "maps": ["prt_fild07", "prt_fild04"]},
                {"mob": "Eclipse", "id": 1093, "maps": ["prt_fild02"]}],
    "min_group": 2, "level_sum_factor": 2.0, "level_gap": 3, "min_hp": 80, "min_potions": 10,
    "max_risk": 0.35, "min_bravery": 0.3, "check_minutes": 20, "answer_seconds": 120,
    "search_minutes": [40, 55], "gap_hours": 6, "fail_ban_hours": 2, "max_per_day": 1, "victory_dedup_minutes": 30,
}
HEALERS = {"Acolyte", "Priest", "Monk", "High Priest", "Champion", "Arch Bishop", "Sura", "High Acolyte"}
HEAL_IDS = ("569", "501", "502", "503", "504")
PHRASES = {"won": ["Победили {mob}! Вот это бой!", "{mob} повержен(а)! Спасибо, команда!", "Есть {mob}! Мы сделали это."]}


class Boss:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "boss", "boss", "boss", False, "world"
    REQUIRES = ("world", "party", "crew", "explorer")
    TICK_ORDER = 115            # после explorer (110): итог похода видит уже закрытую экспедицию
    TAGS, TAG_ORDER = [(TAG, "on_tag")], 45
    EVENTS, EVENT_ORDER = {"kill": "on_kill"}, 70
    # Поля промпта нет: выключенный модуль иначе добавлял бы пустой ключ всем жителям (summary — для отчёта/тестов).

    def __init__(self, mind, world=None, clock=None, rng=None, atlas_obj=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("boss") or {}))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self._atlas = atlas_obj
        self.st = mind.mem.get("boss") or {}
        self.next_check = 0.0

    # ---------- данные ----------

    @property
    def atlas(self):
        if self._atlas is None:
            from . import atlas
            self._atlas = atlas.default()
        return self._atlas

    def save(self):
        self.mind.mem.set("boss", self.st)

    def me(self):
        return self.mind.state.get("name") or self.mind.persona.get("name")

    def trait(self, name):
        return (getattr(getattr(self.mind, "needs", None), "t", None) or {}).get(name, 0.5)

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "boss", "event": kind, "text": text, **data})
        log.info("%s", text)

    def target(self, mob):
        return next((t for t in self.cfg["targets"] if t["mob"].lower() == str(mob).lower()), None)

    def target_info(self, target):
        """Уровень и HP босса по атласу (id из конфига, иначе имя)."""
        info = self.atlas.monster_info(str(target.get("id") or target["mob"])) or {}
        return {"mob": target["mob"], "level": info.get("level"), "hp": info.get("hp"), "boss": info.get("boss")}

    def potions(self):
        items = self.mind.state.get("items") or {}
        return sum(int(items.get(i, 0) or 0) for i in HEAL_IDS)

    def healer(self):
        return 1 if self.mind.state.get("job") in HEALERS else 0

    def me_card(self):
        s = self.mind.state
        return {"name": self.me(), "lv": s.get("lv"), "hp": s.get("hp_pct"), "pots": self.potions(),
                "heal": self.healer()}

    def town(self):
        r = getattr(self.mind, "routine", None)
        return (r.cfg.get("town") or {}).get("map") if r and isinstance(getattr(r, "cfg", None), dict) else None

    def banned(self, mob, now):
        return now < (self.st.get("ban") or {}).get(mob, 0)

    def hunts_today(self, now):
        return sum(1 for ts in self.st.get("starts") or [] if now - ts < 86400)

    # ---------- оценка ----------

    def map_risk(self, hmap, level, info):
        """Риск карты для уровня по атласу без вклада самого босса (его оценивает assess)."""
        risk, why = self.atlas.danger_for(hmap, level)
        if info.get("boss") and info.get("level") and info["level"] > level:
            risk = max(0.0, 1 - (1 - risk) / 0.7)          # atlas.danger_for: босс выше уровня — множитель 0.7
        return round(risk, 3), why

    def ready(self, target=None):
        """Готов ли я сам: HP, зелья или хилер, уровень. (bool, почему)."""
        c, cfg = self.me_card(), self.cfg
        if not isinstance(c["hp"], (int, float)) or c["hp"] < cfg["min_hp"]:
            return False, "hp"
        if not c["heal"] and c["pots"] < cfg["min_potions"]:
            return False, "pots"
        if target:
            info = self.target_info(target)
            if info.get("level") and (not isinstance(c["lv"], int) or c["lv"] < info["level"] - cfg["level_gap"]):
                return False, "lv"
        return True, "ok"

    def assess(self, target, hmap, team):
        """Команда по силам? team — [{name, lv, hp, pots, heal}]. (bool, почему)."""
        cfg = self.cfg
        info = self.target_info(target)
        lvl = info.get("level")
        if not lvl:
            return False, f"{target['mob']}: нет в атласе"
        if len(team) < cfg["min_group"]:
            return False, f"нас {len(team)} — нужно не меньше {cfg['min_group']}"
        levels = [m.get("lv") for m in team]
        if not all(isinstance(lv, int) and lv > 0 for lv in levels):
            return False, "уровень кого-то неизвестен"
        if sum(levels) < cfg["level_sum_factor"] * lvl:
            return False, f"сумма уровней {sum(levels)} < {cfg['level_sum_factor']:g}×{lvl}"
        if min(levels) < lvl - cfg["level_gap"]:
            return False, f"слабейшему {min(levels)} ур. — против {lvl} рано"
        weak = [m["name"] for m in team if not isinstance(m.get("hp"), (int, float)) or m["hp"] < cfg["min_hp"]]
        if weak:
            return False, f"HP ниже {cfg['min_hp']}%: {', '.join(weak)}"
        if not any(m.get("heal") for m in team):
            poor = [m["name"] for m in team if (m.get("pots") or 0) < cfg["min_potions"]]
            if poor:
                return False, f"нет хилера, мало зелий: {', '.join(poor)}"
        risk, why = self.map_risk(hmap, min(levels), info)
        if risk >= cfg["max_risk"]:
            return False, f"риск {hmap} {risk:.2f} ≥ {cfg['max_risk']}: " + "; ".join(why)
        return True, f"{len(team)} чел., Σ ур. {sum(levels)} против {lvl}, риск {risk:.2f}"

    def willing(self, eager=False):
        """Согласие по характеру: смелость против порога (выше в «день осторожности» режиссёра)."""
        director = getattr(self.mind, "director", None)
        bar = self.cfg["min_bravery"] + (0.2 if director and director.active("caution") else 0.0)
        bravery = self.trait("bravery")
        if bravery < bar:
            return False
        p = 0.4 + bravery + (0.3 if eager else 0.0)
        return self.rng.random() < p

    # ---------- лидер: предложить и решить ----------

    def can_start(self, now):
        """None — можно звать; иначе почему нельзя. Общие условия похода для лидера и участника."""
        m, state = self.mind, self.mind.state
        explorer = getattr(m, "explorer", None)
        if not explorer or explorer.trip:
            return "экспедиция уже идёт"
        if self.st.get("hunt"):
            return "поход уже идёт"
        r = getattr(m, "routine", None)
        if not r or not r.st or r.st.get("mode") != "town" or state.get("map") != self.town():
            return "не в городе отдыха"
        if m.plans.store.active():
            return "план встречи"
        social = getattr(m, "social", None)
        if social and social.is_night(now):
            return "ночь"
        return None

    async def tick(self):
        now = self.clock()
        state = self.mind.state
        if not getattr(self.mind, "fresh_state", True):
            return
        if self.st.get("hunt"):
            await self.follow_up(now)
            return
        crew, party = getattr(self.mind, "crew", None), getattr(self.mind, "party", None)
        if not (crew and party and crew.active() and party.is_leader) or state.get("dead"):
            return
        ask = self.st.get("ask")
        if ask:
            answered = set(ask.get("answers") or {}) >= set(ask.get("asked") or [])
            if now >= ask["deadline"] or answered:
                await self.decide(now, ask)
            return
        if now < self.next_check:
            return
        self.next_check = now + self.cfg["check_minutes"] * 60
        await self.propose(now)

    async def propose(self, now):
        cfg = self.cfg
        if self.can_start(now):
            return
        if self.hunts_today(now) >= cfg["max_per_day"] or now - (self.st.get("starts") or [0])[-1] < cfg["gap_hours"] * 3600:
            return
        party = self.mind.party
        here = [m["name"] for m in party.members() if m.get("online") and m.get("map") == self.mind.state.get("map")]
        if len(here) + 1 < cfg["min_group"]:
            return
        director = getattr(self.mind, "director", None)
        eager = bool(director and director.active("boss_call"))
        for target in cfg["targets"]:
            if self.banned(target["mob"], now):
                continue
            ok, _ = self.ready(target)
            if not ok:
                continue
            hmap = self.pick_map(target)
            if not hmap:
                continue
            if not self.willing(eager):
                self.mind.write_decision({"type": "boss", "event": "boss_not_today", "mob": target["mob"]})
                return
            self.st["ask"] = {"mob": target["mob"], "map": hmap, "ts": now, "deadline": now + cfg["answer_seconds"],
                              "asked": sorted(here), "answers": {}}
            self.save()
            self.mind.write_decision({"type": "boss", "event": "boss_ask", "mob": target["mob"], "map": hmap,
                                      "asked": sorted(here), "eager": eager})
            for name in sorted(here):
                await self.whisper(name, f"[boss:ask:{target['mob']}:{hmap}]", f"зову {name} на {target['mob']}")
            return

    def pick_map(self, target):
        """Карта босса, достижимая экспедицией (explore_reach.json) и не исключённая; первая по списку."""
        explorer = self.mind.explorer
        bans = self.mind.routine.bans() if getattr(self.mind, "routine", None) else set()
        for hmap in target["maps"]:
            if hmap in bans or not explorer.reach_maps().get(hmap) or hmap not in self.atlas.maps:
                continue
            return hmap
        return None

    async def decide(self, now, ask):
        self.st.pop("ask", None)
        target = self.target(ask["mob"])
        yes = {n: a for n, a in (ask.get("answers") or {}).items() if a.get("yes")}
        team = [self.me_card()] + [dict(a, name=n) for n, a in sorted(yes.items())]
        ok, why = self.assess(target, ask["map"], team) if target else (False, "нет такой цели")
        if ok:
            ok, why2 = self.ready(target)
            why = why if ok else f"сам не готов: {why2}"
        if not ok or self.can_start(now):
            why = why if not ok else self.can_start(now)
            self.save()
            no = {n: a.get("why") for n, a in (ask.get("answers") or {}).items() if not a.get("yes")}
            self.note("boss_declined", f"Думали сходить на {ask['mob']} ({ask['map']}), но {why} — обошли.", 1,
                      mob=ask["mob"], map=ask["map"], why=why, refused=no)
            return
        names = [m["name"] for m in team[1:]]
        started = await self.start_trip(now, target, ask["map"], role="leader", team=[self.me()] + names)
        if not started:
            return
        for name in names:
            await self.whisper(name, f"[boss:go:{ask['mob']}:{ask['map']}]", f"идём на {ask['mob']}")
        self.note("boss_start", f"Идём группой на {ask['mob']} ({ask['map']}): {', '.join([self.me()] + names)}. {why}.",
                  3, mob=ask["mob"], map=ask["map"], team=[self.me()] + names, role="leader")

    async def start_trip(self, now, target, hmap, role, team, leader=None):
        explorer = self.mind.explorer
        r = explorer.reach_maps().get(hmap) or {}
        m = self.atlas.maps.get(hmap) or {}
        rec = {"map": hmap, "hops": r.get("hops"), "x": r.get("x"), "y": r.get("y"), "kind": m.get("kind"),
               "risk": 0.0, "path": r.get("path", [])}
        cursor = self.mind.mem.db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        self.st["hunt"] = {"mob": target["mob"], "map": hmap, "started": now, "role": role, "team": team,
                           "leader": leader or self.me(), "won": False, "cursor": cursor}   # итог — по событиям после
        self.save()
        ok = await explorer.start(rec, led_by=leader)
        if not ok or not explorer.trip:
            self.st.pop("hunt", None)
            self.save()
            self.mind.write_decision({"type": "boss", "event": "boss_trip_failed", "map": hmap})
            return False
        explorer.trip["stay"] = self.rng.uniform(*self.cfg["search_minutes"]) * 60   # стоянка покрывает респаун
        explorer.trip["boss"] = target["mob"]
        explorer.save()
        self.st["starts"] = [ts for ts in self.st.get("starts") or [] if now - ts < 7 * 86400] + [now]
        self.save()
        return True

    def leading(self, hmap=None):
        """Для explore.lead_group: лидер похода сам зовёт тех, кто согласился, — [explore:trip:] не нужен."""
        h = self.st.get("hunt")
        return bool(h and h.get("role") == "leader" and (hmap is None or h.get("map") == hmap))

    # ---------- метки ----------

    async def whisper(self, to, text, reason):
        await self.mind.execute([{"action": "whisper", "to": to, "text": text}], source="boss",
                                reason=f"мини-босс: {reason}", protocol=True)

    async def on_tag(self, sender, text):
        m = TAG.search(text or "")
        party = getattr(self.mind, "party", None)
        if not m or not party or sender not in self.mind.ctx.peers:
            return
        kind, args = m.group(1), [a for a in m.group(2).split(":") if a]
        now = self.clock()
        if kind == "ask" and sender == party.leader and len(args) == 2:
            await self.answer(now, sender, args[0], args[1])
        elif kind in ("yes", "no") and party.is_leader and self.st.get("ask") and sender in self.st["ask"]["asked"]:
            if kind == "yes" and len(args) == 4 and all(a.isdigit() for a in args):
                lv, hp, pots, heal = (int(a) for a in args)
                self.st["ask"]["answers"][sender] = {"yes": True, "lv": lv, "hp": hp, "pots": pots, "heal": heal}
            else:
                self.st["ask"]["answers"][sender] = {"yes": False, "why": args[0] if args else "?"}
            self.save()
        elif kind == "go" and sender == party.leader and len(args) == 2:
            await self.join(now, sender, args[0], args[1])
        elif kind == "won" and self.st.get("hunt") and sender in self.st["hunt"]["team"]:
            await self.victory(now, by=sender)
        elif kind == "done" and self.st.get("hunt") and sender == self.st["hunt"].get("leader"):
            if args and args[0] == "won":
                await self.victory(now, by=sender)
            else:                                    # лидер вернул группу без победы — и я возвращаюсь
                explorer = self.mind.explorer
                if explorer.trip and explorer.trip.get("map") == self.st["hunt"]["map"]:
                    await explorer.finish(now, ok=False, why=f"{sender} уводит группу")

    async def answer(self, now, leader, mob, hmap):
        target = self.target(mob)
        why = None
        if not target:
            why = "unknown"
        elif self.banned(mob, now):
            why = "ban"
        elif self.can_start(now):
            why = "busy"
        else:
            ok, why_ready = self.ready(target)
            if not ok:
                why = why_ready
            elif not self.willing():
                why = "fear"
        if why:
            self.mind.write_decision({"type": "boss", "event": "boss_refuse", "mob": mob, "map": hmap, "why": why})
            await self.whisper(leader, f"[boss:no:{why}]", f"отказ {leader}: {why}")
            return
        c = self.me_card()
        self.st["agreed"] = {"mob": mob, "map": hmap, "ts": now, "leader": leader}
        self.save()
        self.mind.write_decision({"type": "boss", "event": "boss_agree", "mob": mob, "map": hmap})
        await self.whisper(leader, f"[boss:yes:{int(c['lv'] or 0)}:{int(c['hp'] or 0)}:{min(int(c['pots']), 999)}:"
                                   f"{c['heal']}]", f"согласен идти с {leader}")

    async def join(self, now, leader, mob, hmap):
        agreed = self.st.get("agreed") or {}
        target = self.target(mob)
        if not target or agreed.get("mob") != mob or agreed.get("map") != hmap or now - agreed.get("ts", 0) > 900:
            return
        self.st.pop("agreed", None)
        why = self.can_start(now) or self.mind.explorer.blocked(self.mind.state, now, joining=True)
        if why:
            self.save()
            self.mind.write_decision({"type": "boss", "event": "boss_join_failed", "mob": mob, "why": why})
            return
        if await self.start_trip(now, target, hmap, role="member", team=[leader, self.me()], leader=leader):
            self.note("boss_start", f"Иду с {leader} на {mob} ({hmap}).", 3, mob=mob, map=hmap,
                      team=[leader, self.me()], role="member")

    # ---------- бой и итог ----------

    async def on_kill(self, event):
        h = self.st.get("hunt")
        monster = str((event or {}).get("monster") or "")
        if not h or h.get("won") or monster.lower() != h["mob"].lower():
            return
        if self.mind.state.get("map") not in (None, h["map"]):
            return
        await self.victory(self.clock(), by=self.me())

    async def victory(self, now, by):
        h = self.st.get("hunt")
        if not h or h.get("won"):
            return
        h["won"] = True
        self.save()
        mine = by == self.me()
        self.note("boss_killed", f"Победили {h['mob']} на {h['map']} вместе: {', '.join(h['team'])}"
                                 + ("" if mine else f" (добил(а) {by})") + ".", 4,
                  mob=h["mob"], map=h["map"], team=h["team"], by=by)
        collection = getattr(self.mind, "collection", None)
        if collection is not None and hasattr(collection, "boss_trophy"):
            collection.boss_trophy(h["mob"], now)
        self.publish_victory(now, h, by)
        crew = getattr(self.mind, "crew", None)
        if crew and crew.active() and h["role"] == "leader":
            text = self.rng.choice(PHRASES["won"]).format(mob=h["mob"])
            await self.mind.execute([{"action": "party_say", "text": text}], source="boss",
                                    reason="мини-босс: победа", protocol=True)
        if h["role"] == "member" and mine:
            await self.whisper(h["leader"], f"[boss:won:{h['mob']}]", "победа — лидеру")
        if h["role"] == "leader":
            for name in h["team"]:
                if name != self.me():
                    await self.whisper(name, "[boss:done:won]", f"победа — {name}")
        explorer = self.mind.explorer
        if explorer.trip and explorer.trip.get("map") == h["map"]:
            await explorer.finish(now, ok=True, why=f"победили {h['mob']}")

    def publish_victory(self, now, h, by):
        """Шина: одна запись boss_victory на группу (кто-то из команды уже записал — не дублировать)."""
        bus = getattr(getattr(self.mind, "world", None), "bus", None)
        if bus is None or not hasattr(bus, "recent"):
            return
        try:
            since = now - self.cfg["victory_dedup_minutes"] * 60
            if any(r["data"].get("mob") == h["mob"] and r["bot"] in h["team"] for r in bus.recent("boss_victory", since)):
                return
            bus.publish("boss_victory", {"mob": h["mob"], "map": h["map"], "team": h["team"], "by": by}, 5, now=now)
        except Exception as e:
            log.warning("шина мира недоступна: %s", e)

    async def follow_up(self, now):
        """Экспедиция кончилась — итог похода по фактам: победа уже записана, смерть → поражение, иначе — не нашли."""
        h = self.st["hunt"]
        explorer = self.mind.explorer
        if explorer.trip and explorer.trip.get("map") == h["map"]:
            return
        self.st.pop("hunt", None)
        if h.get("won"):
            self.save()
            return
        if h["role"] == "leader":                    # ушли без победы — участникам тоже назад
            for name in h["team"]:
                if name != self.me():
                    await self.whisper(name, "[boss:done:back]", f"возвращаемся — {name}")
        cursor = h.get("cursor", 0)
        died = self.mind.mem.db.execute("SELECT COUNT(*) FROM events WHERE kind = 'died' AND id > ?",
                                        (cursor,)).fetchone()[0]
        last = self.mind.mem.db.execute("SELECT data FROM events WHERE kind = 'explore_done' AND id > ? "
                                        "ORDER BY id DESC LIMIT 1", (cursor,)).fetchone()
        why = (json.loads(last[0]).get("why") if last else None) or "вернулись"
        if died:
            ban = self.st.setdefault("ban", {})
            ban[h["mob"]] = now + self.cfg["fail_ban_hours"] * 3600
            self.save()
            self.note("boss_failed", f"Поход на {h['mob']} ({h['map']}) не удался: погиб(ла). "
                                     f"{self.cfg['fail_ban_hours']} ч его не трогаю.", 4,
                      mob=h["mob"], map=h["map"], team=h["team"], why=why)
            bus = getattr(getattr(self.mind, "world", None), "bus", None)
            if bus is not None and hasattr(bus, "publish"):
                try:
                    bus.publish("boss_failed", {"mob": h["mob"], "map": h["map"]}, 3, now=now)
                except Exception as e:
                    log.warning("шина мира недоступна: %s", e)
            return
        self.save()
        self.note("boss_missed", f"Сходили на {h['mob']} ({h['map']}) — не встретили или отступили: {why}.", 2,
                  mob=h["mob"], map=h["map"], team=h["team"], why=why)

    def summary(self):
        h = self.st.get("hunt")
        if h:
            return {"охота на": h["mob"], "карта": h["map"], "команда": h["team"]}
        return None


CHRONICLE_LINES = {
    "boss_start": lambda d: f"собрались на {d.get('mob')} ({d.get('map')}): {', '.join(d.get('team') or [])}",
    "boss_killed": lambda d: f"победили {d.get('mob')} группой: {', '.join(d.get('team') or [])}",
    "boss_failed": lambda d: f"поход на {d.get('mob')} не удался — осторожнее",
    "boss_missed": lambda d: f"сходили на {d.get('mob')}, но не встретили",
}
