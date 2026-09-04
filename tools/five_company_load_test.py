from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import time
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auth_service import AuthService
from catalog_configuration_service import (
    CatalogConfigurationService,
    normalized_attribute_key,
)
from data_service import DataService
from saas_service import SaaSService
from schema import ensure_database
from tests.subscription_fixtures import activate_trial_subscription


SCENARIOS = (
    {
        "name": "Auto Parts KZ", "code": "kaspi", "seller": "Auto Parts Store",
        "url": "https://kaspi.kz/shop/m/12917020/products?productCode=123271857&masterSku=123271857",
        "action": "kaspi_catalog_collect", "attributes": {"Сезон": "Зима", "Ширина": "205"},
    },
    {
        "name": "Digital House", "code": "ozon", "seller": "Digital House",
        "url": "https://www.ozon.ru/seller/ridial/",
        "action": "ozon_catalog_collect", "attributes": {"Объём памяти": "256 GB", "Диагональ": "15.6"},
    },
    {
        "name": "Home Studio", "code": "halyk_market", "seller": "Home Studio",
        "url": "https://halykmarket.kz/merchant/24955?f=merchantName%3AMechta.kz",
        "action": "halyk_catalog_collect", "attributes": {"Материал": "Дерево", "Комнат": "3"},
    },
    {
        "name": "Fashion Point", "code": "forte_market", "seller": "Fashion Point",
        "url": "https://market.forte.kz/items/noutbuk-asus-rog-strix-g15-g513ie-hn004-90nr0582-m00050-156-eclipse-gray-602890",
        "action": "forte_catalog_collect", "attributes": {"Размер одежды": "M", "Состав": "Хлопок"},
    },
    {
        "name": "Beauty Lab", "code": "ozon_kz", "seller": "Beauty Lab",
        "url": "https://ozon.kz/seller/ridial/",
        "action": "ozon_kz_full_sync", "attributes": {"Тип кожи": "Сухая", "Оттенок": "Natural"},
    },
)


def run_scenario(
    db_path: Path,
    *,
    products_per_company: int = 100,
    read_rounds: int = 25,
) -> dict[str, Any]:
    db_path = Path(db_path)
    ensure_database(db_path)
    auth = AuthService(db_path)
    platform_admin, _ = auth.create_initial_admin(
        "platform@example.com", "Platform Admin", "StrongPassword123!"
    )
    saas = SaaSService(db_path)
    catalog = CatalogConfigurationService(db_path)
    tenant_ids: list[int] = []
    users: list[dict[str, Any]] = []
    schedules: list[dict[str, Any]] = []
    started = time.perf_counter()

    for index, scenario in enumerate(SCENARIOS, 1):
        registration = saas.submit_registration_request({
            "company_name": scenario["name"],
            "registration_number": f"BIN-LOAD-{index}",
            "contact_name": f"Company {index} Admin",
            "email": f"company-{index}@example.com",
            "phone": f"+7 700 000 0{index:03d}",
            "marketplaces": [scenario["code"]],
            "privacy_consent": True,
            "terms_consent": True,
            "launch_mode": "self_service",
            "template_code": "general",
        })
        request_id = int(registration["request_id"])
        provision = saas.provision_tenant_from_request(request_id, None, "pending")
        tenant_id = int(provision["tenant_id"])
        tenant_ids.append(tenant_id)
        saas.review_registration_v2(
            request_id, "approved", int(platform_admin["id"])
        )
        saas.set_marketplace_access(
            tenant_id, [scenario["code"]], int(platform_admin["id"])
        )
        activate_trial_subscription(
            db_path, tenant_id, int(platform_admin["id"])
        )
        granted = saas.marketplace_access(tenant_id, include_unavailable=False)
        effective_grants = [
            item["code"] for item in granted if item["is_allowed"]
        ]
        if effective_grants != [scenario["code"]]:
            raise AssertionError(f"Requested grant mismatch for company {index}: {granted}")
        user, _ = auth.create_user(
            f"admin-{index}@example.com", f"Company {index} Admin",
            f"StrongPassword{index}23!", "admin", int(platform_admin["id"]),
            tenant_id=tenant_id,
        )
        detected = saas.detect_marketplace_url(
            tenant_id, scenario["url"], scenario["code"]
        )
        if not detected["verified"] and detected.get("verification_state") != "parsed":
            raise AssertionError(f"Seller was not detected for {scenario['code']}")
        if scenario["code"] in {"ozon", "ozon_kz"}:
            with patch("saas_service.verify_ozon_storefront", return_value={
                "canonical_seller_id": str(detected["seller_identifier"]),
                "canonical_seller_url": str(detected["seller_url"]),
                "seller_name": str(detected["seller_name"]),
                "catalogue_empty": "false",
            }):
                checked = saas.check_marketplace_source(
                    tenant_id, scenario["url"], int(user["id"]), scenario["code"]
                )
                connected = saas.connect_marketplace(
                    tenant_id,
                    scenario["url"],
                    int(user["id"]),
                    scenario["code"],
                    str(checked["verification_proof"]),
                )
        else:
            connected = saas.connect_marketplace(
                tenant_id, scenario["url"], int(user["id"]), scenario["code"]
            )
        if connected.get("approval_status") != "approved":
            raise AssertionError(f"Marketplace was not approved for {scenario['code']}")
        user = auth.get_user(int(user["id"])) or user
        users.append(user)

        products = [
            {
                "product_id": f"C{index}-{number:05d}",
                "title": f"{scenario['name']} product {number}",
                "price": 1000 + index * 100 + number,
                "currency": "KZT",
                "attributes": scenario["attributes"],
            }
            for number in range(products_per_company)
        ]
        saved = catalog.upsert_catalog_products(tenant_id, scenario["code"], products)
        if saved != products_per_company:
            raise AssertionError(f"Unexpected product count for company {index}: {saved}")
        primary_source = next(iter(scenario["attributes"]))
        filter_key = normalized_attribute_key(primary_source)
        catalog.update_filters(
            tenant_id, [{"attribute_key": filter_key, "is_enabled": True}], int(user["id"])
        )
        schedule = saas.create_schedule(
            tenant_id,
            {
                "name": f"Company {index} operation",
                "action": scenario["action"],
                "recurrence_type": "interval",
                "interval_minutes": 360,
                "is_enabled": True,
            },
            int(user["id"]),
        )
        run_id = saas.begin_schedule_run(schedule)
        saas.attach_task_to_run(run_id, f"load-task-{index}")
        saas.finish_schedule_run(run_id, int(schedule["id"]), "completed", "Load-test completed")
        schedules.append(schedule)

    write_seconds = time.perf_counter() - started

    def read_company(item: tuple[int, dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
        tenant_id, scenario, user = item
        local_catalog = CatalogConfigurationService(db_path)
        local_data = DataService(db_path, scenario["seller"])
        primary_source, primary_value = next(iter(scenario["attributes"].items()))
        filter_key = normalized_attribute_key(primary_source)
        matched = 0
        rows = 0
        for _ in range(read_rounds):
            configuration = local_catalog.filter_configuration(tenant_id, {scenario["code"]})
            if filter_key not in {entry["attribute_key"] for entry in configuration["attributes"]}:
                raise AssertionError(f"Missing dynamic attribute for tenant {tenant_id}")
            result = local_catalog.matching_product_codes(
                tenant_id, {scenario["code"]}, {filter_key: [primary_value]}
            ) or set()
            matched += len(result)
            own_rows = local_data.rows_for_user(int(user["id"]))
            if len(own_rows) != products_per_company:
                raise AssertionError(
                    f"Tenant isolation failed for {tenant_id}: {len(own_rows)} rows"
                )
            rows += len(own_rows)
        return {"tenant_id": tenant_id, "matched": matched, "rows": rows}

    read_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=5) as pool:
        read_results = list(pool.map(read_company, zip(tenant_ids, SCENARIOS, users)))
    read_seconds = time.perf_counter() - read_started

    conn = sqlite3.connect(db_path)
    try:
        tenant_product_counts = {
            int(row[0]): int(row[1])
            for row in conn.execute(
                """SELECT tenant_id,COUNT(*) FROM tenant_catalog_products
                   GROUP BY tenant_id"""
            ).fetchall()
        }
        run_tenants = {
            int(row[0]) for row in conn.execute(
                "SELECT DISTINCT tenant_id FROM schedule_runs WHERE status='completed'"
            ).fetchall()
        }
    finally:
        conn.close()
    expected = set(tenant_ids)
    if set(tenant_product_counts) != expected or any(
        count != products_per_company for count in tenant_product_counts.values()
    ):
        raise AssertionError("Product rows leaked or were lost between companies")
    if run_tenants != expected:
        raise AssertionError("Operation history is not isolated for all five companies")

    read_queries = len(SCENARIOS) * read_rounds * 3
    total_products = len(SCENARIOS) * products_per_company
    return {
        "companies": len(SCENARIOS),
        "marketplaces": [scenario["code"] for scenario in SCENARIOS],
        "products_total": total_products,
        "products_per_company": products_per_company,
        "approved_connections": len(SCENARIOS),
        "completed_operations": len(run_tenants),
        "write_seconds": round(write_seconds, 4),
        "write_products_per_second": round(total_products / max(write_seconds, 0.000001), 2),
        "parallel_read_seconds": round(read_seconds, 4),
        "read_query_groups": read_queries,
        "read_query_groups_per_second": round(read_queries / max(read_seconds, 0.000001), 2),
        "read_results": read_results,
        "isolation": "passed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Five-company tenant isolation/load scenario")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--products", type=int, default=100)
    parser.add_argument("--rounds", type=int, default=25)
    args = parser.parse_args()
    if args.db:
        result = run_scenario(args.db, products_per_company=args.products, read_rounds=args.rounds)
    else:
        with tempfile.TemporaryDirectory(prefix="five_company_load_") as folder:
            result = run_scenario(
                Path(folder) / "load.db",
                products_per_company=args.products,
                read_rounds=args.rounds,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
