"""Заказы между жителями (ORG-070, ТЗ Т-16). Правила без LLM, тик раз в check_seconds.

Житель, которому нужен предмет (economy.wishlist: economy.market.wish, предметы этапа карьеры, приручение питомца),
публикует заказ в шину мира; другой житель, у которого предмет есть или который сам его добывает, берёт заказ
шёпотом и выполняет его обычной сделкой рынка ORG-033 — протокол сделки (offer/offer_sell/offer_buy) не дублируется.

Шина мира (world_bus):
    order {id, item, name, n, reward, until}          заказчик, важность 2 (новости жителей в промпте)
    order_taken {id, by} / order_closed {id, why, by} заказчик, важность 1
    order_done {id, for, item, name, n, reward}       исполнитель, важность 3 (летопись)
Шёпот (метки в конце текста):
    [order:<id>:take]      исполнитель -> заказчик: берусь
    [order:<id>:ok|no]     заказчик: первому взявшему ok (поле taken_by), остальным no — двойного резерва нет
    [order:<id>:cancel]    заказчик -> исполнитель: больше не нужно

Заказчик: один открытый заказ, не чаще post_gap_hours. Что заказывать — из списка желаний: не зени, не запасы share
(их просят [need:]), не то, что продаёт NPC (atlas.item_shops), с известной ценой prices.json. Награда —
prices.buy_limit(item, n): ровно столько экономика заказчика согласится заплатить за предмет из списка желаний
(economy.offer_refuse_reason), и после неё остаётся market.keep_zeny. Закрытие по фактам памяти после публикации:
trade_bought этого предмета — done (от взявшего) или bought (купил у другого); предмет больше не нужен — cancel
(шёпот взявшему); срок days — expired (никто не взял) или failed (взял и не выполнил).
Исполнитель: один взятый заказ; кандидат — чужой открытый (не взят, не закрыт, срок не вышел) заказ, предмет есть в
рюкзаке (state.items) или я сам добывал его за loot_days дней (события loot памяти по имени предмета; дропа монстров
в атласе нет), награда не меньше продажи NPC, предмет не нужен мне самому, заказчик не в ссоре. Сначала — что есть в
рюкзаке, затем дороже. Доставка: предмета хватает и заказчик виден — economy.offer_lot(..., price=награда), не чаще
offer_gap_minutes. Факт — trade_sold с заказчиком на этот предмет и количество (economy проверила оплату ≥ цены) ->
order_done, репутация исполнителя +1 (kv orders.rep). Срыв срока — репутация −1, отношение не меняется (не ссора).
Зарезервированное economy.for_sale не продаёт другим. Без шины мира модуль молчит.
Конфигурация — раздел "orders" brain/world/goals.json (необязателен); BRAIN_DISABLE=orders.
"""
import json
import logging
import re
import secrets
import time

from .safety import fit_text
from .world_bus import WorldBus

log = logging.getLogger("orders")

TAG = re.compile(r"\[order:([a-z0-9]{4,8}):(take|ok|no|cancel)\]")
BUS_KINDS = ("order", "order_taken", "order_closed", "order_done")
DEFAULTS = {
    "enabled": True,
    "check_seconds": 60,
    "post_gap_hours": 6,
    "days": 2,
    "max_n": 30,
    "scan_minutes": 5,
    "offer_gap_minutes": 10,
    "loot_days": 7,
    "answer_seconds": 120,
}


class Orders:
    # реестр модулей (modules.py, W8): создание, тик, метка [order:]
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES, ARGS = "orders", "orders", "orders", True, ("peers", "economy"), "world"
    TICK_ORDER = 25                          # после economy (20): сделки этого тика уже учтены
    TAGS, TAG_ORDER = [(TAG, "on_tag")], 25  # после economy [need:]/[offer:] (20)

    def __init__(self, mind, world=None, clock=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("orders") or {}))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.st = mind.mem.get("orders") or {}
        self.st.setdefault("rep", {"done": 0, "failed": 0})
        self.next_check = 0.0
        self.next_scan = 0.0
        self._shops = None

    # ---------- данные ----------

    @property
    def econ(self):
        return self.mind.economy

    @property
    def state(self):
        return self.mind.state

    def me(self):
        return self.state.get("name") or self.mind.persona["name"]

    def save(self):
        self.mind.mem.set("orders", self.st)

    def bus(self):
        bus = getattr(getattr(self.mind, "world", None), "bus", None)
        return bus if isinstance(bus, WorldBus) else None          # только настоящая шина мира (world_bus.Feed)

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "orders", "event": kind, "text": text, **data})
        log.info("%s", text)

    def publish(self, kind, data, importance):
        bus = self.bus()
        if not bus:
            return None
        try:
            return bus.publish(kind, data, importance, now=self.clock())
        except Exception as e:                                      # общая БД занята — не роняем тик
            log.warning("шина мира: %s", e)
            return None

    def cursor(self):
        return self.mind.mem.db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]

    def find_event(self, kind, after, pred):
        for (data,) in self.mind.mem.db.execute("SELECT data FROM events WHERE id > ? AND kind = ? ORDER BY id",
                                                (after, kind)):
            try:
                d = json.loads(data)
            except ValueError:
                continue
            if pred(d):
                return d
        return None

    def shop_items(self):
        """Предметы, которые продаёт NPC (атлас): их не заказывают — проще купить."""
        if self._shops is None:
            try:
                from . import atlas
                self._shops = set(atlas.default().item_shops)
            except (OSError, ValueError, AttributeError):
                self._shops = set()
        return self._shops

    def lot(self, o):
        return f"{o.get('name') or o['item']} x{o['n']} за {o['reward']}z"

    def visible(self, name, state=None):
        return any(isinstance(p, dict) and p.get("name") == name for p in (state or self.state).get("players") or [])

    def in_quarrel(self, peer):
        society = getattr(self.mind, "society", None)
        return bool(society and society.quarrel(peer))

    def reserved(self):
        """{предмет: n} взятого заказа — economy.for_sale не продаёт его другим."""
        j = self.st.get("job")
        return {j["item"]: j["n"]} if j and j.get("status") == "taken" else {}

    async def whisper(self, to, text, reason):
        await self.mind.execute([{"action": "whisper", "to": to, "text": fit_text(text)}], source="orders",
                                reason=reason, protocol=True)

    # ---------- заказчик ----------

    def candidates(self, state):
        """[(предмет, n)] — что заказать: из списка желаний, кроме зени, запасов share и товаров NPC."""
        econ = self.econ
        out = []
        for item, n in sorted(econ.wishlist(state).items()):
            item = str(item)
            if item == "z" or item in econ.share or item in self.shop_items() or not econ.prices.item(item):
                continue
            out.append((item, max(1, min(int(n), int(self.cfg["max_n"])))))
        return out

    async def maybe_post(self, now, state):
        if self.st.get("mine") or now - self.st.get("last_post", 0) < self.cfg["post_gap_hours"] * 3600:
            return
        econ = self.econ
        zeny = int(state.get("zeny") or 0)
        discount = int((state.get("vend") or {}).get("discount") or 0)
        for item, n in self.candidates(state):
            reward = econ.prices.buy_limit(item, n, discount)
            if reward < 1 or zeny - reward < econ.market["keep_zeny"] or reward > zeny * econ.market["max_share"]:
                continue
            oid = secrets.token_hex(3)
            mine = {"id": oid, "item": item, "name": econ.prices.name(item), "n": n, "reward": reward,
                    "since": now, "until": now + self.cfg["days"] * 86400, "status": "open", "cursor": self.cursor()}
            if self.publish("order", {k: mine[k] for k in ("id", "item", "name", "n", "reward", "until")}, 2) is None:
                return                                              # нет шины — заказ никто не увидит
            self.st["mine"] = mine
            self.st["last_post"] = now
            self.save()
            self.note("order_posted", f"Заказал в шине мира: {self.lot(mine)}.", 2, id=oid, item=item,
                      name=mine["name"], n=n, reward=reward)
            return

    async def check_mine(self, now, state):
        m = self.st["mine"]
        bought = self.find_event("trade_bought", m["cursor"], lambda d: str(d.get("item")) == m["item"])
        if bought:
            by = bought.get("peer")
            if by and by == m.get("taken_by"):
                await self.close_mine("done", f"{by} выполнил мой заказ: {self.lot(m)}.", 3)
            else:
                await self.close_mine("bought", f"Купил {m['name']} у {by} — заказ закрыт.", 1)
        elif m["item"] not in {str(k) for k in self.econ.wishlist(state)}:
            if m.get("taken_by"):
                await self.whisper(m["taken_by"], f"{m['name']} мне больше не нужен, прости. [order:{m['id']}:cancel]",
                                   "заказы: отмена")
            await self.close_mine("cancel", f"Заказ {self.lot(m)} больше не нужен.", 1)
        elif now >= m["until"]:
            if m.get("taken_by"):
                await self.close_mine("failed", f"{m['taken_by']} не выполнил заказ {self.lot(m)} в срок.", 2)
            else:
                await self.close_mine("expired", f"Мой заказ {self.lot(m)} никто не взял.", 1)

    async def close_mine(self, why, text, importance):
        m = self.st.pop("mine")
        self.save()
        self.publish("order_closed", {"id": m["id"], "why": why, "by": m.get("taken_by")}, 1)
        self.note("order_closed", text, importance, id=m["id"], item=m["item"], name=m["name"], n=m["n"],
                  reward=m["reward"], why=why, by=m.get("taken_by"))

    async def on_take(self, sender, oid):
        m = self.st.get("mine")
        if not m or m["id"] != oid or m.get("taken_by") or self.clock() >= m["until"]:
            await self.whisper(sender, f"Спасибо, но заказ уже не открыт. [order:{oid}:no]", "заказы: занят")
            return
        m.update(taken_by=sender, taken_at=self.clock(), status="taken")
        self.save()
        await self.whisper(sender, f"Договорились: {self.lot(m)}. Жду! [order:{oid}:ok]", "заказы: отдаю заказ")
        self.publish("order_taken", {"id": oid, "by": sender}, 1)
        self.note("order_given", f"{sender} взялся за мой заказ: {self.lot(m)}.", 1, id=oid, by=sender, item=m["item"])

    # ---------- исполнитель ----------

    def open_orders(self, now):
        """Чужие открытые заказы из шины: не взяты, не закрыты, срок не вышел (старые первыми)."""
        bus = self.bus()
        if not bus:
            return []
        try:
            rows = bus.read(since=now - (self.cfg["days"] + 1) * 86400, limit=1000)
        except Exception as e:
            log.warning("шина мира: %s", e)
            return []
        closed, taken, orders = set(), set(), []
        for r in rows:
            d = r["data"]
            if r["kind"] in ("order_closed", "order_done"):
                closed.add(d.get("id"))
            elif r["kind"] == "order_taken":
                taken.add(d.get("id"))
            elif r["kind"] == "order" and r["bot"] != self.me() and r["bot"] in self.mind.ctx.peers:
                orders.append(dict(d, customer=r["bot"]))
        refused = set(self.st.get("refused") or [])
        return [o for o in orders if o.get("id") not in closed | taken | refused
                and float(o.get("until") or 0) > now and o.get("item") and int(o.get("n") or 0) > 0]

    def looted(self, name):
        """Добывал ли я этот предмет сам за loot_days дней (событие loot — имя предмета от OpenKore)."""
        since = time.time() - self.cfg["loot_days"] * 86400
        for (data,) in self.mind.mem.db.execute("SELECT data FROM events WHERE kind = 'loot' AND ts >= ?", (since,)):
            try:
                if json.loads(data).get("item") == name:
                    return True
            except ValueError:
                continue
        return False

    def spare(self, item, state):
        """review3: сколько предмета можно отдать — без единственной копии карты альбома (collection.sellable),
        как в economy.for_sale; раньше заказ на карту забирал альбомную копию в обход коллекции."""
        coll = getattr(self.mind, "collection", None)
        if coll is None:
            return self.econ.have(item, state)
        return int(coll.sellable((state or self.state).get("items") or {}).get(str(item), 0) or 0)

    def can_fill(self, o, state):
        """None — могу взять; иначе причина."""
        econ = self.econ
        item, n = str(o["item"]), int(o["n"])
        if self.in_quarrel(o["customer"]):
            return "в ссоре"
        if item in {str(k) for k in econ.wishlist(state)}:
            return "самому нужно"
        if int(o.get("reward") or 0) < econ.prices.npc_sell(item, n, econ.overcharge(state)):
            return "NPC заплатит больше"
        if self.spare(item, state) >= n:                            # review3: не альбомная копия
            return None
        name = o.get("name") or econ.prices.name(item)
        return None if self.looted(name) else "нет в рюкзаке и сам не добываю"

    async def scan(self, now, state):
        if self.st.get("job") or now < self.next_scan:
            return
        self.next_scan = now + self.cfg["scan_minutes"] * 60
        econ = self.econ
        if econ.busy_trade() or econ.giving or econ.mailing or econ.body_elsewhere() or self.mind.plans.store.active():
            return
        options = []
        for o in self.open_orders(now):
            if self.can_fill(o, state) is None:
                have = self.spare(str(o["item"]), state) >= int(o["n"])      # review3:
                options.append((0 if have else 1, -int(o.get("reward") or 0), o["id"], o))
        if not options:
            return
        o = sorted(options, key=lambda t: t[:3])[0][3]
        job = {"id": o["id"], "customer": o["customer"], "item": str(o["item"]), "name": o.get("name"),
               "n": int(o["n"]), "reward": int(o["reward"]), "until": float(o["until"]), "status": "asked",
               "since": now, "cursor": self.cursor()}
        self.st["job"] = job
        self.save()
        await self.whisper(o["customer"], f"{o['customer']}, возьмусь: {self.lot(job)}. [order:{o['id']}:take]",
                           "заказы: берусь")
        self.mind.write_decision({"type": "orders", "event": "take_asked", "id": o["id"], "customer": o["customer"],
                                  "lot": self.lot(job)})

    async def check_job(self, now, state):
        j = self.st["job"]
        if j["status"] == "asked":
            if now - j["since"] >= self.cfg["answer_seconds"]:
                self.drop_job(j["id"])
            return
        sold = self.find_event("trade_sold", j["cursor"], lambda d: d.get("peer") == j["customer"]
                               and str(d.get("item")) == j["item"] and int(d.get("amount") or 0) >= j["n"])
        if sold:
            self.st["job"] = None
            self.st["rep"]["done"] = int(self.st["rep"].get("done", 0)) + 1
            self.save()
            paid = int(sold.get("paid") or sold.get("price") or 0)
            self.publish("order_done", {"id": j["id"], "for": j["customer"], "item": j["item"], "name": j["name"],
                                        "n": j["n"], "reward": paid}, 3)
            self.note("order_done", f"Выполнил заказ {j['customer']}: {self.lot(j)} — сделка завершена сервером.", 3,
                      id=j["id"], customer=j["customer"], item=j["item"], name=j["name"], n=j["n"], reward=paid)
            return
        if now >= j["until"]:
            self.st["job"] = None
            self.st["rep"]["failed"] = int(self.st["rep"].get("failed", 0)) + 1
            self.save()
            self.note("order_failed", f"Не успел выполнить заказ {j['customer']}: {self.lot(j)}.", 2,
                      id=j["id"], customer=j["customer"], item=j["item"])
            return
        econ = self.econ
        if (self.spare(j["item"], state) < j["n"] or not self.visible(j["customer"], state)
                or econ.busy_trade() or econ.giving or econ.mailing or state.get("give")
                or now - j.get("offered", 0) < self.cfg["offer_gap_minutes"] * 60):   # review3: spare
            return
        j["offered"] = now
        self.save()
        await econ.offer_lot(j["customer"], j["item"], j["n"], price=j["reward"])

    def drop_job(self, oid):
        self.st["job"] = None
        self.st["refused"] = ((self.st.get("refused") or []) + [oid])[-50:]
        self.save()

    # ---------- метки ----------

    async def on_tag(self, sender, text):
        m = TAG.search(text or "")
        if not m:
            return
        oid, kind = m.groups()
        if kind == "take":
            await self.on_take(sender, oid)
            return
        j = self.st.get("job")
        if not j or j["id"] != oid or j["customer"] != sender:
            return
        if kind == "ok" and j["status"] == "asked":
            j.update(status="taken", since=self.clock())
            self.save()
            self.note("order_taken", f"Взялся за заказ {sender}: {self.lot(j)}.", 2, id=oid, customer=sender,
                      item=j["item"], name=j["name"], n=j["n"], reward=j["reward"])
        elif kind in ("no", "cancel"):
            self.drop_job(oid)
            self.mind.write_decision({"type": "orders", "event": f"job_{kind}", "id": oid, "customer": sender})

    # ---------- тик ----------

    async def tick(self):
        if not self.cfg.get("enabled", True) or not self.mind.fresh_state or not self.bus():
            return
        now = self.clock()
        if now < self.next_check:
            return
        self.next_check = now + self.cfg["check_seconds"]
        state = self.state
        if state.get("dead"):
            return
        if self.st.get("mine"):
            await self.check_mine(now, state)
        else:
            await self.maybe_post(now, state)
        if self.st.get("job"):
            await self.check_job(now, state)
        else:
            await self.scan(now, state)


# Строки летописи (chronicle.LINES дополняется этим словарём).
CHRONICLE_LINES = {
    "order_posted": lambda d: f"заказал(а) {d.get('name')} x{d.get('n')} за {d.get('reward')}z",
    "order_taken": lambda d: f"взялся(ась) за заказ {d.get('customer')}: {d.get('name')} x{d.get('n')}",
    "order_done": lambda d: f"выполнил(а) заказ {d.get('customer')}: {d.get('name')} x{d.get('n')} за {d.get('reward')}z",
    "order_failed": lambda d: f"не успел(а) выполнить заказ {d.get('customer')}",
    "order_closed": lambda d: {"done": f"заказ выполнил(а) {d.get('by')}", "bought": "заказ закрыт: купил(а) сам(а)",
                               "cancel": "заказ отменён: больше не нужен", "expired": "заказ никто не взял",
                               "failed": f"{d.get('by')} не выполнил(а) заказ в срок"}.get(d.get("why"), "заказ закрыт"),
}
