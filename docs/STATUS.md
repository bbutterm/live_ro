# Статус live_ro

Обновляется разработчиком при каждом push и по каждому отчёту Hermes. Секретов здесь нет.

Колонки:
- **Код** — реализовано в репозитории (commit);
- **Локально** — проверено разработчиком без VPS (статические проверки, песочница, заглушки);
- **Hermes / VPS** — фактический результат проверки Hermes на VPS, с commit и датой.
  Ожидаемый результат здесь не пишется, пока Hermes не прислал фактический.

Обозначения: ✅ да, ❌ провалено, — не делалось, ⏳ ждёт проверки.

## Сервер и мир (сделано оператором вручную, вне scripts/lab)
Отчёт Hermes, получен 2026-10-02:
- runtime `/opt/ro-bot-lab`, пользователь `ro-lab`;
- rAthena собран с PACKETVER=20180620, без LTO; login/char/map работают на 127.0.0.1;
- БД `ro_bot_lab` (основная) и `ro_bot_lab_logs` (логи) — существующие, пересоздавать нельзя;
- OpenKore вошёл персонажем Arkady на `prt_fild08`: подтверждены убийство монстра,
  получение опыта и подбор лута;
- AI-мозг не реализован.

Этот отчёт подтверждает работу ручного стека, а **не** скриптов из репозитория.

## Компоненты репозитория

Отчёты Hermes: `docs/qa/HERMES-56bdba0.md` (PARTIAL PASS), `docs/qa/HERMES-9b8157a.md` (PARTIAL PASS), `docs/qa/HERMES-LIVE-JEV.md` (живой диалог, база c6df504 + 4feb7a8), `docs/qa/HERMES-cbeb2a9.md` (задание №9,
короткая проверка; последний отчёт — по заданиям №10–16 отчётов нет).

| Компонент | Код | Локально | Hermes / VPS |
|---|---|---|---|
| `check.py`: пины, PACKETVER, поиск секретов | ✅ e4914c1 | ✅ | ✅ 56bdba0 |
| `lab prepare` в существующем `/opt/ro-bot-lab` | ✅ 56bdba0 | ✅ | ✅ 56bdba0: создано только недостающее, существующие файлы не изменены |
| `lab doctor` | ✅ 56bdba0 | ✅ | ✅ 9b8157a: без `LC_ALL=C`, `en_US.UTF-8`, ошибок нет |
| Проверка логинов/паролей ≤ 23 ASCII | ✅ 9b8157a | ✅ ru_RU/en_US/C UTF-8 | ✅ 9b8157a |
| `lab status`: находит запущенные вручную процессы | ✅ 56bdba0 | ✅ | ✅ 56bdba0 |
| Реквизиты БД из `inter_conf.txt`, обе БД | ✅ 56bdba0 | ✅ разбор | ✅ 56bdba0: доступ к `ro_bot_lab` и `ro_bot_lab_logs` |
| `lab db-backup` | ✅ 9b8157a (`--lock-tables`, проверка архива) | ✅ песочница с MariaDB | ✅ 9b8157a: два архива, `gzip и маркер завершения OK`; восстановление не проверялось |
| `lab stop/start bot01` (серверы не трогались) | ✅ 56bdba0 | ✅ | ✅ 56bdba0 с `LC_ALL=C`; login/char/map не перезапускались |
| Корректный выход бота из игры при stop (`gracefulStop`) | ✅ v2 после 9b8157a: ждёт подтверждения сервера | ✅ заглушки | ❌ 9b8157a (v1): сигнал обработан, но повторный вход получил `still recognizes your last connection`. Причина — `prevent_logout` rAthena; v2 ⏳ |
| `TERM` для фонового запуска бота | ✅ 9b8157a | ✅ | ✅ 9b8157a: предупреждений нет |
| `servers.txt` addTableFolders | ✅ 56bdba0 | ✅ | ✅ 56bdba0: вход по профилю из Git |
| Профиль из Git: вход, бой, опыт, лут | ✅ 56bdba0 | — | ✅ 56bdba0 |
| Телепорт выключен | ✅ 56bdba0 | ✅ | ✅ 56bdba0: нет `Teleporting due to insufficient HP` |
| `lowHpGuard`: включение при HP < 40% | ✅ 56bdba0 | ✅ заглушки | ✅ 56bdba0: `[lowHpGuard] HP 7% < 40%` |
| `lowHpGuard`: запрет новых целей, восстановление до 90%, снятие защиты | ✅ 56bdba0 | ✅ заглушки | ✅ частично: в логе 56bdba0 два цикла (7%→90%, 35%→90%), между ними 0 `You are now attacking`. Длительный прогон и агрессивные монстры не проверены |
| Ожидание снятия сессии на сервере (`char.online`, только SELECT) при stop/start bot | ✅ после 9b8157a | ✅ песочница с MariaDB | ⏳ |
| Метки START/STOP в логе бота | ✅ после 9b8157a | ✅ | ⏳ |
| Память и отношения переживают перезапуск мозга | ✅ | ✅ тест | ✅ LIVE-JEV: Arkady «память: 2 воспоминаний», отношение к Vera +2 |
| Профили: ignoreAll 0, serverEncoding UTF-8 (личка и русский текст) | ✅ 4feb7a8 (Hermes) | — | ✅ LIVE-JEV |
| Подтверждение доставки сервером (delivery) | ✅ ветка `claude/brain-v2` | ✅ плагин на заглушках: эхо чата, «не в сети» | ⏳ №7 |
| Класс/пол/уровень персонажей в контексте | ✅ `claude/brain-v2` | ✅ тест промпта | ⏳ №7 |
| Группа, следование, лечение Vera (partySkill) | ✅ `claude/brain-v2` | ✅ тесты safety/gate, команды плагина | ⏳ №7 |
| Лимиты: резерв JEV, деньги по usage.cost, размер промпта | ✅ `claude/brain-v2` | ✅ тесты | ⏳ №7 |
| Исполняемый план встречи (propose → accept → движение → присутствие → память) | ✅ `claude/brain-v2` b1fc7e8 | ✅ два настоящих мозга + фейковый мир, 3/3 прогонов | ⏳ №8 |
| Сохранение плана и сверка с игрой после перезапуска мозга | ✅ | ✅ тесты: точка есть / точка потеряна | ⏳ №8 |
| Глобальные цели и распорядок дня (4-5 ч охоты, отдых в городе) | ✅ `claude/brain-routine` | ✅ сутки на поддельных часах, 9 тестов | ✅ частично cbeb2a9: `routine rest` → оба в Пронтере у точки, `routine hunt` → Arkady охотится на prt_fild08, бюджет пережил перезапуск мозга; полные сутки не проверялись |
| Боевые профили всех профессий (combatProfile) | ✅ `claude/brain-routine` | ✅ Perl-тест 29 проверок на заглушках | ✅ cbeb2a9: Swordsman и Acolyte применены по изученным навыкам; лечение в группе не доказано (`party: null`) |
| Экономика: продажа лута и покупка зелий (Tool Dealer prt_in 126,76) | ✅ `claude/brain-routine` | — (только конфиг OpenKore) | ⏳ №10 |
| Склад Kafra (карты и руда не продаются, а на склад) — плагин economy | ✅ `claude/brain-routine` | ✅ Perl-тест на заглушках | ⏳ №11 |
| Взаимопомощь: просьба зелий/зени у жителя, передача сделкой, проверка по инвентарю | ✅ | ✅ 11 тестов мозга + Perl-тест сделки (30 проверок) | ⏳ №11 |
| Сделки только с жителями (dealAuto 3 + отказ чужим) | ✅ | ✅ Perl-тест | ⏳ №11 |
| Лавка Merchant в городе (открыть/закрыть по распорядку) | ✅ | ✅ тест распорядка | — нет торговцев среди жителей |
| Фаза A: пауза без ai manual, авто-респаун из manual, восстановление после смерти до HP 80%, зелья по ID | ✅ | ✅ Perl 10 + тесты распорядка | ⏳ №12 |
| Команды оператора ждут состояния тела (AUT-006), сторож уважает stop (AUT-105), карты мира в doctor | ✅ | ✅ тесты inbox, песочница | ⏳ №12 |
| Группа LR_Arkady (приём приглашения в плагине), совместная охота, поводок, heal_confirmed по пакету | ✅ | ✅ Perl 22 + 9 тестов группы | ⏳ №13 |
| survival: прогноз урона, экстренное зелье, крыло, подъём с отдыха; эффекты по ID | ✅ | ✅ Perl 39 | ⏳ №13 |
| Состояния жителя, арбитр, переподключение, устаревшие ответы модели, лестница застревания | ✅ | ✅ тесты lifecycle/routine | ⏳ №13 |
| Разбор смерти, исключение карт, выбор карты по опыту, слухи, общий бюджет, оповещения | ✅ | ✅ тесты postmortem/maps/reliability | ⏳ №13 |
| Исправления ревизии: sit не блокирует продажу/движение, честное лечение, жители=LAB_BOTS, реплики ≤ 78 | ✅ | ✅ Perl + Python | ⏳ №14 |
| Порядок навыков 24 профессий по skill_tree rAthena (raiseSkill больше не встаёт) | ✅ | ✅ проверка порядка | ⏳ №14 |
| Сон по хронотипу (relog), мотивы и характер числами, выбор карты по опыту/смелости | ✅ | ✅ тесты routine/needs/maps | ⏳ №14 |
| Город: прогулки, разговоры без LLM, эмоции, реакции (social.py) | ✅ | ✅ 21 тест | ⏳ №14 |
| Атлас мира (304 карты, монстры, магазины, Kafra), рост мест охоты, хроника мира | ✅ | ✅ тесты atlas/chronicle | ⏳ №14 |
| Карьера: цели, сценарии Knight/Priest из скриптов, jobChange (авто выкл.) | ✅ | ✅ тесты progression/career, job_change.t | — авто выкл. |
| Застревание, серия смертей, дневник, житель рядом | ✅ | ✅ тесты на поддельных часах | ⏳ №10 |
| `up`/`down`/`report`, сторож с ограничением перезапусков | ✅ | ✅ песочница: убитый бот поднят, падающий ограничен | ⏳ №10 |
| Режим release (`deploy/activate/rollback`, сборка) | ✅ e4914c1 | ✅ песочница | — не нужен на текущем VPS |
| Мозг `brain/live_brain` (OpenRouter, память SQLite, лимит, fallback) | ✅ после d24e128 | ✅ 3 сквозных теста (фейковый OpenRouter); реальный плагин brainBridge + реальный мозг на заглушках OpenKore | ⏳ |
| Плагин `brainBridge` (события наружу, действия внутрь) | ✅ после d24e128 | ✅ на заглушках OpenKore: `c ...` и `conf lockMap` исполнены, подтверждения в журнале | ⏳ |
| `lab start live/brain`, `brain-check` | ✅ после d24e128 | ✅ песочница | ⏳ |
| LLM выключен по умолчанию (`BRAIN_LLM=off`), платные вызовы только по флагу | ✅ ветка `claude/brain-coordinator` | ✅ тест: ключ есть, флага нет — 0 запросов | ⏳ |
| Decision gate (`RuleGate`), место под JEV (не установлено) | ✅ ветка `claude/brain-coordinator` | ✅ модульные тесты | ⏳ |
| SafetyPolicy без LLM (смерть, низкий HP, карты, лимиты чата, пауза ≤ 10 мин) | ✅ ветка `claude/brain-coordinator` | ✅ модульные тесты | ⏳ |
| Первый результат без LLM: событие тела → правило → команда в игре | ✅ ветка `claude/brain-coordinator` | ✅ настоящие brainBridge + live_brain, OpenKore на заглушках | ⏳ задание №6, этап A |
| JEV — быстрый gate (TypeSafe нативно / OpenAI-совместимый) | ✅ 4feb7a8 (адаптер Hermes), JEV_PROVIDER после | ✅ тесты | ✅ LIVE-JEV: CHECK OK 0.3 с, выбор deepseek в живом диалоге |
| DeepSeek через OpenRouter (включение по флагу, лимит на бота) | ✅ | ✅ на фейковом API | ✅ LIVE-JEV: CHECK OK 1.8 с, реплики в игре |
| Два бота (bot01 Arkady, bot02 Vera), мозг на каждого | ✅ | ✅ песочница | ✅ LIVE-JEV: существующие аккаунты, оба мозга подключены |
| Общение ботов-жителей с лимитами | ✅ | ✅ тесты | ✅ LIVE-JEV: автономные реплики Arkady ↔ Vera, получение в console.log обоих |

## Мозг: модули реестра (сверено с `modules.py` и `goals.json` 2026-10-02)

Только то, что есть в коде и покрыто локальными тестами (`cd brain && python3 -m unittest discover -s tests`).
**В игре эти модули не проверялись**, пока в колонке «Hermes / VPS» выше или в `docs/qa/` нет фактического отчёта.

Ядро (`mind.py`, не выключается): `bridge.py` (сокет к плагину brainBridge), `gate.py` (decision gate: правила или
JEV), `safety.py` (SafetyPolicy — через неё любое действие), `lifecycle.py` (состояние жителя и арбитр движения
survival > plan > economy > party > routine), `plans.py` (план встречи и сверка после перезапуска), `postmortem.py`
(разбор смерти, опасные карты), `maps.py` (опыт по картам, выбор карты), `needs.py` (мотивы), `memory.py`
(SQLite жителя), `llm.py` + `budget.py` (модель и общий бюджет; **LLM выключен по умолчанию**, `BRAIN_LLM=off`).

Модули реестра `brain/live_brain/modules.py` — в порядке создания. Выключатель: `BRAIN_DISABLE=<имя>[,<имя>…]` в env
и/или `"<раздел>": {"enabled": …}` в `brain/world/goals.json`. «Нужно» — без этого модуль не создаётся (например,
«другие жители»: при одном боте в `LAB_BOTS` социальные модули молчат). Что создастся при данных env и `goals.json` —
`scripts/lab modules [BOT]`; порядок включения на VPS — [`docs/ROLLOUT.md`](ROLLOUT.md).

| Файл | Назначение | Выключатель | По умолчанию | Нужно |
|---|---|---|---|---|
| `home.py` | дом и точка сохранения у Kafra, отдых распорядка в доме (ORG-014) | `BRAIN_DISABLE=home` | вкл. | мир |
| `mood.py` | настроение −1..1 из фактов 48 ч: окраска фраз, пауза разговоров (ORG-064) | `BRAIN_DISABLE=mood`; `mood.enabled` (умолч. true) | вкл. | — |
| `interests.py` | интересы жителя (ORG-103): 1–3 увлечения из каталога, веса тем хобби, приручения, экспедиции и мечты | `BRAIN_DISABLE=interests`; `interests.enabled` (умолч. true) | вкл. | — |
| `wealth.py` | относительная бедность (ORG-100, ч. 1): мотив `wealth` от копилки мечты и медианы зени мира (presence) вместо порога 50 000 | `BRAIN_DISABLE=wealth`; `wealth.enabled` (умолч. true) | вкл. | шина мира (медиана) |
| `attention.py` | бюджет внимания: суточный запас инициатив речи от общительности и дел, ответы и протокол не ограничиваются (ORG-109) | `BRAIN_DISABLE=attention`; `attention.enabled` (умолч. true) | вкл. | мир |
| `world_calendar.py` | день недели, праздники, дни рождения — множитель мотивов, темы разговора (ORG-059) | `BRAIN_DISABLE=calendar`; `calendar.enabled` (умолч. true) | вкл. | мир |
| `career.py` | цель прогрессии и этапы смены профессии (сам идёт к NPC только при `progression.auto_job_change`) | `BRAIN_DISABLE=career` | вкл. | мир |
| `routine.py` | распорядок: охота/отдых, сон по хронотипу, восстановление, застревание, лавка в городе | `BRAIN_DISABLE=routine` | вкл. | мир |
| `economy.py` | взаимопомощь зельями и зени, рынок лута между жителями, почта RODEX, лавка Merchant (ORG-033–035) | `BRAIN_DISABLE=economy` | вкл. | мир, раздел `economy` |
| `party.py` | группа `LR_<лидер>`, темп лидера, поводок, помощь в опасности | `BRAIN_DISABLE=party`; `party.enabled` (умолч. true) | вкл. | мир, другие жители |
| `activity.py` | выбор занятия по мотивам с инерцией и шумом, цепочки предусловий (ORG-016–018) | `BRAIN_DISABLE=activity` | вкл. | routine |
| `bonds.py` | встреча с продолжением, друзья (friend_request), весточки и поздравления (ORG-023–025) | `BRAIN_DISABLE=bonds` | вкл. | другие жители |
| `crew.py` | жизнь группы: карта решается вместе, чат группы, прогулка за лидером (ORG-053) | `BRAIN_DISABLE=crew` | вкл. | party |
| `pets.py` | питомец: кого приручить, приручение, вылупление, корм (ORG-051) | `BRAIN_DISABLE=pets`; `pets.enabled` (умолч. true) | вкл. | мир |
| `social.py` | город: прогулки по местам, разговоры без LLM, реакции, эмоции | `BRAIN_DISABLE=social`; `social.enabled` (умолч. true) | вкл. | мир, другие жители |
| `rumors.py` | слухи v2: автор, hops, доверие, затухание, проверка слуха (ORG-031, 032, 039) | нет | вкл. | — |
| `society.py` | эмоции на события, чат-комнаты как вывески, ссоры и примирения (ORG-022, 026, 027) | `BRAIN_DISABLE=society`; `society.enabled` (умолч. true) | вкл. | другие жители |
| `aims.py` | цели недели по фактам, усиливают мотивы (ORG-038) | `BRAIN_DISABLE=aims` | вкл. | — |
| `guild.py` | гильдия жителей: основатель, согласие, `guild create`/`request` (ORG-052) | `BRAIN_DISABLE=guild`; `guild.enabled` (умолч. false) | **выкл.** | мир, другие жители |
| `explore.py` | экспедиции на известные, не посещённые карты, группой — за лидером (ORG-054) | `BRAIN_DISABLE=explore`; `explore.enabled` (умолч. false) | вкл. (goals.json) | routine |
| `boss.py` | мини-босс группой: Vocal, Eclipse, оценка группы, поход экспедицией (ORG-079) | `BRAIN_DISABLE=boss`; `boss.enabled` (умолч. false) | **выкл.** | мир, party, crew, explorer |
| `strangers.py` | незнакомцы-люди: «знакомый в лицо», приветствие, шаблонный ответ при LLM off (ORG-063) | `BRAIN_DISABLE=strangers`; `strangers.enabled` (умолч. true) | вкл. | — |
| `world_bus.py` | шина мира `state/shared/world.sqlite`: публикация событий, новости жителей (ORG-045) | `BRAIN_DISABLE=world_bus` | вкл. | — |
| `rivalry.py` | соперник недели, счёт по фактам через шину, подначка (ORG-060) | `BRAIN_DISABLE=rivalry`; `rivalry.enabled` (умолч. true) | вкл. | другие жители |
| `crowd.py` | стигмергия: `presence` в шине, штраф людных карт и занятий (ORG-089) | `BRAIN_DISABLE=crowd`; `crowd.enabled` (умолч. true) | вкл. | — |
| `episodes.py` | «помнишь?»: эпизоды пары из памяти, тема `remember` (ORG-055) | `BRAIN_DISABLE=episodes`; `episodes.enabled` (умолч. true) | вкл. | другие жители |
| `tradition.py` | вечерний круг у фонтана, сила традиции в шине (ORG-058) | `BRAIN_DISABLE=tradition`; `tradition.enabled` (умолч. true) | вкл. | мир |
| `collection.py` | альбом карт и трофеи по `kill`/`loot`, тема `card` (ORG-074) | `BRAIN_DISABLE=collection`; `collection.enabled` (умолч. true) | вкл. | — |
| `places.py` | имена мест: словарь `place_names.json`, свои имена по фактам, консенсус в шине, коды карт в речи → имена (ORG-084) | `BRAIN_DISABLE=places`; `places.enabled` (умолч. true) | вкл. | — |
| `gossip.py` | сплетни о жителях и репутация, история пары, остывание (ORG-056, W5, W6) | `BRAIN_DISABLE=gossip`; `gossip.enabled` (умолч. true) | вкл. | другие жители |
| `habits.py` | привычки и скука по занятиям 7 суток, причуда (ORG-068) | `BRAIN_DISABLE=habits`; `habits.enabled` (умолч. true) | вкл. | — |
| `healer.py` | лекарь у собора: пост, лечение по просьбе `[heal:ask:]`, Blessing/Inc AGI (ORG-069) | `BRAIN_DISABLE=healer`; `healer.enabled` (умолч. true) | вкл. | мир |
| `orders.py` | заказы между жителями через шину, доставка сделкой рынка (ORG-070) | `BRAIN_DISABLE=orders`; `orders.enabled` (умолч. true) | вкл. | другие жители, economy |
| `market.py` | рыночный день (суббота мира): пороги рынка мягче, сбор у площади, лавка чаще (ORG-071) | `BRAIN_DISABLE=market_day`; `market_day.enabled` (умолч. true) | вкл. | мир, calendar, economy |
| `refine.py` | заточка своего оружия ур. 1–2 у Hollgrehenn до безопасного предела, плагин `refine` (ORG-072) | `BRAIN_DISABLE=refine`; `refine.enabled` (умолч. false) | **выкл.** | мир |
| `gaze.py` | взгляд на собеседника-жителя: `look_at` → `lookp` (ORG-067) | `BRAIN_DISABLE=gaze`; `gaze.enabled` (умолч. true) | вкл. | мир, другие жители, social |
| `dream.py` | мечта на месяцы, этапы по фактам, бонус в целях недели (ORG-081) | `BRAIN_DISABLE=dream`; `dream.enabled` (умолч. true) | вкл. | — |
| `savings.py` | копилка мечты; банк rAthena — флаг `savings.bank` (ORG-073) | `BRAIN_DISABLE=savings`; `savings.enabled` (умолч. true) | вкл. | dream |
| `memoir.py` | мемуары: раз в неделю `state/<bot>/memoir.md` по фактам памяти (ORG-082) | `BRAIN_DISABLE=memoir`; `memoir.enabled` (умолч. true) | вкл. | — |
| `mentor.py` | наставник новичку (< 20 ур. или недавно родился): советы, зелья, выпуск (ORG-057); без новичка спит | `BRAIN_DISABLE=mentor`; `mentor.enabled` (умолч. true) | вкл. | мир, другие жители |
| `bestiary.py` | бестиарий: счёт видов, «первый среди жителей» в шине (ORG-077) | `BRAIN_DISABLE=bestiary`; `bestiary.enabled` (умолч. true) | вкл. | — |
| `spar.py` | спарринг жителей на арене PvP Yoyo по согласию, плагин `spar` (ORG-061) | `BRAIN_DISABLE=spar`; `spar.enabled` (умолч. false) | **выкл.** | другие жители |
| `achieve.py` | достижения сервера по пакетам 0A23/0A24; награда — `achieve.claim_rewards` (умолч. false) (ORG-080) | `BRAIN_DISABLE=achieve`; `achieve.enabled` (умолч. true) | вкл. | — |
| `herbal.py` | травник: поездка к Old Pharmacist в Альберту, травы не продаются (ORG-076); нужен житель в `herbal.residents` или `persona.herbalist` | `BRAIN_DISABLE=herbal`; `herbal.enabled` (умолч. false) | **выкл.** | мир, routine |
| `arrows.py` | Arrow Crafting для ветки Archer (ORG-075); без лучника спит, квест — `arrows.quest_auto` (умолч. false) | `BRAIN_DISABLE=arrows`; `arrows.enabled` (умолч. true) | вкл. (спит) | мир, routine |
| `trek.py` | дальний поход группой в город другого региона, привалы с Kafra-сохранением (ORG-078) | `BRAIN_DISABLE=trek`; `trek.enabled` (умолч. false) | **выкл.** | мир, routine, explore, party, crew |
| `director.py` | рассказчик мира без LLM: день осторожности, «помочь», повод в тишину (ORG-086) | `BRAIN_DISABLE=director`; `director.enabled` (умолч. true) | вкл. | мир |

Выключено по умолчанию и почему (всё — решение владельца после проверки в игре):

| Что | Где | Почему выключено |
|---|---|---|
| гильдия (ORG-052) | `goals.json` `guild.enabled: false` | в игре не проверено; Emperium жителю недоступен (Guild Clerk 1 000 000 z, дроп MVP) — нужен выданный Emperium или `server/conf/optional/guild_no_emperium.txt` (docs/GUILD.md) |
| мини-босс группой (ORG-079) | `goals.json` `boss.enabled: false` | опасно для жителей, в игре не проверено; мост не умеет целиться в босса по имени — тело бьёт по attackAuto (docs/WORLD_EVENTS.md) |
| банк rAthena (ORG-073) | `goals.json` `savings.bank: false` | пакеты банка (09A6–09AB) в игре не проверены; копилка мечты работает без банка (docs/LIFE.md) |
| авто-смена профессии | `goals.json` `progression.auto_job_change: false` | сценарии выведены из скриптов rAthena, в игре не проходились; цель видна в отчёте и промпте, сменить может оператор (docs/PROGRESSION.md) |
| стартовая точка в Пронтере | `server/conf/optional/char_start_point.txt` не подключён, `progression.json` `start.override.enabled: false` | изменение сервера; без него новичок появляется в iz_int, выход в izlude есть по данным (NB-1), в игре не проверен (docs/POPULATION.md §6) |
| освоение новых мест охоты | `goals.json` `routine.auto_hunt_maps: false` | включать, когда нужные карты включены на сервере (`scripts/lab doctor`); без флага новые места только записываются и советуются |
| заточка у кузнеца (ORG-072) | `goals.json` `refine.enabled: false` | Refine UI на живом сервере не проверен; тратит зени, снимает и надевает оружие (docs/ECONOMY.md) |
| спарринг на арене (ORG-061) | `goals.json` `spar.enabled: false` | бой на PvP-карте, плата 500 z, в игре не проверено (docs/SOCIETY.md) |
| травник (ORG-076) | `goals.json` `herbal.enabled: false`, `herbal.residents: []` | дорога в Альберту (11 переходов) не проверена; травника среди жителей нет (docs/ECONOMY.md) |
| дальний поход (ORG-078) | `goals.json` `trek.enabled: false` | дальние карты и Kafra-сохранение на привалах, в игре не проверено (docs/EXPLORE.md) |
| награды достижений (ORG-080) | `goals.json` `achieve.claim_rewards: false` | решение владельца; сами достижения модуль читает (docs/SOCIETY.md) |
| квест Arrow Crafting (ORG-075) | `goals.json` `arrows.quest_auto: false` | лучника нет, пути до `moc_ruins` в таблицах OpenKore нет (docs/ECONOMY.md) |
| лавка Merchant (ORG-034) | код включён (`routine.vend_in_town: true`) | спит: среди жителей нет Merchant (`scripts/lab census`) |

Экспедиции (`explore.py`) по умолчанию класса выключены, но включены в `goals.json` (`explore.enabled: true`).

## Известные проблемы
- Бот продолжал бой при низком HP и пытался телепортироваться без навыка/предмета.
  На VPS подтверждено: телепорта нет, защита включается. Полный цикл восстановления не подтверждён.
- HP опускался до 7%: защита включается поздно или бот получает много урона в одном бою.
  Смотрим по итогам длительного наблюдения.
- Восстановление БД из бэкапа не проверялось.
- gracefulStop v2 (d24e128) Hermes ещё не проверил.
- Реальная модель через OpenRouter не вызывалась: ключа у разработчика нет. «Живое поведение» не заявляется, пока Hermes не покажет решение из `decisions.jsonl` и его исполнение в игре.
- `BOT_RUNNER=background` на VPS работает; предупреждения `TERM` устранены (9b8157a).
- Выход в бою: rAthena не даёт выйти в течение 10 с после боя (`prevent_logout`). Если монстр продолжает бить бота, выход без подтверждения и удержание персонажа сервером до 10 с остаются возможными.
