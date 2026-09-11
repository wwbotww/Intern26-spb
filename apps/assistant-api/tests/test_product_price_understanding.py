from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from spb_assistant_api.api.agent_contracts import AgentApiDependencies
from spb_assistant_api.api.agent_schemas import AgentMessageRequest
from spb_assistant_api.domain.agent_actions import InvokeToolAction, HandoffAction
from spb_assistant_api.domain.commands import AgentCommand
from spb_assistant_api.domain.intents import Intent
from spb_assistant_api.domain.product_price_slots import (
    ProductPriceCommand,
)
from spb_assistant_api.services.product_price_preflight import PRODUCT_PRICE_DESCRIPTOR
from spb_assistant_api.services.query_understanding import (
    HybridQueryUnderstander,
    RuleBasedQueryUnderstander,
    StructuredLlmQueryUnderstander,
)
from spb_assistant_api.services.slot_merger import SlotMerger, required_missing_slots
from spb_assistant_api.workflow.policy import WorkflowPolicy


def understand(message, **context):
    return asyncio.run(
        RuleBasedQueryUnderstander(product_price_enabled=True).understand(
            message=message, **context
        )
    )


@pytest.mark.parametrize(
    "message,kind,missing",
    [
        ("iPhone 16 Pro 256GB 价格", "device", []),
        ("苹果多少钱", "unknown", ["conditions.kind"]),
        (
            "苹果一斤多少钱",
            "fresh",
            ["conditions.region_text", "conditions.price_nature"],
        ),
        (
            "苹果 1kg 多少钱",
            "fresh",
            ["conditions.region_text", "conditions.price_nature"],
        ),
        ("上海黄瓜零售价格", "fresh", []),
        ("北京鸡蛋批发价", "fresh", []),
        ("今天上海黄瓜500g零售价", "fresh", []),
        (
            "苹果 500g 多少钱",
            "fresh",
            ["conditions.region_text", "conditions.price_nature"],
        ),
        ("苹果 256GB 多少钱", "device", ["conditions.product_text"]),
        ("各地鸡蛋批发价格分别列出", "fresh", []),
        ("华为手机多少钱", "device", ["conditions.product_text"]),
        (
            "不是问运费，我问这箱苹果价格",
            "fresh",
            ["conditions.region_text", "conditions.price_nature"],
        ),
    ],
)
def test_price_classification_and_required_conditions(message, kind, missing):
    result = understand(message)
    assert result.selected_intent is Intent.PRODUCT_PRICE
    assert result.slots.conditions.kind == kind
    assert result.missing_slots == missing
    assert Intent.POSTAGE not in [item.intent for item in result.candidates]
    assert not hasattr(result.slots, "weight")


@pytest.mark.parametrize(
    "message,intent",
    [
        ("从上海寄到北京 2 公斤多少钱", Intent.POSTAGE),
        ("北京到上海 2 公斤多少钱", Intent.POSTAGE),
        ("从上海寄苹果到北京 1kg多少钱", Intent.POSTAGE),
        ("寄这箱苹果运费多少", Intent.POSTAGE),
        ("查邮件 1234567890123", Intent.TRACKING),
        ("从北京寄到上海要多久", Intent.DELIVERY_TIME),
        ("理赔需要哪些材料", Intent.POLICY),
        ("500g", Intent.UNKNOWN),
    ],
)
def test_other_intents_and_bare_weights(message, intent):
    result = understand(message)
    assert result.selected_intent is intent
    assert Intent.PRODUCT_PRICE not in [item.intent for item in result.candidates]


def test_weight_reply_to_active_postage_still_works():
    result = understand(
        "500g", active_intent=Intent.POSTAGE, expected_slots=("weight",)
    )
    assert result.selected_intent is Intent.POSTAGE
    assert result.slots.weight.value == Decimal(500)
    assert result.slots.weight.unit == "g"


def test_price_and_shipping_explicit_multi_intent():
    result = understand("上海黄瓜零售价格，以及北京到上海 2 公斤运费")
    assert result.multi_intent
    assert {item.intent for item in result.candidates} >= {
        Intent.PRODUCT_PRICE,
        Intent.POSTAGE,
    }


def test_correction_does_not_lock_onto_previous_postage():
    result = understand("不是问运费，我问苹果一斤多少钱", active_intent=Intent.POSTAGE)
    assert result.selected_intent is Intent.PRODUCT_PRICE
    assert "intent_switch_confirmation" in result.ambiguities


@pytest.mark.parametrize(
    "message,capacity,memory,identity",
    [
        ("iPhone 16 Pro Max 256GB 价格", "256GB", None, "iphone 16 pro max"),
        ("MacBook Air M4 16GB内存 512GB硬盘价格", "512GB", "16GB", "macbook air m4"),
        ("华为 Pura 80 12+256G 价格", "256GB", "12GB", "pura 80"),
        ("iPhone 16 Pro 1TB 蓝色价格", "1TB", None, "iphone 16 pro"),
    ],
)
def test_role_bound_specs_preserve_product_identity(
    message, capacity, memory, identity
):
    conditions = understand(message).slots.conditions
    assert conditions.product_text == identity
    assert conditions.specification.capacity == capacity
    assert conditions.specification.memory == memory


@pytest.mark.parametrize(
    "message,ambiguity",
    [
        ("iPhone 16 Pro 256GB 512GB价格", "price_ambiguous_specification"),
        ("iPhone 16 Pro 内存8GB 内存16GB价格", "price_multiple_memory"),
        ("华为 Pura 80 内存8GB 12+256G价格", "price_multiple_memory"),
        ("iPhone 16 Pro 黑色白色价格", "price_multiple_color"),
        ("上海北京黄瓜零售价格", "price_multiple_regions"),
        ("上海黄瓜鸡蛋零售价格", "price_multiple_commodities"),
        ("上海黄瓜零售批发价格", "price_multiple_natures"),
        ("上海黄瓜1斤2公斤零售价格", "price_multiple_units"),
        ("上海黄瓜0kg零售价格", "price_invalid_quantity"),
        ("昨天上海黄瓜零售价格", "price_unsupported_time"),
        ("上海黄瓜一箱零售价格", "price_unsupported_unit"),
    ],
)
def test_ambiguous_or_unsupported_conditions_do_not_execute(message, ambiguity):
    result = understand(message)
    assert ambiguity in result.ambiguities
    decision = WorkflowPolicy({Intent.PRODUCT_PRICE: PRODUCT_PRICE_DESCRIPTOR}).decide(
        {
            "active_intent": result.selected_intent.value,
            "slots": result.slots.model_dump(),
            "missing_slots": result.missing_slots,
            "ambiguities": result.ambiguities,
            "query_id": str(uuid4()),
            "conversation_id": str(uuid4()),
        }
    )
    assert not isinstance(decision.action, InvokeToolAction)


def test_today_is_not_silently_replaced_by_latest():
    result = understand("今天上海黄瓜零售价格")
    command = ProductPriceCommand(
        conditions=result.slots.conditions, time=result.slots.time
    )
    assert command.time.kind == "today"
    assert (
        TypeAdapter(AgentCommand).validate_python(command.model_dump()).intent
        == "product_price"
    )


@pytest.mark.parametrize(
    "conditions",
    [
        {"kind": "unknown"},
        {"kind": "device"},
        {"kind": "device", "product_text": "iPhone 16", "commodity": "黄瓜"},
        {"kind": "device", "product_text": "iPhone 16", "catalog_item_id": 1},
        {"kind": "fresh", "commodity": "黄瓜"},
        {
            "kind": "fresh",
            "commodity": "黄瓜",
            "source_scope": "separate_sources",
            "weight": {"value": 1},
        },
    ],
)
def test_command_rejects_incomplete_or_cross_category_conditions(conditions):
    with pytest.raises(ValidationError):
        ProductPriceCommand(conditions=conditions)


def test_category_clarification_carries_only_the_known_subject():
    first = understand("苹果多少钱")
    reply = understand(
        "生鲜水果",
        active_intent=Intent.PRODUCT_PRICE,
        expected_slots=tuple(first.missing_slots),
    )
    merged = SlotMerger().merge(existing=first.slots, incoming=reply.slots)
    assert merged.slots.conditions.commodity == "苹果"
    assert required_missing_slots(merged.slots) == [
        "conditions.region_text",
        "conditions.price_nature",
    ]
    assert merged.invalidate_price_candidates


def test_fresh_followups_complete_typed_command_without_logistics_codes():
    first = understand("苹果一斤多少钱")
    next_turn = understand(
        "上海零售",
        active_intent=Intent.PRODUCT_PRICE,
        expected_slots=tuple(first.missing_slots),
    )
    merged = SlotMerger().merge(existing=first.slots, incoming=next_turn.slots)
    command = WorkflowPolicy._build_command(Intent.PRODUCT_PRICE, merged.slots)
    assert command.conditions.region_text == "上海"
    assert command.conditions.price_nature == "RETAIL_AVERAGE"
    assert command.conditions.requested_unit.unit == "JIN"
    assert not hasattr(command.conditions, "city_code")


def test_capability_absence_blocks_collection():
    decision = WorkflowPolicy({}).decide(
        {
            "active_intent": "product_price",
            "slots": understand("苹果多少钱").slots.model_dump(),
            "missing_slots": ["conditions.kind"],
        }
    )
    assert isinstance(decision.action, HandoffAction)


def test_public_contract_is_available_but_default_rules_remain_legacy():
    assert AgentMessageRequest(message="苹果价格", explicit_intent="product_price").explicit_intent is Intent.PRODUCT_PRICE
    assert AgentApiDependencies(
        service=object(), capabilities={Intent.PRODUCT_PRICE: PRODUCT_PRICE_DESCRIPTOR},
    ).product_price_enabled
    result = asyncio.run(
        RuleBasedQueryUnderstander().understand(message="iPhone 16 价格")
    )
    assert result.selected_intent is Intent.DEVICE_PRICE


class Model:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    async def classify(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload


def test_model_cannot_invent_price_entities_or_override_confirmed_category():
    model = Model(
        {
            "selected_intent": "product_price",
            "candidates": [{"intent": "product_price", "score": 0.9}],
            "slots": {
                "intent": "product_price",
                "conditions": {
                    "kind": "device",
                    "product_text": "iPhone 99",
                    "brand": "APPLE",
                },
            },
        }
    )
    hybrid = HybridQueryUnderstander(
        rules=RuleBasedQueryUnderstander(product_price_enabled=True),
        model_fallback=StructuredLlmQueryUnderstander(
            model, product_price_enabled=True
        ),
    )
    result = asyncio.run(hybrid.understand(message="帮我看看这东西怎么卖"))
    assert result.selected_intent is Intent.PRODUCT_PRICE
    assert result.slots.conditions.kind == "unknown"
    assert result.missing_slots == ["conditions.kind"]
    assert len(model.calls) == 1
    assert "product_price" in model.calls[0]["prompt"]
    assert result.prompt_version == "query-understanding-product-price-v1"
    asyncio.run(hybrid.understand(message="苹果多少钱"))
    assert len(model.calls) == 1  # Category ambiguity asks the user, not the model.


def test_default_model_cannot_leak_new_intent_to_legacy_runtime():
    model = Model(
        {
            "selected_intent": "product_price",
            "candidates": [{"intent": "product_price", "score": 0.9}],
        }
    )
    result = asyncio.run(
        HybridQueryUnderstander(
            model_fallback=StructuredLlmQueryUnderstander(model)
        ).understand(message="你好")
    )
    assert result.selected_intent is Intent.UNKNOWN
    assert "model_output_invalid" in result.ambiguities


@pytest.mark.parametrize(
    "extra",
    [{"candidate_token": "forged"}, {"sql": "SELECT *"}, {"current_price": "1"}],
)
def test_model_schema_rejects_executable_or_fact_fields(extra):
    model = Model(
        {
            "selected_intent": "product_price",
            "candidates": [{"intent": "product_price", "score": 0.9}],
            "slots": {
                "intent": "product_price",
                "conditions": {"kind": "fresh", **extra},
            },
        }
    )
    result = asyncio.run(
        HybridQueryUnderstander(
            rules=RuleBasedQueryUnderstander(product_price_enabled=True),
            model_fallback=StructuredLlmQueryUnderstander(
                model, product_price_enabled=True
            ),
        ).understand(message="帮我看看这东西怎么卖")
    )
    assert result.selected_intent is Intent.UNKNOWN
    assert "model_output_invalid" in result.ambiguities


def test_unavailable_model_preserves_deterministic_behavior():
    result = understand("上海黄瓜零售价格")
    assert result.source == "rules"
    assert result.slots.conditions.commodity == "黄瓜"


@pytest.mark.parametrize(
    "negation", ["不查运费", "不用查运费", "不需要查运费", "不要问邮费"]
)
def test_negative_postage_clause_is_not_a_positive_shipping_signal(negation):
    result = understand(negation + "，我问苹果一斤多少钱")
    assert result.selected_intent is Intent.PRODUCT_PRICE
    assert Intent.POSTAGE not in [item.intent for item in result.candidates]


def test_declared_specification_role_and_variety_are_not_dropped():
    device = understand("iPad Air 11英寸 Wi-Fi 256GB价格").slots.conditions
    assert device.product_text == "ipad air"
    assert device.specification.size == "11英寸"
    assert device.specification.connectivity == "Wi-Fi"
    fresh = understand("北京新发地批发市场红富士苹果批发价格").slots.conditions
    assert fresh.market_text == "新发地批发市场"
    assert fresh.variety == "红富士"
    assert fresh.commodity == "苹果"


def test_unknown_market_is_not_ignored_when_other_constraints_are_complete():
    result = understand("北京双河批发市场黄瓜批发价格")
    assert "price_unresolved_market" in result.ambiguities


def test_source_scope_choice_is_a_partial_fresh_update():
    result = understand(
        "分别列出多个来源",
        active_intent=Intent.PRODUCT_PRICE,
        expected_slots=("conditions.source_scope",),
    )
    assert result.slots.conditions.source_scope == "separate_sources"


def test_bare_g_in_an_expected_device_capacity_reply_is_not_grams():
    result = understand(
        "256G",
        active_intent=Intent.PRODUCT_PRICE,
        expected_slots=("conditions.specification",),
    )
    assert result.slots.conditions.kind == "device"
    assert result.slots.conditions.specification.capacity == "256GB"


def test_generic_cost_word_does_not_create_postage_intent():
    result = understand("上海黄瓜零售价格费用")
    assert result.selected_intent is Intent.PRODUCT_PRICE
    assert not result.ambiguities
    assert Intent.POSTAGE not in [item.intent for item in result.candidates]
