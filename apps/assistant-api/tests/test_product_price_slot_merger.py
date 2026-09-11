import pytest

from spb_assistant_api.domain.product_price_slots import ProductPriceSlots
from spb_assistant_api.domain.slots import SlotProvenance
from spb_assistant_api.services.slot_merger import SlotMerger


def slots(**conditions):
    return ProductPriceSlots(conditions=conditions)


def test_same_device_partial_specification_refines_without_wholesale_overwrite():
    old = slots(
        kind="device",
        brand="APPLE",
        product_text="iphone 16 pro",
        specification={"capacity": "256GB"},
    )
    new = slots(kind="device", specification={"color": "蓝色"})
    result = SlotMerger().merge(existing=old, incoming=new)
    assert result.slots.conditions.product_text == "iphone 16 pro"
    assert result.slots.conditions.specification.capacity == "256GB"
    assert result.slots.conditions.specification.color == "蓝色"
    assert result.conflicts == []
    assert result.invalidate_price_candidates


@pytest.mark.parametrize("confirmed", [False, True])
def test_changed_model_atomically_resets_old_specifications(confirmed):
    old = slots(
        kind="device",
        brand="APPLE",
        product_text="iphone 16 pro",
        specification={
            "capacity": "256GB",
            "color": "蓝色",
            "attributes": {"chip": "A18"},
        },
    )
    new = slots(
        kind="device",
        brand="APPLE",
        product_text="iphone 16",
        specification={"capacity": "128GB"},
    )
    result = SlotMerger().merge(existing=old, incoming=new, confirm_overwrite=confirmed)
    if not confirmed:
        assert result.slots == old
        assert result.conflicts
        assert not result.invalidate_price_candidates
    else:
        assert result.slots.conditions.product_text == "iphone 16"
        assert result.slots.conditions.specification.capacity == "128GB"
        assert result.slots.conditions.specification.color is None
        assert result.slots.conditions.specification.attributes == {}
        assert "conditions.specification.color" in result.invalidated_slots
        assert result.invalidate_price_candidates


def test_category_change_cannot_mix_fresh_units_with_device():
    old = slots(
        kind="fresh",
        commodity="苹果",
        region_text="上海",
        price_nature="RETAIL_AVERAGE",
        requested_unit={"unit": "JIN", "raw_text": "斤"},
    )
    new = slots(kind="device", brand="APPLE", product_text="iphone 16")
    blocked = SlotMerger().merge(existing=old, incoming=new)
    assert blocked.slots == old
    assert blocked.conflicts[0].slot == "conditions.kind"
    accepted = SlotMerger().merge(existing=old, incoming=new, confirm_overwrite=True)
    assert accepted.slots == new
    assert "conditions.requested_unit" in accepted.invalidated_slots


def test_region_change_clears_stale_market_but_preserves_price_nature():
    old = slots(
        kind="fresh",
        commodity="黄瓜",
        region_text="北京",
        market_text="新发地批发市场",
        price_nature="WHOLESALE_AVERAGE",
    )
    new = slots(kind="fresh", region_text="上海")
    result = SlotMerger().merge(existing=old, incoming=new, confirm_overwrite=True)
    assert result.slots.conditions.market_text is None
    assert result.slots.conditions.price_nature == "WHOLESALE_AVERAGE"
    assert result.slots.conditions.commodity == "黄瓜"


def test_commodity_change_clears_variety_and_package_unit():
    old = slots(
        kind="fresh",
        commodity="苹果",
        variety="红富士",
        region_text="上海",
        price_nature="RETAIL_AVERAGE",
        requested_unit={"unit": "EACH", "raw_text": "个"},
    )
    new = slots(kind="fresh", commodity="黄瓜")
    result = SlotMerger().merge(existing=old, incoming=new, confirm_overwrite=True)
    assert result.slots.conditions.variety is None
    assert result.slots.conditions.requested_unit is None
    assert result.slots.conditions.region_text == "上海"


def test_noop_merge_retains_candidates_and_higher_priority_provenance():
    original = slots(
        kind="fresh",
        commodity="黄瓜",
        region_text="上海",
        price_nature="RETAIL_AVERAGE",
    )
    old = SlotProvenance(slot="conditions.commodity", source="explicit_ui")
    new = SlotProvenance(slot="conditions.commodity", source="model_extractor")
    result = SlotMerger().merge(
        existing=original,
        incoming=original,
        existing_provenance=[old],
        incoming_provenance=[new],
    )
    assert result.provenance == [old]
    assert not result.invalidate_price_candidates


def test_model_does_not_gain_overwrite_authority_from_blanket_confirmation():
    result = SlotMerger().merge(
        existing=slots(kind="fresh", commodity="黄瓜"),
        incoming=slots(kind="fresh", commodity="鸡蛋"),
        incoming_provenance=[
            SlotProvenance(slot="conditions.commodity", source="model_extractor")
        ],
        confirm_overwrite=True,
    )
    assert result.slots.conditions.commodity == "黄瓜"
    assert result.conflicts


def test_brand_only_switch_requires_a_new_model_and_clears_old_spec():
    result = SlotMerger().merge(
        existing=slots(
            kind="device",
            brand="APPLE",
            product_text="iphone 16",
            specification={"capacity": "256GB"},
        ),
        incoming=slots(kind="device", brand="HUAWEI"),
        confirm_overwrite=True,
    )
    assert result.slots.conditions.product_text is None
    assert result.slots.conditions.specification.capacity is None


def test_equivalent_unit_wording_does_not_invalidate_candidates_or_change_fingerprint():
    from spb_assistant_api.domain.product_price_slots import ProductPriceCommand
    from spb_assistant_api.domain.tooling import argument_fingerprint

    old = slots(
        kind="fresh",
        commodity="黄瓜",
        source_scope="separate_sources",
        requested_unit={"unit": "JIN", "raw_text": "一斤"},
    )
    new = slots(kind="fresh", requested_unit={"unit": "JIN", "raw_text": "1斤"})
    merged = SlotMerger().merge(existing=old, incoming=new)
    assert merged.slots == old
    assert not merged.invalidate_price_candidates
    values = old.conditions.model_dump()
    values["requested_unit"]["raw_text"] = "1斤"
    assert argument_fingerprint(
        ProductPriceCommand(conditions=old.conditions)
    ) == argument_fingerprint(ProductPriceCommand(conditions=values))


def test_model_ancestor_provenance_is_also_blocked():
    result = SlotMerger().merge(
        existing=slots(kind="fresh", commodity="黄瓜"),
        incoming=slots(kind="fresh", commodity="鸡蛋"),
        incoming_provenance=[
            SlotProvenance(slot="conditions", source="model_extractor")
        ],
        confirm_overwrite=True,
    )
    assert result.conflicts and result.slots.conditions.commodity == "黄瓜"


def test_explicit_multiple_sources_cannot_silently_keep_old_single_scope():
    old = slots(
        kind="fresh",
        commodity="黄瓜",
        region_text="上海",
        price_nature="RETAIL_AVERAGE",
        market_text="江桥",
    )
    new = slots(kind="fresh", source_scope="separate_sources")
    blocked = SlotMerger().merge(existing=old, incoming=new)
    assert blocked.slots == old
    assert blocked.conflicts[0].slot == "conditions.source_scope"
    accepted = SlotMerger().merge(existing=old, incoming=new, confirm_overwrite=True)
    assert accepted.slots.conditions.commodity == "黄瓜"
    assert accepted.slots.conditions.region_text is None
    assert accepted.slots.conditions.market_text is None
    assert accepted.slots.conditions.price_nature is None
    assert accepted.invalidate_price_candidates


def test_decimal_scale_is_not_part_of_price_command_identity():
    from spb_assistant_api.domain.product_price_slots import ProductPriceCommand
    from spb_assistant_api.domain.tooling import argument_fingerprint

    base = dict(kind="fresh", commodity="黄瓜", source_scope="separate_sources")
    one = ProductPriceCommand(
        conditions={
            **base,
            "requested_unit": {"unit": "KG", "quantity": "1.00", "raw_text": "一公斤"},
        }
    )
    two = ProductPriceCommand(
        conditions={
            **base,
            "requested_unit": {"unit": "KG", "quantity": "1", "raw_text": "1kg"},
        }
    )
    assert argument_fingerprint(one) == argument_fingerprint(two)
