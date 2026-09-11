"""Controlled C-stage composition: synthetic facts, no dotenv or real DB."""

from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from spb_assistant_api import configured_price
from spb_assistant_api.api.app import _default_tools, create_app
from spb_assistant_api.domain.exceptions import PriceRepositoryUnavailableError
from spb_assistant_api.domain.models import QueryMode
from spb_assistant_api.domain.product_price import ProductPriceReadRecord
from spb_assistant_api.domain.product_price_query import PriceReadBatch
from spb_assistant_api.settings import AssistantSettings
from spb_assistant_api.tools.device_price import DevicePriceTool
from spb_assistant_api.tools.unavailable import UnavailableTool
from spb_assistant_api.tools.v2_device_price import V2DevicePriceTool


app_module = importlib.import_module("spb_assistant_api.api.app")
AUTH = {"Authorization": "Bearer synthetic-price-composition-key"}
QUESTION = "苹果 iPhone 16 Pro 256GB 价格"


def synthetic_record() -> ProductPriceReadRecord:
    payload = json.loads((Path(__file__).parent / "fixtures" / "product_price" / "read_records.json").read_text())["device_priced"]
    payload["listing"]["identity"].update(
        product_name="iPhone 16 Pro", series_name="iPhone", model_number="iPhone 16 Pro",
    )
    return ProductPriceReadRecord.model_validate(payload)


class Repository:
    def __init__(self, *, records=(), ready="ready", start_error=None, close_error=None):
        self.records = records
        self.ready = ready
        self.start_error = start_error
        self.close_error = close_error
        self.starts = self.stops = 0
        self.queries = []

    async def initialize(self):
        self.starts += 1
        if self.start_error:
            raise self.start_error

    async def close(self):
        self.stops += 1
        if self.close_error:
            raise self.close_error

    def readiness(self):
        return self.ready if self.starts > self.stops else "not_ready"

    async def search(self, query):
        self.queries.append(query)
        if self.readiness() != "ready":
            raise PriceRepositoryUnavailableError("synthetic unavailable")
        return PriceReadBatch(records=self.records)


def settings(tmp_path, **changes):
    return AssistantSettings(**{
        "price_data_model": "catalog_v2",
        "agent_enabled": True,
        "agent_database_path": str(tmp_path / "agent.db"),
        "api_keys": "synthetic-price-composition-key",
        "rate_limit_enabled": False,
        "rag_base_url": "", "rag_api_key": "", "mysql_dsn": "",
        "query_model_enabled": False, "tracking_enabled": False,
        **changes,
    })


def forbid_v1(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("V2 must never construct the V1 SQL repository")

    monkeypatch.setattr(app_module, "MySQLPriceRepository", forbidden)


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://assistant.test")


@pytest.mark.parametrize("records", [(), ("device",)])
def test_v1_and_agent_share_one_v2_service_and_repository(tmp_path, monkeypatch, records):
    forbid_v1(monkeypatch)
    repository = Repository(records=(synthetic_record(),) if records else ())
    app = create_app(settings=settings(tmp_path), product_price_repository=repository)
    wrapper = app.state.registry.get(QueryMode.DEVICE_PRICE)
    assert isinstance(wrapper, V2DevicePriceTool)
    assert repository.starts == repository.stops == 0 and repository.queries == []
    assert not (tmp_path / "agent.db").exists()

    async def scenario():
        async with app.router.lifespan_context(app), client_for(app) as client:
            v1 = await client.post("/v1/chat", headers=AUTH, json={"mode": "device_price", "question": QUESTION, "stream": False})
            v2 = await client.post("/v2/agent/messages", headers={**AUTH, "Idempotency-Key": "synthetic-price-shared"}, json={
                "message": QUESTION, "explicit_intent": "product_price",
            })
            assert v1.status_code == v2.status_code == 200
            assert v2.json()["result"]["status"] == ("success" if records else "no_match")
            if records:
                old_evidence = v1.json()["evidence"][0]
                new_evidence = v2.json()["result"]["data"]["items"][0]["quote"]
                assert old_evidence["price"] == new_evidence["current_price"]
                for key in ("original_price", "original_price_type", "observed_at"):
                    assert old_evidence[key] == new_evidence[key]
            assert len(repository.queries) == 2
            assert repository.queries[0] == repository.queries[1]
            assert repository.starts == 1 and repository.stops == 0
            ready = (await client.get("/v2/agent/health/ready")).json()
            assert ready["checks"]["capability.product_price"] == "ready"
        assert repository.starts == repository.stops == 1
        assert wrapper.readiness() == "not_ready"
        assert app.state.agent_api is None

    asyncio.run(scenario())


def test_catalog_v2_dsn_constructs_only_owned_v2_repository(tmp_path, monkeypatch):
    forbid_v1(monkeypatch)
    repository = Repository()
    construction = []

    def factory(**kwargs):
        construction.append(kwargs)
        return repository

    monkeypatch.setattr(configured_price, "MySQLProductPriceRepository", factory)
    app = create_app(settings=settings(tmp_path, mysql_dsn="mysql+pymysql://synthetic:synthetic@127.0.0.1/synthetic_price"))
    assert len(construction) == 1 and repository.starts == 0
    assert construction[0]["pool_size"] == 5
    assert isinstance(app.state.registry.get(QueryMode.DEVICE_PRICE), V2DevicePriceTool)

    async def scenario():
        async with app.router.lifespan_context(app):
            assert repository.starts == 1
        assert repository.stops == 1

    asyncio.run(scenario())


def test_default_price_model_remains_legacy_without_constructing_v2(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("default price model must not construct V2")

    monkeypatch.setattr(configured_price, "MySQLProductPriceRepository", forbidden)
    configured = AssistantSettings(mysql_dsn="mysql+pymysql://synthetic@127.0.0.1/synthetic_price")
    assert configured.price_data_model == "device_v1"
    assert configured_price.configured_product_price(configured) is None
    tools = _default_tools(configured)
    assert isinstance(tools[QueryMode.DEVICE_PRICE], DevicePriceTool)


def test_catalog_v2_without_dsn_is_unavailable_and_never_falls_back(tmp_path, monkeypatch):
    forbid_v1(monkeypatch)
    app = create_app(settings=settings(tmp_path))
    assert isinstance(app.state.registry.get(QueryMode.DEVICE_PRICE), UnavailableTool)

    async def scenario():
        async with app.router.lifespan_context(app), client_for(app) as client:
            v1 = await client.post("/v1/chat", headers=AUTH, json={"mode": "device_price", "question": QUESTION, "stream": False})
            assert v1.status_code == 503
            assert v1.json()["detail"]["code"] == "tool_unavailable"
            capabilities = (await client.get("/v2/agent/capabilities", headers=AUTH)).json()
            assert not next(item for item in capabilities if item["intent"] == "product_price")["available"]

    asyncio.run(scenario())


def test_default_tools_catalog_dsn_cannot_create_an_unowned_repository(tmp_path, monkeypatch):
    forbid_v1(monkeypatch)
    tools = _default_tools(settings(tmp_path, mysql_dsn="mysql+pymysql://synthetic@127.0.0.1/synthetic_price"))
    assert isinstance(tools[QueryMode.DEVICE_PRICE], UnavailableTool)


@pytest.mark.parametrize("ready", ["not_ready", "contract_error"])
def test_v2_readiness_failure_stays_v2_and_closes_once(tmp_path, monkeypatch, ready):
    forbid_v1(monkeypatch)
    repository = Repository(ready=ready)
    app = create_app(settings=settings(tmp_path), product_price_repository=repository)

    async def scenario():
        async with app.router.lifespan_context(app), client_for(app) as client:
            health = await client.get("/health/ready")
            assert health.status_code == 503
            assert health.json()["checks"]["device_price"] == ready
            result = await client.post("/v1/chat", headers=AUTH, json={"mode": "device_price", "question": QUESTION, "stream": False})
            assert result.status_code == 503
        assert repository.starts == repository.stops == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_v2_initialization_failure_closes_once_and_preserves_primary_error(tmp_path, cleanup_fails):
    repository = Repository(
        start_error=RuntimeError("synthetic initialization failure"),
        close_error=RuntimeError("synthetic cleanup failure") if cleanup_fails else None,
    )
    app = create_app(settings=settings(tmp_path), product_price_repository=repository)

    async def scenario():
        with pytest.raises(RuntimeError, match="synthetic initialization failure"):
            async with app.router.lifespan_context(app):
                pytest.fail("failed initialization cannot yield")
        assert repository.starts == repository.stops == 1
        assert not (tmp_path / "agent.db").exists()

    asyncio.run(scenario())


def test_later_agent_startup_failure_closes_the_v2_repository(tmp_path):
    repository = Repository()
    app = create_app(settings=settings(tmp_path, agent_database_path=str(tmp_path / "missing" / "agent.db")), product_price_repository=repository)

    async def scenario():
        with pytest.raises(sqlite3.OperationalError):
            async with app.router.lifespan_context(app):
                pytest.fail("missing parent directory must fail")
        assert repository.starts == repository.stops == 1
        assert app.state.agent_api is None

    asyncio.run(scenario())


def test_price_startup_failure_also_closes_already_allocated_policy_client(tmp_path, monkeypatch):
    price = Repository(start_error=RuntimeError("synthetic price initialization failure"))
    policy = Repository()
    monkeypatch.setattr(app_module, "RagPolicyClient", lambda **kwargs: policy)
    app = create_app(
        settings=settings(tmp_path, rag_base_url="http://rag.example.test", rag_api_key="synthetic-rag-key"),
        product_price_repository=price,
    )

    async def scenario():
        with pytest.raises(RuntimeError, match="synthetic price initialization failure"):
            async with app.router.lifespan_context(app):
                pytest.fail("price startup must fail")
        assert price.starts == price.stops == 1
        assert policy.starts == 0 and policy.stops == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("policy_start_error", [False, True])
def test_policy_lifecycle_remains_registry_owned_and_cannot_leak_price(tmp_path, monkeypatch, policy_start_error):
    price = Repository()
    policy = Repository(
        start_error=RuntimeError("synthetic policy initialization failure") if policy_start_error else None,
    )
    monkeypatch.setattr(app_module, "RagPolicyClient", lambda **kwargs: policy)
    app = create_app(settings=settings(tmp_path, rag_base_url="http://rag.example.test", rag_api_key="synthetic-rag-key"), product_price_repository=price)

    async def scenario():
        if policy_start_error:
            with pytest.raises(RuntimeError, match="synthetic policy initialization failure"):
                async with app.router.lifespan_context(app):
                    pytest.fail("policy startup must fail")
        else:
            async with app.router.lifespan_context(app):
                assert price.starts == policy.starts == 1
                assert price.stops == policy.stops == 0
        assert price.starts == price.stops == policy.starts == policy.stops == 1

    asyncio.run(scenario())


def test_v2_data_can_serve_v1_http_without_enabling_agent(tmp_path):
    repository = Repository()
    app = create_app(settings=settings(tmp_path, agent_enabled=False, agent_database_path=""), product_price_repository=repository)
    assert not any(route.startswith("/v2/") for route in app.openapi()["paths"])

    async def scenario():
        async with app.router.lifespan_context(app), client_for(app) as client:
            result = await client.post("/v1/chat", headers=AUTH, json={"mode": "device_price", "question": QUESTION, "stream": False})
            assert result.status_code == 200
            assert result.json()["finish_reason"] == "no_match"
        assert repository.starts == repository.stops == 1
        assert len(repository.queries) == 1

    asyncio.run(scenario())


def test_shutdown_failure_is_not_silently_ignored(tmp_path):
    repository = Repository(close_error=RuntimeError("synthetic shutdown failure"))
    app = create_app(settings=settings(tmp_path), product_price_repository=repository)

    async def scenario():
        with pytest.raises(RuntimeError, match="synthetic shutdown failure"):
            async with app.router.lifespan_context(app):
                pass
        assert repository.starts == repository.stops == 1
        assert app.state.agent_api is None

    asyncio.run(scenario())


def test_wrapper_lifecycle_never_initializes_or_closes_the_borrowed_repository(tmp_path):
    repository = Repository()
    bundle = configured_price.configured_product_price(settings(tmp_path), repository=repository)

    async def scenario():
        await bundle.tool.initialize()
        await bundle.tool.close()
        assert repository.starts == repository.stops == 0

    asyncio.run(scenario())


def test_injected_repository_requires_explicit_model_and_no_conflicting_tools(tmp_path):
    repository = Repository()
    with pytest.raises(ValueError, match="catalog_v2"):
        create_app(settings=settings(tmp_path, price_data_model="device_v1"), product_price_repository=repository)
    with pytest.raises(ValueError, match="不能同时"):
        create_app(settings=settings(tmp_path), tools={}, product_price_repository=repository)
    assert repository.starts == repository.stops == 0


@pytest.mark.parametrize("changes", [
    {"price_data_model": "auto"}, {"price_data_model": "v2"},
    {"price_v2_product_limit": 0}, {"price_v2_product_limit": 21},
    {"price_v2_per_product_limit": 0}, {"price_v2_per_product_limit": 51},
    {"price_v2_product_limit": 1, "price_v2_per_product_limit": 2, "price_result_limit": 3},
])
def test_invalid_v2_configuration_is_rejected(tmp_path, changes):
    with pytest.raises(ValidationError):
        settings(tmp_path, **changes)


def test_candidate_budget_validation_is_specific_to_selected_data_model(tmp_path):
    settings(tmp_path, price_candidate_limit=1, price_result_limit=50)
    with pytest.raises(ValidationError, match="price_candidate_limit"):
        settings(tmp_path, price_data_model="device_v1", price_candidate_limit=1)
    # V1 does not mistakenly use the V2 per-product candidate budget.
    settings(tmp_path, price_data_model="device_v1", price_v2_product_limit=1, price_v2_per_product_limit=1)


def test_v2_query_budgets_are_passed_to_shared_service(tmp_path):
    repository = Repository()
    app = create_app(settings=settings(tmp_path, price_v2_product_limit=2, price_v2_per_product_limit=3, price_result_limit=4), product_price_repository=repository)

    async def scenario():
        async with app.router.lifespan_context(app):
            await app.state.dispatcher.dispatch(mode=QueryMode.DEVICE_PRICE, question=QUESTION)
        assert repository.queries[0].product_limit == 2
        assert repository.queries[0].per_product_limit == 3

    asyncio.run(scenario())


def test_copied_invalid_price_configuration_fails_before_resource_construction(tmp_path, monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("invalid copied settings must fail before resource construction")

    monkeypatch.setattr(configured_price, "MySQLProductPriceRepository", forbidden)
    invalid = settings(tmp_path).model_copy(update={"price_data_model": "auto"})
    with pytest.raises(ValidationError):
        create_app(settings=invalid)
