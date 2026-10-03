"""Жизненный путь жителя: мечта на месяцы (ORG-081, ТЗ Т-17). Правила без LLM, прогресс — только по фактам.

У жителя одна мечта (kv dream) из шаблона DREAMS, выбранная по чертам характера (traits, needs.py), полю
persona.dream (+PERSONA_BONUS) и случайности U(0, 0.25). Мечта — 2–4 этапа; этап — метрика и цель:
    job2      стать второй профессией (Swordman -> Knight, Acolyte -> Priest…): первая профессия (если Novice),
              уровень профессии JOB2_LV, сама смена (state.job);                         diligence; мотив progress
    cards     собрать карт в альбом: +1, +половина, +N (kv collection, ORG-074);          greed, whimsy; progress
    explorer  обойти окрестности города отдыха: карты *_fild* не дальше REGION_HOPS переходов (explore_reach.json),
              треть, две трети, все (kv places: first);                                   curiosity; curiosity
    pet       завести питомца: поймал (pet_tamed), вылупился (pet_hatched / state.pet), PET_DAYS дней с ним;
                                                                                          generosity, sociability; care
    guild     основать гильдию или вступить в гильдию жителей: друзей с отношением ≥ FRIEND_AFFINITY, затем
              state.guild (по пакету сервера);                                            sociability; social
    rich      скопить состояние: зени в кармане + банк (kv savings.bank, ORG-073) — четверть, половина, вся цель;
                                                                                          greed; wealth
    wedding   свадьба rAthena с женихом/невестой (ORG-062, wed.py): уровень 45, копилка на плату, кольцо и наряд,
              обряд у епископа (брак — кольцо или объявление сервера); только помолвленным разнополой парой.
              Помолвка один раз сбрасывает мечту другого вида, разрыв — уходит сама.  sociability; social
Недоступный шаблон пропускается: нужен модуль (collection, pets, guild), жители (guild), или мечта уже исполнена
(второй профессией, с питомцем, в гильдии, окрестности обойдены).

Этапы питают недельные цели aims.py (не дублируя их): шаблон aims «dream» — шаг текущего этапа на неделю
(weekly_aim: «поднять уровень профессии на 3», «найти новую карту»…), его прогресс — aim_progress по той же
метрике; шаблоны aims, продвигающие этап (job, level, new_map, friend, zeny), получают бонус AIM_BONUS (aim_bonus).
Мотив этапа (needs.weighted) усиливается множителем boost 1.0..MAX_BOOST.

Смена мечты:
    исполнена (все этапы) -> dream_done: воспоминание важности 5, шина мира 5, летопись; следующая — другого вида;
    невозможна (модуль выключили, профессия ушла в сторону, гильдия без жителей) или поворот: ≥ DEATHS_TURN смертей
    за неделю у explorer, ссора с лучшим другом у guild, stale_days дней без нового этапа -> dream_changed:
    воспоминание note «решил(а) оставить мечту …».
Копилка (ORG-073, savings.py) берёт цель в зени из save_target(). Тема разговора «dream» (реестр social): о мечте
каждому жителю один раз на этап. Выключатель: BRAIN_DISABLE=dream или goals.json "dream": {"enabled": false}.
"""
import json
import logging
import math
import random
import time
from pathlib import Path

from . import interests as interests_mod                 # interest: ORG-103

log = logging.getLogger("dream")

REACH_PATH = Path(__file__).resolve().parents[1] / "world" / "explore_reach.json"
DEFAULTS = {"enabled": True, "tick_seconds": 60, "stale_days": 45, "region_hops": 2,
            "save": {"job2": 30000, "explorer": 10000, "pet": 5000, "cards": 0, "guild": 0}}
AIM_BONUS = 0.3
MAX_BOOST = 1.2
PERSONA_BONUS = 0.5
JOB2_LV = 40
PET_DAYS = 7
FRIEND_AFFINITY = 3
DEATHS_TURN = 3
HISTORY = 12
NOVICE = ("Novice", "Super Novice")
SECOND = {"Swordman": "Knight", "Swordsman": "Knight", "Acolyte": "Priest", "Mage": "Wizard", "Archer": "Hunter",
          "Thief": "Assassin", "Merchant": "Blacksmith"}
SECOND_ALL = ("Knight", "Crusader", "Priest", "Monk", "Wizard", "Sage", "Hunter", "Bard", "Dancer", "Assassin",
              "Rogue", "Blacksmith", "Alchemist", "Lord Knight", "High Priest", "High Wizard", "Sniper",
              "Assassin Cross", "Whitesmith")

# вид -> черты (вес), мотив, шаблоны aims, продвигающие мечту
DREAMS = {
    "job2": {"traits": {"diligence": 1.0}, "need": "progress", "aims": ("job", "level")},
    "cards": {"traits": {"greed": 0.6, "whimsy": 0.4}, "need": "progress", "aims": ("level",)},
    "explorer": {"traits": {"curiosity": 1.0}, "need": "curiosity", "aims": ("new_map",)},
    "pet": {"traits": {"generosity": 0.6, "sociability": 0.4}, "need": "care", "aims": ("help",)},
    "guild": {"traits": {"sociability": 1.0}, "need": "social", "aims": ("friend",)},
    "rich": {"traits": {"greed": 1.0}, "need": "wealth", "aims": ("zeny",)},
    "wedding": {"traits": {"sociability": 1.0}, "need": "social", "aims": ("level", "zeny")},   # wed: ORG-062
}
WED_BONUS = 1.0                    # wed: помолвленные выбирают свадьбу первой
PHRASES = {   # ≤ 60 символов без метки
    "dream": ["Знаешь, о чём мечтаю? {dream}.",
              "У меня мечта: {dream}. Этап {stage} из {stages}.",
              "Иду к мечте — {dream}. Пока этап {stage} из {stages}."],
    "dream_re": ["Хорошая мечта. Получится!", "Ого. Удачи тебе в этом.", "Верю, что дойдёшь."],
}
SHORT_MAX = 34


def load_reach(path=REACH_PATH):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"towns": {}}


class Dream:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "dream", "dream", "dream", True, "world"
    TICK_ORDER = 145                       # до aims (150): цель недели видит свежий этап
    TICK_EVERY = 60             # perf: реестр не зовёт tick до next_tick (modules.py)
    PROMPT = [("мечта", "summary", 225)]   # после целей недели (220)

    def __init__(self, mind, world=None, clock=None, rng=None, reach=None):
        self.mind = mind
        world = world or {}
        self.cfg = dict(DEFAULTS, **(world.get("dream") or {}))
        self.cfg["save"] = dict(DEFAULTS["save"], **((world.get("dream") or {}).get("save") or {}))
        self.town = ((world.get("routine") or {}).get("town") or {}).get("map", "prontera") \
            if isinstance((world.get("routine") or {}).get("town"), dict) else "prontera"
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self._reach = reach
        self.next_tick = 0.0
        social = getattr(mind, "social", None)
        if social is not None and hasattr(social, "register_topic"):
            for key, pool in PHRASES.items():
                social.phrases.setdefault(key, pool)
            social.register_topic("dream", self.facts, said=self.said, chance=0.5)

    # ---------- данные ----------

    @property
    def st(self):
        return self.mind.mem.get("dream") or {}

    def save(self, st):
        self.mind.mem.set("dream", st)

    def traits(self):
        needs = getattr(self.mind, "needs", None)
        return needs.t if needs else {}

    def home_town(self):
        home = getattr(self.mind, "home", None)
        town = getattr(home, "town", None) if home else None
        routine = getattr(self.mind, "routine", None)
        rt = getattr(routine, "town", None) if routine else None
        if isinstance(town, str) and town:
            return town
        if isinstance(rt, dict) and rt.get("map"):
            return rt["map"]
        return self.town

    def region(self):
        """Окрестности города отдыха: поля (*_fild*) не дальше region_hops переходов, куда дойдёт OpenKore."""
        if self._reach is None:
            self._reach = load_reach()
        maps = ((self._reach.get("towns") or {}).get(self.home_town()) or {}).get("maps") or {}
        return sorted(m for m, v in maps.items()
                      if "_fild" in m and isinstance(v, dict) and v.get("hops", 99) <= self.cfg["region_hops"])

    def album(self):
        return len(((self.mind.mem.get("collection") or {}).get("album") or {}))

    def bank(self):
        return int((self.mind.mem.get("savings") or {}).get("bank") or 0)

    def friends(self):
        peers = sorted(p for p in self.mind.ctx.peers if p != (self.mind.state or {}).get("name"))
        return [p for p in peers if (self.mind.mem.relation(p) or {}).get("affinity", 0) >= FRIEND_AFFINITY]

    def ever(self, kind):
        row = self.mind.mem.db.execute("SELECT MIN(ts) FROM events WHERE kind = ?", (kind,)).fetchone()
        return row[0] if row and row[0] else None

    # ---------- метрики (только факты) ----------

    def value(self, metric, state, p=None):
        p = p or {}
        job, jlv, lv = state.get("job"), state.get("job_lv"), state.get("lv")
        if metric == "job_first":
            return 1 if job and job not in NOVICE else 0
        if metric == "job_lv":
            if job == p.get("second") or job in SECOND_ALL:
                return JOB2_LV
            return int(jlv) if isinstance(jlv, int) and job == p.get("first") else 0
        if metric == "job_is":
            return 1 if job and job == p.get("second") else 0
        if metric == "lv":
            return int(lv) if isinstance(lv, int) else 0
        if metric == "cards":
            return self.album()
        if metric == "region":
            places = self.mind.mem.get("places") or {}
            region = set(p.get("region") or [])
            return sum(1 for m, v in places.items() if m in region and isinstance(v, dict) and v.get("first"))
        if metric == "pet":
            has = bool((state.get("pet") or {}).get("has"))
            if has or self.ever("pet_hatched"):
                return 2
            return 1 if self.ever("pet_tamed") else 0
        if metric == "pet_days":
            first = self.ever("pet_hatched")
            if not first or not (state.get("pet") or {}).get("has"):
                return 0
            return int((self.clock() - first) // 86400)
        if metric == "friends":
            return len(self.friends())
        if metric == "guild":
            return 1 if (state.get("guild") or {}).get("name") else 0
        if metric == "wealth":
            return int(state.get("zeny") or 0) + self.bank()
        if metric == "married":                                              # wed: брак по факту (wed.py)
            wed = getattr(self.mind, "wed", None)                            # wed:
            return 1 if wed and wed.married(state) else 0                    # wed:
        if metric == "places_new":
            return sum(1 for v in (self.mind.mem.get("places") or {}).values() if isinstance(v, dict) and v.get("first"))
        return 0

    # ---------- шаблоны ----------

    def make(self, kind, state):
        """Мечта вида kind для текущего состояния -> {kind, text, short, params, stages} или None (недоступна)."""
        job, lv = state.get("job"), state.get("lv")
        if kind == "job2":
            if not job or job in SECOND_ALL:
                return None
            first = job
            if job in NOVICE:
                career = getattr(self.mind, "career", None)
                if career and hasattr(career, "load"):
                    career.load()
                first = (getattr(career, "target", None) if career else None) or self.mind.persona.get("job")
            second = SECOND.get(first or "")
            if not second:
                return None
            p = {"first": first, "second": second}
            stages = []
            if job in NOVICE:
                stages.append({"text": f"стать {first}", "metric": "job_first", "target": 1})
            stages += [{"text": f"уровень профессии {JOB2_LV}", "metric": "job_lv", "target": JOB2_LV},
                       {"text": f"сменить профессию на {second}", "metric": "job_is", "target": 1}]
            return {"kind": kind, "text": f"стать {second}", "short": f"стать {second}", "params": p, "stages": stages}
        if kind == "cards":
            if getattr(self.mind, "collection", None) is None:
                return None
            n = 5 if self.traits().get("greed", 0.5) >= 0.6 else 3
            base = self.album()
            return {"kind": kind, "text": f"собрать {base + n} карт в альбом", "short": f"собрать {base + n} карт",
                    "params": {"base": base, "n": n},
                    "stages": [{"text": "найти новую карту", "metric": "cards", "target": base + 1},
                               {"text": f"карт в альбоме: {base + math.ceil(n / 2)}", "metric": "cards",
                                "target": base + math.ceil(n / 2)},
                               {"text": f"карт в альбоме: {base + n}", "metric": "cards", "target": base + n}]}
        if kind == "explorer":
            region = self.region()
            p = {"region": region, "town": self.home_town()}
            if len(region) < 3 or self.value("region", state, p) >= len(region):
                return None
            n = len(region)
            marks = sorted({math.ceil(n / 3), math.ceil(2 * n / 3), n})
            return {"kind": kind, "text": f"обойти все окрестности {p['town']} ({n} мест)",
                    "short": f"обойти окрестности {p['town']}", "params": p,
                    "stages": [{"text": f"побывать в {m} из {n} мест окрестностей", "metric": "region", "target": m}
                               for m in marks]}
        if kind == "pet":
            if getattr(self.mind, "pets", None) is None or (state.get("pet") or {}).get("has"):
                return None
            return {"kind": kind, "text": "завести питомца и прожить с ним неделю", "short": "завести питомца",
                    "params": {}, "stages": [{"text": "поймать питомца (яйцо)", "metric": "pet", "target": 1},
                                             {"text": "вырастить питомца из яйца", "metric": "pet", "target": 2},
                                             {"text": f"{PET_DAYS} дней с питомцем", "metric": "pet_days",
                                              "target": PET_DAYS}]}
        if kind == "guild":
            peers = [p for p in self.mind.ctx.peers if p != state.get("name")]
            if getattr(self.mind, "guild", None) is None or not peers or (state.get("guild") or {}).get("name"):
                return None
            need = min(2, len(peers))
            return {"kind": kind, "text": "основать гильдию с друзьями или вступить в гильдию жителей",
                    "short": "своя гильдия с друзьями", "params": {},
                    "stages": [{"text": f"друзей с отношением {FRIEND_AFFINITY}+: {need}", "metric": "friends",
                                "target": need},
                               {"text": "быть в гильдии жителей", "metric": "guild", "target": 1}]}
        if kind == "rich":
            if not isinstance(state.get("zeny"), int):
                return None
            have = int(state["zeny"]) + self.bank()
            target = int(min(5_000_000, max(100_000, math.ceil(have * 3 / 50_000) * 50_000)))
            return {"kind": kind, "text": f"скопить {target} зени", "short": f"скопить {target} зени",
                    "params": {"target": target},
                    "stages": [{"text": f"состояние {target // 4} зени", "metric": "wealth", "target": target // 4},
                               {"text": f"состояние {target // 2} зени", "metric": "wealth", "target": target // 2},
                               {"text": f"состояние {target} зени", "metric": "wealth", "target": target}]}
        if kind == "wedding":                                                # wed: ORG-062 мечта пары
            wed = getattr(self.mind, "wed", None)                            # wed:
            t = wed.terms() if wed else None                                 # wed:
            if not t:                                                        # wed:
                return None                                                  # wed:
            short = f"свадьба с {t['peer']}"                                 # wed:
            return {"kind": kind, "text": f"сыграть свадьбу с {t['peer']}", "short": short, "params": t,   # wed:
                    "stages": [{"text": f"уровень {t['level']}", "metric": "lv", "target": t["level"]},   # wed:
                               {"text": f"скопить {t['cost']} зени на свадьбу", "metric": "wealth",       # wed:
                                "target": t["cost"]},                                                      # wed:
                               {"text": "обряд у епископа в соборе", "metric": "married", "target": 1}]}  # wed:
        return None

    def choose(self, state, exclude=()):
        t = self.traits()
        favorite = self.mind.persona.get("dream")
        scored = []
        for kind, tpl in DREAMS.items():
            if kind in exclude:
                continue
            d = self.make(kind, state)
            if not d:
                continue
            score = sum(w * t.get(trait, 0.5) for trait, w in tpl["traits"].items()) / sum(tpl["traits"].values())
            score *= interests_mod.weight(self.mind, interests_mod.DREAM_INTEREST.get(kind))   # interest: ORG-103
            score += self.rng.uniform(0, 0.25) + (PERSONA_BONUS if favorite == kind else 0)
            score += WED_BONUS if kind == "wedding" else 0                   # wed:
            scored.append((score, kind, d))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [d for _, _, d in scored]

    # ---------- такт ----------

    def tick(self):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        state = self.mind.state or {}
        if not state.get("lv") or not getattr(self.mind, "fresh_state", True) or state.get("dead"):
            return
        st = self.st
        changed = False
        if not st.get("kind"):
            st = self.start(st, state, now)
            if not st:
                return
            changed = True
        else:
            why = self.impossible(st, state, now)
            if why:
                self.drop(st, why, now)
                return
        while st["stage"] < len(st["stages"]):
            stage = st["stages"][st["stage"]]
            v = self.value(stage["metric"], state, st.get("params"))
            if v != stage.get("value"):
                stage["value"] = v
                changed = True
            if v < stage["target"]:
                break
            stage["done"] = now
            st["stage"] += 1
            st["moved"] = now
            changed = True
            n, total = st["stage"], len(st["stages"])
            self.mind.mem.add_event("dream_stage", {"dream": st["text"], "stage": stage["text"], "n": n,
                                                    "of": total})
            self.mind.mem.remember(f"Мечта «{st['text']}»: этап {n} из {total} — {stage['text']} (по данным игры).", 3)
            self.mind.write_decision({"type": "dream", "event": "stage", "dream": st["kind"], "n": n, "of": total})
        if st["stage"] >= len(st["stages"]):
            self.finish(st, now)
            return
        if changed:
            self.save(st)

    def start(self, st, state, now):
        recent = [h["kind"] for h in (st.get("history") or [])[-2:]]
        cand = self.choose(state, exclude=recent) or self.choose(state)
        if not cand:
            return None
        d = cand[0]
        new = dict(d, since=now, moved=now, stage=0, history=st.get("history") or [])
        self.save(new)
        stages = "; ".join(s["text"] for s in d["stages"])
        self.mind.mem.add_event("dream_new", {"dream": d["text"], "type": d["kind"], "stages": len(d["stages"])})
        self.mind.mem.remember(f"Моя мечта: {d['text']}. Этапы: {stages}.", 4, kind="note")
        self.mind.write_decision({"type": "dream", "event": "new", "dream": d["kind"], "text": d["text"]})
        log.info("мечта: %s", d["text"])
        return new

    def impossible(self, st, state, now):
        kind = st["kind"]
        need = {"cards": "collection", "pet": "pets", "guild": "guild"}.get(kind)
        if need and getattr(self.mind, need, None) is None:
            return f"модуль {need} выключен"
        p = st.get("params") or {}
        job = state.get("job")
        if kind == "job2" and job and job not in NOVICE and job != p.get("first") and job != p.get("second"):
            return f"путь ушёл в сторону: я {job}"
        if kind == "guild" and not [x for x in self.mind.ctx.peers if x != state.get("name")]:
            return "рядом нет жителей для гильдии"
        why = self.wed_turn(st, state)                                       # wed: ORG-062
        if why:                                                              # wed:
            return why                                                       # wed:
        week = now - 7 * 86400
        if kind == "explorer" and self.mind.mem.count_events("died", max(week, st["since"])) >= DEATHS_TURN:
            return f"за неделю {DEATHS_TURN}+ смертей — дальние поля не для меня"
        if kind == "guild" and self.mind.mem.count_events("society_quarrel", max(week, st["since"])):
            best = self.best_friend()
            rows = self.mind.mem.db.execute("SELECT data FROM events WHERE kind = 'society_quarrel' AND ts >= ?",
                                            (max(week, st["since"]),)).fetchall()
            if best and any(best in r[0] for r in rows):
                return f"поссорился(ась) с лучшим другом {best}"
        if kind != "wedding" and now - st.get("moved", st["since"]) >= self.cfg["stale_days"] * 86400:   # wed: копят месяцами
            return f"{self.cfg['stale_days']} дней без нового этапа"
        return None

    def wed_turn(self, st, state):                                           # wed: ORG-062
        """Свадьба: разрыв — мечта уходит; помолвка — другая мечта уступает свадьбе (один раз за помолвку)."""
        wed = getattr(self.mind, "wed", None)
        if st["kind"] == "wedding":
            t = wed.terms() if wed else None
            if wed and wed.married(state):
                return None                                                  # последний этап засчитает брак
            if not t or t["peer"] != (st.get("params") or {}).get("peer"):
                return "помолвки больше нет"
            return None
        t = wed.terms() if wed else None
        if not t:
            return None
        tried = any(h.get("kind") == "wedding" and h.get("until", 0) >= t["since"] for h in st.get("history") or [])
        return None if tried else f"помолвка с {t['peer']} — теперь общая мечта: свадьба"

    def best_friend(self):
        peers = [p for p in self.mind.ctx.peers if p != (self.mind.state or {}).get("name")]
        if not peers:
            return None
        aff = {p: (self.mind.mem.relation(p) or {}).get("affinity", 0) for p in peers}
        best = max(peers, key=lambda p: (aff[p], p))
        return best if aff[best] > 0 else None

    def archive(self, st, result, why, now):
        hist = (st.get("history") or []) + [{"kind": st["kind"], "text": st["text"], "since": st["since"],
                                             "until": now, "result": result, "why": why,
                                             "stage": st["stage"], "of": len(st["stages"])}]
        self.save({"history": hist[-HISTORY:]})

    def finish(self, st, now):
        days = int((now - st["since"]) // 86400)
        self.mind.mem.add_event("dream_done", {"dream": st["text"], "type": st["kind"], "days": days})
        self.mind.mem.remember(f"Мечта сбылась: {st['text']} (за {days} дн., по данным игры).", 5)
        self.mind.write_decision({"type": "dream", "event": "done", "dream": st["kind"], "days": days})
        log.info("мечта сбылась: %s", st["text"])
        self.archive(st, "done", None, now)

    def drop(self, st, why, now):
        self.mind.mem.add_event("dream_changed", {"dream": st["text"], "type": st["kind"], "why": why,
                                                  "stage": st["stage"], "of": len(st["stages"])})
        self.mind.mem.remember(f"Решил(а) оставить мечту «{st['text']}»: {why}.", 3, kind="note")
        self.mind.write_decision({"type": "dream", "event": "changed", "dream": st["kind"], "why": why})
        log.info("мечта оставлена: %s (%s)", st["text"], why)
        self.archive(st, "dropped", why, now)

    # ---------- недельные цели (aims.py) и мотивы (needs.py) ----------

    def current(self):
        st = self.st
        if not st.get("kind") or st["stage"] >= len(st["stages"]):
            return None, None
        return st, st["stages"][st["stage"]]

    def weekly_aim(self, state, now):
        """Шаг текущего этапа на неделю — цель aims вида dream (метрика этапа, прирост от base)."""
        st, stage = self.current()
        if not stage:
            return None
        metric = stage["metric"]
        p = st.get("params")
        v = self.value(metric, state, p)
        left = stage["target"] - v
        if left <= 0:
            return None
        step = {"job_lv": 3, "lv": 2, "region": 1, "cards": 1, "friends": 1}.get(metric)
        texts = {"job_lv": "поднять уровень профессии на {n}", "region": "побывать в новом месте окрестностей",
                 "cards": "найти новую карту", "friends": "подружиться ещё с одним жителем"}
        if metric == "wealth":
            step = int(min(left, max(10000, round(stage["target"] * 0.1, -3))))
            text = f"отложить {step} зени на мечту"
        elif metric == "pet_days":
            return None                   # дни идут сами — не цель недели
        elif metric == "married":         # wed: обряд — решение владельца, не цель недели
            return None
        elif step:
            step = min(step, left)
            text = texts[metric].format(n=step)
        else:
            step, text = left, stage["text"]
        return {"kind": "dream", "text": f"{text} (мечта: {st['short']})", "metric": metric, "base": v,
                "target": step, "need": DREAMS[st["kind"]]["need"], "params": p}

    def aim_progress(self, aim, state):
        return max(0, self.value(aim.get("metric"), state, aim.get("params")) - int(aim.get("base") or 0))

    def aim_bonus(self, kind):
        st, stage = self.current()
        if not stage:
            return 0.0
        return AIM_BONUS if kind == "dream" or kind in DREAMS[st["kind"]]["aims"] else 0.0

    def boost(self, need):
        st, stage = self.current()
        if not stage or DREAMS[st["kind"]]["need"] != need:
            return 1.0
        frac = min(1.0, (stage.get("value") or 0) / max(1, stage["target"]))
        return round(1 + (MAX_BOOST - 1) * (1 - frac), 3)

    def save_target(self):
        """Копилка (ORG-073): (сколько зени нужно мечте, на что) или (0, None)."""
        st = self.st
        if not st.get("kind"):
            return 0, None
        if st["kind"] == "rich":
            return int(st["params"]["target"]), st["short"]
        if st["kind"] == "wedding":                                          # wed: плата, кольцо, наряд
            return int(st["params"]["cost"]), st["short"]
        return int(self.cfg["save"].get(st["kind"]) or 0), st["short"]

    # ---------- тема разговора и промпт ----------

    def facts(self, peer, now):
        st, stage = self.current()
        if not stage:
            return None
        told = (st.get("told") or {}).get(peer)
        if told == [st["kind"], st["stage"]]:
            return None
        return {"dream": st["short"][:SHORT_MAX], "stage": st["stage"] + 1, "stages": len(st["stages"])}

    def said(self, peer, facts, now):
        st = self.st
        if st.get("kind"):
            st.setdefault("told", {})[peer] = [st["kind"], st["stage"]]
            self.save(st)

    def summary(self):
        st, stage = self.current()
        if not stage:
            return None
        return {"мечта": st["text"], "этап": f"{st['stage'] + 1}/{len(st['stages'])}: {stage['text']} "
                                             f"({stage.get('value', 0)}/{stage['target']})",
                "с": time.strftime("%Y-%m-%d", time.localtime(st["since"]))}


CHRONICLE_LINES = {
    "dream_new": lambda d: f"мечтает: {d.get('dream')}",
    "dream_stage": lambda d: f"мечта «{d.get('dream')}»: этап {d.get('n')}/{d.get('of')} — {d.get('stage')}",
    "dream_done": lambda d: f"мечта сбылась: {d.get('dream')} (за {d.get('days')} дн.)",
    "dream_changed": lambda d: f"оставил(а) мечту «{d.get('dream')}»: {d.get('why')}",
}
