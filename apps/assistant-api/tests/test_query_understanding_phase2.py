from __future__ import annotations

import asyncio
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import pytest

from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.intents import Intent
from spb_assistant_api.domain.slots import (
    PostageSlots,
    RegionRef,
    RegionResolution,
    TrackingSlots,
)
from spb_assistant_api.services.query_understanding import (
    HybridQueryUnderstander,
    RuleBasedQueryUnderstander,
    StructuredLlmQueryUnderstander,
)
from spb_assistant_api.services.region_resolver import (
    create_demo_region_resolver,
)
from spb_assistant_api.services.slot_merger import SlotMerger


class RecordingStructuredModel:
    def __init__(self, response: Mapping[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, str]] = []

    async def classify(
        self,
        *,
        message: str,
        prompt: str,
        prompt_version: str,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "message": message,
                "prompt": prompt,
                "prompt_version": prompt_version,
            }
        )
        return self.response


class FailingStructuredModel:
    async def classify(
        self,
        *,
        message: str,
        prompt: str,
        prompt_version: str,
    ) -> Mapping[str, Any]:
        del message, prompt, prompt_version
        raise TimeoutError("model timeout")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("理赔需要哪些材料", Intent.POLICY),
        ("iPhone 16 多少钱", Intent.DEVICE_PRICE),
        ("查邮件 1234567890123", Intent.TRACKING),
        ("从北京寄到上海要多久", Intent.DELIVERY_TIME),
        ("北京寄上海 2.5 公斤多少钱", Intent.POSTAGE),
    ],
)
def test_rule_understander_covers_all_five_intents(
    message: str,
    expected: Intent,
) -> None:
    result = asyncio.run(
        RuleBasedQueryUnderstander().understand(message=message)
    )

    assert result.selected_intent is expected
    assert result.slots is not None
    assert result.slots.intent == expected.value


def test_unknown_and_multi_intent_are_explicit_states() -> None:
    understander = RuleBasedQueryUnderstander()

    unknown = asyncio.run(understander.understand(message="你好呀"))
    multiple = asyncio.run(
        understander.understand(
            message=(
                "查邮件 1234567890123，同时算北京到上海 2 公斤邮费"
            )
        )
    )

    assert unknown.selected_intent is Intent.UNKNOWN
    assert unknown.slots is None
    assert multiple.multi_intent
    assert "multiple_intents" in multiple.ambiguities
    assert {item.intent for item in multiple.candidates} >= {
        Intent.TRACKING,
        Intent.POSTAGE,
    }


@pytest.mark.parametrize(
    ("message", "value", "unit"),
    [
        ("2.50 公斤", Decimal("2.50"), "kg"),
        ("500克", Decimal("500"), "g"),
        ("3斤", Decimal("1.5"), "kg"),
        ("1Kg", Decimal("1"), "kg"),
    ],
)
def test_weight_extractor_uses_decimal_and_normalizes_units(
    message: str,
    value: Decimal,
    unit: str,
) -> None:
    weight, ambiguities = RuleBasedQueryUnderstander.extract_weight(
        message
    )

    assert weight is not None
    assert weight.value == value
    assert weight.unit == unit
    assert ambiguities == []


def test_region_resolver_returns_candidates_for_ambiguous_name() -> None:
    resolver = create_demo_region_resolver()

    resolved = resolver.resolve("北京")
    ambiguous = resolver.resolve("朝阳区")
    missing = resolver.resolve("不存在地区")

    assert resolved.resolution is RegionResolution.RESOLVED
    assert resolved.canonical_name == "北京市"
    assert ambiguous.resolution is RegionResolution.AMBIGUOUS
    assert len(ambiguous.candidates) == 2
    assert missing.resolution is RegionResolution.UNRESOLVED
    with pytest.raises(ValueError, match="canonical_name"):
        RegionRef(
            raw_text="北京",
            resolution=RegionResolution.RESOLVED,
        )


def test_route_extraction_keeps_ambiguous_region_out_of_ready_state() -> None:
    result = asyncio.run(
        RuleBasedQueryUnderstander().understand(
            message="从朝阳区寄到上海要多久"
        )
    )

    assert result.selected_intent is Intent.DELIVERY_TIME
    assert result.missing_slots == ["origin"]
    assert "region_origin_ambiguous" in result.ambiguities


def test_region_mention_offsets_remain_correct_with_whitespace() -> None:
    result = asyncio.run(
        RuleBasedQueryUnderstander().understand(
            message="寄到  上海  大概需要几天"
        )
    )

    assert result.selected_intent is Intent.DELIVERY_TIME
    assert result.missing_slots == ["origin"]
    assert result.slots is not None
    assert result.slots.destination is not None
    assert result.slots.destination.canonical_name == "上海市"


def test_active_workflow_accepts_slot_only_reply_but_detects_switch() -> None:
    understander = RuleBasedQueryUnderstander()

    slot_reply = asyncio.run(
        understander.understand(
            message="1234567890123",
            active_intent=Intent.TRACKING,
        )
    )
    switch = asyncio.run(
        understander.understand(
            message="改成查询北京到上海 2 公斤邮费",
            active_intent=Intent.TRACKING,
        )
    )
    explicit = asyncio.run(
        understander.understand(
            message=(
                "查邮件 1234567890123，同时算北京到上海"
                " 2 公斤邮费"
            ),
            explicit_intent=Intent.TRACKING,
        )
    )

    assert slot_reply.selected_intent is Intent.TRACKING
    assert slot_reply.source == "active_workflow"
    assert switch.selected_intent is Intent.POSTAGE
    assert "intent_switch_confirmation" in switch.ambiguities
    assert explicit.selected_intent is Intent.TRACKING
    assert explicit.multi_intent is False
    assert "multiple_intents" not in explicit.ambiguities


def test_slot_merger_preserves_conflict_until_explicit_confirmation() -> None:
    merger = SlotMerger()
    old = TrackingSlots(mail_no="1234567890123")
    new = TrackingSlots(mail_no="9999999999999")

    guarded = merger.merge(existing=old, incoming=new)
    corrected = merger.merge(
        existing=old,
        incoming=new,
        confirm_overwrite=True,
    )

    assert guarded.slots.mail_no == "1234567890123"
    assert [item.slot for item in guarded.conflicts] == ["mail_no"]
    assert corrected.slots.mail_no == "9999999999999"
    assert corrected.conflicts == []


def test_slot_merger_never_reuses_slots_across_intents() -> None:
    merged = SlotMerger().merge(
        existing=TrackingSlots(mail_no="1234567890123"),
        incoming=PostageSlots(),
    )

    assert merged.slots.intent == "tracking"
    assert merged.conflicts[0].reason == "intent_switch"


def test_rule_hit_does_not_invoke_structured_model() -> None:
    model = RecordingStructuredModel(
        {"selected_intent": "unknown", "tool_name": "unsafe"}
    )
    hybrid = HybridQueryUnderstander(
        model_fallback=StructuredLlmQueryUnderstander(model)
    )

    result = asyncio.run(
        hybrid.understand(message="查邮件 1234567890123")
    )

    assert result.selected_intent is Intent.TRACKING
    assert model.calls == []


def test_unknown_rule_result_uses_versioned_model_and_rule_entities() -> None:
    model = RecordingStructuredModel(
        {
            "selected_intent": "policy",
            "candidates": [
                {
                    "intent": "policy",
                    "score": 0.82,
                    "signals": ["semantic_policy"],
                }
            ],
            "slots": {
                "intent": "policy",
                "question": "这种情形应该怎样处理",
            },
        }
    )
    hybrid = HybridQueryUnderstander(
        model_fallback=StructuredLlmQueryUnderstander(model)
    )

    result = asyncio.run(
        hybrid.understand(message="这种情形应该怎样处理")
    )

    assert result.selected_intent is Intent.POLICY
    assert result.source == "model"
    assert result.prompt_version == "query-understanding-v1"
    assert len(model.calls) == 1
    assert model.calls[0]["prompt_version"] == result.prompt_version

    route_model = RecordingStructuredModel(
        {
            "selected_intent": "delivery_time",
            "candidates": [
                {
                    "intent": "delivery_time",
                    "score": 0.80,
                    "signals": ["semantic_route"],
                }
            ],
        }
    )
    route_hybrid = HybridQueryUnderstander(
        model_fallback=StructuredLlmQueryUnderstander(route_model)
    )
    route = asyncio.run(
        route_hybrid.understand(message="北京到上海怎么寄")
    )

    assert route.slots is not None
    assert route.slots.origin is not None
    assert route.slots.destination is not None
    assert route.slots.origin.canonical_name == "北京市"
    assert route.slots.destination.canonical_name == "上海市"
    assert route.missing_slots == []
    assert len(route_model.calls) == 1


def test_model_cannot_return_tool_name_or_bypass_schema() -> None:
    model = RecordingStructuredModel(
        {
            "selected_intent": "tracking",
            "tool_name": "arbitrary_shell_tool",
            "slots": {
                "intent": "tracking",
                "mail_no": "1234567890123",
            },
        }
    )
    structured = StructuredLlmQueryUnderstander(model)

    with pytest.raises(AgentOperationError) as raised:
        asyncio.run(structured.understand(message="帮我处理这个"))

    assert raised.value.failure.code == "query_model_schema_invalid"


def test_invalid_model_output_fails_closed_to_unknown() -> None:
    model = RecordingStructuredModel(
        {"selected_intent": "not-an-intent"}
    )
    hybrid = HybridQueryUnderstander(
        model_fallback=StructuredLlmQueryUnderstander(model)
    )

    result = asyncio.run(hybrid.understand(message="帮我看看这个"))

    assert result.selected_intent is Intent.UNKNOWN
    assert "model_output_invalid" in result.ambiguities


def test_optional_model_outage_fails_closed_to_rule_result() -> None:
    hybrid = HybridQueryUnderstander(
        model_fallback=StructuredLlmQueryUnderstander(
            FailingStructuredModel()
        )
    )

    result = asyncio.run(hybrid.understand(message="帮我看看这个"))

    assert result.selected_intent is Intent.UNKNOWN
    assert "model_fallback_failed" in result.ambiguities


def test_cancel_and_restart_are_deterministic_and_skip_model() -> None:
    model = RecordingStructuredModel({"selected_intent": "policy"})
    hybrid = HybridQueryUnderstander(
        model_fallback=StructuredLlmQueryUnderstander(model)
    )

    cancel = asyncio.run(hybrid.understand(message="取消"))
    restart = asyncio.run(hybrid.understand(message="重新开始"))

    assert cancel.control.value == "cancel"
    assert restart.control.value == "restart"
    assert model.calls == []
