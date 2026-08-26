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
    "view_inventory": "Просмотр остатков и закупочных цен",
    "manage_inventory": "Изменение остатков и закупочных цен",
    "manage_product_matching": "Подтверждение сопоставления товаров",
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

PLATFORM_PERMISSION_DEFINITIONS: dict[str, str] = {
    "billing.view":
        "\u041f\u0440\u043e\u0441\u043c\u043e\u0442\u0440 "
        "\u0431\u0438\u043b\u043b\u0438\u043d\u0433\u0430",
    "billing.invoice.issue":
        "\u0412\u044b\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u0438\u0435 "
        "\u0441\u0447\u0435\u0442\u043e\u0432",
    "billing.invoice.download":
        "\u0421\u043a\u0430\u0447\u0438\u0432\u0430\u043d\u0438\u0435 "
        "\u0441\u0447\u0435\u0442\u043e\u0432",
    "billing.invoice.cancel":
        "\u041e\u0442\u043c\u0435\u043d\u0430 "
        "\u0441\u0447\u0435\u0442\u043e\u0432",
    "billing.payment.view":
        "\u041f\u0440\u043e\u0441\u043c\u043e\u0442\u0440 "
        "\u043e\u043f\u043b\u0430\u0442",
    "billing.payment.confirm":
        "\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435 "
        "\u043e\u043f\u043b\u0430\u0442",
    "billing.payment.reject":
        "\u041e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u0438\u0435 "
        "\u043e\u043f\u043b\u0430\u0442",
    "billing.report.view":
        "\u041f\u0440\u043e\u0441\u043c\u043e\u0442\u0440 "
        "\u0431\u0438\u043b\u043b\u0438\u043d\u0433\u043e\u0432\u044b\u0445 "
        "\u043e\u0442\u0447\u0435\u0442\u043e\u0432",
}

PLATFORM_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "superadmin":
        frozenset(
            PLATFORM_PERMISSION_DEFINITIONS
        ),
    "accountant":
        frozenset(
            PLATFORM_PERMISSION_DEFINITIONS
        ),
    "support":
        frozenset(),
    "technical":
        frozenset(),
    "":
        frozenset(),
}


ROLE_DEFAULT_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({
        "view_dashboard", "view_products", "view_reports", "view_settings", "view_help",
    }),
    "operator": frozenset({
        "view_dashboard", "view_products", "manage_products", "view_operations",
        "run_operations", "manage_operations", "view_reports", "create_reports",
        "view_settings", "view_help", "view_inventory", "manage_inventory",
    }),
    "admin": frozenset(PERMISSION_DEFINITIONS),
}

ROLE_LABELS = {
    "admin": "Администратор",
    "operator": "Оператор",
    "viewer": "Наблюдатель",
}


def has_platform_permission(
    user: dict[str, Any] | None,
    permission: str,
) -> bool:
    value = user or {}

    role = str(
        value.get("platform_role")
        or ""
    ).strip().casefold()

    permissions = (
        PLATFORM_ROLE_PERMISSIONS
        .get(
            role,
            frozenset(),
        )
    )

    return bool(
        permission
        in PLATFORM_PERMISSION_DEFINITIONS
        and permission
        in permissions
    )


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
