from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


CAPACITY_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d+(?:\.\d+)?)\s*(TB|T|GB|G)(?![A-Za-z])",
    re.IGNORECASE,
)
NON_WORD_RE = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff.+-]+")

BRAND_ALIASES: dict[str, tuple[str, ...]] = {
    "APPLE": ("macbook", "iphone", "ipad", "imac", "apple", "苹果"),
    "HUAWEI": ("huawei", "华为", "pura", "mate"),
    "XIAOMI": ("xiaomi", "redmi", "小米", "红米"),
    "OPPO": ("oppo",),
    "VIVO": ("vivo", "iqoo"),
}

QUERY_PHRASES = (
    "设备参考价格",
    "官方参考价格",
    "当前价格",
    "参考价格",
    "设备价格",
    "帮我查询",
    "帮我查",
    "查一下",
    "多少钱",
    "什么价格",
    "价格多少",
    "请问",
    "查询",
    "售价",
    "价钱",
    "价格",
    "大概",
    "现在",
    "目前",
    "官方",
    "商城",
    "设备",
    "这个型号呢",
    "这个型号",
    "该型号",
    "这个",
    "那个",
    "该款",
    "这款",
    "那款",
    "这台",
    "那台",
)
GENERIC_MODEL_WORDS = frozenset(
    {
        "",
        "呢",
        "这个",
        "那个",
        "该",
        "这款",
        "那款",
        "这台",
        "那台",
        "型号",
        "手机",
        "电脑",
        "平板",
        "手表",
        "耳机",
        "机器",
        "产品",
    }
)


@dataclass(frozen=True, slots=True)
class ParsedDeviceQuery:
    normalized: str
    brand_code: str | None
    model_text: str
    terms: tuple[str, ...]
    capacities: tuple[str, ...]
    sufficient: bool


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    normalized = NON_WORD_RE.sub(" ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_capacity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).upper()
    normalized = normalized.replace(" ", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(TB|T|GB|G)", normalized)
    if not match:
        return normalized
    unit = "TB" if match.group(2) in {"T", "TB"} else "GB"
    return f"{match.group(1)}{unit}"


def extract_capacity_tokens(*values: str) -> tuple[str, ...]:
    found: list[str] = []
    for value in values:
        normalized = unicodedata.normalize("NFKC", value)
        for match in CAPACITY_RE.finditer(normalized):
            token = normalize_capacity(
                f"{match.group(1)}{match.group(2)}"
            )
            if token not in found:
                found.append(token)
    return tuple(found)


def parse_device_query(question: str) -> ParsedDeviceQuery:
    normalized = normalize_text(question)
    brand_code, matched_alias = _detect_brand(normalized)
    capacities = extract_capacity_tokens(normalized)

    model_text = normalized
    if matched_alias:
        model_text = model_text.replace(matched_alias, " ")
    model_text = CAPACITY_RE.sub(" ", model_text)
    for phrase in QUERY_PHRASES:
        model_text = model_text.replace(phrase, " ")
    model_text = re.sub(r"\s+", " ", model_text).strip(" .+-")

    tokens = [
        token
        for token in model_text.split()
        if token not in GENERIC_MODEL_WORDS
    ]
    model_text = " ".join(tokens)
    terms = _unique_terms(model_text, tokens)
    has_meaningful_text = any(
        any(
            character.isalpha()
            or "\u4e00" <= character <= "\u9fff"
            for character in token
        )
        for token in tokens
    )
    sufficient = bool(model_text) and (
        brand_code is not None
        or has_meaningful_text
        or len(model_text) >= 4
    )
    return ParsedDeviceQuery(
        normalized=normalized,
        brand_code=brand_code,
        model_text=model_text,
        terms=terms,
        capacities=capacities,
        sufficient=sufficient,
    )


def _detect_brand(value: str) -> tuple[str | None, str | None]:
    matches: list[tuple[int, str, str]] = []
    for code, aliases in BRAND_ALIASES.items():
        for alias in aliases:
            position = value.find(alias)
            if position >= 0:
                matches.append((position, code, alias))
    if not matches:
        return None, None
    _, code, alias = min(matches, key=lambda item: (item[0], -len(item[2])))
    return code, alias


def _unique_terms(model_text: str, tokens: list[str]) -> tuple[str, ...]:
    values: list[str] = []
    for value in (model_text, *tokens):
        normalized = value.strip()
        if len(normalized) < 2 or normalized in values:
            continue
        values.append(normalized)
        if len(values) == 6:
            break
    return tuple(values)
