from __future__ import annotations

import re
from copy import deepcopy
from string import Formatter
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse, urlunparse


RULE_LIST_FIELDS = (
    "allowed_hosts",
    "seller_path_patterns",
    "product_path_patterns",
    "product_query_keys",
    "seller_name_patterns",
    "examples",
)
RULE_TEXT_FIELDS = (
    "seller_url_template",
    "product_url_template",
    "seller_name_template",
    "bare_id_pattern",
)
TEMPLATE_FIELDS = {"seller_id", "seller_id_human", "seller_name", "product_id", "product_slug"}


DEFAULT_MARKETPLACE_SOURCE_RULES: dict[str, dict[str, Any]] = {
    "kaspi": {
        "allowed_hosts": ["kaspi.kz", "www.kaspi.kz"],
        "seller_path_patterns": [
            r"/shop/m/(?P<seller_id>[^/?#]+)",
            r"/shop/info/merchant/(?P<seller_id>[^/?#]+)",
        ],
        "product_path_patterns": [],
        "product_query_keys": ["masterSku", "productCode", "merchantSku"],
        "seller_name_patterns": [],
        "seller_url_template": "https://kaspi.kz/shop/m/{seller_id}/products",
        "product_url_template": "",
        "seller_name_template": "{seller_id}",
        "bare_id_pattern": r"^[A-Za-z0-9._-]{2,160}$",
        "examples": ["12345678", "https://kaspi.kz/shop/m/12345678/products"],
    },
    "ozon": {
        "allowed_hosts": ["ozon.ru", "www.ozon.ru"],
        "seller_path_patterns": [r"/(?:seller|продавец)/(?P<seller_id>[^/?#]+)"],
        "product_path_patterns": [],
        "product_query_keys": [],
        "seller_name_patterns": [],
        "seller_url_template": "https://www.ozon.ru/продавец/{seller_id}/",
        "product_url_template": "",
        "seller_name_template": "{seller_id_human}",
        "bare_id_pattern": r"^[A-Za-z0-9._-]{2,160}$",
        "examples": ["example-store-123", "https://www.ozon.ru/продавец/example-store-123/"],
    },
    "ozon_kz": {
        "allowed_hosts": ["ozon.kz", "www.ozon.kz"],
        "seller_path_patterns": [r"/(?:seller|продавец)/(?P<seller_id>[^/?#]+)"],
        "product_path_patterns": [],
        "product_query_keys": [],
        "seller_name_patterns": [],
        "seller_url_template": "https://ozon.kz/продавец/{seller_id}/",
        "product_url_template": "",
        "seller_name_template": "{seller_id_human}",
        "bare_id_pattern": r"^[A-Za-z0-9._-]{2,160}$",
        "examples": ["example-store-456", "https://ozon.kz/продавец/example-store-456/"],
    },
    "halyk_market": {
        "allowed_hosts": ["halykmarket.kz", "www.halykmarket.kz"],
        "seller_path_patterns": [r"/(?:merchant|seller|shop)/(?P<seller_id>[^/?#]+)"],
        "product_path_patterns": [],
        "product_query_keys": ["productId", "product_id"],
        "seller_name_patterns": [
            r"(?:^|[?&,])(?:f=)?merchantName:(?P<seller_name>[^,&]+)",
        ],
        "seller_url_template": "https://halykmarket.kz/merchant/{seller_id}",
        "product_url_template": "",
        "seller_name_template": "{seller_id}",
        "bare_id_pattern": r"^[A-Za-z0-9._-]{2,160}$",
        "examples": ["12345", "https://halykmarket.kz/merchant/12345"],
    },
    "forte_market": {
        "allowed_hosts": ["market.forte.kz", "forte.kz", "www.forte.kz"],
        "seller_path_patterns": [
            r"/merchant/(?P<seller_id>[^/?#]+)",
            r"/merchant-products/(?P<seller_id>[^/?#]+)",
        ],
        "product_path_patterns": [r"/items/(?P<product_slug>[^/?#]+)"],
        "product_query_keys": ["productId", "product_id"],
        "seller_name_patterns": [],
        "seller_url_template": "https://market.forte.kz/merchant/{seller_id}?type=all",
        "product_url_template": "https://market.forte.kz/items/{product_slug}",
        "seller_name_template": "{seller_id}",
        "bare_id_pattern": r"^[A-Za-z0-9._-]{2,160}$",
        "examples": [
            "example-merchant-123",
            "https://market.forte.kz/merchant/example-merchant-123?type=all",
        ],
    },
    "wildberries": {
        "allowed_hosts": [
            "global.wildberries.ru", "www.wildberries.ru", "wildberries.ru",
        ],
        "seller_path_patterns": [r"/seller/(?P<seller_id>\d+)"],
        "product_path_patterns": [
            r"/catalog/(?P<product_id>\d+)/detail(?:\.aspx)?",
        ],
        "product_query_keys": [],
        "seller_name_patterns": [],
        "seller_url_template": "https://global.wildberries.ru/seller/{seller_id}",
        "product_url_template": "https://global.wildberries.ru/catalog/{product_id}/detail.aspx",
        "seller_name_template": "Продавец {seller_id}",
        "bare_id_pattern": r"^\d{3,12}$",
        "examples": [
            "123456789",
            "https://global.wildberries.ru/seller/123456789",
        ],
    },
}


def _list_value(value: Any) -> list[str]:
    if isinstance(value, str):
        source = value.replace("\r", "").split("\n")
    elif isinstance(value, list):
        source = value
    else:
        source = []
    return list(dict.fromkeys(str(item or "").strip() for item in source if str(item or "").strip()))


def _template_fields(value: str) -> set[str]:
    return {
        str(field_name)
        for _, field_name, _, _ in Formatter().parse(value)
        if field_name
    }


def _validate_template(code: str, field: str, value: str, hosts: set[str]) -> None:
    unknown = _template_fields(value) - TEMPLATE_FIELDS
    if unknown:
        raise ValueError(f"{code}: неизвестные поля шаблона {field}: {', '.join(sorted(unknown))}.")
    if not value:
        return
    sample = {
        "seller_id": "seller-1",
        "seller_id_human": "seller 1",
        "seller_name": "Seller",
        "product_id": "product-1",
        "product_slug": "product-1",
    }
    try:
        rendered = value.format_map(sample)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"{code}: некорректный шаблон {field}.") from exc
    if field.endswith("url_template"):
        parsed = urlparse(rendered)
        if parsed.scheme != "https" or str(parsed.hostname or "").casefold() not in hosts:
            raise ValueError(f"{code}: шаблон {field} должен вести на разрешённый HTTPS-домен.")


def validate_marketplace_source_rules(payload: Any) -> dict[str, dict[str, Any]]:
    raw = payload.get("marketplaces") if isinstance(payload, dict) and isinstance(payload.get("marketplaces"), dict) else payload
    if not isinstance(raw, dict):
        raise ValueError("Передайте объект marketplace source rules.")
    result = deepcopy(DEFAULT_MARKETPLACE_SOURCE_RULES)
    unknown_codes = set(str(code) for code in raw) - set(result)
    if unknown_codes:
        raise ValueError("Неизвестные площадки в правилах: " + ", ".join(sorted(unknown_codes)) + ".")
    for code, default in result.items():
        incoming = raw.get(code, {})
        if not isinstance(incoming, dict):
            raise ValueError(f"{code}: правило должно быть объектом.")
        rule = dict(default)
        for field in RULE_LIST_FIELDS:
            if field in incoming:
                rule[field] = _list_value(incoming[field])
        for field in RULE_TEXT_FIELDS:
            if field in incoming:
                rule[field] = str(incoming[field] or "").strip()
            if len(str(rule[field])) > 1024:
                raise ValueError(f"{code}: значение {field} слишком длинное.")
        hosts = {host.casefold() for host in rule["allowed_hosts"]}
        if not hosts or any(not re.fullmatch(r"[a-z0-9.-]{3,253}", host) for host in hosts):
            raise ValueError(f"{code}: укажите корректные разрешённые домены.")
        rule["allowed_hosts"] = sorted(hosts)
        if not rule["seller_path_patterns"]:
            raise ValueError(f"{code}: нужен хотя бы один шаблон seller path.")
        for field, group_names in (
            ("seller_path_patterns", {"seller_id"}),
            ("product_path_patterns", {"product_id", "product_slug"}),
            ("seller_name_patterns", {"seller_name"}),
        ):
            for pattern in rule[field]:
                if len(pattern) > 512:
                    raise ValueError(f"{code}: regex в {field} слишком длинный.")
                try:
                    compiled = re.compile(pattern, re.IGNORECASE)
                except re.error as exc:
                    raise ValueError(f"{code}: ошибка regex в {field}: {exc}.") from exc
                if not (set(compiled.groupindex) & group_names):
                    raise ValueError(f"{code}: regex в {field} должен содержать named group {sorted(group_names)}.")
        try:
            re.compile(rule["bare_id_pattern"])
        except re.error as exc:
            raise ValueError(f"{code}: ошибка regex bare_id_pattern: {exc}.") from exc
        if "{seller_id}" not in rule["seller_url_template"]:
            raise ValueError(f"{code}: seller_url_template должен содержать {{seller_id}}.")
        for field in ("seller_url_template", "product_url_template", "seller_name_template"):
            _validate_template(code, field, rule[field], hosts)
        if any(
            not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", key)
            for key in rule["product_query_keys"]
        ):
            raise ValueError(f"{code}: некорректный ключ product ID в query.")
        if any(len(example) > 2048 for example in rule["examples"]):
            raise ValueError(f"{code}: пример ссылки слишком длинный.")
        rule["product_query_keys"] = rule["product_query_keys"][:20]
        rule["examples"] = rule["examples"][:10]
        result[code] = rule
    return result


def merged_marketplace_source_rules(stored: Any) -> dict[str, dict[str, Any]]:
    try:
        return validate_marketplace_source_rules(stored if isinstance(stored, dict) else {})
    except ValueError:
        return deepcopy(DEFAULT_MARKETPLACE_SOURCE_RULES)


def _first_group(patterns: list[str], source: str, groups: tuple[str, ...]) -> dict[str, str]:
    for pattern in patterns:
        match = re.search(pattern, source, re.IGNORECASE)
        if not match:
            continue
        values = {
            group: unquote(str(match.groupdict().get(group) or "")).strip()
            for group in groups
        }
        if any(values.values()):
            return values
    return {group: "" for group in groups}


def _render(value: str, fields: dict[str, str]) -> str:
    encoded = {key: quote(str(item or "").strip(), safe="-._~") for key, item in fields.items()}
    return value.format_map(encoded) if value else ""


def _render_text(value: str, fields: dict[str, str]) -> str:
    return value.format_map({key: str(item or "").strip() for key, item in fields.items()}) if value else ""


def parse_marketplace_source(
    source_input: str,
    marketplace_code: str = "",
    rules: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    value = str(source_input or "").strip()
    if not value or len(value) > 2048 or any(ord(char) < 32 for char in value):
        raise ValueError("Укажите ссылку, ID продавца или slug магазина.")
    active_rules = rules or deepcopy(DEFAULT_MARKETPLACE_SOURCE_RULES)
    expected = str(marketplace_code or "").strip().casefold()
    if expected and expected not in active_rules:
        raise ValueError("Неизвестная площадка.")

    normalized = value
    if value.startswith("//"):
        normalized = "https:" + value
    elif "://" not in value and re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/|$)", value):
        normalized = "https://" + value
    parsed = urlparse(normalized)
    is_url = bool(parsed.scheme or parsed.netloc)
    host = str(parsed.hostname or "").casefold()

    if is_url:
        if parsed.scheme != "https" or not host:
            raise ValueError("Ссылки принимаются только по HTTPS.")
        detected_codes = [
            code for code, rule in active_rules.items()
            if host in {str(item).casefold() for item in rule["allowed_hosts"]}
        ]
        if not detected_codes:
            raise ValueError("Ссылка не относится к поддерживаемой площадке.")
        detected_code = detected_codes[0]
        if expected and expected != detected_code:
            raise ValueError(f"Ссылка относится к другой площадке: {detected_code}.")
    else:
        if not expected:
            raise ValueError("Для ID продавца выберите площадку.")
        detected_code = expected

    rule = active_rules[detected_code]
    seller_id = ""
    seller_name = ""
    product_id = ""
    product_slug = ""
    if is_url:
        decoded = unquote(urlunparse(("", "", parsed.path, parsed.params, parsed.query, parsed.fragment)))
        seller_id = _first_group(rule["seller_path_patterns"], decoded, ("seller_id",))["seller_id"]
        product_values = _first_group(rule["product_path_patterns"], decoded, ("product_id", "product_slug"))
        product_id = product_values["product_id"]
        product_slug = product_values["product_slug"]
        query = parse_qs(parsed.query, keep_blank_values=False)
        for key in rule["product_query_keys"]:
            raw_values = query.get(key) or []
            if raw_values and str(raw_values[0]).strip():
                product_id = unquote(str(raw_values[0])).strip()
                break
        seller_name = _first_group(rule["seller_name_patterns"], decoded, ("seller_name",))["seller_name"]
    else:
        if not re.fullmatch(rule["bare_id_pattern"], value):
            raise ValueError("ID содержит недопустимые символы для выбранной площадки.")
        seller_id = value

    fields = {
        "seller_id": seller_id,
        "seller_id_human": re.sub(r"[-_]+", " ", seller_id).strip(),
        "seller_name": seller_name,
        "product_id": product_id,
        "product_slug": product_slug or product_id,
    }
    if seller_id:
        seller_name = seller_name or _render_text(rule["seller_name_template"], fields) or seller_id
        fields["seller_name"] = seller_name
        canonical = _render(rule["seller_url_template"], fields)
        scope = "seller"
        identifier = seller_id
    elif product_id or product_slug:
        identifier_value = product_slug or product_id
        canonical = _render(rule["product_url_template"], fields) or normalized
        seller_name = identifier_value
        scope = "product"
        identifier = f"product:{identifier_value}"
    else:
        raise ValueError("Не удалось извлечь ID продавца или товара по текущим правилам площадки.")

    canonical_parsed = urlparse(canonical)
    allowed_hosts = {str(item).casefold() for item in rule["allowed_hosts"]}
    if canonical_parsed.scheme != "https" or str(canonical_parsed.hostname or "").casefold() not in allowed_hosts:
        raise ValueError("Собранная ссылка не прошла проверку разрешённого домена.")
    return {
        "marketplace_code": detected_code,
        "seller_identifier": identifier,
        "seller_name": seller_name,
        "seller_url": canonical,
        "source_scope": scope,
        "product_id": product_id,
        "product_slug": product_slug,
        "host": host or str(canonical_parsed.hostname or "").casefold(),
        "input_type": "url" if is_url else "seller_id",
        "source_input": value,
    }
