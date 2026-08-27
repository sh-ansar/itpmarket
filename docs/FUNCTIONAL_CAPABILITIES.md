# Spyon: функциональные возможности

Документ описывает состояние проекта после аудита 19 августа 2026 года. Источники истины — исполняемый код, схема данных и автоматические тесты; наличие элемента в интерфейсе само по себе не считается подтверждением backend-функции.

## Multi-user and Multi-seller Architecture

| Возможность | Статус | Подтверждение / ограничение |
| --- | --- | --- |
| Multiple tenants | `SUPPORTED` | Tenant-scoped RBAC, catalogs, sellers, jobs, schedules и direct API IDOR regression tests. |
| Multiple users and roles | `SUPPORTED` | `superadmin`, tenant `admin`/`operator`/`viewer`, marketplace overrides и backend permission checks покрыты HTTP/unit tests. |
| Multiple sellers одного marketplace | `SUPPORTED` | Канонический `tenant_seller_id`, seller catalog PK, seller-aware UI/API и одинаковый SKU у двух sellers без collision; выбор продавца показывается только при двух и более активных вариантах. |
| Multiple marketplace accounts | `SUPPORTED` | Seller registry разрешает несколько accounts на tenant + marketplace; legacy integration row больше не является seller identity. |
| Parallel jobs разных sellers | `SUPPORTED` | Seller-scoped resource locks; 6 simultaneous subprocess jobs и 2 TaskManager instances проверены автоматически. |
| Две операции одного seller | `SUPPORTED` | Безопасная модель C: второй Full Sync/Price job отклоняется как conflict, очередь не реализована. |
| Browser/session isolation | `PARTIALLY SUPPORTED` | Путь `.runtime/browser_profiles/t<tenant>/<marketplace>/s<seller>` и передача его collectors проверены; live cookies нескольких аккаунтов не инспектировались. |
| Seller-scoped catalog и Kaspi analytics | `SUPPORTED` | Catalog, own-price и exact-offer snapshots имеют tenant/marketplace/seller keys. |
| Cross-marketplace product identity | `SUPPORTED WITH CONFIRMATION` | Динамические предложения по артикулу/строгим характеристикам, журнал решений и ручное подтверждение; автоматического слияния нет. |
| Единый остаток для связанных listings | `SUPPORTED` | Физический товар хранит количество/закупочную цену один раз; tenant summary не удваивает их по числу площадок. |
| Multi-seller detailed Ozon/Halyk/Forte analytics | `PARTIALLY SUPPORTED` | Seller catalog безопасен; legacy detailed offer enrichment при >1 seller не используется до seller-native analytics migration. |
| Scheduler multi-seller и duplicate claim | `SUPPORTED` | Schedule/run хранит seller; atomic claim и scheduler/manual conflict tests PASS. |
| Subscription limits under concurrency | `SUPPORTED` | Capacity считается суммарно на tenant + marketplace и проверяется внутри serialized transaction. |
| Failure/restart isolation | `PARTIALLY SUPPORTED` | Non-zero job не меняет соседние jobs; dead child восстанавливается как `interrupted`; live auth/network error matrix не проверена. |
| Seller credential storage | `PARTIALLY SUPPORTED` | Зашифрованное seller-scoped storage и ownership tests есть; штатный HTTP/browser runtime ещё не использует vault. |
| Live 2–4 seller Kaspi/Ozon concurrency | `NOT VERIFIED` | В audit environment нет нескольких авторизованных profiles/credentials. |
| Live PostgreSQL concurrency | `NOT VERIFIED` | Bounded pool и SQL/transaction tests PASS, но локального PostgreSQL server нет. |
| In-process job queue | `NOT SUPPORTED` | При capacity/conflict запуск явно отклоняется; постоянная очередь не заявлена. |

Подробные дефекты, исправления, baseline и acceptance-матрица: [MULTI_SELLER_CONCURRENCY_AUDIT.md](MULTI_SELLER_CONCURRENCY_AUDIT.md).

## Назначение и архитектура

Spyon — многопользовательская SaaS-панель для подключения магазинов к маркетплейсам, формирования изолированных каталогов компании, сбора собственных и конкурентных цен, запуска операций и подготовки отчётов. Поддерживаются Kaspi, Ozon.ru, Ozon.kz, Halyk Market, Forte Market и Wildberries.

Основной процесс — Flask-приложение под Waitress. При импорте `app.py` создаются сервисы аутентификации, компаний, подписок, каталога, задач и уведомлений, проверяется выбранное хранилище и, если не установлен `ITP_DISABLE_SCHEDULER=1`, запускается поток планировщика. Долгие операции выполняются отдельными Python-процессами через `TaskManager`; их состояние и хвосты логов отображаются в API/UI. В production Waitress слушает только `127.0.0.1`, а внешний HTTPS завершает Caddy.

SQLite является локальным backend по умолчанию. Production-контракт требует PostgreSQL. Слой совместимости отображает основные и отдельные реестры Ozon в схемы `app`, `ozon_ru` и `ozon_kz`. Инициализация PostgreSQL вынесена в явные команды и не должна незаметно выполняться диагностикой.

## Компании, пользователи и безопасность

- Регистрация создаёт подтверждённую компанию и владельца без доступа к маркетплейсам. После подтверждения email выбранный trial активируется автоматически, а платный пакет переходит в `awaiting_invoice`; ручное подтверждение компании или тарифа не требуется. Проверка остаётся только для конкретного подключения marketplace и для платежа.
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

Дополнения увеличивают лимит позиций выбранной площадки: +100 за 5 000 KZT, +500 за 18 000 KZT и +1 000 за 30 000 KZT. Поддерживаются счёт, безопасное переоформление неоплаченного счёта до загрузки платёжного документа, загрузка подтверждения и ручная проверка оплаты, снимок условий утверждённой подписки, одноразовый trial и напоминания об окончании подписки. Схема self-service регистрации, счетов и подтверждений оплаты закреплена аддитивной PostgreSQL-миграцией `20260827_self_service_billing_v1.sql`, которая автоматически применяется при будущей production-поставке после backup. Лимиты каталога проверяются до замены существующего снимка, а суточный лимит операций резервируется атомарно.

## Каталог, аналитика и операции

- Компания задаёт собственный набор товаров и привязки к площадкам. Поддерживаются категории, характеристики, алиасы атрибутов, статические и динамические фильтры, выбор всех/отфильтрованных/отмеченных товаров.
- Для точных предложений различаются строго уникальный минимум и равная минимальная цена. Равенство получает статус `EXACT_TIED_LOWEST`, показывает размер ничьей и не создаёт фиктивный ценовой потенциал.
- В карточке можно вести внутренний SKU, фактический остаток, закупочную цену и целевую наценку. Закупочная сумма и валовая сценарная рекомендация доступны только по отдельным правам. Расчёт не включает комиссии, логистику, налоги и возвраты.
- Межплощадочный matching формирует предложения динамически: бренд + артикул производителя, затем строгий ключ характеристик, затем требующая проверки похожая модель того же бренда/типа/размера. Объединение выполняется только вручную и записывается в audit events. Подробности: [CATALOG_MATCHING_AND_INVENTORY_RU.md](CATALOG_MATCHING_AND_INVENTORY_RU.md).
- Чтение tenant-каталога использует короткий двухсекундный in-process snapshot и single-flight только в пределах одинаковых tenant/marketplace scope. Это объединяет одновременные запросы списка, фильтров и сводки, не блокируя другие компании; после TTL данные снова читаются из PostgreSQL. Product drawer повторно использует уже проверенный tenant-каталог вместо второй полной выборки.
- Операции включают сбор каталога, актуализацию собственных цен, получение точных предложений продавцов, поиск рыночных совпадений, повтор ошибок, полный sync, аудит каталога, экспорт и резервное копирование.
- Для Ozon.ru есть отдельный реестр обнаружения, нормализация характеристик, оценка качества карточки, очередь повторов, история цен, market matching и HTML/табличный экспорт.
- Фоновое задание считается успешным только при нулевом exit code. Состояния `PARTIAL`, `BLOCKED`, `FAILED` и `INTERRUPTED` теперь завершают Ozon-задачу ошибкой; частичные ошибки точных предложений Kaspi, Halyk и Forte также возвращают ненулевой код.
- Планировщик запускает разрешённые операции, повторно проверяет роль, tenant, доступ к площадке и feature `schedules`, резервирует лимит подписки и записывает результат исполнения.
- Уведомления создаются для запуска, завершения, ошибки/остановки операций и для приближающегося окончания подписки; доступны непрочитанные, чтение одного и чтение всех. Те же персональные события могут доставляться Telegram-ботом после входа по email/паролю в личном чате. Сообщения с логином и паролем сразу удаляются, пароль не сохраняется; действуют ограничение попыток, пауза, повтор доставки и ручная отвязка.

## Карта функций

| Функция | Назначение | Backend module | UI/API | Внешняя зависимость | Подтверждённый статус |
| --- | --- | --- | --- | --- | --- |
| Регистрация и вход | Самообслуживаемое создание компании, email confirmation, session auth и logout | `auth_service.py`, `saas_service.py`, `app.py` | публичные страницы и auth API | SMTP не является обязательным runtime-сервисом | `PASS` unit/HTTP |
| Super Admin | Компании, supplier requisites, source rules, пакеты и подтверждение платежей | `saas_service.py`, `subscription_service.py`, `billing_service.py` | отдельные platform routes/pages | нет | `PASS` unit |
| Пользователи/RBAC | Роли, персональные права и marketplace overrides | `tenant_security.py`, `auth_service.py` | company settings/API | нет | `PASS` unit |
| Каталог компании | Tenant-снимок, категории, атрибуты, алиасы и фильтры | `catalog_configuration_service.py`, `data_service.py` | товары, фильтры и API | данные площадок | `PASS` unit |
| Единый товар и остатки | Tenant-scoped physical product, purchase cost, linked marketplace listings и сводка без дублей | `inventory_service.py`, `app.py` | product drawer, `/api/inventory/summary` | заполнение пользователем | `PASS` unit/HTTP |
| Межплощадочный matching | Динамические предложения, confirm/reject, защита уже заполненных складских товаров | `inventory_service.py` | product drawer и product match API | качество артикулов/характеристик | `PASS` unit/HTTP; только с ручным подтверждением |
| Подписки | Планы, счета, оплаты, feature/position/daily limits, add-ons; замена неоплаченного пакета сохраняет историю и supersedes proof | `subscription_service.py`, `billing_service.py` | settings/platform API | ручное подтверждение оплаты | `PASS` unit |
| Jobs | Subprocess lifecycle, concurrency, stop, logs и status cache | `task_manager.py`, `app.py` | operations API/UI | Python/Chrome/сеть по типу job | `PASS` unit и local runtime |
| Scheduler | Запуск due schedules с повторной авторизацией и лимитами | `scheduler_service.py`, `saas_service.py` | operations/settings API/UI | процесс приложения | `PASS` unit; production task не проверялась локально |
| Уведомления | Состояния jobs, marketplaces, billing и окончание подписки; пользовательские каналы с обязательной доставкой security-событий | `notification_service.py` | notification API/UI | нет | `PASS` code/unit paths |
| Telegram-бот | Вход в личном чате, персональная привязка, доставка без повторной постановки, retry/pause/logout | `telegram_bot.py`, `app.py` | Bot API и Settings status API/UI | Telegram Bot API, secret token в runtime env | `PASS` unit/API; `getMe` подтверждает настроенного бота |
| Отчёты | Tenant/marketplace-scoped экспорт market intelligence | `engine/export_market_intelligence.py`, `engine/export_report.py` | reports/operations UI | файловая система, данные площадок | `PASS` unit/code; реальный production export не запускался |
| Dashboard | Агрегаты каталога, цен, конкурентов и операций | `data_service.py`, `public_product_service.py`, `app.py` | dashboard UI/API | актуальные данные сборщиков | `PASS` unit/code; содержимое зависит от sync |
| Audit/logging | Audit каталога, task/deploy logs, редактирование чувствительных значений | `engine/catalog_audit.py`, `task_manager.py`, `security_hygiene.py` | reports/operations и файловые логи | writable `logs`/`output` | `PASS` code/local ACL |
| Backup | SQLite copy либо `pg_dump` для PostgreSQL | `engine/backup_database.py` | только разрешённая system job | `pg_dump` в production PATH | `PASS` code; production backup не запускался |
| Health/readiness | Раздельная liveness и проверка основной БД | `app.py` | `/health`, `/ready`, `/` | PostgreSQL в production | `PASS` local HTTP |
| Credential vault | Шифрованное tenant-хранилище credential payload | `credential_vault.py` | не подключено к текущему app API/UI | `ITP_CREDENTIAL_MASTER_KEY` | `EXPERIMENTAL`: unit PASS, runtime integration отсутствует |

## Интеграции

| Integration | Purpose | Entry point | Required dependencies | Required env/config | External executable | Browser | Profile | Scheduler | PostgreSQL | Local test | Result |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Kaspi | Каталог, свои цены, предложения, full sync | `engine/kaspi_search_compare_v8_2.py`, `engine/exact_offer_refresh.py`; `app.py` actions | Selenium, certifi | seller/city/zone в seller connection; optional `CHROMEDRIVER_PATH` | Chrome; ChromeDriver или Selenium Manager | да | `.runtime/browser_profiles/t<tenant>/kaspi/s<seller>`; `.kaspi_profile` legacy fallback | да, по тарифу | да через app storage | CLI, unit, seller/tenant/limit/atomic tests | `PASS` seller storage/isolation; `BLOCKED` live: нет нескольких подтверждённых сессий |
| Ozon.ru | Discovery, enrichment, prices, market matching, retry, export | `collectors/ozon/ozon_collector.py` | Selenium, selenium-stealth | source/seller connection; optional `OZON_CHROME_PATH` | Chrome и driver | да | один точно сопоставленный active seller сохраняет `collectors/ozon/chrome_vpn_profile`; остальные — `.runtime/browser_profiles/t<tenant>/ozon/s<seller>` | да, по тарифу | да, schema `ozon_ru`, включая seller-scoped registry paths | прямой `SELF_TEST.py`, 8 fixture-товаров; unit/CLI | `PASS` fixture/seller catalog/schema routing/legacy owner; live single-seller проверяется при deploy |
| Ozon.kz | Отдельный KZ storefront catalog/prices/full sync | `collectors/ozon_kz/ozon_kz_collector.py` | Selenium и общий Ozon browser runtime | source/seller; dynamic debug port; optional Chrome path | Chrome remote debugging с безопасным reuse `DevToolsActivePort` того же профиля | да | один точно сопоставленный active seller сохраняет `collectors/ozon/chrome_kz_profile`; остальные — `.runtime/browser_profiles/t<tenant>/ozon_kz/s<seller>` | да, по тарифу | да, schema `ozon_kz` | CLI, storage, source-boundary, legacy owner и active-profile reuse tests | `PASS` contract/seller path/profile reuse; live single-seller проверяется при deploy |
| Halyk Market | Публичный каталог и точные предложения | `collectors/halyk/halyk_collector.py` | certifi/stdlib HTTPS | seller, location, query/category в config/connection | optional `curl.exe` fallback | нет | нет | да, по тарифу | да через app storage | ограниченная публичная probe: 1 item, reported total 816; unit | `PASS` probe; partial cards дают exit 2 |
| Forte Market | Catalog/product/offer API и fallback сортировки | `collectors/forte/forte_collector.py` | Python HTTPS stack | seller/merchant/city/category в config/connection | нет | нет | нет | да, по тарифу | да через app storage | публичная probe: total 12 736 и sample offer; unit | `PASS` probe; partial cards дают exit 2 |
| Wildberries | Публичный seller catalog, цены и tenant-снимок | `collectors/wildberries/wildberries_collector.py` | Python HTTPS stack | seller ID, currency/destination | нет | нет | нет | да, по тарифу | да через app storage | seller `250000260`: total 4 296 и sample; unit | `PASS` probe; внешний API может меняться |

Live-пробы были только публичными и ограниченными по объёму. Они не изменяли каталог приложения. Полные Kaspi/Ozon проверки сознательно не запускались без профилей и авторизованных сессий.

## UI и backend

Публичная часть содержит регистрацию, вход, описание продукта и тарифы; `/api/public/plans` доступен и до первичной настройки пользователей. Регистрация использует единый стиль полей и нижний проводник по пяти блокам. Рабочая панель показывает обзор, товары, операции, отчёты, настройки, подписку, уведомления, персональное состояние Telegram и подключаемую контекстную справку для каждого раздела согласно эффективным правам. Платформенный интерфейс отделён от company-admin и включает рассмотрение компаний, выдачу площадок, правила source URL и подписки.

Backend предоставляет отдельные проверки прав на маршрутах и не полагается на скрытие кнопок. Каталог и операции поддерживают server-side фильтрацию и пагинацию. Интерфейс отменяет устаревшие поисковые запросы, сохраняет последние успешно загруженные строки во время обновления и показывает skeleton/retry состояния. API добавляет `Server-Timing`; запросы дольше двух секунд записываются как `slow_request` без query string и секретов. Health-контракты разделены: `/health` проверяет процесс, `/ready` — доступность основной БД, `/` — реальный HTTP/UI путь.

## Что подтверждено и что остаётся ограничением

Подтверждено: 155 автоматических тестов, Python compile, JavaScript syntax checks, PowerShell parse, согласованность Python-пакетов, read-only runtime diagnostic без `FAIL` и локальный HTTP 200 для `/health`, `/ready`, `/`, `/register`, `/api/public/plans` и новых static assets. Отдельно покрыты Telegram login/delivery/isolation, параллельные catalog reads, seller-selector cardinality, seller-scoped PostgreSQL routing Ozon.ru, точная source-bound привязка одного legacy-профиля и очереди товаров, priced `webOutOfStock`, ненулевой exit code частичного Ozon.kz, KZT-нормализация общего KZ registry и tenant-каталога, перенос цены/наличия по storefront slug и числовому seller ID, безопасная non-interactive остановка production PID, повторное подключение к Chrome-профилю Ozon.kz, отмена устаревших UI-запросов, равные минимальные цены, tenant-scoped остатки, отсутствие двойного подсчёта, match confirm/reject, RBAC и direct API marketplace boundary.

Read-only соединение с production PostgreSQL и каталог tenant на 15 825 строк проверены: прогретая страница сократилась с 1.100 до 0.262 секунды, а четыре одновременных холодных чтения — с 3.911 до 1.973 секунды. Не подтверждены live-сессии нескольких Kaspi/Ozon seller, полный сетевой сбор и конкурентный write/deadlock stress. Это внешние эксплуатационные проверки, а не подтверждённые дефекты кода.

Экспериментальными/операционно хрупкими следует считать browser automation Ozon/Kaspi, внешний API fallback и standalone `CredentialVault` до его подключения к runtime. Изменения HTML, антибот-защиты или публичных API маркетплейсов могут потребовать адаптации сборщиков.

## Secure password recovery

Password recovery uses a time-limited single-use email token. Only the token
hash is persisted. The reset form requires the new password twice.

A successful reset consumes the token atomically, replaces the password hash
and increments session_version so previously issued sessions become invalid.

Production schema changes are tracked by checksum and safe additive migrations
can be applied automatically during deployment after a verified backup.
