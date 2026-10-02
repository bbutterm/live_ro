"""Генеративная грамматика реплик без LLM (ORG-065, ТЗ Т-24; слабое место W13 — скудный словарь).

Слой поверх фраз персон в social.phrase(): фраза персоны выбирается прежней ротацией, грамматика
    1) с вероятностью mix заменяет её шаблоном темы из brain/world/speech.json (если все слоты шаблона есть в фактах);
    2) раскрывает альтернативы {привет|здорово|ну привет} (вложенные, пустые, со слотами внутри);
    3) ставит род: слово(а) / дошёл(дошла) / появился(лась) — род говорящего (state.sex, затем persona.sex),
       <рад/рада> — говорящего явно, <@рад/рада> — собеседника (known_players[peer].sex); пол неизвестен — «рад(а)»;
    4) подставляет факты ({name}, {kills}) и заменяет коды карт в тексте формами имени места с предлогом
       («на prt_fild08» -> «на Южном поле» через резолвер ORG-084, без имени — «на карте prt_fild08»);
    5) добавляет словечко персоны в начало («Ну, ...», «Хм. ...») или в конец («... Вот так.») по характеру;
    6) не повторяет текст из последних recent реплик (до tries пересборок).
Длина: результат ≤ limit (60) или ≤ длины прежней фразы персоны; шаблон, который не влез, не звучит.
LLM не нужен: шаблоны — контент (speech.json), правила — здесь.
"""
import json
import logging
import re
from pathlib import Path

log = logging.getLogger("grammar")

SPEECH_PATH = Path(__file__).resolve().parent.parent / "world" / "speech.json"
DEFAULTS = {"enabled": True, "mix": 0.5, "tic_chance": 0.3, "tail_chance": 0.12, "recent": 60, "tries": 6,
            "limit": 60}

SLOT = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
ALT = re.compile(r"\{([^{}]*\|[^{}]*)\}")
PAREN = re.compile(r"([А-Яа-яЁё]+)\(([а-яё]{1,7})\)")          # Был(а), дошёл(дошла), появился(лась)
SEXFORM = re.compile(r"<(@?)([^<>/|]*)/([^<>|]*)>")              # <рад/рада>, <@устал/устала>
OPEN, CLOSE = "\x01", "\x02"
INTERJECTION = re.compile(r"[А-ЯЁ][а-яё]{0,3}[,!.]|[A-Z][a-z]+[!.?]")   # «О,», «Ого!», «Vera!» — словечко не нужно

# ---------- альтернативы ----------


def _protect(text):
    return SLOT.sub(lambda m: f"{OPEN}{m.group(1)}{CLOSE}", text)


def _restore(text):
    return text.replace(OPEN, "{").replace(CLOSE, "}")


def expand(text, rng=None, pick=None):
    """Раскрыть альтернативы {a|b|c} изнутри наружу; слоты {name} не трогаются."""
    t = _protect(text)
    while True:
        m = ALT.search(t)
        if not m:
            break
        opts = m.group(1).split("|")
        choice = pick(opts) if pick else rng.choice(opts)
        t = t[:m.start()] + choice + t[m.end():]
    return _restore(t)


def longest(text):
    """Самый длинный вариант шаблона (для проверки длины): в каждой альтернативе — самая длинная."""
    return expand(text, pick=lambda opts: max(opts, key=len))


def variants(text, cap=10000):
    """Сколько разных раскрытий даёт шаблон (оценка сверху, без учёта совпадений)."""
    t, n = _protect(text), 1
    while True:
        m = ALT.search(t)
        if not m:
            return min(n, cap)
        n *= len(m.group(1).split("|"))
        t = t[:m.start()] + "x" + t[m.end():]


def fields(template):
    return set(SLOT.findall(template))


# ---------- род ----------


def sex_of(value):
    """'Male'/'Female' (мост), 'м'/'ж', 'male'... -> 'm' / 'f' / None."""
    v = str(value or "").strip().lower()
    if v in ("male", "m", "м", "муж", "мужской", "1"):
        return "m"
    if v in ("female", "f", "ж", "жен", "женский", "0"):
        return "f"
    return None


def feminine(word, p):
    """Женская форма по записи слово(p): Был(а), дошёл(дошла), появился(лась), нашёл(шла), завёл(а)."""
    if len(p) >= 3 and p[:2] == word[:2].lower():                  # дошёл(дошла): полная форма
        return (word[0] + p[1:]) if word[0].isupper() else p
    i = word.rfind(p[0]) if len(p) >= 2 else -1
    if i > 0:                                                      # появился(лась), нашёл(шла)
        return word[:i] + p
    if word.endswith("ёл") and p[0] == "а":                        # завёл(а) -> завела
        return word[:-2] + "ел" + p
    return word + p                                                # Был(а), дорос(ла)


def gendered(text, sex=None, peer_sex=None):
    """Род: <м/ж> и <@м/ж> (собеседник), слово(а) — говорящий. Неизвестный пол — запись «рад(а)»."""
    def form(m):
        s = peer_sex if m.group(1) else sex
        a, b = m.group(2), m.group(3)
        if s == "m":
            return a
        if s == "f":
            return b
        rest = b[len(a):] if a and b.startswith(a) else b
        return f"{a}\x03{rest}\x04"                    # «рад(а)»; скобки — после PAREN, чтобы род говорящего не тронул

    text = SEXFORM.sub(form, text)
    if sex == "m":
        text = PAREN.sub(lambda m: m.group(1), text)
    elif sex == "f":
        text = PAREN.sub(lambda m: feminine(m.group(1), m.group(2)), text)
    return text.replace("\x03", "(").replace("\x04", ")")


# ---------- склонение известных слов ----------

VELAR, HUSH = "кгх", "жшчщ"
ADJ = {   # тип основы -> род -> падеж (acc — неодушевлённый)
    "hard": {"m": ("ый", "ого", "ом", "ый"), "n": ("ое", "ого", "ом", "ое"), "f": ("ая", "ой", "ой", "ую")},
    "stressed": {"m": ("ой", "ого", "ом", "ой"), "n": ("ое", "ого", "ом", "ое"), "f": ("ая", "ой", "ой", "ую")},
    "velar": {"m": ("ий", "ого", "ом", "ий"), "n": ("ое", "ого", "ом", "ое"), "f": ("ая", "ой", "ой", "ую")},
    "hush": {"m": ("ий", "его", "ем", "ий"), "n": ("ее", "его", "ем", "ее"), "f": ("ая", "ей", "ей", "ую")},
    "soft": {"m": ("ий", "его", "ем", "ий"), "n": ("ее", "его", "ем", "ее"), "f": ("яя", "ей", "ей", "юю")},
}
CASES = ("nom", "gen", "loc", "acc")
# существительные мест: род, падежи, предлог места («на поле», «в лесу»)
NOUNS = {
    "поле": {"g": "n", "nom": "поле", "gen": "поля", "loc": "поле", "acc": "поле", "prep": "на"},
    "холм": {"g": "m", "nom": "холм", "gen": "холма", "loc": "холме", "acc": "холм", "prep": "на"},
    "луг": {"g": "m", "nom": "луг", "gen": "луга", "loc": "лугу", "acc": "луг", "prep": "на"},
    "лес": {"g": "m", "nom": "лес", "gen": "леса", "loc": "лесу", "acc": "лес", "prep": "в"},
    "роща": {"g": "f", "nom": "роща", "gen": "рощи", "loc": "роще", "acc": "рощу", "prep": "в"},
    "пустыня": {"g": "f", "nom": "пустыня", "gen": "пустыни", "loc": "пустыне", "acc": "пустыню", "prep": "в"},
    "пещера": {"g": "f", "nom": "пещера", "gen": "пещеры", "loc": "пещере", "acc": "пещеру", "prep": "в"},
    "подземелье": {"g": "n", "nom": "подземелье", "gen": "подземелья", "loc": "подземелье", "acc": "подземелье",
                   "prep": "в"},
    "канализация": {"g": "f", "nom": "канализация", "gen": "канализации", "loc": "канализации",
                    "acc": "канализацию", "prep": "в"},
    "муравейник": {"g": "m", "nom": "муравейник", "gen": "муравейника", "loc": "муравейнике", "acc": "муравейник",
                   "prep": "в"},
    "логово": {"g": "n", "nom": "логово", "gen": "логова", "loc": "логове", "acc": "логово", "prep": "в"},
    "пирамида": {"g": "f", "nom": "пирамида", "gen": "пирамиды", "loc": "пирамиде", "acc": "пирамиду", "prep": "в"},
    "шахта": {"g": "f", "nom": "шахта", "gen": "шахты", "loc": "шахте", "acc": "шахту", "prep": "в"},
    "башня": {"g": "f", "nom": "башня", "gen": "башни", "loc": "башне", "acc": "башню", "prep": "в"},
    "корабль": {"g": "m", "nom": "корабль", "gen": "корабля", "loc": "корабле", "acc": "корабль", "prep": "на"},
    "тропа": {"g": "f", "nom": "тропа", "gen": "тропы", "loc": "тропе", "acc": "тропу", "prep": "на"},
    "долина": {"g": "f", "nom": "долина", "gen": "долины", "loc": "долине", "acc": "долину", "prep": "в"},
    "берег": {"g": "m", "nom": "берег", "gen": "берега", "loc": "берегу", "acc": "берег", "prep": "на"},
}


def adj_kind(masc):
    if masc.endswith("ой"):
        return "stressed"
    if masc.endswith("ый"):
        return "hard"
    if masc.endswith("ий"):
        c = masc[-3:-2]
        return "velar" if c in VELAR else "hush" if c in HUSH else "soft"
    raise ValueError(f"прилагательное {masc!r}: нужна форма мужского рода на -ый/-ий/-ой")


def adjective(masc, gender, case):
    """Южный, n, loc -> Южном; Дальний, f, nom -> Дальняя; Затонувший, n, gen -> Затонувшего."""
    return masc[:-2] + ADJ[adj_kind(masc)][gender][CASES.index(case)]


def cap(text):
    """Прописная в начале — только у кириллицы (код карты и имя латиницей не трогаем)."""
    return text[:1].upper() + text[1:] if text and re.match(r"[а-яё]", text[0]) else text


def place_forms(noun, adjs=(), tail="", nouns=None):
    """Формы составного имени места: [прилагательные] существительное [хвост без склонения].

    -> {"nom", "gen", "loc" (с предлогом), "acc", "from" (с/из + род.)}; первое слово — с прописной.
    """
    n = (nouns or NOUNS)[noun]
    out = {}
    for case in CASES:
        words = [adjective(a.lower(), n["g"], case) for a in adjs] + [n[case]]
        out[case] = cap(" ".join(words)) + (f" {tail}" if tail else "")
    out["from"] = ("с " if n["prep"] == "на" else "из ") + out["gen"]
    out["loc"] = f"{n['prep']} {out['loc']}"
    return out


# ---------- коды карт в тексте ----------

TOWNS = ("prontera", "izlude", "geffen", "payon", "morocc", "alberta", "aldebaran", "yuno", "comodo", "umbala",
         "amatsu", "einbroch", "einbech", "lighthalzen", "hugel", "rachel", "veins", "moc_ruins", "pay_arche")
LOC_PREP = ("на", "в", "во")
GEN_PREP = ("до", "у", "от", "около", "возле", "мимо")
FROM_PREP = ("из", "с", "со")
PREPS = LOC_PREP + GEN_PREP + FROM_PREP
CODE = r"(?:[a-z][a-z0-9]*(?:_[a-z0-9]+)+|" + "|".join(t for t in TOWNS if "_" not in t) + ")"
MAP_RE = re.compile(r"(?:(?<![\w])(?P<prep>" + "|".join(p.capitalize() + "|" + p for p in PREPS) + r") )?"
                    r"(?<![\w\[:])(?P<code>" + CODE + r")(?![\w\]:])")
BEFORE_NOM = re.compile(r"(^|[:(«—,\-]\s*)$")               # после «:», «,», «—» — именительный (перечисление)
CARD_WORD = re.compile(r"карт[аеуы]\s$")


def code_forms(code):
    """Конструкция без склонения, когда имени места нет: «на карте X», «до карты X»."""
    return {"nom": code, "gen": f"карты {code}", "loc": f"на карте {code}", "acc": code, "from": f"с карты {code}"}


def maps_in_text(text, resolver=None, level=0):
    """Коды карт -> формы имени места. resolver(code) -> [формы] (лучшие первыми) или None.

    level — какой вариант из списка резолвера брать (0 — полный, дальше — короче); за пределами списка —
    конструкция «на карте X». Предлог «на/в» заменяется предлогом имени (в Южном лесу), «из/с» — «с/из».
    """
    def sub(m):
        code, prep = m.group("code"), m.group("prep")
        options = (resolver(code) if resolver else None) or []
        f = options[level] if level < len(options) else code_forms(code)
        before = text[:m.start()]
        if prep:
            low = prep.lower()
            if low in LOC_PREP:
                out = f["loc"]
            elif low in FROM_PREP:
                out = f["from"]
            else:
                out = f"{low} {f['gen']}"
            return cap(out) if prep[0].isupper() else out
        if f is not None and f["nom"] == code:
            return code
        if CARD_WORD.search(before):
            return f"«{f['nom']}»"
        if BEFORE_NOM.search(before):
            return f["nom"]
        return f["acc"]

    return MAP_RE.sub(sub, text)


def has_codes(text):
    return bool(MAP_RE.search(text or ""))


# ---------- словарь и слой реплик ----------

_SPEECH = None


def load_speech(path=None):
    """speech.json (кэш на процесс); нет или битый — пустой словарь: остаются фразы персон."""
    global _SPEECH
    if path is None and _SPEECH is not None:
        return _SPEECH
    try:
        data = json.loads(Path(path or SPEECH_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("speech.json не прочитан: %s — только фразы персон", e)
        data = {}
    if path is None:
        _SPEECH = data
    return data


def proper_start(text, proper):
    """Первое слово — имя или название (не опускать регистр после «Ну, »)."""
    m = re.match(r"[«\"]?([A-Za-zА-Яа-яЁё\-]+)", text)
    if not m:
        return True
    w = m.group(1)
    if not re.match(r"[А-ЯЁ][а-яё\-]*$", w):          # латиница, аббревиатура, цифры — не трогать
        return True
    return w in proper or text.startswith("«")


class Grammar:
    """Слой грамматики над фразами персоны. sex() и peer_sex(name) — функции (пол может прийти позже)."""

    def __init__(self, speech, persona, rng, cfg=None, sex=None, peer_sex=None):
        self.speech = speech or {}
        self.persona = persona or {}
        self._rng = rng                                # Random или функция -> Random (social.rng подменяют тесты)
        self.cfg = dict(DEFAULTS, **(cfg or {}))
        self.sex = sex or (lambda: sex_of(self.persona.get("sex")))
        self.peer_sex = peer_sex or (lambda name: None)
        self.resolver = None                           # places: ORG-084 подставляет places.forms
        self.proper = set(self.speech.get("proper") or [])
        self.topics = self.speech.get("topics") or {}
        self.tics = self.persona_tics()

    @property
    def rng(self):
        return self._rng() if callable(self._rng) and not hasattr(self._rng, "random") else self._rng

    def persona_tics(self):
        """Словечки: persona.speech_tics {open, tail}; иначе — общие + по сильным чертам (≥ 0.6)."""
        own = self.persona.get("speech_tics")
        if isinstance(own, dict) and (own.get("open") or own.get("tail")):
            return {"open": list(own.get("open") or []), "tail": list(own.get("tail") or [])}
        tics = self.speech.get("tics") or {}
        out = {"open": list((tics.get("base") or {}).get("open") or []),
               "tail": list((tics.get("base") or {}).get("tail") or [])}
        traits = self.persona.get("traits") or {}
        for trait, part in sorted((tics.get("traits") or {}).items()):
            if isinstance(traits.get(trait), (int, float)) and traits[trait] >= 0.6:
                out["open"] += part.get("open") or []
                out["tail"] += part.get("tail") or []
        return out

    def whimsy(self):
        v = (self.persona.get("traits") or {}).get("whimsy")
        return float(v) if isinstance(v, (int, float)) else 0.3

    def templates(self, key, facts):
        """Шаблоны темы из speech.json, все слоты которых есть в фактах."""
        pool = self.topics.get(key)
        if pool is None and "_" in key:
            pool = self.topics.get(key.split("_")[0] + "_*")
        return [t for t in pool or [] if fields(t) <= set(facts)]

    def render(self, template, facts, limit=None):
        """Шаблон -> текст (альтернативы, род, факты, карты). -> (текст, влез ли в limit) или (None, False)."""
        t = expand(template, self.rng)
        t = gendered(t, self.sex(), self.peer_sex(facts.get("name")))
        try:
            text = t.format(**facts)
        except (KeyError, IndexError, ValueError, AttributeError):
            return None, False
        text = " ".join(text.split())
        out = text
        for level in range(3):
            out = cap(maps_in_text(text, self.resolver, level))
            if limit is None or len(out) <= limit:
                return out, True
        out = cap(maps_in_text(text, None))
        return out, limit is None or len(out) <= limit

    def decorate(self, text, limit):
        """Словечко в начало или в конец по характеру; не влезло — без него."""
        p_open = self.cfg["tic_chance"] * (0.5 + self.whimsy())
        opens, tails = self.tics.get("open") or [], self.tics.get("tail") or []
        if opens and not INTERJECTION.match(text) and self.rng.random() < p_open:
            o = self.rng.choice(opens)
            body = text
            if o.endswith(",") and not proper_start(body, self.proper):
                body = body[:1].lower() + body[1:]
            cand = f"{o} {body}"
            if len(cand) <= limit:
                return cand
        if tails and text[-1:] in ".!" and self.rng.random() < self.cfg["tail_chance"]:
            cand = f"{text} {self.rng.choice(tails)}"
            if len(cand) <= limit:
                return cand
        return text

    def say(self, key, choice, facts, recent=()):
        """Реплика темы key: choice — фраза персоны (ротация social), facts — факты. Не повторяет recent."""
        try:
            plain = " ".join(longest(choice).format(**facts).split()) if choice is not None else ""
        except (KeyError, IndexError, ValueError, AttributeError):
            plain = ""
        limit = max(self.cfg["limit"], len(plain))
        pool = self.templates(key, facts)
        recent = set(recent or ())
        last = None
        for _ in range(max(1, int(self.cfg["tries"]))):
            text = None
            if pool and (choice is None or self.rng.random() < self.cfg["mix"]):
                text, ok = self.render(self.rng.choice(pool), facts, self.cfg["limit"])
                if not ok:
                    text = None
            if text is None and choice is not None:
                text, _ = self.render(choice, facts, limit)
            if text is None:
                continue
            text = self.decorate(text, limit)
            last = text
            if text not in recent:
                return text
        return last
