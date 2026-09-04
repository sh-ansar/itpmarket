#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import traceback
import uuid
from dataclasses import replace
from urllib.parse import quote_plus, urlparse
from datetime import datetime
from pathlib import Path
from typing import Any
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collector_config import ROOT, Settings, load_settings, seller_root_url
from ozon_probe_core import parse_product_json
from ozon_validation_core import normalize_for_import, seller_match_status
from registry import Registry, now_iso
from reporting import generate_dashboard


from collectors.ozon.market_matching import build_search_queries, evaluate_match
from catalog_configuration_service import CatalogConfigurationService
from storage.postgres_compat import configure_connection, connect_database


def run_id_for(mode: str) -> str:
    safe = mode.replace("-", "_")
    return (
        datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        + "_" + safe + "_" + uuid.uuid4().hex[:8]
    )


def sleep_range(pair: tuple[float, float], multiplier: float = 1.0) -> None:
    delay = random.uniform(pair[0], pair[1]) * multiplier
    time.sleep(delay)


def normalize_marketplace_item(
    item: dict[str, Any], collected_at: str, run_id: str, start_url: str
) -> dict[str, Any]:
    value = dict(item)
    host = str(urlparse(str(start_url or "")).hostname or "").casefold()
    if host in {"ozon.kz", "www.ozon.kz"}:
        value["source"] = "ozon_kz"
        value["currency"] = "KZT"
    return normalize_for_import(value, collected_at, run_id)


def portable_storage_path(path: Path) -> str:
    """Keep raw artefact paths portable across the RU and KZ collectors."""
    resolved = path.resolve()
    for root in (ROOT.resolve(), PROJECT_ROOT.resolve()):
        try:
            return str(resolved.relative_to(root))
        except ValueError:
            continue
    return str(resolved)


STATUS_PRIORITY = {
    "PASSED": 0,
    "PARTIAL": 1,
    "BLOCKED": 2,
    "FAILED": 3,
    "INTERRUPTED": 4,
}

CATALOG_SHRINK_GUARD_MIN_BASELINE_ARTICLES = 100
CATALOG_SHRINK_GUARD_MAX_RETAINED_RATIO = 0.70
CATALOG_SHRINK_GUARD_MIN_OVERLAP_RATIO = 0.90


def combined_status(*results: dict[str, Any]) -> str:
    statuses = [
        str(result.get("status") or "PASSED").upper()
        for result in results
        if isinstance(result, dict)
    ]
    return max(statuses or ["PASSED"], key=lambda value: STATUS_PRIORITY.get(value, 3))


def result_exit_code(result: dict[str, Any] | None) -> int:
    if not isinstance(result, dict):
        return 0
    status = str(result.get("status") or "PASSED").upper()
    # A partial run can preserve successful cards, but the process must still
    # be non-zero so the task manager never reports it as a full success.
    return 0 if status in {"OK", "PASSED", "READY"} else 2


def has_hard_failure(result: dict[str, Any] | None) -> bool:
    """Whether a stage cannot safely continue to the next one."""
    return str((result or {}).get("status") or "PASSED").upper() in {
        "PARTIAL", "BLOCKED", "FAILED", "INTERRUPTED",
    }


def own_offer_availability(item: dict[str, Any]) -> str:
    """Normalize the seller's current PDP state for the RU own catalog.

    The product API has an explicit out-of-stock widget.  For a successful
    regular PDP response it exposes a current price but no separate
    availability widget, which previously left a fresh own offer as UNKNOWN.
    """
    availability = str(item.get("availability_status") or "UNKNOWN").upper()
    if availability != "UNKNOWN":
        return availability
    return "AVAILABLE" if item.get("success") else "UNKNOWN"


def structured_result(result: dict[str, Any] | None, error: Exception | None = None) -> dict[str, Any]:
    status = str((result or {}).get("status") or "").upper()
    text = str(error or "")
    if status in {"OK", "PASSED", "READY"} and error is None:
        reason = "success"
    elif status == "PARTIAL" and error is None:
        reason = "partial_success"
    elif "BLOCKED" in status:
        reason = "ozon_challenge"
    elif "фоновой сессии" in text:
        reason = "browser_hidden_session"
    elif "не открыт" in text:
        reason = "browser_not_open"
    elif "ChromeDriver" in text:
        reason = "chromedriver_unavailable"
    elif "debug" in text.casefold() or "127.0.0.1" in text:
        reason = "browser_debug_unavailable"
    else:
        reason = "collector_failed"
    return {"ok": result_exit_code(result) == 0 and error is None, "reason": reason}


class Collector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.registry = Registry(settings.database_path)
        self.browser: Any | None = None
        settings.runs_dir.mkdir(parents=True, exist_ok=True)
        settings.reports_dir.mkdir(parents=True, exist_ok=True)
        settings.exports_dir.mkdir(parents=True, exist_ok=True)
        settings.raw_dir.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        if self.browser:
            self.browser.close()
        self.registry.close()

    def ensure_browser(self) -> Any:
        if self.browser is None:
            from browser_session import BrowserSession
            self.browser = BrowserSession(
                self.settings.debug_port,
                self.settings.start_url,
                self.settings.browser_profile_path,
            ).connect()
            print(f"Рабочая вкладка: {self.browser.original_url}")
        return self.browser

    def open_browser(self) -> dict[str, Any]:
        browser = self.ensure_browser()
        print("Ozon.ru browser is ready.")
        print(f"Working tab: {browser.original_url}")
        print("Choose a Russian city or pickup point in this Chrome profile if Ozon.ru asks for it.")
        return {
            "status": "READY",
            "debug_port": self.settings.debug_port,
            "url": browser.original_url,
        }

    def _run_dir(self, run_id: str) -> Path:
        path = self.settings.runs_dir / run_id
        path.mkdir(parents=True, exist_ok=True)
        (self.settings.runs_dir.parent / "latest_run.txt").write_text(
            str(path), encoding="utf-8"
        )
        return path

    def _write_trace(self, run_dir: Path, payload: dict[str, Any]) -> None:
        with (run_dir / "traces.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _save_raw_json(
        self,
        article: str,
        data: dict[str, Any],
        product_before: dict[str, Any],
        run_id: str,
    ) -> str:
        policy = self.settings.raw_json_policy
        should_save = policy == "all" or (
            policy == "first_success_and_errors" and not str(product_before.get("raw_json_path") or "")
        )
        if not should_save:
            return ""
        folder = self.settings.raw_dir / article
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{run_id}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return portable_storage_path(path)

    def discover(self, product_limit: int | None = None, max_pages: int | None = None) -> dict[str, Any]:
        mode = "discover"
        run_id = run_id_for(mode)
        run_dir = self._run_dir(run_id)
        source_urls = list(self.settings.start_urls or (self.settings.start_url,))
        source_boundary = "\n".join(source_urls)
        self.registry.begin_run(run_id, mode, source_boundary)
        started = time.monotonic()
        metrics = {
            "sources_total": len(source_urls),
            "sources_completed": 0,
            "pages_loaded": 0,
            "items_total": 0,
            "items_success": 0,
            "items_failed": 0,
            "items_blocked": 0,
        }
        status = "PASSED"
        seen: set[str] = set()
        new_count = 0
        changed_count = 0
        limit = self.settings.catalog_product_limit if product_limit is None else max(0, product_limit)
        pages = self.settings.catalog_max_pages if max_pages is None else max(1, max_pages)
        browser = self.ensure_browser()
        source_summaries: list[dict[str, Any]] = []
        stop_all = False
        result_metadata: dict[str, Any] = {}
        try:
            print("=" * 78)
            print("OZON COLLECTOR 3.1 — SCROLL-STABLE MULTI-SOURCE DISCOVERY")
            print("Сначала каталог продавца, затем дополнительные категории.")
            print("Article, canonical URL и каталожная цена сохраняются в SQLite.")
            print("=" * 78)
            print(f"Источников: {len(source_urls)}")
            for source_index, source_url in enumerate(source_urls, start=1):
                if stop_all:
                    break
                current_url = source_url
                source_seen_before = len(seen)
                source_pages = 0
                source_status = "PASSED"
                print(f"\n[ИСТОЧНИК {source_index}/{len(source_urls)}] {source_url}")
                for page_no in range(1, pages + 1):
                    print(f"\n[СТРАНИЦА {page_no}/{pages}] {current_url}")
                    response = browser.load_catalog(
                        current_url,
                        self.settings.catalog_wait_seconds,
                        self.settings.page_reloads,
                        navigate=True,
                    )
                    self._write_trace(run_dir, {
                        "stage": "catalog", "source_index": source_index,
                        "source_url": source_url, "page": page_no, "url": current_url,
                        "status": response.get("status"), "elapsed_ms": response.get("elapsed_ms"),
                        "events": response.get("events"),
                    })
                    if not response.get("ok"):
                        blocked = str(response.get("status")).startswith("BLOCKED")
                        metrics["items_blocked" if blocked else "items_failed"] += 1
                        if blocked:
                            source_status = "BLOCKED"
                            status = "BLOCKED"
                            stop_all = True
                        else:
                            source_status = "PARTIAL"
                            if status != "BLOCKED":
                                status = "PARTIAL"
                        print(f"Источник пропущен: {response.get('status')}")
                        break
                    metrics["pages_loaded"] += 1
                    source_pages += 1
                    page_new = 0
                    for item in response.get("products") or []:
                        article = str(item.get("article") or "")
                        if not article or article in seen:
                            continue
                        seen.add(article)
                        is_new, changed = self.registry.upsert_catalog_product(
                            item, source_url, run_id, page_no, now_iso()
                        )
                        new_count += int(is_new)
                        changed_count += int(changed)
                        page_new += 1
                        metrics["items_success"] += 1
                        if limit and len(seen) >= limit:
                            stop_all = True
                            break
                    print(f"Найдено новых на странице: {page_new}; уникальных всего: {len(seen)}")
                    if stop_all:
                        # A diagnostic cap is not proof that the seller
                        # catalogue was collected to its real end.
                        if limit:
                            source_status = "PARTIAL"
                            if status != "BLOCKED":
                                status = "PARTIAL"
                        break
                    next_page = str(response.get("next_page") or "")
                    if not next_page:
                        print("Следующей страницы у этого источника нет.")
                        break
                    current_url = next_page
                    sleep_range(self.settings.page_delay_seconds)
                if source_status == "PASSED":
                    metrics["sources_completed"] += 1
                source_summaries.append({
                    "index": source_index,
                    "url": source_url,
                    "status": source_status,
                    "pages_loaded": source_pages,
                    "unique_products_added": len(seen) - source_seen_before,
                })
            metrics["items_total"] = len(seen)
            # A source that never produced a seller catalogue is not a
            # recoverable per-card partial result.  Continuing would refresh
            # an empty queue and make the job look successful in the UI.
            if not seen and metrics["sources_completed"] == 0 and status == "PARTIAL":
                status = "FAILED"
            diagnostic_limit_used = product_limit is not None or bool(limit)
            if status == "PASSED" and not diagnostic_limit_used:
                published_run_id = self.registry.current_published_catalog_run_id()
                baseline_run_id = published_run_id
                baseline_source = "published" if published_run_id else ""
                if not baseline_run_id:
                    baseline_run_id = (
                        self.registry.strongest_previous_passed_discovery_run_id(
                            source_boundary,
                            run_id,
                        )
                    )
                    if baseline_run_id:
                        baseline_source = "previous_passed_discovery"
                baseline_articles = (
                    self.registry.catalog_articles(baseline_run_id)
                    if baseline_run_id else set()
                )
                previous_count = len(baseline_articles)
                discovered_count = len(seen)
                if previous_count >= CATALOG_SHRINK_GUARD_MIN_BASELINE_ARTICLES and discovered_count:
                    retained_ratio = discovered_count / previous_count
                    overlap_ratio = len(seen & baseline_articles) / discovered_count
                    if (
                        retained_ratio < CATALOG_SHRINK_GUARD_MAX_RETAINED_RATIO
                        and overlap_ratio >= CATALOG_SHRINK_GUARD_MIN_OVERLAP_RATIO
                    ):
                        status = "PARTIAL"
                        guard_details = {
                            "reason": "CATALOG_SHRINK_GUARD",
                            "previous_run_id": baseline_run_id,
                            "previous_count": previous_count,
                            "baseline_source": baseline_source,
                            "baseline_run_id": baseline_run_id,
                            "baseline_count": previous_count,
                            "discovered_count": discovered_count,
                            "retained_ratio": round(retained_ratio, 6),
                            "overlap_ratio": round(overlap_ratio, 6),
                        }
                        result_metadata = {
                            "reason": "CATALOG_SHRINK_GUARD",
                            "catalog_shrink_guard": guard_details,
                        }
                        metrics["notes"] = "CATALOG_SHRINK_GUARD"
            summary = {
                "run_id": run_id,
                "mode": mode,
                "status": status,
                "start_url": self.settings.start_url,
                "start_urls": source_urls,
                "sources": source_summaries,
                "products_seen": len(seen),
                "new_products": new_count,
                "catalog_price_changed": changed_count,
                **metrics,
                **result_metadata,
            }
            (run_dir / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except KeyboardInterrupt:
            status = "INTERRUPTED"
            raise
        except Exception:
            status = "FAILED"
            (run_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            raise
        finally:
            metrics["duration_seconds"] = round(time.monotonic() - started, 2)
            self.registry.finish_run(run_id, status, metrics)
            self.generate_outputs()
        print(
            f"\nDiscovery завершён: источников {metrics['sources_completed']}/{metrics['sources_total']}; "
            f"товаров {len(seen)}; новых {new_count}; изменили цену {changed_count}"
        )
        return {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "status": status,
            **metrics,
            **result_metadata,
        }

    @staticmethod
    def task_type_for_mode(mode: str) -> str:
        return {
            "enrich-new": "ENRICH",
            "refresh-prices": "REFRESH_PRICE",
            "refresh-stale": "REFRESH_DETAILS",
            "retry-failed": "RETRY",
            "stress-test": "STRESS",
        }[mode]

    def process(
        self,
        mode: str,
        limit: int | None = None,
        articles: set[str] | None = None,
        *,
        catalog_run_id: str = "",
    ) -> dict[str, Any]:
        run_id = run_id_for(mode)
        run_dir = self._run_dir(run_id)
        self.registry.begin_run(run_id, mode, self.settings.start_url)
        started = time.monotonic()
        batch_limit = self.settings.batch_limit if limit is None else max(0, limit)
        source_articles = self.registry.articles_for_sources(
            self.settings.start_urls or (self.settings.start_url,),
            catalog_run_id=catalog_run_id,
        )
        allowed_articles = (
            source_articles
            if articles is None
            else {str(article) for article in articles} & source_articles
        )
        selected_articles = self.registry.select_articles(
            mode,
            batch_limit,
            stale_days=self.settings.stale_days,
            max_attempts=self.settings.max_task_attempts,
            allowed_articles=allowed_articles,
        )
        metrics = {
            "pages_loaded": 0,
            "items_total": len(selected_articles),
            "items_success": 0,
            "items_failed": 0,
            "items_blocked": 0,
        }
        status = "PASSED"
        task_type = self.task_type_for_mode(mode)
        consecutive_blocked = 0
        browser = self.ensure_browser()
        print("=" * 78)
        print(f"OZON COLLECTOR 3.0 — {mode.upper()}")
        print(f"В очереди: {len(selected_articles)}")
        print("Checkpoint сохраняется в SQLite после каждой карточки.")
        print("=" * 78)
        try:
            for index, article in enumerate(selected_articles, start=1):
                self.registry.claim_task(article, task_type)
                before = self.registry.get_product(article)
                print(f"\n[ТОВАР {index}/{len(selected_articles)}] {article} — {before.get('title','')}")
                response = browser.load_product_api(
                    article,
                    self.settings.request_wait_seconds,
                    self.settings.product_reloads,
                )
                trace = {
                    "stage": "product", "index": index, "article": article,
                    "status": response.get("status"), "elapsed_ms": response.get("elapsed_ms"),
                    "events": response.get("events"),
                }
                self._write_trace(run_dir, trace)
                if response.get("ok") and isinstance(response.get("json"), dict):
                    item = parse_product_json(
                        article,
                        response["json"],
                        self.registry.catalog_product_for_parser(article),
                    )
                    item["detail_success"] = bool(item.get("success"))
                    item["detail_status"] = "API_OK" if item.get("success") else "API_INCOMPLETE"
                    item["overall_status"] = "COMPLETE" if item.get("success") else "INCOMPLETE"
                    item["seller_match_status"] = seller_match_status(
                        item, self.settings.expected_seller
                    )
                    item["availability_status"] = own_offer_availability(item)
                    normalized = normalize_marketplace_item(
                        item, now_iso(), run_id, self.settings.start_url
                    )
                    raw_path = self._save_raw_json(article, response["json"], before, run_id)
                    if item.get("success"):
                        self.registry.update_from_detail(
                            item, normalized, run_id, now_iso(), raw_path
                        )
                        self.registry.complete_task(article, task_type)
                        metrics["items_success"] += 1
                        consecutive_blocked = 0
                        print(
                            f"COMPLETE | {item.get('seller_name')} | "
                            f"{int(item.get('card_price') or item.get('regular_price') or 0)} "
                            f"{normalized.get('currency')} | "
                            f"{normalized.get('tire_size')} | identity {normalized.get('identity_completeness_percent')}%"
                        )
                    else:
                        self.registry.fail_task(
                            article, task_type, "FAILED", str(item.get("error") or "API_INCOMPLETE"), 60
                        )
                        metrics["items_failed"] += 1
                        status = "PARTIAL"
                else:
                    response_status = str(response.get("status") or "NO_JSON")
                    blocked = response_status.startswith("BLOCKED")
                    queue_status = "BLOCKED" if blocked else "FAILED"
                    backoff = 120 if blocked else 60
                    self.registry.fail_task(article, task_type, queue_status, response_status, backoff)
                    metrics["items_blocked" if blocked else "items_failed"] += 1
                    status = "PARTIAL"
                    consecutive_blocked = consecutive_blocked + 1 if blocked else 0
                    print(f"{response_status}; отложено в очередь повторов.")
                    if consecutive_blocked >= self.settings.blocked_stop_after:
                        print("Circuit breaker: слишком много блокировок подряд. Проход остановлен.")
                        status = "BLOCKED"
                        break
                    if consecutive_blocked == self.settings.blocked_pause_after:
                        print(
                            f"Circuit breaker: пауза {self.settings.blocked_pause_seconds} сек."
                        )
                        time.sleep(self.settings.blocked_pause_seconds)
                if self.settings.technical_pause_every and index % self.settings.technical_pause_every == 0:
                    print("Техническая пауза между блоками товаров...")
                    sleep_range(self.settings.technical_pause_seconds)
                else:
                    elapsed = float(response.get("elapsed_ms") or 0) / 1000
                    multiplier = 1.5 if elapsed > 15 else 1.0
                    sleep_range(self.settings.product_delay_seconds, multiplier)
        except KeyboardInterrupt:
            status = "INTERRUPTED"
            print("\nОстановлено пользователем. Уже обработанные карточки сохранены.")
        except Exception:
            status = "FAILED"
            (run_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            raise
        finally:
            metrics["duration_seconds"] = round(time.monotonic() - started, 2)
            self.registry.finish_run(run_id, status, metrics)
            summary = {"run_id": run_id, "mode": mode, "status": status, **metrics}
            (run_dir / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.generate_outputs()
        print(
            f"\nГотово: {metrics['items_success']}/{metrics['items_total']}; "
            f"ошибок {metrics['items_failed']}; блокировок {metrics['items_blocked']}; "
            f"время {metrics['duration_seconds']} сек."
        )
        return {"run_id": run_id, "run_dir": str(run_dir), "status": status, **metrics}


    def _enrich_market_candidate(
        self,
        article: str,
        run_id: str,
        run_dir: Path,
        *,
        force: bool = False,
    ) -> bool:
        before = self.registry.get_product(article)
        if (
            not force
            and before.get("detail_status") == "COMPLETE"
            and before.get("last_detail_at")
        ):
            return True
        response = self.ensure_browser().load_product_api(
            article, self.settings.request_wait_seconds, self.settings.product_reloads
        )
        self._write_trace(run_dir, {
            "stage": "market_candidate_detail", "article": article,
            "forced_refresh": bool(force),
            "status": response.get("status"), "elapsed_ms": response.get("elapsed_ms"),
            "events": response.get("events"),
        })
        if not response.get("ok") or not isinstance(response.get("json"), dict):
            return False
        item = parse_product_json(
            article, response["json"], self.registry.catalog_product_for_parser(article)
        )
        item["detail_success"] = bool(item.get("success"))
        item["detail_status"] = "API_OK" if item.get("success") else "API_INCOMPLETE"
        item["overall_status"] = "COMPLETE" if item.get("success") else "INCOMPLETE"
        item["seller_match_status"] = seller_match_status(item, self.settings.expected_seller)
        if not item.get("success"):
            return False
        normalized = normalize_marketplace_item(
            item, now_iso(), run_id, self.settings.start_url
        )
        raw_path = self._save_raw_json(article, response["json"], before, run_id)
        self.registry.update_from_detail(item, normalized, run_id, now_iso(), raw_path)
        return True

    def _market_snapshot_counts(
        self,
        run_id: str,
        client_article: str,
    ) -> tuple[int, int, int]:
        rows = self.registry.conn.execute(
            """
            SELECT match_level,COUNT(*) c
            FROM market_analysis_candidates
            WHERE market_run_id=?
              AND client_article=?
              AND match_level IN ('EXACT','STRONG','COMPARABLE')
            GROUP BY match_level
            """,
            (str(run_id), str(client_article)),
        ).fetchall()
        counts = {str(row[0]): int(row[1]) for row in rows}
        exact = counts.get("EXACT", 0) + counts.get("STRONG", 0)
        comparable = counts.get("COMPARABLE", 0)
        return exact + comparable, exact, comparable

    def _is_expected_market_seller(self, offer: dict[str, Any]) -> bool:
        identifiers = marketplace_seller_identifiers(self.settings)
        seller_id = str(offer.get("seller_id") or "").strip().casefold()
        seller_name = str(offer.get("seller_name") or "").strip().casefold()
        seller_url = str(offer.get("seller_url") or "").strip().casefold()
        return any(
            identifier == seller_id
            or identifier == seller_name
            or (identifier and identifier in seller_url)
            for identifier in identifiers
        )

    def _collect_same_product_offers(
        self,
        owner: dict[str, Any],
        run_id: str,
        run_dir: Path,
        all_catalog_articles: set[str],
    ) -> dict[str, Any]:
        owner_article = str(owner.get("article") or "").strip()
        product_response = self.ensure_browser().load_product_api(
            owner_article,
            int(getattr(self.settings, "request_wait_seconds", 1)),
            int(getattr(self.settings, "product_reloads", 0)),
        )
        if not isinstance(product_response, dict):
            return {
                "available": False,
                "failed": False,
                "request_made": False,
                "seller_list_found": False,
                "offers": 0,
                "skipped_own": 0,
                "deduplicated": 0,
                "error": "",
            }
        self._write_trace(
            run_dir,
            {
                "stage": "same_product_probe",
                "client_article": owner_article,
                "status": product_response.get("status"),
                "elapsed_ms": product_response.get("elapsed_ms"),
                "events": product_response.get("events"),
            },
        )
        if not product_response.get("ok") or not isinstance(
            product_response.get("json"), dict
        ):
            return {
                "available": False,
                "failed": True,
                "request_made": False,
                "seller_list_found": False,
                "offers": 0,
                "skipped_own": 0,
                "deduplicated": 0,
                "error": str(product_response.get("status") or "NO_JSON"),
            }

        modal_response = self.ensure_browser().load_other_seller_offers(
            owner_article,
            product_response["json"],
            int(getattr(self.settings, "request_wait_seconds", 1)),
            int(getattr(self.settings, "product_reloads", 0)),
        )
        if not isinstance(modal_response, dict):
            return {
                "available": False,
                "failed": True,
                "request_made": True,
                "seller_list_found": False,
                "offers": 0,
                "skipped_own": 0,
                "deduplicated": 0,
                "error": "INVALID_MODAL_RESPONSE",
            }
        self._write_trace(
            run_dir,
            {
                "stage": "same_product_modal",
                "client_article": owner_article,
                "modal_link": modal_response.get("modal_link"),
                "request_made": bool(modal_response.get("request_made")),
                "seller_list_found": bool(modal_response.get("seller_list_found")),
                "seller_count": int(modal_response.get("seller_count") or 0),
                "status": modal_response.get("status"),
                "elapsed_ms": modal_response.get("elapsed_ms"),
                "events": modal_response.get("events"),
            },
        )
        if not modal_response.get("ok"):
            return {
                "available": False,
                "failed": True,
                "request_made": bool(modal_response.get("request_made")),
                "seller_list_found": False,
                "offers": 0,
                "skipped_own": 0,
                "deduplicated": 0,
                "error": str(modal_response.get("status") or "NO_JSON"),
            }

        skipped_own = 0
        deduplicated = 0
        seen: set[tuple[str, str, str]] = set()
        valid_offers: list[dict[str, Any]] = []
        for offer in modal_response.get("offers") or []:
            candidate_article = str(offer.get("candidate_article") or "").strip()
            current_price = int(offer.get("card_price") or 0)
            if not candidate_article or current_price <= 0:
                continue
            if (
                candidate_article in all_catalog_articles
                or self._is_expected_market_seller(offer)
            ):
                skipped_own += 1
                continue
            dedupe_key = (
                owner_article,
                candidate_article,
                str(offer.get("seller_id") or "").strip(),
            )
            if dedupe_key in seen:
                deduplicated += 1
                continue
            seen.add(dedupe_key)
            valid_offers.append(offer)

        modal_link = str(modal_response.get("modal_link") or "")
        modal_url = str(modal_response.get("url") or modal_link)
        keep_keys: set[tuple[str, str]] = set()
        same_product_match = {
            "accepted": True,
            "level": "EXACT",
            "score": 100,
            "method": "OZON_SAME_PRODUCT_GROUP",
            "reason": "Ozon otherOffersFromSellers for the client product",
            "reasons": ["ozon_other_offers_from_sellers"],
        }
        for rank, offer in enumerate(valid_offers, start=1):
            candidate_article = str(offer["candidate_article"])
            candidate_url = str(offer.get("product_url") or owner.get("canonical_url") or "")
            candidate = {
                "article": candidate_article,
                "name": str(owner.get("title") or ""),
                "url": candidate_url,
                "catalog_card_price": int(offer.get("card_price") or 0),
            }
            self.registry.upsert_catalog_product(
                candidate,
                modal_url,
                run_id,
                0,
                now_iso(),
                queue_detail=False,
            )
            self.registry.save_market_candidate(
                owner_article,
                candidate_article,
                "Ozon otherOffersFromSellers",
                modal_url,
                rank,
                same_product_match,
                run_id,
                candidate=candidate,
                offer=offer,
                replace_analysis_candidate=False,
            )
            seller_key = str(
                offer.get("seller_id") or offer.get("seller_name") or ""
            ).strip()
            keep_keys.add((candidate_article, seller_key))

        seller_list_found = bool(modal_response.get("seller_list_found"))
        stale_removed = 0
        if seller_list_found:
            stale_removed = self.registry.reconcile_same_product_candidates(
                run_id,
                owner_article,
                keep_keys,
            )
        return {
            "available": bool(valid_offers),
            "failed": False,
            "request_made": bool(modal_response.get("request_made")),
            "seller_list_found": seller_list_found,
            "offers": len(valid_offers),
            "articles": [
                str(offer.get("candidate_article") or "")
                for offer in valid_offers
            ],
            "skipped_own": skipped_own,
            "deduplicated": deduplicated,
            "stale_removed": stale_removed,
            "modal_link": modal_link,
            "modal_url": modal_url,
            "error": "",
        }

    def market_search(
        self, limit: int | None = None, articles: set[str] | None = None,
        *, catalog_run_id: str = "", catalog_articles: set[str] | None = None,
    ) -> dict[str, Any]:
        mode = "market-search"
        run_id = run_id_for(mode)
        run_dir = self._run_dir(run_id)
        # market_search_batch_limit is a transport/concurrency batch size, not
        # a total full-sync cap.  An explicit CLI limit remains a total cap.
        batch_size = self.settings.market_search_batch_limit
        total_limit = max(1, int(limit)) if limit is not None and int(limit) > 0 else 0
        catalog_run_id = str(
            catalog_run_id or self.registry.current_published_catalog_run_id()
        ).strip()
        all_catalog_articles = self.registry.catalog_articles(catalog_run_id)
        current_catalog_articles = (
            set(catalog_articles)
            if catalog_articles is not None
            else set(all_catalog_articles)
        )
        all_catalog_articles |= current_catalog_articles
        if articles is not None:
            current_catalog_articles &= {str(article) for article in articles}
        owners = self.registry.client_products_for_market_search(
            total_limit, allowed_articles=current_catalog_articles,
            catalog_run_id=catalog_run_id,
        )
        self.registry.begin_run(run_id, mode, self.settings.start_url)
        self.registry.begin_market_analysis(
            run_id, catalog_run_id, len(current_catalog_articles)
        )
        browser = self.ensure_browser()
        marketplace_host = str(
            urlparse(str(self.settings.start_url or "")).hostname or ""
        ).casefold()
        search_origin = (
            "https://ozon.kz"
            if marketplace_host in {"ozon.kz", "www.ozon.kz"}
            else "https://www.ozon.ru"
        )
        started = time.monotonic()
        metrics = {
            "pages_loaded": 0, "items_total": len(owners), "items_success": 0,
            "items_failed": 0, "items_blocked": 0, "candidates_found": 0,
            "exact_found": 0, "comparable_found": 0,
            "known_candidates": 0, "known_refreshed": 0,
            "known_refresh_failed": 0, "existing_candidates_seen": 0,
            "new_candidates": 0,
            "same_product_probe": 0, "same_product_requests": 0,
            "same_product_success": 0, "same_product_failed": 0,
            "same_product_available": 0, "same_product_unavailable": 0,
            "same_product_offers": 0, "same_product_skipped_own": 0,
            "same_product_deduplicated": 0,
            "same_product_search_skipped": 0,
            "same_product_fallback_search": 0,
            "same_product_stale_removed": 0,
        }
        status = "PASSED" if catalog_run_id and len(owners) == len(current_catalog_articles) else "PARTIAL"
        refreshed_candidate_articles: dict[str, bool] = {}
        same_product_articles: set[str] = set()
        print("=" * 78)
        print("OZON COLLECTOR 3.3.2 — ПОИСК РЫНОЧНЫХ ПРЕДЛОЖЕНИЙ")
        print("Основной уровень: бренд + модель + размер. Резервный: бренд + размер.")
        print("Совпадения разных брендов и размеров автоматически отклоняются.")
        print("=" * 78)
        print(f"Товаров клиента в очереди: {len(owners)}")
        try:
            for owner_index, owner in enumerate(owners, start=1):
                if (owner_index - 1) % batch_size == 0:
                    batch_number = ((owner_index - 1) // batch_size) + 1
                    remaining = len(owners) - owner_index + 1
                    print(
                        f"[MARKET BATCH {batch_number}] "
                        f"processed={owner_index - 1} remaining={remaining}"
                    )
                owner_article = str(owner.get("article") or "")
                self.registry.begin_market_search(owner_article, run_id)
                metrics["same_product_probe"] += 1
                same_product = self._collect_same_product_offers(
                    owner,
                    run_id,
                    run_dir,
                    all_catalog_articles,
                )
                metrics["same_product_requests"] += int(
                    bool(same_product.get("request_made"))
                )
                metrics["same_product_skipped_own"] += int(
                    same_product.get("skipped_own") or 0
                )
                metrics["same_product_deduplicated"] += int(
                    same_product.get("deduplicated") or 0
                )
                metrics["same_product_stale_removed"] += int(
                    same_product.get("stale_removed") or 0
                )
                if same_product.get("failed"):
                    metrics["same_product_failed"] += 1
                    if status == "PASSED":
                        status = "PARTIAL"
                elif same_product.get("request_made"):
                    metrics["same_product_success"] += 1

                if same_product.get("available"):
                    metrics["same_product_available"] += 1
                    metrics["same_product_offers"] += int(
                        same_product.get("offers") or 0
                    )
                    metrics["same_product_search_skipped"] += 1
                    same_product_articles.update(
                        str(article)
                        for article in same_product.get("articles") or []
                    )
                    total_candidates, exact_count, comparable_count = (
                        self._market_snapshot_counts(run_id, owner_article)
                    )
                    modal_link = str(same_product.get("modal_link") or "")
                    modal_url = str(same_product.get("modal_url") or modal_link)
                    self.registry.finish_market_search(
                        owner_article,
                        "Ozon otherOffersFromSellers",
                        modal_url,
                        "COMPLETED",
                        total_candidates,
                        exact_count,
                        comparable_count,
                        run_id,
                    )
                    self.registry.record_market_analysis_product(
                        run_id,
                        owner_article,
                        "COMPLETED",
                        "Ozon otherOffersFromSellers",
                        modal_url,
                        total_candidates,
                        exact_count,
                        comparable_count,
                    )
                    metrics["candidates_found"] += total_candidates
                    metrics["exact_found"] += exact_count
                    metrics["comparable_found"] += comparable_count
                    metrics["items_success"] += 1
                    print(
                        f"  SAME PRODUCT | offers={same_product.get('offers', 0)}; "
                        "catalog search skipped"
                    )
                    continue

                metrics["same_product_unavailable"] += int(
                    not same_product.get("failed")
                )
                metrics["same_product_fallback_search"] += 1
                known_rows = list(
                    self.registry.known_market_candidates(owner_article)
                )
                known_articles = {
                    str(row.get("candidate_article") or "")
                    for row in known_rows
                    if str(row.get("candidate_article") or "")
                }
                metrics["known_candidates"] += len(known_rows)
                owner_known_refreshed = 0

                for known in known_rows:
                    candidate_article = str(
                        known.get("candidate_article") or ""
                    )
                    if not candidate_article:
                        continue
                    if candidate_article in same_product_articles:
                        continue
                    if (
                        same_product.get("failed")
                        and str(known.get("match_method") or "")
                        == "OZON_SAME_PRODUCT_GROUP"
                    ):
                        # A transient product/modal failure must not turn the
                        # inherited Ozon-confirmed row into a PDP/search row.
                        continue

                    refreshed = refreshed_candidate_articles.get(candidate_article)
                    if refreshed is None:
                        refreshed = self._enrich_market_candidate(
                            candidate_article,
                            run_id,
                            run_dir,
                            force=True,
                        )
                        refreshed_candidate_articles[candidate_article] = bool(refreshed)

                    if not refreshed:
                        metrics["known_refresh_failed"] += 1
                        if status == "PASSED":
                            status = "PARTIAL"
                        continue

                    candidate = self.registry.get_product(candidate_article)
                    offer = self.registry.primary_offer(candidate_article)
                    match = evaluate_match(owner, candidate)
                    if self._is_expected_market_seller(offer):
                        match = {
                            "accepted": False,
                            "level": "REJECTED",
                            "score": 0,
                            "method": "OWN_SELLER",
                            "reason": "Собственный продавец",
                            "reasons": [],
                        }
                    self.registry.save_market_candidate(
                        owner_article,
                        candidate_article,
                        str(known.get("query_text") or ""),
                        str(known.get("query_url") or ""),
                        int(known.get("catalog_rank") or 0),
                        match,
                        run_id,
                        candidate=candidate,
                        offer=offer,
                    )
                    metrics["known_refreshed"] += 1
                    owner_known_refreshed += 1

                total_candidates, exact_count, comparable_count = (
                    self._market_snapshot_counts(run_id, owner_article)
                )
                queries = build_search_queries(owner)
                print(f"\n[ПОЗИЦИЯ {owner_index}/{len(owners)}] {owner_article} — {owner.get('title','')}")
                if not queries:
                    print("SKIP | недостаточно бренда или размера для поиска")
                    reason = "Нет безопасного identity для поиска"
                    item_status = (
                        "COMPLETED"
                        if exact_count or comparable_count
                        else "NO_SAFE_IDENTITY"
                    )
                    self.registry.finish_market_search(
                        owner_article, "", "", item_status, total_candidates,
                        exact_count, comparable_count, run_id, reason,
                    )
                    self.registry.record_market_analysis_product(
                        run_id, owner_article, item_status, "", "", total_candidates,
                        exact_count, comparable_count, reason,
                    )
                    metrics["candidates_found"] += total_candidates
                    metrics["exact_found"] += exact_count
                    metrics["comparable_found"] += comparable_count
                    metrics["items_success"] += 1
                    continue
                unique_candidates: dict[str, tuple[dict[str, Any], str, str, int]] = {}
                processed_candidate_articles: set[str] = set()
                last_query = last_url = ""
                search_error = ""
                for query_index, query in enumerate(queries, start=1):
                    # Manufacturer/article and model searches are primary. Brand+size is fallback.
                    if query_index > 1 and exact_count > 0:
                        break
                    search_url = f"{search_origin}/search/?text={quote_plus(query)}&from_global=true"
                    last_query, last_url = query, search_url
                    print(f"  Поиск {query_index}/{len(queries)}: {query}")
                    current_url = search_url
                    for page_no in range(1, self.settings.market_search_max_pages + 1):
                        response = browser.load_catalog(
                            current_url, self.settings.catalog_wait_seconds,
                            self.settings.page_reloads, navigate=True,
                        )
                        self._write_trace(run_dir, {
                            "stage": "market_search", "client_article": owner_article,
                            "query": query, "page": page_no, "url": current_url,
                            "status": response.get("status"), "elapsed_ms": response.get("elapsed_ms"),
                            "events": response.get("events"),
                        })
                        if not response.get("ok"):
                            search_error = str(response.get("status") or "NO_CATALOG")
                            if search_error.startswith("BLOCKED"):
                                metrics["items_blocked"] += 1
                            break
                        metrics["pages_loaded"] += 1
                        for rank, item in enumerate(response.get("products") or [], start=1):
                            article = str(item.get("article") or "")
                            if article in known_articles:
                                metrics["existing_candidates_seen"] += 1
                                continue
                            if (
                                not article or article == owner_article
                                or article in all_catalog_articles
                                or article in same_product_articles
                                or article in unique_candidates
                            ):
                                continue
                            self.registry.upsert_catalog_product(item, search_url, run_id, page_no, now_iso())
                            unique_candidates[article] = (item, query, search_url, rank)
                            metrics["new_candidates"] += 1
                            if len(unique_candidates) >= self.settings.market_search_candidate_limit:
                                break
                        if len(unique_candidates) >= self.settings.market_search_candidate_limit:
                            break
                        next_page = str(response.get("next_page") or "")
                        if not next_page:
                            break
                        current_url = next_page
                    # First query results are enriched before deciding whether fallback is needed.
                    detail_articles = [
                        article
                        for article in list(unique_candidates)[
                            :self.settings.market_search_detail_limit
                        ]
                        if article not in processed_candidate_articles
                    ]
                    for candidate_article in detail_articles:
                        processed_candidate_articles.add(candidate_article)
                        refreshed = refreshed_candidate_articles.get(candidate_article)
                        if refreshed is None:
                            refreshed = self._enrich_market_candidate(
                                candidate_article,
                                run_id,
                                run_dir,
                                force=True,
                            )
                            refreshed_candidate_articles[candidate_article] = bool(refreshed)
                        if not refreshed:
                            continue
                        candidate = self.registry.get_product(candidate_article)
                        offer = self.registry.primary_offer(candidate_article)
                        match = evaluate_match(owner, candidate)
                        if self._is_expected_market_seller(offer):
                            match = {
                                "accepted": False,
                                "level": "REJECTED",
                                "score": 0,
                                "method": "OWN_SELLER",
                                "reason": "Собственный продавец",
                                "reasons": [],
                            }
                        _, q, q_url, rank = unique_candidates[candidate_article]
                        self.registry.save_market_candidate(
                            owner_article, candidate_article, q, q_url, rank, match, run_id,
                            candidate=candidate, offer=offer,
                        )
                    total_candidates, exact_count, comparable_count = (
                        self._market_snapshot_counts(run_id, owner_article)
                    )
                    if exact_count:
                        break
                    sleep_range(self.settings.market_search_delay_seconds)
                metrics["candidates_found"] += total_candidates
                metrics["exact_found"] += exact_count
                metrics["comparable_found"] += comparable_count
                item_status = (
                    "BLOCKED" if search_error.startswith("BLOCKED")
                    else "FAILED" if search_error
                    else "COMPLETED" if exact_count or comparable_count
                    else "NO_MATCH"
                )
                if search_error:
                    metrics["items_failed"] += 1
                    status = "BLOCKED" if item_status == "BLOCKED" else "PARTIAL"
                self.registry.finish_market_search(owner_article,last_query,last_url,item_status,total_candidates,exact_count,comparable_count,run_id,search_error)
                self.registry.record_market_analysis_product(
                    run_id, owner_article, item_status, last_query, last_url,
                    total_candidates, exact_count, comparable_count, search_error,
                )
                if not search_error:
                    metrics["items_success"] += 1
                print(f"  Результат: кандидатов {total_candidates}; точных/сильных {exact_count}; бренд+размер {comparable_count}")
                sleep_range(self.settings.market_search_delay_seconds)
        except KeyboardInterrupt:
            status = "INTERRUPTED"
        except Exception:
            status = "FAILED"
            (run_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            raise
        finally:
            metrics["duration_seconds"] = round(time.monotonic() - started, 2)
            status = self.registry.finish_market_analysis(run_id, status, metrics)
            self.registry.finish_run(run_id, status, metrics)
            (run_dir / "summary.json").write_text(json.dumps({"run_id":run_id,"mode":mode,"status":status,**metrics},ensure_ascii=False,indent=2),encoding="utf-8")
            self.generate_outputs()
        print(f"\nГотово: {metrics['items_success']}/{metrics['items_total']}; точных/сильных {metrics['exact_found']}; бренд+размер {metrics['comparable_found']}; время {metrics['duration_seconds']} сек.")
        return {
            "run_id": run_id, "run_dir": str(run_dir), "status": status,
            "catalog_run_id": catalog_run_id, **metrics,
        }

    def sync_catalog(self, limit: int | None = None) -> dict[str, Any]:
        discovery = self.discover()

        if has_hard_failure(discovery):
            discovery_status = str(
                discovery.get("status") or "PARTIAL"
            ).upper()

            print(
                "Discovery не завершён. "
                "Обогащение и актуализация не запускаются."
            )

            return {
                "status": discovery_status,
                "discovery": discovery,
                "details": None,
            }

        # The snapshot must include fresh own price/availability for every
        # product that this discovery actually observed.
        details = self.process(
            "refresh-prices",
            0,
            catalog_run_id=str(discovery.get("run_id") or ""),
        )
        expected_articles = int(discovery.get("items_total") or 0)
        refreshed_articles = int(details.get("items_total") or 0)
        if refreshed_articles != expected_articles:
            details = {
                **details,
                "status": "PARTIAL",
                "expected_catalog_articles": expected_articles,
                "selected_catalog_articles": refreshed_articles,
            }
        return {
            "status": combined_status(
                discovery,
                details,
            ),
            "discovery": discovery,
            "details": details,
        }

    def full_sync(self, limit: int | None = None) -> dict[str, Any]:
        catalog = self.sync_catalog(limit)

        if str(catalog.get("status") or "").upper() not in {"PASSED", "OK", "READY"}:
            return {
                "status": str(
                    catalog.get("status") or "PARTIAL"
                ).upper(),
                "catalog": catalog,
                "market": None,
            }
        # Catalog synchronisation has already captured the seller's own
        # current price.  Full sync proceeds directly to market analysis.
        market = self.market_search(
            limit,
            catalog_run_id=str((catalog.get("discovery") or {}).get("run_id") or ""),
        )

        return {
            "status": combined_status(
                catalog,
                market,
            ),
            "catalog": catalog,
            "market": market,
        }

    def generate_outputs(self) -> None:
        self.registry.export_current(
            self.settings.exports_dir / "unityre_current.json",
            self.settings.exports_dir / "unityre_current.csv",
        )
        generate_dashboard(
            self.settings.database_path,
            self.settings.reports_dir / "index.html",
        )


def load_article_filter(path_value: str | None) -> set[str] | None:
    if not path_value:
        return None
    path = Path(path_value).resolve()
    value = json.loads(path.read_text(encoding="utf-8"))
    raw = value.get("articles") if isinstance(value, dict) else value
    if not isinstance(raw, list):
        raise ValueError("Некорректный список товаров Ozon.ru.")
    result = {str(article).strip().removeprefix("ozon:") for article in raw if str(article).strip()}
    return result


def current_tenant_ozon_articles(args: argparse.Namespace) -> set[str] | None:
    """Use the active tenant seller catalogue as the market-search boundary."""
    tenant_id = int(getattr(args, "tenant_id", 0) or 0)
    app_db = str(getattr(args, "app_db", "") or "").strip()
    if tenant_id <= 0 or not app_db:
        return None
    memberships = CatalogConfigurationService(Path(app_db)).catalog_memberships(
        tenant_id,
        {"ozon"},
        tenant_seller_id=int(getattr(args, "tenant_seller_id", 0) or 0) or None,
    )
    return {
        str(article) for marketplace, article in memberships
        if marketplace == "ozon" and str(article).strip()
    }


def settings_for_args(settings: Settings, args: argparse.Namespace) -> Settings:
    updates: dict[str, Any] = {}
    source_url = str(getattr(args, "source_url", "") or "").strip()
    if source_url:
        root_url = seller_root_url(source_url)
        if not root_url:
            raise ValueError("Для российского коллектора укажите ссылку продавца на ozon.ru.")
        updates["start_url"] = root_url
        updates["start_urls"] = (root_url,)
    expected = str(getattr(args, "expected_seller", "") or "").strip()
    if expected:
        updates["expected_seller"] = expected
    database_path = str(getattr(args, "database_path", "") or "").strip()
    if database_path:
        updates["database_path"] = Path(database_path).resolve()
    runtime_dir = str(getattr(args, "runtime_dir", "") or "").strip()
    if runtime_dir:
        runtime = Path(runtime_dir).resolve()
        updates.update({
            "runs_dir": runtime / "runs",
            "reports_dir": runtime / "reports",
            "exports_dir": runtime / "exports",
            "raw_dir": runtime / "raw",
        })
    profile_path = str(getattr(args, "profile_path", "") or "").strip()
    if profile_path:
        updates["browser_profile_path"] = Path(profile_path).resolve()
    debug_port = getattr(args, "debug_port", None)
    if debug_port is not None:
        updates["debug_port"] = int(debug_port)
    return replace(settings, **updates) if updates else settings


def marketplace_seller_identifiers(settings: Settings) -> tuple[str, ...]:
    values = {str(settings.expected_seller or "").strip().casefold()}
    parsed = urlparse(str(settings.start_url or ""))
    parts = [part.strip().casefold() for part in parsed.path.split("/") if part.strip()]
    if len(parts) >= 2 and parts[0] in {"seller", "продавец"}:
        slug = parts[1]
        values.add(slug)
        values.update(
            part for part in slug.split("-")
            if part.isdigit() and len(part) >= 3
        )
    values.discard("")
    return tuple(sorted(values))


def materialize_tenant_catalog(
    settings: Settings,
    tenant_id: int,
    app_db: str,
    marketplace_code: str = "ozon",
    tenant_seller_id: int | None = None,
    catalog_run_id: str = "",
    refresh_run_id: str = "",
) -> int:
    if int(tenant_id or 0) <= 0 or not str(app_db or "").strip():
        return 0
    source_urls = {str(url).strip() for url in settings.start_urls if str(url).strip()}
    conn = connect_database(settings.database_path, timeout=30)
    configure_connection(conn, busy_timeout=30000)
    try:
        params: list[Any] = []
        where = "p.active=1"
        if source_urls:
            placeholders = ",".join("?" for _ in source_urls)
            run_clause = " AND ps.last_run_id=?" if str(catalog_run_id).strip() else ""
            where += f" AND EXISTS(SELECT 1 FROM product_sources ps WHERE ps.article=p.article AND ps.source_url IN ({placeholders}){run_clause})"
            params.extend(sorted(source_urls))
            if run_clause:
                params.append(str(catalog_run_id).strip())
        rows = conn.execute(
            f"""SELECT p.* FROM products p WHERE {where} ORDER BY p.article""",
            params,
        ).fetchall()
        identifiers = marketplace_seller_identifiers(settings)
        offer_conditions: list[str] = []
        offer_params: list[Any] = []
        for identifier in identifiers:
            offer_conditions.append(
                "(lower(o.seller_name)=? OR lower(o.seller_id)=? "
                "OR lower(o.seller_url) LIKE ? OR lower(o.seller_url) LIKE ?)"
            )
            offer_params.extend(
                (
                    identifier,
                    identifier,
                    f"%/seller/{identifier}/%",
                    f"%/продавец/{identifier}/%",
                )
            )
        offers: dict[str, dict[str, Any]] = {}
        if offer_conditions:
            if marketplace_code == "ozon" and str(refresh_run_id).strip():
                offer_rows = conn.execute(
                    """SELECT o.*,
                              ph.card_price AS fresh_card_price,
                              ph.regular_price AS fresh_regular_price,
                              ph.original_price AS fresh_original_price,
                              ph.catalog_price AS fresh_catalog_price,
                              ph.availability_status AS fresh_availability_status,
                              ph.currency AS fresh_currency,
                              ph.collected_at AS fresh_collected_at
                       FROM price_history ph
                       JOIN offers o ON o.article=ph.article AND o.seller_key=ph.seller_key
                       WHERE ph.run_id=? AND o.active=1 AND ("""
                    + " OR ".join(offer_conditions)
                    + ") ORDER BY o.article,o.last_checked_at DESC",
                    [str(refresh_run_id).strip(), *offer_params],
                ).fetchall()
            else:
                offer_rows = conn.execute(
                    "SELECT o.* FROM offers o WHERE o.active=1 AND ("
                    + " OR ".join(offer_conditions)
                    + ") ORDER BY o.article,o.last_checked_at DESC",
                    offer_params,
                ).fetchall()
            for row in offer_rows:
                offers.setdefault(str(row["article"]), dict(row))
        if marketplace_code == "ozon" and str(catalog_run_id).strip():
            if not str(refresh_run_id).strip():
                raise RuntimeError("Ozon.ru publication requires a passed refresh run.")
            expected_articles = {str(row["article"]) for row in rows}
            missing_fresh_offers = sorted(expected_articles - set(offers))
            if missing_fresh_offers:
                raise RuntimeError(
                    "Ozon.ru publication refused: fresh own offer is missing for "
                    f"{len(missing_fresh_offers)} discovered article(s)."
                )
    finally:
        conn.close()
    products: list[dict[str, Any]] = []
    currency = "KZT" if marketplace_code == "ozon_kz" else "RUB"
    for raw in rows:
        value = dict(raw)
        own = offers.get(str(value.get("article") or ""), {})
        fresh_own = marketplace_code == "ozon" and str(refresh_run_id).strip()
        attributes = [
            {"name": label, "value": value.get(key)}
            for key, label in (
                ("model", "Модель"), ("manufacturer_article", "Артикул производителя"),
                ("tire_size", "Размер"), ("season", "Сезон"),
            )
            if value.get(key) not in (None, "", "UNKNOWN")
        ]
        products.append({
            "product_id": value.get("article") or "",
            "title": value.get("title") or value.get("article") or "",
            "brand": value.get("brand") or "",
            "model": value.get("model") or "",
            "url": value.get("canonical_url") or "",
            "image_url": value.get("image_url") or "",
            "price": (
                own.get("fresh_card_price") or own.get("fresh_regular_price")
                if fresh_own else own.get("card_price") or own.get("regular_price")
            ) or (
                None if fresh_own else value.get("catalog_price") or None
            ),
            "currency": (
                "KZT" if marketplace_code == "ozon_kz"
                else own.get("fresh_currency") if fresh_own else own.get("currency") or currency
            ),
            "availability": (
                own.get("fresh_availability_status") if fresh_own
                else own.get("availability_status") or ""
            ),
            "category": "",
            "attributes": attributes,
            "updated_at": (
                own.get("fresh_collected_at") if fresh_own
                else value.get("last_price_at") or value.get("last_detail_at") or value.get("last_seen_at")
            ),
        })
    return CatalogConfigurationService(Path(app_db)).replace_catalog_products(
        int(tenant_id), marketplace_code, products,
        tenant_seller_id=tenant_seller_id,
    )


def add_connection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-url", default="")
    parser.add_argument("--expected-seller", default="")
    parser.add_argument("--database-path", default="")
    parser.add_argument("--runtime-dir", default="")
    parser.add_argument("--profile-path", default="")
    parser.add_argument("--debug-port", type=int, default=None)
    parser.add_argument("--tenant-id", type=int, default=0)
    parser.add_argument("--tenant-seller-id", type=int, default=0)
    parser.add_argument("--app-db", default="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ozon.ru Collector 3.0")
    sub = parser.add_subparsers(dest="command", required=True)
    open_browser = sub.add_parser("open-browser")
    add_connection_options(open_browser)
    discover = sub.add_parser("discover")
    add_connection_options(discover)
    discover.add_argument("--limit", type=int, default=None)
    discover.add_argument("--pages", type=int, default=None)
    sync_catalog = sub.add_parser("sync-catalog")
    add_connection_options(sync_catalog)
    sync_catalog.add_argument("--limit", type=int, default=None)
    for name in ("enrich-new", "refresh-prices", "refresh-stale", "retry-failed", "stress-test"):
        child = sub.add_parser(name)
        add_connection_options(child)
        child.add_argument("--limit", type=int, default=None)
        child.add_argument("--articles-file", default="")
    market = sub.add_parser("market-search")
    add_connection_options(market)
    market.add_argument("--limit", type=int, default=None)
    market.add_argument("--articles-file", default="")
    full = sub.add_parser("full-sync")
    add_connection_options(full)
    full.add_argument("--limit", type=int, default=None)
    for name in ("report", "export", "stats"):
        child = sub.add_parser(name)
        add_connection_options(child)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = settings_for_args(load_settings(), args)
    collector = Collector(settings)
    article_filter = load_article_filter(getattr(args, "articles_file", ""))
    result: dict[str, Any] | None = None
    failure: Exception | None = None
    try:
        if args.command == "open-browser":
            result = collector.open_browser()
        elif args.command == "discover":
            result = collector.discover(args.limit, args.pages)
        elif args.command == "sync-catalog":
            result = collector.sync_catalog(args.limit)
        elif args.command in {"enrich-new", "refresh-prices", "refresh-stale", "retry-failed", "stress-test"}:
            result = collector.process(args.command, args.limit, article_filter)
        elif args.command == "market-search":
            result = collector.market_search(
                args.limit, article_filter,
                catalog_articles=current_tenant_ozon_articles(args),
            )
        elif args.command == "full-sync":
            catalog_only = collector.sync_catalog(args.limit)
            result = {
                "status": str(
                    catalog_only.get("status") or "PARTIAL"
                ).upper(),
                "catalog": catalog_only,
                "market": None,
            }
        elif args.command == "report":
            collector.generate_outputs()
            print(settings.reports_dir / "index.html")
        elif args.command == "export":
            collector.generate_outputs()
            print(settings.exports_dir)
        elif args.command == "stats":
            print(json.dumps(collector.registry.counts(), ensure_ascii=False, indent=2))
        catalog_result = (
            result.get("catalog") if args.command == "full-sync" and result
            else result
        )
        if (
            args.command in {"sync-catalog", "full-sync"}
            and isinstance(catalog_result, dict)
            and str(catalog_result.get("status") or "").upper() == "PASSED"
        ):
            materialize_tenant_catalog(
                settings, int(args.tenant_id or 0), str(args.app_db or ""), "ozon",
                tenant_seller_id=int(args.tenant_seller_id or 0) or None,
                catalog_run_id=str(
                    (catalog_result.get("discovery") or {}).get("run_id") or ""
                ),
                refresh_run_id=str(
                    (catalog_result.get("details") or {}).get("run_id") or ""
                ),
            )
            if int(args.tenant_id or 0) > 0 and str(args.app_db or "").strip():
                collector.registry.mark_catalog_published(str(
                    (catalog_result.get("discovery") or {}).get("run_id") or ""
                ))

            if args.command == "full-sync":
                market = collector.market_search(
                    args.limit,
                    catalog_run_id=str(
                        (catalog_result.get("discovery") or {}).get("run_id")
                        or ""
                    ),
                )
                result = {
                    "status": combined_status(
                        catalog_result,
                        market,
                    ),
                    "catalog": catalog_result,
                    "market": market,
                }

        return result_exit_code(result)
    except Exception as exc:
        print(f"Collector error: {exc}", file=sys.stderr)
        result = {"status": "FAILED"}
        failure = exc
        return 2
    finally:
        if result is not None:
            print("SPYON_RESULT " + json.dumps(structured_result(result, failure), ensure_ascii=False))
        collector.close()


if __name__ == "__main__":
    raise SystemExit(main())
