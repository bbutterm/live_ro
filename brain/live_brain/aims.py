"""Недельные цели жителя (ORG-038). Правила без LLM; прогресс — только по фактам игры и памяти.

Раз в неделю житель выбирает PER_WEEK цели из шаблонов по характеру (traits из personas) и состоянию:
    level    — уровень +N (N по уровню: 3 до 40, 2 до 70, дальше 1);       черта diligence, мотив progress;
    job      — сменить профессию (доступна, когда job_lv ≥ JOB_READY);       diligence, progress;
    zeny     — накопить ещё N зени (30% текущих, от 10 000 до 200 000);     greed, wealth;
    new_map  — побывать в новом месте (places: first после начала недели); curiosity, curiosity;
    friend   — подружиться с жителем (отношение +2 к самому близкому);      sociability, social;
    help     — помочь жителям HELP_TARGET раз (heal_given, gift_given);     generosity, care.
Оценка шаблона = черта + U(0, 0.25); недоступные (нет жителей, рано для профессии) пропускаются.
Прогресс считается по памяти (события, kv places, отношения) и состоянию тела; цель выполнена —
воспоминание fact и событие aim_done. Конец недели — итог по каждой цели (aim_result) в память и шину.
Срок годности: к середине недели прогноз (темп × неделя) < половины цели — цель заменяется
другой один раз (aim_replaced), чтобы недостижимое не висело всю неделю.
Влияние на мотивы: boost(need) — множитель 1.0..MAX_BOOST для мотива невыполненной цели (тем больше,
чем меньше сделано и ближе конец недели); needs.py подключит его позже. Модель видит цели в промпте.
Смена профессии по данным игры (job в состоянии изменился) пишется как событие job_changed.
"""
import logging
import random
import time

log = logging.getLogger("aims")

WEEK = 7 * 86400
PER_WEEK = 2
TICK_SEC = 60
JOB_READY = 30
HELP_TARGET = 3
MAX_BOOST = 1.5
NOVICE = ("Novice", "Super Novice")
SECOND_JOBS_HINT = ("Knight", "Crusader", "Priest", "Monk", "Wizard", "Sage", "Hunter", "Bard", "Dancer",
                    "Assassin", "Rogue", "Blacksmith", "Alchemist")

TEMPLATES = {
    "level": {"trait": "diligence", "need": "progress"},
    "job": {"trait": "diligence", "need": "progress"},
    "zeny": {"trait": "greed", "need": "wealth"},
    "new_map": {"trait": "curiosity", "need": "curiosity"},
    "friend": {"trait": "sociability", "need": "social"},
    "help": {"trait": "generosity", "need": "care"},
}


class Aims:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE = "aims", "aims"
    TICK_ORDER = 150
    PROMPT = [("цели_недели", "summary", 220)]

    def __init__(self, mind, clock=None, rng=None):
        self.mind = mind
        self.clock = clock or (lambda: time.time())   # review: время при вызове — реплей подменяет time.time
        self.rng = rng or random.Random()
        self.next_tick = 0.0

    # ---------- данные ----------

    @property
    def st(self):
        return self.mind.mem.get("aims") or {}

    def save(self, st):
        self.mind.mem.set("aims", st)

    def traits(self):
        needs = getattr(self.mind, "needs", None)
        return needs.t if needs else {}

    # ---------- шаблоны ----------

    def make(self, kind, state, now):
        lv, job, jlv, zeny = state.get("lv"), state.get("job"), state.get("job_lv"), state.get("zeny")
        if kind == "level" and isinstance(lv, int) and lv < 99:
            n = 3 if lv < 40 else 2 if lv < 70 else 1
            return {"kind": kind, "text": f"поднять уровень с {lv} до {lv + n}", "base": lv, "target": n}
        if kind == "job" and job and isinstance(jlv, int):
            ready = jlv >= 9 if job in NOVICE else jlv >= JOB_READY
            if ready and not any(job.startswith(s) for s in SECOND_JOBS_HINT):
                return {"kind": kind, "text": f"сменить профессию ({job}, уровень профессии {jlv})",
                        "base": job, "target": 1}
            return None
        if kind == "zeny" and isinstance(zeny, int):
            target = int(min(200000, max(10000, round(zeny * 0.3, -3))))
            return {"kind": kind, "text": f"накопить ещё {target} зени", "base": zeny, "target": target}
        if kind == "new_map":
            n = 2 if self.traits().get("curiosity", 0.5) > 0.6 else 1
            return {"kind": kind, "text": "побывать в новом месте" + (f" ({n})" if n > 1 else ""), "base": now,
                    "target": n}
        if kind == "friend":
            peers = sorted(self.mind.ctx.peers)
            if not peers:
                return None
            aff = {p: (self.mind.mem.relation(p) or {}).get("affinity", 0) for p in peers}
            cand = [p for p in peers if aff[p] < 9]
            if not cand:
                return None
            peer = max(cand, key=lambda p: (aff[p], p))
            return {"kind": kind, "text": f"подружиться с {peer}", "peer": peer, "base": aff[peer], "target": 2}
        if kind == "help":
            if not self.mind.ctx.peers:
                return None
            return {"kind": kind, "text": f"помочь жителям {HELP_TARGET} раза (лечение, подарок)", "base": now,
                    "target": HELP_TARGET}
        return None

    def choose(self, state, now, exclude=()):
        t = self.traits()
        scored = []
        for kind, tpl in TEMPLATES.items():
            if kind in exclude:
                continue
            aim = self.make(kind, state, now)
            if aim:
                scored.append((t.get(tpl["trait"], 0.5) + self.rng.uniform(0, 0.25), kind, aim))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [a for _, _, a in scored]

    # ---------- прогресс ----------

    def progress(self, aim, state, since):
        k = aim["kind"]
        mem = self.mind.mem
        if k == "level":
            lv = state.get("lv")
            return max(0, lv - aim["base"]) if isinstance(lv, int) else 0
        if k == "job":
            return 1 if state.get("job") and state.get("job") != aim["base"] else 0
        if k == "zeny":
            z = state.get("zeny")
            return max(0, z - aim["base"]) if isinstance(z, int) else 0
        if k == "new_map":
            return sum(1 for p in (mem.get("places") or {}).values() if p.get("first", 0) >= aim["base"])
        if k == "friend":
            return max(0, (mem.relation(aim["peer"]) or {}).get("affinity", 0) - aim["base"])
        if k == "help":
            return mem.count_events("heal_given", aim["base"]) + mem.count_events("gift_given", aim["base"])
        return 0

    # ---------- тик ----------

    def tick(self, now=None):
        now = now or self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + TICK_SEC
        state = self.mind.state or {}
        if not state.get("lv") or not getattr(self.mind, "fresh_state", True):
            return
        self.watch_job(state)
        st = self.st
        if st.get("until") and now >= st["until"]:
            self.finish_week(st, state, now)
            st = {}
        if not st.get("items"):
            st = self.new_week(state, now)
            if not st:
                return
        changed = False
        for i, aim in enumerate(list(st["items"])):
            if aim.get("done"):
                continue
            p = self.progress(aim, state, st["start"])
            if p != aim.get("progress"):
                aim["progress"] = p
                changed = True
            if p >= aim["target"]:
                aim["done"] = now
                changed = True
                self.mind.mem.remember(f"Цель недели выполнена: {aim['text']} — по данным игры.", 3)
                self.mind.mem.add_event("aim_done", {"aim": aim["kind"], "text": aim["text"]})
                continue
            elapsed = now - st["start"]
            if (not aim.get("replaced") and elapsed >= WEEK / 2
                    and p / elapsed * WEEK < aim["target"] / 2):
                new = self.choose(state, now, exclude=[a["kind"] for a in st["items"]])
                if new:
                    rep = dict(new[0], replaced=aim["text"], progress=0)
                    if rep["kind"] in ("new_map", "help"):
                        rep["base"] = now
                    st["items"][i] = rep
                    changed = True
                    self.mind.mem.remember(f"Цель «{aim['text']}» за неделю не успеть ({p}/{aim['target']}) — "
                                           f"беру другую: {rep['text']}.", 2, kind="note")
                    self.mind.mem.add_event("aim_replaced", {"old": aim["text"], "new": rep["text"]})
        if changed:
            self.save(st)

    def new_week(self, state, now):
        chosen = self.choose(state, now)[:PER_WEEK]
        if not chosen:
            return None
        st = {"start": now, "until": now + WEEK, "items": [dict(a, progress=0) for a in chosen]}
        self.save(st)
        for a in chosen:
            self.mind.mem.add_event("aim_new", {"aim": a["kind"], "text": a["text"]})
        self.mind.mem.remember("Цели на неделю: " + "; ".join(a["text"] for a in chosen) + ".", 2, kind="note")
        log.info("цели недели: %s", [a["text"] for a in chosen])
        return st

    def finish_week(self, st, state, now):
        for aim in st.get("items") or []:
            p = aim.get("progress", 0) if aim.get("done") else self.progress(aim, state, st["start"])
            done = bool(aim.get("done")) or p >= aim["target"]
            self.mind.mem.remember(f"Итог недели: «{aim['text']}» — " + ("выполнено." if done else
                                   f"не выполнено ({p}/{aim['target']})."), 3)
            self.mind.mem.add_event("aim_result", {"aim": aim["kind"], "text": aim["text"], "done": done,
                                                   "progress": p, "target": aim["target"]})
        self.save({})

    def watch_job(self, state):
        job = state.get("job")
        last = self.mind.mem.get("aims_job")
        if job and job != last:
            self.mind.mem.set("aims_job", job)
            if last:
                self.mind.mem.add_event("job_changed", {"from": last, "to": job, "job_lv": state.get("job_lv")})
                self.mind.mem.remember(f"Сменил профессию: {last} → {job} (по данным игры).", 5)

    # ---------- наружу ----------

    def boost(self, need, now=None):
        """Множитель мотива 1.0..MAX_BOOST от невыполненных целей недели (подключается в needs.py)."""
        now = now or self.clock()
        st = self.st
        if not st.get("items") or now >= st.get("until", 0):
            return 1.0
        frac_time = min(1.0, max(0.0, (now - st["start"]) / WEEK))
        best = 1.0
        for aim in st["items"]:
            if aim.get("done") or TEMPLATES.get(aim["kind"], {}).get("need") != need:
                continue
            left = 1 - min(1.0, aim.get("progress", 0) / max(aim["target"], 1))
            best = max(best, 1 + (MAX_BOOST - 1) * left * (0.5 + 0.5 * frac_time))
        return round(min(MAX_BOOST, best), 3)

    def summary(self):
        st = self.st
        if not st.get("items"):
            return None
        until = time.strftime("%Y-%m-%d", time.localtime(st["until"]))
        return [{"цель": a["text"], "прогресс": f"{a.get('progress', 0)}/{a['target']}",
                 "выполнена": bool(a.get("done")), "до": until} for a in st["items"]]
