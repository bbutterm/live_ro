# Callable-действия жителей Ragnarok

Это интерфейс для вызовов нейромодели/локального оператора, не панель управления. Алгоритмы выполняются доверенным Python-кодом; OpenKore выполняет нативные операции; gateway подтверждает итог по свидетельствам. Ни ACK, ни реплика «согласен», ни выбор модели не означают выполненную задачу.

## Контракт

- `packages.resident_world.actions.discover(config, status)` возвращает JSON-каталог версии 1: описание, JSON Schema аргументов, `available`, точную `reason`, способ подтверждения и политику повторов.
- Старые девять действий сохранены. Новые действия входят в `skills.MENU`, автономную `DECISION_SCHEMA`, нормализацию, `can_now`, промпт и исполнитель. Ключи аргументов выводятся из меню, а не отдельного фиксированного списка.
- Новые callable-аргументы строгие: неизвестные ключи, `bool` вместо целого, недопустимые карты/имена/диапазоны отвергаются. Старый мягкий разбор решений сохранён для старого меню. Автономная модель использует плоские поля схемы и `null` для неиспользуемых; нормализатор также принимает вложенный `args`.
- `skills.execute(name, action, args, task_id=...)` синхронен, ограничен временем. Повтор того же `task_id` читает сохранённую задачу, а не повторяет действие; другие аргументы под тем же ID дают `ACTION_ID_CONFLICT`. Повторная проверка ID производится под writer-lock.
- `skills.action_status(name, task_id)` и `skills.cancel_action(name, task_id)` работают с конкретным ID и конкретным жителем. Задачи сохраняются в `<resident>/action_tasks/<id>.json`, старый `manual_task.json` остаётся совместимым. ID: ASCII буквы/цифры/`_`/`-`, 1–100 символов.
- `active`, `cancel_requested`, `cancelled`, `unconfirmed`, `done`, `failed` — разные состояния. Cancellation request не означает, что тело уже остановилось. При незавершённом native cancellation статус остаётся `cancel_requested`; при неопределённом исходе видны native state и исход задачи.
- Экономика не получает автоматических повторов, даже после потери связи. Повтор ID не переигрывает неопределённую продажу. Отмена активной/неопределённой native покупки или продажи запрещена: `ECONOMIC_ACTION_IN_FLIGHT`.

## Алгоритмы

| Действие | Аргументы | Ограниченный алгоритм и доказательство |
|---|---|---|
| `hunt_solo` | `map`, `minutes` | Поле только `prt_fild08`, 5–30 минут и не выше лимита конфигурации. Дойти на поле → native hunt с `solo=true` → server kill witness. Явно отключает автоматическую поддержку настроенного профиля и в Python, и в Perl. |
| `support` | `player`, `map`, `minutes` | Проверить **точно настроенного** `support_target`, native party с shared XP и online партнёром, learned `AL_HEAL`, job level, режим не V1 → дойти → native follow/partySkill в bounded hunt. Gateway требует наблюдение native поддержки и собственный серверный прогресс job level. **`done` не доказывает отдельный Heal!** |
| `hunt_party` | `player`, `map`, `minutes` | Проверить native shared party → healer поддерживает своего настроенного партнёра; frontline охотится сам с `party_target`. Смена support-партнёра не допускается. Perl прекращает hunt при потере party; gateway не подтверждает результат без действующей party. Нужен kill witness либо support-progress witness. |
| `find_party` | `minutes` | 1–3 минуты. Из nearby наблюдений взять только имена из operator opt-in `party_search_consent` данного жителя, ранжировать по расстоянию/имени (не более 8 из 32 наблюдений), отправить **один** whisper лучшему кандидату, дождаться native shared party с ним и свежего собственного server-position witness. Иначе `PARTY_NOT_CONFIRMED`/ошибка свежести. Чат не создаёт party и не заменяет приглашение. |
| `sell_loot` | `{}` | Дойти до Tool Dealer `prt_in (126,76)` → одна category-sale `loot` → server picklog + согласованное уменьшение inventory. Экипировка не продаётся. |
| `sell_equipment` | `{}` | Дойти к тому же торговцу → одна category-sale `equipment` → picklog/инвентарь. Только разрешённые ненадетые вещи; защищённые, улучшенные, с картами/options, явно неопознанные не продаются. |
| `buy_potions` | `stock_goal`, `max_zeny` | Дойти к торговцу → купить только item 501 до 1–20 штук в сумке (V1 ≤8), бюджет 1–300 zeny → проверенная native цена → максимум один commit → server picklog/инвентарь. Нет предположения о цене и нет слепого повтора. |
| `travel_to` | `map`, `x`, `y`, `r` | Координаты 0–1023, radius 1–5; карта из настроенных `places` либо `prt_in`. Native route до 240 секунд, затем подтверждённая сервером позиция. Диапазон координат не гарантирует проходимость клетки. |
| `respawn` | `{}` | Только погибший персонаж, не V1: native restart → серверные login/loadmap/position + живое тело. Не создание ресурсов и не GM-операция. |
| `native_control` | `skill`, `params` | Один свободный вызов **allowlist** native навыков с собственной строгой схемой params. Не скрывает дополнительный маршрут/покупку. Сохраняет native witness и veto безопасности, не повторяется автоматически. |

У новых охот задача ограничена продолжительностью плюс 300 секунд на дорогу/проверку. У отдельных native операций timeout: hunt `duration+30`, речь 15 секунд, экономика 40 секунд, остальные 240 секунд; gateway имеет собственные ограничения/дополнительное окно доказательства. Чат-прерывания и ID-отмена проверяются между шагами и в bounded polling. Вызовы новых алгоритмов не запускают внутренний цикл LLM.

## Свободное low-level управление

`native_control.skill` разрешает только:

- `travel_to`: `{map,x,y,r}`;
- `rest`: `{hp_pct}` (80–100);
- `say`: `{text}`;
- `whisper`: `{to_name,text}`;
- `hunt`: `{map,duration,min_kills,solo}`; duration 1–900 секунд и лимит мира, min_kills 1–10, **solo только true**;
- `respawn`: `{}`;
- `buy_potions`: `{item_id:501,stock_goal,max_zeny}`;
- `sell_loot`: `{category:"loot"|"equipment"}`.

Текст — русская речь 1–80 знаков, не консоль; управляющие символы/`;;`/обратный слеш/командные префиксы запрещены. Для native say/whisper необязательный общий `say` удаляется: нет двойного сообщения или публичной утечки whisper. Прямое лечение, произвольный cast, arbitrary console, SQL, GM, выдача предметов и фиктивные результаты не доступны.

## Защита продажи

Native `protectedItem` сохраняет надетое/unsellable, стандартные защищённые зелья/крылья/камни и предметы из OpenKore-настройки `residentKeepItems` (список numeric item IDs через пробел/запятую). Для оружия/брони дополнительно сохраняются upgrade, cards, options и явно `identified=0`. Cards/ammo/pet eggs/неразрешённые типы не входят в category-sale. Проверка повторяется непосредственно перед commit, а не только при discovery. Gateway category-sale не подтверждается при продаже предметов другой категории. Legacy `town_service` также сохраняет защищённые предметы.

Оператор должен заранее занести индивидуально ценные обычные вещи в `residentKeepItems`; модель не определяет субъективную ценность вещи. Этот rollout **не меняет** live config/профили.

## CLI / русские примеры

Запускать из правильного checkout; wrapper `tools/ro` может указывать на исторический путь.

```sh
/usr/bin/python3 -m packages.resident_world.cli actions discover
/usr/bin/python3 -m packages.resident_world.cli actions discover "$R"
/usr/bin/python3 -m packages.resident_world.cli actions invoke "$R" hunt_solo '{"map":"prt_fild08","minutes":5}' solo-001
/usr/bin/python3 -m packages.resident_world.cli actions invoke "$R" support '{"player":"ИмяПартнёра","map":"prt_fild08","minutes":5}' support-001
/usr/bin/python3 -m packages.resident_world.cli actions invoke "$R" hunt_party '{"player":"ИмяПартнёра","map":"prt_fild08","minutes":5}' party-001
/usr/bin/python3 -m packages.resident_world.cli actions invoke "$R" find_party '{"minutes":2}' search-001
/usr/bin/python3 -m packages.resident_world.cli actions invoke "$R" sell_equipment '{}' sale-001
/usr/bin/python3 -m packages.resident_world.cli actions invoke "$R" buy_potions '{"stock_goal":10,"max_zeny":200}' buy-001
/usr/bin/python3 -m packages.resident_world.cli actions invoke "$R" native_control '{"skill":"travel_to","params":{"map":"prontera","x":156,"y":191,"r":3}}' route-001
/usr/bin/python3 -m packages.resident_world.cli actions status "$R" route-001
/usr/bin/python3 -m packages.resident_world.cli actions cancel "$R" route-001
```

`$R` — реально настроенное имя/ID жителя. Для поиска party оператор должен явно настроить `party_search_consent: ["ИмяИгрока"]` в записи жителя; нет списка — `NO_CONSENTING_CANDIDATES`. Это opt-in allowlist оператора, **не** выведенное из nearby присутствия согласие. Эти примеры являются документацией, не выполненными live командами.

## Честные ограничения

- `heal(player)` виден в каталоге, но недоступен с `NO_NATIVE_CAST_RESULT`: текущие gateway/plugin не предоставляют одиночный callable cast и подтверждение лечения. Native partySkill работает только через настроенный профиль; поддержка/полученный XP не доказательство конкретного Heal.
- `party_invite(player)` виден, но `NO_NATIVE_PARTY_INVITE`: нет callable invite/accept API. `find_party` наблюдает/спрашивает и ждёт реально подтверждённую party, не притворяется, что whisper означает membership. Создание/принятие party этим интерфейсом не автоматизировано.
- `follow` оставлен без ослабления доказательств. Обычный игрок без зарегистрированного независимого position-witness по-прежнему может дать `NO_TARGET_WITNESS`; client visibility не превращена в серверный факт.
- Каталог без runtime status помечает операции недоступными (`NO_TELEMETRY`); это перечень контрактов, не доказательство готового живого тела. Новая функциональность Python и Perl должна устанавливаться согласованно. Проверки здесь offline; live gameplay/реальное лечение/поиск party не испытаны.
- Архив ID задач сохраняет неопределённые исходы; автоматическая очистка/retention пока не реализована, чтобы не терять защиту от повтора экономических операций.
- Старые conversational схемы/соглашения и fencing gateway сохранены. Не производится платных запросов, перезапуска служб, изменения live state, commit/push.
