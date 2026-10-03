"""Изъян характера (ORG-101, ТЗ Т-49): одно поле persona.flaw из маленького каталога с конкретным влиянием.

Изъян не добавляет поведения — он подкручивает существующие механизмы (строки `# flaw:` в модулях):
    chatterbox  болтун    бюджет внимания (attention.base) ×2 — заговаривает первым вдвое чаще;
    silent      молчун    бюджет внимания ×0.3 — почти не начинает разговор (ответы и протокол не ограничены);
    miser       скряга    не отдаёт зени по просьбе [need:] (зелья — по-прежнему), жадность в ценах +0.2;
    coward      трус      допустимый риск карты (needs.risk_tolerance) ×0.7;
    homebody    домосед   экспедиции (explore.check_target) не дальше 1 перехода от города.
Нет поля или неизвестный изъян (предупреждение в лог) — mind.flaw = None, поведение как раньше.
Выключатель: BRAIN_DISABLE=flaw или "flaw": {"enabled": false} в goals.json.
"""
import logging

log = logging.getLogger("flaws")

RU = {"chatterbox": "болтун", "silent": "молчун", "miser": "скряга", "coward": "трус", "homebody": "домосед"}
EFFECTS = {
    "chatterbox": {"talk": 2.0},
    "silent": {"talk": 0.3},
    "miser": {"greed_add": 0.2, "no_zeny": True},
    "coward": {"risk": 0.7},
    "homebody": {"hops": 1},
}
CATALOG = tuple(EFFECTS)


def flaw_of(persona):
    """Изъян персоны из каталога или None (неизвестный — с предупреждением)."""
    name = (persona or {}).get("flaw")
    if not name:
        return None
    if name not in EFFECTS:
        log.warning("неизвестный изъян %r у %s — пропущен (каталог: %s)", name, (persona or {}).get("name"),
                    ", ".join(CATALOG))
        return None
    return name


def label(persona):
    name = flaw_of(persona)
    return f"изъян: {RU[name]}" if name else None


class Flaw:
    # реестр модулей (modules.py, W8): только создание — без тика, меток, событий
    ATTR, FEATURE, CONFIG, ENABLED = "flaw", "flaw", "flaw", True
    TICK_ORDER = None

    @classmethod
    def brain_create(cls, mind, world):
        name = flaw_of(mind.persona)
        return cls(mind, name) if name else None

    def __init__(self, mind, name):
        self.mind = mind
        self.name = name
        self.effects = EFFECTS[name]

    def factor(self, key):
        """Множитель (talk, risk); нет такого влияния — 1.0."""
        return float(self.effects.get(key, 1.0))

    def cap(self, key, value):
        """Потолок (hops): min(value, предел изъяна)."""
        lim = self.effects.get(key)
        return value if lim is None else min(value, lim)

    def greed(self, g):
        return min(1.0, g + float(self.effects.get("greed_add", 0.0)))

    def refuses_zeny(self):
        return bool(self.effects.get("no_zeny"))

    def summary(self):
        return RU[self.name]
