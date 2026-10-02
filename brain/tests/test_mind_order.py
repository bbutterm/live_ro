"""Порядок обработки в Mind (W8): кто и в каком порядке получает событие, метку шёпота, тик и поле промпта.

Тест фиксирует поведение mind.py ДО перевода модулей на реестр (live_brain/modules.py) и проверяет,
что после рефакторинга порядок вызовов и «поглощение» событий (return) не изменились.
Модули заменяются шпионами (Spy): каждый вызов метода пишется в журнал «атрибут.метод»,
вызов gate — «gate» (событие дошло до правил/LLM, т. е. никто его не поглотил).

Если порядок меняется НАМЕРЕННО (новый модуль, новая подписка) — обновите таблицу:
  cd brain && python3 -m tests.test_mind_order dump   # печатает текущие таблицы для вставки ниже

RegistryTest — новый модуль подключается к реестру без правки mind.py (метка, событие, тик, промпт, выключатели).

Запуск: cd brain && python3 -m unittest -v tests.test_mind_order
"""
import asyncio
import json
import pprint
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from live_brain import world_bus
from live_brain.config import Settings
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
PEERS = {"Arkady", "Vera"}

CORE = ("plans", "life", "postmortem", "maps", "needs", "rumors")
OPTIONAL = ("home", "mood", "calendar", "career", "routine", "economy", "party", "activities", "bonds", "crew",
            "pets", "social", "society", "aims", "guild", "explorer", "strangers", "world", "rivalry", "crowd",
            "episodes", "tradition")
OPTIONAL += ("gossip",)                       # gossip: ORG-056
OPTIONAL += ("habits",)                       # habits: ORG-068


class Ret(str):
    """Ответ шпиона: строка (годится в промпт и JSON) и awaitable (годится для await)."""

    def __await__(self):
        return iter(())


class Spy:
    def __init__(self, _spy_name, _spy_log, _spy_returns=None, **attrs):
        self.__dict__.update(attrs, _name=_spy_name, _log=_spy_log, _returns=_spy_returns or {})

    def __getattr__(self, meth):
        if meth.startswith("__"):
            raise AttributeError(meth)

        def call(*args, **kwargs):
            self._log.append(f"{self._name}.{meth}")
            r = self._returns.get(meth)
            return r if r is not None else Ret(f"{self._name}.{meth}")
        return call


class FakeGate:
    def __init__(self, log):
        self.log = log

    def evaluate(self, event, state, ctx):
        self.log.append("gate")
        return SimpleNamespace(memory=[], note="тест", actions=[], llm=None, llm_kind="event",
                               stranger=event.get("kind") == "chat_private" and event.get("from") == "Stranger")


def V(text):
    return {"kind": "chat_private", "from": "Vera", "text": text}


EVENTS = {
    "world_msg": {"kind": "world_msg", "text": "сервер"},
    "attack": {"kind": "attack", "monster": "Poring"},
    "kill": {"kind": "kill", "monster": "Poring", "map": "prt_fild08"},
    "died": {"kind": "died", "map": "prt_fild08"},
    "escape": {"kind": "escape"},
    "survival": {"kind": "survival"},
    "danger": {"kind": "danger"},
    "support": {"kind": "support", "from": "Vera"},
    "level_up": {"kind": "level_up", "lv": 31},
    "heal_confirmed": {"kind": "heal_confirmed"},
    "loot": {"kind": "loot"},
    "chat_public": {"kind": "chat_public", "from": "Vera", "text": "привет"},
    "chat_private plain": V("привет"),
    "chat_private stranger": {"kind": "chat_private", "from": "Stranger", "text": "[need:abcd:ok]"},
    "tag meet": V("[meet:abcd:ok]"),
    "tag need": V("[need:abcd:ok]"),
    "tag offer": V("[offer:abcd:ok]"),
    "tag info": V("[info:danger:prt_fild08]"),
    "tag gossip": V("[gossip:kind:Arkady:0:Vera:heal]"),     # gossip: ORG-056
    "tag explore": V("[explore:trip:prt_fild08]"),
    "tag crew": V("[crew:pref:prt_fild08:5]"),
    "tag guild": V("[guild:ask:Vera]"),
    "tag party": V("[party:hunt:prt_fild08]"),
    "tag party dead": V("[party:dead:prt_fild08]"),
    "tag chat": V("[chat:hello:1]"),
    "tags crew+party": V("[crew:pref:prt_fild08:5] [party:hunt:x]"),
    "tags party+chat": V("[party:dead:x] [chat:hello:1]"),
    "tags chat+meet": V("[chat:hello:1] [meet:abcd:ok]"),
    "guild_create_result": {"kind": "guild_create_result"},
    "guild_invite_result": {"kind": "guild_invite_result"},
    "guild_invite": {"kind": "guild_invite"},
    "guild_joined_auto": {"kind": "guild_joined_auto"},
    "chat_guild": {"kind": "chat_guild", "from": "Vera", "text": "привет"},
    "pet_tame_result": {"kind": "pet_tame_result"},
    "pet_hatched": {"kind": "pet_hatched"},
    "pet_fed": {"kind": "pet_fed"},
    "job_change_result home": {"kind": "job_change_result", "path": "home"},
    "job_change_result job": {"kind": "job_change_result", "path": "job"},
    "deal_complete": {"kind": "deal_complete"},
    "give_result": {"kind": "give_result"},
    "buy_result": {"kind": "buy_result"},
    "mail_result": {"kind": "mail_result"},
    "mail_taken": {"kind": "mail_taken"},
    "mail_received": {"kind": "mail_received"},
    "npc_sold": {"kind": "npc_sold"},
    "vend_sold": {"kind": "vend_sold"},
}

# Варианты: какие необязательные модули выключены (None). "все" — все включены (шпионы).
VARIANTS = {
    "все": (),
    "без необязательных": OPTIONAL,
    "без party": ("party",),
    "без economy": ("economy",),
    "без social": ("social",),
    "без crew": ("crew",),
    "без explorer": ("explorer",),
    "без guild": ("guild",),
    "без strangers": ("strangers",),
    "без routine и home": ("routine", "home"),
}


class Lab:
    """Настоящий Mind (мир goals.json, два жителя) — затем модули подменяются шпионами."""

    def __init__(self, env=None, world=WORLD, peers=PEERS, world_bus_db=None):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.mem = Memory(root / "m.sqlite")

        async def send(a):
            return 1
        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        self.log = []
        self.mind = Mind(Settings.from_env(env or {}), persona, self.mem, send, root / "d.jsonl", FakeGate(self.log),
                         peers=set(peers), world=world, world_bus_db=world_bus_db)

    def spy(self, off=()):
        m, log = self.mind, self.log
        returns = {"postmortem": {"bans": {}}, "routine": {"summary": {"режим": "охота"}},
                   "needs": {"top": [("social", 0.5)]}}
        attrs = {"routine": {"in_town_mode": False, "cfg": {}, "st": {"mode": "hunt"}},
                 "party": {"name": "LR_Vera", "leader": "Vera", "st": {"confirmed": True}}}
        for name in CORE + OPTIONAL:
            setattr(m, name, None if name in off else Spy(name, log, returns.get(name), **attrs.get(name, {})))
        return self

    def close(self):
        self.mem.close()
        self.tmp.cleanup()


def trace_events(off):
    out = {}
    lab = Lab().spy(off)          # один Mind на вариант: шпионы не зависят от прошлых событий
    try:
        for name, event in EVENTS.items():
            lab.log.clear()
            asyncio.run(lab.mind.on_event(dict(event, type="event", ts=1)))
            out[name] = list(lab.log)
    finally:
        lab.close()
    return out


def trace_tick(off):
    lab = Lab().spy(off)
    try:
        lab.mind.state_received = time.time()      # свежее состояние: сохраняются мотивы и настроение
        asyncio.run(lab.mind.step())
        return list(lab.log)
    finally:
        lab.close()


def trace_prompt(off):
    lab = Lab().spy(off)
    try:
        user = json.loads(lab.mind.build_prompt("повод", {"from": "Vera"})[1]["content"])
        names = set(CORE + OPTIONAL)
        return [[k, v if isinstance(v, str) and v.split(".")[0] in names else "*"] for k, v in user.items()]
    finally:
        lab.close()


def modules_of(lab):
    return {name: (type(getattr(lab.mind, name)).__name__ if getattr(lab.mind, name, None) is not None else None)
            for name in CORE + OPTIONAL}


def creation_table():
    """Какие модули создаются: база и отличия от базы для каждого варианта настроек."""
    def make(**kw):
        lab = Lab(**kw)
        try:
            return modules_of(lab)
        finally:
            lab.close()

    def world_with(key, **cfg):
        w = json.loads(json.dumps(WORLD))
        w[key] = dict(w.get(key) or {}, **cfg)
        return w

    base = make()
    configs = {"world=None": {"world": None}, "без жителей": {"peers": ()}}
    for feat in ("home", "mood", "calendar", "career", "routine", "economy", "party", "activity", "bonds", "crew",
                 "pets", "social", "society", "aims", "guild", "explore", "strangers", "world_bus", "rivalry",
                 "crowd", "episodes", "tradition", "gossip", "habits"):   # gossip: habits:
        configs[f"BRAIN_DISABLE={feat}"] = {"env": {"BRAIN_DISABLE": feat}}
    for key in ("mood", "calendar", "party", "pets", "social", "society", "strangers", "rivalry", "crowd",
                "episodes", "tradition", "explore", "gossip", "habits"):   # gossip: habits:
        configs[f"{key}.enabled=false"] = {"world": world_with(key, enabled=False)}
    configs["guild.enabled=true"] = {"world": world_with("guild", enabled=True)}
    configs["guild.enabled=true, BRAIN_DISABLE=guild"] = {"world": world_with("guild", enabled=True),
                                                          "env": {"BRAIN_DISABLE": "guild"}}
    configs["guild.enabled=true, без жителей"] = {"world": world_with("guild", enabled=True), "peers": ()}
    w = json.loads(json.dumps(WORLD))
    w.pop("economy", None)
    configs["мир без economy"] = {"world": w}
    out = {"база": base}
    tmp = tempfile.TemporaryDirectory()
    try:
        bus = world_bus.WorldBus(Path(tmp.name) / "world.sqlite", "Arkady")
        configs["своя шина мира"] = {"world_bus_db": bus}
        configs["своя шина мира, BRAIN_DISABLE=world_bus"] = {"world_bus_db": bus,
                                                              "env": {"BRAIN_DISABLE": "world_bus"}}
        for name, kw in configs.items():
            got = make(**kw)
            out[name] = {k: v for k, v in got.items() if v != base[k]}
        bus.close()
    finally:
        tmp.cleanup()
    return out


# ---------- таблица порядка (снята с mind.py до перевода на реестр) ----------

EXPECTED_EVENTS = {'все': {'world_msg': ['strangers.private', 'rumors.on_world_msg'],
         'attack': ['routine.on_combat', 'life.on_event', 'explorer.on_event', 'postmortem.observe', 'gate'],
         'kill': ['strangers.private',
                  'routine.on_combat',
                  'life.on_event',
                  'explorer.on_event',
                  'postmortem.on_kill',
                  'maps.on_kill',
                  'gate'],
         'died': ['strangers.private',
                  'life.on_event',
                  'explorer.on_event',
                  'maps.on_death',
                  'postmortem.bans',
                  'postmortem.report',
                  'postmortem.bans',
                  'party.on_my_death',
                  'routine.on_death',
                  'home.on_death',
                  'gate'],
         'escape': ['strangers.private', 'life.on_event', 'explorer.on_event', 'routine.on_escape', 'gate'],
         'survival': ['strangers.private', 'life.on_event', 'explorer.on_event', 'postmortem.observe', 'gate'],
         'danger': ['strangers.private',
                    'life.on_event',
                    'explorer.on_event',
                    'postmortem.observe',
                    'crew.on_event',
                    'party.on_danger',
                    'gate'],
         'support': ['strangers.private',
                     'life.on_event',
                     'explorer.on_event',
                     'crew.on_event',
                     'social.on_support',
                     'party.on_support'],
         'level_up': ['strangers.private',
                      'life.on_event',
                      'explorer.on_event',
                      'crew.on_event',
                      'social.on_level_up',
                      'gate'],
         'heal_confirmed': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
         'loot': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
         'chat_public': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
         'chat_private plain': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
         'chat_private stranger': ['strangers.private',
                                   'life.on_event',
                                   'explorer.on_event',
                                   'gate',
                                   'strangers.on_whisper'],
         'tag meet': ['strangers.private', 'life.on_event', 'explorer.on_event', 'plans.on_tag'],
         'tag need': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_tag'],
         'tag offer': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_tag'],
         'tag info': ['strangers.private', 'life.on_event', 'explorer.on_event', 'rumors.on_tag'],
         'tag gossip': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gossip.on_tag'],
         'tag explore': ['strangers.private', 'life.on_event', 'explorer.on_event', 'explorer.on_tag'],
         'tag crew': ['strangers.private', 'life.on_event', 'explorer.on_event', 'crew.on_tag'],
         'tag guild': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_tag'],
         'tag party': ['strangers.private', 'life.on_event', 'explorer.on_event', 'party.on_tag'],
         'tag party dead': ['strangers.private',
                            'life.on_event',
                            'explorer.on_event',
                            'party.on_tag',
                            'social.on_peer_dead',
                            'crew.on_mate_dead'],
         'tag chat': ['strangers.private', 'life.on_event', 'explorer.on_event', 'social.on_tag'],
         'tags crew+party': ['strangers.private', 'life.on_event', 'explorer.on_event', 'crew.on_tag'],
         'tags party+chat': ['strangers.private',
                             'life.on_event',
                             'explorer.on_event',
                             'party.on_tag',
                             'social.on_peer_dead',
                             'crew.on_mate_dead'],
         'tags chat+meet': ['strangers.private', 'life.on_event', 'explorer.on_event', 'plans.on_tag'],
         'guild_create_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
         'guild_invite_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
         'guild_invite': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
         'guild_joined_auto': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
         'chat_guild': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
         'pet_tame_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
         'pet_hatched': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
         'pet_fed': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
         'job_change_result home': ['strangers.private', 'life.on_event', 'explorer.on_event', 'home.on_result'],
         'job_change_result job': ['strangers.private', 'life.on_event', 'explorer.on_event', 'career.on_result'],
         'deal_complete': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_deal_complete'],
         'give_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_give_result'],
         'buy_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_buy_result'],
         'mail_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_mail_result'],
         'mail_taken': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_mail_taken'],
         'mail_received': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_mail_received'],
         'npc_sold': ['strangers.private', 'life.on_event', 'explorer.on_event'],
         'vend_sold': ['strangers.private', 'life.on_event', 'explorer.on_event']},
 'без необязательных': {'world_msg': ['rumors.on_world_msg'],
                        'attack': ['life.on_event', 'postmortem.observe', 'gate'],
                        'kill': ['life.on_event', 'postmortem.on_kill', 'maps.on_kill', 'gate'],
                        'died': ['life.on_event',
                                 'maps.on_death',
                                 'postmortem.bans',
                                 'postmortem.report',
                                 'postmortem.bans',
                                 'gate'],
                        'escape': ['life.on_event', 'gate'],
                        'survival': ['life.on_event', 'postmortem.observe', 'gate'],
                        'danger': ['life.on_event', 'postmortem.observe', 'gate'],
                        'support': ['life.on_event', 'gate'],
                        'level_up': ['life.on_event', 'gate'],
                        'heal_confirmed': ['life.on_event', 'gate'],
                        'loot': ['life.on_event', 'gate'],
                        'chat_public': ['life.on_event', 'gate'],
                        'chat_private plain': ['life.on_event', 'gate'],
                        'chat_private stranger': ['life.on_event', 'gate'],
                        'tag meet': ['life.on_event', 'plans.on_tag'],
                        'tag need': ['life.on_event', 'gate'],
                        'tag offer': ['life.on_event', 'gate'],
                        'tag info': ['life.on_event', 'rumors.on_tag'],
                        'tag gossip': ['life.on_event', 'gate'],
                        'tag explore': ['life.on_event', 'gate'],
                        'tag crew': ['life.on_event', 'gate'],
                        'tag guild': ['life.on_event', 'gate'],
                        'tag party': ['life.on_event', 'gate'],
                        'tag party dead': ['life.on_event', 'gate'],
                        'tag chat': ['life.on_event', 'gate'],
                        'tags crew+party': ['life.on_event', 'gate'],
                        'tags party+chat': ['life.on_event', 'gate'],
                        'tags chat+meet': ['life.on_event', 'plans.on_tag'],
                        'guild_create_result': ['life.on_event'],
                        'guild_invite_result': ['life.on_event'],
                        'guild_invite': ['life.on_event'],
                        'guild_joined_auto': ['life.on_event'],
                        'chat_guild': ['life.on_event'],
                        'pet_tame_result': ['life.on_event'],
                        'pet_hatched': ['life.on_event'],
                        'pet_fed': ['life.on_event'],
                        'job_change_result home': ['life.on_event'],
                        'job_change_result job': ['life.on_event'],
                        'deal_complete': ['life.on_event'],
                        'give_result': ['life.on_event'],
                        'buy_result': ['life.on_event'],
                        'mail_result': ['life.on_event'],
                        'mail_taken': ['life.on_event'],
                        'mail_received': ['life.on_event'],
                        'npc_sold': ['life.on_event'],
                        'vend_sold': ['life.on_event']},
 'без party': {'world_msg': ['strangers.private', 'rumors.on_world_msg'],
               'attack': ['routine.on_combat', 'life.on_event', 'explorer.on_event', 'postmortem.observe', 'gate'],
               'kill': ['strangers.private',
                        'routine.on_combat',
                        'life.on_event',
                        'explorer.on_event',
                        'postmortem.on_kill',
                        'maps.on_kill',
                        'gate'],
               'died': ['strangers.private',
                        'life.on_event',
                        'explorer.on_event',
                        'maps.on_death',
                        'postmortem.bans',
                        'postmortem.report',
                        'postmortem.bans',
                        'routine.on_death',
                        'home.on_death',
                        'gate'],
               'escape': ['strangers.private', 'life.on_event', 'explorer.on_event', 'routine.on_escape', 'gate'],
               'survival': ['strangers.private', 'life.on_event', 'explorer.on_event', 'postmortem.observe', 'gate'],
               'danger': ['strangers.private',
                          'life.on_event',
                          'explorer.on_event',
                          'postmortem.observe',
                          'crew.on_event',
                          'gate'],
               'support': ['strangers.private',
                           'life.on_event',
                           'explorer.on_event',
                           'crew.on_event',
                           'social.on_support',
                           'gate'],
               'level_up': ['strangers.private',
                            'life.on_event',
                            'explorer.on_event',
                            'crew.on_event',
                            'social.on_level_up',
                            'gate'],
               'heal_confirmed': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
               'loot': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
               'chat_public': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
               'chat_private plain': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
               'chat_private stranger': ['strangers.private',
                                         'life.on_event',
                                         'explorer.on_event',
                                         'gate',
                                         'strangers.on_whisper'],
               'tag meet': ['strangers.private', 'life.on_event', 'explorer.on_event', 'plans.on_tag'],
               'tag need': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_tag'],
               'tag offer': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_tag'],
               'tag info': ['strangers.private', 'life.on_event', 'explorer.on_event', 'rumors.on_tag'],
               'tag gossip': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gossip.on_tag'],
               'tag explore': ['strangers.private', 'life.on_event', 'explorer.on_event', 'explorer.on_tag'],
               'tag crew': ['strangers.private', 'life.on_event', 'explorer.on_event', 'crew.on_tag'],
               'tag guild': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_tag'],
               'tag party': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
               'tag party dead': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
               'tag chat': ['strangers.private', 'life.on_event', 'explorer.on_event', 'social.on_tag'],
               'tags crew+party': ['strangers.private', 'life.on_event', 'explorer.on_event', 'crew.on_tag'],
               'tags party+chat': ['strangers.private', 'life.on_event', 'explorer.on_event', 'social.on_tag'],
               'tags chat+meet': ['strangers.private', 'life.on_event', 'explorer.on_event', 'plans.on_tag'],
               'guild_create_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
               'guild_invite_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
               'guild_invite': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
               'guild_joined_auto': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
               'chat_guild': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
               'pet_tame_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
               'pet_hatched': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
               'pet_fed': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
               'job_change_result home': ['strangers.private',
                                          'life.on_event',
                                          'explorer.on_event',
                                          'home.on_result'],
               'job_change_result job': ['strangers.private',
                                         'life.on_event',
                                         'explorer.on_event',
                                         'career.on_result'],
               'deal_complete': ['strangers.private',
                                 'life.on_event',
                                 'explorer.on_event',
                                 'economy.on_deal_complete'],
               'give_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_give_result'],
               'buy_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_buy_result'],
               'mail_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_mail_result'],
               'mail_taken': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_mail_taken'],
               'mail_received': ['strangers.private',
                                 'life.on_event',
                                 'explorer.on_event',
                                 'economy.on_mail_received'],
               'npc_sold': ['strangers.private', 'life.on_event', 'explorer.on_event'],
               'vend_sold': ['strangers.private', 'life.on_event', 'explorer.on_event']},
 'без economy': {'world_msg': ['strangers.private', 'rumors.on_world_msg'],
                 'attack': ['routine.on_combat', 'life.on_event', 'explorer.on_event', 'postmortem.observe', 'gate'],
                 'kill': ['strangers.private',
                          'routine.on_combat',
                          'life.on_event',
                          'explorer.on_event',
                          'postmortem.on_kill',
                          'maps.on_kill',
                          'gate'],
                 'died': ['strangers.private',
                          'life.on_event',
                          'explorer.on_event',
                          'maps.on_death',
                          'postmortem.bans',
                          'postmortem.report',
                          'postmortem.bans',
                          'party.on_my_death',
                          'routine.on_death',
                          'home.on_death',
                          'gate'],
                 'escape': ['strangers.private', 'life.on_event', 'explorer.on_event', 'routine.on_escape', 'gate'],
                 'survival': ['strangers.private',
                              'life.on_event',
                              'explorer.on_event',
                              'postmortem.observe',
                              'gate'],
                 'danger': ['strangers.private',
                            'life.on_event',
                            'explorer.on_event',
                            'postmortem.observe',
                            'crew.on_event',
                            'party.on_danger',
                            'gate'],
                 'support': ['strangers.private',
                             'life.on_event',
                             'explorer.on_event',
                             'crew.on_event',
                             'social.on_support',
                             'party.on_support'],
                 'level_up': ['strangers.private',
                              'life.on_event',
                              'explorer.on_event',
                              'crew.on_event',
                              'social.on_level_up',
                              'gate'],
                 'heal_confirmed': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                 'loot': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                 'chat_public': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                 'chat_private plain': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                 'chat_private stranger': ['strangers.private',
                                           'life.on_event',
                                           'explorer.on_event',
                                           'gate',
                                           'strangers.on_whisper'],
                 'tag meet': ['strangers.private', 'life.on_event', 'explorer.on_event', 'plans.on_tag'],
                 'tag need': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                 'tag offer': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                 'tag info': ['strangers.private', 'life.on_event', 'explorer.on_event', 'rumors.on_tag'],
                 'tag gossip': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gossip.on_tag'],
                 'tag explore': ['strangers.private', 'life.on_event', 'explorer.on_event', 'explorer.on_tag'],
                 'tag crew': ['strangers.private', 'life.on_event', 'explorer.on_event', 'crew.on_tag'],
                 'tag guild': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_tag'],
                 'tag party': ['strangers.private', 'life.on_event', 'explorer.on_event', 'party.on_tag'],
                 'tag party dead': ['strangers.private',
                                    'life.on_event',
                                    'explorer.on_event',
                                    'party.on_tag',
                                    'social.on_peer_dead',
                                    'crew.on_mate_dead'],
                 'tag chat': ['strangers.private', 'life.on_event', 'explorer.on_event', 'social.on_tag'],
                 'tags crew+party': ['strangers.private', 'life.on_event', 'explorer.on_event', 'crew.on_tag'],
                 'tags party+chat': ['strangers.private',
                                     'life.on_event',
                                     'explorer.on_event',
                                     'party.on_tag',
                                     'social.on_peer_dead',
                                     'crew.on_mate_dead'],
                 'tags chat+meet': ['strangers.private', 'life.on_event', 'explorer.on_event', 'plans.on_tag'],
                 'guild_create_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
                 'guild_invite_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
                 'guild_invite': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
                 'guild_joined_auto': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
                 'chat_guild': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
                 'pet_tame_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
                 'pet_hatched': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
                 'pet_fed': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
                 'job_change_result home': ['strangers.private',
                                            'life.on_event',
                                            'explorer.on_event',
                                            'home.on_result'],
                 'job_change_result job': ['strangers.private',
                                           'life.on_event',
                                           'explorer.on_event',
                                           'career.on_result'],
                 'deal_complete': ['strangers.private', 'life.on_event', 'explorer.on_event'],
                 'give_result': ['strangers.private', 'life.on_event', 'explorer.on_event'],
                 'buy_result': ['strangers.private', 'life.on_event', 'explorer.on_event'],
                 'mail_result': ['strangers.private', 'life.on_event', 'explorer.on_event'],
                 'mail_taken': ['strangers.private', 'life.on_event', 'explorer.on_event'],
                 'mail_received': ['strangers.private', 'life.on_event', 'explorer.on_event'],
                 'npc_sold': ['strangers.private', 'life.on_event', 'explorer.on_event'],
                 'vend_sold': ['strangers.private', 'life.on_event', 'explorer.on_event']},
 'без social': {'world_msg': ['strangers.private', 'rumors.on_world_msg'],
                'attack': ['routine.on_combat', 'life.on_event', 'explorer.on_event', 'postmortem.observe', 'gate'],
                'kill': ['strangers.private',
                         'routine.on_combat',
                         'life.on_event',
                         'explorer.on_event',
                         'postmortem.on_kill',
                         'maps.on_kill',
                         'gate'],
                'died': ['strangers.private',
                         'life.on_event',
                         'explorer.on_event',
                         'maps.on_death',
                         'postmortem.bans',
                         'postmortem.report',
                         'postmortem.bans',
                         'party.on_my_death',
                         'routine.on_death',
                         'home.on_death',
                         'gate'],
                'escape': ['strangers.private', 'life.on_event', 'explorer.on_event', 'routine.on_escape', 'gate'],
                'survival': ['strangers.private', 'life.on_event', 'explorer.on_event', 'postmortem.observe', 'gate'],
                'danger': ['strangers.private',
                           'life.on_event',
                           'explorer.on_event',
                           'postmortem.observe',
                           'crew.on_event',
                           'party.on_danger',
                           'gate'],
                'support': ['strangers.private',
                            'life.on_event',
                            'explorer.on_event',
                            'crew.on_event',
                            'party.on_support'],
                'level_up': ['strangers.private', 'life.on_event', 'explorer.on_event', 'crew.on_event', 'gate'],
                'heal_confirmed': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                'loot': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                'chat_public': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                'chat_private plain': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                'chat_private stranger': ['strangers.private',
                                          'life.on_event',
                                          'explorer.on_event',
                                          'gate',
                                          'strangers.on_whisper'],
                'tag meet': ['strangers.private', 'life.on_event', 'explorer.on_event', 'plans.on_tag'],
                'tag need': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_tag'],
                'tag offer': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_tag'],
                'tag info': ['strangers.private', 'life.on_event', 'explorer.on_event', 'rumors.on_tag'],
                'tag gossip': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gossip.on_tag'],
                'tag explore': ['strangers.private', 'life.on_event', 'explorer.on_event', 'explorer.on_tag'],
                'tag crew': ['strangers.private', 'life.on_event', 'explorer.on_event', 'crew.on_tag'],
                'tag guild': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_tag'],
                'tag party': ['strangers.private', 'life.on_event', 'explorer.on_event', 'party.on_tag'],
                'tag party dead': ['strangers.private',
                                   'life.on_event',
                                   'explorer.on_event',
                                   'party.on_tag',
                                   'crew.on_mate_dead'],
                'tag chat': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                'tags crew+party': ['strangers.private', 'life.on_event', 'explorer.on_event', 'crew.on_tag'],
                'tags party+chat': ['strangers.private',
                                    'life.on_event',
                                    'explorer.on_event',
                                    'party.on_tag',
                                    'crew.on_mate_dead'],
                'tags chat+meet': ['strangers.private', 'life.on_event', 'explorer.on_event', 'plans.on_tag'],
                'guild_create_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
                'guild_invite_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
                'guild_invite': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
                'guild_joined_auto': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
                'chat_guild': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
                'pet_tame_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
                'pet_hatched': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
                'pet_fed': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
                'job_change_result home': ['strangers.private',
                                           'life.on_event',
                                           'explorer.on_event',
                                           'home.on_result'],
                'job_change_result job': ['strangers.private',
                                          'life.on_event',
                                          'explorer.on_event',
                                          'career.on_result'],
                'deal_complete': ['strangers.private',
                                  'life.on_event',
                                  'explorer.on_event',
                                  'economy.on_deal_complete'],
                'give_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_give_result'],
                'buy_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_buy_result'],
                'mail_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_mail_result'],
                'mail_taken': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_mail_taken'],
                'mail_received': ['strangers.private',
                                  'life.on_event',
                                  'explorer.on_event',
                                  'economy.on_mail_received'],
                'npc_sold': ['strangers.private', 'life.on_event', 'explorer.on_event'],
                'vend_sold': ['strangers.private', 'life.on_event', 'explorer.on_event']},
 'без crew': {'world_msg': ['strangers.private', 'rumors.on_world_msg'],
              'attack': ['routine.on_combat', 'life.on_event', 'explorer.on_event', 'postmortem.observe', 'gate'],
              'kill': ['strangers.private',
                       'routine.on_combat',
                       'life.on_event',
                       'explorer.on_event',
                       'postmortem.on_kill',
                       'maps.on_kill',
                       'gate'],
              'died': ['strangers.private',
                       'life.on_event',
                       'explorer.on_event',
                       'maps.on_death',
                       'postmortem.bans',
                       'postmortem.report',
                       'postmortem.bans',
                       'party.on_my_death',
                       'routine.on_death',
                       'home.on_death',
                       'gate'],
              'escape': ['strangers.private', 'life.on_event', 'explorer.on_event', 'routine.on_escape', 'gate'],
              'survival': ['strangers.private', 'life.on_event', 'explorer.on_event', 'postmortem.observe', 'gate'],
              'danger': ['strangers.private',
                         'life.on_event',
                         'explorer.on_event',
                         'postmortem.observe',
                         'party.on_danger',
                         'gate'],
              'support': ['strangers.private',
                          'life.on_event',
                          'explorer.on_event',
                          'social.on_support',
                          'party.on_support'],
              'level_up': ['strangers.private', 'life.on_event', 'explorer.on_event', 'social.on_level_up', 'gate'],
              'heal_confirmed': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
              'loot': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
              'chat_public': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
              'chat_private plain': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
              'chat_private stranger': ['strangers.private',
                                        'life.on_event',
                                        'explorer.on_event',
                                        'gate',
                                        'strangers.on_whisper'],
              'tag meet': ['strangers.private', 'life.on_event', 'explorer.on_event', 'plans.on_tag'],
              'tag need': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_tag'],
              'tag offer': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_tag'],
              'tag info': ['strangers.private', 'life.on_event', 'explorer.on_event', 'rumors.on_tag'],
              'tag gossip': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gossip.on_tag'],
              'tag explore': ['strangers.private', 'life.on_event', 'explorer.on_event', 'explorer.on_tag'],
              'tag crew': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
              'tag guild': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_tag'],
              'tag party': ['strangers.private', 'life.on_event', 'explorer.on_event', 'party.on_tag'],
              'tag party dead': ['strangers.private',
                                 'life.on_event',
                                 'explorer.on_event',
                                 'party.on_tag',
                                 'social.on_peer_dead'],
              'tag chat': ['strangers.private', 'life.on_event', 'explorer.on_event', 'social.on_tag'],
              'tags crew+party': ['strangers.private', 'life.on_event', 'explorer.on_event', 'party.on_tag'],
              'tags party+chat': ['strangers.private',
                                  'life.on_event',
                                  'explorer.on_event',
                                  'party.on_tag',
                                  'social.on_peer_dead'],
              'tags chat+meet': ['strangers.private', 'life.on_event', 'explorer.on_event', 'plans.on_tag'],
              'guild_create_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
              'guild_invite_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
              'guild_invite': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
              'guild_joined_auto': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
              'chat_guild': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
              'pet_tame_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
              'pet_hatched': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
              'pet_fed': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
              'job_change_result home': ['strangers.private', 'life.on_event', 'explorer.on_event', 'home.on_result'],
              'job_change_result job': ['strangers.private',
                                        'life.on_event',
                                        'explorer.on_event',
                                        'career.on_result'],
              'deal_complete': ['strangers.private',
                                'life.on_event',
                                'explorer.on_event',
                                'economy.on_deal_complete'],
              'give_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_give_result'],
              'buy_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_buy_result'],
              'mail_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_mail_result'],
              'mail_taken': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_mail_taken'],
              'mail_received': ['strangers.private',
                                'life.on_event',
                                'explorer.on_event',
                                'economy.on_mail_received'],
              'npc_sold': ['strangers.private', 'life.on_event', 'explorer.on_event'],
              'vend_sold': ['strangers.private', 'life.on_event', 'explorer.on_event']},
 'без explorer': {'world_msg': ['strangers.private', 'rumors.on_world_msg'],
                  'attack': ['routine.on_combat', 'life.on_event', 'postmortem.observe', 'gate'],
                  'kill': ['strangers.private',
                           'routine.on_combat',
                           'life.on_event',
                           'postmortem.on_kill',
                           'maps.on_kill',
                           'gate'],
                  'died': ['strangers.private',
                           'life.on_event',
                           'maps.on_death',
                           'postmortem.bans',
                           'postmortem.report',
                           'postmortem.bans',
                           'party.on_my_death',
                           'routine.on_death',
                           'home.on_death',
                           'gate'],
                  'escape': ['strangers.private', 'life.on_event', 'routine.on_escape', 'gate'],
                  'survival': ['strangers.private', 'life.on_event', 'postmortem.observe', 'gate'],
                  'danger': ['strangers.private',
                             'life.on_event',
                             'postmortem.observe',
                             'crew.on_event',
                             'party.on_danger',
                             'gate'],
                  'support': ['strangers.private',
                              'life.on_event',
                              'crew.on_event',
                              'social.on_support',
                              'party.on_support'],
                  'level_up': ['strangers.private', 'life.on_event', 'crew.on_event', 'social.on_level_up', 'gate'],
                  'heal_confirmed': ['strangers.private', 'life.on_event', 'gate'],
                  'loot': ['strangers.private', 'life.on_event', 'gate'],
                  'chat_public': ['strangers.private', 'life.on_event', 'gate'],
                  'chat_private plain': ['strangers.private', 'life.on_event', 'gate'],
                  'chat_private stranger': ['strangers.private', 'life.on_event', 'gate', 'strangers.on_whisper'],
                  'tag meet': ['strangers.private', 'life.on_event', 'plans.on_tag'],
                  'tag need': ['strangers.private', 'life.on_event', 'economy.on_tag'],
                  'tag offer': ['strangers.private', 'life.on_event', 'economy.on_tag'],
                  'tag info': ['strangers.private', 'life.on_event', 'rumors.on_tag'],
                  'tag gossip': ['strangers.private', 'life.on_event', 'gossip.on_tag'],
                  'tag explore': ['strangers.private', 'life.on_event', 'gate'],
                  'tag crew': ['strangers.private', 'life.on_event', 'crew.on_tag'],
                  'tag guild': ['strangers.private', 'life.on_event', 'guild.on_tag'],
                  'tag party': ['strangers.private', 'life.on_event', 'party.on_tag'],
                  'tag party dead': ['strangers.private',
                                     'life.on_event',
                                     'party.on_tag',
                                     'social.on_peer_dead',
                                     'crew.on_mate_dead'],
                  'tag chat': ['strangers.private', 'life.on_event', 'social.on_tag'],
                  'tags crew+party': ['strangers.private', 'life.on_event', 'crew.on_tag'],
                  'tags party+chat': ['strangers.private',
                                      'life.on_event',
                                      'party.on_tag',
                                      'social.on_peer_dead',
                                      'crew.on_mate_dead'],
                  'tags chat+meet': ['strangers.private', 'life.on_event', 'plans.on_tag'],
                  'guild_create_result': ['strangers.private', 'life.on_event', 'guild.on_event'],
                  'guild_invite_result': ['strangers.private', 'life.on_event', 'guild.on_event'],
                  'guild_invite': ['strangers.private', 'life.on_event', 'guild.on_event'],
                  'guild_joined_auto': ['strangers.private', 'life.on_event', 'guild.on_event'],
                  'chat_guild': ['strangers.private', 'life.on_event', 'guild.on_event'],
                  'pet_tame_result': ['strangers.private', 'life.on_event', 'pets.on_event'],
                  'pet_hatched': ['strangers.private', 'life.on_event', 'pets.on_event'],
                  'pet_fed': ['strangers.private', 'life.on_event', 'pets.on_event'],
                  'job_change_result home': ['strangers.private', 'life.on_event', 'home.on_result'],
                  'job_change_result job': ['strangers.private', 'life.on_event', 'career.on_result'],
                  'deal_complete': ['strangers.private', 'life.on_event', 'economy.on_deal_complete'],
                  'give_result': ['strangers.private', 'life.on_event', 'economy.on_give_result'],
                  'buy_result': ['strangers.private', 'life.on_event', 'economy.on_buy_result'],
                  'mail_result': ['strangers.private', 'life.on_event', 'economy.on_mail_result'],
                  'mail_taken': ['strangers.private', 'life.on_event', 'economy.on_mail_taken'],
                  'mail_received': ['strangers.private', 'life.on_event', 'economy.on_mail_received'],
                  'npc_sold': ['strangers.private', 'life.on_event'],
                  'vend_sold': ['strangers.private', 'life.on_event']},
 'без guild': {'world_msg': ['strangers.private', 'rumors.on_world_msg'],
               'attack': ['routine.on_combat', 'life.on_event', 'explorer.on_event', 'postmortem.observe', 'gate'],
               'kill': ['strangers.private',
                        'routine.on_combat',
                        'life.on_event',
                        'explorer.on_event',
                        'postmortem.on_kill',
                        'maps.on_kill',
                        'gate'],
               'died': ['strangers.private',
                        'life.on_event',
                        'explorer.on_event',
                        'maps.on_death',
                        'postmortem.bans',
                        'postmortem.report',
                        'postmortem.bans',
                        'party.on_my_death',
                        'routine.on_death',
                        'home.on_death',
                        'gate'],
               'escape': ['strangers.private', 'life.on_event', 'explorer.on_event', 'routine.on_escape', 'gate'],
               'survival': ['strangers.private', 'life.on_event', 'explorer.on_event', 'postmortem.observe', 'gate'],
               'danger': ['strangers.private',
                          'life.on_event',
                          'explorer.on_event',
                          'postmortem.observe',
                          'crew.on_event',
                          'party.on_danger',
                          'gate'],
               'support': ['strangers.private',
                           'life.on_event',
                           'explorer.on_event',
                           'crew.on_event',
                           'social.on_support',
                           'party.on_support'],
               'level_up': ['strangers.private',
                            'life.on_event',
                            'explorer.on_event',
                            'crew.on_event',
                            'social.on_level_up',
                            'gate'],
               'heal_confirmed': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
               'loot': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
               'chat_public': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
               'chat_private plain': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
               'chat_private stranger': ['strangers.private',
                                         'life.on_event',
                                         'explorer.on_event',
                                         'gate',
                                         'strangers.on_whisper'],
               'tag meet': ['strangers.private', 'life.on_event', 'explorer.on_event', 'plans.on_tag'],
               'tag need': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_tag'],
               'tag offer': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_tag'],
               'tag info': ['strangers.private', 'life.on_event', 'explorer.on_event', 'rumors.on_tag'],
               'tag gossip': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gossip.on_tag'],
               'tag explore': ['strangers.private', 'life.on_event', 'explorer.on_event', 'explorer.on_tag'],
               'tag crew': ['strangers.private', 'life.on_event', 'explorer.on_event', 'crew.on_tag'],
               'tag guild': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
               'tag party': ['strangers.private', 'life.on_event', 'explorer.on_event', 'party.on_tag'],
               'tag party dead': ['strangers.private',
                                  'life.on_event',
                                  'explorer.on_event',
                                  'party.on_tag',
                                  'social.on_peer_dead',
                                  'crew.on_mate_dead'],
               'tag chat': ['strangers.private', 'life.on_event', 'explorer.on_event', 'social.on_tag'],
               'tags crew+party': ['strangers.private', 'life.on_event', 'explorer.on_event', 'crew.on_tag'],
               'tags party+chat': ['strangers.private',
                                   'life.on_event',
                                   'explorer.on_event',
                                   'party.on_tag',
                                   'social.on_peer_dead',
                                   'crew.on_mate_dead'],
               'tags chat+meet': ['strangers.private', 'life.on_event', 'explorer.on_event', 'plans.on_tag'],
               'guild_create_result': ['strangers.private', 'life.on_event', 'explorer.on_event'],
               'guild_invite_result': ['strangers.private', 'life.on_event', 'explorer.on_event'],
               'guild_invite': ['strangers.private', 'life.on_event', 'explorer.on_event'],
               'guild_joined_auto': ['strangers.private', 'life.on_event', 'explorer.on_event'],
               'chat_guild': ['strangers.private', 'life.on_event', 'explorer.on_event'],
               'pet_tame_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
               'pet_hatched': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
               'pet_fed': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
               'job_change_result home': ['strangers.private',
                                          'life.on_event',
                                          'explorer.on_event',
                                          'home.on_result'],
               'job_change_result job': ['strangers.private',
                                         'life.on_event',
                                         'explorer.on_event',
                                         'career.on_result'],
               'deal_complete': ['strangers.private',
                                 'life.on_event',
                                 'explorer.on_event',
                                 'economy.on_deal_complete'],
               'give_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_give_result'],
               'buy_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_buy_result'],
               'mail_result': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_mail_result'],
               'mail_taken': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_mail_taken'],
               'mail_received': ['strangers.private',
                                 'life.on_event',
                                 'explorer.on_event',
                                 'economy.on_mail_received'],
               'npc_sold': ['strangers.private', 'life.on_event', 'explorer.on_event'],
               'vend_sold': ['strangers.private', 'life.on_event', 'explorer.on_event']},
 'без strangers': {'world_msg': ['rumors.on_world_msg'],
                   'attack': ['routine.on_combat',
                              'life.on_event',
                              'explorer.on_event',
                              'postmortem.observe',
                              'gate'],
                   'kill': ['routine.on_combat',
                            'life.on_event',
                            'explorer.on_event',
                            'postmortem.on_kill',
                            'maps.on_kill',
                            'gate'],
                   'died': ['life.on_event',
                            'explorer.on_event',
                            'maps.on_death',
                            'postmortem.bans',
                            'postmortem.report',
                            'postmortem.bans',
                            'party.on_my_death',
                            'routine.on_death',
                            'home.on_death',
                            'gate'],
                   'escape': ['life.on_event', 'explorer.on_event', 'routine.on_escape', 'gate'],
                   'survival': ['life.on_event', 'explorer.on_event', 'postmortem.observe', 'gate'],
                   'danger': ['life.on_event',
                              'explorer.on_event',
                              'postmortem.observe',
                              'crew.on_event',
                              'party.on_danger',
                              'gate'],
                   'support': ['life.on_event',
                               'explorer.on_event',
                               'crew.on_event',
                               'social.on_support',
                               'party.on_support'],
                   'level_up': ['life.on_event', 'explorer.on_event', 'crew.on_event', 'social.on_level_up', 'gate'],
                   'heal_confirmed': ['life.on_event', 'explorer.on_event', 'gate'],
                   'loot': ['life.on_event', 'explorer.on_event', 'gate'],
                   'chat_public': ['life.on_event', 'explorer.on_event', 'gate'],
                   'chat_private plain': ['life.on_event', 'explorer.on_event', 'gate'],
                   'chat_private stranger': ['life.on_event', 'explorer.on_event', 'gate'],
                   'tag meet': ['life.on_event', 'explorer.on_event', 'plans.on_tag'],
                   'tag need': ['life.on_event', 'explorer.on_event', 'economy.on_tag'],
                   'tag offer': ['life.on_event', 'explorer.on_event', 'economy.on_tag'],
                   'tag info': ['life.on_event', 'explorer.on_event', 'rumors.on_tag'],
                   'tag gossip': ['life.on_event', 'explorer.on_event', 'gossip.on_tag'],
                   'tag explore': ['life.on_event', 'explorer.on_event', 'explorer.on_tag'],
                   'tag crew': ['life.on_event', 'explorer.on_event', 'crew.on_tag'],
                   'tag guild': ['life.on_event', 'explorer.on_event', 'guild.on_tag'],
                   'tag party': ['life.on_event', 'explorer.on_event', 'party.on_tag'],
                   'tag party dead': ['life.on_event',
                                      'explorer.on_event',
                                      'party.on_tag',
                                      'social.on_peer_dead',
                                      'crew.on_mate_dead'],
                   'tag chat': ['life.on_event', 'explorer.on_event', 'social.on_tag'],
                   'tags crew+party': ['life.on_event', 'explorer.on_event', 'crew.on_tag'],
                   'tags party+chat': ['life.on_event',
                                       'explorer.on_event',
                                       'party.on_tag',
                                       'social.on_peer_dead',
                                       'crew.on_mate_dead'],
                   'tags chat+meet': ['life.on_event', 'explorer.on_event', 'plans.on_tag'],
                   'guild_create_result': ['life.on_event', 'explorer.on_event', 'guild.on_event'],
                   'guild_invite_result': ['life.on_event', 'explorer.on_event', 'guild.on_event'],
                   'guild_invite': ['life.on_event', 'explorer.on_event', 'guild.on_event'],
                   'guild_joined_auto': ['life.on_event', 'explorer.on_event', 'guild.on_event'],
                   'chat_guild': ['life.on_event', 'explorer.on_event', 'guild.on_event'],
                   'pet_tame_result': ['life.on_event', 'explorer.on_event', 'pets.on_event'],
                   'pet_hatched': ['life.on_event', 'explorer.on_event', 'pets.on_event'],
                   'pet_fed': ['life.on_event', 'explorer.on_event', 'pets.on_event'],
                   'job_change_result home': ['life.on_event', 'explorer.on_event', 'home.on_result'],
                   'job_change_result job': ['life.on_event', 'explorer.on_event', 'career.on_result'],
                   'deal_complete': ['life.on_event', 'explorer.on_event', 'economy.on_deal_complete'],
                   'give_result': ['life.on_event', 'explorer.on_event', 'economy.on_give_result'],
                   'buy_result': ['life.on_event', 'explorer.on_event', 'economy.on_buy_result'],
                   'mail_result': ['life.on_event', 'explorer.on_event', 'economy.on_mail_result'],
                   'mail_taken': ['life.on_event', 'explorer.on_event', 'economy.on_mail_taken'],
                   'mail_received': ['life.on_event', 'explorer.on_event', 'economy.on_mail_received'],
                   'npc_sold': ['life.on_event', 'explorer.on_event'],
                   'vend_sold': ['life.on_event', 'explorer.on_event']},
 'без routine и home': {'world_msg': ['strangers.private', 'rumors.on_world_msg'],
                        'attack': ['life.on_event', 'explorer.on_event', 'postmortem.observe', 'gate'],
                        'kill': ['strangers.private',
                                 'life.on_event',
                                 'explorer.on_event',
                                 'postmortem.on_kill',
                                 'maps.on_kill',
                                 'gate'],
                        'died': ['strangers.private',
                                 'life.on_event',
                                 'explorer.on_event',
                                 'maps.on_death',
                                 'postmortem.bans',
                                 'postmortem.report',
                                 'postmortem.bans',
                                 'party.on_my_death',
                                 'gate'],
                        'escape': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                        'survival': ['strangers.private',
                                     'life.on_event',
                                     'explorer.on_event',
                                     'postmortem.observe',
                                     'gate'],
                        'danger': ['strangers.private',
                                   'life.on_event',
                                   'explorer.on_event',
                                   'postmortem.observe',
                                   'crew.on_event',
                                   'party.on_danger',
                                   'gate'],
                        'support': ['strangers.private',
                                    'life.on_event',
                                    'explorer.on_event',
                                    'crew.on_event',
                                    'social.on_support',
                                    'party.on_support'],
                        'level_up': ['strangers.private',
                                     'life.on_event',
                                     'explorer.on_event',
                                     'crew.on_event',
                                     'social.on_level_up',
                                     'gate'],
                        'heal_confirmed': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                        'loot': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                        'chat_public': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                        'chat_private plain': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gate'],
                        'chat_private stranger': ['strangers.private',
                                                  'life.on_event',
                                                  'explorer.on_event',
                                                  'gate',
                                                  'strangers.on_whisper'],
                        'tag meet': ['strangers.private', 'life.on_event', 'explorer.on_event', 'plans.on_tag'],
                        'tag need': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_tag'],
                        'tag offer': ['strangers.private', 'life.on_event', 'explorer.on_event', 'economy.on_tag'],
                        'tag info': ['strangers.private', 'life.on_event', 'explorer.on_event', 'rumors.on_tag'],
                        'tag gossip': ['strangers.private', 'life.on_event', 'explorer.on_event', 'gossip.on_tag'],
                        'tag explore': ['strangers.private', 'life.on_event', 'explorer.on_event', 'explorer.on_tag'],
                        'tag crew': ['strangers.private', 'life.on_event', 'explorer.on_event', 'crew.on_tag'],
                        'tag guild': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_tag'],
                        'tag party': ['strangers.private', 'life.on_event', 'explorer.on_event', 'party.on_tag'],
                        'tag party dead': ['strangers.private',
                                           'life.on_event',
                                           'explorer.on_event',
                                           'party.on_tag',
                                           'social.on_peer_dead',
                                           'crew.on_mate_dead'],
                        'tag chat': ['strangers.private', 'life.on_event', 'explorer.on_event', 'social.on_tag'],
                        'tags crew+party': ['strangers.private', 'life.on_event', 'explorer.on_event', 'crew.on_tag'],
                        'tags party+chat': ['strangers.private',
                                            'life.on_event',
                                            'explorer.on_event',
                                            'party.on_tag',
                                            'social.on_peer_dead',
                                            'crew.on_mate_dead'],
                        'tags chat+meet': ['strangers.private', 'life.on_event', 'explorer.on_event', 'plans.on_tag'],
                        'guild_create_result': ['strangers.private',
                                                'life.on_event',
                                                'explorer.on_event',
                                                'guild.on_event'],
                        'guild_invite_result': ['strangers.private',
                                                'life.on_event',
                                                'explorer.on_event',
                                                'guild.on_event'],
                        'guild_invite': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
                        'guild_joined_auto': ['strangers.private',
                                              'life.on_event',
                                              'explorer.on_event',
                                              'guild.on_event'],
                        'chat_guild': ['strangers.private', 'life.on_event', 'explorer.on_event', 'guild.on_event'],
                        'pet_tame_result': ['strangers.private',
                                            'life.on_event',
                                            'explorer.on_event',
                                            'pets.on_event'],
                        'pet_hatched': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
                        'pet_fed': ['strangers.private', 'life.on_event', 'explorer.on_event', 'pets.on_event'],
                        'job_change_result home': ['strangers.private', 'life.on_event', 'explorer.on_event'],
                        'job_change_result job': ['strangers.private',
                                                  'life.on_event',
                                                  'explorer.on_event',
                                                  'career.on_result'],
                        'deal_complete': ['strangers.private',
                                          'life.on_event',
                                          'explorer.on_event',
                                          'economy.on_deal_complete'],
                        'give_result': ['strangers.private',
                                        'life.on_event',
                                        'explorer.on_event',
                                        'economy.on_give_result'],
                        'buy_result': ['strangers.private',
                                       'life.on_event',
                                       'explorer.on_event',
                                       'economy.on_buy_result'],
                        'mail_result': ['strangers.private',
                                        'life.on_event',
                                        'explorer.on_event',
                                        'economy.on_mail_result'],
                        'mail_taken': ['strangers.private',
                                       'life.on_event',
                                       'explorer.on_event',
                                       'economy.on_mail_taken'],
                        'mail_received': ['strangers.private',
                                          'life.on_event',
                                          'explorer.on_event',
                                          'economy.on_mail_received'],
                        'npc_sold': ['strangers.private', 'life.on_event', 'explorer.on_event'],
                        'vend_sold': ['strangers.private', 'life.on_event', 'explorer.on_event']}}

EXPECTED_TICK = {'все': ['needs.weighted',
         'mood.snapshot',
         'life.tick',
         'plans.tick',
         'routine.tick',
         'economy.tick',
         'party.tick',
         'career.tick',
         'activities.tick',
         'bonds.tick',
         'social.tick',
         'pets.tick',
         'crew.tick',
         'home.tick',
         'explorer.tick',
         'rumors.tick',
         'gossip.tick',
         'society.tick',
         'strangers.tick',
         'aims.tick',
         'guild.tick',
         'tradition.tick',
         'world.tick',
         'rivalry.tick',
         'crowd.tick',
         'habits.tick',
         'episodes.tick'],
 'без необязательных': ['needs.weighted', 'life.tick', 'plans.tick', 'rumors.tick']}

EXPECTED_PROMPT = {'все': [['я', '*'],
         ['повод', '*'],
         ['подробности', '*'],
         ['моё_состояние', '*'],
         ['текущая_цель', '*'],
         ['настроение', 'mood.summary'],
         ['последние_события', '*'],
         ['воспоминания', '*'],
         ['рядом_игроки', '*'],
         ['план', 'plans.summary'],
         ['распорядок', '*'],
         ['глобальные_цели', 'routine.goals'],
         ['хозяйство', 'economy.summary'],
         ['копилка', '*'],                    # dreams: ORG-073 (реальный модуль, не шпион)
         ['рынок', 'economy.market_summary'],
         ['мотивы', '*'],
         ['день_мира', 'calendar.summary'],
         ['традиция', 'tradition.summary'],
         ['карьера', '*'],
         ['занятие', 'activities.summary'],
         ['травник', '*'],                    # herbal: ORG-076 (реальный модуль, не шпион)
         ['экспедиция', 'explorer.summary'],
         ['наставничество', '*'],             # mentor: ORG-057 (реальный модуль, не шпион)
         ['цели_недели', 'aims.summary'],
         ['привычки', 'habits.summary'],
         ['мечта', '*'],                      # dreams: ORG-081 (реальный модуль, не шпион)
         ['соперник', 'rivalry.summary'],
         ['бестиарий', '*'],                  # bestiary: ORG-077 (реальный модуль, не шпион)
         ['слухи_не_факты', 'rumors.summary'],
         ['репутация', 'gossip.summary'],
         ['новости_мира', 'world.summary'],
         ['опасные_монстры', 'postmortem.risky_monsters'],
         ['опыт_по_картам', 'maps.summary'],
         ['закрытые_карты_до', '*'],
         ['группа', '*'],
         ['другие_жители', '*'],
         ['в_ссоре', 'society.summary'],
         ['гильдия', 'guild.summary'],
         ['отношение_к_собеседнику', '*'],
         ['собеседник_по_данным_игры', '*']],
 'без необязательных': [['я', '*'],
                        ['повод', '*'],
                        ['подробности', '*'],
                        ['моё_состояние', '*'],
                        ['текущая_цель', '*'],
                        ['настроение', '*'],
                        ['последние_события', '*'],
                        ['воспоминания', '*'],
                        ['рядом_игроки', '*'],
                        ['план', 'plans.summary'],
                        ['распорядок', '*'],
                        ['глобальные_цели', '*'],
                        ['хозяйство', '*'],
                        ['копилка', '*'],     # dreams:
                        ['рынок', '*'],
                        ['мотивы', '*'],
                        ['день_мира', '*'],
                        ['традиция', '*'],
                        ['карьера', '*'],
                        ['занятие', '*'],
                        ['травник', '*'],          # herbal:
                        ['экспедиция', '*'],
                        ['наставничество', '*'],   # mentor:
                        ['цели_недели', '*'],
                        ['привычки', '*'],
                        ['мечта', '*'],       # dreams:
                        ['соперник', '*'],
                        ['бестиарий', '*'],        # bestiary:
                        ['слухи_не_факты', 'rumors.summary'],
                        ['репутация', '*'],
                        ['новости_мира', '*'],
                        ['опасные_монстры', 'postmortem.risky_monsters'],
                        ['опыт_по_картам', 'maps.summary'],
                        ['закрытые_карты_до', '*'],
                        ['группа', '*'],
                        ['другие_жители', '*'],
                        ['в_ссоре', '*'],
                        ['гильдия', '*'],
                        ['отношение_к_собеседнику', '*'],
                        ['собеседник_по_данным_игры', '*']]}

EXPECTED_CREATE = {'база': {'plans': 'PlanExecutor',
          'life': 'Lifecycle',
          'postmortem': 'Postmortem',
          'maps': 'MapStats',
          'needs': 'Needs',
          'rumors': 'Rumors',
          'home': 'Home',
          'mood': 'Mood',
          'calendar': 'WorldCalendar',
          'career': 'Career',
          'routine': 'Routine',
          'economy': 'Economy',
          'party': 'Party',
          'activities': 'Activities',
          'bonds': 'Bonds',
          'crew': 'Crew',
          'pets': 'Pets',
          'social': 'Social',
          'society': 'Society',
          'aims': 'Aims',
          'guild': None,
          'explorer': 'Explorer',
          'strangers': 'Strangers',
          'world': None,
          'rivalry': 'Rivalry',
          'crowd': 'Crowd',
          'episodes': 'Episodes',
          'tradition': 'Tradition',
          'gossip': 'Gossip',
          'habits': 'Habits'},
 'world=None': {'home': None,
                'calendar': None,
                'career': None,
                'routine': None,
                'economy': None,
                'party': None,
                'activities': None,
                'crew': None,
                'pets': None,
                'social': None,
                'explorer': None,
                'tradition': None},
 'без жителей': {'party': None,
                 'bonds': None,
                 'crew': None,
                 'social': None,
                 'society': None,
                 'rivalry': None,
                 'episodes': None,
                 'gossip': None},
 'BRAIN_DISABLE=home': {'home': None},
 'BRAIN_DISABLE=mood': {'mood': None},
 'BRAIN_DISABLE=calendar': {'calendar': None},
 'BRAIN_DISABLE=career': {'career': None},
 'BRAIN_DISABLE=routine': {'routine': None, 'activities': None, 'explorer': None},
 'BRAIN_DISABLE=economy': {'economy': None},
 'BRAIN_DISABLE=party': {'party': None, 'crew': None},
 'BRAIN_DISABLE=activity': {'activities': None},
 'BRAIN_DISABLE=bonds': {'bonds': None},
 'BRAIN_DISABLE=crew': {'crew': None},
 'BRAIN_DISABLE=pets': {'pets': None},
 'BRAIN_DISABLE=social': {'social': None},
 'BRAIN_DISABLE=society': {'society': None},
 'BRAIN_DISABLE=aims': {'aims': None},
 'BRAIN_DISABLE=guild': {},
 'BRAIN_DISABLE=explore': {'explorer': None},
 'BRAIN_DISABLE=strangers': {'strangers': None},
 'BRAIN_DISABLE=world_bus': {},
 'BRAIN_DISABLE=rivalry': {'rivalry': None},
 'BRAIN_DISABLE=crowd': {'crowd': None},
 'BRAIN_DISABLE=episodes': {'episodes': None},
 'BRAIN_DISABLE=tradition': {'tradition': None},
 'BRAIN_DISABLE=gossip': {'gossip': None},
 'BRAIN_DISABLE=habits': {'habits': None},
 'mood.enabled=false': {'mood': None},
 'calendar.enabled=false': {'calendar': None},
 'party.enabled=false': {'party': None, 'crew': None},
 'pets.enabled=false': {'pets': None},
 'social.enabled=false': {'social': None},
 'society.enabled=false': {'society': None},
 'strangers.enabled=false': {'strangers': None},
 'rivalry.enabled=false': {'rivalry': None},
 'crowd.enabled=false': {'crowd': None},
 'episodes.enabled=false': {'episodes': None},
 'tradition.enabled=false': {'tradition': None},
 'explore.enabled=false': {'explorer': None},
 'gossip.enabled=false': {'gossip': None},
 'habits.enabled=false': {'habits': None},
 'guild.enabled=true': {'guild': 'Guild'},
 'guild.enabled=true, BRAIN_DISABLE=guild': {},
 'guild.enabled=true, без жителей': {'party': None,
                                     'bonds': None,
                                     'crew': None,
                                     'social': None,
                                     'society': None,
                                     'rivalry': None,
                                     'episodes': None,
                                     'gossip': None},
 'мир без economy': {'economy': None},
 'своя шина мира': {'world': 'Feed'},
 'своя шина мира, BRAIN_DISABLE=world_bus': {'world': 'Feed'}}


class MindOrderTest(unittest.TestCase):
    maxDiff = None

    def test_events(self):
        for variant, off in VARIANTS.items():
            with self.subTest(variant=variant):
                self.assertEqual(trace_events(off), EXPECTED_EVENTS[variant])

    def test_tick(self):
        for variant in ("все", "без необязательных"):
            with self.subTest(variant=variant):
                self.assertEqual(trace_tick(VARIANTS[variant]), EXPECTED_TICK[variant])

    def test_prompt(self):
        for variant in ("все", "без необязательных"):
            with self.subTest(variant=variant):
                self.assertEqual(trace_prompt(VARIANTS[variant]), EXPECTED_PROMPT[variant])

    def test_creation(self):
        self.assertEqual(creation_table(), EXPECTED_CREATE)


class Fishing:
    """Новый модуль «из будущего»: подключается строкой в реестре, mind.py не меняется."""
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES, ARGS = "fishing", "fishing", "fishing", True, ("world", "routine"), "config"
    TICK_ORDER = 85
    TAGS, TAG_ORDER = [(r"\[fish:[a-z]+\]", "on_tag")], 75
    EVENTS, EVENT_ORDER = {"level_up": "on_level_up", "fish_caught": {"call": "on_caught", "own": True}}, 25
    PROMPT = [("рыбалка", "summary", 205)]

    def __init__(self, mind, cfg):
        self.mind, self.cfg = mind, cfg

    def _log(self, what):
        self.mind.fishing_log.append(f"fishing.{what}")

    async def tick(self):
        self._log("tick")

    async def on_tag(self, sender, text):
        self._log("on_tag")

    def on_level_up(self, event):
        self._log("on_level_up")

    def on_caught(self, event):
        self._log("on_caught")

    def summary(self):
        return "клюёт"


class RegistryTest(unittest.TestCase):
    def lab(self, env=None):
        from live_brain import modules
        lab = Lab(env=env)
        self.addCleanup(lab.close)
        lab.mind.fishing_log = lab.log
        lab.mind.registry = modules.Registry(modules.MODULES + (Fishing,))
        modules.Registry((Fishing,)).build(lab.mind, WORLD)        # то же, что сделал бы Mind.__init__
        return lab

    def test_new_module_without_mind_edit(self):
        lab = self.lab()
        self.assertIsInstance(lab.mind.fishing, Fishing)
        lab.spy()                                     # остальные модули — шпионы, fishing остаётся настоящим
        run = lambda ev: asyncio.run(lab.mind.on_event(dict(ev, type="event", ts=1)))
        run(V("[fish:carp]"))
        self.assertEqual(lab.log[-1], "fishing.on_tag", "метка нового модуля забирает шёпот (до social [chat:])")
        lab.log.clear()
        run(EVENTS["level_up"])
        self.assertEqual(lab.log[-4:], ["crew.on_event", "social.on_level_up", "fishing.on_level_up", "gate"])
        lab.log.clear()
        run({"kind": "fish_caught"})
        self.assertNotIn("gate", lab.log, "own: вид нового модуля не уходит в gate")
        lab.log.clear()
        asyncio.run(lab.mind.step())
        self.assertEqual(lab.log[lab.log.index("pets.tick") + 1], "fishing.tick", "TICK_ORDER 85: после pets")
        user = json.loads(lab.mind.build_prompt("повод", {})[1]["content"])
        keys = list(user)
        self.assertEqual(user["рыбалка"], "клюёт")
        self.assertEqual(keys[keys.index("занятие") + 1], "рыбалка")

    def test_new_module_switches(self):
        self.assertIsNone(self.lab(env={"BRAIN_DISABLE": "fishing"}).mind.fishing, "BRAIN_DISABLE")
        lab = self.lab(env={"BRAIN_DISABLE": "routine"})
        self.assertIsNone(lab.mind.fishing, "REQUIRES routine")
        lab = self.lab().spy()
        lab.mind.fishing = None
        asyncio.run(lab.mind.on_event({"type": "event", "kind": "fish_caught"}))
        self.assertNotIn("gate", lab.log, "own поглощает вид и при выключенном модуле")

    def test_registry_checks(self):
        from live_brain import modules

        class NoMethod:
            ATTR, TICK_ORDER = "nomethod", 1
        with self.assertRaises(ValueError):
            modules.Registry((NoMethod,))
        with self.assertRaises(ValueError):
            modules.Registry((Fishing, Fishing))


def dump():
    tables = {
        "EXPECTED_EVENTS": {v: trace_events(off) for v, off in VARIANTS.items()},
        "EXPECTED_TICK": {v: trace_tick(VARIANTS[v]) for v in ("все", "без необязательных")},
        "EXPECTED_PROMPT": {v: trace_prompt(VARIANTS[v]) for v in ("все", "без необязательных")},
        "EXPECTED_CREATE": creation_table(),
    }
    for name, table in tables.items():
        print(f"{name} = " + pprint.pformat(table, width=118, sort_dicts=False) + "\n")


if __name__ == "__main__" and sys.argv[1:] == ["dump"]:
    dump()
elif __name__ == "__main__":
    unittest.main()
