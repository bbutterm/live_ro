# Witness v0 — операторская установка

Только на новой изолированной schema residents: применить server/schema/001_witness.sql через локального DB-администратора; миграция намеренно отказывает при повторном CREATE DATABASE. Никогда не применять на чужую схему. Registry добавить отдельно по char_id реально созданного персонажа.

Скопировать residents_witness.txt в npc/custom/ runtime rAthena, проверить readable под service user. Применить server/patches/witness-autoload.patch к pinned rAthena конфигурации; ядро не изменяется. На действующем стенде hot-load выполнен через команду операторского тестера `@loadnpc npc/custom/residents_witness.txt`. Повторная загрузка без unload может создать дубликаты: перед любым повторным применением проверить имеющийся NPC. Автозагрузка после map-server restart проверена отдельно: свежий startup log и новые серверные pos обычного ResidentA.

Rollback: снять autoload include, unload NPC, сохранить необходимые события, затем удалить только собственную schema residents и reader-user при согласованном decommissioning.

Тесты actual logout/login и движение на tester пройдены. Kill/die пока отсутствуют. Loadmap зависит от mapflag loadevent, пока не принят. Для нескольких characters нужны отдельные отрицательные проверки registry. Каждый pos раз в секунду, хранения/retention ещё нет; не считать это готовым массовым наблюдением.
