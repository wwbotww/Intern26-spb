from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from spb_assistant_api.domain.agent_actions import (
    InvokeToolAction,
    NextAction,
)
from spb_assistant_api.domain.commands import TrackingCommand
from spb_assistant_api.domain.failures import FailureCategory
from spb_assistant_api.domain.intents import Intent
from spb_assistant_api.domain.results import (
    AgentResult,
    AgentResultStatus,
    PolicyData,
    PostageData,
)
from spb_assistant_api.domain.slots import (
    PostageSlots,
    RegionRef,
    RegionResolution,
    SlotPayload,
    TrackingSlots,
    WeightValue,
)
from spb_assistant_api.domain.understanding import (
    IntentCandidate,
    QueryUnderstandingResult,
)


def _resolved_region(name: str, code: str) -> RegionRef:
    return RegionRef(
        raw_text=name,
        canonical_name=name,
        province_code=code,
        resolution=RegionResolution.RESOLVED,
    )


def test_slot_payload_is_discriminated_by_intent() -> None:
    adapter = TypeAdapter(SlotPayload)

    tracking = adapter.validate_python(
        {"intent": "tracking", "mail_no": "AB123"}
    )
    postage = adapter.validate_python(
        {
            "intent": "postage",
            "origin": {"raw_text": "北京"},
            "destination": {"raw_text": "上海"},
            "weight": {"value": "2.50", "unit": "kg"},
        }
    )

    assert isinstance(tracking, TrackingSlots)
    assert tracking.mail_no == "AB123"
    assert isinstance(postage, PostageSlots)
    assert postage.weight == WeightValue(
        value=Decimal("2.50"),
        unit="kg",
    )


def test_mail_number_schema_does_not_freeze_demo_length() -> None:
    command = TrackingCommand(mail_no="AB123456789CN")

    assert command.mail_no == "AB123456789CN"


def test_next_action_rejects_unknown_fields_and_preserves_command() -> None:
    payload = {
        "type": "invoke_tool",
        "tool_name": "tracking",
        "command": {
            "intent": "tracking",
            "mail_no": "1234567890123",
        },
        "tool_call_id": str(uuid4()),
        "argument_fingerprint": "sha256:example",
        "attempt": 1,
        "deadline_at": "2026-09-03T10:00:00Z",
    }
    adapter = TypeAdapter(NextAction)

    action = adapter.validate_python(payload)

    assert isinstance(action, InvokeToolAction)
    assert isinstance(action.command, TrackingCommand)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        adapter.validate_python({**payload, "function": "arbitrary"})


def test_agent_result_rejects_data_from_another_intent() -> None:
    with pytest.raises(ValidationError, match="intent"):
        AgentResult(
            tool="tracking",
            intent=Intent.TRACKING,
            status=AgentResultStatus.SUCCESS,
            answer="查询完成",
            data=PolicyData(evidence_ids=["policy-1"]),
        )


def test_money_and_weight_remain_decimal_in_json_contract() -> None:
    data = PostageData(
        origin=_resolved_region("北京市", "110000"),
        destination=_resolved_region("上海市", "310000"),
        input_weight=WeightValue(value=Decimal("2.50"), unit="kg"),
        amount=Decimal("12.30"),
        currency="CNY",
        queried_at=datetime(2026, 9, 3, tzinfo=UTC),
    )

    payload = data.model_dump(mode="json")

    assert payload["input_weight"]["value"] == "2.50"
    assert payload["amount"] == "12.30"
    assert payload["currency"] == "CNY"


def test_failure_categories_are_stable_and_distinct() -> None:
    values = {category.value for category in FailureCategory}

    assert "no_match" in values
    assert "upstream_timeout" in values
    assert "contract_violation" in values
    assert "persistence_unavailable" in values
    assert len(values) == len(FailureCategory)


def test_query_understanding_contract_preserves_source_and_slots() -> None:
    result = QueryUnderstandingResult(
        original_query=" 查邮件 AB123 ",
        normalized_query="查邮件 AB123",
        selected_intent=Intent.TRACKING,
        candidates=[
            IntentCandidate(
                intent=Intent.TRACKING,
                score=0.95,
                signals=["mail_no_pattern"],
            )
        ],
        slots=TrackingSlots(mail_no="AB123"),
        source="rules",
        parser_version="rules-v1",
    )

    assert result.original_query == "查邮件 AB123"
    assert result.slots is not None
    assert result.slots.intent == "tracking"


def test_query_understanding_rejects_mismatched_slots() -> None:
    with pytest.raises(ValidationError, match="selected_intent"):
        QueryUnderstandingResult(
            original_query="查价格",
            normalized_query="查价格",
            selected_intent=Intent.DEVICE_PRICE,
            slots=TrackingSlots(mail_no="AB123"),
            source="rules",
            parser_version="rules-v1",
        )


def test_query_understanding_rejects_duplicate_candidates() -> None:
    with pytest.raises(ValidationError, match="候选意图不能重复"):
        QueryUnderstandingResult(
            original_query="多久能到",
            normalized_query="多久能到",
            selected_intent=Intent.DELIVERY_TIME,
            candidates=[
                IntentCandidate(intent=Intent.DELIVERY_TIME, score=0.8),
                IntentCandidate(intent=Intent.DELIVERY_TIME, score=0.7),
            ],
            source="rules",
            parser_version="rules-v1",
        )
