# Ozon.kz — отдельный публичный сборщик

Ozon.kz работает независимо от российского Ozon: используется внутренний код
`ozon_kz`, префикс товара `ozon_kz:`, отдельная SQLite-база, профиль браузера,
порт отладки и таблицы товаров, предложений и истории цен. Cookies и данные
Ozon.ru не переиспользуются.

Пользователь указывает seller URL вида `https://ozon.kz/seller/ridial/`.
После подтверждения площадки сборщик получает публичную витрину продавца,
обогащает карточки характеристиками и сохраняет только каталог этой компании.

Основные команды:

```powershell
python collectors/ozon_kz/ozon_kz_collector.py sync-catalog --source-url https://ozon.kz/seller/ridial/
python collectors/ozon_kz/ozon_kz_collector.py refresh-prices --source-url https://ozon.kz/seller/ridial/
python collectors/ozon_kz/ozon_kz_collector.py full-sync --source-url https://ozon.kz/seller/ridial/
```

Проверка домена и состояния отдельного реестра:

```powershell
python -m collectors.ozon_kz.ozon_kz_connector validate-source --source-url https://ozon.kz/seller/ridial/
python -m collectors.ozon_kz.ozon_kz_connector status
```

Российский Ozon по-прежнему запускается через
`collectors/ozon/ozon_collector.py`; этот файл его поведение не меняет.
