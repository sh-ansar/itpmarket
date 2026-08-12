from __future__ import annotations

from typing import Any


COMPANY_STATUS_LABELS = {
    "pending": "На рассмотрении",
    "approved": "Подтверждена",
    "rejected": "Отклонена",
    "blocked": "Заблокирована",
}

# Read compatibility for databases created by older releases.
COMPANY_STATUS_ALIASES = {
    "new": "pending",
    "review": "pending",
    "setup": "approved",
    "active": "approved",
    "confirmed": "approved",
    "approved": "approved",
    "declined": "rejected",
    "suspended": "blocked",
    "archived": "blocked",
    "pending": "pending",
    "rejected": "rejected",
    "blocked": "blocked",
}

PERMISSION_DEFINITIONS: dict[str, str] = {
    "view_dashboard": "Просмотр обзора",
    "view_products": "Просмотр товаров",
    "manage_products": "Изменение состояния товаров",
    "view_operations": "Просмотр операций и расписаний",
    "run_operations": "Запуск marketplace-операций",
    "manage_operations": "Остановка и удаление операций",
    "view_reports": "Просмотр отчётов",
    "create_reports": "Формирование отчётов",
    "view_settings": "Просмотр настроек компании",
    "manage_company": "Изменение профиля компании",
    "manage_marketplaces": "Настройка подключений marketplace",
    "manage_filters": "Настройка фильтров каталога",
    "manage_users": "Управление сотрудниками и правами",
    "view_help": "Просмотр справки",
}

ROLE_DEFAULT_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({
        "view_dashboard", "view_products", "view_reports", "view_settings", "view_help",
    }),
    "operator": frozenset({
        "view_dashboard", "view_products", "manage_products", "view_operations",
        "run_operations", "manage_operations", "view_reports", "create_reports",
        "view_settings", "view_help",
    }),
    "admin": frozenset(PERMISSION_DEFINITIONS),
}

ROLE_LABELS = {
    "admin": "Администратор",
    "operator": "Оператор",
    "viewer": "Наблюдатель",
}


def canonical_company_status(value: Any) -> str:
    return COMPANY_STATUS_ALIASES.get(str(value or "").strip().casefold(), "pending")


def company_is_approved(value: Any) -> bool:
    return canonical_company_status(value) == "approved"


def company_status_label(value: Any) -> str:
    return COMPANY_STATUS_LABELS[canonical_company_status(value)]


def permission_map(codes: set[str] | frozenset[str]) -> dict[str, bool]:
    return {code: code in codes for code in PERMISSION_DEFINITIONS}


def has_permission(user: dict[str, Any] | None, permission: str) -> bool:
    value = user or {}
    if str(value.get("platform_role") or "") == "superadmin":
        return permission in PERMISSION_DEFINITIONS
    permissions = value.get("permissions")
    return bool(
        permission in PERMISSION_DEFINITIONS
        and isinstance(permissions, dict)
        and permissions.get(permission)
    )
