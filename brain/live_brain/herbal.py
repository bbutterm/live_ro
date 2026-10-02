"""Травник: зелья у старого фармацевта (ORG-076, ТЗ Т-32). Правила без LLM.

Житель-травник (goals.json herbal.residents или persona.herbalist) не продаёт травы с лутом, копит их и иногда едет
в Альберту к Old Pharmacist (alberta_in,16,28, npc/merchants/old_pharmacist.txt:27): тот варит зелья из трав за плату.
Данные NPC, меню и рецепты — brain/world/crafts.json (scripts/gen_crafts.py, ссылки file:line); путь от города
отдыха до клетки у NPC — там же (routes.<город>.pharmacist: клетки прибытия на каждой карте пути).

Тело: плагин jobChange (как дом у Kafra, home.py) — действие job_change {path herbal, stage pharmacist}: шаги move по
клеткам прибытия пути (у каждого шага свой таймаут плагина), move к NPC, talk на каждый рецепт с ответами по ТЕКСТУ
пунктов («Make Potion» -> «White Potion.» -> «Make as many as I can.»); итог плагина — фраза «Here you go» на карте
alberta_in. Мозг верит только факту: зелий рецепта в state.items стало больше, чем перед поездкой.
Не продавать травы и бутылки — действие craft_setup {keep} (brainBridge: %items_control до перезагрузки); бутылки
Empty Bottle (713) — craft_setup {bottles: N} включает блок buyAuto «Empty Bottle» (Tool Dealer prt_in 126,76,
профиль бота), затем service (OpenKore после продажи докупает); после поездки покупка выключается.
Выгода рецепта = цена NPC зелья − плата − бутылка − продажная цена трав (prices.json): по ценам renewal выгодны
White и Blue, остальные дешевле купить. Экономия поездки — в событии herbal_brewed, kv herbal.saved, теме herbal.
Итог этапа job_change_result с path herbal забирает этот модуль (реестр: consume "result"), в career он не идёт.
Возвращение — как после любого этапа jobChange: плагин возвращает прежний lockMap (город отдыха).
По умолчанию выключено (goals.json herbal.enabled false): дорога в Альберту в игре не проверена.
"""
import json
import logging
import random
import time
from pathlib import Path

from .lifecycle import quest_busy

log = logging.getLogger("herbal")

CRAFTS_PATH = Path(__file__).resolve().parents[1] / "world" / "crafts.json"
PATH, STAGE = "herbal", "pharmacist"
HERBS = (507, 508, 509, 510, 511)
BOTTLE = 713
DEFAULTS = {
    "enabled": False,
    "residents": [],            # имена травников; пусто — только persona.herbalist
    "tick_seconds": 30,
    "gap_hours": 96,            # не чаще раза в 4 дня
    "min_potions": 5,           # меньше выгодных зелий — не ехать
    "max_potions": 60,          # за поездку (вес)
    "min_gain": 1,              # выгода рецепта на зелье, z
    "reserve_zeny": 2000,       # не тратить последнее
    "weight_margin": 100,       # к свободному весу NPC (500) — запас на зелья
    "min_hp": 80,
    "sleep_guard_hours": 3,
    "wait_result_minutes": 240,
    "verify_minutes": 5,
    "retry_hours": 6,
    "max_fails": 3,
    "return_hours": 6,
    "keep_every_minutes": 10,
    "brag_days": 4,
    "brag_chance": 0.5,
}
PHRASES = {   # ≤ 60 символов
    "herbal": ["Сварил(а) у фармацевта {n} {potion}, сберёг(ла) {saved}z!", "Съездил(а) в Альберту: {n} {potion} из трав."],
    "herbal_re": ["Травник! Буду знать, к кому идти.", "Сам(а) бы так не смог(ла).", "Вот это хозяйственность!"],
}


def load_crafts(path=CRAFTS_PATH):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Herbal:
    # реестр модулей (modules.py, W8)
    ATTR, FEATURE, CONFIG, ENABLED = "herbal", "herbal", "herbal", False
    REQUIRES, ARGS = ("world", "routine"), "world"
    TICK_ORDER = 103                          # после home (100), до explorer (110)
    EVENTS = {"job_change_result": {"call": "on_result", "consume": "result"}}   # только path herbal
    EVENT_ORDER = 15
    PROMPT = [("травник", "summary", 207)]    # после «занятие» (200), до «экспедиция» (210)

    def __init__(self, mind, world=None, clock=None, rng=None, crafts=None, prices=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("herbal") or {}))
        self.clock = clock or (lambda: time.time())
        self.rng = rng or random.Random()
        self.crafts = crafts if crafts is not None else load_crafts()
        self.npc = self.crafts.get("pharmacist") or {}
        if prices is None:
            from .prices import Prices
            prices = Prices.load()
        self.prices = prices
        self.st = mind.mem.get("herbal") or {}
        for key, val in (("trips", []), ("brewed", {}), ("told", {})):
            self.st.setdefault(key, val)
        self.next_tick = 0.0
        self.keep_sent = 0.0
        self.said_why = None
        social = getattr(mind, "social", None)
        if social is not None and hasattr(social, "register_topic"):
            for key, pool in PHRASES.items():
                social.phrases.setdefault(key, pool)
            social.register_topic("herbal", self.facts, said=self.said, chance=self.cfg["brag_chance"])

    # ---------- данные ----------

    def save(self):
        self.mind.mem.set("herbal", self.st)

    def me(self):
        return (self.mind.state or {}).get("name") or self.mind.persona.get("name")

    def is_herbalist(self):
        names = self.cfg.get("residents") or []
        return bool(self.mind.persona.get("herbalist")) or self.me() in names or self.mind.persona.get("name") in names

    def town(self):
        r = getattr(self.mind, "routine", None)
        return (r.cfg.get("town") or {}).get("map") if r else None

    def route(self):
        rec = ((self.crafts.get("routes") or {}).get(self.town()) or {}).get("pharmacist") or {}
        return rec if rec.get("legs") is not None and rec.get("stand") else None

    def craft(self, state=None):
        return (state if state is not None else self.mind.state).get("craft") or {}

    def count(self, item, state=None):
        state = state if state is not None else self.mind.state
        c = (self.craft(state).get("items") or {}).get(str(item))
        if c is None:
            c = (state.get("items") or {}).get(str(item))
        return int(c or 0)

    def gain(self, recipe):
        """Выгода одного зелья: цена NPC зелья − плата − бутылка − продажная цена трав."""
        p = self.prices
        herbs = sum(p.npc_sell(h, n) for h, n in recipe["herbs"].items())
        return p.npc_buy(recipe["potion"]) - recipe["fee"] - p.npc_buy(BOTTLE) - herbs

    def plan(self, state=None):
        """Выгодные рецепты по травам в рюкзаке: [{potion, name, answer, n, fee, gain}] (без учёта бутылок)."""
        state = state if state is not None else self.mind.state
        left = {str(h): self.count(h, state) for h in HERBS}
        out, total = [], 0
        recipes = sorted(self.npc.get("recipes") or [], key=lambda r: -self.gain(r))
        for r in recipes:
            g = self.gain(r)
            if g < self.cfg["min_gain"]:
                continue
            n = min(left.get(h, 0) // k for h, k in r["herbs"].items())
            n = min(n, self.cfg["max_potions"] - total)
            if n <= 0:
                continue
            for h, k in r["herbs"].items():
                left[h] -= n * k
            total += n
            out.append({"potion": r["potion"], "name": r["name"], "answer": r["answer"], "n": n, "fee": r["fee"],
                        "gain": g})
        return out

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        if now < self.next_tick or not self.mind.fresh_state or not self.is_herbalist():
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        state = self.mind.state
        await self.ensure_keep(now, state)
        pending = self.st.get("pending")
        if pending:
            await self.follow(now, state, pending)
            return
        self.check_return(now, state)
        if self.st.get("bottles_off"):                   # поездка позади — бутылки больше не докупать
            self.st.pop("bottles_off", None)
            self.save()
            await self.mind.execute([{"action": "craft_setup", "bottles": 0}], source="herbal",
                                    reason="травник: докупка бутылок выключена", protocol=True)
        why = self.why_not_now(now, state)
        if why and why.startswith("бутылок"):
            await self.want_bottles(now, state)
        if why:
            if why != self.said_why:
                self.said_why = why
                self.mind.write_decision({"type": "herbal", "event": "wait", "why": why})
            return
        await self.start(now, state)

    async def ensure_keep(self, now, state):
        """Травы и бутылки не продавать: craft_setup keep, если мост их ещё не держит."""
        craft = self.craft(state)
        if not craft:
            return
        want = list(HERBS) + [BOTTLE]
        kept = {int(x) for x in craft.get("kept") or []}
        if set(want) <= kept or now - self.keep_sent < self.cfg["keep_every_minutes"] * 60:
            return
        self.keep_sent = now
        await self.mind.execute([{"action": "craft_setup", "keep": want}], source="herbal",
                                reason="травник: травы и бутылки не продавать", protocol=True)

    def why_not_now(self, now, state):
        """None — можно ехать к фармацевту сейчас, иначе причина."""
        m, cfg = self.mind, self.cfg
        if not self.npc or not self.route():
            return f"нет пути из {self.town()} к фармацевту (crafts.json)"
        if now < self.st.get("next_try", 0):
            return "пауза после неудачи"
        if now - self.st.get("last", 0) < cfg["gap_hours"] * 3600:
            return f"не чаще раза в {cfg['gap_hours']} ч"
        if self.st.get("back"):
            return "ещё возвращаюсь"
        craft = self.craft(state)
        if not craft:
            return "мост не сообщает рюкзак ремесла (state.craft)"
        plan = self.plan(state)
        n = sum(p["n"] for p in plan)
        if n < cfg["min_potions"]:
            return f"трав на {n} выгодных зелий < {cfg['min_potions']}"
        bottles = self.count(BOTTLE, state)
        fee = sum(p["n"] * p["fee"] for p in plan)
        zeny = int(state.get("zeny") or 0)
        if bottles < n:
            return f"бутылок {bottles} < {n}"
        if zeny < fee + cfg["reserve_zeny"]:
            return f"зени {zeny} < плата {fee} + запас {cfg['reserve_zeny']}"
        free = craft.get("weight_free")
        need = int(self.npc.get("free_weight", 500)) + cfg["weight_margin"]
        if free is None or free < need:
            return f"свободный вес {free} < {need}"
        if state.get("dead") or state.get("map") != self.town():
            return "не в городе отдыха"
        hp = state.get("hp_pct")
        if hp is None or hp < cfg["min_hp"]:
            return f"HP {hp}% < {cfg['min_hp']}%"
        r = getattr(m, "routine", None)
        if not (r and r.in_town_mode and r.st.get("arrived")):
            return "не отдых в городе"
        social = getattr(m, "social", None)
        if social and social.is_night(now):
            return "ночь"
        win = r.sleep_window(now) if hasattr(r, "sleep_window") else None
        if win and win[0] - cfg["sleep_guard_hours"] * 3600 <= now < win[1]:
            return "скоро сон"
        if m.plans.store.active():
            return "план встречи"
        if quest_busy(m, state, now):
            return "идёт этап квеста"
        for attr in ("explorer", "trek"):
            mod = getattr(m, attr, None)
            if mod and mod.busy():
                return "экспедиция"
        may_move = getattr(m, "may_move", None)
        if may_move and not may_move("plan")[0]:
            return "телом владеет другая задача"
        econ = getattr(m, "economy", None)
        if econ and (econ.body_busy() or econ.mail_busy()):
            return "сделка или почта"
        if (state.get("vend") or {}).get("open"):
            return "открыта лавка"
        return None

    async def want_bottles(self, now, state):
        """Бутылок не хватает на выгодные зелья — включить докупку и попросить тело сходить к торговцу."""
        plan = self.plan(state)
        n = sum(p["n"] for p in plan)
        have = self.count(BOTTLE, state)
        if n < self.cfg["min_potions"] or have >= n:
            return False
        if self.st.get("bottles_asked", 0) > now - 3600:
            return False
        price = self.prices.npc_buy(BOTTLE, n - have)
        if int(state.get("zeny") or 0) < price + self.cfg["reserve_zeny"]:
            return False
        self.st["bottles_asked"] = now
        self.save()
        await self.mind.execute([{"action": "craft_setup", "bottles": n}, {"action": "service"}], source="herbal",
                                reason=f"травник: докупить бутылки до {n}", protocol=True)
        self.mind.write_decision({"type": "herbal", "event": "bottles", "want": n, "have": have, "price": price})
        return True

    # ---------- поездка ----------

    def action(self, plan):
        rt, npc = self.route(), self.npc
        steps = [{"do": "move", "map": m, "x": x, "y": y} for m, x, y in rt["legs"]]
        steps.append({"do": "move", "map": npc["map"], "x": rt["stand"][0], "y": rt["stand"][1]})
        for p in plan:
            steps.append({"do": "talk", "x": npc["x"], "y": npc["y"], "ordered": True,
                          "answers": [{"text": npc["menu"]["text"]}, {"text": p["answer"]},
                                      {"text": npc["amount"]["text"]}]})
        return {"action": "job_change", "path": PATH, "stage": STAGE, "steps": steps,
                "success": {"text": npc["proof"]["text"], "map": npc["map"]}}

    async def start(self, now, state):
        plan = self.plan(state)
        before = {str(p["potion"]): self.count(p["potion"], state) for p in plan}
        self.st["pending"] = {"ts": now, "plan": plan, "before": before, "zeny": state.get("zeny"),
                              "herbs": {str(h): self.count(h, state) for h in HERBS}}
        self.st["last"] = now
        self.save()
        what = ", ".join(f"{p['n']} {p['name']}" for p in plan)
        self.note("herbal_start", f"Еду в Альберту к старому фармацевту: сварить {what} из своих трав.", 2,
                  potions={p["name"]: p["n"] for p in plan}, hops=self.route()["hops"])
        routine = getattr(self.mind, "routine", None)
        if routine:
            routine.set_goal("еду к фармацевту в Альберту")
        await self.mind.execute([self.action(plan)], source="herbal", reason="травник: к фармацевту в Альберту",
                                protocol=True)

    async def follow(self, now, state, pending):
        """Ждать итога плагина, затем проверить факт: зелья в рюкзаке."""
        running = (state.get("job_change") or {}).get("running")
        if pending.get("result_ok"):
            got = {k: self.count(k, state) - v for k, v in pending["before"].items()}
            if any(d > 0 for d in got.values()):
                self.brewed(now, pending, got)
            elif now - pending["result_ok"] >= self.cfg["verify_minutes"] * 60:
                self.failed(now, "фармацевт ответил, а зелий в рюкзаке нет")
            return
        if not running and now - pending["ts"] >= self.cfg["wait_result_minutes"] * 60:
            self.failed(now, "нет итога этапа от тела")

    def on_result(self, event):
        """job_change_result: свой (path herbal) — True (поглощено, в career не идёт); чужой — False."""
        if event.get("path") != PATH:
            return False
        now = self.clock()
        pending = self.st.get("pending")
        if not pending:
            self.mind.write_decision({"type": "herbal", "event": "stray_result", "ok": bool(event.get("ok"))})
            return True
        if event.get("ok"):
            pending["result_ok"] = now
            self.save()
            self.mind.write_decision({"type": "herbal", "event": "result_ok", "text": "фармацевт ответил"})
        else:
            self.failed(now, str(event.get("reason") or "этап не пройден"))
        return True

    def brewed(self, now, pending, got):
        plan = {str(p["potion"]): p for p in pending["plan"]}
        potions, saved = {}, 0
        for pid, n in got.items():
            if n <= 0 or pid not in plan:
                continue
            p = plan[pid]
            potions[p["name"]] = n
            saved += n * p["gain"]
            self.st["brewed"][p["name"]] = self.st["brewed"].get(p["name"], 0) + n
        self.st["saved"] = self.st.get("saved", 0) + saved
        trip = {"ts": now, "potions": potions, "saved": saved, "ok": True}
        self.st["trips"] = (self.st["trips"] + [trip])[-20:]
        self.st.pop("pending", None)
        self.st["fails"] = 0
        self.st["back"] = now
        if self.st.pop("bottles_asked", None):
            self.st["bottles_off"] = True
        self.save()
        what = ", ".join(f"{n} {k}" for k, n in potions.items())
        self.note("herbal_brewed", f"Старый фармацевт сварил мне {what} — сберёг(ла) {saved}z против лавки.", 3,
                  potions=potions, saved=saved)

    def failed(self, now, reason):
        self.st.pop("pending", None)
        self.st["fails"] = self.st.get("fails", 0) + 1
        self.st["next_try"] = now + self.cfg["retry_hours"] * 3600
        self.st["back"] = now
        if self.st.pop("bottles_asked", None):
            self.st["bottles_off"] = True
        self.st["trips"] = (self.st["trips"] + [{"ts": now, "ok": False, "why": reason}])[-20:]
        text = f"Поездка к фармацевту не удалась: {reason}."
        if self.st["fails"] >= self.cfg["max_fails"]:
            self.st["next_try"] = now + 86400
            self.st["fails"] = 0
            alert = getattr(self.mind, "alert", None)
            if alert:
                alert("herbal", f"фармацевт: {self.cfg['max_fails']} неудачи подряд — пауза на сутки")
            text += " Попробую завтра."
        self.save()
        self.note("herbal_failed", text, 2, reason=reason)

    def check_return(self, now, state):
        back = self.st.get("back")
        if not back:
            return
        if state.get("map") == self.town():
            self.st.pop("back", None)
            self.save()
            self.mind.mem.add_event("herbal_returned", {"minutes": round((now - back) / 60, 1)})
            self.mind.write_decision({"type": "herbal", "event": "returned"})
        elif now - back > self.cfg["return_hours"] * 3600:
            self.st.pop("back", None)
            self.save()
            self.mind.write_decision({"type": "herbal", "event": "return_late"})

    def busy(self):
        return bool(self.st.get("pending"))

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "herbal", "event": kind, "text": text, **data})
        log.info("%s", text)

    # ---------- разговор и промпт ----------

    def facts(self, peer, now):
        told = set(self.st["told"].get(peer) or [])
        for trip in reversed(self.st["trips"]):
            if not trip.get("ok") or now - trip["ts"] > self.cfg["brag_days"] * 86400:
                continue
            key = str(int(trip["ts"]))
            if key in told:
                return None
            name, n = max(trip["potions"].items(), key=lambda kv: kv[1])
            return {"n": n, "potion": name[:20], "saved": trip["saved"], "_trip": key}
        return None

    def said(self, peer, facts, now):
        key = (facts or {}).get("_trip")
        if key:
            self.st["told"][peer] = ((self.st["told"].get(peer) or []) + [key])[-20:]
            self.save()

    def summary(self):
        if not self.is_herbalist():
            return None
        out = {"трав": {str(h): self.count(h) for h in HERBS if self.count(h)}, "бутылок": self.count(BOTTLE)}
        if self.st.get("pending"):
            out["сейчас"] = "еду к фармацевту"
        if self.st.get("saved"):
            out["сберёг_z"] = self.st["saved"]
        return out


def brewed_text(d):
    what = ", ".join(f"{n} {k}" for k, n in (d.get("potions") or {}).items())
    return f"сварил(а) у фармацевта в Альберте {what} (сбережено {d.get('saved')}z)"


CHRONICLE_LINES = {"herbal_brewed": brewed_text,
                   "herbal_failed": lambda d: f"поездка к фармацевту не удалась: {d.get('reason')}"}
