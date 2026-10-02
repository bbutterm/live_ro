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
from .gate import RuleGate, make_fast_gate
from .routine import load_world
from .memory import Memory
from .mind import Mind

log = logging.getLogger("live_brain")


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
    party = memory.get("party") or {}
    if party:
        print(f"   группа: {'подтверждена сервером' if party.get('confirmed') else 'нет'}; "
              f"лечений подтверждено за сутки {count('heal_confirmed')}")
    for m in memory.db.execute("SELECT text FROM memories ORDER BY id DESC LIMIT 3"):
        print(f"   помнит: {m[0]}")
    return 0


def peer_names(persona_path):
    names = set()
    for f in Path(persona_path).parent.glob("*.json"):
        try:
            names.add(load_persona(f)["name"])
        except (ValueError, OSError):
            continue
    return names


async def main_async(args, settings, persona, memory, state_dir):
    socket_path = os.path.join(args.lab_root, "run", "brain", f"{args.bot}.sock")
    mind = None

    async def on_message(msg):
        await mind.on_message(msg)

    bridge = Bridge(socket_path, on_message)
    fast = make_fast_gate(settings)
    peers = peer_names(args.persona)
    shared = SharedBudget(Path(args.lab_root) / "state" / "shared" / "budget.sqlite", args.bot)
    mind = Mind(settings, persona, memory, bridge.send_action, state_dir / "decisions.jsonl",
                RuleGate(), fast=fast, peers=peers,
                inbox_path=os.path.join(args.lab_root, "run", "brain", f"{args.bot}.inbox"),
                world=load_world(args.world) if os.path.exists(args.world) else None,
                shared_budget=shared, alerts_path=os.path.join(args.lab_root, "run", "alerts.log"))
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
    await stop.wait()
    log.info("остановка мозга")
    runner.cancel()
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
