#!/usr/bin/env python3
"""
Kaspi Search Compare V8.2.

Этап сравнения конкурентов поверх существующей базы Kaspi Market Monitor V7.

Алгоритм:
1. Берет название, цену и характеристики товара Unityre из текущей SQLite-базы.
2. Через видимый интерфейс Kaspi вводит название в строку поиска.
3. Собирает карточки и цены с одной или нескольких страниц выдачи.
4. Выполняет быстрый отбор по названию, типу товара, размеру и индексам.
5. Открывает несколько наиболее дешевых подходящих карточек.
6. Сравнивает характеристики исходного товара и кандидата.
7. Принимает, отправляет на проверку или отклоняет кандидата.
8. Рассчитывает минимальную, максимальную и среднюю рыночную цену.
9. Сохраняет состояние после каждого исходного товара и продолжает после остановки.

Скрипт не обходит CAPTCHA и ограничения Kaspi. При появлении проверки
он приостанавливает работу и ожидает ручного прохождения в Chromium.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import difflib
import html
import json
import random
import re
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.postgres_compat import configure_connection, connect_database

try:
    from playwright.async_api import (
        BrowserContext,
        Page,
        Playwright,
        TimeoutError as PlaywrightTimeoutError,
        async_playwright,
    )
except ImportError:
    BrowserContext = Page = Playwright = Any  # type: ignore
    PlaywrightTimeoutError = Exception
    async_playwright = None


VERSION = "8.2"
HOME_URL = "https://kaspi.kz/shop/almaty/"
DEFAULT_CITY_ID = "750000000"

BLOCK_MARKERS = (
    "captcha",
    "я не робот",
    "подтвердите, что вы не робот",
    "проверка безопасности",
    "слишком много запросов",
    "доступ ограничен",
)

GENERIC_WORDS = {
    "шина", "шины", "мотошина", "камера", "автошина", "покрышка",
    "передняя", "задняя", "универсальная", "без", "шипов", "с",
    "летние", "зимние", "всесезонные", "для", "легкового",
    "автомобиля", "внедорожника", "r", "zr", "tl", "tt",
}

SUMMARY_SPEC_NAMES = {
    "общие характеристики",
    "основные характеристики",
    "дополнительно",
}

SEARCH_INPUT_SELECTORS = (
    ".search-bar__input",
    "input.search-bar-input",
    "#search-bar-input",
    "input[name='text']",
    "input[placeholder*='Поиск']",
)

SEARCH_BUTTON_SELECTORS = (
    ".search-bar__submit",
    "button.search-bar__submit",
    "button[type='submit']",
)

SEARCH_CARD_JS = r"""
(cards, pageNumber) => cards.map((card, index) => {
  const clean = value => (value || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
  const link = card.querySelector('.item-card__name-link') || card.querySelector('a[href*="/shop/p/"]');
  const priceNode =
    card.querySelector('.item-card__debet .item-card__prices-price') ||
    card.querySelector('.item-card__prices-price') ||
    card.querySelector('[class*="prices-price"]');
  const reviewNode =
    card.querySelector('.item-card__rating a') ||
    card.querySelector('a[href*="tab=reviews"]');
  let rating = null;
  const ratingNode = card.querySelector('.rating') || card.querySelector('[class*="rating"]');
  if (ratingNode) {
    const textMatch = clean(ratingNode.textContent).replace(',', '.').match(/\b([0-5](?:\.\d)?)\b/);
    const classMatch = String(ratingNode.className).match(/(?:^|\s)_(\d{2})(?:\s|$)/);
    if (textMatch) rating = Number(textMatch[1]);
    else if (classMatch) rating = Number(classMatch[1]) / 10;
  }
  return {
    search_page: pageNumber,
    position: index + 1,
    candidate_product_code: card.getAttribute('data-product-id') || '',
    candidate_title: clean(link?.textContent),
    candidate_url: link ? new URL(link.getAttribute('href'), location.origin).href : '',
    candidate_price_text: clean(priceNode?.textContent),
    candidate_reviews_text: clean(reviewNode?.textContent),
    candidate_rating: rating,
  };
})
"""

DETAIL_JS = r"""
() => {
  const clean = value => (value || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
  const firstText = selectors => {
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      const value = clean(node?.textContent);
      if (value) return value;
    }
    return '';
  };

  const title = firstText([
    '.item__heading',
    '[data-test-id="product-name"]',
    '.product__title',
    'h1'
  ]);

  const codeMatch = clean(document.body?.innerText)
    .match(/Код\s+товара\s*:?\s*(\d{5,})/i);

  const specifications = [];
  const seen = new Set();

  const add = (section, name, value) => {
    section = clean(section);
    name = clean(name).replace(/\s*[:：]\s*$/, '');
    value = clean(value);
    if (!name || !value || name === value) return;
    const key = `${section}|${name}|${value}`;
    if (seen.has(key)) return;
    seen.add(key);
    specifications.push({section, name, value});
  };

  const list = document.querySelector('.specifications-list');
  if (list) {
    const rows = list.querySelectorAll(
      '.specifications-list__spec, .specifications-list__item, li, tr'
    );
    for (const row of rows) {
      const nameNode = row.querySelector(
        '.specifications-list__spec-term-text, ' +
        '.specifications-list__spec-term, ' +
        '.specifications-list__term, ' +
        '[class*="spec-term"], [class*="spec-name"]'
      );
      const valueNode = row.querySelector(
        '.specifications-list__spec-definition, ' +
        '.specifications-list__spec-definition-text, ' +
        '.specifications-list__definition, ' +
        '[class*="spec-definition"], [class*="spec-value"]'
      );
      if (nameNode && valueNode) add('', nameNode.textContent, valueNode.textContent);
    }
  }

  for (const dl of document.querySelectorAll('dl')) {
    const children = [...dl.children];
    for (let i = 0; i < children.length; i++) {
      if (children[i].tagName !== 'DT') continue;
      const dd = children.slice(i + 1).find(node => node.tagName === 'DD');
      if (dd) add('', children[i].textContent, dd.textContent);
    }
  }

  return {
    candidate_code_detail: codeMatch ? codeMatch[1] : '',
    candidate_title_detail: title,
    specifications,
  };
}
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def normalize(value: Any) -> str:
    text = clean_text(value).casefold().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", " ", text).strip()


def compact(value: Any) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", normalize(value))


def parse_int(value: Any) -> int | None:
    text = clean_text(value)
    match = re.search(r"\d[\d\s\u00a0\u202f]*", text)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    return int(digits) if digits else None


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:[.,]\d+)?", clean_text(value))
    return float(match.group(0).replace(",", ".")) if match else None


def safe_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default
    try:
        return json.loads(str(value))
    except Exception:
        return default


def clean_specs(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in safe_json(value, []):
        if not isinstance(row, dict):
            continue
        name = clean_text(row.get("name") or row.get("specification"))
        spec_value = clean_text(row.get("value"))
        section = clean_text(row.get("section"))
        if not name or not spec_value:
            continue
        if normalize(name) in SUMMARY_SPEC_NAMES and len(spec_value) > 30:
            continue
        key = (compact(name), compact(spec_value))
        if key in seen:
            continue
        seen.add(key)
        result.append({"section": section, "name": name, "value": spec_value})
    return result


def specs_map(value: Any) -> dict[str, str]:
    return {normalize(x["name"]): clean_text(x["value"]) for x in clean_specs(value)}


def spec_get(mapping: dict[str, str], *names: str) -> str:
    for name in names:
        found = mapping.get(normalize(name))
        if found:
            return found
    return ""


def product_type(title: str, specs: dict[str, str] | None = None) -> str:
    specs = specs or {}
    text = normalize(title + " " + spec_get(specs, "Тип", "Тип товара"))
    if "ободная лента" in text:
        return "rim_tape"
    if "цеп" in text:
        return "chain"
    if "камера" in text:
        return "tube"
    if any(word in text for word in ("мотошина", "передняя", "задняя", "ось применения")):
        return "moto_tire"
    if any(word in text for word in ("шина", "r15", "r16", "r17", "r18", "r19", "r20", "r21", "r22")):
        return "tire"
    return "other"


def first_number(text: str) -> str:
    match = re.search(r"\d+(?:[.,]\d+)?", clean_text(text))
    return match.group(0).replace(",", ".") if match else ""


def extract_attributes(title: str, specs_value: Any = None) -> dict[str, str]:
    specs = specs_map(specs_value)
    title_clean = clean_text(title)
    title_norm = normalize(title_clean)
    upper = title_clean.upper().replace("Ё", "Е")

    width = first_number(spec_get(specs, "Ширина профиля"))
    height = first_number(spec_get(specs, "Высота профиля"))
    diameter = first_number(spec_get(specs, "Диаметр диска"))

    if not (width and height and diameter):
        patterns = [
            r"(?<!\d)(\d{2,3}(?:[.,]\d+)?)\s*/\s*(\d{1,3}(?:[.,]\d+)?)\s*(?:ZR|R|B|-)?\s*(\d{2})(?!\d)",
            r"(?<!\d)(\d{1,2}(?:[.,]\d+)?)\s*/\s*(\d{2})\s*R\s*(\d{2})(?!\d)",
        ]
        for pattern in patterns:
            match = re.search(pattern, upper)
            if match:
                width = width or match.group(1).replace(",", ".")
                height = height or match.group(2).replace(",", ".")
                diameter = diameter or match.group(3)
                break

    if not diameter:
        match = re.search(r"\bD\s*(\d{2})\b", upper)
        if match:
            diameter = match.group(1)

    load = ""
    speed = ""
    match = re.search(r"(?<!\d)(\d{2,3})\s*([A-Z])(?:\b|$)", upper)
    if match:
        load, speed = match.group(1), match.group(2)

    if not load:
        load = first_number(spec_get(specs, "Индекс нагрузки"))
        load = load.split(".")[0] if load else ""

    if not speed:
        speed_match = re.search(
            r"\b([A-Z])\b",
            spec_get(specs, "Индекс максимальной скорости").upper(),
        )
        speed = speed_match.group(1) if speed_match else ""

    season = normalize(spec_get(specs, "Сезонность"))
    studs = normalize(spec_get(specs, "Шипы"))
    axis = normalize(spec_get(specs, "Ось применения"))
    if not axis:
        if "передняя" in title_norm:
            axis = "передняя"
        elif "задняя" in title_norm:
            axis = "задняя"
        elif "универсальная" in title_norm:
            axis = "универсальная"

    model = normalize(spec_get(specs, "Название модели"))
    runflat = normalize(spec_get(specs, "Технология RunFlat", "RunFlat"))
    tire_type = normalize(spec_get(specs, "Тип шины"))
    purpose = normalize(spec_get(specs, "Назначение"))
    offroad_marking = normalize(spec_get(specs, "Маркировка внедорожных шин"))
    reinforced = normalize(spec_get(specs, "Усиленная"))
    valve = normalize(spec_get(specs, "Вентиль", "Тип вентиля"))

    commercial = "да" if re.search(
        r"(?:ZR|R|B|-)?\s*\d{2}\s*C(?:\b|$)",
        upper,
    ) else "нет"

    return {
        "type": product_type(title, specs),
        "width": width,
        "height": height,
        "diameter": diameter,
        "load": load,
        "speed": speed,
        "season": season,
        "studs": studs,
        "axis": axis,
        "model": model,
        "runflat": runflat,
        "tire_type": tire_type,
        "purpose": purpose,
        "offroad_marking": offroad_marking,
        "reinforced": reinforced,
        "valve": valve,
        "commercial": commercial,
    }


def title_tokens(title: str) -> set[str]:
    tokens = set(re.findall(r"[a-zа-я0-9]+", normalize(title)))
    return {x for x in tokens if x not in GENERIC_WORDS and len(x) > 1}


def probable_brand(title: str) -> str:
    tokens = re.findall(r"[A-Za-zА-Яа-я0-9]+", clean_text(title))
    ignored = {"шина", "мотошина", "камера", "ободная", "лента", "цепи"}
    for token in tokens:
        if normalize(token) not in ignored and not token.isdigit():
            return normalize(token)
    return ""


@dataclass
class MatchResult:
    score: float
    decision: str
    reason: str
    hard_mismatch: bool


def fast_match(
    source_code: str,
    source_title: str,
    source_specs: Any,
    candidate_code: str,
    candidate_title: str,
) -> MatchResult:
    if source_code and source_code == candidate_code:
        return MatchResult(100.0, "accepted", "совпадает код товара Kaspi", False)

    src_attr = extract_attributes(source_title, source_specs)
    dst_attr = extract_attributes(candidate_title)
    source_norm = normalize(source_title)
    candidate_norm = normalize(candidate_title)
    src_tokens = title_tokens(source_title)
    dst_tokens = title_tokens(candidate_title)

    sequence = difflib.SequenceMatcher(None, source_norm, candidate_norm).ratio()
    overlap = len(src_tokens & dst_tokens) / max(1, len(src_tokens | dst_tokens))
    score = sequence * 30 + overlap * 20
    reasons = [f"text={sequence:.2f}", f"tokens={overlap:.2f}"]
    hard_mismatch = False

    src_brand = probable_brand(source_title)
    dst_brand = probable_brand(candidate_title)
    if src_brand and dst_brand:
        if src_brand == dst_brand:
            score += 14
            reasons.append("brand=ok")
        else:
            score -= 18
            reasons.append("brand=mismatch")

    src_type = src_attr["type"]
    dst_type = dst_attr["type"]
    if src_type == dst_type:
        score += 10
        reasons.append("type=ok")
    elif src_type != "other" and dst_type != "other":
        score -= 35
        hard_mismatch = True
        reasons.append("type=HARD_MISMATCH")

    weights = {"width": 10, "height": 8, "diameter": 12}
    for field, weight in weights.items():
        src_value = src_attr[field]
        dst_value = dst_attr[field]
        if src_value and dst_value:
            if src_value == dst_value:
                score += weight
                reasons.append(f"{field}=ok")
            else:
                score -= weight * 3
                hard_mismatch = True
                reasons.append(f"{field}=HARD_MISMATCH")

    for field, weight in (("load", 4), ("speed", 3)):
        src_value = src_attr[field]
        dst_value = dst_attr[field]
        if src_value and dst_value:
            if src_value == dst_value:
                score += weight
                reasons.append(f"{field}=ok")
            else:
                score -= weight * 1.5
                reasons.append(f"{field}=mismatch")

    score = max(0.0, min(100.0, score))
    if hard_mismatch:
        decision = "rejected"
    elif score >= 72:
        decision = "preselected"
    elif score >= 55:
        decision = "review"
    else:
        decision = "rejected"
    return MatchResult(score, decision, "; ".join(reasons), hard_mismatch)


def detail_match(
    source_title: str,
    source_specs_value: Any,
    candidate_title: str,
    candidate_specs_value: Any,
    fast_score: float,
) -> MatchResult:
    src_specs = specs_map(source_specs_value)
    dst_specs = specs_map(candidate_specs_value)
    src_attr = extract_attributes(source_title, source_specs_value)
    dst_attr = extract_attributes(candidate_title, candidate_specs_value)

    score = fast_score * 0.45
    reasons = [f"fast={fast_score:.1f}"]
    hard_mismatch = False

    # Базовые параметры обязаны совпадать для любого типа товара.
    important: list[tuple[str, int, bool]] = [
        ("type", 12, True),
        ("width", 12, True),
        ("height", 10, True),
        ("diameter", 14, True),
    ]

    source_type = src_attr["type"]
    if source_type in {"tire", "moto_tire"}:
        # Для шин индексы, сезонность и конструктивные признаки не являются
        # второстепенными: сравнение цены допустимо только внутри одной
        # технической конфигурации.
        important.extend(
            [
                ("load", 6, True),
                ("speed", 5, True),
                ("season", 6, True),
                ("studs", 6, True),
                ("runflat", 4, True),
                ("tire_type", 4, True),
                ("commercial", 5, True),
                ("offroad_marking", 7, True),
            ]
        )
        if source_type == "moto_tire":
            important.append(("axis", 6, True))
    elif source_type == "tube":
        # Для камер усиление и тип вентиля существенно меняют назначение.
        important.extend(
            [
                ("reinforced", 8, True),
                ("valve", 6, True),
            ]
        )

    possible = 0.0
    earned = 0.0
    for field, weight, hard in important:
        src_value = src_attr[field]
        dst_value = dst_attr[field]
        if not src_value or not dst_value:
            continue
        possible += weight
        if src_value == dst_value:
            earned += weight
            reasons.append(f"{field}=ok")
        else:
            reasons.append(f"{field}=HARD_MISMATCH" if hard else f"{field}=mismatch")
            if hard:
                hard_mismatch = True

    common_spec_names = set(src_specs) & set(dst_specs)
    comparable_pairs = 0
    equal_pairs = 0
    for name in common_spec_names:
        src_value = compact(src_specs[name])
        dst_value = compact(dst_specs[name])
        if not src_value or not dst_value:
            continue
        comparable_pairs += 1
        if src_value == dst_value:
            equal_pairs += 1

    if possible:
        score += 45 * (earned / possible)
    elif comparable_pairs:
        score += 25 * (equal_pairs / comparable_pairs)

    model_src = src_attr["model"]
    model_dst = dst_attr["model"]
    if model_src and model_dst:
        model_ratio = difflib.SequenceMatcher(None, model_src, model_dst).ratio()
        score += 10 * model_ratio
        reasons.append(f"model={model_ratio:.2f}")

    if comparable_pairs:
        spec_ratio = equal_pairs / comparable_pairs
        score += 5 * spec_ratio
        reasons.append(f"specs={equal_pairs}/{comparable_pairs}")

    score = max(0.0, min(100.0, score))
    if hard_mismatch:
        decision = "rejected"
    elif score >= 78:
        decision = "accepted"
    elif score >= 65:
        decision = "review"
    else:
        decision = "rejected"

    return MatchResult(score, decision, "; ".join(reasons), hard_mismatch)


def with_city(url: str, city_id: str) -> str:
    if not url:
        return url
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("c", city_id)
    return urlunparse(parsed._replace(query=urlencode(query)))


def save_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})
    temp.replace(path)


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


class Database:
    def __init__(self, path: Path):
        self.path = path
        if not path.exists():
            raise RuntimeError(
                f"Не найдена база {path}. Сначала укажите путь к существующему "
                "output_market_v7\\kaspi_market.db"
            )
        self.conn = connect_database(path, timeout=60)
        configure_connection(self.conn, journal_mode="WAL", synchronous="NORMAL")
        self.create_schema()

    def create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS market_search_runs (
                source_product_code TEXT PRIMARY KEY,
                query_text TEXT,
                status TEXT,
                candidates_found INTEGER,
                candidates_validated INTEGER,
                accepted_count INTEGER,
                review_count INTEGER,
                error TEXT,
                started_at TEXT,
                finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS market_candidates (
                source_product_code TEXT,
                candidate_product_code TEXT,
                search_page INTEGER,
                search_position INTEGER,
                candidate_title TEXT,
                candidate_url TEXT,
                candidate_price_kzt INTEGER,
                candidate_rating REAL,
                candidate_reviews INTEGER,
                fast_score REAL,
                fast_decision TEXT,
                fast_reason TEXT,
                candidate_title_detail TEXT,
                candidate_specs_json TEXT,
                detail_score REAL,
                final_decision TEXT,
                detail_reason TEXT,
                checked_at TEXT,
                PRIMARY KEY(source_product_code, candidate_product_code)
            );

            CREATE TABLE IF NOT EXISTS market_seller_offers (
                source_product_code TEXT,
                candidate_product_code TEXT,
                merchant_id TEXT,
                merchant_name TEXT,
                merchant_sku TEXT,
                price_kzt REAL,
                merchant_rating REAL,
                merchant_reviews INTEGER,
                captured_at TEXT,
                PRIMARY KEY(
                    source_product_code,
                    candidate_product_code,
                    merchant_id,
                    merchant_sku,
                    price_kzt
                )
            );

            CREATE TABLE IF NOT EXISTS market_search_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_product_code TEXT,
                stage TEXT,
                message TEXT,
                created_at TEXT
            );
            """
        )
        self.conn.commit()

    def jobs(
        self,
        limit: int,
        refresh: bool,
        codes: list[str],
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ["1=1"]
        if not refresh:
            where.append(
                """NOT EXISTS (
                    SELECT 1 FROM market_search_runs r
                    WHERE r.source_product_code=c.product_code
                      AND r.status='ok'
                )"""
            )
        if codes:
            placeholders = ",".join("?" for _ in codes)
            where.append(f"c.product_code IN ({placeholders})")
            params.extend(codes)

        sql = f"""
        SELECT
            c.product_code,
            c.title_catalog,
            c.catalog_price_kzt,
            c.catalog_rating,
            c.catalog_reviews,
            c.product_url,
            c.page_number,
            d.title_detail,
            d.specifications_json,
            d.detail_status
        FROM catalog_products c
        LEFT JOIN product_details d ON d.product_code=c.product_code
        WHERE {' AND '.join(where)}
        ORDER BY c.page_number, c.position_on_page, c.product_code
        """
        rows = [dict(row) for row in self.conn.execute(sql, params).fetchall()]
        return rows[:limit] if limit > 0 else rows

    def begin_run(self, code: str, query: str) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO market_search_runs VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (code, query, "running", 0, 0, 0, 0, None, now_iso(), None),
        )
        self.conn.execute(
            "DELETE FROM market_candidates WHERE source_product_code=?",
            (code,),
        )
        self.conn.execute(
            "DELETE FROM market_seller_offers WHERE source_product_code=?",
            (code,),
        )
        self.conn.commit()

    def finish_run(
        self,
        code: str,
        status: str,
        candidates_found: int,
        validated: int,
        accepted: int,
        review: int,
        error: str | None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE market_search_runs
            SET status=?, candidates_found=?, candidates_validated=?,
                accepted_count=?, review_count=?, error=?, finished_at=?
            WHERE source_product_code=?
            """,
            (
                status,
                candidates_found,
                validated,
                accepted,
                review,
                error,
                now_iso(),
                code,
            ),
        )
        self.conn.commit()

    def save_candidate(self, source_code: str, row: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO market_candidates VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                source_code,
                clean_text(row.get("candidate_product_code")),
                parse_int(row.get("search_page")),
                parse_int(row.get("position")),
                clean_text(row.get("candidate_title")),
                clean_text(row.get("candidate_url")),
                parse_int(row.get("candidate_price_kzt")),
                parse_float(row.get("candidate_rating")),
                parse_int(row.get("candidate_reviews")),
                parse_float(row.get("fast_score")),
                clean_text(row.get("fast_decision")),
                clean_text(row.get("fast_reason")),
                clean_text(row.get("candidate_title_detail")),
                json.dumps(clean_specs(row.get("candidate_specs")), ensure_ascii=False),
                parse_float(row.get("detail_score")),
                clean_text(row.get("final_decision")),
                clean_text(row.get("detail_reason")),
                now_iso(),
            ),
        )

    def save_offers(
        self,
        source_code: str,
        candidate_code: str,
        offers: list[dict[str, Any]],
    ) -> None:
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            price = parse_float(offer.get("price"))
            merchant_id = clean_text(offer.get("merchantId"))
            if price is None or not merchant_id:
                continue
            self.conn.execute(
                """
                INSERT OR REPLACE INTO market_seller_offers VALUES(
                    ?,?,?,?,?,?,?,?,?
                )
                """,
                (
                    source_code,
                    candidate_code,
                    merchant_id,
                    clean_text(offer.get("merchantName")),
                    clean_text(offer.get("merchantSku")),
                    price,
                    parse_float(offer.get("merchantRating")),
                    parse_int(offer.get("merchantReviewsQuantity")),
                    now_iso(),
                ),
            )

    def add_error(self, code: str, stage: str, message: str) -> None:
        self.conn.execute(
            """
            INSERT INTO market_search_errors(
                source_product_code, stage, message, created_at
            ) VALUES(?,?,?,?)
            """,
            (code, stage, message, now_iso()),
        )
        self.conn.commit()

    def commit(self) -> None:
        self.conn.commit()


async def ensure_playwright() -> None:
    if async_playwright is None:
        raise RuntimeError(
            "Playwright не установлен. Выполните: "
            ".\\.venv\\Scripts\\python.exe -m pip install playwright"
        )


async def launch_context(
    playwright: Playwright,
    profile: Path,
    headless: bool,
) -> BrowserContext:
    return await playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile.resolve()),
        headless=headless,
        locale="ru-RU",
        timezone_id="Asia/Almaty",
        viewport={"width": 1440, "height": 1000},
    )


async def light_route(route: Any) -> None:
    if route.request.resource_type in {"image", "font", "media"}:
        await route.abort()
    else:
        await route.continue_()


async def page_is_blocked(page: Page) -> bool:
    try:
        text = normalize(await page.locator("body").inner_text(timeout=2000))
    except Exception:
        return False
    return any(marker in text for marker in BLOCK_MARKERS)


async def close_city_modal(page: Page) -> None:
    selectors = (
        ".dialog__close",
        ".modal__close",
        "[aria-label='Закрыть']",
        "button:has-text('Нет')",
    )
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if await locator.count() and await locator.first.is_visible():
                await locator.first.click(timeout=1500)
                return
        except Exception:
            continue


class BlockController:
    def __init__(self) -> None:
        self.allowed = asyncio.Event()
        self.allowed.set()
        self.lock = asyncio.Lock()

    async def checkpoint(self) -> None:
        await self.allowed.wait()

    async def handle(self, page: Page) -> None:
        if not await page_is_blocked(page):
            return
        async with self.lock:
            if not await page_is_blocked(page):
                return
            self.allowed.clear()
            print(
                "\n[Проверка Kaspi] Обнаружена CAPTCHA/проверка. "
                "Пройдите её вручную в открытом Chromium. Ожидание до 10 минут."
            )
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                await asyncio.sleep(1)
                if not await page_is_blocked(page):
                    self.allowed.set()
                    print("[Проверка Kaspi] Работа продолжена.")
                    return
            self.allowed.set()
            raise RuntimeError("Проверка Kaspi не была пройдена за 10 минут")


async def find_visible(page: Page, selectors: tuple[str, ...]):
    for selector in selectors:
        locator = page.locator(selector)
        try:
            if await locator.count() and await locator.first.is_visible():
                return locator.first
        except Exception:
            continue
    return None


async def ensure_search_page(
    page: Page,
    timeout_ms: int,
    controller: BlockController,
) -> None:
    await controller.checkpoint()
    search_input = await find_visible(page, SEARCH_INPUT_SELECTORS)
    if search_input is not None:
        return
    await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    await close_city_modal(page)
    await controller.handle(page)
    search_input = await find_visible(page, SEARCH_INPUT_SELECTORS)
    if search_input is None:
        raise RuntimeError("Не найдена строка поиска Kaspi")


async def submit_search(
    page: Page,
    query: str,
    timeout_ms: int,
    controller: BlockController,
) -> None:
    await ensure_search_page(page, timeout_ms, controller)
    await controller.checkpoint()

    input_locator = await find_visible(page, SEARCH_INPUT_SELECTORS)
    if input_locator is None:
        raise RuntimeError("Не найдена строка поиска")

    await input_locator.click()
    await input_locator.fill("")
    await input_locator.fill(query)

    button = await find_visible(page, SEARCH_BUTTON_SELECTORS)
    if button is not None:
        try:
            await button.click(timeout=3000)
        except Exception:
            await input_locator.press("Enter")
    else:
        await input_locator.press("Enter")

    try:
        await page.wait_for_url(re.compile(r"/shop/(?:search|c/|p/)"), timeout=timeout_ms)
    except Exception:
        pass

    await close_city_modal(page)
    await controller.handle(page)

    try:
        await page.wait_for_selector(
            ".item-card[data-product-id], .item-card__name-link",
            timeout=timeout_ms,
        )
    except PlaywrightTimeoutError:
        body = normalize(await page.locator("body").inner_text())
        if "ничего не найдено" in body or "товары не найдены" in body:
            return
        raise RuntimeError("После поиска не появились карточки товаров")


async def collect_search_cards(
    page: Page,
    pages: int,
    timeout_ms: int,
    controller: BlockController,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    for page_number in range(1, max(1, pages) + 1):
        await controller.checkpoint()
        await controller.handle(page)

        cards = page.locator(".item-card[data-product-id]")
        count = await cards.count()
        if not count:
            break

        rows = await cards.evaluate_all(SEARCH_CARD_JS, page_number)
        for row in rows:
            code = clean_text(row.get("candidate_product_code"))
            if not code or code in seen:
                continue
            seen.add(code)
            row["candidate_price_kzt"] = parse_int(row.get("candidate_price_text"))
            row["candidate_reviews"] = parse_int(row.get("candidate_reviews_text"))
            result.append(row)

        if page_number >= pages:
            break

        next_locator = page.locator(".pagination__el").filter(
            has_text=re.compile("Следующая", re.I)
        )
        if not await next_locator.count():
            break

        next_button = next_locator.last
        classes = clean_text(await next_button.get_attribute("class"))
        if "_disabled" in classes or "disabled" in classes:
            break

        before = tuple(
            await cards.evaluate_all(
                "nodes => nodes.map(x => x.getAttribute('data-product-id') || '')"
            )
        )
        await next_button.scroll_into_view_if_needed()
        try:
            await next_button.click(timeout=5000)
        except Exception:
            await next_button.dispatch_event("click")

        deadline = time.monotonic() + timeout_ms / 1000
        changed = False
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            current_cards = page.locator(".item-card[data-product-id]")
            current = tuple(
                await current_cards.evaluate_all(
                    "nodes => nodes.map(x => x.getAttribute('data-product-id') || '')"
                )
            )
            if current and current != before:
                changed = True
                break
        if not changed:
            break

    return result


async def capture_offers_response(
    page: Page,
    url: str,
    timeout_ms: int,
    city_id: str,
    controller: BlockController,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    loop = asyncio.get_running_loop()
    offer_future: asyncio.Future[list[dict[str, Any]]] = loop.create_future()

    async def inspect_response(response: Any) -> None:
        if offer_future.done():
            return
        if "offer-view/offers" not in response.url:
            return
        try:
            payload = await response.json()
            offers = payload.get("offers") if isinstance(payload, dict) else None
            if isinstance(offers, list):
                offer_future.set_result(
                    [x for x in offers if isinstance(x, dict)]
                )
        except Exception:
            return

    def handler(response: Any) -> None:
        asyncio.create_task(inspect_response(response))

    page.on("response", handler)
    try:
        await controller.checkpoint()
        await page.goto(
            with_city(url, city_id),
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        await close_city_modal(page)
        await controller.handle(page)

        try:
            await page.wait_for_function(
                r"""() => Boolean(
                    document.querySelector('.item__heading') ||
                    document.querySelector('h1') ||
                    document.querySelector('.specifications-list') ||
                    /Код\s+товара/i.test(document.body?.innerText || '')
                )""",
                timeout=min(timeout_ms, 15000),
            )
        except Exception:
            pass

        try:
            tab = page.get_by_text("Характеристики", exact=False)
            if await tab.count() and await tab.first.is_visible():
                await tab.first.click(timeout=2500)
                await page.wait_for_timeout(500)
        except Exception:
            pass

        detail = await page.evaluate(DETAIL_JS)
        detail["specifications"] = clean_specs(detail.get("specifications"))

        offers: list[dict[str, Any]] = []
        try:
            offers = await asyncio.wait_for(offer_future, timeout=4.0)
        except asyncio.TimeoutError:
            offers = []

        return detail, offers
    finally:
        page.remove_listener("response", handler)


def choose_candidates(
    source_code: str,
    source_title: str,
    source_specs: Any,
    cards: list[dict[str, Any]],
    validate_top: int,
) -> list[dict[str, Any]]:
    evaluated: list[dict[str, Any]] = []
    for card in cards:
        match = fast_match(
            source_code,
            source_title,
            source_specs,
            clean_text(card.get("candidate_product_code")),
            clean_text(card.get("candidate_title")),
        )
        row = dict(card)
        row["fast_score"] = round(match.score, 2)
        row["fast_decision"] = match.decision
        row["fast_reason"] = match.reason
        evaluated.append(row)

    plausible = [
        row for row in evaluated
        if row["fast_decision"] in {"accepted", "preselected", "review"}
        and row.get("candidate_price_kzt") is not None
    ]

    exact = [
        row for row in plausible
        if clean_text(row.get("candidate_product_code")) == source_code
    ]

    cheapest = sorted(
        plausible,
        key=lambda row: (
            row.get("candidate_price_kzt") is None,
            row.get("candidate_price_kzt") or 10**18,
            -(row.get("fast_score") or 0),
        ),
    )

    best_score = sorted(
        plausible,
        key=lambda row: -(row.get("fast_score") or 0),
    )

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in exact + cheapest + best_score:
        code = clean_text(row.get("candidate_product_code"))
        if not code or code in seen:
            continue
        seen.add(code)
        selected.append(row)
        if len(selected) >= validate_top:
            break

    return selected


async def save_debug(
    page: Page,
    debug_dir: Path,
    source_code: str,
    stage: str,
    reason: str,
) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    safe_stage = re.sub(r"[^A-Za-z0-9_-]+", "_", stage)
    prefix = debug_dir / f"{source_code}_{safe_stage}_{int(time.time())}"
    try:
        prefix.with_suffix(".txt").write_text(
            f"reason={reason}\nurl={page.url}\ntitle={await page.title()}\n",
            encoding="utf-8",
        )
    except Exception:
        pass
    try:
        prefix.with_suffix(".html").write_text(
            await page.content(),
            encoding="utf-8",
        )
    except Exception:
        pass
    try:
        await page.screenshot(
            path=str(prefix.with_suffix(".png")),
            full_page=True,
        )
    except Exception:
        pass


async def compare_command(args: argparse.Namespace) -> None:
    await ensure_playwright()

    workers = max(1, min(args.workers, 4))
    if workers != args.workers:
        print(f"[Настройка] Число воркеров ограничено значением {workers}.")

    db_path = Path(args.db)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)

    codes = [clean_text(x) for x in args.codes.split(",") if clean_text(x)]
    jobs = db.jobs(args.limit, args.refresh, codes)
    if not jobs:
        print("[Сравнение] Нет необработанных товаров.")
        export_reports(db, output, args.seller_name)
        db.conn.close()
        return

    print(
        f"[Сравнение] Товаров: {len(jobs)}; воркеров: {workers}; "
        f"страниц поиска: {args.search_pages}; "
        f"проверяемых карточек на товар: {args.validate_top}"
    )
    print(
        "[Сравнение] Каждый воркер использует две вкладки: "
        "поиск и проверка характеристик."
    )

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    for job in jobs:
        queue.put_nowait(job)
    for _ in range(workers):
        queue.put_nowait(None)

    db_lock = asyncio.Lock()
    progress = {"done": 0, "ok": 0, "error": 0}
    controller = BlockController()
    debug_dir = output / "debug"
    tasks: list[asyncio.Task[Any]] = []

    async with async_playwright() as playwright:
        context = await launch_context(
            playwright,
            Path(args.profile),
            args.headless,
        )
        try:
            async def worker(worker_id: int) -> None:
                search_page = await context.new_page()
                detail_page = await context.new_page()
                await search_page.route("**/*", light_route)
                await detail_page.route("**/*", light_route)

                try:
                    await search_page.goto(
                        HOME_URL,
                        wait_until="domcontentloaded",
                        timeout=args.timeout * 1000,
                    )
                    await close_city_modal(search_page)
                    await controller.handle(search_page)

                    while True:
                        item = await queue.get()
                        if item is None:
                            queue.task_done()
                            break

                        source_code = clean_text(item.get("product_code"))
                        source_title = clean_text(
                            item.get("title_detail")
                            or item.get("title_catalog")
                        )
                        source_specs = item.get("specifications_json") or "[]"
                        started = time.monotonic()

                        async with db_lock:
                            db.begin_run(source_code, source_title)

                        cards: list[dict[str, Any]] = []
                        validated = 0
                        accepted = 0
                        review = 0
                        error: str | None = None

                        try:
                            await controller.checkpoint()
                            await submit_search(
                                search_page,
                                source_title,
                                args.timeout * 1000,
                                controller,
                            )
                            cards = await collect_search_cards(
                                search_page,
                                args.search_pages,
                                args.timeout * 1000,
                                controller,
                            )

                            selected = choose_candidates(
                                source_code,
                                source_title,
                                source_specs,
                                cards,
                                args.validate_top,
                            )

                            selected_codes = {
                                clean_text(row.get("candidate_product_code"))
                                for row in selected
                            }

                            # Сохраняем весь поисковый результат, даже если
                            # карточка не была выбрана для детальной проверки.
                            for card in cards:
                                match = fast_match(
                                    source_code,
                                    source_title,
                                    source_specs,
                                    clean_text(card.get("candidate_product_code")),
                                    clean_text(card.get("candidate_title")),
                                )
                                card["fast_score"] = round(match.score, 2)
                                card["fast_decision"] = match.decision
                                card["fast_reason"] = match.reason
                                card["final_decision"] = (
                                    "not_validated"
                                    if clean_text(card.get("candidate_product_code"))
                                    not in selected_codes
                                    else "pending"
                                )
                                card["candidate_specs"] = []
                                async with db_lock:
                                    db.save_candidate(source_code, card)
                                    db.commit()

                            for candidate in selected:
                                await controller.checkpoint()
                                candidate_code = clean_text(
                                    candidate.get("candidate_product_code")
                                )
                                candidate_url = clean_text(
                                    candidate.get("candidate_url")
                                )
                                if not candidate_url:
                                    continue

                                detail, offers = await capture_offers_response(
                                    detail_page,
                                    candidate_url,
                                    args.timeout * 1000,
                                    args.city_id,
                                    controller,
                                )

                                candidate_title_detail = clean_text(
                                    detail.get("candidate_title_detail")
                                    or candidate.get("candidate_title")
                                )
                                candidate_specs = detail.get("specifications") or []

                                result = detail_match(
                                    source_title,
                                    source_specs,
                                    candidate_title_detail,
                                    candidate_specs,
                                    float(candidate.get("fast_score") or 0),
                                )

                                candidate["candidate_title_detail"] = (
                                    candidate_title_detail
                                )
                                candidate["candidate_specs"] = candidate_specs
                                candidate["detail_score"] = round(result.score, 2)
                                candidate["final_decision"] = result.decision
                                candidate["detail_reason"] = result.reason

                                validated += 1
                                accepted += int(result.decision == "accepted")
                                review += int(result.decision == "review")

                                async with db_lock:
                                    db.save_candidate(source_code, candidate)
                                    if result.decision == "accepted" and offers:
                                        db.save_offers(
                                            source_code,
                                            candidate_code,
                                            offers,
                                        )
                                    db.commit()

                                await asyncio.sleep(
                                    random.uniform(
                                        max(0.2, args.min_delay / 2),
                                        max(0.4, args.max_delay / 2),
                                    )
                                )

                            if not cards:
                                error = "поиск не вернул карточек"
                            status = "ok" if cards else "error"

                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            error = str(exc)
                            status = "error"
                            await save_debug(
                                search_page,
                                debug_dir,
                                source_code,
                                "search",
                                error,
                            )
                            async with db_lock:
                                db.add_error(source_code, "compare", error)

                        async with db_lock:
                            db.finish_run(
                                source_code,
                                status,
                                len(cards),
                                validated,
                                accepted,
                                review,
                                error,
                            )
                            progress["done"] += 1
                            progress["ok"] += int(status == "ok")
                            progress["error"] += int(status == "error")

                            elapsed = time.monotonic() - started
                            print(
                                f"[W{worker_id}] {progress['done']}/{len(jobs)} "
                                f"{source_code} — cards={len(cards)}, "
                                f"checked={validated}, accepted={accepted}, "
                                f"review={review}, {elapsed:.1f}s"
                                + (f", ERROR: {error}" if error else "")
                            )

                        await asyncio.sleep(
                            random.uniform(args.min_delay, args.max_delay)
                        )
                        queue.task_done()
                finally:
                    await search_page.close()
                    await detail_page.close()

            tasks = [
                asyncio.create_task(worker(index + 1))
                for index in range(workers)
            ]
            await queue.join()
            await asyncio.gather(*tasks)

        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\nОстановлено. Уже завершенные товары сохранены в SQLite.")
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await context.close()

    export_reports(db, output, args.seller_name)
    db.conn.close()



def _price_position(
    own_price: float | int | None,
    benchmark_price: float | int | None,
    missing_status: str,
) -> tuple[str, float | None, float | None]:
    if benchmark_price is None:
        return missing_status, None, None
    if own_price is None:
        return "NO_OWN_PRICE", None, None

    difference = float(own_price) - float(benchmark_price)
    difference_pct = (
        round(difference / float(benchmark_price) * 100, 2)
        if benchmark_price
        else None
    )

    if difference < 0:
        status = "WIN"
    elif difference == 0:
        status = "TIE"
    elif difference_pct is not None and difference_pct <= 3:
        status = "NEAR"
    else:
        status = "LOSE"

    return status, round(difference, 2), difference_pct


def report_rows(
    db: Database,
    seller_name: str,
    product_codes: list[str] | None = None,
) -> list[dict[str, Any]]:
    requested_codes = [clean_text(value) for value in (product_codes or []) if clean_text(value)]
    code_filter = ""
    params: list[Any] = []
    if product_codes is not None:
        if not requested_codes:
            return []
        code_filter = f"WHERE c.product_code IN ({','.join('?' for _ in requested_codes)})"
        params = requested_codes
    sources = [
        dict(row)
        for row in db.conn.execute(
            f"""
            SELECT
                c.product_code,
                COALESCE(NULLIF(d.title_detail, ''), c.title_catalog) AS title,
                c.catalog_price_kzt AS own_price_kzt,
                c.product_url,
                c.catalog_rating,
                c.catalog_reviews,
                r.status AS scan_status,
                r.candidates_found,
                r.candidates_validated,
                r.accepted_count,
                r.review_count,
                r.error,
                r.finished_at
            FROM catalog_products c
            LEFT JOIN product_details d ON d.product_code=c.product_code
            LEFT JOIN market_search_runs r
                ON r.source_product_code=c.product_code
            {code_filter}
            ORDER BY c.page_number, c.position_on_page, c.product_code
            """,
            params,
        ).fetchall()
    ]

    result: list[dict[str, Any]] = []
    seller_norm = normalize(seller_name)

    for source in sources:
        code = clean_text(source["product_code"])
        scan_status = clean_text(source.get("scan_status"))
        own_catalog_price = source.get("own_price_kzt")

        accepted = [
            dict(row)
            for row in db.conn.execute(
                """
                SELECT * FROM market_candidates
                WHERE source_product_code=?
                  AND final_decision='accepted'
                  AND candidate_price_kzt IS NOT NULL
                ORDER BY candidate_price_kzt, detail_score DESC
                """,
                (code,),
            ).fetchall()
        ]
        review_cards = [
            dict(row)
            for row in db.conn.execute(
                """
                SELECT * FROM market_candidates
                WHERE source_product_code=?
                  AND final_decision='review'
                  AND candidate_price_kzt IS NOT NULL
                ORDER BY candidate_price_kzt, detail_score DESC
                """,
                (code,),
            ).fetchall()
        ]
        cheapest_review = review_cards[0] if review_cards else {}

        exact_cards = [
            row for row in accepted
            if clean_text(row.get("candidate_product_code")) == code
        ]
        analog_cards = [
            row for row in accepted
            if clean_text(row.get("candidate_product_code")) != code
        ]

        exact_card = exact_cards[0] if exact_cards else {}
        exact_card_price = exact_card.get("candidate_price_kzt")

        analog_prices = [
            int(row["candidate_price_kzt"])
            for row in analog_cards
            if row.get("candidate_price_kzt") is not None
        ]
        analog_min = min(analog_prices) if analog_prices else None
        analog_max = max(analog_prices) if analog_prices else None
        analog_average = (
            round(statistics.mean(analog_prices), 2)
            if analog_prices else None
        )
        analog_median = (
            statistics.median(analog_prices)
            if analog_prices else None
        )
        cheapest_analog = analog_cards[0] if analog_cards else {}

        all_offers = [
            dict(row)
            for row in db.conn.execute(
                """
                SELECT * FROM market_seller_offers
                WHERE source_product_code=?
                ORDER BY price_kzt
                """,
                (code,),
            ).fetchall()
        ]

        exact_offers = [
            row for row in all_offers
            if clean_text(row.get("candidate_product_code")) == code
        ]

        own_exact_offers = [
            row for row in exact_offers
            if seller_norm
            and seller_norm in normalize(
                f"{row.get('merchant_id', '')} "
                f"{row.get('merchant_name', '')}"
            )
        ]
        exact_competitor_offers = [
            row for row in exact_offers
            if not (
                seller_norm
                and seller_norm in normalize(
                    f"{row.get('merchant_id', '')} "
                    f"{row.get('merchant_name', '')}"
                )
            )
        ]

        own_exact_offer = own_exact_offers[0] if own_exact_offers else {}
        exact_competitor = (
            exact_competitor_offers[0]
            if exact_competitor_offers else {}
        )

        captured_own_price = own_exact_offer.get("price_kzt")
        exact_competitor_price = exact_competitor.get("price_kzt")
        own_basis_price = (
            captured_own_price
            if captured_own_price is not None
            else own_catalog_price
        )

        if not scan_status:
            exact_status = "NOT_SCANNED"
            analog_status = "NOT_SCANNED"
            price_status = "NOT_SCANNED"
            exact_difference = exact_difference_pct = None
            analog_difference = analog_difference_pct = None
        elif scan_status == "error":
            exact_status = "SCAN_ERROR"
            analog_status = "SCAN_ERROR"
            price_status = "SCAN_ERROR"
            exact_difference = exact_difference_pct = None
            analog_difference = analog_difference_pct = None
        else:
            (
                exact_status,
                exact_difference,
                exact_difference_pct,
            ) = _price_position(
                own_basis_price,
                exact_competitor_price,
                "NO_EXACT_COMPETITOR",
            )
            (
                analog_status,
                analog_difference,
                analog_difference_pct,
            ) = _price_position(
                own_basis_price,
                analog_min,
                "NO_ANALOG_MATCH",
            )

            if exact_competitor_price is not None:
                price_status = f"EXACT_{exact_status}"
            elif analog_min is not None:
                price_status = f"ANALOG_{analog_status}"
            elif exact_card:
                price_status = "ONLY_OWN_OFFER"
            else:
                price_status = "NO_ACCEPTED_MATCH"

        result.append(
            {
                **source,
                "own_effective_price_kzt": own_basis_price,

                # Точное сравнение: та же карточка Kaspi, но другой продавец.
                "exact_card_found": bool(exact_card),
                "exact_card_price_kzt": exact_card_price,
                "exact_offer_count": len(exact_offers),
                "exact_own_offer_price_kzt": captured_own_price,
                "exact_competitor_count": len(exact_competitor_offers),
                "exact_competitor_min_price_kzt": exact_competitor_price,
                "exact_competitor_name": exact_competitor.get(
                    "merchant_name"
                ),
                "exact_difference_kzt": exact_difference,
                "exact_difference_pct": exact_difference_pct,
                "exact_price_status": exact_status,

                # Аналоги: другая карточка товара, но совпадающие основные
                # характеристики, размеры, индексы, сезонность и тип.
                "analog_accepted_count": len(analog_cards),
                "analog_min_price_kzt": analog_min,
                "analog_max_price_kzt": analog_max,
                "analog_average_price_kzt": analog_average,
                "analog_median_price_kzt": analog_median,
                "cheapest_analog_code": cheapest_analog.get(
                    "candidate_product_code"
                ),
                "cheapest_analog_title": cheapest_analog.get(
                    "candidate_title"
                ),
                "cheapest_analog_url": cheapest_analog.get(
                    "candidate_url"
                ),
                "analog_difference_kzt": analog_difference,
                "analog_difference_pct": analog_difference_pct,
                "analog_price_status": analog_status,

                # Кандидаты на ручную проверку не влияют на итоговую цену,
                # но показываются отдельно для контроля качества сопоставления.
                "review_candidate_count": len(review_cards),
                "review_min_price_kzt": cheapest_review.get(
                    "candidate_price_kzt"
                ),
                "review_candidate_code": cheapest_review.get(
                    "candidate_product_code"
                ),
                "review_candidate_title": cheapest_review.get(
                    "candidate_title"
                ),
                "review_candidate_score": cheapest_review.get(
                    "detail_score"
                ),

                # Итоговый статус: точное сравнение имеет приоритет.
                "price_status": price_status,

                # Совместимость с предыдущими отчетами.
                "market_accepted_count": len(analog_cards),
                "market_min_price_kzt": analog_min,
                "market_max_price_kzt": analog_max,
                "market_average_price_kzt": analog_average,
                "market_median_price_kzt": analog_median,
                "cheapest_candidate_code": cheapest_analog.get(
                    "candidate_product_code"
                ),
                "cheapest_candidate_title": cheapest_analog.get(
                    "candidate_title"
                ),
                "cheapest_candidate_url": cheapest_analog.get(
                    "candidate_url"
                ),
                "difference_to_market_min_kzt": analog_difference,
                "difference_to_market_min_pct": analog_difference_pct,
                "captured_seller_offers": len(all_offers),
                "captured_own_offer_price_kzt": captured_own_price,
                "captured_min_competitor_offer_kzt": (
                    exact_competitor_price
                ),
                "captured_min_competitor_name": exact_competitor.get(
                    "merchant_name"
                ),
            }
        )

    return result


def export_reports(db: Database, output: Path, seller_name: str) -> None:
    output.mkdir(parents=True, exist_ok=True)

    comparison = report_rows(db, seller_name)
    comparison_fields = [
        "product_code",
        "title",
        "own_price_kzt",
        "own_effective_price_kzt",
        "product_url",
        "scan_status",
        "candidates_found",
        "candidates_validated",
        "accepted_count",
        "review_count",

        "exact_card_found",
        "exact_card_price_kzt",
        "exact_offer_count",
        "exact_own_offer_price_kzt",
        "exact_competitor_count",
        "exact_competitor_min_price_kzt",
        "exact_competitor_name",
        "exact_difference_kzt",
        "exact_difference_pct",
        "exact_price_status",

        "analog_accepted_count",
        "analog_min_price_kzt",
        "analog_max_price_kzt",
        "analog_average_price_kzt",
        "analog_median_price_kzt",
        "cheapest_analog_code",
        "cheapest_analog_title",
        "cheapest_analog_url",
        "analog_difference_kzt",
        "analog_difference_pct",
        "analog_price_status",

        "review_candidate_count",
        "review_min_price_kzt",
        "review_candidate_code",
        "review_candidate_title",
        "review_candidate_score",

        "price_status",
        "captured_seller_offers",
        "error",
        "finished_at",
    ]
    save_csv(output / "price_comparison.csv", comparison, comparison_fields)
    save_json(output / "price_comparison.json", comparison)

    processed = [
        row for row in comparison
        if clean_text(row.get("scan_status"))
    ]
    save_csv(
        output / "price_comparison_processed.csv",
        processed,
        comparison_fields,
    )
    save_json(
        output / "price_comparison_processed.json",
        processed,
    )

    candidates = [
        dict(row)
        for row in db.conn.execute(
            """
            SELECT * FROM market_candidates
            ORDER BY source_product_code,
                     CASE final_decision
                       WHEN 'accepted' THEN 1
                       WHEN 'review' THEN 2
                       WHEN 'pending' THEN 3
                       ELSE 4
                     END,
                     candidate_price_kzt,
                     detail_score DESC
            """
        ).fetchall()
    ]
    candidate_fields = [
        "source_product_code",
        "candidate_product_code",
        "search_page",
        "search_position",
        "candidate_title",
        "candidate_url",
        "candidate_price_kzt",
        "candidate_rating",
        "candidate_reviews",
        "fast_score",
        "fast_decision",
        "fast_reason",
        "candidate_title_detail",
        "candidate_specs_json",
        "detail_score",
        "final_decision",
        "detail_reason",
        "checked_at",
    ]
    save_csv(output / "all_candidates.csv", candidates, candidate_fields)
    save_json(output / "all_candidates.json", candidates)

    accepted = [
        row for row in candidates
        if row.get("final_decision") == "accepted"
    ]
    review = [
        row for row in candidates
        if row.get("final_decision") == "review"
    ]
    save_csv(
        output / "accepted_candidates.csv",
        accepted,
        candidate_fields,
    )
    save_csv(
        output / "review_candidates.csv",
        review,
        candidate_fields,
    )

    offers = [
        dict(row)
        for row in db.conn.execute(
            """
            SELECT * FROM market_seller_offers
            ORDER BY source_product_code, candidate_product_code, price_kzt
            """
        ).fetchall()
    ]
    offer_fields = [
        "source_product_code",
        "candidate_product_code",
        "merchant_id",
        "merchant_name",
        "merchant_sku",
        "price_kzt",
        "merchant_rating",
        "merchant_reviews",
        "captured_at",
    ]
    save_csv(
        output / "captured_seller_offers.csv",
        offers,
        offer_fields,
    )
    save_json(output / "captured_seller_offers.json", offers)

    statuses: dict[str, int] = {}
    for row in comparison:
        status = clean_text(row.get("price_status") or "NOT_SCANNED")
        statuses[status] = statuses.get(status, 0) + 1

    dashboard_rows = [
        row for row in comparison
        if clean_text(row.get("scan_status"))
    ]

    rows_html = []
    for row in dashboard_rows:
        status = html.escape(clean_text(row.get("price_status")))
        rows_html.append(
            "<tr>"
            f"<td>{html.escape(clean_text(row.get('product_code')))}</td>"
            f"<td>{html.escape(clean_text(row.get('title')))}</td>"
            f"<td>{html.escape(clean_text(row.get('own_effective_price_kzt')))}</td>"
            f"<td>{html.escape(clean_text(row.get('exact_competitor_min_price_kzt')))}</td>"
            f"<td>{html.escape(clean_text(row.get('exact_competitor_name')))}</td>"
            f"<td>{html.escape(clean_text(row.get('analog_min_price_kzt')))}</td>"
            f"<td>{html.escape(clean_text(row.get('cheapest_analog_title')))}</td>"
            f"<td>{html.escape(clean_text(row.get('analog_difference_pct')))}</td>"
            f"<td>{html.escape(clean_text(row.get('review_candidate_title')))}</td>"
            f"<td>{html.escape(clean_text(row.get('review_min_price_kzt')))}</td>"
            f"<td class='{status}'>{status}</td>"
            "</tr>"
        )

    total = len(comparison)
    processed_count = len(dashboard_rows)
    not_scanned_count = total - processed_count

    dashboard = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Kaspi Search Compare V8.2</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 24px; color: #222; }}
.cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }}
.card {{ border: 1px solid #ddd; border-radius: 8px; padding: 12px 16px; min-width: 140px; }}
.note {{ max-width: 1100px; background: #f6f7f8; padding: 12px; border-radius: 8px; margin-bottom: 18px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border: 1px solid #ddd; padding: 7px; text-align: left; }}
th {{ background: #f2f2f2; position: sticky; top: 0; }}
.EXACT_WIN, .EXACT_TIE, .ANALOG_WIN, .ANALOG_TIE {{ background: #d9ead3; }}
.EXACT_NEAR, .ANALOG_NEAR {{ background: #fff2cc; }}
.EXACT_LOSE, .ANALOG_LOSE {{ background: #f4cccc; }}
.NO_ACCEPTED_MATCH, .NOT_SCANNED, .NO_EXACT_COMPETITOR, .NO_ANALOG_MATCH {{ background: #eeeeee; }}
.SCAN_ERROR {{ background: #f4cccc; }}
</style>
</head>
<body>
<h1>Kaspi Search Compare V8.2</h1>
<p>Сформировано: {html.escape(now_iso())}</p>
<div class="note">
<b>Точное сравнение</b> — та же карточка Kaspi и другой продавец.
<br>
<b>Сравнение аналогов</b> — другая карточка, но совпадающие основные
характеристики товара.
</div>
<div class="cards">
<div class="card"><b>Всего в каталоге</b><br>{total}</div>
<div class="card"><b>Обработано</b><br>{processed_count}</div>
<div class="card"><b>Не обработано</b><br>{not_scanned_count}</div>
{''.join(
    f"<div class='card'><b>{html.escape(k)}</b><br>{v}</div>"
    for k, v in sorted(statuses.items())
)}
</div>
<table>
<thead>
<tr>
<th>Код</th>
<th>Товар Unityre</th>
<th>Цена Unityre</th>
<th>Точный конкурент</th>
<th>Продавец точного товара</th>
<th>Минимум среди аналогов</th>
<th>Самый дешёвый аналог</th>
<th>Разница с аналогом, %</th>
<th>Кандидат на проверку</th>
<th>Цена кандидата</th>
<th>Итоговый статус</th>
</tr>
</thead>
<tbody>
{''.join(rows_html)}
</tbody>
</table>
</body>
</html>"""
    (output / "dashboard.html").write_text(
        dashboard,
        encoding="utf-8",
    )

    print(f"[Отчет] {output / 'price_comparison.csv'}")
    print(
        f"[Отчет] "
        f"{output / 'price_comparison_processed.csv'}"
    )
    print(f"[Отчет] {output / 'accepted_candidates.csv'}")
    print(f"[Отчет] {output / 'review_candidates.csv'}")
    print(f"[Отчет] {output / 'dashboard.html'}")


def report_command(args: argparse.Namespace) -> None:
    db = Database(Path(args.db))
    try:
        export_reports(db, Path(args.output), args.seller_name)
    finally:
        db.conn.close()


def status_command(args: argparse.Namespace) -> None:
    db = Database(Path(args.db))
    try:
        catalog = db.conn.execute(
            "SELECT COUNT(*) FROM catalog_products"
        ).fetchone()[0]
        completed = db.conn.execute(
            "SELECT COUNT(*) FROM market_search_runs WHERE status='ok'"
        ).fetchone()[0]
        errors = db.conn.execute(
            "SELECT COUNT(*) FROM market_search_runs WHERE status='error'"
        ).fetchone()[0]
        accepted = db.conn.execute(
            """
            SELECT COUNT(*) FROM market_candidates
            WHERE final_decision='accepted'
            """
        ).fetchone()[0]
        reviews = db.conn.execute(
            """
            SELECT COUNT(*) FROM market_candidates
            WHERE final_decision='review'
            """
        ).fetchone()[0]
        print(f"Каталог: {catalog}")
        print(f"Сравнение завершено: {completed}")
        print(f"Ошибки: {errors}")
        print(f"Принятые кандидаты: {accepted}")
        print(f"Кандидаты на ручную проверку: {reviews}")
        print(f"Осталось: {max(0, catalog - completed)}")
    finally:
        db.conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Поиск и интеллектуальная сверка цен Kaspi V8.2."
    )
    parser.add_argument(
        "--db",
        default=r"output_market_v7\kaspi_market.db",
        help="Путь к существующей базе V7.",
    )
    parser.add_argument(
        "--output",
        default="output_search_v8",
        help="Каталог отчетов V8.",
    )
    parser.add_argument(
        "--profile",
        default=".kaspi_profile",
        help="Профиль Chromium.",
    )
    parser.add_argument(
        "--seller-name",
        default="Unityre",
        help="Название собственного продавца.",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    compare = sub.add_parser(
        "compare",
        help="Выполнить поиск и сравнение.",
    )
    compare.add_argument("--workers", type=int, default=2)
    compare.add_argument("--limit", type=int, default=0)
    compare.add_argument("--codes", default="")
    compare.add_argument("--search-pages", type=int, default=2)
    compare.add_argument("--validate-top", type=int, default=4)
    compare.add_argument("--timeout", type=int, default=45)
    compare.add_argument("--min-delay", type=float, default=1.5)
    compare.add_argument("--max-delay", type=float, default=3.0)
    compare.add_argument("--city-id", default=DEFAULT_CITY_ID)
    compare.add_argument("--refresh", action="store_true")
    compare.add_argument("--headless", action="store_true")

    sub.add_parser("report", help="Перестроить отчеты из базы.")
    sub.add_parser("status", help="Показать прогресс.")

    return parser


async def async_main(args: argparse.Namespace) -> None:
    if args.command == "compare":
        await compare_command(args)
    elif args.command == "report":
        report_command(args)
    elif args.command == "status":
        status_command(args)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(async_main(args))
        return 0
    except KeyboardInterrupt:
        print("\nОстановлено пользователем. Завершенные товары сохранены.")
        return 130
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
