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
    keep_hunting — ничего; check_rumor — карта слуха на сессию (rumors.to_check, ORG-032; итог — rumors.check);
    explore — экспедиция на новую карту (explore.py, ORG-054).
Факт завершения (proof) за proof_minutes — иначе занятие «не удалось» (activity_failed), а не «сделано»:
    moved — позиция сменилась; peer_near — житель рядом; supply_better — вес меньше или зелий больше;
    on_hunt_map — на карте охоты; in_town — на карте города; map_changed — карта охоты другая;
    explore_arrived — дошёл до цели экспедиции.
Цепочки предусловий (ORG-018, GOAP-лайт; activities.json chains): если занятие с лучшей оценкой недоступно из-за
    requires (light — рюкзак не тяжёлый, potions_min, zeny_min, hp_ok, peer_visible…), а занятия текущего режима
    с provides этого условия есть — короткая цепочка (вместе с целью не больше chains.max_steps): исправители по
    порядку, шаг готов, когда его условие выполнено по данным игры (не позже step_minutes), затем цель. Условия
    из chains.unfixable (ночь, группа) занятием не исправить. Одна попытка на цель за ttl_minutes (запрет повторов),
    одно занятие в цепочке не повторяется. Журнал: activity chain_start / chain_step / chain_step_done / chain_done /
    chain_failed / chain_impossible.
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
            elif key == "light":                            # home: ORG-018 рюкзак не тяжёлый (вес < heavy_pct)
                ok = (state.get("weight_pct") or 0) < self.cfg.get("heavy_pct", 50)   # home:
            elif key == "potions_min":                      # home: зелий не меньше want (счётчиков нет — не мешает)
                ok = state.get("items") is None or self.potions(state) >= want        # home:
            elif key == "zeny_min":                         # home: денег не меньше want
                ok = (state.get("zeny") or 0) >= want                                 # home:
            elif key == "in_party":                         # home: житель в своей группе (по данным сервера)
                party = getattr(self.mind, "party", None)                             # home:
                ok = bool(party and party.confirmed())                                # home:
            elif key == "explore_target":                   # explore: ORG-054 есть цель экспедиции и её можно начать
                explorer = getattr(self.mind, "explorer", None)     # explore:
                ok = bool(explorer and explorer.available(state))   # explore:
            elif key == "rumor_to_check":                   # events: ORG-032 есть слух, который стоит проверить
                rumors = getattr(self.mind, "rumors", None)     # events:
                ok = bool(rumors and rumors.to_check(state))    # events:
            else:
                ok = False                                   # неизвестное условие — занятие недоступно
            if bool(ok) != bool(want) and key not in ("supply_min", "potions_min", "zeny_min"):   # home: числа
                return False
            if key in ("supply_min", "potions_min", "zeny_min") and not ok:                  # home:
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
        explorer = getattr(m, "explorer", None)                 # explore: экспедиция идёт — других занятий нет
        if explorer and explorer.busy():                        # explore:
            return "экспедиция"                                 # explore:
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
        crowd = getattr(self.mind, "crowd", None)               # crowd: ORG-089 толпа и повторы за день
        out = {}
        for name, a in self.catalog.items():
            if mode not in a["modes"]:
                continue
            if now - self.st.get("last", {}).get(name, 0) < a.get("cooldown_minutes", 0) * 60 and name != current:
                continue
            if not self.requires_ok(a.get("requires") or {}, state, needs):
                continue
            score = sum(w * needs.get(k, 0) for k, w in a["satisfies"].items())
            if crowd:                                                                   # crowd:
                score -= crowd.activity_penalty(name, mode, current)                    # crowd:
            if name == current:
                score += self.cfg["inertia"]
            out[name] = round(score + noise * self.rng.uniform(-1, 1) * 0.3, 3)
        return out, needs

    async def tick(self):
        now = self.clock()
        state = self.mind.state
        await self.check_proof(now, state)
        if self.st.get("chain"):                               # home: ORG-018 идёт цепочка — выбор подождёт
            await self.chain_tick(now, state)                  # home:
            return                                             # home:
        if now < self.next_decide or self.blocked(state):
            return
        r = self.mind.routine
        if r.st.get("mode") == "hunt" and now - r.st.get("mode_since", now) < self.cfg["min_hunt_minutes"] * 60:
            return
        lo, hi = self.cfg["decide_minutes"]
        self.next_decide = now + self.rng.uniform(lo, hi) * 60
        scored, needs = self.scores(state)
        if await self.try_chain(now, state, needs, scored):   # home: ORG-018 лучшее недоступно — цепочка
            return                                             # home:
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
        elif name == "explore":                                 # explore: ORG-054 экспедиция
            explorer = getattr(self.mind, "explorer", None)         # explore:
            if explorer:                                            # explore:
                await explorer.start()                              # explore:
        elif name == "check_rumor":                             # events: ORG-032 проверить слух на сессию
            rumors = getattr(self.mind, "rumors", None)             # events:
            rec = rumors.to_check(state) if rumors else None        # events:
            if rec and (rec["map"] in self.mind.persona["hunt_maps"] or self.mind.learn_hunt_map(rec["map"])):  # events:
                rumors.start_check(rec)                             # events:
                r.st["prefer_map"] = rec["map"]                     # events:
                r.last_sent = 0                                     # events:
                r.note("routine_map_choice", f"Проверю слух от {rec.get('author')}: {rec['map']} ({rec['kind']}).", 1)  # events:

    # ---------- home: ORG-018 цепочки предусловий (GOAP-лайт) ----------

    def chain_cfg(self):
        return self.cfg.get("chains") or {}

    def missing(self, req, state, needs):
        """Какие предусловия занятия не выполнены (по одному, тем же requires_ok)."""
        return {k: v for k, v in req.items() if not self.requires_ok({k: v}, state, needs)}

    def on_cooldown(self, name, now):
        a = self.catalog[name]
        return now - self.st.get("last", {}).get(name, 0) < a.get("cooldown_minutes", 0) * 60

    def plan_chain(self, target, missing, state, needs, now):
        """Короткая цепочка исправителей для target: ([{name, fixes}], None) или (None, почему нельзя).
        Исправитель — занятие текущего режима с provides нужного условия, не на перезарядке; одно занятие не
        повторяется (исправляет сразу несколько условий); всего шагов вместе с target — не больше max_steps."""
        cfg = self.chain_cfg()
        limit = int(cfg.get("max_steps", 3)) - 1
        unfixable = cfg.get("unfixable") or {}
        mode = self.mode()
        steps = []

        def solve(key, want, depth):
            if key in unfixable:
                return unfixable[key]
            for st in steps:                                   # уже взятое занятие даёт и это условие
                if key in self.catalog[st["name"]].get("provides", []):
                    st["fixes"][key] = want
                    return None
            fixers = [n for n, a in self.catalog.items()
                      if key in a.get("provides", []) and mode in a["modes"] and n != target
                      and not self.on_cooldown(n, now)]
            fixers.sort(key=lambda n: (-sum(w * needs.get(k, 0) for k, w in self.catalog[n]["satisfies"].items()), n))
            why = f"нет доступного занятия, которое даёт «{key}»"
            for name in fixers:
                if len(steps) >= limit:
                    return f"цепочка длиннее {limit + 1} шагов"
                sub = self.missing(self.catalog[name].get("requires") or {}, state, needs)
                if sub and depth <= 1:
                    why = f"{name}: не выполнено {sorted(sub)}"
                    continue
                mark = len(steps)
                err = next((e for e in (solve(k, v, depth - 1) for k, v in sub.items()) if e), None)
                if not err and len(steps) >= limit:
                    err = f"цепочка длиннее {limit + 1} шагов"
                if err:
                    del steps[mark:]
                    why = err
                    continue
                steps.append({"name": name, "fixes": {key: want}})
                return None
            return why

        for key, want in missing.items():
            err = solve(key, want, limit)
            if err:
                return None, err
        return steps, None

    async def try_chain(self, now, state, needs, scored):
        """Лучшее по мотивам занятие недоступно из-за предусловия, а исправитель есть — начать цепочку."""
        cfg = self.chain_cfg()
        if not cfg.get("enabled", True):
            return False
        best = max(scored.values()) if scored else float("-inf")
        mode = self.mode()
        cand = []
        for name, a in self.catalog.items():
            if mode not in a["modes"] or name in scored or self.on_cooldown(name, now):
                continue
            miss = self.missing(a.get("requires") or {}, state, needs)
            score = round(sum(w * needs.get(k, 0) for k, w in a["satisfies"].items()), 3)
            if miss and score > best + self.cfg["switch_margin"]:
                cand.append((score, name, miss))
        ttl = cfg.get("ttl_minutes", 60) * 60
        tried = self.st.setdefault("chain_last", {})
        for score, name, miss in sorted(cand, key=lambda c: (-c[0], c[1])):
            if now - tried.get(name, 0) < ttl:                 # запрет повторов: одна попытка на цель за TTL
                continue
            tried[name] = now
            steps, why = self.plan_chain(name, miss, state, needs, now)
            if steps is None:
                self.save()
                self.mind.write_decision({"type": "activity", "event": "chain_impossible", "target": name,
                                          "missing": miss, "why": why, "score": score})
                continue
            self.st["chain"] = {"target": name, "steps": steps, "i": 0, "mode": mode, "since": now,
                                "until": now + ttl, "missing": miss}
            self.save()
            plan = " → ".join([s["name"] for s in steps] + [name])
            self.mind.write_decision({"type": "activity", "event": "chain_start", "target": name, "missing": miss,
                                      "steps": steps, "score": score, "best_available": best, "plan": plan})
            self.mind.mem.add_event("activity_chain", {"target": name, "plan": plan})
            log.info("цепочка: %s (не хватает %s)", plan, ", ".join(miss))
            await self.chain_tick(now, state)
            return True
        return False

    async def chain_tick(self, now, state):
        ch = self.st["chain"]
        if self.mode() != ch.get("mode"):
            return self.chain_end(now, False, "режим распорядка сменился")
        if now >= ch.get("until", 0):
            return self.chain_end(now, False, "не уложился в отведённое время")
        needs = self.mind.needs.weighted()
        steps = ch["steps"]
        while ch["i"] < len(steps):
            step = steps[ch["i"]]
            if self.requires_ok(step["fixes"], state, needs):  # условие выполнено (или было) — дальше
                ch["i"] += 1
                self.save()
                self.mind.write_decision({"type": "activity", "event": "chain_step_done", "target": ch["target"],
                                          "step": step["name"], "fixes": step["fixes"]})
                continue
            if step.get("started"):
                if now >= step["deadline"]:
                    return self.chain_end(now, False, f"{step['name']} не дал {sorted(step['fixes'])}")
                return
            if self.blocked(state):
                return
            step["started"] = now
            step["deadline"] = now + self.chain_cfg().get("step_minutes", 20) * 60
            self.save()
            self.mind.write_decision({"type": "activity", "event": "chain_step", "target": ch["target"],
                                      "step": step["name"], "fixes": step["fixes"]})
            await self.start(step["name"], now, state)
            return
        target = ch["target"]
        miss = self.missing(self.catalog[target].get("requires") or {}, state, needs)
        if miss:
            return self.chain_end(now, False, f"после цепочки не выполнено {sorted(miss)}")
        if self.blocked(state):
            return
        self.chain_end(now, True, "предусловия выполнены")
        await self.start(target, now, state)

    def chain_end(self, now, ok, why):
        ch = self.st.pop("chain", None) or {}
        self.save()
        kind = "chain_done" if ok else "chain_failed"
        self.mind.write_decision({"type": "activity", "event": kind, "target": ch.get("target"), "why": why,
                                  "steps": [s["name"] for s in ch.get("steps", [])]})
        self.mind.mem.add_event("activity_" + kind, {"target": ch.get("target"), "why": why})
        log.info("цепочка к «%s»: %s — %s", ch.get("target"), "готово" if ok else "не вышло", why)

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
        elif proof == "explore_arrived":                     # explore: дошёл до цели экспедиции (по state.map)
            explorer = getattr(self.mind, "explorer", None)
            ok = bool(explorer and explorer.st.get("arrived_at", 0) >= self.st.get("since", 0)) or None
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
