"""Исполняемые планы: встреча двух жителей в реальной игре.

Модель только выбирает цель (предложить, принять, отклонить, отменить). Исполняет и
проверяет план этот модуль — правилами, раз в секунду, без вызовов LLM.

Протокол в игре — шёпот с меткой (человекочитаемый текст + машинная метка):
    [meet:<id>:<map>:<x>:<y>]   предложение встретиться в точке (позиция предлагающего)
    [meet:<id>:ok]              согласие
    [meet:<id>:no]              отказ
    [meet:<id>:done]            «я на месте и вижу тебя»

Статусы (status) и фазы (phase):
    planned    awaiting_answer   — я предложил, жду ответа
               awaiting_my_answer — мне предложили, решаю (модель или правило)
    executing  moving            — иду к точке (OpenKore: lockMap_x/y)
               waiting           — дошёл, жду партнёра у точки
    completed  —                 — партнёр виден рядом со мной у точки (по моим данным игры)
    failed     —                 — отказ, таймаут, смерть, отмена, истёк при перезапуске

Доказательства:
    «дошёл»       — моя позиция из игры не дальше ARRIVE_DIST от точки;
    «встретился»  — партнёр есть в списке игроков рядом не дальше MEET_DIST от меня.
Слова и ack команды доказательством не считаются.

После перезапуска мозга активный план сверяется с игрой: срок, выставлена ли точка
в OpenKore (lock_x/lock_y в состоянии), где персонаж. Команды не повторяются вслепую.
"""
import json
import logging
import math
import re
import secrets
import time

log = logging.getLogger("plans")

TAG = re.compile(r"\[meet:([a-z0-9]{4,8}):(?:(ok|no|done)|([a-z0-9_]{3,16}):(\d{1,3}):(\d{1,3}))\]")

# Таймауты по умолчанию (BRAIN_PLAN_* в env): ответ 120 с, автопринятие 45 с, путь 300 с, ожидание 240 с.
POINT_RETRY = 30          # не чаще раза в 30 с повторно выставлять точку
ARRIVE_DIST = 4
MEET_DIST = 8

ACTIVE = ("planned", "executing")

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, partner TEXT NOT NULL, role TEXT NOT NULL,
    map TEXT NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,
    status TEXT NOT NULL, phase TEXT, result TEXT,
    created REAL NOT NULL, updated REAL NOT NULL, phase_since REAL NOT NULL,
    point_sent REAL NOT NULL DEFAULT 0, partner_done INTEGER NOT NULL DEFAULT 0,
    history TEXT NOT NULL DEFAULT '[]');
"""


def dist(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))      # клетки RO: расстояние Чебышёва


class PlanStore:
    def __init__(self, db):
        self.db = db
        self.db.executescript(SCHEMA)
        self.db.commit()

    def create(self, **plan):
        now = time.time()
        plan.setdefault("phase_since", now)
        plan.update(created=now, updated=now, history=json.dumps([[now, "создан"]], ensure_ascii=False))
        cols = ", ".join(plan)
        self.db.execute(f"INSERT INTO plans ({cols}) VALUES ({', '.join('?' * len(plan))})", list(plan.values()))
        self.db.commit()
        return self.get(plan["id"])

    def get(self, plan_id):
        row = self.db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        return dict(row) if row else None

    def active(self):
        row = self.db.execute("SELECT * FROM plans WHERE status IN ('planned', 'executing') "
                              "ORDER BY created DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def recent(self, n=5):
        return [dict(r) for r in self.db.execute(
            "SELECT id, kind, partner, role, map, x, y, status, phase, result, created, updated "
            "FROM plans ORDER BY created DESC LIMIT ?", (n,))]

    def update(self, plan_id, note=None, **fields):
        now = time.time()
        plan = self.get(plan_id)
        if "phase" in fields and fields["phase"] != plan["phase"]:
            fields["phase_since"] = now
        history = json.loads(plan["history"])
        if note:
            history.append([now, note])
        fields.update(updated=now, history=json.dumps(history[-50:], ensure_ascii=False))
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE plans SET {sets} WHERE id = ?", [*fields.values(), plan_id])
        self.db.commit()
        return self.get(plan_id)


class PlanExecutor:
    """Ведёт один активный план. mind даёт состояние, память, отправку действий и LLM-триггер."""

    def __init__(self, mind, store):
        self.mind = mind
        self.store = store
        self.reconciled = False
        self.started = time.time()       # сверять нужно только планы, созданные до запуска мозга
        st = mind.s
        self.answer_timeout = st.plan_answer_timeout
        self.auto_accept = st.plan_auto_accept       # модель молчит — правило принимает предложение жителя
        self.travel_timeout = st.plan_travel_timeout
        self.wait_timeout = st.plan_wait_timeout

    # ---------- данные игры ----------

    @property
    def state(self):
        return self.mind.state

    def my_pos(self):
        s = self.state
        if s.get("x") is None or s.get("y") is None:
            return None
        return s.get("map"), int(s["x"]), int(s["y"])

    def partner_near(self, partner):
        me = self.my_pos()
        if not me:
            return None
        for p in self.state.get("players") or []:
            if isinstance(p, dict) and p.get("name") == partner and p.get("x") is not None:
                d = dist(me[1], me[2], int(p["x"]), int(p["y"]))
                if d <= MEET_DIST:
                    return d
        return None

    def summary(self):
        plan = self.store.active()
        if not plan:
            last = self.store.recent(1)
            return {"активный_план": None,
                    "последний_план": {k: last[0][k] for k in ("partner", "status", "result")} if last else None}
        return {"активный_план": {k: plan[k] for k in ("id", "kind", "partner", "role", "map", "x", "y",
                                                       "status", "phase", "result")}}

    # ---------- общение ----------

    async def whisper(self, partner, text, reason):
        await self.mind.execute([{"action": "whisper", "to": partner, "text": text[:100]}],
                                source="plan", reason=reason, protocol=True)

    async def point(self, plan, reason):
        await self.mind.execute([{"action": "meet_point", "map": plan["map"], "x": plan["x"], "y": plan["y"]}],
                                source="plan", reason=reason, protocol=True)
        return self.store.update(plan["id"], point_sent=time.time())

    async def clear_point(self, reason):
        await self.mind.execute([{"action": "clear_point"}], source="plan", reason=reason, protocol=True)

    def remember(self, text, importance, kind, plan):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, {"plan": plan["id"], "partner": plan["partner"],
                                       "map": plan["map"], "x": plan["x"], "y": plan["y"]})
        self.mind.write_decision({"type": "plan", "event": kind, "plan": plan["id"], "partner": plan["partner"],
                                  "status": plan["status"], "phase": plan["phase"], "text": text})
        log.info("план %s: %s", plan["id"], text)

    # ---------- решения (от модели, правила или оператора) ----------

    async def propose(self, partner, source):
        if partner not in self.mind.ctx.peers:
            return f"{partner} не житель"
        if self.store.active():
            return "уже есть активный план"
        me = self.my_pos()
        if not me or me[0] not in self.mind.persona["hunt_maps"]:
            return "не на разрешённой карте или позиция неизвестна"
        if self.state.get("dead"):
            return "персонаж мёртв"
        plan = self.store.create(id=secrets.token_hex(3), kind="meet", partner=partner, role="proposer",
                                 map=me[0], x=me[1], y=me[2], status="planned", phase="awaiting_answer")
        self.store.update(plan["id"], note=f"предложено ({source})")
        await self.whisper(partner, f"{partner}, встретимся? Я на {me[0]} {me[1]},{me[2]}. "
                                    f"[meet:{plan['id']}:{me[0]}:{me[1]}:{me[2]}]", "план: предложение встречи")
        self.remember(f"Я предложил {partner} встретиться на {me[0]} ({me[1]},{me[2]}).", 2,
                      "plan_proposed", plan)
        return None

    async def accept(self, plan_id, source):
        plan = self.store.get(plan_id)
        if not plan or plan["phase"] != "awaiting_my_answer":
            return "нет предложения, ждущего ответа"
        if self.state.get("dead"):
            return await self.decline(plan_id, source, "я мёртв")
        plan = self.store.update(plan_id, note=f"принято ({source})", status="executing", phase="moving")
        await self.whisper(plan["partner"], f"Хорошо, иду к тебе. [meet:{plan_id}:ok]", "план: согласие")
        self.remember(f"Я согласился встретиться с {plan['partner']} на {plan['map']} ({plan['x']},{plan['y']}).",
                      2, "plan_accepted", plan)
        await self.point(plan, "план: иду к точке встречи")
        return None

    async def decline(self, plan_id, source, why="не сейчас"):
        plan = self.store.get(plan_id)
        if not plan or plan["phase"] != "awaiting_my_answer":
            return "нет предложения, ждущего ответа"
        plan = self.store.update(plan_id, note=f"отклонено ({source}): {why}", status="failed",
                                 phase=None, result=f"я отказался: {why}")
        await self.whisper(plan["partner"], f"Не смогу, {why}. [meet:{plan_id}:no]", "план: отказ")
        self.remember(f"Я отказался встретиться с {plan['partner']}: {why}.", 2, "plan_declined", plan)
        return None

    async def cancel(self, source, why="передумал"):
        plan = self.store.active()
        if not plan:
            return "нет активного плана"
        await self.fail(plan, f"отменён ({source}): {why}", notify=True)
        return None

    async def fail(self, plan, why, notify=False):
        executing = plan["status"] == "executing"
        plan = self.store.update(plan["id"], note=why, status="failed", phase=None, result=why)
        if executing:
            await self.clear_point("план провален: вернуться к охоте")
        if notify:
            await self.whisper(plan["partner"], f"Встреча отменяется. [meet:{plan['id']}:no]", "план: отмена")
        self.remember(f"Встреча с {plan['partner']} не состоялась: {why}.", 3, "plan_failed", plan)

    # ---------- входящие метки ----------

    async def on_tag(self, sender, text):
        m = TAG.search(text or "")
        if not m:
            return False
        plan_id, reply, pmap, px, py = m.groups()
        if reply is None:
            await self.on_offer(sender, plan_id, pmap, int(px), int(py))
            return True
        plan = self.store.get(plan_id)
        if not plan or plan["partner"] != sender:
            log.info("метка %s от %s для неизвестного плана %s", reply, sender, plan_id)
            return True
        if reply == "ok" and plan["phase"] == "awaiting_answer":
            plan = self.store.update(plan_id, note=f"{sender} согласился", status="executing", phase="moving")
            self.remember(f"{sender} согласился встретиться со мной.", 2, "plan_partner_accepted", plan)
            await self.point(plan, "план: держусь у точки встречи")
        elif reply == "no" and plan["status"] in ACTIVE:
            await self.fail(plan, f"{sender} отказался или отменил")
        elif reply == "done":
            self.store.update(plan_id, note=f"{sender} сообщил: на месте и видит меня", partner_done=1)
            if plan["status"] == "completed":
                self.remember(f"{sender} подтвердил встречу со своей стороны.", 3, "meeting_partner_confirmed", plan)
        return True

    async def on_offer(self, sender, plan_id, pmap, px, py):
        if sender not in self.mind.ctx.peers:
            return
        if self.store.get(plan_id):
            return
        busy = self.store.active()
        plan = self.store.create(id=plan_id, kind="meet", partner=sender, role="acceptor", map=pmap, x=px, y=py,
                                 status="planned", phase="awaiting_my_answer")
        self.remember(f"{sender} предложил встретиться на {pmap} ({px},{py}).", 2, "plan_offered", plan)
        why = None
        if busy:
            why = "у меня уже другой план"
        elif pmap not in self.mind.persona["hunt_maps"]:
            why = "мне туда нельзя"
        elif self.state.get("dead"):
            why = "я мёртв"
        if why:
            await self.decline(plan_id, "rule", why)
            return
        if self.mind.s.llm_enabled:
            self.mind.trigger(f"{sender} предлагает встретиться (план {plan_id}, {pmap} {px},{py}): "
                              f"ответь accept_meeting или decline_meeting с id {plan_id}",
                              {"from": sender, "plan": plan_id}, kind="chat")

    # ---------- тик исполнителя ----------

    async def tick(self, now=None):
        now = now or time.time()
        if not self.mind.fresh_state:          # после запуска — только по свежим данным игры
            return
        plan = self.store.active()
        if not plan:
            return
        if not self.reconciled:
            self.reconciled = True
            if plan["updated"] < self.started:
                plan = await self.reconcile(plan, now)
                if not plan or plan["status"] not in ACTIVE:
                    return
        if self.state.get("dead"):
            await self.fail(plan, "я погиб")
            return
        phase, since = plan["phase"], plan["phase_since"]
        if phase == "awaiting_answer":
            if now - since > self.answer_timeout:
                await self.fail(plan, f"{plan['partner']} не ответил за {self.answer_timeout} с")
        elif phase == "awaiting_my_answer":
            if now - since > self.auto_accept:
                await self.accept(plan["id"], "rule")
        elif phase in ("moving", "waiting"):
            await self.drive(plan, now)

    async def drive(self, plan, now):
        me = self.my_pos()
        if me is None:
            return
        at_point = me[0] == plan["map"] and dist(me[1], me[2], plan["x"], plan["y"]) <= ARRIVE_DIST
        point_set = (self.state.get("lock_map") == plan["map"] and self.state.get("lock_x") == plan["x"]
                     and self.state.get("lock_y") == plan["y"])
        if not point_set and now - plan["point_sent"] > POINT_RETRY:
            plan = await self.point(plan, "план: точка не выставлена в OpenKore — выставляю")
        if plan["phase"] == "moving":
            if at_point:
                plan = self.store.update(plan["id"], note=f"дошёл: {me[0]} {me[1]},{me[2]}", phase="waiting")
                self.remember(f"Я дошёл до точки встречи с {plan['partner']} на {plan['map']} "
                              f"({plan['x']},{plan['y']}).", 2, "plan_arrived", plan)
            elif now - plan["phase_since"] > self.travel_timeout:
                await self.fail(plan, f"не дошёл до точки за {self.travel_timeout} с (я на {me[0]} {me[1]},{me[2]})")
                return
        if plan["phase"] == "waiting":
            d = self.partner_near(plan["partner"])
            if d is not None and at_point:
                plan = self.store.update(plan["id"], note=f"{plan['partner']} рядом ({d} клеток)",
                                         status="completed", phase=None, result=f"встреча, расстояние {d}")
                was = "была" if (self.mind.mem.get("known_players", {}).get(plan["partner"], {}).get("sex")
                                 == "Female") else "был"
                self.remember(f"Я встретился с {plan['partner']} на {plan['map']} ({plan['x']},{plan['y']}): "
                              f"{plan['partner']} {was} в {d} клетках от меня.", 4, "meeting_confirmed", plan)
                self.mind.mem.update_relation(plan["partner"], 1, None)
                await self.whisper(plan["partner"], f"Я на месте и вижу тебя. [meet:{plan['id']}:done]",
                                   "план: встреча подтверждена")
                await self.clear_point("план выполнен: вернуться к охоте")
            elif now - plan["phase_since"] > self.wait_timeout:
                await self.fail(plan, f"не дождался {plan['partner']} у точки за {self.wait_timeout} с")

    async def reconcile(self, plan, now):
        """Первый тик после запуска: сверка сохранённого плана с игрой, без слепого повтора команд."""
        age = now - plan["updated"]
        limits = {"awaiting_answer": self.answer_timeout, "awaiting_my_answer": self.answer_timeout,
                  "moving": self.travel_timeout, "waiting": self.wait_timeout}
        if now - plan["phase_since"] > limits.get(plan["phase"], 0) + 60:
            await self.fail(plan, f"истёк, пока мозг был выключен ({int(age)} с без обновлений)")
            return None
        point_set = (self.state.get("lock_x") == plan["x"] and self.state.get("lock_y") == plan["y"])
        me = self.my_pos()
        note = (f"восстановлен после перезапуска: фаза {plan['phase']}, точка в OpenKore "
                f"{'выставлена' if point_set else 'не выставлена'}, я на {me}")
        extra = {}
        if plan["phase"] in ("moving", "waiting") and not point_set:
            extra["point_sent"] = 0           # точки в игре нет — выставить на ближайшем тике
        plan = self.store.update(plan["id"], note=note, **extra)
        self.mind.write_decision({"type": "plan", "event": "reconciled", "plan": plan["id"],
                                  "phase": plan["phase"], "point_set": point_set, "pos": me})
        log.info("план %s: %s", plan["id"], note)
        return plan
