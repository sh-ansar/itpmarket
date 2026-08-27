from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
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
from werkzeug.middleware.proxy_fix import ProxyFix

from auth_service import AuthService, normalize_email, validate_password
from billing_service import (
    BillingService,
    PAYMENT_PROOF_MAX_BYTES,
)
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
from inventory_service import InventoryService
from catalog_configuration_service import CatalogConfigurationService
from schema import ensure_database
from task_manager import TaskManager
from saas_service import SaaSService, INTEGRATION_CATALOG, SCHEDULE_ACTIONS
from scheduler_service import SchedulerService
from public_product_service import PublicProductService, PUBLIC_CAPABILITIES
from legal_documents import LEGAL_DOCUMENTS
from marketplace_registry import (
    LEGACY_MARKETPLACE_CODES,
    MARKETPLACE_CODES,
    allowed_marketplaces_from_user,
    marketplace_for_action,
    marketplace_for_product_code,
    parse_product_code,
)
from security_hygiene import redact_sensitive
from tenant_security import (
    company_is_approved,
    has_permission,
    has_platform_permission,
)
from subscription_service import (
    SubscriptionError,
    SubscriptionLimitError,
    SubscriptionService,
)
from notification_service import NotificationService
from telegram_bot import TelegramBotWorker, TelegramLinkService
from email_service import EmailOutboxWorker, EmailService
from storage.database_backend import DatabaseSettings
from storage.postgres_compat import connect_database
from runtime_scope import SellerRuntimeScope, seller_scope

VERSION = "3.8.7"
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = get_secret_key()


def environment_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


IS_PRODUCTION = str(os.environ.get("ITP_ENV") or "").strip().casefold() == "production"
if environment_flag("ITP_TRUST_PROXY"):
    # The production launcher binds Waitress to loopback, so exactly one
    # trusted reverse proxy (Caddy) can supply the external host and scheme.
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=0
    )

CFG = ensure_directories(load_config())
DB_PATH = resolve_path(CFG, "database")
DatabaseSettings.from_environment().assert_runtime_ready()
ensure_database(DB_PATH)
AUTH = AuthService(DB_PATH)
SAAS = SaaSService(DB_PATH)
SUBSCRIPTIONS = SubscriptionService(DB_PATH)
BILLING = BillingService(
    DB_PATH,
    document_root=ROOT,
)
EMAIL = EmailService(DB_PATH)
NOTIFICATIONS = NotificationService(DB_PATH, EMAIL)
TELEGRAM_LINKS = TelegramLinkService(DB_PATH)
PUBLIC = PublicProductService(DB_PATH)
DATA = DataService(
    DB_PATH,
    str(CFG["kaspi"]["seller_name"]),
    ROOT / "collectors" / "ozon" / "data" / "ozon_registry.db",
    seller_id=str(CFG["kaspi"]["seller_id"]),
    halyk_seller_name=str(CFG["halyk"]["seller_name"]),
    forte_seller_name=str(CFG["forte"]["seller_name"]),
    ozon_kz_db_path=ROOT / "collectors" / "ozon_kz" / "data" / "ozon_kz_registry.db",
)
CATALOG = CatalogConfigurationService(
    DB_PATH, ROOT / "collectors" / "ozon" / "data" / "ozon_registry.db"
)
INVENTORY = InventoryService(DB_PATH)
TASKS = TaskManager(
    ROOT,
    resolve_path(CFG, "logs"),
    ROOT / "data" / "tasks_state.json",
    max_parallel=int(CFG["app"].get("max_parallel_tasks", 3)),
)
PID_PATH = ROOT / "data" / "server.pid"


def subscription_service() -> SubscriptionService:
    """Return the service bound to the active application database.

    Test/staging deployments may replace DB_PATH without importing the whole
    module again. Keeping this lookup path-aware also prevents writes to a
    stale database after a controlled runtime reconfiguration.
    """
    global SUBSCRIPTIONS
    if Path(SUBSCRIPTIONS.db_path).resolve() != Path(DB_PATH).resolve():
        SUBSCRIPTIONS = SubscriptionService(DB_PATH)
    return SUBSCRIPTIONS


def billing_service() -> BillingService:
    """Return billing bound to the active application database."""
    global BILLING

    if (
        Path(BILLING.db_path).resolve()
        != Path(DB_PATH).resolve()
    ):
        BILLING = BillingService(
            DB_PATH,
            document_root=ROOT,
        )

    return BILLING


def subscription_snapshot(
    tenant_id: int,
) -> dict[str, Any]:
    result = (
        subscription_service()
        .tenant_snapshot(
            int(tenant_id)
        )
    )

    result["billing"] = (
        billing_service()
        .tenant_billing_snapshot(
            int(tenant_id)
        )
    )

    return result


def notification_service() -> NotificationService:
    """Return the notification store bound to the current application DB."""
    global NOTIFICATIONS
    if Path(NOTIFICATIONS.db_path).resolve() != Path(DB_PATH).resolve():
        NOTIFICATIONS = NotificationService(DB_PATH, email_service())
    else:
        NOTIFICATIONS.set_email_service(email_service())
    return NOTIFICATIONS


def email_service() -> EmailService:
    """Return the transactional-mail service bound to the active application DB."""
    global EMAIL
    if Path(EMAIL.db_path).resolve() != Path(DB_PATH).resolve():
        EMAIL = EmailService(DB_PATH)
    return EMAIL


def email_action_url(endpoint: str, **values: Any) -> str:
    return email_service().settings.public_url + url_for(endpoint, **values)


def queue_verification_email(user: dict[str, Any], *, request_ip: str = "") -> None:
    token = AUTH.issue_auth_token(
        int(user["id"]), "verify_email", expires_minutes=24 * 60,
        request_ip=request_ip,
    )
    email_service().queue_for_user(
        user_id=int(user["id"]), tenant_id=user.get("tenant_id"),
        template_key="verify_email", security=True,
        payload={
            "recipient_name": user.get("display_name") or "",
            "action_url": email_action_url("verify_email", token=token),
            "action_label": "Подтвердить почту",
        },
        dedupe_key=(
            f"verify-email:{int(user['id'])}:"
            f"{hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]}"
        ),
    )


def queue_password_changed_email(user: dict[str, Any]) -> None:
    email_service().queue_for_user(
        user_id=int(user["id"]), tenant_id=user.get("tenant_id"),
        template_key="password_changed", security=True,
        payload={"action_url": email_action_url("login")},
        dedupe_key=f"password-changed:{int(user['id'])}:{user.get('password_changed_at') or now_epoch()}",
    )


def notify_billing_payment_confirmed(result: dict[str, Any]) -> None:
    """Publish one idempotent billing event per active company user."""
    subscription = dict(result.get("subscription") or {})
    tenant_id = int(subscription.get("tenant_id") or 0)
    subscription_id = int(subscription.get("id") or 0)
    payment_id = int((result.get("payment") or {}).get("id") or 0)
    if not tenant_id or not subscription_id or not payment_id:
        return

    try:
        ends_at = datetime.fromisoformat(
            str(subscription.get("ends_at") or "").replace("Z", "+00:00")
        ).strftime("%d.%m.%Y")
    except ValueError:
        ends_at = str(subscription.get("ends_at") or "")[:10]

    status = str(subscription.get("status") or "")
    if status == "active":
        message = f"Оплата подтверждена. Тариф активирован до {ends_at}."
    else:
        message = f"Оплата подтверждена. Тариф будет действовать до {ends_at}."

    for recipient in AUTH.list_users(tenant_id):
        if not recipient.get("is_active"):
            continue
        notification_service().create(
            tenant_id=tenant_id,
            user_id=int(recipient["id"]),
            category="billing",
            event_type="payment_confirmed",
            title="Оплата подтверждена",
            message=message,
            level="success",
            action_url="/app#settings",
            dedupe_key=(
                f"billing:payment:{payment_id}:subscription:{subscription_id}:"
                f"payment-confirmed:user:{int(recipient['id'])}"
            ),
        )



def finalize_verified_registration(
    user: dict[str, Any],
) -> dict[str, Any] | None:
    """Finish the package selected during self-service registration.

    Company registration itself does not require manual platform review.
    Email verification is the account ownership gate. Marketplace
    connections keep their own independent review lifecycle.
    """
    if not bool(
        user.get(
            "email_verified"
        )
    ):
        return None

    tenant_id = int(
        user.get(
            "tenant_id"
        )
        or 0
    )

    if tenant_id <= 0:
        return None

    snapshot = (
        subscription_service()
        .tenant_snapshot(
            tenant_id
        )
    )

    pending = next(
        (
            item
            for item
            in snapshot.get(
                "requests",
                [],
            )
            if str(
                item.get(
                    "status"
                )
                or ""
            )
            == "pending"
        ),
        None,
    )

    if not pending:
        return None

    return (
        subscription_service()
        .review_subscription(
            int(
                pending[
                    "id"
                ]
            ),
            "approved",
            int(
                user[
                    "id"
                ]
            ),
            review_note=(
                "Self-service activation "
                "after email verification"
            ),
        )
    )

def telegram_link_service() -> TelegramLinkService:
    """Return personal Telegram links bound to the active application DB."""
    global TELEGRAM_LINKS
    if Path(TELEGRAM_LINKS.db_path).resolve() != Path(DB_PATH).resolve():
        TELEGRAM_LINKS = TelegramLinkService(DB_PATH)
    return TELEGRAM_LINKS


def sync_telegram_notification_sources() -> None:
    """Create bot-bound events even when no browser is polling the web inbox."""
    service = notification_service()
    service.sync_tasks(TASKS.raw_states())
    for tenant_id in telegram_link_service().active_tenant_ids():
        service.ensure_expiry_reminders(tenant_id)


def inventory_service() -> InventoryService:
    """Return inventory storage bound to the active application database."""
    global INVENTORY
    if Path(INVENTORY.db_path).resolve() != Path(DB_PATH).resolve():
        INVENTORY = InventoryService(DB_PATH)
    return INVENTORY


def warm_data_cache() -> None:
    try:
        DATA.rows()
    except Exception:
        pass


threading.Thread(target=warm_data_cache, daemon=True).start()

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=environment_flag("ITP_COOKIE_SECURE", IS_PRODUCTION),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=int(CFG["app"].get("session_hours", 12))),
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    PREFERRED_URL_SCHEME="https" if IS_PRODUCTION else "http",
)
trusted_hosts = [
    item.strip()
    for item in str(os.environ.get("ITP_TRUSTED_HOSTS") or "").split(",")
    if item.strip()
]
if trusted_hosts:
    app.config["TRUSTED_HOSTS"] = trusted_hosts

LOGIN_ATTEMPTS: dict[str, list[float]] = {}
FORM_ATTEMPTS: dict[str, list[float]] = {}
LOGIN_LOCK_SECONDS = 15 * 60
LOGIN_WINDOW_SECONDS = 10 * 60
LOGIN_MAX_ATTEMPTS = 6
RECOVERY_MAX_ATTEMPTS = 5
REGISTRATION_MAX_ATTEMPTS = 8
CODE_RE = re.compile(r"^[A-Za-z0-9:_-]{1,96}$")
SENSITIVE_LOG_RE = re.compile(
    r"(?i)(authorization|api[_-]?key|client[_-]?secret|password|passwd|token|cookie)"
    r"(\s*[:=]\s*|\s+)([^\s,;]+)"
)

ACTION_INFO = {
    "full_sync_all": {
        "label": "Полная синхронизация доступных площадок",
        "resource": [
            "kaspi_browser", "ozon_browser", "ozon_kz", "halyk_api",
            "forte_api", "wildberries_api",
        ],
        "roles": {"admin", "operator"},
        "platform": "system",
    },
    "kaspi_catalog_collect": {
        "label": "Kaspi: сбор каталога",
        "resource": ["kaspi_browser"],
        "roles": {"admin", "operator"},
        "platform": "kaspi",
    },
    "kaspi_price_actualize": {
        "label": "Kaspi: актуализация цен",
        "resource": ["kaspi_browser"],
        "roles": {"admin", "operator"},
        "platform": "kaspi",
    },
    "kaspi_full_sync": {
        "label": "Kaspi: полная синхронизация",
        "resource": ["kaspi_browser"],
        "roles": {"admin", "operator"},
        "platform": "kaspi",
    },
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
        "platform": "system",
    },
    "audit_catalog": {
        "label": "Аудит каталога",
        "resource": ["reports"],
        "roles": {"admin", "operator"},
        "platform": "kaspi",
    },
    "backup_database": {
        "label": "Резервное копирование",
        "resource": ["backups"],
        "roles": {"admin"},
        "platform": "system",
    },
    "ozon_catalog_collect": {"label": "Ozon.ru: сбор каталога", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_price_actualize": {"label": "Ozon.ru: актуализация цен", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_open_browser": {"label": "Ozon.ru: открыть браузер", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_discover": {"label": "Ozon.ru: обнаружение товаров", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_enrich": {"label": "Ozon.ru: характеристики новых товаров", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_market_search": {"label": "Ozon.ru: поиск рыночных предложений", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_refresh_prices": {"label": "Ozon.ru: обновление цен", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_refresh_stale": {"label": "Ozon.ru: обновление характеристик", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_retry": {"label": "Ozon.ru: повтор ошибок", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_full_sync": {"label": "Ozon.ru: полная синхронизация", "resource": ["ozon_browser"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_export": {"label": "Ozon.ru: экспорт реестра", "resource": ["reports"], "roles": {"admin", "operator"}, "platform": "ozon"},
    "ozon_kz_status": {"label": "Ozon.kz: статус сборщика", "resource": ["ozon_kz"], "roles": {"admin", "operator"}, "platform": "ozon_kz"},
    "ozon_kz_catalog_collect": {"label": "Ozon.kz: сбор каталога", "resource": ["ozon_kz"], "roles": {"admin", "operator"}, "platform": "ozon_kz"},
    "ozon_kz_price_actualize": {"label": "Ozon.kz: актуализация цен", "resource": ["ozon_kz"], "roles": {"admin", "operator"}, "platform": "ozon_kz"},
    "ozon_kz_full_sync": {"label": "Ozon.kz: полная синхронизация", "resource": ["ozon_kz"], "roles": {"admin", "operator"}, "platform": "ozon_kz"},
    "halyk_catalog_collect": {"label": "Halyk Market: сбор каталога", "resource": ["halyk_api"], "roles": {"admin", "operator"}, "platform": "halyk_market"},
    "halyk_price_actualize": {"label": "Halyk Market: актуализация цен", "resource": ["halyk_api"], "roles": {"admin", "operator"}, "platform": "halyk_market"},
    "halyk_sync_catalog": {"label": "Halyk Market: синхронизация каталога", "resource": ["halyk_api"], "roles": {"admin", "operator"}, "platform": "halyk_market"},
    "halyk_refresh_offers": {"label": "Halyk Market: точные предложения продавцов", "resource": ["halyk_api"], "roles": {"admin", "operator"}, "platform": "halyk_market"},
    "halyk_full_sync": {"label": "Halyk Market: полная синхронизация", "resource": ["halyk_api"], "roles": {"admin", "operator"}, "platform": "halyk_market"},
    "forte_catalog_collect": {"label": "Forte Market: сбор каталога", "resource": ["forte_api"], "roles": {"admin", "operator"}, "platform": "forte_market"},
    "forte_price_actualize": {"label": "Forte Market: актуализация цен", "resource": ["forte_api"], "roles": {"admin", "operator"}, "platform": "forte_market"},
    "forte_probe": {"label": "Forte Market: проверка подключения", "resource": ["forte_api"], "roles": {"admin", "operator"}, "platform": "forte_market"},
    "forte_sync_catalog": {"label": "Forte Market: синхронизация каталога", "resource": ["forte_api"], "roles": {"admin", "operator"}, "platform": "forte_market"},
    "forte_refresh_offers": {"label": "Forte Market: точные предложения продавцов", "resource": ["forte_api"], "roles": {"admin", "operator"}, "platform": "forte_market"},
    "forte_full_sync": {"label": "Forte Market: полная синхронизация", "resource": ["forte_api"], "roles": {"admin", "operator"}, "platform": "forte_market"},
    "wb_catalog_collect": {"label": "Wildberries: сбор каталога", "resource": ["wildberries_api"], "roles": {"admin", "operator"}, "platform": "wildberries"},
    "wb_price_actualize": {"label": "Wildberries: актуализация цен", "resource": ["wildberries_api"], "roles": {"admin", "operator"}, "platform": "wildberries"},
    "wb_full_sync": {"label": "Wildberries: полная синхронизация", "resource": ["wildberries_api"], "roles": {"admin", "operator"}, "platform": "wildberries"},
}

FILTERABLE_ACTIONS = {
    "kaspi_price_actualize", "update_own_prices", "scan_market", "refresh_market", "retry_errors",
    "ozon_price_actualize", "ozon_enrich", "ozon_market_search", "ozon_refresh_prices",
    "ozon_refresh_stale", "ozon_retry",
    "ozon_kz_price_actualize",
    "halyk_price_actualize", "halyk_refresh_offers",
    "forte_price_actualize", "forte_refresh_offers",
    "export_report",
}
FORCE_ALL_ACTIONS = {
    "full_sync_all",
    "kaspi_catalog_collect", "kaspi_full_sync", "sync_catalog",
    "ozon_catalog_collect", "ozon_discover", "ozon_full_sync", "ozon_export", "ozon_open_browser",
    "ozon_kz_status", "ozon_kz_catalog_collect", "ozon_kz_full_sync",
    "halyk_catalog_collect", "halyk_sync_catalog", "halyk_full_sync",
    "forte_probe", "forte_catalog_collect", "forte_sync_catalog", "forte_full_sync",
    "wb_catalog_collect", "wb_price_actualize", "wb_full_sync",
    "audit_catalog", "backup_database",
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


SUBSCRIPTION_PERMISSION_FEATURES = {
    "view_products": "products", "manage_products": "products",
    "view_inventory": "products", "manage_inventory": "products",
    "manage_product_matching": "products",
    "view_operations": "operations", "run_operations": "operations",
    "manage_operations": "operations", "view_reports": "reports",
    "create_reports": "reports", "manage_filters": "dynamic_filters",
    "manage_users": "team_management",
}


def apply_subscription_permissions(user: dict[str, Any]) -> dict[str, Any]:
    if is_superadmin(user) or not user.get("tenant_id"):
        return user
    entitlement = subscription_service().entitlement(int(user["tenant_id"]))
    features = entitlement.get("features", {}) if entitlement.get("active") else {}
    permissions = dict(user.get("permissions") or {})
    for permission, feature in SUBSCRIPTION_PERMISSION_FEATURES.items():
        if not features.get(feature, False):
            permissions[permission] = False
    user["permissions"] = permissions
    enabled_marketplaces = {
        code for code, value in entitlement.get("marketplaces", {}).items()
        if value.get("enabled")
    }
    for field in ("marketplaces", "available_marketplaces", "marketplace_permissions"):
        values = dict(user.get(field) or {})
        user[field] = {
            code: enabled for code, enabled in values.items()
            if code in enabled_marketplaces
        }
    user["subscription_status"] = entitlement.get("status")
    user["subscription_features"] = features
    return user


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


def visible_tasks(
    user: dict[str, Any] | None = None, *, enrich: bool = True
) -> list[dict[str, Any]]:
    value = user or current_user() or {}
    tasks = TASKS.states() if enrich else TASKS.raw_states()
    if is_superadmin(value):
        return tasks
    tenant_id = value.get("tenant_id")
    user_id = value.get("id")
    permitted = allowed_marketplaces(value)
    result = []
    for task in tasks:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        task_tenant_id = metadata.get("tenant_id")
        same_tenant = (
            task_tenant_id is not None and tenant_id is not None
            and int(task_tenant_id) == int(tenant_id)
        )
        own_legacy_task = (
            task_tenant_id is None and user_id is not None
            and int(metadata.get("requested_by_id") or 0) == int(user_id)
        )
        platform = str(metadata.get("platform") or marketplace_for_action(
            str(task.get("action") or ""), ACTION_INFO
        ))
        action = str(task.get("name") or task.get("action") or "")
        if action == "backup_database":
            continue
        task_platforms = {
            str(item) for item in (metadata.get("platforms") or [])
            if str(item) in MARKETPLACE_CODES
        }
        if platform == "system":
            if task_platforms:
                platform_allowed = task_platforms <= permitted
            else:
                platform_allowed = (
                    int(metadata.get("requested_by_id") or 0) == int(user_id or -1)
                    or LEGACY_MARKETPLACE_CODES <= permitted
                )
        else:
            platform_allowed = platform in permitted
        if (same_tenant or own_legacy_task) and (
            platform_allowed
        ):
            result.append(task)
    return result


def visible_task(task_id: str, user: dict[str, Any] | None = None) -> dict[str, Any] | None:
    return next((task for task in visible_tasks(user) if str(task.get("id")) == str(task_id)), None)


def public_task(task: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the task state without executable commands or server paths."""
    if not task:
        return None
    result = {
        key: value for key, value in task.items()
        if key not in {"command", "log_file", "pid_file"}
    }
    metadata = result.get("metadata")
    if isinstance(metadata, dict):
        result["metadata"] = {
            key: value for key, value in metadata.items()
            if key not in {"command", "log_file", "selection_file", "credentials"}
        }
    return result


def redact_log_text(value: Any) -> str:
    return SENSITIVE_LOG_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", str(value or ""))


def schedule_actions_for_user(user: dict[str, Any]) -> list[dict[str, str]]:
    if (
        not has_permission(user, "run_operations")
        or (not company_is_approved(user.get("tenant_status")) and not is_superadmin(user))
        or (not bool(user.get("tenant_profile_complete")) and not is_superadmin(user))
    ):
        return []
    permitted = allowed_marketplaces(user)
    return [
        {"code": code, "platform": value[0], "name": value[1]}
        for code, value in SCHEDULE_ACTIONS.items()
        if not (code == "backup_database" and not is_superadmin(user))
        and (
            marketplace_for_action(code, ACTION_INFO) == "system"
            or marketplace_for_action(code, ACTION_INFO) in permitted
        )
    ]


def action_access_error(action: str, user: dict[str, Any]) -> str | None:
    if action not in ACTION_INFO:
        return "Выберите поддерживаемую операцию."
    if action == "backup_database" and not is_superadmin(user):
        return "Резервная копия всей базы доступна только platform superadmin."
    if not company_is_approved(user.get("tenant_status")) and not is_superadmin(user):
        return "Компания ещё не подтверждена. Реальные операции доступны после одобрения."
    if not bool(user.get("tenant_profile_complete")) and not is_superadmin(user):
        return "Заполните обязательные поля компании в настройках перед запуском операций."
    if not has_permission(user, "run_operations"):
        return "Нет разрешения на запуск операций."
    if action == "export_report" and not has_permission(user, "create_reports"):
        return "Нет разрешения на формирование отчётов."
    platform = marketplace_for_action(action, ACTION_INFO)
    if platform != "system" and platform not in allowed_marketplaces(user):
        return "Для компании эта площадка не подключена."
    if not is_superadmin(user):
        subscription_error = subscription_service().operation_error(
            int(user.get("tenant_id") or 0), platform
        )
        if subscription_error:
            return subscription_error
    return None


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


def permission_required(permission_code: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        @login_required
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not has_permission(current_user(), permission_code):
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


def platform_permission_required(
    permission_code: str,
) -> Callable[
    [Callable[..., Any]],
    Callable[..., Any],
]:
    def decorator(
        view: Callable[..., Any],
    ) -> Callable[..., Any]:
        @wraps(view)
        @login_required
        def wrapped(
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            if not has_platform_permission(
                current_user(),
                permission_code,
            ):
                if is_api_request():
                    return jsonify(
                        {
                            "ok": False,
                            "error":
                                "\u041d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e "
                                "\u043f\u0440\u0430\u0432 "
                                "\u0434\u043b\u044f "
                                "\u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0438 "
                                "\u0431\u0438\u043b\u043b\u0438\u043d\u0433\u0430.",
                        }
                    ), 403

                abort(403)

            return view(
                *args,
                **kwargs,
            )

        return wrapped

    return decorator


def current_tenant() -> dict[str, Any] | None:
    user = current_user() or {}
    tenant = SAAS.tenant_for_user(int(user["id"])) if user.get("id") and user.get("tenant_id") else None
    if tenant:
        tenant["workspace_profile"] = SAAS.workspace_profile_for_row(tenant)
    return tenant


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
    return set(allowed_marketplaces_from_user(value))


def product_codes_access_error(codes: list[str], user: dict[str, Any]) -> str | None:
    forbidden = {
        marketplace_for_product_code(code)
        for code in codes
        if marketplace_for_product_code(code) not in allowed_marketplaces(user)
    }
    if not forbidden:
        return None
    return "Нет доступа к площадкам выбранных товаров: " + ", ".join(sorted(forbidden))


def report_visible_to_user(report: dict[str, Any], user: dict[str, Any]) -> bool:
    if is_superadmin(user):
        return True
    raw = report.get("platforms_json")
    try:
        platforms = {
            str(value) for value in json.loads(str(raw or "[]"))
            if str(value) in MARKETPLACE_CODES
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        platforms = set()
    if platforms:
        return platforms <= allowed_marketplaces(user)
    # Legacy reports did not record their marketplace scope. Only the author,
    # or a user with the complete legacy access set, may open them.
    return (
        int(report.get("created_by") or 0) == int(user.get("id") or -1)
        or LEGACY_MARKETPLACE_CODES <= allowed_marketplaces(user)
    )


def requested_platform_filters() -> tuple[dict[str, Any], set[str]]:
    filters = product_filters_from_request()
    visible = allowed_marketplaces() & set(MARKETPLACE_CODES)
    requested_one = str(filters.get("platform") or "").strip()
    raw_many = filters.get("platforms")
    if isinstance(raw_many, (list, tuple, set)):
        requested_many = {str(value or "").strip() for value in raw_many if str(value or "").strip()}
    else:
        requested_many = {value.strip() for value in str(raw_many or "").split(",") if value.strip()}
    requested = requested_many | ({requested_one} if requested_one else set())
    if requested - visible:
        raise PermissionError("Нет доступа к одной из выбранных площадок.")
    if not requested:
        if len(visible) == 1:
            filters["platform"] = next(iter(visible))
        elif visible:
            # Keep an unfiltered catalogue inside the effective marketplace
            # grant and use the same tenant/scope cache key as inventory/detail.
            filters["platforms"] = sorted(visible)
        elif not visible:
            filters["platform"] = "__no_marketplace_access__"
    selections: dict[str, list[str]] = {}
    raw_json = request.args.get("attributes", "")
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                selections.update({
                    str(key)[:80]: _payload_filter_values(value, 500)
                    for key, value in parsed.items()
                })
        except json.JSONDecodeError:
            raise PermissionError("Некорректный фильтр характеристик.")
    for key in request.args:
        if key.startswith("attr."):
            selections[key[5:][:80]] = _payload_filter_values(
                request.args.getlist(key), 500
            )
    user = current_user() or {}
    selected_platforms = requested or visible
    attribute_codes = CATALOG.matching_product_codes(
        int(user["tenant_id"]), selected_platforms, selections
    )
    filters["attribute_product_codes"] = (
        sorted(attribute_codes) if attribute_codes is not None else None
    )
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
    conn = connect_database(DB_PATH, timeout=30)
    try:
        conn.execute(
            """
            INSERT INTO app_events(user_id,event_type,entity_type,entity_id,details_json,created_at,tenant_id)
            VALUES(?,?,?,?,?,datetime('now'),?)
            """,
            (
                user.get("id"), event_type, entity_type, entity_id,
                json.dumps(redact_sensitive(details or {}), ensure_ascii=False),
                user.get("tenant_id"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def reload_services() -> None:
    global CFG, DB_PATH, AUTH, DATA, CATALOG, INVENTORY, TASKS
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
        forte_seller_name=str(CFG["forte"]["seller_name"]),
        ozon_kz_db_path=ROOT / "collectors" / "ozon_kz" / "data" / "ozon_kz_registry.db",
    )
    CATALOG = CatalogConfigurationService(
        DB_PATH, ROOT / "collectors" / "ozon" / "data" / "ozon_registry.db"
    )
    INVENTORY = InventoryService(DB_PATH)
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
        conn = connect_database(db_path)
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



def py_command(script: Path, *args: str) -> list[str]:
    return [sys.executable, "-u", str(script), *map(str, args)]


def temporary_json(directory: Path, prefix: str, payload: Any) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{prefix}_{secrets.token_hex(10)}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def workflow_command(
    steps: list[tuple[str, list[str]]],
    cleanup_files: list[Path] | None = None,
) -> list[str]:
    manifest_path = temporary_json(
        ROOT / "data" / "workflows",
        "workflow",
        {
            "steps": [{"label": label, "command": command} for label, command in steps],
            "cleanup_files": [str(path) for path in (cleanup_files or [])],
        },
    )
    return py_command(ROOT / "engine" / "workflow_runner.py", "--manifest", str(manifest_path))


def ozon_article_selection(codes: list[str]) -> Path:
    articles = [
        parse_product_code(code)[2]
        for code in codes if marketplace_for_product_code(code) == "ozon"
    ]
    return temporary_json(ROOT / "data" / "operation_selections", "ozon_articles", {"articles": articles})


def command_option(command: list[str], name: str, value: str) -> list[str]:
    result = list(command)
    if name in result:
        index = result.index(name)
        if index + 1 < len(result):
            result[index + 1] = str(value)
        else:
            result.append(str(value))
    else:
        result += [name, str(value)]
    return result


def cleanup_pending_command(command: list[str]) -> None:
    candidates: list[Path] = []
    for option in ("--selection-file", "--articles-file"):
        if option in command:
            try:
                candidates.append(Path(command[command.index(option) + 1]))
            except (IndexError, ValueError):
                pass
    if "--manifest" in command:
        try:
            manifest_path = Path(command[command.index("--manifest") + 1])
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                candidates.extend(Path(str(value)) for value in manifest.get("cleanup_files", []) if str(value).strip())
                for step in manifest.get("steps", []):
                    nested = step.get("command") if isinstance(step, dict) else None
                    if isinstance(nested, list):
                        cleanup_pending_command([str(value) for value in nested])
            candidates.append(manifest_path)
        except (OSError, ValueError, json.JSONDecodeError, IndexError):
            pass
    for path in candidates:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


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


OZON_LEGACY_PROFILE_PATHS = {
    "ozon": ROOT / "collectors" / "ozon" / "chrome_vpn_profile",
    "ozon_kz": ROOT / "collectors" / "ozon" / "chrome_kz_profile",
}
OZON_LEGACY_DEBUG_PORTS = {"ozon": 9222, "ozon_kz": 9333}


def ozon_seller_identity(value: Any) -> str:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return ""
    host = str(parsed.hostname or "").casefold().removeprefix("www.")
    parts = [part.casefold() for part in parsed.path.split("/") if part]
    if host not in {"ozon.ru", "ozon.kz"} or len(parts) < 2 or parts[0] != "seller":
        return ""
    return f"{host}/seller/{parts[1]}"


def legacy_ozon_profile_owner(
    legacy_profile: Path,
    seller_sources: list[dict[str, Any]],
    default_debug_port: int = 0,
) -> int | None:
    """Bind a shared legacy profile only to the seller visible in its browser."""
    try:
        if not legacy_profile.is_dir() or next(legacy_profile.iterdir(), None) is None:
            return None
    except OSError:
        return None
    candidates = {
        int(item.get("id") or 0): ozon_seller_identity(item.get("source_url"))
        for item in seller_sources
        if int(item.get("id") or 0) > 0
        and ozon_seller_identity(item.get("source_url"))
    }
    marker_path = legacy_profile / ".spyon_seller_owner.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker_id = int(marker.get("seller_id") or 0)
        if candidates.get(marker_id) == str(marker.get("seller_identity") or ""):
            return marker_id
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass

    ports: list[int] = (
        [int(default_debug_port)] if 0 < int(default_debug_port or 0) <= 65535 else []
    )
    for name in (".spyon_devtools_port", "DevToolsActivePort"):
        try:
            port = int(
                legacy_profile.joinpath(name)
                .read_text(encoding="utf-8", errors="replace")
                .splitlines()[0]
            )
        except (OSError, IndexError, ValueError):
            continue
        if 0 < port <= 65535:
            ports.append(port)
    browser_identities: set[str] = set()
    if ports:
        import urllib.request

        for port in dict.fromkeys(ports):
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/json/list",
                    headers={"User-Agent": "Spyon-Profile-Owner/3.8"},
                )
                with urllib.request.urlopen(request, timeout=1) as response:
                    tabs = json.loads(response.read().decode("utf-8", errors="replace"))
                browser_identities.update(
                    identity
                    for identity in (
                        ozon_seller_identity(item.get("url"))
                        for item in tabs if isinstance(item, dict)
                    )
                    if identity
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
    matched = [
        (seller_id, identity)
        for seller_id, identity in candidates.items()
        if identity in browser_identities
    ]
    if len(matched) != 1:
        return None
    seller_id, identity = matched[0]
    try:
        marker_path.write_text(
            json.dumps(
                {"seller_id": seller_id, "seller_identity": identity},
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
    except OSError:
        return None
    return seller_id


def browser_profile_for_seller(
    runtime: SellerRuntimeScope | None,
    marketplace_code: str,
    seller_sources: list[dict[str, Any]],
) -> Path | None:
    """Keep an original Ozon session only for its observed active seller.

    Existing installations already have VPN/cookie state in collector-local
    profiles. The live seller URL is persisted as a non-secret owner marker;
    every other seller remains isolated in its seller-scoped runtime profile.
    """
    if runtime is None:
        return None
    legacy_profile = OZON_LEGACY_PROFILE_PATHS.get(
        str(marketplace_code or "").strip().casefold()
    )
    if legacy_profile is None:
        return runtime.profile_dir
    owner_id = legacy_ozon_profile_owner(
        legacy_profile,
        seller_sources,
        OZON_LEGACY_DEBUG_PORTS.get(str(marketplace_code or "").strip().casefold(), 0),
    )
    if int(runtime.tenant_seller_id) == int(owner_id or 0):
        return legacy_profile.resolve()
    return runtime.profile_dir


def build_action_command(
    action: str,
    codes: list[str],
    user_id: int,
    scope: str = "all",
    tenant_seller_id: int | None = None,
) -> list[str]:
    action_user = AUTH.get_user(int(user_id)) or {}
    tenant_id = int(action_user.get("tenant_id") or 0)
    if tenant_id <= 0:
        raise ValueError("Компания пользователя не найдена.")
    default_tenant_id = SAAS.default_tenant_id()
    action_platform = marketplace_for_action(action, ACTION_INFO)
    integration_by_code = {
        str(item.get("integration_code") or ""): item
        for item in SAAS.integrations(tenant_id)
    }
    selected_seller: dict[str, Any] | None = None
    active_sellers: list[dict[str, Any]] = []
    if action_platform in MARKETPLACE_CODES:
        active_sellers = SAAS.sellers(
            tenant_id, action_platform, active_only=True
        )
        if tenant_seller_id not in (None, 0) or active_sellers:
            selected_seller = SAAS.resolve_seller(
                tenant_id, action_platform, tenant_seller_id
            )
    runtime = seller_scope(ROOT, tenant_id, action_platform, selected_seller)
    if runtime:
        runtime.ensure_directories()
    legacy_seller_sources = (
        SAAS.active_seller_sources(action_platform)
        if action_platform in OZON_LEGACY_PROFILE_PATHS
        else []
    )
    browser_profile = browser_profile_for_seller(
        runtime, action_platform, legacy_seller_sources
    )

    def connection(code: str) -> dict[str, Any]:
        if selected_seller and code == action_platform:
            return {
                "seller_name": selected_seller.get("display_name") or "",
                "seller_identifier": selected_seller.get("external_seller_id") or "",
                "seller_url": selected_seller.get("source_url") or "",
                "config": selected_seller.get("config") or {},
                "discovery": selected_seller.get("discovery") or {},
                "tenant_seller_id": selected_seller.get("id"),
            }
        return integration_by_code.get(code) or {}

    def stable_identifier(code: str) -> str:
        value = str(connection(code).get("seller_identifier") or "").strip()
        return "" if value.startswith("candidate:") else value

    kaspi = dict(CFG["kaspi"])
    halyk = dict(CFG["halyk"])
    forte = dict(CFG["forte"])
    wildberries = dict(CFG["wildberries"])
    for code, target, allowed_keys in (
        ("kaspi", kaspi, {"city_id", "zone_id", "timeout_seconds", "retries", "min_delay", "max_delay"}),
        ("halyk_market", halyk, {"location_id", "catalog_query", "catalog_category_id", "page_size", "max_products", "timeout_seconds", "sleep_seconds"}),
        ("forte_market", forte, {"city_id", "category_id", "page_size", "max_products", "timeout_seconds", "sleep_seconds"}),
        ("wildberries", wildberries, {"currency", "destination", "max_products", "timeout_seconds", "retries", "sleep_seconds"}),
    ):
        saved_config = connection(code).get("config")
        if isinstance(saved_config, dict):
            for key in allowed_keys:
                if saved_config.get(key) not in (None, ""):
                    target[key] = saved_config[key]
    if connection("kaspi").get("seller_name"):
        kaspi["seller_name"] = str(connection("kaspi")["seller_name"])
    if stable_identifier("kaspi"):
        kaspi["seller_id"] = stable_identifier("kaspi")
    if connection("halyk_market").get("seller_name"):
        halyk["seller_name"] = str(connection("halyk_market")["seller_name"])
        halyk["catalog_query"] = ""
        halyk["catalog_category_id"] = ""
    if connection("forte_market").get("seller_name"):
        forte["seller_name"] = str(connection("forte_market")["seller_name"])
    forte_identifier = stable_identifier("forte_market")
    forte_source_url = str(connection("forte_market").get("seller_url") or "").strip()
    if forte_identifier and not forte_identifier.startswith("product:"):
        forte["merchant_id"] = forte_identifier
    elif forte_identifier.startswith("product:"):
        forte["merchant_id"] = ""
    elif tenant_id != default_tenant_id:
        forte["merchant_id"] = ""

    if (
        action_platform == "kaspi" and tenant_id != default_tenant_id
        and not stable_identifier("kaspi")
    ):
        raise ValueError("Подключите магазин Kaspi по ссылке в настройках компании.")
    analysis = CFG["analysis"]
    codes_text = ",".join(
        parse_product_code(code)[2]
        for code in codes
        if not code.startswith(("ozon:", "ozon_kz:", "halyk:", "forte:", "wb:"))
    )
    ozon_cli = ROOT / "collectors" / "ozon" / "ozon_collector.py"
    ozon_kz_cli = ROOT / "collectors" / "ozon_kz" / "ozon_kz_collector.py"
    ozon_kz_status_cli = ROOT / "collectors" / "ozon_kz" / "ozon_kz_connector.py"
    halyk_cli = ROOT / "collectors" / "halyk" / "halyk_collector.py"
    halyk_codes_text = ",".join(
        parse_product_code(code)[2]
        for code in codes if marketplace_for_product_code(code) == "halyk_market"
    )
    forte_cli = ROOT / "collectors" / "forte" / "forte_collector.py"
    forte_codes_text = ",".join(
        parse_product_code(code)[2]
        for code in codes if marketplace_for_product_code(code) == "forte_market"
    )
    wildberries_cli = ROOT / "collectors" / "wildberries" / "wildberries_collector.py"
    seller_internal_id = int((selected_seller or {}).get("id") or 0)
    kaspi_profile = runtime.profile_dir if runtime else resolve_path(CFG, "profile")

    if action == "full_sync_all":
        full_sync_actions = {
            "kaspi": "kaspi_full_sync",
            "ozon": "ozon_full_sync",
            "ozon_kz": "ozon_kz_full_sync",
            "halyk_market": "halyk_full_sync",
            "forte_market": "forte_full_sync",
            "wildberries": "wb_full_sync",
        }
        permitted_for_plan = set(MARKETPLACE_CODES)
        if not is_superadmin(action_user):
            entitlement = subscription_service().entitlement(tenant_id)
            permitted_for_plan = {
                code for code, limit in entitlement.get("marketplaces", {}).items()
                if limit.get("enabled")
            }
        connected = [
            code for code in MARKETPLACE_CODES
            if code in allowed_marketplaces(action_user)
            and code in permitted_for_plan
            and code in full_sync_actions
        ]
        if len(connected) < 2:
            raise ValueError(
                "Полная синхронизация доступна при подключении минимум двух площадок."
            )
        steps: list[tuple[str, list[str]]] = []
        for code in sorted(connected):
            sellers = SAAS.sellers(tenant_id, code, active_only=True)
            seller_targets: list[dict[str, Any] | None] = sellers or [None]
            for seller in seller_targets:
                seller_label = str(
                    (seller or {}).get("display_name")
                    or (seller or {}).get("external_seller_id")
                    or ""
                )
                label = ACTION_INFO[full_sync_actions[code]]["label"]
                if seller_label:
                    label = f"{label} — {seller_label}"
                steps.append((
                    label,
                    build_action_command(
                        full_sync_actions[code], [], user_id, "all",
                        int((seller or {}).get("id") or 0) or None,
                    ),
                ))
        return workflow_command(steps)

    if action == "kaspi_catalog_collect":
        return build_action_command(
            "sync_catalog", [], user_id, "all", tenant_seller_id
        )
    if action == "kaspi_price_actualize":
        return workflow_command([
            ("Собственные цены Kaspi", build_action_command("update_own_prices", codes, user_id, scope, tenant_seller_id)),
            ("Предложения продавцов Kaspi", build_action_command("scan_market", codes, user_id, scope, tenant_seller_id)),
        ])
    if action == "kaspi_full_sync":
        return workflow_command([
            ("Каталог Kaspi", build_action_command("sync_catalog", [], user_id, "all", tenant_seller_id)),
            ("Собственные цены Kaspi", build_action_command("update_own_prices", [], user_id, "all", tenant_seller_id)),
            ("Предложения продавцов Kaspi", build_action_command("scan_market", [], user_id, "all", tenant_seller_id)),
        ])
    if action == "ozon_price_actualize":
        selection_path = ozon_article_selection(codes) if codes else None
        operation_limit = str(max(1, len(codes)) if codes else 100000)
        refresh = command_option(build_action_command("ozon_refresh_prices", [], user_id, scope, tenant_seller_id), "--limit", operation_limit)
        market = command_option(build_action_command("ozon_market_search", [], user_id, scope, tenant_seller_id), "--limit", operation_limit)
        if selection_path:
            refresh += ["--articles-file", str(selection_path)]
            market += ["--articles-file", str(selection_path)]
        return workflow_command(
            [("Цены Ozon.ru", refresh), ("Рыночные предложения Ozon.ru", market)],
            [selection_path] if selection_path else [],
        )
    if action == "ozon_kz_price_actualize":
        return build_action_command("ozon_kz_refresh_prices", codes, user_id, scope, tenant_seller_id)
    if action == "halyk_catalog_collect":
        return build_action_command("halyk_sync_catalog", [], user_id, "all", tenant_seller_id)
    if action == "halyk_price_actualize":
        return build_action_command("halyk_refresh_offers", codes, user_id, scope, tenant_seller_id)
    if action == "forte_catalog_collect":
        return build_action_command("forte_sync_catalog", [], user_id, "all", tenant_seller_id)
    if action == "forte_price_actualize":
        return build_action_command("forte_refresh_offers", codes, user_id, scope, tenant_seller_id)

    wildberries_actions = {
        "wb_catalog_collect": "sync-catalog",
        "wb_price_actualize": "refresh-prices",
        "wb_full_sync": "full-sync",
    }
    if action in wildberries_actions:
        seller_id = stable_identifier("wildberries")
        source_url = str(connection("wildberries").get("seller_url") or "").strip()
        if not seller_id or not seller_id.isdigit():
            raise ValueError("Подключите продавца Wildberries по ссылке в настройках компании.")
        command = py_command(
            wildberries_cli, wildberries_actions[action],
            "--db", str(DB_PATH),
            "--tenant-id", str(tenant_id),
            "--tenant-seller-id", str(seller_internal_id),
            "--seller-id", seller_id,
            "--source-url", source_url,
            "--currency", str(wildberries.get("currency") or "kzt"),
            "--destination", str(wildberries.get("destination") or "123585596"),
            "--timeout", str(wildberries.get("timeout_seconds") or 30),
            "--retries", str(wildberries.get("retries") or 4),
            "--sleep", str(wildberries.get("sleep_seconds") or 0.35),
        )
        max_products = int(wildberries.get("max_products") or 0)
        if max_products > 0:
            command += ["--max-products", str(max_products)]
        return command

    if action == "ozon_kz_status":
        return py_command(
            ozon_kz_status_cli, "status",
            "--db", str(
                runtime.registry_path if runtime
                else ROOT / "collectors" / "ozon_kz" / "data" / "ozon_kz_registry.db"
            ),
        )
    ozon_kz_actions = {
        "ozon_kz_catalog_collect": "sync-catalog",
        "ozon_kz_refresh_prices": "refresh-prices",
        "ozon_kz_full_sync": "full-sync",
    }
    if action in ozon_kz_actions:
        kz_connection = connection("ozon_kz")
        source_url = str(kz_connection.get("seller_url") or "").strip()
        if not source_url:
            raise ValueError("Подключите магазин Ozon.kz по ссылке в настройках компании.")
        command = py_command(
            ozon_kz_cli, ozon_kz_actions[action],
            "--db", str(
                runtime.registry_path if runtime
                else ROOT / "collectors" / "ozon_kz" / "data" / "ozon_kz_registry.db"
            ),
            "--app-db", str(DB_PATH),
            "--tenant-id", str(tenant_id),
            "--tenant-seller-id", str(seller_internal_id),
            "--source-url", source_url,
            "--expected-seller", str(
                kz_connection.get("seller_name") or kz_connection.get("seller_identifier") or ""
            ),
        )
        if runtime:
            command += [
                "--runtime-dir", str(runtime.base_dir),
                "--profile-path", str(browser_profile),
                "--debug-port", "0",
            ]
        if action == "ozon_kz_refresh_prices" and codes:
            command += ["--articles", ",".join(
                parse_product_code(code)[2]
                for code in codes if marketplace_for_product_code(code) == "ozon_kz"
            )]
        return command
    ozon_actions = {
        "ozon_open_browser": "open-browser", "ozon_catalog_collect": "sync-catalog",
        "ozon_discover": "discover", "ozon_enrich": "enrich-new", "ozon_market_search": "market-search",
        "ozon_refresh_prices": "refresh-prices", "ozon_refresh_stale": "refresh-stale",
        "ozon_retry": "retry-failed", "ozon_full_sync": "full-sync", "ozon_export": "export",
    }
    if action in ozon_actions:
        command = py_command(ozon_cli, ozon_actions[action])
        ozon_connection = connection("ozon")
        source_url = str(ozon_connection.get("seller_url") or "").strip()
        if source_url:
            command += [
                "--source-url", source_url,
                "--expected-seller", str(
                    ozon_connection.get("seller_name") or ozon_connection.get("seller_identifier") or ""
                ),
                "--tenant-id", str(tenant_id),
                "--tenant-seller-id", str(seller_internal_id),
                "--app-db", str(DB_PATH),
            ]
        if runtime:
            command += [
                "--database-path", str(runtime.registry_path),
                "--runtime-dir", str(runtime.base_dir),
                "--profile-path", str(browser_profile),
                "--debug-port", "0",
            ]
        if action in {"ozon_enrich", "ozon_market_search", "ozon_refresh_prices", "ozon_refresh_stale", "ozon_retry"}:
            command += ["--limit", str(30 if action == "ozon_market_search" else 100)]
        if codes and action in {"ozon_enrich", "ozon_market_search", "ozon_refresh_prices", "ozon_refresh_stale", "ozon_retry"}:
            selection_path = ozon_article_selection(codes)
            command += ["--articles-file", str(selection_path)]
            return workflow_command([(ACTION_INFO[action]["label"], command)], [selection_path])
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
            "--db", str(runtime.registry_path if runtime else DB_PATH),
            "--app-db", str(DB_PATH),
            "--tenant-id", str(tenant_id),
            "--tenant-seller-id", str(seller_internal_id),
            "--seller-name", str(halyk["seller_name"]),
            "--merchant-id", stable_identifier("halyk_market"),
            "--source-url", str(connection("halyk_market").get("seller_url") or ""),
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
    forte_actions = {
        "forte_probe": "probe",
        "forte_sync_catalog": "sync-catalog",
        "forte_refresh_offers": "refresh-offers",
        "forte_full_sync": "full-sync",
    }
    if action in forte_actions:
        merchant_id = str(forte.get("merchant_id") or "").strip()
        forte_discovery = connection("forte_market").get("discovery")
        seed_product_id = str(
            forte_discovery.get("product_id") if isinstance(forte_discovery, dict) else ""
        ).strip()
        if action in {"forte_sync_catalog", "forte_full_sync"} and not merchant_id and not forte_source_url:
            raise ValueError("Подключите продавца или карточку товара Forte Market в настройках.")
        command = py_command(
            forte_cli,
            forte_actions[action],
            "--db", str(runtime.registry_path if runtime else DB_PATH),
            "--app-db", str(DB_PATH),
            "--tenant-id", str(tenant_id),
            "--tenant-seller-id", str(seller_internal_id),
            "--seller-name", str(
                connection("forte_market").get("seller_name")
                or action_user.get("tenant_name")
                or "Компания"
            ),
            "--merchant-id", merchant_id,
            "--source-url", forte_source_url,
            "--seed-product-id", seed_product_id,
            "--city-id", str(forte.get("city_id") or "KZ"),
            "--category-id", str(forte.get("category_id") or ""),
            "--page-size", str(forte.get("page_size") or 100),
            "--workers", str(max(1, int(analysis["discover_workers"]))),
            "--timeout", str(forte.get("timeout_seconds") or 30),
            "--sleep", str(forte.get("sleep_seconds") or 0.25),
        )
        max_products = int(forte.get("max_products") or 0)
        if max_products > 0:
            command += ["--max-products", str(max_products)]
        if action == "forte_refresh_offers" and forte_codes_text:
            command += ["--product-ids", forte_codes_text]
        return command
    if action == "sync_catalog":
        command = py_command(
            ROOT / "engine" / "catalog_sync.py",
            "--db", str(DB_PATH),
            "--tenant-id", str(tenant_id),
            "--tenant-seller-id", str(seller_internal_id),
            "--profile", str(kaspi_profile),
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
            "--tenant-id", str(tenant_id),
            "--tenant-seller-id", str(seller_internal_id),
            "--profile", str(kaspi_profile),
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
            "--tenant-id", str(tenant_id),
            "--tenant-seller-id", str(seller_internal_id),
            "--profile", str(kaspi_profile),
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
        report_user = AUTH.get_user(int(user_id)) or {}
        report_platforms = sorted(allowed_marketplaces(report_user))
        command = py_command(
            ROOT / "engine" / "export_market_intelligence.py",
            "--db", str(DB_PATH),
            "--ozon-db", str(ROOT / "collectors" / "ozon" / "data" / "ozon_registry.db"),
            "--ozon-kz-db", str(ROOT / "collectors" / "ozon_kz" / "data" / "ozon_kz_registry.db"),
            "--output", str(resolve_path(CFG, "output")),
            "--seller-name", str(kaspi["seller_name"]),
            "--seller-id", str(kaspi["seller_id"]),
            "--user-id", str(user_id),
            "--tenant-id", str(int(report_user.get("tenant_id") or 0)),
            "--allowed-platforms", ",".join(report_platforms),
        )
        if codes:
            selection_dir = ROOT / "data" / "report_selections"
            selection_dir.mkdir(parents=True, exist_ok=True)
            selection_path = selection_dir / f"selection_{secrets.token_hex(10)}.json"
            selection_path.write_text(
                json.dumps(
                    {
                        "scope": scope if scope in {"selected", "filtered"} else "selected",
                        "codes": codes,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            command += ["--selection-file", str(selection_path)]
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
    user_loader=AUTH.get_user,
    subscription_service=subscription_service(),
)
if os.environ.get("ITP_DISABLE_SCHEDULER", "0") != "1":
    SCHEDULER.start()
atexit.register(SCHEDULER.stop)

EMAIL_WORKER = EmailOutboxWorker(
    email_service(), interval_seconds=float(os.environ.get("ITP_EMAIL_WORKER_INTERVAL", "5"))
)
if os.environ.get("ITP_DISABLE_EMAIL_WORKER", "0") != "1":
    EMAIL_WORKER.start()
atexit.register(EMAIL_WORKER.stop)

TELEGRAM_WORKER: TelegramBotWorker | None = None
if environment_flag("ITP_TELEGRAM_BOT_ENABLED"):
    telegram_token = str(os.environ.get("ITP_TELEGRAM_BOT_TOKEN") or "").strip()
    if telegram_token:
        TELEGRAM_WORKER = TelegramBotWorker(
            DB_PATH,
            AUTH,
            telegram_token,
            public_url=str(
                os.environ.get("SPYON_PUBLIC_URL")
                or f"https://{os.environ.get('SPYON_DOMAIN') or 'spyon.kz'}"
            ),
            notification_sync=sync_telegram_notification_sources,
        )
        TELEGRAM_WORKER.start()
        atexit.register(TELEGRAM_WORKER.stop)
    else:
        app.logger.error(
            "telegram_bot_disabled reason=ITP_TELEGRAM_BOT_TOKEN_missing"
        )


@app.before_request
def before_request() -> Any:
    g.request_started_at = time.perf_counter()
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        user = AUTH.get_user(int(user_id))
        if user and user.get("is_active"):
            expected_version = session.get("session_version")
            if expected_version is not None and int(expected_version) != int(user.get("session_version") or 0):
                session.clear()
            else:
                # Pre-email-auth sessions did not have a version. Preserve them
                # once, then make them eligible for password-reset revocation.
                session["session_version"] = int(user.get("session_version") or 0)
                g.user = apply_subscription_permissions(user)
        else:
            session.clear()

    if not AUTH.has_users() and request.endpoint not in {
        "setup", "setup_complete", "static", "health", "ready", "landing",
        "registration", "registration_complete", "legal_document",
        "legal_document_version", "legal_pdf",
        "api_public_plans",
    }:
        if is_api_request():
            return json_error("Требуется первичная настройка.", 428)
        return redirect(url_for("setup"))

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.endpoint in {
            "login", "setup", "forgot_password", "registration",
            "resend_verification", "verify_email", "reset_password",
            "accept_invitation",
        }:
            token = request.form.get("csrf_token")
        elif is_api_request():
            token = request.headers.get("X-CSRF-Token")
        else:
            token = request.form.get("csrf_token")
        if not valid_csrf(token):
            if is_api_request():
                return json_error("Сессия устарела. Обновите страницу.", 419)
            flash("Сессия устарела. Обновите страницу и повторите действие.", "error")
            return redirect(request.referrer or url_for("login"))


@app.after_request
def after_request(response: Any) -> Any:
    started_at = getattr(g, "request_started_at", None)
    if started_at is not None:
        duration_ms = max(0.0, (time.perf_counter() - float(started_at)) * 1000)
        response.headers["Server-Timing"] = f"app;dur={duration_ms:.1f}"
        if duration_ms >= 2000 and request.path.startswith("/api/"):
            user = current_user() or {}
            app.logger.warning(
                "slow_request method=%s path=%s status=%s duration_ms=%.1f user_id=%s tenant_id=%s",
                request.method,
                request.path,
                response.status_code,
                duration_ms,
                user.get("id"),
                user.get("tenant_id"),
            )
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
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif request.path.startswith("/static/"):
        # Static URLs are versioned with ?v=<app version>, so re-downloading all
        # CSS, JS and icons on every page transition only wastes time.
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable"
            if request.args.get("v") else "public, max-age=3600"
        )
    else:
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    if request.path.endswith(".js"):
        response.headers["Content-Type"] = "application/javascript; charset=utf-8"
    return response


@app.context_processor
def template_context() -> dict[str, Any]:
    user = current_user()
    template_user = dict(user) if user else None
    visible_catalog = INTEGRATION_CATALOG
    if user and user.get("tenant_id") and not is_superadmin(user):
        company_catalog = SAAS.marketplace_access(
            int(user["tenant_id"]), include_unavailable=False
        )
        available = user.get("available_marketplaces") or {}
        personal = user.get("marketplace_permissions") or {}
        visible_codes = {
            code for code, enabled in available.items()
            if bool(enabled) and personal.get(code, True) is not False
        }
        visible_catalog = [
            item for item in company_catalog
            if str(item.get("code") or "") in visible_codes
        ]
    if template_user is not None:
        template_user["marketplace_sellers"] = {}
        if template_user.get("tenant_id"):
            for marketplace in MARKETPLACE_CODES:
                template_user["marketplace_sellers"][marketplace] = [
                    {
                        "id": int(seller["id"]),
                        "external_seller_id": str(
                            seller.get("external_seller_id") or ""
                        ),
                        "display_name": str(seller.get("display_name") or ""),
                    }
                    for seller in SAAS.sellers(
                        int(template_user["tenant_id"]),
                        marketplace,
                        active_only=True,
                    )
                ]
    return {
        "csrf_token": ensure_csrf(),
        "current_user": template_user,
        "current_tenant": current_tenant(),
        "version": VERSION,
        "integration_catalog": visible_catalog,
        "visible_marketplace_codes": tuple(
            str(item.get("code") or "") for item in visible_catalog
            if str(item.get("code") or "") in MARKETPLACE_CODES
        ),
        "public_capabilities": PUBLIC_CAPABILITIES,
        "public_settings": PUBLIC.settings(),
    }


@app.get("/health")
def health() -> Any:
    return jsonify({"ok": True, "version": VERSION})


@app.get("/ready")
def ready() -> Any:
    """Readiness probe that verifies the configured primary database."""
    conn = None
    try:
        conn = connect_database(DB_PATH, timeout=5)
        conn.execute("SELECT 1").fetchone()
        return jsonify({"ok": True, "version": VERSION})
    except Exception:
        app.logger.exception("Database readiness probe failed")
        return jsonify({"ok": False, "version": VERSION}), 503
    finally:
        if conn is not None:
            conn.close()


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
            session["session_version"] = int(user.get("session_version") or 0)
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
        elif not user.get("email_verified"):
            # Correct credentials are required before exposing the unverified
            # account state, so this does not create an enumeration endpoint.
            session.clear()
            session["pending_verification_email"] = user.get("email")
            session["csrf_token"] = secrets.token_urlsafe(32)
            flash("Подтвердите электронную почту, чтобы продолжить.", "info")
            return redirect(url_for("verification_sent"))
        else:
            LOGIN_ATTEMPTS.pop(ip, None)
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["session_version"] = int(user.get("session_version") or 0)
            session["csrf_token"] = secrets.token_urlsafe(32)
            next_url = request.args.get("next")
            return redirect(next_url if next_url and next_url.startswith("/") else url_for("app_index"))
    return render_template("login.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password() -> Any:
    ensure_csrf()
    if request.method == "POST":
        email_value = normalize_email(str(request.form.get("email") or ""))
        if rate_limit_hit("forgot_password", email_value, RECOVERY_MAX_ATTEMPTS, LOGIN_LOCK_SECONDS):
            # Do not disclose whether the address was registered.
            flash("Если такой адрес зарегистрирован, мы отправили инструкции по восстановлению.", "success")
            return render_template("forgot_password.html"), 429
        try:
            raw_token = AUTH.request_password_reset(email_value, request.remote_addr or "")
            if raw_token:
                user = AUTH.get_user_by_email(email_value)
                if user:
                    email_service().queue_for_user(
                        user_id=int(user["id"]), tenant_id=user.get("tenant_id"),
                        template_key="password_reset", security=True,
                        payload={
                            "recipient_name": user.get("display_name") or "",
                            "action_url": email_action_url("reset_password", token=raw_token),
                            "action_label": "Сбросить пароль",
                        },
                        dedupe_key=(
                            f"password-reset:{int(user['id'])}:"
                            f"{hashlib.sha256(raw_token.encode('utf-8')).hexdigest()[:16]}"
                        ),
                    )
        except (ValueError, RuntimeError):
            # The public answer remains the same for malformed/inactive users and
            # for a temporarily unavailable optional mail channel.
            pass
        flash("Если такой адрес зарегистрирован, мы отправили инструкции по восстановлению.", "success")
        return redirect(url_for("forgot_password"))
    return render_template("forgot_password.html")


@app.get("/verification-sent")
def verification_sent() -> Any:
    ensure_csrf()
    return render_template(
        "verification_sent.html",
        email=session.get("pending_verification_email") or "",
    )


@app.post("/resend-verification")
def resend_verification() -> Any:
    ensure_csrf()
    email_value = normalize_email(
        str(request.form.get("email") or session.get("pending_verification_email") or "")
    )
    if not rate_limit_hit("resend_verification", email_value, 3, LOGIN_LOCK_SECONDS):
        user = AUTH.get_user_by_email(email_value)
        if user and user.get("is_active") and not user.get("email_verified"):
            try:
                queue_verification_email(user, request_ip=request.remote_addr or "")
            except (ValueError, RuntimeError):
                pass
    flash("Если такой адрес зарегистрирован и ещё не подтверждён, мы отправили новую ссылку.", "success")
    return redirect(url_for("verification_sent"))


@app.route("/verify-email/<token>", methods=["GET", "POST"])
def verify_email(token: str) -> Any:
    ensure_csrf()
    status = AUTH.auth_token_status(token, "verify_email")
    if not status:
        return render_template("auth_token_error.html", kind="verification"), 400
    if request.method == "POST":
        user = AUTH.verify_email(token)
        if not user:
            return render_template("auth_token_error.html", kind="verification"), 400

        try:
            finalize_verified_registration(
                user
            )
        except SubscriptionError:
            app.logger.exception(
                "Unable to finalize verified registration"
            )

        session.clear()
        session.permanent = True
        session["user_id"] = int(user["id"])
        session["session_version"] = int(user.get("session_version") or 0)
        session["csrf_token"] = secrets.token_urlsafe(32)
        return render_template("email_verified.html", user=user)
    return render_template("verify_email.html", token=token)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str) -> Any:
    ensure_csrf()
    if not AUTH.auth_token_status(token, "password_reset"):
        return render_template("auth_token_error.html", kind="reset"), 400
    if request.method == "POST":
        password = str(request.form.get("password") or "")
        if password != str(request.form.get("password_confirm") or ""):
            flash("Пароли не совпадают.", "error")
        else:
            try:
                user = AUTH.reset_password_from_token(token, password)
                if user:
                    queue_password_changed_email(user)
                    flash("Пароль изменён. Теперь можно войти.", "success")
                    return redirect(url_for("login"))
                return render_template("auth_token_error.html", kind="reset"), 400
            except ValueError as exc:
                flash(str(exc), "error")
    return render_template("reset_password.html", token=token)


@app.route("/invite/<token>", methods=["GET", "POST"])
def accept_invitation(token: str) -> Any:
    ensure_csrf()
    if not AUTH.auth_token_status(token, "user_invitation"):
        return render_template("auth_token_error.html", kind="invitation"), 400
    if request.method == "POST":
        password = str(request.form.get("password") or "")
        if password != str(request.form.get("password_confirm") or ""):
            flash("Пароли не совпадают.", "error")
        else:
            try:
                user = AUTH.accept_invitation(token, password)
                if user:
                    session.clear()
                    session.permanent = True
                    session["user_id"] = int(user["id"])
                    session["session_version"] = int(user.get("session_version") or 0)
                    session["csrf_token"] = secrets.token_urlsafe(32)
                    return redirect(url_for("app_index"))
                return render_template("auth_token_error.html", kind="invitation"), 400
            except ValueError as exc:
                flash(str(exc), "error")
    return render_template("accept_invitation.html", token=token)


@app.route("/logout", methods=["GET", "POST"])
@login_required
def logout() -> Any:
    session.clear()
    return redirect(url_for("landing"))


@app.get("/")
def landing() -> Any:
    return render_template(
        "landing.html", capabilities=PUBLIC_CAPABILITIES,
        plans=subscription_service().plans(public_only=True), has_users=AUTH.has_users(),
    )


@app.route("/register", methods=["GET", "POST"])
def registration() -> Any:
    ensure_csrf()
    workspace_templates = SAAS.workspace_templates()
    default_workspace_template_code = SAAS.default_workspace_template_code()
    default_workspace_template = next(
        (item for item in workspace_templates if item["code"] == default_workspace_template_code),
        workspace_templates[0] if workspace_templates else {},
    )
    template_data = {
        "public_capabilities": PUBLIC_CAPABILITIES,
        "workspace_templates": workspace_templates,
        "public_integrations": SAAS.public_integrations(),
        "default_workspace_template_code": default_workspace_template_code,
        "default_workspace_template": default_workspace_template,
        "default_marketplaces": default_workspace_template.get("recommended_integrations", []),
        "selected_marketplaces": request.form.getlist("marketplaces") if request.method == "POST" else [],
        "default_theme": default_workspace_template.get("theme", "system"),
        "subscription_plans": subscription_service().plans(public_only=True),
        "selected_plan_code": str(
            request.form.get("plan_code") or request.args.get("plan") or ""
        ),
    }
    if request.method == "POST":
        if rate_limit_hit("registration", str(request.form.get("email") or ""), REGISTRATION_MAX_ATTEMPTS, LOGIN_WINDOW_SECONDS):
            flash("Слишком много заявок с этого адреса. Повторите позже.", "error")
            return render_template("register.html", **template_data), 429
        email = str(request.form.get("email") or "").strip()
        if AUTH.get_user_by_email(email):
            flash("Пользователь с такой почтой уже существует.", "error")
            return render_template("register.html", **template_data), 409
        password = str(request.form.get("password") or "")
        if password != str(request.form.get("password_confirm") or ""):
            flash("Пароли не совпадают.", "error")
            return render_template("register.html", **template_data), 400
        phone_country_code = str(
            request.form.get("phone_country_code") or ""
        ).strip()

        if not re.fullmatch(r"\+\d{1,4}", phone_country_code):
            flash("\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043a\u043e\u0434 \u0441\u0442\u0440\u0430\u043d\u044b.", "error")
            return render_template(
                "register.html",
                **template_data,
            ), 400

        raw_phone = str(
            request.form.get("phone")
            or ""
        ).strip()

        if raw_phone.startswith("+"):
            phone_value = re.sub(
                r"[^\d+]",
                "",
                raw_phone,
            )
        else:
            phone_value = (
                phone_country_code
                + re.sub(
                    r"\D",
                    "",
                    raw_phone,
                )
            )

        plan_code = str(
            request.form.get("plan_code") or ""
        ).strip().casefold()

        public_plan_codes = {
            str(item.get("code") or "").strip().casefold()
            for item in template_data["subscription_plans"]
        }

        if not plan_code:
            flash("\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043f\u0430\u043a\u0435\u0442.", "error")
            return render_template(
                "register.html",
                **template_data,
            ), 400

        if plan_code not in public_plan_codes:
            flash("\u0412\u044b\u0431\u0440\u0430\u043d\u043d\u044b\u0439 \u043f\u0430\u043a\u0435\u0442 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d.", "error")
            return render_template(
                "register.html",
                **template_data,
            ), 400

        payload = {
            "company_name": request.form.get("company_name", ""),
            "registration_number": request.form.get("registration_number", ""),
            "contact_name": request.form.get("contact_name", ""),
            "email": email,
            "phone": phone_value,
            "legal_address": request.form.get(
                "legal_address",
                "",
            ),
            "actual_address": request.form.get(
                "actual_address",
                "",
            ),
            "capabilities": request.form.getlist("capabilities"),
            "marketplaces": request.form.getlist("marketplaces"),
            "estimated_products": request.form.get("estimated_products", "0"),
            "comment": request.form.get("comment", ""),
            "privacy_consent": request.form.get("privacy_consent") == "1",
            "offer_acceptance": request.form.get("offer_acceptance") == "1",
            "locale": request.form.get("locale", "ru"),
            "launch_mode": "self_service",
            "template_code": request.form.get("template_code", ""),
            "theme": request.form.get("theme", ""),
            "source_page": "public_registration",
            "subscription_plan_code": plan_code,
        }
        try:
            validate_password(password, email, str(payload["contact_name"] or payload["company_name"]))
            submission = SAAS.submit_registration_request(payload)
            request_id = int(submission["request_id"])
            profile = dict(submission["workspace_profile"])
            session["registration_request_id"] = request_id
            completion_result: dict[str, Any] = {
                "mode": "self_service",
                "request_id": request_id,
                "company_name": payload["company_name"],
                "contact_name": payload["contact_name"],
                "email": payload["email"],
                "workspace_profile": profile,
            }
            verification_required = bool(
                email_service().settings.enabled
            )
            registration_conn = SAAS._connect()
            try:
                if isinstance(registration_conn, sqlite3.Connection):
                    registration_conn.execute("BEGIN IMMEDIATE")
                provision = SAAS.provision_tenant_from_request(
                    request_id,
                    None,
                    "approved",
                    grant_marketplaces=False,
                    conn=registration_conn,
                    commit=False,
                )
                user, _recovery_code = AUTH.create_user(
                    payload["email"],
                    payload["contact_name"] or payload["company_name"],
                    password,
                    "admin",
                    None,
                    tenant_id=int(provision["tenant_id"]),
                    email_verified=not verification_required,
                    legal_acceptances=LEGAL_DOCUMENTS.acceptance_records(
                        ip_address=request.remote_addr or "",
                        user_agent=request.headers.get("User-Agent", ""),
                        locale=str(payload.get("locale") or "ru"),
                    ),
                    conn=registration_conn,
                    commit=False,
                )
                registration_conn.commit()
            except Exception:
                registration_conn.rollback()
                raise
            finally:
                registration_conn.close()
            locale = str(payload.get("locale") or "ru").strip().casefold()
            if locale not in {"ru", "kk", "en"}:
                locale = "ru"
            DATA.save_preferences(int(user["id"]), {"locale": locale, "theme": str(profile.get("theme") or "system")})
            subscription_request = (
                subscription_service().request_plan(
                    int(provision["tenant_id"]),
                    str(payload["subscription_plan_code"]),
                    int(user["id"]),
                )
            )

            selected_plan = next(
                (
                    dict(item)
                    for item in template_data[
                        "subscription_plans"
                    ]
                    if str(
                        item.get("code") or ""
                    ).strip().casefold()
                    == str(
                        payload[
                            "subscription_plan_code"
                        ]
                    ).strip().casefold()
                ),
                {},
            )

            marketplace_names = {
                str(
                    item.get("code") or ""
                ): str(
                    item.get("name")
                    or item.get("code")
                    or ""
                )
                for item in template_data[
                    "public_integrations"
                ]
            }

            selected_marketplaces = [
                {
                    "code": str(code),
                    "name": marketplace_names.get(
                        str(code),
                        str(code),
                    ),
                }
                for code in profile.get(
                    "selected_integrations",
                    [],
                )
            ]

            completion_result.update(
                {
                    "subscription_request":
                        subscription_request,
                    "plan": selected_plan,
                    "marketplaces":
                        selected_marketplaces,
                    "payment_required": (
                        str(
                            selected_plan.get("code")
                            or ""
                        ).casefold()
                        != "trial"
                        and float(
                            selected_plan.get(
                                "price_amount"
                            )
                            or 0
                        )
                        > 0
                    ),
                }
            )
            if verification_required:
                try:
                    queue_verification_email(
                        user,
                        request_ip=request.remote_addr or "",
                    )
                except (ValueError, RuntimeError):
                    app.logger.exception(
                        "Unable to queue registration verification email"
                    )

                session.clear()
                session["csrf_token"] = secrets.token_urlsafe(32)
                session["pending_verification_email"] = str(
                    user.get("email") or email
                )
                session["registration_request_id"] = request_id

                return redirect(
                    url_for("verification_sent")
                )

            # Compatibility mode while email delivery is disabled.
            # The account is already email_verified=True here, so the
            # selected package follows the same self-service lifecycle.
            finalize_verified_registration(
                user
            )

            session.clear()
            session.permanent = True
            session["user_id"] = int(user["id"])
            session["session_version"] = int(
                user.get("session_version") or 0
            )
            session["csrf_token"] = secrets.token_urlsafe(32)
            session["registration_request_id"] = request_id

            completion_result.update({
                "tenant_id": int(provision["tenant_id"]),
                "tenant": provision["tenant"],
                "request": provision["request"],
                "user": user,
            })

            return render_template(
                "registration_complete.html",
                result=completion_result,
                **template_data,
            )
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("register.html", **template_data)


@app.get("/register/complete")
def registration_complete() -> Any:
    return render_template(
        "registration_complete.html",
        request_id=session.pop("registration_request_id", None),
        result=None,
    )


@app.get("/api/public/plans")
def api_public_plans() -> Any:
    return json_ok(
        plans=subscription_service().plans(public_only=True),
        addons=subscription_service().addons(public_only=True),
    )


@app.get("/legal/<document>/<version>.pdf")
def legal_pdf(document: str, version: str) -> Any:
    definition = LEGAL_DOCUMENTS.get(document, version)
    if definition is None or not definition.pdf_path.is_file():
        abort(404)
    download = str(request.args.get("download") or "").casefold() in {"1", "true", "yes"}
    return send_file(
        definition.pdf_path,
        mimetype="application/pdf",
        as_attachment=download,
        download_name=f"Spyon_{definition.document_type}_{definition.version}.pdf",
        max_age=31536000,
    )


@app.get("/legal/<document>/<version>")
def legal_document_version(document: str, version: str) -> Any:
    definition = LEGAL_DOCUMENTS.get(document, version)
    if definition is None:
        abort(404)
    return render_template(
        "legal_versioned.html",
        legal=LEGAL_DOCUMENTS.metadata(definition),
        document_html=LEGAL_DOCUMENTS.html(definition),
        embedded=str(request.args.get("embed") or "").casefold() in {"1", "true"},
    )


@app.get("/legal/<document>/pdf")
def legal_current_pdf(document: str) -> Any:
    """Serve the published current PDF through a stable public URL."""
    definition = LEGAL_DOCUMENTS.get(document)
    if definition is None:
        abort(404)
    return legal_pdf(definition.document_type, definition.version)


@app.get("/legal/<document>")
def legal_document(document: str) -> Any:
    definition = LEGAL_DOCUMENTS.get(document)
    if definition is not None:
        return legal_document_version(document, definition.version)
    if document not in {"privacy", "terms", "cookies", "consent", "offer"}: abort(404)
    locale=str(request.args.get("lang") or "ru").casefold()
    try: value=PUBLIC.legal_document(document,locale)
    except KeyError: abort(404)
    return render_template("legal.html", document=value, has_users=AUTH.has_users())


@app.get("/app")
@login_required
def app_index() -> Any:
    return render_template("app.html")


@app.get("/platform")
@platform_roles_required("superadmin", "accountant")
def platform_root() -> Any:
    user = current_user() or {}

    if (
        str(
            user.get("platform_role")
            or ""
        )
        == "accountant"
    ):
        return redirect(
            url_for(
                "platform_index",
                section="payments",
            )
        )

    return render_template(
        "platform.html",
        platform_section="companies",
    )


@app.get("/platform/<section>")
@platform_roles_required("superadmin", "accountant")
def platform_index(section: str) -> Any:
    value = str(
        section
        or ""
    ).strip().casefold()

    if value not in {
        "companies",
        "packages",
        "link-rules",
        "payments",
    }:
        abort(404)

    user = current_user() or {}

    if (
        str(
            user.get("platform_role")
            or ""
        )
        == "accountant"
        and value != "payments"
    ):
        abort(403)

    return render_template(
        "platform.html",
        platform_section=value,
    )


@app.get("/api/session")
@login_required
def api_session() -> Any:
    user = current_user() or {}
    return json_ok(
        user=user,
        tenant=current_tenant(),
        integrations=SAAS.integrations(int(user["tenant_id"]), allowed_only=True) if user.get("tenant_id") else [],
        csrf_token=ensure_csrf(),
        version=VERSION,
    )


@app.get("/api/overview")
@permission_required("view_dashboard")
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
@permission_required("view_products")
def api_product_options() -> Any:
    user = current_user() or {}
    return json_ok(**DATA.filter_options(
        allowed_marketplaces(user), user_id=int(user["id"])
    ))


def product_filters_from_request() -> dict[str, Any]:
    return {
        "query": request.args.get("query", ""),
        "brand": request.args.get("brand", ""),
        "platform": request.args.get("platform", ""),
        "platforms": ",".join(request.args.getlist("platforms")),
        "status": request.args.get("status", ""),
        "watched": request.args.get("watched", ""),
        "freshness": request.args.get("freshness", ""),
        "product_type": request.args.get("product_type", ""),
        "size": request.args.get("size", ""),
        "season": request.args.get("season", ""),
        "characteristic_group": request.args.get("characteristic_group", ""),
        "scope": request.args.get("scope", "all"),
        "sort": request.args.get("sort", "updated"),
        "direction": request.args.get("direction", "desc"),
    }


def _payload_filter_values(value: Any, max_item_length: int, max_items: int = 200) -> list[str]:
    """Normalize scalar, comma-separated and array filter values without changing legacy API calls."""
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = str(value or "").split(",")
    result: list[str] = []
    for raw_value in raw_values:
        item = str(raw_value or "").strip()[:max_item_length]
        if item and item not in result:
            result.append(item)
        if len(result) >= max_items:
            break
    return result


def product_filters_from_payload(raw: Any, user: dict[str, Any]) -> dict[str, Any]:
    """Build the same safe product filter set for background operations and reports."""
    source = raw if isinstance(raw, dict) else {}
    platforms = _payload_filter_values(source.get("platforms"), 40, 10)
    filters = {
        "query": str(source.get("query") or "")[:300],
        "brand": _payload_filter_values(source.get("brand"), 160),
        "platform": str(source.get("platform") or "")[:40],
        "platforms": platforms,
        "status": _payload_filter_values(source.get("status"), 80),
        "watched": str(source.get("watched") or "")[:20],
        "freshness": _payload_filter_values(source.get("freshness"), 40),
        "product_type": _payload_filter_values(source.get("product_type"), 40),
        "size": _payload_filter_values(source.get("size"), 80),
        "season": _payload_filter_values(source.get("season"), 40),
        "characteristic_group": _payload_filter_values(source.get("characteristic_group"), 180, 400),
        "scope": str(source.get("scope") or "all")[:40],
        "sort": str(source.get("sort") or "updated")[:40],
        "direction": str(source.get("direction") or "desc")[:10],
    }
    if filters["scope"] not in {"all", "risks", "opportunities", "unscanned", "watched"}:
        filters["scope"] = "all"
    if filters["direction"].casefold() not in {"asc", "desc"}:
        filters["direction"] = "desc"

    allowed = allowed_marketplaces(user) & set(MARKETPLACE_CODES)
    requested = set(filters["platforms"])
    if filters["platform"]:
        requested.add(filters["platform"])
    if requested - allowed:
        raise PermissionError("Нет доступа к одной из выбранных площадок.")
    if not requested:
        if len(allowed) == 1:
            filters["platform"] = next(iter(allowed))
        elif allowed:
            filters["platforms"] = sorted(allowed)
        elif not allowed:
            filters["platform"] = "__no_marketplace_access__"
    raw_attributes = source.get("attributes")
    selections = {
        str(key)[:80]: _payload_filter_values(value, 500)
        for key, value in (raw_attributes.items() if isinstance(raw_attributes, dict) else [])
    }
    attribute_codes = CATALOG.matching_product_codes(
        int(user["tenant_id"]), requested or allowed, selections
    )
    filters["attribute_product_codes"] = (
        sorted(attribute_codes) if attribute_codes is not None else None
    )
    return filters


@app.get("/api/products")
@permission_required("view_products")
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
@permission_required("view_products")
def api_product_codes() -> Any:
    try:
        filters, _ = requested_platform_filters()
    except PermissionError as exc:
        return json_error(str(exc), 403)
    return json_ok(codes=DATA.product_codes(
        filters, user_id=int((current_user() or {})["id"])
    ))


@app.get("/api/products/<code>")
@permission_required("view_products")
def api_product(code: str) -> Any:
    user = current_user() or {}
    if user.get("tenant_id") is None:
        return json_error("Складской контур доступен только внутри компании.", 403)
    platform = marketplace_for_product_code(code)
    if platform not in allowed_marketplaces(user):
        return json_error("Нет доступа к выбранной площадке.", 403)
    rows = DATA.rows_for_user(int(user["id"]), allowed_marketplaces(user))
    product = DATA.product(code, int(user["id"]), rows=rows)
    if not product:
        return json_error("Товар не найден.", 404)
    try:
        context = inventory_service().context(
            int(user["tenant_id"]), code, rows,
            include_inventory=has_permission(user, "view_inventory"),
        )
    except PermissionError as exc:
        return json_error(str(exc), 403)
    context["can_manage_inventory"] = has_permission(user, "manage_inventory")
    context["can_manage_matching"] = has_permission(user, "manage_product_matching")
    product["inventory_context"] = context
    return json_ok(product=product)


@app.get("/api/inventory/summary")
@permission_required("view_inventory")
def api_inventory_summary() -> Any:
    user = current_user() or {}
    if user.get("tenant_id") is None:
        return json_error("Складской контур доступен только внутри компании.", 403)
    rows = DATA.rows_for_user(int(user["id"]), allowed_marketplaces(user))
    visible_codes = {
        str(row.get("product_code") or "") for row in rows
        if row.get("product_code")
    }
    return json_ok(summary=inventory_service().summary(
        int(user["tenant_id"]), visible_codes
    ))


@app.put("/api/products/<code>/inventory")
@permission_required("manage_inventory")
def api_product_inventory(code: str) -> Any:
    user = current_user() or {}
    if user.get("tenant_id") is None:
        return json_error("Складской контур доступен только внутри компании.", 403)
    if marketplace_for_product_code(code) not in allowed_marketplaces(user):
        return json_error("Нет доступа к выбранной площадке.", 403)
    rows = DATA.rows_for_user(int(user["id"]), allowed_marketplaces(user))
    source = next((row for row in rows if str(row.get("product_code")) == code), None)
    if not source:
        return json_error("Товар не найден в каталоге компании.", 404)
    try:
        inventory_service().save_inventory(
            int(user["tenant_id"]), code, source, json_payload(), int(user["id"])
        )
        context = inventory_service().context(
            int(user["tenant_id"]), code, rows, include_inventory=True
        )
        context["can_manage_inventory"] = True
        context["can_manage_matching"] = has_permission(user, "manage_product_matching")
        return json_ok(inventory_context=context)
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except ValueError as exc:
        return json_error(str(exc))


@app.post("/api/products/<code>/match")
@permission_required("manage_product_matching")
def api_product_match(code: str) -> Any:
    user = current_user() or {}
    if user.get("tenant_id") is None:
        return json_error("Сопоставление доступно только внутри компании.", 403)
    payload = json_payload()
    candidate_code = str(payload.get("candidate_code") or "").strip()
    decision = str(payload.get("decision") or "").strip().casefold()
    if not candidate_code or decision not in {"confirmed", "rejected"}:
        return json_error("Выберите карточку и корректное решение по сопоставлению.")
    if product_codes_access_error([code, candidate_code], user):
        return json_error("Нет доступа к одной из выбранных площадок.", 403)
    rows = DATA.rows_for_user(int(user["id"]), allowed_marketplaces(user))
    by_code = {str(row.get("product_code") or ""): row for row in rows}
    source, candidate = by_code.get(code), by_code.get(candidate_code)
    if not source or not candidate:
        return json_error("Одна из товарных позиций не найдена в каталоге компании.", 404)
    try:
        current_context = inventory_service().context(
            int(user["tenant_id"]), code, rows,
            include_inventory=has_permission(user, "view_inventory"),
        )
        suggestion = next((
            item for item in current_context["matching"]["suggestions"]
            if str(item.get("listing_code")) == candidate_code
        ), None)
        if not suggestion:
            return json_error(
                "Система не нашла безопасного основания для этого сопоставления.", 409
            )
        inventory_service().decide_match(
            int(user["tenant_id"]), source, candidate, decision, int(user["id"]),
            match_method=str(suggestion.get("match_method") or "MANUAL_CONFIRMATION"),
            match_score=float(suggestion.get("match_score") or 0),
            reason=str(suggestion.get("match_reason") or ""),
        )
        context = inventory_service().context(
            int(user["tenant_id"]), code, rows,
            include_inventory=has_permission(user, "view_inventory"),
        )
        context["can_manage_inventory"] = has_permission(user, "manage_inventory")
        context["can_manage_matching"] = True
        return json_ok(inventory_context=context)
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except ValueError as exc:
        return json_error(str(exc), 409)


@app.put("/api/products/state")
@permission_required("manage_products")
def api_product_state() -> Any:
    payload = json_payload()
    codes = clean_codes(payload.get("codes"))
    if not codes:
        return json_error("Не выбраны товары.")
    user = current_user() or {}
    access_error = product_codes_access_error(codes, user)
    if access_error:
        return json_error(access_error, 403)
    try:
        count = DATA.set_product_state(
            codes,
            payload.get("watched") if "watched" in payload else None,
            payload.get("priority") if "priority" in payload else None,
            payload.get("note") if "note" in payload else None,
            int(user["id"]),
            payload.get("expected_monthly_units") if "expected_monthly_units" in payload else None,
        )
        return json_ok(updated=count)
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except ValueError as exc:
        return json_error(str(exc))


@app.get("/api/tasks")
@permission_required("view_operations")
def api_tasks() -> Any:
    return json_ok(tasks=[public_task(task) for task in visible_tasks()])


@app.get("/api/notifications")
@login_required
def api_notifications_get() -> Any:
    user = current_user() or {}
    tasks = visible_tasks(user, enrich=False)
    service = notification_service()
    service.sync_tasks(tasks)
    service.ensure_expiry_reminders(user.get("tenant_id"))
    return json_ok(**service.list_for_user(
        int(user["id"]), int(request.args.get("limit") or 50)
    ))


@app.post("/api/notifications/read-all")
@login_required
def api_notifications_read_all() -> Any:
    return json_ok(updated=notification_service().mark_read(int((current_user() or {})["id"])))


@app.post("/api/notifications/<int:notification_id>/read")
@login_required
def api_notification_read(notification_id: int) -> Any:
    return json_ok(updated=notification_service().mark_read(
        int((current_user() or {})["id"]), notification_id
    ))


@app.get("/api/telegram/status")
@login_required
def api_telegram_status() -> Any:
    user = current_user() or {}
    link = telegram_link_service().status_for_user(int(user["id"]))
    configured_username = str(
        (TELEGRAM_WORKER.bot_username if TELEGRAM_WORKER else "")
        or os.environ.get("ITP_TELEGRAM_BOT_USERNAME")
        or ""
    ).strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,64}", configured_username):
        configured_username = ""
    public_link = None
    if link:
        public_link = {
            "telegram_username": link["telegram_username"],
            "telegram_display_name": link["telegram_display_name"],
            "is_enabled": link["is_enabled"],
            "linked_at": link["linked_at"],
        }
    return json_ok(
        available=bool(
            environment_flag("ITP_TELEGRAM_BOT_ENABLED")
            and configured_username
        ),
        bot_username=configured_username,
        link=public_link,
    )


@app.post("/api/telegram/enabled")
@login_required
def api_telegram_enabled() -> Any:
    user = current_user() or {}
    payload = json_payload()
    enabled = payload.get("enabled") is True
    if not telegram_link_service().set_enabled(int(user["id"]), enabled):
        return json_error("Telegram ещё не привязан.", 404)
    return json_ok(enabled=enabled)


@app.post("/api/telegram/disconnect")
@login_required
def api_telegram_disconnect() -> Any:
    user = current_user() or {}
    disconnected = telegram_link_service().unlink_user(
        int(user["id"]), actor_user_id=int(user["id"])
    )
    return json_ok(disconnected=disconnected)


@app.post("/api/tasks/start")
@permission_required("run_operations")
def api_task_start() -> Any:
    payload = json_payload()
    action = str(payload.get("action") or "")
    info = ACTION_INFO.get(action)
    user = current_user() or {}
    if not info:
        return json_error("Неизвестная операция.")
    access_error = action_access_error(action, user)
    if access_error:
        return json_error(access_error, 403)
    task_platform = marketplace_for_action(action, ACTION_INFO)
    try:
        requested_seller_id = int(payload.get("tenant_seller_id") or 0)
    except (TypeError, ValueError):
        return json_error("Некорректный идентификатор продавца.")
    selected_seller: dict[str, Any] | None = None
    if task_platform in MARKETPLACE_CODES:
        active_sellers = SAAS.sellers(
            int(user["tenant_id"]), task_platform, active_only=True
        )
        if requested_seller_id or active_sellers:
            try:
                selected_seller = SAAS.resolve_seller(
                    int(user["tenant_id"]),
                    task_platform,
                    requested_seller_id or None,
                )
            except PermissionError as exc:
                return json_error(str(exc), 403)
            except ValueError as exc:
                return json_error(str(exc), 409)
    effective_seller_id = int((selected_seller or {}).get("id") or 0)
    codes = clean_codes(payload.get("codes"))
    access_error = product_codes_access_error(codes, user)
    if access_error:
        return json_error(access_error, 403)
    scope = str(payload.get("scope") or ("selected" if codes else "all")).strip().casefold()
    if scope not in {"all", "selected", "filtered"}:
        scope = "all"
    operation_filters: dict[str, Any] = {}

    if action in FORCE_ALL_ACTIONS:
        codes = []
        scope = "all"
    elif scope == "filtered":
        if action not in FILTERABLE_ACTIONS:
            return json_error("Эта операция выполняется только для всего каталога.")
        try:
            operation_filters = product_filters_from_payload(payload.get("filters"), user)
        except PermissionError as exc:
            return json_error(str(exc), 403)
        if task_platform in MARKETPLACE_CODES:
            operation_filters["platform"] = task_platform
            operation_filters["platforms"] = []
        codes = DATA.product_codes(
            operation_filters, limit=10000, user_id=int(user["id"])
        )
        if not codes:
            return json_error("По выбранным фильтрам товары не найдены.")
    elif scope == "selected":
        if action not in FILTERABLE_ACTIONS:
            return json_error("Эта операция выполняется только для всего каталога.")
        if not codes:
            return json_error("Не выбраны товары.")

    if task_platform == "ozon":
        codes = [code for code in codes if code.startswith("ozon:")]
    elif task_platform == "halyk_market":
        codes = [code for code in codes if code.startswith("halyk:")]
    elif task_platform == "forte_market":
        codes = [code for code in codes if code.startswith("forte:")]
    elif task_platform == "kaspi":
        codes = [code for code in codes if not code.startswith(("ozon:", "ozon_kz:", "halyk:", "forte:"))]
    if scope in {"selected", "filtered"} and action != "export_report" and not codes:
        return json_error("Для выбранной площадки подходящие товары не найдены.")
    if codes and effective_seller_id and task_platform in MARKETPLACE_CODES:
        memberships = CATALOG.catalog_memberships(
            int(user["tenant_id"]), [task_platform], effective_seller_id
        )
        permitted_sources = {code for _, code in memberships}
        source_codes = {parse_product_code(code)[2] for code in codes}
        if source_codes - permitted_sources:
            return json_error(
                "Один или несколько товаров не принадлежат выбранному продавцу.",
                403,
            )
    command: list[str] = []
    quota_platforms: list[str] = []
    try:
        command = build_action_command(
            action, codes, int(user["id"]), scope,
            effective_seller_id or None,
        )
        if scope == "filtered":
            suffix = f" — по фильтрам, {len(codes)} поз."
        elif codes:
            suffix = f" — {len(codes)} поз."
        else:
            suffix = " — весь каталог"
        label = info["label"] + (suffix if action not in {"sync_catalog", "audit_catalog", "backup_database"} else "")
        if not is_superadmin(user):
            quota_targets = (
                sorted(allowed_marketplaces(user))
                if action == "full_sync_all"
                else [task_platform]
            )
            for quota_platform in quota_targets:
                subscription_service().consume_operation(
                    int(user["tenant_id"]), quota_platform
                )
                quota_platforms.append(quota_platform)
        task_scope = seller_scope(
            ROOT, int(user["tenant_id"]), task_platform, selected_seller
        )
        task_resources = (
            task_scope.task_resources(info["resource"])
            if task_scope else info["resource"]
        )
        if action == "full_sync_all":
            task_resources = []
            resource_by_platform = {
                "kaspi": "kaspi_browser",
                "ozon": "ozon_browser",
                "ozon_kz": "ozon_kz",
                "halyk_market": "halyk_api",
                "forte_market": "forte_api",
                "wildberries": "wildberries_api",
            }
            for platform in sorted(allowed_marketplaces(user)):
                sellers = SAAS.sellers(
                    int(user["tenant_id"]), platform, active_only=True
                )
                if sellers:
                    task_resources.extend(
                        f"seller:{int(user['tenant_id'])}:{platform}:{int(seller['id'])}"
                        for seller in sellers
                    )
                elif platform in resource_by_platform:
                    task_resources.append(resource_by_platform[platform])
        task = TASKS.start(
            action,
            label,
            command,
            task_resources,
            metadata={
                "scope": scope,
                "codes_count": len(codes),
                "requested_by": user.get("display_name"),
                "requested_by_id": user.get("id"),
                "tenant_id": user.get("tenant_id"),
                "tenant_seller_id": effective_seller_id or None,
                "seller_id": str(
                    (selected_seller or {}).get("external_seller_id") or ""
                ),
                "seller_name": str(
                    (selected_seller or {}).get("display_name") or ""
                ),
                "platform": task_platform,
                "platforms": (
                    sorted(allowed_marketplaces(user))
                    if action == "full_sync_all"
                    else
                    sorted({marketplace_for_product_code(code) for code in codes})
                    if action == "export_report" and codes
                    else sorted(allowed_marketplaces(user))
                    if action == "export_report"
                    else [task_platform] if task_platform in MARKETPLACE_CODES else []
                ),
                "filters": operation_filters,
            },
        )
        record_event("task_started", "task", task["id"], {"action": action, "scope": scope, "codes": len(codes)})
        return json_ok(task=public_task(task))
    except PermissionError as exc:
        for quota_platform in quota_platforms:
            subscription_service().release_operation(
                int(user.get("tenant_id") or 0), quota_platform
            )
        cleanup_pending_command(command)
        return json_error(str(exc), 403)
    except (ValueError, RuntimeError, SubscriptionError) as exc:
        for quota_platform in quota_platforms:
            subscription_service().release_operation(
                int(user.get("tenant_id") or 0), quota_platform
            )
        cleanup_pending_command(command)
        return json_error(str(exc), 409)


@app.post("/api/tasks/<task_id>/stop")
@permission_required("manage_operations")
def api_task_stop(task_id: str) -> Any:
    if not visible_task(task_id):
        return json_error("Операция не найдена.", 404)
    task = TASKS.stop(task_id)
    record_event("task_stopped", "task", task_id, {})
    return json_ok(task=public_task(task))


@app.post("/api/tasks/stop_by_product")
@permission_required("manage_operations")
def api_tasks_stop_by_product() -> Any:
    payload = json_payload()
    product_code = str(payload.get("product_code") or "").strip()
    if not product_code:
        return json_error("Не указан код товара.", 400)
    user = current_user() or {}
    access_error = product_codes_access_error([product_code], user)
    if access_error:
        return json_error(access_error, 403)
    # Determine platform and raw code
    platform = "kaspi"
    raw_code = product_code
    if product_code.startswith("ozon:"):
        platform = "ozon"
        raw_code = product_code.split(":", 1)[1]
    elif product_code.startswith("ozon_kz:"):
        platform = "ozon_kz"
        raw_code = product_code.split(":", 1)[1]
    elif product_code.startswith("halyk:"):
        platform = "halyk_market"
        raw_code = product_code.split(":", 1)[1]
    elif product_code.startswith("forte:"):
        platform = "forte_market"
        raw_code = product_code.split(":", 1)[1]
    stopped: list[str] = []
    for task in visible_tasks(user):
        try:
            if not task.get("running"):
                continue
            meta = task.get("metadata") or {}
            task_platform = str(meta.get("platform") or "").strip()
            # If task is platform-wide (ozon/halyk), stop tasks for same platform
            if platform in {"ozon", "ozon_kz", "halyk_market", "forte_market"} and task_platform and task_platform == platform:
                TASKS.stop(str(task["id"]))
                record_event("task_stopped", "task", str(task["id"]), {"by_product": product_code})
                stopped.append(str(task["id"]))
                continue
            # For kaspi, check command args for the product code (commands include --codes with raw ids)
            if platform == "kaspi":
                cmd = " ".join(map(str, task.get("command") or []))
                if str(raw_code) and str(raw_code) in cmd:
                    TASKS.stop(str(task["id"]))
                    record_event("task_stopped", "task", str(task["id"]), {"by_product": product_code})
                    stopped.append(str(task["id"]))
        except Exception:
            # ignore failures per-task
            continue
    return json_ok(stopped=stopped)


@app.get("/api/tasks/<task_id>/log")
@permission_required("view_operations")
def api_task_log(task_id: str) -> Any:
    task = visible_task(task_id)
    if not task or task.get("status") == "missing":
        return json_error("Операция не найдена.", 404)
    return json_ok(
        task=public_task(task),
        log=redact_log_text(TASKS.tail(task_id, int(request.args.get("lines", 500)))),
    )


@app.delete("/api/tasks/<task_id>")
@permission_required("manage_operations")
def api_task_delete(task_id: str) -> Any:
    if not visible_task(task_id):
        return json_error("Операция не найдена.", 404)
    try:
        task = TASKS.delete(task_id)
        record_event("task_deleted", "task", task_id, {})
        return json_ok(task=public_task(task))
    except RuntimeError as exc:
        return json_error(str(exc), 409)


@app.delete("/api/tasks")
@permission_required("manage_operations")
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
@permission_required("view_dashboard")
def api_analytics_dashboard() -> Any:
    user = current_user() or {}
    try:
        filters, _ = requested_platform_filters()
    except PermissionError as exc:
        return json_error(str(exc), 403)
    return json_ok(analytics=DATA.analytics_dashboard(
        int(user["id"]), allowed_marketplaces(user), filters=filters
    ))


@app.get("/api/reports")
@permission_required("view_reports")
def api_reports() -> Any:
    user = current_user() or {}
    clause, params = tenant_visibility_predicate("r", user)
    conn = connect_database(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""
            SELECT r.id,r.report_type,r.scope,r.file_name,r.rows_count,r.created_at,
                   r.created_by,r.platforms_json,u.display_name
            FROM app_reports r LEFT JOIN app_users u ON u.id=r.created_by
            WHERE {clause}
            ORDER BY r.id DESC LIMIT 100
            """,
            params,
        ).fetchall()
    finally:
        conn.close()
    reports = []
    for row in rows:
        item = dict(row)
        if report_visible_to_user(item, user):
            try:
                item["platforms"] = json.loads(str(item.pop("platforms_json") or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                item["platforms"] = []
            reports.append(item)
    return json_ok(reports=reports)


@app.get("/api/reports/<int:report_id>/download")
@permission_required("view_reports")
def api_report_download(report_id: int) -> Any:
    user = current_user() or {}
    clause, params = tenant_visibility_predicate("app_reports", user)
    conn = connect_database(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            f"""SELECT file_name,file_path,created_by,platforms_json
                FROM app_reports WHERE id=? AND {clause}""",
            [int(report_id), *params],
        ).fetchone()
    finally:
        conn.close()
    if not row or not report_visible_to_user(dict(row), user):
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
        for path in sorted(
            [*folder.glob("*.db"), *folder.glob("*.dump")],
            key=lambda value: value.stat().st_mtime,
            reverse=True,
        )
    ]
    return json_ok(backups=items[:50])


@app.get("/api/backups/<path:name>/download")
@platform_roles_required("superadmin")
def api_backup_download(name: str) -> Any:
    if Path(name).name != name:
        abort(404)
    path = (resolve_path(CFG, "backups") / name).resolve()
    folder = resolve_path(CFG, "backups").resolve()
    if folder not in path.parents or not path.exists() or path.suffix.lower() not in {".db", ".dump"}:
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)


@app.get("/api/events")
@permission_required("view_settings")
def api_events() -> Any:
    user = current_user() or {}
    return json_ok(
        events=DATA.latest_events(
            int(request.args.get("limit", 40)),
            tenant_id=None if is_superadmin(user) else int(user["tenant_id"]),
        )
    )


@app.get("/api/settings")
@permission_required("view_settings")
def api_settings_get() -> Any:
    user = current_user() or {}
    legal_conn = AUTH._connect()
    try:
        legal_documents = LEGAL_DOCUMENTS.accepted_documents_for_user(
            legal_conn,
            int(user["id"]),
            int(user["tenant_id"]) if user.get("tenant_id") else None,
        )
    finally:
        legal_conn.close()
    config_result = None
    if is_superadmin(user):
        config_result = public_config(CFG)
        config_result["ozon"] = load_ozon_public_config()
    return json_ok(
        preferences=DATA.preferences(int(user["id"])),
        notification_preferences=notification_service().preferences_for_user(
            int(user["id"])
        ),
        config=config_result,
        tenant=current_tenant(),
        subscription=(
            subscription_snapshot(
                int(user["tenant_id"])
            )
            if user.get("tenant_id")
            else None
        ),
        legal_documents=legal_documents,
    )


@app.put("/api/settings")
@permission_required("view_settings")
def api_settings_put() -> Any:
    payload = json_payload()
    user = current_user() or {}
    try:
        preferences_payload = payload.get("preferences") if isinstance(payload.get("preferences"), dict) else payload
        notification_preferences_payload = payload.get("notification_preferences")
        tenant_result = None

        if (
            has_permission(user, "manage_company")
            and isinstance(
                payload.get("tenant"),
                dict,
            )
            and user.get("tenant_id")
        ):
            tenant_payload = dict(
                payload["tenant"]
            )

            existing_tenant = (
                current_tenant()
                or {}
            )

            protected_fields = (
                "name",
                "registration_number",
                "contact_email",
                "contact_phone",
                "legal_address",
                "actual_address",
            )

            changed_fields = [
                key
                for key in protected_fields
                if (
                    key in tenant_payload
                    and str(
                        tenant_payload.get(key)
                        or ""
                    ).strip()
                    != str(
                        existing_tenant.get(key)
                        or ""
                    ).strip()
                )
            ]

            if (
                changed_fields
                and not is_superadmin(user)
            ):
                current_password = str(
                    payload.get(
                        "current_password"
                    )
                    or ""
                )

                if not current_password:
                    return json_error(
                        "\u0414\u043b\u044f "
                        "\u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f "
                        "\u0434\u0430\u043d\u043d\u044b\u0445 "
                        "\u043a\u043e\u043c\u043f\u0430\u043d\u0438\u0438 "
                        "\u0432\u0432\u0435\u0434\u0438\u0442\u0435 "
                        "\u0442\u0435\u043a\u0443\u0449\u0438\u0439 "
                        "\u043f\u0430\u0440\u043e\u043b\u044c.",
                        403,
                    )

                if not AUTH.verify_password(
                    int(user["id"]),
                    current_password,
                ):
                    return json_error(
                        "\u0422\u0435\u043a\u0443\u0449\u0438\u0439 "
                        "\u043f\u0430\u0440\u043e\u043b\u044c "
                        "\u0443\u043a\u0430\u0437\u0430\u043d "
                        "\u043d\u0435\u0432\u0435\u0440\u043d\u043e.",
                        403,
                    )

            tenant_result = (
                SAAS.update_tenant_profile(
                    int(user["tenant_id"]),
                    tenant_payload,
                    int(user["id"]),
                )
            )
        preferences = DATA.save_preferences(int(user["id"]), preferences_payload)
        notification_preferences = None
        if isinstance(notification_preferences_payload, dict):
            notification_preferences = notification_service().save_preferences(
                int(user["id"]), notification_preferences_payload
            )

        config_result = None
        if is_superadmin(user) and isinstance(payload.get("config"), dict):
            config_payload = payload["config"]
            updated = load_config()
            kaspi = config_payload.get("kaspi") if isinstance(config_payload.get("kaspi"), dict) else {}
            analysis = config_payload.get("analysis") if isinstance(config_payload.get("analysis"), dict) else {}
            app_values = config_payload.get("app") if isinstance(config_payload.get("app"), dict) else {}
            ozon_values = config_payload.get("ozon") if isinstance(config_payload.get("ozon"), dict) else {}
            halyk_values = config_payload.get("halyk") if isinstance(config_payload.get("halyk"), dict) else {}
            forte_values = config_payload.get("forte") if isinstance(config_payload.get("forte"), dict) else {}
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
            for key in ("seller_name", "merchant_id", "city_id", "category_id"):
                if key in forte_values:
                    updated["forte"][key] = str(forte_values[key]).strip()
            for key, minimum, maximum in (("page_size", 1, 100), ("timeout_seconds", 10, 120), ("max_products", 0, 100000)):
                if key in forte_values:
                    updated["forte"][key] = max(minimum, min(int(forte_values[key]), maximum))
            if "sleep_seconds" in forte_values:
                updated["forte"]["sleep_seconds"] = max(0.0, min(float(forte_values["sleep_seconds"]), 10.0))
            for key, minimum, maximum in (("discover_workers", 1, 4), ("price_workers", 1, 4), ("search_pages", 1, 5), ("validate_top", 1, 12), ("search_cache_days", 0, 90), ("detail_cache_days", 0, 180)):
                if key in analysis:
                    value = float(analysis[key]) if "days" in key else int(analysis[key])
                    updated["analysis"][key] = max(minimum, min(value, maximum))
            if "max_parallel_tasks" in app_values:
                updated["app"]["max_parallel_tasks"] = max(1, min(int(app_values["max_parallel_tasks"]), 12))
            if "product_page_size" in app_values:
                updated["app"]["product_page_size"] = max(10, min(int(app_values["product_page_size"]), 100))
            save_config(updated)
            if ozon_values:
                save_ozon_public_config(ozon_values)
            reload_services()
            config_result = public_config(CFG)
            config_result["ozon"] = load_ozon_public_config()
        record_event("settings_updated", "settings", "user", {"locale": preferences.get("locale")})
        return json_ok(
            preferences=preferences,
            notification_preferences=notification_preferences,
            config=config_result,
            tenant=tenant_result,
        )
    except (ValueError, TypeError) as exc:
        return json_error(f"Некорректные настройки: {exc}")


@app.get("/api/users")
@permission_required("manage_users")
def api_users_get() -> Any:
    return json_ok(users=AUTH.list_users(int((current_user() or {})["tenant_id"])))


@app.post("/api/users")
@permission_required("manage_users")
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
        return json_ok(user=user, recovery_code=recovery)
    except ValueError as exc:
        return json_error(str(exc))


@app.put("/api/users/<int:user_id>")
@permission_required("manage_users")
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
@permission_required("manage_users")
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
@permission_required("manage_users")
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
@permission_required("view_settings")
def api_tenant_get() -> Any:
    user = current_user() or {}
    include_unavailable = (
        str(
            request.args.get("include_unavailable")
            or ""
        ).strip().casefold()
        in {"1", "true", "yes", "on"}
        and has_permission(
            user,
            "manage_marketplaces",
        )
    )

    marketplace_access = SAAS.marketplace_access(
        int(user["tenant_id"]),
        include_unavailable=include_unavailable,
    )
    return json_ok(
        tenant=current_tenant(),
        integrations=SAAS.integrations(int(user["tenant_id"]), allowed_only=True),
        marketplace_access=marketplace_access,
        integration_catalog=[
            {key: item[key] for key in (
                "code", "name", "description", "availability", "connection_fields",
                "credential_fields", "capabilities", "limitations"
            ) if key in item}
            for item in marketplace_access
        ],
    )


@app.get("/api/catalog/filters")
@permission_required("view_products")
def api_catalog_filters_get() -> Any:
    user = current_user() or {}
    permitted = allowed_marketplaces(user)
    requested = {
        value.strip() for value in request.args.get("platforms", "").split(",")
        if value.strip()
    }
    if requested - permitted:
        return json_error("Нет доступа к одной из выбранных площадок.", 403)
    scoped = requested or permitted
    configuration = CATALOG.filter_configuration(int(user["tenant_id"]), scoped)
    return json_ok(**configuration)


@app.put("/api/catalog/filters")
@permission_required("manage_filters")
def api_catalog_filters_put() -> Any:
    user = current_user() or {}
    payload = json_payload()
    filters = payload.get("filters")
    if not isinstance(filters, list):
        return json_error("Передайте массив filters.")
    visible_configuration = CATALOG.filter_configuration(
        int(user["tenant_id"]), allowed_marketplaces(user)
    )
    visible_keys = {
        str(item.get("attribute_key") or "")
        for item in visible_configuration.get("filters", [])
    }
    requested_keys = {
        str(item.get("attribute_key") or "")
        for item in filters if isinstance(item, dict)
    }
    if requested_keys - visible_keys:
        return json_error("Нет доступа к одной из характеристик каталога.", 403)
    try:
        CATALOG.update_filters(
            int(user["tenant_id"]), filters, int(user["id"])
        )
        configuration = CATALOG.filter_configuration(
            int(user["tenant_id"]), allowed_marketplaces(user)
        )
        return json_ok(**configuration)
    except ValueError as exc:
        return json_error(str(exc))


@app.post("/api/catalog/attributes/refresh")
@permission_required("manage_filters")
def api_catalog_attributes_refresh() -> Any:
    user = current_user() or {}
    result = CATALOG.refresh_registry(
        int(user["tenant_id"]), allowed_marketplaces(user)
    )
    return json_ok(result=result)


@app.post("/api/tenant/marketplaces/check")
@permission_required("manage_marketplaces")
def api_tenant_marketplace_check() -> Any:
    user = current_user() or {}
    payload = json_payload()
    try:
        result = SAAS.detect_marketplace_url(
            int(user["tenant_id"]),
            str(payload.get("source") or payload.get("seller_url") or payload.get("url") or ""),
            str(payload.get("marketplace_code") or ""),
        )
        return json_ok(result=result)
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except ValueError as exc:
        return json_error(str(exc))


@app.post("/api/tenant/marketplaces/connect")
@permission_required("manage_marketplaces")
def api_tenant_marketplace_connect() -> Any:
    user = current_user() or {}
    payload = json_payload()
    try:
        result = SAAS.connect_marketplace(
            int(user["tenant_id"]),
            str(payload.get("source") or payload.get("seller_url") or payload.get("url") or ""),
            int(user["id"]),
            str(payload.get("marketplace_code") or ""),
        )
        return json_ok(
            result=result,
            marketplace_access=SAAS.marketplace_access(
                int(user["tenant_id"]), include_unavailable=False
            ),
        )
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except ValueError as exc:
        return json_error(str(exc))


@app.get("/api/schedules")
@permission_required("view_operations")
def api_schedules_get() -> Any:
    user = current_user() or {}
    tenant_id = int(user["tenant_id"])
    permitted = allowed_marketplaces(user)
    schedules = [
        item for item in SAAS.schedules(tenant_id)
        if str(item.get("platform") or marketplace_for_action(item.get("action"), ACTION_INFO)) == "system"
        or str(item.get("platform") or marketplace_for_action(item.get("action"), ACTION_INFO)) in permitted
    ]
    visible_schedule_ids = {int(item["id"]) for item in schedules}
    runs = [
        item for item in SAAS.schedule_runs(tenant_id, int(request.args.get("limit", 50)))
        if int(item.get("schedule_id") or 0) in visible_schedule_ids
    ]
    return json_ok(
        schedules=schedules,
        runs=runs,
        actions=schedule_actions_for_user(user),
    )


@app.post("/api/schedules")
@permission_required("run_operations")
def api_schedules_create() -> Any:
    user = current_user() or {}
    payload = json_payload()
    if not is_superadmin(user) and not subscription_service().entitlement(
        int(user["tenant_id"])
    ).get("features", {}).get("schedules", False):
        return json_error("Расписания не входят в активный пакет компании.", 403)
    access_error = action_access_error(str(payload.get("action") or ""), user)
    if access_error:
        return json_error(access_error, 403)
    try:
        return json_ok(schedule=SAAS.create_schedule(int(user["tenant_id"]), payload, int(user["id"])))
    except (ValueError, TypeError) as exc:
        return json_error(str(exc))


@app.put("/api/schedules/<int:schedule_id>")
@permission_required("run_operations")
def api_schedules_update(schedule_id: int) -> Any:
    user = current_user() or {}
    if not is_superadmin(user) and not subscription_service().entitlement(
        int(user["tenant_id"])
    ).get("features", {}).get("schedules", False):
        return json_error("Расписания не входят в активный пакет компании.", 403)
    current = SAAS.schedule(schedule_id, int(user["tenant_id"]))
    if not current:
        return json_error("Расписание не найдено.", 404)
    access_error = action_access_error(str(current.get("action") or ""), user)
    if access_error:
        return json_error(access_error, 403)
    try:
        return json_ok(schedule=SAAS.update_schedule(schedule_id, int(user["tenant_id"]), json_payload(), int(user["id"])))
    except (ValueError, TypeError) as exc:
        return json_error(str(exc))


@app.delete("/api/schedules/<int:schedule_id>")
@permission_required("manage_operations")
def api_schedules_delete(schedule_id: int) -> Any:
    user = current_user() or {}
    current = SAAS.schedule(schedule_id, int(user["tenant_id"]))
    if not current:
        return json_error("Расписание не найдено.", 404)
    platform = marketplace_for_action(str(current.get("action") or ""), ACTION_INFO)
    if platform != "system" and platform not in allowed_marketplaces(user):
        return json_error("Нет доступа к площадке расписания.", 403)
    try:
        SAAS.delete_schedule(schedule_id, int(user["tenant_id"]), int(user["id"]))
        return json_ok(deleted=True)
    except ValueError as exc:
        return json_error(str(exc), 404)


@app.get("/api/subscription")
@permission_required("view_settings")
def api_subscription_get() -> Any:
    user = current_user() or {}

    return json_ok(
        subscription=subscription_snapshot(
            int(user["tenant_id"])
        )
    )


@app.post("/api/subscription/invoice")
@permission_required("manage_company")
def api_subscription_invoice_create() -> Any:
    user = current_user() or {}
    payload = json_payload()

    try:
        months_count = int(
            payload.get(
                "months_count"
            )
            or 0
        )

        billing = billing_service()

        state = (
            billing
            .tenant_billing_snapshot(
                int(user["tenant_id"])
            )
        )

        subscription = (
            state.get(
                "subscription"
            )
            or {}
        )

        if not subscription:
            raise SubscriptionError(
                "\u041d\u0435\u0442 "
                "\u043f\u0430\u043a\u0435\u0442\u0430, "
                "\u0434\u043b\u044f "
                "\u043a\u043e\u0442\u043e\u0440\u043e\u0433\u043e "
                "\u043c\u043e\u0436\u043d\u043e "
                "\u0441\u0444\u043e\u0440\u043c\u0438\u0440\u043e\u0432\u0430\u0442\u044c "
                "\u0441\u0447\u0451\u0442."
            )

        subscription_status = str(
            subscription.get(
                "status"
            )
            or ""
        )

        existing_invoice = (
            state.get(
                "invoice"
            )
            or {}
        )

        retry_pdf = (
            subscription_status
            in {
                "awaiting_payment",
                "payment_review",
                "payment_rejected",
            }
            and int(
                existing_invoice.get(
                    "id"
                )
                or 0
            ) > 0
            and str(
                existing_invoice.get(
                    "status"
                )
                or ""
            ) == "issued"
            and not bool(
                existing_invoice.get(
                    "pdf_ready"
                )
            )
        )

        if (
            subscription_status
            != "awaiting_invoice"
            and not retry_pdf
        ):
            raise SubscriptionError(
                "\u0421\u0447\u0451\u0442 "
                "\u0443\u0436\u0435 "
                "\u0441\u0444\u043e\u0440\u043c\u0438\u0440\u043e\u0432\u0430\u043d "
                "\u0438\u043b\u0438 "
                "\u043f\u0430\u043a\u0435\u0442 "
                "\u0435\u0449\u0451 "
                "\u043d\u0435 "
                "\u0433\u043e\u0442\u043e\u0432 "
                "\u043a "
                "\u043e\u043f\u043b\u0430\u0442\u0435."
            )

        if retry_pdf:
            billing.generate_invoice_pdf(
                int(
                    existing_invoice[
                        "id"
                    ]
                )
            )

            return json_ok(
                billing=(
                    billing
                    .tenant_billing_snapshot(
                        int(
                            user[
                                "tenant_id"
                            ]
                        )
                    )
                )
            )

        supplier = (
            billing.supplier_settings()
        )

        if not supplier.get(
            "is_complete"
        ):
            raise SubscriptionError(
                "\u0420\u0435\u043a\u0432\u0438\u0437\u0438\u0442\u044b "
                "\u0434\u043b\u044f "
                "\u0432\u044b\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u0438\u044f "
                "\u0441\u0447\u0451\u0442\u0430 "
                "\u0435\u0449\u0451 "
                "\u043d\u0435 "
                "\u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d\u044b."
            )

        seller_snapshot = dict(
            supplier
        )

        seller_snapshot.pop(
            "is_complete",
            None,
        )

        seller_snapshot.pop(
            "missing_fields",
            None,
        )

        invoice = billing.create_invoice(
            int(
                subscription[
                    "id"
                ]
            ),
            months_count,
            int(user["id"]),
            seller_snapshot=
                seller_snapshot,
            due_days=int(
                supplier.get(
                    "invoice_due_days"
                )
                or 5
            ),
        )

        billing.generate_invoice_pdf(
            int(invoice["id"])
        )

        return json_ok(
            billing=(
                billing
                .tenant_billing_snapshot(
                    int(
                        user["tenant_id"]
                    )
                )
            )
        )

    except (
        SubscriptionError,
        TypeError,
        ValueError,
    ) as exc:
        return json_error(
            str(exc),
            409,
        )


@app.get(
    "/api/subscription/invoice/<int:invoice_id>/pdf"
)
@permission_required("view_settings")
def api_subscription_invoice_pdf(
    invoice_id: int,
) -> Any:
    user = current_user() or {}
    billing = billing_service()

    invoice = billing.invoice_by_id(
        int(invoice_id)
    )

    if (
        not invoice
        or int(
            invoice.get(
                "tenant_id"
            )
            or 0
        )
        != int(
            user["tenant_id"]
        )
    ):
        return json_error(
            "\u0421\u0447\u0451\u0442 "
            "\u043d\u0435 "
            "\u043d\u0430\u0439\u0434\u0435\u043d.",
            404,
        )

    if str(
        invoice.get(
            "status"
        )
        or ""
    ) == "cancelled":
        return json_error(
            "\u0421\u0447\u0451\u0442 "
            "\u043e\u0442\u043c\u0435\u043d\u0451\u043d.",
            409,
        )

    try:
        document = (
            billing.invoice_pdf(
                int(invoice_id)
            )
        )

        return send_file(
            document["path"],
            mimetype="application/pdf",
            as_attachment=True,
            download_name=(
                str(
                    invoice[
                        "invoice_number"
                    ]
                )
                + ".pdf"
            ),
            max_age=0,
        )

    except SubscriptionError as exc:
        return json_error(
            str(exc),
            409,
        )


@app.post("/api/subscription/invoice/<int:invoice_id>/revise")
@permission_required("manage_company")
def api_subscription_invoice_revise(invoice_id: int) -> Any:
    user = current_user() or {}
    payload = json_payload()
    try:
        billing = billing_service()
        supplier = billing.supplier_settings()
        if not supplier.get("is_complete"):
            raise SubscriptionError(
                "Реквизиты для выставления счёта ещё не настроены."
            )
        seller_snapshot = dict(supplier)
        seller_snapshot.pop("is_complete", None)
        seller_snapshot.pop("missing_fields", None)
        invoice = billing.revise_invoice(
            int(invoice_id),
            int(user["tenant_id"]),
            int(user["id"]),
            int(payload.get("months_count") or 0),
            seller_snapshot=seller_snapshot,
            due_days=int(supplier.get("invoice_due_days") or 5),
        )
        billing.generate_invoice_pdf(int(invoice["id"]))
        return json_ok(
            billing=billing.tenant_billing_snapshot(int(user["tenant_id"]))
        )
    except (SubscriptionError, TypeError, ValueError) as exc:
        return json_error(str(exc), 409)


@app.post(
    "/api/subscription/invoice/<int:invoice_id>/payment-proof"
)
@permission_required("manage_company")
def api_subscription_payment_proof_upload(
    invoice_id: int,
) -> Any:
    user = current_user() or {}

    upload = request.files.get(
        "file"
    )

    if upload is None:
        return json_error(
            "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 "
            "\u0444\u0430\u0439\u043b "
            "\u043f\u043b\u0430\u0442\u0451\u0436\u043d\u043e\u0433\u043e "
            "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430.",
            400,
        )

    content = upload.read(
        PAYMENT_PROOF_MAX_BYTES + 1
    )

    try:
        billing = billing_service()

        billing.save_payment_proof(
            int(invoice_id),
            int(user["tenant_id"]),
            int(user["id"]),
            original_filename=str(
                upload.filename
                or ""
            ),
            mime_type=str(
                upload.mimetype
                or upload.content_type
                or ""
            ),
            content=content,
        )

        return json_ok(
            billing=(
                billing
                .tenant_billing_snapshot(
                    int(
                        user["tenant_id"]
                    )
                )
            )
        )

    except (
        SubscriptionError,
        TypeError,
        ValueError,
    ) as exc:
        return json_error(
            str(exc),
            409,
        )


@app.get(
    "/api/subscription/invoice/<int:invoice_id>/payment-proof"
)
@permission_required("view_settings")
def api_subscription_payment_proof_download(
    invoice_id: int,
) -> Any:
    user = current_user() or {}
    billing = billing_service()

    invoice = billing.invoice_by_id(
        int(invoice_id)
    )

    if (
        not invoice
        or int(
            invoice.get(
                "tenant_id"
            )
            or 0
        )
        != int(
            user["tenant_id"]
        )
    ):
        return json_error(
            "\u041f\u043b\u0430\u0442\u0451\u0436\u043d\u044b\u0439 "
            "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442 "
            "\u043d\u0435 "
            "\u043d\u0430\u0439\u0434\u0435\u043d.",
            404,
        )

    proof = (
        billing
        .payment_proof_for_invoice(
            int(invoice_id),
            tenant_id=int(
                user["tenant_id"]
            ),
        )
    )

    if not proof:
        return json_error(
            "\u041f\u043b\u0430\u0442\u0451\u0436\u043d\u044b\u0439 "
            "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442 "
            "\u043d\u0435 "
            "\u043d\u0430\u0439\u0434\u0435\u043d.",
            404,
        )

    try:
        document = (
            billing.payment_proof_file(
                int(proof["id"]),
                int(user["tenant_id"]),
            )
        )

        extension = (
            document["path"]
            .suffix
            .lower()
        )

        if extension not in {
            ".pdf",
            ".jpg",
            ".png",
        }:
            extension = ""

        download_name = (
            "payment-proof-"
            + str(
                invoice[
                    "invoice_number"
                ]
            )
            + extension
        )

        return send_file(
            document["path"],
            mimetype=str(
                proof.get(
                    "mime_type"
                )
                or "application/octet-stream"
            ),
            as_attachment=True,
            download_name=download_name,
            max_age=0,
        )

    except SubscriptionError as exc:
        return json_error(
            str(exc),
            409,
        )


@app.post("/api/subscription/request")
@permission_required("manage_company")
def api_subscription_request() -> Any:
    user = current_user() or {}
    payload = json_payload()

    tenant_id = int(
        user["tenant_id"]
    )

    actor_id = int(
        user["id"]
    )

    plan_code = str(
        payload.get(
            "plan_code"
        )
        or ""
    ).strip().casefold()

    if not plan_code:
        return json_error(
            "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 "
            "\u043f\u0430\u043a\u0435\u0442.",
            400,
        )

    try:
        result = (
            subscription_service()
            .request_plan(
                tenant_id,
                plan_code,
                actor_id,
                replace_unpaid=True,
            )
        )

        # Package selection does not require platform approval.
        # Marketplace connections have their own approval lifecycle.
        if str(
            result.get(
                "status"
            )
            or ""
        ) == "pending":
            result = (
                subscription_service()
                .review_subscription(
                    int(
                        result[
                            "id"
                        ]
                    ),
                    "approved",
                    actor_id,
                    review_note=(
                        "Self-service package selection"
                    ),
                )
            )

        return json_ok(
            request=result,
            subscription=(
                subscription_service()
                .tenant_snapshot(
                    tenant_id
                )
            ),
        )

    except SubscriptionError as exc:
        return json_error(
            str(exc),
            409,
        )


@app.post("/api/subscription/addons/request")
@permission_required("manage_company")
def api_subscription_addon_request() -> Any:
    user = current_user() or {}
    payload = json_payload()
    try:
        result = subscription_service().request_addon(
            int(user["tenant_id"]), str(payload.get("addon_code") or ""),
            str(payload.get("marketplace_code") or ""), int(payload.get("quantity") or 1),
            int(user["id"]),
        )
        return json_ok(request=result)
    except SubscriptionError as exc:
        return json_error(str(exc), 409)


@app.get("/api/platform/subscriptions")
@platform_roles_required("superadmin")
def api_platform_subscriptions_get() -> Any:
    return json_ok(**subscription_service().admin_snapshot())


@app.post("/api/platform/subscription-plans")
@platform_roles_required("superadmin")
def api_platform_subscription_plan_create() -> Any:
    try:
        return json_ok(plan=subscription_service().save_plan(
            json_payload(), int((current_user() or {})["id"])
        ))
    except SubscriptionError as exc:
        return json_error(str(exc), 409)


@app.put("/api/platform/subscription-plans/<int:plan_id>")
@platform_roles_required("superadmin")
def api_platform_subscription_plan_update(plan_id: int) -> Any:
    payload = json_payload()
    payload["id"] = int(plan_id)
    try:
        return json_ok(plan=subscription_service().save_plan(
            payload, int((current_user() or {})["id"])
        ))
    except SubscriptionError as exc:
        return json_error(str(exc), 409)


@app.post("/api/platform/subscription-addons")
@platform_roles_required("superadmin")
def api_platform_subscription_addon_create() -> Any:
    try:
        return json_ok(addon=subscription_service().save_addon(
            json_payload(), int((current_user() or {})["id"])
        ))
    except SubscriptionError as exc:
        return json_error(str(exc), 409)


@app.put("/api/platform/subscription-addons/<int:addon_id>")
@platform_roles_required("superadmin")
def api_platform_subscription_addon_update(addon_id: int) -> Any:
    payload = json_payload()
    payload["id"] = int(addon_id)
    try:
        return json_ok(addon=subscription_service().save_addon(
            payload, int((current_user() or {})["id"])
        ))
    except SubscriptionError as exc:
        return json_error(str(exc), 409)


@app.post("/api/platform/subscriptions/<int:subscription_id>/<decision>")
@platform_roles_required("superadmin")
def api_platform_subscription_review(subscription_id: int, decision: str) -> Any:
    payload = json_payload()
    try:
        result = subscription_service().review_subscription(
            subscription_id, decision, int((current_user() or {})["id"]),
            term_days=(int(payload["term_days"]) if payload.get("term_days") not in (None, "") else None),
            price_amount=(float(payload["price_amount"]) if payload.get("price_amount") not in (None, "") else None),
            starts_at=str(
                payload.get("starts_at") or ""
            ).strip() or None,
            ends_at=str(
                payload.get("ends_at") or ""
            ).strip() or None,
            review_note=str(payload.get("review_note") or ""),
        )
        return json_ok(subscription=result)
    except (SubscriptionError, TypeError, ValueError) as exc:
        return json_error(str(exc), 409)


@app.put("/api/platform/tenants/<int:tenant_id>/subscription")
@platform_roles_required("superadmin")
def api_platform_tenant_subscription_assign(
    tenant_id: int,
) -> Any:
    payload = json_payload()

    try:
        result = (
            subscription_service()
            .assign_plan(
                tenant_id=tenant_id,
                plan_code=str(
                    payload.get(
                        "plan_code"
                    )
                    or ""
                ),
                actor_user_id=int(
                    (current_user() or {})[
                        "id"
                    ]
                ),
                starts_at=str(
                    payload.get(
                        "starts_at"
                    )
                    or ""
                ).strip() or None,
                ends_at=str(
                    payload.get(
                        "ends_at"
                    )
                    or ""
                ).strip() or None,
                price_amount=(
                    float(
                        payload[
                            "price_amount"
                        ]
                    )
                    if payload.get(
                        "price_amount"
                    )
                    not in (
                        None,
                        "",
                    )
                    else None
                ),
                review_note=str(
                    payload.get(
                        "review_note"
                    )
                    or ""
                ),
            )
        )

        return json_ok(
            subscription=result,
            snapshot=(
                subscription_service()
                .tenant_snapshot(
                    tenant_id
                )
            ),
        )

    except (
        SubscriptionError,
        TypeError,
        ValueError,
    ) as exc:
        return json_error(
            str(exc),
            409,
        )


@app.post("/api/platform/subscription-addons/requests/<int:request_id>/<decision>")
@platform_roles_required("superadmin")
def api_platform_subscription_addon_review(request_id: int, decision: str) -> Any:
    try:
        result = subscription_service().review_addon(
            request_id, decision, int((current_user() or {})["id"]),
            str(json_payload().get("review_note") or ""),
        )
        return json_ok(addon_request=result)
    except SubscriptionError as exc:
        return json_error(str(exc), 409)


@app.get(
    "/api/platform/billing/supplier-settings"
)
@platform_roles_required("superadmin")
def api_platform_billing_supplier_settings_get() -> Any:
    return json_ok(
        supplier=(
            billing_service()
            .supplier_settings()
        )
    )


@app.put(
    "/api/platform/billing/supplier-settings"
)
@platform_roles_required("superadmin")
def api_platform_billing_supplier_settings_put() -> Any:
    try:
        supplier = (
            billing_service()
            .update_supplier_settings(
                json_payload(),
                int(
                    (current_user() or {})[
                        "id"
                    ]
                ),
            )
        )

        return json_ok(
            supplier=supplier
        )

    except (
        SubscriptionError,
        TypeError,
        ValueError,
    ) as exc:
        return json_error(
            str(exc),
            409,
        )

@app.get(
    "/api/platform/billing/payments"
)
@platform_permission_required(
    "billing.payment.view"
)
def api_platform_billing_payments_get() -> Any:
    return json_ok(
        items=(
            billing_service()
            .platform_payment_items()
        )
    )


@app.get(
    "/api/platform/billing/invoices/"
    "<int:invoice_id>/pdf"
)
@platform_permission_required(
    "billing.invoice.download"
)
def api_platform_billing_invoice_pdf(
    invoice_id: int,
) -> Any:
    billing = billing_service()

    invoice = billing.invoice_by_id(
        int(invoice_id)
    )

    if not invoice:
        return json_error(
            "\u0421\u0447\u0451\u0442 "
            "\u043d\u0435 "
            "\u043d\u0430\u0439\u0434\u0435\u043d.",
            404,
        )

    try:
        document = billing.invoice_pdf(
            int(invoice_id)
        )

        number = re.sub(
            r"[^A-Za-z0-9._-]+",
            "-",
            str(
                invoice.get(
                    "invoice_number"
                )
                or invoice_id
            ),
        ).strip(
            ".-"
        )

        if not number:
            number = str(
                int(invoice_id)
            )

        return send_file(
            document["path"],
            mimetype="application/pdf",
            as_attachment=True,
            download_name=(
                "invoice-"
                + number
                + ".pdf"
            ),
            max_age=0,
        )

    except SubscriptionError as exc:
        return json_error(
            str(exc),
            409,
        )


@app.get(
    "/api/platform/billing/invoices/"
    "<int:invoice_id>/payment-proof"
)
@platform_permission_required(
    "billing.payment.view"
)
def api_platform_billing_payment_proof(
    invoice_id: int,
) -> Any:
    billing = billing_service()

    invoice = billing.invoice_by_id(
        int(invoice_id)
    )

    if not invoice:
        return json_error(
            "\u041f\u043b\u0430\u0442\u0451\u0436\u043d\u044b\u0439 "
            "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442 "
            "\u043d\u0435 "
            "\u043d\u0430\u0439\u0434\u0435\u043d.",
            404,
        )

    proof = (
        billing
        .payment_proof_for_invoice(
            int(invoice_id)
        )
    )

    if not proof:
        return json_error(
            "\u041f\u043b\u0430\u0442\u0451\u0436\u043d\u044b\u0439 "
            "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442 "
            "\u043d\u0435 "
            "\u043d\u0430\u0439\u0434\u0435\u043d.",
            404,
        )

    try:
        document = (
            billing
            .payment_proof_file(
                int(
                    proof["id"]
                ),
                int(
                    invoice[
                        "tenant_id"
                    ]
                ),
            )
        )

        extension = (
            Path(
                document["path"]
            )
            .suffix
            .lower()
        )

        if extension not in {
            ".pdf",
            ".jpg",
            ".jpeg",
            ".png",
        }:
            extension = ""

        number = re.sub(
            r"[^A-Za-z0-9._-]+",
            "-",
            str(
                invoice.get(
                    "invoice_number"
                )
                or invoice_id
            ),
        ).strip(
            ".-"
        )

        if not number:
            number = str(
                int(invoice_id)
            )

        return send_file(
            document["path"],
            mimetype=str(
                proof.get(
                    "mime_type"
                )
                or
                "application/octet-stream"
            ),
            as_attachment=True,
            download_name=(
                "payment-proof-"
                + number
                + extension
            ),
            max_age=0,
        )

    except SubscriptionError as exc:
        return json_error(
            str(exc),
            409,
        )


@app.post(
    "/api/platform/billing/invoices/"
    "<int:invoice_id>/confirm"
)
@platform_permission_required(
    "billing.payment.confirm"
)
def api_platform_billing_payment_confirm(
    invoice_id: int,
) -> Any:
    payload = json_payload()

    try:
        result = (
            billing_service()
            .confirm_invoice_payment(
                int(invoice_id),
                int(
                    (
                        current_user()
                        or {}
                    )["id"]
                ),
                note=str(
                    payload.get(
                        "note"
                    )
                    or ""
                ),
            )
        )

        if not result.get("already_confirmed"):
            notify_billing_payment_confirmed(result)

        return json_ok(
            result=result,
            items=(
                billing_service()
                .platform_payment_items()
            ),
        )

    except (
        SubscriptionError,
        TypeError,
        ValueError,
    ) as exc:
        return json_error(
            str(exc),
            409,
        )


@app.post(
    "/api/platform/billing/invoices/"
    "<int:invoice_id>/reject"
)
@platform_permission_required(
    "billing.payment.reject"
)
def api_platform_billing_payment_reject(
    invoice_id: int,
) -> Any:
    payload = json_payload()

    try:
        result = (
            billing_service()
            .reject_invoice_payment(
                int(invoice_id),
                int(
                    (
                        current_user()
                        or {}
                    )["id"]
                ),
                review_note=str(
                    payload.get(
                        "review_note"
                    )
                    or ""
                ),
            )
        )

        return json_ok(
            result=result,
            items=(
                billing_service()
                .platform_payment_items()
            ),
        )

    except (
        SubscriptionError,
        TypeError,
        ValueError,
    ) as exc:
        return json_error(
            str(exc),
            409,
        )


@app.get("/api/platform/overview")
@platform_roles_required("superadmin", "accountant")
def api_platform_overview() -> Any:
    section = str(
        request.args.get("section")
        or "companies"
    ).strip().casefold()

    user = current_user() or {}
    platform_role = str(
        user.get("platform_role")
        or ""
    )

    if (
        platform_role == "accountant"
        and section != "payments"
    ):
        return json_error(
            "\u041d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e "
            "\u043f\u0440\u0430\u0432.",
            403,
        )

    if section == "companies":
        active_subscriptions = (
            subscription_service()
            .active_subscriptions()
        )

        result = SAAS.platform_overview()

        active_by_tenant = {
            int(item["tenant_id"]):
                item
            for item
            in active_subscriptions
        }

        for tenant in result.get(
            "tenants",
            [],
        ):
            tenant["subscription"] = (
                active_by_tenant.get(
                    int(tenant["id"])
                )
            )

        subscriptions: dict[
            str,
            Any,
        ] = {}

    elif section == "packages":
        subscriptions = (
            subscription_service()
            .admin_snapshot()
        )

        result = {
            "tenants": [],
            "totals": {
                "tenants": 0,
                "active_tenants": 0,
                "new_requests": 0,
                "products": 0,
            },
        }

    elif section == "payments":
        snapshot = (
            subscription_service()
            .admin_snapshot()
        )

        subscriptions = {
            "payments":
                snapshot.get(
                    "payments",
                    [],
                ),
            "active_subscriptions":
                snapshot.get(
                    "active_subscriptions",
                    [],
                ),
        }

        result = {
            "tenants": [],
            "totals": {
                "tenants": 0,
                "active_tenants": 0,
                "new_requests": 0,
                "products": 0,
            },
        }

    else:
        subscriptions = {}

        result = {
            "tenants": [],
            "totals": {
                "tenants": 0,
                "active_tenants": 0,
                "new_requests": 0,
                "products": 0,
            },
        }
    return json_ok(
        **result,
        requests=[],
        integration_catalog=SAAS.public_integrations() if section == "link-rules" else [],
        marketplace_source_rules=SAAS.marketplace_source_rules() if section == "link-rules" else {},
        subscriptions=subscriptions,
    )


@app.get("/api/platform/marketplace-source-rules")
@platform_roles_required("superadmin")
def api_platform_marketplace_source_rules_get() -> Any:
    return json_ok(
        marketplace_source_rules=SAAS.marketplace_source_rules(),
        integration_catalog=SAAS.public_integrations(),
    )


@app.put("/api/platform/marketplace-source-rules")
@platform_roles_required("superadmin")
def api_platform_marketplace_source_rules_put() -> Any:
    try:
        rules = SAAS.update_marketplace_source_rules(
            json_payload(), int((current_user() or {})["id"])
        )
        return json_ok(
            marketplace_source_rules=rules,
            integration_catalog=SAAS.public_integrations(),
        )
    except ValueError as exc:
        return json_error(str(exc))


@app.post("/api/platform/marketplace-source-rules/preview")
@platform_roles_required("superadmin")
def api_platform_marketplace_source_rules_preview() -> Any:
    payload = json_payload()
    try:
        return json_ok(result=SAAS.preview_marketplace_source(
            str(payload.get("source") or ""),
            str(payload.get("marketplace_code") or ""),
            payload.get("marketplace_source_rules"),
        ))
    except ValueError as exc:
        return json_error(str(exc))


@app.get("/api/platform/tenants/<int:tenant_id>/detail")
@platform_roles_required("superadmin")
def api_platform_tenant_detail(tenant_id: int) -> Any:
    try:
        detail = SAAS.tenant_detail_with_profile(tenant_id)
        detail["users"] = AUTH.list_users(tenant_id)
        detail["integration_catalog"] = SAAS.public_integrations()
        detail["subscription"] = subscription_service().tenant_snapshot(tenant_id)
        return json_ok(**detail)
    except ValueError as exc: return json_error(str(exc),404)


@app.put("/api/platform/tenants/<int:tenant_id>")
@platform_roles_required("superadmin")
def api_platform_tenant_update(tenant_id: int) -> Any:
    payload = json_payload()
    try:
        actor_id = int((current_user() or {})["id"])
        if any(
            key in payload
            for key in (
                "name",
                "registration_number",
                "contact_email",
                "contact_phone",
                "legal_address",
                "actual_address",
            )
        ):
            SAAS.update_tenant_profile(tenant_id, payload, actor_id)
        tenant = SAAS.update_tenant(tenant_id, payload, actor_id)
        return json_ok(tenant=tenant)
    except ValueError as exc:
        return json_error(str(exc))


@app.put("/api/platform/tenants/<int:tenant_id>/marketplaces")
@platform_roles_required("superadmin")
def api_platform_tenant_marketplaces_update(tenant_id: int) -> Any:
    payload = json_payload()
    values = payload.get("marketplaces")
    if not isinstance(values, (dict, list)):
        return json_error("Передайте marketplaces в виде списка или объекта.")
    try:
        result = SAAS.set_marketplace_access(
            tenant_id, values, int((current_user() or {})["id"])
        )
        return json_ok(marketplace_access=result)
    except ValueError as exc:
        return json_error(str(exc), 409)


@app.post("/api/platform/tenants/<int:tenant_id>/marketplaces/<marketplace_code>/<decision>")
@platform_roles_required("superadmin")
def api_platform_tenant_marketplace_review(
    tenant_id: int, marketplace_code: str, decision: str
) -> Any:
    payload = json_payload()
    try:
        result = SAAS.review_marketplace_connection(
            tenant_id,
            marketplace_code,
            decision,
            int((current_user() or {})["id"]),
            str(payload.get("review_note") or ""),
            int(payload.get("tenant_seller_id") or 0) or None,
        )
        return json_ok(integration=result)
    except ValueError as exc:
        return json_error(str(exc), 409)


@app.put("/api/platform/tenants/<int:tenant_id>/users/<int:user_id>")
@platform_roles_required("superadmin")
def api_platform_tenant_user_update(tenant_id: int, user_id: int) -> Any:
    target = AUTH.get_user(user_id)
    if not target:
        return json_error("Пользователь не найден.", 404)
    if target.get("tenant_id") is None or int(target["tenant_id"]) != int(tenant_id):
        return json_error("Пользователь не относится к выбранной компании.", 404)
    try:
        return json_ok(
            user=AUTH.update_user(
                user_id, json_payload(), int((current_user() or {})["id"])
            )
        )
    except ValueError as exc:
        return json_error(str(exc))


@app.post("/api/platform/tenants/<int:tenant_id>/users/<int:user_id>/recovery")
@platform_roles_required("superadmin")
def api_platform_tenant_user_recovery(tenant_id: int, user_id: int) -> Any:
    target = AUTH.get_user(user_id)
    if not target or target.get("tenant_id") is None or int(target["tenant_id"]) != int(tenant_id):
        return json_error("Пользователь не найден в выбранной компании.", 404)
    try:
        recovery_code = AUTH.regenerate_recovery(
            user_id, int((current_user() or {})["id"])
        )
        return json_ok(recovery_code=recovery_code)
    except ValueError as exc:
        return json_error(str(exc))


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
        return json_ok(request=SAAS.review_registration_v2(request_id,decision,int((current_user() or {})["id"])))
    except ValueError as exc:
        return json_error(str(exc),409)


@app.post("/api/account/password")
@login_required
def api_account_password() -> Any:
    payload = json_payload()

    if (
        payload.get("new_password")
        != payload.get("new_password_confirm")
    ):
        return json_error(
            "Новые пароли не совпадают."
        )

    try:
        user = AUTH.change_password(
            int((current_user() or {})["id"]),
            str(payload.get("current_password") or ""),
            str(payload.get("new_password") or ""),
        )

        # Password change increments session_version.
        # Keep the current browser session valid while
        # previously issued sessions become invalid.
        session["session_version"] = int(
            user.get("session_version") or 0
        )

        try:
            queue_password_changed_email(user)
        except (ValueError, RuntimeError):
            app.logger.exception(
                "Unable to queue password changed email"
            )

        return json_ok(
            message="Пароль изменён."
        )

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
    print("=" * 72)
    serve(
        app,
        host=host,
        port=port,
        threads=12,
        channel_timeout=120,
    )
