"""Взаимопомощь жителей: поделиться зельями и зени при встрече в городе. Правила, без LLM.

Сами продажа лута, закупка зелий и склад Kafra — настройки OpenKore (sellAuto, buyAuto,
storageAuto) и плагин economy. Этот модуль решает только, кто кому и что отдаёт.

Протокол — шёпот жителю с меткой (человекочитаемый текст + машинная метка):
    [need:<id>:<предмет>:<сколько>]   прошу; предмет — ID (501) или z (зени)
    [need:<id>:ok]                    отдаю (тело идёт к просящему и предлагает сделку)
    [need:<id>:no]                    не могу

Когда прошу: режим «отдых в городе», нет активного плана встречи, житель виден рядом,
мне не хватает предмета из economy.share (меньше need_below) или зени (меньше need_below),
с прошлой просьбы прошло ask_gap_minutes.
Когда отдаю: прошу не больше ask, у меня после передачи остаётся не меньше keep,
за сутки отдал меньше gifts_per_day раз, не идёт другая передача.
Доказательства:
    «отдал»     — событие give_result ok от тела: сервер завершил сделку;
    «получил»   — сервер завершил сделку именно с этим жителем (deal_complete) И количество предмета/зени
                  в моём состоянии выросло (покупка у NPC в то же время подарком не считается).
Слова и ack команды доказательством не считаются.

Торговля между жителями (ORG-033), цены — prices.py (prices.json из базы rAthena):
    [offer:<id>:<предмет>:<сколько>:<цена>]   продаю лот за цену (зени за весь лот)
    [offer:<id>:ok] / [offer:<id>:no]         беру / не беру
Продавец: в городе, рядом житель (Merchant — первым), в рюкзаке лот дороже valuable_lot, не нужный самому.
Покупатель берёт, если зени хватает с запасом (keep_zeny, не больше max_share зени) и предмет в его списке
желаний (economy.market.wish, нехватка share, предметы этапа карьеры progression) по цене не выше
buy_limit — или для перепродажи NPC с выгодой resale_gain (Overcharge покупателя).
Сделка одна и двусторонняя: продавец offer_sell (кладёт предмет), покупатель offer_buy (кладёт зени),
плагин каждой стороны проверяет часть другой до подтверждения; обмен на сервере атомарен.
Доказательства: продавец — give_result ok с paid ≥ price; покупатель — buy_result ok (сервер завершил сделку,
плагин видел товар в окне сделки) И рост предмета в рюкзаке. Нет роста за VERIFY_TIMEOUT — запись
trade_unverified (не «купил»). Если продавец не получил оплату целиком, а предмет ушёл — долг в памяти
(market_debts) и отношение −1; вечных обещаний нет: долг только записан, не взыскивается.

Почта RODEX (ORG-035): подарок на расстоянии — просьба [need:...] от жителя, которого нет рядом,
исполняется письмом mail_send (сбор: 2 % зени + 2500z за предмет — rAthena misc.conf); получатель
забирает вложение (mail_received -> mail_take -> mail_taken), это и есть доказательство «получил».
Раз в неделю — письмо «Итог недели» другу (affinity >= week_friend). Лимит писем — safety.MAIL_PER_DAY.
Лавка (ORG-034): у жителя с MC_VENDING и тележкой — offer_shop с товарами и ценами из prices.py.
Метрики (ORG-037): economy_metrics(memory, since) — для report и хроники.
"""
import json
import logging
import re
import secrets
import time

from . import progression
from .prices import Prices

log = logging.getLogger("economy")

TAG = re.compile(r"\[need:([a-z0-9]{4,8}):(?:(ok|no)|(z|\d{1,6}):(\d{1,7}))\]")
NEAR = 8                  # житель виден не дальше 8 клеток
ANSWER_TIMEOUT = 120      # ответ на просьбу
RECEIVE_TIMEOUT = 240     # после ok — дождаться передачи
GIVE_TIMEOUT = 180        # отдаю: итог give_result от тела должен прийти раньше
OFFER_TAG = re.compile(r"\[offer:([a-z0-9]{4,8}):(?:(ok|no)|(\d{1,6}):(\d{1,5}):(\d{1,9}))\]")
SELL_TIMEOUT = 300        # продаю: подойти, сделка, итог give_result
BUY_TIMEOUT = 300         # покупаю: продавец подходит, итог buy_result
VERIFY_TIMEOUT = 60       # после buy_result ok — рост предмета в рюкзаке
MAIL_TIMEOUT = 120        # итог mail_result / mail_taken от тела
REMOTE_RECEIVE = 900      # подарок почтой: письмо надо получить и забрать
MAIL_TAX_ITEM = 2500      # rAthena conf/battle/misc.conf mail_attachment_price
MERCHANTS = ("Merchant", "Blacksmith", "Alchemist", "Whitesmith", "Creator", "Mechanic", "Genetic")
MARKET = {"enabled": True, "offer_gap_minutes": 30, "trades_per_day": 4, "keep_zeny": 5000, "max_share": 0.5,
          "wish": [], "mail_gifts": True, "remote_gap_minutes": 120, "week_friend": 3, "shop_hours": 6, "decline_hours": 12}


def dist(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


class Economy:
    def __init__(self, mind, cfg, clock=None):
        self.mind = mind
        self.cfg = cfg
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.share = {str(k): v for k, v in (cfg.get("share") or {}).items()}
        self.zeny = cfg.get("zeny") or {}
        self.gap = cfg.get("ask_gap_minutes", 20) * 60
        self.per_day = cfg.get("gifts_per_day", 6)
        mem = mind.mem
        self.req = mem.get("econ_request")           # моя просьба: id, peer, item, amount, base, status, since
        self.last_ask = mem.get("econ_last_ask", 0)
        self.giving = None                           # что отдаю сейчас: id, to, item, amount
        self.market = dict(MARKET, **(cfg.get("market") or {}))
        self.prices = Prices.load(cfg=self.market.get("prices"))
        self.offer = mem.get("econ_offer")           # продаю: id, peer, item, amount, price, status, since
        self.buying = mem.get("econ_buying")         # покупаю: id, peer, item, amount, price, status, since, base
        self.mailing = None                          # письмо в пути: id, to, kind (gift|week), item, amount
        self.taking = None                           # забираю письмо: mail_id, from, since
        self.mail_pending = []                       # review: письма, пришедшие, пока был занят (плагин сообщает один раз)
        self.last_offer = mem.get("econ_last_offer", 0)

    # ---------- данные ----------

    @property
    def state(self):
        return self.mind.state

    def have(self, item, state=None):
        state = state or self.state
        if item == "z":
            return int(state.get("zeny") or 0)
        return int((state.get("items") or {}).get(str(item), 0) or 0)

    def item_name(self, item):
        return "зени" if item == "z" else self.share.get(str(item), {}).get("name", f"предмет {item}")

    def label(self, item, amount):
        return f"{amount} {self.item_name(item)}"

    def peer_near(self, state):
        if state.get("x") is None:
            return None
        for p in state.get("players") or []:
            if (isinstance(p, dict) and p.get("name") in self.mind.ctx.peers and p.get("x") is not None
                    and dist(int(state["x"]), int(state["y"]), int(p["x"]), int(p["y"])) <= NEAR):
                return p["name"]
        return None

    def my_need(self, state):
        if not state.get("items"):
            return None                              # тело без плагина economy — счётчиков нет
        for item, rule in self.share.items():
            if self.have(item, state) < rule.get("need_below", 0):
                return item, int(rule["ask"])
        if self.zeny and self.have("z", state) < self.zeny.get("need_below", 0):
            return "z", int(self.zeny["ask"])
        return None

    def gifts_today(self, now):
        return self.mind.mem.count_events("gift_given", now - 86400)

    def summary(self):
        s = self.state
        out = {"зени": s.get("zeny"),
               "запасы": {self.share[i].get("name", i): self.have(i) for i in self.share} if s.get("items") else None,
               "отдал_за_сутки": self.gifts_today(self.clock())}
        if self.req:
            out["моя_просьба"] = {"кому": self.req["peer"], "что": self.label(self.req["item"], self.req["amount"]),
                                  "статус": self.req["status"]}
        return out

    def market_summary(self):
        """market: поле промпта «рынок» — оценка рюкзака и текущая сделка (факты, не обещания)."""
        s = self.state
        inv = self.prices.inventory_value(s.get("items") or {}, self.overcharge())
        out = {"рюкзак_npc_зени": inv["npc"], "рюкзак_для_жителей_зени": inv["value"],
               "ценное": [f"{self.prices.name(i)} x{n} ~{v}z" for i, n, v in self.for_sale()[:5]],
               "сделок_за_сутки": self.trades_today(self.clock())}
        if self.offer:
            out["продаю"] = {"кому": self.offer["peer"], "что": self.lot(self.offer), "статус": self.offer["status"]}
        if self.buying:
            out["покупаю"] = {"у": self.buying["peer"], "что": self.lot(self.buying), "статус": self.buying["status"]}
        debts = self.mind.mem.get("market_debts") or {}
        if debts:
            out["мне_должны"] = debts
        return out

    def save(self):
        self.mind.mem.set("econ_request", self.req)
        self.mind.mem.set("econ_last_ask", self.last_ask)

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "economy", "event": kind, "text": text, **data})
        log.info("%s", text)

    async def whisper(self, to, text, reason):
        await self.mind.execute([{"action": "whisper", "to": to, "text": text[:100]}],
                                source="economy", reason=reason, protocol=True)

    # ---------- тик: просить и проверять ----------

    def can_ask(self):
        r = self.mind.routine
        return (r is None or r.in_town_mode) and not self.mind.plans.store.active()

    async def tick(self):
        now = self.clock()
        if not self.mind.fresh_state:
            return
        state = self.state
        if self.giving and now - self.giving["since"] >= GIVE_TIMEOUT:
            g, self.giving = self.giving, None           # тело не сообщило итог (перезапуск?) — не держать
            self.note("gift_failed", f"Нет итога передачи {g['to']} за {GIVE_TIMEOUT} с.", 1, peer=g["to"])
        if self.mailing and now - self.mailing["since"] >= MAIL_TIMEOUT:
            m, self.mailing = self.mailing, None
            self.note("mail_failed", f"Нет итога письма {m['to']} за {MAIL_TIMEOUT} с.", 1, peer=m["to"])
        if self.taking and now - self.taking["since"] >= MAIL_TIMEOUT:
            self.taking = None
        if (self.mail_pending and not (self.taking or self.mailing or self.giving or self.busy_trade())
                and not state.get("dead")):                 # review: отложенное письмо — забрать, когда свободен
            await self.on_mail_received(self.mail_pending.pop(0))
        await self.check_market(now, state)
        if self.req:
            await self.check_request(now, state)
            return
        if state.get("dead") or not self.can_ask():
            return
        if now - self.last_ask >= self.gap:
            need = self.my_need(state)
            if need:
                await self.ask(*need, remote=bool(self.market["mail_gifts"]))
                return
        if self.market["enabled"]:
            await self.market_tick(now, state)

    async def ask(self, item, amount, force=False, remote=False):
        """Попросить у жителя рядом. force — команда оператора: без проверки нехватки и интервала.
        remote — рядом никого: попросить друга шёпотом, он пришлёт почтой (ORG-035)."""
        now = self.clock()
        state = self.state
        if not re.fullmatch(r"z|\d{1,6}", item) or not isinstance(amount, int) or amount <= 0:
            return "неверный предмет или количество"
        if self.req:
            return "уже жду ответа на просьбу"
        if state.get("dead"):
            return "персонаж мёртв"
        if force and not self.mind.fresh_state:
            return "нет состояния от тела"
        peer = self.peer_near(state)
        far = False
        if not peer and remote and now - (self.mind.mem.get("econ_last_remote") or 0) >= self.market["remote_gap_minutes"] * 60:
            peer, far = self.best_friend(hidden_only=True), True
            if peer:
                self.mind.mem.set("econ_last_remote", now)
        if not peer:
            return "рядом нет жителя"
        rid = secrets.token_hex(3)
        self.req = {"id": rid, "peer": peer, "item": item, "amount": amount, "base": self.have(item, state),
                    "status": "asked", "since": now, "remote": far}
        self.last_ask = now
        self.save()
        text = f"{peer}, у меня почти кончились {self.item_name(item)}. Выручишь?"
        tag = f"[need:{rid}:{item}:{amount}]"
        await self.whisper(peer, f"{text[:99 - len(tag)]} {tag}", "экономика: прошу у жителя")   # метка не обрезается
        self.note("gift_asked", f"Попросил у {peer} {self.label(item, amount)}.", 1, peer=peer, item=item, amount=amount,
                  force=force)
        return None

    async def check_request(self, now, state):
        r = self.req
        if r["status"] == "asked" and now - r["since"] >= ANSWER_TIMEOUT:
            self.finish(f"{r['peer']} не ответил на просьбу.", "gift_failed", 1)
        elif r["status"] == "accepted":
            got = self.have(r["item"], state) - r["base"]
            # рост запаса И сделка с этим жителем (не покупка у NPC) или забранное письмо от него (market:)
            if got > 0 and (r.get("deal_done") or r.get("mail_done")):
                self.mind.mem.update_relation(r["peer"], 1, "выручил, когда мне не хватало")
                self.finish(f"{r['peer']} дал мне {self.label(r['item'], got)} — по данным игры.", "gift_received", 3,
                            got=got, mail=bool(r.get("mail_done")))
            elif now - r["since"] >= (REMOTE_RECEIVE if r.get("remote") else RECEIVE_TIMEOUT):
                self.finish(f"{r['peer']} согласился, но передача не пришла.", "gift_failed", 1)

    def finish(self, text, kind, importance, **data):
        r = self.req
        self.req = None
        self.save()
        self.note(kind, text, importance, peer=r["peer"], item=r["item"], amount=r["amount"], **data)

    # ---------- входящие ----------

    async def on_tag(self, sender, text):
        if OFFER_TAG.search(text or ""):
            await self.on_offer_tag(sender, text)
            return
        m = TAG.search(text or "")
        if not m:
            return
        rid, answer, item, amount = m.groups()
        if answer:
            r = self.req
            if not r or r["id"] != rid or r["peer"] != sender or r["status"] != "asked":
                return
            if answer == "no":
                self.finish(f"{sender} не смог поделиться {self.label(r['item'], r['amount'])}.", "gift_failed", 1)
            else:
                r.update(status="accepted", since=self.clock(), base=self.have(r["item"]))
                self.save()
                log.info("%s согласился передать %s", sender, self.label(r["item"], r["amount"]))
            return
        await self.on_ask(sender, rid, item, int(amount))

    def refuse_reason(self, sender, item, amount):
        now = self.clock()
        state = self.state
        if sender not in self.mind.ctx.peers:
            return "ты не житель"
        if state.get("dead"):
            return "я мёртв"
        if self.mind.plans.store.active():
            return "иду на встречу"
        if self.giving or state.get("give") or self.mailing or self.busy_trade():
            return "уже передаю"
        if (why := self.body_elsewhere()):                  # review: квест профессии или сон
            return why
        if (state.get("vend") or {}).get("open"):
            return "стою с лавкой"
        if self.gifts_today(now) >= self.per_day:
            return f"за сутки уже отдал {self.per_day} раз"
        rule = self.zeny if item == "z" else self.share.get(item)
        if not rule:
            return "этим не делюсь"
        if amount > int(rule.get("ask", 0)):
            return "слишком много"
        if item != "z" and not state.get("items"):
            return "не знаю своих запасов"
        if self.have(item) - amount < int(rule.get("keep", 0)):
            return "самому мало"
        return None

    async def on_ask(self, sender, rid, item, amount):
        why = self.refuse_reason(sender, item, amount)
        if why:
            await self.whisper(sender, f"Извини, не могу: {why}. [need:{rid}:no]", "экономика: отказ")
            self.note("gift_refused", f"{sender} просил {self.label(item, amount)}, отказал: {why}.", 1,
                      peer=sender, item=item, amount=amount)
            return
        if not any(isinstance(p, dict) and p.get("name") == sender for p in self.state.get("players") or []):
            await self.gift_by_mail(sender, rid, item, amount)      # не виден — тело не дойдёт, только почта
            return
        await self.whisper(sender, f"Держи, сейчас подойду. [need:{rid}:ok]", "экономика: отдаю")
        self.giving = {"id": rid, "to": sender, "item": item, "amount": amount, "since": self.clock()}
        give_item = "zeny" if item == "z" else int(item)
        await self.mind.execute([{"action": "give", "to": sender, "item": give_item, "amount": amount}],
                                source="economy", reason=f"экономика: отдать {sender} {self.label(item, amount)}",
                                protocol=True)

    def on_deal_complete(self, event):
        """Сервер завершил сделку с жителем — для получателя это доказательство передачи (вместе с ростом запаса)."""
        r = self.req
        if r and r["status"] == "accepted" and event.get("with") == r["peer"]:
            r["deal_done"] = True
            self.save()

    def on_give_result(self, event):
        if event.get("price") is not None:
            self.on_sell_result(event)                   # итог продажи жителю (offer_sell)
            return
        g = self.giving
        self.giving = None
        to = event.get("to") or (g or {}).get("to")
        item = "z" if event.get("item") == "zeny" else str(event.get("item"))
        amount = event.get("amount")
        if event.get("ok"):
            self.mind.mem.update_relation(str(to), 1, "помог ему, когда ему не хватало")
            self.note("gift_given", f"Отдал {to} {self.label(item, amount)} — сделка завершена сервером.", 2,
                      peer=to, item=item, amount=amount)
        else:
            self.note("gift_failed", f"Не получилось передать {to} {self.label(item, amount)}: {event.get('reason')}.",
                      1, peer=to, item=item, amount=amount)

    def on_rejected(self, action, why):
        """Тело или safety отклонили give — передача не началась."""
        if action.get("action") == "give" and self.giving:
            g, self.giving = self.giving, None
            self.note("gift_failed", f"Не смог начать передачу {g['to']}: {why}.", 1, peer=g["to"])
        kind = action.get("action")
        if kind == "offer_sell" and self.offer:
            self.end_offer(f"Не смог начать продажу {self.offer['peer']}: {why}.", "trade_failed", 1)
        elif kind == "offer_buy" and self.buying:
            self.end_buying(f"Не смог начать покупку у {self.buying['peer']}: {why}.", "trade_failed", 1)
        elif kind == "mail_send" and self.mailing:
            m, self.mailing = self.mailing, None
            self.note("mail_failed", f"Письмо {m['to']} не ушло: {why}.", 1, peer=m["to"])
        elif kind == "mail_take":
            self.taking = None

    # ---------- общее для рынка ----------

    def overcharge(self, state=None):
        return int(((state or self.state).get("vend") or {}).get("overcharge") or 0)

    def greed(self):
        return float((((getattr(self.mind, "persona", None) or {}).get("traits")) or {}).get("greed", 0.5))

    def affinity(self, peer):
        return int((self.mind.mem.relation(peer) or {}).get("affinity") or 0)

    def lot(self, o):
        return f"{self.prices.name(o['item'])} x{o['amount']} за {o['price']}z"

    def trades_today(self, now):
        return sum(self.mind.mem.count_events(k, now - 86400) for k in ("trade_sold", "trade_bought"))

    def busy_trade(self):
        return bool(self.offer or self.buying)

    def body_busy(self):                                    # review: для арбитра (lifecycle) — сделка держит тело
        """Тело занято передачей или сделкой с жителем: продавец идёт к покупателю, покупатель ждёт на месте."""
        s = self.state
        return bool(self.giving or s.get("give") or s.get("buy")
                    or (self.offer and self.offer.get("status") == "selling")
                    or (self.buying and self.buying.get("status") == "waiting"))

    def mail_busy(self):                                    # review: письмо в работе — relog его оборвёт
        return bool(self.mailing or self.taking)

    def body_elsewhere(self):                               # review: квест профессии или сон — не до сделок
        if ((self.state.get("job_change") or {}).get("running")):
            return "занят квестом профессии"
        r = getattr(self.mind, "routine", None)
        if r and getattr(r, "sleeping", False):
            return "ложусь спать"
        return None

    def sender_near(self, name, state=None):
        state = state or self.state
        if state.get("x") is None:
            return False
        return any(isinstance(p, dict) and p.get("name") == name and p.get("x") is not None
                   and dist(int(state["x"]), int(state["y"]), int(p["x"]), int(p["y"])) <= NEAR
                   for p in state.get("players") or [])

    def best_friend(self, min_affinity=None, hidden_only=False):
        """Житель с лучшим отношением (для подарка почтой и итога недели). hidden_only — только те,
        кого не видно рядом (видимого далеко проще дождаться, чем платить сбор почты)."""
        seen = {p.get("name") for p in self.state.get("players") or [] if isinstance(p, dict)}
        peers = sorted(p for p in self.mind.ctx.peers if not (hidden_only and p in seen))
        if not peers:
            return None
        best = max(peers, key=lambda p: (self.affinity(p), p))
        if min_affinity is not None and self.affinity(best) < min_affinity:
            return None
        return best

    def wishlist(self, state=None):
        """Что мне нужно: economy.market.wish, нехватка share (до keep), предметы этапа карьеры."""
        state = state or self.state
        want = {str(i): 1 for i in self.market.get("wish") or []}
        for item, rule in self.share.items():
            short = int(rule.get("keep", 0)) - self.have(item, state)
            if short > 0:
                want[item] = max(want.get(item, 0), short)
        career = getattr(self.mind, "career", None)
        data = career.load() if career else None
        if data:
            try:
                for m in progression.readiness(state, data)["missing"]:
                    if m.get("kind") == "item":
                        want[str(m["id"])] = max(want.get(str(m["id"]), 0), int(m["need"]) - int(m.get("have") or 0))
            except (KeyError, TypeError, ValueError):
                pass
        pets = getattr(self.mind, "pets", None)                     # pets: предмет приручения любимца, инкубатор
        for item, n in (pets.wants() if pets else {}).items():
            want[item] = max(want.get(item, 0), n)
        return want

    def for_sale(self, state=None):
        """Ценные лоты рюкзака, которые можно продать: не share, не из своего списка желаний."""
        state = state or self.state
        keep = set(self.share) | set(self.wishlist(state))
        return self.prices.valuables(state.get("items") or {}, keep=keep)

    def declined(self, item, now):
        ts = (self.mind.mem.get("market_declined") or {}).get(str(item), 0)
        return now - ts < self.market["decline_hours"] * 3600

    def save_market(self):
        self.mind.mem.set("econ_offer", self.offer)
        self.mind.mem.set("econ_buying", self.buying)
        self.mind.mem.set("econ_last_offer", self.last_offer)

    # ---------- продавец (ORG-033) ----------

    def pick_buyer(self, state):
        """Житель рядом; Merchant-ветка (Overcharge) — первым."""
        near = [p for p in state.get("players") or [] if isinstance(p, dict) and p.get("name") in self.mind.ctx.peers
                and self.sender_near(p["name"], state)]
        if not near:
            return None
        near.sort(key=lambda p: (0 if any(m in str(p.get("job") or "") for m in MERCHANTS) else 1, p["name"]))
        return near[0]["name"]

    async def market_tick(self, now, state):
        if self.busy_trade() or self.giving or self.mailing:
            return
        await self.maybe_week_mail(now, state)
        await self.maybe_shop(now, state)
        if now - self.last_offer < self.market["offer_gap_minutes"] * 60:
            return
        if self.trades_today(now) >= self.market["trades_per_day"]:
            return
        lots = [lot for lot in self.for_sale(state) if not self.declined(lot[0], now)]
        peer = self.pick_buyer(state) if lots else None
        if not peer:
            return
        await self.offer_lot(peer, *lots[0][:2])

    async def offer_lot(self, peer, item, amount):
        now = self.clock()
        price = self.prices.resident_price(item, amount, self.greed(), self.affinity(peer), self.overcharge())
        rid = secrets.token_hex(3)
        self.offer = {"id": rid, "peer": peer, "item": str(item), "amount": int(amount), "price": price,
                      "status": "offered", "since": now}
        self.last_offer = now
        self.save_market()
        tag = f"[offer:{rid}:{item}:{amount}:{price}]"
        text = f"{peer}, есть {self.prices.name(item)} x{amount} за {price}z. Возьмёшь?"
        await self.whisper(peer, f"{text[:77 - len(tag)]} {tag}", "рынок: предлагаю жителю до NPC")
        self.note("offer_made", f"Предложил {peer} {self.lot(self.offer)}.", 1, peer=peer, item=str(item),
                  amount=int(amount), price=price)

    def end_offer(self, text, kind, importance, **data):
        o, self.offer = self.offer, None
        self.save_market()
        self.note(kind, text, importance, peer=o["peer"], item=o["item"], amount=o["amount"], price=o["price"],
                  role="seller", **data)

    def on_sell_result(self, event):
        o = self.offer
        if not o:
            return
        paid = int(event.get("paid") or 0)
        if event.get("ok") and paid >= o["price"]:
            self.mind.mem.update_relation(o["peer"], 1, "честно сторговались")
            self.end_offer(f"Продал {o['peer']} {self.lot(o)} — сделка завершена сервером.", "trade_sold", 2, paid=paid)
        elif event.get("ok"):
            # предмет ушёл, оплата не вся: долг — только запись, без «вечных» обещаний
            debts = self.mind.mem.get("market_debts") or {}
            debts[o["peer"]] = int(debts.get(o["peer"], 0)) + o["price"] - paid
            self.mind.mem.set("market_debts", debts)
            self.mind.mem.update_relation(o["peer"], -1, "недоплатил за товар")
            self.end_offer(f"{o['peer']} заплатил {paid}z из {o['price']}z — записал долг.", "trade_debt", 3, paid=paid)
        else:
            self.end_offer(f"Продажа {o['peer']} не состоялась: {event.get('reason')}.", "trade_failed", 1)

    # ---------- покупатель ----------

    def offer_refuse_reason(self, sender, item, amount, price):
        now = self.clock()
        state = self.state
        if sender not in self.mind.ctx.peers:
            return "ты не житель"
        if not self.market["enabled"]:
            return "сейчас не торгую"
        if state.get("dead"):
            return "я мёртв"
        if self.mind.plans.store.active():
            return "иду на встречу"
        if self.busy_trade() or self.giving or self.mailing or state.get("give"):
            return "занят другой сделкой"
        if (why := self.body_elsewhere()):                  # review: квест профессии или сон
            return why
        if (state.get("vend") or {}).get("open"):
            return "стою с лавкой"
        if self.trades_today(now) >= self.market["trades_per_day"]:
            return "на сегодня хватит сделок"
        if not self.prices.item(item):
            return "не знаю цену"
        zeny = int(state.get("zeny") or 0)
        if zeny - price < self.market["keep_zeny"] or price > zeny * self.market["max_share"]:
            return "зени не хватит с запасом"
        if item in self.wishlist(state):
            limit = self.prices.buy_limit(item, amount, int((state.get("vend") or {}).get("discount") or 0))
            if price <= limit:
                return None
            return f"дороговато: дам не больше {limit}z"
        if self.prices.resale_ok(item, amount, price, self.overcharge(state)):
            return None
        return "мне не нужно, и перепродать невыгодно"

    async def on_offer_tag(self, sender, text):
        m = OFFER_TAG.search(text or "")
        rid, answer, item, amount, price = m.groups()
        if answer:
            o = self.offer
            if not o or o["id"] != rid or o["peer"] != sender or o["status"] != "offered":
                return
            if answer == "no":
                declined = self.mind.mem.get("market_declined") or {}
                declined[o["item"]] = self.clock()
                self.mind.mem.set("market_declined", declined)
                self.end_offer(f"{sender} не взял {self.lot(o)}.", "offer_refused", 1)
                return
            o.update(status="selling", since=self.clock())
            self.save_market()
            await self.mind.execute([{"action": "offer_sell", "to": sender, "item": int(o["item"]),
                                      "amount": o["amount"], "price": o["price"]}],
                                    source="economy", reason=f"рынок: продать {sender} {self.lot(o)}", protocol=True)
            return
        amount, price = int(amount), int(price)
        why = self.offer_refuse_reason(sender, item, amount, price)
        lot = {"item": item, "amount": amount, "price": price}
        if why:
            await self.whisper(sender, f"Нет, спасибо: {why}. [offer:{rid}:no]", "рынок: отказ")
            self.note("offer_declined", f"{sender} предлагал {self.lot(lot)}, отказал: {why}.", 1,
                      peer=sender, item=item, amount=amount, price=price)
            return
        await self.whisper(sender, f"Беру, подходи. [offer:{rid}:ok]", "рынок: беру")
        self.buying = {"id": rid, "peer": sender, "item": item, "amount": amount, "price": price,
                       "status": "waiting", "since": self.clock(), "base": self.have(item)}
        self.save_market()
        await self.mind.execute([{"action": "offer_buy", "from": sender, "item": int(item), "amount": amount,
                                  "price": price}],
                                source="economy", reason=f"рынок: купить у {sender} {self.lot(lot)}", protocol=True)

    def end_buying(self, text, kind, importance, **data):
        b, self.buying = self.buying, None
        self.save_market()
        self.note(kind, text, importance, peer=b["peer"], item=b["item"], amount=b["amount"], price=b["price"],
                  role="buyer", **data)

    def on_buy_result(self, event):
        b = self.buying
        if not b or event.get("from") != b["peer"]:
            return
        if event.get("ok"):
            b.update(status="verify", since=self.clock())  # сервер завершил; ждём рост предмета в рюкзаке
            self.save_market()
        else:
            self.end_buying(f"Покупка у {b['peer']} не состоялась: {event.get('reason')}.", "trade_failed", 1)

    async def check_market(self, now, state):
        o, b = self.offer, self.buying
        if o and o["status"] == "offered" and now - o["since"] >= ANSWER_TIMEOUT:
            self.end_offer(f"{o['peer']} не ответил на предложение.", "trade_failed", 1)
        elif o and o["status"] == "selling" and now - o["since"] >= SELL_TIMEOUT:
            self.end_offer(f"Нет итога продажи {o['peer']} за {SELL_TIMEOUT} с.", "trade_failed", 1)
        if b and b["status"] == "waiting" and now - b["since"] >= BUY_TIMEOUT:
            self.end_buying(f"{b['peer']} так и не пришёл с товаром.", "trade_failed", 1)
        elif b and b["status"] == "verify":
            got = self.have(b["item"], state) - int(b.get("base") or 0)
            if got >= b["amount"]:
                self.mind.mem.update_relation(b["peer"], 1, "честно сторговались")
                self.end_buying(f"Купил у {b['peer']} {self.lot(b)} — по данным игры.", "trade_bought", 2, got=got)
            elif now - b["since"] >= VERIFY_TIMEOUT:
                self.end_buying(f"Сервер завершил сделку с {b['peer']}, но предмета в рюкзаке не вижу.",
                                "trade_unverified", 2, got=got)

    # ---------- почта (ORG-035) ----------

    async def gift_by_mail(self, to, rid, item, amount):
        """Просящий не рядом — подарок письмом (сбор платит дарящий)."""
        tax = amount // 50 if item == "z" else MAIL_TAX_ITEM
        if self.have("z") - tax - (amount if item == "z" else 0) < int(self.zeny.get("keep", 0)) // 2:
            await self.whisper(to, f"Извини, почта мне сейчас не по карману. [need:{rid}:no]", "экономика: отказ")
            self.note("gift_refused", f"{to} просил издалека {self.label(item, amount)}: почта дорога.", 1,
                      peer=to, item=item, amount=amount)
            return
        await self.whisper(to, f"Пришлю почтой, проверь ящик. [need:{rid}:ok]", "экономика: отдаю почтой")
        await self.send_mail(to, "Подарок", f"Держи {self.label(item, amount)}. Береги себя!", "gift",
                             zeny=amount if item == "z" else 0, item=None if item == "z" else int(item),
                             amount=amount)

    async def send_mail(self, to, title, body, kind, zeny=0, item=None, amount=None):
        self.mailing = {"to": to, "kind": kind, "item": item, "amount": amount, "zeny": zeny, "since": self.clock()}
        a = {"action": "mail_send", "to": to, "title": title, "body": body, "zeny": zeny}
        if item is not None:
            a.update(item=item, amount=amount)
        await self.mind.execute([a], source="economy", reason=f"почта: {title} для {to}", protocol=True)

    def on_mail_result(self, event):
        m, self.mailing = self.mailing, None
        to = event.get("to") or (m or {}).get("to")
        kind = (m or {}).get("kind", "letter")
        if not event.get("ok"):
            self.note("mail_failed", f"Письмо {to} не отправлено: {event.get('reason')}.", 1, peer=to)
            return
        item = "z" if not event.get("item") else str(event.get("item"))
        amount = event.get("amount") if event.get("item") else event.get("zeny")
        self.note("mail_sent", f"Отправил письмо {to}: {event.get('title')} — сервер принял.", 1, peer=to,
                  zeny=int(event.get("zeny") or 0), item=event.get("item"), amount=event.get("amount"), via=kind)
        if kind == "gift":
            self.mind.mem.update_relation(str(to), 1, "помог ему издалека, почтой")
            self.note("gift_given", f"Отдал {to} {self.label(item, amount)} почтой.", 2, peer=to, item=item,
                      amount=amount, mail=True)

    async def on_mail_received(self, event):
        sender = event.get("from")
        if sender not in self.mind.ctx.peers:
            return
        try:
            mail_id = int(event.get("mail_id"))
        except (TypeError, ValueError):
            return
        if self.taking or self.busy_trade() or self.giving:  # review: не терять — забрать позже (tick)
            if event.get("attach") and all(int(e.get("mail_id") or 0) != mail_id for e in self.mail_pending):
                self.mail_pending = (self.mail_pending + [dict(event)])[-10:]
            return
        if not event.get("attach"):
            self.note("mail_got", f"Письмо от {sender}: {event.get('title')}.", 1, peer=sender, zeny=0)
            return
        self.taking = {"mail_id": mail_id, "from": sender, "since": self.clock()}
        await self.mind.execute([{"action": "mail_take", "mail_id": mail_id}], source="economy",
                                reason=f"почта: забрать письмо от {sender}", protocol=True)

    def on_mail_taken(self, event):
        self.taking = None
        sender = event.get("from")
        if not event.get("ok"):
            self.note("mail_failed", f"Не забрал письмо от {sender}: {event.get('reason')}.", 1, peer=sender)
            return
        zeny = int(event.get("zeny") or 0)
        self.note("mail_got", f"Забрал письмо от {sender}: {zeny}z, предметов {len(event.get('items') or [])}.", 1,
                  peer=sender, zeny=zeny, items=event.get("items") or [])
        r = self.req
        if r and r["status"] == "accepted" and r["peer"] == sender:
            r["mail_done"] = True                       # доказательство вместе с ростом запаса (check_request)
            self.save()

    async def maybe_week_mail(self, now, state):
        last = self.mind.mem.get("econ_week_mail")
        if last is None:
            self.mind.mem.set("econ_week_mail", now)    # первый запуск: отсчёт недели с сегодня
            return
        if now - last < 7 * 86400:
            return
        friend = self.best_friend(self.market["week_friend"])
        self.mind.mem.set("econ_week_mail", now)
        if not friend:
            return
        m = economy_metrics(self.mind.mem, now - 7 * 86400)
        body = (f"За неделю: сделок с жителями {m['сделок с жителями']}, оборот {m['оборот между жителями, z']}z, "
                f"подарков {m['подарков отдал']}/{m['подарков получил']}, у NPC выручил {m['продажи NPC, z']}z. "
                f"Зени сейчас {state.get('zeny')}. Спасибо, что рядом!")
        await self.send_mail(friend, "Итог недели", body, "week")

    # ---------- лавка (ORG-034) ----------

    async def maybe_shop(self, now, state):
        """Merchant с MC_VENDING и тележкой: товары лавки и цены из prices.json (раз в shop_hours)."""
        vend = state.get("vend") or {}
        if not vend.get("can") or vend.get("open"):
            return
        if now - (self.mind.mem.get("econ_shop_ts") or 0) < self.market["shop_hours"] * 3600:
            return
        goods = {str(k): int(v) for k, v in (vend.get("cart") or {}).items()}
        for item, n, _ in self.for_sale(state):
            goods[item] = goods.get(item, 0) + n
        items = self.prices.shop_items(goods, self.greed(), self.overcharge(state),
                                       max_items=int(vend.get("slots") or 3))
        self.mind.mem.set("econ_shop_ts", now)
        if not items:
            return
        title = f"{state.get('name') or 'Лавка'}: товар"
        await self.mind.execute([{"action": "offer_shop", "title": title,
                                  "items": [{k: it[k] for k in ("id", "price", "amount")} for it in items]}],
                                source="economy", reason="лавка: товары и цены из prices.json", protocol=True)
        self.note("shop_prepared", f"Подготовил лавку: {', '.join(it['name'] for it in items)}.", 1,
                  items=[it["id"] for it in items])


# ---------- метрики (ORG-037) ----------

def metrics_from_rows(rows, last_state=None):
    """rows — (kind, data) событий памяти; data — dict или JSON-строка. Для report и хроники."""
    m = {"зени": (last_state or {}).get("zeny"), "сделок с жителями": 0, "продал жителям, z": 0,
         "купил у жителей, z": 0, "подарков отдал": 0, "подарков получил": 0, "подарено зени": 0,
         "писем отправил": 0, "писем получил": 0, "продажи NPC, z": 0, "продажи из лавки, z": 0,
         "долги жителей, z": 0, "оборот между жителями, z": 0, "доля продаж жителям": None}
    for kind, data in rows:
        d = json.loads(data) if isinstance(data, str) else (data or {})
        if kind == "trade_sold":
            m["сделок с жителями"] += 1
            m["продал жителям, z"] += int(d.get("paid") or d.get("price") or 0)
        elif kind == "trade_bought":
            m["сделок с жителями"] += 1
            m["купил у жителей, z"] += int(d.get("price") or 0)
        elif kind == "trade_debt":
            m["долги жителей, z"] += int(d.get("price") or 0) - int(d.get("paid") or 0)
        elif kind in ("gift_given", "gift_received"):
            m["подарков отдал" if kind == "gift_given" else "подарков получил"] += 1
            if d.get("item") == "z":
                m["подарено зени"] += int(d.get("got") or d.get("amount") or 0)
        elif kind == "mail_sent":
            m["писем отправил"] += 1
        elif kind == "mail_got":
            m["писем получил"] += 1
        elif kind == "npc_sold":
            m["продажи NPC, z"] += int(d.get("zeny") or 0)
        elif kind == "vend_sold":
            m["продажи из лавки, z"] += int(d.get("zeny") or 0)
    m["оборот между жителями, z"] = m["продал жителям, z"] + m["купил у жителей, z"] + m["подарено зени"]
    total = m["продал жителям, z"] + m["продажи NPC, z"] + m["продажи из лавки, z"]
    m["доля продаж жителям"] = round(m["продал жителям, z"] / total, 2) if total else None
    return m


METRIC_KINDS = ("trade_sold", "trade_bought", "trade_debt", "gift_given", "gift_received", "mail_sent", "mail_got",
                "npc_sold", "vend_sold")


def economy_metrics(memory, since):
    """ORG-037: экономика жителя по фактам памяти с момента since (dict для report/хроники)."""
    rows = memory.db.execute(f"SELECT kind, data FROM events WHERE ts >= ? AND kind IN "
                             f"({', '.join('?' * len(METRIC_KINDS))})", (since, *METRIC_KINDS)).fetchall()
    return metrics_from_rows([(r[0], r[1]) for r in rows], memory.get("last_state") or {})


# Строки хроники для событий рынка и почты (chronicle.LINES можно дополнить этим словарём).
CHRONICLE_LINES = {
    "trade_sold": lambda d: f"продал {d.get('peer')} {d.get('amount')} × {d.get('item')} за {d.get('paid') or d.get('price')}z",
    "trade_bought": lambda d: f"купил у {d.get('peer')} {d.get('amount')} × {d.get('item')} за {d.get('price')}z",
    "trade_debt": lambda d: f"{d.get('peer')} недоплатил {int(d.get('price') or 0) - int(d.get('paid') or 0)}z",
    "mail_sent": lambda d: f"отправил письмо {d.get('peer')}" + (f" ({d.get('zeny')}z)" if d.get("zeny") else ""),
    "mail_got": lambda d: f"получил письмо от {d.get('peer')}" + (f" ({d.get('zeny')}z)" if d.get("zeny") else ""),
}
