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
