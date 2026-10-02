"""Цены для экономики жителей (ORG-030). Чистые функции, без LLM и без сети.

Источник — brain/world/prices.json (scripts/gen_prices.py из базы rAthena):
    buy  — цена покупки у NPC без скидки, sell — сколько платит NPC без Overcharge.
Формулы скидки и наценки NPC — как в rAthena (src/map/pc.cpp pc_modifybuyvalue / pc_modifysellvalue):
    процент = 5 + 2 × уровень навыка (на 10-м уровне на 1 меньше): MC_DISCOUNT — покупка, MC_OVERCHARGE — продажа.
Правила live_ro (не из rAthena, настраиваются в goals.json economy.market):
    value         — «базовая» цена предмета для жителя: цена NPC продажи; у карт не меньше card_floor
                    (NPC платит за любую карту 10z, а между жителями карта ценна — точной цены в базе нет);
    цена жителя   = value × (1 + max_markup × greed) × (1 − скидка другу), не ниже цены NPC продажи
                    (дешевле, чем даёт NPC, житель не продаёт); greed — traits.greed 0..1 из personas;
    скидка другу  = friend_discount × affinity (отношение из памяти, только положительное), не больше max_discount.
"""
import json
from pathlib import Path

PRICES_PATH = Path(__file__).resolve().parents[1] / "world" / "prices.json"
DEFAULTS = {
    "max_markup": 0.5,          # жадность 1.0 — наценка 50 %
    "friend_discount": 0.03,    # за каждый пункт симпатии (affinity 0..10) — 3 %
    "max_discount": 0.25,
    "card_floor": 1000,         # карта между жителями не дешевле 1000z за штуку
    "valuable_lot": 500,        # лот дороже 500z (по value) — ценный: предложить жителю до NPC
    "resale_gain": 0.1,         # перекупщик берёт, если продаст NPC дороже хотя бы на 10 %
}
_CACHE = {}


def skill_rate(lv):
    """Процент MC_DISCOUNT/MC_OVERCHARGE уровня lv (rAthena pc.cpp)."""
    lv = int(lv or 0)
    if lv <= 0:
        return 0
    lv = min(lv, 10)
    return 5 + lv * 2 - (1 if lv == 10 else 0)


class Prices:
    def __init__(self, data, cfg=None):
        self.data = {str(k): v for k, v in data.items() if not str(k).startswith("_")}
        self.cfg = dict(DEFAULTS, **(cfg or {}))

    @classmethod
    def load(cls, path=None, cfg=None):
        path = Path(path or PRICES_PATH)
        if path not in _CACHE:
            _CACHE[path] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return cls(_CACHE[path], cfg)

    def item(self, item_id):
        return self.data.get(str(item_id))

    def name(self, item_id):
        it = self.item(item_id)
        return it["name"] if it else f"предмет {item_id}"

    def is_card(self, item_id):
        return (self.item(item_id) or {}).get("type") == "Card"

    # ---------- NPC ----------

    def npc_sell(self, item_id, count=1, overcharge_lv=0):
        """Сколько NPC заплатит за count штук (с Overcharge продавца). Неизвестный предмет — 0."""
        it = self.item(item_id)
        if not it:
            return 0
        unit = int(it["sell"] * (100 + skill_rate(overcharge_lv)) / 100)
        return unit * int(count)

    def npc_buy(self, item_id, count=1, discount_lv=0):
        """Сколько стоит купить у NPC count штук (со скидкой Discount). Не значит, что NPC его продаёт."""
        it = self.item(item_id)
        if not it:
            return 0
        unit = max(1, int(it["buy"] * (100 - skill_rate(discount_lv)) / 100)) if it["buy"] else 0
        return unit * int(count)

    # ---------- жители ----------

    def value(self, item_id):
        """Базовая цена штуки для жителей (правило live_ro): NPC продажа, у карт не меньше card_floor."""
        it = self.item(item_id)
        if not it:
            return 0
        v = int(it["sell"])
        if it.get("type") == "Card":
            v = max(v, int(self.cfg["card_floor"]))
        return v

    def markup(self, greed):
        g = max(0.0, min(1.0, float(greed or 0)))
        return self.cfg["max_markup"] * g

    def discount(self, affinity):
        a = max(0, int(affinity or 0))
        return min(self.cfg["max_discount"], self.cfg["friend_discount"] * a)

    def resident_price(self, item_id, count=1, greed=0.0, affinity=0, overcharge_lv=0):
        """Цена лота жителю: база × (1 + наценка характера) × (1 − скидка другу), не ниже цены NPC продажи."""
        base = self.value(item_id) * int(count)
        price = int(round(base * (1 + self.markup(greed)) * (1 - self.discount(affinity))))
        return max(price, self.npc_sell(item_id, count, overcharge_lv))

    def buy_limit(self, item_id, count=1, discount_lv=0):
        """Сколько разумно заплатить жителю за нужный предмет: value с максимальной наценкой, но не дороже
        покупки у NPC. Цена Buy ниже value (у карт Buy 20z) — номинальная: NPC такое не продаёт, её не учитываем."""
        count = int(count)
        cap = int(self.value(item_id) * (1 + self.cfg["max_markup"])) * count
        npc = self.npc_buy(item_id, count, discount_lv)
        return min(cap, npc) if npc >= self.value(item_id) * count else cap

    def resale_ok(self, item_id, count, price, overcharge_lv=0):
        """Перекупка: NPC заплатит мне (с моим Overcharge) больше цены хотя бы на resale_gain."""
        if price <= 0:
            return False
        return self.npc_sell(item_id, count, overcharge_lv) >= price * (1 + self.cfg["resale_gain"])

    # ---------- инвентарь ----------

    def inventory_value(self, items, overcharge_lv=0):
        """Оценка state.items {id: count}: сколько даст NPC и сколько стоит для жителей."""
        rows, unknown = [], []
        for iid, n in sorted((items or {}).items(), key=lambda kv: str(kv[0])):
            n = int(n or 0)
            if n <= 0:
                continue
            if not self.item(iid):
                unknown.append(str(iid))
                continue
            rows.append({"id": str(iid), "name": self.name(iid), "count": n,
                         "npc": self.npc_sell(iid, n, overcharge_lv), "value": self.value(iid) * n})
        return {"npc": sum(r["npc"] for r in rows), "value": sum(r["value"] for r in rows),
                "items": rows, "unknown": unknown}

    def valuables(self, items, keep=(), min_lot=None):
        """Ценные лоты рюкзака: value лота ≥ min_lot, кроме keep (нужное самому). Дорогие — первыми."""
        min_lot = self.cfg["valuable_lot"] if min_lot is None else min_lot
        keep = {str(k) for k in keep}
        out = []
        for iid, n in (items or {}).items():
            n = int(n or 0)
            if n <= 0 or str(iid) in keep or not self.item(iid):
                continue
            lot = self.value(iid) * n
            if lot >= min_lot:
                out.append((str(iid), n, lot))
        return sorted(out, key=lambda x: (-x[2], x[0]))

    # ---------- лавка (ORG-034) ----------

    def shop_items(self, cart, greed=0.0, overcharge_lv=0, max_items=None):
        """Товары лавки из тележки {id: count}: цена штуки — цена жителя, но не ниже NPC продажи с
        Overcharge + 1z (иначе выгоднее сдать NPC). Неизвестное базе не выставляется."""
        out = []
        for iid, n in sorted((cart or {}).items(), key=lambda kv: -self.value(kv[0]) * int(kv[1] or 0)):
            n = int(n or 0)
            if n <= 0 or not self.item(iid):
                continue
            unit = max(self.resident_price(iid, 1, greed), self.npc_sell(iid, 1, overcharge_lv) + 1)
            out.append({"id": int(iid), "name": self.name(iid), "price": min(unit, 1_000_000_000), "amount": min(n, 30000)})
            if max_items and len(out) >= max_items:
                break
        return out

    def shop_txt(self, title, items):
        """Текст shop.txt OpenKore (FileParsers::parseShopControl): первая строка — название,
        дальше «имя<TAB>цена<TAB>количество». Цены без запятых (их проверка не нужна).
        Имя — из базы rAthena: у снаряжения со слотами OpenKore показывает «Имя [N]» — для
        таких предметов надёжнее offer_shop (плагин ищет товар в тележке по ID)."""
        title = " ".join(str(title).replace("#", "").split())[:36] or "Shop"
        lines = [title]
        for it in items:
            name = str(it["name"]).replace("\t", " ")
            lines.append(f"{name}\t{int(it['price'])}\t{int(it['amount'])}")
        return "\n".join(lines) + "\n"
