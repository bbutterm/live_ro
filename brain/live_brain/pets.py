"""Питомец жителя (ORG-051): кого хочется приручить, приручение, вылупление, корм. Правила без LLM.

Данные — brain/world/pets.json (scripts/gen_pets.py из db/re/pet_db.yml): монстр, предмет приручения, яйцо, корм.
Тело — плагин bots/plugins/pets (pet_setup / pet_tame / pet_hatch, состояние state.pet).

Любимцы жителя: питомцы, чьи монстры живут на его картах охоты (атлас) и не сильнее его уровня; впереди те,
что едят Pet Food (его докупает OpenKore у Pet Groomer Пронтеры), затем по шансу поимки. Предметы
приручения любимцев житель держит (items_control) и хочет купить у жителей (economy.wishlist через wants()).
Желание завести питомца — от черты generosity (мотив care, needs.py): при generosity < MIN_CARE не заводит.
Шаги:
    после подключения тела — pet_setup: что отслеживать (предметы любимцев и их монстры), докупать ли Pet Food;
    яйцо в рюкзаке и Pet Incubator — pet_hatch (не в бою: HP ≥ SAFE_HP);
    предмет приручения и его монстр в TAME_DIST клетках, охота, HP ≥ SAFE_HP — pet_tame (не чаще TAME_GAP,
    не больше MAX_TRIES в сутки).
Факты: pet_tame_result, pet_hatched, pet_fed — только от тела (пакеты сервера); «у меня питомец» — по state.pet.
Голод (hungry ≤ HUNGRY) без корма в рюкзаке — запись в память (не чаще раза в HUNGRY_GAP).
"""
import json
import logging
import time
from pathlib import Path

log = logging.getLogger("pets")

PETS_PATH = Path(__file__).resolve().parents[1] / "world" / "pets.json"
PET_FOOD = 537
INCUBATOR = 643
FAVORITES = 3
MIN_CARE = 0.2
SAFE_HP = 60
TAME_DIST = 8
TAME_GAP = 300
MAX_TRIES = 6
HUNGRY = 20
HUNGRY_GAP = 7200
CHECK_EVERY = 15


def load(path=PETS_PATH):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return data


class Pets:
    def __init__(self, mind, cfg=None, clock=None, data=None, atlas_maps=None):
        self.mind = mind
        self.cfg = cfg or {}
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self._data = data
        self._maps = atlas_maps                       # {карта: {mob_id: число}}; None — из атласа
        self.st = mind.mem.get("pets") or {"tries": [], "last_try": 0, "hungry_note": 0, "fed": 0, "pet": None}
        self.setup_epoch = None
        self.last_check = 0.0

    # ---------- данные ----------

    def data(self):
        if self._data is None:
            try:
                self._data = load()
            except (OSError, ValueError) as e:
                log.warning("pets.json недоступен: %s", e)
                self._data = {}
        return self._data

    def map_mobs(self):
        if self._maps is None:
            try:
                from . import atlas
                self._maps = {m: d.get("monsters", {}) for m, d in atlas.default().maps.items()}
            except (OSError, ValueError, KeyError) as e:
                log.warning("атлас недоступен: %s", e)
                self._maps = {}
        return self._maps

    def save(self):
        self.mind.mem.set("pets", self.st)

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "pets", "event": kind, "text": text, **data})
        log.info("%s", text)

    def caring(self):
        needs = getattr(self.mind, "needs", None)
        t = getattr(needs, "t", None) or {}
        return t.get("generosity", 0.5) >= MIN_CARE

    # ---------- кого хочется ----------

    def favorites(self):
        """До FAVORITES питомцев: монстр на картах охоты жителя, уровень ≤ уровня жителя."""
        lv = self.mind.state.get("lv") or 1
        maps = self.map_mobs()
        here = {}
        for m in self.mind.persona.get("hunt_maps", []):
            for mid, n in (maps.get(m) or {}).items():
                here[str(mid)] = here.get(str(mid), 0) + n
        cands = [(mid, p) for mid, p in self.data().items() if mid in here and p.get("level", 99) <= lv]
        cands.sort(key=lambda kv: (kv[1]["food"] != PET_FOOD, -kv[1].get("capture", 0), -here[kv[0]], kv[0]))
        return [dict(p, id=int(mid)) for mid, p in cands[:FAVORITES]]

    def wants(self):
        """Предметы, которые житель хотел бы получить (economy.wishlist): приручение любимцев, инкубатор к яйцу."""
        if not self.cfg.get("enabled", True) or not self.caring():
            return {}
        pet = self.mind.state.get("pet") or {}
        if pet.get("has"):
            return {}
        want = {str(p["tame"]): 1 for p in self.favorites()}
        if pet.get("eggs") and not (pet.get("items") or {}).get(str(INCUBATOR)):
            want[str(INCUBATOR)] = 1
        return want

    def topic(self):
        """Повод для разговора (social): есть питомец — его имя и вид."""
        pet = self.st.get("pet")
        return f"мой питомец {pet['name']}" if pet and pet.get("name") else None

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        state = self.mind.state
        if not self.cfg.get("enabled", True) or not self.mind.fresh_state or state.get("dead"):
            return
        if now - self.last_check < CHECK_EVERY:
            return
        self.last_check = now
        pet = state.get("pet")
        if pet is None:                                   # плагин pets не загружен
            return
        if self.setup_epoch != self.mind.epoch:
            await self.setup(pet)
        if pet.get("running"):
            return
        if pet.get("has"):
            if not self.st.get("pet") and pet.get("type"):     # питомец был до этого мозга (по данным игры)
                self.st["pet"] = {"type": pet.get("type"), "name": pet.get("name"), "since": now}
                self.save()
            self.watch_hunger(pet, now)
            return
        if self.st.get("pet"):                                  # питомца больше нет (вернули в яйцо / убежал)
            self.note("pet_gone", f"Питомца {self.st['pet'].get('name')} больше нет рядом.", 2,
                      mob=self.st["pet"].get("type"))
            self.st["pet"] = None
            self.save()
        if not self.caring() or (state.get("hp_pct") or 0) < SAFE_HP:
            return
        items = {str(k): int(v or 0) for k, v in (pet.get("items") or {}).items()}
        eggs = [e for e in pet.get("eggs") or [] if any(p["egg"] == e for p in self.data().values())]
        if eggs and items.get(str(INCUBATOR)):
            await self.mind.execute([{"action": "pet_hatch", "egg": eggs[0]}], source="pets",
                                    reason="питомец: вылупить яйцо", protocol=True)
            return
        self.st["tries"] = [t for t in self.st.get("tries", []) if now - t < 86400]
        if len(self.st["tries"]) >= MAX_TRIES or now - self.st.get("last_try", 0) < TAME_GAP:
            return
        r = self.mind.routine
        if r and r.st.get("mode") != "hunt":
            return
        near = {str(k): v for k, v in (pet.get("near") or {}).items()}
        for p in self.favorites():
            if items.get(str(p["tame"])) and near.get(str(p["id"]), 99) <= TAME_DIST:
                self.st["last_try"] = now
                self.st["tries"].append(now)
                self.save()
                await self.mind.execute([{"action": "pet_tame", "item": p["tame"], "mob": p["id"]}], source="pets",
                                        reason=f"питомец: приручить {p['name']}", protocol=True)
                return

    async def setup(self, pet):
        fav = self.favorites()
        kind = (self.data().get(str(pet.get("type"))) or {}) if pet.get("has") else {}
        food_on = kind.get("food") == PET_FOOD
        self.setup_epoch = self.mind.epoch
        await self.mind.execute([{"action": "pet_setup", "food_on": food_on,
                                  "items": sorted({p["tame"] for p in fav}), "mobs": [p["id"] for p in fav]}],
                                source="pets", reason="питомец: что отслеживать", protocol=True)

    def watch_hunger(self, pet, now):
        hungry = pet.get("hungry")
        kind = self.data().get(str(pet.get("type"))) or {}
        food = kind.get("food")
        have = int(((pet.get("items") or {}).get(str(food))) or 0) if food else 0
        if (isinstance(hungry, int) and hungry <= HUNGRY and not have
                and now - self.st.get("hungry_note", 0) >= HUNGRY_GAP):
            self.st["hungry_note"] = now
            self.save()
            self.note("pet_hungry", f"Питомец {pet.get('name') or kind.get('name')} голоден, а корма "
                                    f"{kind.get('food_name', '?')} нет.", 2, food=food)

    # ---------- события тела ----------

    def on_event(self, event):
        kind = event.get("kind")
        data = self.data()
        if kind == "pet_tame_result":
            name = (data.get(str(event.get("mob"))) or {}).get("name", event.get("mob"))
            if event.get("ok"):
                self.note("pet_tamed", f"Поймал(а) {name} — у меня яйцо питомца (по данным игры).", 4,
                          mob=event.get("mob"), name=name)
            else:
                self.note("pet_tame_failed", f"Не вышло приручить {name}: {event.get('reason')}.", 1,
                          mob=event.get("mob"), reason=event.get("reason"))
        elif kind == "pet_hatched":
            if event.get("ok"):
                p = data.get(str(event.get("mob"))) or {}
                self.st["pet"] = {"type": event.get("mob"), "name": event.get("name") or p.get("name"),
                                  "since": self.clock()}
                self.setup_epoch = None                   # корм мог смениться — настроить заново
                self.save()
                self.note("pet_hatched", f"Из яйца вылупился {self.st['pet']['name']} — теперь у меня питомец.", 5,
                          mob=event.get("mob"), name=self.st["pet"]["name"])
            else:
                self.note("pet_hatch_failed", f"Яйцо не вылупилось: {event.get('reason')}.", 1,
                          egg=event.get("egg"), reason=event.get("reason"))
        elif kind == "pet_fed":
            if event.get("ok"):
                self.st["fed"] = self.st.get("fed", 0) + 1
                self.save()
                self.mind.mem.add_event("pet_fed", {"food": event.get("food")})
            else:
                self.note("pet_hungry", "Нечем покормить питомца (сервер: корма нет).", 2, food=event.get("food"))
