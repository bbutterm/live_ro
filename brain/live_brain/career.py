"""Карьера жителя: цели прогрессии и смена профессии (AUT-079/080/083/084). Правила без LLM.

Данные и чистые функции — progression.py (сценарии Knight/Priest и Novice -> первая профессия выведены
из скриптов rAthena со ссылками на строки, docs/PROGRESSION.md). Цель Novice (какую профессию брать) —
поле job персоны или запись жителя в brain/world/roster.json (по имени персоны). Этот модуль:
    - раз в SUMMARY_EVERY с пишет цель прогрессии в kv "career" (промпт модели, report, хроника);
    - при goals.json progression.auto_job_change = true и готовности этапа (stage_action) в городе,
      без плана встречи и с разрешения арбитра — отправляет действие job_change плагину jobChange;
    - итог этапа (событие job_change_result) — память; провал — повтор не раньше RETRY_GAP,
      после MAX_FAILS провалов подряд — пауза на сутки и оповещение владельцу.
По умолчанию auto_job_change выключен: сценарии не проходились в игре (только тесты разбора скриптов).
"""
import logging
import time

from .lifecycle import quest_busy                 # review2: один плагин jobChange на дом и карьеру

log = logging.getLogger("career")

SUMMARY_EVERY = 600
RETRY_GAP = 3600
MAX_FAILS = 3


class Career:
    def __init__(self, mind, cfg=None, clock=None):
        self.mind = mind
        self.cfg = cfg or {}
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.data = None
        self.target = None          # newborn: первая профессия жителя (Novice), из персоны или roster.json
        self.last_summary = 0.0
        self.st = mind.mem.get("career_state") or {"done": [], "fails": 0, "next_try": 0}

    def load(self):
        if self.data is None:
            try:
                from . import progression
                self.data = progression.load()
                # newborn: цель Novice — persona["job"] или job жителя в brain/world/roster.json по имени
                persona = getattr(self.mind, "persona", None) or {}
                self.target = persona.get("job") or progression.target_job(persona.get("name"))
            except (OSError, ValueError, KeyError) as e:
                log.warning("прогрессия недоступна: %s", e)
                self.data = {}
        return self.data or None

    def save(self):
        self.mind.mem.set("career_state", self.st)

    async def tick(self):
        now = self.clock()
        state = self.mind.state
        if not self.mind.fresh_state or state.get("dead") or not self.load():
            return
        from . import progression
        if now - self.last_summary >= SUMMARY_EVERY:
            self.last_summary = now
            try:
                text = progression.summary(state, self.data, now, target=self.target)    # newborn: target
                self.mind.mem.set("career", {"text": text, "ts": now})
            except (KeyError, TypeError, ValueError) as e:
                log.warning("сводка прогрессии: %s", e)
        if not self.cfg.get("auto_job_change") or now < self.st.get("next_try", 0):
            return
        if quest_busy(self.mind, state, now):                   # review2: и этап дома (Kafra), отправленный только что
            return
        r = self.mind.routine
        if not (r and r.in_town_mode and r.st.get("arrived")) or self.mind.plans.store.active():
            return
        ok, blocker = self.mind.may_move("plan")
        if not ok:
            return
        econ = getattr(self.mind, "economy", None)
        if econ and (econ.body_busy() or econ.mail_busy()):   # review: этап уводит тело к NPC — сделку не рвать
            return
        try:
            action = progression.stage_action(state, self.data, done=tuple(self.st.get("done", [])),
                                               target=self.target)
        except (KeyError, TypeError, ValueError) as e:
            log.warning("этап прогрессии: %s", e)
            return
        if not action:
            return
        self.st["next_try"] = now + RETRY_GAP                # не чаще раза в час, даже если тело отклонит
        self.save()
        self.note("career_stage_start", f"Иду проходить этап {action['path']}/{action['stage']}.", 2,
                  path=action["path"], stage=action["stage"])
        await self.mind.execute([action], source="career", reason=f"карьера: этап {action['stage']}", protocol=True)

    def on_result(self, event):
        stage, path = event.get("stage"), event.get("path")
        if event.get("ok"):
            self.st["done"] = (self.st.get("done", []) + [stage])[-30:]
            self.st["fails"] = 0
            self.note("career_stage_done", f"Этап {path}/{stage} пройден — по данным игры.", 4, path=path, stage=stage)
        else:
            self.st["fails"] = self.st.get("fails", 0) + 1
            self.note("career_stage_failed", f"Этап {path}/{stage} не пройден: {event.get('reason')}.", 2,
                      path=path, stage=stage, reason=event.get("reason"))
            if self.st["fails"] >= MAX_FAILS:
                self.st["next_try"] = self.clock() + 86400
                self.st["fails"] = 0
                alert = getattr(self.mind, "alert", None)
                if alert:
                    alert("career", f"этап {path}/{stage}: {MAX_FAILS} провала подряд — пауза на сутки")
        self.save()

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "career", "event": kind, "text": text, **data})
        log.info("%s", text)
