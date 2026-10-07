# Witness event contract v1 — интерфейс для независимых workers

Проектный контракт, ещё не развёрнутая схема. Только интегратор меняет этот файл. Оригинал ARCHITECTURE §3.3; изменения требуют отдельного обсуждения.

Schema `residents` (создание только изолированной новой схемы после проверки отсутствия):
- registry: char_id INT PRIMARY KEY, resident_id VARCHAR(24) UNIQUE NOT NULL, enabled TINYINT NOT NULL.
- resident_events: id BIGINT AUTO_INCREMENT PRIMARY KEY; ts DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3); char_id INT NOT NULL; kind VARCHAR(16) NOT NULL; map VARCHAR(16), x SMALLINT, y SMALLINT; a1 INT, a2 INT; INDEX(char_id,id).
- kinds: login, logout, die, kill, blvup, jlvup, loadmap, pos.
- Координаты/HP не читать как live из сохранённой char-таблицы. Payload выше не содержит HP; не делать claims по HP.
- id — серверный курсор; timestamp — серверное время без timezone (указать DB timezone при нормализации), не UTC по предположению.
- Reader: только SELECT, polling id > persisted_cursor. При local SQLite ingestion cursor и event insert в одной транзакции; unique(source, source_key), source_key resident_events:<id>. Mock fixtures помечены synthetic, не runtime evidence.
- a1/a2 semantics согласовать с witness implementation для каждого kind до принятия; не добавлять незаметно обязательные поля.
- Никаких подтверждений body actions в текущем контракте; они появятся в вехе B отдельно.
