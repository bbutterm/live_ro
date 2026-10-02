"""Дальний поход группой (ORG-078, ТЗ Т-34): экспедиция в город другого региона с привалами. Правила без LLM.

Расширение экспедиций ORG-054 (explore.py): поход — цепочка плеч «город -> город» (brain/world/trek_routes.json,
scripts/gen_trek_routes.py: переходы portals.txt профиля, подтверждённые варпами сервера, плечо ≤ 7 переходов,
обратный путь обязателен). Каждое плечо — экспедиция explorer.start(город, led_by) — как поход на мини-босса
(boss.py): explore.py не меняется, распорядок и прогулки тело не двигают (explorer.busy). Своё приглашение
[explore:trip:] лидер на плечах не шлёт (cfg.group экспедиции выключается на время start) — участники идут своими
плечами по своему походу.

Сбор. Лидер (crew активна, я лидер группы) раз в gap_days, днём, в городе отдыха, с запасами (зелья ≥ min_potions,
вес < max_weight_pct, зени ≥ min_zeny), если поход успевает закончиться за час до сна, выбирает цель из targets:
путь есть (≤ max_legs плеч), риск всех карт пути ниже max_risk для слабейшего (explorer.weakest: уровни участников из
crew), давно там не был (revisit_days). Зовёт участников онлайн на своей карте шёпотом [trek:go:<город>]; участник
сам проверяет себя (экспедиция возможна, запасы, тот же дом, риск для своего уровня) и отвечает [trek:ok:<город>] или
[trek:no:<город>]. За gather_seconds набралось ≥ min_members (с лидером) — лидер подтверждает каждому [trek:ok:<город>]
и выходит; иначе поход отменён.
Плечи: explorer.start(город), срок = переходы × hop_minutes, стоянка — halt_minutes (в цели — stay_minutes). Плечо
кончилось ok на карте города -> привал: при halt_save — сохранение у Kafra этого города (jobChange path trek, данные
homes.json; запись дома home.st.saved = город: после возвращения home.py сам пересохранится дома), затем следующее
плечо; цель -> стоянка -> обратные плечи до дома. Плечо прервано (ночь, HP, тревога, смерть, срок, лидер ушёл) —
поход окончен ok=false, домой ведёт распорядок.
События памяти (летопись, тема trek): trek_start {target, members, legs}, trek_halt {town}, trek_arrived {target},
trek_done {target, ok, why}. По умолчанию выключено (goals.json trek.enabled false): в игре не проверено.
"""
import heapq
import json
import logging
import random
import re
import time
from pathlib import Path

from .lifecycle import quest_busy                 # review4: begin — тело занято этапом

log = logging.getLogger("trek")

WORLD = Path(__file__).resolve().parents[1] / "world"
ROUTES_PATH = WORLD / "trek_routes.json"
TAG = re.compile(r"\[trek:(go|ok|no):([a-z0-9_]{3,16})\]")
PATH, STAGE = "trek", "kafra_save"
POTIONS = ("501", "502", "503", "504", "505")
DEFAULTS = {
    "enabled": False,
    "targets": ["geffen", "payon", "alberta", "aldebaran"],
    "min_members": 2,             # с лидером
    "max_members": 3,
    "gap_days": 7,
    "retry_hours": 12,            # после отмены сбора
    "min_curiosity": 0.2,
    "min_potions": 10,
    "max_weight_pct": 50,
    "min_zeny": 1000,
    "max_risk": 0.35,             # риск карт пути (atlas.danger_for) для слабейшего; pay_fild04 (Ghostring) — 0.3
    "max_legs": 3,
    "hop_minutes": 6,             # срок плеча — на переход
    "halt_minutes": [10, 20],
    "stay_minutes": [30, 60],
    "gather_seconds": 120,
    "halt_save": True,
    "revisit_days": 21,
    "save_wait_minutes": 10,
    "brag_days": 7,
    "brag_chance": 0.5,
}
PHRASES = {
    "trek": ["Ходил(а) в {town} с {mate} — далеко, но дошли!", "Мы с {mate} сходили в {town} и вернулись!"],
    "trek_halt": ["Мы с {mate} были в {town}: привал в {halt}.", "Ходил(а) в {town} через {halt} — далеко!"],
    "trek_re": ["Ничего себе путь! Возьмёте в следующий раз?", "Далеко забрались!", "Расскажешь, что там?"],
}


def load_routes(path=ROUTES_PATH):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"towns": {}}


def chain(routes, home, target, max_legs):
    """Кратчайшая по переходам цепочка плеч (при равенстве — меньше плеч): [(город, переходы, путь)] или None."""
    towns = routes.get("towns") or {}
    heap = [(0, 0, home, [])]
    best = {}
    while heap:
        hops, n, town, legs = heapq.heappop(heap)
        if town == target:
            return legs
        if best.get(town, (1 << 30, 0)) <= (hops, n) or n >= max_legs:
            continue
        best[town] = (hops, n)
        for to, rec in sorted(((towns.get(town) or {}).get("legs") or {}).items()):
            if to not in {t for t, _h, _p in legs} and to != home:
                heapq.heappush(heap, (hops + rec["hops"], n + 1, to, legs + [(to, rec["hops"], rec["path"])]))
    return None


class Trek:
    # реестр модулей (modules.py, W8)
    ATTR, FEATURE, CONFIG, ENABLED = "trek", "trek", "trek", False
    REQUIRES, ARGS = ("world", "routine", "explorer", "party", "crew"), "world"
    TICK_ORDER = 112                          # после explorer (110): конец плеча видит уже закрытую экспедицию
    TAGS, TAG_ORDER = [(TAG, "on_tag")], 45   # после explorer [explore:] (40), до crew (50)
    EVENTS = {"job_change_result": {"call": "on_result", "consume": "result"}}   # только path trek
    EVENT_ORDER = 17
    PROMPT = [("поход", "summary", 212)]      # после «экспедиция» (210)

    def __init__(self, mind, world=None, clock=None, rng=None, routes=None, homes=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("trek") or {}))
        self.clock = clock or (lambda: time.time())
        self.rng = rng or random.Random()
        self.routes = routes if routes is not None else load_routes()
        if homes is None:
            try:
                homes = json.loads((WORLD / "homes.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                homes = {}
        self.homes = homes
        self.st = mind.mem.get("trek") or {}
        for key, val in (("visited", {}), ("done", []), ("told", {})):
            self.st.setdefault(key, val)
        social = getattr(mind, "social", None)
        if social is not None and hasattr(social, "register_topic"):
            for key, pool in PHRASES.items():
                social.phrases.setdefault(key, pool)
            social.register_topic("trek", self.facts, said=self.said, chance=self.cfg["brag_chance"])

    # ---------- данные ----------

    def save(self):
        self.mind.mem.set("trek", self.st)

    @property
    def trip(self):
        return self.st.get("trip")

    def busy(self):
        return bool(self.trip)

    def town(self):
        return self.mind.explorer.town()

    def legs_to(self, target):
        """Плечи туда и обратно: [{to, hops, path}] или None."""
        home = self.town()
        out = chain(self.routes, home, target, self.cfg["max_legs"])
        if not out:
            return None
        back = chain(self.routes, target, home, self.cfg["max_legs"])
        if not back:
            return None
        return [{"to": t, "hops": h, "path": p} for t, h, p in out + back]

    def risk(self, legs, level):
        a = self.mind.explorer.atlas
        worst, where = 0.0, None
        for leg in legs:
            for m in leg["path"][1:]:
                r, _ = a.danger_for(m, level)
                if r > worst:
                    worst, where = r, m
        return worst, where

    def supplies(self, state):
        potions = sum(int((state.get("items") or {}).get(p) or 0) for p in POTIONS)
        if potions < self.cfg["min_potions"]:
            return f"зелий {potions} < {self.cfg['min_potions']}"
        if (state.get("weight_pct") or 0) >= self.cfg["max_weight_pct"]:
            return f"вес {state.get('weight_pct')}% — сначала продать"
        if int(state.get("zeny") or 0) < self.cfg["min_zeny"]:
            return f"зени < {self.cfg['min_zeny']}"
        return None

    def duration(self, legs):
        stays = len(legs) * self.cfg["halt_minutes"][1] + self.cfg["stay_minutes"][1]
        return (sum(l["hops"] for l in legs) * self.cfg["hop_minutes"] + stays) * 60

    def fits_sleep(self, now, legs):
        r = self.mind.routine
        win = r.sleep_window(now) if hasattr(r, "sleep_window") else None
        return not win or win[0] - now >= self.duration(legs) + 3600 or now >= win[1]

    # ---------- выбор цели и сбор (лидер) ----------

    def mates_here(self):
        party, state = self.mind.party, self.mind.state
        return [m["name"] for m in party.members() if m.get("online") and m.get("map") == state.get("map")]

    def can_lead(self, now):
        m, cfg, state = self.mind, self.cfg, self.mind.state
        if self.trip:
            return "поход уже идёт"
        if now - self.st.get("last", 0) < cfg["gap_days"] * 86400:
            return f"не чаще раза в {cfg['gap_days']} дн."
        if now < self.st.get("next_try", 0):
            return "пауза после отмены"
        if not (m.crew.active() and m.party.is_leader):
            return "не лидер группы"
        why = m.explorer.blocked(state, now, joining=True)
        if why:
            return why
        if len(self.mates_here()) < cfg["min_members"] - 1:
            return "участников рядом мало"
        needs = getattr(m, "needs", None)
        cur = needs.weighted().get("curiosity", 0) if needs else 1.0
        if cur < cfg["min_curiosity"]:
            return f"любопытство {cur:.2f} < {cfg['min_curiosity']}"
        return self.supplies(state)

    def candidates(self, now):
        level = self.mind.explorer.weakest()
        out = []
        for target in self.cfg["targets"]:
            if target == self.town() or now - self.st["visited"].get(target, 0) < self.cfg["revisit_days"] * 86400:
                continue
            legs = self.legs_to(target)
            if not legs or not level or not self.fits_sleep(now, legs):
                continue
            risk, _ = self.risk(legs, level)
            if risk >= self.cfg["max_risk"]:
                continue
            hops = sum(l["hops"] for l in legs)
            out.append((-hops * 0.02 - risk + self.rng.uniform(0, 0.2), target, legs))
        out.sort(key=lambda c: (-c[0], c[1]))
        return [(t, legs) for _s, t, legs in out]

    async def maybe_lead(self, now):
        why = self.can_lead(now)
        if why:
            return
        cands = self.candidates(now)
        if not cands:
            return
        target, legs = cands[0]
        mates = self.mates_here()[:self.cfg["max_members"] - 1]
        self.st["trip"] = {"target": target, "legs": legs, "i": 0, "phase": "gather", "led_by": None,
                           "invited": mates, "members": [], "started": now,
                           "until": now + self.cfg["gather_seconds"]}
        self.save()
        self.mind.write_decision({"type": "trek", "event": "gather", "target": target, "invited": mates,
                                  "legs": [l["to"] for l in legs]})
        for name in mates:
            await self.whisper(name, f"[trek:go:{target}]", f"поход: зову {name} в {target}")

    async def whisper(self, to, text, reason):
        await self.mind.execute([{"action": "whisper", "to": to, "text": text}], source="trek", reason=reason,
                                protocol=True)

    # ---------- метки ----------

    async def on_tag(self, sender, text):
        m = TAG.search(text or "")
        if not m:
            return False
        kind, target = m.group(1), m.group(2)
        trip, party = self.trip, self.mind.party
        if kind == "go":
            return await self.on_invite(sender, target)
        if not trip or trip["target"] != target:
            return False
        if trip["led_by"] is None and trip["phase"] == "gather":         # я лидер: ответ участника
            if kind == "ok" and sender in trip["invited"] and sender not in trip["members"]:
                trip["members"].append(sender)
            self.save()
            return True
        if trip["led_by"] == sender == party.leader and trip["phase"] == "gather" and kind == "ok":
            await self.begin(self.clock())                                # лидер подтвердил — выходим
            return True
        if trip["led_by"] == sender and kind == "no":
            self.cancel(self.clock(), f"{sender} отменил поход")
        return True

    async def on_invite(self, sender, target):
        now, state, party = self.clock(), self.mind.state, self.mind.party
        why = None
        if party.is_leader or sender != party.leader:
            why = "зовёт не мой лидер"
        elif self.trip:
            why = "поход уже идёт"
        else:
            why = self.mind.explorer.blocked(state, now, joining=True) or self.supplies(state)
        legs = None
        if not why:
            legs = self.legs_to(target)
            if not legs:
                why = f"нет пути из {self.town()} в {target}"
            elif not self.fits_sleep(now, legs):
                why = "не успею до сна"
            else:
                risk, where = self.risk(legs, state.get("lv"))
                if risk >= self.cfg["max_risk"]:
                    why = f"опасно для меня: {where} (риск {risk:.2f})"
        if why:
            self.mind.write_decision({"type": "trek", "event": "decline", "target": target, "from": sender, "why": why})
            await self.whisper(sender, f"[trek:no:{target}]", f"поход: отказ {sender}")
            return True
        self.st["trip"] = {"target": target, "legs": legs, "i": 0, "phase": "gather", "led_by": sender,
                           "members": [sender], "started": now, "until": now + self.cfg["gather_seconds"] + 60}
        self.save()
        await self.whisper(sender, f"[trek:ok:{target}]", f"поход: иду с {sender} в {target}")
        return True

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        if not getattr(self.mind, "fresh_state", True) or not self.cfg.get("enabled", True):
            return
        trip = self.trip
        if not trip:
            await self.maybe_lead(now)
            return
        phase = trip["phase"]
        if phase == "gather":
            await self.tick_gather(now, trip)
        elif phase == "leg":
            await self.tick_leg(now, trip)
        elif phase == "save":
            if not (self.mind.state.get("job_change") or {}).get("running") and \
                    now - trip.get("save_ts", now) >= self.cfg["save_wait_minutes"] * 60:
                self.mind.write_decision({"type": "trek", "event": "save_timeout", "town": trip["legs"][trip["i"]]["to"]})
                await self.next_leg(now)
        elif phase == "saved":
            await self.next_leg(now)

    async def tick_gather(self, now, trip):
        if trip["led_by"] is not None:                                    # участник ждёт подтверждения лидера
            if now >= trip["until"]:
                self.cancel(now, "лидер не подтвердил поход")
            return
        full = len(trip["members"]) + 1 >= self.cfg["max_members"] or len(trip["members"]) >= len(trip["invited"])
        if not full and now < trip["until"]:
            return
        if len(trip["members"]) + 1 < self.cfg["min_members"]:
            self.cancel(now, "не собрались")
            self.st["next_try"] = now + self.cfg["retry_hours"] * 3600
            self.save()
            return
        for name in trip["members"]:
            await self.whisper(name, f"[trek:ok:{trip['target']}]", f"поход: выходим с {name}")
        await self.begin(now)

    async def begin(self, now):
        trip = self.trip
        if self.mind.explorer.trip:                     # пока собирались, ушёл в обычную экспедицию
            self.cancel(now, "уже в экспедиции")
            return
        if quest_busy(self.mind, self.mind.state, now):  # review4: пока собирались, начат этап jobChange/заточка/
            self.cancel(now, "тело занято этапом")        # review4: спарринг — плечо увело бы тело из-под него
            return
        trip["started"] = now
        self.st["last"] = now
        self.save()
        names = trip["members"]
        halts = [l["to"] for l in trip["legs"][:-1] if l["to"] != trip["target"]]
        lead = f" за {trip['led_by']}" if trip["led_by"] else ""
        self.note("trek_start", f"Выхожу в поход в {trip['target']}{lead} с {', '.join(names)}" +
                  (f", привалы: {', '.join(dict.fromkeys(halts))}." if halts else "."), 3,
                  target=trip["target"], members=names, legs=[l["to"] for l in trip["legs"]],
                  leader=trip["led_by"] or self.mind.party.me)
        await self.start_leg(now)

    async def start_leg(self, now):
        trip = self.trip
        leg = trip["legs"][trip["i"]]
        ex = self.mind.explorer
        last = trip["i"] == len(trip["legs"]) - 1
        target = {"map": leg["to"], "x": None, "y": None, "kind": "town", "hops": leg["hops"]}
        group = ex.cfg.get("group")
        ex.cfg["group"] = False                          # своё [explore:trip:] на плечах не зовём — у участников свой поход
        try:
            ok = await ex.start(target, led_by=trip["led_by"])
        finally:
            ex.cfg["group"] = group
        if not ok or not ex.trip:
            await self.finish(now, False, f"плечо до {leg['to']} не началось")
            return
        if leg["to"] == trip["target"]:
            stay = self.rng.uniform(*self.cfg["stay_minutes"]) * 60
        elif last:
            stay = 60
        else:
            stay = self.rng.uniform(*self.cfg["halt_minutes"]) * 60
        ex.trip.update(deadline=now + leg["hops"] * self.cfg["hop_minutes"] * 60, stay=stay, trek=trip["target"])
        ex.save()
        trip["phase"] = "leg"
        self.save()

    async def tick_leg(self, now, trip):
        ex = self.mind.explorer
        if ex.trip:
            return                                      # плечо идёт (путь или стоянка)
        leg = trip["legs"][trip["i"]]
        back = ex.st.get("back") or {}
        if back.get("map") != leg["to"] or not back.get("ok"):
            await self.finish(now, False, f"плечо до {leg['to']} прервано")
            return
        if trip["i"] == len(trip["legs"]) - 1:
            await self.finish(now, True, "вернулись домой")
            return
        if leg["to"] == trip["target"]:
            self.st["visited"][trip["target"]] = now
            self.note("trek_arrived", f"Дошли до {trip['target']}! Осмотрелись — пора назад.", 4, target=trip["target"])
        else:
            self.note("trek_halt", f"Привал в {leg['to']}.", 2, town=leg["to"], target=trip["target"])
        town = (self.homes.get("towns") or {}).get(leg["to"])
        if self.cfg["halt_save"] and town and leg["to"] != self.town() and self.mind.state.get("map") == leg["to"]:
            await self.save_at(now, leg["to"], town)
            return
        await self.next_leg(now)

    async def save_at(self, now, name, town):
        cfg = self.homes.get("save") or {}
        npc, stand = town["npc"], town["stand"]
        trip = self.trip
        trip.update(phase="save", save_ts=now)
        self.save()
        await self.mind.execute([{"action": "job_change", "path": PATH, "stage": STAGE,
                                  "steps": [{"do": "move", "map": name, "x": stand["x"], "y": stand["y"]},
                                            {"do": "talk", "x": npc["x"], "y": npc["y"],
                                             "answers": [{"text": cfg.get("answer", "Save")}]}],
                                  "success": {"text": cfg.get("proof_text", "Respawn Point"), "map": name}}],
                                source="trek", reason=f"поход: сохраниться у Kafra в {name}", protocol=True)

    def on_result(self, event):
        if event.get("path") != PATH:
            return False
        trip = self.trip
        if not trip or trip["phase"] != "save":
            return True
        name = trip["legs"][trip["i"]]["to"]
        if event.get("ok"):
            home = getattr(self.mind, "home", None)
            if home is not None:
                sp = ((self.homes.get("towns") or {}).get(name) or {}).get("savepoint") or {}
                home.st["saved"] = {"map": name, "x": sp.get("x"), "y": sp.get("y"), "ts": self.clock(), "how": "kafra"}
                home.save()                              # дома home.py пересохранится сам (точка не дома)
            self.note("trek_saved", f"Сохранился(лась) у Kafra в {name} — в походе возрожусь здесь.", 2, town=name)
        else:
            self.mind.write_decision({"type": "trek", "event": "save_failed", "town": name,
                                      "why": event.get("reason")})
        trip["save_ts"] = 0                              # следующий такт — следующее плечо
        trip["phase"] = "saved"
        self.save()
        return True

    async def next_leg(self, now):
        trip = self.trip
        trip["i"] += 1
        self.save()
        await self.start_leg(now)

    async def finish(self, now, ok, why):
        trip = self.trip
        self.st["trip"] = None
        self.st["done"] = (self.st["done"] + [{"ts": now, "target": trip["target"], "ok": ok, "why": why,
                                               "members": trip["members"],
                                               "halts": [l["to"] for l in trip["legs"][:-1]
                                                         if l["to"] != trip["target"]]}])[-20:]
        self.save()
        text = (f"Вернулся(лась) из похода в {trip['target']}." if ok else
                f"Поход в {trip['target']} окончен: {why}. Иду домой.")
        self.note("trek_done", text, 4 if ok else 2, target=trip["target"], ok=ok, why=why)

    def cancel(self, now, why):
        trip = self.trip
        self.st["trip"] = None
        self.save()
        self.mind.write_decision({"type": "trek", "event": "cancel", "target": trip and trip["target"], "why": why})

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "trek", "event": kind, "text": text, **data})
        log.info("%s", text)

    # ---------- разговор и промпт ----------

    def facts(self, peer, now):
        told = set(self.st["told"].get(peer) or [])
        for rec in reversed(self.st["done"]):
            if not rec.get("ok") or now - rec["ts"] > self.cfg["brag_days"] * 86400:
                return None
            key = str(int(rec["ts"]))
            if key in told or peer in rec.get("members", []):
                return None
            mate = (rec.get("members") or ["друзьями"])[0]
            f = {"town": rec["target"], "mate": mate, "_trek": key}
            if rec.get("halts"):
                f.update(halt=rec["halts"][0], _key="trek_halt")
            return f
        return None

    def said(self, peer, facts, now):
        key = (facts or {}).get("_trek")
        if key:
            self.st["told"][peer] = ((self.st["told"].get(peer) or []) + [key])[-20:]
            self.save()

    def summary(self):
        trip = self.trip
        if not trip:
            return None
        phase = {"gather": "сбор", "leg": "в пути", "save": "привал у Kafra", "saved": "привал"}.get(trip["phase"])
        return {"поход": trip["target"], "этап": phase, "плечо": f"{trip['i'] + 1}/{len(trip['legs'])}",
                "с": trip["members"]}


CHRONICLE_LINES = {
    "trek_start": lambda d: f"вышел(шла) в поход в {d.get('target')} с {', '.join(d.get('members') or [])}",
    "trek_arrived": lambda d: f"дошёл(шла) в походе до {d.get('target')}",
    "trek_done": lambda d: (f"вернулся(лась) из похода в {d.get('target')}" if d.get("ok")
                            else f"поход в {d.get('target')} прерван: {d.get('why')}"),
}
