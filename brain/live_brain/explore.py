"""Исследование мира (ORG-054): экспедиции жителей на новые карты. Правила без LLM, тик 1 с.

Конфигурация — brain/world/goals.json раздел "explore" (DEFAULTS ниже), достижимость — brain/world/explore_reach.json
(scripts/gen_explore_reach.py: куда OpenKore дойдёт пешком из города по portals.txt, подтверждённым варпами сервера,
и откуда вернётся). Подробно — docs/EXPLORE.md.

Карта знаний жителя (knowledge): посещённые карты (kv places, source seen), услышанные из слухов (rumors.py, places
told) и «видимые дороги» — соседи посещённых по атласу (выход с карты видно, куда он ведёт — нет). Атлас —
знание сервера, житель его целиком не знает: цель выбирается только из известных, но ещё не посещённых карт.
Цель (candidates):
    достижима для OpenKore из города отдыха (explore_reach.json) не дальше max_hops переходов;
    есть в атласе, вид из kinds (поле, город), не pvp/gvg, не из deny (полигон новичков iz_int*/int_land*,
    перестроенный izlude, гильдии) и не исключена после смертей (routine.bans);
    безопасна по уровню СЛАБЕЙШЕГО (свой уровень, у лидера группы — и уровни участников из crew): риск
    atlas.danger_for ниже min(max_risk, допуск смелости); промежуточные карты пути — тоже;
    нет свежего слуха danger (доверие ≥ DANGER_TRUST).
    Очки: 1 − 0.1·переходы − риск + rumor_bonus·доверие слуха new/rich («поди проверь») + бонус города + шум характера.
Старт — занятие explore каталога (activity.py, режим town): высокий curiosity, день, не чаще gap_hours, HP ≥ min_hp,
    житель в городе отдыха, нет плана встречи, арбитр разрешает routine, сон не ближе sleep_guard_hours, участник
    группы сам не выходит (ведёт лидер).
Группа: лидер (crew активна) шёпотом зовёт участников на той же карте [explore:trip:<карта>]; участник проверяет
    цель сам (достижимость, свой уровень, HP, город, план) и идёт той же экспедицией.
Путь: действие explore {map[, x, y]} (safety.check_explore: карта из атласа, не полигон, по уровню; мост ставит
    lockMap). По умолчанию без точки: на поле OpenKore ходит и охотится слегка, в городе стоит у входа; точка
    (клетка прибытия) — только при town_point (она у самого варпа). Пока экспедиция идёт,
    распорядок и прогулки по городу тело не двигают (routine.tick, social.may_walk, crew.walk — блоки explore:).
Прибытие — только по факту state.map == цель. На месте stay_minutes; место запоминается в kv places
    (first, visits, kafra, shops, monsters — убитые там по событиям kill), событие explore_arrived; новое место —
    explore_found (шина мира place_found, хроника «открыл(а) X»), фраза в чат группы.
Возврат: экспедиция заканчивается, распорядок снова ведёт тело в город (routine.enforce); прибытие в город —
    explore_returned. Прерывание (abort): тревога survival/danger/escape, смерть, HP < abort_hp, план встречи,
    ночь/скорый сон, не дошёл за travel_minutes.
Находки — слух жителям (rumors.share), один за экспедицию: danger (погиб там/тревога), rich (побед в час ≥
    rich_kills_per_hour без смертей), new (новое место без происшествий).
"""
import json
import logging
import random
import re
import time
from pathlib import Path

log = logging.getLogger("explore")

REACH_PATH = Path(__file__).resolve().parents[1] / "world" / "explore_reach.json"
TAG = re.compile(r"\[explore:(trip):([a-z0-9_]{3,16})\]")
RESEND = 60
DANGER_TRUST = 0.4
ALARMS = ("survival", "danger", "escape")
DEFAULTS = {
    "enabled": True,
    "max_hops": 4,
    "gap_hours": 6,
    "stay_minutes": [10, 20],
    "travel_minutes": 40,
    "return_minutes": 60,
    "min_hp": 80,
    "abort_hp": 50,
    "min_curiosity": 0.25,
    "max_risk": 0.3,
    "kinds": ["field", "town"],
    "deny": ["izlude", "izlude_in", "iz_ac01", "iz_ac02", "iz_int", "int_land", "prt_gld", "pay_gld", "gef_fild13",
             "alde_gld", "prt_monk", "monk_test"],
    "deny_prefix": ["iz_int", "int_land", "izlude_", "new_", "prtg_", "payg_", "gefg_", "aldeg_"],
    "sleep_guard_hours": 2,
    "rumor_bonus": 0.5,
    "town_bonus": 0.2,
    "rich_kills_per_hour": 60,
    "group": True,
    "town_point": False,
}


def denied(hmap, cfg):
    """Карта запрещена для экспедиций: полигон новичков, izlude (порталы renewal в bots/common/tables есть, но в игре
    не проверены — снять из deny после проверки), гильдии (cfg deny/deny_prefix)."""
    return hmap in cfg.get("deny", ()) or any(hmap.startswith(p) for p in cfg.get("deny_prefix", ()))


def load_reach(path=REACH_PATH):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"towns": {}}


class Explorer:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES, ARGS = "explorer", "explore", "explore", False, ("routine",), "config"
    TICK_ORDER = 110            # on_event(любое событие) — явно в mind (стык выживания)
    TAGS, TAG_ORDER = [(TAG, "on_tag")], 40
    PROMPT = [("экспедиция", "summary", 210)]

    def __init__(self, mind, cfg=None, clock=None, rng=None, reach=None, atlas_obj=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **(cfg or {}))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.reach = reach if reach is not None else load_reach()
        self._atlas = atlas_obj
        self.st = mind.mem.get("explore") or {}
        self.last_sent = 0.0
        self.alarm = None                 # причина тревоги во время экспедиции

    # ---------- данные ----------

    @property
    def atlas(self):
        if self._atlas is None:
            from . import atlas
            self._atlas = atlas.default()
        return self._atlas

    def save(self):
        self.mind.mem.set("explore", self.st)

    @property
    def trip(self):
        return self.st.get("trip")

    def busy(self):
        """Экспедиция ведёт тело (в пути или на месте): распорядок и прогулки не двигают его."""
        return bool(self.trip)

    def town(self):
        r = getattr(self.mind, "routine", None)
        return (r.cfg.get("town") or {}).get("map") if r else None

    def reach_maps(self):
        return ((self.reach.get("towns") or {}).get(self.town()) or {}).get("maps") or {}

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "explore", "event": kind, "text": text, **data})
        log.info("%s", text)

    def risk_limit(self):
        needs = getattr(self.mind, "needs", None)
        tol = needs.risk_tolerance() if needs else 0.5
        return min(self.cfg["max_risk"], tol)

    # ---------- карта знаний ----------

    def knowledge(self):
        """{seen, heard, roads}: посещённые карты, услышанные из слухов, соседи посещённых по атласу."""
        places = self.mind.mem.get("places") or {}
        seen = {m for m, p in places.items() if p.get("source") == "seen" or p.get("first")}
        heard = {m for m, p in places.items() if p.get("rumors")}
        rumors = getattr(self.mind, "rumors", None)
        if rumors:
            heard |= {r["map"] for r in rumors.all().values() if r.get("map")}
        town = self.town()
        if town:
            seen.add(town)
        roads = set()
        for m in seen:
            roads |= set(self.atlas.neighbors(m))
        return {"seen": seen, "heard": heard - seen, "roads": roads - seen}

    def rumor_trust(self, hmap, kinds):
        rumors = getattr(self.mind, "rumors", None)
        if not rumors:
            return 0.0
        best = 0.0
        for r in rumors.all().values():
            if r.get("map") == hmap and r.get("kind") in kinds and r.get("status") != "refuted":
                best = max(best, rumors.trust(r))
        return best

    def weakest(self):
        """Уровень слабейшего: свой; у лидера группы — и уровни участников из желаний crew."""
        levels = [self.mind.state.get("lv")]
        crew = getattr(self.mind, "crew", None)
        party = getattr(self.mind, "party", None)
        if crew and party and crew.active() and party.is_leader:
            levels += [p.get("lv") for p in crew.fresh_prefs(self.clock()).values()]
        levels = [int(lv) for lv in levels if isinstance(lv, (int, float)) and lv]
        return min(levels) if levels else None

    def check_target(self, hmap, level, need_unknown=True, know=None):
        """(запись цели, None) или (None, причина): достижимость, безопасность, знание."""
        cfg, a = self.cfg, self.atlas
        r = self.reach_maps().get(hmap)
        if not r:
            return None, "OpenKore не дойдёт пешком из города (нет в explore_reach.json)"
        if r["hops"] > cfg["max_hops"]:
            return None, f"дальше {cfg['max_hops']} переходов"
        m = a.maps.get(hmap)
        if not m:
            return None, "нет в атласе"
        if m.get("kind") not in cfg["kinds"] or {"pvp", "gvg", "gvg_castle"} & set(m.get("flags", ())):
            return None, f"вид карты {m.get('kind')} не для экспедиций"
        if denied(hmap, cfg):
            return None, "запрещена (полигон/перестроена/гильдия)"
        routine = getattr(self.mind, "routine", None)
        if routine and hmap in routine.bans():
            return None, "исключена после смертей/застреваний"
        if self.rumor_trust(hmap, ("danger",)) >= DANGER_TRUST:
            return None, "свежий слух: опасно"
        if need_unknown:
            know = know or self.knowledge()
            if hmap in know["seen"]:
                return None, "уже был"
            if hmap not in know["heard"] and hmap not in know["roads"]:
                return None, "не знаю о ней"
        if not level:
            return None, "уровень неизвестен"
        limit = self.risk_limit()
        risk, why = a.danger_for(hmap, level)
        if risk >= limit:
            return None, f"риск {risk:.2f} ≥ {limit:.2f}: " + "; ".join(why)
        for mid in r.get("path", [])[1:-1]:
            mr, _ = a.danger_for(mid, level)
            if mr >= limit:
                return None, f"по пути {mid}: риск {mr:.2f}"
        return {"map": hmap, "hops": r["hops"], "x": r["x"], "y": r["y"], "kind": m.get("kind"),
                "risk": risk, "path": r.get("path", [])}, None

    def candidates(self, level=None):
        level = level or self.weakest()
        know = self.knowledge()
        out = []
        noise = self.mind.needs.noise() if getattr(self.mind, "needs", None) else 0.0
        for hmap in sorted(know["heard"] | know["roads"]):
            rec, _ = self.check_target(hmap, level, know=know)
            if not rec:
                continue
            tip = self.rumor_trust(hmap, ("new", "rich"))
            score = (1 - 0.1 * rec["hops"] - rec["risk"] + self.cfg["rumor_bonus"] * tip
                     + (self.cfg["town_bonus"] if rec["kind"] == "town" else 0) + noise * self.rng.uniform(-1, 1) * 0.3)
            rec.update(score=round(score, 3), rumor=round(tip, 2))
            out.append(rec)
        out.sort(key=lambda c: (-c["score"], c["map"]))
        return out

    # ---------- можно ли начать ----------

    def blocked(self, state, now=None, joining=False):
        """Причина, по которой экспедиция сейчас невозможна, или None."""
        now = now or self.clock()
        m, cfg = self.mind, self.cfg
        r = getattr(m, "routine", None)
        if not cfg.get("enabled", True):
            return "выключено"
        if self.trip:
            return "экспедиция уже идёт"
        if not r or not r.st or not getattr(m, "fresh_state", True) or state.get("dead"):
            return "нет данных или мёртв"
        if not joining and now - self.st.get("last", 0) < cfg["gap_hours"] * 3600:
            return f"не чаще раза в {cfg['gap_hours']} ч"
        if r.st.get("mode") != "town" or state.get("map") != self.town():
            return "не в городе отдыха"
        hp = state.get("hp_pct")
        if hp is None or hp < cfg["min_hp"]:
            return f"HP {hp}% < {cfg['min_hp']}%"
        if m.plans.store.active():
            return "план встречи"
        may_move = getattr(m, "may_move", None)
        if may_move and not may_move("routine")[0]:
            return "телом владеет другая задача"
        social = getattr(m, "social", None)
        if social and social.is_night(now):
            return "ночь"
        win = r.sleep_window(now) if hasattr(r, "sleep_window") else None
        if win and win[0] - cfg["sleep_guard_hours"] * 3600 <= now < win[1]:
            return "скоро сон"
        party = getattr(m, "party", None)
        if not joining and party and party.leader_wants(now):
            return "в группе ведёт лидер"
        if not joining:
            needs = getattr(m, "needs", None)
            cur = needs.weighted().get("curiosity", 0) if needs else 1.0
            if cur < cfg["min_curiosity"]:
                return f"любопытство {cur:.2f} < {cfg['min_curiosity']}"
        return None

    def available(self, state):
        """Для каталога занятий: лучшая цель, если начать можно, иначе None."""
        if self.blocked(state):
            return None
        c = self.candidates()
        return c[0] if c else None

    # ---------- старт ----------

    async def start(self, target=None, led_by=None):
        now = self.clock()
        state = self.mind.state
        target = target or self.available(state)
        if not target:
            return False
        stay = self.rng.uniform(*self.cfg["stay_minutes"]) * 60
        self.st["trip"] = {"map": target["map"], "x": target.get("x"), "y": target.get("y"),
                           "kind": target.get("kind"), "hops": target.get("hops"), "phase": "go",
                           "started": now, "deadline": now + self.cfg["travel_minutes"] * 60, "stay": stay,
                           "led_by": led_by, "new": target["map"] not in self.knowledge()["seen"]}
        self.st["last"] = now
        self.st.pop("back", None)
        self.save()
        self.alarm = None
        why = f" за {led_by}" if led_by else (f" (слух, доверие {target['rumor']})" if target.get("rumor") else "")
        self.note("explore_start", f"Отправляюсь исследовать {target['map']} ({target.get('hops')} перех.){why}.", 2,
                  map=target["map"], hops=target.get("hops"), led_by=led_by)
        routine = getattr(self.mind, "routine", None)
        if routine:
            routine.set_goal(f"исследую {target['map']}")
        await self.lead_group(target)
        await self.send(now, force=True)
        return True

    async def lead_group(self, target):
        """Лидер зовёт участников группы, которые рядом (та же карта), той же экспедицией."""
        crew, party = getattr(self.mind, "crew", None), getattr(self.mind, "party", None)
        if not (self.cfg.get("group") and crew and party and crew.active() and party.is_leader):
            return
        for mate in party.members():
            if mate.get("online") and mate.get("map") == self.mind.state.get("map"):
                await self.mind.execute([{"action": "whisper", "to": mate["name"],
                                          "text": f"[explore:trip:{target['map']}]"}],
                                        source="explore", reason=f"экспедиция: зову {mate['name']}", protocol=True)

    async def on_tag(self, sender, text):
        """Участник группы: лидер зовёт в экспедицию — проверить цель самому и пойти."""
        m = TAG.search(text or "")
        party = getattr(self.mind, "party", None)
        if not m or not party or party.is_leader or sender != party.leader or not self.cfg.get("group"):
            return False
        hmap = m.group(2)
        why = self.blocked(self.mind.state, joining=True)
        rec = None
        if not why:
            rec, why = self.check_target(hmap, self.mind.state.get("lv"), need_unknown=False)
        if why:
            self.mind.write_decision({"type": "explore", "event": "explore_decline", "map": hmap, "from": sender,
                                      "why": why})
            return False
        return await self.start(rec, led_by=sender)

    # ---------- такт ----------

    async def send(self, now, force=False):
        trip = self.trip
        state = self.mind.state
        if not trip or (not force and now - self.last_sent < RESEND):
            return
        # Точку (клетку прибытия) шлём только при town_point: клетка у самого варпа, а lockMap_rand может выбрать
        # клетку в зоне варпа и унести тело обратно. Без точки OpenKore просто доходит до карты (в городе стоит).
        want_point = self.cfg.get("town_point", False) and trip.get("kind") != "field" and trip.get("x") is not None
        ok = state.get("lock_map") == trip["map"] and (
            (state.get("lock_x") == trip.get("x") and state.get("lock_y") == trip.get("y")) if want_point
            else state.get("lock_x") is None)
        if ok:
            return
        may_move = getattr(self.mind, "may_move", None)
        if may_move and not may_move("routine")[0]:
            return
        self.last_sent = now
        action = {"action": "explore", "map": trip["map"]}
        if want_point:
            action.update(x=trip["x"], y=trip["y"])
        await self.mind.execute([action], source="explore", reason=f"экспедиция: к {trip['map']}", protocol=True)

    async def tick(self):
        now = self.clock()
        state = self.mind.state
        if not getattr(self.mind, "fresh_state", True):
            return
        self.check_back(now, state)
        trip = self.trip
        if not trip:
            return
        why = self.abort_reason(now, state)
        if why:
            await self.finish(now, ok=False, why=why)
            return
        if trip["phase"] == "go":
            if state.get("map") == trip["map"]:
                await self.arrive(now, state)
            elif now >= trip["deadline"]:
                await self.finish(now, ok=False, why=f"не дошёл до {trip['map']} за {self.cfg['travel_minutes']} мин")
                return
        elif trip["phase"] == "stay" and now >= trip["stay_until"]:
            await self.finish(now, ok=True, why="осмотрелся")
            return
        await self.send(now)

    def abort_reason(self, now, state):
        trip = self.trip
        if state.get("dead"):
            return "погиб"
        if self.alarm:
            return self.alarm
        hp = state.get("hp_pct")
        if hp is not None and hp < self.cfg["abort_hp"]:
            return f"HP {hp}% — возвращаюсь"
        if self.mind.plans.store.active():
            return "план встречи"
        social = getattr(self.mind, "social", None)
        if social and social.is_night(now):
            return "ночь"
        r = getattr(self.mind, "routine", None)
        win = r.sleep_window(now) if r and hasattr(r, "sleep_window") else None
        if win and win[0] - 1800 <= now < win[1]:
            return "пора спать"
        if r and r.st and r.st.get("mode") == "sleep":
            return "сон"
        if r and r.st and r.st.get("nap_until", 0) > now:      # review2: сторож попросил уснуть раньше (request_sleep)
            return "пора спать"
        if trip.get("led_by"):
            party = getattr(self.mind, "party", None)
            lead = party.member(trip["led_by"]) if party else None
            if not lead or not lead.get("online"):
                return f"{trip['led_by']} не в группе или не в игре"
        return None

    async def arrive(self, now, state):
        trip = self.trip
        hmap = trip["map"]
        places = self.mind.mem.get("places") or {}
        p = places.get(hmap) or {}
        new = not (p.get("source") == "seen" or p.get("first"))
        m = self.atlas.maps.get(hmap) or {}
        p.update(source="seen", first=p.get("first", now), last=now, visits=p.get("visits", 0) + 1,
                 kafra=bool(m.get("kafra")), shops=len(m.get("shops") or []), explored=now)
        places[hmap] = p
        self.mind.mem.set("places", places)
        trip.update(phase="stay", arrived=now, stay_until=now + trip["stay"], new=new or trip.get("new", False))
        self.st["arrived_at"] = now
        self.save()
        extra = []
        if m.get("kafra"):
            extra.append("есть Kafra")
        if m.get("shops"):
            extra.append(f"торговцев {len(m['shops'])}")
        text = f"Добрался до {hmap}" + (" — новое место" if trip["new"] else "") + (f" ({', '.join(extra)})" if extra else "") + "."
        self.note("explore_arrived", text, 3 if trip["new"] else 1, map=hmap, hops=trip.get("hops"),
                  new=trip["new"], led_by=trip.get("led_by"))
        if trip["new"]:
            self.mind.mem.add_event("explore_found", {"map": hmap, "hops": trip.get("hops"), "kind": trip.get("kind"),
                                                      "kafra": bool(m.get("kafra")), "shops": len(m.get("shops") or [])})
            crew = getattr(self.mind, "crew", None)
            if crew and crew.active():
                await crew.say("explore", map=hmap)

    async def finish(self, now, ok, why):
        trip = self.trip
        hmap = trip["map"]
        arrived = trip.get("arrived")
        deaths = self.deaths_on(hmap, trip["started"])
        kills_rows = self.mind.mem.db.execute(
            "SELECT data FROM events WHERE kind = 'kill' AND ts >= ?", (arrived or now,)).fetchall() if arrived else []
        kills, mobs = 0, set()
        for (d,) in kills_rows:
            try:
                e = json.loads(d)
            except ValueError:
                continue
            if e.get("map") in (None, hmap):
                kills += 1
                if e.get("monster"):
                    mobs.add(str(e["monster"]))
        if arrived and mobs:
            places = self.mind.mem.get("places") or {}
            p = places.get(hmap) or {}
            p["monsters"] = sorted(set(p.get("monsters") or []) | mobs)[:12]
            places[hmap] = p
            self.mind.mem.set("places", places)
        minutes = (now - arrived) / 60 if arrived else 0
        rumor = None
        if deaths or trip.get("alarm_here"):
            rumor = "danger"                              # погиб там или тревога на самой карте
        elif arrived and minutes >= 5 and kills / max(minutes / 60, 1e-9) >= self.cfg["rich_kills_per_hour"]:
            rumor = "rich"
        elif arrived and ok and trip.get("new"):
            rumor = "new"
        self.st["trip"] = None
        self.st["back"] = {"map": hmap, "since": now, "ok": ok}
        if arrived:
            self.st["found"] = (self.st.get("found") or [])[-19:] + [{"map": hmap, "ts": arrived, "new": trip.get("new"),
                                                                       "rumor": rumor}]
        self.save()
        self.alarm = None
        routine = getattr(self.mind, "routine", None)
        if routine:
            routine.last_sent = 0                         # распорядок сразу ведёт тело в город
            if routine.st and routine.st.get("mode") == "town":
                routine.st["arrived"] = False             # по возвращении — «я в городе», сесть отдохнуть
            routine.set_goal(f"возвращаюсь в {self.town()}")
        text = (f"Экспедиция на {hmap} окончена: {why}" if arrived else f"Экспедиция на {hmap} прервана: {why}") + \
            (f", побед {kills}" if kills else "") + ". Возвращаюсь."
        self.note("explore_done", text, 2, map=hmap, ok=ok, arrived=bool(arrived), why=why, kills=kills,
                  minutes=round(minutes, 1), rumor=rumor)
        if rumor:
            share = getattr(self.mind, "share_rumor", None)
            if share:
                await share(hmap, rumor)

    def check_back(self, now, state):
        back = self.st.get("back")
        if not back:
            return
        if state.get("map") == self.town():
            self.st.pop("back", None)
            self.save()
            self.mind.mem.add_event("explore_returned", {"map": back["map"],
                                                          "minutes": round((now - back["since"]) / 60, 1)})
            self.mind.write_decision({"type": "explore", "event": "explore_returned", "map": back["map"]})
        elif now - back["since"] > self.cfg["return_minutes"] * 60:
            self.st.pop("back", None)
            self.save()
            self.mind.write_decision({"type": "explore", "event": "explore_return_late", "map": back["map"]})

    def deaths_on(self, hmap, since):
        rows = self.mind.mem.db.execute("SELECT data FROM events WHERE kind = 'died' AND ts >= ?", (since,))
        n = 0
        for (d,) in rows:
            try:
                if json.loads(d).get("map") == hmap:
                    n += 1
            except ValueError:
                continue
        return n

    # ---------- события ----------

    def on_event(self, kind, event):
        """Тревога survival/danger/escape или смерть во время экспедиции — вернуться (abort в следующий такт)."""
        trip = self.trip
        if not trip:
            return
        if kind in ALARMS:
            self.alarm = f"опасность ({kind}) — возвращаюсь"
        elif kind == "died":
            self.alarm = "погиб"
        else:
            return
        if self.mind.state.get("map") == trip["map"]:
            trip["alarm_here"] = True
            self.save()

    # ---------- для рассказов и отчёта ----------

    def last_trip(self, since=0):
        """Последняя экспедиция с прибытием (для social: тема «где был»)."""
        found = [f for f in self.st.get("found") or [] if f.get("ts", 0) >= since]
        return found[-1] if found else None

    def summary(self):
        trip = self.trip
        if trip:
            return {"экспедиция": trip["map"], "этап": {"go": "в пути", "stay": "на месте"}.get(trip["phase"])}
        return None
