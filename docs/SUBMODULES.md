# Работа с submodules

`upstream/rathena` и `upstream/openkore` — submodules. Родительский репозиторий
хранит **только ссылку на commit** (gitlink), не файлы.

## Главная ловушка
Правка файла внутри `upstream/rathena` и `git commit` в родителе **не сохраняют
эту правку**. В родителе фиксируется лишь gitlink (и только если внутри submodule
сделан commit). Изменения, которые не закоммичены внутри submodule или не запушены
в его remote, пропадут при следующем `git submodule update` и не попадут ни к
кому, кто клонирует репозиторий.

`python3 scripts/check.py` и `scripts/lab deploy` отказываются работать с грязным
submodule именно поэтому.

## Как менять исходники upstream: патч (предпочтительно)

```sh
cd upstream/rathena
# правки...
git diff > ../../server/patches/rathena/0001-kratkoe-opisanie.patch
git checkout -- . && git clean -fd     # вернуть submodule к закреплённой версии
cd ../..
python3 scripts/check.py
git add server/patches/rathena/0001-*.patch && git commit
```
Патчи накладываются при `deploy` в новый release. Номера задают порядок.
Если патч перестал накладываться (например, после обновления upstream), deploy остановится.

## Fork (если патчей становится много)
1. Fork upstream в GitHub, commit и **push** в fork.
2. В `.gitmodules` поменять url на fork, `git submodule sync`.
3. `cd upstream/rathena && git fetch && git checkout <commit из fork>`.
4. Обновить `PINS` в `scripts/check.py`, затем `git add upstream/rathena scripts/check.py .gitmodules`.
Перед commit родителя убедитесь, что commit submodule уже есть в remote
(`git -C upstream/rathena branch -r --contains <sha>`), иначе у других клон сломается.

## Обновление версии upstream
Обновлять можно только с проверкой совместимости OpenKore (PACKETVER, `serverType`,
`recvpackets`), см. AGENTS.md. Обновление меняет gitlink и `PINS` в одном commit,
а при смене PACKETVER ещё `server/build.conf` и `bots/*/tables/servers.txt`.

## Полезные команды
```sh
git submodule status            # '-' не инициализирован, '+' checkout != gitlink
git submodule update --init --recursive   # вернуть всё к закреплённым версиям
git diff --submodule=log        # что изменилось в gitlink
```
