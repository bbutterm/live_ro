# Отчёт Hermes: задание №1

Проверенный commit: `56bdba090258ac96e6ff9c1b9578ce0b5c914d5e`.
Checkout VPS: `/opt/ro-bot-lab/src/live_ro-qa` (отдельный автономный clone).
Это промежуточный runtime-отчёт; полное 20-минутное наблюдение ещё не завершено.

## Выполнено
1. Submodules и `python3 scripts/check.py`: OK, закреплённые версии, чистый checkout, secret scan.
2. `scripts/lab prepare`: OK, создан live_ro.env и недостающие каталоги. Существующие credentials.json и серверные конфиги не изменены.
3. Env заполнен локально, ro-lab, права 600, existing mode. Секреты не опубликованы.
4. `scripts/lab doctor`: FAIL без LC_ALL=C (ошибочно отклоняет BOT01_USER/PASS); OK при `env LC_ALL=C scripts/lab doctor`. SELECT подтвердил Arkady slot 0, lv 16 на момент doctor, карта prt_fild08. Доступ к обеим БД OK.
5. `scripts/lab status`: OK, обнаружены существующие login/char/map и старый bot01.
6. `scripts/lab db-backup`: OK, ro_bot_lab-20261002T100405Z.sql.gz (8398 байт), ro_bot_lab_logs-20261002T100405Z.sql.gz (9792 байт). gzip -t обоих OK. Дамп не проверен восстановлением; single-transaction не обеспечивает консистентность MyISAM.
7. Старый control сохранён в backups/bot01-control-before-56bdba0. С LC_ALL=C stop bot01 и start bot01: OK. Только бот перезапущен. PID нового бота 112320.
8. lowHpGuard загружен. Повторный вход, бой, опыт и лут подтверждены логом. При последней проверке: 23 строки смерти цели; нет `Teleporting due to insufficient HP`.
9. Есть фактическая строка: `[lowHpGuard] HP 7% < 40%: новые цели не выбираю, восстанавливаюсь`.
   Это подтверждает включение защиты, но НЕ полную проверку запрета целей и восстановления до 90%. Снятие защиты пока не подтверждено.

## Ошибки разработчику
- Bash regex `^[!-~]{1,23}$` зависит от локали. Репро: `v=bot01; [[ "$v" =~ ^[!-~]{1,23}$ ]]` возвращает 1 в текущей локали, 0 при LC_ALL=C. Зафиксировать ASCII-валидацию в самом скрипте.
- Background runner не задаёт TERM: повторяющиеся предупреждения `Use of uninitialized value $ENV{"TERM"}`. Передавать TERM=dumb или подходящую настройку.
- При немедленном входе после SIGTERM было `The server still recognizes your last connection`; затем бот автоматически вошёл. Не ошибка окончательного запуска.
- Документация db-backup говорит о блокировках MyISAM, но используется --single-transaction. Уточнить гарантию консистентности; restore-test не выполнялся.

## Проверенный diff профиля без секретов
- lowHpGuard_lower 40 / upper 90.
- Отключены teleportAuto_hp, maxDmg, deadly, atkMiss, dropTargetEngaged.
- Добавлено применение зелий при HP < 50%.
- lowHpGuard добавлен в loadPlugins_list.
- Очищены adminPassword/secureAdminPassword; также исчезла пустая строка callSign.

## Сохранность
login PID 102668, char 102847, map 104040 — не перезапускались. БД не пересоздавались, SQL write/migration не выполнялись в этом задании. Код разработчика не исправлялся.

## Итог
PARTIAL PASS: управление существующим runtime и повторный вход работают с LC_ALL=C. Включение защиты наблюдалось. Полный цикл восстановления и длительная стабильность ещё не подтверждены. Не отмечать задание как полностью PASS.
