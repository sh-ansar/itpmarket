from __future__ import annotations

import json
import zlib
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from storage.postgres_compat import PostgresConnection, configure_connection, connect_database, database_error_types

from public_product_service import PUBLIC_CAPABILITIES, CONSENT_VERSION
from marketplace_registry import MARKETPLACE_BY_CODE, marketplace_catalog, marketplace_for_action
from marketplace_source_rules import (
    DEFAULT_MARKETPLACE_SOURCE_RULES,
    merged_marketplace_source_rules,
    parse_marketplace_source,
    validate_marketplace_source_rules,
)
from ozon_source_verification import OzonSourceVerificationError, verify_ozon_storefront
from security_hygiene import redact_sensitive
from tenant_security import (
    COMPANY_STATUS_LABELS,
    ROLE_DEFAULT_PERMISSIONS,
    ROLE_LABELS,
    canonical_company_status,
    company_is_approved,
    company_status_label,
)

INTEGRATION_CATALOG = marketplace_catalog()

PUBLIC_MARKETPLACE_SOURCE_EXAMPLES = {
    "kaspi": [
        "12345678",
        "https://kaspi.kz/shop/m/12345678/products",
    ],
    "ozon": [
        "example-store-123",
        "https://www.ozon.ru/seller/example-store-123/",
    ],
    "ozon_kz": [
        "example-store-456",
        "https://ozon.kz/seller/example-store-456/",
    ],
    "halyk_market": [
        "12345",
        "https://halykmarket.kz/merchant/12345",
    ],
    "forte_market": [
        "example-merchant-123",
        "https://market.forte.kz/merchant/example-merchant-123?type=all",
    ],
    "wildberries": [
        "123456789",
        "https://global.wildberries.ru/seller/123456789",
    ],
}

WORKSPACE_TEMPLATES = [
    {
        "code": "tire",
        "label": "Шины и диски",
        "description": "Подходит для шин, дисков, камер, автоаксессуаров и сервисной розницы.",
        "theme": "dark",
        "theme_label": "Тёмная",
        "recommended_integrations": ["kaspi", "ozon", "halyk_market", "forte_market"],
        "categories": ["Шины", "Диски", "Камеры", "Автоаксессуары"],
        "marketplace_categories": {
            "kaspi": ["Шины", "Диски", "Камеры"],
            "ozon": ["Автомобильные шины", "Колёса и диски", "Автоаксессуары"],
            "halyk_market": ["Шины и диски", "Автотовары"],
            "forte_market": ["Шины", "Диски", "Автомобильные аксессуары"],
        },
    },
    {
        "code": "electronics",
        "label": "Электроника",
        "description": "Для смартфонов, ноутбуков, аксессуаров и бытовой техники.",
        "theme": "system",
        "theme_label": "Системная",
        "recommended_integrations": ["kaspi", "ozon", "forte_market"],
        "categories": ["Смартфоны", "Ноутбуки", "Аксессуары", "Бытовая техника"],
        "marketplace_categories": {
            "kaspi": ["Смартфоны", "Ноутбуки", "Аксессуары"],
            "ozon": ["Электроника", "Гаджеты", "Компьютеры"],
            "halyk_market": ["Техника", "Аксессуары", "Гаджеты"],
            "forte_market": ["Электроника", "Аксессуары", "Бытовая техника"],
        },
    },
    {
        "code": "fashion",
        "label": "Одежда и обувь",
        "description": "Для одежды, обуви, аксессуаров и сезонных коллекций.",
        "theme": "light",
        "theme_label": "Светлая",
        "recommended_integrations": ["kaspi", "ozon", "forte_market"],
        "categories": ["Одежда", "Обувь", "Аксессуары", "Сезонные коллекции"],
        "marketplace_categories": {
            "kaspi": ["Одежда", "Обувь", "Аксессуары"],
            "ozon": ["Одежда", "Обувь", "Сумки"],
            "halyk_market": ["Одежда", "Обувь", "Текстиль"],
            "forte_market": ["Одежда", "Обувь", "Аксессуары"],
        },
    },
    {
        "code": "home",
        "label": "Дом и ремонт",
        "description": "Для мебели, товаров для дома, кухни и ремонтных категорий.",
        "theme": "system",
        "theme_label": "Системная",
        "recommended_integrations": ["kaspi", "ozon", "halyk_market"],
        "categories": ["Дом и кухня", "Мебель", "Ремонт", "Хранение"],
        "marketplace_categories": {
            "kaspi": ["Дом и кухня", "Мебель", "Ремонт"],
            "ozon": ["Дом", "Кухня", "Интерьер"],
            "halyk_market": ["Дом и ремонт", "Мебель", "Хранение"],
            "forte_market": ["Дом и кухня", "Интерьер", "Ремонт"],
        },
    },
    {
        "code": "beauty",
        "label": "Красота и уход",
        "description": "Для косметики, ухода, парфюмерии и товаров для здоровья.",
        "theme": "light",
        "theme_label": "Светлая",
        "recommended_integrations": ["kaspi", "ozon", "forte_market"],
        "categories": ["Косметика", "Уход", "Парфюмерия", "Здоровье"],
        "marketplace_categories": {
            "kaspi": ["Косметика", "Уход", "Парфюмерия"],
            "ozon": ["Красота", "Уход", "Парфюмерия"],
            "halyk_market": ["Уход", "Красота", "Здоровье"],
            "forte_market": ["Косметика", "Уход", "Парфюмерия"],
        },
    },
    {
        "code": "general",
        "label": "Универсальная розница",
        "description": "Подойдёт, если компания пока тестирует платформу и не хочет жёстких рамок.",
        "theme": "system",
        "theme_label": "Системная",
        "recommended_integrations": ["kaspi", "ozon", "halyk_market"],
        "categories": ["Смешанный каталог", "Топ-позиции", "Акции", "Новинки"],
        "marketplace_categories": {
            "kaspi": ["Смешанный каталог", "Топ-позиции"],
            "ozon": ["Смешанный каталог", "Новинки"],
            "halyk_market": ["Смешанный каталог", "Акции"],
            "forte_market": ["Смешанный каталог", "Подготовка к запуску"],
        },
    },
]

WORKSPACE_TEMPLATE_LOOKUP = {item["code"]: item for item in WORKSPACE_TEMPLATES}
WORKSPACE_TEMPLATE_DEFAULT = "general"
WORKSPACE_THEME_LABELS = {
    "system": "Системная",
    "light": "Светлая",
    "dark": "Тёмная",
}
FORTE_MARKET_CATEGORIES = [
    "Бытовая техника",
    "Ноутбуки и компьютеры",
    "Смартфоны и гаджеты",
    "ТВ, аудио, видео",
    "Ювелирные изделия",
    "Строительство и ремонт",
    "Красота и здоровье",
    "Автотовары",
    "Зоотовары",
    "Активный отдых и спорт",
    "Мебель",
    "Одежда, обувь и аксессуары",
    "Детские товары",
    "Подарки, цветы, все для праздника",
    "Товары для дома и дачи",
    "Досуг и творчество",
    "Забота и гигиена",
    "Канцелярские товары",
]
TEMPLATE_INFERENCE_RULES = (
    ("tire", ("шина", "шины", "диск", "диски", "авто", "tire", "wheel", "колес")),
    ("electronics", ("электро", "electronics", "tech", "gadget", "смартфон", "телефон", "ноутбук", "техника")),
    ("fashion", ("одеж", "fashion", "clothes", "wear", "shoe", "обув", "style", "аксессуар")),
    ("home", ("дом", "home", "мебел", "кух", "ремонт", "interior", "decor", "хранение")),
    ("beauty", ("beauty", "космет", "уход", "парф", "makeup", "care", "здоровье")),
)

SCHEDULE_ACTIONS = {
    "kaspi_catalog_collect": ("Kaspi", "Сбор каталога"),
    "kaspi_price_actualize": ("Kaspi", "Актуализация цен"),
    "kaspi_full_sync": ("Kaspi", "Полная синхронизация"),
    "ozon_catalog_collect": ("Ozon.ru", "Сбор каталога"),
    "ozon_price_actualize": ("Ozon.ru", "Актуализация цен"),
    "ozon_full_sync": ("Ozon.ru", "Полная синхронизация"),
    "ozon_kz_status": ("Ozon.kz", "Проверка сборщика"),
    "ozon_kz_catalog_collect": ("Ozon.kz", "Сбор каталога"),
    "ozon_kz_price_actualize": ("Ozon.kz", "Актуализация цен"),
    "ozon_kz_full_sync": ("Ozon.kz", "Полная синхронизация"),
    "halyk_catalog_collect": ("Halyk Market", "Сбор каталога"),
    "halyk_price_actualize": ("Halyk Market", "Актуализация цен"),
    "halyk_full_sync": ("Halyk Market", "Полная синхронизация"),
    "forte_catalog_collect": ("Forte Market", "Сбор каталога"),
    "forte_price_actualize": ("Forte Market", "Актуализация цен"),
    "forte_full_sync": ("Forte Market", "Полная синхронизация"),
    "wb_catalog_collect": ("Wildberries", "Сбор каталога"),
    "wb_price_actualize": ("Wildberries", "Актуализация цен"),
    "wb_full_sync": ("Wildberries", "Полная синхронизация"),
    "export_report": ("Система", "Формирование отчёта"),
    "backup_database": ("Система", "Резервное копирование"),
}



def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (value or "").casefold()).strip("-")
    return text or "company"


def _json_or_default(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return default
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    if default is None:
        return parsed
    if isinstance(default, dict) and isinstance(parsed, dict):
        return parsed
    if isinstance(default, list) and isinstance(parsed, list):
        return parsed
    return default


def _copy_template(template: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": str(template["code"]),
        "label": str(template["label"]),
        "description": str(template["description"]),
        "theme": str(template["theme"]),
        "theme_label": str(template["theme_label"]),
        "recommended_integrations": [str(code) for code in template.get("recommended_integrations", [])],
        "categories": [str(category) for category in template.get("categories", [])],
        "marketplace_categories": {
            str(code): [str(category) for category in categories]
            for code, categories in dict(template.get("marketplace_categories", {})).items()
        },
    }


def _template_by_code(code: str | None) -> dict[str, Any]:
    template = WORKSPACE_TEMPLATE_LOOKUP.get(str(code or "").strip().casefold())
    if template is None:
        template = WORKSPACE_TEMPLATE_LOOKUP[WORKSPACE_TEMPLATE_DEFAULT]
    return _copy_template(template)


def _theme_label(theme: str) -> str:
    return WORKSPACE_THEME_LABELS.get(str(theme or "").casefold(), WORKSPACE_THEME_LABELS["system"])


def _normalize_codes(raw: Any, allowed: set[str]) -> list[str]:
    if isinstance(raw, str):
        source = [raw]
    elif isinstance(raw, list):
        source = raw
    else:
        source = []
    result: list[str] = []
    for value in source:
        code = str(value or "").strip().casefold()
        if code and code in allowed and code not in result:
            result.append(code)
    return result


def _infer_template_code(company: str, comment: str, explicit_code: str | None = None) -> str:
    requested = str(explicit_code or "").strip().casefold()
    if requested in WORKSPACE_TEMPLATE_LOOKUP:
        return requested
    haystack = f"{company} {comment}".casefold()
    for code, keywords in TEMPLATE_INFERENCE_RULES:
        if any(keyword in haystack for keyword in keywords):
            return code
    return WORKSPACE_TEMPLATE_DEFAULT


def build_workspace_profile(payload: dict[str, Any]) -> dict[str, Any]:
    company = str(payload.get("company_name") or "").strip()
    comment = str(payload.get("comment") or "").strip()
    mode = str(payload.get("launch_mode") or "self_service").strip().casefold()
    if mode not in {"self_service", "review"}:
        mode = "self_service"
    template_code = _infer_template_code(company, comment, payload.get("template_code"))
    template = _template_by_code(template_code)
    explicit_theme = str(payload.get("theme") or "").strip().casefold()
    theme = explicit_theme if explicit_theme in {"system", "light", "dark"} else template["theme"]
    all_integrations = {item["code"] for item in INTEGRATION_CATALOG}
    has_explicit_integrations = "marketplaces" in payload or "integrations" in payload
    selected_integrations = _normalize_codes(
        payload.get("marketplaces") if "marketplaces" in payload else payload.get("integrations"),
        all_integrations,
    )
    if not selected_integrations and not has_explicit_integrations:
        selected_integrations = [code for code in template["recommended_integrations"] if code in all_integrations]
    marketplace_categories = {
        code: [str(category) for category in categories]
        for code, categories in template["marketplace_categories"].items()
    }
    marketplace_categories["forte_market"] = [str(category) for category in FORTE_MARKET_CATEGORIES]
    return {
        "mode": mode,
        "mode_label": "Сразу создать компанию" if mode == "self_service" else "Только заявка",
        "template_code": template["code"],
        "template_label": template["label"],
        "template_description": template["description"],
        "theme": theme,
        "theme_label": _theme_label(theme),
        "theme_key": f"theme_{theme}" if theme in {"system", "light", "dark"} else "theme_system",
        "selected_integrations": selected_integrations,
        "selected_integration_names": [
            item["name"]
            for item in INTEGRATION_CATALOG
            if item["code"] in selected_integrations
        ],
        "categories": [str(category) for category in template["categories"]],
        "marketplace_categories": marketplace_categories,
        "company_name": company,
    }


class SaaSService:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._ensure_identity_uniqueness_guards()

    def _connect(self) -> sqlite3.Connection:
        conn = connect_database(self.db_path, timeout=30)
        return configure_connection(conn, foreign_keys=True, busy_timeout=30000)

    def _ensure_identity_uniqueness_guards(self) -> None:
        """Reject new duplicate company BINs/emails without rewriting legacy rows.

        A few historical demo companies predate the uniqueness requirement and
        contain the same placeholder BIN.  Triggers let us enforce the rule for
        every new or changed identity immediately, while preserving those rows
        until an administrator supplies their real requisites.
        """
        conn = self._connect()
        try:
            if isinstance(conn, PostgresConnection):
                conn.execute(
                    """CREATE OR REPLACE FUNCTION enforce_tenant_identity_unique()
                       RETURNS trigger AS $$
                       BEGIN
                         IF (TG_OP='INSERT' OR NEW.registration_number IS DISTINCT FROM OLD.registration_number)
                            AND NULLIF(BTRIM(NEW.registration_number),'') IS NOT NULL
                            AND EXISTS(
                              SELECT 1 FROM tenants t WHERE t.id<>COALESCE(NEW.id,0)
                                AND lower(BTRIM(COALESCE(t.registration_number,'')))=
                                    lower(BTRIM(NEW.registration_number))
                            ) THEN
                           RAISE EXCEPTION 'duplicate company registration number' USING ERRCODE='23505';
                         END IF;
                         IF (TG_OP='INSERT' OR NEW.contact_email IS DISTINCT FROM OLD.contact_email)
                            AND NULLIF(BTRIM(NEW.contact_email),'') IS NOT NULL
                            AND EXISTS(
                              SELECT 1 FROM tenants t WHERE t.id<>COALESCE(NEW.id,0)
                                AND lower(BTRIM(COALESCE(t.contact_email,'')))=
                                    lower(BTRIM(NEW.contact_email))
                            ) THEN
                           RAISE EXCEPTION 'duplicate company email' USING ERRCODE='23505';
                         END IF;
                         RETURN NEW;
                       END;
                       $$ LANGUAGE plpgsql"""
                )
                conn.execute("DROP TRIGGER IF EXISTS trg_tenant_identity_unique ON tenants")
                conn.execute(
                    """CREATE TRIGGER trg_tenant_identity_unique
                       BEFORE INSERT OR UPDATE OF registration_number,contact_email ON tenants
                       FOR EACH ROW EXECUTE FUNCTION enforce_tenant_identity_unique()"""
                )
            else:
                triggers = {
                    "trg_tenant_bin_unique_insert": """BEFORE INSERT ON tenants
                      WHEN NULLIF(TRIM(NEW.registration_number),'') IS NOT NULL AND EXISTS(
                        SELECT 1 FROM tenants t WHERE lower(TRIM(COALESCE(t.registration_number,'')))=
                          lower(TRIM(NEW.registration_number)))""",
                    "trg_tenant_bin_unique_update": """BEFORE UPDATE OF registration_number ON tenants
                      WHEN lower(TRIM(COALESCE(NEW.registration_number,'')))<>
                           lower(TRIM(COALESCE(OLD.registration_number,''))) AND EXISTS(
                        SELECT 1 FROM tenants t WHERE t.id<>NEW.id AND
                          lower(TRIM(COALESCE(t.registration_number,'')))=lower(TRIM(NEW.registration_number)))""",
                    "trg_tenant_email_unique_insert": """BEFORE INSERT ON tenants
                      WHEN NULLIF(TRIM(NEW.contact_email),'') IS NOT NULL AND EXISTS(
                        SELECT 1 FROM tenants t WHERE lower(TRIM(COALESCE(t.contact_email,'')))=
                          lower(TRIM(NEW.contact_email)))""",
                    "trg_tenant_email_unique_update": """BEFORE UPDATE OF contact_email ON tenants
                      WHEN lower(TRIM(COALESCE(NEW.contact_email,'')))<>
                           lower(TRIM(COALESCE(OLD.contact_email,''))) AND EXISTS(
                        SELECT 1 FROM tenants t WHERE t.id<>NEW.id AND
                          lower(TRIM(COALESCE(t.contact_email,'')))=lower(TRIM(NEW.contact_email)))""",
                }
                for name, clause in triggers.items():
                    conn.execute(
                        f"""CREATE TRIGGER IF NOT EXISTS {name} {clause}
                            BEGIN SELECT RAISE(ABORT,'duplicate company identity'); END"""
                    )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _seed_tenant_security(
        conn: sqlite3.Connection, tenant_id: int, stamp: str
    ) -> None:
        for role_code, label in ROLE_LABELS.items():
            conn.execute(
                """INSERT INTO tenant_roles(
                       tenant_id,role_code,display_name,is_system,created_at,updated_at
                   ) VALUES(?,?,?,1,?,?)
                   ON CONFLICT(tenant_id,role_code) DO NOTHING""",
                (int(tenant_id), role_code, label, stamp, stamp),
            )
            for permission_code in ROLE_DEFAULT_PERMISSIONS[role_code]:
                conn.execute(
                    """INSERT INTO tenant_role_permissions(
                           tenant_id,role_code,permission_code,is_enabled,created_at,updated_at
                       ) VALUES(?,?,?,1,?,?)
                       ON CONFLICT(tenant_id,role_code,permission_code) DO NOTHING""",
                    (int(tenant_id), role_code, permission_code, stamp, stamp),
                )
        for key, label, order in (
            ("title", "Название", 10),
            ("marketplace", "Marketplace", 20),
        ):
            conn.execute(
                """INSERT INTO tenant_catalog_filters(
                       tenant_id,attribute_key,display_name,is_enabled,display_order,
                       config_json,created_at,updated_at
                   ) VALUES(?,?,?,1,?,'{}',?,?)
                   ON CONFLICT(tenant_id,attribute_key) DO NOTHING""",
                (int(tenant_id), key, label, order, stamp, stamp),
            )

    def default_tenant_id(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute("SELECT id FROM tenants ORDER BY id LIMIT 1").fetchone()
            if row is None:
                raise RuntimeError("Рабочее пространство не создано.")
            return int(row["id"])
        finally:
            conn.close()

    def tenant_for_user(self, user_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT t.*,tu.tenant_role,tu.is_primary
                FROM tenant_users tu JOIN tenants t ON t.id=tu.tenant_id
                WHERE tu.user_id=? AND tu.is_active=1
                ORDER BY tu.is_primary DESC,t.id LIMIT 1
                """,
                (int(user_id),),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["status_raw"] = result.get("status")
            result["status"] = canonical_company_status(result.get("status"))
            result["status_label"] = company_status_label(result["status"])
            return result
        finally:
            conn.close()

    def integrations(self, tenant_id: int, allowed_only: bool = False) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            result: list[dict[str, Any]] = []
            query = "SELECT ti.* FROM tenant_integrations ti"
            params: list[Any] = []
            if allowed_only:
                query += (
                    " JOIN tenant_marketplace_access tma"
                    " ON tma.tenant_id=ti.tenant_id"
                    " AND tma.marketplace_code=ti.integration_code"
                    " AND tma.is_allowed=1"
                )
            query += " WHERE ti.tenant_id=? ORDER BY ti.id"
            params.append(int(tenant_id))
            for row in conn.execute(query, params).fetchall():
                item = dict(row)
                item["config"] = _json_or_default(item.pop("config_json", "{}"), {})
                item["discovery"] = _json_or_default(item.pop("discovery_json", "{}"), {})
                item["sellers"] = self._seller_rows(
                    conn, int(tenant_id), str(item.get("integration_code") or "")
                )
                result.append(item)
            return result
        finally:
            conn.close()

    @staticmethod
    def _seller_rows(
        conn: sqlite3.Connection,
        tenant_id: int,
        marketplace_code: str = "",
    ) -> list[dict[str, Any]]:
        where = "tenant_id=?"
        params: list[Any] = [int(tenant_id)]
        if marketplace_code:
            where += " AND marketplace_code=?"
            params.append(str(marketplace_code))
        result: list[dict[str, Any]] = []
        for row in conn.execute(
            f"""SELECT * FROM tenant_marketplace_sellers
                 WHERE {where}
                 ORDER BY marketplace_code,
                          CASE WHEN status='active' AND approval_status='approved'
                               THEN 0 ELSE 1 END,
                          id""",
            params,
        ).fetchall():
            item = dict(row)
            item["config"] = _json_or_default(item.pop("config_json", "{}"), {})
            item["discovery"] = _json_or_default(item.pop("discovery_json", "{}"), {})
            item["credential_configured"] = bool(item.pop("credential_ref", None))
            result.append(item)
        return result

    def sellers(
        self,
        tenant_id: int,
        marketplace_code: str = "",
        *,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        code = str(marketplace_code or "").strip().casefold()
        if code and code not in MARKETPLACE_BY_CODE:
            raise ValueError("Неизвестная площадка.")
        conn = self._connect()
        try:
            rows = self._seller_rows(conn, int(tenant_id), code)
            if active_only:
                rows = [
                    item for item in rows
                    if item.get("status") == "active"
                    and item.get("approval_status") == "approved"
                ]
            return rows
        finally:
            conn.close()

    def active_seller_sources(
        self, marketplace_code: str
    ) -> list[dict[str, Any]]:
        """Return global active sources used to identify a legacy browser owner."""
        code = str(marketplace_code or "").strip().casefold()
        if code not in MARKETPLACE_BY_CODE:
            raise ValueError("Неизвестная площадка.")
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT id,tenant_id,external_seller_id,source_url
                   FROM tenant_marketplace_sellers
                   WHERE marketplace_code=?
                     AND status='active'
                     AND approval_status='approved'
                   ORDER BY id
                """,
                (code,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    @staticmethod
    def _legacy_ozon_runtime_id(
        tenant_id: int, marketplace_code: str, source_url: str,
    ) -> int:
        key = f"{int(tenant_id)}:{marketplace_code}:{source_url.strip().casefold()}"
        return 1_000_000_000 + (zlib.crc32(key.encode("utf-8")) & 0x7FFFFFFF)

    def ozon_runtime_sellers(self) -> list[dict[str, Any]]:
        """The single seller set used by Ozon operations and the launcher."""
        explicit: list[dict[str, Any]] = []
        for marketplace in ("ozon", "ozon_kz"):
            for seller in self.active_seller_sources(marketplace):
                seller["marketplace_code"] = marketplace
                seller["runtime_seller_id"] = int(seller["id"])
                explicit.append(seller)
        known = {
            (int(item["tenant_id"]), str(item["marketplace_code"]), str(item["source_url"]).strip().casefold())
            for item in explicit
        }
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT tenant_id,integration_code AS marketplace_code,seller_identifier,
                          seller_name,seller_url AS source_url,status,approval_status
                   FROM tenant_integrations
                   WHERE integration_code IN ('ozon','ozon_kz')
                     AND status='active' AND approval_status='approved'
                     AND TRIM(COALESCE(seller_url,''))<>''
                   ORDER BY tenant_id,integration_code"""
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            item = dict(row)
            key = (int(item["tenant_id"]), str(item["marketplace_code"]), str(item["source_url"]).strip().casefold())
            if key in known:
                continue
            item.update({
                "id": None,
                "runtime_seller_id": self._legacy_ozon_runtime_id(*key),
                "external_seller_id": str(item.pop("seller_identifier") or ""),
                "display_name": str(item.pop("seller_name") or ""),
                "config": {}, "discovery": {}, "legacy": True,
            })
            explicit.append(item)
        return explicit

    def active_ozon_marketplaces(self) -> list[str]:
        """Marketplace browser launcher input; intentionally not seller scoped."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT DISTINCT integration_code
                   FROM tenant_integrations
                   WHERE integration_code IN ('ozon','ozon_kz')
                     AND status='active' AND approval_status='approved'
                   ORDER BY integration_code"""
            ).fetchall()
            return [str(row[0]) for row in rows]
        finally:
            conn.close()

    def seller(
        self, tenant_id: int, tenant_seller_id: int
    ) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            rows = self._seller_rows(conn, int(tenant_id))
            return next(
                (item for item in rows if int(item.get("id") or 0) == int(tenant_seller_id)),
                None,
            )
        finally:
            conn.close()

    def resolve_seller(
        self,
        tenant_id: int,
        marketplace_code: str,
        tenant_seller_id: int | None = None,
        *,
        require_active: bool = True,
    ) -> dict[str, Any]:
        code = str(marketplace_code or "").strip().casefold()
        if code not in MARKETPLACE_BY_CODE:
            raise ValueError("Неизвестная площадка.")
        candidates = self.sellers(int(tenant_id), code, active_only=require_active)
        if tenant_seller_id not in (None, "", 0, "0"):
            selected = next(
                (
                    item for item in candidates
                    if int(item.get("id") or 0) == int(tenant_seller_id)
                ),
                None,
            )
            if not selected:
                raise PermissionError("Продавец не принадлежит компании или недоступен.")
            return selected
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise ValueError("Выберите продавца для запуска операции.")

        # Compatibility entries use the same runtime seller set as the launcher.
        if code in {"ozon", "ozon_kz"}:
            legacy = next(
                (item for item in self.ozon_runtime_sellers()
                 if item.get("legacy") and int(item["tenant_id"]) == int(tenant_id)
                 and item["marketplace_code"] == code),
                None,
            )
            if legacy:
                return legacy

        # Compatibility for non-Ozon installations that predate seller rows.
        connection = next(
            (
                item for item in self.integrations(int(tenant_id))
                if str(item.get("integration_code") or "") == code
            ),
            None,
        )
        if connection and str(connection.get("seller_identifier") or "").strip():
            return {
                "id": None,
                "tenant_id": int(tenant_id),
                "marketplace_code": code,
                "external_seller_id": str(connection.get("seller_identifier") or ""),
                "display_name": str(connection.get("seller_name") or ""),
                "source_url": str(connection.get("seller_url") or ""),
                "status": str(connection.get("status") or ""),
                "approval_status": str(connection.get("approval_status") or ""),
                "config": dict(connection.get("config") or {}),
                "discovery": dict(connection.get("discovery") or {}),
                "legacy": True,
            }
        raise ValueError("Подключите и подтвердите продавца для этой площадки.")

    def marketplace_access(
        self, tenant_id: int, include_unavailable: bool = True
    ) -> list[dict[str, Any]]:
        """Return company grants and live connection state as separate flags."""
        definitions = self.public_integrations()
        conn = self._connect()
        try:
            rows = {
                str(row["integration_code"]): dict(row)
                for row in conn.execute(
                    """SELECT ti.*,COALESCE(tma.is_allowed,0) AS is_allowed,
                              tma.granted_at,tma.updated_at AS access_updated_at
                       FROM tenant_integrations ti
                       LEFT JOIN tenant_marketplace_access tma
                         ON tma.tenant_id=ti.tenant_id
                        AND tma.marketplace_code=ti.integration_code
                       WHERE ti.tenant_id=?""",
                    (int(tenant_id),),
                ).fetchall()
            }
            result: list[dict[str, Any]] = []
            for definition in definitions:
                row = rows.get(str(definition["code"])) or {}
                sellers = self._seller_rows(
                    conn, int(tenant_id), str(definition["code"])
                )
                active_sellers = [
                    seller for seller in sellers
                    if seller.get("status") == "active"
                    and seller.get("approval_status") == "approved"
                ]
                pending_sellers = [
                    seller for seller in sellers
                    if seller.get("approval_status") == "pending"
                ]
                allowed = bool(row.get("is_allowed"))
                approval_status = str(row.get("approval_status") or "draft")
                connected = (
                    allowed
                    and (
                        bool(active_sellers)
                        or (
                            not sellers
                            and str(row.get("status") or "") == "active"
                            and approval_status == "approved"
                        )
                    )
                )
                connection_status = (
                    "connected" if connected
                    else "pending" if pending_sellers or approval_status == "pending"
                    else "rejected" if approval_status == "rejected"
                    else "available" if allowed
                    else "not_allowed"
                )
                item = {
                    **dict(definition),
                    "is_allowed": allowed,
                    "is_connected": connected,
                    "connection_status": connection_status,
                    "approval_status": approval_status,
                    "submitted_at": row.get("submitted_at"),
                    "reviewed_at": row.get("reviewed_at"),
                    "review_note": str(row.get("review_note") or ""),
                    "seller_name": str(row.get("seller_name") or ""),
                    "seller_identifier": str(row.get("seller_identifier") or ""),
                    "seller_url": str(row.get("seller_url") or ""),
                    "last_sync_at": row.get("last_sync_at"),
                    "last_error": str(row.get("last_error") or ""),
                    "granted_at": row.get("granted_at"),
                    "sellers": sellers,
                    "seller_count": len(sellers),
                    "active_seller_count": len(active_sellers),
                }
                if include_unavailable or allowed:
                    result.append(item)
            # Keep the service catalog order inside each group, but put the
            # company's already chosen or connected marketplaces first.
            result.sort(
                key=lambda item: not bool(
                    item.get("is_allowed") or item.get("is_connected")
                )
            )
            return result
        finally:
            conn.close()

    def set_marketplace_access(
        self,
        tenant_id: int,
        marketplaces: dict[str, Any] | list[str],
        actor_user_id: int,
    ) -> list[dict[str, Any]]:
        if isinstance(marketplaces, dict):
            requested = {code: bool(marketplaces.get(code, False)) for code in MARKETPLACE_BY_CODE}
        else:
            enabled = {str(code).strip() for code in (marketplaces or [])}
            unknown = enabled.difference(MARKETPLACE_BY_CODE)
            if unknown:
                raise ValueError("Неизвестная площадка: " + ", ".join(sorted(unknown)) + ".")
            requested = {code: code in enabled for code in MARKETPLACE_BY_CODE}
        stamp = now_iso()
        conn = self._connect()
        try:
            tenant = conn.execute("SELECT status FROM tenants WHERE id=?", (int(tenant_id),)).fetchone()
            if not tenant:
                raise ValueError("Компания не найдена.")
            if any(requested.values()) and not company_is_approved(tenant["status"]):
                raise ValueError("Сначала подтвердите компанию.")
            for code, allowed in requested.items():
                conn.execute(
                    """INSERT INTO tenant_marketplace_access(
                           tenant_id,marketplace_code,is_allowed,granted_by,granted_at,updated_at
                       ) VALUES(?,?,?,?,?,?)
                       ON CONFLICT(tenant_id,marketplace_code) DO UPDATE SET
                           is_allowed=excluded.is_allowed,
                           granted_by=excluded.granted_by,
                           granted_at=CASE WHEN excluded.is_allowed=1 THEN excluded.granted_at ELSE NULL END,
                           updated_at=excluded.updated_at""",
                    (int(tenant_id), code, 1 if allowed else 0, int(actor_user_id), stamp if allowed else None, stamp),
                )
                if not allowed:
                    conn.execute(
                        """UPDATE tenant_integrations SET status='disabled',updated_at=?
                           WHERE tenant_id=? AND integration_code=?""",
                        (stamp, int(tenant_id), code),
                    )
            self._audit(
                conn, actor_user_id, "tenant_marketplace_access_updated", int(tenant_id),
                "tenant", str(tenant_id), {"marketplaces": requested},
            )
            conn.commit()
        finally:
            conn.close()
        return self.marketplace_access(tenant_id)

    @staticmethod
    def _write_marketplace_access(
        conn: sqlite3.Connection,
        tenant_id: int,
        enabled_codes: list[str] | set[str],
        actor_user_id: int | None,
        stamp: str,
    ) -> None:
        enabled = {str(code) for code in enabled_codes if str(code) in MARKETPLACE_BY_CODE}
        for code in MARKETPLACE_BY_CODE:
            allowed = code in enabled
            conn.execute(
                """INSERT INTO tenant_marketplace_access(
                       tenant_id,marketplace_code,is_allowed,granted_by,granted_at,updated_at
                   ) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(tenant_id,marketplace_code) DO UPDATE SET
                       is_allowed=excluded.is_allowed,
                       granted_by=excluded.granted_by,
                       granted_at=CASE WHEN excluded.is_allowed=1 THEN excluded.granted_at ELSE NULL END,
                       updated_at=excluded.updated_at""",
                (
                    int(tenant_id), code, 1 if allowed else 0, actor_user_id,
                    stamp if allowed else None, stamp,
                ),
            )
            if not allowed:
                conn.execute(
                    """UPDATE tenant_integrations SET status='disabled',updated_at=?
                       WHERE tenant_id=? AND integration_code=?""",
                    (stamp, int(tenant_id), code),
                )

    def detect_marketplace_url(
        self, tenant_id: int, seller_url: str, marketplace_code: str = ""
    ) -> dict[str, Any]:
        value = str(seller_url or "").strip()
        expected = str(marketplace_code or "").strip().casefold()
        source = parse_marketplace_source(value, expected, self.marketplace_source_rules())
        detected = MARKETPLACE_BY_CODE[source["marketplace_code"]]
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT t.status,COALESCE(tma.is_allowed,0) AS is_allowed
                   FROM tenants t
                   LEFT JOIN tenant_marketplace_access tma
                     ON tma.tenant_id=t.id AND tma.marketplace_code=?
                   WHERE t.id=?""",
                (detected.code, int(tenant_id)),
            ).fetchone()
            if not row:
                raise ValueError("Компания не найдена.")
            if not company_is_approved(row["status"]):
                raise ValueError("Компания ещё не подтверждена.")
        finally:
            conn.close()
        identifier = str(source.get("seller_identifier") or "")
        if not identifier:
            raise ValueError(
                "Не удалось определить магазин или товар по ссылке. "
                "Откройте страницу продавца либо карточку товара поддерживаемой площадки."
            )
        is_ozon = detected.code in {"ozon", "ozon_kz"}
        return {
            "marketplace_code": detected.code,
            "marketplace_name": detected.label,
            "seller_identifier": identifier,
            "seller_name": str(source.get("seller_name") or identifier),
            "seller_url": str(source.get("seller_url") or value),
            "source_scope": str(source.get("source_scope") or "seller"),
            "product_id": str(source.get("product_id") or ""),
            "product_slug": str(source.get("product_slug") or ""),
            "host": str(source.get("host") or ""),
            "input_type": str(source.get("input_type") or "url"),
            "source_input": str(source.get("source_input") or value),
            # URL parsing only proves syntax.  Ozon needs independent browser
            # evidence before an administrator can activate the source.
            "verification_state": "parsed" if is_ozon else "verified",
            "verified": not is_ozon,
        }

    def connect_marketplace(
        self,
        tenant_id: int,
        seller_url: str,
        actor_user_id: int,
        marketplace_code: str = "",
    ) -> dict[str, Any]:
        detected = self.detect_marketplace_url(tenant_id, seller_url, marketplace_code)
        stamp = now_iso()
        verification_state = str(detected.get("verification_state") or "parsed")
        discovery = {
            "evidence": "public_url",
            "host_verified": True,
            "verification_state": verification_state,
            "verified": verification_state == "verified",
            "source_scope": detected.get("source_scope") or "seller",
            "product_id": detected.get("product_id") or "",
            "product_slug": detected.get("product_slug") or "",
            "input_type": detected.get("input_type") or "url",
            "source_input": detected.get("source_input") or seller_url,
        }
        conn = self._connect()
        try:
            tenant = conn.execute("SELECT * FROM tenants WHERE id=?", (int(tenant_id),)).fetchone()
            missing = self.company_profile_missing(tenant) if tenant else ["Компания"]
            if missing:
                raise ValueError("Заполните обязательные поля компании: " + ", ".join(missing) + ".")
            conn.execute(
                """INSERT INTO tenant_marketplace_sellers(
                       tenant_id,marketplace_code,external_seller_id,display_name,source_url,
                       status,discovery_status,approval_status,discovery_json,
                       submitted_by,submitted_at,review_note,created_at,updated_at
                   ) VALUES(?,?,?,?,?,'pending',?,'pending',?,?,?,'',?,?)
                   ON CONFLICT(tenant_id,marketplace_code,external_seller_id) DO UPDATE SET
                       display_name=excluded.display_name,source_url=excluded.source_url,
                       status='pending',discovery_status=excluded.discovery_status,
                       approval_status='pending',discovery_json=excluded.discovery_json,
                       submitted_by=excluded.submitted_by,submitted_at=excluded.submitted_at,
                       reviewed_by=NULL,reviewed_at=NULL,review_note='',
                       updated_at=excluded.updated_at""",
                (
                    int(tenant_id), detected["marketplace_code"], detected["seller_identifier"],
                    detected["seller_name"], detected["seller_url"], verification_state,
                    json.dumps(discovery, ensure_ascii=False, separators=(",", ":")),
                    int(actor_user_id), stamp, stamp, stamp,
                ),
            )
            seller_row = conn.execute(
                """SELECT id FROM tenant_marketplace_sellers
                   WHERE tenant_id=? AND marketplace_code=? AND external_seller_id=?""",
                (
                    int(tenant_id), detected["marketplace_code"],
                    detected["seller_identifier"],
                ),
            ).fetchone()
            active_exists = conn.execute(
                """SELECT 1 FROM tenant_marketplace_sellers
                   WHERE tenant_id=? AND marketplace_code=? AND status='active'
                     AND approval_status='approved' LIMIT 1""",
                (int(tenant_id), detected["marketplace_code"]),
            ).fetchone()
            if active_exists:
                # A pending replacement must not change the live integration
                # summary or collector target before it has been approved.
                conn.execute(
                    """UPDATE tenant_integrations
                       SET submitted_by=?,submitted_at=?,updated_at=?
                       WHERE tenant_id=? AND integration_code=?""",
                    (int(actor_user_id), stamp, stamp, int(tenant_id), detected["marketplace_code"]),
                )
            else:
                conn.execute(
                    """UPDATE tenant_integrations SET seller_name=?,seller_identifier=?,seller_url=?,
                           discovery_status=?,approval_status=?,discovery_json=?,
                           status=?,submitted_by=?,submitted_at=?,updated_at=?
                       WHERE tenant_id=? AND integration_code=?""",
                    (
                        detected["seller_name"], detected["seller_identifier"], detected["seller_url"], verification_state,
                        "pending", json.dumps(discovery, ensure_ascii=False, separators=(",", ":")),
                        "setup", int(actor_user_id), stamp, stamp,
                        int(tenant_id), detected["marketplace_code"],
                    ),
                )
            self._audit(
                conn, actor_user_id, "tenant_marketplace_submitted", int(tenant_id),
                "tenant_integration", detected["marketplace_code"], detected,
            )
            conn.commit()
        finally:
            conn.close()
        return detected | {
            "tenant_seller_id": int(seller_row["id"]) if seller_row else None,
            "is_connected": False,
            "approval_status": "pending",
        }

    def stage_marketplace_source_replacement(
        self,
        tenant_id: int,
        marketplace_code: str,
        replaced_tenant_seller_id: int,
        source_url: str,
        actor_user_id: int,
    ) -> dict[str, Any]:
        """Stage a replacement source without touching the current seller.

        A replacement is deliberately a pending seller.  The old active seller
        remains usable until the ordinary review path has verified and approved
        the candidate; only then is it marked replaced (its collected data is
        retained for a separately confirmed, seller-scoped purge).
        """
        code = str(marketplace_code or "").strip().casefold()
        conn = self._connect()
        try:
            existing = conn.execute(
                """SELECT id,external_seller_id,status,approval_status
                   FROM tenant_marketplace_sellers
                   WHERE id=? AND tenant_id=? AND marketplace_code=?""",
                (int(replaced_tenant_seller_id), int(tenant_id), code),
            ).fetchone()
            if not existing:
                raise ValueError("Seller does not belong to this tenant and marketplace.")
            if not (str(existing["status"]) == "active" and str(existing["approval_status"]) == "approved"):
                raise ValueError("Only an approved active seller can be replaced.")
        finally:
            conn.close()

        detected = self.detect_marketplace_url(int(tenant_id), source_url, code)
        if str(detected.get("seller_identifier") or "").casefold() == str(existing["external_seller_id"] or "").casefold():
            raise ValueError("The replacement source resolves to the current seller.")
        staged = self.connect_marketplace(int(tenant_id), source_url, int(actor_user_id), code)
        candidate_id = int(staged.get("tenant_seller_id") or 0)
        if not candidate_id:
            raise ValueError("The replacement source was not staged.")

        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT discovery_json FROM tenant_marketplace_sellers WHERE id=? AND tenant_id=?",
                (candidate_id, int(tenant_id)),
            ).fetchone()
            discovery = _json_or_default(row["discovery_json"] if row else "{}", {})
            discovery["replacement_of_tenant_seller_id"] = int(replaced_tenant_seller_id)
            conn.execute(
                """UPDATE tenant_marketplace_sellers
                   SET discovery_json=?,updated_at=?
                   WHERE id=? AND tenant_id=? AND marketplace_code=?
                     AND approval_status='pending'""",
                (json.dumps(discovery, ensure_ascii=False, separators=(",", ":")), now_iso(), candidate_id, int(tenant_id), code),
            )
            self._audit(
                conn, int(actor_user_id), "tenant_marketplace_replacement_staged",
                int(tenant_id), "tenant_marketplace_seller", str(candidate_id),
                {"marketplace_code": code, "replacement_of_tenant_seller_id": int(replaced_tenant_seller_id)},
            )
            conn.commit()
        finally:
            conn.close()
        return staged | {"replacement_of_tenant_seller_id": int(replaced_tenant_seller_id)}

    def review_marketplace_connection(
        self,
        tenant_id: int,
        marketplace_code: str,
        decision: str,
        actor_user_id: int,
        review_note: str = "",
        tenant_seller_id: int | None = None,
    ) -> dict[str, Any]:
        code = str(marketplace_code or "").strip().casefold()
        target = str(decision or "").strip().casefold()
        if code not in MARKETPLACE_BY_CODE:
            raise ValueError("Неизвестная площадка.")
        if target not in {"approved", "rejected"}:
            raise ValueError("Решение должно быть approved или rejected.")
        stamp = now_iso()
        conn = self._connect()
        try:
            params: list[Any] = [int(tenant_id), code]
            seller_where = "tenant_id=? AND marketplace_code=? AND approval_status='pending'"
            if tenant_seller_id not in (None, 0):
                seller_where += " AND id=?"
                params.append(int(tenant_seller_id))
            pending = conn.execute(
                f"SELECT * FROM tenant_marketplace_sellers WHERE {seller_where} ORDER BY id",
                params,
            ).fetchall()
            if not pending:
                raise ValueError("Подключение не найдено.")
            if len(pending) > 1:
                raise ValueError("Укажите tenant_seller_id заявки продавца.")
            seller_row = pending[0]
            prior_discovery = _json_or_default(seller_row["discovery_json"], {})
            if target == "approved" and code in {"ozon", "ozon_kz"}:
                try:
                    evidence = verify_ozon_storefront(code, str(seller_row["source_url"] or ""))
                except OzonSourceVerificationError as exc:
                    raise ValueError(
                        "Ozon source is parsed but not verified: " + str(exc)
                    ) from exc
                discovery = {
                    "evidence": "interactive_browser",
                    "verification_state": "verified",
                    "verified": True,
                    "canonical_seller_id": evidence["canonical_seller_id"],
                    "canonical_seller_url": evidence["canonical_seller_url"],
                    "seller_name": evidence["seller_name"],
                    "catalogue_empty": evidence["catalogue_empty"] == "true",
                }
                conn.execute(
                    """UPDATE tenant_marketplace_sellers
                       SET external_seller_id=?,display_name=?,source_url=?,
                           discovery_status='verified',discovery_json=?,updated_at=?
                       WHERE id=? AND tenant_id=?""",
                    (
                        evidence["canonical_seller_id"], evidence["seller_name"],
                        evidence["canonical_seller_url"],
                        json.dumps(discovery, ensure_ascii=False, separators=(",", ":")),
                        stamp, int(seller_row["id"]), int(tenant_id),
                    ),
                )
                seller_row = conn.execute(
                    "SELECT * FROM tenant_marketplace_sellers WHERE id=? AND tenant_id=?",
                    (int(seller_row["id"]), int(tenant_id)),
                ).fetchone()
            status = "active" if target == "approved" else "rejected"
            if target == "approved":
                replaced_id = int(prior_discovery.get("replacement_of_tenant_seller_id") or 0)
                if replaced_id and replaced_id != int(seller_row["id"]):
                    replaced = conn.execute(
                        """SELECT id FROM tenant_marketplace_sellers
                           WHERE id=? AND tenant_id=? AND marketplace_code=?
                             AND status='active' AND approval_status='approved'""",
                        (replaced_id, int(tenant_id), code),
                    ).fetchone()
                    if not replaced:
                        raise ValueError("The source being replaced is no longer active.")
                    conn.execute(
                        """UPDATE tenant_marketplace_sellers
                           SET status='replaced',approval_status='replaced',
                               reviewed_by=?,reviewed_at=?,updated_at=?
                           WHERE id=? AND tenant_id=? AND marketplace_code=?""",
                        (int(actor_user_id), stamp, stamp, replaced_id, int(tenant_id), code),
                    )
            conn.execute(
                """UPDATE tenant_marketplace_sellers
                   SET approval_status=?,status=?,reviewed_by=?,reviewed_at=?,
                       review_note=?,updated_at=? WHERE id=? AND tenant_id=?""",
                (
                    target, status, int(actor_user_id), stamp,
                    str(review_note or "").strip(), stamp,
                    int(seller_row["id"]), int(tenant_id),
                ),
            )
            summary = conn.execute(
                """SELECT * FROM tenant_marketplace_sellers
                   WHERE tenant_id=? AND marketplace_code=?
                   ORDER BY CASE
                       WHEN status='active' AND approval_status='approved' THEN 0
                       WHEN approval_status='pending' THEN 1 ELSE 2 END,
                       COALESCE(reviewed_at,submitted_at,updated_at) DESC,id DESC LIMIT 1""",
                (int(tenant_id), code),
            ).fetchone()
            active_count = int(conn.execute(
                """SELECT COUNT(*) FROM tenant_marketplace_sellers
                   WHERE tenant_id=? AND marketplace_code=? AND status='active'
                     AND approval_status='approved'""",
                (int(tenant_id), code),
            ).fetchone()[0])
            pending_count = int(conn.execute(
                """SELECT COUNT(*) FROM tenant_marketplace_sellers
                   WHERE tenant_id=? AND marketplace_code=? AND approval_status='pending'""",
                (int(tenant_id), code),
            ).fetchone()[0])
            summary_status = "active" if active_count else "setup" if pending_count else "disabled"
            summary_approval = "approved" if active_count else "pending" if pending_count else target
            conn.execute(
                """UPDATE tenant_integrations
                   SET seller_name=?,seller_identifier=?,seller_url=?,approval_status=?,
                       status=?,reviewed_by=?,reviewed_at=?,review_note=?,updated_at=?
                   WHERE tenant_id=? AND integration_code=?""",
                (
                    str(summary["display_name"] if summary else ""),
                    str(summary["external_seller_id"] if summary else ""),
                    str(summary["source_url"] if summary else ""),
                    summary_approval, summary_status, int(actor_user_id), stamp,
                    str(review_note or "").strip(), stamp, int(tenant_id), code,
                ),
            )
            # MARKETPLACE_APPROVAL_AUTO_GRANT_V1
            if target == "approved":
                conn.execute(
                    """INSERT INTO tenant_marketplace_access(
                           tenant_id,
                           marketplace_code,
                           is_allowed,
                           granted_by,
                           granted_at,
                           updated_at
                       )
                       VALUES(?,?,1,?,?,?)
                       ON CONFLICT(
                           tenant_id,
                           marketplace_code
                       ) DO UPDATE SET
                           is_allowed=1,
                           granted_by=excluded.granted_by,
                           granted_at=excluded.granted_at,
                           updated_at=excluded.updated_at
                    """,
                    (
                        int(tenant_id),
                        code,
                        int(actor_user_id),
                        stamp,
                        stamp,
                    ),
                )

            self._audit(
                conn, actor_user_id, f"tenant_marketplace_{target}", int(tenant_id),
                "tenant_marketplace_seller", str(seller_row["id"]),
                {"review_note": review_note, "marketplace_code": code},
            )
            conn.commit()
            result = conn.execute(
                "SELECT * FROM tenant_marketplace_sellers WHERE id=? AND tenant_id=?",
                (int(seller_row["id"]), int(tenant_id)),
            ).fetchone()
            return dict(result) if result else {}
        finally:
            conn.close()

    def marketplace_seller_purge_preview(
        self, tenant_id: int, marketplace_code: str, tenant_seller_id: int,
    ) -> dict[str, Any]:
        """Return a strictly seller-scoped cleanup preview; never mutate data."""
        code = str(marketplace_code or "").strip().casefold()
        conn = self._connect()
        try:
            seller = conn.execute(
                "SELECT * FROM tenant_marketplace_sellers WHERE id=? AND tenant_id=? AND marketplace_code=?",
                (int(tenant_seller_id), int(tenant_id), code),
            ).fetchone()
            if not seller:
                raise ValueError("Seller does not belong to this tenant and marketplace.")
            tables = (
                "tenant_seller_catalog_products", "tenant_seller_price_snapshots",
                "tenant_seller_offer_scans", "tenant_seller_offer_snapshots", "tenant_catalogs",
            )
            counts = {
                table: int(conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE tenant_id=? AND marketplace_code=? AND tenant_seller_id=?",
                    (int(tenant_id), code, int(tenant_seller_id)),
                ).fetchone()[0])
                for table in tables
            }
            return {"seller": dict(seller), "counts": counts, "total": sum(counts.values())}
        finally:
            conn.close()

    def purge_marketplace_seller_data(
        self, tenant_id: int, marketplace_code: str, tenant_seller_id: int, actor_user_id: int,
    ) -> dict[str, Any]:
        """Remove only disposable seller-scoped collection data in one transaction."""
        code = str(marketplace_code or "").strip().casefold()
        conn = self._connect()
        try:
            preview = self.marketplace_seller_purge_preview(tenant_id, code, tenant_seller_id)
            if isinstance(conn, PostgresConnection):
                conn.execute("SELECT id FROM tenant_marketplace_sellers WHERE id=? FOR UPDATE", (int(tenant_seller_id),))
            else:
                conn.execute("BEGIN IMMEDIATE")
            deleted: dict[str, int] = {}
            for table in (
                "tenant_seller_offer_snapshots", "tenant_seller_offer_scans",
                "tenant_seller_price_snapshots", "tenant_seller_catalog_products", "tenant_catalogs",
            ):
                cursor = conn.execute(
                    f"DELETE FROM {table} WHERE tenant_id=? AND marketplace_code=? AND tenant_seller_id=?",
                    (int(tenant_id), code, int(tenant_seller_id)),
                )
                deleted[table] = int(cursor.rowcount or 0)
            conn.execute(
                """UPDATE tenant_marketplace_sellers SET product_count=0,last_status='purged',
                   last_error='',updated_at=? WHERE id=? AND tenant_id=? AND marketplace_code=?""",
                (now_iso(), int(tenant_seller_id), int(tenant_id), code),
            )
            self._audit(conn, actor_user_id, "tenant_marketplace_seller_data_purged", int(tenant_id),
                        "tenant_marketplace_seller", str(int(tenant_seller_id)),
                        {"marketplace_code": code, "preview_counts": preview["counts"], "deleted": deleted})
            conn.commit()
            return {"seller": preview["seller"], "deleted": deleted, "total": sum(deleted.values())}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def public_integrations(self) -> list[dict[str, Any]]:
        rules = self.marketplace_source_rules()
        result: list[dict[str, Any]] = []
        for raw in INTEGRATION_CATALOG:
            item = dict(raw)
            rule = rules.get(str(item["code"]), {})
            examples = list(
                PUBLIC_MARKETPLACE_SOURCE_EXAMPLES.get(
                    str(item["code"]),
                    [
                        str(value)
                        for value
                        in rule.get("examples", [])
                        if str(value).strip()
                    ],
                )
            )
            fields = [dict(field) for field in item.get("connection_fields", [])]
            if fields:
                fields[0].update({
                    "label": "Ссылка, ID продавца или slug",
                    "type": "text",
                    "placeholder": examples[0] if examples else "Ссылка или ID продавца",
                })
            item["connection_fields"] = fields
            item["source_examples"] = examples
            result.append(item)
        return result

    def marketplace_source_rules(self) -> dict[str, dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value_json FROM platform_settings WHERE setting_key='marketplace_source_rules'"
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return merged_marketplace_source_rules({})
        try:
            stored = json.loads(str(row["value_json"] or "{}"))
        except json.JSONDecodeError:
            stored = {}
        if isinstance(stored, dict) and isinstance(stored.get("marketplaces"), dict):
            stored = stored["marketplaces"]
        return merged_marketplace_source_rules(stored)

    def update_marketplace_source_rules(
        self, payload: dict[str, Any], actor_user_id: int
    ) -> dict[str, dict[str, Any]]:
        rules = validate_marketplace_source_rules(payload)
        stamp = now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO platform_settings(setting_key,value_json,updated_by,updated_at)
                   VALUES('marketplace_source_rules',?,?,?)
                   ON CONFLICT(setting_key) DO UPDATE SET
                       value_json=excluded.value_json,updated_by=excluded.updated_by,
                       updated_at=excluded.updated_at""",
                (
                    json.dumps({"version": 1, "marketplaces": rules}, ensure_ascii=False, separators=(",", ":")),
                    int(actor_user_id), stamp,
                ),
            )
            self._audit(
                conn, actor_user_id, "marketplace_source_rules_updated", None,
                "platform_settings", "marketplace_source_rules",
                {"marketplaces": sorted(rules)},
            )
            conn.commit()
        finally:
            conn.close()
        return rules

    def preview_marketplace_source(
        self,
        source_input: str,
        marketplace_code: str,
        rules_payload: Any = None,
    ) -> dict[str, Any]:
        rules = (
            validate_marketplace_source_rules(rules_payload)
            if isinstance(rules_payload, dict)
            else self.marketplace_source_rules()
        )
        result = parse_marketplace_source(source_input, marketplace_code, rules)
        definition = MARKETPLACE_BY_CODE[result["marketplace_code"]]
        return {**result, "marketplace_name": definition.label, "verified": True}

    @staticmethod
    def company_profile_missing(tenant: sqlite3.Row | dict[str, Any]) -> list[str]:
        value = dict(tenant)
        fields = (
            ("name", "Название компании"),
            ("registration_number", "Регистрационный номер / БИН"),
            ("contact_email", "Email компании"),
            ("contact_phone", "Телефон компании"),
        )
        return [label for key, label in fields if not str(value.get(key) or "").strip()]

    @staticmethod
    def _source_from_url(code: str, seller_url: str) -> dict[str, Any]:
        return parse_marketplace_source(
            seller_url, code, DEFAULT_MARKETPLACE_SOURCE_RULES
        )

    def workspace_templates(self) -> list[dict[str, Any]]:
        templates = [_copy_template(item) for item in WORKSPACE_TEMPLATES]
        for template in templates:
            template["marketplace_categories"]["forte_market"] = [str(category) for category in FORTE_MARKET_CATEGORIES]
        return templates

    def default_workspace_template_code(self) -> str:
        return WORKSPACE_TEMPLATE_DEFAULT

    def workspace_profile_for_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return build_workspace_profile(payload)

    def workspace_profile_for_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        profile = _json_or_default(value.get("workspace_profile_json"), {})
        if not isinstance(profile, dict) or not profile:
            profile = build_workspace_profile(
                {
                    "company_name": value.get("company_name") or value.get("name") or "",
                    "comment": value.get("comment") or "",
                    "launch_mode": "review",
                    "template_code": value.get("plan_code") or WORKSPACE_TEMPLATE_DEFAULT,
                    "marketplaces": _json_or_default(value.get("integrations_json"), []),
                }
            )
        else:
            profile = dict(profile)
        template = _template_by_code(profile.get("template_code") or value.get("plan_code"))
        has_explicit_integrations = "selected_integrations" in profile or "marketplaces" in profile
        selected = _normalize_codes(
            profile.get("selected_integrations") if "selected_integrations" in profile
            else profile.get("marketplaces") if "marketplaces" in profile
            else _json_or_default(value.get("integrations_json"), []),
            {item["code"] for item in INTEGRATION_CATALOG},
        )
        if not selected and not has_explicit_integrations:
            selected = [code for code in template["recommended_integrations"] if code in {item["code"] for item in INTEGRATION_CATALOG}]
        profile["mode"] = str(profile.get("mode") or "review").strip().casefold()
        if profile["mode"] not in {"self_service", "review"}:
            profile["mode"] = "review"
        profile["mode_label"] = "Сразу создать компанию" if profile["mode"] == "self_service" else "Только заявка"
        profile["template_code"] = template["code"]
        profile["template_label"] = template["label"]
        profile["template_description"] = template["description"]
        profile["theme"] = str(profile.get("theme") or template["theme"]).strip().casefold()
        if profile["theme"] not in {"system", "light", "dark"}:
            profile["theme"] = template["theme"]
        profile["theme_label"] = _theme_label(profile["theme"])
        profile["theme_key"] = f"theme_{profile['theme']}" if profile["theme"] in {"system", "light", "dark"} else "theme_system"
        profile["selected_integrations"] = selected
        profile["selected_integration_names"] = [
            item["name"] for item in INTEGRATION_CATALOG if item["code"] in selected
        ]
        profile["categories"] = [
            str(category) for category in profile.get("categories", template["categories"]) or template["categories"]
        ]
        profile["marketplace_categories"] = {
            str(code): [str(category) for category in categories]
            for code, categories in dict(profile.get("marketplace_categories") or template["marketplace_categories"]).items()
        }
        profile["marketplace_categories"]["forte_market"] = [str(category) for category in FORTE_MARKET_CATEGORIES]
        profile["company_name"] = str(value.get("company_name") or profile.get("company_name") or value.get("name") or "").strip()
        return profile

    def registration_requests(self) -> list[dict[str, Any]]:
        conn=self._connect()
        try:
            out=[]
            for row in conn.execute("SELECT * FROM registration_requests ORDER BY created_at DESC").fetchall():
                item=dict(row)
                try:item["integrations"]=json.loads(item.pop("integrations_json") or "[]")
                except json.JSONDecodeError:item["integrations"]=[]
                try:item["capabilities"]=json.loads(item.pop("capabilities_json") or "[]")
                except json.JSONDecodeError:item["capabilities"]=[]
                out.append(item)
            return out
        finally: conn.close()

    def submit_registration_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        company = str(payload.get("company_name") or "").strip()
        contact = str(payload.get("contact_name") or "").strip()
        email = str(payload.get("email") or "").strip().casefold()
        registration_number = str(payload.get("registration_number") or "").strip()
        phone = str(payload.get("phone") or "").strip()
        legal_address = str(
            payload.get("legal_address") or ""
        ).strip()
        actual_address = str(
            payload.get("actual_address") or ""
        ).strip()
        locale = str(payload.get("locale") or "ru").casefold()
        locale = locale if locale in {"ru", "kk", "en"} else "ru"
        known_capabilities = {item["code"] for item in PUBLIC_CAPABILITIES}
        requested = payload.get("capabilities")
        if isinstance(requested, str):
            requested = [requested]
        capabilities = [
            value
            for value in dict.fromkeys(str(x).strip() for x in (requested or []))
            if value in known_capabilities
        ]
        if not registration_number:
            raise ValueError("Укажите регистрационный номер / БИН.")
        phone = re.sub(
            r"[^\d+]",
            "",
            phone,
        )

        if not re.fullmatch(
            r"\+\d{7,15}",
            phone,
        ):
            raise ValueError(
                "\u0423\u043a\u0430\u0436\u0438\u0442\u0435 "
                "\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u044b\u0439 "
                "\u0442\u0435\u043b\u0435\u0444\u043e\u043d "
                "\u043a\u043e\u043c\u043f\u0430\u043d\u0438\u0438."
            )

        if (
            str(payload.get("source_page") or "")
            == "public_registration"
        ):
            if len(legal_address) < 5:
                raise ValueError(
                    "\u0423\u043a\u0430\u0436\u0438\u0442\u0435 "
                    "\u044e\u0440\u0438\u0434\u0438\u0447\u0435\u0441\u043a\u0438\u0439 "
                    "\u0430\u0434\u0440\u0435\u0441."
                )

            if len(actual_address) < 5:
                raise ValueError(
                    "\u0423\u043a\u0430\u0436\u0438\u0442\u0435 "
                    "\u0444\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 "
                    "\u0430\u0434\u0440\u0435\u0441."
                )
        if len(company) < 2:
            raise ValueError("Укажите название компании.")
        if len(contact) < 2:
            raise ValueError("Укажите контактное лицо.")
        if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            raise ValueError("Укажите корректную электронную почту.")
        if not bool(payload.get("privacy_consent")):
            raise ValueError("Необходимо согласие с Политикой конфиденциальности.")
        if (
            str(payload.get("source_page") or "") == "public_registration"
            and not bool(payload.get("offer_acceptance"))
        ):
            raise ValueError("Необходимо принять Публичную оферту Spyon.")
        raw_estimated_value = payload.get("estimated_products")
        is_public_registration = (
            str(payload.get("source_page") or "")
            == "public_registration"
        )

        if (
            is_public_registration
            and raw_estimated_value in (None, "")
        ):
            raise ValueError(
                "\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u043f\u0440\u0438\u043c\u0435\u0440\u043d\u043e\u0435 \u0447\u0438\u0441\u043b\u043e \u0442\u043e\u0432\u0430\u0440\u043e\u0432."
            )

        raw_estimated = str(
            raw_estimated_value
            if raw_estimated_value not in (None, "")
            else "0"
        ).strip()

        if not re.fullmatch(
            r"\d{1,8}",
            raw_estimated,
        ):
            raise ValueError(
                "\u041f\u0440\u0438\u043c\u0435\u0440\u043d\u043e\u0435 "
                "\u0447\u0438\u0441\u043b\u043e "
                "\u0442\u043e\u0432\u0430\u0440\u043e\u0432 "
                "\u0434\u043e\u043b\u0436\u043d\u043e "
                "\u0431\u044b\u0442\u044c "
                "\u0446\u0435\u043b\u044b\u043c "
                "\u0447\u0438\u0441\u043b\u043e\u043c."
            )

        estimated = int(
            raw_estimated
        )

        if is_public_registration and estimated < 1:
            raise ValueError(
                "\u041f\u0440\u0438\u043c\u0435\u0440\u043d\u043e\u0435 \u0447\u0438\u0441\u043b\u043e \u0442\u043e\u0432\u0430\u0440\u043e\u0432 \u0434\u043e\u043b\u0436\u043d\u043e \u0431\u044b\u0442\u044c \u043d\u0435 \u043c\u0435\u043d\u044c\u0448\u0435 1."
            )

        if estimated > 10_000_000:
            raise ValueError(
                "\u0421\u043b\u0438\u0448\u043a\u043e\u043c "
                "\u0431\u043e\u043b\u044c\u0448\u043e\u0435 "
                "\u0447\u0438\u0441\u043b\u043e "
                "\u0442\u043e\u0432\u0430\u0440\u043e\u0432."
            )
        profile = self.workspace_profile_for_payload(payload)
        if not profile["selected_integrations"]:
            raise ValueError("Выберите хотя бы один маркетплейс для заявки.")
        stamp = now_iso()
        conn = self._connect()
        try:
            # Serialize registration of the same BIN/email. PostgreSQL cannot
            # receive a strict legacy unique index yet because old demo rows
            # contain duplicates; advisory locks still make every new signup
            # and edit race-safe.
            if isinstance(conn, PostgresConnection):
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"bin:{registration_number.casefold()}",))
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"email:{email}",))
            else:
                conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                """SELECT name FROM tenants
                   WHERE lower(COALESCE(registration_number,''))=lower(?)
                      OR lower(COALESCE(contact_email,''))=lower(?)
                   LIMIT 1""",
                (registration_number, email),
            ).fetchone()
            if duplicate:
                raise ValueError("Компания с таким БИН или email уже зарегистрирована.")
            duplicate_request = conn.execute(
                """SELECT id FROM registration_requests
                   WHERE status IN ('new','review','pending')
                     AND (lower(registration_number)=lower(?) OR lower(email)=lower(?))
                   LIMIT 1""",
                (registration_number, email),
            ).fetchone()
            if duplicate_request:
                raise ValueError("Заявка с таким БИН или email уже ожидает рассмотрения.")
            cursor = conn.execute(
                """
                INSERT INTO registration_requests(
                    company_name,registration_number,contact_name,email,phone,
                    legal_address,actual_address,
                    integrations_json,capabilities_json,estimated_products,comment,status,
                    consent_version,consent_at,locale,source_page,workspace_profile_json,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'new',?,?,?,?,?,?,?)
                """,
                (
                    company,
                    registration_number,
                    contact,
                    email,
                    phone,
                    legal_address,
                    actual_address,
                    json.dumps(profile["selected_integrations"], ensure_ascii=False),
                    json.dumps(capabilities, ensure_ascii=False),
                    estimated,
                    str(payload.get("comment") or "").strip(),
                    CONSENT_VERSION,
                    stamp,
                    locale,
                    str(payload.get("source_page") or "public_site"),
                    json.dumps(profile, ensure_ascii=False),
                    stamp,
                    stamp,
                ),
            )
            conn.commit()
            return {"request_id": int(cursor.lastrowid), "workspace_profile": profile}
        finally:
            conn.close()

    def registration_requests_view(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for item in self.registration_requests():
            item["workspace_profile"] = self.workspace_profile_for_row(item)
            item["status_raw"] = item.get("status")
            # Registration request workflow is separate from company status.
            # Older self-service requests used `pending`; expose those as `new`
            # so the platform reviewer still receives approve/reject controls.
            item["status"] = "new" if str(item.get("status")) == "pending" else str(item.get("status"))
            item["status_label"] = {
                "new": "Новая",
                "review": "На рассмотрении",
                "approved": "Подтверждена",
                "declined": "Отклонена",
                "rejected": "Отклонена",
            }.get(item["status"], item["status"])
            items.append(item)
        return items

    def provision_tenant_from_request(
        self,
        request_id: int,
        actor_user_id: int | None,
        request_status: str = "pending",
        *,
        grant_marketplaces: bool = True,
        conn: Any | None = None,
        commit: bool = True,
    ) -> dict[str, Any]:
        tenant_status = canonical_company_status(request_status)
        stamp = now_iso()
        owns_connection = conn is None
        conn = conn or self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM registration_requests WHERE id=?",
                (int(request_id),),
            ).fetchone()
            if not row:
                raise ValueError("Заявка не найдена.")
            if row["tenant_id"] is not None:
                tenant_id = int(row["tenant_id"])
                tenant_row = conn.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
                if tenant_row:
                    request = dict(row)
                    request["workspace_profile"] = self.workspace_profile_for_row(row)
                    tenant = dict(tenant_row)
                    tenant["workspace_profile"] = request["workspace_profile"]
                    return {
                        "request": request,
                        "tenant": tenant,
                        "tenant_id": tenant_id,
                        "workspace_profile": request["workspace_profile"],
                    }
            if row["status"] not in {"new", "review", "pending"}:
                raise ValueError("Заявка уже обработана.")
            profile = self.workspace_profile_for_row(row)
            cur = conn.execute(
                """
                INSERT INTO tenants(
                    name,slug,registration_number,status,plan_code,
                    contact_email,contact_phone,legal_address,actual_address,
                    workspace_profile_json,created_at,updated_at,approved_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row["company_name"],
                    self._unique_slug(conn, row["company_name"]),
                    row["registration_number"],
                    tenant_status,
                    profile["template_code"],
                    row["email"],
                    row["phone"],
                    str(row["legal_address"] or ""),
                    str(row["actual_address"] or ""),
                    json.dumps(profile, ensure_ascii=False),
                    stamp,
                    stamp,
                    stamp if tenant_status == "approved" else None,
                ),
            )
            tenant_id = int(cur.lastrowid)
            self._seed_tenant_security(conn, tenant_id, stamp)
            for item in INTEGRATION_CATALOG:
                conn.execute(
                    """
                    INSERT INTO tenant_integrations(
                        tenant_id,integration_code,display_name,status,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (tenant_id, item["code"], item["name"], "disabled", stamp, stamp),
                )
            if (
                tenant_status == "approved"
                and grant_marketplaces
            ):
                self._write_marketplace_access(
                    conn,
                    tenant_id,
                    profile["selected_integrations"],
                    actor_user_id,
                    stamp,
                )
            request_workflow_status = "pending" if tenant_status == "pending" else tenant_status
            conn.execute(
                """
                UPDATE registration_requests
                SET status=?,tenant_id=?,reviewed_by=?,reviewed_at=?,updated_at=?
                WHERE id=?
                """,
                (request_workflow_status, tenant_id, actor_user_id, stamp, stamp, int(request_id)),
            )
            self._audit(
                conn,
                actor_user_id,
                "registration_reviewed",
                tenant_id,
                "registration_request",
                str(request_id),
                {"status": tenant_status, "template_code": profile["template_code"]},
            )
            if commit:
                conn.commit()
            tenant = dict(conn.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone())
            tenant["workspace_profile"] = profile
            request = dict(row)
            request["status"] = request_workflow_status
            request["tenant_id"] = tenant_id
            request["reviewed_by"] = actor_user_id
            request["reviewed_at"] = stamp
            request["workspace_profile"] = profile
            return {"request": request, "tenant": tenant, "tenant_id": tenant_id, "workspace_profile": profile}
        finally:
            if owns_connection:
                conn.close()

    def review_registration_v2(self, request_id: int, decision: str, actor_user_id: int) -> dict[str, Any]:
        raw_decision = str(decision or "").casefold()
        if raw_decision not in {"approved", "declined", "rejected"}:
            raise ValueError("Неизвестное решение.")
        target_status = "approved" if raw_decision == "approved" else "rejected"
        request_status = "approved" if target_status == "approved" else "declined"
        stamp = now_iso()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM registration_requests WHERE id=?",
                (int(request_id),),
            ).fetchone()
            if not row:
                raise ValueError("Заявка не найдена.")
            if canonical_company_status(row["status"]) not in {"pending"}:
                raise ValueError("Заявка уже обработана.")
            tenant_id = int(row["tenant_id"]) if row["tenant_id"] is not None else None
            if tenant_id is None and target_status == "approved":
                conn.close()
                return self.provision_tenant_from_request(
                    request_id, actor_user_id, "approved"
                )["request"]
            if tenant_id is not None:
                if target_status == "approved":
                    tenant_row = conn.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
                    missing = self.company_profile_missing(tenant_row) if tenant_row else ["Компания"]
                    if missing:
                        raise ValueError("Заполните обязательные поля компании: " + ", ".join(missing) + ".")
                conn.execute(
                    """UPDATE tenants SET status=?,updated_at=?,approved_at=? WHERE id=?""",
                    (
                        target_status,
                        stamp,
                        stamp if target_status == "approved" else None,
                        tenant_id,
                    ),
                )
                if target_status == "approved":
                    profile = self.workspace_profile_for_row(row)
                    self._write_marketplace_access(
                        conn, tenant_id, profile["selected_integrations"], actor_user_id, stamp
                    )
            conn.execute(
                """
                UPDATE registration_requests
                SET status=?,reviewed_by=?,reviewed_at=?,updated_at=?
                WHERE id=?
                """,
                (request_status, actor_user_id, stamp, stamp, int(request_id)),
            )
            self._audit(
                conn,
                actor_user_id,
                "registration_reviewed",
                tenant_id,
                "registration_request",
                str(request_id),
                {"status": target_status},
            )
            conn.commit()
            item = dict(row)
            item["status"] = request_status
            item["reviewed_by"] = actor_user_id
            item["reviewed_at"] = stamp
            item["workspace_profile"] = self.workspace_profile_for_row(item)
            return item
        finally:
            try:
                conn.close()
            except database_error_types():
                pass

    def platform_overview_with_profiles(
        self,
        current_catalog_count: int = 0,
        current_processed_count: int = 0,
    ) -> dict[str, Any]:
        result = self.platform_overview(current_catalog_count, current_processed_count)
        for tenant in result.get("tenants", []):
            tenant["workspace_profile"] = self.workspace_profile_for_row(tenant)
        return result

    def tenant_detail_with_profile(self, tenant_id: int) -> dict[str, Any]:
        result = self.tenant_detail(tenant_id)
        result["tenant"]["workspace_profile"] = self.workspace_profile_for_row(result["tenant"])
        return result

    def _unique_slug(self, conn: sqlite3.Connection, name: str) -> str:
        base=slugify(name); value=base; i=2
        while conn.execute("SELECT 1 FROM tenants WHERE slug=?",(value,)).fetchone():
            value=f"{base}-{i}"; i+=1
        return value

    def platform_overview(self,current_catalog_count:int=0,current_processed_count:int=0)->dict[str,Any]:
        conn=self._connect()
        try:
            default_row=conn.execute("SELECT id FROM tenants ORDER BY id LIMIT 1").fetchone()
            default_id=int(default_row["id"]) if default_row else 0
            tenants=[]
            rows=conn.execute(
                """
                SELECT t.*,COUNT(DISTINCT tu.user_id) users_count,
                       COUNT(DISTINCT CASE WHEN ti.status IN ('active','setup') AND ti.approval_status='approved' THEN ti.id END) integrations_count,
                       COUNT(DISTINCT CASE WHEN ti.status='error' OR COALESCE(ti.last_error,'')<>'' THEN ti.id END) integration_errors,
                       MAX(ti.last_sync_at) last_sync_at,
                       (SELECT COUNT(*) FROM operation_schedules os WHERE os.tenant_id=t.id AND os.is_enabled=1) schedules_count,
                       (SELECT COALESCE(SUM(COALESCE(tic.product_count,0)),0)
                          FROM tenant_integrations tic WHERE tic.tenant_id=t.id) integration_product_count
                FROM tenants t LEFT JOIN tenant_users tu ON tu.tenant_id=t.id AND tu.is_active=1
                LEFT JOIN tenant_integrations ti ON ti.tenant_id=t.id
                GROUP BY t.id ORDER BY t.created_at DESC
                """
            ).fetchall()
            owners: dict[int, dict[str, Any]] = {}
            for owner_row in conn.execute(
                """SELECT tu.tenant_id,u.display_name,u.email FROM tenant_users tu
                   JOIN app_users u ON u.id=tu.user_id
                   WHERE tu.is_active=1 AND u.is_active=1
                   ORDER BY tu.tenant_id,
                            CASE WHEN tu.tenant_role='admin' THEN 0 ELSE 1 END,
                            tu.is_primary DESC,tu.created_at"""
            ).fetchall():
                owners.setdefault(int(owner_row["tenant_id"]), {
                    "display_name": owner_row["display_name"], "email": owner_row["email"],
                })
            access_by_tenant: dict[int, list[dict[str, Any]]] = {}
            for access_row in conn.execute(
                """SELECT tma.tenant_id,tma.marketplace_code,tma.is_allowed,
                          ti.status,ti.approval_status
                   FROM tenant_marketplace_access tma
                   JOIN tenant_integrations ti ON ti.tenant_id=tma.tenant_id
                    AND ti.integration_code=tma.marketplace_code"""
            ).fetchall():
                access_by_tenant.setdefault(int(access_row["tenant_id"]), []).append(dict(access_row))
            for row in rows:
                item=dict(row)
                item["owner"] = owners.get(int(item["id"]))
                access_rows = access_by_tenant.get(int(item["id"]), [])
                allowed_codes = {str(x["marketplace_code"]) for x in access_rows if bool(x["is_allowed"])}
                connected_codes = {
                    str(x["marketplace_code"]) for x in access_rows
                    if bool(x["is_allowed"]) and str(x["status"]) == "active"
                    and str(x.get("approval_status") or "") == "approved"
                }
                item["available_marketplaces"] = [
                    definition["name"] for definition in INTEGRATION_CATALOG
                    if definition["code"] in allowed_codes
                ]
                item["connected_marketplaces"] = [
                    definition["name"] for definition in INTEGRATION_CATALOG
                    if definition["code"] in connected_codes
                ]
                item["status_raw"]=item.get("status")
                item["status"]=canonical_company_status(item.get("status"))
                item["status_label"]=company_status_label(item["status"])
                integration_product_count = int(item.pop("integration_product_count", 0) or 0)
                item["product_count"]=(
                    int(current_catalog_count)
                    if int(item["id"])==default_id and int(current_catalog_count)>0
                    else integration_product_count
                )
                item["processed_count"]=int(current_processed_count) if int(item["id"])==default_id else 0
                tenants.append(item)
            return {"tenants":tenants,"totals":{"tenants":len(tenants),"active_tenants":sum(1 for x in tenants if company_is_approved(x["status"])),"new_requests":int(conn.execute("SELECT COUNT(*) FROM registration_requests WHERE status IN ('new','review','pending')").fetchone()[0]),"enabled_schedules":int(conn.execute("SELECT COUNT(*) FROM operation_schedules WHERE is_enabled=1").fetchone()[0]),"products":sum(int(x.get("product_count") or 0) for x in tenants)}}
        finally: conn.close()

    def tenant_detail(self, tenant_id: int) -> dict[str, Any]:
        conn=self._connect()
        try:
            tenant=conn.execute("SELECT * FROM tenants WHERE id=?",(int(tenant_id),)).fetchone()
            if tenant is None: raise ValueError("Компания не найдена.")
            integrations=[]
            for integration_row in conn.execute(
                "SELECT * FROM tenant_integrations WHERE tenant_id=? ORDER BY id",
                (int(tenant_id),),
            ).fetchall():
                integration = dict(integration_row)
                integration["config"] = _json_or_default(integration.pop("config_json", "{}"), {})
                integration["discovery"] = _json_or_default(integration.pop("discovery_json", "{}"), {})
                integrations.append(integration)
            sellers=[]
            for seller_row in conn.execute(
                """SELECT * FROM tenant_marketplace_sellers
                   WHERE tenant_id=?
                   ORDER BY marketplace_code,
                            CASE WHEN status='active' AND approval_status='approved' THEN 0
                                 WHEN approval_status='pending' THEN 1 ELSE 2 END,
                            id""",
                (int(tenant_id),),
            ).fetchall():
                seller = dict(seller_row)
                seller["discovery"] = _json_or_default(seller.pop("discovery_json", "{}"), {})
                sellers.append(seller)
            users=[dict(r) for r in conn.execute("""SELECT u.id,u.display_name,u.email,u.role,u.is_active,tu.tenant_role FROM tenant_users tu JOIN app_users u ON u.id=tu.user_id WHERE tu.tenant_id=? ORDER BY u.display_name""",(int(tenant_id),)).fetchall()]
            schedules=[dict(r) for r in conn.execute("""SELECT id,name,action,platform,is_enabled,last_run_at,next_run_at,last_status,last_error FROM operation_schedules WHERE tenant_id=? ORDER BY is_enabled DESC,next_run_at""",(int(tenant_id),)).fetchall()]
            recent_runs=[dict(r) for r in conn.execute("""SELECT r.*,s.name schedule_name FROM schedule_runs r JOIN operation_schedules s ON s.id=r.schedule_id WHERE r.tenant_id=? ORDER BY r.started_at DESC LIMIT 20""",(int(tenant_id),)).fetchall()]
            tenant_value=dict(tenant)
            tenant_value["status_raw"]=tenant_value.get("status")
            tenant_value["status"]=canonical_company_status(tenant_value.get("status"))
            tenant_value["status_label"]=company_status_label(tenant_value["status"])
            tenant_value["profile_missing"] = self.company_profile_missing(tenant_value)
            tenant_value["profile_complete"] = not tenant_value["profile_missing"]
            owner = next((item for item in users if str(item.get("tenant_role")) == "admin" and bool(item.get("is_active"))), users[0] if users else None)
            return {
                "tenant":tenant_value,
                "owner":owner,
                "marketplace_access":self.marketplace_access(tenant_id),
                "integrations":integrations,
                "sellers":sellers,
                "users":users,
                "schedules":schedules,
                "recent_runs":recent_runs,
            }
        finally: conn.close()

    def update_tenant(self,tenant_id:int,payload:dict[str,Any],actor_user_id:int)->dict[str,Any]:
        raw_status=str(payload.get("status") or "").casefold(); name=str(payload.get("name") or "").strip()
        status=canonical_company_status(raw_status) if raw_status else ""
        if raw_status and raw_status not in COMPANY_STATUS_LABELS and raw_status not in {"setup","active","confirmed","declined","suspended","archived"}: raise ValueError("Неизвестный статус компании.")
        conn=self._connect()
        try:
            row=conn.execute("SELECT * FROM tenants WHERE id=?",(int(tenant_id),)).fetchone()
            if not row: raise ValueError("Компания не найдена.")
            if status == "approved":
                missing = self.company_profile_missing(row)
                if missing:
                    raise ValueError("Заполните обязательные поля компании: " + ", ".join(missing) + ".")
            fields=[]; params=[]
            if name: fields.append("name=?"); params.append(name)
            if status:
                fields.append("status=?"); params.append(status)
                fields.append("approved_at=?"); params.append(now_iso() if status=="approved" else None)
            if fields:
                fields.append("updated_at=?"); params.append(now_iso()); params.append(int(tenant_id)); conn.execute(f"UPDATE tenants SET {', '.join(fields)} WHERE id=?",params)
                if status in {"approved", "rejected"}:
                    conn.execute(
                        """UPDATE registration_requests SET status=?,reviewed_by=?,reviewed_at=?,updated_at=?
                           WHERE tenant_id=? AND status IN ('new','review','pending')""",
                        (
                            "approved" if status == "approved" else "declined",
                            int(actor_user_id), now_iso(), now_iso(), int(tenant_id),
                        ),
                    )
                self._audit(conn,actor_user_id,"tenant_updated",tenant_id,"tenant",str(tenant_id),payload); conn.commit()
            result=dict(conn.execute("SELECT * FROM tenants WHERE id=?",(int(tenant_id),)).fetchone())
            result["status_raw"]=result.get("status")
            result["status"]=canonical_company_status(result.get("status"))
            result["status_label"]=company_status_label(result["status"])
            return result
        finally: conn.close()

    def update_tenant_profile(self, tenant_id: int, payload: dict[str, Any], actor_user_id: int) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        registration_number = str(payload.get("registration_number") or "").strip()
        contact_email = str(payload.get("contact_email") or "").strip().casefold()
        contact_phone = str(payload.get("contact_phone") or "").strip()
        if len(name) < 2:
            raise ValueError("Укажите название компании.")
        if not registration_number:
            raise ValueError("Укажите регистрационный номер / БИН.")
        if "@" not in contact_email or "." not in contact_email.rsplit("@", 1)[-1]:
            raise ValueError("Укажите корректный email компании.")
        if not contact_phone:
            raise ValueError("Укажите телефон компании.")
        stamp = now_iso()
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM tenants WHERE id=?", (int(tenant_id),)).fetchone()
            if not row:
                raise ValueError("Компания не найдена.")
            legal_address = str(
                payload.get("legal_address")
                if "legal_address" in payload
                else row["legal_address"] or ""
            ).strip()

            actual_address = str(
                payload.get("actual_address")
                if "actual_address" in payload
                else row["actual_address"] or ""
            ).strip()

            if (
                "legal_address" in payload
                and len(legal_address) < 5
            ):
                raise ValueError(
                    "\u0423\u043a\u0430\u0436\u0438\u0442\u0435 "
                    "\u044e\u0440\u0438\u0434\u0438\u0447\u0435\u0441\u043a\u0438\u0439 "
                    "\u0430\u0434\u0440\u0435\u0441."
                )

            if (
                "actual_address" in payload
                and len(actual_address) < 5
            ):
                raise ValueError(
                    "\u0423\u043a\u0430\u0436\u0438\u0442\u0435 "
                    "\u0444\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 "
                    "\u0430\u0434\u0440\u0435\u0441."
                )

            if isinstance(conn, PostgresConnection):
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"bin:{registration_number.casefold()}",))
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"email:{contact_email}",))
            else:
                conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                """SELECT id FROM tenants WHERE id<>?
                   AND (lower(COALESCE(registration_number,''))=lower(?)
                     OR lower(COALESCE(contact_email,''))=lower(?)) LIMIT 1""",
                (int(tenant_id), registration_number, contact_email),
            ).fetchone()
            if duplicate:
                raise ValueError("БИН или email уже используется другой компанией.")
            conn.execute(
                """
                UPDATE tenants
                SET name=?,
                    registration_number=?,
                    contact_email=?,
                    contact_phone=?,
                    legal_address=?,
                    actual_address=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    name,
                    registration_number,
                    contact_email,
                    contact_phone,
                    legal_address,
                    actual_address,
                    stamp,
                    int(tenant_id),
                ),
            )
            self._audit(
                conn,
                actor_user_id,
                "tenant_profile_updated",
                int(tenant_id),
                "tenant",
                str(tenant_id),
                {
                    "name": name,
                    "registration_number": registration_number,
                    "contact_email": contact_email,
                    "contact_phone": contact_phone,
                    "legal_address": legal_address,
                    "actual_address": actual_address,
                },
            )
            conn.commit()
            return dict(conn.execute("SELECT * FROM tenants WHERE id=?", (int(tenant_id),)).fetchone())
        finally:
            conn.close()

    @staticmethod
    def next_run_for(
        recurrence_type: str,
        time_of_day: str | None,
        weekdays: list[int] | None,
        interval_minutes: int | None,
        run_date: str | None = None,
        base: datetime | None = None,
    ) -> str | None:
        now = base or datetime.now().astimezone()
        recurrence = str(recurrence_type or "daily").casefold()

        if recurrence == "interval":
            return (
                now + timedelta(minutes=max(60, int(interval_minutes or 360)))
            ).isoformat(timespec="seconds")

        try:
            hour, minute = [int(x) for x in str(time_of_day or "03:00").split(":", 1)]
        except (TypeError, ValueError):
            hour, minute = 3, 0
        hour = max(0, min(hour, 23))
        minute = max(0, min(minute, 59))

        if recurrence == "once":
            value = str(run_date or "").strip()
            if not value:
                return None
            try:
                day = datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                return None
            candidate = day.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
                tzinfo=now.tzinfo,
            )
            return candidate.isoformat(timespec="seconds") if candidate > now else None

        if recurrence == "weekly":
            days = sorted({max(0, min(int(x), 6)) for x in (weekdays or [0])})
            for offset in range(8):
                day = now + timedelta(days=offset)
                if day.weekday() not in days:
                    continue
                candidate = day.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                if candidate > now:
                    return candidate.isoformat(timespec="seconds")

        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.isoformat(timespec="seconds")

    def schedules(self,tenant_id:int)->list[dict[str,Any]]:
        conn=self._connect()
        try:
            out=[]
            for row in conn.execute(
                """SELECT os.*,s.external_seller_id,s.display_name AS seller_name
                   FROM operation_schedules os
                   LEFT JOIN tenant_marketplace_sellers s
                     ON s.id=os.tenant_seller_id AND s.tenant_id=os.tenant_id
                   WHERE os.tenant_id=?
                   ORDER BY os.is_enabled DESC,os.next_run_at,os.name""",
                (int(tenant_id),),
            ).fetchall():
                item=dict(row)
                try:item["weekdays"]=json.loads(item.pop("weekdays_json") or "[]")
                except json.JSONDecodeError:item["weekdays"]=[]
                out.append(item)
            return out
        finally: conn.close()

    def schedule(self,schedule_id:int,tenant_id:int|None=None)->dict[str,Any]|None:
        conn=self._connect()
        try:
            q=("SELECT os.*,s.external_seller_id,s.display_name AS seller_name "
               "FROM operation_schedules os LEFT JOIN tenant_marketplace_sellers s "
               "ON s.id=os.tenant_seller_id AND s.tenant_id=os.tenant_id WHERE os.id=?")
            params=[int(schedule_id)]
            if tenant_id is not None:q+=" AND os.tenant_id=?"; params.append(int(tenant_id))
            row=conn.execute(q,params).fetchone()
            if not row:return None
            item=dict(row)
            try:item["weekdays"]=json.loads(item.pop("weekdays_json") or "[]")
            except json.JSONDecodeError:item["weekdays"]=[]
            return item
        finally: conn.close()

    def create_schedule(
        self, tenant_id: int, payload: dict[str, Any], actor_user_id: int
    ) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        action = str(payload.get("action") or "").strip()
        recurrence = str(payload.get("recurrence_type") or "daily").casefold()

        if len(name) < 2:
            raise ValueError("Укажите название задания.")
        if action not in SCHEDULE_ACTIONS:
            raise ValueError("Выберите поддерживаемую операцию.")
        if recurrence not in {"once", "daily", "weekly", "interval"}:
            raise ValueError("Неизвестный тип расписания.")

        weekdays = payload.get("weekdays") if isinstance(payload.get("weekdays"), list) else []
        if recurrence == "weekly" and not weekdays:
            raise ValueError("Выберите хотя бы один день недели.")

        try:
            interval = max(60, min(int(payload.get("interval_minutes") or 360), 10080))
        except (TypeError, ValueError):
            interval = 360

        tod = str(payload.get("time_of_day") or "03:00")
        run_date = str(payload.get("run_date") or "").strip() or None
        next_run = self.next_run_for(
            recurrence, tod, weekdays, interval, run_date=run_date
        )
        enabled = bool(payload.get("is_enabled", True))
        if recurrence == "once" and enabled and next_run is None:
            raise ValueError("Для однократного запуска выберите будущую дату и время.")

        platform = marketplace_for_action(action)
        requested_seller_id = int(payload.get("tenant_seller_id") or 0)
        selected_seller_id: int | None = None
        if platform in MARKETPLACE_BY_CODE:
            active_sellers = self.sellers(
                int(tenant_id), platform, active_only=True
            )
            if requested_seller_id or active_sellers:
                selected = self.resolve_seller(
                    int(tenant_id), platform, requested_seller_id or None
                )
                selected_seller_id = int(selected.get("id") or 0) or None
        stamp = now_iso()
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO operation_schedules(
                    tenant_id,name,action,platform,tenant_seller_id,scope,recurrence_type,time_of_day,
                    run_date,weekdays_json,interval_minutes,is_enabled,retry_count,
                    max_duration_minutes,next_run_at,created_by,created_at,updated_at
                )
                VALUES(?,?,?,?,?,'all',?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(tenant_id), name, action, platform, selected_seller_id,
                    recurrence, tod,
                    run_date, json.dumps(weekdays), interval,
                    1 if enabled else 0,
                    max(0, min(int(payload.get("retry_count") or 1), 5)),
                    max(10, min(int(payload.get("max_duration_minutes") or 180), 1440)),
                    next_run if enabled else None,
                    int(actor_user_id), stamp, stamp,
                ),
            )
            conn.commit()
            return self.schedule(int(cur.lastrowid), tenant_id) or {}
        finally:
            conn.close()

    def update_schedule(
        self,
        schedule_id: int,
        tenant_id: int,
        payload: dict[str, Any],
        actor_user_id: int,
    ) -> dict[str, Any]:
        current = self.schedule(schedule_id, tenant_id)
        if not current:
            raise ValueError("Расписание не найдено.")

        merged = {**current, **payload}
        recurrence = str(merged.get("recurrence_type") or "daily").casefold()
        if recurrence not in {"once", "daily", "weekly", "interval"}:
            raise ValueError("Неизвестный тип расписания.")

        weekdays = merged.get("weekdays") if isinstance(merged.get("weekdays"), list) else []
        if recurrence == "weekly" and not weekdays:
            raise ValueError("Выберите хотя бы один день недели.")

        try:
            interval = max(60, min(int(merged.get("interval_minutes") or 360), 10080))
        except (TypeError, ValueError):
            interval = 360

        tod = str(merged.get("time_of_day") or "03:00")
        run_date = str(merged.get("run_date") or "").strip() or None
        enabled = bool(merged.get("is_enabled"))
        next_run = self.next_run_for(
            recurrence, tod, weekdays, interval, run_date=run_date
        ) if enabled else None

        if recurrence == "once" and enabled and next_run is None:
            raise ValueError("Для однократного запуска выберите будущую дату и время.")

        platform = str(current.get("platform") or marketplace_for_action(current["action"]))
        requested_seller_id = int(merged.get("tenant_seller_id") or 0)
        selected_seller_id: int | None = None
        if platform in MARKETPLACE_BY_CODE:
            active_sellers = self.sellers(
                int(tenant_id), platform, active_only=True
            )
            if requested_seller_id or active_sellers:
                selected = self.resolve_seller(
                    int(tenant_id), platform, requested_seller_id or None
                )
                selected_seller_id = int(selected.get("id") or 0) or None

        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE operation_schedules
                SET name=?,recurrence_type=?,time_of_day=?,run_date=?,
                    weekdays_json=?,interval_minutes=?,is_enabled=?,retry_count=?,
                    max_duration_minutes=?,next_run_at=?,tenant_seller_id=?,updated_at=?
                WHERE id=? AND tenant_id=?
                """,
                (
                    str(merged.get("name") or current["name"]).strip(),
                    recurrence, tod, run_date, json.dumps(weekdays), interval,
                    1 if enabled else 0,
                    max(0, min(int(merged.get("retry_count") or 1), 5)),
                    max(10, min(int(merged.get("max_duration_minutes") or 180), 1440)),
                    next_run, selected_seller_id, now_iso(),
                    int(schedule_id), int(tenant_id),
                ),
            )
            self._audit(
                conn, actor_user_id, "schedule_updated", tenant_id,
                "schedule", str(schedule_id), payload,
            )
            conn.commit()
            return self.schedule(schedule_id, tenant_id) or {}
        finally:
            conn.close()

    def delete_schedule(self,schedule_id:int,tenant_id:int,actor_user_id:int)->None:
        conn=self._connect()
        try:
            if not conn.execute("SELECT 1 FROM operation_schedules WHERE id=? AND tenant_id=?",(int(schedule_id),int(tenant_id))).fetchone(): raise ValueError("Расписание не найдено.")
            conn.execute("DELETE FROM operation_schedules WHERE id=? AND tenant_id=?",(int(schedule_id),int(tenant_id))); conn.commit()
        finally: conn.close()

    def schedule_runs(self,tenant_id:int,limit:int=50)->list[dict[str,Any]]:
        conn=self._connect()
        try:return [dict(r) for r in conn.execute("SELECT r.*,s.name schedule_name,s.action,s.platform FROM schedule_runs r JOIN operation_schedules s ON s.id=r.schedule_id WHERE r.tenant_id=? ORDER BY r.started_at DESC LIMIT ?",(int(tenant_id),max(1,min(int(limit),200)))).fetchall()]
        finally:conn.close()

    def active_schedule_runs(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            return [dict(row) for row in conn.execute(
                """SELECT r.*,s.action,s.platform,s.created_by
                   FROM schedule_runs r
                   JOIN operation_schedules s ON s.id=r.schedule_id
                   WHERE r.status IN ('queued','running')
                   ORDER BY r.started_at"""
            ).fetchall()]
        finally:
            conn.close()

    def due_schedules(self)->list[dict[str,Any]]:
        conn=self._connect()
        try:return [dict(r) for r in conn.execute("""
            SELECT os.*
            FROM operation_schedules os
            LEFT JOIN app_users u ON u.id=os.created_by
            WHERE os.is_enabled=1
              AND os.next_run_at IS NOT NULL
              AND datetime(os.next_run_at)<=datetime('now','localtime')
              AND (os.action<>'backup_database' OR COALESCE(u.platform_role,'')='superadmin')
            ORDER BY os.next_run_at LIMIT 10
            """).fetchall()]
        finally:conn.close()

    def begin_schedule_run(self, schedule: dict[str, Any]) -> int | None:
        try:
            weekdays = json.loads(schedule.get("weekdays_json") or "[]")
        except json.JSONDecodeError:
            weekdays = []

        recurrence = str(schedule.get("recurrence_type") or "daily").casefold()
        if recurrence == "once":
            next_run = None
            enabled = 0
        else:
            next_run = self.next_run_for(
                recurrence,
                schedule.get("time_of_day"),
                weekdays,
                schedule.get("interval_minutes"),
                run_date=schedule.get("run_date"),
            )
            enabled = 1

        stamp = now_iso()
        conn = self._connect()
        try:
            if isinstance(conn, PostgresConnection):
                current = conn.execute(
                    "SELECT * FROM operation_schedules WHERE id=? FOR UPDATE",
                    (int(schedule["id"]),),
                ).fetchone()
            else:
                conn.execute("BEGIN IMMEDIATE")
                current = conn.execute(
                    "SELECT * FROM operation_schedules WHERE id=?",
                    (int(schedule["id"]),),
                ).fetchone()
            if (
                not current
                or not bool(current["is_enabled"])
                or not current["next_run_at"]
                or str(current["next_run_at"]) != str(schedule.get("next_run_at") or "")
            ):
                conn.rollback()
                return None
            cur = conn.execute(
                """
                INSERT INTO schedule_runs(
                    schedule_id,tenant_id,tenant_seller_id,status,message,started_at
                ) VALUES(?,?,?,'queued','Ожидает запуска',?)
                """,
                (
                    int(schedule["id"]), int(schedule["tenant_id"]),
                    schedule.get("tenant_seller_id"), stamp,
                ),
            )
            conn.execute(
                """
                UPDATE operation_schedules
                SET next_run_at=?,is_enabled=?,last_status='queued',updated_at=?
                WHERE id=?
                """,
                (next_run, enabled, stamp, int(schedule["id"])),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def attach_task_to_run(self,run_id:int,task_id:str)->None:
        conn=self._connect(); conn.execute("UPDATE schedule_runs SET task_id=?,status='running',message='Операция запущена' WHERE id=?",(str(task_id),int(run_id))); conn.commit(); conn.close()

    def finish_schedule_run(self,run_id:int,schedule_id:int,status:str,message:str)->None:
        stamp=now_iso(); conn=self._connect()
        try:
            conn.execute("UPDATE schedule_runs SET status=?,message=?,finished_at=? WHERE id=?",(status,message,stamp,int(run_id)))
            conn.execute("UPDATE operation_schedules SET last_run_at=?,last_status=?,last_error=?,updated_at=? WHERE id=?",(stamp,status,message if status!='completed' else None,stamp,int(schedule_id))); conn.commit()
        finally:conn.close()

    @staticmethod
    def _audit(conn:sqlite3.Connection,actor_user_id:int|None,action:str,tenant_id:int|None,entity_type:str|None,entity_id:str|None,details:dict[str,Any])->None:
        conn.execute("INSERT INTO platform_audit_log(actor_user_id,action,tenant_id,entity_type,entity_id,details_json,created_at) VALUES(?,?,?,?,?,?,?)",(actor_user_id,action,tenant_id,entity_type,entity_id,json.dumps(redact_sensitive(details),ensure_ascii=False),now_iso()))
