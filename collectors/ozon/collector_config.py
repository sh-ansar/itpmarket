#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

ROOT = Path(__file__).resolve().parent


def _values(path: Path) -> list[str]:
    if not path.exists():
        return []
    result: list[str] = []
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        value = raw.strip()
        if value and not value.startswith("#") and value not in result:
            result.append(value)
    return result


def _first_value(path: Path) -> str:
    values = _values(path)
    return values[0] if values else ""


def seller_root_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    host = parsed.netloc.lower().split(":")[0]
    if host not in {"ozon.ru", "www.ozon.ru"}:
        return ""
    match = re.search(r"/(?:seller|продавец)/([^/?#]+)/?", parsed.path, re.IGNORECASE)
    if not match:
        return ""
    return urlunparse(("https", "www.ozon.ru", f"/seller/{match.group(1)}/", "", "", ""))


def _pair(value: Any, default: tuple[float, float]) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            left = float(value[0])
            right = float(value[1])
            return (min(left, right), max(left, right))
        except (TypeError, ValueError):
            pass
    return default


@dataclass(frozen=True)
class Settings:
    start_url: str
    start_urls: tuple[str, ...]
    expected_seller: str
    debug_port: int
    browser_profile_path: Path
    catalog_max_pages: int
    catalog_product_limit: int
    batch_limit: int
    stale_days: int
    request_wait_seconds: int
    catalog_wait_seconds: int
    page_reloads: int
    product_reloads: int
    product_delay_seconds: tuple[float, float]
    page_delay_seconds: tuple[float, float]
    technical_pause_every: int
    technical_pause_seconds: tuple[float, float]
    blocked_pause_after: int
    blocked_pause_seconds: int
    blocked_stop_after: int
    max_task_attempts: int
    raw_json_policy: str
    market_search_batch_limit: int
    market_search_max_pages: int
    market_search_candidate_limit: int
    market_search_detail_limit: int
    market_search_delay_seconds: tuple[float, float]
    database_path: Path
    runs_dir: Path
    reports_dir: Path
    exports_dir: Path
    raw_dir: Path


def load_settings() -> Settings:
    config_path = ROOT / "CONFIG.json"
    config: dict[str, Any] = {}
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))

    start_urls = _values(ROOT / "START_URLS.txt")
    if not start_urls:
        start_urls = _values(ROOT / "START_URL.txt")
    configured_urls = config.get("start_urls")
    if not start_urls and isinstance(configured_urls, list):
        start_urls = [str(value).strip() for value in configured_urls if str(value).strip()]
    if not start_urls:
        configured_start = str(config.get("start_url") or "").strip()
        if configured_start:
            start_urls = [configured_start]
    if not start_urls:
        start_urls = ["https://www.ozon.ru/category/shiny-zimnie-8803/?__rr=1"]

    # Storefront roots are processed first, so saved seller-category links do not
    # accidentally limit discovery to only one storefront section.
    sellers = [
        url for url in start_urls
        if re.search(r"/(?:seller|продавец)/", url, re.IGNORECASE)
    ]
    others = [url for url in start_urls if url not in sellers]
    ordered_urls: list[str] = []
    for url in sellers:
        root_url = seller_root_url(url)
        if root_url and root_url not in ordered_urls:
            ordered_urls.append(root_url)
        if url not in ordered_urls:
            ordered_urls.append(url)
    for url in others:
        if url not in ordered_urls:
            ordered_urls.append(url)

    start_url = ordered_urls[0]
    expected_seller = _first_value(ROOT / "EXPECTED_SELLER.txt") or str(
        config.get("expected_seller") or ""
    )

    return Settings(
        start_url=start_url,
        start_urls=tuple(ordered_urls),
        expected_seller=expected_seller,
        debug_port=int(config.get("debug_port", 9222)),
        browser_profile_path=ROOT / "chrome_vpn_profile",
        catalog_max_pages=max(1, int(config.get("catalog_max_pages", 100))),
        catalog_product_limit=max(0, int(config.get("catalog_product_limit", 0))),
        batch_limit=max(1, int(config.get("batch_limit", 100))),
        stale_days=max(1, int(config.get("stale_days", 30))),
        request_wait_seconds=max(20, int(config.get("request_wait_seconds", 45))),
        catalog_wait_seconds=max(15, int(config.get("catalog_wait_seconds", 45))),
        page_reloads=max(0, min(3, int(config.get("page_reloads", 3)))),
        product_reloads=max(0, min(3, int(config.get("product_reloads", 3)))),
        product_delay_seconds=_pair(config.get("product_delay_seconds"), (2.5, 4.0)),
        page_delay_seconds=_pair(config.get("page_delay_seconds"), (4.0, 7.0)),
        technical_pause_every=max(0, int(config.get("technical_pause_every", 50))),
        technical_pause_seconds=_pair(config.get("technical_pause_seconds"), (20.0, 40.0)),
        blocked_pause_after=max(1, int(config.get("blocked_pause_after", 3))),
        blocked_pause_seconds=max(5, int(config.get("blocked_pause_seconds", 120))),
        blocked_stop_after=max(2, int(config.get("blocked_stop_after", 5))),
        max_task_attempts=max(1, int(config.get("max_task_attempts", 3))),
        raw_json_policy=str(config.get("raw_json_policy", "first_success_and_errors")),
        market_search_batch_limit=max(1, int(config.get("market_search_batch_limit", 30))),
        market_search_max_pages=max(1, min(3, int(config.get("market_search_max_pages", 1)))),
        market_search_candidate_limit=max(3, min(30, int(config.get("market_search_candidate_limit", 12)))),
        market_search_detail_limit=max(2, min(15, int(config.get("market_search_detail_limit", 8)))),
        market_search_delay_seconds=_pair(config.get("market_search_delay_seconds"), (4.0, 7.0)),
        database_path=ROOT / str(config.get("database_path", "data/ozon_registry.db")),
        runs_dir=ROOT / str(config.get("runs_dir", "runs")),
        reports_dir=ROOT / str(config.get("reports_dir", "reports")),
        exports_dir=ROOT / str(config.get("exports_dir", "exports")),
        raw_dir=ROOT / str(config.get("raw_dir", "raw")),
    )
