"""Взгляд на собеседника (ORG-067, ТЗ Т-29). Правила без LLM.

Во время разговора и встречи житель поворачивается к собеседнику, а не стоит спиной. Действие моста
look_at {name} -> OpenKore «lookp <номер игрока>» (Commands.pm cmdLookPlayer -> Misc::lookAtPosition -> пакет смены
направления). rAthena (clif_parse_ChangeDir) меняет направление без проверок pc_cant_act — можно сидя и в чат-комнате —
и рассылает его соседям (clif_changed_dir, AREA_WOS); у наблюдателя OpenKore обновляет look.body актёра
(Receive.pm actor_look_at), мост кладёт его в state.players[].dir (0..7). Это и есть подтверждение: собеседник видит,
что я смотрю на него (faces), счётчик kv gaze.seen.

Когда поворачиваться: новый social_said (я сказал жителю), meeting_confirmed (встреча), входящая реплика [chat:] жителя
(эхо реестра после social). Условия: собеседник виден в state.players не дальше near клеток, я жив, на карте города
(распорядок: routine.town), не иду и не дерусь (activity), к этому жителю не чаще peer_gap_seconds, всего — не чаще
gap_seconds. Решения — в decisions.jsonl (type gaze), в память не пишутся (не шум).
Выключатель: BRAIN_DISABLE=gaze или goals.json "gaze": {"enabled": false}.
"""
import json
import math
import time

DEFAULTS = {"enabled": True, "check_seconds": 2, "near": 9, "gap_seconds": 20, "peer_gap_seconds": 60,
            "seen_gap_seconds": 60}
MOVING = ("route", "move", "attack", "follow", "NPC", "items_take", "take")
KINDS = {"social_said": "peer", "meeting_confirmed": "partner"}


def direction(src, dst):
    """Направление тела 0..7 от src к dst — как OpenKore Misc::lookAtPosition: 0 — север, 2 — запад, 4 — юг, 6 — восток."""
    dx, dy = int(dst["x"]) - int(src["x"]), int(dst["y"]) - int(src["y"])
    if dx == 0 and dy == 0:
        return None
    if dy == 0:
        deg = 270.0 if dx < 0 else 90.0
    else:
        deg = math.degrees(math.atan2(dx, dy))
        deg = deg + 360 if deg < 0 else deg
    return int(round((360 - deg) / 45)) % 8


def faces(player, me):
    """Игрок смотрит на меня: его dir равен направлению от него ко мне."""
    if not isinstance(player, dict) or player.get("dir") is None or player.get("x") is None or me.get("x") is None:
        return False
    want = direction(player, me)
    return want is not None and int(player["dir"]) == want


def dist(a, b):
    return max(abs(int(a["x"]) - int(b["x"])), abs(int(a["y"]) - int(b["y"])))


class Gaze:
    # реестр модулей (modules.py, W8): тик после social, эхо на [chat:] жителя
    ATTR, FEATURE, CONFIG, ENABLED = "gaze", "gaze", "gaze", True
    REQUIRES, ARGS = ("world", "peers", "social"), "world"
    TICK_ORDER = 75
    ECHO = [("social", r"\[chat:", "on_chat", 20)]

    def __init__(self, mind, world=None, clock=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("gaze") or {}))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.st = mind.mem.get("gaze") or {}
        self.st.setdefault("sent", 0)
        self.st.setdefault("seen", 0)
        self.last = 0.0                     # последний поворот
        self.peer_last = {}                 # житель -> последний поворот к нему
        self.seen_last = {}                 # житель -> когда последний раз видел его взгляд на себе
        self.next_check = 0.0

    def save(self):
        self.mind.mem.set("gaze", self.st)

    def cursor(self):
        if self.st.get("cursor") is None:   # первый запуск: историю не переносим
            self.st["cursor"] = self.mind.mem.db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
            self.save()
        return self.st["cursor"]

    def town_map(self):
        r = getattr(self.mind, "routine", None)
        town = getattr(r, "town", None) if r else None
        return town.get("map") if isinstance(town, dict) else None

    def player(self, state, name):
        for p in state.get("players") or []:
            if isinstance(p, dict) and p.get("name") == name and p.get("x") is not None:
                return p
        return None

    def why_not(self, peer, now, state):
        """None — можно повернуться к peer, иначе причина."""
        if state.get("dead") or state.get("x") is None:
            return "нет тела"
        if not state.get("map") or state.get("map") != self.town_map():
            return "не в городе"
        if str(state.get("activity") or "") in MOVING:
            return "в пути"
        p = self.player(state, peer)
        if not p or dist(p, state) > self.cfg["near"]:
            return "не виден рядом"
        if faces({"x": state["x"], "y": state["y"], "dir": state.get("dir")}, p):
            return "уже смотрю"
        if now - self.last < self.cfg["gap_seconds"]:
            return "недавно поворачивался"
        if now - self.peer_last.get(peer, 0) < self.cfg["peer_gap_seconds"]:
            return "к нему уже поворачивался"
        return None

    async def look(self, peer, why, now=None):
        now = self.clock() if now is None else now
        if peer not in self.mind.ctx.peers:
            return False
        state = self.mind.state
        reason = self.why_not(peer, now, state)
        if reason:
            return False
        self.last = now
        self.peer_last[peer] = now
        self.st["sent"] = int(self.st.get("sent") or 0) + 1
        self.save()
        self.mind.write_decision({"type": "gaze", "event": "look_at", "to": peer, "why": why})
        await self.mind.execute([{"action": "look_at", "name": peer}], source="gaze",
                                reason=f"взгляд: повернуться к {peer} ({why})", protocol=True)
        return True

    # ---------- подписки ----------

    async def on_chat(self, sender):
        """Эхо реестра: житель написал мне [chat:] — повернуться к нему."""
        if self.cfg.get("enabled", True) and self.mind.fresh_state:
            await self.look(sender, "мне говорят")

    async def tick(self):
        if not self.cfg.get("enabled", True) or not self.mind.fresh_state:
            return
        now = self.clock()
        if now < self.next_check:
            return
        self.next_check = now + self.cfg["check_seconds"]
        state = self.mind.state
        self.count_seen(now, state)
        cur = self.cursor()
        rows = self.mind.mem.db.execute(
            "SELECT id, kind, data FROM events WHERE id > ? AND kind IN ('social_said', 'meeting_confirmed') "
            "ORDER BY id", (cur,)).fetchall()
        if not rows:
            return
        self.st["cursor"] = rows[-1][0]
        self.save()
        targets = []
        for _id, kind, data in reversed(rows):            # свежие первыми, каждый собеседник — один раз
            try:
                peer = json.loads(data).get(KINDS[kind])
            except (ValueError, AttributeError):
                continue
            if peer and peer not in [t for t, _ in targets]:
                targets.append((peer, "разговор" if kind == "social_said" else "встреча"))
        for peer, why in targets:
            if await self.look(peer, why, now):
                return                                     # не чаще gap_seconds — один поворот за такт

    def count_seen(self, now, state):
        """Житель рядом смотрит на меня (его dir из state.players) — подтверждение взгляда, счётчик seen."""
        if state.get("x") is None:
            return
        for p in state.get("players") or []:
            if not isinstance(p, dict) or p.get("name") not in self.mind.ctx.peers:
                continue
            if p.get("x") is None or dist(p, state) > self.cfg["near"] or not faces(p, state):
                continue
            if now - self.seen_last.get(p["name"], 0) < self.cfg["seen_gap_seconds"]:
                continue
            self.seen_last[p["name"]] = now
            self.st["seen"] = int(self.st.get("seen") or 0) + 1
            self.save()
            self.mind.write_decision({"type": "gaze", "event": "seen", "from": p["name"]})
