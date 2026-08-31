"""Read-only Telegram production diagnostic; never prints secret values."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_config, resolve_path  # noqa: E402
from storage.postgres_compat import configure_connection, connect_database  # noqa: E402
from telegram_bot import TelegramBotApi  # noqa: E402


def enabled(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().casefold() in {"1", "true", "yes", "on"}


def main() -> int:
    is_enabled = enabled("ITP_TELEGRAM_BOT_ENABLED")
    token = str(os.environ.get("ITP_TELEGRAM_BOT_TOKEN") or "").strip()
    username = str(os.environ.get("ITP_TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
    public_url = str(os.environ.get("SPYON_PUBLIC_URL") or "").strip()
    status: dict[str, object] = {
        "enabled": is_enabled,
        "configured": bool(token and username),
        "token_present": bool(token),
        "bot_api": "SKIPPED",
        "bot_username": username or None,
        "database_schema": "FAIL",
        "link_table": "FAIL",
        "public_url": "OK" if urlparse(public_url).scheme in {"http", "https"} and urlparse(public_url).netloc else "FAIL",
    }
    try:
        db_path = resolve_path(load_config(), "database")
        conn = configure_connection(connect_database(db_path, timeout=10), foreign_keys=True, busy_timeout=10000)
        try:
            status["database_schema"] = "OK"
            try:
                conn.execute("SELECT 1 FROM telegram_link_tokens LIMIT 1").fetchone()
                status["link_table"] = "OK"
            except Exception:
                status["link_table"] = "FAIL"
        finally:
            conn.close()
    except Exception:
        pass
    if is_enabled:
        if not token:
            status["bot_api"] = "FAIL"
        else:
            try:
                identity = TelegramBotApi(token).get_me()
                status["bot_api"] = "OK"
                status["bot_username"] = str(identity.get("username") or username or "") or None
            except Exception:
                status["bot_api"] = "FAIL"
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    if not is_enabled:
        return 0
    return 0 if all(status[key] == "OK" for key in ("bot_api", "database_schema", "link_table", "public_url")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
