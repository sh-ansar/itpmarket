# Deployment Spyon на Windows Server

Этот документ фиксирует действующий production-контракт. Он имеет приоритет над legacy-инструкциями, которые упоминают `C:\ITPMarket` или self-hosted GitHub Actions runner.

## Схема поставки

```text
локальный feature branch
        │ tests + diagnose_runtime.ps1
        ▼
review / явное подтверждение
        │ fast-forward merge в production и push
        ▼
GitHub origin/production
        │ Scheduled Task: Spyon Auto Deploy
        ▼
C:\Spyon\deploy-production.ps1
        │ git fetch origin production
        │ git merge --ff-only origin/production
        ▼
C:\Spyon\current  (branch production)
        │ restart Scheduled Task: Spyon Production
        ▼
127.0.0.1:8765 / Waitress ── Caddy / HTTPS
        │
        └─ /health, /ready, /
```

Production — Windows Server 2016. Активный checkout: `C:\Spyon\current`, только ветка `production`. Автодеплой выполняет `C:\Spyon\deploy-production.ps1` через Scheduled Task `Spyon Auto Deploy`; приложение работает в Scheduled Task `Spyon Production`. Текущий механизм не использует workflow `.github/workflows/deploy-windows.yml`: тот оставлен только как ручной legacy workflow для старого `C:\ITPMarket`.

## Неподлежащие нарушению правила

- Не делать merge/push в `production` без явного подтверждения владельца после отчёта по feature-ветке.
- Не использовать `git reset --hard`, `git clean`, forced checkout или ручное копирование поверх `C:\Spyon\current`.
- Не удалять и не заменять `.runtime`, `.venv`, БД, `data`, browser profiles, `logs`, `output`, `backups` и Playwright browsers.
- Не выполнять bootstrap/migration на существующей production PostgreSQL «для проверки». `--check` — read-only; инициализация без него может менять схему/данные.
- Не печатать и не коммить `.runtime\production.env`, `DATABASE_URL`, `ITP_SESSION_SECRET`, `ITP_CREDENTIAL_MASTER_KEY`, cookies или профили.
- Не открывать Waitress наружу. `ITP_HOST=127.0.0.1`; внешний трафик принимает Caddy.

## Подготовка feature-ветки

```powershell
git switch production
git pull --ff-only origin production
git switch -c feature/<name>

.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q -x '(\.venv|\.git|runtime|data|__pycache__)' .
.\.venv\Scripts\python.exe -m pip check
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\diagnose_runtime.ps1 -Mode Local
```

До approval подготовьте diff, список тестов, известные блокеры и рекомендуемый commit message. Production deployment начинается только после отдельного подтверждения.

Если release включает multi-seller и единый складской товар, код нельзя перезапускать поверх старой схемы. После backup и успешной staging-репетиции уполномоченный оператор применяет PostgreSQL-миграции строго по порядку:

1. `migrations/20260818_multi_seller_v1.sql`.
2. `migrations/20260818_inventory_matching_v1.sql`.

Обе миграции аддитивны. Вторая не переносит и не объединяет карточки автоматически; она добавляет отсутствующие default-права системных ролей (`admin` и `operator`) без удаления персональных overrides. После применения `engine/postgres_initialize.py --check` должен увидеть новые таблицы; только затем разрешён restart приложения. В рамках локальной feature-разработки эти миграции к production не применяются.

После явного approval локальное продвижение выполняется только fast-forward:

```powershell
git switch production
git pull --ff-only origin production
git merge --ff-only feature/<name>
git push origin production
```

Сразу после push сервер должен обновиться автоматически; исходники на сервер вручную не копируются.

## Проверка сервера перед деплоем

На сервере выполняйте только read-only проверку:

```powershell
Set-Location C:\Spyon\current
git branch --show-current
git status --short --branch
git rev-parse HEAD
Get-Content C:\Spyon\logs\deploy-production.log -Tail 50

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\diagnose_runtime.ps1 `
  -Mode Production `
  -EnvironmentFile C:\Spyon\current\.runtime\production.env
```

Ожидается branch `production`, чистые tracked files, Python 3.10/3.11, целые зависимости, локальный Playwright, machine-wide/custom Chrome, доступная PostgreSQL со схемой `app.tenants`, корректные каталоги/ACL, задачи `Spyon Production` и `Spyon Auto Deploy`, listener и успешные HTTP-пробы. Диагностика скрывает значения секретов. `WARN` нужно объяснить; любой `FAIL` блокирует деплой.

Дополнительная read-only проверка startup-контракта:

```powershell
Set-Location C:\Spyon\current
.\.venv\Scripts\python.exe engine\postgres_initialize.py --check
.\.venv\Scripts\python.exe environment_check.py --check-only
.\.venv\Scripts\python.exe -m pip check
```

## Разрешённый production rollout

После явного approval изменения должны попасть в `origin/production` обычным fast-forward/PR-процессом. На сервере не выполняйте ручной merge. Запустите или дождитесь `Spyon Auto Deploy`; он обязан:

1. Выполнить `git fetch origin production`.
2. Выполнить только `git merge --ff-only origin/production` в `C:\Spyon\current`.
3. Перезапустить Scheduled Task `Spyon Production`.
4. Проверить `http://127.0.0.1:8765/health`, `/ready` и `/`.
5. При любой ошибке завершиться ненулевым кодом и сохранить deploy log.

`deploy\windows\start-production.ps1` загружает secret-bearing env, принудительно задаёт loopback/без открытия браузера, проверяет PostgreSQL, Python imports, Playwright, `pip check` и Chrome, затем запускает `app.py`. Для уже подготовленной БД deploy-обвязка должна вызывать его с `-SkipDatabaseInitialization`: этот режим всё равно делает `postgres_initialize.py --check`, но не пытается инициализировать БД.

## Post-deploy verification

```powershell
$base = 'http://127.0.0.1:8765'
Invoke-WebRequest -UseBasicParsing "$base/health"
Invoke-WebRequest -UseBasicParsing "$base/ready"
Invoke-WebRequest -UseBasicParsing "$base/"

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File C:\Spyon\current\scripts\diagnose_runtime.ps1 `
  -Mode Production `
  -EnvironmentFile C:\Spyon\current\.runtime\production.env
```

Проверьте deploy log, состояние обеих Scheduled Tasks и Caddy, а затем минимальный вход в UI. `/health=200` подтверждает живой процесс; `/ready=200` обязателен для БД; `/=200` подтверждает основной web route. Деплой нельзя объявлять успешным только по одному health endpoint.

## Откат и сбои

Не делайте импровизированный reset/clean. Если `--ff-only` отклонён, worktree грязный, БД недоступна или health/readiness не прошли, остановите rollout, сохраните commit SHA и лог и запросите решение владельца. Откат должен быть новым проверенным revert-коммитом в Git и проходить тот же approval/deploy-путь. Существующие БД и runtime не заменяются checkout-операциями.

Типовые причины блокировки:

- ветка сервера не `production` или tracked worktree изменён;
- отсутствует/повреждена `.runtime\production.env`;
- Python не 3.10/3.11, нарушены зависимости или не найден Chromium/Chrome;
- PostgreSQL недоступна либо схема не готова;
- порт занят посторонним процессом, Scheduled Task/Caddy не запущены;
- browser-профиль отсутствует: это блокирует только соответствующую live-интеграцию, но не должно маскироваться как её успешная проверка.
