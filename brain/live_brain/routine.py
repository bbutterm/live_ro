"""Распорядок дня и глобальные цели (brain/world/goals.json + routine в характере).

Жители не качаются круглые сутки: в день 4-5 часов охоты сессиями по 60-100 минут,
между ними и после дневной нормы — отдых в городе (сидят, общаются).
Всё это — правила без LLM (тик 1 с). Модель видит распорядок и цели в промпте и может
лишь выбрать предпочитаемую карту охоты на следующую сессию.

Режимы (kv "routine" в SQLite):
    hunt  — охота на карте охоты (OpenKore: lockMap <карта>, без точки)
    town  — отдых в городе у точки (lockMap <город>, lockMap_x/y; сидит по прибытии)

Усталость считается только по фактическому времени на карте охоты (жив, карта из списка).
Переходы:
    hunt -> town: сессия закончилась или дневная норма выбрана;
    town -> hunt: перерыв прошёл и норма не выбрана; новый день — новая норма.
Сверка с игрой каждый тик: если настройка OpenKore не соответствует режиму (например, после
перезапуска бота), команда отправляется заново — не чаще раза в RESEND секунд.
Во время активного плана встречи распорядок не вмешивается (только закрывает лавку: с ней не ходят).
Безопасность важнее расписания (AUT-008, AUT-026, AUT-086):
    после любой смерти — режим «восстановление»: город, отдых, на охоту только при HP >= min_hp_to_hunt;
    на охоте HP < LOW_HP и нечем лечиться дольше LOW_HP_SEC — в город;
    из города на охоту не уходит, пока HP ниже min_hp_to_hunt (норма охоты подождёт).
Застревание (AUT-037): не считается, пока персонаж сидит, торгует, говорит с NPC или в бою.
Цель (AUT-098): при смене режима мозг записывает цель из распорядка — текст цели не расходится с телом.
Сон (ORG-012, characters sleep {start "HH:MM", hours [от, до]}): в своё время житель уходит в город и
    выходит из игры (действие sleep -> OpenKore relog <секунды>: выход и вход через заданное время).
    Режим sleep до пробуждения; новый день жителя начинается с пробуждения (а не в 00:00 у всех).
    Не уснул (тело в игре через SLEEP_RETRY после команды) — повтор, после трёх неудач — оповещение.
Поездка по делам (ORG-018): в городе при тяжёлом рюкзаке или нехватке зелий — действие service
    (OpenKore autostorage/autosell -> продажа, склад, закупка); не чаще SERVICE_GAP.
Лавка (vend_in_town, только Merchant с навыком и тележкой): открыть по прибытии в город,
закрыть перед охотой — с открытой лавкой персонаж не двигается.
"""
import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("routine")

RESEND = 60
SLEEP_RETRY = 600
SERVICE_GAP = 1800
SAVE_EVERY = 30
MAX_TICK_GAP = 5          # не засчитывать охоту за время, когда мозг не работал
STUCK_SEC = 300           # на охоте без движения и без боя дольше — «застрял»
UNSTUCK_GAP = 120         # не чаще раза в 2 минуты
LADDER_WINDOW = 1800      # лестница выхода из застревания (AUT-038/042): попытки за 30 мин
STUCK_BAN = 1800          # после неудачной лестницы карта исключается на 30 мин
DEATH_WINDOW = 1800       # 3 смерти за 30 минут — отдых и карта полегче
DEATH_LIMIT = 3
DEATH_BAN = 3600          # после серии смертей карта исключена на час
LOW_HP = 25               # на охоте ниже — и без зелий — уходить в город
LOW_HP_SEC = 20
HEAL_ITEMS = ("569", "501", "502", "503", "504")   # Novice/Red/Orange/Yellow/White Potion (ID)
BUSY = ("sitAuto", "sitting", "storageAuto", "sellAuto", "buyAuto", "deal", "NPC", "attack",
        "skill_use", "take", "items_take", "items_gather", "dead")


def load_world(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def merged_routine(world, persona):
    routine = dict(world.get("routine", {}))
    routine.update(persona.get("routine", {}))
    return routine


class Routine:
    def __init__(self, mind, world, rng=None, clock=None):
        self.mind = mind
        self.world = world
        self.cfg = merged_routine(world, mind.persona)
        self.town = self.cfg["town"]
        self.tz = timezone(timedelta(hours=world.get("timezone_offset_hours", 0)))
        self.rng = rng or random.Random()
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.last_tick = None
        self.last_sent = 0.0
        self.anchor = None          # (map, x, y, время) — где стоял в последний раз
        self.last_combat = 0.0
        self.last_unstuck = 0.0
        self.last_vend = 0.0
        self.last_saved = 0.0
        self.saved_mode = None
        self.low_hp_since = None
        self.waiting_hp_noted = False
        self.st = mind.mem.get("routine") or {}

    # ---------- данные ----------

    def save(self, throttle=False):
        """В SQLite. Тик сохраняет не чаще SAVE_EVERY с (ORG D7: не коммит каждую секунду), но смена
        режима и все прочие изменения — сразу. При перезапуске теряется не больше SAVE_EVERY с охоты."""
        now = self.clock()
        if throttle and now - self.last_saved < SAVE_EVERY and self.st.get("mode") == self.saved_mode:
            return
        self.last_saved, self.saved_mode = now, self.st.get("mode")
        self.mind.mem.set("routine", self.st)

    def day_shift(self):
        """Часы от полуночи до начала «дня» жителя: час пробуждения (середина диапазона сна), иначе 0."""
        sl = self.mind.persona.get("sleep")
        if not sl:
            return 0.0
        h, m = map(int, sl["start"].split(":"))
        return (h + m / 60 + sum(sl["hours"]) / 2) % 24

    def today(self, now):
        return datetime.fromtimestamp(now - self.day_shift() * 3600, self.tz).strftime("%Y-%m-%d")

    def sleep_window(self, now):
        """(начало, конец) ближайшей ночи жителя по местному времени мира; None — сна в характере нет."""
        sl = self.mind.persona.get("sleep")
        if not sl:
            return None
        local = datetime.fromtimestamp(now, self.tz)
        h, m = map(int, sl["start"].split(":"))
        start = local.replace(hour=h, minute=m, second=0, microsecond=0)
        if start.timestamp() > now + 12 * 3600:
            start -= timedelta(days=1)
        elif start.timestamp() + 18 * 3600 < now:
            start += timedelta(days=1)
        key = start.strftime("%Y-%m-%d")
        if self.st.get("sleep_night") != key:                  # длительность сна — своя каждую ночь
            self.st["sleep_night"] = key
            self.st["sleep_hours"] = self.rng.uniform(*sl["hours"])
        return start.timestamp(), start.timestamp() + self.st["sleep_hours"] * 3600

    def minutes(self, key):
        lo, hi = self.cfg[key]
        factor = 1.0
        needs = getattr(self.mind, "needs", None)
        if needs and key == "session_minutes":
            factor = needs.session_factor()               # ORG-019: усердие и терпение удлиняют сессию
        return self.rng.uniform(lo, hi) * 60 * factor

    def hunt_map(self):
        """Карта охоты: выбор модели/группы, но не исключённая после смертей (AUT-010)."""
        pref = self.st.get("prefer_map")
        maps = self.mind.persona["hunt_maps"]
        bans = self.bans()
        allowed = [m for m in maps if m not in bans] or maps
        return pref if pref in allowed else allowed[0]

    def bans(self):
        """Исключённые карты (смерти, застревания) — общее хранилище kv map_bans."""
        now = self.clock()
        return {m: t for m, t in (self.mind.mem.get("map_bans") or {}).items() if t > now}

    def summary(self, now=None):
        now = now or self.clock()
        if not self.st:
            return None
        s = self.st
        out = {"режим": "охота" if s["mode"] == "hunt" else "отдых в городе",
               "наохотился_сегодня_мин": int(s["hunted"] / 60),
               "норма_на_день_мин": int(s["budget"] / 60),
               "карта_охоты": self.hunt_map(),
               "город": self.town["map"]}
        if s["mode"] == "hunt":
            out["до_конца_сессии_мин"] = max(0, int((s["session_end"] - s["hunted"]) / 60))
        else:
            if s["rest_until"] == float("inf"):
                out["отдых"] = "до завтра: дневная норма охоты выбрана"
            else:
                out["отдых_ещё_мин"] = max(0, int((s["rest_until"] - now) / 60))
        return out

    def goals(self):
        state = self.mind.state
        result = []
        for g in self.world.get("goals", []) + self.mind.persona.get("goals_extra", []):
            item = {"цель": g["text"]}
            if g.get("metric") and state.get(g["metric"]) is not None:
                item["сейчас"] = state[g["metric"]]
                item["нужно"] = g.get("target")
            result.append(item)
        return result

    # ---------- тик ----------

    def new_day(self, now, keep_mode=None):
        if self.st.get("day"):
            self.diary(self.st)
        self.st = {"day": self.today(now), "budget": self.minutes("hunt_hours_per_day") * 60,
                   "hunted": 0.0, "mode": keep_mode or "hunt", "mode_since": now,
                   "session_end": 0.0, "rest_until": 0.0, "arrived": False,
                   "prefer_map": self.st.get("prefer_map")}
        self.st["session_end"] = min(self.st["budget"], self.minutes("session_minutes"))
        log.info("новый день %s: норма охоты %d мин", self.st["day"], self.st["budget"] / 60)

    async def tick(self):
        now = self.clock()
        if not self.mind.fresh_state:
            return
        state = self.mind.state
        if not self.st:
            self.new_day(now)
        elif self.st["day"] != self.today(now):
            self.new_day(now, keep_mode=self.st["mode"])
            if self.st["mode"] == "town":
                self.st["rest_until"] = now          # новый день: можно идти охотиться сразу
        gap = (now - self.last_tick) if self.last_tick else 0
        self.last_tick = now
        if await self.sleep_tick(now, state):
            self.save(throttle=True)
            return
        if self.mind.plans.store.active():
            if (state.get("vend") or {}).get("open") and now - self.last_vend >= RESEND:
                self.last_vend = now
                await self.send({"action": "shop_close"}, "распорядок: закрыть лавку — иду на встречу")
            self.save(throttle=True)
            return                                    # план встречи важнее распорядка

        party = getattr(self.mind, "party", None)
        lead = party.leader_wants(now) if party else None     # участник группы: режим задаёт лидер
        maps = getattr(self.mind, "maps", None)
        if maps:
            maps.tick(state, hunting=self.st["mode"] == "hunt" and state.get("map") in self.mind.persona["hunt_maps"])
        if self.st["mode"] == "hunt":
            if (0 < gap <= MAX_TICK_GAP and not state.get("dead")
                    and state.get("map") in self.mind.persona["hunt_maps"]):
                self.st["hunted"] += gap
                await self.check_stuck(now, state)
            if self.no_heal_low_hp(now, state):
                self.note("routine_low_hp", f"HP {state.get('hp_pct')}%, а лечиться нечем — ухожу в город "
                                            "восстановиться.", 2)
                await self.to_town(now, rest_minutes=self.cfg.get("after_death_rest_minutes", 10), recover=True)
                if party:
                    await party.need_recover()
            elif lead:
                await self.follow_lead(now, state, lead)
            elif self.st["hunted"] >= self.st["session_end"] or self.st["hunted"] >= self.st["budget"]:
                await self.to_town(now)
        elif lead:
            await self.in_town(now, state, own_schedule=False)
            await self.follow_lead(now, state, lead)
        else:
            await self.in_town(now, state)
        await self.enforce(now, state)
        self.save(throttle=True)

    # ---------- сон ----------

    async def sleep_tick(self, now, state):
        """True — распорядок сейчас занят сном (остальное не делать)."""
        win = self.sleep_window(now)
        if self.st.get("mode") == "sleep":
            if now >= self.st.get("wake_at", 0):
                self.st.update(mode="town", mode_since=now, arrived=False, rest_until=now)
                self.note("routine_wake", "Проснулся — начинаю новый день.", 2)
                self.last_sent = 0
                return False
            if now - self.st.get("sleep_sent", 0) >= SLEEP_RETRY:   # тело всё ещё в игре — не уснуло
                tries = self.st.get("sleep_tries", 0) + 1
                self.st.update(sleep_tries=tries, sleep_sent=now)
                if tries > 3:
                    alert = getattr(self.mind, "alert", None)
                    if alert:
                        alert("sleep", "житель не выходит из игры по команде relog")
                await self.send({"action": "sleep", "seconds": int(self.st["wake_at"] - now)}, "распорядок: сон (повтор)")
            return True
        if not win or not (win[0] <= now < win[1]) or self.mind.plans.store.active():
            return False
        if self.st.get("mode") == "hunt":
            self.note("routine_bedtime", "Поздно — заканчиваю охоту и иду спать в город.", 1)
            await self.to_town(now)
            return False
        if not self.st.get("arrived") and now - win[0] < 1200:
            return False                                     # сначала дойти до города (не дольше 20 мин)
        self.st.update(mode="sleep", mode_since=now, wake_at=win[1], sleep_sent=now, sleep_tries=0)
        hours = (win[1] - now) / 3600
        self.note("routine_sleep", f"Ложусь спать на {hours:.1f} ч.", 2)
        self.set_goal("сплю")
        await self.send({"action": "sleep", "seconds": int(win[1] - now)}, f"распорядок: сон {hours:.1f} ч")
        return True

    # ---------- поездка по делам ----------

    async def service_check(self, now, state):
        """ORG-018: тяжело (>= 40%) или мало зелий при деньгах на закупку — продать/сдать/докупить сейчас."""
        if now - self.st.get("service_at", 0) < SERVICE_GAP or not self.st.get("arrived"):
            return
        heal = self.heal_items(state)
        heavy = (state.get("weight_pct") or 0) >= 40
        short = heal is not None and heal < 10 and (state.get("zeny") or 0) > 3000
        if not (heavy or short) or state.get("activity") in BUSY:
            return
        await self.service_now(now, "Схожу по делам: " + ("рюкзак тяжёлый" if heavy else "мало зелий") + ".")

    async def service_now(self, now, text):
        """Продать/сдать на склад/докупить сейчас (OpenKore autostorage/autosell) — распорядок или каталог занятий."""
        self.st["service_at"] = now
        self.note("routine_service", text, 1)
        await self.send({"action": "service"}, "распорядок: продать/сдать/докупить")

    # ---------- самостоятельность: застревание, смерти, дневник ----------

    def on_combat(self, now=None):
        self.last_combat = now or self.clock()

    def heal_items(self, state):
        items = state.get("items")
        if items is None:
            return None                              # тело без счётчиков (нет плагина economy)
        return sum(int(items.get(i, 0) or 0) for i in HEAL_ITEMS)

    def no_heal_low_hp(self, now, state):
        hp = state.get("hp_pct")
        if state.get("dead") or hp is None or hp >= LOW_HP or self.heal_items(state) != 0:
            self.low_hp_since = None
            return False
        self.low_hp_since = self.low_hp_since or now
        return now - self.low_hp_since >= LOW_HP_SEC

    def hp_ok(self, state):
        hp = state.get("hp_pct")
        return not state.get("dead") and (hp is None or hp >= self.cfg.get("min_hp_to_hunt", 80))

    async def check_stuck(self, now, state):
        pos = (state.get("map"), state.get("x"), state.get("y"))
        if pos[1] is None:
            return
        if state.get("sitting") or state.get("activity") in BUSY or state.get("give"):
            self.anchor = (pos[0], pos[1], pos[2], now)      # законно стоит: отдых, торговля, бой
            return
        if (not self.anchor or self.anchor[0] != pos[0]
                or max(abs(self.anchor[1] - pos[1]), abs(self.anchor[2] - pos[2])) > 2):
            self.anchor = (pos[0], pos[1], pos[2], now)
            return
        still = now - self.anchor[3]
        if (still >= STUCK_SEC and now - self.last_combat >= STUCK_SEC
                and now - self.last_unstuck >= UNSTUCK_GAP):
            self.last_unstuck = now
            self.anchor = (pos[0], pos[1], pos[2], now)
            await self.stuck_ladder(now, pos, still)

    async def stuck_ladder(self, now, pos, still):
        """Лестница: шаг 10 клеток -> шаг 25 -> уйти в город и исключить карту -> BLOCKED + оповещение."""
        tries = [t for t in self.st.get("stuck_tries", []) if now - t < LADDER_WINDOW]
        step = len(tries)
        self.st["stuck_tries"] = tries + [now]
        where = f"{pos[0]} ({pos[1]},{pos[2]})"
        if step < 2:
            radius = 10 if step == 0 else 25
            self.note("routine_stuck", f"Застрял на {where} — {int(still / 60)} мин без движения и боя, "
                                       f"пробую выбраться (попытка {step + 1}, шаг до {radius} клеток).", 1)
            await self.send({"action": "unstuck", "radius": radius}, f"распорядок: застрял, попытка {step + 1}")
            return
        bans = self.mind.mem.get("map_bans") or {}
        bans[pos[0]] = now + STUCK_BAN
        self.mind.mem.set("map_bans", bans)
        if step == 2:
            self.note("routine_stuck_relocate", f"Не выбрался на {where} за две попытки — ухожу в город, "
                                                f"на {pos[0]} пока не хожу.", 2)
        else:
            self.st["blocked"] = f"застревает на {pos[0]} снова и снова"
            self.note("routine_blocked", f"Застреваю на {where} в {step + 1}-й раз за полчаса — нужна помощь.", 3)
            alert = getattr(self.mind, "alert", None)
            if alert:
                alert("stuck", f"{where}: {step + 1} застревания за 30 мин, лестница не помогла")
        await self.to_town(now, rest_minutes=5)

    async def on_death(self, now=None):
        now = now or self.clock()
        if not self.st:
            self.new_day(now)
        deaths = self.mind.mem.count_events("died", now - DEATH_WINDOW)
        if deaths < DEATH_LIMIT:
            # AUT-008: после респауна не идти сразу в бой с 1 HP — город, отдых, восстановление.
            rest = self.cfg.get("after_death_rest_minutes", 10)
            self.note("routine_recover", "Погиб — после возрождения отдохну в городе и восстановлюсь.", 2)
            if self.st.get("mode") == "hunt":
                await self.to_town(now, rest_minutes=rest, recover=True)
            elif self.st.get("rest_until") != float("inf"):
                # погиб по дороге/в городе — отдых не короче rest минут (реплей ORG-050 нашёл этот случай)
                self.st["rest_until"] = max(self.st.get("rest_until", 0), now + rest * 60)
            self.st["recover"] = True
            self.save()
            return
        bad = self.hunt_map()
        # Не обещать конкретную карту: её выберет pick_map при выходе на охоту (с учётом опыта и атласа).
        # Опасную карту исключить на DEATH_BAN — тогда выбор точно будет другим, и запись правдива.
        bans = self.mind.mem.get("map_bans") or {}
        bans[bad] = max(bans.get(bad, 0), now + DEATH_BAN)
        self.mind.mem.set("map_bans", bans)
        if self.st.get("prefer_map") == bad:
            self.st.pop("prefer_map", None)
        self.note("routine_deaths", f"Погиб {deaths} раза за полчаса на {bad} — отдохну, а туда пока не пойду.", 3)
        alert = getattr(self.mind, "alert", None)
        if alert:
            alert("deaths", f"{deaths} смерти за 30 мин на {bad}")
        await self.to_town(now, recover=True)
        self.save()

    async def on_escape(self, now=None):
        """Тело улетело крылом от смерти (плагин survival) — не возвращаться в тот же бой: город, восстановление."""
        now = now or self.clock()
        if not self.st:
            self.new_day(now)
        if self.st.get("mode") == "hunt":
            self.note("routine_escape", "Еле ушёл крылом от смерти — отдохну в городе и восстановлюсь.", 3)
            await self.to_town(now, rest_minutes=self.cfg.get("after_death_rest_minutes", 10), recover=True)
        self.save()

    def diary(self, day_state):
        """Итог прошедшего дня — одно воспоминание без LLM."""
        start = datetime.strptime(day_state["day"], "%Y-%m-%d").replace(tzinfo=self.tz).timestamp() \
            + self.day_shift() * 3600                         # «день» жителя — с пробуждения
        end = start + 86400
        mem = self.mind.mem
        count = lambda kind: mem.db.execute(
            "SELECT COUNT(*) FROM events WHERE kind = ? AND ts >= ? AND ts < ?", (kind, start, end)).fetchone()[0]
        levels = [json.loads(r[0]).get("level") for r in mem.db.execute(
            "SELECT data FROM events WHERE kind = 'level_up' AND ts >= ? AND ts < ?", (start, end))]
        met = sorted({json.loads(r[0]).get("partner") for r in mem.db.execute(
            "SELECT data FROM events WHERE kind = 'meeting_confirmed' AND ts >= ? AND ts < ?", (start, end))} - {None})
        hunted = int(day_state.get("hunted", 0) / 60)
        parts = [f"охотился {hunted // 60} ч {hunted % 60} мин", f"победил {count('kill')} монстров"]
        deaths = count("died")
        if deaths:
            parts.append(f"погиб {deaths} раз")
        if levels:
            parts.append(f"достиг {max(l for l in levels if l is not None) if any(levels) else '?'} уровня")
        if met:
            parts.append("встречался с " + ", ".join(met))
        heals = count("heal_confirmed")
        if heals:
            parts.append(f"лечение в группе подтверждено сервером {heals} раз")
        for kind, label in (("gift_given", "отдал жителям"), ("gift_received", "получил от жителей")):
            n = count(kind)
            if n:
                parts.append(f"{label} {n} раз")
        text = f"Дневник {day_state['day']}: " + ", ".join(parts) + "."
        mem.remember(text, 3)
        mem.add_event("diary", {"day": day_state["day"], "text": text})
        if getattr(self.mind, "s", None) and self.mind.s.llm_enabled:
            # ORG-049: один вызов в сутки — пересказ дня своим голосом; факты — только из text выше.
            self.mind.trigger("напиши в remember запись дневника (2-3 предложения, своим голосом, importance 3) "
                              f"строго по фактам дня, ничего не добавляя: {text}", {"diary": day_state["day"]},
                              kind="diary")
        self.mind.write_decision({"type": "routine", "event": "diary", "text": text})
        log.info("%s", text)

    async def to_town(self, now, rest_minutes=None, recover=False):
        done = self.st["hunted"] >= self.st["budget"]
        rest = rest_minutes * 60 if rest_minutes is not None else self.minutes("break_minutes")
        self.st.update(mode="town", mode_since=now, arrived=False,
                       rest_until=now + rest if not done else float("inf"))
        if recover:
            self.st["recover"] = True
        self.set_goal(f"отдыхаю в {self.town['map']}" + (" и восстанавливаюсь" if recover else ""))
        hunted_min = int(self.st["hunted"] / 60)
        text = (f"Наохотился за день ({hunted_min} мин), иду в {self.town['map']} отдыхать и общаться."
                if done else f"Устал после охоты ({hunted_min} мин за день), иду в {self.town['map']} "
                             f"передохнуть минут {int(rest / 60)}.")
        self.note("routine_town", text, 2)
        self.last_sent = 0

    async def follow_lead(self, now, state, lead):
        """AUT-059: участник группы живёт в темпе лидера; безопасность (HP) — всё равно первой."""
        mode, lmap = lead
        if mode == "town" and self.st["mode"] == "hunt":
            self.note("routine_party_town", "Лидер группы отдыхает — иду к нему в город.", 1)
            await self.to_town(now)
        elif mode == "hunt":
            if lmap in self.mind.persona["hunt_maps"] and self.st.get("prefer_map") != lmap:
                self.st["prefer_map"] = lmap
                self.last_sent = 0
            if self.st["mode"] == "town":
                if not self.hp_ok(state):
                    party = getattr(self.mind, "party", None)
                    if party:
                        await party.need_recover()
                    return
                self.st.update(mode="hunt", mode_since=now, arrived=False, recover=False,
                               session_end=max(self.st["session_end"], self.st["hunted"] + 60))
                if self.st["hunted"] >= self.st["budget"]:
                    self.st["budget"] = self.st["hunted"] + 3600         # с группой можно и сверх нормы
                self.note("routine_party_hunt", f"Лидер группы охотится на {self.hunt_map()} — иду к нему.", 1)
                self.set_goal(f"охочусь в группе на {self.hunt_map()}")
                self.last_sent = 0

    async def in_town(self, now, state, own_schedule=True):
        at_town = (state.get("map") == self.town["map"] and state.get("x") is not None
                   and max(abs(int(state["x"]) - self.town["x"]), abs(int(state["y"]) - self.town["y"]))
                   <= self.town.get("radius", 3) + 2)
        vend = state.get("vend") or {}
        vending = self.cfg.get("vend_in_town") and vend.get("can")   # лавку открывают стоя (навык)
        if at_town and not self.st["arrived"]:
            self.st["arrived"] = True
            self.note("routine_arrived", f"Я в городе {self.town['map']}, отдыхаю.", 1)
            if self.cfg.get("sit_in_town", True) and not vending:
                await self.send({"action": "sit"}, "распорядок: сесть отдохнуть в городе")
        if (self.st["arrived"] and self.cfg.get("vend_in_town") and vend.get("can") and not vend.get("open")
                and now - self.last_vend >= RESEND):
            self.last_vend = now
            await self.send({"action": "shop_open"}, "распорядок: открыть лавку в городе")
        await self.service_check(now, state)
        if not self.hp_ok(state):
            await self.check_recover_blocked(now, state)
        if own_schedule and self.st["hunted"] < self.st["budget"] and now >= self.st["rest_until"]:
            if not self.hp_ok(state):                      # AUT-086: больной не идёт драться по расписанию
                if not self.waiting_hp_noted:
                    self.waiting_hp_noted = True
                    self.note("routine_wait_hp", f"Перерыв прошёл, но HP {state.get('hp_pct')}% — "
                                                 f"жду хотя бы {self.cfg.get('min_hp_to_hunt', 80)}%.", 1)
                return
            self.waiting_hp_noted = False
            self.st["recover"] = False
            self.st.pop("blocked", None)                 # новая сессия охоты — блокировка снята
            self.pick_map()
            left = self.st["budget"] - self.st["hunted"]
            self.st.update(mode="hunt", mode_since=now, arrived=False,
                           session_end=self.st["hunted"] + min(left, self.minutes("session_minutes")))
            self.note("routine_hunt", f"Отдохнул, иду качаться на {self.hunt_map()}.", 2)
            self.set_goal(f"охочусь на {self.hunt_map()}")
            self.last_sent = 0

    async def check_recover_blocked(self, now, state):
        """AUT-014: при весе >= 50% RO не восстанавливает HP, а зелий нет — сидеть бессмысленно.
        Попросить зелья у жителя рядом (economy) и один раз сообщить оператору (alert)."""
        stuck = (state.get("weight_pct") or 0) >= 50 and self.heal_items(state) == 0
        if not stuck:
            self.st.pop("recover_blocked_since", None)
            return
        since = self.st.setdefault("recover_blocked_since", now)
        if now - since < 300 or self.st.get("recover_blocked_noted"):
            return
        self.st["recover_blocked_noted"] = True
        self.note("recover_blocked", f"Вес {state.get('weight_pct')}% и нет зелий — HP не восстанавливается. "
                                     "Прошу зелья у жителей; нужна разгрузка.", 3)
        alert = getattr(self.mind, "alert", None)
        if alert:
            alert("recover_blocked", f"вес {state.get('weight_pct')}%, зелий нет, HP {state.get('hp_pct')}%")
        economy = getattr(self.mind, "economy", None)
        if economy:
            await economy.ask("501", 10, force=True)

    def set_goal(self, text):
        """AUT-098: цель в памяти следует за распорядком (модель может уточнить, но не отменить режим)."""
        self.mind.mem.set("goal", text)
        self.mind.mem.set("goal_source", "routine")

    async def enforce(self, now, state):
        """Сверка настройки OpenKore с режимом; при расхождении — команда, не чаще RESEND с."""
        if now - self.last_sent < RESEND:
            return
        may_move = getattr(self.mind, "may_move", None)
        if may_move and not may_move("routine")[0]:
            return                                   # AUT-005: телом сейчас владеет более важная задача
        if self.st["mode"] == "hunt" and (state.get("vend") or {}).get("open"):
            self.last_sent = now
            await self.send({"action": "shop_close"}, "распорядок: закрыть лавку перед охотой")
            return
        if self.st["mode"] == "hunt":
            ok = state.get("lock_map") == self.hunt_map() and state.get("lock_x") is None
            action = {"action": "hunt", "map": self.hunt_map()}
            reason = f"распорядок: охота на {self.hunt_map()}"
        else:
            ok = (state.get("lock_map") == self.town["map"] and state.get("lock_x") == self.town["x"]
                  and state.get("lock_y") == self.town["y"])
            action = {"action": "meet_point", "map": self.town["map"], "x": self.town["x"], "y": self.town["y"]}
            reason = f"распорядок: отдых в {self.town['map']}"
        if not ok:
            self.last_sent = now
            await self.send(action, reason)

    async def send(self, action, reason):
        await self.mind.execute([action], source="routine", reason=reason, protocol=True)

    def note(self, kind, text, importance):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, {"hunted_min": int(self.st["hunted"] / 60),
                                       "budget_min": int(self.st["budget"] / 60)})
        self.mind.write_decision({"type": "routine", "event": kind, "text": text,
                                  "hunted_min": int(self.st["hunted"] / 60), "mode": self.st["mode"]})
        log.info("распорядок: %s", text)

    # ---------- оператор ----------

    async def force(self, what):
        """Команда оператора для быстрой проверки: rest — в город сейчас, hunt — на охоту сейчас."""
        now = self.clock()
        if not self.st:
            self.new_day(now)
        if what == "rest":
            if self.st["mode"] == "town":
                return "уже отдыхает"
            await self.to_town(now)
        elif what == "hunt":
            if self.st["mode"] == "hunt":
                return "уже охотится"
            if self.st["hunted"] >= self.st["budget"]:
                self.st["budget"] = self.st["hunted"] + self.minutes("session_minutes")
            self.st["rest_until"] = now
            await self.in_town(now, self.mind.state)
            if self.st["mode"] != "hunt":
                self.save()
                return f"HP {self.mind.state.get('hp_pct')}% ниже {self.cfg.get('min_hp_to_hunt', 80)}% — сначала восстановлюсь"
        else:
            return "rest или hunt"
        self.save()
        return None

    # ---------- модель ----------

    def pick_map(self):
        """AUT-045: карта на новую сессию — по опыту (maps.py), если модель не выбрала сама."""
        maps = getattr(self.mind, "maps", None)
        if not maps or self.st.get("prefer_source") == "llm":
            self.st.pop("prefer_source", None)               # выбор модели действует одну сессию
            return
        bans = self.bans()
        self.grow(maps)
        choice, why = maps.choose(self.mind.persona["hunt_maps"], bans, level=self.mind.state.get("lv"),
                                  needs=getattr(self.mind, "needs", None), rng=self.rng)
        if choice != self.st.get("prefer_map"):
            self.note("routine_map_choice", f"На охоту пойду на {choice}: {why}.", 1)
        self.st["prefer_map"] = choice

    def grow(self, maps):
        """Свои карты стали слишком лёгкими для уровня — атлас советует места по силам.
        auto_hunt_maps: true — житель сам добавляет одно новое место в сутки; иначе только запись и
        оповещение владельцу (карты нужно включить на сервере: scripts/lab doctor, «карты мира»)."""
        state = self.mind.state
        if not state.get("lv") or self.st.get("grow_day") == self.st.get("day"):
            return
        advice = maps.advice(self.mind.persona["hunt_maps"], state["lv"], state.get("job"))
        if not advice:
            return
        self.st["grow_day"] = self.st.get("day")
        learn = getattr(self.mind, "learn_hunt_map", None)
        if self.cfg.get("auto_hunt_maps") and learn:
            new = next((m for m in advice if m not in self.mind.persona["hunt_maps"]), None)
            if new and learn(new):
                self.note("routine_grow", f"Мои места охоты стали слишком лёгкими для {state['lv']} уровня — "
                                          f"осваиваю {new}.", 3)
            return
        self.note("routine_grow_advice", f"Мои места охоты слишком лёгкие для {state['lv']} уровня; по атласу "
                                         f"подойдут: {', '.join(advice)}.", 2)
        alert = getattr(self.mind, "alert", None)
        if alert:
            alert("growth", f"{state.get('name')}: lv {state['lv']}, места охоты слишком лёгкие; атлас советует "
                            f"{', '.join(advice)} (включить карты и auto_hunt_maps)", every=86400)

    def prefer(self, hunt_map):
        """Модель выбрала карту: в охоте — сразу, в городе — на следующую сессию."""
        if hunt_map not in self.mind.persona["hunt_maps"]:
            return "карта не из списка hunt_maps"
        self.st["prefer_map"] = hunt_map
        self.st["prefer_source"] = "llm"
        self.last_sent = 0
        self.save()
        return None if self.st.get("mode") == "hunt" else "запомнил: пойду туда после отдыха"

    @property
    def in_town_mode(self):
        return self.st.get("mode") == "town"

    @property
    def sleeping(self):
        return self.st.get("mode") == "sleep"
