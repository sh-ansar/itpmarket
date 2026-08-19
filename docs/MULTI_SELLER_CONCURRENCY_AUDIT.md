# Multi-user / Multi-seller / Concurrency Audit

Дата аудита: 18 августа 2026 года. Документ обновлён после production-read проверки каталога.

## Итог

Приложение теперь имеет явную единицу изоляции `tenant + marketplace + tenant_seller_id`. Каталог продавца, price/offer snapshots Kaspi, credentials, browser profile, runtime artifacts, schedule, job resource и контекст лога привязаны к этой единице. Разные продавцы могут выполняться параллельно; две конфликтующие операции одного продавца отклоняются с сообщением `seller already has active job`.

Автоматические concurrency/regression проверки проходят. Read-only catalog path дополнительно измерен на настоящем production PostgreSQL; live multi-seller запуск Kaspi/Ozon по-прежнему не выполнялся, потому что для него нужны несколько отдельных авторизованных профилей. Поэтому результаты PostgreSQL-чтения не заменяют live-проверку marketplace runtime и конкурентных записей.

## Найденные архитектурные дефекты и исправления

| Дефект / root cause | Риск до исправления | Исправление | Regression evidence |
| --- | --- | --- | --- |
| `tenant_integrations` имел `UNIQUE(tenant_id,integration_code)` и использовался как единственный runtime seller | Второй seller того же marketplace перезаписывал первый | `tenant_marketplace_sellers` стал каноническим списком аккаунтов; все task/schedule/catalog paths получают `tenant_seller_id` | same marketplace/same SKU и acceptance topology tests |
| Legacy каталог имел ключ без seller | Одинаковый SKU Seller B заменял запись Seller A | Добавлен `tenant_seller_catalog_products` с PK `(tenant_id, marketplace_code, tenant_seller_id, source_product_code)`; legacy aggregate сохранён только для совместимости | two sellers same marketplace test |
| Цена/предложения Kaspi записывались без seller scope | История нескольких продавцов могла смешиваться | Добавлены seller-scoped price, scan и offer snapshot tables | Kaspi offer snapshot test |
| Marketplace lock был глобальным | Долгая операция одного seller блокировала других | Resource key теперь `seller:<tenant>:<marketplace>:<seller>`; блокировка только на выбранного seller | six concurrent sellers test |
| Task state защищался только внутри одного процесса | Два app workers могли одновременно принять конфликтующие jobs и перезаписать state | Cross-process lock, unique temp files, owner/process identity и atomic persistent state update | two TaskManager instances test |
| Browser/runtime paths были общими | Cookies, downloads и artifacts могли пересекаться | `.runtime/browser_profiles/t<tenant>/<marketplace>/s<seller>` и аналогичный seller runtime tree | runtime path isolation test |
| Schedule/job identity не содержала seller | Scheduler мог запустить не тот аккаунт или конфликтовать с ручной операцией | `tenant_seller_id` в schedule/run, повторная ownership-проверка и общий seller resource lock | schedule claim и scheduler/manual tests |
| Capacity check выполнялся до write transaction | Два sellers могли одновременно пройти проверку по старому count | PostgreSQL tenant row lock / SQLite `BEGIN IMMEDIATE`; count и check внутри serialized transaction | concurrent capacity test |
| PostgreSQL pool ограничивал только idle connections | Число active connections было фактически неограниченным | Bounded checkout semaphore с timeout и гарантированным release | bounded pool mock test |
| После restart job мог остаться `running` навсегда | Блокировка следующих запусков и неверный UI status | Проверка PID + process creation time; мёртвый subprocess нормализуется в `interrupted`, живой orphan сохраняется | restart recovery test |
| Ozon run ID имел точность до секунды | Одновременные запуски могли получить одинаковый ID | Microseconds + UUID | 1000-ID collision test |

## Модель данных и транзакций

- Seller является внутренней записью с числовым `tenant_seller_id`. Внешние `shop_id`, `merchant_id` и seller names не считаются глобально уникальными и не используются без tenant filter.
- Публичный product code включает marketplace и внутренний seller: `<marketplace>:s<tenant_seller_id>:<source_product_code>`.
- Seller catalog replacement деактивирует только строки выбранного seller. Лимит подписки считается на tenant + marketplace суммарно по всем sellers, в соответствии с существующей бизнес-моделью тарифа.
- PostgreSQL catalog replacement сериализуется row lock по tenant; SQLite использует immediate write transaction. Ошибка приводит к rollback всей операции выбранного seller.
- `tenant_integrations` и `tenant_catalog_products` пока сохраняются как compatibility aggregate для single-seller/legacy code. Они не являются источником seller identity.

Миграция [20260818_multi_seller_v1.sql](../migrations/20260818_multi_seller_v1.sql) additive: создаёт seller-scoped tables/columns/indexes, выполняет однозначный backfill и не содержит `DROP`/`DELETE`. Она применена в production 18 августа 2026 года после verified backup и staging rehearsal.

## Авторизация и IDOR

Роли из кода: platform `superadmin`; tenant roles `admin`, `operator`, `viewer`. Pending/rejected tenant, company marketplace grant, user marketplace override и action permission проверяются backend-ом, а не только скрытием UI. Прямой task API разрешает seller только если он принадлежит текущему tenant и выбранному marketplace; подстановка seller другого tenant и некорректный seller ID возвращают отказ. Job/catalog/schedule/report endpoints сохраняют tenant и marketplace filters.

`CredentialVault` теперь умеет хранить seller-scoped payload и проверяет tenant/marketplace ownership. Однако vault всё ещё не подключён к штатному HTTP/runtime потоку browser collectors: это отдельное ограничение, а browser login остается машинным profile state.

## Browser и runtime isolation

Канонические пути:

```text
.runtime/
  browser_profiles/t<tenant>/<marketplace>/s<seller>/
  marketplaces/t<tenant>/<marketplace>/s<seller>/
    registry/ runs/ reports/ exports/ raw/
```

Kaspi получает отдельный profile path на seller. Для обновлённой установки один активный seller Ozon.ru или Ozon.kz во всех компаниях может сохранить соответствующий непустой collector-local Chrome profile только после точного сопоставления его source URL с открытой seller-вкладкой. Несекретный owner marker повторно проверяется по актуальному source URL; каждый другой seller получает отдельный seller-scoped profile path. Пустой, неоднозначный или недоступный legacy-профиль не используется. Halyk, Forte и Wildberries не используют browser profile; их seller result staging/materialization также получает отдельный runtime/app scope.

## Jobs, failure isolation и observability

Каждый job запускается отдельным subprocess. Persistent task state обновляется под межпроцессным lock. В первой строке job log пишется JSON `JOB_CONTEXT` с timestamp, job ID, tenant, seller, marketplace и operation; имя log также содержит tenant/seller. Exit code одного job изменяет только его собственный status. Concurrency limit по умолчанию равен 6, административная граница — 12.

Проверены deterministic subprocess scenarios: шесть разных seller jobs работают одновременно, седьмой отклоняется capacity limit, повторный запуск одного seller отклоняется, один job завершается non-zero без изменения остальных. Это доказывает process/job isolation, но не заменяет live marketplace semantics для `AUTH_REQUIRED`, 401/403/429, TLS и marketplace-specific retry.

## Performance baseline

Локальный baseline измеряет параллельную seller-scoped materialization по 50 товаров на seller в отдельной временной SQLite DB. Это не browser stress test.

| Sellers | Duration, s | CPU, s | RSS delta, MB | Peak DB workers | Errors | Rows |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.043 | 0.016 | 0.09 | 1 | 0 | 50 |
| 2 | 0.157 | 0.062 | 0.39 | 2 | 0 | 100 |
| 4 | 0.200 | 0.109 | -0.12 | 4 | 0 | 200 |
| 6 | 0.287 | 0.359 | -0.21 | 6 | 0 | 300 |

RSS delta на таком коротком процессе шумный (включая отрицательные значения из-за сборки памяти), поэтому по нему нельзя делать вывод о browser memory leak. Ошибок и незавершённых workers не было. Реальный baseline Chrome + PostgreSQL остаётся `BLOCKED`.

Отдельно выполнен безопасный read-only benchmark production PostgreSQL на tenant с 15 825 seller-scoped строками. Production-код во время замера не менялся; версия с исправлением загружалась из staging-копии модулей.

| Catalog read | До, s | После, s | Изменение |
| --- | ---: | ---: | ---: |
| Один прогретый products page | 1.100 | 0.262 | −76% |
| 1 одновременный холодный запрос | 1.006 | 1.031 | в пределах шума |
| 2 одновременных холодных запроса | 2.025 | 1.342 | −34% |
| 4 одновременных холодных запроса | 3.911 | 1.973 | −50% |

Ускорение получено за счёт двухсекундного tenant snapshot и single-flight одинаковых tenant/scope запросов. Разные tenants используют разные locks. Legacy snapshot после истечения TTL обновляется в фоне, а HTTP получает последний полностью собранный снимок. Product detail повторно использует уже загруженные строки, а CPU matching больше не удерживает соединение из ограниченного PostgreSQL pool.

## Startup prerequisites

- Python virtual environment и зависимости из lock/requirements; `pip check` должен проходить.
- В production — PostgreSQL URL/schema и выполненная контролируемая миграция; локально поддерживается SQLite.
- Chrome/Chromium и совместимый ChromeDriver/Selenium Manager для Kaspi/Ozon; Ozon.kz использует remote-debugging session.
- Отдельная авторизованная browser profile directory для каждого seller, если marketplace требует browser authentication. Первичный интерактивный login может быть неизбежен из-за политики marketplace.
- Writable `.runtime`, `logs`, `output`, app data and task-state paths; приложение безопасно создаёт seller runtime directories.
- CA certificates/certifi и network access к marketplace endpoints; optional external Chrome path и Forte `curl.exe` fallback.
- Scheduler работает внутри приложения, если не установлен `ITP_DISABLE_SCHEDULER=1`; collectors не надо запускать вручную.
- `/health` означает, что Flask process жив; `/ready` дополнительно проверяет основную DB. Отказ отдельного marketplace не делает всё приложение unhealthy.

## Functional limitations

- Seller catalog и Kaspi price/offer analytics полностью seller-scoped в реализованном storage path.
- При нескольких активных sellers Ozon/Halyk/Forte UI получает безопасные seller catalog snapshots, но legacy detailed offer enrichment намеренно не присоединяется, чтобы не смешать sellers. Детальная multi-seller аналитика этих marketplace — `PARTIAL`.
- Real credentials, session expiry, live retries, rate limits и marketplace response variants для 2–4 sellers не проверены.
- Live PostgreSQL deadlock/serialization/exhaustion testing не выполнено; bounded pool и transaction SQL проверены unit/mocks.

## Acceptance matrix

`PASS` означает выполненную локальную automated/integration проверку. `PARTIAL` означает, что безопасная архитектурная/mock часть прошла, но live составляющая не проверена. `BLOCKED` означает отсутствие обязательного внешнего ресурса.

| TEST | RESULT | DETAILS |
| --- | --- | --- |
| Single Kaspi seller | PARTIAL | Single-seller unit/CLI storage path PASS; live авторизованной Kaspi session нет. |
| 2 parallel Kaspi sellers | PARTIAL | Seller-scoped parallel jobs/catalogs, одинаковый SKU и Kaspi offer snapshots PASS; два live профиля отсутствуют. |
| 4 parallel Kaspi sellers | PARTIAL | Four/six seller subprocess and catalog topology PASS; четыре live Kaspi профиля отсутствуют. |
| Kaspi Full Sync + Price Actualization | PASS | Выбран безопасный вариант C: вторая операция того же seller отклоняется `seller already has active job`; повреждение общей session невозможно. |
| Two tenants Kaspi | PARTIAL | Same external seller ID, catalog and direct API tenant isolation PASS; live Kaspi run не выполнен. |
| Kaspi + Ozon parallel | PARTIAL | Разные seller resources/runtime paths выполняются параллельно в orchestration test; live sessions отсутствуют. |
| Kaspi + Halyk parallel | PARTIAL | Cross-marketplace resources и isolated subprocess states PASS; live parallel collection не выполнялся. |
| Kaspi + Ozon + Halyk parallel | PARTIAL | Acceptance topology A1/A2/A3/B1/B2 и six-job failure isolation PASS; live marketplace data blocked. |
| Expired authentication isolation | PARTIAL | Non-zero subprocess одного seller не меняет остальные jobs; live `AUTH_REQUIRED` без просроченного профиля не проверен. |
| Network failure isolation | PARTIAL | Independent failed subprocess/final status PASS; полный 401/403/429/TLS matrix live не проверен. |
| Duplicate job protection | PASS | Один seller resource имеет single active job даже между двумя TaskManager instances. |
| Application restart recovery | PASS | Dead child становится `interrupted`; живой orphan не маркируется ошибочно, следующий запуск разблокирован. |
| Scheduler + manual job | PASS | Оба используют один seller resource; второй конфликтующий запуск отклоняется. |
| Subscription concurrency | PASS | Два синхронных writers не превышают tenant+marketplace capacity; один получает контролируемый отказ. |
| Cross-tenant access | PASS | Direct seller IDOR, seller list, credentials, catalogs и одинаковые external IDs изолированы по tenant. |
| Browser profile isolation | PASS | Deterministic path test доказывает уникальность tenant/marketplace/seller profiles; live cookies не инспектировались. |
| PostgreSQL concurrency | PARTIAL | Read-only каталог измерен на production PostgreSQL, hard connection bound и transaction/locking SQL покрыты; конкурентный write/deadlock stress не выполнялся. |
| UI availability during sync | PASS | Collectors запускаются subprocess-ами вне request thread; local HTTP smoke сохраняет ответы health/ready/UI/API. |

## Команды проверки

```powershell
python -m unittest discover -v
python -m unittest -v tests.test_multi_seller_concurrency
python -m compileall -q .
node --check static/js/app.js
pip check
git diff --check
```
