"""Связи между жителями: встреча с продолжением, друзья, забота (ORG-023, ORG-024, ORG-025). Правила.

Встреча с продолжением (ORG-023): после подтверждённой встречи (plans.py: meeting_confirmed) жители
не расходятся сразу:
    точка на карте охоты обоих — охотятся там вместе (карта на сессию для распорядка, группа — party.py);
    точка в городе — посидеть рядом TOGETHER_MIN минут и поговорить (social: прогулка отложена,
    разговор пары — сразу, без ожидания интервала).
    Запись в память — что выбрали; через TOGETHER_MIN проверка фактом: житель всё ещё рядом (по state.players).
Друзья (ORG-024): при отношении >= FRIEND_AFFINITY и видимом жителе — friend_request (не чаще раза в сутки);
    запрос жителя тело принимает само (brainBridge, хук friend_request, только жители из residents);
    «друзья» — только по списку друзей от сервера (state.friends).
Забота (ORG-025): друга не видел и не говорил с ним дольше MISS_HOURS — короткая весточка (social, тема
    hello); житель сменил профессию (по данным игры: job в state.players) — поздравление (тема congrats).
"""
import logging
import time

log = logging.getLogger("bonds")

TOGETHER_MIN = 15
FRIEND_AFFINITY = 3
MISS_HOURS = 24


class Bonds:
    def __init__(self, mind, clock=None):
        self.mind = mind
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.st = mind.mem.get("bonds") or {"friend_req": {}, "missed": {}, "jobs": {}}
        self.together = None            # (житель, до когда, карта)

    def save(self):
        self.mind.mem.set("bonds", self.st)

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "bonds", "event": kind, "text": text, **data})
        log.info("%s", text)

    def friends(self):
        return {f.get("name") for f in self.mind.state.get("friends") or [] if isinstance(f, dict)}

    def visible(self, name):
        return next((p for p in self.mind.state.get("players") or []
                     if isinstance(p, dict) and p.get("name") == name and p.get("x") is not None), None)

    # ---------- встреча с продолжением ----------

    async def after_meeting(self, plan):
        partner, pmap = plan["partner"], plan["map"]
        now = self.clock()
        r = self.mind.routine
        social = getattr(self.mind, "social", None)
        if r and pmap in self.mind.persona["hunt_maps"] and r.st.get("mode") == "hunt":
            r.st["prefer_map"] = pmap                     # эту сессию — там, где встретились
            r.last_sent = 0
            r.save()
            self.note("bonds_hunt_together", f"После встречи с {partner} охотимся вместе на {pmap}.", 3,
                      partner=partner, map=pmap)
        else:
            if social:
                social.st.setdefault("pairs", {})[partner] = 0          # поговорить сразу
                social.next_walk = now + TOGETHER_MIN * 60              # не уходить гулять
            self.note("bonds_sit_together", f"После встречи с {partner} посидим вместе и поговорим.", 3,
                      partner=partner, map=pmap)
        self.together = (partner, now + TOGETHER_MIN * 60, pmap)

    def check_together(self, now):
        if not self.together or now < self.together[1]:
            return
        partner, _, pmap = self.together
        self.together = None
        if self.visible(partner):
            self.note("bonds_together_done", f"Провели время с {partner} — по данным игры рядом.", 2, partner=partner)
            self.mind.mem.update_relation(partner, 1, "провели время вместе после встречи")
        else:
            self.note("bonds_together_lost", f"После встречи {partner} ушёл(а) — вместе не получилось.", 1,
                      partner=partner)

    # ---------- друзья и забота ----------

    async def tick(self):
        now = self.clock()
        state = self.mind.state
        if not self.mind.fresh_state or state.get("dead"):
            return
        self.check_together(now)
        friends = self.friends()
        for peer in sorted(self.mind.ctx.peers):
            rel = self.mind.mem.relation(peer) or {}
            seen = self.visible(peer)
            if (seen and peer not in friends and rel.get("affinity", 0) >= FRIEND_AFFINITY
                    and now - self.st["friend_req"].get(peer, 0) >= 86400):
                self.st["friend_req"][peer] = now
                self.save()
                await self.mind.execute([{"action": "friend_request", "to": peer}], source="bonds",
                                        reason=f"связи: предложить дружбу {peer}", protocol=True)
            await self.watch_job(peer, seen, now)
            await self.miss(peer, friends, now)

    async def watch_job(self, peer, seen, now):
        if not seen or not seen.get("job"):
            return
        old = self.st["jobs"].get(peer)
        self.st["jobs"][peer] = seen["job"]
        if old and old != seen["job"]:
            self.save()
            self.note("bonds_job_changed", f"{peer} стал(а) {seen['job']} (был(а) {old}) — по данным игры.", 4,
                      peer=peer, old=old, new=seen["job"])
            social = getattr(self.mind, "social", None)
            if social:
                await social.say(peer, "congrats", 4, now)

    async def miss(self, peer, friends, now):
        """Друг онлайн, а не виделись и не говорили давно — весточка (не чаще раза в MISS_HOURS)."""
        if peer not in friends:
            return
        online = any(f.get("name") == peer and f.get("online") for f in self.mind.state.get("friends") or [])
        last = max(self.mind.ctx.last.get(f"talk:{peer}", 0), self.mind.ctx.last.get(f"seen:{peer}", 0))
        if not online or now - last < MISS_HOURS * 3600 or now - self.st["missed"].get(peer, 0) < MISS_HOURS * 3600:
            return
        self.st["missed"][peer] = now
        self.save()
        social = getattr(self.mind, "social", None)
        if social:
            await social.say(peer, "hello", 1, now)
            self.note("bonds_missed", f"Давно не видел(а) {peer} — написал(а) весточку.", 1, peer=peer)
