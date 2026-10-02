"""Реплей (ORG-050): запись потока тела и прогон его через настоящий мозг на ускоренных часах.

Запись: BRAIN_RECORD=1 в env — каждое сообщение тела (hello/state/event/ack/delivery) пишется строкой
JSON в $LAB_ROOT/state/<bot>/replay.jsonl (не больше MAX_BYTES, затем ротация в .1). Реплики
посторонних игроков заменяются на «<скрыто>» (их текст не нужен для проверки поведения).
Прогон (тесты): run(mind, messages, clock) — по секундам от первого до последнего сообщения:
доставить сообщения с ts <= t, затем mind.step(). Часы — объект с полем t, который тест
подставляет вместо time.time (unittest.mock.patch).
Проверки поведения — invariants(): мёртвому не двигаться, после смерти не охотиться с HP < 80%,
без LLM — ноль вызовов моделей, не больше SPAM_PER_MIN действий в минуту.
"""
import json
import os

MAX_BYTES = 10 * 1024 * 1024
SPAM_PER_MIN = 12
MOVES = ("hunt", "meet_point", "follow", "unstuck", "service", "give", "job_change",
         "offer_sell")   # review: продавец идёт к покупателю (economy.pl startGive)


class Recorder:
    def __init__(self, path, peers=()):
        self.path = str(path)
        self.peers = set(peers)

    def __call__(self, msg):
        rec = dict(msg)
        if rec.get("kind") in ("chat_private", "chat_public") and rec.get("from") not in self.peers:
            rec["text"] = "<скрыто>"
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > MAX_BYTES:
                os.replace(self.path, self.path + ".1")
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass                                   # запись реплея не должна ломать мозг


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


async def run(mind, messages, clock, step=1.0):
    """Прогнать сообщения через мозг. Возвращает журнал отправленных действий [(t, действие, последний state)]."""
    msgs = sorted(messages, key=lambda m: m["ts"])
    if not msgs:
        return []
    clock.t = msgs[0]["ts"]
    end = msgs[-1]["ts"] + 2
    i = 0
    while clock.t <= end:
        while i < len(msgs) and msgs[i]["ts"] <= clock.t:
            await mind.on_message(msgs[i])
            i += 1
        if mind.state:
            await mind.step()
        clock.t += step


def invariants(sent, llm_calls=0, llm_enabled=False):
    """sent — [(t, действие, state на момент отправки)]. Возвращает список нарушений (пусто — всё верно)."""
    bad = []
    died_at, recovered = None, True
    per_min = {}
    for t, a, s in sent:
        kind = a.get("action")
        if s.get("dead") and kind in MOVES:
            bad.append(f"{t:.0f}: мёртвому отправлено {kind}")
        if s.get("dead"):
            died_at, recovered = t, False
        elif died_at is not None and not recovered:
            if (s.get("hp_pct") or 0) >= 80:
                recovered = True
            elif kind == "hunt":
                bad.append(f"{t:.0f}: на охоту после смерти с HP {s.get('hp_pct')}%")
        minute = int(t // 60)
        per_min[minute] = per_min.get(minute, 0) + 1
    for minute, n in per_min.items():
        if n > SPAM_PER_MIN:
            bad.append(f"минута {minute}: {n} действий (больше {SPAM_PER_MIN})")
    if not llm_enabled and llm_calls:
        bad.append(f"без LLM было {llm_calls} вызовов моделей")
    return bad
