"""Мемуары жителя (ORG-082, ТЗ Т-19): книга жизни от первого лица по неделям — только факты памяти, без LLM.

Источник — события памяти state/<bot>/memory.sqlite (только чтение) и kv places/last_state. Глава — неделя (с
понедельника, часовой пояс мира); первая глава — неделя первого события памяти («Я появился в мире …»). Абзацы
по видам событий, каждая строка — пересказ фактов, ничего не придумывается:
    уровни (level_up), профессия (job_changed), смерти (death_report/died: где, кто бил), друзья (встречи
    meeting_confirmed, подарки gift_given/gift_received, лечение heal_confirmed, ссоры и примирения society_*),
    мечта (dream_new/stage/done/changed, ORG-081), копилка (savings_progress 100 %, ORG-073), места (explore_found и
    kv places.first), карты (card_found), питомец (pet_tamed/pet_hatched), гильдия (guild_founded/guild_joined),
    цели недели (aim_done). Пустая неделя — «Тихая неделя». Род глагола — по state.sex (Male/Female), иначе «(а)».
В конце главы — «Факты:» виды и число событий недели (проверка: каждая строка из памяти).

LLM-окраска (по умолчанию выкл.): --llm или goals.json "memoir": {"llm": true}, BRAIN_LLM включён и общий бюджет
жителей (budget.py) даёт резерв — один вызов на главу, ответ проходит фильтр дневника routine.diary_only (числа и
латинские имена — только из фактов главы, одна запись); отклонённый — остаётся текст правил. Кэш —
run/memoir-<bot>-<неделя>.json: повторный запуск не тратит вызов.

Модуль мозга Memoir (реестр, тик 230): раз в неделю, когда неделя закончилась, пишет всю книгу (до прошлой недели)
в state/<bot>/memoir.md (не в git) и событие memoir_chapter {week, title}. По запросу:
    python3 -m live_brain.memoir --lab-root /opt/ro-bot-lab --bot bot01 [2026-W40|2026-10-02] [--llm] [--write]
    scripts/lab memoir BOT [НЕДЕЛЯ] [--llm]
"""
import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from .episode import week_bounds

log = logging.getLogger("memoir")

WORLD = Path(__file__).resolve().parents[1] / "world"
DEFAULTS = {"enabled": True, "llm": False, "tick_seconds": 600}
KINDS = ("level_up", "job_changed", "died", "death_report", "meeting_confirmed", "gift_given", "gift_received",
         "heal_confirmed", "society_quarrel", "society_reconciled", "dream_new", "dream_stage", "dream_done",
         "dream_changed", "savings_progress", "explore_found", "card_found", "pet_tamed", "pet_hatched",
         "guild_founded", "guild_joined", "aim_done")
LLM_MAX_TOKENS = 700
LLM_MAX_TEXT = 1500
MAX_NAMES = 6


def _loads(text):
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def g(sex, m, f):
    """Род глагола: «дорос»/«доросла»/«дорос(ла)»; f — полная женская форма."""
    if sex == "Male":
        return m
    if sex == "Female":
        return f
    if f.startswith(m):                                   # дорос(ла), был(а)
        return f"{m}({f[len(m):]})"
    if m.endswith("ся") and f.endswith("сь") and m[:-3] == f[:-4]:   # появился(ась)
        return f"{m}({f[-3:]})"
    return f"{m}/{f}"                                     # нашёл/нашла


def times(n):
    return f"{n} раз" if n % 10 not in (2, 3, 4) or n % 100 in (12, 13, 14) else f"{n} раза"


def _names(xs):
    xs = list(dict.fromkeys(x for x in xs if x))
    return ", ".join(xs[:MAX_NAMES]) + (" и другие" if len(xs) > MAX_NAMES else "")


def item_label(item, amount):
    if str(item) in ("z", "zeny"):
        return f"{amount} зени"
    try:
        name = _prices().name(str(item))       # perf: справочник один на процесс (был Prices.load() на каждую строку)
    except (OSError, ValueError, AttributeError):
        name = None
    return f"{amount} × {name or item}"


_PRICES = []                                    # perf: Prices.load() каждый раз пересобирает словарь ≈ 3 мс


def _prices():
    if not _PRICES:
        from .prices import Prices
        _PRICES.append(Prices.load())
    return _PRICES[0]


# ---------------- чтение памяти ----------------

def facts_of(db, start, end):
    rows = db.execute(f"SELECT ts, kind, data FROM events WHERE ts >= ? AND ts < ? AND kind IN "
                      f"({', '.join('?' * len(KINDS))}) ORDER BY ts, id", (start, end, *KINDS)).fetchall()
    return [{"ts": ts, "kind": kind, "data": _loads(data)} for ts, kind, data in rows]


def kv(db, key):
    row = db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return _loads(row[0]) if row else {}


def born_ts(db):
    return db.execute("SELECT MIN(ts) FROM events").fetchone()[0]


# ---------------- глава ----------------

def chapter(facts, sex, start, end, number, born=None, places=None, tz=None):
    """Глава недели: {number, title, days, lines, kinds}. facts — события недели (facts_of)."""
    by = {}
    for f in facts:
        by.setdefault(f["kind"], []).append(f["data"])
    day = lambda ts: datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")
    lines, title = [], None
    if born is not None and start <= born < end:
        lines.append(f"Я {g(sex, 'появился', 'появилась')} в мире {day(born)}. С этого дня я веду счёт своей жизни.")
        title = "Рождение"
    lv = [d.get("level") for d in by.get("level_up", []) if isinstance(d.get("level"), int)]
    if len(lv) > 1:
        lines.append(f"{g(sex, 'Дорос', 'Доросла')} с {min(lv) - 1} до {max(lv)} уровня.")
    elif lv:
        lines.append(f"{g(sex, 'Достиг', 'Достигла')} {lv[0]} уровня.")
    for d in by.get("job_changed", []):
        lines.append(f"{g(sex, 'Стал', 'Стала')} {d.get('to')} — раньше {g(sex, 'был', 'была')} {d.get('from')}.")
        title = title or f"Я — {d.get('to')}"
    reports = by.get("death_report", [])
    deaths = len(by.get("died", [])) or len(reports)
    if deaths:
        where = [f"{r.get('map')}" + (f" (бил {r.get('cause')})" if r.get("cause") else "") for r in reports] or \
                [d.get("map") for d in by.get("died", [])]
        lines.append(f"{g(sex, 'Погиб', 'Погибла')} {times(deaths)}: {_names(where)}.")
        if deaths >= 3:
            title = title or "Тяжёлая неделя"
    met = [d.get("partner") for d in by.get("meeting_confirmed", [])]
    if met:
        lines.append(f"{g(sex, 'Встречался', 'Встречалась')} с {_names(met)}" +
                     (f" ({times(len(met))})" if len(met) > 1 else "") + ".")
    for d in by.get("gift_given", []):
        lines.append(f"{g(sex, 'Отдал', 'Отдала')} {d.get('peer')} {item_label(d.get('item'), d.get('amount'))}.")
    for d in by.get("gift_received", []):
        lines.append(f"{g(sex, 'Получил', 'Получила')} от {d.get('peer')} "
                     f"{item_label(d.get('item'), d.get('got') or d.get('amount'))}.")
    heals = by.get("heal_confirmed", [])
    healed_me = [d.get("from") for d in heals if d.get("from") and d.get("to") != d.get("from")]
    if heals:
        lines.append(f"Лечение в группе: {times(len(heals))}" + (f", рядом были {_names(healed_me)}" if healed_me else "")
                     + " — сервер подтвердил.")
    for d in by.get("society_quarrel", []):
        lines.append(f"{g(sex, 'Поссорился', 'Поссорилась')} с {d.get('peer')}"
                     + (f": {d.get('cause')}" if d.get("cause") else "") + ".")
        title = title or f"Ссора с {d.get('peer')}"
    for d in by.get("society_reconciled", []):
        lines.append(f"{g(sex, 'Помирился', 'Помирилась')} с {d.get('peer')}.")
    for d in by.get("dream_new", []):
        lines.append(f"{g(sex, 'Решил', 'Решила')}, что моя мечта — {d.get('dream')}.")
    for d in by.get("dream_stage", []):
        lines.append(f"Мечта «{d.get('dream')}»: пройден этап {d.get('n')} из {d.get('of')} — {d.get('stage')}.")
    for d in by.get("dream_done", []):
        lines.append(f"Мечта сбылась: {d.get('dream')}!")
        title = "Мечта сбылась"
    for d in by.get("dream_changed", []):
        lines.append(f"{g(sex, 'Оставил', 'Оставила')} мечту «{d.get('dream')}»: {d.get('why')}.")
    for d in by.get("savings_progress", []):
        if d.get("pct") == 100:
            lines.append(f"{g(sex, 'Накопил', 'Накопила')} на мечту «{d.get('goal')}»: {d.get('target')} зени.")
    found = [d.get("map") for d in by.get("explore_found", [])]
    found += [m for m, p in sorted((places or {}).items(), key=lambda kv: (kv[1] or {}).get("first", 0))
              if isinstance(p, dict) and start <= (p.get("first") or 0) < end]
    if found:
        lines.append(f"Впервые {g(sex, 'побывал', 'побывала')}: {_names(found)}.")
    for d in by.get("card_found", []):
        lines.append(f"{g(sex, 'Нашёл', 'Нашла')} карту {d.get('name')}" + (" — первую в жизни!" if d.get("first") else "."))
        if d.get("first"):
            title = title or "Первая карта"
    for d in by.get("pet_tamed", []):
        lines.append(f"{g(sex, 'Приручил', 'Приручила')} {d.get('name')} — яйцо питомца.")
    for d in by.get("pet_hatched", []):
        lines.append(f"Теперь у меня питомец: {d.get('name')}.")
        title = title or "Питомец"
    for d in by.get("guild_founded", []):
        lines.append(f"{g(sex, 'Основал', 'Основала')} гильдию {d.get('name')}.")
        title = title or f"Гильдия {d.get('name')}"
    for d in by.get("guild_joined", []):
        lines.append(f"{g(sex, 'Вступил', 'Вступила')} в гильдию {d.get('name')}.")
    aims = [d.get("text") for d in by.get("aim_done", []) if d.get("text")]
    if aims:
        lines.append(f"{g(sex, 'Выполнил', 'Выполнила')} цели недели: " + "; ".join(aims) + ".")
    if not lines:
        lines.append("Тихая неделя: в памяти нет событий.")
    kinds = {}
    for f in facts:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    return {"number": number, "title": title or f"Неделя {number}", "days": (day(start), day(end - 1)),
            "lines": lines, "kinds": kinds}


def render_chapter(ch, colored=None):
    out = [f"## Глава {ch['number']}. {ch['title']} ({ch['days'][0]} – {ch['days'][1]})", ""]
    if colored:
        out += [colored, "", "Строго по фактам:"]
    out.append(" ".join(ch["lines"]))
    if ch["kinds"]:
        out += ["", "_Факты: " + ", ".join(f"{k} ×{n}" for k, n in sorted(ch["kinds"].items())) + "._"]
    return "\n".join(out)


def build(db, name, sex, now=None, week=None, tz_hours=0, until_week_end=False):
    """Книга: {name, chapters: [{..., week}]}; week — одна неделя; until_week_end — только законченные недели."""
    now = now or time.time()
    born = born_ts(db)
    places = kv(db, "places")
    out = {"name": name, "chapters": [], "born": born}
    if born is None:
        return out
    first_start, _, _, tz = week_bounds(None, tz_hours, now=born)
    if week:
        start, end, label, tz = week_bounds(week, tz_hours)
        weeks = [(start, end, label)] if end > first_start else []
    else:
        weeks = []
        start = first_start
        while start <= now:
            s, e, label, tz = week_bounds(None, tz_hours, now=start + 3600)
            if until_week_end and e > now:
                break
            weeks.append((s, e, label))
            start = e
    for s, e, label in weeks:
        number = int(round((s - first_start) / (7 * 86400))) + 1
        ch = chapter(facts_of(db, s, e), sex, s, e, number, born=born, places=places, tz=tz)
        ch["week"] = label
        out["chapters"].append(ch)
    return out


def render(book, colored=None):
    colored = colored or {}
    out = [f"# Мемуары {book['name']}", "",
           "Записано по памяти жителя: только то, что подтвердили игра и правила мозга."]
    if not book["chapters"]:
        out += ["", "В памяти ещё нет событий."]
    for ch in book["chapters"]:
        out += ["", render_chapter(ch, colored.get(ch.get("week")))]
    return "\n".join(out) + "\n"


# ---------------- LLM-окраска (по умолчанию выкл.) ----------------

def colorize(ch, name, settings, budget=None, call=None):
    """Один вызов модели на главу: (текст, None) или (None, почему нет). Фильтр — routine.diary_only."""
    from . import llm
    from .routine import diary_only
    call = call or llm.chat
    if not settings.llm_enabled:
        return None, settings.llm_off_reason
    call_id = None
    if budget is not None:
        call_id, why = budget.reserve("openrouter", settings.global_daily_limit, settings.global_daily_usd_limit)
        if why:
            return None, why
    facts = render_chapter(ch)
    messages = [
        {"role": "system", "content": f"Ты — {name}, житель мира Ragnarok Online. Перескажи главу своих мемуаров "
                                      "от первого лица, 3–5 предложений, по-русски. Используй ТОЛЬКО факты главы: "
                                      "не добавляй событий, чисел и имён. Ответ — JSON {\"text\": \"пересказ\"}."},
        {"role": "user", "content": facts[:4000]}]
    try:
        text, usage, _ = call(settings, messages, json_mode=True, max_tokens=LLM_MAX_TOKENS)
    except llm.LLMError as e:
        if budget is not None:
            budget.settle(call_id, 0)
        return None, f"ошибка LLM: {e}"
    if budget is not None:
        budget.settle(call_id, (usage or {}).get("cost"))
    story = " ".join(str(llm.parse_json_object(text).get("text") or "").split())[:LLM_MAX_TEXT]
    if not story:
        return None, "модель не вернула текст"
    decision, rejected = diary_only({"remember": [{"text": story}]}, facts)    # механизм дневника ORG-049
    if rejected or not decision["remember"]:
        why = rejected[0]["why"] if rejected else "пусто"
        return None, f"пересказ отклонён — {why}"
    return decision["remember"][0]["text"], None


def colored_cached(lab_root, bot, book, settings_loader, budget_loader):
    """Окраска глав с кэшем run/memoir-<bot>-<неделя>.json. -> ({неделя: текст}, [заметки])."""
    out, notes = {}, []
    settings = budget = None
    for ch in book["chapters"]:
        path = Path(lab_root) / "run" / f"memoir-{bot}-{ch['week']}.json"
        key = " ".join(ch["lines"])
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("text") and cached.get("facts") == key:
                out[ch["week"]] = cached["text"]
                continue
        except (OSError, ValueError):
            pass
        if settings is None:
            settings = settings_loader()
            if settings is None:
                notes.append("нет env-файла с настройками LLM")
                break
            budget = budget_loader()
        text, why = colorize(ch, book["name"], settings, budget)
        if text:
            out[ch["week"]] = text
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"week": ch["week"], "facts": key, "text": text, "ts": time.time()},
                                       ensure_ascii=False), encoding="utf-8")
        else:
            notes.append(f"{ch['week']}: {why}")
            if not settings.llm_enabled:
                break
    if budget is not None:
        budget.close()
    return out, notes


# ---------------- модуль мозга ----------------

class Memoir:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "memoir", "memoir", "memoir", True, "world"
    TICK_ORDER = 230
    TICK_EVERY = 600            # perf: реестр не зовёт tick до next_tick (modules.py)

    def __init__(self, mind, world=None, clock=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("memoir") or {}))
        self.tz_hours = (world or {}).get("timezone_offset_hours", 0)
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.next_tick = 0.0

    def path(self):
        p = getattr(self.mind.mem, "path", None)
        if not p or str(p) == ":memory:":
            return None
        return Path(p).parent / "memoir.md"

    def tick(self):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        path = self.path()
        if path is None:
            return
        _, _, current, _ = week_bounds(None, self.tz_hours, now=now)
        if self.mind.mem.get("memoir_week") == current:
            return
        state = self.mind.state or {}
        book = build(self.mind.mem.db, state.get("name") or self.mind.persona.get("name") or "жителя",
                     state.get("sex"), now=now, tz_hours=self.tz_hours, until_week_end=True)
        self.mind.mem.set("memoir_week", current)
        if not book["chapters"]:
            return
        try:
            path.write_text(render(book), encoding="utf-8")
        except OSError as e:
            log.warning("мемуары не записаны: %s", e)
            return
        last = book["chapters"][-1]
        self.mind.mem.add_event("memoir_chapter", {"week": last["week"], "title": last["title"],
                                                   "chapters": len(book["chapters"])})
        self.mind.write_decision({"type": "memoir", "week": last["week"], "title": last["title"],
                                  "chapters": len(book["chapters"])})
        log.info("мемуары: глава %s «%s» -> %s", last["number"], last["title"], path)


# ---------------- CLI ----------------

def world_cfg(path):
    try:
        world = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        world = {}
    return world.get("timezone_offset_hours", 0), dict(DEFAULTS, **(world.get("memoir") or {}))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("week", nargs="?", help="2026-W40 или дата недели 2026-10-02; по умолчанию вся жизнь")
    p.add_argument("--lab-root", required=True)
    p.add_argument("--bot", required=True)
    p.add_argument("--world", default=str(WORLD / "goals.json"))
    p.add_argument("--env", help="env-файл лаборатории (BRAIN_LLM, ключ) — нужен только для --llm")
    p.add_argument("--llm", action="store_true", help="окрасить главы (один вызов на главу, кэш), если LLM включён")
    p.add_argument("--write", action="store_true", help="записать книгу в state/<bot>/memoir.md")
    a = p.parse_args(argv)
    tz_hours, cfg = world_cfg(a.world)
    if not cfg.get("enabled", True):
        print("мемуары выключены (goals.json memoir.enabled = false)")
        return 0
    path = Path(a.lab_root) / "state" / a.bot / "memory.sqlite"
    if not path.exists():
        print(f"нет памяти {path}", file=sys.stderr)
        return 2
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        last = kv(db, "last_state")
        try:
            book = build(db, last.get("name") or a.bot, last.get("sex"), week=a.week, tz_hours=tz_hours)
        except ValueError:
            print(f"неделя «{a.week}»: нужно 2026-W40 или 2026-10-02", file=sys.stderr)
            return 2
    finally:
        db.close()
    colored, notes = {}, []
    if a.llm or cfg.get("llm"):
        def settings_loader():
            from .config import Settings, load_env
            return Settings.from_env(load_env(a.env)) if a.env and Path(a.env).exists() else None

        def budget_loader():
            from .budget import SharedBudget
            return SharedBudget(Path(a.lab_root) / "state" / "shared" / "budget.sqlite", "memoir")
        colored, notes = colored_cached(a.lab_root, a.bot, book, settings_loader, budget_loader)
    text = render(book, colored)
    print(text, end="")
    if a.write:
        (Path(a.lab_root) / "state" / a.bot / "memoir.md").write_text(text, encoding="utf-8")
    for n in notes:
        print(f"({n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
