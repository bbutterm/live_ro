"""Рыночный день (ORG-071, ТЗ Т-27). Правила без LLM, новых протоколов сделки нет.

Раз в неделю (день недели мира из cfg.weekdays, по умолчанию 6 — суббота календаря ORG-059, «рыночный день» в
brain/world/calendar.json) жители в городе сходятся к рыночной площади Пронтеры, выставляют лишнее и торгуют между
собой активнее, заказы публикуются чаще. Работают прежние механизмы — рынок ORG-033 ([offer:]), лавка ORG-034,
вывески ORG-026 и заказы ORG-070; модуль только меняет их пороги на один день и добавляет точку прогулки:

    economy.market.offer_gap_minutes × offer_gap_factor (0.5)    предлагать лот жителю чаще
    economy.market.trades_per_day + trades_bonus (4)             больше сделок за сутки
    economy.market.shop_hours -> shop_hours (2)                  лавка Merchant — чаще
    economy.prices.cfg.valuable_lot × valuable_factor (0.5)      выставить и менее ценное «лишнее»
    orders.cfg.post_gap_hours × orders_gap_factor (0.5)          заказы — чаще
    society.cfg.room_sign_chance -> sign_chance (0.9)            вывеска «Продаю/Куплю» вместо «Отдыхаю»
    social.cfg.point_weights.market -> point_weight (6)          прогулка — чаще на площадь (в обычный день 0)

Исходные значения снимаются при создании модуля и возвращаются в обычный день (модули создаются раньше: Economy,
Orders, Society, Social стоят выше в modules.MODULES).

Точка market (prontera 155,180, «к рыночной площади у фонтана»): клетки ±2 проходимы (db/re/map_cache.dat), до
ближайшего NPC ≥ 5 клеток — лавке и чат-комнате мешает NPC ближе min_npc_vendchat_distance 3
(conf/battle/player.conf:191), до варпов prt03/prt06 ≥ 20 клеток. Модуль кладёт её в копию social.cfg.points.
Сбор: в рыночный день, когда житель в режиме town дошёл до отдыха, один раз за день — social.walk_now() (точку
выбирает обычная прогулка по весам); запись market_day_open. Лавка: Merchant с vend.can, лавка закрыта, тело у
площади (≤ 3 клетки) — один раз за день сбросить kv econ_shop_ts, economy.maybe_shop в том же такте откроет её здесь.
Итог: первый такт после рыночного дня — market_day_summary {date, deals, sold, bought, vend, zeny} по событиям
памяти trade_sold / trade_bought / vend_sold за тот день (часовой пояс мира); летопись «рыночный день: N сделок».
Выключатель: BRAIN_DISABLE=market_day или goals.json "market_day": {"enabled": false}; без календаря — не создаётся.
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("market")

DEFAULTS = {
    "enabled": True,
    "weekdays": [6],
    "point": {"map": "prontera", "x": 155, "y": 180, "label": "к рыночной площади у фонтана"},
    "point_name": "market",
    "point_weight": 6,
    "offer_gap_factor": 0.5,
    "trades_bonus": 4,
    "shop_hours": 2,
    "valuable_factor": 0.5,
    "orders_gap_factor": 0.5,
    "sign_chance": 0.9,
    "shop_cells": 3,
}
DEAL_KINDS = ("trade_sold", "trade_bought", "vend_sold")


def dist(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


class MarketDay:
    # реестр модулей (modules.py, W8): создание и тик (до economy — пороги дня уже стоят, когда она торгует)
    ATTR, FEATURE, CONFIG, ENABLED = "market_day", "market_day", "market_day", True
    REQUIRES, ARGS = ("world", "calendar", "economy"), "world"
    TICK_ORDER = 15

    def __init__(self, mind, world=None, clock=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("market_day") or {}))
        self.tz_hours = (world or {}).get("timezone_offset_hours", 0)
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.st = mind.mem.get("market_day") or {}
        self.active = None                            # пороги какого дня стоят сейчас: True/False/None (не ставились)
        self.base = self.snapshot()
        self.install_point()

    # ---------- данные ----------

    def save(self):
        self.mind.mem.set("market_day", self.st)

    def snapshot(self):
        """Исходные пороги модулей — чтобы вернуть их в обычный день."""
        m = self.mind
        econ, orders, society = m.economy, getattr(m, "orders", None), getattr(m, "society", None)
        base = {"offer_gap_minutes": econ.market["offer_gap_minutes"], "trades_per_day": econ.market["trades_per_day"],
                "shop_hours": econ.market["shop_hours"], "valuable_lot": econ.prices.cfg["valuable_lot"]}
        if orders is not None:
            base["post_gap_hours"] = orders.cfg["post_gap_hours"]
        if society is not None:
            base["room_sign_chance"] = society.cfg["room_sign_chance"]
        return base

    def install_point(self):
        """Точка площади — в копию social.cfg.points (общий словарь мира не трогаем); вес в обычный день 0."""
        social = getattr(self.mind, "social", None)
        if social is None:
            return
        name = self.cfg["point_name"]
        social.cfg["points"] = dict(social.cfg.get("points") or {}, **{name: dict(self.cfg["point"])})
        social.cfg["point_weights"] = dict(social.cfg.get("point_weights") or {}, **{name: 0})

    def local(self, now):
        return datetime.fromtimestamp(now, timezone(timedelta(hours=self.tz_hours or 0)))

    def date(self, now):
        return self.local(now).strftime("%Y-%m-%d")

    def is_market(self, now=None):
        now = self.clock() if now is None else now
        cal = getattr(self.mind, "calendar", None)
        day = cal.day(now) if cal else None
        wd = day["weekday"] if isinstance(day, dict) else self.local(now).isoweekday()
        return wd in [int(d) for d in self.cfg["weekdays"]]

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "market_day", "event": kind, "text": text, **data})
        log.info("%s", text)

    # ---------- пороги дня ----------

    def apply(self, market):
        """Поставить пороги рыночного (market=True) или обычного дня."""
        if self.active is market:
            return
        self.active = market
        b, c, m = self.base, self.cfg, self.mind
        econ = m.economy
        econ.market["offer_gap_minutes"] = b["offer_gap_minutes"] * (c["offer_gap_factor"] if market else 1)
        econ.market["trades_per_day"] = b["trades_per_day"] + (int(c["trades_bonus"]) if market else 0)
        econ.market["shop_hours"] = min(b["shop_hours"], c["shop_hours"]) if market else b["shop_hours"]
        econ.prices.cfg["valuable_lot"] = b["valuable_lot"] * (c["valuable_factor"] if market else 1)
        orders = getattr(m, "orders", None)
        if isinstance(getattr(orders, "cfg", None), dict) and "post_gap_hours" in b:
            orders.cfg["post_gap_hours"] = b["post_gap_hours"] * (c["orders_gap_factor"] if market else 1)
        society = getattr(m, "society", None)
        if isinstance(getattr(society, "cfg", None), dict) and "room_sign_chance" in b:
            society.cfg["room_sign_chance"] = max(b["room_sign_chance"], c["sign_chance"]) if market \
                else b["room_sign_chance"]
        social = getattr(m, "social", None)
        if isinstance(getattr(social, "cfg", None), dict):
            social.cfg.setdefault("point_weights", {})[c["point_name"]] = c["point_weight"] if market else 0
        m.write_decision({"type": "market_day", "event": "thresholds", "market": market,
                          "offer_gap_minutes": econ.market["offer_gap_minutes"],
                          "trades_per_day": econ.market["trades_per_day"]})

    # ---------- такт ----------

    def ready(self):
        """Модули, чьи пороги меняются, — настоящие (в тестах порядка их подменяют шпионами или None)."""
        econ = getattr(self.mind, "economy", None)
        return (isinstance(getattr(econ, "market", None), dict)
                and isinstance(getattr(getattr(econ, "prices", None), "cfg", None), dict))

    async def tick(self):
        if not self.cfg.get("enabled", True) or not self.ready():
            return
        now = self.clock()
        market = self.is_market(now)
        self.apply(market)
        date = self.date(now)
        self.summarize(date, market, now)
        if not market or not self.mind.fresh_state:
            return
        state = self.mind.state
        if state.get("dead"):
            return
        r = getattr(self.mind, "routine", None)
        if not (r and r.in_town_mode and r.st.get("arrived")):
            return
        await self.gather(date, state)
        self.shop(date, state)

    async def gather(self, date, state):
        """Один раз за рыночный день — на площадь (прогулкой social, точку выбирают веса)."""
        if self.st.get("gathered") == date:
            return
        social = getattr(self.mind, "social", None)
        p = self.cfg["point"]
        if social is None or state.get("map") != p["map"]:
            return
        self.st["gathered"] = date
        self.save()
        social.walk_now()
        self.note("market_day_open", "Сегодня рыночный день — иду на площадь.", 1, date=date)

    def shop(self, date, state):
        """Merchant у площади: лавку открыть здесь (economy.maybe_shop в этом же такте)."""
        vend = state.get("vend") or {}
        if not vend.get("can") or vend.get("open") or self.st.get("shop") == date:
            return
        p = self.cfg["point"]
        if (state.get("map") != p["map"] or state.get("x") is None
                or dist(int(state["x"]), int(state["y"]), p["x"], p["y"]) > self.cfg["shop_cells"]):
            return
        self.st["shop"] = date
        self.save()
        self.mind.mem.set("econ_shop_ts", 0)
        self.mind.write_decision({"type": "market_day", "event": "shop_here", "date": date})

    # ---------- итог дня ----------

    def bounds(self, date):
        tz = timezone(timedelta(hours=self.tz_hours or 0))
        start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=tz).timestamp()
        return start, start + 86400

    def deals(self, start, end):
        """Сделки за [start, end) по фактам памяти: продажи и покупки жителям, продажи из лавки."""
        out = {"deals": 0, "sold": 0, "bought": 0, "vend": 0, "zeny": 0}
        for kind, data in self.mind.mem.db.execute(
                f"SELECT kind, data FROM events WHERE ts >= ? AND ts < ? AND kind IN ({', '.join('?' * len(DEAL_KINDS))})",
                (start, end, *DEAL_KINDS)):
            try:
                d = json.loads(data)
            except ValueError:
                d = {}
            if kind == "trade_sold":
                out["sold"] += 1
                out["deals"] += 1
                out["zeny"] += int(d.get("paid") or d.get("price") or 0)
            elif kind == "trade_bought":
                out["bought"] += 1
                out["deals"] += 1
                out["zeny"] += int(d.get("price") or 0)
            else:
                out["vend"] += int(d.get("zeny") or 0)
        return out

    def summarize(self, date, market, now):
        """День сменился, а прошлый был рыночным — итог по фактам памяти."""
        last = self.st.get("day")
        if last == date:
            return
        prev_market = self.st.get("day_market")
        self.st.update(day=date, day_market=market)
        self.save()
        if not last or not prev_market or self.st.get("summarized") == last:
            return
        self.st["summarized"] = last
        self.save()
        start, end = self.bounds(last)
        d = self.deals(start, end)
        text = (f"Рыночный день {last}: сделок с жителями {d['deals']} (продал {d['sold']}, купил {d['bought']})"
                + (f", из лавки {d['vend']}z" if d["vend"] else "") + ".")
        self.note("market_day_summary", text, 2, date=last, **d)


# Строки летописи (chronicle.LINES дополняется этим словарём).
CHRONICLE_LINES = {
    "market_day_open": lambda d: "рыночный день: пошёл(пошла) на площадь",
    "market_day_summary": lambda d: (f"рыночный день: {d.get('deals', 0)} сделок (продал {d.get('sold', 0)}, "
                                     f"купил {d.get('bought', 0)}"
                                     + (f", из лавки {d.get('vend')}z" if d.get("vend") else "") + ")"),
}
