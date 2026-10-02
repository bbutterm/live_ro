# Экономика жителей (ORG-030, 033, 034, 035, 037)

Правила без LLM. Мозг — `brain/live_brain/economy.py` (решения) и `brain/live_brain/prices.py` (цены),
тело — плагин `bots/plugins/economy/economy.pl` (сделка, почта, лавка), мост — `brainBridge.pl`
(действия `offer_*`, `mail_*`), проверки — `safety.py` (только исполнители правил, модель их не получает).

**Статус: проверено только на заглушках** (`brain/tests/test_economy.py`, `test_prices.py`,
`bots/tests/economy.t`). В игре (rAthena + OpenKore) не запускалось — см. «Что не проверено».

## Цены (ORG-030)

`scripts/gen_prices.py upstream/rathena > brain/world/prices.json` — из `db/re/item_db_{usable,etc,equip}.yml`:

```
{"501": {"name": "Red Potion", "buy": 10, "sell": 5, "weight": 7, "type": "Healing"}, ...}
```

- `buy` — поле Buy (нет — Sell × 2), `sell` — поле Sell (нет — Buy / 2), как в rAthena; `weight` — в единицах игры.
- В файл попадают Etc, Card, Healing, Usable, DelayConsume, Weapon, Armor с ценой, ID ≤ 20000,
  снаряжение с EquipLevelMin ≤ 99 и без костюмов. Нет Cash, яиц/брони питомцев, патронов, теневой экипировки.
  Сейчас 6415 предметов, ~0.5 МБ (лимит генератора 1.5 МБ). Ключи по возрастанию — вывод детерминирован
  (тест сверяет файл с генератором, если доступен `upstream/rathena` или `LIVE_RO_RATHENA`).

`prices.py`:

| функция | правило |
|---|---|
| `npc_sell(id, n, overcharge_lv)` | Sell × (100 + r) / 100, r = 5 + 2·lv (на 10 ур. −1) — rAthena `pc_modifysellvalue` |
| `npc_buy(id, n, discount_lv)` | Buy × (100 − r) / 100 — rAthena `pc_modifybuyvalue` |
| `value(id)` | базовая цена для жителей: Sell; у карт не меньше `card_floor` (1000z) — **правило live_ro** |
| `resident_price(id, n, greed, affinity)` | value·n × (1 + max_markup·greed) × (1 − скидка другу), не ниже npc_sell |
| `buy_limit(id, n)` | value × (1 + max_markup), но не дороже NPC (если Buy ≥ value — номинальный Buy карт не учитывается) |
| `resale_ok(id, n, price, overcharge_lv)` | NPC даст ≥ price × (1 + resale_gain) |
| `inventory_value(items)` / `valuables(items, keep)` | оценка `state.items`; ценный лот — value лота ≥ `valuable_lot` (500z) |
| `shop_items(cart, greed, overcharge)` / `shop_txt(title, items)` | товары и текст shop.txt |

`greed` — `traits.greed` (0..1) из `brain/personas/*.json`; `affinity` — отношение в памяти (только > 0
даёт скидку: 3 % за пункт, не больше 25 %). Параметры — `goals.json economy.market.prices` (по умолчанию
`prices.DEFAULTS`).

Тело отдаёт мозгу в `state.items` кроме зелий/крыльев ещё ценное рюкзака (не надетое): карты, руду из
`economy_storeIds`, оружие/броню, прочий лут (тип 3) — до 40 позиций (`economy::itemCounts`). В `state.vend`
— `overcharge`, `discount` (уровни навыков), `cart` {id: n} и `slots` (MC_VENDING + 2), если есть тележка.

## Торговля между жителями (ORG-033)

Метки в шёпоте (по образцу `[need:...]`; safety держит шёпот ≤ 78 символов, метка не обрезается):

```
[offer:<id>:<предмет>:<сколько>:<цена>]   продаю лот за цену (зени за весь лот)
[offer:<id>:ok] / [offer:<id>:no]         беру / не беру
```

**Продавец** (`market_tick`, только в режиме «отдых в городе», без плана встречи, без просьбы/передачи):
лот из `valuables` кроме share и своего списка желаний, не отклонённый за `decline_hours` (12 ч); покупатель —
житель в 8 клетках, Merchant-ветка (по `job` из state) — первым. Не чаще `offer_gap_minutes` (30),
не больше `trades_per_day` (4) сделок в сутки.

**Покупатель** берёт, если: зени после покупки ≥ `keep_zeny` (5000) и цена ≤ `max_share` (50 %) зени; и
предмет в списке желаний (`economy.market.wish`, нехватка share до keep, предметы этапа карьеры из
`progression.readiness` — `progression.json`) по цене ≤ `buy_limit` — или перепродажа NPC выгодна
(`resale_ok`, Overcharge покупателя). Иначе — `[offer:id:no]` с причиной.

**Сделка — одна, двусторонняя.** OpenKore умеет: каждая сторона кладёт своё (`deal add <инв.#> <n>` /
`deal add z <n>`, Commands.pm cmdDeal), `dealAuto 3` подтверждает после второй стороны (AI/CoreLogic.pm
processDeal), обмен на сервере атомарен (rAthena trade). Порядок:

1. покупатель отвечает ok и шлёт телу `offer_buy {from, item, amount, price}` — плагин ждёт сделку
   от продавца (`%buy`, таймаут 240 с) и при её открытии (`engaged_deal`) выполняет `deal add z <price>`;
2. продавец получает ok и шлёт `offer_sell {to, item, amount, price}` — как `give`: подойти, `deal "<имя>"`,
   положить предмет, подтвердить;
3. хук `finalized_deal` (вторая сторона подтвердила): продавец проверяет `$currentDeal{other_zeny} ≥ price`,
   покупатель — `$currentDeal{other}{<id>}{amount} ≥ amount`; не так — `deal no` до обмена;
4. `complete_deal` → `give_result {price, paid, ok}` у продавца, `buy_result {ok}` у покупателя.

**Доказательства.** «Продал» — `give_result ok` и `paid ≥ price`. «Купил» — `buy_result ok` И рост
предмета в рюкзаке за 60 с; нет роста — `trade_unverified`, не «купил». Слова и ack не доказательство.
**Честность:** если предмет ушёл, а оплата не вся (плагин этого не допускает, но мозг не доверяет) — долг
в памяти `market_debts`, отношение −1, событие `trade_debt`. Долг только записан: никто его не взыскивает
и не «обещает вернуть». Удачная сделка — отношение +1 у обоих.

## Почта RODEX (ORG-035)

Команды сверены с `upstream/openkore/src/Commands.pm` (cmdRodex) и `Network/Receive.pm`:

| шаг | команда / хук | ждём |
|---|---|---|
| открыть ящик | `rodex open` | `$rodexList` (rodex_mail_list) |
| начать письмо | `rodex write <имя>` | `$rodexWrite->{target}{char_id}` (rodex_check_player: адресат найден) |
| заполнить | `rodex settitle <4-24>`, `rodex setbody <текст>`, `rodex setzeny <n>`, `rodex add <инв.#> <n>` | `$rodexWrite->{items}->size` (rodex_add_item) |
| отправить | `rodex send` | хук `packet/rodex_write_result` {fail} |
| закрыть | `rodex close` | — |
| новые письма | хук `rodex_unread_mail` или действие `mail_check` → `rodex open` | хук `rodex_mail_list` {mails} |
| забрать | `rodex read <#>` → хук `rodex_mail` {zeny, items} → `rodex getzeny <#>` / `rodex getitems <#>` | `packet/rodex_get_zeny`, `packet/rodex_get_item` {fail} |

`<#>`: cmdRodex понимает 1-3 цифры как номер в списке (page_index), длиннее — как mail_id; короткий mail_id
плагин переводит в page_index, только если он однозначен (`economy::mailRef`). Сбор (cmdRodex send, rAthena
`misc.conf`): 2 % зени + 2500z за вложение — плагин проверяет до отправки. Текст: без управляющих символов,
`;;` → `;` (Commands::run делит строку по `;;`), заголовок ≤ 24, текст ≤ 200.

Действия мозга (safety: только жителям, protocol): `mail_send {to, title 4-24, body 1-200, zeny?, item?, amount?}`
— не больше `MAIL_PER_DAY` (5) в сутки; `mail_check {}`; `mail_take {mail_id}`. События тела: `mail_result
{to, ok, reason}`, `mail_received {mail_id, from, title, attach}` (только непрочитанные от жителей, один раз),
`mail_taken {mail_id, from, zeny, items, ok}`.

Использование:
- **Подарок на расстоянии.** Нехватка share/зени, рядом никого не видно — просьба `[need:...]` шёпотом другу
  (лучшее отношение, не чаще `remote_gap_minutes` = 120). Дарящий, если просящего не видно, отвечает ok и
  шлёт письмо (если после сбора остаётся ≥ половины zeny.keep). Получатель на `mail_received` делает `mail_take`;
  «получил» = `mail_taken ok` от этого жителя И рост запаса (ожидание до 15 мин).
- **Итог недели.** Раз в 7 дней (отсчёт с первого запуска) письмо «Итог недели» жителю с отношением ≥ `week_friend` (3):
  сделки, оборот, подарки, выручка NPC по `economy_metrics`.

## Лавка торговца (ORG-034)

Житель с MC_VENDING и тележкой (`state.vend.can`), в городе, не чаще `shop_hours` (6 ч): товары — то, что
в тележке, плюс ценные лоты рюкзака; цена штуки — `resident_price` с наценкой характера, но не ниже
NPC-продажи с его Overcharge + 1z; не больше `slots` (MC_VENDING + 2, Misc::makeShop). Действие
`offer_shop {title, items:[{id, price, amount}]}`: плагин ставит `items_control <id>` = cart_add 1, sell 0
(OpenKore сам перекладывает в тележку, AI/CoreLogic processAutoCart) и собирает `%shop` (title_line, items)
из того, что уже лежит в тележке, по ID — имя берётся у OpenKore (у снаряжения со слотами оно «Имя [N]»,
поэтому не из prices.json). Открывает лавку распорядок (`shop_open` → `openshop`, Misc::openShop/makeShop).
`prices.shop_txt()` даёт тот же список текстом shop.txt (parseShopControl) — для оператора.
Без Merchant-жителя в мире функция спит (`vend.can` = 0).

## Заказы между жителями (ORG-070) — `brain/live_brain/orders.py`

Житель, которому нужен предмет, публикует заказ в шину мира; другой житель берёт его шёпотом и выполняет обычной
сделкой рынка ORG-033 — протокол сделки не дублируется. Без LLM, без изменений OpenKore.

- **Что заказывать** — из `economy.wishlist` (`market.wish`, предметы этапа карьеры, приручение питомца): не зени, не
  запасы `share` (их просят `[need:]`), не то, что продаёт NPC (`atlas.item_shops`), с ценой в `prices.json`.
  Один открытый заказ, не чаще `post_gap_hours` (6). Награда — `prices.buy_limit(item, n)`: ровно столько экономика
  заказчика согласится заплатить за предмет из списка желаний (`offer_refuse_reason`); после неё у заказчика
  остаётся `market.keep_zeny` и тратится не больше `market.max_share` зени. Срок — `days` (2).
- **Шина**: `order {id, item, name, n, reward, until}` (важность 2 — новости жителей), `order_taken {id, by}` и
  `order_closed {id, why, by}` (1), `order_done {id, for, item, name, n, reward}` (3, летопись).
- **Шёпот**: исполнитель `[order:<id>:take]`; заказчик — арбитр: первому `[order:<id>:ok]` (поле `taken_by`),
  остальным `[order:<id>:no]`, поэтому двойного резерва нет; `[order:<id>:cancel]` — больше не нужно.
- **Исполнитель** берёт один заказ: чужой открытый (не взят, не закрыт, срок не вышел), предмет есть в рюкзаке
  (`state.items`) или он сам добывал его за `loot_days` (7) дней — события `loot` по имени (дропа монстров в атласе
  нет: `atlas.json monsters` без `drops`); награда не меньше продажи NPC; предмет не нужен ему самому; заказчик не в
  ссоре. Сначала — что есть в рюкзаке, затем дороже. Ответа нет за `answer_seconds` (120) — заказ пропускается.
- **Доставка**: предмета хватает и заказчик виден — `economy.offer_lot(заказчик, item, n, price=награда)` (не чаще
  `offer_gap_minutes`, 10) → обычные `[offer:…]`, `offer_sell`/`offer_buy`, проверки плагина economy. Взятый
  предмет `economy.for_sale` другим не предлагает (`Orders.reserved`).
- **Факты**: исполнитель — `trade_sold` с заказчиком на этот предмет и количество после взятия (economy уже проверила
  оплату ≥ цены) → `order_done` (память, шина, летопись), репутация `orders.rep.done` +1. Заказчик — `trade_bought`
  этого предмета после публикации → `order_closed` `done` (от взявшего) или `bought` (купил у другого). Предмет
  больше не нужен — `cancel` и шёпот взявшему. Срок вышел — `expired` (никто не взял) / `failed` (взял и не
  выполнил; у исполнителя `order_failed`, `rep.failed` +1; отношение не меняется — не ссора).

Настройки `goals.json → orders` (необязательно): `enabled`, `check_seconds`, `post_gap_hours`, `days`, `max_n`,
`scan_minutes`, `offer_gap_minutes`, `loot_days`, `answer_seconds`. Выключатель: `BRAIN_DISABLE=orders`; без шины мира
модуль молчит. Тесты: `brain/tests/test_orders.py`. Ограничения: доставка только встречей (оплата письмом RODEX не
атомарна — не используется); OpenKore `sellAuto` может продать NPC собранное под заказ (предмет из
`items_control.txt` с флагом продажи) — модуль это не запрещает; дроп по атласу не проверяется; в игре не проверено.

## Рыночный день (ORG-071) — `brain/live_brain/market.py`

Раз в неделю — в день недели мира из `goals.json market_day.weekdays` (по умолчанию 6: суббота календаря ORG-059,
`calendar.json` «рыночный день») — модуль `MarketDay` на один день мягчит пороги уже существующих механизмов.
Новых протоколов сделки нет: торгуют `[offer:]` ORG-033, лавка ORG-034, вывески ORG-026, заказы ORG-070.

| Что | Обычный день | Рыночный день |
|---|---|---|
| `economy.market.offer_gap_minutes` (предложить лот жителю) | 30 | × 0.5 |
| `economy.market.trades_per_day` | 4 | + 4 |
| `economy.market.shop_hours` (лавка Merchant) | 6 | 2 |
| `prices.valuable_lot` («лишнее» для продажи) | 500z | × 0.5 |
| `orders.post_gap_hours` (новый заказ) | 6 | × 0.5 |
| `society.room_sign_chance` (вывеска «Продаю/Куплю») | 0.7 | 0.9 |
| вес точки `market` в прогулке `social` | 0 | 6 (фонтан 2, остальные 1) |

Исходные значения снимаются при создании модуля и возвращаются в первый такт обычного дня. Тик модуля — 15:
после распорядка, до экономики, поэтому в этом же такте экономика уже торгует по порогам дня.

**Площадь** `market` — `prontera 155,180` («к рыночной площади у фонтана»): клетки ±2 проходимы (`db/re/map_cache.dat`),
ближайший NPC из загружаемых скриптов (`npc/re/scripts_main.conf`) — дальше 5 клеток (лавке и комнате мешает NPC
ближе `min_npc_vendchat_distance: 3`, `conf/battle/player.conf:191`), варпы `prt03`/`prt06` — дальше 20 клеток.
Модуль кладёт её в копию `social.cfg.points`; общий словарь мира не меняется.

**Сбор.** В рыночный день житель в режиме `town`, дошедший до отдыха, один раз за день начинает прогулку сразу
(`social.walk_now()`), точку выбирают обычные веса — чаще всего площадь. Запись `market_day_open`.
**Лавка.** Merchant с `vend.can`, лавка закрыта, тело у площади (≤ 3 клеток) — один раз за день сбрасывается
`econ_shop_ts`, и `economy.maybe_shop` в том же такте готовит лавку прямо на площади.
**Итог.** В первый такт после рыночного дня — событие `market_day_summary {date, deals, sold, bought, vend, zeny}`
по фактам памяти (`trade_sold`, `trade_bought`, `vend_sold` за тот день по часовому поясу мира), строка летописи
«рыночный день: N сделок (продал X, купил Y, из лавки Zz)». Сделка двух жителей видна у каждого со своей стороны.
Выключить — `goals.json market_day.enabled: false` или `BRAIN_DISABLE=market_day`; без календаря модуль не создаётся.
Не проверено в игре: сколько сделок реально прибавляется, не мешает ли толпа у площади открыть лавку
(`min_npc_vendchat_distance` касается только NPC, другие лавки не мешают).

## Заточка у кузнеца (ORG-072) — `brain/live_brain/refine.py`, плагин `refine`

**Выключено по умолчанию** (`goals.json refine.enabled: false`): путь через Refine UI на живом сервере не проверен.

**Что в скриптах rAthena.** Vestri (`npc/re/merchants/refine.txt:25`) точит только +10 и выше — для обычной заточки
он не нужен. Кузнец +0..+10 в Пронтере — **Hollgrehenn** `prt_in,63,60` (`npc/merchants/refine.txt:526`). При
`feature.refineui: on` (`conf/battle/feature.conf:85`, PACKETVER 20180620 ≥ 20161012) он после `mes`/`close2` открывает
**Refine UI** (`refineui()`): предмет и руда выбираются пакетами, сервер присылает список руды с шансом
(0AA2, `clif_refineui_info`: `chance = Rate / 100`), итог — пакет 0188 (`item_upgrade`: OpenKore сам меняет `upgrade`
предмета). Безопасный предел renewal (`db/re/refine.yml`, `Rate: 10000`; совпадает с `.@safe` в `refinemain`):

| Оружие | Предел без риска | Руда | Плата за попытку | Руда у Vurewell |
|---|---|---|---|---|
| ур. 1 | +7 | Phracon 1010 | 50z | 200z |
| ур. 2 | +6 | Emveretarcon 1011 | 200z | 1000z |
| ур. 3 / 4 | +5 / +4 | Oridecon 984 | 5000z / 20000z | не продаётся — не точим |

Руда — у **Vurewell** `prt_in,56,68` (`refine.txt:970`, функция `phramain`: меню «Phracon - 200 Zeny», ввод количества
до 500). Dietrich `prt_in,63,69` в renewal — два NPC в одной клетке (`refine.txt:1084` и `re/merchants/refine.txt:646`),
поэтому не используется. Данные — `brain/world/refine.json` (`scripts/gen_refine.py upstream/rathena`).

**Плагин `refine`** (`bots/plugins/refine/refine.pl`, действие моста `refine`, в state — `refine {running, phase,
weapon, ores}`): докупить руду (`talknpc 56 68 c r~/^Phracon/ c d<N> n`, готово — руды стало больше) → к кузнецу,
`talknpc 63 60`, ждать `$refineUI` (пакет 0AA0) → надетое оружие снять (`uneq`; OpenKore не выбирает надетый предмет в
Refine UI — «Cannot select equipped», `Commands.pm:8168`) → `refineui select <inv>` → **только если у руды шанс 100** —
`refineui refine <inv> <руда> 0` (без Blacksmith Blessing), ждать роста `upgrade` → повтор до цели; шанс < 100 —
стоп «дальше риск» без попытки → `refineui cancel`, `eq <inv>`, событие `refine_result {item, inv, from, to, done, ok,
reason}`. Тайм-ауты шагов, смерть, пропажа предмета, снижение `upgrade` — провал. Пока плагин работает, арбитр
(`lifecycle.quest_busy`) считает тело занятым — распорядок и прогулки его не уводят.

**Мозг.** Раз в 30 мин, в Пронтере на отдыхе, без плана, этапа квеста, сделки, почты, экспедиции и лавки, с прошлой
попытки ≥ 3 дней, с шансом 0.3: оружие ур. 1–2 из state, шагов — до предела, не больше 3; стоимость (плата + докупка)
≤ 25 % излишка зени сверх `economy.keep_zeny` и копилки мечты. Ритуал: эмоция «хм» и реплика в общий чат («Ну, с
богом… Несу Knife к Hollgrehenn. Только до +7, без риска.»), после подтверждения — радость («Knife теперь +7! Руки до
сих пор дрожат.»). «Заточил» — только `refine_result ok` **и** `upgrade` того же оружия в state ≥ итога за 60 с
(`refine_done`, память 3, летопись, шина мира 2); иначе `refine_unverified`; провал — `refine_failed`.
Safety: только от правил, руда 1010/1011, цель 1..10, докупка 0..20, точки — карта и координаты, из города или `prt_in`.

**Не проверено.** Всё — только на заглушках (`bots/tests/refine.t`, `brain/tests/test_refine.py`). Не проверены на
нашем сервере: открытие Refine UI после `close2` при `autoTalkCont`, отправка «talk cancel» OpenKore (нужна, чтобы
скрипт дошёл до `refineui()`), формат 0AA2 для PACKETVER 20180620 (2-байтный ID руды), `uneq`/`eq` по индексу,
ввод количества у Vurewell (`d<N>`). Броня (Elunium) и оружие ур. 3–4 не точатся: руду не продают.

## Метрики (ORG-037)

`economy.economy_metrics(memory, since)` → dict (для report; `__main__.py` не изменён):
`зени`, `сделок с жителями`, `продал жителям, z`, `купил у жителей, z`, `подарков отдал/получил`,
`подарено зени`, `писем отправил/получил`, `продажи NPC, z` (событие тела `npc_sold`: прирост зени за
автопродажу между хуками AI_sell_auto и AI_sell_auto_completed), `продажи из лавки, z` (`vend_sold`, хук
vending_item_sold), `долги жителей, z`, `оборот между жителями, z` (продажи + покупки + подаренные зени),
`доля продаж жителям` (продал жителям / (жителям + NPC + лавка)). `metrics_from_rows(rows)` — то же по
строкам событий (для хроники); `CHRONICLE_LINES` — строки хроники для trade_*/mail_*.

## Поле промпта

`рынок`: оценка рюкзака (NPC и для жителей), до 5 ценных лотов, сделок за сутки, текущая продажа/покупка,
долги. Модель торговать и писать письма не может — только видит факты.

## Ограничения и что не проверено

- Ничего не запускалось в игре. Не проверены: двусторонняя сделка с `dealAuto 3` у обеих сторон на нашем
  rAthena (порядок пакетов finalize/trade), гонка `deal no` в хуке `finalized_deal` с автоподтверждением AI
  (отмена уходит на сервер раньше trade, но не проверено), пакеты RODEX для PACKETVER 20180620
  (0A12 open_write, 0A51 check_player, 0AC2 mail_list), приход `rodex_unread_mail` онлайн
  (rAthena `mail_show_status: 0` — при входе уведомления нет, поэтому есть `mail_check`), имена в тележке.
- `card_floor`, наценки, пороги — правила live_ro, не рыночные цены; реальной цены карт в базе нет.
- Список желаний не читает `items_control.txt` профиля — только `economy.market.wish`, share и карьеру.
- `npc_sold` — разница зени за автопродажу; если в то же время пришли другие зени, они попадут в выручку.
- Подарок почтой предметом стоит 2500z сбора — дорого для зелий; ограничен `remote_gap_minutes` и
  запасом зени дарящего. Лимит писем в safety в памяти процесса (после перезапуска мозга сбрасывается).
- Если тело перезапустилось посреди сделки, мозг освобождает её по таймаутам (SELL/BUY 300 с, почта 120 с).
- Включение: `goals.json economy.market` (по умолчанию `enabled: true`, `mail_gifts: true`) — goals.json
  не менялся; выключить торговлю — `"market": {"enabled": false}`.
