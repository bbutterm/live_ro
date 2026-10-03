"""Снаряжение у NPC: что купить и надеть лучшее из рюкзака (ORG-097, часть 1; ТЗ Т-44 в docs/IDEAS2.md). Правила без LLM.

Сток зени (риск R8): деньги сверх запасов и копилки получают понятную цель — вещь из магазина своего города.
Данные — brain/world/jobs/catalog.json (scripts/gen_progression.py: магазины Пронтеры/Изюда из npc/merchants,
поля Jobs, EquipLevelMin, Attack, Defense, Locations, Gender из db/re/item_db_equip.yml) и готовый
progression.next_equipment: слоты по порядку, тип оружия профессии, щит, двуручное. Сравнение — только ATK оружия
и DEF брони (карты, заточка, формула renewal не учитываются).

    Бюджет     = (зени − keep_zeny экономики − копилка мечты savings.reserve) / (1 + greed_scale × жадность):
                 жадный тянет дольше; бюджет ≤ 0 — совета нет. Ниже keep и копилки не тратится ничего.
    Совет      = next_equipment(state, бюджет): вещь лучше надетой по ATK/DEF, профессия и уровень по каталогу.
                 Тело не сообщило надетое (нет state.equip — мост без строк # sinks:) — совета нет, не гадаем.
                 Новый лучший предмет — событие gear_wish (один раз на предмет), поле промпта «снаряжение».
    Надеть     = в рюкзаке (state.equip_bag) вещь из каталога лучше надетой (та же проверка профессии, уровня,
                 пола и типа оружия) -> действие equip {item} (brainBridge: «eq <номер>»). Только в режиме
                 отдыха, тело свободно, не чаще wear_gap_minutes. Факт — state.equip показал вещь в слоте
                 (не ack): событие gear_worn и воспоминание. Нет за WEAR_TIMEOUT — gear_wear_failed.
Покупки у NPC здесь НЕТ (вторая часть ORG-097: диалог магазина или разовый блок buyAuto и защита покупки от
sellAuto/storageAuto economy.pl — новое действие моста с деньгами, проверка в игре обязательна).
Выключатель: goals.json "gear": {"enabled": false} (по умолчанию) или BRAIN_DISABLE=gear. Состояние — kv gear
{pick, told, wearing, wear_ts}.
"""
import logging
import time

from . import progression

log = logging.getLogger("gear")

DEFAULTS = {"enabled": False, "tick_seconds": 300, "greed_scale": 0.5, "wear": True, "wear_gap_minutes": 10}
WEAR_TIMEOUT = 120
KEEP_ZENY = 5000                     # economy.MARKET keep_zeny по умолчанию
_DATA = []


def data():
    if not _DATA:
        _DATA.append(progression.load())
    return _DATA[0]


def job_key(job, d):
    """Имя профессии state (OpenKore «Swordsman» или rAthena «Swordman») -> ключ classes progression.json."""
    if job in d["classes"]:
        return job
    return next((k for k, c in d["classes"].items() if c.get("rathena") == job), None)


def slot_of(item, cls):
    """Слот вещи каталога для профессии cls или None (не её тип оружия, не броня)."""
    locs = set(item["locations"])
    for slot, names in progression.SLOT_LOCATIONS.items():
        if not locs & set(names):
            continue
        if slot == "weapon":
            return slot if item.get("subtype") in cls.get("weapon_types", ()) else None
        return slot if item["type"] == "Armor" else None
    return None


def stat(item, slot):
    return item["attack"] if slot == "weapon" else item["defense"]


def best_in_bag(state, d=None):
    """Лучшая вещь рюкзака (state.equip_bag), которая сильнее надетой в своём слоте; None — нечего надеть."""
    d = d or data()
    equip, bag = state.get("equip"), state.get("equip_bag") or []
    key = job_key(state.get("job") or "", d)
    if equip is None or not bag or not key:
        return None
    cls = d["classes"][key]
    by_id = {e["id"]: e for e in d["catalog"]["equipment"]}
    lv = int(state.get("lv") or 0)
    worn_weapon = by_id.get(equip.get("weapon"))
    two_hand = bool(worn_weapon and "Both_Hand" in worn_weapon["locations"])
    best = None
    for iid in bag:
        e = by_id.get(iid)
        if not e or cls["rathena"] not in e["jobs"] or e["equip_lv"] > lv or not progression._sex_ok(e, state):
            continue
        slot = slot_of(e, cls)
        if not slot or (slot == "Left_Hand" and (not cls.get("use_shield") or two_hand)):
            continue
        cur_id = equip.get(slot)
        if cur_id and cur_id not in by_id:
            continue                                     # надето не из магазина — сравнить не с чем
        gain = stat(e, slot) - (stat(by_id[cur_id], slot) if cur_id else 0)
        if gain > 0 and (best is None or gain > best["gain"]):
            best = {"id": e["id"], "name": e["name"], "slot": slot, "gain": gain}
    return best


class Gear:
    # реестр модулей (modules.py, W8): создание, тик, промпт
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "gear", "gear", "gear", False, "world"
    REQUIRES = ("economy",)                     # keep_zeny и занятость тела — от экономики
    TICK_ORDER = 148                            # после копилки (147), до целей недели (150)
    WARMUP = 60                 # warmup: необязательная инициатива — через 60–120 с после пробуждения (modules.py)
    TICK_EVERY = 30
    PROMPT = [("снаряжение", "summary", 137)]   # рядом с копилкой (135)

    def __init__(self, mind, world=None, clock=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("gear") or {}))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.st = mind.mem.get("gear") or {}
        self.next_tick = 0.0
        self.last_pick = 0.0

    def save(self):
        self.mind.mem.set("gear", self.st)

    # ---------- данные ----------

    def keep(self):
        econ = getattr(self.mind, "economy", None)
        return int((getattr(econ, "market", None) or {}).get("keep_zeny", KEEP_ZENY))

    def greed(self):
        return float((getattr(getattr(self.mind, "needs", None), "t", None) or {}).get("greed", 0.5))

    def budget(self, state=None):
        state = state if state is not None else (self.mind.state or {})
        sv = getattr(self.mind, "savings", None)
        spare = int(state.get("zeny") or 0) - self.keep() - (sv.reserve(state) if sv else 0)
        return max(0, int(spare / (1 + self.cfg["greed_scale"] * self.greed())))

    def pick(self, state=None):
        state = state if state is not None else (self.mind.state or {})
        if state.get("equip") is None:
            return None                                   # тело не сообщило надетое — не гадаем
        b = self.budget(state)
        if b <= 0:
            return None
        d = data()
        key = job_key(state.get("job") or "", d)
        if not key:
            return None
        return progression.next_equipment(dict(state, job=key), b, d)

    def body_free(self, state):
        routine = getattr(self.mind, "routine", None)
        if routine and (not routine.in_town_mode or getattr(routine, "sleeping", False)):
            return False
        econ = getattr(self.mind, "economy", None)
        if econ and (econ.body_busy() or econ.mail_busy() or econ.busy_trade() or econ.body_elsewhere()):
            return False
        plans = getattr(self.mind, "plans", None)
        if plans and plans.store.active():
            return False
        return not (state.get("give") or (state.get("vend") or {}).get("open")
                    or (state.get("job_change") or {}).get("running") or state.get("activity") == "attack")

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + 30
        state = self.mind.state or {}
        if not getattr(self.mind, "fresh_state", True) or state.get("dead") or not isinstance(state.get("zeny"), int):
            return
        if self.check_worn(state, now):
            return
        if self.cfg["wear"] and not self.st.get("wearing"):
            await self.wear(state, now)
        if now - self.last_pick >= self.cfg["tick_seconds"]:
            self.last_pick = now
            self.advise(state)

    def check_worn(self, state, now):
        """Надевание в пути: факт — вещь в слоте state.equip; иначе по таймауту — неудача."""
        w = self.st.get("wearing")
        if not w:
            return False
        if (state.get("equip") or {}).get(w["slot"]) == w["item"]:
            self.st["wearing"] = None
            self.save()
            self.mind.mem.add_event("gear_worn", {"item": w["item"], "name": w["name"], "slot": w["slot"],
                                                  "gain": w["gain"]})
            self.mind.mem.remember(f"Надел(а) {w['name']} — лучше прежнего (+{w['gain']}), по данным игры.", 2)
            self.mind.write_decision({"type": "gear", "event": "worn", "item": w["item"], "slot": w["slot"]})
            return True
        if now - w.get("since", 0) >= WEAR_TIMEOUT:
            self.st["wearing"] = None
            self.save()
            self.mind.mem.add_event("gear_wear_failed", {"item": w["item"], "name": w["name"]})
            self.mind.write_decision({"type": "gear", "event": "wear_failed", "item": w["item"]})
        return True

    async def wear(self, state, now):
        if now - (self.st.get("wear_ts") or 0) < self.cfg["wear_gap_minutes"] * 60 or not self.body_free(state):
            return
        best = best_in_bag(state)
        if not best:
            return
        self.st.update(wearing=dict(best, item=best["id"], since=now), wear_ts=now)
        self.save()
        await self.mind.execute([{"action": "equip", "item": best["id"]}], source="gear",
                                reason=f"снаряжение: надеть {best['name']} (+{best['gain']})", protocol=True)

    def advise(self, state):
        p = self.pick(state)
        self.st["pick"] = p
        if p and p["id"] != self.st.get("told"):
            self.st["told"] = p["id"]
            shop = p.get("shop") or {}
            self.mind.mem.add_event("gear_wish", {"item": p["id"], "name": p["name"], "slot": p["slot"],
                                                  "price": p["price"], "gain": p["gain"],
                                                  "shop": f"{shop.get('map')} {shop.get('npc')}"})
            self.mind.write_decision({"type": "gear", "event": "wish", "item": p["id"], "price": p["price"],
                                      "budget": self.budget(state)})
        self.save()

    # ---------- наружу ----------

    def summary(self):
        p = self.st.get("pick")
        if not p:
            return None
        stat_name = "ATK" if p["slot"] == "weapon" else "DEF"
        shop = p.get("shop") or {}
        return f"купить {p['name']} (+{p['gain']} {stat_name}) за {p['price']}z — {shop.get('map')} {shop.get('npc')}"


CHRONICLE_LINES = {
    "gear_wish": lambda d: f"присмотрел(а) {d.get('name')} за {d.get('price')}z ({d.get('shop')})",
    "gear_worn": lambda d: f"надел(а) {d.get('name')}",
}
