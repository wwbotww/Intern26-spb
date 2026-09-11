"""D4–D7 public contract gates with synthetic facts and real SQLite/LangGraph."""

import asyncio
import copy
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from spb_assistant_api.api.agent_schemas import AgentMessageRequest, AgentResultResponse
from spb_assistant_api.api.app import create_app
from spb_assistant_api.api.product_price_schemas import ProductPriceResponseData
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.product_price_slots import ProductPriceCommand
from spb_assistant_api.domain.product_price_query import SelectedPriceReadQuery
from spb_assistant_api.services.product_price_query import ProductPriceQueryService
from spb_assistant_api.tools.product_price import ProductPriceTool
from spb_assistant_api.workflow.migrations import AgentStateMigrator
from spb_eval.product_price_contract import ProductPriceResponseData as EvalPriceData
from spb_eval.schemas import AgentPublicResponse

from .product_price_device_fixture import baseline_v2_record
from .test_product_price_agent_loop import Facts
from .test_product_price_composition import settings, AUTH
from .test_product_price_query_service import _record

ORIGIN = "http://127.0.0.1:13006"
KEY = AUTH["Authorization"].split()[1]


class PublicRepository(Facts):
    async def initialize(self):
        pass

    async def close(self):
        pass

    def readiness(self):
        return "ready"


def public_fresh_record(name="fresh_retail_500g"):
    from spb_assistant_api.domain.product_price import ProductPriceReadRecord

    raw = _record(name).model_dump(mode="json")
    raw["read_at"] = "2026-09-11T00:00:00Z"
    raw["listing"]["identity"]["commodity_name"] = (
        "黄瓜" if name == "fresh_retail_500g" else "鸡蛋"
    )
    return ProductPriceReadRecord.model_validate(raw)


def public_app(path, repository, *, browser=False):
    options = dict(agent_browser_session_enabled=browser)
    if browser:
        options.update(
            agent_browser_proxy_api_key=KEY,
            agent_browser_signing_key="synthetic-catalog-browser-signing-key-only",
            agent_browser_public_origin=ORIGIN,
            agent_browser_cookie_secure=False,
        )
    return create_app(
        settings=settings(path, **options), product_price_repository=repository
    )


async def send(
    client, *, message=None, conversation=None, key=None, selection=None, stream=False
):
    body = {"conversation_id": conversation, "stream": stream}
    if message is not None:
        body["message"] = message
    if selection is not None:
        body["price_selection"] = {"candidate_token": selection}
    response = await client.post(
        "/v2/agent/messages",
        json=body,
        headers={"Idempotency-Key": key or str(uuid4())},
    )
    if response.status_code == 200 and not stream:
        AgentPublicResponse.model_validate(response.json())
    return response


async def browser(client):
    response = await client.post("/v2/agent/browser-session", json={})
    assert response.status_code == 200, response.text
    client.headers["X-Agent-Session"] = response.json()["session_ref"]


def test_owned_candidates_snapshot_restart_json_sse_replay_and_new_query(tmp_path):
    async def run():
        repository = PublicRepository(
            baseline_v2_record("apple_pro_256"), baseline_v2_record("apple_pro_512")
        )
        app = public_app(tmp_path, repository, browser=True)
        async with app.router.lifespan_context(app):
            async with (
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app),
                    base_url=ORIGIN,
                    headers={**AUTH, "Origin": ORIGIN},
                ) as a,
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app),
                    base_url=ORIGIN,
                    headers={**AUTH, "Origin": ORIGIN},
                ) as b,
            ):
                await browser(a)
                await browser(b)
                # Old browsers are rejected before stream headers, SQL or checkpoint creation.
                denied = await send(a, message="iPhone 16 Pro多少钱", stream=True)
                assert (
                    denied.status_code == 409
                    and "text/event-stream" not in denied.headers["content-type"]
                )
                assert not repository.queries
                for client in (a, b):
                    client.headers["X-Agent-Contract"] = "product-price-v1"
                catalog = (await a.get("/v2/agent/capabilities")).json()
                assert {row["intent"] for row in catalog} == {
                    "policy",
                    "product_price",
                    "tracking",
                    "delivery_time",
                    "postage",
                }
                initial = await send(a, message="iPhone 16 Pro多少钱", key="discover")
                assert initial.status_code == 200, initial.text
                first = initial.json()
                assert first["phase"] == "waiting_user"
                choices = first["required_inputs"][0]["price_candidates"]
                assert len(choices) == 2
                assert set(choices[0]) == {"candidate_token", "label", "expires_at"}
                conversation = first["conversation_id"]
                snapshot = await a.get(f"/v2/agent/conversations/{conversation}")
                assert snapshot.json()["required_inputs"] == first["required_inputs"]
                assert len(repository.queries) == 1
                assert (
                    await b.get(f"/v2/agent/conversations/{conversation}")
                ).status_code == 404
                assert (
                    await send(
                        b,
                        conversation=conversation,
                        selection=choices[0]["candidate_token"],
                    )
                ).status_code == 404
                assert (
                    await b.delete(f"/v2/agent/conversations/{conversation}")
                ).status_code == 404
                # HTTP selection payload owns its fingerprint; replay uses the saved facts.
                selected = await send(
                    a,
                    conversation=conversation,
                    selection=choices[0]["candidate_token"],
                    key="select",
                )
                assert selected.status_code == 200, selected.text
                assert selected.json()["result"]["status"] == "success"
                assert len(repository.queries) == 2 and isinstance(
                    repository.queries[-1], SelectedPriceReadQuery
                )
                serialized = json.dumps(selected.json())
                for private in (
                    "source_listing_id",
                    "observation_id",
                    "revision_id",
                    "identity_fingerprint",
                    "command_fingerprint",
                    "selection_reference",
                ):
                    assert private not in serialized
                replay = await send(
                    a,
                    conversation=conversation,
                    selection=choices[0]["candidate_token"],
                    key="select",
                    stream=True,
                )
                events = [
                    block
                    for block in replay.text.split("\n\n")
                    if block.startswith("event: done")
                ]
                response = json.loads(events[0].split("data: ", 1)[1])["response"]
                assert response["result"] == selected.json()["result"]
                assert len(repository.queries) == 2
                conflict = await send(
                    a,
                    conversation=conversation,
                    selection=choices[1]["candidate_token"],
                    key="select",
                )
                assert conflict.status_code == 409
                assert len(repository.queries) == 2
                old_cookie = a.cookies
            # Shutdown/reopen the actual SQLite state; read-only restore does not execute.
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app),
                base_url=ORIGIN,
                headers={
                    **AUTH,
                    "Origin": ORIGIN,
                    "X-Agent-Contract": "product-price-v1",
                },
                cookies=old_cookie,
            ) as a:
                await browser(a)
                restored = await a.get(f"/v2/agent/conversations/{conversation}")
                assert restored.json()["result"] == selected.json()["result"]
                assert len(repository.queries) == 2
                again = await send(
                    a, conversation=conversation, message="iPhone 16 Pro多少钱"
                )
                assert again.json()["phase"] == "waiting_user"
                assert len(repository.queries) == 3
                assert (
                    again.json()["required_inputs"][0]["price_candidates"][0][
                        "candidate_token"
                    ]
                    != choices[0]["candidate_token"]
                )

    asyncio.run(run())


def public_fact(name="fresh_retail_500g"):
    record = _record(name)
    command = ProductPriceCommand(
        conditions={
            "kind": "fresh",
            "commodity": record.listing.identity.commodity_name,
            "region_text": "上海",
            "price_nature": "RETAIL_AVERAGE",
        }
    )
    result = asyncio.run(
        ProductPriceTool(ProductPriceQueryService(Facts(record))).execute(command)
    )
    return AgentResultResponse.from_domain(result).data


def test_public_and_eval_price_whitelists_match_and_preserve_unit_and_times():
    data = public_fact()
    assert ProductPriceResponseData.model_validate(data).model_dump(
        mode="json"
    ) == EvalPriceData.model_validate(data).model_dump(mode="json")
    item = data["items"][0]
    assert item["quote"]["quoted_unit"] == "CNY_PER_500G"
    assert item["quote"]["unit_price"]["unit"] == "CNY_PER_KG"
    assert item["freshness"] == "unknown"


@pytest.mark.parametrize(
    "path,value",
    [
        ("items.0.source_listing_id", 1),
        ("schema_version", "2"),
        ("items.0.quote.current_price", 3.50),
        ("items.0.quote.current_price", "0.00"),
        ("items.0.quote.kind", "availability_only"),
        ("items.0.quote.unit_price.amount", "999.00"),
        ("items.0.quote.observed_at", "2026-08-01T00:00:00"),
        ("items.0.quote.observed_at", "2099-08-01T00:00:00Z"),
        ("items.0.quote.quoted_unit", "CNY_PER_PIECE"),
        ("items.0.quote.price_nature", "RETAIL_OFFER"),
        ("items.0.source.url", "https://user:password@example.test"),
        ("items.0.quote.original_price", "10.00"),
    ],
)
def test_public_and_eval_reject_malformed_or_private_price_data(path, value):
    data = public_fact()
    target = data
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    target[parts[-1]] = value
    for model in (ProductPriceResponseData, EvalPriceData):
        with pytest.raises(ValidationError):
            model.model_validate(data)


@pytest.mark.parametrize(
    "extra",
    [
        {"message": "换成苹果"},
        {"explicit_intent": "product_price"},
        {"confirm_overwrite": True},
        {"conversation_id": None},
    ],
)
def test_selection_cannot_mix_conditions_or_create_a_conversation(extra):
    with pytest.raises(ValidationError):
        AgentMessageRequest.model_validate(
            {
                "conversation_id": str(uuid4()),
                "price_selection": {"candidate_token": "a" * 43},
                **extra,
            }
        )


@pytest.mark.parametrize("phase", ["waiting_user", "ready", "executing", "recovering"])
def test_old_price_pending_fails_closed_without_mutation(phase):
    raw = {
        "schema_version": "3",
        "query_id": str(uuid4()),
        "active_intent": "device_price",
        "phase": phase,
    }
    saved = copy.deepcopy(raw)
    with pytest.raises(AgentOperationError) as error:
        AgentStateMigrator().migrate(raw)
    assert error.value.failure.code == "price_query_restart_required"
    assert raw == saved


def test_completed_legacy_price_retains_exact_result_and_query_identity():
    raw = {
        "schema_version": "3",
        "query_id": str(uuid4()),
        "active_intent": "device_price",
        "phase": "completed",
        "result": {
            "type": "device_price",
            "price": "100.00",
            "observed_at": "original",
        },
    }
    migrated = AgentStateMigrator().migrate(raw)
    assert migrated.state == {**raw, "schema_version": "4"}
