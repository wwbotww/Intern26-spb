from __future__ import annotations

import asyncio
import hashlib
import json
from decimal import Decimal, localcontext

import pytest
from pydantic import TypeAdapter, ValidationError

from spb_assistant_api.adapters.fake_shipping import FakePostageGateway
from spb_assistant_api.api.agent_schemas import AgentResultResponse
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.postage import PostageFeeItem, PostageQuoteBasis, QuoteMoney, exact_weight_grams
from spb_assistant_api.domain.postage_observation import PostageQuoteObservation
from spb_assistant_api.domain.results import AgentResult, PostageData
from spb_assistant_api.domain.slots import RegionRef, WeightValue
from spb_assistant_api.domain.tooling import argument_fingerprint
from spb_assistant_api.services.postage_preflight import PostageCatalog, PostagePreflight
from spb_assistant_api.services.result_validator import AgentResultValidator
from spb_assistant_api.tools.postage import PostageTool

from .postage_p1_fixture import FIXTURES, NOW, REGIONS, command, observation, preflight


@pytest.mark.parametrize("value,unit,expected", [("1.25", "kg", 1250), ("1250", "g", 1250), ("0.001", "kg", 1), ("30000", "g", 30000)])
def test_exact_grams(value, unit, expected):
    assert exact_weight_grams(Decimal(value), unit, maximum=30000) == expected


@pytest.mark.parametrize("value,unit", [("0", "g"), ("-1", "kg"), ("NaN", "g"), ("Infinity", "kg"), ("0.1", "g"), ("0.0001", "kg"), ("30000.1", "g"), ("1e100000", "g"), ("1e-100000", "kg"), ("1", "lb")])
def test_unexecutable_weight_is_rejected(value, unit):
    with pytest.raises(ValueError):
        exact_weight_grams(Decimal(value), unit, maximum=30000)


def test_gram_conversion_does_not_use_ambient_decimal_rounding():
    with localcontext() as ctx:
        ctx.prec = 2
        assert exact_weight_grams(Decimal("1.255"), "kg", maximum=30000) == 1255
        with pytest.raises(ValueError):
            exact_weight_grams(Decimal("1.25500000000000001"), "kg", maximum=30000)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_weight_value_is_finite_even_on_legacy_path(value):
    with pytest.raises(ValidationError):
        WeightValue(value=value)


@pytest.mark.parametrize("value", [None, "", "NaN", "Infinity", "-1", "12.301", "1000000000"])
def test_money_is_not_coerced_to_zero_or_rounded(value):
    with pytest.raises(ValidationError):
        TypeAdapter(QuoteMoney).validate_python(value)


def test_zero_money_is_an_explicit_value_and_null_is_not_zero():
    assert TypeAdapter(QuoteMoney).validate_python("0.00") == Decimal(0)
    fee = PostageFeeItem(kind="fuel", amount="0.00")
    assert fee.included_in_amount == "unknown"
    with pytest.raises(ValidationError):
        PostageQuoteBasis(context=preflight().prepare(command()).pricing_context, fees=(fee, fee))


def test_catalog_is_explicit_and_synthetic():
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    assert manifest["supplier_confirmed"] is False
    assert manifest["live_requests"] == 0
    cfg = preflight().catalog
    assert cfg.evidence == "synthetic"
    assert all(p.code.startswith("SYN-") for p in cfg.products)
    for name in ("currency", "amount_kind", "products", "origins", "destinations"):
        raw = cfg.model_dump()
        raw.pop(name)
        with pytest.raises(ValidationError):
            PostageCatalog.model_validate(raw)
    for field, invalid in (("name", "   "), ("aliases", ["  "])):
        raw = cfg.model_dump(mode="json")
        raw["products"][0][field] = invalid
        with pytest.raises(ValidationError):
            PostageCatalog.model_validate(raw)


@pytest.mark.parametrize("key", ["products", "origins", "destinations"])
def test_catalog_rejects_duplicate_or_empty_bindings(key):
    raw = preflight().catalog.model_dump()
    raw[key] = [raw[key][0], raw[key][0]]
    with pytest.raises(ValueError):
        PostagePreflight(PostageCatalog.model_validate(raw))
    raw[key] = []
    with pytest.raises(ValueError):
        PostagePreflight(PostageCatalog.model_validate(raw))


def test_products_are_resolved_only_from_catalog_mentions():
    policy = preflight()
    assert policy.product_mentions("演示甲") == ["SYN-A"]
    assert policy.product_mentions("SYN-A SYN-B") == ["SYN-A", "SYN-B"]
    assert policy.product_mentions("SYN-AX UNKNOWN") == []
    assert policy.product_mentions("syn-a") == ["SYN-A"]
    assert policy.select_product("产品 UNKNOWN 或 SYN-A", model_product=None) == (None, True)


def test_name_resolution_does_not_imply_billing_region_support():
    policy = preflight()
    for region in (REGIONS.resolve("广州"), RegionRef(raw_text="北京", canonical_name="北京市", resolution="resolved")):
        with pytest.raises(AgentOperationError, match="计费地区"):
            policy.prepare(command(origin=region))
    # A known city parent can serve as the explicitly configured origin binding.
    prepared = policy.prepare(command(origin=REGIONS.resolve("北京朝阳")))
    assert prepared.pricing_context.origin_billing_id == "synthetic-origin-beijing"
    with pytest.raises(AgentOperationError):
        policy.prepare(command(destination=REGIONS.resolve("北京朝阳")))


@pytest.mark.parametrize("changes", [{"product_code": None}, {"product_code": "UNKNOWN"}, {"declared_value": "0"}, {"declared_value": "500"}])
def test_cannot_execute_unknown_product_or_ignore_declared_value(changes):
    with pytest.raises(AgentOperationError):
        preflight().prepare(command(**changes))


def test_context_is_frozen_before_fingerprinting_and_configuration_drift_is_detected():
    policy = preflight()
    prepared = policy.prepare(command())
    assert prepared.pricing_context.weight_grams == 1250
    assert prepared.weight.value == Decimal("1.25")
    assert argument_fingerprint(prepared) != argument_fingerprint(command())
    changed = preflight(currency="USD")  # Same catalog version but different content.
    assert changed.fingerprint != policy.fingerprint
    with pytest.raises(AttributeError):
        policy.catalog = changed.catalog
    with pytest.raises(AgentOperationError) as failure:
        changed.validate_command(prepared)
    assert failure.value.failure.code == "postage_context_changed_restart"


def test_legacy_command_fingerprint_and_result_remain_readable():
    cmd = command()
    values = cmd.model_dump(mode="json")
    values.pop("pricing_context")
    old = "sha256:" + hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert argument_fingerprint(cmd) == old
    data = observation(preflight().prepare(cmd)).data.model_dump()
    data.pop("quote_basis")
    data["billable_weight"] = None
    assert PostageData.model_validate(data).quote_basis is None


@pytest.mark.parametrize("change", [{"billable_weight": None}, {"currency": "USD"}, {"product_code": "SYN-B"}, {"amount": "12.345"}, {"queried_at": NOW.replace(tzinfo=None)}])
def test_explicit_quote_requires_billable_weight_product_currency_and_time(change):
    with pytest.raises(ValidationError):
        observation(preflight().prepare(command()), **change)


@pytest.mark.parametrize("legacy", [True, False])
def test_strict_tool_does_not_promote_legacy_data_or_none_to_an_observed_quote(legacy):
    cmd = preflight().prepare(command())
    data = observation(cmd).data if legacy else None
    gateway = FakePostageGateway(data)
    with pytest.raises(AgentOperationError) as failure:
        asyncio.run(PostageTool(gateway, preflight=preflight()).execute(cmd))
    assert failure.value.failure.code == "postage_observation_required"


def test_unprepared_old_pending_execution_cannot_call_strict_gateway():
    gateway = FakePostageGateway()
    with pytest.raises(AgentOperationError):
        asyncio.run(PostageTool(gateway, preflight=preflight()).execute(command()))
    assert gateway.commands == []


def test_observation_and_restored_result_are_validated_before_use():
    policy = preflight()
    cmd = policy.prepare(command())
    observed = observation(cmd)
    result = asyncio.run(PostageTool(FakePostageGateway(observed), preflight=policy).execute(cmd))
    AgentResultValidator().validate(command=cmd, result=result)
    assert result.provenance[0].source_type == "fake_gateway"
    assert "合成" in result.warnings[0]
    assert "最终支付价" in result.answer
    raw = result.model_dump(mode="json")
    raw["data"]["quote_basis"] = None
    with pytest.raises(AgentOperationError):
        AgentResultValidator().validate(command=cmd, result=AgentResult.model_validate(raw))
    raw = result.model_dump(mode="json")
    raw["provenance"][0]["source_profile"] = ""
    with pytest.raises(AgentOperationError):
        AgentResultValidator().validate(command=cmd, result=AgentResult.model_validate(raw))
    with pytest.raises(ValidationError):
        PostageQuoteObservation.model_validate({**observed.model_dump(), "queried_at": NOW.replace(tzinfo=None)})


def test_p1_internal_pricing_bindings_are_not_implicitly_added_to_public_api():
    policy = preflight()
    cmd = policy.prepare(command())
    result = asyncio.run(PostageTool(FakePostageGateway(observation(cmd)), preflight=policy).execute(cmd))
    public = AgentResultResponse.from_domain(result)
    assert "quote_basis" not in public.data
    assert "synthetic-origin-beijing" not in public.model_dump_json()
    assert policy.fingerprint not in public.model_dump_json()
    assert public.provenance == []  # Coordinated public source projection is P3.
    assert public.data["amount"] == "12.30"


def test_synthetic_catalog_cannot_label_its_quote_as_a_real_source():
    policy = preflight()
    cmd = policy.prepare(command())
    observed = observation(cmd)
    observed = observed.model_copy(update={"source": observed.source.model_copy(update={"source_type": "external_api"})})
    with pytest.raises(AgentOperationError) as failure:
        asyncio.run(PostageTool(FakePostageGateway(observed), preflight=policy).execute(cmd))
    assert failure.value.failure.code == "postage_observation_invalid"
