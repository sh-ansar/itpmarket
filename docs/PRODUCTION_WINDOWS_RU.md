# Production-развёртывание Spyon на Windows

Старый архив `spyon.rar` содержит PHP-сайт, а не переносимое серверное
окружение. Из него используется только подтверждённый адрес `spyon.kz`.
Старый исходный код, PHP-конфигурация и реквизиты базы в новый проект не
копируются.

## Архитектура

```text
Интернет -> роутер TCP 80/443 -> Caddy -> 127.0.0.1:8765 -> Waitress -> PostgreSQL
```

Waitress обязан слушать только loopback. Caddy завершает TLS, автоматически
обновляет сертификат и проверяет `/ready`. PostgreSQL не публикуется в интернет.

## Чистая установка

```powershell
git clone --branch feature/admin-subscriptions-notifications `
  https://github.com/sh-ansar/itpmarket.git C:\Users\Admin\spyon-production
cd C:\Users\Admin\spyon-production

$databaseUrl = 'postgresql://spyon:URL_ENCODED_PASSWORD@127.0.0.1:55433/spyon'
.\deploy\windows\install-production.ps1 -DatabaseUrl $databaseUrl
.\deploy\windows\install-caddy.ps1
```

Настоящие секреты записываются только в игнорируемый файл
`.runtime/production.env`. Команды запуска:

```powershell
.\deploy\windows\start-production.ps1
.\deploy\windows\start-caddy.ps1
```

Останавливать нужно проверенными скриптами:

```powershell
.\deploy\windows\stop-caddy.ps1
.\deploy\windows\stop-production.ps1
```

## Проверка до переключения DNS

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/ready
```

Обе проверки должны вернуть `ok: true`. Затем следует проверить вход,
админку, запуск тестовой операции и получение уведомления.

## Переключение `spyon.kz`

На момент подготовки домен указывает на старый сервер `85.159.27.17`, а
публичный адрес новой машины — `85.159.27.24`; локальный адрес машины —
`192.168.1.69`. Перед переключением:

1. Закрепить `192.168.1.69` за этой машиной в DHCP и убедиться, что
   `85.159.27.24` является статическим публичным адресом.
2. На роутере направить TCP 80 и 443 на `192.168.1.69`.
3. Разрешить входящие TCP 80/443 в Windows Firewall только для Caddy.
4. Уменьшить TTL A-записей до 300 секунд.
5. Изменить A-записи `@` и `www` с `85.159.27.17` на `85.159.27.24`.
6. Запустить Caddy и дождаться успешной выдачи сертификата.
7. Из внешней сети проверить `https://spyon.kz/ready`, вход и операции.

Старый Apache выключается только после успешной внешней проверки. Безопасный
вариант — сначала остановить старый virtual host через панель хостинга, но не
удалять файлы и базу. Для отката нужно снова включить старый virtual host и
вернуть A-записи на `85.159.27.17`.

## Обновление

```powershell
.\deploy\windows\stop-production.ps1
git fetch origin
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\deploy\windows\start-production.ps1
```

Перед каждым обновлением требуется резервная копия PostgreSQL. Нельзя
одновременно держать два экземпляра планировщика на одной базе: у проверочного
экземпляра задаётся `ITP_DISABLE_SCHEDULER=1`.
