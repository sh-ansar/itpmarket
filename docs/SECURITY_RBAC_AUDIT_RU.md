# Аудит RBAC, tenants и секретов

## Модель доступа

Решение принимается по четырём независимым признакам:

1. активная учётная запись;
2. членство в tenant и tenant role (`admin`, `operator`, `viewer`);
3. фактическое состояние подключения tenant (`active`);
4. разрешение площадки компании в `tenant_marketplace_access`.

Отсутствующая или повреждённая карта площадок теперь означает отсутствие
доступа (fail closed). Внутренние коды стабильны: `ozon` означает Ozon.ru,
`ozon_kz` — Ozon.kz.

Проверки выполняются на backend для товаров, массового состояния, задач,
логов, расписаний, отчётов и глобальных настроек. Frontend только скрывает
недоступные элементы и не считается границей безопасности.

## Закрытые обходы

- task list/log теперь фильтруются по tenant и marketplace;
- executable command и пути log-файлов не возвращаются через API;
- прямой запуск задачи и `stop_by_product` проверяют marketplace товара;
- расписание проверяется при создании/изменении/удалении и повторно в момент
  фонового запуска (включая деактивацию пользователя и смену роли);
- отчёты содержат `tenant_id` и список площадок; legacy-отчёт без scope виден
  только автору или пользователю с полным legacy-доступом;
- export формирует строки только по разрешённым площадкам;
- заметки/наблюдение/приоритет/план продаж перенесены в
  `tenant_product_state` и не попадают в общий cache;
- tenant admin больше не читает и не меняет глобальные collector settings;
- автоматическая legacy-миграция больше не добавляет всех пользователей в
  default tenant; ошибочная поздняя membership деактивируется без удаления.

## Временное ограничение shared catalog

Текущие таблицы Kaspi, Ozon.ru, Halyk Market и Forte Market исторически не
содержат `tenant_id`. До завершения PostgreSQL/repository migration каталог и
collector operations доступны только первому (владельцу legacy dataset)
tenant. Другие tenants получают пустой каталог и пустую карту marketplace.
Это намеренный deny-by-default: показывать им общий каталог было бы утечкой.

## Секреты

- пароли и recovery codes остаются one-way scrypt hashes;
- обратимо расшифровываемые API credentials сохраняются только через
  `CredentialVault` (Fernet ciphertext);
- master key берётся из `ITP_CREDENTIAL_MASTER_KEY` и не хранится в БД;
- production session key обязан поступать из `ITP_SESSION_SECRET`;
- публичная конфигурация и audit details рекурсивно очищаются от secret/token
  fields;
- task API не отдаёт команды, а log API маскирует credential-like значения;
- token-поля записанных Ozon.ru fixtures заменены маркером redacted.

Credential vault пока не подключён к Ozon.kz: источник и способ авторизации
ещё не предоставлены. В таблице Ozon.kz допускается только `credential_ref`.
