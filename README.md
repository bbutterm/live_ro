# RO Live — живые жители Ragnarok Online

Новая реализация вместо прежнего live_ro. История Git сохранена для отката.

## Цель
OpenKore исполняет ограниченные навыки, rAthena подтверждает фактические игровые события, один оркестратор ведёт журнал и планы. LLM позже выбирает допустимые варианты. ACK команды — не успех в игре.

## Текущее состояние
**Первый сквозной срез проверен, автономии пока нет.** Tester создан и вошёл; NPC-свидетель подтвердил движение, logout/login; SELECT-only reader получил эти события. Это привилегированный операторский тестер, не обычный житель. Веха A ещё не завершена. Null-session crash воспроизведён и исправлен; обычный group0 житель проверен. См. [QA_02](docs/QA_02.md). [STATUS](docs/STATUS.md), [HANDOFF](docs/HANDOFF.md).

## Исходники
- `tools/`: одноразовые скрипты для новой изолированной установки. **Не запускать повторно на существующей БД.**
- `checks/01.py`: базовая инфраструктурная проверка; не доказывает interserver auth.
- `checks/01b.py`: проверка свидетельств конкретного tester-прогона на лабораторных путях, не universal health check.
- `server/schema/`, `server/witness/`, `server/patches/`: witness v0, миграция отдельной schema, config patch и минимальный char-server null-session guard.
- `packages/witness_reader/`: bounded SELECT-only reader с SQLite inbox/атомарным курсором и ограниченными retries. Restart/recovery проверены; automatic source reset и timezone ещё требуют приёмки. См. [QA_03](docs/QA_03.md).
- `server/qa/`, `checks/03_game_event.py`: вручную загружаемый LAB-сценарий и серверные gates покупки/kill/die/loadmap обычного жителя; не startup и не автономная жизнь.
- `ops/units/`: изолированные systemd-сервисы, не включённые на загрузку ОС.
- `vendor/`: официальные pinned submodules.
- `docs/ro_residents_report/`: проектные критерии, не утверждение об их выполнении.
- [Issues](docs/TASKS.md) — backlog; сейчас один исполнитель, внешние модели не запущены. [Правила на случай параллельной работы](docs/COLLABORATION.md).

## Получение зависимостей
```sh
git clone --recurse-submodules https://github.com/bbutterm/live_ro.git
```
Закреплено: rAthena `e985006171d2eb320ee512a653f4c83aea3d81b6`, PACKETVER `20180620`, renewal; OpenKore `51de1ddfc4449ae5217f6886de702f87ca934030`.

## Ограничения воспроизводимости
Лабораторные скрипты используют `/root/ragnarok/repos/rathena`, submodules — `vendor/rathena`. Универсального инсталлятора нет. Import templates, service ACL и systemd linking выполнялись отдельно. Не запускать на другом хосте без настройки путей.

`prepare_tester.py` теперь сохраняет addTableFolders и charBlockSize155. PIN отключён runtime-only, не в исходном provision. Проверять service-account read access после атомарной замены config: root-readability не означает daemon-readability. Автозагрузка witness после map-server restart проверена по новому startup log и свежим серверным pos.

## Проверки
```sh
python3 -m unittest discover -s tests -v
```
Source tests не заменяют игровую приёмку. В Git нет credentials, БД, сырых логов, GRF и бинарников. Push не означает deploy.
