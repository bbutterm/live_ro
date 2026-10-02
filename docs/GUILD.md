# Гильдия жителей (ORG-052)

Статус: **код и локальные тесты, выключено** (`"guild": {"enabled": false}` в `brain/world/goals.json`). В игре не
проверено — критерий «Готово» из [`ORGANIC_BACKLOG.md`](ORGANIC_BACKLOG.md) («гильдия создана, ≥ 2 жителя в ней
по пакету сервера») открыт. С двумя активными жителями (`brain/world/roster.json`: Arkady, Vera) и `min_members: 3`
гильдия не основывается — нужен третий житель или `min_members: 2`.

Модуль — `brain/live_brain/guild.py` (класс `Guild`), правила без LLM, тик 1 с. Мост — `brainBridge.pl`
(действия `guild_*`, хуки пакетов, `state.guild`). Выключить: `BRAIN_DISABLE=guild` или `enabled: false`.

## Как сервер создаёт гильдию (upstream rAthena)

| Что | Где (upstream/rathena) |
|---|---|
| Команда клиента `0165` (CZ_REQ_MAKE_GUILD) → `guild_create`; на карте с mapflag `guildlock` и в клане — нельзя | `src/map/clif.cpp` `clif_parse_CreateGuild` |
| Уже в гильдии — ответ `0167` type 1; при `guild_emperium_check` и без Emperium (714) в рюкзаке — type 3 | `src/map/guild.cpp` `guild_create` |
| Успех — `0167` type 0, тратится один Emperium | `src/map/guild.cpp` `guild_created` |
| Имя: до 23 символов (`NAME_LENGTH` 24); буквы — по `char_name_option`/`char_name_letters`; занято — type 2 | `src/common/mmo.hpp`, `src/char/int_guild.cpp` `mapif_parse_CreateGuild`, `conf/char_athena.conf` (`char_name_option: 1`, буквы `a-z A-Z 0-9` и пробел) |
| `guild_emperium_check: yes` по умолчанию | `conf/battle/guild.conf` |
| GM-команда `@guild <имя>` создаёт без Emperium (временно ставит проверку в 0) — жителям не подходит, это GM | `src/map/atcommand.cpp` `ACMD_FUNC(guild)` |
| Приглашение: только с правом invite (мастер), цель не в гильдии, не в замке, гильдия не полна (16 без навыка) | `src/map/guild.cpp` `guild_invite` |

Поэтому имя гильдии — только латиница, цифры, пробел (подчёркивание `_` из `LR_*` групп **нельзя**).

### Где взять Emperium

| Источник | Достижимость для жителя |
|---|---|
| Guild Clerk, `prt_in 212,169` (`npc/re/merchants/Emperium_Seller.txt`, подключён в `npc/re/scripts_athena.conf`): marketshop, **1 000 000 зени**, пн–сб 18:00–23:59 по часам сервера, 100 шт. в сутки | Теоретически: нужно накопить миллион (жители держат десятки тысяч) и купить через диалог NPC + marketshop — сценария покупки в мосте нет |
| Дроп MVP: Baphomet, Golden Thief Bug, Nightmare Baphomet | Нет: жителям уровня 30–50 MVP не по силам |
| Дроп редких монстров (`db/re/mob_db.yml`, без учёта рейтов): Shining Plant 0.05 %, Angeling 0.2 %, Ghostring 0.15 %, Orc Zombie 0.01 %, Golden Savage 0.5 % | Практически нет: редкие спавны/высокий уровень |

**Честная оценка:** с правилами по умолчанию жители гильдию не основут в обозримое время. Реальный путь — выключить
проверку Emperium на сервере (решение владельца, ниже) или передать жителю Emperium вручную (GM `@item 714`) для
проверки в лаборатории.

### Emperium и решение владельца

Готовый, **не включённый** файл: [`server/conf/optional/guild_no_emperium.txt`](../server/conf/optional/guild_no_emperium.txt)
(`guild_emperium_check: no`). Внутри — зачем, что делает, как включить/выключить. Коротко:
1. рендер: скопировать строку в новый `server/conf/import-tmpl/battle_conf.txt` (рендерятся все `*.txt`), commit,
   доставка, `scripts/lab start`; existing: дописать в `$RATHENA/conf/import/battle_conf.txt`; перезапуск map-server;
2. `brain/world/goals.json`: `"guild": {"enabled": true, "emperium_check": false}`.

Последствие: без Emperium гильдию может основать **любой** игрок сервера, не только жители. БД не меняется.

## OpenKore (upstream/openkore)

| Что | Где |
|---|---|
| `guild create <имя>` (пакет 0165), `guild request <игрок>` (0168 по ID — игрок должен быть **виден**, `Match::player`), `guild join 1/0`, `guild leave` | `src/Commands.pm` `cmdGuild` |
| `g <текст>` — чат гильдии | `src/Commands.pm` (`['g', ..., \&cmdChat]`) |
| `016A` приглашение → `%incomingGuild`; `guildAutoDeny 1` (профили) отказывает через `ai_guildAutoDeny` 3 с | `src/Network/Receive.pm` `guild_request`, `src/AI/CoreLogic.pm` |
| `016C` → `$char->{guild}{name}`, `$char->{guildID}` и запрос состава | `Receive.pm` `guild_name` |
| `0A84` (наш PACKETVER) → `%guild` без имени мастера, только `master_char_id`; `0AA5` → `$guild{member}` без имён, OpenKore дозапрашивает имена | `Receive.pm` `guild_info`, `guild_members_list`, `character_name`; rAthena `clif_guild_basicinfo`/`clif_guild_memberlist` |
| Хуки: каждый обработчик пакета вызывает `packet/<имя>` (`guild_request`, `guild_create_result`, `guild_invite_result`); чат — `packet_guildMsg {MsgUser, Msg}` | `src/Network/PacketParser.pm`, `Receive/ServerType0.pm` `guild_chat` |

## Мост (brainBridge.pl, блоки `# guild:`)

| Действие | Проверка моста | Команда OpenKore |
|---|---|---|
| `guild_create {name}` | имя `^[A-Za-z0-9 ]{1,23}$`, не в гильдии | `guild create <name>` |
| `guild_invite {to}` | житель (`residents`), я в гильдии, житель виден | `guild request <to>` |
| `guild_say {text}` | в гильдии, текст очищен | `g <text>` |
| `guild_expect {name}` | имя как выше, не в гильдии | без команды: 300 с ждать приглашения гильдии `name` |

Хук `packet/guild_request`: имя гильдии ожидается (`guild_expect`) — сразу `guild join 1` (иначе `guildAutoDeny`
откажет через 3 с) и событие `guild_joined_auto`; иначе — событие `guild_invite`, решает OpenKore.
В state: `guild {name, master, members [{name, online, lv}], online}` (мастер — по `master_char_id`), `emperium`.
События: `guild_create_result {code}`, `guild_invite_result {code}`, `chat_guild {from, text}` (своё — нет).

## Мозг (guild.py)

- **Основатель** — один на мир, без переговоров: `goals.json guild.founder` или житель онлайн (друзья/группа/гильдия
  онлайн, видимые рядом) с наибольшей общительностью (`traits.sociability` персон из `roster.json`), затем уровнем,
  затем по имени. Если два жителя видят онлайн разное и оба спрашивают — уступает тот, чьё имя дальше по алфавиту.
- **Условия:** не в гильдии; гильдии жителей ещё нет (новость шины `guild_founded` или имя из приглашения);
  Emperium в рюкзаке (`state.emperium`) или `emperium_check: false`; уровень ≥ `min_level`; онлайн ≥ `min_members`
  жителей вместе со мной, к каждому отношение ≥ `min_affinity` (2) и не в ссоре (`society.quarrel`).
- **Взаимность:** основатель шепчет `[guild:ask:]`; житель отвечает `[guild:yes:]` только при своём отношении к
  основателю ≥ 2 и не в ссоре (иначе `[guild:no:]`). Набралось «да» на `min_members − 1` за 15 мин — `guild_create`.
- **Имя:** `persona.guild_name` → `goals.json guild.name` → шаблон (`Hearth of Prontera`, `Prontera Circle`,
  `<Основатель> Friends`…), очищенный под rAthena. Ответ «имя занято» (type 2) — следующий шаблон; «нет Emperium»
  (type 3) — пауза сутки.
- **Создана / вступил** — только по `state.guild` (пакеты сервера), не по ack: события памяти `guild_founded`
  (мастер — я) / `guild_joined` / `guild_left` → шина мира (`guild_founded` 4, `guild_joined` 3) и хроника.
- **Приглашение** (мастер): житель онлайн и виден, отношение ≥ 2, не в ссоре → `[guild:invite:<имя>]`; житель
  проверяет то же со своей стороны, шлёт телу `guild_expect` и отвечает `[guild:ready:]` → `guild_invite`.
- **Чат гильдии** (`guild_say`): приветствие раз в 20 ч, «кто в городе?» (в городе, раз в 30 мин шанс 0.3, тема —
  раз в 6 ч), ответ «я в Prontera» на этот вопрос, итоги дня вечером (21–24, по `kill` за сутки), приветствие нового
  члена от мастера. Не чаще 10 мин, не больше 8 в сутки, ночью (1–7) молчит. Свои фразы — `persona.phrases.guild_<тема>`.

Safety (`safety.py`, `check_guild`): все четыре действия — только `protocol` (исполнитель правил), модель их не
получает; имя по правилам rAthena; создать/ждать — не в гильдии; звать — только жителя и только в гильдии.

Настройки `goals.json` → `guild`: `enabled` false, `emperium_check` true, `min_members` 3, `min_affinity` 2,
`founder` null, `name` null, `min_level` 1.

## Тесты

- `brain/tests/test_guild.py` (24): основатель, условия (Emperium, отношения, ссора, уже есть гильдия), согласие и
  создание, уступка, ответы жителя, рукопожатие приглашения, создание/вступление/выход по state, результаты
  создания, имена под rAthena, редкость чата, ответ «кто в городе», ночь/итоги, выключено по умолчанию, safety.
- `bots/tests/brain_bridge.t` (блок `guild:`): команды, отказ без гильдии/чужому/невидимому, принятие только
  ожидаемой гильдии, состав из `%guild` с мастером по charID, события, Emperium в рюкзаке.

## Как проверить в игре (не сделано)

1. Три жителя в Пронтере, отношения ≥ 2; включить гильдию в `goals.json` и либо файл `guild_no_emperium.txt`, либо
   выдать основателю `@item 714`.
2. `decisions.jsonl` основателя: `guild_create`; `console.log`: «Guild create successful»; в state — `guild.name`,
   событие памяти `guild_founded`.
3. У приглашённого в `console.log`: `[brainBridge] приглашение в гильдию жителей … — принимаю`; в state
   обоих — `guild.members` из ≥ 2 жителей (это и есть критерий «Готово»).
4. `scripts/lab chronicle` — строки «основал гильдию …», «вступил в гильдию …».

Риски: `guild request` находит только видимого игрока; имена членов при 0AA5 приходят отдельными пакетами — первые
секунды состав без имён (мост их пропускает); `invite_request_check`/висящее приглашение в группу у цели блокирует
приглашение (rAthena `guild_invite`); Emperium покупкой у Guild Clerk не реализован.
