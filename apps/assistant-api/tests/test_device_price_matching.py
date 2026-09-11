"""Product identity gates apply equally to arbitrary V1 and V2 payloads."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from spb_assistant_api.domain.device_query import parse_device_query
from spb_assistant_api.services.device_price_matching import (
    DeviceMatchCandidate,
    DevicePriceMatchingStrategy,
)


def _candidate(
    name: str = "iPhone 16 Pro",
    *,
    product_id: str | None = None,
    brand: str = "APPLE",
    series: str = "iPhone 16",
    model: str = "",
    capacities: tuple[str, ...] = ("256GB",),
    order_key: tuple[int, float, str] = (0, -1.0, "001"),
) -> DeviceMatchCandidate[dict[str, object]]:
    return DeviceMatchCandidate(
        record={"synthetic": True, "id": order_key[2], "name": name},
        product_key=(brand, product_id or name),
        brand_code=brand,
        brand_name=brand,
        product_name=name,
        series_name=series,
        model_number=model,
        capacities=capacities,
        order_key=order_key,
    )


def _match(question: str, *candidates: DeviceMatchCandidate):
    return DevicePriceMatchingStrategy().match(
        parse_device_query(question), candidates, match_threshold=65
    )


@pytest.mark.parametrize(
    ("question", "name"),
    [
        ("iPhone 16 Pro 多少钱", "iPhone 16 Pro Max"),
        ("iPhone 16 Pro 多少钱", "iPhone 16 ProMax"),
        ("iPhone 16 Pro 多少钱", "iPhone16ProMax"),
        ("iPhone 16 多少钱", "iPhone 16 Pro"),
        ("iPhone 16 多少钱", "iPhone 16 Pro Max"),
        ("iPhone 16 Pro Max 多少钱", "iPhone 16 Pro"),
        ("iPhone 16 Pro 多少钱", "iPhone 16"),
        ("iPhone 16 Plus 多少钱", "iPhone 16"),
        ("iPhone 16 多少钱", "iPhone 16 Plus"),
        ("iPhone 16 Pro 多少钱", "iPhone 17 Pro"),
    ],
)
def test_wrong_variant_or_generation_cannot_be_rescued_by_fuzzy_score(
    question: str, name: str
) -> None:
    candidate = _candidate(name, series=name)
    result = DevicePriceMatchingStrategy().match(
        parse_device_query(question), [candidate], match_threshold=0
    )
    assert result.ranked == ()
    assert result.specification_mismatch is False


@pytest.mark.parametrize(
    "question",
    ["iPhone 16 Pro Max 多少钱", "iPhone16ProMax 多少钱", "iPhone 16 ProMax 多少钱"],
)
@pytest.mark.parametrize("name", ["iPhone 16 Pro Max", "iPhone16ProMax"])
def test_compact_variant_chain_is_equivalent_to_separated_variants(
    question: str, name: str
) -> None:
    candidate = _candidate(name)
    result = _match(question, candidate)
    assert len(result.ranked) == 1
    assert result.ranked[0].record is candidate.record
    assert result.ranked[0].score == 100


def test_base_does_not_aggregate_pro_despite_shared_series_exact_match() -> None:
    base = _candidate("iPhone 16", order_key=(0, -1, "002"))
    pro = _candidate(order_key=(0, -2, "001"))
    result = _match("iPhone 16 多少钱", pro, base)
    assert [item.record for item in result.ranked] == [base.record]


def test_explicit_brand_rejects_same_named_product_from_other_brand() -> None:
    result = _match("Apple iPhone 16 Pro 多少钱", _candidate(brand="HUAWEI"))
    assert result.ranked == ()


def test_family_and_model_do_not_come_from_sku_capacity_payload() -> None:
    candidate = _candidate(
        "MacBook Pro", series="MacBook Pro", capacities=("16GB", "256GB")
    )
    candidate.record["sku_name"] = "iPhone 16 Pro 256GB"
    result = _match("iPhone 16 Pro 256GB 多少钱", candidate)
    assert result.ranked == ()
    assert result.specification_mismatch is False


def test_required_specification_failure_is_distinct_from_identity_failure() -> None:
    result = _match("iPhone 16 Pro 512GB 多少钱", _candidate())
    assert result.ranked == ()
    assert result.specification_mismatch is True


def test_capacity_match_cannot_make_wrong_variant_substitute_for_right_product() -> None:
    pro = _candidate(capacities=("256GB",))
    pro_max = _candidate("iPhone 16 Pro Max", capacities=("512GB",))
    result = _match("iPhone 16 Pro 512GB 多少钱", pro, pro_max)
    assert result.ranked == ()
    assert result.specification_mismatch is True


def test_memory_capacity_plus_does_not_become_model_plus_variant() -> None:
    candidate = _candidate(
        "HUAWEI Mate 70 Pro", brand="HUAWEI", series="Mate 70",
        capacities=("12GB", "512GB"),
    )
    result = _match("华为 Mate 70 Pro 12GB+512GB 多少钱", candidate)
    assert [item.record for item in result.ranked] == [candidate.record]


def test_product_plus_variant_stays_distinct_after_memory_capacity_removal() -> None:
    pro = _candidate(
        "HUAWEI Mate 70 Pro", brand="HUAWEI", series="Mate 70",
        capacities=("12GB", "512GB"),
    )
    plus = replace(pro, product_name="HUAWEI Mate 70 Pro+", record={"name": "plus"})
    result = _match("华为 Mate 70 Pro+ 12GB+512GB 多少钱", pro, plus)
    assert [item.record for item in result.ranked] == [plus.record]


def test_each_candidate_must_pass_identity_even_if_product_key_is_shared() -> None:
    correct = _candidate(product_id="shared")
    wrong = _candidate("iPhone 16 Pro Max", product_id="shared")
    result = _match("iPhone 16 Pro 多少钱", correct, wrong)
    assert [item.record for item in result.ranked] == [correct.record]


def test_only_best_product_is_retained_before_specification_filter() -> None:
    exact = _candidate(
        "Mac Studio", brand="APPLE", series="Mac Studio", capacities=("256GB",)
    )
    partial = _candidate(
        "Mac", brand="APPLE", series="Mac", capacities=("512GB",)
    )
    result = _match("Mac Studio 512GB 多少钱", partial, exact)
    assert result.ranked == ()
    assert result.specification_mismatch is True


def test_all_equal_best_product_groups_remain_candidates_in_stable_order() -> None:
    later = _candidate(product_id="second", order_key=(0, -2, "003"))
    older = _candidate(product_id="first", order_key=(0, -1, "001"))
    result = _match("iPhone 16 Pro 多少钱", older, later)
    assert [item.record for item in result.ranked] == [later.record, older.record]


def test_sku_order_is_supplied_by_caller_and_payload_never_reconstructed() -> None:
    available_old = _candidate(order_key=(0, -1, "002"))
    available_new = _candidate(order_key=(0, -2, "003"))
    unavailable_new = _candidate(order_key=(3, -3, "001"))
    result = _match(
        "iPhone 16 Pro 多少钱", unavailable_new, available_old, available_new
    )
    assert [item.record for item in result.ranked] == [
        available_new.record, available_old.record, unavailable_new.record
    ]
    assert result.ranked[0].record is available_new.record


def test_insufficient_query_cannot_select_any_payload() -> None:
    assert _match("Pro 多少钱", _candidate()).ranked == ()


def test_candidate_and_result_are_immutable_metadata() -> None:
    candidate = _candidate()
    result = _match("iPhone 16 Pro 多少钱", candidate)
    with pytest.raises(FrozenInstanceError):
        candidate.model_number = "other"
    with pytest.raises(FrozenInstanceError):
        result.specification_mismatch = True
