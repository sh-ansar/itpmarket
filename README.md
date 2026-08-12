# Spyon

Локальная многопользовательская панель для каталогов и цен маркетплейсов. Поддерживаются Kaspi, Ozon.ru, Ozon.kz, Halyk Market, Forte Market и Wildberries.

## Запуск

1. Выполните `INSTALL.bat` при первой установке.
2. Проверьте окружение через `CHECK_ENV.bat`.
3. Запустите приложение командой `START.bat`.
4. Откройте `http://127.0.0.1:8765`.

`cryptography` входит в `requirements.txt` и требуется для защищённого хранилища реквизитов интеграций.

## Проверки

```powershell
python -m unittest discover -s tests -p "test_*.py"
node --check static/js/app.js
```

Интеграционные и нагрузочные сценарии находятся в `tests/` и `tools/`. Устаревшие версионные self-test и hotfix-копии в корне проекта больше не используются.

## Структура

- `app.py` — HTTP API и маршруты интерфейса.
- `auth_service.py`, `tenant_security.py` — пользователи, роли и права.
- `saas_service.py`, `marketplace_registry.py`, `marketplace_source_rules.py` — компании и подключения площадок.
- `catalog_configuration_service.py` — tenant-изолированные каталоги, характеристики и фильтры.
- `collectors/` — сборщики отдельных маркетплейсов.
- `engine/` — фоновые операции, аналитика и отчёты.
- `templates/`, `static/` — интерфейс.
- `tests/` — актуальные автоматические тесты.

Доступ сотрудника к площадке вычисляется как пересечение доступа компании, активного подключения и персонального разрешения. Все tenant-запросы дополнительно проверяются сервером.
