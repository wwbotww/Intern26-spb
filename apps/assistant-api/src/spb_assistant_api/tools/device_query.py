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
    "APPLE": ("apple", "苹果"),
    "HUAWEI": ("huawei", "华为"),
    "XIAOMI": ("xiaomi", "小米"),
    "OPPO": ("oppo",),
    "VIVO": ("vivo",),
}
PRODUCT_FAMILY_BRAND_HINTS: dict[str, tuple[str, ...]] = {
    "APPLE": ("macbook", "iphone", "ipad", "imac", "mac"),
    "HUAWEI": ("matebook", "mate", "pura", "nova"),
    "XIAOMI": ("redmi", "红米"),
    "OPPO": ("find", "reno"),
    "VIVO": ("iqoo",),
}
PRODUCT_FAMILY_TOKENS = tuple(
    dict.fromkeys(
        alias
        for aliases in PRODUCT_FAMILY_BRAND_HINTS.values()
        for alias in aliases
    )
)
DEVICE_ALIASES = tuple(
    dict.fromkeys(
        alias
        for mapping in (BRAND_ALIASES, PRODUCT_FAMILY_BRAND_HINTS)
        for aliases in mapping.values()
        for alias in aliases
    )
)
MODEL_VARIANT_WORDS = ("pro", "max", "plus", "ultra", "se")
NUMBER_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?")

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
        "的",
        "款",
        "版",
        "版本",
        "+",
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
            token = _capacity_token(match)
            if token is None:
                continue
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
    model_text = CAPACITY_RE.sub(_remove_capacity_match, model_text)
    for phrase in QUERY_PHRASES:
        model_text = model_text.replace(phrase, " ")
    model_text = re.sub(r"\s+", " ", model_text).strip(" .-")

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
    has_identity_token = any(
        token not in MODEL_VARIANT_WORDS for token in tokens
    )
    sufficient = bool(terms) and has_identity_token and (
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
    brand_match = _first_alias_match(value, BRAND_ALIASES)
    if brand_match is not None:
        code, alias = brand_match
        return code, alias

    family_match = _first_alias_match(value, PRODUCT_FAMILY_BRAND_HINTS)
    if family_match is not None:
        code, _ = family_match
        return code, None
    return None, None


def _capacity_token(match: re.Match[str]) -> str | None:
    amount = float(match.group(1))
    unit = match.group(2).upper()
    if unit in {"T", "TB"} and amount > 8:
        return None
    return normalize_capacity(f"{match.group(1)}{match.group(2)}")


def _remove_capacity_match(match: re.Match[str]) -> str:
    return " " if _capacity_token(match) is not None else match.group(0)


def _first_alias_match(
    value: str,
    aliases_by_code: dict[str, tuple[str, ...]],
) -> tuple[str, str] | None:
    matches: list[tuple[int, str, str]] = []
    for code, aliases in aliases_by_code.items():
        for alias in aliases:
            position = value.find(alias)
            if position >= 0:
                matches.append((position, code, alias))
    if not matches:
        return None
    _, code, alias = min(matches, key=lambda item: (item[0], -len(item[2])))
    return code, alias


def _unique_terms(model_text: str, tokens: list[str]) -> tuple[str, ...]:
    values: list[str] = []
    compact = model_text.replace(" ", "").replace("-", "")
    inferred_terms = (
        *(
            family
            for family in PRODUCT_FAMILY_TOKENS
            if family in compact
        ),
        *(
            variant
            for variant in MODEL_VARIANT_WORDS
            if variant in compact
        ),
        *NUMBER_TOKEN_RE.findall(model_text),
    )
    for value in (model_text, *tokens, *inferred_terms):
        normalized = value.strip()
        if len(normalized) < 2 or normalized in values:
            continue
        values.append(normalized)
        if len(values) == 6:
            break
    return tuple(values)
