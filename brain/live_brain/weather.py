"""Погода мира (ORG-085): общий детерминированный факт вместо выдуманного. Без LLM, без состояния.

Погода — функция местного времени мира (timezone_offset_hours): у всех жителей в одну минуту одна и та же.
Сутки делятся на 8 блоков по 3 часа; погода меняется только на границе блока (не чаще раза в 3 ч).
Блок 0 суток — по распределению сезона (сезон — по месяцу; после календаря мира ORG-059 —
TODO(ORG-059): брать world_calendar.today(...)["season"]); следующий блок — марковский переход:
с вероятностью STAY[вид] погода держится, иначе — новая по распределению сезона.
Случайность — sha256(seed|YYYY-MM-DD|блок|соль), без глобального random: повторяемо и одинаково у всех.

В жизни: тема разговора weather (social.py, через реестр тем) говорит фразой weather_<вид>
(нет таких фраз — общей weather); прогулка (activity.py, занятие stroll) — множитель stroll_factor.
Выключатель: BRAIN_DISABLE=weather — погода не называется (общие фразы weather как раньше), прогулка без множителя.
"""
import hashlib
from datetime import datetime, timedelta, timezone

KINDS = ("clear", "cloudy", "rain", "wind", "hot", "cold")
LABEL = {"clear": "ясно", "cloudy": "облачно", "rain": "дождь", "wind": "ветрено", "hot": "жара", "cold": "холодно"}
BLOCK_H = 3
SEASON = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
          6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}
DIST = {   # сезон -> вероятности видов (сумма 1); зимой жары нет, летом нет холода
    "winter": {"cold": 0.35, "cloudy": 0.30, "clear": 0.15, "wind": 0.15, "rain": 0.05, "hot": 0.0},
    "spring": {"clear": 0.30, "cloudy": 0.25, "rain": 0.25, "wind": 0.15, "hot": 0.03, "cold": 0.02},
    "summer": {"hot": 0.30, "clear": 0.35, "cloudy": 0.15, "rain": 0.12, "wind": 0.08, "cold": 0.0},
    "autumn": {"cloudy": 0.30, "rain": 0.30, "wind": 0.20, "clear": 0.15, "cold": 0.05, "hot": 0.0},
}
STAY = {"clear": 0.65, "cloudy": 0.5, "rain": 0.45, "wind": 0.4, "hot": 0.6, "cold": 0.6}   # марковская таблица
STROLL = {"rain": 0.8, "clear": 1.1}


def unit(*parts):
    """Детерминированное число [0, 1) из частей."""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64


def draw(season, u):
    acc = 0.0
    for kind in KINDS:
        acc += DIST[season].get(kind, 0.0)
        if u < acc:
            return kind
    return max(DIST[season], key=DIST[season].get)


def season_of(month):
    return SEASON[month]


def day_chain(date, season, seed="live_ro"):
    """Погода 8 блоков суток: [вид блока 0, ..., вид блока 7]."""
    kinds = [draw(season, unit(seed, date, 0, "start"))]
    for b in range(1, 24 // BLOCK_H):
        prev = kinds[-1]
        if unit(seed, date, b, "stay") < STAY[prev] and DIST[season].get(prev, 0) > 0:
            kinds.append(prev)
        else:
            kinds.append(draw(season, unit(seed, date, b, "new")))
    return kinds


def weather(ts, tz_hours, seed="live_ro"):
    """{"kind", "label", "since_hour", "season"} по местному времени мира; у всех жителей одинаково."""
    d = datetime.fromtimestamp(ts, timezone(timedelta(hours=tz_hours)))
    season = season_of(d.month)
    chain = day_chain(d.strftime("%Y-%m-%d"), season, seed)
    block = d.hour // BLOCK_H
    kind = chain[block]
    start = block
    while start > 0 and chain[start - 1] == kind:
        start -= 1
    return {"kind": kind, "label": LABEL[kind], "since_hour": start * BLOCK_H, "season": season}


def stroll_factor(kind):
    """Множитель желания прогуляться: дождь — реже, ясно — охотнее."""
    return STROLL.get(kind, 1.0)


def kind_now(mind, now):
    """Вид погоды сейчас для жителя (часовой пояс мира — из social); выключено — None."""
    s = getattr(mind, "s", None)
    if s is not None and hasattr(s, "feature") and not s.feature("weather"):
        return None
    social = getattr(mind, "social", None)
    return weather(now, getattr(social, "tz_hours", 0))["kind"]
