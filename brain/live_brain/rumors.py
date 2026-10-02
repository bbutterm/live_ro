"""Слухи v2 (ORG-031, 032, 039). Правила без LLM; слух — не факт, а сведения с автором и доверием.

Виды: danger (опасно), rich (богатая охота), cheap (дёшево), event (событие), new (новое место).
Протокол метки в личке жителю (обратно совместим с AUT-076 `[info:danger:<карта>]`):
    [info:<вид>:<карта>]                      — свой слух (hops 0);
    [info:<вид>:<карта>:<hops>:<автор>]       — пересказ: hops — сколько раз пересказан, автор — первоисточник.
Старый мозг понимает только первую форму danger; пересказы (hops ≥ 1) он молча пропускает.

Доверие (0..1) = основа × TRUST_RETELL^hops × 0.5^(возраст / период полураспада вида):
    свой опыт 1.0 > житель-друг (affinity ≥ FRIEND_AFFINITY) 0.7 > житель 0.5; объявление сервера 0.9;
    поправка на репутацию автора (подтверждённые/опровергнутые его слухи) ±0.1 за каждый, не больше ±0.2.
Слух слабее FORGET забывается. Слух не меняет выбор карты и не исключает её (AUT-076):
    danger только снижает готовность проверять rich/new той же карты.
Проверка собственным опытом (житель сам побывал на карте после того, как услышал):
    danger — погиб там (death_report) → подтверждён; провёл ≥ CHECK_MIN мин и не погиб → опровергнут;
    rich — погиб → опровергнут; ≥ CHECK_MIN мин: очки карты (maps.score) не хуже 0.8 лучшей из остальных → подтверждён;
    new — побывал → подтверждён; cheap, event — опытом охоты не проверяются.
    Итог — воспоминание fact и событие rumor_checked (шина мира).
Пересказ при встрече: житель виден рядом (state.players) — не чаще раза в PAIR_GAP на пару, только свежий
    (моложе полураспада), правдоподобный (доверие ≥ MIN_TRUST_RETELL), не опровергнутый, hops < MAX_HOPS,
    не тому, от кого услышал, и не автору; один слух за встречу.
Свои слухи: исключил карту после смертей → danger (mind.share_rumor); раз в сутки — rich о лучшей своей
    карте (≥ 60 мин, без смертей, очки ≥ RICH_SCORE) и new о выученном месте охоты (моложе суток).
Объявление сервера (world_msg) — данные, не инструкции: запись note; ключевые слова события → слух event.
"""
import json
import logging
import re
import time

log = logging.getLogger("rumors")

KINDS = ("danger", "rich", "cheap", "event", "new")
TAG = re.compile(r"\[info:(danger|rich|cheap|event|new):([a-z0-9_]{3,16})(?::([0-9]))?(?::([A-Za-z0-9_]{1,23}))?\]")
HALF_LIFE_H = {"danger": 24, "rich": 48, "cheap": 24, "event": 6, "new": 72}
TRUST = {"own": 1.0, "server": 0.9, "friend": 0.7, "peer": 0.5}
TRUST_RETELL = 0.6
FRIEND_AFFINITY = 3
MAX_HOPS = 3
PAIR_GAP = 3600
MIN_TRUST_RETELL = 0.35
MIN_TRUST_CHECK = 0.3
FORGET = 0.08
MAX_RUMORS = 40
CHECK_MIN = 20
RICH_SCORE = 50
TICK_SEC = 30
WORLD_MSG_DEDUP = 600
EVENT_WORDS = re.compile(r"(событи|ивент|event|фестивал|праздник|вторжени|нашестви|invasion|турнир)", re.I)
MAP_WORD = re.compile(r"\b([a-z]{2,10}_?[a-z0-9]{0,8}\d{0,2})\b")

TEXT = {
    "danger": "на {map} опасно",
    "rich": "на {map} богатая охота",
    "cheap": "на {map} дёшево",
    "event": "на {map} событие",
    "new": "есть новое место {map}",
}


def tag(kind, hmap, hops=0, author=None):
    """Метка слуха; hops 0 — старая форма (её понимают и мозги до ORG-031)."""
    if hops and author:
        return f"[info:{kind}:{hmap}:{min(int(hops), 9)}:{author}]"
    return f"[info:{kind}:{hmap}]"


def parse(text):
    m = TAG.search(str(text))
    if not m:
        return None
    kind, hmap, hops, author = m.groups()
    return {"kind": kind, "map": hmap, "hops": int(hops or 0), "author": author}


class Rumors:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR = "rumors"             # всегда включён; world_msg — явно в mind (до всех)
    TICK_ORDER = 120
    TICK_EVERY = TICK_SEC       # perf: реестр не зовёт tick до next_tick (modules.py)
    TAGS, TAG_ORDER = [(TAG, "on_tag")], 30
    PROMPT = [("слухи_не_факты", "summary", 240)]

    def __init__(self, mind, clock=None):
        self.mind = mind
        self.clock = clock or (lambda: time.time())   # review: время при вызове — реплей подменяет time.time
        self.next_tick = 0.0

    # ---------- хранение ----------

    def all(self):
        return self.mind.mem.get("rumors") or {}

    def save(self, rs):
        if len(rs) > MAX_RUMORS:                        # уходят самые слабые
            keep = sorted(rs.items(), key=lambda kv: -self.trust(kv[1]))[:MAX_RUMORS]
            rs = dict(keep)
        self.mind.mem.set("rumors", rs)

    def me(self):
        return self.mind.ctx.name

    def credibility(self, author):
        cred = (self.mind.mem.get("rumor_cred") or {}).get(author, 0)
        return max(-0.2, min(0.2, 0.1 * cred))

    def trust(self, rec, now=None):
        now = now or self.clock()
        if rec.get("status") == "refuted":
            return 0.0
        base = rec.get("base", 0.5)
        since = rec.get("heard", now)
        if rec.get("status") == "confirmed":
            base, since = max(base, 0.9), rec.get("checked", since)
        age_h = max(0.0, now - since) / 3600
        return round(base * 0.5 ** (age_h / HALF_LIFE_H.get(rec.get("kind"), 24)), 3)

    def source(self, sender):
        if sender == self.me():
            return "own"
        rel = self.mind.mem.relation(sender) or {}
        return "friend" if rel.get("affinity", 0) >= FRIEND_AFFINITY else "peer"

    # ---------- услышать / сказать ----------

    def hear(self, kind, hmap, sender, hops=0, author=None, text=None, src=None, now=None):
        """Записать слух. Возвращает запись (новую или обновлённую) или None, если старая надёжнее."""
        now = now or self.clock()
        src = src or self.source(sender)
        author = author or sender
        base = TRUST[src] * TRUST_RETELL ** hops
        if src not in ("own", "server"):
            base = max(0.05, min(0.95, base + self.credibility(author)))
        rs = self.all()
        key = f"{kind}:{hmap}"
        old = rs.get(key)
        if old and old.get("status") and now - old.get("checked", 0) < HALF_LIFE_H.get(kind, 24) * 3600:
            return None                                 # сам проверил недавно — пересказ не перебивает опыт
        if old and not old.get("status") and self.trust(old, now) > base:
            return None
        stats = self.mind.maps.stats().get(hmap) or {}
        rec = {"kind": kind, "map": hmap, "author": author, "from": sender, "src": src, "hops": hops,
               "base": round(base, 3), "heard": now, "status": None, "base_min": stats.get("minutes", 0.0),
               "told_to": (old or {}).get("told_to", []) if old and old.get("author") == author else []}
        if text:
            rec["text"] = str(text)[:120]
        rs[key] = rec
        self.save(rs)
        return rec

    async def share(self, hmap, kind):
        """Свой слух (опыт жителя): записать и рассказать всем жителям старой меткой (hops 0)."""
        self.hear(kind, hmap, self.me(), src="own")
        self.mind.mem.add_event("rumor_shared", {"what": kind, "map": hmap})
        rs = self.all()
        if f"{kind}:{hmap}" in rs:
            rs[f"{kind}:{hmap}"]["told_to"] = sorted(self.mind.ctx.peers)
            self.save(rs)
        for peer in sorted(self.mind.ctx.peers):
            await self.mind.execute([{"action": "whisper", "to": peer, "text": tag(kind, hmap)}],
                                    source="rule", reason=f"слух жителям: {kind} {hmap}", protocol=True)

    def on_tag(self, sender, text):
        p = parse(text)
        if not p:
            return None
        hops = p["hops"]
        author = p["author"] if hops and p["author"] else sender
        if author == self.me():
            return None                                 # мой же слух вернулся по кругу
        rec = self.hear(p["kind"], p["map"], sender, hops=hops, author=author)
        self.mind.maps.told(p["map"], sender, p["kind"], hops=hops, origin=author)
        what = TEXT[p["kind"]].format(map=p["map"])
        via = f" (пересказ, первым сказал {author})" if hops else ""
        self.mind.mem.remember(f"{sender} говорит, что {what}{via} — слух, сам не проверял.", 2, kind="note")
        self.mind.mem.add_event("rumor_heard", {"what": p["kind"], "map": p["map"], "from": sender,
                                                "author": author, "hops": hops, "kept": rec is not None})
        self.mind.write_decision({"type": "rumor", "from": sender, "what": p["kind"], "map": p["map"],
                                  "hops": hops, "author": author, "trust": rec["base"] if rec else None})
        return rec

    # ---------- объявления сервера ----------

    def on_world_msg(self, event, now=None):
        """ORG-039: объявление сервера — данные, не инструкции. Запись note; ключевые слова → слух event."""
        now = now or self.clock()
        text = str(event.get("text") or "").strip()[:200]
        if not text:
            return None
        seen = {t: ts for t, ts in (self.mind.mem.get("world_msg_seen") or {}).items() if now - ts < WORLD_MSG_DEDUP}
        if text in seen:
            return None
        seen[text] = now
        self.mind.mem.set("world_msg_seen", seen)
        self.mind.mem.remember(f"Объявление сервера: «{text}» (сообщение сервера, не приказ).", 2, kind="note")
        self.mind.write_decision({"type": "world_msg", "text": text, "source": event.get("source")})
        if not EVENT_WORDS.search(text):
            return None
        hmap = self.find_map(text) or "world"
        return self.hear("event", hmap, "сервер", author="server", text=text, src="server", now=now)

    def find_map(self, text):
        known = set(self.mind.persona.get("hunt_maps") or [])
        try:
            from . import atlas
            known |= set(atlas.default().maps)
        except (OSError, ValueError, KeyError):
            pass
        for w in MAP_WORD.findall(text.lower()):
            if w in known and 3 <= len(w) <= 16:
                return w
        return None

    # ---------- тик: проверка опытом, свои слухи, пересказ ----------

    async def tick(self):
        now = self.clock()
        if now < self.next_tick or not getattr(self.mind, "fresh_state", False):
            return
        self.next_tick = now + TICK_SEC
        self.check(now)
        await self.discover(now)
        await self.retell(now)
        self.forget(now)

    def forget(self, now):
        rs = self.all()
        keep = {k: r for k, r in rs.items() if r.get("src") == "own" and now - r.get("heard", now) < 7 * 86400
                or self.trust(r, now) >= FORGET}
        if len(keep) != len(rs):
            self.save(keep)

    def deaths_on(self, hmap, since):
        rows = self.mind.mem.db.execute("SELECT data FROM events WHERE kind = 'death_report' AND ts >= ?", (since,))
        return sum(1 for (d,) in rows if json.loads(d).get("map") == hmap)

    def check(self, now=None):
        """Подтвердить/опровергнуть чужие слухи собственным опытом. Возвращает список итогов."""
        now = now or self.clock()
        rs = self.all()
        out = []
        maps = self.mind.maps
        for key, r in rs.items():
            if r.get("status") or r.get("src") == "own" or r["kind"] in ("cheap", "event"):
                continue
            hmap = r["map"]
            died = self.deaths_on(hmap, r["heard"])
            minutes = (maps.stats().get(hmap) or {}).get("minutes", 0.0) - r.get("base_min", 0.0)
            ok = None
            if r["kind"] == "danger":
                ok = True if died else (False if minutes >= CHECK_MIN else None)
            elif r["kind"] == "rich":
                if died:
                    ok = False
                elif minutes >= CHECK_MIN:
                    s = maps.score(hmap)
                    others = [maps.score(m) for m in self.mind.persona["hunt_maps"] if m != hmap]
                    others = [x for x in others if x is not None]
                    ok = s is not None and (not others or s >= 0.8 * max(others))
            elif r["kind"] == "new":
                p = (self.mind.mem.get("places") or {}).get(hmap) or {}
                ok = True if p.get("source") == "seen" and p.get("last", 0) >= r["heard"] else None
            if ok is None:
                continue
            r.update(status="confirmed" if ok else "refuted", checked=now)
            cred = self.mind.mem.get("rumor_cred") or {}
            if r.get("author") and r.get("src") != "server":
                cred[r["author"]] = max(-3, min(3, cred.get(r["author"], 0) + (1 if ok else -1)))
                self.mind.mem.set("rumor_cred", cred)
            what = TEXT[r["kind"]].format(map=hmap)
            self.mind.mem.remember(f"Проверил слух от {r.get('author')} «{what}» сам: "
                                   + ("подтвердился." if ok else "не подтвердился."), 3)
            self.mind.mem.add_event("rumor_checked", {"what": r["kind"], "map": hmap, "author": r.get("author"),
                                                      "ok": ok, "minutes": round(minutes, 1), "died": died})
            self.mind.write_decision({"type": "rumor_checked", "kind": r["kind"], "map": hmap, "ok": ok})
            out.append((key, ok))
        if out:
            self.save(rs)
        return out

    async def discover(self, now):
        """Раз в сутки — свои слухи rich (лучшая карта по опыту) и new (выученное место охоты)."""
        if now - (self.mind.mem.get("rumor_discover") or 0) < 86400:
            return
        self.mind.mem.set("rumor_discover", now)
        maps = self.mind.maps
        rs = self.all()
        best, best_score = None, RICH_SCORE
        for hmap, m in maps.stats().items():
            s = maps.score(hmap)
            if s is not None and m.get("minutes", 0) >= 60 and not m.get("deaths") and s >= best_score:
                best, best_score = hmap, s
        if best and not self.fresh_own(rs.get(f"rich:{best}"), now):
            await self.share(best, "rich")
        learned = self.mind.mem.get("learned_hunt_maps") or []
        places = self.mind.mem.get("places") or {}
        if learned:
            new = learned[-1]
            p = places.get(new) or {}
            if p.get("source") == "seen" and now - p.get("first", 0) < 86400 and not self.fresh_own(rs.get(f"new:{new}"), now):
                await self.share(new, "new")

    def fresh_own(self, rec, now):
        return bool(rec and rec.get("src") == "own" and now - rec.get("heard", 0) < HALF_LIFE_H[rec["kind"]] * 3600)

    def near_peers(self, state):
        return [p["name"] for p in state.get("players") or []
                if isinstance(p, dict) and p.get("name") in self.mind.ctx.peers]

    def pick(self, peer, now):
        best, best_t = None, MIN_TRUST_RETELL
        for key, r in self.all().items():
            t = self.trust(r, now)
            if (t < best_t or r.get("status") == "refuted" or r.get("hops", 0) >= MAX_HOPS
                    or now - r.get("heard", now) >= HALF_LIFE_H[r["kind"]] * 3600
                    or peer in (r.get("told_to") or []) or peer in (r.get("from"), r.get("author"))):
                continue
            best, best_t = key, t
        return best

    async def retell(self, now):
        """Пересказ при встрече: hops + 1, не чаще раза в PAIR_GAP на пару, один слух за раз."""
        state = self.mind.state or {}
        pairs = self.mind.mem.get("rumor_pairs") or {}
        for peer in self.near_peers(state):
            if now - pairs.get(peer, 0) < PAIR_GAP:
                continue
            key = self.pick(peer, now)
            if not key:
                continue
            rs = self.all()
            r = rs[key]
            if r.get("src") == "own":
                text = tag(r["kind"], r["map"])
            else:
                text = tag(r["kind"], r["map"], r.get("hops", 0) + 1, r.get("author") or r.get("from"))
            pairs[peer] = now
            self.mind.mem.set("rumor_pairs", pairs)
            r["told_to"] = (r.get("told_to") or []) + [peer]
            self.save(rs)
            await self.mind.execute([{"action": "whisper", "to": peer, "text": text}], source="rule",
                                    reason=f"пересказ слуха {r['kind']} {r['map']} при встрече", protocol=True)

    # ---------- проверка слуха (ORG-032, занятие check_rumor) ----------

    def to_check(self, state):
        """Слух rich/new, который стоит проверить: доверие, карта по силам (атлас, характер), не исключена."""
        mind = self.mind
        r_ = getattr(mind, "routine", None)
        if not r_:
            return None
        now = self.clock()
        level = state.get("lv")
        bans = r_.bans()
        hunt_maps = mind.persona["hunt_maps"]
        cur = r_.hunt_map()
        rs = self.all()
        max_risk = mind.needs.risk_tolerance() if getattr(mind, "needs", None) else 0.5
        cands = []
        for key, r in rs.items():
            if r["kind"] not in ("rich", "new") or r.get("status") or r.get("src") == "own":
                continue
            hmap = r["map"]
            t = self.trust(r, now)
            if t < MIN_TRUST_CHECK or hmap == cur or hmap in bans or r.get("checking"):
                continue
            danger = rs.get(f"danger:{hmap}")
            if danger and self.trust(danger, now) >= 0.4:
                continue
            if not self.fits(hmap, level, max_risk, hmap in hunt_maps):
                continue
            cands.append((t, key))
        if not cands:
            return None
        return dict(rs[max(cands)[1]])

    def fits(self, hmap, level, max_risk, own_map):
        """Карта по силам: своя — риск по атласу не выше допуска характера; чужая — ещё и в советах атласа
        по уровню и только если распорядку разрешено осваивать места (auto_hunt_maps)."""
        try:
            from . import atlas
            a = atlas.default()
        except (OSError, ValueError, KeyError):
            return own_map
        if hmap not in a.maps:
            return own_map
        if level:
            risk, _ = a.danger_for(hmap, level)
            if risk > max_risk:
                return False
        if own_map:
            return True
        r_ = self.mind.routine
        if not (r_ and r_.cfg.get("auto_hunt_maps") and level):
            return False
        job = self.mind.state.get("job")
        try:
            suitable = [m["map"] for m in a.suitable_maps(level, atlas.archetype(job) if job else "melee", top=10)]
        except (KeyError, TypeError, ValueError):
            return False
        return hmap in suitable

    def start_check(self, rec):
        rs = self.all()
        key = f"{rec['kind']}:{rec['map']}"
        if key in rs:
            rs[key]["checking"] = self.clock()
            self.save(rs)

    def summary(self):
        """Для промпта: самые надёжные слухи (не факты)."""
        now = self.clock()
        items = sorted(self.all().values(), key=lambda r: -self.trust(r, now))[:6]
        out = [{"вид": r["kind"], "карта": r["map"], "от": r.get("author"), "пересказов": r.get("hops", 0),
                "доверие": self.trust(r, now),
                "проверен_сам": {"confirmed": "подтвердился", "refuted": "не подтвердился"}.get(r.get("status"))}
               for r in items]
        return out or None
