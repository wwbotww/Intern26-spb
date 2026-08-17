from __future__ import annotations

from spb_assistant_api.tools.device_query import (
    extract_capacity_tokens,
    parse_device_query,
)


def test_parse_device_query_normalizes_brand_model_and_capacities() -> None:
    parsed = parse_device_query(
        "华为 Mate 70 Pro 12GB+512GB 多少钱"
    )

    assert parsed.brand_code == "HUAWEI"
    assert parsed.model_text == "mate 70 pro"
    assert parsed.capacities == ("12GB", "512GB")
    assert parsed.sufficient is True


def test_parse_device_query_uses_product_family_as_brand_hint() -> None:
    parsed = parse_device_query("iPhone 16 Pro 256G")

    assert parsed.brand_code == "APPLE"
    assert parsed.model_text == "16 pro"
    assert parsed.capacities == ("256GB",)
    assert parsed.sufficient is True


def test_parse_device_query_rejects_brand_only_and_context_reference() -> None:
    brand_only = parse_device_query("小米多少钱")
    context_reference = parse_device_query("这个型号呢？")
    generic_device = parse_device_query("这个设备多少钱？")

    assert brand_only.sufficient is False
    assert context_reference.sufficient is False
    assert context_reference.terms == ()
    assert generic_device.sufficient is False
    assert generic_device.terms == ()


def test_capacity_extraction_deduplicates_equivalent_units() -> None:
    assert extract_capacity_tokens(
        "运行内存 12G",
        "存储 512GB",
        "12GB+512G",
    ) == ("12GB", "512GB")
