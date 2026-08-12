from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class MarketplaceDefinition:
    code: str
    label: str
    product_prefix: str
    action_prefix: str
    description: str
    availability: str = "available"
    seller_id_label: str = "ID продавца"
    seller_url_placeholder: str = ""
    supported_hosts: tuple[str, ...] = ()
    credential_fields: tuple[tuple[str, str], ...] = ()
    capabilities: tuple[str, ...] = ()


# Stable internal codes are intentionally kept separate from user-facing names.
# In particular, ``ozon`` remains the legacy-compatible identifier for Russian Ozon.
MARKETPLACES: tuple[MarketplaceDefinition, ...] = (
    MarketplaceDefinition(
        "kaspi", "Kaspi", "kaspi:", "kaspi_",
        "Каталог продавца, предложения одной карточки и история цен.",
        seller_url_placeholder="https://kaspi.kz/shop/info/merchant/SELLER_ID/address-tab/",
        supported_hosts=("kaspi.kz", "www.kaspi.kz"),
        capabilities=("catalog", "offers", "prices", "attributes"),
    ),
    MarketplaceDefinition(
        "ozon", "Ozon.ru", "ozon:", "ozon_",
        "Российский Ozon: действующий публичный сборщик каталога, характеристик и цен.",
        seller_id_label="ID продавца",
        seller_url_placeholder="https://www.ozon.ru/seller/store-name-123456/",
        supported_hosts=("ozon.ru", "www.ozon.ru"),
        capabilities=("catalog", "offers", "prices", "stock", "attributes"),
    ),
    MarketplaceDefinition(
        "ozon_kz", "Ozon.kz", "ozon_kz:", "ozon_kz_",
        "Казахстанский Ozon: отдельный публичный сборщик каталога, характеристик и цен.",
        seller_url_placeholder="https://ozon.kz/seller/store-name-123456/",
        supported_hosts=("ozon.kz", "www.ozon.kz"),
        capabilities=("catalog", "offers", "prices", "stock", "attributes"),
    ),
    MarketplaceDefinition(
        "halyk_market", "Halyk Market", "halyk:", "halyk_",
        "Каталог продавца, предложения одной карточки и история цен.",
        seller_url_placeholder="https://halykmarket.kz/merchant/...",
        supported_hosts=("halykmarket.kz", "www.halykmarket.kz"),
        capabilities=("catalog", "offers", "prices", "attributes"),
    ),
    MarketplaceDefinition(
        "forte_market", "Forte Market", "forte:", "forte_",
        "Публичный каталог продавца и предложения одной карточки Forte Market.",
        seller_id_label="ID продавца",
        seller_url_placeholder="https://market.forte.kz/items/product-name-123456",
        supported_hosts=("market.forte.kz", "forte.kz", "www.forte.kz"),
        capabilities=("catalog", "offers", "prices", "attributes"),
    ),
    MarketplaceDefinition(
        "wildberries", "Wildberries", "wb:", "wb_",
        "Публичный каталог продавца Wildberries, остатки, рейтинг и цены в тенге.",
        seller_id_label="ID продавца",
        seller_url_placeholder="https://global.wildberries.ru/seller/250000260",
        supported_hosts=(
            "global.wildberries.ru", "www.wildberries.ru", "wildberries.ru",
        ),
        capabilities=("catalog", "prices", "stock", "attributes"),
    ),
)
MARKETPLACE_BY_CODE = {item.code: item for item in MARKETPLACES}
MARKETPLACE_CODES = frozenset(MARKETPLACE_BY_CODE)
LEGACY_MARKETPLACE_CODES = frozenset({"kaspi", "ozon", "halyk_market", "forte_market"})
SYSTEM_ACTIONS = frozenset({"export_report", "backup_database"})


def marketplace_catalog() -> list[dict[str, Any]]:
    """Return the single public registry used by company and platform UIs."""
    return [
        {
            "code": item.code,
            "name": item.label,
            "description": item.description,
            "availability": item.availability,
            "connection_fields": [{
                "key": "seller_url",
                "label": "Ссылка или ID продавца",
                "required": True,
                "type": "text",
                "placeholder": item.seller_url_placeholder,
            }],
            "credential_fields": [
                {"key": key, "label": label, "type": "password", "required": True}
                for key, label in item.credential_fields
            ],
            "capabilities": list(item.capabilities),
            "limitations": "",
        }
        for item in MARKETPLACES
    ]


def marketplace_label(code: Any) -> str:
    value = str(code or "").strip()
    definition = MARKETPLACE_BY_CODE.get(value)
    return definition.label if definition else value


def marketplace_for_product_code(code: Any) -> str:
    value = str(code or "").strip()
    # Check longer prefixes first so ozon_kz never falls through to legacy Ozon.ru.
    for item in sorted(MARKETPLACES, key=lambda row: len(row.product_prefix), reverse=True):
        if value.startswith(item.product_prefix):
            return item.code
    # Historic Kaspi codes were stored without a prefix.
    return "kaspi"


def marketplace_for_action(action: Any, action_info: dict[str, dict[str, Any]] | None = None) -> str:
    value = str(action or "").strip()
    if action_info:
        explicit = str((action_info.get(value) or {}).get("platform") or "").strip()
        if explicit:
            return explicit
    if value in SYSTEM_ACTIONS:
        return "system"
    for item in sorted(MARKETPLACES, key=lambda row: len(row.action_prefix), reverse=True):
        if value.startswith(item.action_prefix):
            return item.code
    # Historic low-level Kaspi actions have no marketplace prefix.
    return "kaspi"


def product_code_marketplaces(codes: Iterable[Any]) -> set[str]:
    return {marketplace_for_product_code(code) for code in codes if str(code or "").strip()}


def allowed_marketplaces_from_user(user: dict[str, Any] | None) -> set[str]:
    value = user or {}
    access = value.get("marketplaces")
    if not isinstance(access, dict):
        return set()
    return {
        code
        for code, enabled in access.items()
        if code in MARKETPLACE_CODES and bool(enabled)
    }
