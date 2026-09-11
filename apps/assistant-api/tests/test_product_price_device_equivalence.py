"""C4: V2 must meet A's explicit expectations, including both legacy defects.

This is same-evidence consumer equivalence, not different-day live price equality
and not proof of source ingestion coverage. No Gold or fixture hashes change.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from spb_assistant_api.adapters.legacy_agent_tools import DevicePriceAssistantToolAdapter
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.commands import DevicePriceCommand
from spb_assistant_api.domain.exceptions import ToolUnavailableError
from spb_assistant_api.domain.failures import FailureCategory
from spb_assistant_api.services.product_price_query import ProductPriceQueryService
from spb_assistant_api.services.result_validator import AgentResultValidator
from spb_assistant_api.tools.v2_device_price import V2DevicePriceTool

from .product_price_device_fixture import (
    DEVICE_BASELINE,
    SyntheticV2DeviceRepository,
    baseline_record_values,
    baseline_v2_record,
)


ALL_CASES = [*DEVICE_BASELINE["cases"], *DEVICE_BASELINE["known_gaps"]]


@pytest.mark.parametrize("surface", ["legacy", "agent"])
@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case["id"])
def test_v2_meets_unchanged_device_baseline_on_both_surfaces(case: dict[str, Any], surface: str) -> None:
    repository = SyntheticV2DeviceRepository(case)
    service = ProductPriceQueryService(
        repository, product_limit=20, per_product_limit=50,
        result_limit=case.get("result_limit", 20), match_threshold=65,
    )
    tool = V2DevicePriceTool(service=service)
    adapter = DevicePriceAssistantToolAdapter(tool)
    command = DevicePriceCommand(question=case["question"])
    expected = case["expected"]

    if "failure" in expected:
        if surface == "legacy":
            with pytest.raises(ToolUnavailableError):
                asyncio.run(tool.execute(case["question"]))
        else:
            with pytest.raises(AgentOperationError) as raised:
                asyncio.run(adapter.execute(command))
            failure = raised.value.failure
            assert failure.category is FailureCategory.UPSTREAM_UNAVAILABLE
            assert failure.code == expected["agent_failure_code"]
            assert failure.retryable is expected["retryable"]
    else:
        if surface == "legacy":
            result = asyncio.run(tool.execute(case["question"]))
            evidence = list(result.evidence)
            missing = list(result.missing_fields)
        else:
            result = asyncio.run(adapter.execute(command))
            AgentResultValidator().validate(command=command, result=result)
            evidence = list(result.data.evidence) if result.data else []
            missing = result.missing_slots
            assert len(result.provenance) == len(evidence)
            for item, reference in zip(evidence, result.provenance, strict=True):
                assert reference.record_id == item.evidence_id
                assert reference.source_url == item.source_url
                assert reference.source_name == item.source
        assert result.status.value == expected["status"]
        assert missing == expected.get("missing_fields", [])
        assert len(evidence) == len(expected["evidence"])
        for item, key in zip(evidence, expected["evidence"], strict=True):
            expected_fields = {
                **DEVICE_BASELINE["evidence_defaults"], **DEVICE_BASELINE["expected_evidence"][key],
            }
            assert {field: getattr(item, field) for field in expected_fields} == expected_fields
        for fragment in expected.get("warnings_contain", []):
            assert any(fragment in warning for warning in result.warnings), (fragment, result.warnings)

    assert len(repository.queries) == expected.get("repository_calls", 1)
    for query in repository.queries:
        assert query.kind == "device"
        assert query.product_limit == 20
        assert query.per_product_limit == 50
        assert query.terms


def test_same_evidence_fixture_preserves_all_source_facts_and_does_not_invent_official_ids() -> None:
    assert len(DEVICE_BASELINE["cases"]) == 27
    assert len(DEVICE_BASELINE["known_gaps"]) == 2
    product_groups = {}
    for key in DEVICE_BASELINE["records"]:
        original = baseline_record_values(key)
        record = baseline_v2_record(key)
        identity, observation = record.listing.identity, record.observation
        assert identity.official_product_id == original["official_product_id"]
        assert identity.official_sku_id == (original["official_sku_id"] or None)
        assert identity.product_name == original["product_name"]
        assert identity.model_number == (original["model_number"] or None)
        assert observation.current_price == Decimal(original["current_price"])
        assert observation.original_price == (
            Decimal(original["original_price"]) if original["original_price"] is not None else None
        )
        assert observation.original_price_type == original["original_price_type"]
        assert observation.availability == original["availability"]
        assert observation.observed_at == datetime.fromisoformat(original["observed_at"])
        assert str(record.listing.source.source_url) == (original["source_url"] or original["official_product_url"])
        assert record.listing.source.channel_name == original["channel_name"]
        for field in ("capacity", "memory", "color", "connectivity", "size"):
            assert getattr(identity.specification, field) == (original[field] or None)
        assert identity.specification.attributes == {}
        group = (identity.brand_code, identity.official_product_id)
        product_groups.setdefault(group, identity.catalog_item_id)
        assert product_groups[group] == identity.catalog_item_id
    assert len(set(product_groups.values())) == len(product_groups)
