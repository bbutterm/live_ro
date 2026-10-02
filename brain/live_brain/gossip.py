"""Сплетни о жителях и репутация (ORG-056), история и остывание отношений (W5, W6). Правила без LLM.

Сплетня — не выдумка, а пересказ ФАКТА о другом жителе (не обо мне и не о постороннем игроке) с источником и
доверием, как слух v2 о карте (rumors.py). Откуда факты:
    своя память (по курсору kv gossip.cursor): heal_confirmed (меня лечил X) — kind/heal; gift_received — kind/gift;
        trade_sold, trade_bought — kind/fair; trade_debt — mean/debt; society_relation_drop — mean/drop;
    шина мира (события других жителей, курсор kv gossip.bus_cursor): level_up, job_changed, place_found — brave;
        card_found, pet_hatched — luck; heal_confirmed (лекарь data.from), gift_given — kind;
    пересказ жителя — метка [gossip:<вид>:<о_ком>:<hops>:<автор>:<что>] в личке (TAG).
Первый запуск берёт факты за последние seed_days (7) молча — без пересказа истории в журнал решений.

Хранение — kv gossip.known {"<о_ком>:<что>": запись}: одна история о жителе («Arkady лечит в бою») — одна запись,
сколько бы раз её ни пересказали; остаётся самая надёжная версия, рассказчики копятся в heard (им не пересказывают).
Доверие (0..1) = основа × 0.5^(возраст / half_life_days): основа — свой опыт 1.0, шина мира 0.8, друг 0.7,
житель 0.5, × TRUST_RETELL^hops, ± надёжность автора как рассказчика слухов (kv rumor_cred, ±0.2).
Слабее forget — забывается; записей не больше max_known (уходят слабые).

Репутация жителя в моих глазах: rep = Σ знак × доверие × (1 + 0.5·min(n − 1, 2)), −3..3 (знак: kind +1, mean −1,
brave +0.5, luck +0.25; n — сколько раз я сам видел факт). Сплетня меняет только rep, не affinity уже знакомых.
Первое впечатление: с жителем, отношение с которым появилось после запуска модуля (знакомство), affinity ±1 =
clamp(round(rep / 2), −1, 1) (половина — от нуля: rep 1 → +1) с причиной «по слухам: …».

Пересказ — только при встрече. Голосом — тема gossip реестра social.register_topic (ORG-066): фраза «Говорят, Arkady
лечит жителей в бою.», затем служебная метка (собеседник записывает факт). При LLM или без social — тихо, меткой
жителю рядом (как rumors.retell), не чаще pair_gap_minutes на пару. Кому нельзя: тому, о ком; автору; тем, от кого
слышал; кому уже говорил. Что нельзя: доверие < MIN_TRUST, hops ≥ MAX_HOPS, плохое (mean) о друге (affinity ≥ 3)
и вообще плохое при щедрости (generosity) ≥ 0.7. О себе житель сплетен не пересказывает и о себе их не хранит.

Отношения (W5): memory.update_relation пишет историю пары (kv relation_log: Δ, причина, итог) — причина не
перезаписывается. Остывание раз в сутки: нет общения cool_days (14) — affinity на 1 к нулю (не чаще раза в
cool_days), не ниже опоры min(2, хороших причин // 3); обида остывает так же, если нет идущей ссоры (society).
W6: KEEP_EVENTS дополнены (memory.py), история пары и сплетни — в kv, prune их не чистит.
Выключатель: BRAIN_DISABLE=gossip или goals.json "gossip": {"enabled": false}.
"""
import json
import logging
import random
import re
import time

log = logging.getLogger("gossip")

NAME = r"[A-Za-z0-9_]{1,23}"
TAG = re.compile(rf"\[gossip:(kind|mean|brave|luck):({NAME}):([0-9]):({NAME}):([a-z]{{3,6}})\]")
NAME_RX = re.compile(NAME)
SIGN = {"kind": 1.0, "mean": -1.0, "brave": 0.5, "luck": 0.25}
TRUST = {"own": 1.0, "news": 0.8, "friend": 0.7, "peer": 0.5}
TRUST_RETELL = 0.6
FRIEND_AFFINITY = 3
MAX_HOPS = 3
MIN_TRUST = 0.35
WHAT_MAX = 28
COOL_NOTE = "остывание:"
# что -> (вид, общее «о ком-то», о третьем {whom}, обо мне)
WHAT = {
    "heal": ("kind", "лечит жителей в бою", "подлечил(а) {whom} в бою", "подлечил(а) меня в бою"),
    "gift": ("kind", "выручает жителей вещами", "выручил(а) {whom} вещами", "выручил(а) меня вещами"),
    "fair": ("kind", "честно торгуется", "честно сторговался(лась) с {whom}", "честно со мной торговал(а)"),
    "debt": ("mean", "недоплачивает за товар", "недоплатил(а) {whom} за товар", "недоплатил(а) мне за товар"),
    "drop": ("mean", "подводит жителей", "подвёл(подвела) {whom}", "подвёл(подвела) меня"),
    "level": ("brave", "растёт в уровнях", "дорос(ла) до {level} уровня", None),
    "job": ("brave", "сменил(а) профессию", "стал(а) {job}", None),
    "place": ("brave", "ходит в новые места", "дошёл(шла) до {map}", None),
    "card": ("luck", "нашёл(шла) карту монстра", "нашёл(шла) карту {card}", None),
    "pet": ("luck", "завёл(вела) питомца", "завёл(вела) питомца {pet}", None),
}
OWN = {"heal_confirmed": ("heal", "from"), "gift_received": ("gift", "peer"), "trade_sold": ("fair", "peer"),
       "trade_bought": ("fair", "peer"), "trade_debt": ("debt", "peer"), "society_relation_drop": ("drop", "peer")}
NEWS = {"level_up": "level", "job_changed": "job", "place_found": "place", "card_found": "card",
        "pet_hatched": "pet", "heal_confirmed": "heal", "gift_given": "gift"}
DETAIL = {"level": ("level", "level"), "job": ("job", "to"), "place": ("map", "map"), "card": ("card", "name"),
          "pet": ("pet", "name")}
DEFAULTS = {"enabled": True, "tick_seconds": 30, "half_life_days": 7, "forget": 0.08, "max_known": 150,
            "seed_days": 7, "chance": 0.5, "pair_gap_minutes": 60, "cool_days": 14, "kind_generosity": 0.7}
PHRASES = {   # ≤ 60 символов без метки при имени ≤ 8 и «что» ≤ WHAT_MAX
    "gossip_own": ["Знаешь, {who} {what}.", "Скажу тебе: {who} {what}.", "А {who} {what}. Хороший!"],
    "gossip_own_mean": ["Знаешь, {who} {what}. Обидно.", "Скажу тебе: {who} {what}."],
    "gossip_kind": ["Говорят, {who} {what}. Добрая душа!", "Слышал(а), {who} {what}."],
    "gossip_mean": ["Слышал(а), {who} {what}. Увы.", "Говорят, {who} {what}. Не знаю..."],
    "gossip_brave": ["Слышал(а)? {who} {what}!", "Говорят, {who} {what}. Молодец!"],
    "gossip_luck": ["Везёт же: {who} {what}!", "Говорят, {who} {what}. Удача!"],
    "gossip_re": ["Вот как? Не знал(а).", "Буду знать.", "Ну и дела!", "Надо же!"],
}


def tag(kind, who, hops, author, what):
    return f"[gossip:{kind}:{who}:{min(int(hops), 9)}:{author}:{what}]"


def parse(text):
    m = TAG.search(str(text or ""))
    if not m:
        return None
    kind, who, hops, author, what = m.groups()
    if what not in WHAT or WHAT[what][0] != kind:
        return None
    return {"kind": kind, "who": who, "hops": int(hops), "author": author, "what": what}


def _loads(text):
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


class Gossip:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES, ARGS = "gossip", "gossip", "gossip", True, ("peers",), "world"
    TICK_ORDER = 125                  # после rumors (120)
    TAGS, TAG_ORDER = [(TAG, "on_tag")], 35   # после rumors [info:] (30)
    PROMPT = [("репутация", "summary", 245)]

    def __init__(self, mind, world=None, clock=None, rng=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("gossip") or {}))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.st = mind.mem.get("gossip") or {}
        for key in ("known", "pairs", "cooled"):
            self.st.setdefault(key, {})
        self.st.setdefault("impressed", [])
        self.next_tick = 0.0
        self.outbox = []                              # (кому, метка) — после фразы темы gossip
        social = getattr(mind, "social", None)
        if social is not None and hasattr(social, "register_topic"):
            for key, pool in PHRASES.items():
                social.phrases.setdefault(key, pool)
            social.register_topic("gossip", self.facts, said=self.said, chance=self.cfg["chance"])

    # ---------- данные ----------

    def save(self):
        self.mind.mem.set("gossip", self.st)

    def me(self):
        return (self.mind.state or {}).get("name") or self.mind.ctx.name

    def peers(self):
        return set(self.mind.ctx.peers) - {self.me()}

    def resident(self, name):
        return bool(name) and name in self.peers() and bool(NAME_RX.fullmatch(str(name)))

    def affinity(self, name):
        return (self.mind.mem.relation(name) or {}).get("affinity", 0)

    def trait(self, name):
        return (getattr(getattr(self.mind, "needs", None), "t", None) or {}).get(name, 0.5)

    def known(self):
        return self.st["known"]

    def trust(self, rec, now=None):
        now = now or self.clock()
        age_d = max(0.0, now - rec.get("heard", now)) / 86400
        return round(rec.get("base", 0.5) * 0.5 ** (age_d / self.cfg["half_life_days"]), 3)

    def credibility(self, author):
        rumors = getattr(self.mind, "rumors", None)
        return rumors.credibility(author) if rumors else 0.0

    def text(self, rec):
        """Что сделал житель — по-русски, с подробностью, если она известна мне (не передаётся меткой)."""
        _, generic, other, own = WHAT[rec["what"]]
        if rec.get("whom") == self.me() and own:
            return own
        try:
            out = other.format(**(rec.get("detail") or {}), whom=rec.get("whom")) if (
                rec.get("detail") or rec.get("whom")) else generic
        except (KeyError, IndexError, ValueError):
            out = generic
        return out if len(out) <= WHAT_MAX else generic

    # ---------- факты ----------

    def note_fact(self, who, what, src, now, whom=None, detail=None, quiet=False):
        """Свой опыт или новость шины мира о жителе who: запись (одна на «о ком:что»), n растёт."""
        if not self.resident(who):
            return None
        key = f"{who}:{what}"
        old = self.known().get(key)
        if old and old.get("src") in ("own", "news") and TRUST[old["src"]] > TRUST[src]:
            return old                                 # сам видел — новость шины того же не перебивает
        if old and old.get("src") == src:
            rec = old
            rec["n"] = min(9, rec.get("n", 1) + 1)     # снова то же — факт весомее
            rec["heard"] = max(rec.get("heard", 0), now)
            rec["told_to"] = []                        # новый случай — снова есть что рассказать
        else:
            rec = {"kind": WHAT[what][0], "what": what, "who": who, "src": src, "from": self.me(),
                   "author": self.me(), "hops": 0, "base": TRUST[src], "heard": now, "n": 1, "told_to": [],
                   "heard_from": sorted(set((old or {}).get("heard_from") or []) | {(old or {}).get("from")}
                                        - {None, self.me()})}
        if whom:
            rec["whom"] = whom
        if detail:
            rec["detail"] = detail
        self.known()[key] = rec
        if not quiet:
            self.mind.write_decision({"type": "gossip", "event": "fact", "src": src, "who": who, "what": what})
        return rec

    def collect_own(self, now):
        mem = self.mind.mem
        cur = self.st.get("cursor")
        quiet = cur is None
        if quiet:
            row = mem.db.execute("SELECT COALESCE(MAX(id), 0) FROM events WHERE ts < ?",
                                 (now - self.cfg["seed_days"] * 86400,)).fetchone()
            cur = row[0]
        kinds = list(OWN)
        rows = mem.db.execute(f"SELECT id, ts, kind, data FROM events WHERE id > ? AND kind IN "
                              f"({', '.join('?' * len(kinds))}) ORDER BY id LIMIT 500", (cur, *kinds)).fetchall()
        for _id, ts, kind, data in rows:
            what, field = OWN[kind]
            d = _loads(data)
            who = d.get(field)
            if kind == "heal_confirmed" and d.get("to") not in (None, self.me()):
                continue
            self.note_fact(who, what, "own", ts, whom=self.me(), quiet=quiet)
        if rows:
            self.st["cursor"] = rows[-1][0]
        else:                                          # нужных видов дальше нет — курсор на конец
            self.st["cursor"] = max(cur, mem.db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0])

    def collect_news(self, now):
        bus = getattr(getattr(self.mind, "world", None), "bus", None)
        if not bus:
            return
        cur = self.st.get("bus_cursor")
        quiet = cur is None
        try:
            if quiet:
                rows = bus.read(after_id=0, since=now - self.cfg["seed_days"] * 86400, others=True, limit=500)
            else:
                rows = bus.read(after_id=cur, others=True, limit=200)
            last = bus.last_id() if quiet and not rows else None
        except Exception as e:                        # общая БД занята — повтор в следующий раз
            log.warning("шина мира недоступна: %s", e)
            return
        for e in rows:
            what = NEWS.get(e["kind"])
            if not what:
                continue
            d = e.get("data") or {}
            who, whom = e["bot"], None
            if e["kind"] == "heal_confirmed":
                who, whom = d.get("from"), e["bot"]
            elif e["kind"] == "gift_given":
                whom = d.get("peer")
            if whom == who or (whom and whom != self.me() and not self.resident(whom)):
                whom = None                            # посторонний игрок не называется
            detail = None
            if what in DETAIL and d.get(DETAIL[what][1]) not in (None, ""):
                detail = {DETAIL[what][0]: str(d[DETAIL[what][1]])[:20]}
            self.note_fact(who, what, "news", e["ts"], whom=whom, detail=detail, quiet=quiet)
        if rows:
            self.st["bus_cursor"] = rows[-1]["id"]
        elif last is not None:
            self.st["bus_cursor"] = last

    # ---------- услышать ----------

    def on_tag(self, sender, text):
        p = parse(text)
        if not p:
            return None
        now = self.clock()
        me = self.me()
        who, author, hops = p["who"], p["author"], p["hops"]
        why = None
        if who == me:
            why = "обо мне"
        elif author == me:
            why = "моя же сплетня вернулась"
        elif not self.resident(who) or who == sender:
            why = "не о жителе"
        if why:
            self.mind.write_decision({"type": "gossip", "event": "ignored", "from": sender, "who": who, "why": why})
            return None
        src = "friend" if self.affinity(sender) >= FRIEND_AFFINITY else "peer"
        base = TRUST[src] * TRUST_RETELL ** hops
        base = round(max(0.05, min(0.95, base + self.credibility(author))), 3)
        key = f"{who}:{p['what']}"
        old = self.known().get(key)
        heard_from = sorted(set((old or {}).get("heard_from") or []) | {sender})
        kept = not (old and self.trust(old, now) >= base)
        if kept:
            self.known()[key] = {"kind": p["kind"], "what": p["what"], "who": who, "src": src, "from": sender,
                                 "author": author, "hops": hops, "base": base, "heard": now, "n": 1,
                                 "told_to": (old or {}).get("told_to") or [], "heard_from": heard_from}
        else:
            old["heard_from"] = heard_from
        rec = self.known()[key]
        what = WHAT[p["what"]][1]
        via = f" (пересказ, первым сказал {author})" if hops else ""
        self.mind.mem.remember(f"{sender} говорит, что {who} {what}{via} — сплетня, сам(а) не видел(а).", 1,
                               kind="note")
        self.mind.mem.add_event("gossip_heard", {"from": sender, "who": who, "what": p["what"], "kind": p["kind"],
                                                 "hops": hops, "author": author, "kept": kept})
        self.mind.write_decision({"type": "gossip", "event": "heard", "from": sender, "who": who,
                                  "what": p["what"], "hops": hops, "author": author,
                                  "trust": base if kept else None, "rep": self.reputation(who, now)})
        self.save()
        return rec if kept else None

    # ---------- репутация ----------

    def reputation(self, name, now=None):
        now = now or self.clock()
        total = 0.0
        for rec in self.known().values():
            if rec.get("who") == name:
                total += SIGN[rec["kind"]] * self.trust(rec, now) * (1 + 0.5 * min(rec.get("n", 1) - 1, 2))
        return round(max(-3.0, min(3.0, total)), 2)

    def reasons(self, name, n=3, now=None):
        now = now or self.clock()
        recs = sorted((r for r in self.known().values() if r.get("who") == name),
                      key=lambda r: -self.trust(r, now))[:n]
        out = []
        for r in recs:
            src = {"own": "сам(а) видел(а)", "news": "новости мира"}.get(r.get("src")) or (
                f"от {r.get('from')}" + (" (пересказ)" if r.get("hops") else ""))
            out.append(f"{name} {self.text(r)} — {src}")
        return out

    # ---------- рассказать ----------

    def may_tell(self, rec, peer, now):
        if peer in (rec.get("who"), rec.get("author"), rec.get("from")) or peer in (rec.get("heard_from") or []):
            return False
        if peer in (rec.get("told_to") or []) or rec.get("hops", 0) >= MAX_HOPS:
            return False
        if self.trust(rec, now) < MIN_TRUST:
            return False
        if rec["kind"] == "mean" and (self.affinity(rec["who"]) >= FRIEND_AFFINITY
                                      or self.trait("generosity") >= self.cfg["kind_generosity"]):
            return False
        return True

    def pick(self, peer, now):
        best, best_t = None, 0.0
        for key, rec in sorted(self.known().items()):
            if not self.may_tell(rec, peer, now):
                continue
            t = self.trust(rec, now)
            if t > best_t:
                best, best_t = key, t
        return best

    def tag_for(self, rec):
        if rec.get("src") in ("own", "news"):
            return tag(rec["kind"], rec["who"], 0, self.me(), rec["what"])
        return tag(rec["kind"], rec["who"], rec.get("hops", 0) + 1, rec.get("author") or rec.get("from"), rec["what"])

    def mark_told(self, peer, key, now):
        rec = self.known().get(key)
        if not rec:
            return None
        rec["told_to"] = (rec.get("told_to") or [])[-20:] + [peer]
        self.st["pairs"][peer] = now
        self.mind.mem.add_event("gossip_told", {"to": peer, "who": rec["who"], "what": rec["what"]})
        self.save()
        return rec

    def facts(self, peer, now):
        """Тема gossip (ORG-066): факт о третьем жителе, который этому собеседнику можно рассказать."""
        if now - self.st["pairs"].get(peer, 0) < self.cfg["pair_gap_minutes"] * 60:
            return None
        key = self.pick(peer, now)
        if not key:
            return None
        rec = self.known()[key]
        return {"who": rec["who"], "what": self.text(rec), "_id": key,
                "_key": (("gossip_own_mean" if rec["kind"] == "mean" else "gossip_own") if rec.get("src") == "own"
                         else f"gossip_{rec['kind']}")}

    def said(self, peer, facts, now):
        key = (facts or {}).get("_id")
        rec = self.mark_told(peer, key, now) if key else None
        if rec:
            self.outbox.append((peer, self.tag_for(rec)))

    def voiced(self):
        """Голос — тема реестра social (без LLM); иначе тихий пересказ меткой."""
        social = getattr(self.mind, "social", None)
        topics = getattr(social, "topics", None)
        return bool(social and isinstance(topics, dict) and "gossip" in topics and not social.llm())

    def near_peers(self):
        return sorted({p["name"] for p in (self.mind.state or {}).get("players") or []
                       if isinstance(p, dict) and p.get("name") in self.peers()})

    async def send(self, peer, text, why):
        await self.mind.execute([{"action": "whisper", "to": peer, "text": text}], source="rule",
                                reason=why, protocol=True)

    async def retell(self, now):
        for peer in self.near_peers():
            if now - self.st["pairs"].get(peer, 0) < self.cfg["pair_gap_minutes"] * 60:
                continue
            key = self.pick(peer, now)
            if not key:
                continue
            rec = self.mark_told(peer, key, now)
            await self.send(peer, self.tag_for(rec), f"сплетня жителю {peer} при встрече: {rec['who']} {rec['what']}")
            return

    # ---------- знакомство и остывание ----------

    def impressions(self, now):
        since = self.st.setdefault("since", time.time())
        for peer in sorted(self.peers()):
            if peer in self.st["impressed"]:
                continue
            rel = self.mind.mem.relation(peer)
            if not rel:
                continue
            self.st["impressed"].append(peer)
            if rel.get("first_seen", 0) < since:
                continue                               # знакомы до модуля — сплетни affinity не меняют
            rep = self.reputation(peer, now)
            half = rep / 2
            delta = max(-1, min(1, int(half + 0.5) if half >= 0 else -int(-half + 0.5)))   # 0.5 → 1
            if not delta:
                continue
            why = (self.reasons(peer, 1, now) or ["?"])[0]
            self.mind.mem.adjust_affinity(peer, delta, f"первое впечатление по слухам: {why}"[:120])
            self.mind.mem.remember(f"Познакомился(лась) с {peer}; по слухам {'хорош' if delta > 0 else 'плох'}"
                                   f" ({why}).", 2, kind="note")
            self.mind.mem.add_event("gossip_impression", {"peer": peer, "delta": delta, "rep": rep})
            self.mind.write_decision({"type": "gossip", "event": "impression", "peer": peer, "delta": delta,
                                      "rep": rep})

    def cool(self, now):
        """W5: без общения cool_days отношение остывает на 1 к нулю (раз в cool_days), не ниже опоры причин."""
        if now - self.st.get("cool_check", 0) < 86400:
            return
        self.st["cool_check"] = now
        span = self.cfg["cool_days"] * 86400
        society = getattr(self.mind, "society", None)
        for peer in sorted(self.peers()):
            rel = self.mind.mem.relation(peer)
            if not rel or not rel.get("affinity"):
                continue
            hist = self.mind.mem.relation_log(peer)
            contact = max([rel.get("last_seen", 0)] + [h["ts"] for h in hist
                                                         if not str(h.get("note", "")).startswith(COOL_NOTE)])
            if now - contact < span or now - self.st["cooled"].get(peer, 0) < span:
                continue
            aff = rel["affinity"]
            if aff > 0:
                floor = min(2, sum(1 for h in hist if h.get("delta", 0) > 0
                                   and not str(h.get("note", "")).startswith(COOL_NOTE)) // 3)
                if aff <= floor:
                    continue
                delta, why = -1, f"{COOL_NOTE} давно не общались"
            else:
                if society and society.quarrel(peer):
                    continue
                delta, why = 1, f"{COOL_NOTE} обида прошла"
            self.st["cooled"][peer] = now
            self.mind.mem.adjust_affinity(peer, delta, why)
            self.mind.mem.add_event("relation_cooled", {"peer": peer, "delta": delta, "affinity": aff + delta})
            self.mind.write_decision({"type": "gossip", "event": "cooled", "peer": peer, "delta": delta})

    def forget(self, now):
        known = self.known()
        drop = [k for k, r in known.items() if self.trust(r, now) < self.cfg["forget"]]
        for k in drop:
            del known[k]
        if len(known) > self.cfg["max_known"]:
            keep = sorted(known.items(), key=lambda kv: -self.trust(kv[1], now))[:self.cfg["max_known"]]
            self.st["known"] = dict(keep)

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        while self.outbox:
            peer, text = self.outbox.pop(0)
            await self.send(peer, text, f"сплетня жителю {peer} (метка после фразы)")
        if now < self.next_tick:
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        self.collect_own(now)
        self.collect_news(now)
        self.impressions(now)
        self.cool(now)
        self.forget(now)
        self.save()
        if getattr(self.mind, "fresh_state", False) and not self.voiced():
            await self.retell(now)

    def summary(self):
        """Для промпта: репутация жителей в моих глазах и почему (факты и пересказы, не мнения модели)."""
        now = self.clock()
        names = {r["who"] for r in self.known().values()}
        out = {}
        for name in sorted(names, key=lambda n: -abs(self.reputation(n, now)))[:4]:
            rep = self.reputation(name, now)
            if abs(rep) >= 0.3:
                out[name] = {"репутация": rep, "почему": self.reasons(name, 2, now)}
        return out or None

