# Явные изменения поверх pinned upstream

rAthena pin не изменён. Runtime содержит два записанных diff:
- witness-autoload.patch — npc/scripts_custom.conf;
- char-null-session.patch — src/char/char_clif.cpp, null guard перед packet database dispatch.

На pristine checkout соответствующего pin из корня parent repository:
```
git -C vendor/rathena apply --check ../../server/patches/char-null-session.patch
git -C vendor/rathena apply ../../server/patches/char-null-session.patch
```
Затем конфигурация protocol20180620 и сборка только char по лабораторному build flow. Бинарники и runtime credentials не публикуются. Не применять повторно; на deployed source reverse --check подтверждает наличие изменения. Откат source через git apply -R требует пересборки и отдельного scope restart.

Изменение ядра сделано интегратором после фактического SIGSEGV и RED/ GREEN wire-теста, не делегировано worker. Не воспринимать submodule SHA без patch files как эквивалент исправленного стенда.
