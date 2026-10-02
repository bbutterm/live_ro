"""Гильдия жителей (ORG-052). Правила без LLM, тик 1 с. Выключено по умолчанию: goals.json "guild": {"enabled": false}.

Сервер (rAthena, upstream src/map/guild.cpp guild_create): гильдию создаёт команда клиента (пакет 0165,
OpenKore «guild create <имя>»); при battle_config guild_emperium_check (conf/battle/guild.conf, по умолчанию yes)
в рюкзаке нужен Emperium (714), он тратится при создании. Имя — до 23 символов (NAME_LENGTH 24), char-server
проверяет буквы по char_name_option/char_name_letters (upstream conf/char_athena.conf: 1 и «a-z A-Z 0-9 пробел»)
и уникальность (int_guild.cpp mapif_parse_CreateGuild). Подробно — docs/GUILD.md.

Основатель — один на мир, без переговоров: goals.json guild.founder или житель онлайн с наибольшей
общительностью (traits.sociability персоны), затем уровнем, затем по имени. Условия основания:
    не в гильдии, гильдии жителей ещё нет (шина мира guild_founded / приглашение основателя);
    Emperium в рюкзаке (state.emperium от моста) или guild.emperium_check = false (владелец выключил проверку
    на сервере — server/conf/optional/guild_no_emperium.txt);
    онлайн не меньше min_members жителей вместе со мной, к каждому отношение ≥ min_affinity и не в ссоре (society);
    взаимность — жители сами отвечают на шёпот [guild:ask:]: [guild:yes:] только при своём отношении к
    основателю ≥ min_affinity и не в ссоре. Набралось yes — guild_create.
Создана — только по пакету сервера: state.guild.name (OpenKore $char->{guild}{name}, пакет 016C) и мастер — я.
Приглашение (друзья: отношение ≥ min_affinity, не в ссоре, видимы рядом — OpenKore «guild request» ищет игрока
в зоне видимости): [guild:invite:<имя>] шёпотом -> житель шлёт телу guild_expect {name} (мост примет приглашение
этой гильдии сам, как группу LR_*: guildAutoDeny 1 отказывает через 3 с) и отвечает [guild:ready:] -> guild_invite.
Вступил — тоже по state.guild. События памяти guild_founded / guild_joined -> шина мира и хроника.
Чат гильдии (guild_say -> OpenKore «g <текст>»): приветствие раз в сутки, «кто в городе?», ответ на этот
вопрос, итоги дня вечером, приветствие нового члена. Не чаще CHAT_GAP, не больше CHAT_DAY в сутки.
"""
import json
import logging
import random
import re
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("guild")

TAG = re.compile(r"\[guild:(ask|yes|no|invite|ready):([A-Za-z0-9 ]{0,23})\]")
NAME_OK = re.compile(r"^[A-Za-z0-9 ]{1,23}$")      # rAthena: NAME_LENGTH 24 и char_name_letters upstream
EMPERIUM = 714
ASK_GAP = 1800            # спросить того же жителя не чаще
ASK_TTL = 900             # ответ «да» действует
CREATE_GAP = 600
INVITE_GAP = 300
READY_TTL = 240           # мост ждёт приглашения 300 с (EXPECT_SEC в brainBridge) — успеть раньше
REPLY_GAP = 60
CHAT_GAP = 600
CHAT_DAY = 8
NO_EMPERIUM_PAUSE = 86400

DEFAULTS = {"enabled": False, "emperium_check": True, "min_members": 3, "min_affinity": 2,
            "founder": None, "name": None, "min_level": 1}

NAME_TEMPLATES = ["Hearth of {town}", "{town} Circle", "{founder} Friends", "{town} Lanterns", "Fountain Kin",
                  "{founder} Hearth"]

PHRASES = {
    "hello": ["Всем привет!", "Привет, гильдия. Я на месте.", "Доброго дня всем."],
    "who": ["Кто в городе?", "Есть кто в городе?", "Кто сейчас в городе? Посидим?"],
    "here": ["Я в {town}.", "Я в городе, в {town}.", "Здесь, в {town}."],
    "day": ["Итог дня: побед {kills}, уровень {lv}.", "За день побед {kills}. Уровень {lv}.",
            "День прошёл: {kills} побед, {lv} уровень."],
    "welcome": ["Добро пожаловать, {who}!", "{who}, рады тебе.", "С нами теперь {who}. Привет!"],
}

CHRONICLE_LINES = {
    "guild_founded": lambda d: f"основал гильдию {d.get('name')} (по пакету сервера)",
    "guild_joined": lambda d: f"вступил в гильдию {d.get('name')} (мастер {d.get('master')})",
    "guild_left": lambda d: f"больше не в гильдии {d.get('name')}",
    "guild_create_failed": lambda d: f"не смог основать гильдию: {d.get('why')}",
}

CREATE_RESULT = {1: "уже в гильдии", 2: "имя гильдии занято", 3: "нет Emperium"}
INVITE_RESULT = {0: "уже в гильдии", 1: "отказался", 2: "согласился", 3: "гильдия полна"}


def clean_name(text):
    """Имя гильдии под ограничения rAthena: латиница, цифры, пробел; ≤ 23; без двойных пробелов."""
    text = re.sub(r"[_\-.]", " ", str(text or ""))                 # «_» нет в char_name_letters — пробел
    text = " ".join(re.sub(r"[^A-Za-z0-9 ]", "", text).split())
    return text[:23].rstrip()


def resident_traits(base=None):
    """Общительность жителей: brain/world/roster.json -> brain/personas/<persona>.json (traits.sociability)."""
    base = Path(base) if base else Path(__file__).resolve().parents[1]
    out = {}
    try:
        roster = json.loads((base / "world" / "roster.json").read_text(encoding="utf-8")).get("residents") or {}
    except (OSError, ValueError):
        roster = {}
    for bot, rec in roster.items():
        if not isinstance(rec, dict):
            continue
        try:
            p = json.loads((base / "personas" / f"{rec.get('persona') or bot}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            p = {}
        name = p.get("name") or rec.get("name")
        if name:
            out[name] = float((p.get("traits") or {}).get("sociability", 0.5))
    return out


class Guild:
    def __init__(self, mind, world=None, clock=None, rng=None, traits=None):
        self.mind = mind
        world = world or {}
        self.cfg = dict(DEFAULTS, **(world.get("guild") or {}))
        self.tz = timezone(timedelta(hours=world.get("timezone_offset_hours", 3)))
        self.town = (((world.get("routine") or {}).get("town") or {}).get("map")) or "prontera"
        self.clock = clock or (lambda: time.time())     # время при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.traits = traits if traits is not None else resident_traits()
        self.st = mind.mem.get("guild") or {}
        self.st.setdefault("yes", {})
        self.st.setdefault("ready", {})
        self.st.setdefault("chat", {})
        self.last = {}                                    # отметки действий (не в БД)
        self.chat_times = []
        self.pending_welcome = []                         # новые члены гильдии (по state) — поприветствовать

    # ---------- общее ----------

    @property
    def me(self):
        return self.mind.state.get("name") or self.mind.persona["name"]

    def save(self):
        self.mind.mem.set("guild", self.st)

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "guild", "event": kind, "text": text, **data})
        log.info("%s", text)

    def due(self, key, gap, now):
        if now - self.last.get(key, -1e18) < gap:
            return False
        self.last[key] = now
        return True

    def guild(self):
        g = self.mind.state.get("guild")
        return g if isinstance(g, dict) and g.get("name") else None

    def members(self):
        g = self.guild() or {}
        return {m.get("name"): m for m in g.get("members") or [] if isinstance(m, dict) and m.get("name")}

    def affinity(self, peer):
        return (self.mind.mem.relation(peer) or {}).get("affinity", 0)

    def quarrel(self, peer):
        society = getattr(self.mind, "society", None)
        return bool(society and society.quarrel(peer))

    def friendly(self, peer):
        return (peer in self.mind.ctx.peers and self.affinity(peer) >= self.cfg["min_affinity"]
                and not self.quarrel(peer))

    def visible(self, name):
        return any(isinstance(p, dict) and p.get("name") == name for p in self.mind.state.get("players") or [])

    def online(self):
        """Жители онлайн по данным тела: друзья, группа, гильдия (флаг online) и видимые рядом."""
        st = self.mind.state
        names = {p.get("name") for p in st.get("players") or [] if isinstance(p, dict)}
        for key in ("friends", "party_members"):
            names |= {m.get("name") for m in st.get(key) or [] if isinstance(m, dict) and m.get("online")}
        names |= {n for n, m in self.members().items() if m.get("online")}
        return names & set(self.mind.ctx.peers)

    def level_of(self, name):
        if name == self.me:
            return self.mind.state.get("lv") or 0
        for p in self.mind.state.get("players") or []:
            if isinstance(p, dict) and p.get("name") == name and p.get("lv"):
                return p["lv"]
        known = (self.mind.mem.get("known_players") or {}).get(name) or {}
        return known.get("lv") or 0

    def sociability(self, name):
        if name == self.me:
            needs = getattr(self.mind, "needs", None)
            t = getattr(needs, "t", None) or self.mind.persona.get("traits") or {}
            return float(t.get("sociability", self.traits.get(name, 0.5)))
        return self.traits.get(name, 0.5)

    def founder(self):
        """Основатель: из goals.json или житель онлайн с наибольшей общительностью, затем уровнем, затем имя."""
        if self.cfg.get("founder"):
            return self.cfg["founder"]
        cands = self.online() | {self.me}
        return min(cands, key=lambda n: (-round(self.sociability(n), 2), -int(self.level_of(n) or 0), n))

    def has_emperium(self):
        return not self.cfg.get("emperium_check", True) or int(self.mind.state.get("emperium") or 0) > 0

    def guild_name(self):
        """Имя из персоны (guild_name), goals.json (guild.name) или шаблона; вариант растёт после «имя занято»."""
        n = int(self.st.get("name_try", 0))
        own = self.mind.persona.get("guild_name") or self.cfg.get("name")
        if own and n == 0 and clean_name(own):
            return clean_name(own)
        town = self.town.split("_")[0].capitalize()
        i = (zlib.crc32(self.me.encode()) + n) % len(NAME_TEMPLATES)
        name = clean_name(NAME_TEMPLATES[i].format(town=town, founder=self.me))
        if n >= len(NAME_TEMPLATES):
            name = clean_name(f"{name[:20]} {n}")
        return name or clean_name(f"Residents {n}")

    def known_guild(self):
        """Гильдия жителей уже есть: своё знание или новость шины мира (guild_founded другого жителя)."""
        if self.st.get("known"):
            return self.st["known"]
        for e in self.mind.mem.get("world_news") or []:
            if isinstance(e, dict) and e.get("kind") == "guild_founded" and (e.get("data") or {}).get("name"):
                self.st["known"] = e["data"]["name"]
                self.save()
                return self.st["known"]
        return None

    async def whisper(self, to, kind, arg=""):
        await self.mind.execute([{"action": "whisper", "to": to, "text": f"[guild:{kind}:{arg}]"}],
                                source="guild", reason=f"гильдия: {kind} {to}", protocol=True)

    # ---------- тик ----------

    async def tick(self):
        if not self.cfg.get("enabled"):
            return
        now = self.clock()
        state = self.mind.state
        if not self.mind.fresh_state or state.get("dead"):
            return
        self.watch(now)
        g = self.guild()
        if g is None:
            if self.founder() == self.me and not self.known_guild():
                await self.found(now)
            return
        if g.get("master") == self.me:
            await self.invite(now, g)
        await self.chat_tick(now)

    def watch(self, now):
        """Изменения гильдии — только по state (пакеты сервера через OpenKore)."""
        g = self.guild()
        name = g["name"] if g else None
        old = self.st.get("guild")
        if name != old:
            self.st["guild"] = name
            if name:
                master = g.get("master")
                members = sorted(self.members())
                if master == self.me:
                    self.st["known"] = name
                    self.note("guild_founded", f"Основал(а) гильдию {name} — сервер подтвердил.", 5,
                              name=name, members=", ".join(members))
                else:
                    self.st["known"] = name
                    self.note("guild_joined", f"Вступил(а) в гильдию {name} (мастер {master or '?'}) — по пакету сервера.",
                              4, name=name, master=master)
                self.st["seen_members"] = members
            elif old:
                self.note("guild_left", f"Больше не в гильдии {old} — по данным сервера.", 3, name=old)
            self.st.pop("creating", None)
            self.save()
        elif g:
            cur = sorted(self.members())
            new = [n for n in cur if n not in (self.st.get("seen_members") or []) and n != self.me]
            if cur != self.st.get("seen_members"):
                self.st["seen_members"] = cur
                self.save()
            self.pending_welcome += new

    # ---------- основатель ----------

    def candidates(self):
        return sorted(p for p in self.online() if self.friendly(p))

    async def found(self, now):
        if now < self.st.get("no_emperium_until", 0) or not self.has_emperium():
            return
        if int(self.mind.state.get("lv") or 0) < int(self.cfg.get("min_level") or 1):
            return
        if now < self.st.get("yield_until", 0):
            return
        need = int(self.cfg["min_members"]) - 1
        cands = self.candidates()
        if len(cands) < need:
            return
        yes = [p for p in cands if now - self.st["yes"].get(p, -1e18) < ASK_TTL]
        if len(yes) >= need:
            creating = self.st.get("creating")
            if creating and now - creating["ts"] < CREATE_GAP:
                return
            name = self.guild_name()
            self.st["creating"] = {"name": name, "ts": now}
            self.save()
            self.mind.write_decision({"type": "guild", "event": "guild_create", "name": name, "yes": yes})
            await self.mind.execute([{"action": "guild_create", "name": name}], source="guild",
                                    reason=f"гильдия: основать {name} (согласны {', '.join(yes)})", protocol=True)
            return
        for p in cands:
            if p not in yes and self.due(f"ask:{p}", ASK_GAP, now):
                await self.whisper(p, "ask")

    async def invite(self, now, g):
        inside = set(self.members())
        for p in sorted(self.online()):
            if p in inside or not self.friendly(p):
                continue
            ready = self.st["ready"].get(p)
            if ready and now - ready < READY_TTL:
                if self.visible(p) and self.due(f"request:{p}", INVITE_GAP, now):
                    await self.mind.execute([{"action": "guild_invite", "to": p}], source="guild",
                                            reason=f"гильдия: зову {p} в {g['name']}", protocol=True)
                continue
            if self.visible(p) and self.due(f"invite:{p}", INVITE_GAP, now):
                await self.whisper(p, "invite", g["name"])

    # ---------- события ----------

    async def on_tag(self, sender, text):
        m = TAG.search(text or "")
        if not m or sender not in self.mind.ctx.peers or not self.cfg.get("enabled"):
            return
        kind, arg = m.groups()
        now = self.clock()
        if kind == "ask":
            if sender < self.me and self.guild() is None:
                self.st["yield_until"] = now + ASK_TTL        # два основателя: уступает тот, чьё имя дальше
            ok = self.guild() is None and self.friendly(sender)
            if self.due(f"reply:{sender}", REPLY_GAP, now):
                await self.whisper(sender, "yes" if ok else "no")
        elif kind == "yes":
            self.st["yes"][sender] = now
            self.save()
            log.info("%s согласен(на) в гильдию", sender)
        elif kind == "no":
            self.st["yes"].pop(sender, None)
            self.save()
        elif kind == "invite":
            name = clean_name(arg)
            if not name or self.guild() is not None or not self.friendly(sender):
                if self.due(f"reply:{sender}", REPLY_GAP, now):
                    await self.whisper(sender, "no")
                return
            self.st["known"] = name
            self.save()
            await self.mind.execute([{"action": "guild_expect", "name": name}], source="guild",
                                    reason=f"гильдия: жду приглашения {name} от {sender}", protocol=True)
            await self.whisper(sender, "ready")
        elif kind == "ready":
            self.st["ready"][sender] = now
            self.save()
            g = self.guild()
            if g and g.get("master") == self.me and self.visible(sender) and self.due(f"request:{sender}", INVITE_GAP, now):
                await self.mind.execute([{"action": "guild_invite", "to": sender}], source="guild",
                                        reason=f"гильдия: зову {sender} в {g['name']}", protocol=True)

    async def on_event(self, kind, event):
        if not self.cfg.get("enabled"):
            return
        now = self.clock()
        if kind == "guild_create_result":
            code = event.get("code")
            if code == 0:
                log.info("сервер: гильдия создана — жду состав в state")
                return
            why = CREATE_RESULT.get(code, f"код {code}")
            self.st.pop("creating", None)
            if code == 2:
                self.st["name_try"] = int(self.st.get("name_try", 0)) + 1
            elif code == 3:
                self.st["no_emperium_until"] = now + NO_EMPERIUM_PAUSE
            self.save()
            self.note("guild_create_failed", f"Гильдию основать не вышло: {why} (пакет сервера).", 2, why=why, code=code)
        elif kind == "guild_invite_result":
            code = event.get("code")
            self.mind.write_decision({"type": "guild", "event": "invite_result", "code": code,
                                      "text": INVITE_RESULT.get(code, f"код {code}")})
        elif kind == "chat_guild":
            who, text = str(event.get("from") or ""), str(event.get("text") or "")
            if who and who != self.me and re.search(r"кто\b.*\bгород", text.lower()):
                if self.mind.state.get("map") == self.town:
                    await self.say("here", town=self.town.capitalize())

    # ---------- чат гильдии ----------

    def local(self, now):
        return datetime.fromtimestamp(now, self.tz)

    async def say(self, topic, gap=0, **fmt):
        """Фраза в чат гильдии. True — отправлена. Редко: CHAT_GAP, CHAT_DAY, тема — не чаще gap."""
        if not self.guild():
            return False
        now = self.clock()
        self.chat_times = [t for t in self.chat_times if now - t < 86400]
        if (len(self.chat_times) >= CHAT_DAY or (self.chat_times and now - self.chat_times[-1] < CHAT_GAP)
                or now - self.st["chat"].get(topic, -1e18) < gap):
            return False
        own = (self.mind.persona.get("phrases") or {}).get(f"guild_{topic}")
        pool = own if isinstance(own, list) and own else PHRASES[topic]
        try:
            text = self.rng.choice(pool).format(**fmt)
        except (KeyError, IndexError, ValueError):
            return False
        self.chat_times.append(now)
        self.st["chat"][topic] = now
        self.save()
        await self.mind.execute([{"action": "guild_say", "text": text}], source="guild",
                                reason=f"гильдия: {topic}", protocol=True)
        return True

    async def chat_tick(self, now):
        if (self.guild() or {}).get("master") != self.me:
            self.pending_welcome = []                               # приветствует новых только мастер
        elif self.pending_welcome:
            if await self.say("welcome", who=self.pending_welcome[0]):
                self.pending_welcome = self.pending_welcome[1:]
            return
        h = self.local(now).hour
        if 1 <= h < 7:
            return                                                  # ночью молчим
        if await self.say("hello", gap=20 * 3600):
            return
        state = self.mind.state
        r = getattr(self.mind, "routine", None)
        in_town = state.get("map") == self.town and (r is None or not r.st or r.st.get("mode") != "hunt")
        if in_town and self.due("who_roll", 1800, now) and self.rng.random() < 0.3:
            if await self.say("who", gap=6 * 3600):
                return
        if 21 <= h < 24:
            kills = self.mind.mem.count_events("kill", now - 86400)
            await self.say("day", gap=20 * 3600, kills=kills, lv=state.get("lv") or "?")

    def summary(self):
        g = self.guild()
        if not g:
            return None
        return {"имя": g["name"], "мастер": g.get("master"),
                "онлайн": sorted(n for n, m in self.members().items() if m.get("online"))}
