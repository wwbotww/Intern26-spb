"""Synthetic A1 capability baseline shared by legacy and typed Agent adapters.

The fixture contains explicit expectations, not snapshots generated from the
implementation. Known wrong identity matches remain strict xfails until stage C;
they must never be copied into the V2 replacement's accepted behaviour.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from spb_assistant_api.adapters.legacy_agent_tools import (
    DevicePriceAssistantToolAdapter,
)
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.commands import DevicePriceCommand
from spb_assistant_api.domain.device_price import (
    DevicePriceRecord,
    DevicePriceSearchQuery,
)
from spb_assistant_api.domain.exceptions import (
    PriceRepositoryUnavailableError,
    ToolUnavailableError,
)
from spb_assistant_api.domain.failures import FailureCategory
from spb_assistant_api.services.result_validator import AgentResultValidator
from spb_assistant_api.tools.device_price import DevicePriceTool


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "product_price"
    / "legacy_device_baseline.json"
)
BASELINE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class _SyntheticRepository:
    """Returns deliberately unfiltered candidates; no DB, HTTP or model access."""

    def __init__(self, case: dict[str, Any]) -> None:
        self._case = case
        self.queries: list[DevicePriceSearchQuery] = []

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def readiness(self) -> str:
        return "ready"

    async def search(
        self, query: DevicePriceSearchQuery
    ) -> list[DevicePriceRecord]:
        self.queries.append(query)
        if self._case.get("repository_unavailable"):
            raise PriceRepositoryUnavailableError("synthetic unavailable")
        return [_record(key) for key in self._case["records"]]


def _record(key: str) -> DevicePriceRecord:
    values = {**BASELINE["record_defaults"], **BASELINE["records"][key]}
    values["current_price"] = Decimal(values["current_price"])
    if values["original_price"] is not None:
        values["original_price"] = Decimal(values["original_price"])
    values["observed_at"] = datetime.fromisoformat(values["observed_at"])
    return DevicePriceRecord(**values)


def _assert_evidence(evidence: list[Any], expected_keys: list[str]) -> None:
    assert len(evidence) == len(expected_keys)
    for item, key in zip(evidence, expected_keys, strict=True):
        expected = {
            **BASELINE["evidence_defaults"],
            **BASELINE["expected_evidence"][key],
        }
        assert {field: getattr(item, field) for field in expected} == expected


def _check_case(case: dict[str, Any], surface: str) -> None:
    repository = _SyntheticRepository(case)
    tool = DevicePriceTool(
        repository=repository,
        candidate_limit=500,
        result_limit=case.get("result_limit", 20),
        match_threshold=65,
    )
    expected = case["expected"]
    command = DevicePriceCommand(question=case["question"])
    adapter = DevicePriceAssistantToolAdapter(tool)

    if "failure" in expected:
        if surface == "legacy":
            with pytest.raises(ToolUnavailableError):
                asyncio.run(tool.execute(case["question"]))
        else:
            with pytest.raises(AgentOperationError) as raised:
                asyncio.run(adapter.execute(command))
            assert raised.value.failure.category is (
                FailureCategory.UPSTREAM_UNAVAILABLE
            )
            assert raised.value.failure.code == expected["agent_failure_code"]
            assert raised.value.failure.retryable is expected["retryable"]
    else:
        if surface == "legacy":
            result = asyncio.run(tool.execute(case["question"]))
            evidence = list(result.evidence)
            missing_fields = list(result.missing_fields)
        else:
            result = asyncio.run(adapter.execute(command))
            AgentResultValidator().validate(command=command, result=result)
            evidence = list(result.data.evidence) if result.data else []
            missing_fields = result.missing_slots
            assert len(result.provenance) == len(evidence)
            for item, source in zip(evidence, result.provenance, strict=True):
                assert source.record_id == item.evidence_id
                assert source.source_url == item.source_url
                assert source.source_name == item.source
        assert result.status.value == expected["status"]
        assert missing_fields == expected.get("missing_fields", [])
        _assert_evidence(evidence, expected["evidence"])
        for fragment in expected.get("warnings_contain", []):
            assert any(fragment in warning for warning in result.warnings)

    assert len(repository.queries) == expected.get("repository_calls", 1)
    for query in repository.queries:
        assert query.limit == 500
        assert query.terms


@pytest.mark.parametrize("surface", ["legacy", "agent"])
@pytest.mark.parametrize("case", BASELINE["cases"], ids=lambda case: case["id"])
def test_legacy_device_capability_baseline(
    case: dict[str, Any], surface: str
) -> None:
    _check_case(case, surface)


@pytest.mark.parametrize("surface", ["legacy", "agent"])
@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            case,
            id=case["id"],
            marks=pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason=f"Planned stage {case['planned_stage']}: {case['reason']}",
            ),
        )
        for case in BASELINE["known_gaps"]
    ],
)
def test_target_identity_guards_not_yet_met_by_legacy_tool(
    case: dict[str, Any], surface: str
) -> None:
    _check_case(case, surface)


def test_legacy_baseline_fixture_is_explicit_and_self_contained() -> None:
    assert BASELINE["synthetic"] is True
    cases = [*BASELINE["cases"], *BASELINE["known_gaps"]]
    identifiers = [case["id"] for case in cases]
    assert len(identifiers) == len(set(identifiers))
    for case in cases:
        assert case["question"]
        assert set(case["records"]) <= BASELINE["records"].keys()
        if "evidence" in case["expected"]:
            assert set(case["expected"]["evidence"]) <= (
                BASELINE["expected_evidence"].keys()
            )
            assert set(case["expected"]["evidence"]) <= set(case["records"])
    for key in BASELINE["records"]:
        record = _record(key)
        assert record.official_sku_id.startswith("synthetic-")
        assert record.official_product_id.startswith("synthetic-")
        assert record.observed_at.tzinfo is not None
        assert record.current_price.is_finite()
        assert record.original_price_type in {
            "CROSSED_OUT", "MSRP", "EXPLICIT_ORIGINAL", "NONE"
        }
