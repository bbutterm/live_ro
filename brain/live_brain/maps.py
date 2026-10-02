"""Опыт по картам и знания о местах (AUT-030, 045, 073, 075, 076). Правила без LLM.

Статистика карт (kv map_stats): минуты охоты (жив, на карте), победы, смерти, опыт (рост exp_pct),
зени (изменение за время на карте). Выбор карты на сессию (AUT-045):
    исключённые после смертей/застреваний — не выбираются;
    карта с данными меньше EXPLORE_MIN минут — сначала попробовать (ограниченное исследование, AUT-075:
    только из hunt_maps характера, с тем же распорядком и правилами отступления);
    иначе — лучшая по очкам: победы/час − DEATH_PENALTY × смерти/час.
    Модель может предложить карту (prefer), но опыт и исключения важнее догадки.
Места (kv places): карта -> первый/последний раз, источник seen (был сам) или told (сказал житель,
с автором и временем). Слух не становится фактом: told не исключает карту и не меняет выбор (AUT-076).
Слухи v2 (ORG-031): виды, пересказы (hops), доверие и проверка опытом — rumors.py; здесь только след в places.
"""
import logging
import random
import time

log = logging.getLogger("maps")

EXPLORE_MIN = 20
DEATH_PENALTY = 100          # смерть в час «стоит» 100 очков (10% опыта): две смерти за сессию перевешивают богатую карту
MAX_GAP = 5


class MapStats:
    def __init__(self, mind, clock=time.time):
        self.mind = mind
        self.clock = clock
        self.last = None            # (ts, map, exp_pct, zeny)
        self.cache = None
        self.saved = 0.0

    def stats(self):
        if self.cache is None:
            self.cache = self.mind.mem.get("map_stats", {})
        return self.cache

    def save(self, st, throttle=False):
        """Тик копит в памяти процесса и пишет в SQLite не чаще раза в 30 с (ORG D7); события — сразу."""
        self.cache = st
        now = self.clock()
        if throttle and now - self.saved < 30:
            return
        self.saved = now
        self.mind.mem.set("map_stats", st)

    def tick(self, state, hunting):
        """Каждый тик распорядка: накопить время/опыт/зени на карте охоты."""
        now = self.clock()
        cur = (now, state.get("map"), state.get("exp_pct"), state.get("zeny"))
        prev, self.last = self.last, cur
        self.seen(state.get("map"), now)
        if not (hunting and prev and prev[1] == cur[1] and 0 < now - prev[0] <= MAX_GAP) or state.get("dead"):
            return
        st = self.stats()
        m = st.setdefault(cur[1], {"minutes": 0.0, "kills": 0, "deaths": 0, "exp": 0.0, "zeny": 0})
        m["minutes"] += (now - prev[0]) / 60
        if prev[2] is not None and cur[2] is not None and cur[2] >= prev[2]:
            m["exp"] += cur[2] - prev[2]                       # уровень-ап сбрасывает %, его не считаем
        if prev[3] is not None and cur[3] is not None:
            m["zeny"] += cur[3] - prev[3]
        self.save(st, throttle=True)

    def on_kill(self, hmap):
        self._bump(hmap, "kills")

    def on_death(self, hmap):
        self._bump(hmap, "deaths")

    def _bump(self, hmap, key):
        if not hmap:
            return
        st = self.stats()
        m = st.setdefault(hmap, {"minutes": 0.0, "kills": 0, "deaths": 0, "exp": 0.0, "zeny": 0})
        m[key] += 1
        self.save(st)

    def score(self, hmap):
        """ORG-010/D13: очки в час = опыт %·10 + зени/1000 + победы·0.05 − смерти·DEATH_PENALTY."""
        m = self.stats().get(hmap)
        if not m or m["minutes"] < EXPLORE_MIN - 1e-6:           # допуск на сложение дробных минут
            return None
        hours = m["minutes"] / 60
        return (10 * m["exp"] + m["zeny"] / 1000 + 0.05 * m["kills"] - DEATH_PENALTY * m["deaths"]) / hours

    def choose(self, maps, bans, level=None, needs=None, rng=None):
        """Карта на сессию. needs (needs.py) — характер: допустимый риск по смелости и шум по причудливости."""
        allowed = [m for m in maps if m not in bans] or list(maps)
        if level:
            allowed = self.safe_for_level(allowed, level, needs.risk_tolerance() if needs else None)
        rng = rng or random
        unexplored = [m for m in allowed if self.score(m) is None]
        if unexplored:                                          # любопытный пробует новое не по порядку списка
            pick = rng.choice(unexplored) if needs and rng.random() < needs.t["curiosity"] else unexplored[0]
            return pick, "мало опыта на карте — попробую"
        noise = needs.noise() if needs else 0.0
        scored = {m: self.score(m) * (1 + noise * rng.uniform(-1, 1)) for m in allowed}
        best = max(allowed, key=lambda m: scored[m])
        return best, f"лучшая по опыту и добыче: {self.score(best):.0f} очков/час"

    def safe_for_level(self, maps, level, max_risk=None):
        """Атлас мира (atlas.py): не идти туда, где монстры заведомо сильнее уровня. Нет атласа — без фильтра.
        max_risk — допустимый риск по характеру (смелость); по умолчанию — порог атласа."""
        try:
            from . import atlas
            a = atlas.default()
            ok, rejected = a.filter_hunt_maps(maps, level, **({"max_risk": max_risk} if max_risk else {}))
        except (OSError, ValueError, KeyError):
            return maps
        for name, why in rejected.items():
            log.info("карта %s не по уровню %s: %s", name, level, why)
        return ok or maps

    def advice(self, maps, level, job=None):
        """Подсказка о росте: все свои карты слишком лёгкие — какие места атлас считает подходящими."""
        try:
            from . import atlas
            a = atlas.default()
            easy = all((a.maps.get(m, {}).get("level") or {}).get("max", 0) < level - 10 for m in maps)
            if not easy:
                return None
            return [m["map"] if isinstance(m, dict) else m[0]
                    for m in a.suitable_maps(level, atlas.archetype(job) if job else "melee", top=3)]
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return None

    def summary(self):
        out = {}
        for name, m in self.stats().items():
            hours = max(m["minutes"] / 60, 1e-9)
            out[name] = {"минут": int(m["minutes"]), "побед_в_час": round(m["kills"] / hours, 1),
                         "смертей": m["deaths"], "опыт_%_в_час": round(m["exp"] / hours, 1),
                         "зени_в_час": int(m["zeny"] / hours)}
        return out

    # ---------- места ----------

    def seen(self, hmap, now=None):
        if not hmap:
            return
        now = now or self.clock()
        places = self.mind.mem.get("places", {})
        p = places.get(hmap) or {}
        if p.get("source") == "seen" and now - p.get("last", 0) < 600:
            return                                              # не писать в БД каждый тик
        # Обновить поля, а не заменить запись: слухи с авторами сохраняются (ORG D12, AUT-076).
        p.update(source="seen", first=p.get("first", now), last=now)
        places[hmap] = p
        self.mind.mem.set("places", places)

    def told(self, hmap, author, what, hops=0, origin=None):
        """Слух от жителя: хранится с автором и временем, но не превращается в факт.
        ORG-031: hops — сколько раз пересказан, origin — первоисточник (доверие считает rumors.py)."""
        places = self.mind.mem.get("places", {})
        p = places.setdefault(hmap, {"source": "told"})
        p.setdefault("rumors", [])
        rec = {"from": author, "what": what, "ts": self.clock()}
        if hops:
            rec.update(hops=hops, origin=origin)
        p["rumors"] = (p["rumors"] + [rec])[-5:]
        self.mind.mem.set("places", places)
