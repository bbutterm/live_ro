"""Выбор занятия по мотивам (ORG-016, ORG-017). Каталог — данные brain/world/activities.json.

Раз в decide_minutes (на охоте — не раньше min_hunt_minutes от начала сессии) житель оценивает
доступные в текущем режиме занятия:
    оценка = Σ satisfies[мотив] × взвешенный мотив (needs.py) + инерция (если это текущее занятие)
             + шум характера (needs.noise) × U(-1, 1)
Переключение — только если лучшее лучше текущего на switch_margin (порог прерывания).
Журнал: decisions.jsonl type activity — выбранное, топ-3 с оценками и мотивы; kv activity — текущее.

Исполнители — существующие модули (каталог не дублирует тело):
    rest — ничего (тело садится само); stroll — social.walk_now(); socialize — social.visit(к жителю);
    service — routine.service_now(); hunt_early — распорядок: отдых закончен (выход — по HP-правилу);
    end_hunt — routine.to_town(); change_map — другая карта по опыту (maps.choose без текущей);
    keep_hunting — ничего.
Факт завершения (proof) за proof_minutes — иначе занятие «не удалось» (activity_failed), а не «сделано»:
    moved — позиция сменилась; peer_near — житель рядом; supply_better — вес меньше или зелий больше;
    on_hunt_map — на карте охоты; in_town — на карте города; map_changed — карта охоты другая.
Не вмешивается: сон, восстановление, план встречи, арбитр не даёт двигать тело, участник группы
(режим задаёт лидер — занятия, меняющие режим, недоступны: requires.no_leader).
"""
import json
import logging
import random
import time
from pathlib import Path

log = logging.getLogger("activity")

CATALOG = Path(__file__).resolve().parents[1] / "world" / "activities.json"
HEAL_IDS = ("569", "501", "502", "503", "504")


def dist(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


class Activities:
    def __init__(self, mind, path=CATALOG, clock=None, rng=None):
        self.mind = mind
        self.cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        self.catalog = self.cfg["activities"]
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.st = mind.mem.get("activity") or {}
        self.next_decide = 0.0

    # ---------- данные ----------

    def save(self):
        self.mind.mem.set("activity", self.st)

    def mode(self):
        r = self.mind.routine
        return r.st.get("mode") if r and r.st else None

    def potions(self, state):
        items = state.get("items") or {}
        return sum(int(items.get(i, 0) or 0) for i in HEAL_IDS)

    def visible_peer(self, state):
        for p in state.get("players") or []:
            if isinstance(p, dict) and p.get("name") in self.mind.ctx.peers and p.get("x") is not None:
                return p
        return None

    def requires_ok(self, req, state, needs):
        r = self.mind.routine
        social = getattr(self.mind, "social", None)
        for key, want in req.items():
            if key == "day":
                ok = not (social and social.is_night(self.clock()))
            elif key == "peer_visible":
                ok = self.visible_peer(state) is not None and social is not None
            elif key == "supply_min":
                ok = needs.get("supply", 0) >= want
            elif key == "hp_ok":
                ok = r.hp_ok(state)
            elif key == "budget_left":
                ok = r.st.get("hunted", 0) < r.st.get("budget", 0)
            elif key == "no_leader":
                party = getattr(self.mind, "party", None)
                ok = not (party and party.leader_wants())
            elif key == "other_maps":
                ok = len([m for m in self.mind.persona["hunt_maps"] if m not in r.bans()]) > 1
            else:
                ok = False                                   # неизвестное условие — занятие недоступно
            if bool(ok) != bool(want) and key != "supply_min":
                return False
            if key == "supply_min" and not ok:
                return False
        return True

    def blocked(self, state):
        m = self.mind
        r = m.routine
        if not r or not r.st or not m.fresh_state or state.get("dead"):
            return "нет данных"
        if r.st.get("mode") == "sleep" or r.st.get("recover") or r.st.get("blocked"):
            return "сон/восстановление/тупик"
        if m.plans.store.active():
            return "план встречи"
        may_move = getattr(m, "may_move", None)
        if may_move and not may_move("routine")[0]:
            return "телом владеет другая задача"
        if r.st.get("mode") == "town" and not r.st.get("arrived"):
            return "ещё иду в город"
        return None

    # ---------- выбор ----------

    def scores(self, state):
        needs_obj = self.mind.needs
        needs = needs_obj.weighted()
        noise = needs_obj.noise()
        mode = self.mode()
        current = self.st.get("name")
        now = self.clock()
        out = {}
        for name, a in self.catalog.items():
            if mode not in a["modes"]:
                continue
            if now - self.st.get("last", {}).get(name, 0) < a.get("cooldown_minutes", 0) * 60 and name != current:
                continue
            if not self.requires_ok(a.get("requires") or {}, state, needs):
                continue
            score = sum(w * needs.get(k, 0) for k, w in a["satisfies"].items())
            if name == current:
                score += self.cfg["inertia"]
            out[name] = round(score + noise * self.rng.uniform(-1, 1) * 0.3, 3)
        return out, needs

    async def tick(self):
        now = self.clock()
        state = self.mind.state
        await self.check_proof(now, state)
        if now < self.next_decide or self.blocked(state):
            return
        r = self.mind.routine
        if r.st.get("mode") == "hunt" and now - r.st.get("mode_since", now) < self.cfg["min_hunt_minutes"] * 60:
            return
        lo, hi = self.cfg["decide_minutes"]
        self.next_decide = now + self.rng.uniform(lo, hi) * 60
        scored, needs = self.scores(state)
        if not scored:
            return
        top = sorted(scored.items(), key=lambda kv: -kv[1])[:3]
        best, best_score = top[0]
        current = self.st.get("name")
        if current in scored and best != current and best_score - scored[current] < self.cfg["switch_margin"]:
            best = current                                    # порог прерывания: не дёргаться
        self.mind.write_decision({"type": "activity", "chosen": best, "current": current, "top": top,
                                  "needs": needs, "mode": self.mode()})
        if best == current and self.st.get("mode") == self.mode():
            return
        await self.start(best, now, state)

    async def start(self, name, now, state):
        a = self.catalog[name]
        self.st.update(name=name, mode=self.mode(), since=now, proof=a.get("proof", "none"), proved=None,
                       deadline=now + a.get("proof_minutes", 0) * 60, base=self.baseline(state))
        self.st.setdefault("last", {})[name] = now
        self.save()
        self.mind.mem.add_event("activity", {"name": name})
        log.info("занятие: %s", a["label"])
        await self.execute(name, now, state)

    def baseline(self, state):
        return {"map": state.get("map"), "x": state.get("x"), "y": state.get("y"),
                "weight": state.get("weight_pct"), "potions": self.potions(state),
                "hunt_map": self.mind.routine.hunt_map() if self.mind.routine else None}

    async def execute(self, name, now, state):
        r = self.mind.routine
        social = getattr(self.mind, "social", None)
        if name == "stroll" and social:
            social.walk_now()
        elif name == "socialize" and social:
            p = self.visible_peer(state)
            if p:
                await social.visit(state.get("map"), p["x"], p["y"], f"к {p['name']}")
        elif name == "service":
            await r.service_now(now, "занятие: сходить по делам")
        elif name == "hunt_early":
            r.st["rest_until"] = now                          # выход на охоту — распорядком, с проверкой HP
            r.save()
        elif name == "end_hunt":
            await r.to_town(now)
            r.save()
        elif name == "change_map":
            maps = getattr(self.mind, "maps", None)
            cur = r.hunt_map()
            others = [m for m in self.mind.persona["hunt_maps"] if m != cur]
            if maps and others:
                choice, why = maps.choose(others, r.bans(), level=state.get("lv"),
                                          needs=getattr(self.mind, "needs", None), rng=self.rng)
                r.st["prefer_map"] = choice
                r.last_sent = 0
                r.note("routine_map_choice", f"Сменю место охоты на {choice}: {why}.", 1)

    # ---------- факт завершения ----------

    async def check_proof(self, now, state):
        proof = self.st.get("proof")
        if not proof or proof == "none" or self.st.get("proved") is not None:
            return
        b = self.st.get("base") or {}
        ok = None
        if proof == "moved" and state.get("x") is not None and b.get("x") is not None:
            ok = dist(int(state["x"]), int(state["y"]), int(b["x"]), int(b["y"])) >= 3 or None
        elif proof == "peer_near":
            p = self.visible_peer(state)
            ok = (p is not None and state.get("x") is not None
                  and dist(int(p["x"]), int(p["y"]), int(state["x"]), int(state["y"])) <= 4) or None
        elif proof == "supply_better":
            ok = ((state.get("weight_pct") or 0) < (b.get("weight") or 0) - 5
                  or self.potions(state) > (b.get("potions") or 0)) or None
        elif proof == "on_hunt_map":
            ok = state.get("map") in self.mind.persona["hunt_maps"] or None
        elif proof == "in_town":
            r = self.mind.routine
            ok = (r and state.get("map") == r.cfg["town"]["map"]) or None
        elif proof == "map_changed":
            ok = (state.get("map") in self.mind.persona["hunt_maps"] and state.get("map") != b.get("map")) or None
        if ok:
            self.finish(True, now)
        elif now >= self.st.get("deadline", 0):
            self.finish(False, now)

    def finish(self, ok, now):
        name = self.st.get("name")
        self.st["proved"] = bool(ok)
        self.save()
        kind = "activity_done" if ok else "activity_failed"
        label = self.catalog.get(name, {}).get("label", name)
        self.mind.mem.add_event(kind, {"name": name, "minutes": round((now - self.st.get("since", now)) / 60, 1)})
        self.mind.write_decision({"type": "activity", "event": kind, "name": name})
        log.info("занятие «%s»: %s", label, "сделано по данным игры" if ok else "не удалось за отведённое время")

    def summary(self):
        name = self.st.get("name")
        return {"занятие": self.catalog.get(name, {}).get("label"), "с": self.st.get("since")} if name else None
