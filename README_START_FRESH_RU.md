# ITP Market Intelligence 3.2.0 — чистый запуск

## Почему старый INSTALL завершился ошибкой

Старая версия пыталась удалить `.venv`, пока `python.exe`, Playwright `node.exe`
или greenlet ещё использовались процессом. Windows вернул «Отказано в доступе»,
после чего установщик продолжил использовать уже повреждённую `.venv`.

Версия 3.2.0 не удаляет и не использует старую `.venv`.
Новое изолированное окружение создаётся здесь:

```text
.runtime\venv_3_2_0
```

## Что перенести из старого проекта

Обязательно:

```text
data\unityre_kaspi.db
```

Для Kaspi:

```text
.kaspi_profile
```

Для Ozon:

```text
collectors\ozon\data\ozon_registry.db
collectors\ozon\chrome_vpn_profile
```

Не переносить:

```text
.venv
.runtime
.git
logs
output
__pycache__
```

Для автоматического переноса запустите:

```text
MIGRATE_FROM_OLD_PROJECT.bat
```

и укажите путь к старой папке.

## Локальный запуск

```text
INSTALL.bat
MIGRATE_FROM_OLD_PROJECT.bat
VERIFY_INSTALL.bat
SELF_TEST_MVP.bat
START.bat
```

## Сервер 192.168.1.75

Распаковать проект в:

```text
C:\ITPMarket\app
```

Затем:

```text
INSTALL.bat
MIGRATE_FROM_OLD_PROJECT.bat
SERVER_SETUP_192_168_1_75.bat
REGISTER_SERVER_STARTUP.bat
START_SERVER.bat
CHECK_SERVER.bat
```

Панель:

```text
http://192.168.1.75:8765
```
