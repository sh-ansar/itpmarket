# Spyon: функциональные возможности

Документ описывает состояние ветки `feature/runtime-audit-and-startup` после runtime-аудита 18 августа 2026 года. Источники истины — исполняемый код, схема данных и автоматические тесты; наличие элемента в интерфейсе само по себе не считается подтверждением backend-функции.

## Назначение и архитектура

Spyon — многопользовательская SaaS-панель для подключения магазинов к маркетплейсам, формирования изолированных каталогов компании, сбора собственных и конкурентных цен, запуска операций и подготовки отчётов. Поддерживаются Kaspi, Ozon.ru, Ozon.kz, Halyk Market, Forte Market и Wildberries.

Основной процесс — Flask-приложение под Waitress. При импорте `app.py` создаются сервисы аутентификации, компаний, подписок, каталога, задач и уведомлений, проверяется выбранное хранилище и, если не установлен `ITP_DISABLE_SCHEDULER=1`, запускается поток планировщика. Долгие операции выполняются отдельными Python-процессами через `TaskManager`; их состояние и хвосты логов отображаются в API/UI. В production Waitress слушает только `127.0.0.1`, а внешний HTTPS завершает Caddy.

SQLite является локальным backend по умолчанию. Production-контракт требует PostgreSQL. Слой совместимости отображает основные и отдельные реестры Ozon в схемы `app`, `ozon_ru` и `ozon_kz`. Инициализация PostgreSQL вынесена в явные команды и не должна незаметно выполняться диагностикой.

## Компании, пользователи и безопасность

- Регистрация создаёт компанию в статусе `pending` и владельца без доступа к маркетплейсам. `superadmin` платформы рассматривает компанию, подтверждает или отклоняет её и выдаёт доступ к конкретным площадкам.
- Роли компании: `viewer` (просмотр), `operator` (просмотр, изменение товарных состояний, запуск/остановка операций и отчёты) и `admin` (все права компании, включая профиль, подключения, фильтры и пользователей).
- Эффективный доступ к площадке — пересечение разрешения компании, активного подключения и персонального разрешения сотрудника. Эти проверки повторяются в backend и при выполнении отложенного расписания.
- Каталоги, подключения, товарные состояния, задания, отчёты и расписания фильтруются по tenant. Изоляция нескольких компаний покрыта тестами.
- Есть проверенный модуль `CredentialVault` с Fernet-шифрованием, но текущий HTTP/runtime-путь приложения его не подключает. Сессионные данные browser-интеграций остаются машинными файлами в исключённых из Git каталогах.
- Логи API редактируются по известным именам секретов; диагностический скрипт скрывает URL PostgreSQL и значения secret-переменных.

## Подписки и ограничения

Планы и функции хранятся в БД и могут редактироваться платформенным администратором. Начальные значения кода:

| План | Срок | Цена, KZT | Позиций на площадку | Операций в день | Отключено |
| --- | ---: | ---: | ---: | ---: | --- |
| `trial` | 3 дня | 0 | 25 | 3 | расписания, динамические фильтры, команда |
| `starter` | 30 дней | 14 900 | 100 | 10 | расписания |
| `growth` | 30 дней | 39 900 | 500 | 50 | — |
| `business` | 30 дней | 89 900 | 1 000 | без лимита | — |

Дополнения увеличивают лимит позиций выбранной площадки: +100 за 5 000 KZT, +500 за 18 000 KZT и +1 000 за 30 000 KZT. Поддерживаются заявки, ручное подтверждение оплаты, снимок условий утверждённой подписки, одноразовый trial и напоминания об окончании подписки. Лимиты каталога проверяются до замены существующего снимка, а суточный лимит операций резервируется атомарно.

## Каталог, аналитика и операции

- Компания задаёт собственный набор товаров и привязки к площадкам. Поддерживаются категории, характеристики, алиасы атрибутов, статические и динамические фильтры, выбор всех/отфильтрованных/отмеченных товаров.
- Операции включают сбор каталога, актуализацию собственных цен, получение точных предложений продавцов, поиск рыночных совпадений, повтор ошибок, полный sync, аудит каталога, экспорт и резервное копирование.
- Для Ozon.ru есть отдельный реестр обнаружения, нормализация характеристик, оценка качества карточки, очередь повторов, история цен, market matching и HTML/табличный экспорт.
- Фоновое задание считается успешным только при нулевом exit code. Состояния `PARTIAL`, `BLOCKED`, `FAILED` и `INTERRUPTED` теперь завершают Ozon-задачу ошибкой; частичные ошибки точных предложений Kaspi, Halyk и Forte также возвращают ненулевой код.
- Планировщик запускает разрешённые операции, повторно проверяет роль, tenant, доступ к площадке и feature `schedules`, резервирует лимит подписки и записывает результат исполнения.
- Уведомления создаются для запуска, завершения, ошибки/остановки операций и для приближающегося окончания подписки; доступны непрочитанные, чтение одного и чтение всех.

## Карта функций

| Функция | Назначение | Backend module | UI/API | Внешняя зависимость | Подтверждённый статус |
| --- | --- | --- | --- | --- | --- |
| Регистрация и вход | Создание pending-компании, session auth, recovery и logout | `auth_service.py`, `saas_service.py`, `app.py` | публичные страницы и auth API | SMTP не является обязательным runtime-сервисом | `PASS` unit/HTTP |
| Super Admin | Рассмотрение компаний, grants площадок, source rules, тарифы | `saas_service.py`, `subscription_service.py` | отдельные platform routes/pages | нет | `PASS` unit |
| Пользователи/RBAC | Роли, персональные права и marketplace overrides | `tenant_security.py`, `auth_service.py` | company settings/API | нет | `PASS` unit |
| Каталог компании | Tenant-снимок, категории, атрибуты, алиасы и фильтры | `catalog_configuration_service.py`, `data_service.py` | товары, фильтры и API | данные площадок | `PASS` unit |
| Подписки | Планы, оплаты, feature/position/daily limits, add-ons | `subscription_service.py` | публичные тарифы и settings/platform API | ручное подтверждение оплаты | `PASS` unit |
| Jobs | Subprocess lifecycle, concurrency, stop, logs и status cache | `task_manager.py`, `app.py` | operations API/UI | Python/Chrome/сеть по типу job | `PASS` unit и local runtime |
| Scheduler | Запуск due schedules с повторной авторизацией и лимитами | `scheduler_service.py`, `saas_service.py` | operations/settings API/UI | процесс приложения | `PASS` unit; production task не проверялась локально |
| Уведомления | Состояния jobs и окончание подписки | `notification_service.py` | notification API/UI | нет | `PASS` code/unit paths |
| Отчёты | Tenant/marketplace-scoped экспорт market intelligence | `engine/export_market_intelligence.py`, `engine/export_report.py` | reports/operations UI | файловая система, данные площадок | `PASS` unit/code; реальный production export не запускался |
| Dashboard | Агрегаты каталога, цен, конкурентов и операций | `data_service.py`, `public_product_service.py`, `app.py` | dashboard UI/API | актуальные данные сборщиков | `PASS` unit/code; содержимое зависит от sync |
| Audit/logging | Audit каталога, task/deploy logs, редактирование чувствительных значений | `engine/catalog_audit.py`, `task_manager.py`, `security_hygiene.py` | reports/operations и файловые логи | writable `logs`/`output` | `PASS` code/local ACL |
| Backup | SQLite copy либо `pg_dump` для PostgreSQL | `engine/backup_database.py` | только разрешённая system job | `pg_dump` в production PATH | `PASS` code; production backup не запускался |
| Health/readiness | Раздельная liveness и проверка основной БД | `app.py` | `/health`, `/ready`, `/` | PostgreSQL в production | `PASS` local HTTP |
| Credential vault | Шифрованное tenant-хранилище credential payload | `credential_vault.py` | не подключено к текущему app API/UI | `ITP_CREDENTIAL_MASTER_KEY` | `EXPERIMENTAL`: unit PASS, runtime integration отсутствует |

## Интеграции

| Integration | Purpose | Entry point | Required dependencies | Required env/config | External executable | Browser | Profile | Scheduler | PostgreSQL | Local test | Result |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Kaspi | Каталог, свои цены, предложения, full sync | `engine/kaspi_search_compare_v8_2.py`, `engine/exact_offer_refresh.py`; `app.py` actions | Selenium, certifi | seller/city/zone в config/tenant connection; optional `CHROMEDRIVER_PATH` | Chrome; ChromeDriver или Selenium Manager | да | `.kaspi_profile` | да, по тарифу | да через app storage | CLI, unit, tenant/limit/atomic tests | `BLOCKED` live: профиль пуст и нет подтверждённой сессии |
| Ozon.ru | Discovery, enrichment, prices, market matching, retry, export | `collectors/ozon/ozon_collector.py` | Selenium, selenium-stealth | source/seller connection; optional `OZON_CHROME_PATH` | Chrome и driver | да | `collectors/ozon/chrome_vpn_profile` | да, по тарифу | да, schema `ozon_ru` | прямой `SELF_TEST.py`, 8 fixture-товаров; unit/CLI | `PASS` fixture; `BLOCKED` live: нет профиля/VPN-сессии |
| Ozon.kz | Отдельный KZ storefront catalog/prices/full sync | `collectors/ozon_kz/ozon_kz_collector.py` | Selenium и общий Ozon browser runtime | source/seller; debug port (default 9333); optional Chrome path | Chrome remote debugging | да | `collectors/ozon/chrome_kz_profile` | да, по тарифу | да, schema `ozon_kz` | CLI, storage и source-boundary tests | `PASS` contract; `BLOCKED` live: нет отдельной сессии |
| Halyk Market | Публичный каталог и точные предложения | `collectors/halyk/halyk_collector.py` | certifi/stdlib HTTPS | seller, location, query/category в config/connection | optional `curl.exe` fallback | нет | нет | да, по тарифу | да через app storage | ограниченная публичная probe: 1 item, reported total 816; unit | `PASS` probe; partial cards дают exit 2 |
| Forte Market | Catalog/product/offer API и fallback сортировки | `collectors/forte/forte_collector.py` | Python HTTPS stack | seller/merchant/city/category в config/connection | нет | нет | нет | да, по тарифу | да через app storage | публичная probe: total 12 736 и sample offer; unit | `PASS` probe; partial cards дают exit 2 |
| Wildberries | Публичный seller catalog, цены и tenant-снимок | `collectors/wildberries/wildberries_collector.py` | Python HTTPS stack | seller ID, currency/destination | нет | нет | нет | да, по тарифу | да через app storage | seller `250000260`: total 4 296 и sample; unit | `PASS` probe; внешний API может меняться |

Live-пробы были только публичными и ограниченными по объёму. Они не изменяли каталог приложения. Полные Kaspi/Ozon проверки сознательно не запускались без профилей и авторизованных сессий.

## UI и backend

Публичная часть содержит регистрацию, вход, описание продукта и тарифы; `/api/public/plans` доступен и до первичной настройки пользователей. Рабочая панель показывает обзор, товары, операции, отчёты, настройки, подписку, уведомления и справку согласно эффективным правам. Платформенный интерфейс отделён от company-admin и включает рассмотрение компаний, выдачу площадок, правила source URL и подписки.

Backend предоставляет отдельные проверки прав на маршрутах и не полагается на скрытие кнопок. Каталог и операции поддерживают server-side фильтрацию и пагинацию. Health-контракты разделены: `/health` проверяет процесс, `/ready` — доступность основной БД, `/` — реальный HTTP/UI путь.

## Что подтверждено и что остаётся ограничением

Подтверждено: 92 автоматических теста, Python compile, JavaScript syntax, PowerShell parse, согласованность Python-пакетов, локальный Waitress startup и HTTP 200 для `/health`, `/ready`, `/`, `/api/public/plans`; локальные Chromium и Chrome обнаружены.

Не подтверждено из этой рабочей станции: соединение с реальной production PostgreSQL, состояние `C:\Spyon\current`, Scheduled Tasks и Caddy на сервере, production ACL, live-сессии Kaspi/Ozon и полный сетевой сбор. Это не дефекты кода, а проверки, требующие доступа к соответствующей машине/учётной сессии. PostgreSQL-совместимость проверена unit-тестами и анализом SQL, но локального сервера PostgreSQL в среде аудита не было.

Экспериментальными/операционно хрупкими следует считать browser automation Ozon/Kaspi, внешний API fallback и standalone `CredentialVault` до его подключения к runtime. Изменения HTML, антибот-защиты или публичных API маркетплейсов могут потребовать адаптации сборщиков.
