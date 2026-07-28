from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

GENERIC_TOKENS = {
    "шина", "шины", "автошина", "автошины", "мотошина", "мотошины",
    "летняя", "летние", "зимняя", "зимние", "всесезонная", "всесезонные",
    "шипованные", "нешипованные", "шипованная", "нешипованная", "front", "rear",
    "для", "автомобиля", "коммерческого", "транспорта", "xl", "runflat", "rft",
    "m", "s", "8pr", "6pr", "4pr", "12pr",
}


def compact(value: Any) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", str(value or "").casefold().replace("ё", "е"))


def words(value: Any) -> list[str]:
    return re.findall(r"[a-zа-я0-9]+", str(value or "").casefold().replace("ё", "е"))


def canonical_size(value: Any, title: Any = "") -> str:
    text = f"{value or ''} {title or ''}".upper().replace("ZR", "R")
    match = re.search(r"(?<!\d)(\d{3})\s*/\s*(\d{2})\s*R\s*(\d{2})(C)?(?!\d)", text)
    if match:
        return f"{match.group(1)}/{match.group(2)}R{match.group(3)}{'C' if match.group(4) else ''}"
    match = re.search(r"(?<!\d)(\d(?:[.,]\d{2}))\s*/?\s*R\s*(\d{1,2})(?!\d)", text)
    if match:
        return f"{match.group(1).replace(',', '.')}R{match.group(2)}"
    return compact(value)


def canonical_season(value: Any, title: Any = "") -> str:
    text = f"{value or ''} {title or ''}".casefold()
    if "всесез" in text or "all season" in text or "allseason" in text:
        return "ALL_SEASON"
    if "зим" in text or "winter" in text:
        return "WINTER"
    if "лет" in text or "summer" in text:
        return "SUMMER"
    return "UNKNOWN"


def infer_product_type(row: dict[str, Any]) -> str:
    text = str(row.get("title") or "").casefold()
    if "мотокамера" in text or ("камера" in text and "мото" in text):
        return "motorcycle_tube"
    if "мотошин" in text or "мото шин" in text:
        return "motorcycle_tire"
    size = canonical_size(row.get("tire_size"), row.get("title"))
    if "коммерческ" in text or size.endswith("C"):
        return "commercial_tire"
    if "грузов" in text or "для груз" in text:
        return "truck_tire"
    if "шин" in text or size:
        return "passenger_tire"
    return "other"


def model_tokens(row: dict[str, Any]) -> list[str]:
    brand_tokens = set(words(row.get("brand")))
    raw = str(row.get("model") or "").strip() or str(row.get("title") or "")
    result: list[str] = []
    seen: set[str] = set()
    for token in words(raw):
        if token in brand_tokens or token in GENERIC_TOKENS or token in seen:
            continue
        if re.fullmatch(r"\d{3}", token) or re.fullmatch(r"\d{2}", token):
            continue
        if re.fullmatch(r"r\d{1,2}c?", token) or re.fullmatch(r"\d{2,3}[a-z]", token):
            continue
        if len(token) < 2:
            continue
        seen.add(token)
        result.append(token)
    return result[:12]


def model_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_compact = compact(left.get("model"))
    right_compact = compact(right.get("model"))
    if left_compact and right_compact:
        if left_compact == right_compact:
            return 1.0
        if min(len(left_compact), len(right_compact)) >= 5 and (
            left_compact in right_compact or right_compact in left_compact
        ):
            return 0.92
        sequence = SequenceMatcher(None, left_compact, right_compact).ratio()
    else:
        sequence = 0.0
    left_tokens, right_tokens = set(model_tokens(left)), set(model_tokens(right))
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens) if left_tokens and right_tokens else 0.0
    return max(sequence, jaccard)


def build_search_queries(product: dict[str, Any]) -> list[str]:
    brand = str(product.get("brand") or "").strip()
    model = str(product.get("model") or "").strip()
    size = str(product.get("tire_size") or "").strip()
    manufacturer = str(product.get("manufacturer_article") or "").strip()
    values = []
    if brand and manufacturer:
        values.append(f"{brand} {manufacturer}")
    if brand and model and size:
        values.append(f"{brand} {model} {size}")
    if brand and size:
        values.append(f"{brand} {size}")
    result=[]
    for value in values:
        normalized=" ".join(value.split())
        if normalized and normalized.casefold() not in {x.casefold() for x in result}:
            result.append(normalized)
    return result[:3]


def evaluate_match(owner: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    owner_article = str(owner.get("article") or "")
    candidate_article = str(candidate.get("article") or "")
    if owner_article and owner_article == candidate_article:
        return {"accepted": False, "level": "REJECTED", "score": 0, "method": "SAME_ARTICLE", "reason": "Та же карточка клиента", "reasons": []}

    owner_type, candidate_type = infer_product_type(owner), infer_product_type(candidate)
    if owner_type != candidate_type:
        return {"accepted": False, "level": "REJECTED", "score": 0, "method": "TYPE_CONFLICT", "reason": "Разный тип товара", "reasons": [owner_type, candidate_type]}

    owner_brand, candidate_brand = compact(owner.get("brand")), compact(candidate.get("brand"))
    if not owner_brand or not candidate_brand or owner_brand != candidate_brand:
        return {"accepted": False, "level": "REJECTED", "score": 0, "method": "BRAND_CONFLICT", "reason": "Бренд не совпадает", "reasons": []}
    reasons.append("бренд")

    owner_size = canonical_size(owner.get("tire_size"), owner.get("title"))
    candidate_size = canonical_size(candidate.get("tire_size"), candidate.get("title"))
    if not owner_size or not candidate_size or owner_size != candidate_size:
        return {"accepted": False, "level": "REJECTED", "score": 0, "method": "SIZE_CONFLICT", "reason": "Размер не совпадает", "reasons": [owner_size, candidate_size]}
    reasons.append("размер")

    owner_season = canonical_season(owner.get("season"), owner.get("title"))
    candidate_season = canonical_season(candidate.get("season"), candidate.get("title"))
    if owner_season != "UNKNOWN" and candidate_season != "UNKNOWN" and owner_season != candidate_season:
        return {"accepted": False, "level": "REJECTED", "score": 0, "method": "SEASON_CONFLICT", "reason": "Сезонность не совпадает", "reasons": [owner_season, candidate_season]}
    if owner_season != "UNKNOWN" and candidate_season == owner_season:
        reasons.append("сезон")

    owner_studded, candidate_studded = owner.get("studded"), candidate.get("studded")
    if owner_studded is not None and candidate_studded is not None and int(owner_studded) != int(candidate_studded):
        return {"accepted": False, "level": "REJECTED", "score": 0, "method": "STUD_CONFLICT", "reason": "Шипованность не совпадает", "reasons": []}

    mfr_left, mfr_right = compact(owner.get("manufacturer_article")), compact(candidate.get("manufacturer_article"))
    if mfr_left and mfr_right and mfr_left == mfr_right:
        return {"accepted": True, "level": "EXACT", "score": 100, "method": "MANUFACTURER_ARTICLE", "reason": "Совпали бренд, размер и артикул производителя", "reasons": reasons + ["артикул производителя"]}

    similarity = model_similarity(owner, candidate)
    load_left, load_right = compact(owner.get("load_index")), compact(candidate.get("load_index"))
    speed_left, speed_right = compact(owner.get("speed_index")), compact(candidate.get("speed_index"))
    index_conflict = bool((load_left and load_right and load_left != load_right) or (speed_left and speed_right and speed_left != speed_right))

    if similarity >= 0.9 and not index_conflict:
        return {"accepted": True, "level": "EXACT", "score": 96, "method": "BRAND_MODEL_SIZE", "reason": "Совпали бренд, модель и размер", "reasons": reasons + ["модель"]}
    if similarity >= 0.58 and not index_conflict:
        return {"accepted": True, "level": "STRONG", "score": round(78 + similarity * 12, 1), "method": "BRAND_SIZE_MODEL_SIMILAR", "reason": "Совпали бренд и размер, модель близка", "reasons": reasons + ["близкая модель"]}

    reason = "Совпали бренд и размер"
    if index_conflict:
        reason += ", но отличаются индексы"
    elif similarity > 0:
        reason += ", модель отличается"
    return {"accepted": True, "level": "COMPARABLE", "score": round(60 + similarity * 10, 1), "method": "BRAND_SIZE", "reason": reason, "reasons": reasons}
