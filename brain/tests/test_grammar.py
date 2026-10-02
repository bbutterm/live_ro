"""Грамматика реплик (ORG-065, ТЗ Т-24): шаблоны с альтернативами, род, карты, словечки, повторы < 20 %.

Запуск: cd brain && python3 -m unittest -v tests.test_grammar
"""
import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import grammar as g
from live_brain.__main__ import organic_metrics, repeat_share
from live_brain.memory import Memory
from live_brain.safety import MAX_TEXT

from tests.test_topics import BRAIN_DIR, LONGEST, PERSONAS, WORLD, Clock, at_hour, make_mind

SPEECH = json.loads((BRAIN_DIR / "world" / "speech.json").read_text(encoding="utf-8"))
# смесь тем за день у общительного жителя: приветствия и прощания чаще всего
DAY_MIX = [("hello", 50), ("bye", 40), ("weather_rain", 10), ("weather_clear", 10), ("hunt", 20), ("loot", 15),
           ("tired", 10), ("congrats", 10), ("condolence", 5), ("thanks", 10), ("level", 5), ("death", 5),
           ("trip", 10)]
FACT_SLOTS = set(LONGEST) | {"trip_map"}


class SyntaxTest(unittest.TestCase):
    def test_alternatives(self):
        rng = random.Random(1)
        got = {g.expand("{Привет|Здорово|Ну привет}, {name}!", rng) for _ in range(60)}
        self.assertEqual(got, {"Привет, {name}!", "Здорово, {name}!", "Ну привет, {name}!"})
        got = {g.expand("{Ты как, {name}?|Держись, {name}.}", rng) for _ in range(40)}
        self.assertEqual(got, {"Ты как, {name}?", "Держись, {name}."})        # слот внутри альтернативы
        got = {g.expand("{Ну, |}{до встречи|пока}", rng) for _ in range(80)}
        self.assertEqual(got, {"Ну, до встречи", "Ну, пока", "до встречи", "пока"})   # пустая — по желанию
        self.assertEqual(g.expand("{a|{b|cc}}", pick=lambda o: o[-1]), "cc")         # вложенные
        self.assertEqual(g.longest("{a|bbb}-{cc|d}"), "bbb-cc")
        self.assertEqual(g.variants("{a|b}{c|d|e}"), 6)
        self.assertEqual(g.fields("{Привет|Здорово}, {name}. {Побед|Добычи}: {kills}"), {"name", "kills"})

    def test_gender_paren(self):
        cases = {"Был(а) на поле": ("Был на поле", "Была на поле"),
                 "Дошёл(дошла) до места": ("Дошёл до места", "Дошла до места"),
                 "появился(лась)": ("появился", "появилась"),
                 "нашёл(шла)": ("нашёл", "нашла"),
                 "завёл(а) питомца": ("завёл питомца", "завела питомца"),
                 "Слёг(слегла)": ("Слёг", "Слегла"),
                 "дорос(ла)": ("дорос", "доросла"),
                 "Буду должен(должна)": ("Буду должен", "Буду должна")}
        for src, (m, f) in cases.items():
            self.assertEqual(g.gendered(src, "m"), m)
            self.assertEqual(g.gendered(src, "f"), f)
            self.assertEqual(g.gendered(src, None), src)                         # пол неизвестен — как было

    def test_gender_explicit_and_peer(self):
        self.assertEqual(g.gendered("<рад/рада>, не устал<@/а>?", "f", "m"), "рада, не устал?")
        self.assertEqual(g.gendered("<рад/рада>, не устал<@/а>?", "m", "f"), "рад, не устала?")
        self.assertEqual(g.gendered("<рад/рада>, не устал<@/а>?", None, None), "рад(а), не устал(а)?")
        self.assertEqual(g.sex_of("Female"), "f")
        self.assertEqual(g.sex_of("Male"), "m")
        self.assertIsNone(g.sex_of(None))

    def test_adjectives(self):
        table = {("Южный", "n", "loc"): "Южном", ("Южный", "f", "acc"): "Южную", ("Южный", "m", "gen"): "Южного",
                 ("Дальний", "n", "nom"): "Дальнее", ("Дальний", "f", "nom"): "Дальняя",
                 ("Дальний", "f", "acc"): "Дальнюю", ("Дальний", "m", "loc"): "Дальнем",
                 ("Тихий", "n", "nom"): "Тихое", ("Тихий", "m", "gen"): "Тихого",
                 ("Затонувший", "m", "loc"): "Затонувшем", ("Затонувший", "f", "gen"): "Затонувшей",
                 ("Часовой", "f", "loc"): "Часовой", ("Часовой", "m", "nom"): "Часовой",
                 ("Гиблый", "m", "nom"): "Гиблый", ("Гиблый", "n", "nom"): "Гиблое",
                 ("Северо-западный", "n", "loc"): "Северо-западном"}
        for (word, gender, case), want in table.items():
            self.assertEqual(g.adjective(word, gender, case), want, (word, gender, case))
        with self.assertRaises(ValueError):
            g.adjective("поле", "n", "nom")

    def test_place_forms(self):
        f = g.place_forms("лес", ["Южный"], "Пайона")
        self.assertEqual(f, {"nom": "Южный лес Пайона", "gen": "Южного леса Пайона", "loc": "в Южном лесу Пайона",
                             "acc": "Южный лес Пайона", "from": "из Южного леса Пайона"})
        f = g.place_forms("пустыня", ["Дальний", "Северный"])
        self.assertEqual((f["nom"], f["loc"], f["acc"]),
                         ("Дальняя северная пустыня", "в Дальней северной пустыне", "Дальнюю северную пустыню"))


class MapsTest(unittest.TestCase):
    NAMES = {"prt_fild08": [g.place_forms("поле", ["Южный"], "Пронтеры"), g.place_forms("поле", ["Южный"])],
             "pay_fild01": [g.place_forms("лес", ["Восточный"], "Пайона")]}

    def resolver(self, code):
        return self.NAMES.get(code)

    def test_no_names_construction(self):
        """Имени нет — склонять нечего: «на карте X», «до карты X», «с карты X»."""
        self.assertEqual(g.maps_in_text("Помер на prt_fild08. До pay_fild01 далеко."),
                         "Помер на карте prt_fild08. До карты pay_fild01 далеко.")
        self.assertEqual(g.maps_in_text("Вышли из pay_dun00, были в prontera"),
                         "Вышли с карты pay_dun00, были на карте prontera")
        self.assertEqual(g.maps_in_text("Помнишь prt_fild07?"), "Помнишь prt_fild07?")
        self.assertEqual(g.maps_in_text("Слух [info:rich:prt_fild08]"), "Слух [info:rich:prt_fild08]")   # метки

    def test_names_with_cases(self):
        r = self.resolver
        self.assertEqual(g.maps_in_text("На prt_fild08 опасно", r), "На Южном поле Пронтеры опасно")
        self.assertEqual(g.maps_in_text("Охочусь в pay_fild01", r), "Охочусь в Восточном лесу Пайона")
        self.assertEqual(g.maps_in_text("Дошёл до prt_fild08", r), "Дошёл до Южного поля Пронтеры")
        self.assertEqual(g.maps_in_text("Вернулся с pay_fild01", r), "Вернулся из Восточного леса Пайона")
        self.assertEqual(g.maps_in_text("Помнишь prt_fild08?", r), "Помнишь Южное поле Пронтеры?")
        self.assertEqual(g.maps_in_text("исключил карту prt_fild08", r), "исключил карту «Южное поле Пронтеры»")
        self.assertEqual(g.maps_in_text("Vera: prt_fild08", r), "Vera: Южное поле Пронтеры")
        self.assertEqual(g.maps_in_text("На prt_fild08 опасно", r, level=1), "На Южном поле опасно")   # короче
        self.assertEqual(g.maps_in_text("На prt_fild08 опасно", r, level=2), "На карте prt_fild08 опасно")


class LayerTest(unittest.TestCase):
    def make(self, persona=None, sex="m", **cfg):
        return g.Grammar(SPEECH, persona or {"traits": {"whimsy": 0.5}}, random.Random(5), cfg,
                         sex=lambda: sex, peer_sex=lambda name: "f" if name == "Vera" else None)

    def test_tic_lowercase_but_not_names(self):
        gr = self.make({"speech_tics": {"open": ["Ну,"], "tail": []}}, tic_chance=10)
        self.assertEqual(gr.decorate("Погода ничего.", 60), "Ну, погода ничего.")
        self.assertEqual(gr.decorate("Vera, привет.", 60), "Ну, Vera, привет.")
        gr.proper.add("Пронтера")
        self.assertEqual(gr.decorate("Пронтера шумит.", 60), "Ну, Пронтера шумит.")
        self.assertEqual(gr.decorate("О, Vera!", 60), "О, Vera!")                  # уже с междометием
        self.assertEqual(gr.decorate("x" * 59, 60), "x" * 59)                        # не влезло — без словечка

    def test_persona_tics_by_traits(self):
        gr = self.make({"traits": {"whimsy": 0.9, "patience": 0.1}})
        self.assertIn("Эх,", gr.tics["open"])
        self.assertNotIn("Хм.", gr.tics["open"])

    def test_say_uses_persona_and_templates(self):
        gr = self.make(sex="f", mix=0.5)
        facts = {"name": "Vera", "kills": 12, "map": "prt_fild08"}
        persona = "Побед за день: {kills}. Был(а) на {map}."
        got = {gr.say("hunt", persona, facts) for _ in range(80)}
        self.assertTrue(any("Была на карте prt_fild08" in t for t in got))          # фраза персоны: род и карта
        self.assertTrue(any("Была" not in t for t in got))                           # и шаблоны speech.json
        self.assertTrue(all(len(t) <= 60 for t in got))
        self.assertGreater(len(got), 15)

    def test_unknown_key_only_persona(self):
        gr = self.make(tic_chance=0, tail_chance=0)
        self.assertEqual(gr.say("pet_re", "Береги его, {name}.", {"name": "Vera"}), "Береги его, Vera.")

    def test_no_repeat_of_recent(self):
        gr = self.make(tic_chance=0, tail_chance=0, mix=1.0, tries=30)
        facts = {"name": "Vera"}
        recent = []
        for _ in range(30):
            recent.append(gr.say("hello", None, facts, recent))
        self.assertEqual(len(set(recent)), 30)

    def test_peer_gender(self):
        gr = self.make(mix=1.0, tic_chance=0)
        gr.topics = {"hello": ["Не устал<@/а>, {name}?"]}
        self.assertEqual(gr.say("hello", None, {"name": "Vera"}), "Не устала, Vera?")
        self.assertEqual(gr.say("hello", None, {"name": "Bors"}), "Не устал(а), Bors?")


class SpeechFileTest(unittest.TestCase):
    def test_templates_fit_and_use_known_facts(self):
        for key, pool in SPEECH["topics"].items():
            self.assertGreaterEqual(len(pool), 1, key)
            for t in pool:
                self.assertLessEqual(g.fields(t), FACT_SLOTS, f"{key}: {t}")
                subs = dict(LONGEST, trip_map=LONGEST["map"])
                for sex in ("m", "f", None):
                    text = g.maps_in_text(g.gendered(g.longest(t), sex, None).format(**subs))
                    self.assertLessEqual(len(text), 60, f"{key}: {text}")
                    self.assertLessEqual(len(f"{text} [chat:{key.split('_')[0][:12]}:4]"), MAX_TEXT)
        self.assertNotIn("weather", SPEECH["topics"])        # погода — только по факту (W3), ключи weather_*

    def test_many_variants(self):
        for key in ("hello", "bye", "hunt", "loot"):
            self.assertGreaterEqual(sum(g.variants(t) for t in SPEECH["topics"][key]), 40, key)

    def test_persona_tics_fit(self):
        for path in PERSONAS:
            p = json.loads(path.read_text(encoding="utf-8"))
            for part in ("open", "tail"):
                for tic in (p.get("speech_tics") or {}).get(part) or []:
                    self.assertLessEqual(len(tic), 24, f"{path.name}: {tic}")


class DayTest(unittest.TestCase):
    """200 реплик дня через social.phrase: дословных повторов < 20 % (без грамматики — около 35 %)."""

    def day(self, bot, name, grammar=True, seed=7):
        with tempfile.TemporaryDirectory() as d:
            m = make_mind(Path(d), bot, name, {"Arkady", "Vera", "Ilsa"}, Clock(at_hour(14)), [])
            s = m.social
            s.grammar = s.make_grammar(WORLD) if grammar else None
            m.mem.set("known_players", {p: {"sex": "Male"} for p in ("Arkady", "Bors")})   # Ilsa, Mila — пол неизвестен
            peers = [p for p in ("Arkady", "Vera", "Ilsa", "Bors", "Mila") if p != name][:4]
            rng = random.Random(seed)
            seq = [k for k, n in DAY_MIX for _ in range(n)]
            rng.shuffle(seq)
            out = []
            for i, key in enumerate(seq):
                facts = {"name": peers[i % 4], "me": name, "lv": 31, "kills": 120 + i // 40, "loot": 14 + i // 50,
                         "hours": 3, "death_map": "prt_fild07", "map": "prt_fild08", "trip_map": "pay_fild01",
                         "weather": "дождь", "amount": 120, "peer_lv": 30}
                text = s.phrase(key, facts)
                self.assertIsNotNone(text, key)
                self.assertLessEqual(len(text), 80)
                out.append(text)
            m.mem.close()
            return out

    def test_repeats_below_20_percent(self):
        for bot, name in (("bot01", "Arkady"), ("bot02", "Vera")):
            for seed in (1, 7):
                out = self.day(bot, name, seed=seed)
                self.assertEqual(len(out), 200)
                self.assertLess(repeat_share(out), 20, f"{name}: {repeat_share(out)} %")
            self.assertGreater(repeat_share(self.day(bot, name, grammar=False)), 20)   # было — выше цели

    def test_gender_from_persona(self):
        """Род говорящего — из persona.sex (в игре — state.sex); собеседника — из known_players."""
        vera = self.day("bot02", "Vera")
        self.assertFalse([t for t in vera if "(ла" in t or "Был(а)" in t or "Рад(а)" in t])
        self.assertFalse([t for t in vera if "(а)" in t and ("Arkady" in t or "Bors" in t)])
        self.assertTrue(any("Устала" in t or "Охотилась" in t or "Была" in t for t in vera))

    def test_disabled_is_old_behavior(self):
        with tempfile.TemporaryDirectory() as d:
            world = dict(WORLD, grammar={"enabled": False})
            m = make_mind(Path(d), "bot01", "Arkady", {"Arkady", "Vera"}, Clock(at_hour(14)), [])
            self.assertIsNone(m.social.make_grammar(world))
            m.mem.close()


class MetricTest(unittest.TestCase):
    def test_organic_metric(self):
        with tempfile.TemporaryDirectory() as d:
            mem = Memory(Path(d) / "m.sqlite")
            now = time.time()
            for text in ("Привет", "Пока", "Привет", "Как дела?"):
                mem.add_event("social_said", {"peer": "Vera", "topic": "hello", "fact": False, "text": text})
            mem.add_event("social_said", {"peer": "Vera", "topic": "cold", "fact": False})   # старое — без текста
            m = organic_metrics(mem, now - 3600, now=now + 1)
            self.assertEqual(m["повторов реплик, %"], 25)
            self.assertEqual(m["реплик без LLM"], 5)
            mem.close()
        self.assertEqual(repeat_share([]), 0)


if __name__ == "__main__":
    unittest.main()
