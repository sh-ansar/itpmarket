# Локальная разработка Spyon

Инструкция рассчитана на Windows PowerShell и чистый checkout. Поддерживаются только Python 3.10 и 3.11. Для разработки используется отдельное окружение `.venv`; штатные `INSTALL.bat`/`START.bat` создают собственный игнорируемый runtime `.runtime\venv_3_2_0`.

## Подготовка

```powershell
git switch production
git pull --ff-only origin production
git switch -c feature/<name>

py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-postgres.txt
$env:PLAYWRIGHT_BROWSERS_PATH = (Join-Path (Get-Location) '.playwright')
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe environment_check.py --check-only
```

`--check-only` ничего не устанавливает и не создаёт: он только проверяет Python, импорты и Chromium. Обычный `environment_check.py` может подготовить недостающий runtime, поэтому для диагностики используйте именно read-only режим.

Google Chrome нужен browser-сборщикам Kaspi/Ozon. Установите его для всей машины либо задайте `OZON_CHROME_PATH`. Явный совместимый ChromeDriver задаётся `CHROMEDRIVER_PATH`; без него Selenium Manager может потребовать исходящий доступ в интернет.

## Локальный запуск на SQLite

`.env.example` — справочник, приложение не загружает его автоматически. Переменные устанавливаются в текущем PowerShell-сеансе:

```powershell
$env:ITP_ENV = 'local'
$env:ITP_HOST = '127.0.0.1'
$env:ITP_PORT = '8765'
$env:ITP_OPEN_BROWSER = '0'
$env:ITP_STORAGE_BACKEND = 'sqlite'
$env:ITP_DISABLE_SCHEDULER = '1'
$env:PLAYWRIGHT_BROWSERS_PATH = (Join-Path (Get-Location) '.playwright')
.\.venv\Scripts\python.exe app.py
```

Откройте `http://127.0.0.1:8765`. Для повседневного запуска после `INSTALL.bat` можно использовать `START.bat`; он проверит `.runtime` и откроет браузер. Не выставляйте `ITP_HOST=0.0.0.0` без осознанной настройки firewall и аутентификации.

SQLite хранится по пути из `config.json`/`config.local.json` (по умолчанию `data\unityre_kaspi.db`). `config.local.json`, БД, секрет сессии, runtime, профили и браузеры игнорируются Git. Не используйте рабочую или production-БД в тестах.

`ensure_database()` аддитивно создаёт локальные таблицы `tenant_inventory_products`, `tenant_product_listings`, `tenant_product_match_decisions` и `tenant_inventory_events`. Остаток и закупочная цена принадлежат единому физическому товару; карточки площадок связываются с ним отдельно. Для проверки используйте только новую dev-БД или копию без реальных данных.

## Опциональный PostgreSQL для разработки

Используйте отдельную локальную БД, никогда production URL:

```powershell
$env:ITP_STORAGE_BACKEND = 'postgresql'
$env:DATABASE_URL = 'postgresql://spyon:LOCAL_PASSWORD@127.0.0.1:55433/spyon_dev'
.\.venv\Scripts\python.exe engine\postgres_initialize.py --check
```

`--check` только читает состояние и завершится ошибкой, если схемы не готовы. Инициализация без `--check` и миграционные команды меняют БД; запускайте их только для явно выбранной новой dev-БД после просмотра плана в `docs/POSTGRESQL_MIGRATION_RU.md`.

Для существующей dev PostgreSQL миграции применяются явно через `engine/postgres_migrations.py apply`. В частности, `20260827_self_service_billing_v1.sql` добавляет self-service поля регистрации, счета и подтверждения оплаты. Миграции аддитивны: они добавляют таблицы/индексы и отсутствующие поля, не объединяют карточки автоматически и не удаляют персональные overrides.

Для локального теста Telegram используйте отдельного dev-бота и задайте `ITP_TELEGRAM_BOT_ENABLED=1`, `ITP_TELEGRAM_BOT_TOKEN`, `ITP_TELEGRAM_BOT_USERNAME` и `SPYON_PUBLIC_URL`. Реальный token не записывайте в `.env.example`, код, логи или тестовые fixtures.

SMTP локально остаётся opt-in: задайте `ITP_EMAIL_ENABLED=1`, host, sender и совместимую TLS-настройку только в локальном окружении. Для authenticated SMTP username и password должны задаваться парой.

## Проверки

```powershell
$env:ITP_STORAGE_BACKEND = 'sqlite'
$env:ITP_ENV = 'test'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q -x '(\.venv|\.git|runtime|data|__pycache__)' .
.\.venv\Scripts\python.exe -m pip check

Get-ChildItem -Recurse -File -Filter *.js |
  Where-Object { $_.FullName -notmatch '\\.venv\\|\\runtime\\|\\node_modules\\' } |
  ForEach-Object { node --check $_.FullName }

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\diagnose_runtime.ps1 -Mode Local
```

## Версионные юридические документы

DOCX в `docs/legal` являются единственным юридическим источником текста и
должны попадать в feature change вместе с PDF. Не редактируйте текст при
преобразовании. Реестр `legal_documents.py` ограничивает типы и версии,
поэтому произвольные пути и хеши из HTTP не принимаются. При добавлении новой
версии сохраните старые файлы, добавьте запись в реестр и один раз создайте
статический PDF:

```powershell
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\python.exe .\scripts\generate_legal_pdfs.py
```

После публикации PDF версии не перегенерируются. Для новой версии внесите в
реестр SHA-256 exact DOCX; изменение уже закреплённого файла намеренно
останавливает выдачу документа. Для локальной проверки
запустите `tests/test_legal_documents.py` вместе с миграционными тестами.

Диагностика выводит `PASS/WARN/FAIL` для branch/worktree, Python, зависимостей, Playwright, БД, Chrome/ChromeDriver, каталогов и ACL, профилей, scheduler, порта и HTTP. Она не печатает секреты и возвращает код 1 только при `FAIL`; отсутствие live-профиля или незапущенное локальное приложение — `WARN`.

После старта должны отвечать:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/ready
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/api/public/plans
```

## Проверка интеграций

Сначала используйте `--help`, fixture/self-test и небольшие `probe`, не полный сбор. Ozon fixture запускается напрямую:

```powershell
.\.venv\Scripts\python.exe collectors\ozon\SELF_TEST.py
.\.venv\Scripts\python.exe collectors\halyk\halyk_collector.py --help
.\.venv\Scripts\python.exe collectors\forte\forte_collector.py --help
.\.venv\Scripts\python.exe collectors\wildberries\wildberries_collector.py --help
```

Live Kaspi требует заполненную `.kaspi_profile`. Ozon.ru использует `collectors\ozon\chrome_vpn_profile`, Ozon.kz — отдельный `collectors\ozon\chrome_kz_profile` и Chrome remote debugging. После обновления legacy-профиль сохраняется только за тем активным seller во всех компаниях, чей source URL точно совпал с открытой seller-вкладкой этого профиля; найденная привязка хранится рядом как несекретный owner marker и повторно проверяется по текущему source URL. Очереди enrichment/price дополнительно ограничиваются `product_sources` выбранного seller, поэтому прежние товары другого магазина из общего legacy registry не попадут в его операцию. Все остальные продавцы используют `.runtime\browser_profiles\t<tenant>\<marketplace>\s<seller>` и не разделяют legacy-сессию. Если профиль пуст, вкладка не совпала однозначно или marker стал неактуален, применяется seller-scoped путь. Если Chrome для профиля уже работает, сборщик повторно использует порт из `DevToolsActivePort`, не завершает чужой процесс и не создаёт второй экземпляр профиля. Не копируйте и не коммитьте профили, cookies или их содержимое. Без подтверждённого seller/source URL и сессии обозначайте проверку как `BLOCKED`, а не как успешную.

В production `ITP_ENV=production` по умолчанию запрещает автоматическое создание Chrome из процесса сборщика: он подключается только к уже открытому browser того же seller. Откройте все активные seller-scoped браузеры в интерактивной сессии командой `\.venv\Scripts\python.exe .\scripts\open_ozon_browsers.py`; `--dry-run` безопасно покажет план. Скрипт никогда не использует `--headless`, не завершает Chrome и переиспользует только порт, сохранённый в profile этого seller.

## Частые проблемы

- `Unsupported Python`: пересоздайте окружение через `py -3.11` или `py -3.10`.
- `Executable doesn't exist` у Playwright: повторите установку Chromium с тем же `PLAYWRIGHT_BROWSERS_PATH`.
- Chrome не найден: установите machine-wide Chrome или задайте абсолютный `OZON_CHROME_PATH`.
- `/health` отвечает, `/ready` нет: процесс жив, но выбранная БД недоступна или не инициализирована.
- Exit code 2 у сборщика: операция частичная/заблокированная; смотрите task log и не трактуйте её как success.
- Порт 8765 занят: выясните владельца процесса; не останавливайте неизвестный процесс. Для dev задайте другой `ITP_PORT`.

## Production migration development

New PostgreSQL migrations belong in migrations/ and use ordered
YYYYMMDD_name.sql filenames.

Safe automatic migrations must contain SPYON-AUTO-MIGRATION, start with BEGIN
and end with COMMIT. Run tests/test_postgres_migrations.py before release.

For billing changes, run `tests/test_billing_invoices.py` and
`tests/test_invoice_pdf_service.py`: they cover creation, cancellation, safe
reissue of an unpaid invoice, immutable seller snapshots, protected operator
fields, and configured stamp SHA-256 verification. Invoice stamp and logo
paths belong in runtime environment (`SPYON_INVOICE_STAMP_PATH` and optional
`SPYON_INVOICE_STAMP_SHA256`), never in tracked configuration.

The password reset regression test verifies the complete flow from requesting
an email link through changing the password and consuming the one-use token.

## Queue verification

Run `python -m unittest tests.test_task_queue -v` after changes to operation
scheduling. The production API and scheduler pass `queue_if_busy=True`; direct
callers can still use `queue_if_busy=False` when a fail-fast `RuntimeError` is
explicitly required by an internal test or tool. The durable JSON state keeps
all active (`queued` and `running`) tasks and trims only terminal history.
# Ozon interactive browser runtime

On Windows production deployments, start `scripts\register_ozon_browser_task.ps1`
from the intended RDP user session. Ozon collection only attaches to Chrome that
uses the resolved seller profile and debug port in a non-zero Windows session.
The web service never starts or terminates Chrome in Session 0.
