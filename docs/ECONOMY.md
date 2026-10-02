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

## Травник у старого фармацевта (ORG-076) — `brain/live_brain/herbal.py`

Житель-травник (`goals.json → herbal.residents` или `persona.herbalist: true`) не продаёт травы, копит их и иногда
едет в Альберту к Old Pharmacist: тот варит зелья из трав за плату. Ремесло без Alchemist. **Выключено по умолчанию**
(`herbal.enabled: false`): дорога в Альберту в игре не проверена. ТЗ — `docs/IDEAS.md` Т-32.

**Данные** — `brain/world/crafts.json`, генератор `scripts/gen_crafts.py` (ссылки file:line, тест сверяет с upstream):

| Что | Откуда |
|---|---|
| NPC `alberta_in,16,28` | `npc/merchants/old_pharmacist.txt:27` |
| меню по тексту: «Make Potion» → «White Potion.» → «Make as many as I can.» | :40, :55, :81/:205 |
| итог «Here you go» | :130, :251 |
| свободный вес ≥ 500 (`MaxWeight - Weight < 5000`, вес ×10) | :42 |
| рецепты из КОДА: 2 травы + Empty Bottle 713 + плата (Red 3z — в тексте 2z; Orange — Red+Yellow Herb, 5z; Yellow 10z; White 20z; Blue 30z; Green 3z) | :57–139, подпрограмма `L_Making` |
| бутылки: Tool Dealer `prt_in,126,76`, 400z | `npc/re/merchants/Dealer_Update.txt:169` |
| путь `prontera 156,185` → клетка 15,28 у NPC: 11 переходов, обратно 11 | `portals.txt` профиля, подтверждённые варпами сервера (как `explore_reach.json`); внутренний варп комнаты `alberta_in 64,31 → 24,29` — по скрипту `npc/warps/cities/alberta.txt:49` (атлас переходы внутри карты не хранит) |

Клетки шагов `move` — клетки прибытия на каждой карте пути не ближе 4 клеток к входу любого перехода (иначе тело
унесёт варпом обратно).

**Как едет.** Действие `job_change {path: herbal, stage: pharmacist}` плагину jobChange (как дом у Kafra): шаги `move`
по клеткам пути (у каждого свой таймаут 900 с), `move` к NPC, `talk` с ответами по тексту (по одному на рецепт,
`ordered`), `success {text: "Here you go", map: alberta_in}`. Итог `job_change_result` с `path: herbal` забирает
модуль (реестр: `consume: "result"`) — в career он не попадает. **Верит только факту**: зелий рецепта в `state.items`
стало больше, чем перед поездкой (`herbal_brewed`); «Here you go» без зелий — провал. Возвращение — как после любого
этапа jobChange: плагин возвращает lockMap города, распорядок ведёт домой (`herbal_returned`).

**Когда.** Травник; путь есть; не чаще `gap_hours` (96); трав на ≥ `min_potions` (5) **выгодных** зелий; бутылок
хватает; зени ≥ плата + `reserve_zeny` (2000); свободный вес ≥ 500 + `weight_margin`; в городе отдыха, HP ≥ 80, не ночь,
сон не ближе 3 ч; нет плана встречи, этапа jobChange, экспедиции, сделки, лавки; арбитр разрешает `plan`.

**Выгода** зелья = цена NPC зелья − плата − бутылка − продажная цена трав (`prices.json`). По ценам renewal выгодны
только White (1200 − 20 − 400 − 120 = 660z) и Blue; Red/Orange/Yellow/Green дешевле купить в лавке — их травник не
варит. Экономия поездки — в событии `herbal_brewed {potions, saved}`, kv `herbal.saved`, теме разговора `herbal`
(«Сварил(а) у фармацевта 6 White Potion, сберёг(ла) 3960z!») и строке летописи.

**Тело (brainBridge).** `state.craft {items, kept, weight_free, skills}` — счётчики трав, бутылки и материалов стрел.
`craft_setup {keep}` — запись «не продавать, не складывать» в `%items_control` (до перезагрузки таблиц; мозг видит
это по `craft.kept` и шлёт снова); `craft_setup {bottles: N}` — блок `buyAuto Empty Bottle` профиля
(`bots/bot0*/control/config.txt`, `disabled 1`): maxAmount N, minAmount N−1, включить; затем `service` — OpenKore
после продажи докупает. После поездки — `bottles: 0`.

**Не проверено и риски.** Поездка не проходилась в игре: маршрут OpenKore может отличаться от нашего (он строит свой),
11 переходов через поля Морокка и Пайона — смерть в пути даёт провал этапа; переход между комнатами `alberta_in`
OpenKore должен найти сам (`move` в другую компоненту карты). Белые/синие травы на полях Пронтеры почти не падают —
травник наберёт их только на картах Геффена/Мьёльнира (охоту модуль не меняет). Строка профиля по ИМЕНИ в
`items_control.txt` важнее записи по ID (так ищет `Misc::items_control`) — для трав таких строк нет. Подарки/продажа
зелий жителям — следующий шаг (рынок ORG-033 продаёт их как обычный лут).

## Arrow Crafting — ремесло лучника (ORG-075) — `brain/live_brain/arrows.py`

Только для жителя ветки Archer (`state.job`: Archer, Hunter, Bard, Dancer и их высшие, детские и третьи формы;
шаблон `bots/templates/archer`, Ilsa). **Пока лучника нет, модуль спит**: такт ничего не делает и не пишет. ТЗ —
`docs/IDEAS.md` Т-33.

**Квест навыка** — Roberto `moc_ruins,118,99` (`npc/quests/skills/archer_skills.txt:18`): `JobLevel >= 30` (:37;
Hunter/Bard/Dancer — без условия), предметы (:43) 20 Resin 907, 7 Mushroom Spore 921, 41 Pointed Scale 906, 13 Trunk
1019, 1 Red Potion 501; при всех предметах меню нет, итог — «as I promised, I will teach you the skill» (:47), навык
`AC_MAKINGARROW` (:54). Данные — `crafts.json` (`scripts/gen_crafts.py`). Этап progression в kv `arrows.stage`:
`exp` (копить опыт профессии) → `items` (не хватает — список в промпте; материалы держит `craft_setup keep`, сбор —
лут) → `no_route` / `ready` → `skill`. **Пути до `moc_ruins` по таблицам OpenKore нет** (Морокк закрыт до патча
порталов): `routes.prontera.roberto = null`, стадия `no_route` записывается один раз и ждёт. С путём и
`arrows.quest_auto: true` — этап jobChange `{path: arrows, stage: roberto}` (шаги `move` по пути, `talk` без ответов,
`success {text: "as I promised", map: moc_ruins}`); навык подтверждается только `state.craft.skills.AC_MAKINGARROW`.

**Ремесло** (навык есть): не чаще `craft_minutes` (20), не в бою, не во время этапа, сделки или лавки, HP ≥ 50 %;
источник — первый из `sources` (Trunk 1019 → 40 Arrow, Jellopy 909 → 4 Arrow, Tree Root 902 → 7 Arrow; рецепты
`db/create_arrow_db.yml`), который есть в рюкзаке. Действие `arrowcraft {item}`: мост — `arrowcraft use` (навык),
сервер присылает список (пакет 01AD, хук `packet/arrowcraft_list`), мост выбирает предмет (`sendArrowCraft`), событие
`arrowcraft_result` — только «отправил». **Итог — по факту**: стрел в `state.craft.items` стало больше →
`arrows_crafted {source, arrow, n}`, тема `arrows` («Наделал(а) 40 стрел из Trunk своими руками.»), строка летописи.

**Не проверено и риски.** Не проходилось в игре: ни квест (нет пути), ни `arrowcraft use` на нашем сервере. Стрелы
OpenKore надевает сам только при настройке экипировки — модуль их лишь делает. Trunk у шаблона archer уходит на склад
(`economy_storeIds`): `craft_setup keep` перекрывает это для лучника. Подарки/продажа стрел другим лучникам и
«заказы самому себе» (ORG-070) для материалов — следующий шаг.

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
