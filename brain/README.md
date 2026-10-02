# Мозг ботов (live_brain)

Один лёгкий Python-процесс на бота (только стандартная библиотека Python 3.8+).
OpenKore остаётся телом: бой, ходьба, лут и отдых. Мозг решает цели и общается.

```
OpenKore + плагин brainBridge  ⇄  Unix-сокет $LAB_ROOT/run/brain/bot01.sock  ⇄  live_brain  →  OpenRouter
   (bots/plugins/brainBridge)       JSON-строки: состояние, события / действия      память SQLite
```

## Поток решения
```
событие тела ─► decision gate ─► действия по правилам ─┐
                     │                                  ├─► SafetyPolicy ─► brainBridge ─► OpenKore
                     └─► (если нужно и BRAIN_LLM включён) LLM ─┘
```
- **Decision gate** (`gate.py`) — первый слой, без LLM. Сейчас `RuleGate`:
  - приветствие при входе в игру (не чаще раза в 6 ч);
  - ответ статусом на `!status` / `!статус` в личку;
  - смерть и новый уровень — в память и повод для LLM;
  - бой и лут — только журнал.

  `JevGate` (`BRAIN_GATE=jev`, `JEV_*`) — быстрая внешняя модель. Транспорт `JEV_PROVIDER`:
  `typesafe` — нативный TypeSafe Decisions API (`typesafe.py`, закрытый выбор deepseek/ignore,
  проверка confidence); `openai` — OpenAI-совместимый chat/completions (может дать короткую реплику
  say/whisper). Сбой, таймаут или лимит `JEV_DAILY_LIMIT` (резервируется до вызова) — решают правила.
- **Несколько ботов:** `LAB_BOTS`, характер `brain/personas/<bot>.json` на каждого. Мозги знают
  друг друга как жителей: не больше `BRAIN_PEER_REPLIES_PER_HOUR` ответов другому боту в час, повод
  заговорить раз в `BRAIN_PEER_SMALLTALK` секунд (только при включённом LLM).
- **SafetyPolicy** (`safety.py`) — критическая безопасность на правилах; через неё проходит любое
  действие, и от правил, и от модели:
  - мёртвому персонажу никаких действий;
  - при HP < `BRAIN_SAFE_HP` нельзя менять карту и снимать паузу;
  - карты только из `hunt_maps`;
  - лимиты общего чата и лички;
  - пауза не дольше 10 минут, затем `resume` по правилу.

  Модель не может выполнить ничего, кроме 5 игровых действий: никаких shell-команд.
- **LLM выключен по умолчанию** (`BRAIN_LLM=off`), даже если ключ есть в env. Включается
  `BRAIN_LLM=openrouter` только после согласования модели и бюджета.

## Игровые действия
`say`, `whisper`, `set_hunt_map`, `pause`, `resume`, `party_create` (имя группы всегда `LR_<имя>`),
`party_invite`, `party_accept`, `party_leave`, `follow`, `unfollow`. Группа и следование — только
с жителями. Приглашение в группу жителя принимает правило. Лечение партнёра (Vera, `AL_HEAL`)
делает OpenKore по `partySkill` в профиле, без LLM.

## Глобальные цели и распорядок дня
`brain/world/goals.json` — общие для всех жителей цели (уровень, профессия, друзья, выживание) и распорядок:
норма охоты **4–5 часов в сутки**, сессии по 60–100 минут, между ними и после нормы — отдых в городе
(`prontera 156,185`): дойти, сесть, общаться (разговор с жителем раз в 10 минут, а не 30).
Персональные отличия — `routine` / `goals_extra` в `brain/personas/<bot>.json`.
- Исполнитель `routine.py` — правила без LLM: усталость = фактическое время на карте охоты (жив);
  состояние в SQLite (`kv routine`); каждый тик сверка настройки OpenKore с режимом.
- Модель видит распорядок и цели с прогрессом; `set_hunt_map` выбирает карту (в городе — на следующую сессию).
- Встреча важнее распорядка: пока план встречи активен, распорядок не переключает режим.
- Оператор: `scripts/lab routine BOT rest|hunt|show`.

## Исполняемые планы: встреча
`plans.py` превращает разговор в действие. Модель выбирает цель (`propose_meeting`, `accept_meeting`,
`decline_meeting`, `cancel_plan`), исполняет и проверяет — правило без LLM (тик 1 с):
```
Vera: propose  ─шёпот [meet:id:map:x:y]─►  Arkady: offer → accept (модель или правило через 45 с)
                ◄─шёпот [meet:id:ok]──────
оба: meet_point (OpenKore lockMap_x/y) → дошёл (≤4 клетки) → партнёр рядом (≤8) → completed
                ◄─шёпот [meet:id:done]──►  clear_point → обратно к охоте
```
- Статусы: `planned` → `executing` → `completed` | `failed`; план и история — в SQLite (`plans`).
- Память различает «предложил», «согласился», «дошёл», «встретился» (важность 4), «не состоялась».
- После перезапуска мозга план, созданный до старта, сверяется с игрой (срок, выставлена ли
  точка, позиция); точка выставляется заново, только если её в OpenKore нет.
- Оператор: `scripts/lab plan bot02 meet Arkady`, `plan BOT cancel`, `plan BOT show`.

## Доставка и контекст
- `ack` — команда исполнена в OpenKore. `delivery` — ответ сервера: шёпот доставлен или нет
  (не в сети / игнор / не принимает), общий чат подтверждён эхом; без ответа 15 с — `timeout`.
- Класс, пол и уровень бота и игроков рядом приходят из игры и запоминаются (`known_players`);
  модели запрещено угадывать пол и профессию.
- Лимиты: запросов в сутки (`BRAIN_DAILY_LIMIT`, `JEV_DAILY_LIMIT`), денег по `usage.cost`
  (`BRAIN_DAILY_USD_LIMIT`), размера промпта (`BRAIN_MAX_PROMPT_CHARS`).

## Что делает
- **Слушает тело:** раз в 15 с приходит состояние (HP, уровень, карта, зени); события —
  вход, смерть, новый уровень, убийство, лут, чат.
- **Помнит** в `$LAB_ROOT/state/bot01/memory.sqlite` (вне Git): события, воспоминания с важностью,
  отношения к игрокам, текущую цель и настроение. Память переживает перезапуск.
- **Думает редко:**
  - по плану раз в `BRAIN_DECIDE_INTERVAL` (300 с);
  - после смерти или нового уровня (не чаще `BRAIN_EVENT_MIN_GAP`);
  - когда ему пишут в личку или называют по имени в чате (не чаще `BRAIN_CHAT_MIN_GAP`).

  Не больше `BRAIN_DAILY_LIMIT` запросов за 24 часа.
- **Действует ограниченно:** `say`, `whisper`, `set_hunt_map` (только карты из `hunt_maps`
  характера), `pause`, `resume`. Не больше 2 действий за решение. Плагин исполняет действие
  командой OpenKore и подтверждает исполнение.
- **Без LLM** (`BRAIN_LLM=off`, нет ключа, исчерпан лимит, ошибка API — пауза 60 с) работают
  правила gate и safety, пишутся память и журнал, а бот играет по профилю OpenKore.
- **Журнал решений:** `$LAB_ROOT/state/bot01/decisions.jsonl` — строки `decision`
  (мысль, цель, действия, отклонённые действия), `ack` (что исполнено в игре), `fallback`, `llm_error`.

## Характер
`brain/personas/bot01.json`: имя (должно совпадать с именем персонажа), характер, манера речи,
цели и разрешённые карты охоты. Список `hunt_maps` проверьте под уровень персонажа.

## Настройки
В `$LAB_ROOT/secrets/live_ro.env`: `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`
(по умолчанию `deepseek/deepseek-chat`, сверить с каталогом OpenRouter), `BRAIN_*`
(см. `live_ro.env.example`). Ключ не пишется в логи и журнал.

## Команды
```sh
scripts/lab brain-check     # один короткий платный запрос; при BRAIN_LLM=off — CHECK SKIP без запроса
scripts/lab start live [BOT|all]   # мозг, затем бот (с плагином brainBridge)
scripts/lab brain-check-jev  # один запрос к JEV (только при BRAIN_GATE=jev)
scripts/lab start brain     # только мозг (бот уже запущен с этим commit)
scripts/lab stop brain      # только мозг; бот продолжает играть сам
scripts/lab status
tail -f $LAB_ROOT/logs/bot01/brain.log
tail -n 20 $LAB_ROOT/state/bot01/decisions.jsonl
```

## Тесты
```sh
cd brain && python3 -m unittest -v tests.test_brain tests.test_rules tests.test_typesafe tests.test_limits tests.test_plans tests.test_routine
```
Сквозной тест без сети: фейковый OpenRouter, настоящий процесс мозга, фейковый плагин.
Проверяет решение, исполнение, память после перезапуска, работу без ключа, `--check` и то,
что ключ не попадает в лог.
