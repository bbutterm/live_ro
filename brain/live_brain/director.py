"""Рассказчик мира — режиссёр без LLM (ORG-086). Правила, только факты шины мира.

Один житель на мир — «режиссёр»: минимальное имя среди онлайн (как лидер в party.py). Онлайн — я сам со свежим
состоянием тела и жители со свежей записью presence в шине (crowd.py, ORG-089); без crowd — все запущенные (ctx.peers).
Раз в check_minutes режиссёр смотрит в шину мира за window_hours и решает, нужен ли миру повод или затишье:

    напряжение = 1·death_report + 1·map_banned + 0.5·слух danger;
        ≥ danger_threshold → caution «день осторожности» (caution_hours): safety ×1.3, rest ×1.2, curiosity ×0.8,
        допуск риска карт (needs.risk_tolerance) ×0.8;
    гибель жителя за help_hours (3 ч), напряжение ниже порога, день → help «просьба помочь»: care ×1.5, social ×1.2
        (помочь, утешить, подарить) — тишину не ждёт: сама смерть — событие;
    тишина — за quiet_hours ни одного события важности ≥ 2 (кроме закулисья и снимков), день, мозг режиссёра
        работает не меньше quiet_hours (иначе «тихо» значит «меня не было») → один повод:
        gathering   — за 2 ч до вечернего круга (tradition.py) или во время него: social ×1.4 до конца окна;
        rich_rumor  — только правда: своя карта (maps.stats ≥ 60 мин без смертей, очки ≥ rumors.RICH_SCORE) →
                      rumors.share; или подтверждённый кем-то слух rich (rumor_checked ok за 48 ч) → пересказ
                      [info:rich:<карта>:1:<автор>]; нет фактов — повода нет;
        expedition  — explore включён: curiosity ×1.4;
        contest     — онлайн ≥ 2: progress ×1.3 («кто больше до вечера»);
        boss_call   — только при boss.enabled (ORG-079): лидер группы охотнее предлагает поход на мини-босса.
        Порядок: gathering → вид, который дольше всех не был (по шине).

Режиссёр НИЧЕГО не приказывает телу: только множители мотивов (needs.weighted → выбор занятий и карт), допуск риска,
свой правдивый слух. Участие добровольное: множитель m смягчается характером — 1 + (m − 1)·w, w от черты
(caution: 1 − 0.5·смелость; help: 0.5 + 0.5·щедрость; gathering: 0.5 + 0.5·общительность; expedition: любопытство;
contest и boss_call: (смелость + усердие)/2); итог в пределах 0.7..1.5.

Лимиты: один действующий инцидент; между поводами не меньше gap_hours; поводов не больше max_per_day за сутки мира;
caution — сверх суточного лимита, но не чаще раза в caution_hours. Лимиты считаются по шине (любой автор), поэтому
смена режиссёра (уснул, пришёл житель с именем меньше) не удваивает события.

Запись: шина, вид director (важность 1: не в новостях промпта), {incident, label, until, needs, risk, params, why,
tension}. Закулисье (world_bus.BACKSTAGE): летопись и серия недели его не видят, дашборд владельца — видит.
Решения и пропуски — decisions.jsonl (type director); житель, узнавший новый инцидент, пишет event heard.
Выключатель: BRAIN_DISABLE=director или goals.json "director": {"enabled": false}; нет шины — модуль молчит.
"""
import logging
import random
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("director")

DEFAULTS = {"enabled": True, "check_minutes": 30, "window_hours": 12, "quiet_hours": 3, "danger_threshold": 3,
            "max_per_day": 2, "gap_hours": 3, "incident_hours": 3, "caution_hours": 6, "help_hours": 3,
            "rumor_hours": 48, "gathering_lead_hours": 2, "presence_minutes": 15, "night_hours": [1, 7],
            "cache_seconds": 60}
FACTOR_MIN, FACTOR_MAX = 0.7, 1.5
CAUTION_RISK = 0.8
SNAPSHOTS = {"rival_score", "presence", "tradition_strength"}     # снимки состояния — не «события» для тишины
KINDS = {   # вид -> подпись и множители мотивов
    "caution": {"label": "день осторожности", "needs": {"safety": 1.3, "rest": 1.2, "curiosity": 0.8}},
    "help": {"label": "просьба помочь", "needs": {"care": 1.5, "social": 1.2}},
    "gathering": {"label": "вечерний сбор", "needs": {"social": 1.4}},
    "rich_rumor": {"label": "слух о богатом месте", "needs": {"curiosity": 1.2, "wealth": 1.1}},
    "expedition": {"label": "повод для экспедиции", "needs": {"curiosity": 1.4}},
    "contest": {"label": "вызов: кто больше побед до вечера", "needs": {"progress": 1.3}},
    "boss_call": {"label": "общий поход на мини-босса", "needs": {"progress": 1.1}},
}
STIR = ("help", "gathering", "rich_rumor", "expedition", "contest", "boss_call")


def clamp(x):
    return max(FACTOR_MIN, min(FACTOR_MAX, float(x)))


class Director:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES, ARGS = "director", "director", "director", True, ("world",), "world"
    TICK_ORDER = 230

    def __init__(self, mind, world=None, clock=None, rng=None):
        self.mind = mind
        world = world or {}
        self.world = world
        self.cfg = dict(DEFAULTS, **(world.get("director") or {}))
        self.tz = timezone(timedelta(hours=world.get("timezone_offset_hours", 0)))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.started = self.clock()
        self.next_check = 0.0
        self.cache = (0.0, None)                      # (когда прочитано, действующий инцидент)
        self.heard = None                             # id последнего узнанного инцидента

    # ---------- данные ----------

    @property
    def bus(self):
        return getattr(getattr(self.mind, "world", None), "bus", None)

    def me(self):
        return self.mind.state.get("name") or self.mind.persona.get("name")

    def trait(self, name):
        return (getattr(getattr(self.mind, "needs", None), "t", None) or {}).get(name, 0.5)

    def local(self, now):
        return datetime.fromtimestamp(now, self.tz)

    def day_start(self, now):
        return self.local(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def night(self, now):
        social = getattr(self.mind, "social", None)
        if social is not None and hasattr(social, "is_night"):
            try:
                return bool(social.is_night(now))
            except Exception:                         # шпион/битый конфиг — по своему окну
                pass
        lo, hi = self.cfg["night_hours"]
        h = self.local(now).hour
        return lo <= h < hi if lo <= hi else (h >= lo or h < hi)

    def awake(self):
        r = getattr(self.mind, "routine", None)
        st = getattr(r, "st", None) if r else None
        return bool(getattr(self.mind, "fresh_state", False) and self.mind.state.get("map")
                    and not self.mind.state.get("dead") and not (isinstance(st, dict) and st.get("mode") == "sleep"))

    def recent(self, kind, since):
        bus = self.bus
        if bus is None or not hasattr(bus, "recent"):
            return []
        try:
            return bus.recent(kind, since)
        except Exception as e:                        # общая БД занята — в этот раз без режиссёра
            log.warning("шина мира недоступна: %s", e)
            return []

    def events(self, since):
        bus = self.bus
        if bus is None or not hasattr(bus, "read"):
            return []
        try:
            return bus.read(since=since, limit=1000)
        except Exception as e:
            log.warning("шина мира недоступна: %s", e)
            return []

    # ---------- кто режиссёр ----------

    def online(self, now=None):
        now = now or self.clock()
        out = {self.me()} if self.awake() else set()
        if getattr(self.mind, "crowd", None) is not None:
            for rec in self.recent("presence", now - self.cfg["presence_minutes"] * 60):
                # review3: спящий (presence mode sleep) — не онлайн, как и я сам в awake(): иначе он держит роль
                if rec["bot"] in self.mind.ctx.peers and (rec.get("data") or {}).get("mode") != "sleep":
                    out.add(rec["bot"])
        else:
            out |= set(self.mind.ctx.peers)
        return out

    def is_director(self, now=None):
        on = self.online(now)
        return self.awake() and bool(on) and min(on) == self.me()

    # ---------- драматургия ----------

    def tension(self, now=None):
        now = now or self.clock()
        since = now - self.cfg["window_hours"] * 3600
        deaths = bans = danger = 0
        last = 0.0
        from .world_bus import BACKSTAGE, QUIET
        for e in self.events(since):
            k = e["kind"]
            if k == "death_report":
                deaths += 1
            elif k == "map_banned":
                bans += 1
            elif k == "rumor" and (e["data"] or {}).get("what") == "danger":
                danger += 1
            if e["importance"] >= 2 and k not in QUIET and k not in BACKSTAGE and k not in SNAPSHOTS:
                last = max(last, e["ts"])
        quiet = (now - last) / 3600 if last else self.cfg["window_hours"]
        return {"score": round(deaths + bans + 0.5 * danger, 2), "deaths": deaths, "bans": bans, "danger": danger,
                "quiet_hours": round(quiet, 2)}

    def history(self, now):
        """Решения режиссёра (любого) за сутки: для лимитов и чередования видов."""
        return self.recent("director", now - 86400)

    def current(self, now=None):
        """Действующий инцидент: последняя запись director с until > now (кэш cache_seconds)."""
        now = now or self.clock()
        ts, cur = self.cache
        if ts and now - ts < self.cfg["cache_seconds"] and (cur is None or cur.get("until", 0) > now):
            return cur
        rows = self.recent("director", now - 2 * 86400)
        cur = None
        if rows:
            last = rows[-1]
            d = dict(last["data"] or {}, id=last["id"], by=last["bot"], ts=last["ts"])
            if d.get("until", 0) > now and d.get("incident") in KINDS:
                cur = d
        self.cache = (now, cur)
        return cur

    def limits(self, now, kind):
        """None — можно; иначе почему нельзя."""
        rows = self.history(now)
        cur = self.current(now)
        if kind == "caution":
            if cur and cur.get("incident") == "caution":
                return "уже день осторожности"
            if any(r["data"].get("incident") == "caution" and now - r["ts"] < self.cfg["caution_hours"] * 3600
                   for r in rows):
                return f"осторожность не чаще раза в {self.cfg['caution_hours']} ч"
            return None
        if cur:
            return f"действует «{cur.get('label')}»"
        if rows and now - rows[-1]["ts"] < self.cfg["gap_hours"] * 3600:
            return f"повод не чаще раза в {self.cfg['gap_hours']} ч"
        today = [r for r in rows if r["ts"] >= self.day_start(now) and r["data"].get("incident") in STIR]
        if len(today) >= self.cfg["max_per_day"]:
            return f"поводов за сутки уже {len(today)}"
        return None

    # ---------- поводы ----------

    def help_fact(self, now):
        since = now - self.cfg["help_hours"] * 3600
        for e in reversed(self.events(since)):
            if e["kind"] == "death_report":
                return {"who": e["bot"], "map": (e["data"] or {}).get("map")}
        return None

    def gathering_fact(self, now):
        t = getattr(self.mind, "tradition", None)
        if t is None or not hasattr(t, "window"):
            return None
        try:
            lo, hi = t.window(t.date(now))
        except Exception:
            return None
        if lo - self.cfg["gathering_lead_hours"] * 3600 <= now < hi:
            return {"until": hi, "point": (getattr(t, "point", None) or {}).get("label")}
        return None

    def rich_fact(self, now):
        """Правдивый повод «на X богатая охота»: свой опыт карты или подтверждённый кем-то слух."""
        from .rumors import RICH_SCORE
        maps = getattr(self.mind, "maps", None)
        best, best_s = None, RICH_SCORE
        if maps is not None:
            try:
                for hmap, m in (maps.stats() or {}).items():
                    s = maps.score(hmap)
                    if s is not None and m.get("minutes", 0) >= 60 and not m.get("deaths") and s >= best_s:
                        best, best_s = hmap, s
            except (AttributeError, TypeError):
                best = None
        if best:
            return {"map": best, "author": self.me(), "src": "own", "score": round(best_s)}
        for e in reversed(self.events(now - self.cfg["rumor_hours"] * 3600)):
            d = e["data"] or {}
            if e["kind"] == "rumor_checked" and d.get("ok") and d.get("what") == "rich" and d.get("map"):
                return {"map": d["map"], "author": e["bot"], "src": "checked"}
        return None

    def stir_options(self, now):
        """Поводы тишины с фактами: [(вид, params, почему)] в порядке приоритета (help — отдельно, в direct)."""
        out = []
        g = self.gathering_fact(now)
        if g:
            out.append(("gathering", g, "скоро вечерний круг — позвать всех"))
        rest = []
        r = self.rich_fact(now)
        if r:
            rest.append(("rich_rumor", r, f"на {r['map']} богатая охота ({'свой опыт' if r['src'] == 'own' else 'подтвердил ' + r['author']})"))
        if (self.world.get("explore") or {}).get("enabled") and getattr(self.mind, "explorer", None) is not None:
            rest.append(("expedition", {}, "давно не открывали новых мест"))
        if len(self.online(now)) >= 2:
            rest.append(("contest", {}, "кто больше побед до вечера"))
        if (self.world.get("boss") or {}).get("enabled"):
            rest.append(("boss_call", {}, "пора проверить силы на мини-боссе"))
        last_used = {}
        for row in self.recent("director", now - 7 * 86400):
            last_used[row["data"].get("incident")] = row["ts"]
        rest.sort(key=lambda o: (last_used.get(o[0], 0), STIR.index(o[0])))
        return out + rest

    async def tick(self):
        now = self.clock()
        cur = self.current(now)
        if cur and cur.get("id") != self.heard:
            self.heard = cur.get("id")
            self.mind.write_decision({"type": "director", "event": "heard", "incident": cur.get("incident"),
                                      "by": cur.get("by"), "until": cur.get("until")})
        if now < self.next_check:
            return
        self.next_check = now + self.cfg["check_minutes"] * 60
        if self.bus is None or not self.is_director(now):
            return
        await self.direct(now)

    async def direct(self, now):
        t = self.tension(now)
        if t["score"] >= self.cfg["danger_threshold"]:
            why = self.limits(now, "caution")
            if why is None:
                await self.publish(now, "caution", {}, f"тяжёлый день: смертей {t['deaths']}, исключённых карт "
                                                       f"{t['bans']}, слухов об опасности {t['danger']}", t,
                                   hours=self.cfg["caution_hours"])
            else:
                self.skip(why, t)
            return
        help_ = self.help_fact(now)                   # гибель — повод сразу, тишину не ждём (сама смерть — событие)
        if help_ and not self.night(now) and self.limits(now, "stir") is None:
            await self.publish(now, "help", help_, f"{help_['who']} погиб(ла) на {help_.get('map')} — помочь и "
                                                   f"поддержать", t, hours=self.cfg["incident_hours"])
            return
        if t["quiet_hours"] < self.cfg["quiet_hours"]:
            return
        if now - self.started < self.cfg["quiet_hours"] * 3600:
            return self.skip("мозг режиссёра работает меньше quiet_hours — тишину не видно", t)
        if self.night(now):
            return self.skip("ночь — поводов нет", t)
        why = self.limits(now, "stir")
        if why:
            return self.skip(why, t)
        options = self.stir_options(now)
        if not options:
            return self.skip("нет повода, подкреплённого фактами", t)
        kind, params, reason = options[0]
        hours = self.cfg["incident_hours"]
        until = None
        if kind == "gathering":
            until = params.pop("until", None)
        if kind == "rich_rumor" and not await self.tell_rich(params):
            return self.skip("слух не отправлен", t)
        await self.publish(now, kind, params, reason, t, hours=hours, until=until)

    async def tell_rich(self, params):
        """Правдивый слух: свой — rumors.share (метка [info:rich:<карта>]); подтверждённый другим — пересказ."""
        rumors = getattr(self.mind, "rumors", None)
        if rumors is None:
            return False
        if params.get("src") == "own":
            await rumors.share(params["map"], "rich")
            return True
        from .rumors import tag
        for peer in sorted(self.mind.ctx.peers - {params["author"]}):
            await self.mind.execute([{"action": "whisper", "to": peer, "text": tag("rich", params["map"], 1,
                                                                                  params["author"])}],
                                    source="rule", reason=f"режиссёр: пересказ слуха rich {params['map']}",
                                    protocol=True)
        return True

    async def publish(self, now, kind, params, why, tension, hours, until=None):
        spec = KINDS[kind]
        data = {"incident": kind, "label": spec["label"], "until": until or now + hours * 3600,
                "needs": spec["needs"], "risk": CAUTION_RISK if kind == "caution" else 1.0, "params": params,
                "why": why, "tension": tension}
        try:
            self.bus.publish("director", data, 1, now=now)
        except Exception as e:
            log.warning("шина мира недоступна: %s", e)
            return
        self.cache = (0.0, None)
        self.mind.write_decision({"type": "director", "event": "incident", **data})
        log.info("режиссёр: %s — %s", spec["label"], why)

    def skip(self, why, tension):
        self.mind.write_decision({"type": "director", "event": "skip", "why": why, "tension": tension})

    # ---------- влияние на жителя ----------

    def weight(self, kind):
        if kind == "caution":
            return 1 - 0.5 * self.trait("bravery")
        if kind == "help":
            return 0.5 + 0.5 * self.trait("generosity")
        if kind == "gathering":
            return 0.5 + 0.5 * self.trait("sociability")
        if kind in ("expedition", "rich_rumor"):
            return self.trait("curiosity")
        return (self.trait("bravery") + self.trait("diligence")) / 2

    def boost(self, need):
        cur = self.current()
        if not cur:
            return 1.0
        m = (cur.get("needs") or {}).get(need)
        if m is None:
            return 1.0
        return round(clamp(1 + (float(m) - 1) * self.weight(cur.get("incident"))), 3)

    def risk_factor(self):
        cur = self.current()
        if not cur or cur.get("incident") != "caution":
            return 1.0
        try:
            return max(0.5, min(1.0, float(cur.get("risk", CAUTION_RISK))))
        except (TypeError, ValueError):
            return CAUTION_RISK

    def active(self, kind=None):
        """Для других модулей (boss.py): действующий инцидент (вида kind) или None."""
        cur = self.current()
        return cur if cur and (kind is None or cur.get("incident") == kind) else None
