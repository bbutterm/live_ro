# Веха A — техническая приёмка PASS

Дата: 2026-10-07. Один исполнитель, без LLM/других моделей. Основание: CHECKS.md §«Веха A», а не матрица T1–T13 вехи B.

## Результаты на настоящем сервере
`python3 checks/A_acceptance.py`: **8/8 gates PASS** (1, 1б, 2–7). Это replay текущего лабораторного прогона с явно зафиксированными char_id и границами событий; не автоматический fresh-game сценарий для другого сервера.

1. rAthena login/char/map и MariaDB active, игровые слушатели loopback.
1б. Отдельный Tester в изолированном tmux `-L ro-residents`, цель `tester`; `tools/tester.sh`. Пронтера: серверный loadmap №5062 `(150,150)`, online=1 при проверке.
2. Обычный ResidentA: char_id=150001, group0; residents.yaml соответствует registry resident_a/enabled=1. Создан ранее протоколом, не INSERT персонажа.
3. Tester купил **1 Red Potion (501)** у штатного `Tool Dealer#prt1`: picklog №4, S, amount=1, prt_in. Цена этого штатного магазина **10 зени**. Тестовые 500 зени выданы GM самому Tester и не называются заработком. Покупка обычного ResidentA за 50 у QA-shop остаётся отдельным доказательством QA_03.
4. Свежий relog: logout №5073 → login №5074 в prontera. Интервал relog около 5 с — это **не** задержка доставки события. Отдельно измеренная задержка нового loadmap до SELECT-reader: **0,120 с**.
5. Нативный Poring на prt_fild08: kill №5069, a1=1002. Затем разрешённый CHECKS.md `@die` **только Tester**: die №5070, prt_fild08; возврат loadmap №5071, prontera. Обычный ResidentA ранее погиб от настоящего fixture-монстра без GM-прав. Job-level hook тоже реально сработал: №5068, уровень 2.
6. `tools/events.py` читает SELECT-only в порядке ID; реальная цепочка kill→die→loadmap. `wait_event.py`: найденное=0, отсутствующее=1, ошибка доступа/SQL timeout=2. Ограниченный follow реально выполнен, exit0; не мок-демонстрация.
7. Замер `evidence/07/resources.txt` вне Git, значения перенесены в RISKS.md §2.1а. rAthena 457,1 MiB RSS, один OpenKore 143,9 MiB; available 1225 MiB, swap used 1315 MiB. Это snapshot, не 30-минутный benchmark.

Финальный прогон: **16/16 unit/source tests PASS**. После штатного завершения QA-тестера SQLite содержит **5080/5080** исходных событий, cursor=5080; повторный запуск выдал **0** строк. Оба QA-персонажа offline, три серверных сервиса active.

## Реально доведено
- Свидетель: login/logout/kill/die/loadmap/base_level/job_level, registry filtering; `a1/a2` согласованы с DDL.
- Позиция: **5 с**, запись лишь при смене карты/сдвиге >3 клеток. 11 с неподвижности: **0 новых pos**. Проба не заменяет будущую 1-секундную телеметрию тела в B.
- Persistent loadevent на prontera/prt_fild08/izlude/iz_int01/guild_vs1. maps.txt и log_conf.txt загружены после реального map restart.
- log_zeny=1, log_chat=7 (global/whisper/party), log_commands=yes. Native purchase создал zenylog; тестовая global-фраза подтверждена chatlog. Приёмку навыка шёпота не делали: B.
- Reader SELECT-only, включая char/party и журналы; реальный DELETE WHERE id=-1 запрещён. Никаких игровых строк SQL не изменяли.
- SQLite inbox+cursor атомарны; restart/dedup/rollback/retry приняты в QA_03. Добавлен fail-closed при MAX(id)<cursor; проверено реальным SELECT с заведомо слишком высоким курсором без сброса/порчи БД.
- Source generation управляется **явно оператором** через --source-id. После TRUNCATE/restore нужна новая generation; сброс, успевший перерасти старый MAX, автоматически не обнаруживается. Это сознательная граница v0, не exactly-once consumer и не готовая веха D.
- Время: MariaDB SYSTEM/CEST, ОС Europe/Amsterdam, offset сейчас +120 мин. `ts` и SQLite сохраняют исходное DB-время; eyes дополнительно дают `ts_utc` и db_timezone. Порядок — ID, не время. При смене timezone/источника новая generation; DST-неоднозначность старых DATETIME требует отдельного контракта до D.

## Повторить / посмотреть
Приватные профили и reader.cnf не в Git. Только на лабораторном VPS:
```sh
python3 -m unittest discover -s tests -v
python3 tools/events.py --char Tester --after 5066
python3 tools/wait_event.py --char Tester --kind kill --map prt_fild08 --after 5066 --timeout 1
python3 tools/events.py --char ResidentA --follow --duration 300
```
Для новой игры нужны новые baselines; сохранённые evidence/checks не выдавать за свежие действия. `tools/tester.sh` работает только с уже запущенной своей tmux-сессией и не запускает тело сам.

## Честная граница приёмки
**Техническая A закрыта. Графический смотр владельца ещё не выполнен**: вход своим совместимым клиентом, две карты и убийства с живой лентой. Ни OpenKore, ни JSON не объявлены доказательством этого смотра. В B/C не переходили; автономия, охота-цикл, PM-навык, телеметрия, LLM не реализованы.

Первое ожидание reconnect после холодного map restart истекло через 30 с; затем настоящий вход подтверждён. Это не PASS для жёсткого reconnect deadline B/D. Не скрываем нефатальные upstream предупреждения barter-disabled, NPC-shop table parsing, plugin unload; реальный protocol/gameplay выше ими не заменён.
