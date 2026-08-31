#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import html
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse


def parse_price(value: Any) -> int:
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits else 0


def normalize_product_url(value: str, base_url: str = "https://www.ozon.ru/") -> str:
    if not value:
        return ""
    absolute = urljoin(base_url, value)
    parsed = urlparse(absolute)
    host = parsed.netloc.lower().split(":")[0]
    base_host = str(urlparse(base_url).hostname or "www.ozon.ru").casefold()
    bare_host = base_host.removeprefix("www.")
    allowed_hosts = {bare_host, f"www.{bare_host}"}
    if host not in allowed_hosts or "/product/" not in parsed.path:
        return ""
    canonical_host = "www.ozon.ru" if bare_host == "ozon.ru" else bare_host
    return urlunparse(("https", canonical_host, parsed.path, "", "", ""))


def article_from_url(url: str) -> str:
    path = urlparse(url).path
    match = re.search(r"-(\d+)/?$", path)
    if match:
        return match.group(1)
    match = re.search(r"/product/(\d+)/?", path)
    return match.group(1) if match else ""


def extract_state_divs(page_html: str, state_prefix: str) -> list[dict[str, Any]]:
    pattern = re.compile(
        rf'<div\s+id="(state-{re.escape(state_prefix)}[^"]*)"\s+data-state="([^"]*)"',
        flags=re.IGNORECASE,
    )
    result: list[dict[str, Any]] = []
    for state_id, raw in pattern.findall(page_html or ""):
        try:
            decoded = html.unescape(raw)
            parsed = json.loads(decoded)
            if isinstance(parsed, dict):
                parsed["_spyon_state_id"] = state_id
                result.append(parsed)
        except Exception:
            continue
    return result


def _extract_text_from_main_state(item: dict[str, Any]) -> str:
    states = item.get("mainState")
    if not isinstance(states, list):
        return ""

    explicit = ""
    fallback: list[str] = []
    for block in states:
        if not isinstance(block, dict) or block.get("type") != "textDS":
            continue
        payload = block.get("textDS")
        if not isinstance(payload, dict):
            continue
        value = str(payload.get("text") or "").strip()
        if not value:
            continue
        if block.get("id") == "name":
            explicit = value
            break
        fallback.append(value)

    if explicit:
        return explicit

    rejected = (
        "осталось",
        "распродажа",
        "хит",
        "новинка",
        "завтра",
        "послезавтра",
        "июля",
        "августа",
    )
    candidates = [v for v in fallback if len(v) >= 12 and not any(r in v.lower() for r in rejected)]
    return max(candidates, key=len) if candidates else ""


def _extract_tile_price(item: dict[str, Any]) -> tuple[int, list[int], str]:
    states = item.get("mainState")
    if not isinstance(states, list):
        return 0, [], ""

    prices: list[int] = []
    selected_price = 0
    selected_style = ""
    for block in states:
        if not isinstance(block, dict) or block.get("type") != "priceV2":
            continue
        payload = block.get("priceV2")
        if not isinstance(payload, dict):
            continue
        style = str((payload.get("priceStyle") or {}).get("styleType") or "")
        for price_obj in payload.get("price") or []:
            if isinstance(price_obj, dict):
                text = str(price_obj.get("text") or "")
                # Ozon.kz often renders the instalment first: "263 ₸ × 12 мес".
                # Stripping non-digits turns that into the false price 26312.
                # Keep only full current/original prices in catalogue analytics.
                if re.search(r"(?:×|x)\s*\d+\s*(?:мес|month)", text, re.IGNORECASE):
                    continue
                value = parse_price(text)
                if value:
                    prices.append(value)
                    text_style = str(price_obj.get("textStyle") or "").upper()
                    if not selected_price and text_style != "ORIGINAL_PRICE":
                        selected_price = value
                        selected_style = style
    return (selected_price or (prices[0] if prices else 0)), prices, selected_style


def _extract_tile_image(item: dict[str, Any]) -> str:
    def find_image(value: Any) -> str:
        if isinstance(value, dict):
            for key in ("link", "url", "src", "imageUrl", "image_url"):
                raw = value.get(key)
                if isinstance(raw, str) and raw.startswith(("http://", "https://", "//")):
                    return "https:" + raw if raw.startswith("//") else raw
            for child in value.values():
                found = find_image(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find_image(child)
                if found:
                    return found
        return ""

    for key in ("tileImage", "image", "images", "gallery"):
        found = find_image(item.get(key))
        if found:
            return found
    return ""


def _seller_identifier_from_catalog_url(value: str) -> str:
    """Return a seller path token only for an actual seller storefront URL."""
    match = re.search(r"/seller/([^/?#]+)", urlparse(str(value or "")).path, re.I)
    return match.group(1).casefold() if match else ""


def parse_catalog_html(page_html: str, base_url: str) -> tuple[list[dict[str, Any]], str]:
    products: list[dict[str, Any]] = []
    expected_seller_id = _seller_identifier_from_catalog_url(base_url)
    accepted_catalogue_grid = False

    grids = extract_state_divs(page_html, "tileGridDesktop")
    recommendation_markers = (
        "возможно, вам понравится", "рекомендуем", "популярное",
        "с этим товаром покупают", "recommend", "popular",
    )
    for grid in grids:
        # Widget state carries its own label/metadata.  A recommendation grid
        # is never a seller catalogue source, even if it contains valid Ozon
        # product URLs.
        grid_context = json.dumps(grid, ensure_ascii=False).casefold()
        if any(marker in grid_context for marker in recommendation_markers):
            continue
        # Product grids are reused by Ozon for cross-sell widgets.  On a seller
        # storefront, require affirmative evidence that the widget belongs to
        # that seller; an unscoped tile grid is not safe catalogue evidence.
        if expected_seller_id and expected_seller_id not in grid_context:
            continue
        items = grid.get("items")
        if not isinstance(items, list):
            continue
        accepted_catalogue_grid = True
        for item in items:
            if not isinstance(item, dict):
                continue
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            url = normalize_product_url(str(action.get("link") or ""), base_url)
            article = str(item.get("sku") or item.get("id") or article_from_url(url))
            if not article or not url:
                continue

            card_price, all_prices, price_style = _extract_tile_price(item)
            products.append(
                {
                    "article": article,
                    "name": _extract_text_from_main_state(item),
                    "catalog_card_price": card_price,
                    "catalog_all_prices": all_prices,
                    "catalog_price_style": price_style,
                    "image_url": _extract_tile_image(item),
                    "url": url,
                }
            )

    next_page = ""
    paginators = extract_state_divs(page_html, "infiniteVirtualPaginator")
    if accepted_catalogue_grid and paginators:
        next_page = str(paginators[-1].get("nextPage") or "")
        if next_page:
            next_page = urljoin(base_url, html.unescape(next_page))

    unique: dict[str, dict[str, Any]] = {}
    for product in products:
        unique[product["article"]] = product

    return list(unique.values()), next_page


def _iter_widget_states(data: dict[str, Any]):
    states = data.get("widgetStates")
    if not isinstance(states, dict):
        return
    for key, raw in states.items():
        if not isinstance(raw, str):
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        if isinstance(parsed, dict):
            yield key, parsed


def _extract_brand(data: dict[str, Any]) -> str:
    seo = data.get("seo")
    if not isinstance(seo, dict):
        return ""
    scripts = seo.get("script")
    if not isinstance(scripts, list):
        return ""
    for entry in scripts:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("type") or "") != "application/ld+json":
            continue
        try:
            payload = json.loads(str(entry.get("innerHTML") or ""))
        except Exception:
            continue
        if isinstance(payload, dict) and payload.get("brand"):
            return str(payload.get("brand"))
    return ""


def _extract_rating(states: list[tuple[str, dict[str, Any]]]) -> tuple[float | None, int | None]:
    for key, obj in states:
        if not key.startswith("webSingleProductScore-"):
            continue
        text = str(obj.get("text") or "")
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*[•·]\s*(\d+)", text)
        if match:
            return float(match.group(1).replace(",", ".")), int(match.group(2))
    return None, None


def _extract_characteristics(states: list[tuple[str, dict[str, Any]]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, obj in states:
        if not key.startswith("webShortCharacteristics-"):
            continue
        for ch in obj.get("characteristics") or []:
            if not isinstance(ch, dict):
                continue
            title = ""
            title_obj = ch.get("title")
            if isinstance(title_obj, dict):
                for part in title_obj.get("textRs") or []:
                    if isinstance(part, dict) and part.get("content"):
                        title = str(part.get("content")).strip()
                        break
            values: list[str] = []
            for value in ch.get("values") or []:
                if isinstance(value, dict) and value.get("text") is not None:
                    values.append(str(value.get("text")).strip())
            if title and values:
                result[title] = ", ".join(v for v in values if v)
    return result



def _find_first_value(obj: Any, key_name: str) -> Any:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == key_name and value not in (None, ""):
                return value
            found = _find_first_value(value, key_name)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_first_value(value, key_name)
            if found not in (None, ""):
                return found
    return None


def _extract_seller_rating(current_seller: dict[str, Any]) -> float | None:
    rating = current_seller.get("rating")
    if not isinstance(rating, dict):
        return None
    title = rating.get("title")
    if isinstance(title, dict):
        text = str(title.get("text") or "").replace(",", ".")
        match = re.search(r"\d+(?:\.\d+)?", text)
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def _characteristic_value(characteristics: dict[str, str], *names: str) -> str:
    lowered = {str(k).strip().lower(): str(v).strip() for k, v in characteristics.items()}
    for name in names:
        value = lowered.get(name.strip().lower())
        if value:
            return value
    return ""

def parse_product_json(
    article: str,
    data: dict[str, Any],
    catalog_product: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog_product = catalog_product or {}
    states = list(_iter_widget_states(data) or [])

    sticky: dict[str, Any] = {}
    heading: dict[str, Any] = {}
    price_data: dict[str, Any] = {}
    current_seller: dict[str, Any] = {}
    out_of_stock: dict[str, Any] = {}

    for key, obj in states:
        if key.startswith("webStickyProducts-"):
            sticky = obj
        elif key.startswith("webProductHeading-"):
            heading = obj
        elif key.startswith("webPrice-"):
            price_data = obj
        elif key.startswith("webCurrentSeller-"):
            current_seller = obj
        elif key.startswith("webOutOfStock-"):
            if str(obj.get("sku") or "").strip() == str(article).strip():
                out_of_stock = obj

    seller = sticky.get("seller") if isinstance(sticky.get("seller"), dict) else {}
    seller_name = str(seller.get("name") or "")
    seller_link = str(seller.get("link") or "")
    if out_of_stock:
        seller_name = str(out_of_stock.get("sellerName") or seller_name)
        seller_link = str(out_of_stock.get("sellerLink") or seller_link)

    seller_cell = current_seller.get("sellerCell")
    if isinstance(seller_cell, dict):
        center = seller_cell.get("centerBlock")
        if isinstance(center, dict):
            title = center.get("title")
            if isinstance(title, dict) and title.get("text"):
                seller_name = str(title.get("text"))
        common = seller_cell.get("common")
        if isinstance(common, dict):
            action = common.get("action")
            if isinstance(action, dict) and action.get("link"):
                seller_link = str(action.get("link"))

    seller_slug = ""
    seller_id = ""
    match = re.search(r"/seller/([^/?#]+)/?", seller_link)
    if match:
        seller_slug = match.group(1)
        id_match = re.search(r"(\d+)$", seller_slug)
        if id_match:
            seller_id = id_match.group(1)

    # В новых ответах Ozon sellerId часто хранится не в URL, а в params
    # действия подписки внутри webCurrentSeller.
    nested_seller_id = _find_first_value(current_seller, "sellerId")
    if nested_seller_id not in (None, ""):
        seller_id = str(nested_seller_id)

    name = str(
        heading.get("title")
        or sticky.get("name")
        or out_of_stock.get("skuName")
        or catalog_product.get("name")
        or ""
    )
    image_url = str(
        sticky.get("coverImageUrl")
        or catalog_product.get("image_url")
        or ""
    )

    rating, review_count = _extract_rating(states)
    characteristics = _extract_characteristics(states)
    width_mm = _characteristic_value(characteristics, "Ширина профиля, мм")
    profile_percent = _characteristic_value(characteristics, "Высота профиля, %")
    diameter_inch = _characteristic_value(characteristics, "Диаметр, дюймы")
    manufacturer_article = _characteristic_value(
        characteristics,
        "Партномер (артикул производителя)",
        "Артикул производителя",
    )
    tire_size = ""
    if width_mm and profile_percent and diameter_inch:
        tire_size = f"{width_mm}/{profile_percent} R{diameter_inch}"

    page_info = data.get("pageInfo") if isinstance(data.get("pageInfo"), dict) else {}
    location = data.get("location") if isinstance(data.get("location"), dict) else {}
    current_location = location.get("current") if isinstance(location.get("current"), dict) else {}

    card_price = parse_price(price_data.get("cardPrice"))
    regular_price = parse_price(price_data.get("price"))
    original_price = parse_price(price_data.get("originalPrice"))
    if not card_price and not regular_price and out_of_stock:
        regular_price = parse_price(out_of_stock.get("price"))
    catalog_card_price = parse_price(catalog_product.get("catalog_card_price"))

    item = {
        "article": article,
        "name": name,
        "brand": _extract_brand(data),
        "image_url": image_url,
        "seller_name": seller_name,
        "seller_link": seller_link,
        "seller_slug": seller_slug,
        "seller_id": seller_id,
        "seller_rating": _extract_seller_rating(current_seller),
        "catalog_card_price": catalog_card_price,
        "card_price": card_price,
        "ozon_card_price": card_price,
        "regular_price": regular_price,
        "original_price": original_price,
        "price_difference_catalog_vs_pdp": (
            card_price - catalog_card_price if card_price and catalog_card_price else None
        ),
        "rating": rating,
        "review_count": review_count,
        "characteristics": characteristics,
        "tire_size": tire_size,
        "width_mm": width_mm,
        "profile_percent": profile_percent,
        "diameter_inch": diameter_inch,
        "manufacturer_article": manufacturer_article,
        "location_city": str(current_location.get("city") or ""),
        "location_country": str(current_location.get("country") or ""),
        "availability_status": "OUT_OF_STOCK" if out_of_stock else "UNKNOWN",
        "page_type": str(page_info.get("pageType") or ""),
        "url": str(catalog_product.get("url") or ""),
        "success": bool(name and (card_price or regular_price)),
        "error": "",
    }

    if not item["success"]:
        item["error"] = "Не найдены обязательные поля: название и цена"

    return item


def flatten_for_csv(item: dict[str, Any]) -> dict[str, Any]:
    flat = dict(item)
    chars = flat.pop("characteristics", {})
    if isinstance(chars, dict):
        for key, value in chars.items():
            flat[f"characteristic_{key}"] = value
    return flat


def write_csv(path: Path, products: list[dict[str, Any]]) -> None:
    flat_rows = [flatten_for_csv(p) for p in products]
    fields: list[str] = []
    seen: set[str] = set()
    for row in flat_rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fields.append(key)

    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(flat_rows)


class _OzonStateHTMLParser(HTMLParser):
    """Извлекает data-state из div state-* и JSON-LD из product HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.states: dict[str, dict[str, Any]] = {}
        self._capture_json_ld = False
        self._json_ld_parts: list[str] = []
        self.json_ld: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}

        if tag.lower() == "div":
            state_id = attrs_dict.get("id", "")
            raw_state = attrs_dict.get("data-state", "")
            if state_id.startswith("state-") and raw_state:
                key = state_id[len("state-"):]
                try:
                    parsed = json.loads(raw_state)
                    if isinstance(parsed, dict):
                        self.states[key] = parsed
                except Exception:
                    pass

        if (
            tag.lower() == "script"
            and attrs_dict.get("type", "").lower() == "application/ld+json"
        ):
            self._capture_json_ld = True
            self._json_ld_parts = []

    def handle_data(self, data: str) -> None:
        if self._capture_json_ld:
            self._json_ld_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._capture_json_ld:
            raw = "".join(self._json_ld_parts).strip()
            self._capture_json_ld = False
            self._json_ld_parts = []
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        self.json_ld.append(parsed)
                    elif isinstance(parsed, list):
                        self.json_ld.extend(
                            item for item in parsed if isinstance(item, dict)
                        )
                except Exception:
                    pass


def parse_product_html(
    article: str,
    page_html: str,
    catalog_product: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Разбирает настоящую HTML-страницу товара без composer fetch API."""

    parser = _OzonStateHTMLParser()
    parser.feed(page_html or "")

    data: dict[str, Any] = {
        "widgetStates": {
            key: json.dumps(value, ensure_ascii=False)
            for key, value in parser.states.items()
        }
    }

    brand = ""
    for item in parser.json_ld:
        value = item.get("brand")
        if isinstance(value, dict):
            brand = str(value.get("name") or "")
        elif value:
            brand = str(value)
        if brand:
            break

    if brand:
        data["seo"] = {
            "script": [
                {
                    "type": "application/ld+json",
                    "innerHTML": json.dumps({"brand": brand}, ensure_ascii=False),
                }
            ]
        }

    result = parse_product_json(article, data, catalog_product)
    result["detail_source"] = "product_html"
    result["embedded_states_count"] = len(parser.states)
    return result
