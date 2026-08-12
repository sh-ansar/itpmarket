# PostgreSQL: безопасный план перехода

PostgreSQL подготовлен как целевая production-СУБД, но приложение пока не
переключено. Это осознанная блокировка: в проекте остаются прямые обращения к
SQLite в web runtime, фоновых задачах, Kaspi/Halyk/Forte collectors и реестре
Ozon.ru. Частичное переключение привело бы к двум источникам истины.

## Целевая схема

- `app` — пользователи, tenants, RBAC, расписания, отчёты и основной каталог;
- `ozon_ru` — существующий независимый реестр Ozon.ru;
- `ozon_kz` — отдельный реестр Ozon.kz;
- `itp_migration` — технический ledger повторяемой миграции.

Секреты не входят в дампы миграции в открытом виде. В PostgreSQL переносится
только ciphertext и `key_id`; master key остаётся во внешнем secret manager.

## Этапы

1. Создать PostgreSQL, отдельного владельца схем и runtime-роль с минимальными
   правами. Включить TLS, резервные копии, PITR и мониторинг.
2. Описать PostgreSQL DDL и constraints для всех таблиц, включая timezone-aware
   timestamps и sequences. Прогнать его в staging.
3. Получить read-only планы для каждой SQLite-базы и сверить counts/PK.
4. Выполнить append-only shadow migration. Инструмент не удаляет и не
   перезаписывает строки; изменившаяся исходная строка останавливает прогон.
5. Перевести модули на общий repository layer, затем включить dual-read
   verification без пользовательской записи в PostgreSQL.
6. Провести приёмку RBAC, tenants, все marketplace collectors, расписания,
   отчёты, backup/restore и нагрузку.
7. Только отдельным согласованным релизом переключить runtime. SQLite оставить
   read-only на период отката; удаление не входит в миграцию.

## Read-only инвентаризация

```powershell
python engine/postgres_migration.py plan --sqlite data/unityre_kaspi.db --target-schema app
python engine/postgres_migration.py plan --sqlite collectors/ozon/data/ozon_registry.db --target-schema ozon_ru
```

## Shadow copy

Целевые таблицы должны быть заранее созданы и иметь все исходные columns.
Команда требует одновременно `DATABASE_URL`, действие `apply` и явный флаг
`--apply`:

```powershell
$env:DATABASE_URL = '<из secret manager>'
python engine/postgres_migration.py apply --apply --sqlite data/unityre_kaspi.db --target-schema app --source-id production-main-v1
```

В текущем релизе `ITP_STORAGE_BACKEND=postgresql` намеренно блокирует runtime,
пока перечисленные SQLite-модули не переведены и не пройдена staging-приёмка.
