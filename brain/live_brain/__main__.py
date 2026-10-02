"""Запуск: python3 -m live_brain --env FILE --bot bot01 --lab-root DIR [--check]

Обычно вызывается через `scripts/lab start brain` / `scripts/lab brain-check`.
"""
import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

from . import llm
from .bridge import Bridge
from .config import Settings, load_env, load_persona
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
    p.add_argument("--check", action="store_true", help="один тестовый запрос к модели и выход")
    a = p.parse_args(argv)
    a.persona = a.persona or str(here / "personas" / f"{a.bot}.json")
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


async def main_async(args, settings, persona, memory, state_dir):
    socket_path = os.path.join(args.lab_root, "run", "brain", f"{args.bot}.sock")
    mind = None

    async def on_message(msg):
        await mind.on_message(msg)

    bridge = Bridge(socket_path, on_message)
    mind = Mind(settings, persona, memory, bridge.send_action, state_dir / "decisions.jsonl")
    await bridge.start()
    memory.add_event("brain_started", {"model": settings.model, "llm": settings.llm_enabled})
    log.info("мозг %s запущен: модель %s, LLM %s, лимит %d/сутки, план раз в %d с; "
             "память: %s воспоминаний", persona["name"], settings.model,
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
        asyncio.run(main_async(args, settings, persona, memory, state_dir))
        return 0
    finally:
        memory.close()


if __name__ == "__main__":
    sys.exit(main())
