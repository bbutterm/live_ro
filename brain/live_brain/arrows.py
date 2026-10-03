"""Arrow Crafting — ремесло лучника (ORG-075, ТЗ Т-33). Правила без LLM.

Только для жителя ветки Archer (state.job: Archer, Hunter, Bard, Dancer и их высшие/детские/третьи формы; шаблон
bots/templates/archer, Ilsa). Пока лучника нет, модуль спит: tick ничего не делает и ничего не пишет.

Навык AC_MAKINGARROW даёт Roberto (moc_ruins,118,99, npc/quests/skills/archer_skills.txt:18): JobLevel >= 30
(Hunter/Bard/Dancer — без условия, :37) и предметы (:43) 20 Resin 907, 7 Mushroom Spore 921, 41 Pointed Scale 906,
13 Trunk 1019, 1 Red Potion 501; при всех предметах меню нет, итог — «as I promised, I will teach you the skill» (:47).
Данные — brain/world/crafts.json (scripts/gen_crafts.py). Квест — этап progression в kv arrows.stage:
    exp      job_lv < 30 — копить опыт профессии;
    items    не хватает предметов — список (craft_setup keep держит их от продажи; сбор — лут);
    no_route нет пути OpenKore до moc_ruins (Морокк закрыт до патча порталов) — честная запись один раз, ждёт;
    ready    всё есть и путь есть; при arrows.quest_auto — этап jobChange {path arrows, stage roberto};
    skill    навык есть (подтверждение — только state.craft.skills.AC_MAKINGARROW, а не фраза NPC).
Ремесло: навык есть — не чаще craft_minutes, вне боя и этапов, источник из sources (лут: Trunk, Jellopy, Tree Root),
рецепт — crafts.json arrows (db/create_arrow_db.yml). Действие arrowcraft {item}: мост — «arrowcraft use» (навык),
сервер присылает список (пакет 01AD), мост выбирает предмет (sendArrowCraft). Итог — по факту: стрел в
state.craft.items стало больше (arrows_crafted {source, arrow, n}); событие тела arrowcraft_result — только «отправил».
"""
import json
import logging
import time
from pathlib import Path

from .lifecycle import quest_busy

log = logging.getLogger("arrows")

CRAFTS_PATH = Path(__file__).resolve().parents[1] / "world" / "crafts.json"
PATH, STAGE = "arrows", "roberto"
SKILL = "AC_MAKINGARROW"
ARCHERS = {"Archer", "Hunter", "Bard", "Dancer", "High Archer", "Sniper", "Clown", "Gypsy", "Baby Archer",
           "Baby Hunter", "Baby Bard", "Baby Dancer", "Ranger", "Minstrel", "Wanderer", "Baby Ranger",
           "Baby Minstrel", "Baby Wanderer"}
FIRST = {"Archer", "High Archer", "Baby Archer"}       # у остальных Roberto не спрашивает JobLevel (:37)
DEFAULTS = {
    "enabled": True,
    "tick_seconds": 30,
    "quest_auto": False,          # идти к Roberto самому (путь до Морокка пока не найден)
    "craft_minutes": 20,
    "sources": [1019, 909, 902],  # Trunk, Jellopy, Tree Root — лут, из которого делать стрелы
    "min_hp": 50,
    "verify_minutes": 3,
    "wait_result_minutes": 240,
    "retry_hours": 12,
    "keep_every_minutes": 10,
    "brag_days": 3,
    "brag_chance": 0.4,
}
PHRASES = {
    "arrows": ["Наделал(а) {n} стрел из {source} своими руками.", "Сделал(а) {n} {arrow} — хватит надолго."],
    "arrows_re": ["Ловко! Поделишься парой?", "Полезное ремесло.", "Вот это руки!"],
}


def load_crafts(path=CRAFTS_PATH):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Arrows:
    # реестр модулей (modules.py, W8)
    ATTR, FEATURE, CONFIG, ENABLED = "arrows", "arrows", "arrows", True
    REQUIRES, ARGS = ("world", "routine"), "world"
    TICK_ORDER = 104                          # после herbal (103), до explorer (110)
    EVENTS = {"job_change_result": {"call": "on_result", "consume": "result"},     # только path arrows
              "arrowcraft_result": {"call": "on_craft", "own": True}}              # событие тела — не для LLM
    EVENT_ORDER = 16
    PROMPT = [("ремесло", "summary", 208)]

    def __init__(self, mind, world=None, clock=None, crafts=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("arrows") or {}))
        self.clock = clock or (lambda: time.time())
        self.crafts = crafts if crafts is not None else load_crafts()
        self.roberto = self.crafts.get("roberto") or {}
        self.recipes = self.crafts.get("arrows") or {}
        self.st = mind.mem.get("arrows") or {}
        for key, val in (("made", {}), ("told", {}), ("log", [])):
            self.st.setdefault(key, val)
        self.next_tick = 0.0
        self.keep_sent = 0.0
        social = getattr(mind, "social", None)
        if social is not None and hasattr(social, "register_topic"):
            for key, pool in PHRASES.items():
                social.phrases.setdefault(key, pool)
            social.register_topic("arrows", self.facts, said=self.said, chance=self.cfg["brag_chance"],
                                  interest="craft")   # interest: ORG-103

    # ---------- данные ----------

    def save(self):
        self.mind.mem.set("arrows", self.st)

    def is_archer(self, state=None):
        return (state if state is not None else self.mind.state).get("job") in ARCHERS

    def craft(self, state=None):
        return (state if state is not None else self.mind.state).get("craft") or {}

    def count(self, item, state=None):
        state = state if state is not None else self.mind.state
        c = (self.craft(state).get("items") or {}).get(str(item))
        if c is None:
            c = (state.get("items") or {}).get(str(item))
        return int(c or 0)

    def skill(self, state=None):
        return int((self.craft(state).get("skills") or {}).get(SKILL) or 0)

    def town(self):
        r = getattr(self.mind, "routine", None)
        return (r.cfg.get("town") or {}).get("map") if r else None

    def route(self):
        rec = ((self.crafts.get("routes") or {}).get(self.town()) or {}).get("roberto") or {}
        return rec if rec.get("legs") is not None and rec.get("stand") else None

    def missing(self, state=None):
        """{id: сколько не хватает} для Roberto."""
        out = {}
        for item, need in (self.roberto.get("items") or {}).items():
            have = self.count(item, state)
            if have < need:
                out[item] = need - have
        return out

    def quest_stage(self, state=None):
        state = state if state is not None else self.mind.state
        if self.skill(state):
            return "skill"
        if state.get("job") in FIRST and int(state.get("job_lv") or 0) < int(self.roberto.get("job_lv", 30)):
            return "exp"
        if self.missing(state):
            return "items"
        return "ready" if self.route() else "no_route"

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        if now < self.next_tick or not self.mind.fresh_state:
            return
        state = self.mind.state
        if not self.is_archer(state) or not self.craft(state):
            return                                         # лучника нет — модуль спит
        self.next_tick = now + self.cfg["tick_seconds"]
        stage = self.quest_stage(state)
        self.set_stage(stage, state, now)
        if stage == "skill":
            await self.crafting(now, state)
            return
        await self.ensure_keep(now, state)
        if stage == "ready":
            await self.quest(now, state)
        elif self.st.get("quest") and not (state.get("job_change") or {}).get("running") \
                and now - self.st["quest"]["ts"] >= self.cfg["wait_result_minutes"] * 60:
            self.quest_failed(now, "нет итога этапа от тела")

    def set_stage(self, stage, state, now):
        if stage == self.st.get("stage"):
            return
        before = self.st.get("stage")
        self.st["stage"] = stage
        self.save()
        if stage == "skill":
            quest = self.st.pop("quest", None)
            self.save()
            text = ("Roberto научил меня делать стрелы — навык Arrow Crafting есть!" if quest else
                    "Теперь умею делать стрелы — навык Arrow Crafting появился." if before else
                    "Умею делать стрелы (Arrow Crafting).")
            self.note("arrows_skill", text, 4 if quest or before else 1, learned=bool(quest or before),
                      roberto=bool(quest))
            return
        texts = {"exp": f"Arrow Crafting: Roberto учит с {self.roberto.get('job_lv', 30)} уровня профессии — коплю опыт.",
                 "items": "Arrow Crafting: собираю для Roberto " + ", ".join(
                     f"{n} × {i}" for i, n in sorted(self.missing(state).items())) + ".",
                 "no_route": "Arrow Crafting: всё для Roberto собрано, но дороги в Морокк для меня нет — жду.",
                 "ready": "Arrow Crafting: всё для Roberto собрано, путь в Морокк есть."}
        self.note("arrows_stage", texts[stage], 2 if stage in ("no_route", "ready") else 1, stage=stage)

    async def ensure_keep(self, now, state):
        craft = self.craft(state)
        want = sorted(int(i) for i in (self.roberto.get("items") or {}) if int(i) != 501)   # Red Potion — запас зелий
        kept = {int(x) for x in craft.get("kept") or []}
        if not want or set(want) <= kept or now - self.keep_sent < self.cfg["keep_every_minutes"] * 60:
            return
        self.keep_sent = now
        await self.mind.execute([{"action": "craft_setup", "keep": want}], source="arrows",
                                reason="лучник: материалы для Roberto не продавать", protocol=True)

    def busy_reason(self, now, state):
        m = self.mind
        if state.get("dead"):
            return "мёртв"
        hp = state.get("hp_pct")
        if hp is None or hp < self.cfg["min_hp"]:
            return "мало HP"
        if state.get("activity") in ("attack", "skill_use", "NPC", "deal", "buyAuto", "sellAuto", "storageAuto"):
            return f"занят: {state.get('activity')}"
        if quest_busy(m, state, now):
            return "идёт этап квеста"
        econ = getattr(m, "economy", None)
        if econ and (econ.body_busy() or econ.mail_busy()):
            return "сделка или почта"
        if (state.get("vend") or {}).get("open"):
            return "открыта лавка"
        return None

    # ---------- ремесло ----------

    def source(self, state):
        for src in self.cfg["sources"]:
            if str(src) in self.recipes and self.count(src, state) > 0:
                return int(src)
        return None

    async def crafting(self, now, state):
        pend = self.st.get("craft")
        if pend:
            arrow, before = pend["arrow"], pend["before"]
            got = self.count(arrow, state) - before
            if pend.get("sent_ok") and got > 0:
                self.crafted(now, pend, got)
            elif now - pend["ts"] >= self.cfg["verify_minutes"] * 60:
                self.st.pop("craft", None)
                self.st["last_craft"] = now
                self.save()
                self.mind.write_decision({"type": "arrows", "event": "craft_failed",
                                          "why": pend.get("why") or "стрел в рюкзаке не прибавилось"})
            return
        if now - self.st.get("last_craft", 0) < self.cfg["craft_minutes"] * 60:
            return
        src = self.source(state)
        if src is None or self.busy_reason(now, state):
            return
        arrow, name, n = self.recipes[str(src)]["make"][0]
        self.st["craft"] = {"ts": now, "source": src, "arrow": arrow, "arrow_name": name, "per": n,
                            "before": self.count(arrow, state)}
        self.st["last_craft"] = now
        self.save()
        await self.mind.execute([{"action": "arrowcraft", "item": src}], source="arrows",
                                reason=f"лучник: стрелы из {self.recipes[str(src)]['name']}", protocol=True)

    def on_craft(self, event):
        pend = self.st.get("craft")
        if not pend:
            return
        if event.get("ok"):
            pend["sent_ok"] = True
        else:
            pend["why"] = str(event.get("reason") or "мост не сделал")
            pend["ts"] = 0                                 # закрыть в следующий такт
        self.save()

    def crafted(self, now, pend, got):
        src_name = (self.recipes.get(str(pend["source"])) or {}).get("name", str(pend["source"]))
        self.st.pop("craft", None)
        self.st["made"][pend["arrow_name"]] = self.st["made"].get(pend["arrow_name"], 0) + got
        self.st["log"] = (self.st["log"] + [{"ts": now, "n": got, "arrow": pend["arrow_name"], "source": src_name}])[-20:]
        self.save()
        self.note("arrows_crafted", f"Сделал(а) {got} × {pend['arrow_name']} из {src_name} (Arrow Crafting).", 2,
                  source=src_name, arrow=pend["arrow_name"], n=got)

    # ---------- квест навыка ----------

    def action(self):
        rt, npc = self.route(), self.roberto
        steps = [{"do": "move", "map": m, "x": x, "y": y} for m, x, y in rt["legs"]]
        steps.append({"do": "move", "map": npc["map"], "x": rt["stand"][0], "y": rt["stand"][1]})
        steps.append({"do": "talk", "x": npc["x"], "y": npc["y"], "answers": []})   # при всех предметах меню нет
        return {"action": "job_change", "path": PATH, "stage": STAGE, "steps": steps,
                "success": {"text": npc["proof"]["text"], "map": npc["map"]}}

    async def quest(self, now, state):
        if not self.cfg["quest_auto"] or self.st.get("quest") or now < self.st.get("next_try", 0):
            return
        r = getattr(self.mind, "routine", None)
        if not (r and r.in_town_mode and r.st.get("arrived")) or state.get("map") != self.town():
            return
        if self.busy_reason(now, state) or self.mind.plans.store.active():
            return
        may_move = getattr(self.mind, "may_move", None)
        if may_move and not may_move("plan")[0]:
            return
        self.st["quest"] = {"ts": now}
        self.save()
        self.note("arrows_quest", "Иду к Roberto в Морокк учиться делать стрелы.", 2)
        await self.mind.execute([self.action()], source="arrows", reason="лучник: квест Arrow Crafting", protocol=True)

    def on_result(self, event):
        if event.get("path") != PATH:
            return False
        if event.get("ok"):
            self.mind.write_decision({"type": "arrows", "event": "quest_result_ok",
                                      "text": "Roberto ответил — навык жду по данным игры"})
        else:
            self.quest_failed(self.clock(), str(event.get("reason") or "этап не пройден"))
        return True

    def quest_failed(self, now, reason):
        self.st.pop("quest", None)
        self.st["next_try"] = now + self.cfg["retry_hours"] * 3600
        self.save()
        self.note("arrows_quest_failed", f"У Roberto не вышло: {reason}.", 2, reason=reason)

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "arrows", "event": kind, "text": text, **data})
        log.info("%s", text)

    # ---------- разговор и промпт ----------

    def facts(self, peer, now):
        if not self.is_archer():
            return None
        told = set(self.st["told"].get(peer) or [])
        for rec in reversed(self.st["log"]):
            if now - rec["ts"] > self.cfg["brag_days"] * 86400:
                return None
            key = str(int(rec["ts"]))
            if key in told:
                return None
            return {"n": rec["n"], "arrow": rec["arrow"][:20], "source": rec["source"][:20], "_craft": key}
        return None

    def said(self, peer, facts, now):
        key = (facts or {}).get("_craft")
        if key:
            self.st["told"][peer] = ((self.st["told"].get(peer) or []) + [key])[-20:]
            self.save()

    def summary(self):
        if not self.is_archer() or not self.craft():
            return None
        stage = self.st.get("stage")
        out = {"arrow_crafting": {"skill": "есть", "exp": "коплю опыт профессии", "items": "собираю для Roberto",
                                  "no_route": "нет дороги к Roberto", "ready": "готов идти к Roberto"}.get(stage, stage)}
        if stage == "items":
            out["не_хватает"] = {str(k): v for k, v in self.missing().items()}
        if self.st["made"]:
            out["сделано"] = self.st["made"]
        return out


CHRONICLE_LINES = {
    "arrows_crafted": lambda d: f"сделал(а) {d.get('n')} × {d.get('arrow')} из {d.get('source')}",
    "arrows_skill": lambda d: (("научился(лась) Arrow Crafting у Roberto" if d.get("roberto") else
                               "получил(а) навык Arrow Crafting") if d.get("learned") else None),
}
