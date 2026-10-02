"""Группа жителей и совместная жизнь (AUT-055–060, 066). Правила без LLM, тик 1 с.

Роли: лидер — житель с наименьшим именем (Arkady), группа всегда LR_<лидер>.
Формирование (AUT-055):
    лидер: нет группы — party_create; житель не в составе — party_invite (не чаще INVITE_GAP);
    участник: приглашение LR_<житель> принимает плагин brainBridge сразу (partyAuto не успевает отказать).
    «В группе» — только когда сервер прислал состав: state.party == LR_<лидер> и житель в party_members.
Совместный режим (AUT-058, 059):
    лидер шёпотом сообщает режим [party:hunt:<карта>] / [party:town:] — при смене и раз в ANNOUNCE с;
    участник с подтверждённой группой следует режиму лидера (распорядок спрашивает leader_wants());
    на охоте на одной карте участник идёт за лидером (follow).
    Участнику нужно восстановиться — [party:recover:], лидер тоже уходит отдыхать (темп по слабому).
Поводок (AUT-057): лидер на охоте ждёт участника (pause — не искать новых целей), если тот онлайн,
    жив, но на другой карте или дальше WAIT_DIST; не дольше WAIT_MAX, потом продолжает.
Разрыв (AUT-060): участник офлайн, HP 0 или вне группы — лидер не ждёт и не следует за ним.
Опасность (AUT-066): survival-событие danger у участника — [party:danger:] лидеру; лидер снимает
    ожидание и идёт к участнику (follow на HELP_SEC с), OpenKore бьёт нападающих на группу (attackAuto_party).
Поддержка (AUT-061): событие support от плагина (пакет сервера) — запись heal_confirmed с HP; отношение +1
    не чаще раза в час.
"""
import logging
import re
import time

log = logging.getLogger("party")

TAG = re.compile(r"\[party:(hunt|town|danger|recover|dead):([a-z0-9_]{0,16})\]")
CREATE_GAP = 300
INVITE_GAP = 300
ANNOUNCE = 300
LEADER_FRESH = 900        # режим лидера устаревает через 15 мин без повтора
WAIT_DIST = 12
WAIT_MAX = 180
HELP_SEC = 60
SIGNAL_GAP = 30           # срочные сигналы — свой лимит, не общий лимит болтовни (AUT-092)


def dist(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


class Party:
    def __init__(self, mind, cfg=None, clock=time.time):
        self.mind = mind
        self.cfg = cfg or {}
        self.clock = clock
        self.st = mind.mem.get("party") or {}
        self.last = {}                    # отметки времени действий (не в БД: после перезапуска можно повторить)
        self.waiting_since = None
        self.help_until = 0.0

    # ---------- роли и данные ----------

    @property
    def me(self):
        return self.mind.state.get("name") or self.mind.persona["name"]

    @property
    def leader(self):
        return min({self.me} | set(self.mind.ctx.peers))

    @property
    def is_leader(self):
        return self.me == self.leader

    @property
    def name(self):
        return f"LR_{self.leader}"

    def members(self):
        return [m for m in self.mind.state.get("party_members") or []
                if isinstance(m, dict) and m.get("name") in self.mind.ctx.peers]

    def member(self, name):
        return next((m for m in self.members() if m["name"] == name), None)

    def confirmed(self):
        return self.mind.state.get("party") == self.name and bool(self.members())

    def save(self):
        self.mind.mem.set("party", self.st)

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "party", "event": kind, "text": text, **data})
        log.info("%s", text)

    def due(self, key, gap, now):
        if now - self.last.get(key, 0) < gap:
            return False
        self.last[key] = now
        return True

    async def act(self, actions, reason, protocol=False):
        may_move = getattr(self.mind, "may_move", None)
        if may_move and any(a["action"] == "follow" for a in actions):
            ok, blocker = may_move("party")
            if not ok:
                log.info("группа: не двигаю тело — им владеет %s", blocker)
                return
        await self.mind.execute(actions, source="party", reason=reason, protocol=protocol)

    async def signal(self, to, kind, arg=""):
        await self.act([{"action": "whisper", "to": to, "text": f"[party:{kind}:{arg}]"}],
                       f"группа: сигнал {kind} {arg}".strip(), protocol=True)

    # ---------- тик ----------

    async def tick(self):
        now = self.clock()
        state = self.mind.state
        if not self.mind.fresh_state or not self.mind.ctx.peers or state.get("dead"):
            return
        await self.form(now, state)
        ok = self.confirmed()
        if ok != bool(self.st.get("confirmed")):
            self.st["confirmed"] = ok
            self.save()
            if ok:
                names = ", ".join(m["name"] for m in self.members())
                self.note("party_confirmed", f"Группа {self.name} подтверждена сервером: со мной {names}.", 3,
                          party=self.name, members=names)
            else:
                self.note("party_lost", f"Группы {self.name} больше нет по данным сервера.", 2, party=self.name)
        if not ok:
            self.waiting_since = None
            return
        if self.is_leader:
            await self.lead(now, state)
        else:
            await self.follow_leader(now, state)

    async def form(self, now, state):
        party = state.get("party")
        if self.is_leader:
            if not party and self.due("create", CREATE_GAP, now):
                await self.act([{"action": "party_create"}], f"группа: создаю {self.name}")
            elif party == self.name:
                inside = {m["name"] for m in self.members()}
                for peer in sorted(self.mind.ctx.peers - inside):
                    if self.due(f"invite:{peer}", INVITE_GAP, now):
                        await self.act([{"action": "party_invite", "to": peer}], f"группа: зову {peer}")
        elif party and party.startswith("LR_") and party != self.name and self.due("leave", CREATE_GAP, now):
            await self.act([{"action": "party_leave"}], f"группа: {party} — не группа лидера {self.leader}")

    # ---------- лидер ----------

    def my_mode(self):
        r = self.mind.routine
        if not r or not r.st:
            return None, ""
        return r.st.get("mode"), (r.hunt_map() if r.st.get("mode") == "hunt" else "")

    async def lead(self, now, state):
        mode, hmap = self.my_mode()
        if mode:
            key = f"{mode}:{hmap}"
            changed = key != self.st.get("announced")
            for m in self.members():
                if m.get("online") and (changed or self.due(f"announce:{m['name']}", ANNOUNCE, now)):
                    await self.signal(m["name"], mode, hmap)
                    self.last[f"announce:{m['name']}"] = now
            if changed:
                self.st["announced"] = key
                self.save()
        if now < self.help_until:
            return
        if self.help_until and state.get("follow"):
            self.help_until = 0.0
            await self.act([{"action": "unfollow"}], "группа: помощь участнику закончена")
        await self.leash(now, state, mode)

    def lagging(self, state):
        """Участник, которого стоит подождать: онлайн, жив (HP > 0), но далеко или на другой карте."""
        for m in self.members():
            if not m.get("online") or m.get("hp_pct") == 0:
                continue                                    # AUT-060: офлайн/мёртвого не ждём
            if m.get("map") and m["map"] != state.get("map"):
                return m["name"]
            if None not in (m.get("x"), state.get("x")) and \
                    dist(int(m["x"]), int(m["y"]), int(state["x"]), int(state["y"])) > WAIT_DIST:
                return m["name"]
        return None

    async def leash(self, now, state, mode):
        who = self.lagging(state) if mode == "hunt" else None
        if who and self.waiting_since is None:
            self.waiting_since = now
            await self.act([{"action": "pause"}], f"группа: жду {who} — отстал")
        elif self.waiting_since is not None and (not who or now - self.waiting_since >= WAIT_MAX):
            if who:
                self.note("party_wait_timeout", f"Ждал {who} {WAIT_MAX // 60} мин — продолжаю охоту.", 1, who=who)
            self.waiting_since = None
            await self.act([{"action": "resume"}], "группа: все рядом" if not who else "группа: не дождался")

    # ---------- участник ----------

    def leader_wants(self, now=None):
        """Для распорядка участника: ('hunt', карта) / ('town', '') от лидера, или None."""
        now = now or self.clock()
        lm = self.st.get("leader_mode")
        if self.is_leader or not lm or not self.st.get("confirmed") or now - lm["ts"] > LEADER_FRESH:
            return None
        return lm["mode"], lm.get("map", "")

    async def follow_leader(self, now, state):
        lm = self.leader_wants(now)
        lead = self.member(self.leader)
        same_map = lead and lead.get("online") and lead.get("map") == state.get("map")
        hunting = lm and lm[0] == "hunt" and (self.mind.routine is None or not self.mind.routine.in_town_mode)
        if hunting and same_map:
            if state.get("follow") != self.leader and self.due("follow", 30, now):
                await self.act([{"action": "follow", "to": self.leader}], f"группа: иду за {self.leader}")
        elif state.get("follow") == self.leader and self.due("unfollow", 30, now):
            await self.act([{"action": "unfollow"}], "группа: лидер не охотится рядом")

    async def need_recover(self):
        """Распорядок участника: мне нужно восстановиться — пусть лидер тоже отдохнёт."""
        if not self.is_leader and self.st.get("confirmed") and self.due("recover", SIGNAL_GAP, self.clock()):
            await self.signal(self.leader, "recover")

    # ---------- события ----------

    async def on_tag(self, sender, text):
        m = TAG.search(text or "")
        if not m or sender not in self.mind.ctx.peers:
            return
        kind, arg = m.groups()
        now = self.clock()
        if kind in ("hunt", "town") and sender == self.leader and not self.is_leader:
            self.st["leader_mode"] = {"mode": kind, "map": arg, "ts": now}
            self.save()
            log.info("лидер %s: %s %s", sender, kind, arg)
        elif kind == "danger" and self.is_leader:
            self.note("party_danger", f"{sender} в опасности — иду на помощь.", 2, who=sender)
            if self.waiting_since is not None:
                self.waiting_since = None
                await self.act([{"action": "resume"}], f"группа: {sender} в опасности")
            m2 = self.member(sender)
            if m2 and m2.get("map") == self.mind.state.get("map"):
                self.help_until = now + HELP_SEC
                await self.act([{"action": "follow", "to": sender}], f"группа: помогаю {sender}")
        elif kind == "dead":
            pm = getattr(self.mind, "postmortem", None)
            can = pm.can_resurrect() if pm else False
            # AUT-011: не обещать недоступное воскрешение — только факт и честное «не могу».
            self.note("party_member_dead", f"{sender} погиб на {arg or '?'}." +
                      ("" if can else " Воскресить не могу — нет навыка; он вернётся после возрождения."), 3,
                      who=sender, map=arg, can_resurrect=can)
            if self.waiting_since is not None:
                self.waiting_since = None
                await self.act([{"action": "resume"}], f"группа: {sender} погиб — не жду")
        elif kind == "recover" and self.is_leader:
            r = self.mind.routine
            if r and r.st and r.st.get("mode") == "hunt":
                self.note("party_recover", f"{sender} нужно восстановиться — отдыхаем вместе.", 2, who=sender)
                await r.to_town(now, rest_minutes=r.cfg.get("after_death_rest_minutes", 10))
                r.save()

    async def on_my_death(self, event):
        """Я погиб — сказать жителям группы (срочно, свой лимит)."""
        if self.st.get("confirmed") and self.due("dead", SIGNAL_GAP, self.clock()):
            for m in self.members():
                if m.get("online"):
                    await self.signal(m["name"], "dead", event.get("map") or "")

    async def on_danger(self, event):
        """Событие danger от плагина survival у меня — сказать лидеру (срочный сигнал, свой лимит)."""
        if not self.is_leader and self.st.get("confirmed") and self.due("danger", SIGNAL_GAP, self.clock()):
            await self.signal(self.leader, "danger")

    def on_support(self, event):
        if event.get("skill") != "AL_HEAL" or not event.get("amount"):
            return
        frm, to, amount = event.get("from"), event.get("to"), int(event.get("amount") or 0)
        me = self.me
        other = frm if to == me else to
        if frm == me and to == me:
            return                                            # самолечение — не событие группы
        self.mind.mem.add_event("heal_confirmed", {"from": frm, "to": to, "amount": amount})
        self.mind.write_decision({"type": "party", "event": "heal_confirmed", "from": frm, "to": to, "amount": amount})
        log.info("лечение подтверждено сервером: %s -> %s +%d HP", frm, to, amount)
        if other in self.mind.ctx.peers and self.due(f"heal_mem:{other}", 3600, self.clock()):
            text = (f"{frm} подлечил меня Heal на {amount} HP — по пакету сервера." if to == me
                    else f"Мой Heal подлечил {to} на {amount} HP — по пакету сервера.")
            self.mind.mem.remember(text, 3)
            self.mind.mem.update_relation(other, 1, "лечили друг друга в бою")
