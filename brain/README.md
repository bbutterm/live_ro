# Мозг ботов (live_brain)

Один лёгкий Python-процесс на бота (только стандартная библиотека Python 3.8+).
OpenKore остаётся телом: бой, ходьба, лут и отдых. Мозг решает цели и общается.

```
OpenKore + плагин brainBridge  ⇄  Unix-сокет $LAB_ROOT/run/brain/bot01.sock  ⇄  live_brain  →  OpenRouter
   (bots/plugins/brainBridge)       JSON-строки: состояние, события / действия      память SQLite
```

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
- **Без LLM** (нет ключа, исчерпан лимит, ошибка API — пауза 60 с) пишет память и журнал,
  а бот играет по профилю OpenKore как раньше.
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
scripts/lab brain-check     # один короткий запрос к модели: проверка ключа и модели
scripts/lab start live      # бот01 (с плагином brainBridge) + мозг
scripts/lab start brain     # только мозг (бот уже запущен с этим commit)
scripts/lab stop brain      # только мозг; бот продолжает играть сам
scripts/lab status
tail -f $LAB_ROOT/logs/bot01/brain.log
tail -n 20 $LAB_ROOT/state/bot01/decisions.jsonl
```

## Тесты
```sh
cd brain && python3 -m unittest -v tests.test_brain
```
Сквозной тест без сети: фейковый OpenRouter, настоящий процесс мозга, фейковый плагин.
Проверяет решение, исполнение, память после перезапуска, работу без ключа, `--check` и то,
что ключ не попадает в лог.
