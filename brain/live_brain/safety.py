"""Правила безопасности без LLM. Через них проходит любое действие — от правил и от модели.

- мёртвому персонажу никаких действий;
- при HP ниже safe_hp нельзя менять карту охоты и снимать паузу;
- карта охоты только из списка характера;
- чат: не больше say_limit сообщений в общий чат за 10 минут, личка — не чаще раза
  в whisper_gap секунд одному игроку и не больше whisper_limit за 10 минут;
- пауза, поставленная мозгом, не дольше max_pause секунд (потом resume по правилу);
- группа и следование только с другими жителями-ботами (peers); группа создаётся
  только с именем LR_<своё имя> и только если персонаж не в группе;
- передача (give) только жителю, предмет — ID или zeny, количество 1..MAX_GIVE; лавка
  (shop_open/shop_close) и передача — только от исполнителей правил, не от модели.
"""
import time

ACTIONS = ("say", "whisper", "set_hunt_map", "pause", "resume",
           "party_create", "party_invite", "party_accept", "party_leave", "follow", "unfollow")
# Только исполнители плана (plans.py), распорядка (routine.py) и экономики (economy.py) — модель их не получает.
PLAN_ACTIONS = ("meet_point", "clear_point", "hunt", "sit", "stand", "unstuck", "give", "shop_open", "shop_close")
MAX_GIVE = 100000
PEER_ONLY = ("party_invite", "follow")
WINDOW = 600


class SafetyPolicy:
    def __init__(self, hunt_maps, safe_hp=30, say_limit=3, whisper_limit=10, whisper_gap=10,
                 max_pause=600, peers=(), extra_point_maps=()):
        self.hunt_maps = list(hunt_maps)
        self.point_maps = set(hunt_maps) | set(extra_point_maps)     # охота + город отдыха
        self.peers = set(peers)
        self.safe_hp = safe_hp
        self.say_limit = say_limit
        self.whisper_limit = whisper_limit
        self.whisper_gap = whisper_gap
        self.max_pause = max_pause
        self.said = []
        self.whispered = []          # [(время, кому)]
        self.paused_at = None

    def _recent(self, items, now):
        return [x for x in items if now - (x[0] if isinstance(x, tuple) else x) < WINDOW]

    def check(self, action, state, now=None, protocol=False):
        """Возвращает (нормализованное действие, None) или (None, причина отказа).

        protocol=True — служебные действия исполнителя плана: шёпот протокола встречи не
        упирается в чат-лимиты, точка встречи разрешена. Модель protocol не получает.
        """
        now = now or time.time()
        allowed = ACTIONS + (PLAN_ACTIONS if protocol else ())
        if not isinstance(action, dict) or action.get("action") not in allowed:
            return None, "неизвестное действие"
        kind = action["action"]
        if kind in ("clear_point", "stand", "shop_close"):
            return {"action": kind}, None                     # вернуть к охоте / встать можно всегда
        if state.get("dead"):
            return None, "персонаж мёртв"
        if kind in ("sit", "shop_open"):
            return {"action": kind}, None
        if kind == "unstuck":
            try:
                radius = max(5, min(30, int(action.get("radius", 10))))
            except (TypeError, ValueError):
                radius = 10
            return {"action": "unstuck", "radius": radius}, None
        if kind == "give":
            to, item = action.get("to"), action.get("item")
            if to not in self.peers:
                return None, "передавать можно только жителям"
            if not (item == "zeny" or (isinstance(item, int) and 0 < item < 1000000)):
                return None, "неверный предмет"
            amount = action.get("amount")
            if not isinstance(amount, int) or not 0 < amount <= MAX_GIVE:
                return None, "неверное количество"
            return {"action": "give", "to": to, "item": item, "amount": amount}, None
        if kind == "hunt":
            if action.get("map") not in self.hunt_maps:
                return None, "карта охоты не из списка hunt_maps"
            return {"action": "hunt", "map": action["map"]}, None
        if kind == "meet_point":
            if action.get("map") not in self.point_maps:
                return None, "точка не на разрешённой карте"
            try:
                x, y = int(action.get("x")), int(action.get("y"))
            except (TypeError, ValueError):
                return None, "неверные координаты"
            if not (0 < x < 1000 and 0 < y < 1000):
                return None, "координаты вне карты"
            return {"action": "meet_point", "map": action["map"], "x": x, "y": y}, None
        hp = state.get("hp_pct")
        if kind in ("say", "whisper"):
            text = " ".join(str(action.get("text", "")).split())[:100]
            if not text:
                return None, "пустой текст"
            action = dict(action, text=text)
        if protocol and kind in ("say", "whisper"):
            to = str(action.get("to", "")).strip()
            if kind == "whisper" and (not to or len(to) > 23 or '"' in to):
                return None, "неверный адресат"
            if kind == "whisper":
                action = dict(action, to=to)
        elif kind == "say":
            self.said = self._recent(self.said, now)
            if len(self.said) >= self.say_limit:
                return None, f"лимит общего чата {self.say_limit}/10 мин"
            self.said.append(now)
        elif kind == "whisper":
            to = str(action.get("to", "")).strip()
            if not to or len(to) > 23 or '"' in to:
                return None, "неверный адресат"
            self.whispered = self._recent(self.whispered, now)
            if len(self.whispered) >= self.whisper_limit:
                return None, f"лимит лички {self.whisper_limit}/10 мин"
            if any(who == to and now - t < self.whisper_gap for t, who in self.whispered):
                return None, f"слишком часто пишу {to}"
            self.whispered.append((now, to))
            action = dict(action, to=to)
        elif kind == "set_hunt_map":
            if action.get("map") not in self.hunt_maps:
                return None, "карта не из списка hunt_maps"
            if hp is not None and hp < self.safe_hp:
                return None, f"HP {hp}% < {self.safe_hp}%: не меняю карту"
        elif kind == "resume":
            if hp is not None and hp < self.safe_hp:
                return None, f"HP {hp}% < {self.safe_hp}%: не снимаю паузу"
            self.paused_at = None
        elif kind == "pause":
            self.paused_at = self.paused_at or now
        elif kind in PEER_ONLY:
            if action.get("to") not in self.peers:
                return None, "группа и следование только с жителями"
        elif kind == "party_create":
            if state.get("party"):
                return None, f"уже в группе {state.get('party')}"
            name = f"LR_{state.get('name', '')}"[:23]
            if not state.get("name") or not all(c.isascii() and (c.isalnum() or c == "_") for c in name):
                return None, "имя персонажа не подходит для группы LR_<имя>"
            action = dict(action, name=name)
        elif kind == "party_leave" and not state.get("party"):
            return None, "не в группе"
        elif kind == "unfollow" and not state.get("follow"):
            return None, "никого не сопровождаю"
        clean = {k: action[k] for k in ("action", "text", "to", "map", "name") if k in action}
        return clean, None

    def pause_expired(self, now=None):
        now = now or time.time()
        return self.paused_at is not None and now - self.paused_at >= self.max_pause
