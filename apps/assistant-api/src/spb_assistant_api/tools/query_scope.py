from __future__ import annotations

import re

from ..domain.models import ToolResult, ToolStatus
from .device_query import BRAND_ALIASES, normalize_text


PRICE_MARKERS = ("参考价", "价格", "多少钱", "售价", "价钱")
POLICY_MARKERS = (
    "政策",
    "法规",
    "标准",
    "理赔",
    "赔付",
    "材料",
    "流程",
    "办理",
    "规定",
    "条款",
)
CROSS_CONNECTORS = (
    "同时",
    "另外",
    "以及",
    "并且",
    "还要",
    "还想",
    "顺便",
    "和",
)
RAW_PUNCTUATION_CONNECTORS = ("、", "，", ",", ";", "；")
MODEL_NUMBER_RE = re.compile(r"[a-z]+[- ]?\d{2,}|\d{2,}[- ]?[a-z]+")


def is_cross_category_question(question: str) -> bool:
    normalized = normalize_text(question)
    has_price = any(marker in normalized for marker in PRICE_MARKERS)
    has_policy = any(marker in normalized for marker in POLICY_MARKERS)
    has_connector = any(
        connector in normalized for connector in CROSS_CONNECTORS
    ) or any(
        connector in question
        for connector in RAW_PUNCTUATION_CONNECTORS
    )
    aliases = (
        alias
        for values in BRAND_ALIASES.values()
        for alias in values
    )
    has_device = (
        "设备" in normalized
        or any(alias in normalized for alias in aliases)
        or MODEL_NUMBER_RE.search(normalized) is not None
    )
    return has_price and has_policy and has_connector and has_device


def cross_category_result(tool_name: str) -> ToolResult:
    return ToolResult(
        tool=tool_name,
        status=ToolStatus.NEED_MORE_INFO,
        answer=(
            "当前一次请求只支持一类查询。"
            "请将问题拆分为“政策/材料/流程”"
            "和“设备参考价格”两次查询，并分别选择对应类别。"
        ),
        missing_fields=("single_query_category",),
        reason_code="multiple_query_categories",
    )
