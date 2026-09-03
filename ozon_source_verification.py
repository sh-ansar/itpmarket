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
) -> dict[str, str]:
    """Validate independent storefront evidence from an interactive browser.

    A final seller URL, canonical link and seller-specific page data must agree.
    A status code, a seller-looking heading, or a product card alone is not
    evidence and cannot produce ``verified``.
    """
    code = str(marketplace_code or "").strip().casefold()
    final = _canonical_url(final_url, code)
    canonical_raw = _canonical_link(page_html, final_url)
    if not canonical_raw:
        raise OzonSourceVerificationError("Ozon storefront did not expose a canonical seller link.")
    canonical = _canonical_url(canonical_raw, code)
    if canonical != final:
        raise OzonSourceVerificationError("Ozon final URL and canonical seller identity disagree.")
    seller_id = re.search(r"/(?:seller|продавец)/([^/]+)/", canonical).group(1)  # validated above
    evidence = f"{page_title}\n{page_text}\n{page_html}".casefold()
    if seller_id.casefold() not in evidence:
        raise OzonSourceVerificationError("Ozon page has no seller-specific identity evidence.")
    empty_markers = (
        "не нашли товары в магазине",
        "товары в магазине не найдены",
        "no products found in the store",
    )
    is_empty = any(marker in evidence for marker in empty_markers)
    name_match = re.search(
        r"<h1[^>]*>\s*([^<]{2,160})\s*</h1>", str(page_html or ""), re.IGNORECASE
    )
    seller_name = re.sub(r"\s+", " ", name_match.group(1)).strip() if name_match else ""
    if not seller_name:
        # Seller identity survives in the page state on both Ozon locales; do
        # not infer it from recommendation cards.
        name_match = re.search(r'"(?:sellerName|seller_name|name)"\s*:\s*"([^"\\]{2,160})"', page_html)
        seller_name = name_match.group(1).strip() if name_match else ""
    if not seller_name:
        raise OzonSourceVerificationError("Ozon page has no seller name evidence.")
    return {
        "verification_state": "verified",
        "marketplace_code": code,
        "canonical_seller_id": seller_id,
        "canonical_seller_url": canonical,
        "seller_name": seller_name,
        "catalogue_empty": "true" if is_empty else "false",
    }


def _interactive_snapshot(marketplace_code: str, source_url: str) -> dict[str, str]:
    """Use only the existing debug browser; this function never launches Chrome."""
    root = Path(__file__).resolve().parent
    collector_dir = root / "collectors" / "ozon"
    if str(collector_dir) not in sys.path:
        sys.path.insert(0, str(collector_dir))
    from browser_session import BrowserSession  # type: ignore[import-not-found]

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
        original_handles = {str(handle) for handle in driver.window_handles}
        driver.switch_to.new_window("tab")
        temporary_handle = str(driver.current_window_handle)
        driver.get(source_url)
        title, text, page_html = session.snapshot()
        return {
            "final_url": str(driver.current_url or source_url),
            "page_title": title,
            "page_text": text,
            "page_html": page_html,
        }
    finally:
        if driver is not None:
            try:
                handles = {str(handle) for handle in driver.window_handles}
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
                handles = {str(handle) for handle in driver.window_handles}
                restore_handle = (
                    original_handle if original_handle in handles else
                    next((handle for handle in original_handles if handle in handles), "")
                )
                if restore_handle:
                    driver.switch_to.window(restore_handle)
            except Exception:
                pass
        # BrowserSession.close() intentionally navigates its target back to
        # original_url.  The verification tab is already closed above, so a
        # plain detach avoids reloading the user's or collector's active tab.
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
