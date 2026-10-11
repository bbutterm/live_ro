# Новый мир с нуля на чистом VPS

Документ сверен с кодом `main` по коду, а не прогоном. Пины: rAthena `e985006`, OpenKore `51de1dd`.
- Всё выполняется от root. Пути зашиты в код: `/root/ragnarok/...`, `/var/lib/ro-world`, `/opt/ro-residents/world-venv` — менять их нельзя.
- На живом сервере этот документ ещё не прогонялся. Если шаг не совпадает с реальностью:
  1. остановись на этом шаге;
  2. запиши строку `BLOCKED` в `STATUS.md`;
  3. исправь документ в своём коммите.

## Правила
- **Секреты.** Не выводить в чат или лог и не коммитить. Не делать `cat` для `server.json`, `reader.cnf`, `.env`, `control/config.txt` и `conf/import/*.txt`.
- **Одноразовые шаги:** `provision_a1.py`, `001_witness.sql`, `prepare_*.py`. Если шаг упал, сначала выясни, что уже создано, и только потом повторяй.
- **Персонажи** создаются только через протокол OpenKore, строго по одному: Tester, ResidentA, Mira, Borin. SQL пишет только аккаунты (таблица `login`) и `residents.registry`. Персонажей через SQL или GM не менять.
- **Решения и статус.** Обратимые решения — одной строкой в `docs/DECISIONS.md`. Статус — в `STATUS.md`. В конце сделать коммит.
- **Ветка `claude/stage1-reproducible-delivery`** (`scripts/lab`) не используется: там старая архитектура, и патчи `main` она не применяет.

## Решения по умолчанию (владелец может поменять)
- **Чистый лист.** Старую БД, память и дневники не переносим.
- **Аккаунты.** На новом сервере создаём 4 аккаунта штатными `prepare_*` скриптами: Tester (group 99) и трёх жителей.
- **Старт новичков** — сразу в Пронтере (`start_point: prontera,156,191`). С острова новичков жители сами не выберутся.
- **Профессии.** Все трое — Novice 1 уровня. GM-выдачи профессий и уровней нет. В персоны в `residents.yaml` дописываем «пока новичок», чтобы модель не приписывала себе умения мечника или послушника.
- **LLM:** OpenRouter + `deepseek/deepseek-v3.2`, лимит $2 в сутки. Если на сервере уже есть `.env` с ключом, его не трогать.
- **Сеть.** Фаервол и порты не трогаем. rAthena слушает только `127.0.0.1`. Владелец заходит клиентом RagexeRE 2018-06-20 через SSH-туннель на порты 6900, 6121 и 5121.
- **Аккаунт владельца** создаётся только по отдельной просьбе.

## Ф0. Требования
- Ubuntu 24.04 LTS, не меньше 4 ГБ RAM и 2 ГБ swap, от 20 ГБ диска.
- Исходящий HTTPS к github.com и к провайдеру LLM.
- Без ключа LLM мир всё равно живёт, на привычках.

## Ф1. Система
1. Часовой пояс ставится **до** установки MariaDB: apt сразу запускает сервер, а код читает DATETIME из БД как Europe/Amsterdam.
   ```
   timedatectl set-timezone Europe/Amsterdam
   localectl set-locale LANG=C.UTF-8
   ```
2. Пакеты:
   ```
   apt-get update
   apt-get install -y git build-essential make zlib1g-dev libmariadb-dev libmariadb-dev-compat mariadb-server mariadb-client perl libperl-dev libreadline-dev libncurses-dev libcurl4-openssl-dev python3 python3-venv python3-yaml tzdata ca-certificates tmux acl iproute2 openssl sqlite3 jq
   ```
3. Swap, если его нет:
   ```
   swapon --show | grep -q . || { fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab; }
   ```
4. Пользователь и каталоги:
   ```
   useradd --system --user-group --no-create-home --shell /usr/sbin/nologin ro-residents
   mkdir -p /root/ragnarok/repos /root/ragnarok/logs /root/ragnarok/run/private /root/ragnarok/run/B/resident /root/ragnarok/run/Demo
   chmod 700 /root/ragnarok/run /root/ragnarok/run/private
   install -d -m 700 /root/.hermes      # в юнитах rAthena InaccessiblePaths без '-': без каталога 226/NAMESPACE
   ```

Проверка: `date` показывает CEST или CET; `python3 -c 'import yaml'`; `command -v curl-config mariadb tmux setfacl runuser`.

## Ф2. Исходники
Нужны три отдельных настоящих каталога, не симлинки: `checks/01.py` сравнивает cwd процесса. Сабмодули `vendor/` не нужны.
```
# live_ro: если уже клонирован — обновить, иначе клонировать
[ -d /root/ragnarok/repos/live_ro/.git ] && git -C /root/ragnarok/repos/live_ro pull --ff-only origin main \
  || git clone https://github.com/bbutterm/live_ro.git /root/ragnarok/repos/live_ro
git -C /root/ragnarok/repos/live_ro checkout -B deploy/new-world origin/main
git init -q /root/ragnarok/repos/rathena
git -C /root/ragnarok/repos/rathena remote add origin https://github.com/rathena/rathena.git
git -C /root/ragnarok/repos/rathena fetch -q --depth 1 origin e985006171d2eb320ee512a653f4c83aea3d81b6
git -C /root/ragnarok/repos/rathena checkout -q --detach FETCH_HEAD
git init -q /root/ragnarok/repos/openkore
git -C /root/ragnarok/repos/openkore remote add origin https://github.com/OpenKore/openkore.git
git -C /root/ragnarok/repos/openkore fetch -q --depth 1 origin 51de1ddfc4449ae5217f6886de702f87ca934030
git -C /root/ragnarok/repos/openkore checkout -q --detach FETCH_HEAD
```
Проверка: `rev-parse HEAD` у rathena и openkore совпадает с пином. Баннеру «Git Hash» в логе rAthena не верить.

## Ф3. Сборка rAthena (renewal, PACKETVER 20180620)
```
cd /root/ragnarok/repos/rathena
git apply --check /root/ragnarok/repos/live_ro/server/patches/char-null-session.patch && git apply /root/ragnarok/repos/live_ro/server/patches/char-null-session.patch
git apply --check /root/ragnarok/repos/live_ro/server/patches/witness-autoload.patch && git apply /root/ragnarok/repos/live_ro/server/patches/witness-autoload.patch
./configure --enable-packetver=20180620 --disable-lto      # --enable-prere НЕ передавать
make -j2 login char map && make import                    # import создаёт conf/import, msg_conf/import, db/import
```
Проверка:
```
ls -l login-server char-server map-server
git apply -R --check /root/ragnarok/repos/live_ro/server/patches/char-null-session.patch && echo CHAR_PATCH_OK
grep -n residents_witness npc/scripts_custom.conf
```
После шага 5.3 обратная проверка `witness-autoload.patch` перестаёт проходить. Это нормально: его наличие проверяется через `grep`.

## Ф4. MariaDB, схемы, читатель
1. Запуск и проверка времени:
   ```
   systemctl enable --now mariadb      # если часовой пояс меняли после старта: systemctl restart mariadb
   mariadb -NBe "SELECT @@global.time_zone,@@system_time_zone,@@skip_networking,NOW(),UTC_TIMESTAMP()"
   # ожидаем: SYSTEM, CEST|CET, 0, разница 1–2 ч
   ```
2. Схемы и конфиги rAthena. Шаг создаёт `run/private/server.json` и `conf/import/{inter,login,char,map}_conf.txt` с правами 0600:
   ```
   cd /root/ragnarok/repos/live_ro && python3 tools/provision_a1.py
   ```
3. Схема свидетеля, строго после шага 2 (GRANT выдаётся пользователю `ro_residents_srv`):
   ```
   mariadb < /root/ragnarok/repos/live_ro/server/schema/001_witness.sql
   ```
4. Читатель только для SELECT, после шага 3. На `ro_residents_main.login` прав нет: там пароли открытым текстом. Имя пользователя записать в `DECISIONS.md`.
   ```
   python3 - <<'EOF'
   import secrets,subprocess,os
   pw=secrets.token_hex(20);u="'ro_residents_reader'@'127.0.0.1'"
   t=['residents.registry','residents.resident_events','ro_residents_main.`char`','ro_residents_logs.chatlog','ro_residents_logs.picklog','ro_residents_logs.zenylog','ro_residents_logs.atcommandlog']
   subprocess.run(['mariadb'],input=f"CREATE USER {u} IDENTIFIED BY '{pw}';"+''.join(f'GRANT SELECT ON {x} TO {u};' for x in t),text=True,check=True,capture_output=True)
   os.umask(0o077);open('/root/ragnarok/run/private/reader.cnf','w').write(f'[client]\nhost=127.0.0.1\nuser=ro_residents_reader\npassword={pw}\n');print('reader OK')
   EOF
   ```

Проверка:
```
/root/ragnarok/repos/live_ro/tools/sql.sh "SELECT COUNT(*) FROM residents.resident_events"     # 0
mariadb --defaults-extra-file=/root/ragnarok/run/private/reader.cnf -e "SELECT 1 FROM ro_residents_main.login LIMIT 1"    # отказ
mariadb -NBe "SHOW GRANTS FOR 'ro_residents_srv'@'127.0.0.1'"   # ALL на обе БД, SELECT registry, INSERT resident_events
```

## Ф5. Конфиг rAthena сверх provision
1. PIN выключить, старт в Пронтере:
   ```
   printf 'pincode_enabled: no\nstart_point: prontera,156,191\n' >> /root/ragnarok/repos/rathena/conf/import/char_conf.txt
   ```
2. Журналы: `log_chat 7`, `log_zeny 1`, `log_commands yes`. Picklog уже включён по умолчанию.
   ```
   install -m 0600 /root/ragnarok/repos/live_ro/server/witness/log_conf.txt /root/ragnarok/repos/rathena/conf/import/log_conf.txt
   ```
3. NPC свидетеля и mapflag `loadevent`. `server/qa/03_fixture.txt` в автозагрузку **не** включать.
   ```
   install -m 0644 /root/ragnarok/repos/live_ro/server/witness/residents_witness.txt /root/ragnarok/repos/rathena/npc/custom/residents_witness.txt
   install -m 0644 /root/ragnarok/repos/live_ro/server/witness/maps.txt /root/ragnarok/repos/rathena/npc/custom/residents_maps.txt
   printf '\n// live_ro witness loadevent mapflags\nnpc: npc/custom/residents_maps.txt\n' >> /root/ragnarok/repos/rathena/npc/scripts_custom.conf
   ```
4. Права для `ro-residents`. Используем группу и 0640, а не default-ACL: `chmod 0600` обнуляет маску ACL.
   ```
   setfacl -m u:ro-residents:x /root /root/ragnarok /root/ragnarok/repos
   install -d -o ro-residents -g ro-residents -m 750 /root/ragnarok/repos/rathena/log
   chgrp ro-residents /root/ragnarok/repos/rathena/conf/import/*.txt && chmod 0640 /root/ragnarok/repos/rathena/conf/import/*.txt
   runuser -u ro-residents -- cat /root/ragnarok/repos/rathena/conf/import/inter_conf.txt >/dev/null && echo READABLE
   ```
   После любой перезаписи `conf/import` повтори `chgrp`, `chmod` и проверку.

## Ф6. Службы rAthena
В юнитах нет `[Install]` и стоит `Restart=no`. Поэтому добавляем drop-in с автоперезапуском, а автозапуск после перезагрузки даёт `Wants` в ro-world (Ф10). Основание — правило 6, записать в `DECISIONS.md`.
```
for u in login char map; do
  install -m 0644 /root/ragnarok/repos/live_ro/ops/units/ro-residents-$u.service /etc/systemd/system/
  install -d /etc/systemd/system/ro-residents-$u.service.d
  printf '[Service]\nRestart=on-failure\nRestartSec=10\n' > /etc/systemd/system/ro-residents-$u.service.d/10-restart.conf
done
systemctl daemon-reload
systemctl start ro-residents-login ro-residents-char ro-residents-map
```
Проверка:
```
systemctl is-active mariadb ro-residents-login ro-residents-char ro-residents-map     # 4 раза active
ss -ltnH | grep -E ':(3306|6900|6121|5121)\b'                                        # только 127.0.0.1
grep -a 'Using packet version' /root/ragnarok/logs/map-server.log | tail -1           # 20180620
python3 /root/ragnarok/repos/live_ro/checks/01.py                 # RESULT: PASS
python3 /root/ragnarok/repos/live_ro/checks/char_select_unauth.py # PASS, PID char-server тот же
```

## Ф7. OpenKore: XSTools, плагин, поля
XSTools собирать через scons, а не через `make`: Makefile вызывает голый `python` и прячет ошибку. Порядок кэшей важен: `re/map_cache.dat` идёт последним.
```
cd /root/ragnarok/repos/openkore && python3 src/scons-local-3.1.2/scons.py -j2
ls -l src/auto/XSTools/XSTools.so
cd /root/ragnarok/repos/live_ro && perl -I/root/ragnarok/repos/openkore/src -I/root/ragnarok/repos/openkore/src/deps -c plugins/residentBody/residentBody.pl    # syntax OK
python3 checks/b_prepare_fields.py --cache /root/ragnarok/repos/rathena/db/map_cache.dat --cache /root/ragnarok/repos/rathena/db/re/map_cache.dat --out /root/ragnarok/run/B/resident/fields --fallback /root/ragnarok/repos/openkore/fields
# ожидаем {"maps": 1296, ...}
```

## Ф8. Аккаунты и профили
SQL пишет только в таблицу `login`; персонажей на этом шаге нет.
```
cd /root/ragnarok/repos/live_ro
python3 tools/prepare_tester.py       # ro_a_tester, group 99 -> /root/ragnarok/run/tester
python3 tools/prepare_resident.py     # ro_resident_a, group 0 -> /root/ragnarok/run/resident
sed -i 's/^serverEncoding Western$/serverEncoding UTF-8/' /root/ragnarok/run/tester/tables/servers.txt
cp -a /root/ragnarok/run/resident/control /root/ragnarok/run/resident/tables /root/ragnarok/run/B/resident/
sed -i -e 's/^loadPlugins .*/loadPlugins 2/' -e 's/^loadPlugins_list .*/loadPlugins_list residentBody/' /root/ragnarok/run/B/resident/control/sys.txt   # без этого residentBody молча не грузится
install -d -m 700 /root/ragnarok/run/Demo/resident_a     # для resident_a инструмента нет: повторяем то, что prepare_demo делает для b и c
cp -a /root/ragnarok/run/B/resident/control /root/ragnarok/run/B/resident/tables /root/ragnarok/run/Demo/resident_a/
ln -s /root/ragnarok/run/B/resident/fields /root/ragnarok/run/Demo/resident_a/fields
sed -i 's/^serverEncoding Western$/serverEncoding UTF-8/' /root/ragnarok/run/Demo/resident_a/tables/servers.txt
sed -i 's/^attackAuto .*/attackAuto -1/' /root/ragnarok/run/Demo/resident_a/control/config.txt
python3 tools/prepare_demo_resident.py --resident resident_b --name Mira --sex F
python3 tools/prepare_demo_resident.py --resident resident_c --name Borin --sex M
```
Проверка без вывода паролей:
```
for r in a b c; do p=/root/ragnarok/run/Demo/resident_$r; grep -E '^(master|server|username|char) ' $p/control/config.txt; grep '^loadPlugins_list' $p/control/sys.txt; grep -E 'serverType|serverEncoding' $p/tables/servers.txt; ls -L $p/fields/prontera.fld2.gz; done
```
Ожидаем:
- `master ROResidentsLab`, `char 0`;
- `loadPlugins_list residentBody`;
- `kRO_RagexeRE_2018_06_20e`, `UTF-8`.

Здесь печатается строка `username`, но не пароль.

## Ф9. Персонажи
Создаются строго по одному, после каждого сверяем `char_id`. Серверы из Ф6 должны работать.

**1. Tester** создаётся первым и получает `char_id` 150000. В первой сессии он один раз компилирует portalsLOS по полям, это примерно 40 с.
```
tmux -L ro-residents new-session -d -s tester -c /root/ragnarok/repos/openkore "perl openkore.pl --control=/root/ragnarok/run/tester/control --tables=/root/ragnarok/run/tester/tables:tables --fields=/root/ragnarok/run/B/resident/fields --plugins=/root/ragnarok/run/tester/plugins --logs=/root/ragnarok/logs/tester"
# ждать в `tmux -L ro-residents capture-pane -pt tester` строку "Create a new character"
/root/ragnarok/repos/live_ro/tools/tester.sh '0'
/root/ragnarok/repos/live_ro/tools/tester.sh '0 "Tester" 1 1 novice M'
# дождаться "You are now in the game"; сессию tester не закрывать
mariadb -NBe "SELECT char_id,name FROM ro_residents_main.\`char\` ORDER BY char_id"    # 150000 Tester
```

**2. Жители** создаются в своих Demo-профилях, с пустыми плагинами и `--ai=manual`. Пол указывать явно: по умолчанию OpenKore ставит F.
```
mk(){ tmux -L ro-residents new-session -d -s mk_$1 -c /root/ragnarok/repos/openkore "perl openkore.pl --control=/root/ragnarok/run/Demo/$1/control --tables=/root/ragnarok/run/Demo/$1/tables:tables --fields=/root/ragnarok/run/Demo/$1/fields --plugins=/root/ragnarok/run/tester/plugins --logs=/root/ragnarok/logs/resident --ai=manual"; }
key(){ tmux -L ro-residents send-keys -t mk_$1 -l -- "$2"; tmux -L ro-residents send-keys -t mk_$1 Enter; }
```
Для каждого жителя по очереди:
1. `mk`;
2. дождаться меню, затем `key '0'`;
3. `key` со строкой создания;
4. дождаться `You are now in the game`;
5. `key 'quit'`;
6. `SELECT` по `char_id`.

| Житель | Строка создания | Ожидаемый `char_id` |
|---|---|---|
| resident_a | `0 "ResidentA" 1 1 novice M` | 150001 ResidentA |
| resident_b | `0 "Mira" 1 1 novice F` | 150002 Mira |
| resident_c | `0 "Borin" 1 1 novice M` | 150003 Borin |

- Если OpenKore просит пароль или PIN, профиль сломан: остановись.
- Если `char_id` не совпал, тоже остановись; `DELETE`/`UPDATE` персонажей не делать. Новые id впиши в `config/world.yaml` и `residents.yaml`, запиши решение, сделай коммит.
- Перед Ф10 `tmux -L ro-residents ls` должен показывать только `tester`: на один аккаунт — один OpenKore.

**3. Регистрация в свидетеле.** Это своя схема `residents`, писать в неё разрешено.
```
mariadb -e "INSERT INTO residents.registry(char_id,resident_id,enabled) VALUES (150000,'tester',1),(150001,'resident_a',1),(150002,'resident_b',1),(150003,'resident_c',1)"
cd /root/ragnarok/repos/live_ro && python3 tools/events.py --char Tester --after 0 --follow --duration 20
# pos примерно каждые 5 с; ts_utc отличается от date -u на секунды, а не на часы
```

**4. Персоны.** В `residents.yaml` допиши каждому «Пока новичок (Novice), умений мечника/послушника ещё нет» и запиши решение в `DECISIONS.md`.

## Ф10. Мир
1. Python. Мозг запускается системным `/usr/bin/python3`: python из venv — симлинк на тот же бинарник, поэтому re-exec не происходит. Персоны читаются только при установленном в систему `python3-yaml`. `pip` и `openai` не нужны.
   ```
   python3 -m venv --system-site-packages /opt/ro-residents/world-venv
   /opt/ro-residents/world-venv/bin/python -c 'import yaml'
   ```
2. Ключ LLM. Если `.env` с ключом уже есть, только проверить. Иначе ключ вписывает **владелец** в своей SSH-сессии; агент `.env` не читает.
   ```
   cd /root/ragnarok/repos/live_ro && [ -f .env ] || install -m 600 .env.example .env   # владелец: nano .env → RO_WORLD_LLM_API_KEY=...
   git check-ignore .env     # печатает .env
   tools/ro doctor           # OK, код 0; 1 — нет ключа, 2 — отказ или пауза, 3 — ответ не JSON
   ```
3. Конфиг:
   ```
   python3 -c "import json;c=json.load(open('config/world.yaml'));print([(r['name'],r['char_id']) for r in c['residents']],c['chat_service'])"
   # ResidentA 150001, Mira 150002, Borin 150003, standalone
   ```
4. Юниты:
   ```
   install -m 0644 /root/ragnarok/repos/live_ro/ops/units/ro-world.service /root/ragnarok/repos/live_ro/deploy/ro-chat.service /etc/systemd/system/
   install -d /etc/systemd/system/ro-world.service.d
   printf '[Unit]\nWants=ro-residents-login.service ro-residents-char.service ro-residents-map.service\n' > /etc/systemd/system/ro-world.service.d/10-rathena.conf
   systemd-analyze verify /etc/systemd/system/ro-world.service /etc/systemd/system/ro-chat.service
   systemctl daemon-reload
   rm -f /var/lib/ro-world/STOP /root/ragnarok/run/ESTOP
   ls /var/lib/ro-world/*/BEHAVIOR_V1 /var/lib/ro-world/*/behavior-lease.json 2>/dev/null     # пусто
   systemctl enable --now ro-world.service
   # дождаться каталогов /var/lib/ro-world/resident_{a,b,c}/, затем:
   systemctl enable --now ro-chat.service
   ```
5. Первые минуты:
   ```
   python3 -m json.tool /var/lib/ro-world/identity.json        # 9 процессов, pid не null, restarts 0
   grep -h '"ready"' /var/lib/ro-world/resident_*-gateway.log
   grep -a -E 'Loading plugin .*residentBody|You are now in the game|XSTools|Character selection' /var/lib/ro-world/resident_a-body.log | tail
   tail -n 30 /var/lib/ro-world/world.log      # нет повторяющихся "exited; independent backoff"
   ```
6. Обнови `DECISIONS.md` и `STATUS.md` и сделай коммит в `deploy/new-world`. Секретов и профилей в репозитории нет, они лежат вне его. Пуш делай, только если у сервера есть доступ на запись. Иначе пришли владельцу список изменённых файлов.

## Приёмка
Готово — это свидетель плюс `ro status`. Тесты доказательством не считаются.

1. **Службы и порты:**
   ```
   systemctl is-active mariadb ro-residents-login ro-residents-char ro-residents-map ro-world ro-chat    # 6 раз active
   ss -ltnH | grep -E ':(3306|6900|6121|5121)\b'                                                      # только 127.0.0.1
   ```
2. **Проверки rAthena.** `checks/01.py` и `checks/char_select_unauth.py` печатают PASS. `checks/01b.py` и `A_acceptance.py` привязаны к id событий старого стенда, на новом мире их не запускать.
3. **Мозг:** `tools/ro doctor` печатает OK и выходит с кодом 0.
4. **Статус:** `tools/ro status` даёт три строки вида «<Имя>: онлайн; <карта> (x, y); <занятие>; <причина>; решил сам (LLM)». Последняя строка — «Мозг: … сегодня $X из $2 …», без «пауза до».
5. **Решения LLM.** Через 30–60 минут у каждого жителя в `decisions.jsonl` должен быть `MODEL_DECISION` и связанный с ним `GOAL_OUTCOME` с outcome `done` (`decision_id` совпадает с `task_id`):
   ```
   python3 - <<'EOF'
   import json,collections
   dec={};n=collections.Counter();done=collections.Counter()
   for l in open('/var/lib/ro-world/decisions.jsonl',encoding='utf8'):
       e=json.loads(l)
       if e['kind']=='MODEL_DECISION':dec[e['decision_id']]=e['resident'];n[e['resident']]+=1
       elif e['kind']=='GOAL_OUTCOME' and e.get('outcome')=='done' and e.get('task_id') in dec:done[e['resident']]+=1
   for r in ('ResidentA','Mira','Borin'):print(r,'MODEL_DECISION',n[r],'GOAL_OUTCOME done',done[r])
   EOF
   ```
   У всех троих оба числа больше нуля.
6. **Свидетель:**
   ```
   tools/sql.sh "SELECT char_id,kind,COUNT(*) FROM residents.resident_events WHERE ts>NOW()-INTERVAL 30 MINUTE AND char_id IN (150001,150002,150003) GROUP BY char_id,kind"
   ```
   - у всех троих есть `pos` и `login`;
   - у охоты с итогом `done` есть `kill` на `prt_fild08` внутри окна задачи.
7. **Чат.** Отправь `tools/tester.sh 'pm Mira привет'`. Ожидаем:
   - в `decisions.jsonl` появился `CHAT_REPLY`;
   - в `ro_residents_logs.chatlog` за 10 с или быстрее есть строка от 150002;
   - русский текст читается без кракозябр.
8. **Деньги:**
   - в `/var/lib/ro-world/llm-spend.json` за сегодня `usd` < 2 и `calls` > 0;
   - `llm-gate.json` отсутствует, равен `{}` или его `until` в прошлом.
9. **Автоподъём:**
   - после `systemctl kill -s KILL ro-residents-map` служба снова `active`, а жители снова онлайн без ручных действий;
   - если убить тело одного жителя, он снова онлайн не позже чем через 60 с, а у остальных в `identity.json` не изменились `restarts`.
10. **Тесты:** `make check`, результат одной строкой в `STATUS.md`. Тесты — не доказательство.

## Известные риски
- **Переход на зимнее время.** 25.10.2026 с 02:00 до 03:00 DATETIME свидетеля неоднозначен. Около часа жители могут показываться без свежего подтверждения и жить на привычках.
- **Неверные входы.** 7 неверных входов за 5 минут с 127.0.0.1 банят localhost для всех тел на 5 минут. Пароли не перебирать.
- **Тело вошло раньше шлюза.** Если тело вошло в игру раньше первого старта шлюза, житель висит в «нет свежего подтверждения». Лечится одним `systemctl restart ro-world`.
- **ro-chat после `ro stop`** упирается в start-limit. После `ro start` выполнить `systemctl reset-failed ro-chat && systemctl start ro-chat`.
- **Сокет `tools/residents`.** По умолчанию он стучится в старый сокет, поэтому всегда передавать `--socket /var/lib/ro-world/<rid>/actions.sock.control`.
- **LLM-мозг в живой игре ещё не запускался.** Это развёртывание будет первой живой приёмкой.
