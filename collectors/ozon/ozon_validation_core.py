#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any


def normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def seller_match_status(item: dict[str, Any], expected: str) -> str:
    if not expected:
        return "NOT_CONFIGURED"
    seller_name = normalized_text(item.get("seller_name"))
    seller_id = normalized_text(item.get("seller_id"))
    seller_slug = normalized_text(item.get("seller_slug"))
    expected_n = normalized_text(expected)
    if not seller_name and not seller_id and not seller_slug:
        return "UNKNOWN"
    if expected_n in {seller_name, seller_id, seller_slug}:
        return "MATCH"
    if expected_n and (expected_n in seller_name or expected_n in seller_slug):
        return "MATCH"
    return "MISMATCH"


def _norm(value: object) -> str:
    return re.sub(r"[^0-9a-zа-я]+", " ", str(value or "").casefold()).strip()


def _compact(value: object) -> str:
    return re.sub(r"[^0-9a-zа-я]+", "", str(value or "").casefold())


def _characteristics(item: dict[str, Any]) -> dict[str, str]:
    value = item.get("characteristics")
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _first_char(chars: dict[str, str], *names: str) -> str:
    wanted = {_norm(name) for name in names}
    for key, value in chars.items():
        if _norm(key) in wanted and str(value).strip():
            return str(value).strip()
    return ""


def extract_tire_identity(item: dict[str, Any]) -> dict[str, Any]:
    title = str(item.get("name") or item.get("title") or "")
    chars = _characteristics(item)

    brand = str(item.get("brand") or "").strip()
    model = str(item.get("model") or _first_char(chars, "Серия", "Модель", "Модель шины")).strip()

    width = str(item.get("width_mm") or _first_char(chars, "Ширина профиля, мм")).strip()
    profile = str(item.get("profile_percent") or _first_char(chars, "Высота профиля, %")).strip()
    diameter = str(item.get("diameter_inch") or _first_char(chars, "Диаметр, дюймы")).strip()
    tire_size = str(item.get("tire_size") or "").strip()

    size_match = re.search(r"\b(\d{3})\s*/\s*(\d{2,3})\s*[RР]\s*(\d{2})\b", title, re.I)
    if size_match:
        width = width or size_match.group(1)
        profile = profile or size_match.group(2)
        diameter = diameter or size_match.group(3)
    if not tire_size and width and profile and diameter:
        tire_size = f"{width}/{profile} R{diameter}"

    load_index = str(item.get("load_index") or "").strip()
    speed_index = str(item.get("speed_index") or "").strip().upper()
    load_speed_match = re.search(
        r"\b\d{3}\s*/\s*\d{2,3}\s*[RР]\s*\d{2}\s*(?:XL\s*)?(\d{2,3})\s*([A-Z])\b",
        title.upper(),
    )
    if not load_speed_match:
        load_speed_match = re.search(r"\b(\d{2,3})\s*([A-Z])\b", title.upper())
    if load_speed_match:
        load_index = load_index or load_speed_match.group(1)
        speed_index = speed_index or load_speed_match.group(2)

    lower = title.casefold()
    if "всесезон" in lower:
        season = "ALL_SEASON"
    elif "зимн" in lower:
        season = "WINTER"
    elif "летн" in lower:
        season = "SUMMER"
    else:
        season = str(item.get("season") or "UNKNOWN").upper()

    if "нешипован" in lower:
        studded = False
    elif "шипован" in lower or re.search(r"\bшипы\b", lower):
        studded = True
    else:
        studded = item.get("studded") if isinstance(item.get("studded"), bool) else None

    xl = bool(re.search(r"(?<![A-ZА-Я0-9])XL(?![A-ZА-Я0-9])", title.upper()))
    runflat = bool(re.search(r"\b(?:RUN\s*FLAT|RUNFLAT|RFT|SSR|ZP)\b", title.upper()))

    manufacturer_article = str(
        item.get("manufacturer_article")
        or _first_char(chars, "Партномер (артикул производителя)", "Артикул производителя")
        or ""
    ).strip()

    parts = [
        _compact(brand).upper(),
        _compact(model).upper(),
        _compact(tire_size).upper(),
        load_index.upper(),
        speed_index.upper(),
        season,
        "STUD" if studded is True else "FRICTION" if studded is False else "STUD_UNKNOWN",
        "XL" if xl else "STD_LOAD",
        "RUNFLAT" if runflat else "STANDARD",
    ]
    product_identity_key = "|".join(parts)

    points = 0
    points += 15 if brand else 0
    points += 15 if model else 0
    points += 30 if tire_size else 0
    points += 10 if load_index else 0
    points += 5 if speed_index else 0
    points += 10 if season != "UNKNOWN" else 0
    points += 5 if studded is not None else 0
    points += 10 if manufacturer_article else 0

    return {
        "brand": brand,
        "model": model,
        "manufacturer_article": manufacturer_article,
        "tire_size": tire_size,
        "width_mm": width,
        "profile_percent": profile,
        "diameter_inch": diameter,
        "load_index": load_index,
        "speed_index": speed_index,
        "season": season,
        "studded": studded,
        "xl": xl,
        "runflat": runflat,
        "product_identity_key": product_identity_key,
        "identity_completeness_percent": min(points, 100),
    }


def _lowest_current_price(*values: Any) -> int:
    prices: list[int] = []
    for value in values:
        try:
            price = int(value or 0)
        except (TypeError, ValueError):
            continue
        if price > 0:
            prices.append(price)
    return min(prices) if prices else 0


def normalize_for_import(item: dict[str, Any], collected_at: str, run_id: str) -> dict[str, Any]:
    identity = extract_tire_identity(item)
    card_price = int(item.get("card_price") or 0)
    regular_price = int(item.get("regular_price") or 0)
    catalog_price = int(item.get("catalog_card_price") or 0)
    # Ozon can expose two simultaneously payable prices for one seller:
    # the Ozon-bank/card price and the price for other banks. Comparisons use
    # the lower current price; original_price is a crossed-out reference only.
    price = _lowest_current_price(card_price, regular_price) or catalog_price
    return {
        "source": str(item.get("source") or "ozon_ru"),
        "source_product_id": str(item.get("article") or ""),
        "source_seller_id": str(item.get("seller_id") or ""),
        "source_seller_name": str(item.get("seller_name") or ""),
        "source_url": str(item.get("url") or ""),
        "title": str(item.get("name") or ""),
        "brand": identity["brand"],
        "model": identity["model"],
        "manufacturer_article": identity["manufacturer_article"],
        "tire_size": identity["tire_size"],
        "width_mm": identity["width_mm"],
        "profile_percent": identity["profile_percent"],
        "diameter_inch": identity["diameter_inch"],
        "load_index": identity["load_index"],
        "speed_index": identity["speed_index"],
        "season": identity["season"],
        "studded": identity["studded"],
        "xl": identity["xl"],
        "runflat": identity["runflat"],
        "product_identity_key": identity["product_identity_key"],
        "identity_completeness_percent": identity["identity_completeness_percent"],
        "price": price,
        "catalog_price": catalog_price,
        "regular_price": regular_price,
        "original_price": int(item.get("original_price") or 0),
        "price_difference_catalog_vs_pdp": item.get("price_difference_catalog_vs_pdp"),
        "price_source": (
            "PDP_LOWEST_CURRENT" if card_price > 0 and regular_price > 0
            else "PDP_CARD" if card_price > 0
            else "PDP_REGULAR" if regular_price > 0
            else "CATALOG_CARD"
        ),
        "currency": str(item.get("currency") or "RUB"),
        "image_url": str(item.get("image_url") or ""),
        "seller_rating": item.get("seller_rating"),
        "product_rating": item.get("rating"),
        "review_count": item.get("review_count"),
        "location_city": str(item.get("location_city") or ""),
        "location_country": str(item.get("location_country") or ""),
        "availability_status": str(item.get("availability_status") or "UNKNOWN"),
        "seller_match_status": str(item.get("seller_match_status") or "NOT_CONFIGURED"),
        "detail_status": str(item.get("detail_status") or ""),
        "overall_status": str(item.get("overall_status") or ""),
        "collected_at": collected_at,
        "run_id": run_id,
        "import_ready": bool(
            item.get("success")
            and price > 0
            and item.get("article")
            and identity["brand"]
            and identity["tire_size"]
        ),
    }


def score_product(item: dict[str, Any], target: dict[str, Any]) -> tuple[int, list[str]]:
    item_i = extract_tire_identity(item)
    target_i = extract_tire_identity({
        **target,
        "name": target.get("name") or target.get("title") or "",
    })
    reasons: list[str] = []

    critical_pairs = (
        ("brand", 15),
        ("model", 20),
        ("tire_size", 30),
        ("season", 8),
    )
    for field, _ in critical_pairs:
        left_raw = str(item_i.get(field) or "")
        right_raw = str(target_i.get(field) or "")
        if field == "season":
            if left_raw == "UNKNOWN":
                left_raw = ""
            if right_raw == "UNKNOWN":
                right_raw = ""
        left = _compact(left_raw)
        right = _compact(right_raw)
        if left and right and left != right:
            return 0, [f"conflict_{field}"]

    if (
        item_i["studded"] is not None
        and target_i["studded"] is not None
        and item_i["studded"] != target_i["studded"]
    ):
        return 0, ["conflict_studded"]

    score = 0
    target_article = _compact(target_i.get("manufacturer_article"))
    item_article = _compact(item_i.get("manufacturer_article"))
    if target_article and item_article and target_article == item_article:
        score += 60
        reasons.append("manufacturer_article_exact")

    for field, points in critical_pairs:
        left_raw = str(item_i.get(field) or "")
        right_raw = str(target_i.get(field) or "")
        if field == "season":
            if left_raw == "UNKNOWN":
                left_raw = ""
            if right_raw == "UNKNOWN":
                right_raw = ""
        left = _compact(left_raw)
        right = _compact(right_raw)
        if left and right and left == right:
            score += points
            reasons.append(f"{field}_exact")

    for field, points in (("load_index", 8), ("speed_index", 5)):
        left = _compact(item_i.get(field))
        right = _compact(target_i.get(field))
        if left and right and left == right:
            score += points
            reasons.append(f"{field}_exact")

    if item_i["studded"] is not None and target_i["studded"] is not None:
        score += 5
        reasons.append("studded_exact")

    target_tokens = {x for x in _norm(target.get("name") or target.get("title")).split() if len(x) > 2}
    item_tokens = {x for x in _norm(item.get("name") or item.get("title")).split() if len(x) > 2}
    if target_tokens and item_tokens:
        overlap = len(target_tokens & item_tokens) / len(target_tokens)
        name_points = min(10, round(overlap * 10))
        score += name_points
        if name_points:
            reasons.append(f"name_overlap_{name_points}")

    return min(score, 100), reasons


def match_level(score: int) -> str:
    if score >= 90:
        return "EXACT"
    if score >= 75:
        return "STRONG"
    if score >= 55:
        return "CANDIDATE"
    return "NO_MATCH"
