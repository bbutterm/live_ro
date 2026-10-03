"""Интересы: не каждый делает всё (ORG-103, ТЗ Т-42 в docs/IDEAS2.md). Правила без LLM, без тика.

У жителя 1–3 увлечения из каталога CATALOG: поле persona["interests"] или вывод из черт (derive). Хобби-модули
продолжают молча записывать факты у всех (альбом, бестиарий, достижения, места), а инициативу и разговор о хобби
ведут только увлечённые:
    тема разговора (social.register_topic(..., interest=)) — шанс × weight: 1.0 увлечён, other (0.2) нет;
    приручение питомца (pets.py) — не увлечённый пробует только с шансом tame_other (0.2) за попытку;
    экспедиция (explore.py) — любопытство × 1.0 или explore_other (0.7);
    выбор мечты (dream.py) — оценка вида × вес интереса вида (DREAM_INTEREST).
Модуль реестра Interests (mind.interests). Выключен (BRAIN_DISABLE=interests или goals.json interests.enabled=false)
-> mind.interests = None, и weight(mind, ...) везде 1.0 — поведение как раньше.
"""
import hashlib
import logging

log = logging.getLogger("interests")

CATALOG = ("cards", "bestiary", "pets", "explore", "trade", "craft", "achieve", "places", "people")
# черта -> интересы, которые она рождает (из пары выбирается один — детерминированно по имени)
FROM_TRAIT = {
    "greed": ("trade", "cards"),
    "curiosity": ("explore", "bestiary"),
    "sociability": ("people",),
    "whimsy": ("pets", "places"),
    "diligence": ("achieve", "craft"),
}
RU = {"cards": "карты", "bestiary": "бестиарий", "pets": "питомцы", "explore": "путешествия", "trade": "торговля",
      "craft": "ремесло", "achieve": "достижения", "places": "места", "people": "люди"}
DREAM_INTEREST = {"cards": "cards", "explorer": "explore", "pet": "pets", "guild": "people", "rich": "trade"}
DEFAULTS = {"enabled": True, "other": 0.2, "tame_other": 0.2, "explore_other": 0.7}
MAX = 3


def _h(*parts):
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:4], "big")


def derive(traits, name):
    """2 разных интереса из двух самых выраженных черт FROM_TRAIT; ничья и выбор внутри пары — по hash(name)."""
    traits = traits or {}
    order = sorted(FROM_TRAIT, key=lambda t: (-float(traits.get(t, 0.5) or 0), _h(name, t)))
    out = []
    for trait in order:
        pool = FROM_TRAIT[trait]
        pick = pool[_h(name, trait, "pick") % len(pool)]
        if pick not in out:
            out.append(pick)
        if len(out) == 2:
            break
    return out


def interests(persona):
    """persona["interests"] (только из CATALOG, ≤ MAX, без повторов) или derive(...) по чертам."""
    persona = persona or {}
    raw = persona.get("interests")
    if raw:
        good = []
        for x in raw if isinstance(raw, list) else [raw]:
            if x in CATALOG and x not in good:
                good.append(x)
            else:
                log.warning("интересы %s: %r не из каталога %s — пропуск", persona.get("name"), x, CATALOG)
        if good:
            return good[:MAX]
    return derive(persona.get("traits"), persona.get("name") or "")


def weight(mind, name, key="other"):
    """Вес интереса name для модуля: нет модуля интересов (выключен) или name None — 1.0."""
    mod = getattr(mind, "interests", None) if mind is not None else None
    if mod is None or name is None:
        return 1.0
    return mod.weight(name, key)


class Interests:
    # реестр модулей (modules.py, W8): только создание, без тика и подписок
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "interests", "interests", "interests", True, "world"
    TICK_ORDER = None

    def __init__(self, mind, world=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("interests") or {}))
        self.list = interests(mind.persona)

    def has(self, name):
        return name in self.list

    def weight(self, name, key="other"):
        """1.0 — увлечён; иначе cfg[key] (other 0.2, tame_other 0.2, explore_other 0.7)."""
        if name is None or name in self.list:
            return 1.0
        return float(self.cfg.get(key, self.cfg["other"]))

    def summary(self):
        return ", ".join(RU.get(x, x) for x in self.list) or None
