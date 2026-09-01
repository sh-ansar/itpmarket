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
- Не печатать и не коммить `.runtime\production.env`, `DATABASE_URL`, `ITP_SESSION_SECRET`, `ITP_CREDENTIAL_MASTER_KEY`, `ITP_TELEGRAM_BOT_TOKEN`, cookies или профили.
- Не открывать Waitress наружу. `ITP_HOST=127.0.0.1`; внешний трафик принимает Caddy.

## Подготовка feature-ветки

```powershell
git switch production
git pull --ff-only origin production
git switch -c feature/<name>

$env:ITP_STORAGE_BACKEND = 'sqlite'
$env:ITP_ENV = 'test'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q -x '(\.venv|\.git|runtime|data|__pycache__)' .
.\.venv\Scripts\python.exe -m pip check
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\diagnose_runtime.ps1 -Mode Local
```

До approval подготовьте diff, список тестов, известные блокеры и рекомендуемый commit message. Production deployment начинается только после отдельного подтверждения.

Если release включает multi-seller и единый складской товар, код нельзя перезапускать поверх старой схемы. Сначала создайте и проверьте custom-format backup:

```powershell
Set-Location C:\Spyon\current
. .\deploy\windows\environment.ps1
Import-SpyonEnvironment -Path .\.runtime\production.env
.\.venv\Scripts\python.exe engine\backup_database.py `
  --db .\data\unityre_kaspi.db --output C:\Spyon\backups
```

Backup-helper определяет major-версию PostgreSQL-сервера и использует `pg_dump`/`pg_restore` той же версии. Отсутствие подходящих клиентских утилит блокирует rollout: архив от более нового клиента может содержать параметры, которые старый сервер не сможет восстановить.

После backup и успешной staging-репетиции уполномоченный оператор применяет PostgreSQL-миграции строго по порядку:

1. `migrations/20260818_multi_seller_v1.sql`.
2. `migrations/20260818_inventory_matching_v1.sql`.
3. `migrations/20260818_telegram_notifications_v1.sql`.
4. `migrations/20260819_email_auth_notifications_v1.sql`.
5. `migrations/20260820_company_addresses_v1.sql`.
6. `migrations/20260827_self_service_billing_v1.sql` — self-service registration fields, subscriptions, invoices and payment-proof storage. An unpaid invoice is reissued by the application: the source invoice is cancelled and a new one is created; no manual database change is required.
7. `migrations/20260827_legal_acceptances_v1.sql` — immutable evidence of offer/privacy acceptance during registration.

Все миграции аддитивны. Вторая не переносит и не объединяет карточки автоматически; она добавляет отсутствующие default-права системных ролей (`admin` и `operator`) без удаления персональных overrides. Третья создаёт только персональные Telegram-привязки и журнал доставки; токен в PostgreSQL не хранится. После применения `engine/postgres_initialize.py --check` должен увидеть новые таблицы; только затем разрешён restart приложения. В рамках локальной feature-разработки эти миграции к production не применяются.

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

Ожидается branch `production`, чистые tracked files, Python 3.10/3.11, целые зависимости, локальный Playwright, machine-wide/custom Chrome, доступная PostgreSQL со схемой `app.tenants`, корректные каталоги/ACL, задачи `Spyon Production` и `Spyon Auto Deploy`, listener и успешные HTTP-пробы. Для invoice stamp production env задаёт внешний защищённый путь `SPYON_INVOICE_STAMP_PATH` и его `SPYON_INVOICE_STAMP_SHA256`; сама печать не хранится в checkout. Автодеплой сначала ищет `git.exe` в PATH и стандартной установке Git for Windows, затем использует только существующий approved recovery runtime; при отсутствии проверенного Git он завершается с явной ошибкой. Диагностика скрывает значения секретов. `WARN` нужно объяснить; любой `FAIL` блокирует деплой.

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

Создайте production-бота в BotFather командой `/newbot`. Telegram включается только через закрытый `.runtime\production.env`:

```text
ITP_TELEGRAM_BOT_ENABLED=1
ITP_TELEGRAM_BOT_TOKEN=<token from BotFather>
ITP_TELEGRAM_BOT_USERNAME=<username without @>
SPYON_PUBLIC_URL=https://spyon.kz
```

Worker использует long polling внутри единственного production-процесса. Нельзя одновременно запускать два экземпляра с одним bot token. После импорта production env запустите `python C:\Spyon\current\scripts\diagnose_telegram.py`: он вызывает только `getMe`, проверяет schema/table и не печатает token и не отправляет сообщений. Затем перезапустите `Spyon Production`, проверьте `/health` и `/ready`, откройте `Settings → Telegram`, создайте одноразовый код и отправьте боту `/link TOKEN`; `/status` подтверждает связь. Для controlled test создайте обычное тестовое уведомление после привязки.

Transactional SMTP is required in `.runtime\production.env`: set `ITP_EMAIL_ENABLED=1`, a non-local `ITP_SMTP_HOST`, `ITP_MAIL_FROM`, `ITP_SMTP_SECURITY=starttls` or `smtps`, and a public HTTPS `SPYON_PUBLIC_URL`. Set SMTP username and password together when the provider requires authentication. `scripts\diagnose_runtime.ps1 -Mode Production` validates this contract without showing secret values; the optional real-send test remains opt-in through `ITP_EMAIL_INTEGRATION_TEST=1`.

Для Ozon после deploy проверьте пути обеих площадок: непустой collector-local legacy Chrome profile может получить только один активный продавец во всех компаниях, чей source URL однозначно совпал с seller-вкладкой этого браузера; owner marker должен содержать только seller ID и нормализованный публичный seller URL. Каждый другой продавец использует отдельный seller-scoped профиль, а enrichment/price queue должна содержать только статьи из `product_sources` выбранного source URL. Ozon.ru должен подключаться к PostgreSQL schema `ozon_ru`, а повторный Ozon.kz запуск с уже открытым профилем — использовать его `DevToolsActivePort`, не создавать конфликтующий Chrome и не завершать процессы других sellers.

### Visible Ozon browsers

Production collectors run in Session 0 and must only attach to a visible browser opened by an interactive user. `ITP_ENV=production` therefore disables implicit Ozon Chrome creation unless `OZON_AUTO_OPEN_BROWSER` is explicitly enabled (it should remain disabled in production). After deployment, while signed in as the interactive Windows user, register the separate logon task once:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File C:\Spyon\current\scripts\register_ozon_browser_task.ps1
```

It creates only `\Spyon\Spyon Ozon Browsers` with `InteractiveToken` / “Run only when user is logged on”, launches `open_ozon_browsers.py` at logon, and stores no password. It neither changes nor restarts `Spyon Production`. Do not run this registration from a developer workstation or a Session-0 service account.

Ozon source approval attaches to that existing interactive browser only. A parsed URL is not enough: the runtime must verify the final same-marketplace seller URL, canonical link and seller identity. Both legacy `/seller/<slug>/` and current `/продавец/<slug>/` storefront paths are accepted; new connections normalize to the current path. Browser unavailability, a challenge, or `NO_CATALOG` from every seller source must leave the operation failed rather than successful, and deployment itself must not start Chrome from Session 0.

Canonical Ozon URL contract: accept both seller path variants on input, but persist and issue all new Ozon.ru/Ozon.kz source URLs as `https://<host>/seller/<slug>/`.

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

## Automatic production migrations

Production deployment automatically handles safe PostgreSQL schema updates.

The deployment sequence is:

1. Fast-forward the production checkout.
2. Synchronize Python dependencies.
3. Inspect pending PostgreSQL migrations.
4. Create a verified PostgreSQL backup when a migration is pending.
5. Apply safe append-only migrations under an advisory lock.
6. Verify the PostgreSQL schema.
7. Restart Spyon.
8. Require successful /health, /ready and / responses.

Applied migrations are recorded in app.schema_migrations with the migration
filename, SHA-256 checksum and application timestamp.

New automatically applied migrations must contain SPYON-AUTO-MIGRATION and
must use BEGIN/COMMIT. Destructive operations such as DROP, DELETE and TRUNCATE
are rejected by the automatic deployment path.

An already applied migration file must never be edited. A new migration must be
created instead.
# Operation queue after restart

`data/tasks_state.json` is operational state and must not be deleted during a
deployment. On startup, live running child processes are recovered by identity,
and persisted queued tasks are dispatched when capacity is available. The
default queue bound is 1000 waiting operations; reaching it returns an explicit
capacity error, while reaching `max_parallel_tasks` alone does not reject work.

# Ozon browser prerequisite

Register `\Spyon\Spyon Ozon Browsers` from the interactive RDP account with
`scripts\register_ozon_browser_task.ps1`. The task uses `InteractiveToken` and
opens each active Ozon seller's resolved profile after logon; it must not run as
SYSTEM or in a background session.

Marketplace source replacement and deletion are application-level workflows.
Deployment must not delete marketplace data manually. Replacement validates the
new source before its atomic activation, moves only the old seller's schedules,
marks the old record `replaced` and purges only its current materialized data.
Delete source is seller-scoped, password-confirmed, disables only its schedules
and credentials, and preserves run/audit history. Verify these outcomes through
the application drawer after deployment rather than by editing production data.
