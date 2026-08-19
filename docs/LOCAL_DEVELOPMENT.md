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

Для существующей dev PostgreSQL миграции применяются явно и по порядку: `migrations/20260818_multi_seller_v1.sql`, `migrations/20260818_inventory_matching_v1.sql`, затем `migrations/20260818_telegram_notifications_v1.sql`. Миграции добавляют таблицы/индексы и отсутствующие default-права, не объединяют карточки автоматически и не удаляют персональные overrides.

Для локального теста Telegram используйте отдельного dev-бота и задайте `ITP_TELEGRAM_BOT_ENABLED=1`, `ITP_TELEGRAM_BOT_TOKEN`, `ITP_TELEGRAM_BOT_USERNAME` и `SPYON_PUBLIC_URL`. Реальный token не записывайте в `.env.example`, код, логи или тестовые fixtures.

## Проверки

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q -x '(\.venv|\.git|runtime|data|__pycache__)' .
.\.venv\Scripts\python.exe -m pip check

Get-ChildItem -Recurse -File -Filter *.js |
  Where-Object { $_.FullName -notmatch '\\.venv\\|\\runtime\\|\\node_modules\\' } |
  ForEach-Object { node --check $_.FullName }

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\diagnose_runtime.ps1 -Mode Local
```

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

Live Kaspi требует заполненную `.kaspi_profile`. Ozon.ru использует `collectors\ozon\chrome_vpn_profile`, Ozon.kz — отдельный `collectors\ozon\chrome_kz_profile` и Chrome remote debugging. Seller-scoped runtime хранит эти профили в `.runtime\browser_profiles\t<tenant>\<marketplace>\s<seller>`; если Chrome для профиля уже работает, сборщик повторно использует порт из `DevToolsActivePort`, не завершает чужой процесс и не создаёт второй экземпляр профиля. Не копируйте и не коммитьте профили, cookies или их содержимое. Без подтверждённого seller/source URL и сессии обозначайте проверку как `BLOCKED`, а не как успешную.

## Частые проблемы

- `Unsupported Python`: пересоздайте окружение через `py -3.11` или `py -3.10`.
- `Executable doesn't exist` у Playwright: повторите установку Chromium с тем же `PLAYWRIGHT_BROWSERS_PATH`.
- Chrome не найден: установите machine-wide Chrome или задайте абсолютный `OZON_CHROME_PATH`.
- `/health` отвечает, `/ready` нет: процесс жив, но выбранная БД недоступна или не инициализирована.
- Exit code 2 у сборщика: операция частичная/заблокированная; смотрите task log и не трактуйте её как success.
- Порт 8765 занят: выясните владельца процесса; не останавливайте неизвестный процесс. Для dev задайте другой `ITP_PORT`.
