from __future__ import annotations

import json
import secrets
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
LOCAL_CONFIG_PATH = ROOT / "config.local.json"
SECRET_PATH = ROOT / "data" / ".session_secret"

DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
        "host": "127.0.0.1",
        "port": 8765,
        "open_browser": True,
        "session_hours": 12,
        "max_parallel_tasks": 3,
        "product_page_size": 30,
    },
    "kaspi": {
        "seller_id": "Unityre",
        "seller_name": "Unityre",
        "city_id": "750000000",
        "zone_id": "Magnum_ZONE1",
        "expected_count": 0,
        "headless": False,
        "timeout_seconds": 50,
        "retries": 3,
        "min_delay": 1.0,
        "max_delay": 2.2,
    },
    "analysis": {
        "discover_workers": 2,
        "price_workers": 2,
        "search_pages": 2,
        "validate_top": 5,
        "search_cache_days": 14,
        "detail_cache_days": 30,
    },
    "paths": {
        "database": "data/unityre_kaspi.db",
        "profile": ".kaspi_profile",
        "output": "output",
        "logs": "logs",
        "backups": "backups",
    },
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_config() -> dict[str, Any]:
    base = _merge(DEFAULT_CONFIG, _read_json(CONFIG_PATH))
    return _merge(base, _read_json(LOCAL_CONFIG_PATH))


def save_config(config: dict[str, Any]) -> None:
    """Persist runtime/admin settings outside the Git-tracked base config."""
    LOCAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = LOCAL_CONFIG_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(LOCAL_CONFIG_PATH)


def resolve_path(config: dict[str, Any], key: str) -> Path:
    raw = Path(str(config["paths"][key]))
    return raw if raw.is_absolute() else ROOT / raw


def ensure_directories(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    for key in ("database", "profile", "output", "logs", "backups"):
        path = resolve_path(cfg, key)
        if key == "database":
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir(parents=True, exist_ok=True)
    return cfg


def get_secret_key() -> str:
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SECRET_PATH.exists():
        value = SECRET_PATH.read_text(encoding="utf-8").strip()
        if len(value) >= 32:
            return value
    value = secrets.token_urlsafe(48)
    SECRET_PATH.write_text(value, encoding="utf-8")
    return value


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(config)
