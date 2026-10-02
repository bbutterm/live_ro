"""Дом и точка сохранения (ORG-014). Правила без LLM.

Дом жителя — город из brain/world/homes.json: persona.home -> home_town жителя в brain/world/roster.json (по имени)
-> homes.json default (prontera). Неизвестный город — дом по умолчанию и предупреждение в журнале.

Что знает OpenKore о точке сохранения. Сервер её клиенту не сообщает: rAthena savepoint
(src/map/script.cpp BUILDIN_FUNC(savepoint) -> pc_setsavepoint) пишет только в БД персонажа. OpenKore угадывает
saveMap лишь из списка варпов умения Teleport/Warp Portal (src/Network/Receive.pm:3574-3579, warp_portal_list),
поэтому мозг ведёт свою запись kv "home" и подтверждает её фактами игры:
    1) диалог Kafra: этап плагина jobChange (действие job_change, path "home") — move к NPC, talknpc, пункт меню
       по ТЕКСТУ "Save" (default-меню F_Kafra, npc/kafras/functions_kafras.txt:142-148), успех — фраза
       F_KafEnd «Your Respawn Point has been saved here» (functions_kafras.txt:452) и карта = дом;
    2) возрождение: после смерти живое тело на карте дома — точка сохранения дома; на другой карте — точка
       сохранения там (не дома), и при следующем приходе в город житель сохранится у Kafra.
Когда сохраняться: точка не подтверждена домом, житель в режиме town дошёл до города, тело на карте дома, нет плана
встречи, сделки, этапа квеста; арбитр разрешает владельца plan (как career). Неудача — повтор через retry_minutes,
после max_fails подряд — пауза на сутки и оповещение.
Точка отдыха распорядка (routine.town) — в домашнем городе (rest в homes.json; prontera — фонтан goals.json).
Ограничение: точки прогулок social (goals.json social.points) знают только Пронтеру — в другом доме прогулок нет.
Журнал: decisions.jsonl type home; память — сохранение и возрождение дома; события home_saved / home_respawn.
"""
import json
import logging
import time
from pathlib import Path

from .lifecycle import quest_busy                 # review2: один плагин jobChange на дом и карьеру

log = logging.getLogger("home")

WORLD = Path(__file__).resolve().parents[1] / "world"
HOMES = WORLD / "homes.json"
DEFAULT_HOME = "prontera"
PATH = "home"          # path действия job_change: итог этапа (job_change_result) — этому модулю, не career
STAGE = "kafra_save"


def home_town(persona, roster_path=WORLD / "roster.json"):
    """Домашний город по персоне: поле home, иначе home_town жителя в реестре (по имени), иначе None."""
    if persona.get("home"):
        return str(persona["home"])
    try:
        roster = json.loads(Path(roster_path).read_text(encoding="utf-8")).get("residents") or {}
    except (OSError, ValueError):
        return None
    for r in roster.values():
        if isinstance(r, dict) and r.get("name") == persona.get("name") and r.get("home_town"):
            return str(r["home_town"])
    return None


class Home:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, REQUIRES, EARLY = "home", "home", ("world",), True   # до SafetyPolicy: точка отдыха
    TICK_ORDER = 100            # job_change_result(path=home) и on_death — явно в mind (стык с карьерой и смертью)

    def __init__(self, mind, path=HOMES, roster_path=WORLD / "roster.json", clock=None):
        self.mind = mind
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.cfg = self.data.get("save") or {}
        towns = self.data["towns"]
        want = home_town(mind.persona, roster_path) or self.data.get("default", DEFAULT_HOME)
        self.unknown = None if want in towns else want
        if self.unknown:
            log.warning("дом %s не описан в homes.json — живу в %s", want, self.data.get("default", DEFAULT_HOME))
        self.town = want if want in towns else self.data.get("default", DEFAULT_HOME)
        self.rec = towns[self.town]
        self.st = mind.mem.get("home") or {}

    # ---------- данные ----------

    def save(self):
        self.mind.mem.set("home", self.st)

    def saved_map(self):
        return (self.st.get("saved") or {}).get("map")

    def at_home_saved(self):
        return self.saved_map() == self.town

    def rest_point(self, default):
        """Точка отдыха распорядка: в домашнем городе. default — точка goals.json (если она уже в доме — она)."""
        if default and default.get("map") == self.town:
            return default
        rest = self.rec.get("rest") or self.rec["savepoint"]
        return {"map": self.town, "x": int(rest["x"]), "y": int(rest["y"]), "radius": int(rest.get("radius", 3))}

    def summary(self):
        return {"дом": self.town, "точка_сохранения": self.saved_map() or "не подтверждена",
                "сохранён_дома": self.at_home_saved()}

    def action(self):
        """Этап для плагина jobChange: дойти до Kafra, выбрать «Save», дождаться фразы о точке возрождения."""
        npc, stand = self.rec["npc"], self.rec["stand"]
        return {"action": "job_change", "path": PATH, "stage": STAGE,
                "steps": [{"do": "move", "map": self.town, "x": stand["x"], "y": stand["y"]},
                          {"do": "talk", "x": npc["x"], "y": npc["y"],
                           "answers": [{"text": self.cfg.get("answer", "Save")}]}],
                "success": {"text": self.cfg.get("proof_text", "Respawn Point"), "map": self.town}}

    # ---------- тик ----------

    async def tick(self):
        if not self.mind.fresh_state:
            return
        now = self.clock()
        state = self.mind.state
        self.check_respawn(now, state)
        pending = self.st.get("pending")
        if pending and not (state.get("job_change") or {}).get("running"):
            if now - pending >= self.cfg.get("wait_result_minutes", 10) * 60:
                self.failed("нет итога этапа от тела", now)
            return
        if pending:
            return
        why = self.why_not_now(now, state)
        if why:
            return
        self.st["pending"] = now
        self.save()
        npc = self.rec["npc"]
        self.note("home_save_start", f"Точка сохранения не дома ({self.saved_map() or 'неизвестно где'}) — "
                                     f"сохранюсь у Kafra в {self.town} ({npc['x']},{npc['y']}).", 1)
        await self.mind.execute([self.action()], source="home", reason=f"дом: сохраниться у Kafra в {self.town}",
                                protocol=True)

    def why_not_now(self, now, state):
        """None — можно идти сохраняться сейчас, иначе причина (для тестов и отладки)."""
        if self.at_home_saved():
            return "уже сохранён дома"
        if now < self.st.get("next_try", 0):
            return "пауза после неудачи"
        if state.get("dead") or state.get("map") != self.town:
            return "не в домашнем городе"
        r = getattr(self.mind, "routine", None)
        if not (r and r.in_town_mode and r.st.get("arrived")):
            return "не отдых в городе"
        if self.mind.plans.store.active():
            return "план встречи"
        if quest_busy(self.mind, state, now):                     # review2: и этап карьеры, отправленный в этом такте
            return "идёт этап квеста"
        explorer = getattr(self.mind, "explorer", None)        # review2: экспедиция ведёт тело (ещё в городе) —
        if explorer and explorer.busy():                       # review2: Kafra после возвращения
            return "экспедиция"
        may_move = getattr(self.mind, "may_move", None)
        if may_move and not may_move("plan")[0]:
            return "телом владеет другая задача"
        econ = getattr(self.mind, "economy", None)
        if econ and (econ.body_busy() or econ.mail_busy()):
            return "сделка или почта"
        if (state.get("vend") or {}).get("open"):
            return "открыта лавка"
        return None

    # ---------- итоги ----------

    def on_result(self, event):
        """job_change_result с path "home" (mind.on_event направляет сюда)."""
        now = self.clock()
        self.st.pop("pending", None)
        if event.get("ok"):
            sp = self.rec["savepoint"]
            self.st["saved"] = {"map": self.town, "x": sp["x"], "y": sp["y"], "ts": now, "how": "kafra"}
            self.st["fails"] = 0
            self.st.pop("next_try", None)
            self.save()
            self.note("home_saved", f"Сохранился у Kafra в {self.town}: Kafra подтвердила точку возрождения — "
                                    "теперь после смерти появлюсь дома.", 3)
            return
        self.failed(str(event.get("reason") or "этап не пройден"), now)

    def failed(self, reason, now):
        self.st.pop("pending", None)
        self.st["fails"] = self.st.get("fails", 0) + 1
        self.st["next_try"] = now + self.cfg.get("retry_minutes", 30) * 60
        text = f"Не сохранился у Kafra в {self.town}: {reason}."
        if self.st["fails"] >= self.cfg.get("max_fails", 3):
            self.st["next_try"] = now + 86400
            self.st["fails"] = 0
            alert = getattr(self.mind, "alert", None)
            if alert:
                alert("home", f"{self.town}: сохранение у Kafra не удалось {self.cfg.get('max_fails', 3)} раза подряд")
            text += " Попробую завтра."
        self.save()
        self.note("home_save_failed", text, 2)

    def on_death(self, event=None):
        """Погиб: ждать возрождения и проверить, где оно (дом или нет)."""
        now = self.clock()
        expected = self.saved_map()
        self.st["respawn_wait"] = {"since": now, "died_map": (event or {}).get("map") or self.mind.state.get("map"),
                                   "expected": expected, "seen_dead": False}
        self.save()
        where = (f"дома в {self.town}" if expected == self.town else
                 f"в {expected}, а не дома" if expected else "там, где сохранялся (точка не подтверждена)")
        self.mind.write_decision({"type": "home", "event": "death", "expect": expected, "home": self.town,
                                  "text": f"Погиб — возрожусь {where}."})

    def check_respawn(self, now, state):
        wait = self.st.get("respawn_wait")
        if not wait:
            return
        if state.get("dead"):
            if not wait.get("seen_dead"):
                wait["seen_dead"] = True
                self.save()
            return
        if not wait.get("seen_dead") and now - wait.get("since", now) < 30:
            return                                            # состояние ещё «до смерти»
        where = state.get("map")
        if not where:
            return
        self.st.pop("respawn_wait", None)
        if where == wait.get("died_map") and where != self.town:
            self.save()                                       # подняли на месте (воскрешение) — не возрождение
            self.mind.write_decision({"type": "home", "event": "revived_in_place", "map": where})
            return
        before = self.saved_map()
        if where == self.town:
            if before != self.town:
                self.st["saved"] = {"map": where, "ts": now, "how": "respawn"}
            self.save()
            self.note("home_respawn", f"Возродился дома, в {self.town}.", 2, map=where, home=True)
            return
        self.st["saved"] = {"map": where, "ts": now, "how": "respawn"}
        self.save()
        self.note("home_respawn", f"Возродился в {where}, а дом — {self.town}: при случае сохранюсь у Kafra дома.",
                  3, map=where, home=False)

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, {"home": self.town, **data})
        self.mind.write_decision({"type": "home", "event": kind, "text": text, "home": self.town,
                                  "saved": self.saved_map(), **data})
        log.info("дом: %s", text)
