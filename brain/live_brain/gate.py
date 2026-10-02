"""Decision gate: первый фильтр для каждого события от тела.

Gate решает, БЕЗ LLM:
  - какие действия выполнить сразу по правилам (actions);
  - что записать в память (memory);
  - нужен ли вызов LLM и с каким поводом (llm, llm_kind).

Сейчас работает RuleGate. JevGate — место для маленькой локальной модели
(«JEV-подобной»): оценка событий, выбор из ограниченного набора целей и решение,
нужен ли вызов LLM. Она НЕ установлена и не запускается; контракт ниже.

Контракт любого gate:
    evaluate(event: dict, state: dict, ctx: GateContext) -> GateResult
  event  — {"kind": "died"|"level_up"|"kill"|"loot"|"attack"|"chat_private"|
            "chat_public"|"in_game", ...поля события}
  state  — последнее состояние тела (hp_pct, sp_pct, map, lock_map, activity, ai, dead...)
  ctx    — имя персонажа, разрешённые карты, время последних срабатываний правил
  Результат проходит через SafetyPolicy (safety.py) — gate не может обойти правила безопасности.
"""
from dataclasses import dataclass, field
import logging
import time

log = logging.getLogger("gate")

STATUS_COMMANDS = {"!status", "!статус"}


@dataclass
class GateResult:
    actions: list = field(default_factory=list)
    memory: list = field(default_factory=list)      # [(текст, важность 1-5)]
    llm: str = None                                 # повод для LLM или None
    llm_kind: str = "event"                         # event | chat
    note: str = ""                                  # почему так решено (в журнал)


@dataclass
class GateContext:
    name: str
    hunt_maps: list
    greeting: str = ""
    greeting_every: int = 6 * 3600
    last: dict = field(default_factory=dict)        # правило -> время срабатывания


class RuleGate:
    name = "rules"

    def evaluate(self, event, state, ctx):
        kind = event.get("kind")
        handler = getattr(self, f"on_{kind}", None)
        return handler(event, state, ctx) if handler else GateResult(note=f"{kind}: только журнал")

    def on_in_game(self, event, state, ctx):
        now = time.time()
        if ctx.greeting and now - ctx.last.get("greeting", 0) >= ctx.greeting_every:
            ctx.last["greeting"] = now
            return GateResult(actions=[{"action": "say", "text": ctx.greeting}],
                              note="правило: приветствие при входе в игру")
        return GateResult(note="вход в игру")

    def on_died(self, event, state, ctx):
        return GateResult(memory=[(f"Я погиб на карте {event.get('map')}.", 3)],
                          llm="я только что погиб", note="смерть")

    def on_level_up(self, event, state, ctx):
        return GateResult(memory=[(f"Я достиг {event.get('level')} уровня на карте {event.get('map')}.", 3)],
                          llm=f"новый уровень {event.get('level')}", note="новый уровень")

    def on_chat_private(self, event, state, ctx):
        sender = str(event.get("from", ""))
        text = str(event.get("text", "")).strip().lower()
        if text in STATUS_COMMANDS:
            return GateResult(
                actions=[{"action": "whisper", "to": sender, "text": status_line(state)}],
                note=f"правило: {sender} запросил статус")
        return GateResult(llm=f"{sender} пишет мне в личку", llm_kind="chat", note="личное сообщение")

    def on_chat_public(self, event, state, ctx):
        if ctx.name.lower() in str(event.get("text", "")).lower():
            return GateResult(llm=f"{event.get('from')} обращается ко мне в общем чате",
                              llm_kind="chat", note="обращение в общем чате")
        return GateResult(note="общий чат")


def status_line(state):
    def v(key, suffix=""):
        value = state.get(key)
        return f"{value}{suffix}" if value is not None else "?"
    return (f"HP {v('hp_pct', '%')} SP {v('sp_pct', '%')} lv {v('lv')} "
            f"карта {v('map')} охота {v('lock_map')} занят {v('activity')}")[:100]


class JevGate:
    """Заглушка под маленькую локальную модель. Сейчас не загружается и не запускается.

    Подключение позже: реализовать evaluate() по контракту модуля (оценка важности
    события, выбор цели из ограниченного списка, флаг «нужен LLM»), оставив RuleGate
    первым слоем для критических правил.
    """
    name = "jev"

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("JEV-gate не установлен (BRAIN_GATE=jev пока не поддерживается)")


def make_gate(kind):
    if kind in ("", "rules"):
        return RuleGate()
    if kind == "jev":
        try:
            return JevGate()
        except NotImplementedError as e:
            log.warning("%s — использую rules", e)
            return RuleGate()
    log.warning("неизвестный BRAIN_GATE=%s — использую rules", kind)
    return RuleGate()
