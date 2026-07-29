from __future__ import annotations

import atexit
import json
import os
import re
import secrets
import socket
import ipaddress
import sqlite3
import sys
import threading
import time
import webbrowser
from datetime import timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from waitress import serve
import psutil

from auth_service import AuthService
from config import (
    ROOT,
    ensure_directories,
    get_secret_key,
    load_config,
    public_config,
    resolve_path,
    save_config,
)
from data_service import DataService
from schema import ensure_database
from task_manager import TaskManager
from saas_service import SaaSService, INTEGRATION_CATALOG, SCHEDULE_ACTIONS
from scheduler_service import SchedulerService
from public_product_service import PublicProductService, PUBLIC_CAPABILITIES

VERSION = "3.4.7"
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = get_secret_key()

CFG = ensure_directories(load_config())
DB_PATH = resolve_path(CFG, "database")
ensure_database(DB_PATH)
AUTH = AuthService(DB_PATH)
SAAS = SaaSService(DB_PATH)
PUBLIC = PublicProductService(DB_PATH)
DATA = DataService(
    DB_PATH,
    str(CFG["kaspi"]["seller_name"]),
    ROOT / "collectors" / "ozon" / "data" / "ozon_registry.db",
    seller_id=str(CFG["kaspi"]["seller_id"]),
    halyk_seller_name=str(CFG["halyk"]["seller_name"]),
)
TASKS = TaskManager(
    ROOT,
    resolve_path(CFG, "logs"),
    ROOT / "data" / "tasks_state.json",
    max_parallel=int(CFG["app"].get("max_parallel_tasks", 3)),
)
PID_PATH = ROOT / "data" / "server.pid"


def warm_data_cache() -> None:
    try:
        DATA.rows()
    except Exception:
        pass


threading.Thread(target=warm_data_cache, daemon=True).start()

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=str(os.environ.get("ITP_COOKIE_SECURE", "0")).strip() == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=int(CFG["app"].get("session_hours", 12))),
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
)

LOGIN_ATTEMPTS: dict[str, list[float]] = {}
FORM_ATTEMPTS: dict[str, list[float]] = {}
LOGIN_LOCK_SECONDS = 15 * 60
LOGIN_WINDOW_SECONDS = 10 * 60
LOGIN_MAX_ATTEMPTS = 6
RECOVERY_MAX_ATTEMPTS = 5
REGISTRATION_MAX_ATTEMPTS = 8
CODE_RE = re.compile(r"^[A-Za-z0-9:_-]{1,96}$")

ACTION_INFO = {
    "sync_catalog": {
        "label": "Синхронизация каталога",
        "resource": ["kaspi_browser"],
        "roles": {"admin", "operator"},
    },
    "update_own_prices": {
        "label": "Обновление собственных цен Kaspi",
        "resource": ["kaspi_browser"],
        "roles": {"admin", "operator"},
    },
    "scan_market": {
        "label": "Точные предложения продавцов Kaspi",
        "resource": ["kaspi_browser"],
        "roles": {"admin", "operator"},
    },
    "refresh_market": {
        "label": "Обновление точных цен продавцов",
        "resource": ["kaspi_browser"],
        "roles": {"admin", "operator"},
    },
    "retry_errors": {
        "label": "Повтор ошибок точных карточек",
        "resource": ["kaspi_browser"],
        "roles": {"admin", "operator"},
    },
    "export_report": {
        "label": "Формирование отчёта",
        "resource": ["reports"],
        "roles": {"admin", "operator"},
    },
    "audit_catalog": {
        "label": "Аудит каталога",
        "resource": ["reports"],
        "roles": {"admin", "operator"},
    },
    "backup_database": {
        "label": "Резервное копирование",
        "resource": ["backups"],
        "roles": {"admin"},
        "platform": "system",
    },
    "ozon_discover": {"label": "Ozon: обнаружение товаров", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_enrich": {"label": "Ozon: характеристики новых товаров", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_market_search": {"label": "Ozon: поиск рыночных предложений", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_refresh_prices": {"label": "Ozon: обновление цен", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_refresh_stale": {"label": "Ozon: обновление характеристик", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_retry": {"label": "Ozon: повтор ошибок", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_full_sync": {"label": "Ozon: полная синхронизация", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_export": {"label": "Ozon: экспорт реестра", "resource": ["reports"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "halyk_sync_catalog": {"label": "Halyk Market: синхронизация каталога", "resource": ["halyk_api"], "roles": {"admin", "operator"}, "platform": "halyk_market"},
    "halyk_refresh_offers": {"label": "Halyk Market: точные предложения продавцов", "resource": ["halyk_api"], "roles": {"admin", "operator"}, "platform": "halyk_market"},
    "halyk_full_sync": {"label": "Halyk Market: полная синхронизация", "resource": ["halyk_api"], "roles": {"admin", "operator"}, "platform": "halyk_market"},
}


def now_epoch() -> float:
    return time.time()


def ensure_csrf() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return str(token)


def valid_csrf(value: str | None) -> bool:
    expected = str(session.get("csrf_token") or "")
    return bool(expected and value and secrets.compare_digest(expected, str(value)))


def clean_codes(raw: Any) -> list[str]:
    values: list[str] = []
    if isinstance(raw, list):
        source = raw
    elif isinstance(raw, str):
        source = raw.split(",")
    else:
        source = []
    for value in source:
        code = str(value or "").strip()
        if CODE_RE.fullmatch(code):
            values.append(code)
    return list(dict.fromkeys(values))[:5000]


def is_api_request() -> bool:
    return request.path.startswith("/api/")


def current_user() -> dict[str, Any] | None:
    return getattr(g, "user", None)


def is_superadmin(user: dict[str, Any] | None = None) -> bool:
    return (user or current_user() or {}).get("platform_role") == "superadmin"


def rate_limit_hit(scope: str, key: str, max_attempts: int, window_seconds: int) -> bool:
    now = now_epoch()
    bucket = f"{scope}:{request.remote_addr or 'local'}:{key.strip().casefold()[:120]}"
    attempts = [stamp for stamp in FORM_ATTEMPTS.get(bucket, []) if now - stamp < window_seconds]
    attempts.append(now)
    FORM_ATTEMPTS[bucket] = attempts
    return len(attempts) > max_attempts


def tenant_visibility_predicate(alias: str, user: dict[str, Any]) -> tuple[str, list[Any]]:
    if is_superadmin(user):
        return "1=1", []
    tenant_id = user.get("tenant_id")
    if tenant_id is None:
        return "0=1", []
    return (
        f"({alias}.tenant_id=? OR ({alias}.tenant_id IS NULL AND ?=(SELECT id FROM tenants ORDER BY id LIMIT 1)))",
        [int(tenant_id), int(tenant_id)],
    )


def visible_tasks(user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    value = user or current_user() or {}
    tasks = TASKS.states()
    if is_superadmin(value):
        return tasks
    tenant_id = value.get("tenant_id")
    user_id = value.get("id")
    result = []
    for task in tasks:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        task_tenant_id = metadata.get("tenant_id")
        if task_tenant_id is not None and tenant_id is not None and int(task_tenant_id) == int(tenant_id):
            result.append(task)
        elif task_tenant_id is None and user_id is not None and int(metadata.get("requested_by_id") or 0) == int(user_id):
            result.append(task)
    return result


def visible_task(task_id: str, user: dict[str, Any] | None = None) -> dict[str, Any] | None:
    return next((task for task in visible_tasks(user) if str(task.get("id")) == str(task_id)), None)


def schedule_actions_for_user(user: dict[str, Any]) -> list[dict[str, str]]:
    blocked = set()
    if not is_superadmin(user):
        blocked.add("backup_database")
    return [
        {"code": code, "platform": value[0], "name": value[1]}
        for code, value in SCHEDULE_ACTIONS.items()
        if code not in blocked
    ]


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not current_user():
            if is_api_request():
                return jsonify({"ok": False, "error": "Требуется авторизация."}), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def roles_required(*roles: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    allowed = set(roles)

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        @login_required
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            user = current_user() or {}
            if user.get("role") not in allowed:
                if is_api_request():
                    return jsonify({"ok": False, "error": "Недостаточно прав."}), 403
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def platform_roles_required(*roles: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    allowed = set(roles)
    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        @login_required
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            user = current_user() or {}
            if user.get("platform_role") not in allowed:
                if is_api_request():
                    return jsonify({"ok": False, "error": "Доступ к платформенной панели запрещён."}), 403
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def current_tenant() -> dict[str, Any] | None:
    user = current_user() or {}
    return SAAS.tenant_for_user(int(user["id"])) if user.get("id") and user.get("tenant_id") else None


def managed_user_or_error(user_id: int, actor: dict[str, Any]) -> dict[str, Any]:
    target = AUTH.get_user(int(user_id))
    if not target:
        raise LookupError("Пользователь не найден.")
    if is_superadmin(actor):
        return target
    actor_tenant_id = actor.get("tenant_id")
    target_tenant_id = target.get("tenant_id")
    if actor_tenant_id is None or target_tenant_id is None or int(actor_tenant_id) != int(target_tenant_id):
        raise PermissionError("Недостаточно прав для управления этим пользователем.")
    return target


def allowed_marketplaces(user: dict[str, Any] | None = None) -> set[str]:
    value = user or current_user() or {}
    access = value.get("marketplaces")
    if not isinstance(access, dict) or not access:
        return {"kaspi", "ozon", "halyk_market"}
    return {code for code, enabled in access.items() if bool(enabled)}


def requested_platform_filters() -> tuple[dict[str, Any], set[str]]:
    filters = product_filters_from_request()
    allowed = allowed_marketplaces()
    requested = str(filters.get("platform") or "").strip()
    visible = allowed & {"kaspi", "ozon", "halyk_market"}
    if requested and requested not in visible:
        raise PermissionError("Нет доступа к выбранной площадке.")
    if not requested:
        if len(visible) == 1:
            filters["platform"] = next(iter(visible))
        elif not visible:
            filters["platform"] = "__no_marketplace_access__"
    return filters, visible


def json_payload() -> dict[str, Any]:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def json_ok(**payload: Any) -> Any:
    return jsonify({"ok": True, **payload})


def json_error(message: str, status: int = 400) -> Any:
    return jsonify({"ok": False, "error": message}), status


def record_event(event_type: str, entity_type: str | None = None, entity_id: str | None = None, details: dict[str, Any] | None = None) -> None:
    user = current_user() or {}
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.execute(
            """
            INSERT INTO app_events(user_id,event_type,entity_type,entity_id,details_json,created_at,tenant_id)
            VALUES(?,?,?,?,?,datetime('now'),?)
            """,
            (
                user.get("id"), event_type, entity_type, entity_id,
                json.dumps(details or {}, ensure_ascii=False),
                user.get("tenant_id"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def reload_services() -> None:
    global CFG, DB_PATH, AUTH, DATA, TASKS
    CFG = ensure_directories(load_config())
    DB_PATH = resolve_path(CFG, "database")
    ensure_database(DB_PATH)
    AUTH = AuthService(DB_PATH)
    DATA = DataService(
        DB_PATH,
        str(CFG["kaspi"]["seller_name"]),
        ROOT / "collectors" / "ozon" / "data" / "ozon_registry.db",
        seller_id=str(CFG["kaspi"]["seller_id"]),
        halyk_seller_name=str(CFG["halyk"]["seller_name"]),
    )
    TASKS.max_parallel = max(1, int(CFG["app"].get("max_parallel_tasks", 3)))
    threading.Thread(target=warm_data_cache, daemon=True).start()


def _lines(path: Path) -> list[str]:
    try:
        if path.exists():
            result: list[str] = []
            for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                value = raw.strip()
                if value and not value.startswith("#") and value not in result:
                    result.append(value)
            return result
    except Exception:
        pass
    return []


def local_ipv4_addresses() -> list[str]:
    addresses: list[str] = []

    def add(value: str) -> None:
        try:
            ip = ipaddress.ip_address(str(value))
        except ValueError:
            return
        if ip.version != 4 or ip.is_loopback or ip.is_link_local or not ip.is_private:
            return
        text = str(ip)
        if text not in addresses:
            addresses.append(text)

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(1)
        probe.connect(("1.1.1.1", 80))
        add(str(probe.getsockname()[0]))
        probe.close()
    except Exception:
        pass

    try:
        for interface_addresses in psutil.net_if_addrs().values():
            for value in interface_addresses:
                if value.family == socket.AF_INET:
                    add(str(value.address))
    except Exception:
        pass

    try:
        for value in socket.gethostbyname_ex(socket.gethostname())[2]:
            add(value)
    except Exception:
        pass

    return addresses


def load_ozon_public_config() -> dict[str, Any]:
    root = ROOT / "collectors" / "ozon"
    db_path = root / "data" / "ozon_registry.db"
    urls = _lines(root / "START_URLS.txt") or _lines(root / "START_URL.txt")
    client_urls = [url for url in urls if "/seller/" in url.lower()]
    market_urls = [url for url in urls if url not in client_urls]
    result: dict[str, Any] = {
        "client_catalog_urls": "\n".join(client_urls),
        "market_category_urls": "\n".join(market_urls),
        # Backward-compatible fields used by older frontends.
        "seller_catalog_url": client_urls[0] if client_urls else "",
        "category_urls": "\n".join(market_urls),
        "start_urls": urls,
        "expected_seller": (_lines(root / "EXPECTED_SELLER.txt") or [""])[0],
        "current_seller_name": "",
        "current_seller_url": "",
        "current_products": 0,
        "current_market_products": 0,
        "current_offers": 0,
    }
    if not db_path.exists():
        return result
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        tables = {
            str(row[0]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "product_sources" in tables:
            row = conn.execute(
                """
                SELECT
                    COUNT(DISTINCT CASE WHEN source_type='CLIENT_CATALOG' THEN article END) AS client_count,
                    COUNT(DISTINCT CASE WHEN source_type='MARKET_CATEGORY' THEN article END) AS market_count
                FROM product_sources
                """
            ).fetchone()
            result["current_products"] = int(row["client_count"] or 0)
            result["current_market_products"] = int(row["market_count"] or 0)
        else:
            result["current_products"] = int(conn.execute(
                "SELECT COUNT(*) FROM products WHERE active=1 AND lower(discovery_url) LIKE '%/seller/%'"
            ).fetchone()[0] or 0)
            result["current_market_products"] = int(conn.execute(
                "SELECT COUNT(*) FROM products WHERE active=1 AND lower(discovery_url) NOT LIKE '%/seller/%'"
            ).fetchone()[0] or 0)

        row = conn.execute(
            """
            SELECT seller_name,seller_url,COUNT(DISTINCT article) AS product_count,
                   COUNT(*) AS offers_count
            FROM offers
            WHERE active=1
            GROUP BY seller_name,seller_url
            ORDER BY
                CASE WHEN lower(trim(seller_name))=lower(trim(?)) THEN 0 ELSE 1 END,
                product_count DESC,offers_count DESC
            LIMIT 1
            """,
            (result["expected_seller"],),
        ).fetchone()
        result["current_offers"] = int(conn.execute(
            "SELECT COUNT(*) FROM offers WHERE active=1"
        ).fetchone()[0] or 0)
        conn.close()
        if row:
            result["current_seller_name"] = str(row["seller_name"] or "")
            result["current_seller_url"] = str(row["seller_url"] or "")
    except Exception:
        pass
    return result


def ozon_seller_root_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    host = parsed.netloc.lower().split(":")[0]
    if host not in {"ozon.ru", "www.ozon.ru"}:
        return ""
    match = re.search(r"/seller/([^/?#]+)/?", parsed.path, re.IGNORECASE)
    if not match:
        return ""
    return urlunparse(("https", "www.ozon.ru", f"/seller/{match.group(1)}/", "", "", ""))


def save_ozon_public_config(payload: dict[str, Any]) -> None:
    root = ROOT / "collectors" / "ozon"
    root.mkdir(parents=True, exist_ok=True)

    def values(raw: Any) -> list[str]:
        if isinstance(raw, list):
            source = [str(value).strip() for value in raw]
        else:
            source = [value.strip() for value in str(raw or "").splitlines()]
        result: list[str] = []
        for value in source:
            if value and value not in result:
                result.append(value)
        return result

    client_urls = values(
        payload.get("client_catalog_urls")
        or payload.get("seller_catalog_url")
        or ""
    )
    market_urls = values(
        payload.get("market_category_urls")
        or payload.get("category_urls")
        or ""
    )
    normalized_client_urls: list[str] = []
    for url in client_urls:
        root_url = ozon_seller_root_url(url)
        if root_url and root_url not in normalized_client_urls:
            normalized_client_urls.append(root_url)
        if url not in normalized_client_urls:
            normalized_client_urls.append(url)
    urls = [*normalized_client_urls, *[url for url in market_urls if url not in normalized_client_urls]]
    if urls:
        (root / "START_URLS.txt").write_text("\n".join(urls) + "\n", encoding="utf-8")
        (root / "START_URL.txt").write_text(urls[0] + "\n", encoding="utf-8")
    if "expected_seller" in payload:
        (root / "EXPECTED_SELLER.txt").write_text(
            str(payload.get("expected_seller") or "").strip() + "\n",
            encoding="utf-8",
        )



def network_public_config() -> dict[str, Any]:
    port = int(os.environ.get("ITP_PORT") or CFG["app"]["port"])
    return {
        "port": port,
        "local_url": f"http://127.0.0.1:{port}",
        "lan_urls": [f"http://{address}:{port}" for address in local_ipv4_addresses()],
        "lan_enabled": str(os.environ.get("ITP_HOST") or CFG["app"]["host"]) in {"0.0.0.0", "::"},
    }


def py_command(script: Path, *args: str) -> list[str]:
    return [sys.executable, "-u", str(script), *map(str, args)]


def v9_base() -> list[str]:
    return py_command(
        ROOT / "engine" / "kaspi_market_v9_1.py",
        "--db", str(DB_PATH),
        "--output", str(resolve_path(CFG, "output")),
        "--profile", str(resolve_path(CFG, "profile")),
        "--seller-name", str(CFG["kaspi"]["seller_name"]),
    )


def browser_arguments(workers: int) -> list[str]:
    kaspi = CFG["kaspi"]
    result = [
        "--workers", str(max(1, workers)),
        "--timeout", str(kaspi["timeout_seconds"]),
        "--retries", str(kaspi["retries"]),
        "--city-id", str(kaspi["city_id"]),
        "--min-delay", str(kaspi["min_delay"]),
        "--max-delay", str(kaspi["max_delay"]),
    ]
    if kaspi.get("headless"):
        result.append("--headless")
    return result


def build_action_command(action: str, codes: list[str], user_id: int) -> list[str]:
    kaspi = CFG["kaspi"]
    analysis = CFG["analysis"]
    codes_text = ",".join(
        code.removeprefix("kaspi:")
        for code in codes
        if not code.startswith(("ozon:", "halyk:"))
    )
    ozon_cli = ROOT / "collectors" / "ozon" / "ozon_collector.py"
    halyk_cli = ROOT / "collectors" / "halyk" / "halyk_collector.py"
    halyk = CFG["halyk"]
    halyk_codes_text = ",".join(code.removeprefix("halyk:") for code in codes if code.startswith("halyk:"))
    ozon_actions = {
        "ozon_discover": "discover", "ozon_enrich": "enrich-new", "ozon_market_search": "market-search",
        "ozon_refresh_prices": "refresh-prices", "ozon_refresh_stale": "refresh-stale",
        "ozon_retry": "retry-failed", "ozon_full_sync": "full-sync", "ozon_export": "export",
    }
    if action in ozon_actions:
        command = py_command(ozon_cli, ozon_actions[action])
        if action in {"ozon_enrich", "ozon_market_search", "ozon_refresh_prices", "ozon_refresh_stale", "ozon_retry"}:
            command += ["--limit", str(30 if action == "ozon_market_search" else 100)]
        return command
    halyk_actions = {
        "halyk_sync_catalog": "sync-catalog",
        "halyk_refresh_offers": "refresh-offers",
        "halyk_full_sync": "full-sync",
    }
    if action in halyk_actions:
        command = py_command(
            halyk_cli,
            halyk_actions[action],
            "--db", str(DB_PATH),
            "--seller-name", str(halyk["seller_name"]),
            "--location-id", str(halyk["location_id"]),
            "--catalog-query", str(halyk.get("catalog_query") or "shini-i-diski"),
            "--catalog-category-id", str(halyk.get("catalog_category_id") or "10038"),
            "--page-size", str(halyk["page_size"]),
            "--timeout", str(halyk["timeout_seconds"]),
            "--sleep", str(halyk["sleep_seconds"]),
        )
        max_products = int(halyk.get("max_products") or 0)
        if max_products > 0:
            command += ["--max-products", str(max_products)]
        if action == "halyk_refresh_offers" and halyk_codes_text:
            command += ["--product-ids", halyk_codes_text]
        return command
    if action == "sync_catalog":
        command = py_command(
            ROOT / "engine" / "catalog_sync.py",
            "--db", str(DB_PATH),
            "--profile", str(resolve_path(CFG, "profile")),
            "--seller-id", str(kaspi["seller_id"]),
            "--city-id", str(kaspi["city_id"]),
            "--timeout", str(kaspi["timeout_seconds"]),
            "--retries", str(kaspi["retries"]),
            "--min-delay", str(kaspi["min_delay"]),
            "--max-delay", str(kaspi["max_delay"]),
            "--strategy", "auto",
        )
        if kaspi.get("headless"):
            command.append("--headless")
        return command
    if action == "update_own_prices":
        command = py_command(
            ROOT / "engine" / "own_price_refresh.py",
            "--db", str(DB_PATH),
            "--profile", str(resolve_path(CFG, "profile")),
            "--seller-id", str(kaspi["seller_id"]),
            "--city-id", str(kaspi["city_id"]),
            "--zone-id", str(kaspi["zone_id"]),
            "--timeout", str(kaspi["timeout_seconds"]),
            "--retries", str(kaspi["retries"]),
            "--min-delay", "0.35",
            "--max-delay", "0.85",
        )
        if codes:
            command += ["--codes", codes_text]
        if kaspi.get("headless"):
            command.append("--headless")
        return command
    if action in {"scan_market", "refresh_market", "retry_errors"}:
        command = py_command(
            ROOT / "engine" / "exact_offer_refresh.py",
            "--db", str(DB_PATH),
            "--profile", str(resolve_path(CFG, "profile")),
            "--seller-id", str(kaspi["seller_id"]),
            "--seller-name", str(kaspi["seller_name"]),
            "--city-id", str(kaspi["city_id"]),
            "--workers", str(max(1, int(analysis["price_workers"]))),
            "--timeout", str(kaspi["timeout_seconds"]),
            "--retries", str(kaspi["retries"]),
            "--min-delay", str(max(0.35, float(kaspi["min_delay"]) / 2)),
            "--max-delay", str(max(0.8, float(kaspi["max_delay"]) / 1.5)),
        )
        if action == "scan_market":
            command.append("--refresh")
        elif action == "refresh_market":
            command += ["--stale-hours", "24"]
        else:
            command.append("--only-errors")
        if codes:
            command += ["--codes", codes_text, "--refresh"]
        if kaspi.get("headless"):
            command.append("--headless")
        return command
    if action == "export_report":
        command = py_command(
            ROOT / "engine" / "export_market_intelligence.py",
            "--db", str(DB_PATH),
            "--ozon-db", str(ROOT / "collectors" / "ozon" / "data" / "ozon_registry.db"),
            "--output", str(resolve_path(CFG, "output")),
            "--seller-name", str(kaspi["seller_name"]),
            "--seller-id", str(kaspi["seller_id"]),
            "--user-id", str(user_id),
        )
        if codes:
            command += ["--codes", ",".join(codes)]
        return command
    if action == "audit_catalog":
        return v9_base() + [
            "audit-catalog", "--expected-count", str(kaspi["expected_count"])
        ]
    if action == "backup_database":
        return py_command(
            ROOT / "engine" / "backup_database.py",
            "--db", str(DB_PATH),
            "--output", str(resolve_path(CFG, "backups")),
        )
    raise ValueError("Неизвестная операция.")


SCHEDULER = SchedulerService(
    SAAS, TASKS, ACTION_INFO, build_action_command,
    interval_seconds=int(os.environ.get("ITP_SCHEDULER_INTERVAL", "30")),
)
if os.environ.get("ITP_DISABLE_SCHEDULER", "0") != "1":
    SCHEDULER.start()
atexit.register(SCHEDULER.stop)


@app.before_request
def before_request() -> Any:
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        user = AUTH.get_user(int(user_id))
        if user and user.get("is_active"):
            g.user = user
        else:
            session.clear()

    if not AUTH.has_users() and request.endpoint not in {
        "setup", "setup_complete", "static", "health", "landing",
        "registration", "registration_complete", "legal_document"
    }:
        if is_api_request():
            return json_error("Требуется первичная настройка.", 428)
        return redirect(url_for("setup"))

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.endpoint in {"login", "setup", "forgot_password", "registration"}:
            token = request.form.get("csrf_token")
        elif is_api_request():
            token = request.headers.get("X-CSRF-Token")
        else:
            token = request.form.get("csrf_token")
        if not valid_csrf(token):
            if is_api_request():
                return json_error("Сессия устарела. Обновите страницу.", 419)
            abort(419)


@app.after_request
def after_request(response: Any) -> Any:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.path.startswith("/api/") or request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    else:
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    if request.path.endswith(".js"):
        response.headers["Content-Type"] = "application/javascript; charset=utf-8"
    return response


@app.context_processor
def template_context() -> dict[str, Any]:
    return {
        "csrf_token": ensure_csrf(),
        "current_user": current_user(),
        "current_tenant": current_tenant(),
        "version": VERSION,
        "integration_catalog": INTEGRATION_CATALOG,
        "public_capabilities": PUBLIC_CAPABILITIES,
        "public_settings": PUBLIC.settings(),
    }


@app.get("/health")
def health() -> Any:
    return jsonify({"ok": True, "version": VERSION})


@app.route("/setup", methods=["GET", "POST"])
def setup() -> Any:
    if AUTH.has_users():
        return redirect(url_for("app_index" if current_user() else "login"))
    ensure_csrf()
    if request.method == "POST":
        try:
            if request.form.get("password") != request.form.get("password_confirm"):
                raise ValueError("Пароли не совпадают.")
            user, recovery = AUTH.create_initial_admin(
                request.form.get("email", ""),
                request.form.get("display_name", ""),
                request.form.get("password", ""),
            )
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["csrf_token"] = secrets.token_urlsafe(32)
            session["setup_recovery_code"] = recovery
            return redirect(url_for("setup_complete"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("setup.html")


@app.get("/setup/complete")
@login_required
def setup_complete() -> Any:
    recovery = session.pop("setup_recovery_code", None)
    if not recovery:
        return redirect(url_for("app_index"))
    return render_template("setup_complete.html", recovery_code=recovery)


@app.route("/login", methods=["GET", "POST"])
def login() -> Any:
    if current_user():
        return redirect(url_for("app_index"))
    ensure_csrf()
    if request.method == "POST":
        ip = request.remote_addr or "local"
        attempts = [stamp for stamp in LOGIN_ATTEMPTS.get(ip, []) if now_epoch() - stamp < LOGIN_WINDOW_SECONDS]
        LOGIN_ATTEMPTS[ip] = attempts
        if len(attempts) >= LOGIN_MAX_ATTEMPTS:
            flash("Слишком много попыток входа. Повторите через 15 минут.", "error")
            return render_template("login.html"), 429
        user = AUTH.authenticate(request.form.get("email", ""), request.form.get("password", ""))
        if not user:
            attempts.append(now_epoch())
            LOGIN_ATTEMPTS[ip] = attempts
            flash("Неверная почта или пароль.", "error")
        else:
            LOGIN_ATTEMPTS.pop(ip, None)
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["csrf_token"] = secrets.token_urlsafe(32)
            next_url = request.args.get("next")
            return redirect(next_url if next_url and next_url.startswith("/") else url_for("app_index"))
    return render_template("login.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password() -> Any:
    ensure_csrf()
    if request.method == "POST":
        email_value = str(request.form.get("email") or "")
        if rate_limit_hit("recovery", email_value, RECOVERY_MAX_ATTEMPTS, LOGIN_LOCK_SECONDS):
            flash("Слишком много попыток восстановления. Повторите позже.", "error")
            return render_template("forgot_password.html"), 429
        try:
            if request.form.get("password") != request.form.get("password_confirm"):
                raise ValueError("Пароли не совпадают.")
            AUTH.reset_password_with_recovery(
                email_value,
                request.form.get("recovery_code", ""),
                request.form.get("password", ""),
            )
            flash("Пароль изменён. Теперь можно войти.", "success")
            return redirect(url_for("login"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("forgot_password.html")


@app.post("/logout")
@login_required
def logout() -> Any:
    session.clear()
    return redirect(url_for("landing"))


@app.get("/")
def landing() -> Any:
    return render_template("landing.html", capabilities=PUBLIC_CAPABILITIES, has_users=AUTH.has_users())


@app.route("/register", methods=["GET", "POST"])
def registration() -> Any:
    ensure_csrf()
    if request.method == "POST":
        if rate_limit_hit("registration", str(request.form.get("email") or ""), REGISTRATION_MAX_ATTEMPTS, LOGIN_WINDOW_SECONDS):
            flash("Слишком много заявок с этого адреса. Повторите позже.", "error")
            return render_template("register.html", capabilities=PUBLIC_CAPABILITIES), 429
        payload = {
            "company_name": request.form.get("company_name", ""),
            "registration_number": request.form.get("registration_number", ""),
            "contact_name": request.form.get("contact_name", ""),
            "email": request.form.get("email", ""),
            "phone": request.form.get("phone", ""),
            "capabilities": request.form.getlist("capabilities"),
            "estimated_products": request.form.get("estimated_products", "0"),
            "comment": request.form.get("comment", ""),
            "privacy_consent": request.form.get("privacy_consent") == "1",
            "terms_consent": request.form.get("terms_consent") == "1",
            "locale": request.form.get("locale", "ru"),
            "source_page": "public_registration",
        }
        try:
            request_id = SAAS.create_registration_request(payload)
            session["registration_request_id"] = request_id
            return redirect(url_for("registration_complete"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("register.html", capabilities=PUBLIC_CAPABILITIES)


@app.get("/register/complete")
def registration_complete() -> Any:
    return render_template("registration_complete.html", request_id=session.pop("registration_request_id", None))


@app.get("/legal/<document>")
def legal_document(document: str) -> Any:
    if document not in {"privacy", "terms", "cookies", "consent", "offer"}: abort(404)
    locale=str(request.args.get("lang") or "ru").casefold()
    try: value=PUBLIC.legal_document(document,locale)
    except KeyError: abort(404)
    return render_template("legal.html",document=value)


@app.get("/app")
@login_required
def app_index() -> Any:
    return render_template("app.html")


@app.get("/platform")
@platform_roles_required("superadmin")
def platform_index() -> Any:
    return render_template("platform.html")


@app.get("/api/session")
@login_required
def api_session() -> Any:
    user = current_user() or {}
    return json_ok(
        user=user,
        tenant=current_tenant(),
        integrations=SAAS.integrations(int(user["tenant_id"])) if user.get("tenant_id") else [],
        csrf_token=ensure_csrf(),
        version=VERSION,
    )


@app.get("/api/overview")
@login_required
def api_overview() -> Any:
    user = current_user() or {}
    return json_ok(
        overview=DATA.overview(
            int(CFG["kaspi"].get("expected_count", 0)),
            int(CFG["analysis"].get("discover_workers", 2)),
            int(user["id"]),
            allowed_platforms=allowed_marketplaces(),
        ),
        tasks=visible_tasks(user)[:12],
    )


@app.get("/api/products/options")
@login_required
def api_product_options() -> Any:
    return json_ok(**DATA.filter_options(allowed_marketplaces()))


def product_filters_from_request() -> dict[str, Any]:
    return {
        "query": request.args.get("query", ""),
        "brand": request.args.get("brand", ""),
        "platform": request.args.get("platform", ""),
        "status": request.args.get("status", ""),
        "watched": request.args.get("watched", ""),
        "freshness": request.args.get("freshness", ""),
        "scope": request.args.get("scope", "all"),
        "sort": request.args.get("sort", "updated"),
        "direction": request.args.get("direction", "desc"),
    }


@app.get("/api/products")
@login_required
def api_products() -> Any:
    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", CFG["app"].get("product_page_size", 30)))
    except ValueError:
        return json_error("Некорректная пагинация.")
    try:
        filters, _ = requested_platform_filters()
    except PermissionError as exc:
        return json_error(str(exc), 403)
    return json_ok(result=DATA.products(
        page, page_size, filters, int((current_user() or {})["id"])
    ))


@app.get("/api/products/codes")
@login_required
def api_product_codes() -> Any:
    try:
        filters, _ = requested_platform_filters()
    except PermissionError as exc:
        return json_error(str(exc), 403)
    return json_ok(codes=DATA.product_codes(filters))


@app.get("/api/products/<code>")
@login_required
def api_product(code: str) -> Any:
    platform = "halyk_market" if str(code).startswith("halyk:") else "ozon" if str(code).startswith("ozon:") else "kaspi"
    if platform not in allowed_marketplaces():
        return json_error("Нет доступа к выбранной площадке.", 403)
    product = DATA.product(code, int((current_user() or {})["id"]))
    return json_ok(product=product) if product else json_error("Товар не найден.", 404)


@app.put("/api/products/state")
@roles_required("admin", "operator")
def api_product_state() -> Any:
    payload = json_payload()
    codes = clean_codes(payload.get("codes"))
    if not codes:
        return json_error("Не выбраны товары.")
    try:
        count = DATA.set_product_state(
            codes,
            payload.get("watched") if "watched" in payload else None,
            payload.get("priority") if "priority" in payload else None,
            payload.get("note") if "note" in payload else None,
            int((current_user() or {})["id"]),
            payload.get("expected_monthly_units") if "expected_monthly_units" in payload else None,
        )
        return json_ok(updated=count)
    except ValueError as exc:
        return json_error(str(exc))


@app.get("/api/tasks")
@login_required
def api_tasks() -> Any:
    return json_ok(tasks=visible_tasks())


@app.post("/api/tasks/start")
@roles_required("admin", "operator")
def api_task_start() -> Any:
    payload = json_payload()
    action = str(payload.get("action") or "")
    info = ACTION_INFO.get(action)
    user = current_user() or {}
    if not info:
        return json_error("Неизвестная операция.")
    if user.get("role") not in info["roles"]:
        return json_error("Недостаточно прав.", 403)
    if action == "backup_database" and not is_superadmin(user):
        return json_error("Резервная копия всей базы доступна только platform superadmin.", 403)
    task_platform = info.get("platform") or (
        "halyk_market" if action.startswith("halyk_") else
        "ozon" if action.startswith("ozon_") else
        "system" if action in {"export_report", "backup_database"} else "kaspi"
    )
    if task_platform in {"kaspi", "ozon", "halyk_market"} and task_platform not in allowed_marketplaces(user):
        return json_error("Для пользователя не разрешена эта площадка.", 403)
    codes = clean_codes(payload.get("codes"))
    scope = str(payload.get("scope") or ("selected" if codes else "all"))
    if scope == "selected" and not codes:
        return json_error("Не выбраны товары.")
    if action in {"sync_catalog", "audit_catalog", "backup_database", "ozon_discover", "ozon_enrich", "ozon_market_search", "ozon_refresh_prices", "ozon_refresh_stale", "ozon_retry", "ozon_full_sync", "ozon_export", "halyk_sync_catalog", "halyk_full_sync"}:
        codes = []
        scope = "all"
    try:
        command = build_action_command(action, codes, int(user["id"]))
        suffix = f" — {len(codes)} поз." if codes else " — весь каталог"
        label = info["label"] + (suffix if action not in {"sync_catalog", "audit_catalog", "backup_database"} else "")
        task = TASKS.start(
            action,
            label,
            command,
            info["resource"],
            metadata={
                "scope": scope,
                "codes_count": len(codes),
                "requested_by": user.get("display_name"),
                "requested_by_id": user.get("id"),
                "tenant_id": user.get("tenant_id"),
                "platform": info.get("platform") or ("halyk_market" if action.startswith("halyk_") else "ozon" if action.startswith("ozon_") else "kaspi"),
            },
        )
        record_event("task_started", "task", task["id"], {"action": action, "scope": scope, "codes": len(codes)})
        return json_ok(task=task)
    except (ValueError, RuntimeError) as exc:
        return json_error(str(exc), 409)


@app.post("/api/tasks/<task_id>/stop")
@roles_required("admin", "operator")
def api_task_stop(task_id: str) -> Any:
    if not visible_task(task_id):
        return json_error("Операция не найдена.", 404)
    task = TASKS.stop(task_id)
    record_event("task_stopped", "task", task_id, {})
    return json_ok(task=task)


@app.get("/api/tasks/<task_id>/log")
@login_required
def api_task_log(task_id: str) -> Any:
    task = visible_task(task_id)
    if not task or task.get("status") == "missing":
        return json_error("Операция не найдена.", 404)
    return json_ok(task=task, log=TASKS.tail(task_id, int(request.args.get("lines", 500))))


@app.delete("/api/tasks/<task_id>")
@roles_required("admin", "operator")
def api_task_delete(task_id: str) -> Any:
    if not visible_task(task_id):
        return json_error("Операция не найдена.", 404)
    try:
        task = TASKS.delete(task_id)
        record_event("task_deleted", "task", task_id, {})
        return json_ok(task=task)
    except RuntimeError as exc:
        return json_error(str(exc), 409)


@app.delete("/api/tasks")
@roles_required("admin", "operator")
def api_tasks_clear() -> Any:
    if is_superadmin():
        count = TASKS.clear_finished()
    else:
        count = 0
        for task in list(visible_tasks()):
            if not task.get("running"):
                try:
                    TASKS.delete(str(task["id"]))
                    count += 1
                except RuntimeError:
                    pass
    record_event("tasks_cleared", "task", "finished", {"count": count})
    return json_ok(deleted=count)


@app.get("/api/analytics/dashboard")
@login_required
def api_analytics_dashboard() -> Any:
    user = current_user() or {}
    return json_ok(analytics=DATA.analytics_dashboard(int(user["id"]), allowed_marketplaces(user)))


@app.get("/api/reports")
@login_required
def api_reports() -> Any:
    user = current_user() or {}
    clause, params = tenant_visibility_predicate("r", user)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""
            SELECT r.id,r.report_type,r.scope,r.file_name,r.rows_count,r.created_at,u.display_name
            FROM app_reports r LEFT JOIN app_users u ON u.id=r.created_by
            WHERE {clause}
            ORDER BY r.id DESC LIMIT 100
            """,
            params,
        ).fetchall()
    finally:
        conn.close()
    return json_ok(reports=[dict(row) for row in rows])


@app.get("/api/reports/<int:report_id>/download")
@login_required
def api_report_download(report_id: int) -> Any:
    user = current_user() or {}
    clause, params = tenant_visibility_predicate("app_reports", user)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            f"SELECT file_name,file_path FROM app_reports WHERE id=? AND {clause}",
            [int(report_id), *params],
        ).fetchone()
    finally:
        conn.close()
    if not row:
        abort(404)
    path = Path(row["file_path"]).resolve()
    output = resolve_path(CFG, "output").resolve()
    if output not in path.parents or not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=row["file_name"])


@app.get("/api/backups")
@platform_roles_required("superadmin")
def api_backups() -> Any:
    folder = resolve_path(CFG, "backups")
    items = [
        {
            "name": path.name,
            "size": path.stat().st_size,
            "modified_at": path.stat().st_mtime,
        }
        for path in sorted(folder.glob("*.db"), key=lambda value: value.stat().st_mtime, reverse=True)
    ]
    return json_ok(backups=items[:50])


@app.get("/api/backups/<path:name>/download")
@platform_roles_required("superadmin")
def api_backup_download(name: str) -> Any:
    if Path(name).name != name:
        abort(404)
    path = (resolve_path(CFG, "backups") / name).resolve()
    folder = resolve_path(CFG, "backups").resolve()
    if folder not in path.parents or not path.exists() or path.suffix.lower() != ".db":
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)


@app.get("/api/events")
@login_required
def api_events() -> Any:
    user = current_user() or {}
    return json_ok(
        events=DATA.latest_events(
            int(request.args.get("limit", 40)),
            tenant_id=None if is_superadmin(user) else int(user["tenant_id"]),
        )
    )


@app.get("/api/settings")
@login_required
def api_settings_get() -> Any:
    user = current_user() or {}
    config_result = None
    if user.get("role") == "admin":
        config_result = public_config(CFG)
        config_result["ozon"] = load_ozon_public_config()
    return json_ok(
        preferences=DATA.preferences(int(user["id"])),
        config=config_result,
        tenant=current_tenant(),
        network=network_public_config(),
    )


@app.put("/api/settings")
@login_required
def api_settings_put() -> Any:
    payload = json_payload()
    user = current_user() or {}
    try:
        preferences_payload = payload.get("preferences") if isinstance(payload.get("preferences"), dict) else payload
        preferences = DATA.save_preferences(int(user["id"]), preferences_payload)
        tenant_result = None
        if user.get("role") == "admin" and isinstance(payload.get("tenant"), dict) and user.get("tenant_id"):
            tenant_result = SAAS.update_tenant_profile(
                int(user["tenant_id"]),
                payload["tenant"],
                int(user["id"]),
            )
        config_result = None
        if user.get("role") == "admin" and isinstance(payload.get("config"), dict):
            config_payload = payload["config"]
            updated = load_config()
            kaspi = config_payload.get("kaspi") if isinstance(config_payload.get("kaspi"), dict) else {}
            analysis = config_payload.get("analysis") if isinstance(config_payload.get("analysis"), dict) else {}
            app_values = config_payload.get("app") if isinstance(config_payload.get("app"), dict) else {}
            ozon_values = config_payload.get("ozon") if isinstance(config_payload.get("ozon"), dict) else {}
            halyk_values = config_payload.get("halyk") if isinstance(config_payload.get("halyk"), dict) else {}
            for key in ("seller_id", "seller_name", "city_id", "zone_id"):
                if key in kaspi:
                    updated["kaspi"][key] = str(kaspi[key]).strip()
            for key, minimum, maximum in (("expected_count", 0, 100000), ("timeout_seconds", 10, 180), ("retries", 1, 8)):
                if key in kaspi:
                    updated["kaspi"][key] = max(minimum, min(int(kaspi[key]), maximum))
            for key, minimum, maximum in (("min_delay", 0.1, 20.0), ("max_delay", 0.2, 30.0)):
                if key in kaspi:
                    updated["kaspi"][key] = max(minimum, min(float(kaspi[key]), maximum))
            if updated["kaspi"]["max_delay"] < updated["kaspi"]["min_delay"]:
                updated["kaspi"]["max_delay"] = updated["kaspi"]["min_delay"]
            if "headless" in kaspi:
                updated["kaspi"]["headless"] = bool(kaspi["headless"])
            for key in ("seller_name", "location_id", "catalog_query", "catalog_category_id"):
                if key in halyk_values:
                    updated["halyk"][key] = str(halyk_values[key]).strip()
            for key, minimum, maximum in (("page_size", 10, 200), ("timeout_seconds", 10, 120), ("max_products", 0, 100000)):
                if key in halyk_values:
                    updated["halyk"][key] = max(minimum, min(int(halyk_values[key]), maximum))
            if "sleep_seconds" in halyk_values:
                updated["halyk"]["sleep_seconds"] = max(0.0, min(float(halyk_values["sleep_seconds"]), 10.0))
            for key, minimum, maximum in (("discover_workers", 1, 4), ("price_workers", 1, 4), ("search_pages", 1, 5), ("validate_top", 1, 12), ("search_cache_days", 0, 90), ("detail_cache_days", 0, 180)):
                if key in analysis:
                    value = float(analysis[key]) if "days" in key else int(analysis[key])
                    updated["analysis"][key] = max(minimum, min(value, maximum))
            if "max_parallel_tasks" in app_values:
                updated["app"]["max_parallel_tasks"] = max(1, min(int(app_values["max_parallel_tasks"]), 5))
            if "product_page_size" in app_values:
                updated["app"]["product_page_size"] = max(10, min(int(app_values["product_page_size"]), 100))
            save_config(updated)
            if ozon_values:
                save_ozon_public_config(ozon_values)
            reload_services()
            config_result = public_config(CFG)
            config_result["ozon"] = load_ozon_public_config()
        record_event("settings_updated", "settings", "user", {"locale": preferences.get("locale")})
        return json_ok(preferences=preferences, config=config_result, tenant=tenant_result)
    except (ValueError, TypeError) as exc:
        return json_error(f"Некорректные настройки: {exc}")


@app.get("/api/users")
@roles_required("admin")
def api_users_get() -> Any:
    return json_ok(users=AUTH.list_users(int((current_user() or {})["tenant_id"])))


@app.post("/api/users")
@roles_required("admin")
def api_users_create() -> Any:
    payload = json_payload()
    try:
        current = current_user() or {}
        user, recovery = AUTH.create_user(
            str(payload.get("email") or ""),
            str(payload.get("display_name") or ""),
            str(payload.get("password") or ""),
            str(payload.get("role") or "operator"),
            int(current["id"]),
            tenant_id=int(current["tenant_id"]),
        )
        marketplaces = payload.get("marketplaces")
        if marketplaces is not None:
            user = AUTH.update_user(
                int(user["id"]), {"marketplaces": marketplaces}, int(current["id"])
            )
        return json_ok(user=user, recovery_code=recovery)
    except ValueError as exc:
        return json_error(str(exc))


@app.put("/api/users/<int:user_id>")
@roles_required("admin")
def api_users_update(user_id: int) -> Any:
    current = current_user() or {}
    try:
        managed_user_or_error(user_id, current)
        user = AUTH.update_user(user_id, json_payload(), int(current["id"]))
        return json_ok(user=user)
    except LookupError as exc:
        return json_error(str(exc), 404)
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except ValueError as exc:
        return json_error(str(exc))


@app.delete("/api/users/<int:user_id>")
@roles_required("admin")
def api_users_delete(user_id: int) -> Any:
    current = current_user() or {}
    try:
        managed_user_or_error(user_id, current)
        AUTH.delete_user(user_id, int(current["id"]))
        return json_ok(deleted=True)
    except LookupError as exc:
        return json_error(str(exc), 404)
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except ValueError as exc:
        return json_error(str(exc), 409)


@app.post("/api/users/<int:user_id>/recovery")
@roles_required("admin")
def api_users_recovery(user_id: int) -> Any:
    current = current_user() or {}
    try:
        managed_user_or_error(user_id, current)
        code = AUTH.regenerate_recovery(user_id, int(current["id"]))
        return json_ok(recovery_code=code)
    except LookupError as exc:
        return json_error(str(exc), 404)
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except ValueError as exc:
        return json_error(str(exc))


@app.get("/api/tenant")
@login_required
def api_tenant_get() -> Any:
    user = current_user() or {}
    return json_ok(tenant=current_tenant(), integrations=SAAS.integrations(int(user["tenant_id"])))


@app.get("/api/schedules")
@login_required
def api_schedules_get() -> Any:
    user = current_user() or {}
    tenant_id = int(user["tenant_id"])
    return json_ok(
        schedules=SAAS.schedules(tenant_id),
        runs=SAAS.schedule_runs(tenant_id, int(request.args.get("limit", 50))),
        actions=schedule_actions_for_user(user),
    )


@app.post("/api/schedules")
@roles_required("admin", "operator")
def api_schedules_create() -> Any:
    user = current_user() or {}
    payload = json_payload()
    if str(payload.get("action") or "") == "backup_database" and not is_superadmin(user):
        return json_error("Резервная копия всей базы доступна только platform superadmin.", 403)
    try:
        return json_ok(schedule=SAAS.create_schedule(int(user["tenant_id"]), payload, int(user["id"])))
    except (ValueError, TypeError) as exc:
        return json_error(str(exc))


@app.put("/api/schedules/<int:schedule_id>")
@roles_required("admin", "operator")
def api_schedules_update(schedule_id: int) -> Any:
    user = current_user() or {}
    try:
        return json_ok(schedule=SAAS.update_schedule(schedule_id, int(user["tenant_id"]), json_payload(), int(user["id"])))
    except (ValueError, TypeError) as exc:
        return json_error(str(exc))


@app.delete("/api/schedules/<int:schedule_id>")
@roles_required("admin")
def api_schedules_delete(schedule_id: int) -> Any:
    user = current_user() or {}
    try:
        SAAS.delete_schedule(schedule_id, int(user["tenant_id"]), int(user["id"]))
        return json_ok(deleted=True)
    except ValueError as exc:
        return json_error(str(exc), 404)


@app.get("/api/platform/overview")
@platform_roles_required("superadmin")
def api_platform_overview() -> Any:
    user = current_user() or {}
    overview = DATA.overview(int(CFG["kaspi"].get("expected_count", 0)), int(CFG["analysis"].get("discover_workers", 2)), int(user["id"]))
    result = SAAS.platform_overview(int(overview.get("catalog_count") or 0), int(overview.get("processed_total_count") or overview.get("analyzed_count") or 0))
    return json_ok(**result, requests=SAAS.registration_requests(), integration_catalog=SAAS.public_integrations())


@app.get("/api/platform/tenants/<int:tenant_id>/detail")
@platform_roles_required("superadmin")
def api_platform_tenant_detail(tenant_id: int) -> Any:
    try: return json_ok(**SAAS.tenant_detail(tenant_id))
    except ValueError as exc: return json_error(str(exc),404)


@app.get("/api/platform/public-settings")
@platform_roles_required("superadmin")
def api_platform_public_settings_get() -> Any:
    return json_ok(settings=PUBLIC.settings())


@app.put("/api/platform/public-settings")
@platform_roles_required("superadmin")
def api_platform_public_settings_put() -> Any:
    try:
        settings=PUBLIC.update_settings(json_payload(),int((current_user() or {})["id"]))
        record_event("platform_public_settings_updated","platform_settings","public_product")
        return json_ok(settings=settings)
    except ValueError as exc: return json_error(str(exc))


@app.put("/api/platform/tenants/<int:tenant_id>")
@platform_roles_required("superadmin")
def api_platform_tenant_update(tenant_id: int) -> Any:
    try:
        return json_ok(tenant=SAAS.update_tenant(tenant_id, json_payload(), int((current_user() or {})["id"])))
    except ValueError as exc:
        return json_error(str(exc), 404)


@app.post("/api/platform/tenants/<int:tenant_id>/admin")
@platform_roles_required("superadmin")
def api_platform_tenant_admin_create(tenant_id: int) -> Any:
    payload=json_payload()
    try:
        user,recovery=AUTH.create_user(str(payload.get("email") or ""),str(payload.get("display_name") or ""),str(payload.get("password") or ""),"admin",int((current_user() or {})["id"]),tenant_id=tenant_id)
        return json_ok(user=user,recovery_code=recovery)
    except ValueError as exc:
        return json_error(str(exc))


@app.post("/api/platform/registration-requests/<int:request_id>/<decision>")
@platform_roles_required("superadmin")
def api_platform_registration_review(request_id: int, decision: str) -> Any:
    try:
        return json_ok(request=SAAS.review_registration(request_id,decision,int((current_user() or {})["id"])))
    except ValueError as exc:
        return json_error(str(exc),409)


@app.post("/api/account/password")
@login_required
def api_account_password() -> Any:
    payload = json_payload()
    if payload.get("new_password") != payload.get("new_password_confirm"):
        return json_error("Новые пароли не совпадают.")
    try:
        AUTH.change_password(
            int((current_user() or {})["id"]),
            str(payload.get("current_password") or ""),
            str(payload.get("new_password") or ""),
        )
        return json_ok(message="Пароль изменён.")
    except ValueError as exc:
        return json_error(str(exc))


@app.post("/api/account/recovery")
@login_required
def api_account_recovery() -> Any:
    user = current_user() or {}
    code = AUTH.regenerate_recovery(int(user["id"]), int(user["id"]))
    return json_ok(recovery_code=code)


def write_pid() -> None:
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()), encoding="ascii")


def remove_pid() -> None:
    try:
        if PID_PATH.exists() and PID_PATH.read_text(encoding="ascii").strip() == str(os.getpid()):
            PID_PATH.unlink()
    except OSError:
        pass


def runtime_host() -> str:
    return str(os.environ.get("ITP_HOST") or CFG["app"]["host"])


def runtime_port() -> int:
    return int(os.environ.get("ITP_PORT") or CFG["app"]["port"])


def open_panel() -> None:
    override = os.environ.get("ITP_OPEN_BROWSER")
    enabled = bool(CFG["app"].get("open_browser")) if override is None else override.strip().casefold() not in {"0", "false", "no", "off"}
    if enabled:
        time.sleep(1.2)
        webbrowser.open(f"http://127.0.0.1:{runtime_port()}/?v={VERSION}")


if __name__ == "__main__":
    write_pid()
    atexit.register(remove_pid)
    threading.Thread(target=open_panel, daemon=True).start()
    host, port = runtime_host(), runtime_port()
    print("=" * 72)
    print(f" SPYON {VERSION}")
    print(f" Local: http://127.0.0.1:{port}")
    for address in local_ipv4_addresses():
        print(f" Wi-Fi/LAN: http://{address}:{port}")
    if host in {"0.0.0.0", "::"}:
        print(" LAN access: ENABLED. Authentication is still required.")
    print("=" * 72)
    serve(
        app,
        host=host,
        port=port,
        threads=12,
        channel_timeout=120,
    )
