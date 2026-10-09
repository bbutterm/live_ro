# QA B — техническая приёмка

Дата: 2026-10-07. Без LLM. Runtime: isolated rAthena + ordinary ResidentA (char_id 150001, group 0) + OpenKore residentBody + Unix coordinator.
**Итог: технические T1–T13 и регресс 8–18 PASS. Личный графический смотр/подпись владельца не выполнены автоматически. C/D не запускались.**

## Реальные исполнения

Основной live run `b7f73a47-3400-43f0-b0a0-a646ac4f9cc0`: `checks/b_live_acceptance.py --execute`, **exit 0**, 41 положительных проверок, 48 действий: 39 confirmed, 7 ожидаемых failed, 2 cancelled; конечных unknown — 0. Полный raw-пакет находится вне Git: `/root/ragnarok/evidence/B/acceptance-20261007T130603Z/`.

| Проверка | Фактический результат |
|---|---|
| T1 | 5/5 same-map travel ARRIVED; не ACK и не destination вместо позиции |
| T2 | 5/5 Prontera → prt_fild08 ARRIVED, обратные переходы подтверждены |
| T3 | 5/5 многокарточных переходов в Izlude ARRIVED, возвраты подтверждены |
| T4 | UNWALKABLE и NO_ROUTE, без ложного ARRIVED |
| T5 | TIMEOUT, native task отменена |
| T6 | CANCELLED; управление маршрутом прекращено |
| T7 | 2 SIGKILL координатора с 30 с отсутствия; восстановление и ARRIVED без нового запуска маршрута |
| T8 | SIGKILL тела; новый epoch и failed/BODY_RESTARTED, старое действие не стало ARRIVED |
| T9 | Две повторные отправки уже принятого travel: один переход для каждой, без удвоения |
| T10 | 3 whisper SENT, server chatlog; в независимом журнале Tester реально получены BF_B_b7f73a47_0/1/2. Offline → failed/OFFLINE |
| T11 | ESTOP ≤2 с, IDLE_SAFE, положительные стационарные серверные пробы (дополнительный тест ниже) |
| T12 | config.txt SHA256 неизменен до, после прогона и после дополнительных проверок |
| T13 | 2/2 failed/DIED в gef_fild03; настоящие server die, killer gid 110031889/110031937; не @die, не перенос SQL |

Регресс 8–18: свежий login/new epoch с bounded readiness ≤60 с (T8); schema/seq/UUID (unit + live frames); координаты (ниже); native FSM/отмена (T1–T8); режимы/безопасная остановка (T6/T11); dedup (T9); неизменяемый профиль (T12); серверные основания ARRIVED/DIED (T1–T3/T13); restart reconciliation (T7/T8); whisper (T10); ESTOP (T11).

## Проверка сверх PASS драйвера

Первоначальный свидетель не писал неподвижные координаты, поэтому отсутствие событий не принималось как доказательство неподвижности. Теперь **реальная серверная позиция записывается каждые 5 с**, даже когда не меняется; фильтр registry сохранён. Только этот metadata-NPC обновлён операторским Tester через scoped `@reloadnpcfile`, без перезапуска login/char/map, изменения монстров, инвентаря или перемещения игрока. Один неудачный @loadnpc после unload имени раскрыт: имя unload не освобождает запись файла; затем scoped reload успешно выполнен (`Script loaded.`). Обычный ResidentA GM-команд не использовал.

Дополнительный `checks/b_stationary_proof.py --execute`: **exit 0**, action `9297c7cf-71da-4e07-8ade-049fb11bdb8a`:
- ESTOP: **1.027 с**;
- 83 опроса статуса на 16.783 с (не столько уникальных кадров); позиция тела не меняется;
- 3 **свежих серверных проб** на 10.018 с, все `prontera (154,142)`;
- итог действия cancelled, режим IDLE_SAFE; тест снимает только собственный marker, чужой ESTOP не удаляет.

Координаты: **32 пары в окне ±2 с**, все delta ≤3 клетки (условие — минимум 10). Из 36 конечных пар 4 старше окна и не использованы в этом критерии. Фильтрация — по времени, не по величине ошибки. На движении отдельная диагностика дала max delta 4; это раскрыто, не выдано за точность ≤3 на всём движении. Arrival не доказан одним ACK/локальным достижением.

## Метрики и ресурсы

Телеметрия p95 **0.973 с**, witness receipt lag p95 **1.007 с**. Intentional 30-секундные outages не скрыты в trace; p95 — не максимум. Unknown на переходах встречались и разрешались; **конечных unknown 0/48**.

181 замер, **1800.015 с**. Scope: rAthena login/char/map, MariaDB и resident body; **не** полный VPS, не coordinator/Tester/Hermes.

| Процесс | peak RSS, MiB | interval CPU p95, % одного ядра |
|---|---:|---:|
| char-server | 8.18 | 0.1 |
| body | 157.59 | 14.38 |
| map-server | 193.24 | 1.9 |
| mariadbd | 21.65 | 0.4 |
| login-server | 7.61 | 0.1 |

Peak сумма RSS в измеренном scope: 385.723 MiB. Это 30-минутная ресурсная проверка B, не 6-часовая автономная жизнь C.

## Исправления и воспроизведение

- Граф порталов берётся из реально включённых common + renewal manifests rAthena, не только дефолтов OpenKore.
- Критическая геометрия: серверный Izlude 268×300 против штатного OpenKore 268×268; выход (20,98) был ошибочно blocked. Runtime fields кодируются из pinned native rAthena mapcache (native headers 8/20 bytes), ядро OpenKore не меняется.
- Изолированный tests ESTOP, корректный map-only undef, native completion перед ARRIVED, deadline vs cancel, durable idempotency и восстановление witness без игровых replays.

```sh
python3 -m unittest discover -s tests -q
# На подготовленном private стенде. Порядок caches важен: последний override.
python3 checks/b_prepare_fields.py --cache /root/ragnarok/repos/rathena/db/map_cache.dat --cache /root/ragnarok/repos/rathena/db/re/map_cache.dat --out /root/ragnarok/run/B/resident/fields --fallback /root/ragnarok/repos/openkore/fields --execute
# Body обязательно запускается с --fields=/root/ragnarok/run/B/resident/fields
python3 checks/b_live_acceptance.py --execute
python3 checks/b_stationary_proof.py --execute
python3 checks/b_resource_sample.py --out /private/resources.jsonl --seconds 1800
tools/residents status
tools/residents trace ACTION_UUID
tools/residents safe_stop
```

**Локальные тесты: 37/37 PASS**, включая native Perl mocks, journal lifecycle, epoch/replay, route/whisper/cancel/deadline, warp manifests и mapcache. GitHub Actions не подменяют эти проверки; CI-публикация проверяется отдельно. Raw SQL/frames/logs, profiles, credentials, runtime fields/бинарники остаются вне Git. Публичная безопасная сводка: `docs/evidence/B/summary.json`; доказательства читаются вместе с квалификациями этого QA.

Ограничения: один ordinary житель, группа/торговля/охота/автономный 6ч цикл не приняты; никакой LLM и никакого старого runtime. Owner graphic review остаётся отдельно pending.
