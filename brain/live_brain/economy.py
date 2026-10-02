"""Взаимопомощь жителей: поделиться зельями и зени при встрече в городе. Правила, без LLM.

Сами продажа лута, закупка зелий и склад Kafra — настройки OpenKore (sellAuto, buyAuto,
storageAuto) и плагин economy. Этот модуль решает только, кто кому и что отдаёт.

Протокол — шёпот жителю с меткой (человекочитаемый текст + машинная метка):
    [need:<id>:<предмет>:<сколько>]   прошу; предмет — ID (501) или z (зени)
    [need:<id>:ok]                    отдаю (тело идёт к просящему и предлагает сделку)
    [need:<id>:no]                    не могу

Когда прошу: режим «отдых в городе», нет активного плана встречи, житель виден рядом,
мне не хватает предмета из economy.share (меньше need_below) или зени (меньше need_below),
с прошлой просьбы прошло ask_gap_minutes.
Когда отдаю: прошу не больше ask, у меня после передачи остаётся не меньше keep,
за сутки отдал меньше gifts_per_day раз, не идёт другая передача.
Доказательства:
    «отдал»     — событие give_result ok от тела: сервер завершил сделку;
    «получил»   — количество предмета/зени в моём состоянии из игры выросло после ok.
Слова и ack команды доказательством не считаются.
"""
import logging
import re
import secrets
import time

log = logging.getLogger("economy")

TAG = re.compile(r"\[need:([a-z0-9]{4,8}):(?:(ok|no)|(z|\d{1,6}):(\d{1,7}))\]")
NEAR = 8                  # житель виден не дальше 8 клеток
ANSWER_TIMEOUT = 120      # ответ на просьбу
RECEIVE_TIMEOUT = 240     # после ok — дождаться передачи
GIVE_TIMEOUT = 180        # отдаю: итог give_result от тела должен прийти раньше


def dist(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


class Economy:
    def __init__(self, mind, cfg, clock=time.time):
        self.mind = mind
        self.cfg = cfg
        self.clock = clock
        self.share = {str(k): v for k, v in (cfg.get("share") or {}).items()}
        self.zeny = cfg.get("zeny") or {}
        self.gap = cfg.get("ask_gap_minutes", 20) * 60
        self.per_day = cfg.get("gifts_per_day", 6)
        mem = mind.mem
        self.req = mem.get("econ_request")           # моя просьба: id, peer, item, amount, base, status, since
        self.last_ask = mem.get("econ_last_ask", 0)
        self.giving = None                           # что отдаю сейчас: id, to, item, amount

    # ---------- данные ----------

    @property
    def state(self):
        return self.mind.state

    def have(self, item, state=None):
        state = state or self.state
        if item == "z":
            return int(state.get("zeny") or 0)
        return int((state.get("items") or {}).get(str(item), 0) or 0)

    def item_name(self, item):
        return "зени" if item == "z" else self.share.get(str(item), {}).get("name", f"предмет {item}")

    def label(self, item, amount):
        return f"{amount} {self.item_name(item)}"

    def peer_near(self, state):
        if state.get("x") is None:
            return None
        for p in state.get("players") or []:
            if (isinstance(p, dict) and p.get("name") in self.mind.ctx.peers and p.get("x") is not None
                    and dist(int(state["x"]), int(state["y"]), int(p["x"]), int(p["y"])) <= NEAR):
                return p["name"]
        return None

    def my_need(self, state):
        if not state.get("items"):
            return None                              # тело без плагина economy — счётчиков нет
        for item, rule in self.share.items():
            if self.have(item, state) < rule.get("need_below", 0):
                return item, int(rule["ask"])
        if self.zeny and self.have("z", state) < self.zeny.get("need_below", 0):
            return "z", int(self.zeny["ask"])
        return None

    def gifts_today(self, now):
        return self.mind.mem.count_events("gift_given", now - 86400)

    def summary(self):
        s = self.state
        out = {"зени": s.get("zeny"),
               "запасы": {self.share[i].get("name", i): self.have(i) for i in self.share} if s.get("items") else None,
               "отдал_за_сутки": self.gifts_today(self.clock())}
        if self.req:
            out["моя_просьба"] = {"кому": self.req["peer"], "что": self.label(self.req["item"], self.req["amount"]),
                                  "статус": self.req["status"]}
        return out

    def save(self):
        self.mind.mem.set("econ_request", self.req)
        self.mind.mem.set("econ_last_ask", self.last_ask)

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "economy", "event": kind, "text": text, **data})
        log.info("%s", text)

    async def whisper(self, to, text, reason):
        await self.mind.execute([{"action": "whisper", "to": to, "text": text[:100]}],
                                source="economy", reason=reason, protocol=True)

    # ---------- тик: просить и проверять ----------

    def can_ask(self):
        r = self.mind.routine
        return (r is None or r.in_town_mode) and not self.mind.plans.store.active()

    async def tick(self):
        now = self.clock()
        if not self.mind.fresh_state:
            return
        state = self.state
        if self.giving and now - self.giving["since"] >= GIVE_TIMEOUT:
            g, self.giving = self.giving, None           # тело не сообщило итог (перезапуск?) — не держать
            self.note("gift_failed", f"Нет итога передачи {g['to']} за {GIVE_TIMEOUT} с.", 1, peer=g["to"])
        if self.req:
            await self.check_request(now, state)
            return
        if state.get("dead") or not self.can_ask() or now - self.last_ask < self.gap:
            return
        need = self.my_need(state)
        if need:
            await self.ask(*need)

    async def ask(self, item, amount, force=False):
        """Попросить у жителя рядом. force — команда оператора: без проверки нехватки и интервала."""
        now = self.clock()
        state = self.state
        if not re.fullmatch(r"z|\d{1,6}", item) or not isinstance(amount, int) or amount <= 0:
            return "неверный предмет или количество"
        if self.req:
            return "уже жду ответа на просьбу"
        if state.get("dead"):
            return "персонаж мёртв"
        if force and not self.mind.fresh_state:
            return "нет состояния от тела"
        peer = self.peer_near(state)
        if not peer:
            return "рядом нет жителя"
        rid = secrets.token_hex(3)
        self.req = {"id": rid, "peer": peer, "item": item, "amount": amount, "base": self.have(item, state),
                    "status": "asked", "since": now}
        self.last_ask = now
        self.save()
        text = f"{peer}, у меня почти кончились {self.item_name(item)}. Выручишь?"
        tag = f"[need:{rid}:{item}:{amount}]"
        await self.whisper(peer, f"{text[:99 - len(tag)]} {tag}", "экономика: прошу у жителя")   # метка не обрезается
        self.note("gift_asked", f"Попросил у {peer} {self.label(item, amount)}.", 1, peer=peer, item=item, amount=amount,
                  force=force)
        return None

    async def check_request(self, now, state):
        r = self.req
        if r["status"] == "asked" and now - r["since"] >= ANSWER_TIMEOUT:
            self.finish(f"{r['peer']} не ответил на просьбу.", "gift_failed", 1)
        elif r["status"] == "accepted":
            got = self.have(r["item"], state) - r["base"]
            if got > 0:
                self.mind.mem.update_relation(r["peer"], 1, "выручил, когда мне не хватало")
                self.finish(f"{r['peer']} дал мне {self.label(r['item'], got)} — по данным игры.", "gift_received", 3,
                            got=got)
            elif now - r["since"] >= RECEIVE_TIMEOUT:
                self.finish(f"{r['peer']} согласился, но передача не пришла.", "gift_failed", 1)

    def finish(self, text, kind, importance, **data):
        r = self.req
        self.req = None
        self.save()
        self.note(kind, text, importance, peer=r["peer"], item=r["item"], amount=r["amount"], **data)

    # ---------- входящие ----------

    async def on_tag(self, sender, text):
        m = TAG.search(text or "")
        if not m:
            return
        rid, answer, item, amount = m.groups()
        if answer:
            r = self.req
            if not r or r["id"] != rid or r["peer"] != sender or r["status"] != "asked":
                return
            if answer == "no":
                self.finish(f"{sender} не смог поделиться {self.label(r['item'], r['amount'])}.", "gift_failed", 1)
            else:
                r.update(status="accepted", since=self.clock(), base=self.have(r["item"]))
                self.save()
                log.info("%s согласился передать %s", sender, self.label(r["item"], r["amount"]))
            return
        await self.on_ask(sender, rid, item, int(amount))

    def refuse_reason(self, sender, item, amount):
        now = self.clock()
        state = self.state
        if sender not in self.mind.ctx.peers:
            return "ты не житель"
        if state.get("dead"):
            return "я мёртв"
        if self.mind.plans.store.active():
            return "иду на встречу"
        if self.giving or state.get("give"):
            return "уже передаю"
        if (state.get("vend") or {}).get("open"):
            return "стою с лавкой"
        if self.gifts_today(now) >= self.per_day:
            return f"за сутки уже отдал {self.per_day} раз"
        rule = self.zeny if item == "z" else self.share.get(item)
        if not rule:
            return "этим не делюсь"
        if amount > int(rule.get("ask", 0)):
            return "слишком много"
        if item != "z" and not state.get("items"):
            return "не знаю своих запасов"
        if self.have(item) - amount < int(rule.get("keep", 0)):
            return "самому мало"
        return None

    async def on_ask(self, sender, rid, item, amount):
        why = self.refuse_reason(sender, item, amount)
        if why:
            await self.whisper(sender, f"Извини, не могу: {why}. [need:{rid}:no]", "экономика: отказ")
            self.note("gift_refused", f"{sender} просил {self.label(item, amount)}, отказал: {why}.", 1,
                      peer=sender, item=item, amount=amount)
            return
        await self.whisper(sender, f"Держи, сейчас подойду. [need:{rid}:ok]", "экономика: отдаю")
        self.giving = {"id": rid, "to": sender, "item": item, "amount": amount, "since": self.clock()}
        give_item = "zeny" if item == "z" else int(item)
        await self.mind.execute([{"action": "give", "to": sender, "item": give_item, "amount": amount}],
                                source="economy", reason=f"экономика: отдать {sender} {self.label(item, amount)}",
                                protocol=True)

    def on_give_result(self, event):
        g = self.giving
        self.giving = None
        to = event.get("to") or (g or {}).get("to")
        item = "z" if event.get("item") == "zeny" else str(event.get("item"))
        amount = event.get("amount")
        if event.get("ok"):
            self.mind.mem.update_relation(str(to), 1, "помог ему, когда ему не хватало")
            self.note("gift_given", f"Отдал {to} {self.label(item, amount)} — сделка завершена сервером.", 2,
                      peer=to, item=item, amount=amount)
        else:
            self.note("gift_failed", f"Не получилось передать {to} {self.label(item, amount)}: {event.get('reason')}.",
                      1, peer=to, item=item, amount=amount)

    def on_rejected(self, action, why):
        """Тело или safety отклонили give — передача не началась."""
        if action.get("action") == "give" and self.giving:
            g, self.giving = self.giving, None
            self.note("gift_failed", f"Не смог начать передачу {g['to']}: {why}.", 1, peer=g["to"])
