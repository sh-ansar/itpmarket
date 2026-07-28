# Обновление сервера с 3.0.2 до Spyon

Если на сервере старая версия, не копируйте файлы проекта вручную поверх рабочей папки. Обновляйте через Git и один upgrade-скрипт.

## Быстрый запуск

Откройте PowerShell на сервере и выполните команды по одной:

```powershell
New-Item -ItemType Directory -Force C:\Temp | Out-Null
```

```powershell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/sh-ansar/itpmarket/feature/spyon-admin-panel/UPGRADE_SERVER_FROM_3_0_2.ps1" -OutFile "C:\Temp\UPGRADE_SERVER_FROM_3_0_2.ps1"
```

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
```

```powershell
C:\Temp\UPGRADE_SERVER_FROM_3_0_2.ps1 -ProjectRoot C:\ITPMarket\app -Branch feature/spyon-admin-panel
```

## Что делает скрипт

- останавливает сервер;
- делает backup базы, Ozon-базы и config-файлов;
- сохраняет локальные изменения рабочей папки в `backups\pre_upgrade_worktree_*`;
- приводит код к состоянию ветки `feature/spyon-admin-panel`;
- сохраняет рабочие данные: `data`, `collectors\ozon\data`, `.runtime`, `.venv`, `.kaspi_profile`, `.playwright`, `logs`, `output`, `backups`;
- запускает миграции `migrate_3_1_0.py` ... `migrate_3_4_1.py`;
- чистит `__pycache__`;
- запускает self-test, сервер и проверяет `/health`.

## Если снова виден старый интерфейс

Проверьте, что сервер запущен из той же папки:

```powershell
cd C:\ITPMarket\app
git branch --show-current
git log -1 --oneline
```

Ожидается ветка `feature/spyon-admin-panel` и свежий коммит из этой ветки. После успешного обновления в браузере нажмите `Ctrl+F5`.

## Как обновлять дальше

Старые ручные bat-файлы для отдельных версий больше не используются. Для старого сервера используйте `UPGRADE_SERVER_FROM_3_0_2.ps1`.

Для автоматического деплоя нормальная схема такая: проверяем feature-ветку вручную, потом мержим ее в `main`, а self-hosted GitHub runner на сервере запускает `deploy\deploy_from_runner.ps1` после каждого push в `main`.
