"""Цены жителей (prices.py) и генератор prices.json (scripts/gen_prices.py). Без сети и процессов.

Запуск: cd brain && python3 -m unittest -v tests.test_prices
"""
import importlib.util
import json
import os
import unittest
from pathlib import Path

from live_brain.prices import PRICES_PATH, Prices, skill_rate

ROOT = Path(__file__).resolve().parents[2]
RATHENA = Path(os.environ.get("LIVE_RO_RATHENA") or ROOT / "upstream" / "rathena")   # клон без сабмодулей — путь из окружения


def load_gen():
    spec = importlib.util.spec_from_file_location("gen_prices", ROOT / "scripts" / "gen_prices.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PricesJsonTest(unittest.TestCase):
    def test_file(self):
        raw = PRICES_PATH.read_text(encoding="utf-8")
        self.assertLessEqual(len(raw.encode("utf-8")), 1_500_000)
        data = json.loads(raw)
        ids = [int(k) for k in data if not k.startswith("_")]
        self.assertEqual(ids, sorted(ids), "ключи по возрастанию — детерминированно")
        self.assertEqual(data["501"], {"name": "Red Potion", "buy": 10, "sell": 5, "weight": 7, "type": "Healing"})
        self.assertEqual(data["4001"]["type"], "Card")
        self.assertEqual(data["984"]["sell"], 550, "Oridecon: Buy 1100 -> Sell = Buy/2")
        for k, v in data.items():
            if not k.startswith("_"):
                self.assertNotEqual(v["type"], "Cash")
                self.assertTrue(v["buy"] or v["sell"], f"{k} без цены не попадает в файл")

    def test_generator_is_deterministic(self):
        if not (RATHENA / "db" / "re" / "item_db_etc.yml").exists():
            self.skipTest("нет сабмодуля upstream/rathena")
        gen = load_gen()
        a = gen.dump(gen.build(RATHENA))
        self.assertEqual(a, gen.dump(gen.build(RATHENA)))
        self.assertEqual(a, PRICES_PATH.read_text(encoding="utf-8"), "prices.json совпадает с генератором")

    def test_generator_rules(self):
        gen = load_gen()
        self.assertEqual(gen.entry({"name": "X", "type": "Etc", "buy": 0, "sell": 30, "weight": 15})["buy"], 60)
        self.assertEqual(gen.entry({"name": "X", "type": "Etc", "buy": 101, "sell": None, "weight": 10})["sell"], 50)
        base = {"id": 1, "name": "X", "type": "Armor", "buy": 10, "sell": None, "weight": 1, "equip_lv": 1,
                "costume": False}
        self.assertTrue(gen.wanted(base))
        self.assertFalse(gen.wanted(dict(base, costume=True)), "костюмы не нужны")
        self.assertFalse(gen.wanted(dict(base, equip_lv=150)))
        self.assertFalse(gen.wanted(dict(base, type="Cash")))
        self.assertFalse(gen.wanted(dict(base, buy=0)), "без цены — ценность неизвестна")
        self.assertFalse(gen.wanted(dict(base, id=50000)))


class PricesTest(unittest.TestCase):
    def setUp(self):
        self.p = Prices.load()

    def test_skill_rate_like_rathena(self):
        self.assertEqual([skill_rate(i) for i in (0, 1, 5, 9, 10)], [0, 7, 15, 23, 24])

    def test_npc(self):
        self.assertEqual(self.p.npc_sell(501, 10), 50)
        self.assertEqual(self.p.npc_sell(984, 1, overcharge_lv=10), int(550 * 124 / 100))
        self.assertEqual(self.p.npc_buy(501, 10, discount_lv=10), 70, "10 × int(10 × 76 / 100)")
        self.assertEqual(self.p.npc_sell(99999999), 0, "неизвестный предмет — 0")

    def test_value_and_resident_price(self):
        self.assertEqual(self.p.value(4001), 1000, "карта — не ниже card_floor")
        self.assertEqual(self.p.value(984), 550)
        self.assertEqual(self.p.resident_price(984, 2, greed=0), 1100)
        self.assertEqual(self.p.resident_price(984, 2, greed=1.0), 1650, "жадность 1 — +50 %")
        self.assertEqual(self.p.resident_price(984, 2, greed=1.0, affinity=5), int(round(1650 * 0.85)))
        self.assertEqual(self.p.resident_price(984, 2, greed=0, affinity=10), 1100,
                         "со скидкой другу не дешевле, чем заплатит NPC")
        self.assertEqual(self.p.resident_price(984, 1, greed=0, affinity=-5), 550, "неприязнь скидку не даёт")

    def test_buy_limit_and_resale(self):
        self.assertEqual(self.p.buy_limit(4001), 1500, "номинальный Buy карты (20z) не ограничивает")
        self.assertEqual(self.p.buy_limit(501, 10), 70, "зелье: value 5 × 1.5 = 7 за штуку, дешевле NPC (10)")
        self.assertTrue(self.p.resale_ok(984, 1, 450, overcharge_lv=10))
        self.assertFalse(self.p.resale_ok(984, 1, 650, overcharge_lv=10), "682 < 650 × 1.1")
        self.assertFalse(self.p.resale_ok(984, 1, 0))

    def test_inventory(self):
        inv = self.p.inventory_value({"501": 10, "984": 2, "4001": 1, "99999999": 3, "602": 0})
        self.assertEqual(inv["npc"], 50 + 1100 + 10)
        self.assertEqual(inv["value"], 50 + 1100 + 1000)
        self.assertEqual(inv["unknown"], ["99999999"])
        lots = self.p.valuables({"501": 40, "984": 2, "4001": 1, "909": 30})
        self.assertEqual([x[0] for x in lots], ["984", "4001"], "дорогие первыми, зелья и Jellopy — не ценность")
        self.assertEqual(self.p.valuables({"984": 2}, keep={"984"}), [], "нужное самому не продаю")

    def test_shop(self):
        items = self.p.shop_items({"4001": 1, "984": 3, "501": 5, "99999999": 1}, greed=0.5, overcharge_lv=5,
                                  max_items=2)
        self.assertEqual([i["id"] for i in items], [984, 4001])
        for it in items:
            self.assertGreater(it["price"], self.p.npc_sell(it["id"], 1, 5), "дешевле NPC не выставляю")
        text = self.p.shop_txt("Лавка #1\tArkady", items)
        lines = text.splitlines()
        self.assertEqual(lines[0], "Лавка 1 Arkady", "без '#' и табуляций в названии")
        name, price, amount = lines[1].split("\t")
        self.assertEqual((name, amount), ("Oridecon", "3"))
        self.assertRegex(price, r"^\d+$", "цена без запятых — parseShopControl примет")


if __name__ == "__main__":
    unittest.main()
