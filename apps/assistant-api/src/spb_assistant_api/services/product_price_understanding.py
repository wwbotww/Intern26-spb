"""Bounded, explainable price extraction; no database or model calls.

Known nouns/roles are extracted, unsupported text remains a scope constraint.
This is intentionally not a general Chinese NER or an arbitrary category engine.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from ..domain.device_query import CAPACITY_RE, normalize_capacity, parse_device_query
from ..domain.device_price_quote import DevicePriceSpecificationFilter
from ..domain.product_price_slots import (
    DevicePriceConditions,
    FreshPriceConditions,
    PriceTimeConstraint,
    PriceUnitRequest,
    ProductPriceSlots,
    UnknownPriceConditions,
)
from .fresh_price_scope import MOFCOM_REGIONS


PRICE_WORDS = (
    "价格",
    "参考价",
    "售价",
    "多少钱",
    "价钱",
    "批发价",
    "零售价",
    "报价",
    "什么价",
    "怎么卖",
)
FRESH_WORDS = (
    "苹果",
    "黄瓜",
    "鸡蛋",
    "西红柿",
    "番茄",
    "土豆",
    "马铃薯",
    "大白菜",
    "白菜",
    "青椒",
    "茄子",
    "胡萝卜",
    "白萝卜",
    "萝卜",
    "芹菜",
    "韭菜",
    "菠菜",
    "蒜苔",
    "洋葱",
    "生姜",
    "大蒜",
    "猪肉",
    "牛肉",
    "羊肉",
    "鸡肉",
    "鲫鱼",
    "鲤鱼",
    "带鱼",
    "大米",
    "面粉",
    "食用油",
    "香蕉",
    "西瓜",
    "梨",
    "生菜",
    "圆白菜",
)
DEVICE_WORDS = (
    "iphone",
    "ipad",
    "macbook",
    "imac",
    "mac mini",
    "mac studio",
    "apple watch",
    "华为",
    "huawei",
    "小米",
    "xiaomi",
    "redmi",
    "红米",
    "oppo",
    "vivo",
    "iqoo",
    "mate",
    "pura",
    "nova",
    "reno",
    "find",
    "手机",
    "电脑",
    "平板",
    "电子设备",
    "荣耀",
    "三星",
    "笔记本",
)
_SHIPPING = re.compile(
    r"运费|邮费|资费|寄递|寄件|寄到|寄往|快递费|从.+(?:寄|送|到)|(?:北京|上海|广州|深圳|杭州).{0,3}(?:寄|到)|寄.{0,16}(?:多少钱|公斤|千克|斤|kg)",
    re.I,
)
_NEGATED_POSTAGE = re.compile(
    r"(?:不是|不需要|不用|不要|不)(?:在)?(?:问|查|要查|要问)?(?:的)?(?:运费|邮费|资费|寄递费用)"
)
_UNIT = re.compile(
    r"(?P<n>\d+(?:\.\d+)?|一|半)?\s*(?P<u>公斤|千克|kg|斤|克|g|个|箱|盒|袋)(?![A-Za-z])",
    re.I,
)
_REGIONS = (
    "上海",
    "北京",
    "广东",
    "广州",
    "深圳",
    "浙江",
    "杭州",
    "江苏",
    "南京",
    "四川",
    "成都",
    "湖北",
    "武汉",
    "山东",
    "新疆",
    "重庆",
    "全国",
)
# Consumer-owned source names are semantic hints, not claims of data coverage.
_REGIONS = tuple(dict.fromkeys((*_REGIONS, *MOFCOM_REGIONS)))
_HISTORY = re.compile(
    r"昨天|前天|去年|上周|上个月|上月|明天|后天|下周|下个月|历史|趋势|走势|预测|(?:\d{4}[-年/]\d{1,2}(?:[-月/]\d{1,2}日?)?)|\d{1,2}月\d{1,2}日"
)
_MEMORY = re.compile(
    r"(?:内存|RAM)\s*(\d+(?:\.\d+)?\s*(?:GB|G|TB|T))|(\d+(?:\.\d+)?\s*(?:GB|G|TB|T))\s*(?:内存|RAM)",
    re.I,
)
_STORAGE = re.compile(
    r"(?:存储|容量|硬盘)\s*(\d+(?:\.\d+)?\s*(?:GB|G|TB|T))|(\d+(?:\.\d+)?\s*(?:GB|G|TB|T))\s*(?:存储|容量|硬盘)",
    re.I,
)
_COMBO = re.compile(
    r"(?<!\d)(\d+)\s*(?:GB|G)?\s*\+\s*(\d+)\s*(GB|G|TB|T)(?![A-Za-z])", re.I
)


def routing_text(message: str) -> str:
    return _NEGATED_POSTAGE.sub("", unicodedata.normalize("NFKC", message))


def has_shipping_context(message: str) -> bool:
    return bool(_SHIPPING.search(routing_text(message)))


def price_signal(message: str) -> tuple[float, str] | None:
    text = routing_text(message).lower()
    device = any(word in text for word in DEVICE_WORDS)
    fresh = (
        any(word in text for word in FRESH_WORDS) or "水果" in text or "生鲜" in text
    )
    price = any(word in text for word in PRICE_WORDS)
    # Shipping an identified product is still a logistics question. Only an
    # explicit second price request contributes a second intent candidate.
    if has_shipping_context(text) and not re.search(
        r"(?:价格|售价|批发价|零售价).*(?:和|以及|另外|同时)|(?:另外|同时|以及).*(?:价格|售价|批发价|零售价)",
        text,
    ):
        return None
    if price and (device or fresh or "商品" in text):
        return 0.96, "product_price_pair"
    if device:
        return 0.62, "keyword_device"
    if fresh and ("零售" in text or "批发" in text):
        return 0.90, "fresh_price_context"
    return None


def _time(text: str) -> PriceTimeConstraint | None:
    if match := _HISTORY.search(text):
        return PriceTimeConstraint(kind="unsupported", raw_text=match.group())
    if "今天" in text or "今日" in text:
        return PriceTimeConstraint(
            kind="today", raw_text="今天" if "今天" in text else "今日"
        )
    if any(word in text for word in ("最新", "现在", "当前")):
        return PriceTimeConstraint(kind="latest", raw_text="最新可用观察")
    return None


def _specification(text: str) -> tuple[DevicePriceSpecificationFilter, str, list[str]]:
    values: dict[str, str] = {}
    ambiguities = []
    cleaned = text
    for role, pattern in (("memory", _MEMORY), ("capacity", _STORAGE)):
        found = [
            normalize_capacity(next(g for g in match.groups() if g))
            for match in pattern.finditer(text)
        ]
        if len(set(found)) > 1:
            ambiguities.append(f"price_multiple_{role}")
        elif found:
            values[role] = found[0]
        cleaned = pattern.sub(" ", cleaned)
    if combo := _COMBO.search(cleaned):
        for role, value in {
            "memory": normalize_capacity(combo[1] + "G"),
            "capacity": normalize_capacity(combo[2] + combo[3]),
        }.items():
            if role in values and values[role] != value:
                ambiguities.append(f"price_multiple_{role}")
            else:
                values[role] = value
        cleaned = _COMBO.sub(" ", cleaned)
    capacities = [normalize_capacity(m.group()) for m in CAPACITY_RE.finditer(cleaned)]
    if capacities:
        if len(set(capacities)) > 1 or (
            "capacity" in values and values["capacity"] not in capacities
        ):
            ambiguities.append("price_ambiguous_specification")
        elif "capacity" not in values:
            values["capacity"] = capacities[0]
        cleaned = CAPACITY_RE.sub(" ", cleaned)
    colors = [
        color
        for color in (
            "黑色",
            "白色",
            "蓝色",
            "绿色",
            "银色",
            "金色",
            "紫色",
            "粉色",
            "红色",
        )
        if color in cleaned
    ]
    if len(colors) == 1:
        values["color"] = colors[0]
        cleaned = cleaned.replace(colors[0], " ")
    elif len(colors) > 1:
        ambiguities.append("price_multiple_color")
    for role, pattern in (
        ("size", r"\d+(?:\.\d+)?\s*(?:英寸|寸|毫米|mm)(?![A-Za-z])"),
        ("connectivity", r"Wi-?Fi\s*\+\s*蜂窝|Wi-?Fi|WLAN|蜂窝"),
    ):
        found = list(re.finditer(pattern, cleaned, re.I))
        if len(found) == 1:
            values[role] = found[0].group().strip()
            cleaned = re.sub(pattern, " ", cleaned, flags=re.I)
        elif len(found) > 1:
            ambiguities.append("price_ambiguous_specification")
    return DevicePriceSpecificationFilter(**values), cleaned, ambiguities


def extract_product_price_slots(
    message: str,
    *,
    expected_slots: tuple[str, ...] = (),
) -> tuple[ProductPriceSlots, list[str]]:
    text = routing_text(message)
    lowered = text.lower()
    ambiguities: list[str] = []
    time = _time(text)
    if time and time.kind == "unsupported":
        ambiguities.append("price_unsupported_time")
    # Expected slot names only guide a short reply; they do not invent values.
    expected_device = any(
        name.startswith("conditions.specification.")
        or name
        in {"conditions.product_text", "conditions.brand", "conditions.specification"}
        for name in expected_slots
    )
    expected_fresh = any(
        name
        in {
            "conditions.commodity",
            "conditions.region_text",
            "conditions.price_nature",
            "conditions.market_text",
            "conditions.variety",
            "conditions.requested_unit",
            "conditions.source_scope",
        }
        for name in expected_slots
    )
    # Bare G/g is ambiguous (storage vs grams); it is a device role only with
    # device context. GB/TB and explicit storage words are unambiguous.
    capacity_evidence = bool(
        re.search(r"\d\s*(?:GB|TB)(?![A-Za-z])|内存|硬盘|存储", text, re.I)
    )
    device = (
        any(word in lowered for word in DEVICE_WORDS)
        or capacity_evidence
        or "设备" in text
    )
    if (
        expected_device
        and CAPACITY_RE.search(text)
        and not any(word in text for word in ("水果", "生鲜"))
    ):
        device = True
    explicit_fresh = any(
        word in text
        for word in ("水果", "生鲜", "一斤", "每斤", "批发", "零售", "这箱")
    ) or bool(_UNIT.search(text))
    if device or (expected_device and not explicit_fresh):
        specification, product, spec_ambiguities = _specification(text)
        ambiguities.extend(spec_ambiguities)
        product = re.sub(
            r"今天|今日|最新|现在|当前|(?:我问|我想查|我要查|改成|换成|换查|型号是|型号)|的|[，。？?]",
            " ",
            product,
        )
        product = _HISTORY.sub(" ", product)
        parsed = parse_device_query(product)
        # Brand-only and a reply consisting solely of a role/value are not a model.
        identity = parsed.model_text.strip() if parsed.sufficient else None
        if identity and len(identity) > 255:
            identity = None
            ambiguities.append("price_identity_too_long")
        return ProductPriceSlots(
            conditions=DevicePriceConditions(
                brand=parsed.brand_code,
                product_text=identity,
                specification=specification,
            ),
            time=time,
        ), ambiguities

    nouns = [word for word in FRESH_WORDS if word in text]
    nouns = [
        word
        for word in nouns
        if not any(word != other and word in other for other in nouns)
    ]
    fresh = expected_fresh or explicit_fresh or any(word != "苹果" for word in nouns)
    if not fresh:
        return ProductPriceSlots(
            conditions=UnknownPriceConditions(
                subject_text="苹果" if "苹果" in text else None
            ),
            time=time,
        ), ambiguities
    commodity = nouns[0] if len(nouns) == 1 else None
    if len(nouns) > 1:
        ambiguities.append("price_multiple_commodities")
    elif (
        not commodity
        and "conditions.commodity" in expected_slots
        and re.fullmatch(r"[\u4e00-\u9fff]{2,20}", text)
    ):
        commodity = text
    regions = [region for region in _REGIONS if region in text]
    regions = [
        region
        for region in regions
        if not any(region != other and region in other for other in regions)
    ]
    region_text = regions[0] if len(regions) == 1 else None
    if region_text:
        # Preserve unsupported finer scopes for the service to reject. Do not
        # silently quote Shanghai-wide data for Shanghai/Pudong, for example.
        detail = re.search(
            re.escape(region_text) + r"(?:[\u4e00-\u9fff]{1,12}(?:自治州|市|区|县|旗))",
            text,
        )
        if detail:
            region_text = detail.group()
    if len(regions) > 1:
        ambiguities.append("price_multiple_regions")
    if (
        not regions
        and "conditions.region_text" in expected_slots
        and re.fullmatch(r"[\u4e00-\u9fff]{2,20}", text)
        and not any(word in text for word in ("批发", "零售", "来源", "列出", "价格", "多少钱", "品种", "市场"))
    ):
        region_text = text  # Raw request, not a claimed supported source/code.
    nature = None
    if "批发" in text and "零售" in text:
        ambiguities.append("price_multiple_natures")
    elif "批发" in text:
        nature = "WHOLESALE_AVERAGE"
    elif "零售" in text:
        nature = "RETAIL_AVERAGE"
    unit = None
    units = list(_UNIT.finditer(text))
    units = [
        match
        for match in units
        if not (
            match["u"] == "个"
            and (
                text[max(0, match.start() - 1) : match.start()] in {"多", "各"}
                or re.match(r"(?:来源|市场|地区|候选|选项)", text[match.end() :])
            )
        )
    ]
    if len(units) > 1:
        ambiguities.append("price_multiple_units")
    elif units:
        match = units[0]
        raw_n = match["n"] or "1"
        number = Decimal({"一": "1", "半": "0.5"}.get(raw_n, raw_n))
        symbol = {
            "公斤": "KG",
            "千克": "KG",
            "kg": "KG",
            "斤": "JIN",
            "克": "G",
            "g": "G",
            "个": "EACH",
        }.get(match["u"].lower(), "UNSUPPORTED")
        if number <= 0 or number > 100000:
            ambiguities.append("price_invalid_quantity")
        else:
            unit = PriceUnitRequest(
                unit=symbol, quantity=number, raw_text=match.group().strip()
            )
            if symbol == "UNSUPPORTED":
                ambiguities.append("price_unsupported_unit")
    scope = (
        "separate_sources"
        if any(
            word in text
            for word in ("各地", "各来源", "多个来源", "分别列出", "所有来源")
        )
        else None
    )
    if scope == "separate_sources":
        ambiguities = [item for item in ambiguities if item != "price_multiple_natures"]
    elif "单一地区和口径" in text or "单一来源" in text:
        scope = "single_scope"
    market = re.search(
        r"((?:新发地|岳各庄|八里桥|江桥|江南)(?:批发市场|农贸市场|市场)?)", text
    )
    if not market:
        market = re.search(r"市场[：:]\s*([\u4e00-\u9fff]{2,20})", text)
    if not market and re.search(r"[\u4e00-\u9fff]{2,12}(?:批发市场|农贸市场)", text):
        ambiguities.append("price_unresolved_market")
    variety = re.search(r"(红富士|富士|嘎啦|巨峰)", text)
    if not variety:
        variety = re.search(r"品种[：:]\s*([\u4e00-\u9fff]{2,12})", text)
    return ProductPriceSlots(
        conditions=FreshPriceConditions(
            commodity=commodity,
            region_text=region_text,
            price_nature=nature,
            variety=variety.group(1) if variety else None,
            market_text=market.group(1) if market else None,
            requested_unit=unit,
            source_scope=scope,
        ),
        time=time,
    ), ambiguities
