"""Скупка (Buying Store, ORG-036, ТЗ Т-38) и снаряжение торговца. Правила без LLM.

Торговец открывает в городе скупку того, что нужно жителям, а жители сами продают в неё лишнее. Тело — плагин
bots/plugins/buyer (действия buyer_open / buyer_close / buyer_sell, state.buyer), доказательства — только факты тела.

Сервер (upstream/rathena): навык ALL_BUYING_STORE (SP 30 и 1 × Buy Market Permit 6377 на открытие, 5 мест) учит
Mr. Hugh (alberta_in,58,52, npc/merchants/buying_shops.txt:104) Merchant-ветку с MC_VENDING ≥ 1 за 10 000 z (+5 × 6377),
дальше 6377 по 200 z. Покупать можно только то, чего хотя бы 1 штука уже в рюкзаке (buyingstore.cpp:185), не
снаряжение (флаг BuyingStore в item_db), цена 1..99 990 000, лимит зени ≤ зени в кармане. Лавка и скупка не
открываются одновременно (buyingstore_setup: sd->state.vending).

Скупщик (житель с state.buyer.can: навык и 6377, или предмет 12548):
    что скупать — спрос жителей: открытые заказы шины мира (orders.open_orders: чужие, не взятые) > свой список
        желаний (economy.wishlist) > товары жителей goods (травы травнику, руда для заточки: «держать N»);
    цена штуки — value × (1 + bid_markup) (prices.py), но не ниже NPC-продажи + 1 (иначе жителю выгоднее NPC) и не
        выше потолка: prices.buy_limit, по заказу — награда/n × (1 − order_margin) (заработок на перепродаже);
        потолок ниже NPC+1 — предмет не скупаю;
    бюджет — budget_share свободных зени (зени − keep_zeny − копилка мечты savings), не больше max_budget, не меньше
        min_budget; количества урезаются по бюджету в порядке спроса;
    когда — отдых в городе (дошёл), нет встречи, квеста, сделки, сна; не чаще open_gap_minutes; держит open_minutes,
        закрывает раньше при уходе на охоту (routine), встрече, сне. Лавка открыта дольше vend_turn_minutes — её
        закрыть и открыть скупку (по очереди: распорядок не открывает лавку, пока открыта скупка).
Продавец (любой житель): видит скупку жителя рядом (state.buyer.stores), в городе и свободен — buyer_sell с тем,
    что не нужно самому (economy.keep_items, альбом карт, зелья и крылья не продаёт): за штуку не меньше NPC-продажи
    со своим Overcharge + 1 (карта — не меньше value); к одной скупке не чаще sell_gap_minutes.
Доказательства: «купил» — событие buyer_bought (рост предмета в рюкзаке и убыль зени, пока скупка открыта); «продал» —
    buyer_sell_result ok с пакетами 081C и приростом зени ≥ Σ количество × цена. Слова и ack — не доказательство.

Снаряжение торговца (setup, Merchant-ветка): этапы плагина jobChange с path "buying" (итог забирает этот модуль):
    cart     — Kafra своего города (brain/world/homes.json): «Rent a Pushcart» -> «Rent a Pushcart.» -> «Cancel»
               (npc/kafras/functions_kafras.txt:176, F_KafCart :337: Merchant-ветка, MC_PUSHCART ≥ 1, плата
               F_Kafra arg 4 — 800 z в Пронтере, kafras.txt:298); факт — state.buyer.cart;
    license  — Mr. Hugh: три меню и ввод имени (input .@name$, jobChange input_text), 10 000 z; факт — навык
               ALL_BUYING_STORE в state.buyer.skill; дорога — точки этапа first_job пути merchant (progression.json);
    permits  — Mr. Hugh: «Purchase Bulk Buyer Shop License», ввод числа (input_number) по 200 z; факт — рост 6377.
По умолчанию модуль выключен (goals.json buying.enabled false), license/permits выключены отдельно: в игре не проверено.
"""
import json
import logging
import time
from pathlib import Path

from .economy import MERCHANTS
from .lifecycle import quest_busy

log = logging.getLogger("buying")

PATH = "buying"                  # path этапов jobChange: итог (job_change_result) — этому модулю, не career
PERMIT, SHABBY = 6377, 12548
SKILL_SP = 30                    # skill_db.yml ALL_BUYING_STORE SpCost
MAX_AMOUNT = 9999                # buyingstore.cpp BUYINGSTORE_MAX_AMOUNT
LICENSE_FEE = 10000              # buying_shops.txt:198 «10,000 zeny as a one-time registration fee»
PERMIT_PRICE = 200               # buying_shops.txt:122
HUGH = {"map": "alberta_in", "x": 58, "y": 52, "stand": (58, 49), "src": "npc/merchants/buying_shops.txt:104",
        "free_weight": 2400}     # :105 «MaxWeight - Weight < 2400» — разговора не будет
LICENSE_ANSWERS = ("I've never had problems buying items...", "Alright, what's your point?",
                   "Learn how to open Bulk Buyer Shop")              # :154, :175, :205
LICENSE_PROOF = "approved to open the Bulk Buyer Shop"               # :225
PERMITS_ANSWERS = ("Purchase Bulk Buyer Shop License",)              # :115
PERMITS_PROOF = "Thank you for your patronage"                       # :140
CART_ANSWERS = ("Rent a Pushcart", "Rent a Pushcart.", "Cancel")     # functions_kafras.txt:142, :379, :142
# Не продавать в чужую скупку: зелья и крылья (счётчики economy.pl @TRACK), лицензии скупки.
NO_SELL = {"569", "501", "502", "503", "504", "505", "506", "601", "602", str(PERMIT), str(SHABBY)}
WORLD = Path(__file__).resolve().parents[1] / "world"
DEFAULTS = {
    "enabled": False,
    "tick_seconds": 30,
    "open_gap_minutes": 120,
    "open_minutes": 45,
    "vend_turn_minutes": 60,
    "budget_share": 0.3,
    "max_budget": 30000,
    "min_budget": 500,
    "keep_zeny": 5000,
    "bid_markup": 0.2,
    "order_margin": 0.1,
    "max_amount": 50,
    "goods": {},                 # {id: держать штук} — товары жителей
    "sell_gap_minutes": 60,
    "retry_minutes": 30,
    "setup": {"cart": True, "license": False, "permits": False},
    "cart_fee": 800,
    "min_permits": 1,
    "permits_buy": 10,
    "reserve_zeny": 2000,
    "setup_retry_hours": 12,
    "wait_result_minutes": 240,
    "verify_minutes": 3,
}


def load_homes(path=WORLD / "homes.json"):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_progression(path=WORLD / "progression.json"):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Buying:
    # реестр модулей (modules.py, W8)
    ATTR, FEATURE, CONFIG, ENABLED = "buying", "buying", "buying", False
    REQUIRES, ARGS = ("world", "economy", "routine"), "world"
    TICK_ORDER = 27                          # после orders (25): заказы этого тика уже в шине; до party (30)
    EVENTS = {"job_change_result": {"call": "on_result", "consume": "result"},   # только path buying
              "buyer_result": {"call": "on_buyer_result", "own": True},
              "buyer_bought": {"call": "on_bought", "own": True},
              "buyer_closed": {"call": "on_closed", "own": True},
              "buyer_sell_result": {"call": "on_sell_result", "own": True}}
    EVENT_ORDER = 18                         # после trek (17)
    PROMPT = [("скупка", "summary", 145)]    # после «рынок» (140), до мотивов (150)

    def __init__(self, mind, world=None, clock=None, homes=None, progression=None):
        self.mind = mind
        cfg = (world or {}).get("buying") or {}
        self.cfg = dict(DEFAULTS, **cfg)
        self.cfg["setup"] = dict(DEFAULTS["setup"], **(cfg.get("setup") or {}))
        self.clock = clock or (lambda: time.time())
        self.homes = homes if homes is not None else load_homes()
        self.progression = progression if progression is not None else load_progression()
        self.st = mind.mem.get("buying") or {}
        for key, val in (("tried", {}), ("stats", {"bought": 0, "spent": 0, "sold": 0, "earned": 0})):
            self.st.setdefault(key, val)
        self.next_tick = 0.0

    # ---------- данные ----------

    @property
    def state(self):
        return self.mind.state

    @property
    def econ(self):
        return self.mind.economy

    @property
    def prices(self):
        return self.econ.prices

    def save(self):
        self.mind.mem.set("buying", self.st)

    def me(self):
        return self.state.get("name") or self.mind.persona.get("name")

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "buying", "event": kind, "text": text, **data})
        log.info("%s", text)

    def have(self, item, state=None):
        return int(((state or self.state).get("items") or {}).get(str(item), 0) or 0)

    def is_merchant(self, state=None):
        return any(m in str((state or self.state).get("job") or "") for m in MERCHANTS)

    def overcharge(self, state):
        return int(((state.get("vend") or {}).get("overcharge")) or 0)

    def busy(self, now, state):
        """Причина, по которой тело сейчас не до скупки, или None."""
        m = self.mind
        if state.get("dead"):
            return "мёртв"
        if m.plans.store.active():
            return "план встречи"
        if quest_busy(m, state, now):
            return "идёт этап квеста"
        econ = self.econ
        if econ.body_busy() or econ.busy_trade() or econ.mail_busy() or econ.giving:
            return "сделка или почта"
        why = econ.body_elsewhere()
        if why and why != "стою со скупкой":
            return why
        for attr in ("explorer", "trek"):
            mod = getattr(m, attr, None)
            if mod and mod.busy():
                return "экспедиция"
        return None

    def at_rest(self):
        r = getattr(self.mind, "routine", None)
        return bool(r and r.in_town_mode and r.st.get("arrived") and not r.sleeping)

    # ---------- спрос и цены (скупщик) ----------

    def demand(self, now, state):
        """Что скупать: [{id, n, cap, why}] в порядке важности (заказы жителей, свой список, товары)."""
        out = {}

        def add(item, n, cap, why):
            item = str(item)
            if n <= 0 or item in NO_SELL or not self.prices.item(item):
                return
            d = out.setdefault(item, {"id": int(item), "n": 0, "cap": 0, "why": why, "rank": len(out)})
            d["n"] += int(n)
            d["cap"] = max(d["cap"], int(cap))

        orders = getattr(self.mind, "orders", None)
        for o in (orders.open_orders(now) if orders else []):
            n = int(o.get("n") or 0)
            if n > 0 and o.get("customer") != self.me():
                add(o["item"], n, int(int(o.get("reward") or 0) / n * (1 - self.cfg["order_margin"])),
                    f"заказ {o.get('customer')}")
        discount = int(((state.get("vend") or {}).get("discount")) or 0)
        for item, n in self.econ.wishlist(state).items():
            if item != "z":
                add(item, n, self.prices.buy_limit(item, 1, discount), "нужно мне")
        for item, keep in (self.cfg.get("goods") or {}).items():
            add(item, int(keep) - self.have(item, state), self.prices.buy_limit(item, 1, discount), "товар жителей")
        return sorted(out.values(), key=lambda d: d["rank"])

    def price(self, item, cap):
        """Цена штуки: value × (1 + bid_markup), не ниже NPC + 1, не выше cap. None — дешевле NPC не купить."""
        floor = self.prices.npc_sell(item, 1) + 1
        bid = max(floor, int(self.prices.value(item) * (1 + self.cfg["bid_markup"])))
        p = min(bid, int(cap))
        return p if p >= floor else None

    def budget(self, state):
        free = int(state.get("zeny") or 0) - int(self.cfg["keep_zeny"]) - int(self.econ.reserve() or 0)
        b = min(int(self.cfg["max_budget"]), int(free * float(self.cfg["budget_share"])))
        return b if b >= int(self.cfg["min_budget"]) else 0

    def plan(self, now, state):
        """Список скупки [{id, price, amount, why}] в пределах мест и бюджета (пусто — открывать незачем)."""
        buyer = state.get("buyer") or {}
        slots = int(buyer.get("slots") or 0)
        left = self.budget(state)
        if not slots or not left:
            return []
        items = state.get("items") or {}
        cand = [d for d in self.demand(now, state)]
        # образец в рюкзаке (buyingstore.cpp:185): известные — первыми, неизвестные мосту (травы) плагин проверит сам
        cand.sort(key=lambda d: (0 if int(items.get(str(d["id"]), 0) or 0) > 0 else 1, d["rank"]))
        out = []
        for d in cand:
            if str(d["id"]) in items and int(items[str(d["id"])] or 0) <= 0:
                continue
            p = self.price(d["id"], d["cap"])
            if not p:
                continue
            amount = min(d["n"], int(self.cfg["max_amount"]), MAX_AMOUNT - self.have(d["id"], state), left // p)
            if amount <= 0:
                continue
            out.append({"id": d["id"], "price": p, "amount": amount, "why": d["why"]})
            left -= p * amount
            if len(out) >= slots:
                break
        return out

    # ---------- тик ----------

    async def tick(self):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        state = self.state
        buyer = state.get("buyer")
        if not isinstance(buyer, dict):
            return                                   # тело без плагина buyer
        pending = self.st.get("pending")
        if pending:
            await self.follow(now, state, pending)
            return
        if buyer.get("open") or self.st.get("open"):
            await self.maybe_close(now, state, buyer)
            return
        if self.is_merchant(state) and await self.maybe_setup(now, state, buyer):
            return
        if buyer.get("can") and await self.maybe_open(now, state, buyer):
            return
        await self.maybe_sell(now, state, buyer)

    async def maybe_open(self, now, state, buyer):
        if now < self.st.get("next_open", 0):
            return False
        if now - self.st.get("last_open", 0) < self.cfg["open_gap_minutes"] * 60:
            return False
        if not self.at_rest() or self.busy(now, state):
            return False
        if buyer.get("skill") and int(state.get("sp") or 0) < SKILL_SP:
            return False
        plan = self.plan(now, state)
        if not plan:
            return False
        vend = state.get("vend") or {}
        if vend.get("open"):                         # по очереди с лавкой: сначала лавка, потом скупка
            seen = self.st.setdefault("vend_seen", now)
            if now - seen < self.cfg["vend_turn_minutes"] * 60:
                self.save()
                return False
            r = getattr(self.mind, "routine", None)
            if r:
                r.last_vend = now + self.cfg["open_minutes"] * 60   # распорядок не откроет лавку, пока моя очередь
            await self.mind.execute([{"action": "shop_close"}], source="buying",
                                    reason="скупка: очередь скупки — закрыть лавку", protocol=True)
            return True
        self.st.pop("vend_seen", None)
        title = f"{self.me()}: куплю"
        self.st["last_open"] = now
        self.st["asked"] = {"ts": now, "items": plan}
        self.save()
        what = ", ".join(f"{self.prices.name(p['id'])} x{p['amount']} по {p['price']}z" for p in plan)
        self.note("buying_open", f"Открываю скупку: {what}.", 1, items=plan)
        await self.mind.execute([{"action": "buyer_open", "title": title,
                                  "items": [{k: p[k] for k in ("id", "price", "amount")} for p in plan]}],
                                source="buying", reason="скупка: то, что нужно жителям", protocol=True)
        return True

    async def maybe_close(self, now, state, buyer):
        opened = self.st.get("open") or {}
        why = None
        if not buyer.get("open"):
            if opened and now - opened.get("since", now) > 120:
                self.st["open"] = None               # тело закрыло без события (relog) — забыть
                self.save()
            return
        r = getattr(self.mind, "routine", None)
        if opened and now - opened.get("since", now) >= self.cfg["open_minutes"] * 60:
            why = "время скупки вышло"
        elif r and not r.in_town_mode:
            why = "пора на охоту" if r.st.get("mode") == "hunt" else "ухожу из города"
        elif self.mind.plans.store.active():
            why = "иду на встречу"
        elif state.get("dead"):
            why = "мёртв"
        if why and now - self.st.get("close_sent", 0) >= 60:
            self.st["close_sent"] = now
            self.save()
            await self.mind.execute([{"action": "buyer_close"}], source="buying", reason=f"скупка: {why}",
                                    protocol=True)

    # ---------- продавец ----------

    def sell_items(self, state):
        """Что продать в чужую скупку: [{id, keep, min}] — не нужное мне, дороже NPC."""
        keep = self.econ.keep_items(state) | NO_SELL
        items = state.get("items") or {}
        coll = getattr(self.mind, "collection", None)
        if coll:
            items = coll.sellable(items)
        oc = self.overcharge(state)
        ids = [str(i) for i, n in items.items() if int(n or 0) > 0]
        ids += [str(i) for i in (self.cfg.get("goods") or {}) if str(i) not in items]   # травы — мосту не видны
        out = []
        for item in ids:
            if item in keep or not self.prices.item(item):
                continue
            low = self.prices.npc_sell(item, 1, oc) + 1
            if self.prices.is_card(item):
                low = max(low, self.prices.value(item))
            out.append({"id": int(item), "keep": 0, "min": low})
            if len(out) >= 20:
                break
        return out

    async def maybe_sell(self, now, state, buyer):
        stores = [s for s in buyer.get("stores") or [] if isinstance(s, dict)
                  and s.get("name") in self.mind.ctx.peers and s.get("name") != self.me()]
        if not stores or (state.get("vend") or {}).get("open") or buyer.get("open"):
            return False
        if self.st.get("selling") and now - self.st["selling"].get("ts", 0) < 120:
            return False                             # жду итога продажи
        if state.get("map") != (getattr(self.mind, "routine", None) and self.mind.routine.town.get("map")):
            return False
        if self.busy(now, state):
            return False
        tried = self.st["tried"]
        gap = self.cfg["sell_gap_minutes"] * 60
        store = next((s for s in stores if now - tried.get(s["name"], 0) >= gap
                      and int((self.mind.mem.relation(s["name"]) or {}).get("affinity") or 0) > -3), None)
        if not store:
            return False
        items = self.sell_items(state)
        if not items:
            return False
        tried[store["name"]] = now
        self.st["selling"] = {"ts": now, "from": store["name"], "zeny": int(state.get("zeny") or 0)}
        self.save()
        await self.mind.execute([{"action": "buyer_sell", "from": store["name"], "items": items}],
                                source="buying", reason=f"скупка {store['name']}: продать лишнее", protocol=True)
        return True

    # ---------- снаряжение торговца ----------

    def kafra(self, state):
        town = (self.homes.get("towns") or {}).get(state.get("map") or "")
        return town if town and town.get("npc") and town.get("stand") else None

    def alberta_steps(self):
        """Дорога к Mr. Hugh — точки move этапа first_job пути merchant (сверено test_newborn)."""
        path = (self.progression.get("paths") or {}).get("merchant") or {}
        stage = next((s for s in path.get("stages") or [] if s.get("id") == "first_job"), None)
        if not stage or ((path.get("route") or {}).get("from") or [None])[0] is None:
            return None, None
        moves = [{k: s[k] for k in ("do", "map", "x", "y", "time_limit") if k in s}
                 for s in stage["steps"] if s.get("do") == "move"]
        moves.append({"do": "move", "map": HUGH["map"], "x": HUGH["stand"][0], "y": HUGH["stand"][1]})
        return moves, path["route"]["from"][0]

    def setup_need(self, state, buyer):
        """Следующий этап снаряжения (stage, cost) или None."""
        s, cfg = self.cfg["setup"], self.cfg
        if s.get("cart") and int(buyer.get("pushcart") or 0) >= 1 and not buyer.get("cart"):
            return "cart", int(cfg["cart_fee"])
        if s.get("license") and int(buyer.get("vending") or 0) >= 1 and not buyer.get("skill"):
            return "license", LICENSE_FEE
        if (s.get("permits") and buyer.get("skill") and int(buyer.get("permits") or 0) < int(cfg["min_permits"])):
            return "permits", PERMIT_PRICE * int(cfg["permits_buy"])
        return None

    def setup_action(self, stage, state):
        if stage == "cart":
            k = self.kafra(state)
            steps = [{"do": "move", "map": state["map"], "x": k["stand"]["x"], "y": k["stand"]["y"]},
                     {"do": "talk", "x": k["npc"]["x"], "y": k["npc"]["y"], "ordered": True,
                      "answers": [{"text": t} for t in CART_ANSWERS]}]
            return {"action": "job_change", "path": PATH, "stage": stage, "steps": steps, "success": {}}
        moves, _ = self.alberta_steps()
        talk = {"do": "talk", "x": HUGH["x"], "y": HUGH["y"], "ordered": True}
        if stage == "license":
            name = "".join(ch for ch in str(self.me()) if ch.isascii() and ch.isalnum())[:23] or "Merchant"
            talk.update(answers=[{"text": t} for t in LICENSE_ANSWERS], input_text=name)
            success = {"text": LICENSE_PROOF, "map": HUGH["map"]}
        else:
            talk.update(answers=[{"text": t} for t in PERMITS_ANSWERS], input_number=int(self.cfg["permits_buy"]))
            success = {"text": PERMITS_PROOF, "map": HUGH["map"]}
        return {"action": "job_change", "path": PATH, "stage": stage, "steps": moves + [talk], "success": success}

    def setup_blocked(self, stage, cost, now, state):
        if now < self.st.get("setup_next", 0):
            return "пауза после неудачи"
        zeny = int(state.get("zeny") or 0)
        if zeny < cost + int(self.cfg["reserve_zeny"]):
            return f"зени {zeny} < {cost} + запас {self.cfg['reserve_zeny']}"
        if not self.at_rest():
            return "не отдых в городе"
        if self.busy(now, state):
            return self.busy(now, state)
        may_move = getattr(self.mind, "may_move", None)
        if may_move and not may_move("plan")[0]:
            return "телом владеет другая задача"
        if (state.get("vend") or {}).get("open"):
            return "открыта лавка"
        if stage == "cart":
            return None if self.kafra(state) else f"нет Kafra в {state.get('map')} (homes.json)"
        moves, start = self.alberta_steps()
        if not moves:
            return "нет дороги к Mr. Hugh (progression.json merchant)"
        if state.get("map") != start:
            return f"дорога в Альберту — из {start}"
        free = (state.get("craft") or {}).get("weight_free")
        if free is None or int(free) < HUGH["free_weight"]:
            return f"свободный вес {free} < {HUGH['free_weight']} (buying_shops.txt:105)"
        return None

    async def maybe_setup(self, now, state, buyer):
        need = self.setup_need(state, buyer)
        if not need:
            return False
        stage, cost = need
        why = self.setup_blocked(stage, cost, now, state)
        if why:
            if self.st.get("setup_why") != why:
                self.st["setup_why"] = why
                self.mind.write_decision({"type": "buying", "event": "setup_wait", "stage": stage, "why": why})
                self.save()
            return False
        before = {"cart": int(bool(buyer.get("cart"))), "skill": int(buyer.get("skill") or 0),
                  "permits": int(buyer.get("permits") or 0)}
        self.st["pending"] = {"stage": stage, "ts": now, "before": before, "zeny": int(state.get("zeny") or 0)}
        self.st.pop("setup_why", None)
        self.save()
        text = {"cart": "Беру тележку у Kafra.", "license": "Еду в Альберту к Mr. Hugh за правом открыть скупку.",
                "permits": "Еду в Альберту за лицензиями скупки."}[stage]
        self.note("buying_setup", text, 2, stage=stage, cost=cost)
        await self.mind.execute([self.setup_action(stage, state)], source="buying", reason=f"торговец: {stage}",
                                protocol=True)
        return True

    def setup_done(self, pending, buyer):
        stage, b = pending["stage"], pending["before"]
        if stage == "cart":
            return bool(buyer.get("cart"))
        if stage == "license":
            return int(buyer.get("skill") or 0) > b["skill"]
        return int(buyer.get("permits") or 0) > b["permits"]

    async def follow(self, now, state, pending):
        buyer = state.get("buyer") or {}
        if self.setup_done(pending, buyer):
            self.st["pending"] = None
            self.st["setup_fails"] = 0
            self.save()
            text = {"cart": "Взял тележку у Kafra — теперь можно и лавку.",
                    "license": "Mr. Hugh записал меня: теперь я могу открывать скупку.",
                    "permits": f"Купил лицензии скупки: теперь их {buyer.get('permits')}."}[pending["stage"]]
            self.note("buying_setup_done", text, 3, stage=pending["stage"],
                      paid=pending["zeny"] - int(state.get("zeny") or 0))
            return
        running = (state.get("job_change") or {}).get("running")
        if pending.get("result_ok") and now - pending["result_ok"] >= self.cfg["verify_minutes"] * 60:
            self.setup_failed(now, "этап пройден, а в state нет результата")
        elif not running and now - pending["ts"] >= self.cfg["wait_result_minutes"] * 60:
            self.setup_failed(now, "нет итога этапа от тела")

    def setup_failed(self, now, reason):
        pending = self.st.get("pending") or {}
        self.st["pending"] = None
        self.st["setup_fails"] = self.st.get("setup_fails", 0) + 1
        self.st["setup_next"] = now + self.cfg["setup_retry_hours"] * 3600
        self.save()
        self.note("buying_setup_failed", f"Не вышло ({pending.get('stage')}): {reason}.", 2,
                  stage=pending.get("stage"), reason=reason)
        if self.st["setup_fails"] >= 3:
            alert = getattr(self.mind, "alert", None)
            if alert:
                alert("buying", f"торговец: этап {pending.get('stage')} — 3 неудачи подряд ({reason})")

    def on_result(self, event):
        """job_change_result: свой (path buying) — True (поглощено, в career не идёт); чужой — False."""
        if event.get("path") != PATH:
            return False
        pending = self.st.get("pending")
        if not pending:
            self.mind.write_decision({"type": "buying", "event": "stray_result", "ok": bool(event.get("ok"))})
            return True
        if event.get("ok"):
            pending["result_ok"] = self.clock()
            self.save()
        else:
            self.setup_failed(self.clock(), str(event.get("reason") or "этап не пройден"))
        return True

    # ---------- события тела ----------

    def on_buyer_result(self, event):
        now = self.clock()
        if event.get("ok"):
            items = [i for i in event.get("items") or [] if isinstance(i, dict)]
            self.st["open"] = {"since": now, "items": items, "limit": int(event.get("limit") or 0)}
            self.st["fails"] = 0
            what = ", ".join(f"{self.prices.name(i.get('id'))} по {i.get('price')}z" for i in items)
            self.note("buying_opened", f"Скупка открыта: {what}.", 2, items=items, limit=event.get("limit"))
        else:
            self.st["open"] = None
            self.st["fails"] = self.st.get("fails", 0) + 1
            self.st["next_open"] = now + self.cfg["retry_minutes"] * 60 * self.st["fails"]
            self.note("buying_open_failed", f"Скупка не открылась: {event.get('reason')}.", 1,
                      reason=event.get("reason"))
        self.save()

    def on_bought(self, event):
        item, amount, zeny = int(event.get("item") or 0), int(event.get("amount") or 0), int(event.get("zeny") or 0)
        if amount <= 0:
            return
        stats = self.st["stats"]
        stats["bought"] += amount
        stats["spent"] += zeny
        self.save()
        self.note("buying_bought", f"В скупку продали {self.prices.name(item)} x{amount} ({zeny}z).", 2,
                  item=item, amount=amount, zeny=zeny, price=event.get("price"))

    def on_closed(self, event):
        self.st["open"] = None
        self.st["close_sent"] = 0
        self.save()
        bought = event.get("bought") or {}
        what = ", ".join(f"{self.prices.name(i)} x{n}" for i, n in bought.items()) or "ничего"
        self.note("buying_closed", f"Скупка закрыта ({event.get('why')}): куплено {what} за {event.get('spent', 0)}z.",
                  1, why=event.get("why"), bought=bought, spent=event.get("spent"))

    def on_sell_result(self, event):
        self.st["selling"] = None
        sold = [s for s in event.get("sold") or [] if isinstance(s, dict)]
        owed = sum(int(s.get("amount") or 0) * int(s.get("price") or 0) for s in sold)
        gain = int(event.get("zeny_gain") or 0)
        peer = event.get("from")
        if event.get("ok") and sold and gain >= owed > 0:
            stats = self.st["stats"]
            stats["sold"] += sum(int(s.get("amount") or 0) for s in sold)
            stats["earned"] += gain
            what = ", ".join(f"{self.prices.name(s.get('item'))} x{s.get('amount')}" for s in sold)
            self.note("buying_sold", f"Продал в скупку {peer}: {what} за {gain}z.", 2, peer=peer, sold=sold, zeny=gain)
        elif event.get("ok") and sold:
            self.note("buying_unverified", f"Продажа в скупку {peer}: зени пришло {gain} из {owed} — не верю.", 1,
                      peer=peer, sold=sold, zeny=gain, owed=owed)
        else:
            self.mind.write_decision({"type": "buying", "event": "sell_refused", "peer": peer,
                                      "reason": event.get("reason")})
        self.save()

    # ---------- промпт ----------

    def summary(self):
        buyer = self.state.get("buyer")
        if not isinstance(buyer, dict):
            return None
        out = {}
        if buyer.get("open"):
            out["открыта"] = [f"{self.prices.name(i.get('id'))} по {i.get('price')}z" for i in buyer.get("items") or []]
        if self.is_merchant():
            out["тележка"] = bool(buyer.get("cart"))
            out["навык_скупки"] = bool(buyer.get("skill"))
            out["лицензий"] = buyer.get("permits")
        if self.st.get("pending"):
            out["снаряжаюсь"] = self.st["pending"]["stage"]
        stats = self.st.get("stats") or {}
        if any(stats.values()):
            out["итог"] = stats
        return out or None
