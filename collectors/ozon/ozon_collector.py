#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import random
import sqlite3
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
from storage.postgres_compat import connect_database


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
    # PARTIAL means the collector completed its pass and persisted every
    # successful item. Failed items remain eligible for a later retry.
    # It is therefore recoverable and must not abort full-sync/materialization.
    return 0 if status in {"OK", "PASSED", "READY", "PARTIAL"} else 2


def structured_result(result: dict[str, Any] | None, error: Exception | None = None) -> dict[str, Any]:
    status = str((result or {}).get("status") or "").upper()
    text = str(error or "")
    if status == "PARTIAL" and error is None:
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
        self.registry.begin_run(run_id, mode, "\n".join(source_urls))
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
            summary = {
                "run_id": run_id,
                "mode": mode,
                "start_url": self.settings.start_url,
                "start_urls": source_urls,
                "sources": source_summaries,
                "products_seen": len(seen),
                "new_products": new_count,
                "catalog_price_changed": changed_count,
                **metrics,
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
        return {"run_id": run_id, "run_dir": str(run_dir), "status": status, **metrics}

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
        self, mode: str, limit: int | None = None, articles: set[str] | None = None
    ) -> dict[str, Any]:
        run_id = run_id_for(mode)
        run_dir = self._run_dir(run_id)
        self.registry.begin_run(run_id, mode, self.settings.start_url)
        started = time.monotonic()
        batch_limit = self.settings.batch_limit if limit is None else max(0, limit)
        source_articles = self.registry.articles_for_sources(
            self.settings.start_urls or (self.settings.start_url,)
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


    def _enrich_market_candidate(self, article: str, run_id: str, run_dir: Path) -> bool:
        before = self.registry.get_product(article)
        if before.get("detail_status") == "COMPLETE" and before.get("last_detail_at"):
            return True
        response = self.ensure_browser().load_product_api(
            article, self.settings.request_wait_seconds, self.settings.product_reloads
        )
        self._write_trace(run_dir, {
            "stage": "market_candidate_detail", "article": article,
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

    def market_search(
        self, limit: int | None = None, articles: set[str] | None = None
    ) -> dict[str, Any]:
        mode = "market-search"
        run_id = run_id_for(mode)
        run_dir = self._run_dir(run_id)
        batch_limit = self.settings.market_search_batch_limit if limit is None else max(1, int(limit))
        owners = self.registry.client_products_for_market_search(batch_limit, allowed_articles=articles)
        self.registry.begin_run(run_id, mode, self.settings.start_url)
        browser = self.ensure_browser()
        started = time.monotonic()
        metrics = {
            "pages_loaded": 0, "items_total": len(owners), "items_success": 0,
            "items_failed": 0, "items_blocked": 0, "candidates_found": 0,
            "exact_found": 0, "comparable_found": 0,
        }
        status = "PASSED"
        print("=" * 78)
        print("OZON COLLECTOR 3.3.2 — ПОИСК РЫНОЧНЫХ ПРЕДЛОЖЕНИЙ")
        print("Основной уровень: бренд + модель + размер. Резервный: бренд + размер.")
        print("Совпадения разных брендов и размеров автоматически отклоняются.")
        print("=" * 78)
        print(f"Товаров клиента в очереди: {len(owners)}")
        try:
            for owner_index, owner in enumerate(owners, start=1):
                owner_article = str(owner.get("article") or "")
                queries = build_search_queries(owner)
                print(f"\n[ПОЗИЦИЯ {owner_index}/{len(owners)}] {owner_article} — {owner.get('title','')}")
                if not queries:
                    print("SKIP | недостаточно бренда или размера для поиска")
                    self.registry.finish_market_search(owner_article, "", "", "SKIPPED", 0, 0, 0, run_id, "Недостаточно данных")
                    metrics["items_failed"] += 1; status = "PARTIAL"; continue
                self.registry.begin_market_search(owner_article, run_id)
                unique_candidates: dict[str, tuple[dict[str, Any], str, str, int]] = {}
                exact_count = comparable_count = 0
                last_query = last_url = ""
                search_error = ""
                for query_index, query in enumerate(queries, start=1):
                    # Manufacturer/article and model searches are primary. Brand+size is fallback.
                    if query_index > 1 and exact_count > 0:
                        break
                    search_url = f"https://www.ozon.ru/search/?text={quote_plus(query)}&from_global=true"
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
                            if not article or article == owner_article or article in unique_candidates:
                                continue
                            self.registry.upsert_catalog_product(item, search_url, run_id, page_no, now_iso())
                            unique_candidates[article] = (item, query, search_url, rank)
                            if len(unique_candidates) >= self.settings.market_search_candidate_limit:
                                break
                        if len(unique_candidates) >= self.settings.market_search_candidate_limit:
                            break
                        next_page = str(response.get("next_page") or "")
                        if not next_page:
                            break
                        current_url = next_page
                    # First query results are enriched before deciding whether fallback is needed.
                    detail_articles = list(unique_candidates)[:self.settings.market_search_detail_limit]
                    for candidate_article in detail_articles:
                        self._enrich_market_candidate(candidate_article, run_id, run_dir)
                        candidate = self.registry.get_product(candidate_article)
                        offer = self.registry.primary_offer(candidate_article)
                        own_seller = str(offer.get("seller_name") or "").strip().casefold() == str(self.settings.expected_seller or "").strip().casefold()
                        match = evaluate_match(owner, candidate)
                        if own_seller:
                            match = {"accepted": False, "level": "REJECTED", "score": 0, "method": "OWN_SELLER", "reason": "Собственный продавец", "reasons": []}
                        _, q, q_url, rank = unique_candidates[candidate_article]
                        self.registry.save_market_candidate(owner_article, candidate_article, q, q_url, rank, match, run_id)
                    rows = self.registry.conn.execute(
                        "SELECT match_level,COUNT(*) c FROM market_search_candidates WHERE client_article=? AND active=1 GROUP BY match_level",
                        (owner_article,),
                    ).fetchall()
                    counts = {str(row[0]): int(row[1]) for row in rows}
                    exact_count = counts.get("EXACT",0)+counts.get("STRONG",0)
                    comparable_count = counts.get("COMPARABLE",0)
                    if exact_count:
                        break
                    sleep_range(self.settings.market_search_delay_seconds)
                total_candidates = len(unique_candidates)
                metrics["candidates_found"] += total_candidates
                metrics["exact_found"] += exact_count
                metrics["comparable_found"] += comparable_count
                item_status = "COMPLETED" if exact_count or comparable_count else "NO_MATCH"
                self.registry.finish_market_search(owner_article,last_query,last_url,item_status,total_candidates,exact_count,comparable_count,run_id,search_error)
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
            self.registry.finish_run(run_id, status, metrics)
            (run_dir / "summary.json").write_text(json.dumps({"run_id":run_id,"mode":mode,"status":status,**metrics},ensure_ascii=False,indent=2),encoding="utf-8")
            self.generate_outputs()
        print(f"\nГотово: {metrics['items_success']}/{metrics['items_total']}; точных/сильных {metrics['exact_found']}; бренд+размер {metrics['comparable_found']}; время {metrics['duration_seconds']} сек.")
        return {"run_id":run_id,"run_dir":str(run_dir),"status":status,**metrics}

    def sync_catalog(self, limit: int | None = None) -> dict[str, Any]:
        discovery = self.discover()

        if result_exit_code(discovery) != 0:
            discovery_status = str(
                discovery.get("status") or "PARTIAL"
            ).upper()

            print(
                "Discovery ???????? ???????????. "
                "??????????? ? ?????????? ??? ?????????."
            )

            return {
                "status": discovery_status,
                "discovery": discovery,
                "details": None,
            }

        remaining = self.registry.select_articles(
            "enrich-new",
            limit or 0,
            self.settings.stale_days,
            self.settings.max_task_attempts,
        )

        if not remaining:
            print(
                "????? ??? ???????? ???????? ???. "
                "????????? ???? ????????? ???????."
            )

            details = self.process(
                "refresh-prices",
                limit,
            )
        else:
            details = self.process(
                "enrich-new",
                limit,
            )

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

        if result_exit_code(catalog) != 0:
            return {
                "status": str(
                    catalog.get("status") or "PARTIAL"
                ).upper(),
                "catalog": catalog,
                "prices": None,
                "market": None,
            }

        prices = self.process(
            "refresh-prices",
            limit,
        )

        if result_exit_code(prices) != 0:
            return {
                "status": str(
                    prices.get("status") or "PARTIAL"
                ).upper(),
                "catalog": catalog,
                "prices": prices,
                "market": None,
            }

        market = self.market_search(limit)

        return {
            "status": combined_status(
                catalog,
                prices,
                market,
            ),
            "catalog": catalog,
            "prices": prices,
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
    if len(parts) >= 2 and parts[0] == "seller":
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
) -> int:
    if int(tenant_id or 0) <= 0 or not str(app_db or "").strip():
        return 0
    source_urls = {str(url).strip() for url in settings.start_urls if str(url).strip()}
    conn = connect_database(settings.database_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        params: list[Any] = []
        where = "p.active=1"
        if source_urls:
            placeholders = ",".join("?" for _ in source_urls)
            where += f" AND EXISTS(SELECT 1 FROM product_sources ps WHERE ps.article=p.article AND ps.source_url IN ({placeholders}))"
            params.extend(sorted(source_urls))
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
                "OR lower(o.seller_url) LIKE ?)"
            )
            offer_params.extend(
                (identifier, identifier, f"%/seller/{identifier}/%")
            )
        offers: dict[str, dict[str, Any]] = {}
        if offer_conditions:
            offer_rows = conn.execute(
                "SELECT o.* FROM offers o WHERE o.active=1 AND ("
                + " OR ".join(offer_conditions)
                + ") ORDER BY o.article,o.last_checked_at DESC",
                offer_params,
            ).fetchall()
            for row in offer_rows:
                offers.setdefault(str(row["article"]), dict(row))
    finally:
        conn.close()
    products: list[dict[str, Any]] = []
    currency = "KZT" if marketplace_code == "ozon_kz" else "RUB"
    for raw in rows:
        value = dict(raw)
        own = offers.get(str(value.get("article") or ""), {})
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
                own.get("card_price") or own.get("regular_price")
                or value.get("catalog_price") or None
            ),
            "currency": (
                "KZT" if marketplace_code == "ozon_kz"
                else own.get("currency") or currency
            ),
            "availability": own.get("availability_status") or "",
            "category": "",
            "attributes": attributes,
            "updated_at": value.get("last_price_at") or value.get("last_detail_at") or value.get("last_seen_at"),
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
            result = collector.market_search(args.limit, article_filter)
        elif args.command == "full-sync":
            result = collector.full_sync(args.limit)
        elif args.command == "report":
            collector.generate_outputs()
            print(settings.reports_dir / "index.html")
        elif args.command == "export":
            collector.generate_outputs()
            print(settings.exports_dir)
        elif args.command == "stats":
            print(json.dumps(collector.registry.counts(), ensure_ascii=False, indent=2))
        if (
            args.command not in {"open-browser", "stats"}
            and result_exit_code(result) == 0
        ):
            materialize_tenant_catalog(
                settings, int(args.tenant_id or 0), str(args.app_db or ""), "ozon",
                tenant_seller_id=int(args.tenant_seller_id or 0) or None,
            )
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
