"""C2 quotation semantics beyond the old numeric-only evidence baseline."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import traceback

import pytest
from pydantic import ValidationError

from spb_assistant_api.adapters.legacy_agent_tools import DevicePriceAssistantToolAdapter
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.commands import DevicePriceCommand
from spb_assistant_api.domain.device_price_quote import DevicePriceSpecificationFilter
from spb_assistant_api.domain.product_price_slots import ProductPriceCommand
from spb_assistant_api.domain.exceptions import (
    PriceRepositoryUnavailableError, ProductPriceContractError, ProductPriceTimeoutError,
    ToolContractError, ToolUnavailableError,
)
from spb_assistant_api.domain.failures import FailureCategory
from spb_assistant_api.domain.models import ToolStatus
from spb_assistant_api.domain.product_price import ProductPriceReadRecord
from spb_assistant_api.domain.product_price_query import PriceReadBatch
from spb_assistant_api.services.product_price_query import ProductPriceQueryService
from spb_assistant_api.services.result_validator import AgentResultValidator
from spb_assistant_api.tools.v2_device_price import V2DevicePriceTool

from .product_price_device_fixture import baseline_v2_record


class Facts:
    def __init__(self, *records, truncated=False, error=None):
        self.batch = PriceReadBatch(records=records, truncated=truncated)
        self.error = error
        self.queries = []
        self.initialized = self.closed = 0

    async def search(self, query):
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.batch

    async def initialize(self):
        self.initialized += 1

    async def close(self):
        self.closed += 1

    def readiness(self):
        return "ready"


def amountless(key="apple_pro_256", *, history=False):
    raw = baseline_v2_record(key).model_dump()
    if history:
        raw["last_known_price"] = dict(raw["observation"])
        raw["last_known_price"]["observation_id"] += 10000
    observed = raw["observation"]["observed_at"] + timedelta(hours=1)
    raw["pointer"]["observed_at"] = observed
    raw["observation"].update(
        kind="availability_only", availability="OFF_SHELF", price_type="AVAILABILITY_ONLY",
        pricing_basis="UNKNOWN", fee_status="NOT_APPLICABLE", current_price=None,
        original_price=None, original_price_type="NONE", unit_price=None, promotion_label=None,
        observed_at=observed,
    )
    return ProductPriceReadRecord.model_validate(raw)


@pytest.mark.parametrize("history", [False, True])
def test_amountless_state_is_a_match_and_never_uses_historical_amount_as_current(history):
    record = amountless(history=history)
    repo = Facts(record)
    service = ProductPriceQueryService(repo)
    result = asyncio.run(service.quote_device("iPhone 16 Pro 256GB 价格"))
    assert result.status == "matched"
    assert result.candidates[0].record.observation.current_price is None
    assert result.candidates[0].freshness.observed_at == record.observation.observed_at
    assert len(repo.queries) == 1


@pytest.mark.parametrize("surface", ["v1", "agent"])
def test_old_card_protocol_explains_found_state_without_numeric_evidence(surface):
    record = amountless(history=True)
    tool = V2DevicePriceTool(service=ProductPriceQueryService(Facts(record)))
    if surface == "v1":
        result = asyncio.run(tool.execute("iPhone 16 Pro 价格"))
        assert result.evidence == ()
    else:
        command = DevicePriceCommand(question="iPhone 16 Pro 价格")
        result = asyncio.run(DevicePriceAssistantToolAdapter(tool).execute(command))
        AgentResultValidator().validate(command=command, result=result)
        assert result.data is None
    assert result.status.value == "no_match"  # Legacy no numeric quotation, not missing product.
    assert result.reason_code == "current_price_unavailable"
    assert "已找到设备状态记录" in result.answer and "已下架" in result.answer
    assert "2026-08-01T01:00:00Z" in result.answer
    assert str(record.listing.source.source_url) in result.answer
    assert "7999" not in result.answer and "0.00" not in result.answer


def test_mixed_current_states_keep_numeric_evidence_and_explain_amountless_skus():
    repo = Facts(baseline_v2_record("apple_pro_512"), amountless())
    tool = V2DevicePriceTool(service=ProductPriceQueryService(repo))
    result = asyncio.run(tool.execute("iPhone 16 Pro 价格"))
    assert result.status is ToolStatus.PARTIAL
    assert len(result.evidence) == 1 and result.evidence[0].price == "9999.00"
    assert result.reason_code == "current_price_partially_unavailable"
    assert "256GB" in result.answer and "已下架" in result.answer


def test_missing_official_sku_is_not_replaced_with_a_database_identifier():
    raw = baseline_v2_record("apple_pro_256").model_dump()
    raw["listing"]["identity"]["official_sku_id"] = None
    record = ProductPriceReadRecord.model_validate(raw)
    service = ProductPriceQueryService(Facts(record))
    quote = asyncio.run(service.quote_device("iPhone 16 Pro 价格"))
    result = asyncio.run(V2DevicePriceTool(service=service).execute("iPhone 16 Pro 价格"))
    assert quote.candidates[0].record.listing.identity.official_sku_id is None
    assert result.evidence[0].official_sku_id == ""


@pytest.mark.parametrize("specification,matched", [
    ({"capacity": "256G", "memory": "12GB"}, True),
    ({"capacity": "12GB", "memory": "256GB"}, False),
    ({"capacity": "512GB"}, False),
    ({"color": "黑色"}, True),
    ({"color": "蓝色"}, False),
    ({"edition": "不存在的版本"}, False),
    ({"attributes": {"chip": "猜测芯片"}}, False),
])
def test_explicit_specification_filters_bind_roles_and_require_proven_values(specification, matched):
    service = ProductPriceQueryService(Facts(baseline_v2_record("huawei_12_256")))
    result = asyncio.run(service.quote_device(
        "华为 Mate 70 Pro 价格", specification=DevicePriceSpecificationFilter(**specification),
    ))
    assert (result.status == "matched") is matched
    if not matched:
        assert result.reason_code == "no_matching_specification"


def test_explicit_filter_does_not_relax_wrong_product_identity():
    service = ProductPriceQueryService(Facts(baseline_v2_record("apple_pro_max")))
    result = asyncio.run(service.quote_device(
        "iPhone 16 Pro 价格", specification=DevicePriceSpecificationFilter(capacity="256GB"),
    ))
    assert result.status == "no_match"
    assert result.reason_code == "no_matching_device"


@pytest.mark.parametrize("capacity,matched", [
    ("256 GB 1 脚注", True),
    ("256GB 12 脚注", True),
    ("２５６ ＧＢ １ 脚注", True),
    ("512 GB 1 脚注", False),
    ("256GB/512GB 1 脚注", False),
    ("最高 256 GB 1 脚注", False),
    ("256 GB 1 其他说明", False),
    ("256 GB 0 脚注", False),
])
def test_official_capacity_footnote_matches_only_a_single_proven_capacity(capacity, matched):
    raw = baseline_v2_record("apple_pro_256").model_dump()
    raw["listing"]["identity"]["specification"]["capacity"] = capacity
    record = ProductPriceReadRecord.model_validate(raw)
    service = ProductPriceQueryService(Facts(record))
    command = ProductPriceCommand(conditions={
        "kind": "device", "product_text": "iPhone 16 Pro",
        "specification": {"capacity": "256GB"},
    })
    result = asyncio.run(service.quote_product(command))
    assert (result.status == "quote") is matched
    if matched:
        assert result.facts[0].record.listing.identity.specification.capacity == capacity
        assert service.matches_conditions(command, result.facts[0])
    else:
        assert result.reason_code == "no_matching_specification"


def test_capacity_footnote_does_not_move_storage_evidence_into_ram():
    raw = baseline_v2_record("apple_pro_256").model_dump()
    raw["listing"]["identity"]["specification"].update(capacity="256 GB 1 脚注", memory=None)
    service = ProductPriceQueryService(Facts(ProductPriceReadRecord.model_validate(raw)))
    result = asyncio.run(service.quote_device(
        "iPhone 16 Pro", specification=DevicePriceSpecificationFilter(memory="256GB"),
    ))
    assert result.status == "no_match"


def test_truncated_recall_cannot_claim_no_match_for_missing_model_or_specification():
    service = ProductPriceQueryService(Facts(baseline_v2_record("apple_pro_max"), truncated=True))
    result = asyncio.run(service.quote_device("iPhone 16 Pro 价格"))
    assert result.status == "need_more_info" and result.recall_truncated
    assert result.reason_code == "price_candidates_incomplete"
    projected = asyncio.run(V2DevicePriceTool(service=service).execute("iPhone 16 Pro 价格"))
    assert projected.status is ToolStatus.NEED_MORE_INFO
    assert "尚不能确认" in projected.answer


def test_priced_but_truncated_recall_is_partial_not_an_exhaustive_price_range():
    service = ProductPriceQueryService(Facts(baseline_v2_record("apple_pro_256"), truncated=True))
    result = asyncio.run(V2DevicePriceTool(service=service).execute("iPhone 16 Pro 价格"))
    assert result.status is ToolStatus.PARTIAL
    assert result.reason_code == "price_candidates_incomplete"
    assert any("不代表全部规格" in warning for warning in result.warnings)


@pytest.mark.parametrize("recall_truncated", [False, True])
def test_truncated_state_only_subset_cannot_prove_no_numeric_quote_exists(recall_truncated):
    state_raw = amountless().model_dump()
    state_raw["observation"]["availability"] = "OUT_OF_STOCK"
    state = ProductPriceReadRecord.model_validate(state_raw)
    priced_raw = baseline_v2_record("apple_pro_512").model_dump()
    priced_raw["observation"]["availability"] = "OFF_SHELF"
    priced = ProductPriceReadRecord.model_validate(priced_raw)
    # A found state may rank ahead of a priced but off-shelf specification.
    repo = Facts(state, truncated=True) if recall_truncated else Facts(state, priced)
    service = ProductPriceQueryService(repo, result_limit=1)
    quote = asyncio.run(service.quote_device("iPhone 16 Pro 价格"))
    assert quote.status == "matched" and quote.truncated
    result = asyncio.run(V2DevicePriceTool(service=service).execute("iPhone 16 Pro 价格"))
    assert result.status is ToolStatus.NEED_MORE_INFO
    assert result.reason_code == "price_candidates_incomplete"
    assert "尚不能确认" in result.answer
    assert "当前没有可用报价" not in result.answer


def test_stale_evidence_remains_dated_and_is_not_replaced_with_query_time():
    record = baseline_v2_record("apple_pro_256")
    service = ProductPriceQueryService(Facts(record), freshness_max_age_seconds={"APPLE_CN_WEB": 3600})
    quote = asyncio.run(service.quote_device("iPhone 16 Pro 价格"))
    assert quote.candidates[0].freshness.status == "stale"
    result = asyncio.run(V2DevicePriceTool(service=service).execute("iPhone 16 Pro 价格"))
    assert result.evidence[0].observed_at == "2026-08-01T00:00:00Z"
    assert any("不能视为实时价格" in warning for warning in result.warnings)


@pytest.mark.parametrize("error,expected,category,retryable", [
    (ProductPriceContractError("SYNTHETIC_SECRET"), ToolContractError, FailureCategory.CONTRACT_VIOLATION, False),
    (ProductPriceTimeoutError("SYNTHETIC_SECRET"), TimeoutError, FailureCategory.UPSTREAM_TIMEOUT, True),
    (PriceRepositoryUnavailableError("SYNTHETIC_SECRET"), ToolUnavailableError, FailureCategory.UPSTREAM_UNAVAILABLE, True),
])
def test_typed_failures_preserve_taxonomy_and_redact_legacy_exception_chains(error, expected, category, retryable):
    service = ProductPriceQueryService(Facts(error=error))
    tool = V2DevicePriceTool(service=service)
    with pytest.raises(expected) as raised:
        asyncio.run(tool.execute("iPhone 16 Pro 价格"))
    assert "SYNTHETIC_SECRET" not in "".join(traceback.format_exception(raised.value))
    with pytest.raises(AgentOperationError) as raised_agent:
        asyncio.run(DevicePriceAssistantToolAdapter(tool).execute(DevicePriceCommand(question="iPhone 16 Pro 价格")))
    assert raised_agent.value.failure.category is category
    assert raised_agent.value.failure.retryable is retryable
    assert "SYNTHETIC_SECRET" not in "".join(traceback.format_exception(raised_agent.value))


def test_wrapper_borrows_lifecycle_and_cancellation_is_not_a_business_result():
    repo = Facts(error=asyncio.CancelledError())
    tool = V2DevicePriceTool(service=ProductPriceQueryService(repo))
    asyncio.run(tool.initialize())
    assert tool.readiness() == "ready"
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(tool.execute("iPhone 16 Pro 价格"))
    asyncio.run(tool.close())
    assert repo.initialized == repo.closed == 0


@pytest.mark.parametrize("question", ["", 123, "iPhone" * 1000])
def test_invalid_quote_text_is_rejected_without_querying(question):
    repo = Facts()
    with pytest.raises(ProductPriceContractError):
        asyncio.run(ProductPriceQueryService(repo).quote_device(question))
    assert repo.queries == []


@pytest.mark.parametrize("settings", [
    {"result_limit": True}, {"result_limit": 101}, {"result_limit": 0},
    {"product_limit": 0}, {"per_product_limit": 51},
    {"product_limit": 1, "per_product_limit": 1, "result_limit": 2},
    {"match_threshold": True}, {"match_threshold": float("nan")}, {"match_threshold": float("inf")},
])
def test_quote_configuration_is_bounded(settings):
    with pytest.raises((ValueError, ValidationError)):
        ProductPriceQueryService(Facts(), **settings)
