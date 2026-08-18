from __future__ import annotations

import json
import math
import re
import statistics
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from engine.kaspi_search_compare_v8_2 import extract_attributes, normalize, probable_brand, specs_map


PREMIUM_BRANDS = {
    "continental", "michelin", "bridgestone", "pirelli", "goodyear", "nokian",
    "yokohama", "dunlop", "bfgoodrich", "bf goodrich", "toyo",
}
MID_BRANDS = {
    "hankook", "kumho", "nexen", "falken", "maxxis", "gislaved", "matador",
    "firestone", "general", "cooper", "fulda", "uniroyal", "viatti", "cordiant",
    "petlas", "laufenn", "vredestein", "kama", "нижнекамскшина", "белшина",
}
ECONOMY_BRANDS = {
    "sailun", "triangle", "roadx", "linglong", "centara", "goodride", "westlake",
    "kapsen", "powertrac", "zmax", "ovation", "ilink", "ilink", "royal black",
    "arivo", "compasal", "delmax", "wanli", "nankang", "hifly", "joyroad",
    "goform", "invovic", "comforser", "aplus", "double star", "blackhawk",
    "haida", "sonix", "nereus", "davanti",
}

STATUS_INFO: dict[str, dict[str, str]] = {
    "NOT_ANALYZED": {"label": "Точные предложения не проверены", "tone": "neutral"},
    "NO_OTHER_SELLERS": {"label": "Других продавцов не найдено", "tone": "neutral"},
    "EXACT_LOWEST": {"label": "Единственная минимальная цена среди продавцов", "tone": "success"},
    "EXACT_TIED_LOWEST": {"label": "Делит минимальную цену с другими продавцами", "tone": "info"},
    "EXACT_BELOW": {"label": "Ниже медианы продавцов", "tone": "success"},
    "EXACT_IN_MARKET": {"label": "В рыночном диапазоне", "tone": "info"},
    "EXACT_ABOVE": {"label": "Выше медианы продавцов", "tone": "warning"},
    "EXACT_HIGHEST": {"label": "Единственная максимальная цена среди продавцов", "tone": "danger"},
    "EXACT_TIED_HIGHEST": {"label": "Делит максимальную цену с другими продавцами", "tone": "warning"},
    "INSUFFICIENT_DATA": {"label": "Недостаточно точных предложений", "tone": "neutral"},
    "REVIEW_REQUIRED": {"label": "Требует ручной проверки", "tone": "warning"},
    "DATA_COLLECTED": {"label": "Данные собраны", "tone": "info"},
    "DATA_ERROR": {"label": "Ошибка получения точных предложений", "tone": "danger"},
    "COMPARABLE_LOWEST": {"label": "Ниже сопоставимого рынка", "tone": "success"},
    "COMPARABLE_BELOW": {"label": "Ниже медианы бренда и размера", "tone": "success"},
    "COMPARABLE_IN_MARKET": {"label": "В диапазоне бренда и размера", "tone": "info"},
    "COMPARABLE_ABOVE": {"label": "Выше медианы бренда и размера", "tone": "warning"},
    "COMPARABLE_HIGHEST": {"label": "Выше сопоставимого рынка", "tone": "danger"},
    # Старые статусы оставлены для чтения ранее сохранённых отчётов.
    "EXACT_COMPETITIVE": {"label": "В рыночном диапазоне", "tone": "info"},
    "SEGMENT_LOWEST": {"label": "Архивный сегментный статус", "tone": "neutral"},
    "SEGMENT_BELOW": {"label": "Архивный сегментный статус", "tone": "neutral"},
    "SEGMENT_IN_MARKET": {"label": "Архивный сегментный статус", "tone": "neutral"},
    "SEGMENT_ABOVE": {"label": "Архивный сегментный статус", "tone": "neutral"},
    "SEGMENT_HIGHEST": {"label": "Архивный сегментный статус", "tone": "neutral"},
}



def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def compact(value: Any) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", normalize(clean_text(value)))


def canonical_season(value: Any) -> str:
    text = normalize(clean_text(value))
    if "всесез" in text or "all season" in text or "allseason" in text:
        return "ALL_SEASON"
    if "зим" in text or "winter" in text:
        return "WINTER"
    if "лет" in text or "summer" in text:
        return "SUMMER"
    return "UNKNOWN"


def canonical_studs(value: Any) -> str:
    text = normalize(clean_text(value))
    if not text:
        return "UNKNOWN"
    if "без шип" in text or "нешип" in text or text in {"no", "нет", "false", "0"}:
        return "NO"
    if "шип" in text or text in {"yes", "да", "true", "1"}:
        return "YES"
    return "UNKNOWN"


def brand_tier(brand: Any) -> str:
    value = normalize(clean_text(brand))
    if value in PREMIUM_BRANDS:
        return "PREMIUM"
    if value in MID_BRANDS:
        return "MID"
    if value in ECONOMY_BRANDS:
        return "ECONOMY"
    return "UNCLASSIFIED"


MODEL_STOP_TOKENS = {
    "шины", "шина", "летние", "зимние", "всесезонные", "без", "шипов", "с", "xl"
}


def model_token_sequence(value: Any) -> list[str]:
    """Return deterministic, title-ordered model tokens.

    A set must not be converted directly to a model string because Python hash
    randomization can change token order between processes and therefore change
    matching results. The ordered sequence keeps analytics reproducible.
    """
    text = normalize(clean_text(value))
    result: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[a-zа-я0-9]+", text):
        if len(token) <= 1 or token in MODEL_STOP_TOKENS or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def model_tokens(value: Any) -> set[str]:
    return set(model_token_sequence(value))


def model_similarity(left: Any, right: Any) -> float:
    a = normalize(clean_text(left))
    b = normalize(clean_text(right))
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    ta, tb = model_tokens(a), model_tokens(b)
    overlap = len(ta & tb) / max(1, len(ta | tb))
    return max(seq, overlap)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def _offroad_marking(title: str, attrs: dict[str, str]) -> str:
    direct = normalize(attrs.get("offroad_marking") or "")
    if direct:
        return direct.upper().replace(" ", "")
    upper = clean_text(title).upper()
    for marker in ("M/T", "A/T", "H/T", "S/T"):
        if marker in upper:
            return marker
    return ""


def identity(title: str, specs_value: Any, brand_hint: str = "") -> dict[str, str]:
    attrs = extract_attributes(title, specs_value)
    brand = normalize(brand_hint or probable_brand(title))
    model = normalize(attrs.get("model") or "")
    if not model:
        tokens = model_token_sequence(title)
        if brand:
            tokens = [token for token in tokens if token != brand]
        model = " ".join(tokens[:5])
    return {
        "brand": brand,
        "brand_tier": brand_tier(brand),
        "model": model,
        "type": attrs.get("type") or "other",
        "width": attrs.get("width") or "",
        "height": attrs.get("height") or "",
        "diameter": attrs.get("diameter") or "",
        "load": attrs.get("load") or "",
        "speed": attrs.get("speed") or "",
        "season": canonical_season(attrs.get("season")),
        "studs": canonical_studs(attrs.get("studs")),
        "runflat": normalize(attrs.get("runflat") or ""),
        "commercial": normalize(attrs.get("commercial") or ""),
        "purpose": normalize(attrs.get("purpose") or ""),
        "offroad": _offroad_marking(title, attrs),
    }


def technical_compatible(source: dict[str, str], candidate: dict[str, str]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    hard_fields = ("type", "width", "height", "diameter")
    for field in hard_fields:
        left, right = source.get(field), candidate.get(field)
        if left and right and left != right:
            return False, [f"{field}_mismatch"]
        if left and right:
            reasons.append(f"{field}_ok")
    for field in ("load", "speed", "season", "studs", "commercial"):
        left, right = source.get(field), candidate.get(field)
        if left and right and left not in {"UNKNOWN", "-"} and right not in {"UNKNOWN", "-"}:
            if left != right:
                return False, [f"{field}_mismatch"]
            reasons.append(f"{field}_ok")
    for field in ("runflat", "offroad"):
        left, right = source.get(field), candidate.get(field)
        if left and right and left != right:
            return False, [f"{field}_mismatch"]
        if left and right:
            reasons.append(f"{field}_ok")
    return True, reasons


def normalize_specifications(value: Any) -> list[dict[str, str]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = []
    result: list[dict[str, str]] = []
    if isinstance(value, dict):
        for name, item in value.items():
            result.append({"section": "", "name": clean_text(name), "value": clean_text(item)})
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                name = clean_text(item.get("name") or item.get("label") or "")
                val = clean_text(item.get("value") or item.get("text") or "")
                if name or val:
                    result.append({
                        "section": clean_text(item.get("section") or ""),
                        "name": name or "Характеристика",
                        "value": val,
                    })
    return result


@dataclass
class Candidate:
    code: str
    title: str
    url: str
    price: float
    brand: str
    tier: str
    model: str
    score: float
    relation: str
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_product_code": self.code,
            "candidate_title": self.title,
            "candidate_url": self.url,
            "candidate_price_kzt": self.price,
            "candidate_brand": self.brand,
            "candidate_tier": self.tier,
            "relation": self.relation,
            "quality_score": round(self.score, 2),
            "quality_reasons": self.reasons,
        }


def classify_candidates(
    source_title: str,
    source_specs: Any,
    source_brand: str,
    rows: list[dict[str, Any]],
) -> dict[str, list[Candidate]]:
    src = identity(source_title, source_specs, source_brand)
    exact: list[Candidate] = []
    segment: list[Candidate] = []
    review: list[Candidate] = []
    for row in rows:
        price = row.get("candidate_price_kzt")
        if price is None:
            continue
        candidate_specs = row.get("candidate_specs_json") or []
        decision = clean_text(row.get("final_decision")).casefold()
        detail_score = float(row.get("detail_score") or 0)
        validated = bool(candidate_specs) and decision in {"accepted", "accept", "review"} and detail_score >= 72.0
        dst = identity(str(row.get("candidate_title_detail") or row.get("candidate_title") or ""), candidate_specs)
        compatible, reasons = technical_compatible(src, dst)
        similarity = model_similarity(src.get("model"), dst.get("model"))
        same_brand = bool(src.get("brand") and dst.get("brand") and src["brand"] == dst["brand"])
        same_tier = src.get("brand_tier") == dst.get("brand_tier") and src.get("brand_tier") != "UNCLASSIFIED"
        completeness = sum(bool(src.get(key) and dst.get(key)) for key in ("width", "height", "diameter", "load", "speed", "season"))
        quality = min(100.0, completeness * 10 + similarity * 30 + (10 if same_brand else 0) + (10 if same_tier else 0))
        candidate = Candidate(
            code=clean_text(row.get("candidate_product_code")),
            title=clean_text(row.get("candidate_title_detail") or row.get("candidate_title")),
            url=clean_text(row.get("candidate_url")),
            price=float(price),
            brand=dst.get("brand") or "",
            tier=dst.get("brand_tier") or "UNCLASSIFIED",
            model=dst.get("model") or "",
            score=quality,
            relation="REVIEW",
            reasons=reasons + [f"model={similarity:.2f}", f"tier={dst.get('brand_tier')}"]
        )
        if not compatible:
            continue
        if validated and same_brand and similarity >= 0.78:
            candidate.relation = "EXACT_MODEL"
            exact.append(candidate)
        elif validated and same_tier and completeness >= 5 and quality >= 75:
            candidate.relation = "SEGMENT"
            segment.append(candidate)
        elif quality >= 65:
            candidate.relation = "REVIEW"
            candidate.reasons.append(f"legacy_decision={decision or 'none'}")
            review.append(candidate)
    exact.sort(key=lambda item: (item.price, -item.score))
    segment.sort(key=lambda item: (item.price, -item.score))
    review.sort(key=lambda item: (-item.score, item.price))
    return {"exact": exact, "segment": segment, "review": review}


def exact_offer_position(
    own_price: float | None,
    competitors: list[Candidate],
    scan_status: str | None = None,
) -> dict[str, Any]:
    """Calculate market position only from sellers of the same Kaspi product card.

    `competitors` must contain offers captured from the source product URL where
    candidate_product_code equals source_product_code. Search candidates and
    cross-brand analogs are intentionally excluded.
    """
    empty = {
        "reference_type": "KASPI_SAME_CARD",
        "match_method": "KASPI_PRODUCT_CODE",
        "reference_count": 0,
        "market_min_price_kzt": None,
        "market_max_price_kzt": None,
        "market_median_price_kzt": None,
        "market_q1_price_kzt": None,
        "market_q3_price_kzt": None,
        "market_price_kzt": None,
        "difference_kzt": None,
        "difference_pct": None,
        "potential_margin_per_unit_kzt": 0.0,
        "price_rank": None,
        "price_rank_total": None,
        "price_rank_tie_count": 0,
        "lowest_tie_count": 0,
        "highest_tie_count": 0,
        "is_lowest": False,
        "is_unique_lowest": False,
        "is_highest": False,
        "is_unique_highest": False,
        "lowest_product_code": None,
        "lowest_product_title": None,
        "lowest_product_url": None,
        "lowest_product_price_kzt": None,
        "highest_product_code": None,
        "highest_product_title": None,
        "highest_product_url": None,
        "highest_product_price_kzt": None,
    }
    status_value = clean_text(scan_status).casefold()
    if own_price is None or float(own_price or 0) <= 0:
        return {**empty, "price_status": "INSUFFICIENT_DATA"}
    if status_value == "error":
        return {**empty, "price_status": "DATA_ERROR"}
    if not competitors:
        if status_value in {"ok", "no_competitors", "no_offers"}:
            return {**empty, "price_status": "NO_OTHER_SELLERS"}
        return {**empty, "price_status": "NOT_ANALYZED"}

    own = float(own_price)
    # One current offer per merchant. Some pages return repeated SKUs/prices.
    by_merchant: dict[str, Candidate] = {}
    for item in competitors:
        key = clean_text(item.code) or clean_text(item.title)
        existing = by_merchant.get(key)
        if existing is None or item.price < existing.price:
            by_merchant[key] = item
    reference = sorted(by_merchant.values(), key=lambda item: (item.price, item.title))
    prices = [float(item.price) for item in reference if float(item.price) > 0]
    if not prices:
        return {**empty, "price_status": "NO_OTHER_SELLERS"}

    minimum = min(prices)
    maximum = max(prices)
    median = float(statistics.median(prices))
    q1 = float(percentile(prices, 0.25) or median)
    q3 = float(percentile(prices, 0.75) or median)
    lowest = min(reference, key=lambda item: item.price)
    highest = max(reference, key=lambda item: item.price)
    tolerance = max(500.0, median * 0.02)

    price_epsilon = 0.01
    same_as_minimum = math.isclose(own, minimum, rel_tol=0.0, abs_tol=price_epsilon)
    same_as_maximum = math.isclose(own, maximum, rel_tol=0.0, abs_tol=price_epsilon)
    lowest_tie_count = 1 + sum(
        math.isclose(price, own, rel_tol=0.0, abs_tol=price_epsilon)
        for price in prices
    ) if same_as_minimum else 1 if own < minimum else 0
    highest_tie_count = 1 + sum(
        math.isclose(price, own, rel_tol=0.0, abs_tol=price_epsilon)
        for price in prices
    ) if same_as_maximum else 1 if own > maximum else 0

    if own < minimum - price_epsilon:
        status = "EXACT_LOWEST"
    elif same_as_minimum:
        status = "EXACT_TIED_LOWEST"
    elif own > maximum + price_epsilon:
        status = "EXACT_HIGHEST"
    elif same_as_maximum:
        status = "EXACT_TIED_HIGHEST"
    elif own < median - tolerance:
        status = "EXACT_BELOW"
    elif own > median + tolerance:
        status = "EXACT_ABOVE"
    else:
        status = "EXACT_IN_MARKET"

    difference = own - median
    difference_pct = difference / median * 100 if median else None
    potential_per_unit = max(0.0, q1 - own) if status in {"EXACT_LOWEST", "EXACT_BELOW"} else 0.0
    prices_with_own = sorted(prices + [own])
    rank = 1 + sum(price < own - price_epsilon for price in prices)
    rank_tie_count = sum(
        math.isclose(price, own, rel_tol=0.0, abs_tol=price_epsilon)
        for price in prices_with_own
    )
    return {
        "price_status": status,
        "reference_type": "KASPI_SAME_CARD",
        "match_method": "KASPI_PRODUCT_CODE",
        "reference_count": len(reference),
        "market_min_price_kzt": minimum,
        "market_max_price_kzt": maximum,
        "market_median_price_kzt": median,
        "market_q1_price_kzt": q1,
        "market_q3_price_kzt": q3,
        "market_price_kzt": median,
        "difference_kzt": difference,
        "difference_pct": difference_pct,
        "potential_margin_per_unit_kzt": potential_per_unit,
        "price_rank": rank,
        "price_rank_total": len(prices_with_own),
        "price_rank_tie_count": rank_tie_count,
        "lowest_tie_count": lowest_tie_count,
        "highest_tie_count": highest_tie_count,
        "is_lowest": status in {"EXACT_LOWEST", "EXACT_TIED_LOWEST"},
        "is_unique_lowest": status == "EXACT_LOWEST",
        "is_highest": status in {"EXACT_HIGHEST", "EXACT_TIED_HIGHEST"},
        "is_unique_highest": status == "EXACT_HIGHEST",
        "lowest_product_code": lowest.code,
        "lowest_product_title": lowest.title,
        "lowest_product_url": lowest.url,
        "lowest_product_price_kzt": lowest.price,
        "highest_product_code": highest.code,
        "highest_product_title": highest.title,
        "highest_product_url": highest.url,
        "highest_product_price_kzt": highest.price,
    }


def price_position(
    own_price: float | None,
    exact_candidates: list[Candidate],
    segment_candidates: list[Candidate],
    minimum_segment_count: int = 3,
    minimum_exact_count: int = 2,
) -> dict[str, Any]:
    empty = {
        "reference_type": "NONE", "reference_count": 0,
        "market_min_price_kzt": None, "market_max_price_kzt": None,
        "market_median_price_kzt": None, "market_q1_price_kzt": None,
        "market_q3_price_kzt": None, "market_price_kzt": None,
        "difference_kzt": None, "difference_pct": None,
        "potential_margin_per_unit_kzt": 0.0, "price_rank": None,
        "price_rank_total": None, "price_rank_tie_count": 0,
        "lowest_tie_count": 0, "highest_tie_count": 0,
        "is_lowest": False, "is_unique_lowest": False,
        "is_highest": False, "is_unique_highest": False,
        "lowest_product_code": None, "lowest_product_title": None,
        "lowest_product_url": None, "lowest_product_price_kzt": None,
        "highest_product_code": None, "highest_product_title": None,
        "highest_product_url": None, "highest_product_price_kzt": None,
    }
    if own_price is None or own_price <= 0:
        return {**empty, "price_status": "INSUFFICIENT_DATA"}
    own = float(own_price)
    reference = exact_candidates if exact_candidates else segment_candidates
    reference_type = "EXACT" if exact_candidates else "SEGMENT"
    if reference_type == "EXACT" and len(reference) < minimum_exact_count:
        return {**empty, "price_status": "REVIEW_REQUIRED", "reference_type": "EXACT", "reference_count": len(reference)}
    if reference_type == "SEGMENT" and len(reference) < minimum_segment_count:
        return {**empty,
            "price_status": "REVIEW_REQUIRED" if reference else "INSUFFICIENT_DATA",
            "reference_count": len(reference),
        }
    if not reference:
        return {**empty, "price_status": "INSUFFICIENT_DATA"}

    # Remove clear price outliers only when the sample is large enough.
    if len(reference) >= 5:
        raw_prices = [item.price for item in reference]
        raw_q1 = float(percentile(raw_prices, 0.25) or 0)
        raw_q3 = float(percentile(raw_prices, 0.75) or 0)
        iqr = raw_q3 - raw_q1
        lower = max(1.0, raw_q1 - 1.5 * iqr)
        upper = raw_q3 + 1.5 * iqr
        filtered = [item for item in reference if lower <= item.price <= upper]
        if len(filtered) >= (minimum_exact_count if reference_type == "EXACT" else minimum_segment_count):
            reference = filtered
    prices = [item.price for item in reference]
    minimum = min(prices)
    maximum = max(prices)
    median = float(statistics.median(prices))
    q1 = float(percentile(prices, 0.25) or median)
    q3 = float(percentile(prices, 0.75) or median)
    lowest = min(reference, key=lambda item: item.price)
    highest = max(reference, key=lambda item: item.price)
    tolerance = max(500.0, median * 0.02)

    price_epsilon = 0.01
    same_as_minimum = math.isclose(own, minimum, rel_tol=0.0, abs_tol=price_epsilon)
    same_as_maximum = math.isclose(own, maximum, rel_tol=0.0, abs_tol=price_epsilon)
    if reference_type == "EXACT":
        if own < minimum - price_epsilon:
            status = "EXACT_LOWEST"
        elif same_as_minimum:
            status = "EXACT_TIED_LOWEST"
        elif own > maximum + price_epsilon:
            status = "EXACT_HIGHEST"
        elif same_as_maximum:
            status = "EXACT_TIED_HIGHEST"
        elif own < median - tolerance:
            status = "EXACT_BELOW"
        elif own > median + tolerance:
            status = "EXACT_ABOVE"
        else:
            status = "EXACT_IN_MARKET"
    else:
        if own <= minimum + tolerance:
            status = "SEGMENT_LOWEST"
        elif own >= maximum - tolerance:
            status = "SEGMENT_HIGHEST"
        elif own < q1 - tolerance:
            status = "SEGMENT_BELOW"
        elif own > q3 + tolerance:
            status = "SEGMENT_ABOVE"
        else:
            status = "SEGMENT_IN_MARKET"

    difference_to_median = own - median
    difference_to_median_pct = difference_to_median / median * 100 if median else None
    # "Potential margin" is intentionally conservative: only clearly underpriced
    # positions use the lower market quartile as a possible target.
    potential_per_unit = max(0.0, q1 - own) if status in {"EXACT_LOWEST", "SEGMENT_LOWEST", "SEGMENT_BELOW"} else 0.0
    prices_with_own = sorted(prices + [own])
    rank = 1 + sum(price < own - price_epsilon for price in prices)
    rank_tie_count = sum(
        math.isclose(price, own, rel_tol=0.0, abs_tol=price_epsilon)
        for price in prices_with_own
    )
    lowest_tie_count = rank_tie_count if same_as_minimum else 1 if own < minimum else 0
    highest_tie_count = rank_tie_count if same_as_maximum else 1 if own > maximum else 0
    return {
        "price_status": status,
        "reference_type": reference_type,
        "reference_count": len(reference),
        "market_min_price_kzt": minimum,
        "market_max_price_kzt": maximum,
        "market_median_price_kzt": median,
        "market_q1_price_kzt": q1,
        "market_q3_price_kzt": q3,
        "market_price_kzt": median,
        "difference_kzt": difference_to_median,
        "difference_pct": difference_to_median_pct,
        "potential_margin_per_unit_kzt": potential_per_unit,
        "price_rank": rank,
        "price_rank_total": len(prices_with_own),
        "price_rank_tie_count": rank_tie_count,
        "lowest_tie_count": lowest_tie_count,
        "highest_tie_count": highest_tie_count,
        "is_lowest": status in {"EXACT_LOWEST", "EXACT_TIED_LOWEST", "SEGMENT_LOWEST"},
        "is_unique_lowest": status in {"EXACT_LOWEST", "SEGMENT_LOWEST"},
        "is_highest": status in {"EXACT_HIGHEST", "EXACT_TIED_HIGHEST", "SEGMENT_HIGHEST"},
        "is_unique_highest": status in {"EXACT_HIGHEST", "SEGMENT_HIGHEST"},
        "lowest_product_code": lowest.code,
        "lowest_product_title": lowest.title,
        "lowest_product_url": lowest.url,
        "lowest_product_price_kzt": lowest.price,
        "highest_product_code": highest.code,
        "highest_product_title": highest.title,
        "highest_product_url": highest.url,
        "highest_product_price_kzt": highest.price,
    }
