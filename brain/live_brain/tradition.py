"""Вечерний круг у фонтана (ORG-058): возникающая традиция жителей. Правила без LLM.

Вечером (goals.json tradition.hours по часовому поясу мира, по умолчанию 20:00–21:00) бодрствующие жители
могут выбрать занятие gathering (activities.json): дойти до точки (tradition.point — точка social.points,
по умолчанию fountain) и посидеть там (садится само тело, sitAuto_idle). Вес занятия в выборе — × (0.5 + сила).

Сила традиции 0..1 (старт 0.3) — общая для мира:
    удачный сбор (у точки был хоть один житель, кроме меня, ≤ 4 клеток) +0.15, пустой (пришёл один) −0.1;
    вечера без единого сбора — угасание −0.05 за каждые полные сутки сверх первых (считается при чтении).
Источник — шина мира (событие tradition_strength, важность 1), без шины — kv tradition жителя.
Пишет только один житель за окно: минимальное имя среди присутствовавших (как лидер в party.py);
остальные читают. Повторной записи за тот же вечер нет (проверка по шине).
Ступени: < 0.2 «забыта», 0.2–0.6 «бывает», ≥ 0.6 «традиция». Смена ступени — событие памяти tradition_stage
(в шину как tradition, важность 3, и строка летописи «В Пронтере по вечерам собираются у фонтана»).
Окно — внутри одних суток (hours[0] < hours[1]). Выключение: BRAIN_DISABLE=tradition или tradition.enabled false.
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("tradition")

DEFAULTS = {"enabled": True, "point": "fountain", "hours": [20, 21], "minutes": 40}
START = 0.3
GAIN = 0.15
LOSS = 0.1
DECAY_PER_DAY = 0.05
NEAR = 4
STAGES = ((0.6, "tradition", "традиция"), (0.2, "sometimes", "бывает"), (float("-inf"), "forgotten", "забыта"))
TOWNS = {"prontera": "В Пронтере"}
FALLBACK_POINT = {"map": "prontera", "x": 156, "y": 185, "label": "к фонтану в центре"}


def stage(value):
    """(id, название) ступени традиции по силе."""
    for lo, sid, label in STAGES:
        if value >= lo - 1e-9:
            return sid, label
    return STAGES[-1][1:]


def stage_text(sid, point):
    """Строка летописи о смене ступени."""
    where = TOWNS.get(point.get("map"), f"На {point.get('map')}")
    if sid == "tradition":
        return f"{where} по вечерам собираются у фонтана — это уже традиция"
    if sid == "sometimes":
        return f"{where} по вечерам бывает круг у фонтана"
    return f"{where} вечерний круг у фонтана забыт"


def dist(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


class Tradition:
    def __init__(self, mind, cfg, clock=None, world=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **(cfg or {}))
        world = world or {}
        self.tz = timezone(timedelta(hours=world.get("timezone_offset_hours", 0)))
        points = ((world.get("social") or {}).get("points") or {})
        self.point = dict(points.get(self.cfg["point"]) or FALLBACK_POINT)
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.st = mind.mem.get("tradition") or {}

    # ---------- данные ----------

    def save(self):
        self.mind.mem.set("tradition", self.st)

    @property
    def me(self):
        return self.mind.state.get("name") or self.mind.persona["name"]

    def bus(self):
        feed = getattr(self.mind, "world", None)
        return getattr(feed, "bus", None)

    def date(self, now):
        return datetime.fromtimestamp(now, self.tz).strftime("%Y-%m-%d")

    def window(self, day):
        """(начало, конец) окна для дня YYYY-MM-DD по часовому поясу мира."""
        mid = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=self.tz).timestamp()
        lo, hi = self.cfg["hours"]
        return mid + lo * 3600, mid + hi * 3600

    def shared(self):
        """Последняя запись силы: из шины (общая для мира), иначе своя kv. {"strength", "day", "ts"} или None."""
        bus = self.bus()
        if bus is not None:
            try:
                row = bus.db.execute("SELECT ts, data FROM world_events WHERE kind = 'tradition_strength' "
                                     "ORDER BY id DESC LIMIT 1").fetchone()
            except Exception as e:                    # общая БД занята или повреждена — своя kv
                log.warning("шина мира недоступна: %s", e)
                row = None
            if row:
                try:
                    return dict(json.loads(row[1]), ts=row[0])
                except ValueError:
                    pass
        return self.st.get("value")

    def strength(self, now=None):
        """Сила традиции 0..1 с угасанием за вечера без сборов; записи нет — START."""
        now = self.clock() if now is None else now
        rec = self.shared()
        if not rec:
            return START
        idle_days = max(0, int((now - rec.get("ts", now)) // 86400) - 1)
        return round(max(0.0, min(1.0, float(rec.get("strength", START)) - DECAY_PER_DAY * idle_days)), 3)

    def asleep(self):
        r = getattr(self.mind, "routine", None)
        return bool(r and r.st and r.st.get("mode") == "sleep")

    def window_open(self, now=None):
        """Часы мира [hours[0], hours[1]) и житель не спит."""
        now = self.clock() if now is None else now
        lo, hi = self.window(self.date(now))
        return lo <= now < hi and not self.asleep()

    def at_point(self, x, y, map_=None):
        return (x is not None and y is not None and (map_ is None or map_ == self.point["map"])
                and dist(int(x), int(y), self.point["x"], self.point["y"]) <= NEAR)

    def present(self, state):
        """Жители у точки (≤ NEAR клеток), если я сам у точки; иначе пусто."""
        if not self.at_point(state.get("x"), state.get("y"), state.get("map")):
            return []
        return sorted({p["name"] for p in state.get("players") or []
                       if isinstance(p, dict) and p.get("name") in self.mind.ctx.peers
                       and self.at_point(p.get("x"), p.get("y"))})

    def gathered(self, state):
        """Факт сбора (proof gathered): я у точки и рядом хотя бы один житель."""
        return bool(self.present(state))

    # ---------- тик ----------

    def tick(self, now=None):
        now = self.clock() if now is None else now
        state = self.mind.state
        day = self.date(now)
        eve = self.st.get("eve")
        if eve and eve.get("day") != day and eve.get("attended") and not eve.get("closed"):
            self.close(eve, now)                         # окно прошлого дня не закрыто (рестарт) — закрыть
        if not eve or eve.get("day") != day:
            eve = self.st["eve"] = {"day": day, "attended": False, "met": [], "closed": False}
            self.save()
        if (self.window_open(now) and self.mind.fresh_state and not state.get("dead")
                and self.at_point(state.get("x"), state.get("y"), state.get("map"))):
            met = sorted(set(eve["met"]) | set(self.present(state)))
            if not eve["attended"] or met != eve["met"]:
                eve.update(attended=True, met=met)
                self.save()
        if eve["attended"] and not eve["closed"] and now >= self.window(day)[1]:
            self.close(eve, now)

    def close(self, eve, now):
        """Конец окна: запись силы — только у минимального имени среди присутствовавших."""
        eve["closed"] = True
        self.save()
        writer = min({self.me} | set(eve.get("met") or []))
        if writer != self.me:
            self.mind.write_decision({"type": "tradition", "event": "seen", "day": eve["day"],
                                      "met": eve.get("met"), "writer": writer})
            return None
        return self.on_window_end(now, len(eve.get("met") or []), eve["day"])

    def written(self, day):
        bus = self.bus()
        if bus is not None:
            try:
                rows = bus.db.execute("SELECT data FROM world_events WHERE kind = 'tradition_strength' "
                                      "ORDER BY id DESC LIMIT 20").fetchall()
            except Exception:
                rows = []
            for (data,) in rows:
                try:
                    if json.loads(data).get("day") == day:
                        return True
                except ValueError:
                    continue
            return False
        return (self.st.get("value") or {}).get("day") == day

    def on_window_end(self, now, met, day=None):
        """met — сколько жителей (кроме меня) было рядом в окне: ≥ 1 — +GAIN, иначе −LOSS; пределы 0..1."""
        day = day or self.date(now)
        if self.written(day):
            return None                                  # этот вечер уже записан другим жителем
        old = self.strength(now)
        new = round(max(0.0, min(1.0, old + (GAIN if met >= 1 else -LOSS))), 3)
        old_stage, (sid, label) = stage(old)[0], stage(new)
        rec = {"strength": new, "day": day, "met": int(met), "stage": sid}
        bus = self.bus()
        if bus is not None:
            try:
                bus.publish("tradition_strength", rec, 1, now=now)
            except Exception as e:
                log.warning("шина мира недоступна: %s", e)
        self.st["value"] = dict(rec, ts=now)
        self.save()
        self.mind.mem.add_event("tradition_gathering", {"met": int(met), "strength": new, "day": day})
        self.mind.write_decision({"type": "tradition", "event": "update", "day": day, "met": int(met),
                                  "from": old, "to": new, "stage": sid})
        if sid != old_stage:
            text = stage_text(sid, self.point)
            self.mind.mem.add_event("tradition_stage", {"stage": sid, "label": label, "strength": new, "text": text})
            self.mind.mem.remember(text + ".", 2)
            log.info("традиция: %s (%.2f)", label, new)
        return new

    def summary(self, now=None):
        s = self.strength(now)
        return {"вечерний_круг": stage(s)[1], "сила": s}
