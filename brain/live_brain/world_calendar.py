"""Календарь мира (ORG-059): неделя, сезоны, праздники и дни рождения. Правила без LLM.

Данные — brain/world/calendar.json (дни недели с множителями мотивов, сезоны по месяцу, праздники MM-DD,
world_born — день рождения мира), дни рождения жителей — поле born реестра brain/world/roster.json.
Дата — по часовому поясу мира (goals.json timezone_offset_hours).

Где действует:
    needs.weighted — множитель мотива дня (need_factor: произведение дня недели и праздников, 0.7..1.5),
                     рядом с aims.boost: в субботу (рыночный день) сильнее wealth и social;
    chronicle      — заголовок дня: «суббота, рыночный день; Праздник урожая»;
    social         — темы holiday (праздник) и birthday (день рождения собеседника), поставщики
                     holiday_facts / birthday_facts (peer, now) -> dict | None. Поздравление — один раз за день
                     каждому жителю (kv calendar_told: курсор по событиям social_said, тема holiday/birthday).
Модуль называется world_calendar, чтобы не затенять calendar из стандартной библиотеки.
Выключатель: BRAIN_DISABLE=calendar или goals.json "calendar": {"enabled": false}.
"""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORLD = Path(__file__).resolve().parents[1] / "world"
PATH = WORLD / "calendar.json"
ROSTER = WORLD / "roster.json"
FACTOR_MIN, FACTOR_MAX = 0.7, 1.5
WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
TOPICS = ("holiday", "birthday")
# фразы по умолчанию, если в персоне нет своих (без метки ≤ 60 символов с самой длинной подстановкой)
PHRASES = {
    "holiday": ["С праздником! Сегодня {holiday}.", "Сегодня {holiday} — хороший день.",
                "Слышал(а)? Сегодня {holiday}!", "{holiday}! Отдохнём сегодня?"],
    "holiday_re": ["И тебя с праздником!", "Спасибо, и тебя!", "Да, праздник — хорошо!"],
    "birthday": ["С днём рождения, {birthday}!", "{birthday}, поздравляю с днём рождения!",
                 "Сегодня твой день, {birthday}!"],
    "birthday_re": ["Спасибо, что помнишь!", "Ой, спасибо! Приятно.", "Спасибо! Отпразднуем?"],
}


def load(path=None):
    """calendar.json; нет файла или он битый — пустой календарь (множители 1.0, праздников нет)."""
    try:
        return json.loads(Path(path or PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_roster(path=None):
    try:
        return json.loads(Path(path or ROSTER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def clamp(x):
    return max(FACTOR_MIN, min(FACTOR_MAX, float(x)))


def _mmdd(text):
    """'2026-02-29' -> '02-29'; не дата — None."""
    try:
        return datetime.strptime(str(text), "%Y-%m-%d").strftime("%m-%d")
    except (TypeError, ValueError):
        return None


def _matches(mmdd, local):
    """Дата MM-DD приходится на этот день; 29 февраля в невисокосный год отмечается 28 февраля."""
    if mmdd == local.strftime("%m-%d"):
        return True
    if mmdd == "02-29" and local.month == 2 and local.day == 28:
        try:
            local.replace(day=29)
        except ValueError:
            return True
    return False


def today(now, tz_hours, cal, roster=None, world_born=None):
    """День мира: {"date", "weekday": 1..7, "weekday_name", "day_name", "season", "season_name",
    "holidays": [{"id", "name"}], "birthdays": [имя], "needs": {мотив: множитель}}."""
    local = datetime.fromtimestamp(now, timezone(timedelta(hours=tz_hours or 0)))
    cal = cal or {}
    wd = local.isoweekday()
    wcfg = (cal.get("weekdays") or {}).get(str(wd)) or {}
    season = next((s for s in cal.get("seasons") or [] if local.month in (s.get("months") or [])), {})
    holidays = [{"id": h.get("id"), "name": h.get("name"), "needs": h.get("needs") or {}}
                for h in cal.get("holidays") or [] if h.get("date") and _matches(h["date"], local)]
    born = world_born or cal.get("world_born")
    if born and _mmdd(born) and _matches(_mmdd(born), local) and local.year > int(str(born)[:4]):
        holidays.append({"id": "world_day", "name": f"День мира ({local.year - int(str(born)[:4])}-я годовщина)",
                         "needs": {"social": 1.2}})
    birthdays = []
    for rec in ((roster or {}).get("residents") or {}).values():
        if isinstance(rec, dict) and rec.get("name") and _mmdd(rec.get("born")) and _matches(_mmdd(rec["born"]), local):
            birthdays.append(rec["name"])
    needs = {}
    for src in [wcfg.get("needs") or {}] + [h["needs"] for h in holidays]:
        for k, v in src.items():
            try:
                needs[k] = needs.get(k, 1.0) * float(v)
            except (TypeError, ValueError):
                continue
    return {"date": local.strftime("%Y-%m-%d"), "weekday": wd, "weekday_name": WEEKDAYS_RU[wd - 1],
            "day_name": wcfg.get("name"), "season": season.get("id"), "season_name": season.get("name"),
            "holidays": [{"id": h["id"], "name": h["name"]} for h in holidays], "birthdays": sorted(birthdays),
            "needs": {k: round(clamp(v), 3) for k, v in needs.items()}}


def need_factor(day, need):
    """Множитель мотива дня (произведение дня недели и праздников) в пределах 0.7..1.5; нет — 1.0."""
    try:
        return clamp(((day or {}).get("needs") or {}).get(need, 1.0))
    except (TypeError, ValueError):
        return 1.0


def header(day):
    """Строка дня для летописи: «суббота, рыночный день; Праздник урожая; день рождения: Vera (осень)»."""
    head = day["weekday_name"] + (f", {day['day_name']}" if day.get("day_name") else "")
    parts = [head] + [h["name"] for h in day.get("holidays") or [] if h.get("name")]
    if day.get("birthdays"):
        parts.append("день рождения: " + ", ".join(day["birthdays"]))
    return "; ".join(parts) + (f" ({day['season_name']})" if day.get("season_name") else "")


def header_for(date, tz_hours, cal=None, roster=None):
    """Заголовок для дня YYYY-MM-DD (полдень по часовому поясу мира) — для chronicle."""
    tz = timezone(timedelta(hours=tz_hours or 0))
    noon = datetime.strptime(date, "%Y-%m-%d").replace(hour=12, tzinfo=tz).timestamp()
    cal = load() if cal is None else cal
    if not cal:
        return None
    return header(today(noon, tz_hours, cal, load_roster() if roster is None else roster))


class WorldCalendar:
    """Календарь для жителя: день мира с кешем по дате, множители мотивов и темы разговора."""

    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES, ARGS = "calendar", "calendar", "calendar", True, ("world",), "world"
    PROMPT = [("день_мира", "summary", 170)]

    def __init__(self, mind, world=None, cal=None, roster=None, clock=None):
        self.mind = mind
        self.tz_hours = (world or {}).get("timezone_offset_hours", 0)
        self.cal = load() if cal is None else cal
        self.roster = load_roster() if roster is None else roster
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self._day = None
        self._key = None

    def day(self, now=None):
        now = self.clock() if now is None else now
        local = datetime.fromtimestamp(now, timezone(timedelta(hours=self.tz_hours or 0)))
        key = local.strftime("%Y-%m-%d")
        if key != self._key:
            self._key = key
            self._day = today(now, self.tz_hours, self.cal, self.roster, self.mind.mem.get("world_born"))
        return self._day

    def factor(self, need, now=None):
        return need_factor(self.day(now), need)

    # ---------- темы разговора (social.py) ----------

    def told(self, now=None):
        """Кому уже поздравления сегодня: курсор по событиям social_said (тема holiday/birthday)."""
        mem = self.mind.mem
        date = self.day(now)["date"]
        st = mem.get("calendar_told") or {}
        cursor = mem.get("calendar_cursor")
        if cursor is None:                                  # первый запуск: историю не переносим
            cursor = mem.db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        rows = mem.db.execute("SELECT id, data FROM events WHERE id > ? AND kind = 'social_said' ORDER BY id",
                              (cursor,)).fetchall()
        changed = mem.get("calendar_cursor") is None
        for i, data in rows:
            cursor, changed = i, True
            try:
                d = json.loads(data)
            except ValueError:
                continue
            if d.get("topic") in TOPICS and d.get("peer"):
                rec = st.get(d["peer"]) or {}
                rec = rec if rec.get("date") == date else {"date": date, "topics": []}
                rec["topics"] = sorted(set(rec["topics"]) | {d["topic"]})
                st[d["peer"]] = rec
        st = {p: r for p, r in st.items() if r.get("date") == date}
        if changed:
            mem.set("calendar_told", st)
            mem.set("calendar_cursor", cursor)
        return st

    def _fresh(self, peer, topic, now):
        return topic not in (self.told(now).get(peer) or {}).get("topics", [])

    def holiday_facts(self, peer, now=None):
        """Поставщик темы holiday: праздник сегодня и этого жителя ещё не поздравлял -> {"holiday": имя}."""
        day = self.day(now)
        if not peer or not day["holidays"] or not self._fresh(peer, "holiday", now):
            return None
        return {"holiday": day["holidays"][0]["name"]}

    def birthday_facts(self, peer, now=None):
        """Поставщик темы birthday: сегодня день рождения собеседника и не поздравлял -> {"birthday": имя}."""
        day = self.day(now)
        if not peer or peer not in day["birthdays"] or not self._fresh(peer, "birthday", now):
            return None
        return {"birthday": peer}

    def topic_facts(self, peer, now=None):
        """Факты обеих тем для подстановки в facts() social.py (пусто — тем нет)."""
        out = {}
        for provider in (self.holiday_facts, self.birthday_facts):
            out.update(provider(peer, now) or {})
        return out

    def summary(self, now=None):
        """Для промпта: какой сегодня день мира."""
        return header(self.day(now))


def install(social, mind):
    """Подключение к реестру тем social.py (ORG-066, Social.register_topic), когда он есть: темы holiday, birthday.
    Возвращает True, если темы зарегистрированы."""
    cal = getattr(mind, "calendar", None)
    reg = getattr(social, "register_topic", None)
    if not cal or not reg:
        return False
    reg("holiday", cal.holiday_facts)
    reg("birthday", cal.birthday_facts)
    return True
