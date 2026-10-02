"""Заточка у кузнеца — ритуал без риска (ORG-072, ТЗ Т-28). Правила без LLM. ПО УМОЛЧАНИЮ ВЫКЛЮЧЕНО.

Житель с лишними зени иногда несёт СВОЁ оружие (ур. 1–2) к кузнецу Пронтеры и точит его только до безопасного
уровня — там, где успех гарантирован и поломки нет. Риск выше предела не берётся.

Что выяснено по скриптам rAthena (идея ORG-072 называла Vestri — он точит только +10 и выше,
npc/re/merchants/refine.txt:25, «I only refine items that are Level 10 or higher»):
    кузнец +0..+10 — Hollgrehenn prt_in,63,60 (npc/merchants/refine.txt:526); при feature.refineui: on
    (conf/battle/feature.conf:85, PACKETVER 20180620) он открывает Refine UI — плагин refine (bots/plugins/refine)
    выбирает оружие и руду пакетами и точит только при шансе 100 из ответа сервера (0AA2);
    безопасный предел (db/re/refine.yml, Rate 10000) — оружие ур. 1: +7, ур. 2: +6 (ур. 3: +5, ур. 4: +4);
    плата за попытку — Phracon 50z / Emveretarcon 200z; руда — у Vurewell prt_in,56,68 (refine.txt:970):
    Phracon 200z, Emveretarcon 1000z. Данные — brain/world/refine.json (scripts/gen_refine.py).
Когда: раз в check_minutes — житель в режиме town дошёл до отдыха в городе кузнеца (prontera), нет плана встречи,
этапа квеста, сделки, почты, экспедиции, лавки, арбитр отдаёт тело (may_move("plan")), с прошлой попытки прошло
gap_days, выпал шанс chance. Оружие — из state.refine.weapon (плагин refine), уровень — refine.json; шагов
min(предел − текущее, max_steps); стоимость (плата + докупка руды) не больше spare_share излишка зени сверх
economy.keep_zeny и копилки мечты (savings).
Ритуал: перед походом — волнение в общем чате и эмоция «хм»; после подтверждения — радость и эмоция.
Доказательство: событие тела refine_result ok (upgrade предмета вырос по пакету 0188) И в state.refine.weapon того же
предмета upgrade ≥ to за verify_seconds — refine_done (память 3, летопись, шина мира refine_done 2); иначе
refine_unverified. Провал — refine_failed (1), повтор не раньше gap_days.
Пока плагин точит (state.refine.running), арбитр жизненного цикла считает тело занятым этапом (lifecycle.quest_busy,
# refine:), распорядок и прогулки не уводят его.
Включить: goals.json "refine": {"enabled": true}; выключатель BRAIN_DISABLE=refine.
"""
import json
import logging
import random
import time
from pathlib import Path

from .lifecycle import quest_busy

log = logging.getLogger("refine")

WORLD = Path(__file__).resolve().parents[1] / "world"
PATH = WORLD / "refine.json"
DEFAULTS = {
    "enabled": False,
    "town": "prontera",
    "check_minutes": 30,
    "gap_days": 3,
    "chance": 0.3,
    "max_steps": 3,
    "spare_share": 0.25,
    "verify_seconds": 60,
    "wait_minutes": 30,
    "weapon_levels": [1, 2],
}
EMOTE_BEFORE, EMOTE_AFTER = 20, 21          # «hmm» и «no1» (safety.EMOTES)
BEFORE = ["Ну, с богом… Несу {name} к Hollgrehenn. Только до +{to}, без риска.",
          "Руки дрожат: иду точить {name} до +{to}. Дальше не полезу!",
          "Скопил(а) зени — пора к кузнецу. {name} до +{to}, и ни шагом выше.",
          "Сердце колотится… {name} к Hollgrehenn, до +{to}. Безопасно же?"]
AFTER = ["{name} теперь +{to}! Руки до сих пор дрожат.",
         "Получилось: {name} +{to}! Hollgrehenn — мастер.",
         "+{to}! Мой {name} блестит. Дальше рисковать не стану.",
         "Фух… {name} +{to}. Ни одной трещины!"]


def load(path=None):
    try:
        return json.loads(Path(path or PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Refine:
    # реестр модулей (modules.py, W8): создание, тик, событие тела refine_result
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES, ARGS = "refine", "refine", "refine", False, ("world",), "world"
    TICK_ORDER = 105                                   # после home (100): Kafra — раньше заточки
    EVENTS, EVENT_ORDER = {"refine_result": {"call": "on_result", "own": True}}, 65

    def __init__(self, mind, world=None, data=None, clock=None, rng=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("refine") or {}))
        self.data = load() if data is None else data
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.st = mind.mem.get("refine") or {}
        self.next_check = 0.0
        self.levels = {}
        for lv, ids in (self.data.get("weapons") or {}).items():
            for i in ids:
                self.levels[int(i)] = int(lv)

    # ---------- данные ----------

    def save(self):
        self.mind.mem.set("refine", self.st)

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "refine", "event": kind, "text": text, **data})
        log.info("%s", text)

    def publish(self, kind, data, importance):
        bus = getattr(getattr(self.mind, "world", None), "bus", None)
        if bus is None or not hasattr(bus, "publish"):
            return
        try:
            bus.publish(kind, data, importance, now=self.clock())
        except Exception as e:                              # общая БД занята — не роняем тик
            log.warning("шина мира: %s", e)

    async def act(self, actions, reason, protocol=True):
        await self.mind.execute(actions, source="refine", reason=reason, protocol=protocol)

    def body(self, state=None):
        return (state or self.mind.state).get("refine") or {}

    # ---------- решение ----------

    def why_not(self, now, state):
        """None — можно идти точить сейчас, иначе причина (для тестов и журнала)."""
        if not self.body(state):
            return "плагин refine не прислал состояние"
        if self.body(state).get("running"):
            return "уже точу"
        if state.get("dead") or state.get("map") != self.cfg["town"]:
            return "не в городе кузнеца"
        if now - float(self.st.get("last") or 0) < self.cfg["gap_days"] * 86400:
            return "недавно точил(а)"
        m = self.mind
        r = getattr(m, "routine", None)
        if not (r and r.in_town_mode and r.st.get("arrived")):
            return "не отдых в городе"
        if m.plans.store.active():
            return "план встречи"
        if quest_busy(m, state, now):
            return "идёт этап квеста"
        explorer = getattr(m, "explorer", None)
        if explorer and explorer.busy():
            return "экспедиция"
        may_move = getattr(m, "may_move", None)
        if may_move and not may_move("plan")[0]:
            return "телом владеет другая задача"
        econ = getattr(m, "economy", None)
        if econ and (econ.body_busy() or econ.mail_busy() or econ.busy_trade()):
            return "сделка или почта"
        if (state.get("vend") or {}).get("open"):
            return "открыта лавка"
        return None

    def spare(self, state):
        """Излишек зени: сверх запаса экономики и копилки мечты."""
        econ = getattr(self.mind, "economy", None)
        keep = int(econ.market.get("keep_zeny", 5000)) if econ else 5000
        reserve = int(econ.reserve()) if econ else 0
        return int(state.get("zeny") or 0) - keep - reserve

    def plan(self, state):
        """Что точить: {item, inv, name, wlv, from, to, ore, buy, cost} или (None, причина)."""
        w = self.body(state).get("weapon")
        if not isinstance(w, dict) or not w.get("id"):
            return None, "нет оружия в руке"
        wlv = self.levels.get(int(w["id"]))
        if wlv not in [int(x) for x in self.cfg["weapon_levels"]]:
            return None, "оружие не того уровня (руду не продают)" if wlv else "оружие не точится"
        lv = (self.data.get("levels") or {}).get(str(wlv)) or {}
        safe, fee, ore = int(lv.get("safe") or 0), int(lv.get("fee") or 0), lv.get("ore")
        up = int(w.get("upgrade") or 0)
        if up >= safe:
            return None, f"уже на безопасном пределе +{safe}"
        steps = min(safe - up, int(self.cfg["max_steps"]))
        shop = self.data.get("shop") or {}
        price = int(((shop.get("items") or {}).get(str(ore)) or {}).get("price") or 0)
        have = int((self.body(state).get("ores") or {}).get(str(ore)) or 0)
        buy = max(0, steps - have)
        if buy and not price:
            return None, "руду не продают"
        cost = steps * fee + buy * price
        spare = self.spare(state)
        if spare <= 0 or cost > spare * float(self.cfg["spare_share"]):
            return None, f"лишних зени мало: нужно {cost}z, излишек {max(spare, 0)}z"
        return {"item": int(w["id"]), "inv": int(w.get("inv") or 0), "name": str(w.get("name") or w["id"]),
                "wlv": wlv, "from": up, "to": up + steps, "safe": safe, "ore": int(ore), "buy": buy,
                "cost": cost}, None

    # ---------- такт ----------

    async def tick(self):
        if not self.mind.fresh_state:
            return
        now = self.clock()
        state = self.mind.state
        if self.st.get("pending"):
            await self.check_pending(now, state)
            return
        if now < self.next_check:
            return
        self.next_check = now + self.cfg["check_minutes"] * 60
        why = self.why_not(now, state)
        if why or self.rng.random() >= float(self.cfg["chance"]):
            return
        p, why = self.plan(state)
        if not p:
            self.mind.write_decision({"type": "refine", "event": "skip", "why": why})
            return
        await self.start(p, now)

    async def start(self, p, now):
        shop, smith = dict(self.data.get("shop") or {}), dict(self.data.get("smith") or {})
        ore_menu = ((shop.get("items") or {}).get(str(p["ore"])) or {}).get("menu")
        action = {"action": "refine", "item": p["item"], "inv": p["inv"], "target": p["to"], "ore": p["ore"],
                  "buy": p["buy"], "smith": {k: smith[k] for k in ("map", "x", "y", "stand") if k in smith}}
        if p["buy"]:
            action["shop"] = {"map": shop.get("map"), "x": shop.get("x"), "y": shop.get("y"),
                              "stand": shop.get("stand"), "menu": ore_menu}
        self.st["pending"] = dict(p, status="sent", since=now)
        self.save()
        self.note("refine_started", f"Иду точить {p['name']} у Hollgrehenn: +{p['from']} -> +{p['to']} "
                                    f"(безопасно до +{p['safe']}), около {p['cost']}z.", 1,
                  item=p["item"], name=p["name"], **{"from": p["from"]}, to=p["to"], cost=p["cost"])
        await self.act([{"action": "emote", "emotion": EMOTE_BEFORE}], "заточка: волнение")
        await self.act([{"action": "say", "text": self.rng.choice(BEFORE).format(name=p["name"], to=p["to"])}],
                       "заточка: ритуал перед походом", protocol=False)
        await self.act([action], f"заточка: {p['name']} до +{p['to']} без риска")
        self.mind.job_change_sent = time.time()   # refine: арбитр (lifecycle.quest_busy) до state.refine.running

    async def check_pending(self, now, state):
        p = self.st["pending"]
        if p["status"] == "sent":
            if now - p["since"] >= self.cfg["wait_minutes"] * 60 and not self.body(state).get("running"):
                self.fail("нет итога от тела", now)
            return
        w = self.body(state).get("weapon") or {}
        if int(w.get("id") or 0) == p["item"] and int(w.get("upgrade") or 0) >= int(p["to_seen"]):
            await self.done(int(w["upgrade"]), now)
        elif now - p["verify_since"] >= self.cfg["verify_seconds"]:
            self.st.pop("pending", None)
            self.st["last"] = now
            self.save()
            self.note("refine_unverified", f"Тело сообщило о заточке {p['name']}, но в состоянии её не вижу.", 2,
                      item=p["item"], name=p["name"])

    def on_result(self, event):
        p = self.st.get("pending")
        if not p or p["status"] != "sent" or int(event.get("item") or 0) != p["item"]:
            return
        now = self.clock()
        to = event.get("to")
        if event.get("ok") and isinstance(to, int) and to > p["from"]:
            p.update(status="verify", to_seen=to, verify_since=now, reason=event.get("reason"))
            self.save()
            return
        self.fail(str(event.get("reason") or "не вышло"), now)

    def fail(self, reason, now):
        p = self.st.pop("pending", None) or {}
        self.st["last"] = now
        self.save()
        self.note("refine_failed", f"Заточка {p.get('name', 'оружия')} не вышла: {reason}.", 1,
                  item=p.get("item"), name=p.get("name"), reason=reason)

    async def done(self, up, now):
        p = self.st.pop("pending")
        self.st["last"] = now
        self.st["done"] = int(self.st.get("done") or 0) + 1
        self.save()
        stop = p.get("reason") or ""
        self.note("refine_done", f"Заточил(а) {p['name']} у Hollgrehenn: +{p['from']} -> +{up} — по данным игры"
                                 + (f" ({stop})" if stop else "") + ".", 3,
                  item=p["item"], name=p["name"], **{"from": p["from"]}, to=up, cost=p.get("cost"))
        self.publish("refine_done", {"item": p["item"], "name": p["name"], "from": p["from"], "to": up}, 2)
        await self.act([{"action": "emote", "emotion": EMOTE_AFTER}], "заточка: радость")
        await self.act([{"action": "say", "text": self.rng.choice(AFTER).format(name=p["name"], to=up)}],
                       "заточка: итог подтверждён игрой", protocol=False)


# Строки летописи (chronicle.LINES дополняется этим словарём).
CHRONICLE_LINES = {
    "refine_done": lambda d: f"заточил(а) {d.get('name')} у Hollgrehenn: +{d.get('from')} → +{d.get('to')} (без риска)",
    "refine_failed": lambda d: f"не заточил(а) {d.get('name') or 'оружие'}: {d.get('reason')}",
}
