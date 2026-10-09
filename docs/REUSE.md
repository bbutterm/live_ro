# Переиспользование v1
Берём server/witness и packages/witness_reader: серверные факты.
Берём residentBody и body_gateway: нативные команды и журнал действий.
Берём resident_demo/life_cycle: правила охоты, снабжения и reconciliation.
Берём resident_demo/llm_decision, perception, budget: проверенные ограничения.
Новый постоянный supervisor заменяет конечный ночной supervisor, не удаляя его регрессии.
Исторические scenarios/owner_agency_20261008 остаются архивом, не запускаются.
Истёкшие deadline и session guards не применяются к новому миру.
Существующие 156 тестов сохраняем: выброшенной обвязки пока нет.
