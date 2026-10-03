"""«Помнишь?» — эпизоды пары и воспоминания вслух (ORG-055). Правила без LLM.

Эпизод — сжатая долгая память о том, что было с конкретным жителем: он меня вылечил, я его вылечил, подарок,
встреча, примирение, его гибель рядом. Источник — только события памяти (факты игры), которые уже пишут другие
модули; эпизоды переживают чистку событий (memory.prune, 90 дней) и звучат в речи без LLM (слабые места W2, W6).

Чтение: tick() по курсору kv episodes_cursor берёт новые события видов SOURCES (как society.py).
Хранение: kv episodes ≤ MAX_EPISODES; лишние вытесняются по вес × свежесть. Повтор того же вида с тем же жителем
в течение MERGE_SEC не плодит эпизод, а увеличивает счётчик times.
Вспомнить (тема remember реестра social.py, шанс CHANCE, может заменить приветствие): эпизод старше MIN_AGE,
который не вспоминали RECALL_GAP, с наибольшим вес × свежесть; в ссоре не вспоминают примирение и гибель.
Фразы — persona.phrases.remember_<вид> с {ago} («3 дня назад»), {days}, {map}; ответ — remember_re.
Выключатель: BRAIN_DISABLE=episodes или "episodes": {"enabled": false} в goals.json.
"""
import json
import logging
import time

log = logging.getLogger("episodes")

# событие памяти -> (вид эпизода, вес, ключ data со вторым участником, условие «это про меня»)
SOURCES = {
    "heal_confirmed": ("heal", 5, "from", "to"),       # он вылечил меня (запись только у получателя, ORG-002)
    "heal_given": ("healed", 3, "to", "from"),         # я вылечил его (party.py пишет при to != я)
    "gift_received": ("gift", 3, "peer", None),
    "gift_given": ("gift", 2, "peer", None),
    "meeting_confirmed": ("meet", 3, "partner", None),
    "society_reconciled": ("peace", 4, "peer", None),
    "party_member_dead": ("death", 3, "who", None),    # видел гибель товарища по группе
}
QUARREL_SKIP = ("peace", "death")
MAX_EPISODES = 200
MERGE_SEC = 86400
MIN_AGE = 2 * 86400
RECALL_GAP = 7 * 86400
HALF_LIFE_DAYS = 30
CHANCE = 0.35
TICK_SEC = 10
BATCH = 500
MAP_FRESH = 300            # карта события без поля map — из state, если событие свежее


def plural_days(n):
    n = int(n)
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} дня"
    return f"{n} дней"


class Episodes:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES = "episodes", "episodes", "episodes", True, ("peers",)
    TICK_ORDER = 210
    WARMUP = 60                 # warmup: необязательная инициатива — через 60–120 с после пробуждения (modules.py)
    TICK_EVERY = TICK_SEC       # perf: реестр не зовёт tick до next_tick (modules.py)

    def __init__(self, mind, clock=None):
        self.mind = mind
        self.clock = clock or (lambda: time.time())
        self.next_tick = 0.0

    @property
    def me(self):
        return (self.mind.state or {}).get("name") or self.mind.persona["name"]

    def all(self):
        return self.mind.mem.get("episodes") or []

    def save(self, eps):
        if len(eps) > MAX_EPISODES:
            now = self.clock()
            eps = sorted(eps, key=lambda e: -self.score(e, now))[:MAX_EPISODES]
            eps.sort(key=lambda e: e["ts"])
        self.mind.mem.set("episodes", eps)

    @staticmethod
    def score(ep, now):
        age_days = max(0.0, now - ep["ts"]) / 86400
        return ep["weight"] * (1 + 0.2 * (ep.get("times", 1) - 1)) * 0.5 ** (age_days / HALF_LIFE_DAYS)

    # ---------- чтение событий ----------

    def tick(self, now=None):
        now = now or self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + TICK_SEC
        mem = self.mind.mem
        cursor = mem.get("episodes_cursor") or 0
        kinds = tuple(SOURCES)
        rows = mem.db.execute(
            f"SELECT id, ts, kind, data FROM events WHERE id > ? AND kind IN ({', '.join('?' * len(kinds))}) "
            "ORDER BY id LIMIT ?", (cursor, *kinds, BATCH)).fetchall()
        if not rows:
            # soak: своих видов дальше нет — курсор на конец; иначе каждый тик пересматривались все kill/loot после
            # последнего «своего» события (стоимость тика росла с возрастом памяти: ×9 за 7 суток прогона)
            last = mem.db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
            if last > cursor:
                mem.set("episodes_cursor", last)
            return
        eps = self.all()
        added = 0
        for rid, ts, kind, data in rows:
            cursor = rid
            try:
                d = json.loads(data)
            except (TypeError, ValueError):
                continue
            if self.take(eps, kind, d, ts):
                added += 1
        self.save(eps)
        mem.set("episodes_cursor", cursor)
        if added:
            self.mind.write_decision({"type": "episodes", "event": "added", "count": added})

    def take(self, eps, kind, d, ts):
        """Событие -> эпизод (новый или +1 к недавнему того же вида). True, если что-то записано."""
        ep_kind, weight, who_key, me_key = SOURCES[kind]
        peer = d.get(who_key)
        if not peer or peer == self.me or peer not in self.mind.ctx.peers:
            return False
        if me_key and d.get(me_key) != self.me:
            return False
        hmap = d.get("map")
        state = self.mind.state or {}
        if not hmap and abs(time.time() - ts) <= MAP_FRESH and state.get("map"):
            hmap = state["map"]
        for ep in reversed(eps):
            if ep["kind"] == ep_kind and ep["peer"] == peer and ts - ep.get("last", ep["ts"]) < MERGE_SEC:
                ep["times"] = ep.get("times", 1) + 1
                ep["last"] = ts
                ep["map"] = ep.get("map") or hmap
                return True
        eps.append({"id": f"{ep_kind}:{peer}:{int(ts)}", "ts": ts, "last": ts, "kind": ep_kind, "peer": peer,
                    "map": hmap, "item": d.get("item"), "weight": weight, "times": 1, "recalled": None})
        return True

    # ---------- вспомнить ----------

    def with_peer(self, peer):
        return sorted((e for e in self.all() if e["peer"] == peer), key=lambda e: -e["ts"])

    def weight_with(self, peer, now=None):                                             # habit2: ORG-096
        """Сумма весов эпизодов пары с затуханием HALF_LIFE_DAYS (как score): мера общей истории для wed."""
        now = now or self.clock()
        return round(sum(self.score(e, now) for e in self.all() if e["peer"] == peer), 2)

    def quarrel(self, peer):
        society = getattr(self.mind, "society", None)
        return bool(society and society.quarrel(peer))

    def recall(self, peer, now=None):
        """Эпизод для «помнишь?»: старше MIN_AGE, не вспоминали RECALL_GAP; в ссоре — без примирения и гибели."""
        now = now or self.clock()
        angry = self.quarrel(peer)
        best = None
        for ep in self.with_peer(peer):
            if now - ep["ts"] < MIN_AGE or (ep.get("recalled") and now - ep["recalled"] < RECALL_GAP):
                continue
            if angry and ep["kind"] in QUARREL_SKIP:
                continue
            if best is None or self.score(ep, now) > self.score(best, now):
                best = ep
        return best

    def mark_recalled(self, ep_id, now=None):
        now = now or self.clock()
        eps = self.all()
        for ep in eps:
            if ep["id"] == ep_id:
                ep["recalled"] = now
        self.mind.mem.set("episodes", eps)

    def facts(self, peer, now):
        """Поставщик темы remember (social.register_topic)."""
        ep = self.recall(peer, now)
        if not ep:
            return None
        days = max(1, int((now - ep["ts"]) // 86400))
        out = {"_key": f"remember_{ep['kind']}", "_ep": ep["id"], "days": days, "ago": f"{plural_days(days)} назад"}
        if ep.get("map"):
            out["map"] = ep["map"]
        return out

    def said(self, peer, facts, now):
        self.mark_recalled(facts["_ep"], now)
        self.mind.mem.add_event("episode_recalled", {"peer": peer, "episode": facts["_ep"]})
