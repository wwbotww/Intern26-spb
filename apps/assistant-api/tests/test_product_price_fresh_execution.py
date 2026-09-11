from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.product_price import ProductPriceReadRecord
from spb_assistant_api.domain.product_price_slots import (
    FreshPriceConditions,
    ProductPriceCommand,
)
from spb_assistant_api.services.fresh_price_scope import (
    fresh_regions,
    UnsupportedFreshScope,
)
from spb_assistant_api.services.product_price_query import ProductPriceQueryService
from spb_assistant_api.services.result_validator import AgentResultValidator
from spb_assistant_api.tools.product_price import ProductPriceTool

from .test_product_price_agent_loop import Facts, setup_runtime, start, state
from .test_product_price_query_service import _record


@pytest.mark.parametrize(
    "region,nature,expected",
    [
        ("上海", "RETAIL_AVERAGE", [("CITY", "310100")]),
        ("上海市", "WHOLESALE_AVERAGE", [("PROVINCE", "310000")]),
        ("北京", "WHOLESALE_AVERAGE", [("PROVINCE", "110000")]),
        ("新疆生产建设兵团", "WHOLESALE_AVERAGE", [("PROVINCE", "XJ_CORPS")]),
        ("上海", None, [("CITY", "310100"), ("PROVINCE", "310000")]),
    ],
)
def test_source_geography_not_logistics_region_codes(region, nature, expected):
    scopes = fresh_regions(
        FreshPriceConditions(
            region_text=region, price_nature=nature, source_scope="separate_sources"
        )
    )
    assert [(r.scope, r.code) for r in scopes] == expected


@pytest.mark.parametrize(
    "region,nature",
    [
        ("北京", "RETAIL_AVERAGE"),
        ("杭州市", "WHOLESALE_AVERAGE"),
        ("上海市浦东新区", "RETAIL_AVERAGE"),
        ("未知地域", "WHOLESALE_AVERAGE"),
    ],
)
def test_source_scope_never_widens_an_unknown_city_or_retail_region(region, nature):
    with pytest.raises(UnsupportedFreshScope):
        fresh_regions(FreshPriceConditions(region_text=region, price_nature=nature))


def fresh_command(record, **changes):
    conditions = {
        "kind": "fresh",
        "commodity": record.listing.identity.commodity_name,
        "region_text": "上海",
        "price_nature": "RETAIL_AVERAGE",
        **changes,
    }
    return ProductPriceCommand(conditions=conditions)


@pytest.mark.parametrize("unit,quantity", [("KG", "1"), ("G", "500"), ("JIN", "1")])
def test_supported_requested_units_preserve_original_and_normalized_fact(
    unit, quantity
):
    async def run():
        record = _record("fresh_retail_500g")
        command = fresh_command(
            record,
            requested_unit={
                "unit": unit,
                "quantity": quantity,
                "raw_text": "合成单位请求",
            },
        )
        result = await ProductPriceTool(
            ProductPriceQueryService(Facts(record))
        ).execute(command)
        AgentResultValidator().validate(command=command, result=result)
        actual = result.data.facts[0].record
        assert actual == record
        assert actual.listing.identity.quoted_unit == "CNY_PER_500G"
        assert actual.observation.unit_price.unit == "CNY_PER_KG"
        assert (
            actual.observation.unit_price.amount == actual.observation.current_price * 2
        )

    asyncio.run(run())


def test_each_unit_is_not_guessed_as_kg_or_piece_conversion():
    async def run():
        record = _record("fresh_retail_500g")
        repo = Facts(record)
        command = fresh_command(
            record, requested_unit={"unit": "EACH", "raw_text": "每个"}
        )
        with pytest.raises(AgentOperationError) as error:
            await ProductPriceTool(ProductPriceQueryService(repo)).execute(command)
        assert (
            error.value.failure.code == "price_scope_unsupported" and not repo.queries
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "constraint",
    [
        {"market_text": "不存在市场"},
        {"variety": "不存在品种"},
        {"commodity": "不相同的黄瓜"},
    ],
)
def test_fresh_hard_conditions_are_not_discarded_after_broad_recall(constraint):
    async def run():
        record = _record("fresh_retail_500g")
        result = await ProductPriceTool(
            ProductPriceQueryService(Facts(record))
        ).execute(fresh_command(record, **constraint))
        assert result.status.value == "no_match" and result.data is None

    asyncio.run(run())


def test_two_shanghai_scopes_remain_separate_facts_not_an_averaged_price():
    async def run():
        retail = _record("fresh_retail_500g")
        raw = _record("fresh_wholesale_kg").model_dump()
        raw["listing"]["identity"]["commodity_name"] = (
            retail.listing.identity.commodity_name
        )
        for field in ("pointer", "observation"):
            raw[field]["region"] = {"scope": "PROVINCE", "code": "310000"}
        wholesale = ProductPriceReadRecord.model_validate(raw)
        repo = Facts(retail, wholesale)
        command = fresh_command(
            retail, price_nature=None, source_scope="separate_sources"
        )
        result = await ProductPriceTool(ProductPriceQueryService(repo)).execute(command)
        AgentResultValidator().validate(command=command, result=result)
        assert len(repo.queries) == 2
        assert len(result.data.facts) == 2 and result.status.value == "success"
        assert (
            result.data.facts[0].record.observation.current_price
            == retail.observation.current_price
        )
        assert (
            result.data.facts[1].record.observation.current_price
            == wholesale.observation.current_price
        )

    asyncio.run(run())


def test_fresh_single_scope_multiple_market_candidates_can_be_selected():
    async def run():
        raw = _record("fresh_wholesale_kg").model_dump()
        raw["listing"]["identity"]["commodity_name"] = "鸡蛋"
        for field in ("pointer", "observation"):
            raw[field]["region"] = {"scope": "PROVINCE", "code": "110000"}
        first = ProductPriceReadRecord.model_validate(raw)
        raw = _record("fresh_wholesale_kg", offset=1000).model_dump()
        raw["listing"]["identity"].update(
            commodity_name="鸡蛋",
            source_market_id="another-market",
            source_market_name="另一合成市场",
        )
        for field in ("pointer", "observation"):
            raw[field]["region"] = {"scope": "PROVINCE", "code": "110000"}
        second = ProductPriceReadRecord.model_validate(raw)
        runtime, repo, _ = setup_runtime(first, second)
        waiting = await start(runtime, "北京鸡蛋批发价格")
        assert waiting["phase"] == "waiting_user"
        assert len((await state(runtime))["price_candidates"]["choices"]) == 2
        completed = await runtime.resume(
            thread_id="price-loop", message="第二个", owner_id="visitor-a"
        )
        assert completed["phase"] == "completed"
        assert (
            completed["result"]["data"]["facts"][0]["record"]["listing"]["identity"][
                "source_market_name"
            ]
            == "另一合成市场"
        )
        assert len(repo.queries) == 2

    asyncio.run(run())


def test_today_uses_shanghai_source_day_instead_of_utc_calendar_date():
    async def run():
        record = _record("fresh_retail_500g")
        # 16:00 UTC is the next source calendar day; both are the same China day.
        raw = record.model_dump()
        raw["read_at"] = record.observation.observed_at + timedelta(hours=10)
        record = ProductPriceReadRecord.model_validate(raw)
        command = ProductPriceCommand.model_validate(
            fresh_command(record).model_dump()
            | {"time": {"kind": "today", "raw_text": "今天"}}
        )
        result = await ProductPriceTool(
            ProductPriceQueryService(Facts(record))
        ).execute(command)
        assert result.status.value == "success"

    asyncio.run(run())


@pytest.mark.parametrize(
    "message,region",
    [
        ("上海市浦东新区黄瓜零售价", "上海市浦东新区"),
        ("河北省石家庄市黄瓜批发价", "河北省石家庄市"),
        ("新疆生产建设兵团鸡蛋批发价", "新疆生产建设兵团"),
    ],
)
def test_rule_extraction_preserves_source_region_and_rejects_finer_scope_widening(
    message, region
):
    from spb_assistant_api.services.product_price_understanding import (
        extract_product_price_slots,
    )

    slots, ambiguities = extract_product_price_slots(message)
    assert slots.conditions.region_text == region
    assert not ambiguities
    if "新区" in region or "石家庄市" in region:
        with pytest.raises(UnsupportedFreshScope):
            fresh_regions(slots.conditions)


@pytest.mark.parametrize("message", ["黄瓜多少钱", "黄瓜一斤多少钱"])
def test_explicit_multiple_sources_reply_is_not_misread_as_each_unit(message):
    async def run():
        raw = _record("fresh_retail_500g").model_dump()
        raw["listing"]["identity"]["commodity_name"] = "黄瓜"
        runtime, repo, _ = setup_runtime(ProductPriceReadRecord.model_validate(raw))
        waiting = await start(runtime, message)
        assert waiting["phase"] == "waiting_user" and not repo.queries
        result = await runtime.resume(
            thread_id="price-loop", owner_id="visitor-a", message="分别列出多个来源"
        )
        assert result["phase"] == "completed" and len(repo.queries) == 1
        assert (
            result["result"]["data"]["facts"][0]["record"]["listing"]["identity"][
                "commodity_name"
            ]
            == "黄瓜"
        )

    asyncio.run(run())
