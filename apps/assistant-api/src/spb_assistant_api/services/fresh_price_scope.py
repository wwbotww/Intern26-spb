"""Consumer-owned source geography, independent from logistics region resolution.

Names/codes reviewed against producer 7191523 mofcom_fresh.py. This directory
maps scope, not live data coverage. Unknown city/market names are never widened.
"""

from __future__ import annotations

from ..domain.product_price import PriceRegion
from ..domain.product_price_slots import FreshPriceConditions


_PROVINCES = (
    ("北京", "北京市", "110000"),
    ("天津", "天津市", "120000"),
    ("河北", "河北省", "130000"),
    ("山西", "山西省", "140000"),
    ("内蒙古", "内蒙古自治区", "150000"),
    ("辽宁", "辽宁省", "210000"),
    ("吉林", "吉林省", "220000"),
    ("黑龙江", "黑龙江省", "230000"),
    ("上海", "上海市", "310000"),
    ("江苏", "江苏省", "320000"),
    ("浙江", "浙江省", "330000"),
    ("安徽", "安徽省", "340000"),
    ("福建", "福建省", "350000"),
    ("江西", "江西省", "360000"),
    ("山东", "山东省", "370000"),
    ("河南", "河南省", "410000"),
    ("湖北", "湖北省", "420000"),
    ("湖南", "湖南省", "430000"),
    ("广东", "广东省", "440000"),
    ("广西", "广西壮族自治区", "450000"),
    ("海南", "海南省", "460000"),
    ("重庆", "重庆市", "500000"),
    ("四川", "四川省", "510000"),
    ("贵州", "贵州省", "520000"),
    ("云南", "云南省", "530000"),
    ("西藏", "西藏自治区", "540000"),
    ("陕西", "陕西省", "610000"),
    ("甘肃", "甘肃省", "620000"),
    ("青海", "青海省", "630000"),
    ("宁夏", "宁夏回族自治区", "640000"),
    ("新疆", "新疆维吾尔自治区", "650000"),
    ("新疆生产建设兵团", "新疆生产建设兵团", "XJ_CORPS"),
)
MOFCOM_REGIONS = {
    name: code for short, full, code in _PROVINCES for name in (short, full)
}


class UnsupportedFreshScope(ValueError):
    pass


def fresh_regions(conditions: FreshPriceConditions) -> tuple[PriceRegion | None, ...]:
    """One or two bounded queries; Shanghai's two publications have different scopes."""
    name = conditions.region_text
    if name == "全国" and conditions.source_scope == "separate_sources":
        return (None,)
    if name is None:
        if conditions.source_scope == "separate_sources":
            return (None,)
        raise UnsupportedFreshScope("请指定地区或明确分别列出来源。")
    if name not in MOFCOM_REGIONS:
        raise UnsupportedFreshScope(
            "暂不支持该地区层级的价格查询，请使用来源支持的省级地区或上海零售口径。"
        )
    retail = conditions.price_nature == "RETAIL_AVERAGE"
    if retail:
        if name not in {"上海", "上海市"}:
            raise UnsupportedFreshScope("当前零售均价来源仅支持上海。")
        return (PriceRegion(scope="CITY", code="310100"),)
    wholesale = PriceRegion(scope="PROVINCE", code=MOFCOM_REGIONS[name])
    if conditions.price_nature is None and name in {"上海", "上海市"}:
        return (PriceRegion(scope="CITY", code="310100"), wholesale)
    return (wholesale,)
