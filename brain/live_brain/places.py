"""Имена мест (ORG-084, ТЗ Т-25): человеческие названия карт и собственные имена мест от жителей. Правила без LLM.

Словарь — brain/world/place_names.json (scripts/gen_place_names.py по атласу): «Южное поле Пронтеры», «Пещера
Пайона, ярус 2», падежи и короткая форма без города. Поверх — собственные имена, которые житель даёт местам по
фактам своей памяти (раз в check_minutes, по курсору событий):
    rescue — heal_confirmed ко мне при HP ≤ rescue_hp (карта — из события support того же жителя) -> «Холм спасения»;
    death  — свои смерти (died) + чужие death_report из шины на карте ≥ death_min -> «Гиблый холм»;
    found  — explore_found, и никто раньше не публиковал place_found этой карты -> «Холм Arkady» (первооткрыватель);
    meet   — meeting_confirmed на карте ≥ meet_min -> «Холм встреч»;
    mob    — побед над монстром из словаря speech.json → monsters ≥ mob_min -> «Холм Порингов».
Приоритет — в этом порядке. У карты одно имя, оно не меняется; имя, занятое другой картой, пропускается.
Слово места (land) — из словаря (у prt_fild08 — «холм»), род и падежи — grammar.place_forms.

Консенсус через шину мира (world_bus): своё имя — запись place_name {map, name, kind, forms, why} (важность 2,
новость для других и летопись); поддержка чужого имени — та же запись с adopt=true (важность 1). Житель
поддерживает чужое имя, если своего для карты нет и он знает место (есть свои события на этой карте) или дружит с
автором (affinity ≥ friend_affinity). Имя закреплено, когда его используют ≥ min_users разных жителей; у карты с
несколькими именами — больше пользователей, затем раньше. Без шины — только свои имена, без закрепления.

Речь: social.grammar.resolver = forms — коды карт в любой реплике звучат именем (закреплённое > своё > словарь;
в своём городе — короткая форма). Тема place: «Я зову одно место «Гиблый холм»: там я погиб.» (раз каждому жителю).
Летопись: humanize() — «погиб на prt_fild08» -> «погиб на Гиблом холме» (закреплённые имена и словарь).
Выключатель: BRAIN_DISABLE=places или "places": {"enabled": false} в goals.json — коды карт звучат «на карте X».
"""
import json
import logging
import time
from pathlib import Path

from . import grammar as gram
from . import world_bus

log = logging.getLogger("places")

NAMES_PATH = Path(__file__).resolve().parent.parent / "world" / "place_names.json"
DEFAULTS = {"enabled": True, "check_minutes": 10, "death_min": 3, "meet_min": 2, "mob_min": 150, "rescue_hp": 0.35,
            "min_users": 2, "friend_affinity": 3, "lookback_days": 30, "topic_chance": 0.5, "batch": 2000}
KINDS = ("rescue", "death", "found", "meet", "mob")
SCAN = ("died", "kill", "heal_confirmed", "explore_found", "meeting_confirmed")
NAME_MAX = 20
KV = "place_names"                    # kv "places" занят explorer (открытые места) — не путать
BASE_MAX = 22                         # словарное имя длиннее — фразы с {base} не звучат
PHRASES = {   # ≤ 60 символов: имя до NAME_MAX, пояснение до 24, словарное имя до BASE_MAX (тест)
    "place": ["«{place}» — {why}.",
              "Есть место «{place}»: {why}.",
              "Я зову {base} «{place}».",
              "Знаешь «{place}»? Это {base}."],
    "place_re": ["Красиво. Запомню это имя.", "Хорошее название, так и буду звать.", "Надо будет сходить посмотреть.",
                 "Запомню. Имена местам — это правильно."],
}
_NAMES = None
CHRONICLE_LINES = {                                     # летопись (chronicle.py): память жителя
    "place_named": lambda d: f"назвал(а) {where(d.get('map'))} «{d.get('name')}»: {d.get('why')}",
    "place_adopted": lambda d: f"зовёт {where(d.get('map'))} «{d.get('name')}», как {d.get('from')}",
}


def load_names(path=None):
    """place_names.json (кэш на процесс); нет файла — пустой словарь (коды карт — «на карте X»)."""
    global _NAMES
    if path is None and _NAMES is not None:
        return _NAMES
    try:
        data = json.loads(Path(path or NAMES_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("place_names.json не прочитан: %s", e)
        data = {}
    data.setdefault("maps", {})
    data.setdefault("towns", {})
    if path is None:
        _NAMES = data
    return data


def base_forms(names, code, town=None):
    """Словарные формы карты: в своём городе — [короткая, полная], иначе [полная, короткая]; город — [формы]."""
    if code in names.get("towns", {}):
        return [names["towns"][code]]
    m = names.get("maps", {}).get(code)
    if not m:
        return []
    return [m["short"], m["full"]] if town and m.get("town") == town else [m["full"], m["short"]]


def where(code):
    """Карта словарным именем (вин. падеж) — в строках о самом имени места код не заменяется именем."""
    forms = base_forms(load_names(), code)
    return forms[0]["acc"] if forms else code


def personal_forms(names, code, kind, who=None, mob=None):
    """Собственное имя места по виду события -> формы или None (нет карты в словаре / нет слова)."""
    m = names.get("maps", {}).get(code)
    if not m:
        return None
    land = m.get("land") or m.get("noun")
    if land not in gram.NOUNS:
        return None
    if kind == "death":
        f = gram.place_forms(land, ["Гиблый"])
    elif kind == "rescue":
        f = gram.place_forms(land, (), "спасения")
    elif kind == "meet":
        f = gram.place_forms(land, (), "встреч")
    elif kind == "found" and who:
        f = gram.place_forms(land, (), who)
    elif kind == "mob" and mob:
        f = gram.place_forms(land, (), mob)
    else:
        return None
    return f if len(f["nom"]) <= NAME_MAX else None


def consensus(rows, min_users=2):
    """Записи place_name шины [{bot, ts, data}] -> {карта: {"name", "forms", "users", "ts", "kind"}} закреплённых."""
    by = {}
    for r in sorted(rows, key=lambda r: (r.get("ts") or 0, r.get("id") or 0)):
        d = r.get("data") or {}
        code, name = d.get("map"), d.get("name")
        if not code or not name or not isinstance(d.get("forms"), dict):
            continue
        e = by.setdefault((code, name), {"name": name, "forms": d["forms"], "users": [], "ts": r.get("ts") or 0,
                                         "kind": d.get("kind")})
        if r.get("bot") and r["bot"] not in e["users"]:
            e["users"].append(r["bot"])
    out = {}
    for (code, _), e in sorted(by.items(), key=lambda kv: (-len(kv[1]["users"]), kv[1]["ts"])):
        if len(e["users"]) >= min_users and code not in out:
            out[code] = e
    return out


def humanize(text, resolver):
    """Коды карт в тексте -> имена с предлогами (летопись, дашборд): grammar.maps_in_text полными формами."""
    return gram.maps_in_text(text, resolver, 0)


def chronicle_resolver(lab_root, until=None, names=None, min_users=2):
    """Для летописи: закреплённые имена из шины state/shared/world.sqlite (до until) + полный словарь."""
    names = names if names is not None else load_names()
    rows = world_bus.read_period(Path(lab_root) / "state" / "shared" / "world.sqlite", 0,
                                 until if until is not None else time.time() + 1)
    fixed = consensus([r for r in rows if r["kind"] == "place_name"], min_users)

    def resolve(code):
        out = [fixed[code]["forms"]] if code in fixed else []
        m = names.get("maps", {}).get(code)
        if m:
            return out + [m["full"], m["short"]]
        return out + base_forms(names, code)
    return resolve


class Places:
    # реестр модулей (modules.py, W8): создание, тик
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "places", "places", "places", True, "world"
    TICK_ORDER = 215                    # после episodes (210), до collection (220)

    def __init__(self, mind, world=None, clock=None, names=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("places") or {}))
        self.clock = clock or (lambda: time.time())   # время при вызове (реплей подменяет)
        self.names = names if names is not None else load_names()
        self.monsters = gram.load_speech().get("monsters") or {}
        self.st = mind.mem.get(KV) or {}
        for key in ("own", "count", "mobs", "rescue", "found", "told", "used"):
            self.st.setdefault(key, {})
        self.fixed = {}
        self.next_check = 0.0
        social = getattr(mind, "social", None)
        g = getattr(social, "grammar", None)
        if g is not None:                                  # places: коды карт в речи -> имена мест
            g.resolver = self.forms
            g.proper |= self.proper_words()
        if social is not None and hasattr(social, "register_topic"):
            for key, pool in PHRASES.items():
                social.phrases.setdefault(key, pool)
            social.register_topic("place", self.facts, said=self.said, chance=self.cfg["topic_chance"])

    # ---------- данные ----------

    @property
    def me(self):
        return (getattr(self.mind, "state", None) or {}).get("name") or self.mind.persona.get("name")

    @property
    def bus(self):
        world = getattr(self.mind, "world", None)
        return getattr(world, "bus", None)

    def save(self):
        self.mind.mem.set(KV, self.st)

    def town(self):
        r = getattr(self.mind, "routine", None)
        try:
            return r.cfg["town"]["map"]
        except (AttributeError, KeyError, TypeError):
            return "prontera"

    def proper_words(self):
        """Первые слова названий — после словечка «Ну,» не опускать регистр («Ну, Южное поле…»)."""
        out = set()
        for m in self.names.get("maps", {}).values():
            for f in (m.get("full"), m.get("short")):
                if f:
                    out.add(f["nom"].split()[0])
        for t in self.names.get("towns", {}).values():
            out.add(t["nom"].split()[0])
        return out

    def forms(self, code):
        """Для grammar: [формы] лучшие первыми — закреплённое, своё, словарь (в своём городе — короткое)."""
        out = []
        if code in self.fixed:
            out.append(self.fixed[code]["forms"])
        elif code in self.st["own"]:
            out.append(self.st["own"][code]["forms"])
        return out + base_forms(self.names, code, self.town())

    def name(self, code):
        f = self.forms(code)
        return f[0]["nom"] if f else code

    # ---------- правила ----------

    def tick(self):
        now = self.clock()
        if now < self.next_check:
            return
        self.next_check = now + self.cfg["check_minutes"] * 60
        try:
            self.scan(now)
            rows = self.bus_rows(now)
            self.fixed = consensus(rows, self.cfg["min_users"])
            self.adopt(rows, now)
            self.decide(now, rows)
        except Exception as e:                       # noqa: BLE001 — сбой правил имён не роняет мозг
            log.warning("имена мест: %s", e)
        self.save()

    def scan(self, now):
        """Новые события памяти по курсору -> счётчики фактов на картах."""
        mem = self.mind.mem
        cur = self.st.get("cursor")
        if cur is None:                                   # первый запуск: история за lookback_days
            cur = 0
        since = now - self.cfg["lookback_days"] * 86400
        kinds = SCAN + ("support",)
        rows = mem.db.execute(f"SELECT id, ts, kind, data FROM events WHERE id > ? AND ts >= ? AND kind IN "
                              f"({', '.join('?' * len(kinds))}) ORDER BY id LIMIT ?",
                              (cur, since, *kinds, self.cfg["batch"])).fetchall()
        last_support = self.st.setdefault("support", {})
        me = self.me
        for _id, ts, kind, data in rows:
            cur = _id
            try:
                d = json.loads(data)
            except ValueError:
                continue
            code = d.get("map")
            if kind == "support":
                if d.get("to") == me and d.get("from") and d.get("map"):
                    last_support[d["from"]] = d["map"]
                continue
            if kind == "heal_confirmed":
                hp, hp_max = d.get("hp_before"), d.get("hp_max")
                frm = d.get("from")
                if (d.get("to") == me and frm and frm != me and isinstance(hp, (int, float)) and hp_max
                        and hp / hp_max <= self.cfg["rescue_hp"]):
                    code = last_support.get(frm) or (getattr(self.mind, "state", None) or {}).get("map")
                    if code:
                        self.st["rescue"].setdefault(code, frm)
                continue
            if not code:
                continue
            if kind == "died":
                self.bump(code, "death")
            elif kind == "meeting_confirmed":
                self.bump(code, "meet")
            elif kind == "explore_found":
                self.st["found"].setdefault(code, ts)
            elif kind == "kill" and d.get("monster") in self.monsters:
                mobs = self.st["mobs"].setdefault(code, {})
                mobs[d["monster"]] = mobs.get(d["monster"], 0) + 1
        self.st["cursor"] = cur
        if len(last_support) > 50:
            self.st["support"] = dict(list(last_support.items())[-50:])

    def bump(self, code, kind):
        c = self.st["count"].setdefault(code, {})
        c[kind] = c.get(kind, 0) + 1

    def bus_rows(self, now):
        bus = self.bus
        if bus is None:
            return []
        return bus.recent("place_name", since=now - world_bus.KEEP_DAYS * 86400, limit=1000)

    def others_deaths(self, now):
        bus = self.bus
        if bus is None:
            return {}
        out = {}
        for r in bus.recent("death_report", since=now - self.cfg["lookback_days"] * 86400, limit=1000):
            if r["bot"] != bus.bot and (r["data"] or {}).get("map"):
                out[r["data"]["map"]] = out.get(r["data"]["map"], 0) + 1
        return out

    def pioneer(self, code, rows_found):
        """Я первым открыл карту: в шине нет более ранней записи place_found другого жителя."""
        mine = self.st["found"].get(code)
        return mine is not None and not any(r["data"].get("map") == code and r["ts"] < mine for r in rows_found)

    def candidates(self, code, deaths_bus, rows_found):
        """Виды имени, условия которых выполнены, по приоритету."""
        c = self.st["count"].get(code) or {}
        out = []
        if code in self.st["rescue"]:
            out.append(("rescue", {"who": self.st["rescue"][code]}))
        if c.get("death", 0) + deaths_bus.get(code, 0) >= self.cfg["death_min"]:
            out.append(("death", {"own": c.get("death", 0)}))
        if code in self.st["found"] and self.pioneer(code, rows_found):
            out.append(("found", {"who": self.me}))
        if c.get("meet", 0) >= self.cfg["meet_min"]:
            out.append(("meet", {}))
        mobs = self.st["mobs"].get(code) or {}
        best = max(sorted(mobs), key=lambda k: mobs[k], default=None)
        if best and mobs[best] >= self.cfg["mob_min"]:
            out.append(("mob", {"mob": best}))
        return out

    def why(self, kind, info):
        """Пояснение имени в речи — уже в роде говорящего (и спасителя)."""
        social = getattr(self.mind, "social", None)
        sex = social.my_sex() if social is not None and hasattr(social, "my_sex") else None
        if kind == "rescue":
            who = info.get("who")
            psex = social.peer_sex(who) if social is not None and hasattr(social, "peer_sex") else None
            return gram.gendered(f"там {who} <@спас/спасла> меня", sex, psex)
        if kind == "death":
            return gram.gendered("там я погиб(ла)", sex) if info.get("own") else "там гибнут жители"
        if kind == "found":
            return gram.gendered("я тут <был первым/была первой>", sex)
        if kind == "meet":
            return "там мы встречаемся"
        if kind == "mob":
            return f"там полно {self.monsters[info['mob']]['gen_pl'].lower()}"
        return ""

    def taken(self, rows):
        """Имена, занятые картами: {имя: карта} (свои и из шины)."""
        out = {o["name"]: code for code, o in self.st["own"].items()}
        for r in rows:
            d = r.get("data") or {}
            if d.get("name") and d.get("map"):
                out.setdefault(d["name"], d["map"])
        return out

    def decide(self, now, rows):
        names = self.taken(rows)
        deaths_bus = self.others_deaths(now)
        rows_found = self.bus.recent("place_found", since=0, limit=1000) if self.bus is not None else []
        codes = (set(self.st["count"]) | set(self.st["mobs"]) | set(self.st["rescue"]) | set(self.st["found"])
                 | set(deaths_bus))
        for code in sorted(codes):
            if code in self.st["own"] or code not in self.names.get("maps", {}):
                continue
            for kind, info in self.candidates(code, deaths_bus, rows_found):
                monster = self.monsters.get(info.get("mob"), {}).get("gen_pl")
                forms = personal_forms(self.names, code, kind, who=info.get("who") if kind == "found" else None,
                                       mob=monster)
                if not forms or names.get(forms["nom"], code) != code:
                    continue
                self.give(code, kind, forms, self.why(kind, info), now)
                names[forms["nom"]] = code
                break

    def give(self, code, kind, forms, why, now):
        name = forms["nom"]
        self.st["own"][code] = {"name": name, "kind": kind, "forms": forms, "why": why, "ts": now, "by": self.me}
        base = (base_forms(self.names, code, self.town()) or [gram.code_forms(code)])[0]
        self.mind.mem.add_event("place_named", {"map": code, "name": name, "rule": kind, "why": why})
        self.mind.mem.remember(f"Я зову {base['acc']} «{name}»: {why}.", 2, kind="fact")
        self.mind.write_decision({"type": "places", "event": "named", "map": code, "name": name, "kind": kind})
        self.publish(code, name, kind, forms, why, adopt=False, now=now)
        log.info("имя места: %s -> «%s» (%s)", code, name, kind)

    def publish(self, code, name, kind, forms, why, adopt, now):
        bus = self.bus
        if bus is None:
            return
        try:
            bus.publish("place_name", {"map": code, "name": name, "kind": kind, "forms": forms, "why": why,
                                       "adopt": adopt}, 1 if adopt else 2, now=now)
            self.st["used"][f"{code}|{name}"] = now
        except Exception as e:                       # noqa: BLE001
            log.warning("шина: имя места не опубликовано: %s", e)

    def knows(self, code):
        row = self.mind.mem.db.execute("SELECT 1 FROM events WHERE data LIKE ? LIMIT 1",
                                       (f'%"map": "{code}"%',)).fetchone()
        return row is not None

    def adopt(self, rows, now):
        """Чужое имя: своего для карты нет и место знаю или автор — друг -> пользуюсь им (запись adopt в шину)."""
        bus = self.bus
        if bus is None:
            return
        for r in rows:
            d = r.get("data") or {}
            code, name, author = d.get("map"), d.get("name"), r.get("bot")
            if (not code or not name or author == bus.bot or code in self.st["own"] or d.get("adopt")
                    or not isinstance(d.get("forms"), dict) or f"{code}|{name}" in self.st["used"]):
                continue
            rel = self.mind.mem.relation(author) or {}
            if not (self.knows(code) or rel.get("affinity", 0) >= self.cfg["friend_affinity"]):
                continue
            self.st["own"][code] = {"name": name, "kind": d.get("kind"), "forms": d["forms"],
                                    "why": d.get("why") or "", "ts": now, "by": author, "adopted": True}
            self.mind.mem.add_event("place_adopted", {"map": code, "name": name, "from": author})
            self.mind.write_decision({"type": "places", "event": "adopted", "map": code, "name": name,
                                      "from": author})
            self.publish(code, name, d.get("kind"), d["forms"], d.get("why"), adopt=True, now=now)
        self.fixed = consensus(self.bus_rows(now), self.cfg["min_users"])

    # ---------- тема разговора ----------

    def facts(self, peer, now):
        """Тема place: своё (или закреплённое) имя места, которое этому жителю ещё не называл."""
        told = set(self.st["told"].get(peer) or [])
        for code, o in sorted(self.st["own"].items(), key=lambda kv: kv[1].get("ts") or 0):
            name = (self.fixed.get(code) or o)["name"]
            if name in told or o.get("adopted"):
                continue
            base = (base_forms(self.names, code, self.town()) or [gram.code_forms(code)])[0]
            out = {"place": name, "why": o.get("why") or "так уж повелось", "_id": name}
            if len(base["acc"]) <= BASE_MAX:
                out["base"] = base["acc"]
            return out
        return None

    def said(self, peer, facts, now):
        lst = [x for x in (self.st["told"].get(peer) or []) if x != facts["_id"]] + [facts["_id"]]
        self.st["told"][peer] = lst[-30:]
