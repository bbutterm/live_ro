"""Настроение (ORG-064): медленное состояние жителя −1..1 из фактов последних суток. Правила без LLM.

value = clamp(−1, 1, Σ WEIGHTS[вид] · 0.5^(возраст / HALF_LIFE_H) · (1 − 0.4·patience)) по событиям памяти
за WINDOW_H часов: терпеливый переживает и радуется мягче. heal_confirmed — только если лечили меня.
Настроение влияет ТОЛЬКО на социальное поведение: варианты фраз (social.phrase: <ключ>_good/_bad, если есть),
паузу между разговорами (talk_factor), «молчуна» (при value < SILENT житель не заговаривает первым, но отвечает)
и шанс радостной эмоции (society.py). Выживание, распорядок, экономика и группа его не читают (это проверяет тест).
Для отчёта и промпта — kv mood {value, label, reasons} раз в минуту (mind.py).
Выключатель: BRAIN_DISABLE=mood или "mood": {"enabled": false} в goals.json.
"""
import json
import time

WEIGHTS = {"died": -0.3, "death_report": -0.1, "level_up": 0.3, "gift_received": 0.2, "heal_confirmed": 0.1,
           "society_quarrel": -0.3, "society_reconciled": 0.3, "trade_sold": 0.05, "aim_done": 0.3,
           "pet_hatched": 0.4, "meeting_confirmed": 0.1}
REASON = {"died": "погиб", "death_report": "разбор гибели", "level_up": "новый уровень",
          "gift_received": "получил подарок", "heal_confirmed": "меня вылечили", "society_quarrel": "поссорился",
          "society_reconciled": "помирился", "trade_sold": "удачно продал", "aim_done": "выполнил цель недели",
          "pet_hatched": "завёл питомца", "meeting_confirmed": "встреча с жителем"}
HALF_LIFE_H = 12
WINDOW_H = 48
GOOD, BAD = 0.4, -0.4          # пороги вариантов фраз
SILENT = -0.5                  # хуже — не начинает разговор первым
CACHE_SEC = 30


def clamp(v, lo=-1.0, hi=1.0):
    return max(lo, min(hi, v))


def times(n):
    n = int(n)
    return "раза" if n % 10 in (2, 3, 4) and not 12 <= n % 100 <= 14 else "раз"


class Mood:
    def __init__(self, mind, clock=None):
        self.mind = mind
        self.clock = clock or (lambda: time.time())
        self.cache = (None, None)       # (время, вклады)

    def patience(self):
        p = (self.mind.persona.get("traits") or {}).get("patience", 0.5)
        return clamp(float(p), 0.0, 1.0)

    def me(self):
        return (self.mind.state or {}).get("name") or self.mind.persona.get("name")

    def contributions(self, now=None):
        """[(вид, вклад)] по событиям окна; heal_confirmed — только мне."""
        now = now or self.clock()
        t, cached = self.cache
        if t is not None and abs(now - t) < CACHE_SEC:
            return cached
        kinds = tuple(WEIGHTS)
        rows = self.mind.mem.db.execute(
            f"SELECT ts, kind, data FROM events WHERE ts >= ? AND kind IN ({', '.join('?' * len(kinds))})",
            (now - WINDOW_H * 3600, *kinds)).fetchall()
        soft = 1 - 0.4 * self.patience()
        out = []
        for ts, kind, data in rows:
            if ts > now + 60:                     # событие «из будущего» (реплей, подменные часы) — не считать
                continue
            if kind == "heal_confirmed":
                try:
                    if json.loads(data).get("to") != self.me():
                        continue
                except (TypeError, ValueError):
                    continue
            out.append((kind, WEIGHTS[kind] * 0.5 ** (max(0.0, now - ts) / 3600 / HALF_LIFE_H) * soft))
        self.cache = (now, out)
        return out

    def value(self, now=None):
        return round(clamp(sum(w for _, w in self.contributions(now))), 3)

    def label(self, now=None):
        v = self.value(now)
        if v < -0.5:
            return "мрачное"
        if v < -0.15:
            return "хмурое"
        if v <= 0.15:
            return "ровное"
        return "доброе" if v <= 0.5 else "отличное"

    def reasons(self, now=None, n=2):
        """Главные вклады: «погиб 2 раза», «получил подарок»."""
        total, count = {}, {}
        for kind, w in self.contributions(now):
            total[kind] = total.get(kind, 0) + w
            count[kind] = count.get(kind, 0) + 1
        top = sorted(total, key=lambda k: -abs(total[k]))[:n]
        return [REASON[k] + (f" {count[k]} {times(count[k])}" if count[k] > 1 else "") for k in top]

    def phrase_key(self, key):
        """key_good при value ≥ GOOD, key_bad при ≤ BAD — если такие фразы есть у персоны; иначе key."""
        v = self.value()
        phrases = self.mind.persona.get("phrases") or {}
        if v >= GOOD and phrases.get(f"{key}_good"):
            return f"{key}_good"
        if v <= BAD and phrases.get(f"{key}_bad"):
            return f"{key}_bad"
        return key

    def talk_factor(self):
        """Множитель паузы между разговорами: в плохом настроении реже, в хорошем чаще."""
        v = self.value()
        return 1.3 if v <= BAD else (0.8 if v >= GOOD else 1.0)

    def silent(self):
        return self.value() < SILENT

    def emote_chance(self):
        """Шанс радостной эмоции: (1 + value)/2 + 0.5, не больше 1."""
        return min(1.0, (1 + self.value()) / 2 + 0.5)

    def snapshot(self, now=None):
        return {"value": self.value(now), "label": self.label(now), "reasons": self.reasons(now)}

    def summary(self):
        s = self.snapshot()
        return f"{s['label']} ({s['value']:+.2f})" + (": " + ", ".join(s["reasons"]) if s["reasons"] else "")
