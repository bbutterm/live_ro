"""Темы разговора из жизни мира (ORG-066): встроенные поставщики реестра тем social.py. Правила без LLM.

Реестр — Social.register_topic(name, provider, reply): provider(peer, now) -> dict фактов или None (темы нет).
Факты — только то, что житель знает по данным игры и своей памяти; фраза темы берётся из persona.phrases[name]
(или из ключа "_key" в фактах), ответ собеседника — из phrases[reply] (по умолчанию <name>_re).

Встроенные темы (install):
    pet    — у меня есть питомец (state.pet.has и имя) -> {"pet": имя, "hunger": n};
    rumor  — свежий правдоподобный слух (rumors.py: доверие ≥ MIN_TRUST, не опровергнут, не свой опыт),
             который этому жителю ещё не говорили (kv social.told_rumor[peer]) и который не от него -> {"map", "kind", "what"};
    aim    — активная невыполненная цель недели (aims.py) -> {"aim": кратко, "pct": прогресс %};
    news   — последнее событие из kv world_news (шина мира) про третьего жителя (не собеседника и не меня),
             младше NEWS_HOURS, ещё не рассказанное этому жителю (kv social.told_news[peer]) -> {"who", "what"}.
    remember — «помнишь?» (episodes.py, ORG-055): эпизод пары → {ago, days, map}, фразы remember_<вид>;
             шанс CHANCE, может заменить приветствие.
Выключатель: BRAIN_DISABLE=topics или "topics": {"enabled": false} в goals.json (погода и эпизоды — свои).
"""
import json
import logging

from .episodes import CHANCE

log = logging.getLogger("topics")

MIN_TRUST = 0.35
NEWS_HOURS = 24
TOLD_KEEP = 30
WHAT = {"danger": "опасно", "rich": "богатая охота", "cheap": "всё дёшево", "event": "что-то затевается",
        "new": "новое место"}
# новость шины мира -> короткий пересказ (мужской, женский, неизвестный пол); прочие виды не пересказываются
NEWS = {
    "level_up": ("дорос до {level} уровня", "доросла до {level} уровня", "дорос(ла) до {level} уровня"),
    "death_report": ("погиб на {map}", "погибла на {map}", "погиб(ла) на {map}"),
    "job_changed": ("стал {to}", "стала {to}", "стал(а) {to}"),
    "pet_hatched": ("завёл питомца {name}", "завела питомца {name}", "теперь с питомцем {name}"),
    "pet_tamed": ("приручил {name}", "приручила {name}", "приручил(а) {name}"),
    "aim_done": ("выполнил цель недели", "выполнила цель недели", "выполнил(а) цель недели"),
    "hunt_map_new": ("охотится на {map}", "охотится на {map}", "охотится на {map}"),
    "map_banned": ("бросил охоту на {map}", "бросила охоту на {map}", "бросил(а) охоту на {map}"),
}
NEWS_MAX = 28
AIM_SHORT = {
    "level": lambda a: f"взять {a['base'] + a['target']} уровень",
    "job": lambda a: "сменить профессию",
    "zeny": lambda a: f"скопить {a['target']} зени",
    "new_map": lambda a: "найти новое место",
    "friend": lambda a: f"сдружиться с {a['peer']}",
    "help": lambda a: "помогать жителям",
}


def install(social, mind, world=None):
    """Зарегистрировать темы в реестре social: remember (свой выключатель episodes) и pet, rumor, aim, news."""
    episodes = getattr(mind, "episodes", None)                     # ORG-055: «помнишь?» (episodes.py)
    if episodes:
        social.register_topic("remember", episodes.facts, said=episodes.said, chance=CHANCE, opener=True)
    s = getattr(mind, "s", None)
    if s is not None and not s.feature("topics"):
        return
    if not ((world or {}).get("topics") or {}).get("enabled", True):
        return
    social.register_topic("pet", lambda peer, now: pet(mind), interest="pets")   # interest: ORG-103
    social.register_topic("rumor", lambda peer, now: rumor(mind, social, peer, now), said=told_rumor(social))
    social.register_topic("aim", lambda peer, now: aim(mind))
    social.register_topic("news", lambda peer, now: news(mind, social, peer, now), said=told_news(social))


# ---------- поставщики ----------

def pet(mind):
    p = (mind.state or {}).get("pet") or {}
    if not p.get("has") or not p.get("name"):
        return None
    out = {"pet": str(p["name"])[:24]}
    if isinstance(p.get("hungry"), int):
        out["hunger"] = p["hungry"]
    return out


def told(social, key, peer):
    return (social.st.setdefault(key, {})).get(peer) or []


def mark(social, key, peer, item):
    lst = [x for x in told(social, key, peer) if x != item] + [item]
    social.st[key][peer] = lst[-TOLD_KEEP:]


def rumor(mind, social, peer, now):
    rumors = getattr(mind, "rumors", None)
    if not rumors:
        return None
    done = set(told(social, "told_rumor", peer))
    best, best_t = None, MIN_TRUST
    for key, r in sorted(rumors.all().items()):
        t = rumors.trust(r, now)
        if (t < best_t or r.get("status") == "refuted" or r.get("src") == "own" or key in done
                or peer in (r.get("from"), r.get("author")) or peer in (r.get("told_to") or [])):
            continue
        best, best_t = (key, r), t
    if not best:
        return None
    key, r = best
    return {"map": r["map"], "kind": r["kind"], "what": WHAT.get(r["kind"], r["kind"]), "_id": key}


def told_rumor(social):
    return lambda peer, facts, now: mark(social, "told_rumor", peer, facts["_id"])


def aim(mind):
    aims = getattr(mind, "aims", None)
    items = (aims.st.get("items") if aims else None) or []
    for a in items:
        if a.get("done") or a.get("kind") not in AIM_SHORT:
            continue
        try:
            text = AIM_SHORT[a["kind"]](a)
        except (KeyError, TypeError):
            continue
        pct = int(100 * min(1.0, (a.get("progress") or 0) / max(1, a.get("target") or 1)))
        return {"aim": text, "pct": pct}
    return None


def news_id(e):
    return f"{e.get('ts')}|{e.get('bot')}|{e.get('kind')}"


def mentions(data, name):
    try:
        return name in json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        return True


def news(mind, social, peer, now):
    done = set(told(social, "told_news", peer))
    me = social.me
    for e in reversed(mind.mem.get("world_news") or []):
        who, kind, data = e.get("bot"), e.get("kind"), e.get("data") or {}
        if (who in (peer, me, None) or kind not in NEWS or now - (e.get("ts") or 0) > NEWS_HOURS * 3600
                or news_id(e) in done or mentions(data, peer)):
            continue
        sex = ((mind.mem.get("known_players") or {}).get(who) or {}).get("sex")
        form = NEWS[kind][{"Male": 0, "Female": 1}.get(sex, 2)]
        try:
            what = form.format(**data)
        except (KeyError, TypeError, IndexError, ValueError):
            continue
        if len(what) > NEWS_MAX:
            continue
        return {"who": who, "what": what, "_id": news_id(e)}
    return None


def told_news(social):
    return lambda peer, facts, now: mark(social, "told_news", peer, facts["_id"])
