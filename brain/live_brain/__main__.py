"""Запуск: python3 -m live_brain --env FILE --bot bot01 --lab-root DIR [--check]

Обычно вызывается через `scripts/lab start brain` / `scripts/lab brain-check`.
"""
import argparse
import json
import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

from . import llm
from .bridge import Bridge
from .budget import SharedBudget
from .config import Settings, load_env, load_persona
from .economy import economy_metrics
from .gate import RuleGate, make_fast_gate
from .routine import load_world
from .memory import Memory
from .mind import Mind
from . import resources  # ops: ORG-047 замер ресурсов
from .collection import album_size  # collect: ORG-074 метрика альбома
from .bestiary import species as bestiary_species   # bestiary: ORG-077 метрика бестиария

log = logging.getLogger("live_brain")
RESOURCE_EVERY = 600      # ops: ORG-047 — замер RSS/CPU мозга раз в 10 мин


def parse_args(argv):
    here = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(prog="live_brain")
    p.add_argument("--env", required=True, help="env-файл с OPENROUTER_API_KEY и BRAIN_*")
    p.add_argument("--bot", default="bot01")
    p.add_argument("--lab-root", required=True)
    p.add_argument("--persona", help="JSON характера (по умолчанию brain/personas/<bot>.json)")
    p.add_argument("--check", action="store_true", help="один тестовый запрос к основной модели и выход")
    p.add_argument("--check-jev", action="store_true", help="один тестовый запрос к JEV и выход")
    p.add_argument("--plans", action="store_true", help="показать последние планы из памяти и выйти")
    p.add_argument("--world", help="глобальные цели и распорядок (по умолчанию brain/world/goals.json)")
    p.add_argument("--routine", action="store_true", help="показать распорядок из памяти и выйти")
    p.add_argument("--report", action="store_true", help="сводка жизни бота из памяти (только чтение) и выход")
    a = p.parse_args(argv)
    a.persona = a.persona or str(here / "personas" / f"{a.bot}.json")
    a.world = a.world or str(here / "world" / "goals.json")
    return a


def check(settings, persona, memory):
    if not settings.llm_enabled:
        print(f"CHECK SKIP: {settings.llm_off_reason}. Платный запрос не выполнен. "
              "Включается BRAIN_LLM=openrouter после согласования модели и бюджета.")
        return 2
    print(f"модель: {settings.model}; лимит {settings.daily_limit}/сутки, "
          f"использовано за 24 ч: {memory.llm_calls_since(time.time() - 86400)}")
    messages = [
        {"role": "system", "content": f"Ты — {persona['name']}. {persona['character']} "
                                      'Ответь JSON: {"greeting": "одна короткая фраза"}'},
        {"role": "user", "content": "Проверка связи. Поздоровайся."},
    ]
    try:
        text, usage, latency = llm.chat(settings, messages, max_tokens=80)
        obj = llm.parse_json_object(text)
    except llm.LLMError as e:
        memory.log_llm_call(False, error=str(e))
        print(f"CHECK FAIL: {e}")
        return 1
    memory.log_llm_call(True, latency=latency, usage=usage)
    print(f"CHECK OK за {latency:.1f} с, токены {usage}: {obj.get('greeting', obj)}")
    return 0


def check_jev(settings, persona, memory):
    gate = make_fast_gate(settings)
    if gate is None:
        print("CHECK SKIP: JEV выключен (нужны BRAIN_GATE=jev и JEV_API_BASE/JEV_API_KEY/JEV_MODEL)")
        return 2
    from .gate import GateContext
    ctx = GateContext(name=persona["name"], hunt_maps=persona["hunt_maps"])
    messages = gate.messages({"kind": "chat_private", "from": "Tester", "text": "Привет!"},
                             {"hp_pct": 100}, ctx, persona, None)
    try:
        d, usage, latency = gate.call(messages)
    except llm.LLMError as e:
        memory.log_llm_call(False, error=str(e), provider="jev")
        print(f"CHECK FAIL (JEV): {e}")
        return 1
    memory.log_llm_call(True, latency=latency, usage=usage, provider="jev")
    print(f"CHECK OK (JEV {settings.jev.model}) за {latency:.1f} с: {d}")
    return 0


def report(args, memory, state_dir):
    """Сводка для оператора: кто, где, чем занят, что было за сутки, сколько стоило. Только чтение."""
    now = time.time()
    day = now - 86400
    st = memory.get("last_state") or {}
    rt = memory.get("routine") or {}
    count = lambda kind: memory.db.execute("SELECT COUNT(*) FROM events WHERE kind = ? AND ts >= ?",
                                           (kind, day)).fetchone()[0]
    calls = memory.db.execute("SELECT provider, COUNT(*), COALESCE(SUM(cost), 0) FROM llm_calls "
                              "WHERE ts >= ? GROUP BY provider", (day,)).fetchall()
    try:
        plan = memory.db.execute("SELECT partner, status, phase, result FROM plans ORDER BY created DESC "
                                 "LIMIT 1").fetchone()
    except Exception:
        plan = None
    decisions = state_dir / "decisions.jsonl"
    last_decision = time.strftime("%H:%M:%S", time.localtime(decisions.stat().st_mtime)) if decisions.exists() else "—"
    mode = {"hunt": "охота", "town": "отдых в городе"}.get(rt.get("mode"), "—")
    print(f"== {args.bot}: {st.get('name', '?')} — {st.get('job', '?')} {st.get('lv', '?')}/{st.get('job_lv', '?')}, "
          f"{st.get('map', '?')} ({st.get('x', '?')},{st.get('y', '?')}), HP {st.get('hp_pct', '?')}% SP {st.get('sp_pct', '?')}%, "
          f"зени {st.get('zeny', '?')}, вес {st.get('weight_pct', '?')}%")
    if rt:
        print(f"   распорядок: {mode}; охота сегодня {int(rt.get('hunted', 0) / 60)} из {int(rt.get('budget', 0) / 60)} мин")
    print(f"   за сутки: побед {count('kill')}, смертей {count('died')}, уровней {count('level_up')}, "
          f"встреч {count('meeting_confirmed')}, застреваний {count('routine_stuck')}")
    items = st.get("items") or {}
    print(f"   хозяйство: красных зелий {items.get('501', '?')}, отдал жителям {count('gift_given')}, "
          f"получил {count('gift_received')}, просьб {count('gift_asked')}, неудач {count('gift_failed')}"
          + (f"; лавка {'открыта' if (st.get('vend') or {}).get('open') else 'закрыта'}"
             if (st.get("vend") or {}).get("can") else ""))
    if plan:
        print(f"   последний план: встреча с {plan[0]} — {plan[1]} {plan[2] or ''} {plan[3] or ''}".rstrip())
    lat = dict(memory.db.execute("SELECT provider, AVG(latency) FROM llm_calls WHERE ts >= ? AND ok = 1 "
                                 "GROUP BY provider", (day,)).fetchall())
    print("   вызовы моделей за сутки: " + (", ".join(f"{p} {n} (${c:.4f}, в среднем {lat.get(p) or 0:.1f} с)"
                                                    for p, n, c in calls) or "нет")
          + f"; последнее решение {last_decision}")
    status = memory.get("status") or {}
    if status:
        print(f"   состояние: {status.get('state')} — {status.get('why')}")
    organic = organic_metrics(memory, day)
    print("   органичность за сутки: " + ", ".join(f"{k} {v}" for k, v in organic.items()))
    econ = {k: v for k, v in economy_metrics(memory, day).items() if v}
    if econ:
        print("   экономика за сутки: " + ", ".join(f"{k} {v}" for k, v in econ.items()))
    needs = memory.get("needs") or {}
    if needs:
        top = sorted(needs.items(), key=lambda kv: -kv[1])[:3]
        print("   мотивы сейчас: " + ", ".join(f"{k} {v:.2f}" for k, v in top))
    mood = memory.get("mood") or {}                                            # talk: ORG-064
    if mood:                                                                   # talk:
        print(f"   настроение: {mood.get('label')} ({mood.get('value', 0):+.2f})"  # talk:
              + (": " + ", ".join(mood["reasons"]) if mood.get("reasons") else ""))   # talk:
    party = memory.get("party") or {}
    if party:
        print(f"   группа: {'подтверждена сервером' if party.get('confirmed') else 'нет'}; "
              f"лечений подтверждено за сутки {count('heal_confirmed')}")
    res_line = resources.brain_line(memory.get("resources"))   # ops: ORG-047 RSS/CPU мозга из kv
    if res_line:                                              # ops:
        print(res_line)                                       # ops:
    for m in memory.db.execute("SELECT text FROM memories ORDER BY id DESC LIMIT 3"):
        print(f"   помнит: {m[0]}")
    return 0


def env_flag(env_path, key):
    try:
        return load_env(env_path).get(key, "").strip() in ("1", "yes", "true")
    except OSError:
        return False


def env_bots(env_path):
    try:
        return load_env(env_path).get("LAB_BOTS", "").strip().strip("\"'")
    except OSError:
        return ""


def organic_metrics(memory, since, now=None):
    """ORG-046: разнообразие жизни по фактам памяти (не по словам модели)."""
    rows = memory.db.execute("SELECT kind, data FROM events WHERE ts >= ? AND kind IN "
                             "('activity', 'social_walk', 'social_said')", (since,)).fetchall()
    acts, points, said, fact_said, sleep_h = set(), set(), 0, 0, 0.0
    texts = []                                                          # grammar: ORG-065 повторы реплик
    starts = 0                                                          # crowd: ORG-089 начатых занятий
    for kind, data in rows:
        d = json.loads(data)
        if kind == "activity":
            acts.add(d.get("name"))
            starts += 1 if d.get("name") else 0                         # crowd:
        elif kind == "social_walk":
            points.add(d.get("point"))
        elif kind == "social_said":
            said += 1
            fact_said += 1 if d.get("fact") else 0
            if d.get("text"):                                           # grammar:
                texts.append(d["text"])                                 # grammar:
    diaries = memory.db.execute("SELECT COUNT(*) FROM events WHERE kind = 'diary' AND ts >= ?", (since,)).fetchone()[0]
    calls = memory.db.execute("SELECT COUNT(*) FROM llm_calls WHERE ts >= ?", (since,)).fetchone()[0]
    # ops: сон — фактическое время между routine_sleep и routine_wake за период (раньше — плановая длина
    # ночи из kv routine, она есть и у не уснувшего жителя)
    st = memory.get("routine") or {}
    sleep_s = sleep_seconds(memory, since, now or time.time(), st.get("mode") == "sleep")
    sleep_h = round(sleep_s / 3600, 1)
    trophies = memory.db.execute("SELECT COUNT(*) FROM events WHERE kind IN ('card_found', 'trophy_first', "   # collect:
                                 "'trophy_rare') AND ts >= ?", (since,)).fetchone()[0]                      # collect:
    found = memory.db.execute("SELECT COUNT(*) FROM events WHERE kind = 'explore_found' AND ts >= ?",   # explore:
                              (since,)).fetchone()[0]                                                  # explore:
    firsts = memory.db.execute("SELECT COUNT(*) FROM events WHERE kind = 'bestiary_first' AND ts >= ?",  # bestiary:
                               (since,)).fetchone()[0]                                                 # bestiary:
    return {"занятий": len(acts - {None}), "мест в городе": len(points - {None}),
            "реплик без LLM": said, "из них о событиях": fact_said, "сон, ч": sleep_h,
            "вызовов моделей": calls, "дневников": diaries,
            "открытых мест": found,                                                                     # explore: ORG-054
            "карт в альбоме": album_size(memory), "трофеев за период": trophies,                         # collect: ORG-074
            "видов в бестиарии": bestiary_species(memory), "открытий первым": firsts,                    # bestiary: ORG-077
            "разнообразие занятий": round(len(acts - {None}) / starts, 2) if starts else 0.0,           # crowd: ORG-089
            "повторов реплик, %": repeat_share(texts)}                                                 # grammar: ORG-065


def repeat_share(texts):
    """grammar: ORG-065 — доля реплик (в %), чей текст уже звучал раньше в этот период; цель < 20 %."""
    return round(100 * (len(texts) - len(set(texts))) / len(texts)) if texts else 0


def sleep_seconds(memory, since, now, sleeping_now):
    """ops: ORG-046 — секунды сна в [since, now) по событиям routine_sleep/routine_wake."""
    rows = memory.db.execute("SELECT ts, kind FROM events WHERE kind IN ('routine_sleep', 'routine_wake') "
                             "AND ts >= ? AND ts < ? ORDER BY ts", (since - 86400, now)).fetchall()
    total, start = 0.0, None
    for ts, kind in rows:
        if kind == "routine_sleep":
            start = ts if start is None else start
        elif start is not None:
            total += max(0.0, ts - max(start, since))
            start = None
    if start is not None and sleeping_now:
        total += max(0.0, now - max(start, since))
    return total


def roster_residents(persona_path):
    """ORG-040: активные жители из brain/world/roster.json ({bot: запись}) или None, если реестра нет."""
    path = Path(persona_path).resolve().parent.parent / "world" / "roster.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    res = doc.get("residents")
    if not isinstance(res, dict):
        return None
    return {b: r for b, r in res.items() if isinstance(r, dict) and r.get("active")}


def peer_names(persona_path, bots=None):
    """Имена жителей. bots (LAB_BOTS) — только запущенные: иначе лидер группы звал бы офлайн-жителя,
    а слухи уходили бы в пустоту (ORG-004). Есть реестр brain/world/roster.json — активные жители реестра
    ∩ LAB_BOTS, имя из персоны (поле persona), иначе из реестра. Реестра нет: без LAB_BOTS — все характеры."""
    names = set()
    folder = Path(persona_path).parent
    roster = roster_residents(persona_path)
    if roster is not None:
        for b, r in roster.items():
            if bots and b not in bots:
                continue
            try:
                names.add(load_persona(folder / f"{r.get('persona') or b}.json")["name"])
            except (ValueError, OSError, KeyError):
                if r.get("name"):
                    names.add(r["name"])
        return names
    files = [folder / f"{b}.json" for b in bots] if bots else folder.glob("*.json")
    for f in files:
        try:
            names.add(load_persona(f)["name"])
        except (ValueError, OSError):
            continue
    return names


async def sample_resources(memory, every=RESOURCE_EVERY):
    """ops: ORG-047 — свой RSS/CPU в kv "resources" раз в every секунд (report читает без /proc)."""
    while True:
        try:
            resources.brain_sample(memory)
        except Exception as e:                                 # замер не должен ронять мозг
            log.warning("замер ресурсов не удался: %s", e)
        await asyncio.sleep(every)


async def main_async(args, settings, persona, memory, state_dir):
    socket_path = os.path.join(args.lab_root, "run", "brain", f"{args.bot}.sock")
    mind = None

    async def on_message(msg):
        await mind.on_message(msg)

    bridge = Bridge(socket_path, on_message)
    fast = make_fast_gate(settings)
    lab_bots = (env_bots(args.env) or os.environ.get("LAB_BOTS", "")).split()
    peers = peer_names(args.persona, lab_bots or None)
    shared = SharedBudget(Path(args.lab_root) / "state" / "shared" / "budget.sqlite", args.bot)
    mind = Mind(settings, persona, memory, bridge.send_action, state_dir / "decisions.jsonl",
                RuleGate(), fast=fast, peers=peers,
                inbox_path=os.path.join(args.lab_root, "run", "brain", f"{args.bot}.inbox"),
                world=load_world(args.world) if os.path.exists(args.world) else None,
                shared_budget=shared, alerts_path=os.path.join(args.lab_root, "run", "alerts.log"))
    if env_flag(args.env, "BRAIN_RECORD"):           # ORG-050: поток тела для реплея
        from .replay import Recorder
        mind.recorder = Recorder(state_dir / "replay.jsonl", peers)
    await bridge.start()
    memory.add_event("brain_started", {"model": settings.model, "llm": settings.llm_enabled})
    log.info("мозг %s запущен: gate %s, жители %s, модель %s, LLM %s, лимит %d/сутки, план раз в %d с; "
             "память: %s воспоминаний", persona["name"],
             f"rules+jev({settings.jev.model})" if fast else "rules",
             sorted(peers - {persona["name"]}) or "нет", settings.model,
             "включена" if settings.llm_enabled else f"ВЫКЛЮЧЕНА ({settings.llm_off_reason})",
             settings.daily_limit, settings.decide_interval, len(memory.top_memories(1000)))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    runner = asyncio.create_task(mind.run(lambda: bridge.connected))
    sampler = asyncio.create_task(sample_resources(memory))   # ops: ORG-047
    await stop.wait()
    log.info("остановка мозга")
    runner.cancel()
    sampler.cancel()                                          # ops:
    memory.add_event("brain_stopped", {})
    await bridge.close()


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")
    env = load_env(args.env)
    settings = Settings.from_env(env)
    persona = load_persona(args.persona)
    state_dir = Path(args.lab_root) / "state" / args.bot
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    memory = Memory(state_dir / "memory.sqlite")
    try:
        if args.check:
            return check(settings, persona, memory)
        if args.check_jev:
            return check_jev(settings, persona, memory)
        if args.report:
            return report(args, memory, state_dir)
        if args.routine:
            print(json.dumps(memory.get("routine"), ensure_ascii=False, default=str))
            return 0
        if args.plans:
            from .plans import PlanStore
            for plan in PlanStore(memory.db).recent(10):
                plan["created"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(plan["created"]))
                plan["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(plan["updated"]))
                print(json.dumps(plan, ensure_ascii=False))
            return 0
        asyncio.run(main_async(args, settings, persona, memory, state_dir))
        return 0
    finally:
        memory.close()


if __name__ == "__main__":
    sys.exit(main())
