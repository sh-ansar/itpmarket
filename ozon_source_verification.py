"""Evidence-based Ozon storefront verification.

Parsing a seller URL is deliberately not verification.  This module keeps the
network/browser part behind a small boundary so application code can fail
closed and tests can exercise the evidence contract with recorded snapshots.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse, urlunparse


class OzonSourceVerificationError(ValueError):
    """The supplied source has not been proven to be an Ozon storefront."""


_HOSTS = {
    "ozon": {"ozon.ru", "www.ozon.ru"},
    "ozon_kz": {"ozon.kz", "www.ozon.kz"},
}


def _canonical_url(value: str, marketplace_code: str) -> str:
    code = str(marketplace_code or "").strip().casefold()
    parsed = urlparse(str(value or "").strip())
    host = str(parsed.hostname or "").casefold()
    if code not in _HOSTS or parsed.scheme != "https" or host not in _HOSTS[code]:
        raise OzonSourceVerificationError("Ozon source belongs to a different marketplace host.")
    match = re.fullmatch(r"/(?:seller|продавец)/([^/?#]+)/?", parsed.path, re.IGNORECASE)
    if not match:
        raise OzonSourceVerificationError("The final Ozon URL is not a seller storefront.")
    seller_id = match.group(1).strip()
    if not seller_id:
        raise OzonSourceVerificationError("The Ozon seller identifier is empty.")
    canonical_host = "www.ozon.ru" if code == "ozon" else "ozon.kz"
    return urlunparse(("https", canonical_host, f"/seller/{seller_id}/", "", "", ""))


def _canonical_link(page_html: str, final_url: str) -> str:
    for tag in re.findall(r"<link\b[^>]*>", str(page_html or ""), re.IGNORECASE):
        if not re.search(r"\brel\s*=\s*(['\"])canonical\1", tag, re.IGNORECASE):
            continue
        match = re.search(r"\bhref\s*=\s*(['\"])(.*?)\1", tag, re.IGNORECASE | re.DOTALL)
        if match:
            return urljoin(final_url, match.group(2).strip())
    return ""


def resolve_ozon_snapshot(
    marketplace_code: str,
    *,
    final_url: str,
    page_html: str,
    page_text: str,
    page_title: str = "",
) -> dict[str, Any]:
    """Verify an Ozon seller storefront by its real seller catalogue."""

    code = str(marketplace_code or "").strip().casefold()

    # The browser must remain on a seller storefront for the selected
    # marketplace. Search, category, product and foreign-host pages fail here.
    final = _canonical_url(final_url, code)

    canonical_raw = _canonical_link(page_html, final_url)
    canonical = final

    # Canonical is corroborating evidence only. Ozon SPA may omit it.
    # When present it must identify exactly the same seller.
    if canonical_raw:
        canonical = _canonical_url(canonical_raw, code)
        if canonical != final:
            raise OzonSourceVerificationError(
                "Ozon final URL and canonical seller identity disagree."
            )

    seller_match = re.search(
        r"/seller/([^/]+)/",
        final,
        re.IGNORECASE,
    )
    if not seller_match:
        raise OzonSourceVerificationError(
            "The final Ozon URL is not a seller storefront."
        )

    seller_id = seller_match.group(1)

    # Product evidence is the actual verification contract.
    # parse_catalog_html accepts seller catalogue grids and explicitly
    # rejects recommendation grids.
    from collectors.ozon.ozon_probe_core import parse_catalog_html

    grid_scan: dict[str, Any] = {}
    products, _next_page = parse_catalog_html(
        str(page_html or ""),
        final,
        grid_scan,
    )

    if not products:
        raise OzonSourceVerificationError(
            "Ozon seller storefront has no verified catalogue products."
        )

    # Name is presentation metadata only. It must never block connection.
    seller_name = ""

    name_match = re.search(
        r"<h1[^>]*>\s*([^<]{2,160})\s*</h1>",
        str(page_html or ""),
        re.IGNORECASE,
    )
    if name_match:
        seller_name = re.sub(
            r"\s+",
            " ",
            name_match.group(1),
        ).strip()

    if not seller_name:
        name_match = re.search(
            r'"(?:sellerName|seller_name)"\s*:\s*"([^"\\]{2,160})"',
            str(page_html or ""),
            re.IGNORECASE,
        )
        if name_match:
            seller_name = name_match.group(1).strip()

    # The proof/connect pipeline expects a non-empty display identity.
    # If Ozon does not expose a name, use the verified seller slug.
    if not seller_name:
        seller_name = seller_id

    first_product = products[0] if products else {}

    return {
        "verification_state": "verified",
        "marketplace_code": code,
        "canonical_seller_id": seller_id,
        "canonical_seller_url": canonical,
        "seller_name": seller_name,
        "catalogue_empty": False,
        "product_count": len(products),
        "sample_product_id": str(first_product.get("article") or ""),
        "sample_product_url": str(first_product.get("url") or ""),
    }


def _interactive_snapshot(
    marketplace_code: str,
    source_url: str,
) -> dict[str, Any]:
    """Wait for a real Ozon seller catalogue in the existing debug browser."""

    import time

    root = Path(__file__).resolve().parent
    collector_dir = root / "collectors" / "ozon"

    if str(collector_dir) not in sys.path:
        sys.path.insert(0, str(collector_dir))

    from browser_session import BrowserSession  # type: ignore[import-not-found]
    from collectors.ozon.ozon_probe_core import parse_catalog_html

    port = 9222 if marketplace_code == "ozon" else 9333
    session = BrowserSession(port, source_url)

    driver = None
    original_handle = ""
    original_handles: set[str] = set()
    temporary_handle = ""

    try:
        session.connect()

        assert session.driver is not None
        driver = session.driver

        original_handle = str(driver.current_window_handle)
        original_handles = {
            str(handle)
            for handle in driver.window_handles
        }

        driver.switch_to.new_window("tab")
        temporary_handle = str(driver.current_window_handle)

        # BrowserSession uses page_load_strategy="none", therefore driver.get()
        # intentionally returns before Ozon's SPA has populated the catalogue.
        driver.get(source_url)

        started = time.monotonic()
        deadline = started + 15.0
        latest_snapshot: dict[str, Any] = {}

        while True:
            title, text, page_html = session.snapshot()
            final_url = str(driver.current_url or source_url)

            grid_scan: dict[str, Any] = {}

            try:
                products, _next_page = parse_catalog_html(
                    page_html,
                    final_url,
                    grid_scan,
                )
            except Exception:
                products = []

            latest_snapshot = {
                "final_url": final_url,
                "page_title": title,
                "page_text": text,
                "page_html": page_html,
                "product_count": len(products),
                "grid_scan": grid_scan,
                "verification_wait_ms": int(
                    (time.monotonic() - started) * 1000
                ),
            }

            # Stop immediately when an actual seller catalogue product appears.
            if products:
                return latest_snapshot

            if time.monotonic() >= deadline:
                return latest_snapshot

            # Ozon may lazy-initialize storefront widgets only after the page
            # gets a small real scroll. Do not jump to the page bottom because
            # recommendation widgets live there as well.
            try:
                driver.execute_script(
                    """
                    const root =
                        document.scrollingElement
                        || document.documentElement
                        || document.body;

                    if (root) {
                        const target = Math.min(
                            900,
                            Math.max(
                                0,
                                (root.scrollHeight || 0) * 0.18
                            )
                        );
                        root.scrollTo(0, target);
                    }
                    """
                )
            except Exception:
                pass

            time.sleep(0.5)

    finally:
        if driver is not None:
            try:
                handles = {
                    str(handle)
                    for handle in driver.window_handles
                }

                if (
                    temporary_handle
                    and temporary_handle not in original_handles
                    and temporary_handle in handles
                ):
                    driver.switch_to.window(temporary_handle)
                    driver.close()

            except Exception:
                pass

            try:
                handles = {
                    str(handle)
                    for handle in driver.window_handles
                }

                restore_handle = (
                    original_handle
                    if original_handle in handles
                    else next(
                        (
                            handle
                            for handle in original_handles
                            if handle in handles
                        ),
                        "",
                    )
                )

                if restore_handle:
                    driver.switch_to.window(restore_handle)

            except Exception:
                pass

        # Detach only. Never close the shared Ozon browser.
        session.driver = None

def verify_ozon_storefront(
    marketplace_code: str,
    source_url: str,
    *,
    snapshot_fetcher: Callable[[str, str], dict[str, str]] | None = None,
) -> dict[str, str]:
    """Resolve and verify an Ozon seller source via the interactive runtime."""
    fetcher = snapshot_fetcher or _interactive_snapshot
    try:
        snapshot = fetcher(str(marketplace_code), str(source_url))
    except OzonSourceVerificationError:
        raise
    except Exception as exc:
        raise OzonSourceVerificationError(
            "Ozon storefront verification requires the existing interactive browser runtime."
        ) from exc
    return resolve_ozon_snapshot(
        marketplace_code,
        final_url=str(snapshot.get("final_url") or ""),
        page_html=str(snapshot.get("page_html") or ""),
        page_text=str(snapshot.get("page_text") or ""),
        page_title=str(snapshot.get("page_title") or ""),
    )
