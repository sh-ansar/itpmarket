# Обновление сервера с 3.0.2 до Spyon 3.4.1

Если на сервере осталась старая версия 3.0.2, простой pull может быть недостаточен:
нужно подтянуть правильную ветку, прогнать все миграции базы и перезапустить процесс.

## Быстрый вариант

На сервере откройте PowerShell в папке приложения и выполните:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\UPGRADE_SERVER_FROM_3_0_2.ps1 -Branch feature/spyon-admin-panel
```

Скрипт:

- останавливает сервер;
- делает резервные копии `data\unityre_kaspi.db`, `collectors\ozon\data\ozon_registry.db` и config-файлов;
- делает `git fetch`, `checkout` и `pull --ff-only`;
- устанавливает runtime;
- запускает миграции `3.1.0 -> 3.4.1`;
- чистит `__pycache__`;
- запускает self-test и сервер;
- проверяет `/health`.

## Если всё ещё виден старый интерфейс

Проверьте:

1. Сервер запущен из той же папки, где обновлялся Git.
2. Ветка:

```powershell
git branch --show-current
git log -1 --oneline
```

Ожидается ветка `feature/spyon-admin-panel` и коммит с `Rebrand admin panel to Spyon`.

3. Процесс был перезапущен после pull.
4. В браузере нажмите `Ctrl+F5`, потому что static-файлы версионируются query-string, но браузер всё равно может держать старые вкладки.

## Про старые файлы

Не удаляйте вручную `data`, `.runtime`, `.kaspi_profile`, `.playwright`, `logs`, `output`, `backups` и данные Ozon.
Они содержат рабочую базу, профили браузеров, runtime и историю запусков.

Если разворачивать через `deploy\deploy_from_runner.ps1`, он использует mirror-copy и удаляет старые файлы приложения,
но сохраняет постоянные директории.
