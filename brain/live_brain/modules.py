"""Реестр модулей мозга (W8): создание, тик, метки шёпота, события и поля промпта без правки mind.py.

Модуль объявляет о себе атрибутами КЛАССА (читаются у класса, не у экземпляра — тесты могут подменять
mind.<атрибут> шпионом или None, диспетчер каждый раз берёт текущий getattr(mind, ATTR)):

  ATTR        имя атрибута Mind: mind.<ATTR> — экземпляр или None (модуль выключен).
  FEATURE     имя для BRAIN_DISABLE (settings.feature); None — выключателя нет.
  CONFIG      ключ goals.json (world[CONFIG]) — его "enabled" и конфиг модуля.
  ENABLED     значение world[CONFIG].enabled по умолчанию: True/False; None — не проверять.
  REQUIRES    что нужно для создания: "world" (мир задан), "peers" (есть другие жители),
              "config" (world[CONFIG] непустой), либо ATTR другого модуля ("routine", "party") —
              тот должен быть создан раньше (стоять выше в MODULES).
  ARGS        как вызвать конструктор: "mind" -> cls(mind); "world" -> cls(mind, world);
              "config" -> cls(mind, world.get(CONFIG)). Особый случай — classmethod brain_create(mind, world)
              (может вернуть None).
  EARLY       True — создаётся до SafetyPolicy (дом задаёт точку отдыха); остальные — после ядра.
  CREATED     имя метода экземпляра, вызываемого сразу после создания (до следующих модулей).
  TICK_ORDER  место в тике mind.step() (число; None — модуль не тикает). Метод tick() — sync или async.
  TICK_EVERY  perf: тик не чаще раза в N секунд (None — каждый такт, 1 с). Если у экземпляра есть числовой
              next_tick (модуль сам ведёт срок: `if now < self.next_tick: return` первой строкой tick), реестр
              не зовёт tick, пока now < next_tick (часы — module.clock, иначе time.time): то же «рано», без вызова,
              и сброс next_tick = 0 работает как раньше. Иначе срок ведёт реестр: следующий вызов — через N с
              после предыдущего. Модулю, который должен реагировать на состояние тела каждую секунду, не ставить.
              Тест-страж — tests/test_perf.py (docs/PERF.md).
  TAGS        [(regex, "метод")] — метки в шёпоте ДРУГОГО ЖИТЕЛЯ; метод(sender, text). Совпавшая метка
              поглощает шёпот: дальше (gate/LLM) он не идёт. TAG_ORDER — место модуля в цепочке меток.
  ECHO        [("ATTR источника", regex, "метод", порядок)] — отклик на метку, которую обработал ДРУГОЙ
              модуль: метод(sender) после обработчика источника (например, [party:dead:] -> сочувствие).
  EVENTS      {вид события: "метод" | {"call": "метод"|None, "kind": bool, "consume": bool, "own": bool}}
              метод(event) или метод(kind, event) при "kind"; "consume" — после вызова событие поглощено
              (если модуль включён); "own" — вид принадлежит модулю: поглощается ВСЕГДА, даже если модуль
              выключен (не уходит в gate/LLM). EVENT_ORDER — место модуля среди подписчиков одного вида.
              "consume": "result" — поглощено, только если метод вернул истину (свой path у job_change_result).
  PROMPT      [("поле", "метод", порядок)] — поле промпта LLM = метод() или None, если модуль выключен.
              Порядок — общий с полями ядра (mind.CORE_PROMPT), шаг 10.

Таблица мест (снята с mind.py до реестра; тест tests/test_mind_order.py фиксирует её вызовами):
  метки:   10 plans [meet:] (явно в mind) · 20 economy [need:] [offer:] · 30 rumors [info:] · 40 explorer
           [explore:] · 50 crew [crew:] · 60 guild [guild:] · 70 party [party:] (эхо: social, crew на
           [party:dead:]) · 80 social [chat:]
  события: 10 crew · 20 social · 30 party · 40 guild · 50 pets · 60 economy
  тик:     (ядро: life, safety, plans) · 10 routine · 20 economy · 30 party · 40 career · 50 activities ·
           60 bonds · 70 social · 80 pets · 90 crew · 100 home · 110 explorer · 115 boss · 120 rumors · 125 gossip ·
           130 society · 135 healer · 140 strangers · 145 dream · 147 savings · 150 aims · 160 guild · 170 tradition ·
           180 world · 190 rivalry · 200 crowd · 205 habits · 210 episodes · 220 collection · 230 memoir · 230 director
           (25 orders; метки: [gossip:] 35, [order:] 25, [heal:] 65; промпт: habits 225, gossip 245)
Свободные числа между ними — для новых модулей (например, 85 — после pets, до crew).
Новые после таблицы: healer (ORG-069) — метка [heal:] 65, события support/chat_* 25, тик 135.  # healer:
orders (ORG-070) — метка [order:] 25, тик 25.  # orders:
mentor (ORG-057) — метка [mentor:] 55, тик 95, промпт 215.  # mentor:
bestiary (ORG-077) — событие kill 75, тик 225, промпт 235.  # bestiary:
market_day (ORG-071) — тик 15 (пороги дня до economy).  # market:
refine (ORG-072) — тик 105, событие refine_result 65 (own).  # refine:
gaze (ORG-067) — тик 75, эхо social [chat:] 20.  # look:
achieve (ORG-080) — события achievement/achievement_list/achievement_reward 80 (own), тик 227, промпт 237.  # achieve:
spar (ORG-061) — метка [spar:] 47, события spar_step/spar_result/spar_fall 85 (own; spar_fall — review4), тик 195 (без поля промпта).  # spar:
herbal (ORG-076) — событие job_change_result 15 (consume "result": поглощает только свой path herbal), тик 103,
промпт 207.  # herbal:
arrows (ORG-075) — события job_change_result 16 (consume "result", path arrows), arrowcraft_result 16 (own), тик 104,
промпт 208.  # arrows:
trek (ORG-078) — метка [trek:] 45, событие job_change_result 17 (consume "result", path trek), тик 112, промпт 212.  # trek:
wed (ORG-062) — метка [wed:] 57, тик 143, промпт 227; объявление о браке — из rumors.on_world_msg.  # wed:
legacy (ORG-083) — тик 232 (после memoir), без поля промпта; по умолчанию выключен.  # legacy:
fest (ORG-087) — тик 122, событие kill 76; объявление — из rumors.on_world_msg (on_announce).  # events2:
buying (ORG-036) — тик 27, события job_change_result 18 (consume "result", path buying), buyer_* 18 (own), промпт 145;
по умолчанию выключен.  # buying:
attention (ORG-109) — без тика, меток и событий: бюджет инициатив, строки `# attention:` в говорящих модулях.  # attention:
"""
import inspect
import re
import time

from .activity import Activities
from .aims import Aims
from .bonds import Bonds
from .boss import Boss                  # boss: ORG-079 мини-босс группой
from .career import Career
from .crew import Crew
from .crowd import Crowd
from .economy import Economy
from .episodes import Episodes
from .explore import Explorer
from .gossip import Gossip                # gossip: ORG-056
from .habits import Habits                # habits: ORG-068
from .guild import Guild
from .home import Home
from .mood import Mood
from .interests import Interests   # interests: ORG-103 увлечения жителя (без тика)
from .attention import Attention       # attention: ORG-109 бюджет внимания
from .party import Party
from .pets import Pets
from .rivalry import Rivalry
from .routine import Routine
from .rumors import Rumors
from .social import Social
from .society import Society
from .strangers import Strangers
from .tradition import Tradition
from .collection import Collection
from .director import Director          # director: ORG-086 рассказчик мира
from .healer import Healer           # healer: ORG-069 лекарь у собора
from .orders import Orders           # orders: ORG-070 заказы между жителями
from .buying import Buying                # buying: ORG-036 скупка и снаряжение торговца
from .dream import Dream             # dreams: ORG-081 жизненный путь
from .savings import Savings         # dreams: ORG-073 копилка мечты и банк
from .wealth import Wealth           # wealth: ORG-100 относительная бедность (без тика)
from .memoir import Memoir           # dreams: ORG-082 мемуары жителя
from .mentor import Mentor           # mentor: ORG-057 наставничество новичков
from .bestiary import Bestiary       # bestiary: ORG-077 бестиарий и первооткрыватели
from .places import Places           # places: ORG-084 имена мест
from .market import MarketDay        # market: ORG-071 рыночный день
from .refine import Refine           # refine: ORG-072 заточка у кузнеца
from .gaze import Gaze               # look: ORG-067 взгляд на собеседника
from .spar import Spar               # spar: ORG-061 спарринг на арене (по умолчанию выключен)
from .achieve import Achieve         # achieve: ORG-080 достижения сервера
from .herbal import Herbal           # herbal: ORG-076 травник у старого фармацевта
from .arrows import Arrows           # arrows: ORG-075 Arrow Crafting — ремесло лучника
from .trek import Trek               # trek: ORG-078 дальний поход группой
from .wed import Wed                 # wed: ORG-062 помолвка и свадьба
from .legacy import Legacy           # legacy: ORG-083 наследие и уход на покой (по умолчанию выключен)
from .fest import Fest               # events2: ORG-087 реакция на ивенты rAthena
from .world_bus import Feed
from .world_calendar import WorldCalendar

# Порядок СОЗДАНИЯ (конструкторы читают уже созданные модули: Routine — дом, Crew — группу,
# Activities/Explorer — распорядок). Новый модуль — одна строка здесь, остальное — атрибуты его класса.
MODULES = (
    Home,              # EARLY: до SafetyPolicy
    Mood,
    Interests,         # interests: ORG-103 — до хобби-модулей (они читают mind.interests при вызове)
    Attention,         # attention: ORG-109 после mood (только создание; routine/calendar читает при вызове)
    WorldCalendar,
    Career,
    Routine,
    Economy,
    Party,
    Activities,
    Bonds,
    Crew,
    Pets,
    Social,
    Rumors,
    Society,
    Aims,
    Guild,
    Explorer,
    Boss,              # boss: после party, crew, explorer (REQUIRES)
    Strangers,
    Feed,              # шина мира (world_bus.Feed): mind.world
    Rivalry,
    Crowd,
    Episodes,
    Tradition,
    Collection,        # после social: регистрирует тему card
    Places,            # places: ORG-084 после social (резолвер карт в grammar, тема place); шину читает в тике
    Gossip,            # gossip: ORG-056, после social (тема gossip) и rumors
    Habits,            # habits: ORG-068 (activity.scores, social.pick_point читают mind.habits)
    Healer,            # healer: ORG-069 (тик 135, метка [heal:] 65, события support/чат 25)
    Orders,            # orders: ORG-070 после economy и шины (тик 25, метка [order:] 25)
    Buying,            # buying: ORG-036 после economy, orders, routine (тик 27; по умолчанию выключен)
    MarketDay,         # market: ORG-071 после calendar, economy, orders, society, social (тик 15 — до economy)
    Refine,            # refine: ORG-072 (тик 105, событие refine_result 65 own; по умолчанию выключен)
    Gaze,              # look: ORG-067 после social (тик 75, эхо [chat:] 20)
    Fest,              # events2: ORG-087 после rumors, social (тема fest), routine, economy
    Wed,               # wed: ORG-062 после social (тема wed), society, episodes; до dream (мечта wedding)
    Dream,             # dreams: после social (тема dream) и collection/pets/guild (условия мечты)
    Savings,           # dreams: REQUIRES dream — цель копилки от мечты
    Wealth,            # wealth: ORG-100 после dream, savings, crowd (мотив wealth в needs.values)
    Memoir,            # dreams: мемуары раз в неделю (только чтение памяти и файл memoir.md)
    Mentor,            # mentor: ORG-057 (тик 95, метка [mentor:] 55; читает economy, party, routine, society, шину)
    Legacy,            # legacy: ORG-083 после mentor, wed, dream, memoir, economy (наследник, посылки, прощание)
    Bestiary,          # bestiary: ORG-077 после social (тема bestiary); тик 225, событие kill 75, промпт 235
    Spar,              # spar: ORG-061 после rivalry и society (соперник, ссоры); тик 195, метка [spar:] 47
    Achieve,           # achieve: ORG-080 после social (тема achieve) и rivalry; тик 227, события 80, промпт 237
    Herbal,            # herbal: ORG-076 после social (тема herbal) и routine; job_change_result path herbal
    Arrows,            # arrows: ORG-075 после social (тема arrows); спит без жителя-лучника
    Trek,              # trek: ORG-078 после explorer, party, crew (REQUIRES), social (тема trek)
    Director,          # director: после всех — читает шину (world), crowd, tradition, rumors, explorer
)


async def call(fn, *args):
    """Вызвать обработчик модуля: sync или async — одинаково."""
    result = fn(*args)
    if inspect.isawaitable(result):
        result = await result
    return result


def _sub(spec):
    if isinstance(spec, str) or spec is None:
        spec = {"call": spec}
    consume = spec.get("consume")
    consume = consume if consume == "result" else bool(consume)          # herbal: поглотить по ответу метода
    return {"call": spec.get("call"), "kind": bool(spec.get("kind")), "consume": consume,
            "own": bool(spec.get("own"))}


def _rx(pattern):
    return re.compile(pattern) if isinstance(pattern, str) else pattern


class Registry:
    def __init__(self, classes=MODULES):
        self.classes = tuple(classes)
        attrs = [cls.ATTR for cls in self.classes]
        dup = {a for a in attrs if attrs.count(a) > 1}
        if dup:
            raise ValueError(f"реестр модулей: повтор ATTR {sorted(dup)}")
        ticks, tags, events, echo, prompt = [], [], {}, {}, []
        for i, cls in enumerate(self.classes):
            attr = cls.ATTR
            if getattr(cls, "TICK_ORDER", None) is not None:
                self._need(cls, "tick")
                ticks.append((cls.TICK_ORDER, i, attr))
            for j, (pattern, meth) in enumerate(getattr(cls, "TAGS", ())):
                self._need(cls, meth)
                tags.append((cls.TAG_ORDER, i, j, _rx(pattern), attr, meth))
            for kind, spec in getattr(cls, "EVENTS", {}).items():
                sub = _sub(spec)
                if sub["call"]:
                    self._need(cls, sub["call"])
                events.setdefault(kind, []).append((cls.EVENT_ORDER, i, attr, sub))
            for source, pattern, meth, order in getattr(cls, "ECHO", ()):
                self._need(cls, meth)
                echo.setdefault(source, []).append((order, i, _rx(pattern), attr, meth))
            for key, meth, order in getattr(cls, "PROMPT", ()):
                self._need(cls, meth)
                prompt.append((order, key, attr, meth))
        self.ticks = [attr for _, _, attr in sorted(ticks)]
        self.every = {cls.ATTR: cls.TICK_EVERY for cls in self.classes            # perf: TICK_EVERY
                      if getattr(cls, "TICK_ORDER", None) is not None and getattr(cls, "TICK_EVERY", None)}
        self.tags = [(rx, attr, meth) for *_, rx, attr, meth in sorted(tags, key=lambda t: t[:3])]
        self.events = {k: [(attr, sub) for _, _, attr, sub in sorted(v, key=lambda t: t[:2])]
                       for k, v in events.items()}
        self.echo = {k: [(rx, attr, meth) for _, _, rx, attr, meth in sorted(v, key=lambda t: t[:2])]
                     for k, v in echo.items()}
        self.prompt = prompt

    @staticmethod
    def _need(cls, meth):
        if not callable(getattr(cls, meth, None)):
            raise ValueError(f"реестр модулей: у {cls.__name__} нет метода {meth}")

    # ---------- создание ----------

    @staticmethod
    def wanted(cls, mind, world):
        """Условие включения: REQUIRES, BRAIN_DISABLE (FEATURE) и goals.json → CONFIG.enabled (ENABLED)."""
        cfg_key = getattr(cls, "CONFIG", None)
        for req in getattr(cls, "REQUIRES", ()):
            if req == "world":
                ok = bool(world)
            elif req == "peers":
                ok = bool(mind.ctx.peers)
            elif req == "config":
                ok = bool((world or {}).get(cfg_key))
            else:
                ok = bool(getattr(mind, req, None))
            if not ok:
                return False
        feature = getattr(cls, "FEATURE", None)
        if feature and not mind.s.feature(feature):
            return False
        default = getattr(cls, "ENABLED", None)
        if default is not None and not ((world or {}).get(cfg_key) or {}).get("enabled", default):
            return False
        return True

    @staticmethod
    def construct(cls, mind, world):
        if hasattr(cls, "brain_create"):
            return cls.brain_create(mind, world)
        args = getattr(cls, "ARGS", "mind")
        if args == "world":
            return cls(mind, world)
        if args == "config":
            return cls(mind, (world or {}).get(cls.CONFIG))
        return cls(mind)

    def build(self, mind, world, early=False):
        """Создать модули (EARLY или остальные) в порядке MODULES; выключенный — mind.<ATTR> = None."""
        for cls in self.classes:
            if bool(getattr(cls, "EARLY", False)) != early:
                continue
            inst = self.construct(cls, mind, world) if self.wanted(cls, mind, world) else None
            setattr(mind, cls.ATTR, inst)
            if inst is not None and getattr(cls, "CREATED", None):
                getattr(inst, cls.CREATED)()

    # ---------- диспетчер ----------

    async def tick(self, mind):
        for attr in self.ticks:
            module = getattr(mind, attr, None)
            if module and self.due(mind, attr, module):
                await call(module.tick)

    def due(self, mind, attr, module):
        """perf: TICK_EVERY — пора ли звать tick (см. описание атрибута в начале файла)."""
        every = self.every.get(attr)
        if not every:
            return True
        own = getattr(module, "__dict__", {})
        now = (own.get("clock") or time.time)()
        nt = own.get("next_tick")
        if isinstance(nt, (int, float)) and not isinstance(nt, bool):
            return now >= nt
        due = mind.__dict__.setdefault("_tick_due", {})
        if now < due.get(attr, 0.0):
            return False
        due[attr] = now + every
        return True

    async def tag(self, mind, sender, text):
        """Метка в шёпоте жителя: первый включённый модуль с совпавшей меткой забирает шёпот (True)."""
        for rx, attr, meth in self.tags:
            module = getattr(mind, attr, None)
            if module and rx.search(text):
                await call(getattr(module, meth), sender, text)
                for erx, eattr, emeth in self.echo.get(attr, ()):
                    other = getattr(mind, eattr, None)
                    if other and erx.search(text):
                        await call(getattr(other, emeth), sender)
                return True
        return False

    async def event(self, mind, kind, event):
        """Подписчики вида события по порядку; True — событие поглощено (не идёт в gate/LLM)."""
        for attr, sub in self.events.get(kind, ()):
            module = getattr(mind, attr, None)
            if module and sub["call"]:
                fn = getattr(module, sub["call"])
                res = await call(fn, *((kind, event) if sub["kind"] else (event,)))
                if (bool(res) if sub["consume"] == "result" else sub["consume"]):   # herbal: "result"
                    return True
            if sub["own"]:
                return True
        return False

    def prompt_fields(self, mind):
        """[(порядок, поле, функция значения)] — поля промпта от модулей (None, если модуль выключен)."""
        def value(attr, meth):
            def get():
                module = getattr(mind, attr, None)
                return getattr(module, meth)() if module else None
            return get
        return [(order, key, value(attr, meth)) for order, key, attr, meth in self.prompt]


REGISTRY = Registry()
