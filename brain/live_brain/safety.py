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
  (shop_open/shop_close) и передача — только от исполнителей правил, не от модели;
- эмоция (emote) — только от исполнителей правил (social.py), номер из EMOTES, не больше
  EMOTE_LIMIT за 10 минут;
- чат-комната (chat_room) — только от исполнителей правил (society.py): открыть — в городе, заголовок
  ≤ 36 символов/байт без '#', не чаще CHAT_ROOM_GAP; закрыть — всегда (society:).
- гильдия (guild_create/guild_invite/guild_say/guild_expect) — только от исполнителей правил (guild.py):
  имя по правилам rAthena, приглашение — только жителю (guild:).
"""
import re
import time

ACTIONS = ("say", "whisper", "set_hunt_map", "pause", "resume",
           "party_create", "party_invite", "party_accept", "party_leave", "follow", "unfollow")
# Только исполнители плана (plans.py), распорядка (routine.py), экономики (economy.py) и общения (social.py) — модель их не получает.
PLAN_ACTIONS = ("friend_request", "job_change", "sleep", "service", "meet_point", "clear_point", "hunt", "sit", "stand", "unstuck", "give", "shop_open", "shop_close",
                "emote")
PLAN_ACTIONS += ("offer_sell", "offer_buy", "offer_shop", "mail_send", "mail_check", "mail_take")   # market: торговля и почта (economy.py)
PLAN_ACTIONS += ("pet_setup", "pet_tame", "pet_hatch")   # pets: питомец (pets.py, ORG-051)
PLAN_ACTIONS += ("party_say",)   # crew: чат группы (crew.py, ORG-053)
PLAN_ACTIONS += ("chat_room",)   # society: чат-комната-вывеска (society.py, ORG-026)
PLAN_ACTIONS += ("guild_create", "guild_invite", "guild_say", "guild_expect")   # guild: гильдия (guild.py, ORG-052)
GUILD_NAME = re.compile(r"^[A-Za-z0-9 ]{1,23}$")   # guild: rAthena NAME_LENGTH 24, char_name_letters upstream
PLAN_ACTIONS += ("explore",)     # explore: экспедиция на карту атласа (explore.py, ORG-054)
CHAT_ROOM_GAP = 600              # society: открывать не чаще раза в 10 мин
CHAT_TITLE_MAX = 36              # society: символов и байт UTF-8 (rAthena CHATROOM_TITLE_SIZE 36+1)
MAX_PRICE = 100_000_000          # market: цена лота между жителями
MAIL_PER_DAY = 5                 # market: писем в сутки от одного жителя (rAthena mail_daily_count 100 — наш лимит строже)
# Безопасные эмоции (номер -> команда OpenKore «e <команда>», tables/emotions.txt); тот же список в brainBridge.pl.
EMOTES = {1: "?", 2: "ho", 3: "lv", 5: "ic", 9: "...", 12: "wav", 15: "thx", 17: "sry", 18: "heh",
          20: "hmm", 21: "no1", 28: "sob", 29: "gg", 33: "ok"}
EMOTE_LIMIT = 6
MAX_GIVE = 100000
PEER_ONLY = ("party_invite", "follow")
WINDOW = 600


# OpenKore режет сообщение длиннее message_length_max (80 символов в профилях) на несколько шёпотов
# (Misc::sendMessage) — машинная метка оторвалась бы от текста. Одно сообщение — не длиннее MAX_TEXT.
MAX_TEXT = 78
TAIL_TAG = re.compile(r"\s*(\[[a-z]+:[^\[\]]{1,40}\])$")


def fit_text(text, limit=MAX_TEXT):
    """Схлопнуть пробелы и уложить в limit символов; метку [вид:...] в конце сохранить целиком."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    m = TAIL_TAG.search(text)
    if not m:
        return text[:limit].rstrip()
    tag = m.group(1)
    body = text[:m.start()][:max(0, limit - len(tag) - 1)].rstrip()
    return f"{body} {tag}".strip()


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
        self.texts = {}              # (кому, текст) -> время: не повторять одно и то же (AUT-095)
        self.emoted = []             # время эмоций: не больше EMOTE_LIMIT за WINDOW
        self.chat_maps = set(extra_point_maps)   # society: чат-комната — только в городе отдыха
        self.room_opened = 0.0                   # society: когда открывал комнату

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
        if kind == "chat_room":                                   # society: закрыть можно всегда
            return self.check_chat_room(action, state, now)       # society:
        if kind in ("clear_point", "stand", "shop_close"):
            return {"action": kind}, None                     # вернуть к охоте / встать можно всегда
        if state.get("dead"):
            return None, "персонаж мёртв"
        if kind in ("sit", "shop_open"):
            return {"action": kind}, None
        if kind == "emote":
            eid = action.get("emotion", action.get("id"))   # society: номер эмоции (id — устаревшее имя поля)
            if isinstance(eid, bool) or not isinstance(eid, int) or eid not in EMOTES:
                return None, "эмоция не из списка EMOTES"
            self.emoted = self._recent(self.emoted, now)
            if len(self.emoted) >= EMOTE_LIMIT:
                return None, f"лимит эмоций {EMOTE_LIMIT}/10 мин"
            self.emoted.append(now)
            # society: поле id сообщения затирает bridge.send_action (номер действия для ack) — мост читает emotion
            return {"action": "emote", "id": eid, "emotion": eid}, None
        if kind == "friend_request":
            if action.get("to") not in self.peers:
                return None, "дружба — только с жителями"
            return {"action": "friend_request", "to": action["to"]}, None
        if kind == "job_change":
            # Шаги — только из brain/world/progression.json (career.py), их проверяет и плагин jobChange.
            steps = action.get("steps")
            if not (isinstance(action.get("path"), str) and isinstance(action.get("stage"), str)
                    and isinstance(steps, list) and 0 < len(steps) <= 80):
                return None, "неверный этап"
            return {k: action[k] for k in ("action", "path", "stage", "steps", "success") if k in action}, None
        if kind == "service":
            return {"action": "service"}, None
        if kind == "sleep":
            sec = action.get("seconds")
            if not isinstance(sec, int) or not 600 <= sec <= 43200:
                return None, "сон от 10 минут до 12 часов"
            return {"action": "sleep", "seconds": sec}, None
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
        if kind in ("offer_sell", "offer_buy", "offer_shop", "mail_send", "mail_check", "mail_take"):   # market:
            return self.check_market(kind, action, state, now)                                       # market:
        if kind == "party_say":                                                                       # crew:
            text = fit_text(str(action.get("text", "")))
            if not text or not state.get("party"):
                return None, "пустой текст или нет группы"
            return {"action": "party_say", "text": text}, None
        if kind in ("guild_create", "guild_invite", "guild_say", "guild_expect"):                    # guild:
            return self.check_guild(kind, action, state)                                             # guild:
        if kind == "explore":                                                                         # explore:
            return self.check_explore(action, state)                                                 # explore:
        if kind in ("pet_setup", "pet_tame", "pet_hatch"):                                           # pets:
            return self.check_pet(kind, action)                                                      # pets:
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
            text = fit_text(str(action.get("text", "")))
            if not text:
                return None, "пустой текст"
            action = dict(action, text=text)
        if not protocol and kind in ("say", "whisper"):
            key = (str(action.get("to", "")) if kind == "whisper" else "", action["text"].lower())
            self.texts = {k: t for k, t in self.texts.items() if now - t < 3600}
            if key in self.texts:
                return None, "та же реплика тому же адресату меньше часа назад"
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
        if not protocol and kind in ("say", "whisper"):
            self.texts[(str(clean.get("to", "")) if kind == "whisper" else "", clean["text"].lower())] = now
        return clean, None

    def check_explore(self, action, state):                                                     # explore:
        """explore: экспедиция (ORG-054) — карта из атласа, не pvp/gvg, не полигон новичков/перестроенный izlude
        (explore.denied), риск по уровню ниже atlas.MAX_RISK, HP не ниже safe_hp; координаты — в пределах карты."""
        from . import atlas
        from .explore import DEFAULTS, denied
        hmap = action.get("map")
        if not isinstance(hmap, str) or not re.fullmatch(r"[a-z0-9_]{3,16}", hmap):
            return None, "неверная карта"
        try:
            a = atlas.default()
        except (OSError, ValueError):
            return None, "атлас недоступен"
        m = a.maps.get(hmap)
        if not m:
            return None, "карты нет в атласе"
        if m.get("kind") == "pvp" or {"pvp", "gvg", "gvg_castle"} & set(m.get("flags", ())):
            return None, "pvp/gvg-карта"
        if denied(hmap, DEFAULTS):
            return None, "карта запрещена для экспедиций (полигон/перестроена/гильдия)"
        level = state.get("lv")
        if isinstance(level, int) and level > 0:
            risk, _ = a.danger_for(hmap, level)
            if risk >= atlas.MAX_RISK:
                return None, f"риск {risk:.2f} для уровня {level}"
        hp = state.get("hp_pct")
        if hp is not None and hp < self.safe_hp:
            return None, f"HP {hp}% < {self.safe_hp}%"
        clean = {"action": "explore", "map": hmap}
        if action.get("x") is not None or action.get("y") is not None:
            x, y = action.get("x"), action.get("y")
            if not all(isinstance(v, int) and not isinstance(v, bool) and 0 < v < 1000 for v in (x, y)):
                return None, "неверные координаты"
            clean.update(x=x, y=y)
        return clean, None

    def check_pet(self, kind, action):  # pets: ID — целые из pets.json, списки короткие; мёртвому — отказ выше
        ints = lambda v, lo, hi: isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi
        if kind == "pet_setup":
            items, mobs = action.get("items") or [], action.get("mobs") or []
            if not (isinstance(items, list) and isinstance(mobs, list) and len(items) <= 20 and len(mobs) <= 20
                    and all(ints(i, 100, 99999) for i in items) and all(ints(m, 1000, 99999) for m in mobs)):
                return None, "неверные списки питомца"
            return {"action": kind, "food_on": bool(action.get("food_on")), "items": items, "mobs": mobs}, None
        if kind == "pet_tame":
            if not (ints(action.get("item"), 100, 99999) and ints(action.get("mob"), 1000, 99999)):
                return None, "неверный предмет или монстр"
            return {"action": kind, "item": action["item"], "mob": action["mob"]}, None
        if not ints(action.get("egg"), 9000, 9999):
            return None, "неверное яйцо"
        return {"action": kind, "egg": action["egg"]}, None

    def check_guild(self, kind, action, state):                                                    # guild:
        """guild: только исполнитель правил (guild.py); имя — латиница/цифры/пробел ≤ 23; звать и ждать
        приглашения — только жителей и гильдию с таким именем; создать — не в гильдии; говорить и звать — в гильдии."""
        in_guild = bool((state.get("guild") or {}).get("name")) if isinstance(state.get("guild"), dict) else False
        if kind in ("guild_create", "guild_expect"):
            name = " ".join(str(action.get("name", "")).split())
            if not GUILD_NAME.match(name):
                return None, "имя гильдии: 1..23 латинских букв, цифр, пробелов"
            if in_guild:
                return None, "уже в гильдии"
            return {"action": kind, "name": name}, None
        if not in_guild:
            return None, "не в гильдии"
        if kind == "guild_invite":
            if action.get("to") not in self.peers:
                return None, "в гильдию — только жителей"
            return {"action": kind, "to": action["to"]}, None
        text = fit_text(str(action.get("text", "")))
        if not text:
            return None, "пустой текст"
        return {"action": kind, "text": text}, None

    def check_chat_room(self, action, state, now):                                             # society:
        """society: чат-комната — op close всегда; op open только живому, в городе, без лавки, заголовок
        1..36 символов и ≤ 36 байт UTF-8 без '#' и '"', лимит 2..20, не чаще CHAT_ROOM_GAP."""
        op = action.get("op")
        if op == "close":
            return {"action": "chat_room", "op": "close"}, None
        if op != "open":
            return None, "чат-комната: op open или close"
        if state.get("dead"):
            return None, "персонаж мёртв"
        if state.get("map") not in self.chat_maps:
            return None, "чат-комната только в городе"
        if (state.get("vend") or {}).get("open"):
            return None, "открыта лавка"
        title = " ".join(str(action.get("title", "")).split())
        if not title or "#" in title or '"' in title or not title.isprintable():
            return None, "заголовок пустой или с запрещёнными символами"
        if len(title) > CHAT_TITLE_MAX or len(title.encode("utf-8")) > CHAT_TITLE_MAX:
            return None, f"заголовок длиннее {CHAT_TITLE_MAX} символов/байт"
        limit = action.get("limit", 5)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 2 <= limit <= 20:
            return None, "лимит комнаты 2..20"
        if now - self.room_opened < CHAT_ROOM_GAP:
            return None, f"чат-комнату не чаще раза в {CHAT_ROOM_GAP // 60} мин"
        self.room_opened = now
        return {"action": "chat_room", "op": "open", "title": title, "limit": limit}, None

    def check_market(self, kind, action, state, now):                                             # market:
        """market: торговля с жителем (offer_*) и почта RODEX (mail_*) — только жителям, числа в пределах."""
        def num(key, lo, hi):
            v = action.get(key)
            return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None

        if kind == "mail_check":
            return {"action": "mail_check"}, None
        if kind == "mail_take":
            mid = num("mail_id", 1, 9_999_999_999)
            return ({"action": "mail_take", "mail_id": mid}, None) if mid else (None, "неверный номер письма")
        if kind == "offer_shop":
            title = " ".join(str(action.get("title", "")).replace("#", "").split())[:36]
            items = action.get("items")
            if not title or not isinstance(items, list) or not 0 < len(items) <= 12:
                return None, "лавка: название и 1..12 товаров"
            clean = []
            for it in items:
                ok = (isinstance(it, dict) and all(isinstance(it.get(k), int) and not isinstance(it.get(k), bool)
                                                   for k in ("id", "price", "amount"))
                      and 0 < it["id"] < 1000000 and 0 < it["price"] <= 1_000_000_000 and 0 <= it["amount"] <= 30000)
                if not ok:
                    return None, "лавка: неверный товар"
                clean.append({"id": it["id"], "price": it["price"], "amount": it["amount"]})
            return {"action": "offer_shop", "title": title, "items": clean}, None
        who = "from" if kind == "offer_buy" else "to"
        if action.get(who) not in self.peers:
            return None, "торговать и писать можно только жителям"
        if kind == "mail_send":
            title = " ".join(str(action.get("title", "")).split())
            body = " ".join(str(action.get("body", "")).split())
            if not 4 <= len(title) <= 24 or not 0 < len(body) <= 200:
                return None, "письмо: заголовок 4-24, текст 1-200 символов"
            zeny = num("zeny", 0, MAX_GIVE) if "zeny" in action else 0
            if zeny is None:
                return None, "неверная сумма"
            clean = {"action": "mail_send", "to": action["to"], "title": title, "body": body, "zeny": zeny}
            if action.get("item") is not None:
                item, amount = num("item", 1, 999999), num("amount", 1, 30000)
                if not item or not amount:
                    return None, "неверный предмет"
                clean.update(item=item, amount=amount)
            self.mailed = [t for t in getattr(self, "mailed", []) if now - t < 86400]
            if len(self.mailed) >= MAIL_PER_DAY:
                return None, f"лимит писем {MAIL_PER_DAY} в сутки"
            self.mailed.append(now)
            return clean, None
        item, amount, price = num("item", 1, 999999), num("amount", 1, 30000), num("price", 1, MAX_PRICE)
        if not item or not amount or not price:
            return None, "неверный предмет, количество или цена"
        if kind == "offer_buy" and state.get("zeny") is not None and int(state["zeny"]) < price:
            return None, "не хватает зени"
        return {"action": kind, who: action[who], "item": item, "amount": amount, "price": price}, None

    def pause_expired(self, now=None):
        now = now or time.time()
        return self.paused_at is not None and now - self.paused_at >= self.max_pause
