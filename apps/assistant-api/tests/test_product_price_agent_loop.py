"""D3 isolated internal runtime: no HTTP activation, dotenv, paid model or business DB."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from spb_assistant_api.adapters.checkpointer_factory import (
    create_in_memory_checkpointer,
)
from spb_assistant_api.adapters.in_memory_receipts import (
    InMemoryToolExecutionRepository,
)
from spb_assistant_api.domain.agent_actions import AgentResumeInput, InvokeToolAction
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.exceptions import ProductPriceTimeoutError
from spb_assistant_api.domain.product_price import ProductPriceReadRecord
from spb_assistant_api.domain.product_price_execution import (
    PriceSelectionInput,
    PriceSelectionReference,
)
from spb_assistant_api.domain.product_price_slots import ProductPriceCommand
from spb_assistant_api.domain.product_price_query import (
    PriceReadBatch,
    SelectedPriceReadQuery,
)
from spb_assistant_api.domain.tooling import argument_fingerprint
from spb_assistant_api.services.agent_tools import (
    AgentCommandDispatcher,
    AgentToolRegistry,
    ToolExecutor,
)
from spb_assistant_api.services.product_price_query import ProductPriceQueryService
from spb_assistant_api.services.result_validator import AgentResultValidator
from spb_assistant_api.tools.product_price import ProductPriceTool
from spb_assistant_api.workflow.composition import create_agent_runtime
from spb_assistant_api.workflow.price_candidates import resolve_selection

from .product_price_device_fixture import baseline_v2_record
from .test_product_price_query_service import _record
from .test_product_price_device_quote import amountless


NOW = datetime(2026, 9, 11, tzinfo=UTC)


class Facts:
    def __init__(self, *records, truncated=False, failures=()):
        self.records = list(records)
        self.truncated = truncated
        self.queries = []
        self.failures = set(failures)

    async def search(self, query):
        self.queries.append(query)
        if len(self.queries) in self.failures:
            raise ProductPriceTimeoutError("synthetic timeout")
        if isinstance(query, SelectedPriceReadQuery):
            records = [
                r
                for r in self.records
                if r.pointer.source_listing_id == query.source_listing_id
                and r.pointer.region == query.region
            ]
        else:
            records = [r for r in self.records if r.listing.identity.kind == query.kind]
            if query.kind == "fresh":
                records = [
                    r
                    for r in records
                    if (query.region is None or query.region == r.pointer.region)
                    and (
                        query.price_nature is None
                        or query.price_nature == r.observation.price_nature
                    )
                ]
        return PriceReadBatch(
            records=tuple(records),
            truncated=self.truncated and not isinstance(query, SelectedPriceReadQuery),
        )


def setup_runtime(
    *records, truncated=False, failures=(), checkpointer=None, receipts=None, clock=None
):
    repository = Facts(*records, truncated=truncated, failures=failures)
    service = ProductPriceQueryService(repository)
    receipts = receipts or InMemoryToolExecutionRepository()
    clock = clock or (lambda: NOW)
    runtime = create_agent_runtime(
        checkpointer=checkpointer or create_in_memory_checkpointer(),
        receipts=receipts,
        product_price_service=service,
        clock=clock,
        workflow_trace_sink=None,
    )
    return runtime, repository, receipts


async def start(
    runtime, message="iPhone 16 Pro多少钱", *, thread="price-loop", owner="visitor-a"
):
    return await runtime.start(
        thread_id=thread,
        message=message,
        owner_id=owner,
        session_expires_at=NOW + timedelta(minutes=30),
    )


async def state(runtime, thread="price-loop"):
    return dict((await runtime.graph.aget_state(runtime.config(thread))).values)


@pytest.mark.parametrize("selection_type", ["token", "ordinal"])
def test_discovery_then_selection_rereads_current_identity_and_uses_distinct_receipts(
    selection_type,
):
    async def run():
        runtime, repo, receipts = setup_runtime(
            baseline_v2_record("apple_pro_256"), baseline_v2_record("apple_pro_512")
        )
        waiting = await start(runtime)
        assert waiting["phase"] == "waiting_user"
        before = await state(runtime)
        candidate = before["price_candidates"]["choices"][0]
        # Refresh amount/observation only, while the user's selected identity stays fixed.
        target_id = candidate["reference"]["source_listing_id"]
        for index, record in enumerate(repo.records):
            if record.pointer.source_listing_id == target_id:
                raw = record.model_dump()
                raw["observation"]["current_price"] = "7000.00"
                raw["observation"]["observation_id"] += 9000
                raw["pointer"]["price_observation_id"] += 9000
                repo.records[index] = ProductPriceReadRecord.model_validate(raw)
        kwargs = (
            {"price_selection": PriceSelectionInput(candidate_token=candidate["token"])}
            if selection_type == "token"
            else {"message": "第一个"}
        )
        completed = await runtime.resume(
            thread_id="price-loop", owner_id="visitor-a", **kwargs
        )
        assert completed["phase"] == "completed"
        assert (
            completed["result"]["data"]["facts"][0]["record"]["observation"][
                "current_price"
            ]
            == "7000.00"
        )
        after = await state(runtime)
        assert after["query_id"] == before["query_id"]
        assert after["tool_call_count"] == 2 and after["price_clarification_count"] == 1
        assert len(repo.queries) == 2 and isinstance(
            repo.queries[-1], SelectedPriceReadQuery
        )
        calls = [item for item in after["tool_calls"] if item["status"] == "succeeded"]
        assert len(calls) == 2 and calls[0]["tool_call_id"] != calls[1]["tool_call_id"]
        from uuid import UUID

        results = [
            await receipts.find(
                conversation_id="price-loop", tool_call_id=UUID(item["tool_call_id"])
            )
            for item in calls
        ]
        assert [r.result.status.value for r in results] == ["need_more_info", "success"]

    asyncio.run(run())


@pytest.mark.parametrize("key", ["apple_pro_256", "amountless"])
def test_unambiguous_price_or_amountless_state_completes_in_one_call(key):
    async def run():
        record = amountless() if key == "amountless" else baseline_v2_record(key)
        runtime, repo, _ = setup_runtime(record)
        completed = await start(runtime)
        assert (
            completed["phase"] == "completed"
            and completed["result"]["status"] == "success"
        )
        actual = completed["result"]["data"]["facts"][0]["record"]["observation"]
        assert actual["current_price"] == (None if key == "amountless" else "7999.00")
        assert len(repo.queries) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "mode", ["missing", "identity_change", "wrong_token", "expired"]
)
def test_selection_fails_closed_without_substitution(mode):
    async def run():
        time = [NOW]
        runtime, repo, _ = setup_runtime(
            baseline_v2_record("apple_pro_256"),
            baseline_v2_record("apple_pro_512"),
            clock=lambda: time[0],
        )
        await start(runtime)
        before = await state(runtime)
        choice = before["price_candidates"]["choices"][0]
        if mode == "missing":
            repo.records = [
                r
                for r in repo.records
                if r.pointer.source_listing_id
                != choice["reference"]["source_listing_id"]
            ]
        if mode == "identity_change":
            for index, record in enumerate(repo.records):
                if (
                    record.pointer.source_listing_id
                    == choice["reference"]["source_listing_id"]
                ):
                    raw = record.model_dump()
                    raw["listing"]["identity"]["specification"]["color"] = "已变化颜色"
                    repo.records[index] = ProductPriceReadRecord.model_validate(raw)
        if mode == "expired":
            time[0] = NOW + timedelta(minutes=11)
        token = "x" * 43 if mode == "wrong_token" else choice["token"]
        completed = await runtime.resume(
            thread_id="price-loop",
            owner_id="visitor-a",
            price_selection=PriceSelectionInput(candidate_token=token),
        )
        assert completed["phase"] == "failed" and completed["result"] is None
        assert len(repo.queries) == (1 if mode in {"wrong_token", "expired"} else 2)

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("price_owner_id", "other"),
        ("query_id", str(uuid4())),
        ("conversation_id", "other"),
    ],
)
def test_candidate_scope_binding_rejects_cross_owner_query_and_conversation(
    field, value
):
    async def run():
        runtime, _, _ = setup_runtime(
            baseline_v2_record("apple_pro_256"), baseline_v2_record("apple_pro_512")
        )
        await start(runtime)
        current = await state(runtime)
        token = current["price_candidates"]["choices"][0]["token"]
        with pytest.raises(AgentOperationError):
            resolve_selection({**current, field: value}, token, NOW)
        current["slots"]["conditions"]["specification"]["capacity"] = "512GB"
        with pytest.raises(AgentOperationError):
            resolve_selection(current, token, NOW)

    asyncio.run(run())


def test_resume_preserves_budgets_even_when_sent_as_ordinary_start_message():
    async def run():
        runtime, repo, _ = setup_runtime(
            baseline_v2_record("apple_pro_256"), baseline_v2_record("apple_pro_512")
        )
        await start(runtime)
        query_id = (await state(runtime))["query_id"]
        for _ in range(2):
            waiting = await start(runtime, "iPhone 16 Pro多少钱")
            assert waiting["phase"] == "waiting_user"
        stopped = await start(runtime, "iPhone 16 Pro多少钱")
        current = await state(runtime)
        assert stopped["phase"] == "failed"
        assert (
            current["query_id"] == query_id
            and current["price_clarification_count"] == 3
        )
        assert len(repo.queries) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "failures,expected_status,attempts,retries",
    [((1,), "completed", 3, 1), ((2,), "completed", 3, 1), ((1, 3), "failed", 3, 1)],
)
def test_query_wide_retry_budget_is_not_reset_by_selection(
    failures, expected_status, attempts, retries
):
    async def run():
        runtime, repo, _ = setup_runtime(
            baseline_v2_record("apple_pro_256"),
            baseline_v2_record("apple_pro_512"),
            failures=failures,
        )
        await start(runtime)
        token = (await state(runtime))["price_candidates"]["choices"][0]["token"]
        result = await runtime.resume(
            thread_id="price-loop",
            owner_id="visitor-a",
            price_selection=PriceSelectionInput(candidate_token=token),
        )
        current = await state(runtime)
        assert result["phase"] == expected_status
        assert (
            len(repo.queries) == attempts
            and current["retry_count"] == retries
            and current["tool_call_count"] == 2
        )

    asyncio.run(run())


def test_internal_checkpoint_resume_reuses_the_original_candidates_after_runtime_rebuild():
    async def run():
        checkpointer, receipts = (
            create_in_memory_checkpointer(),
            InMemoryToolExecutionRepository(),
        )
        records = (
            baseline_v2_record("apple_pro_256"),
            baseline_v2_record("apple_pro_512"),
        )
        runtime, repo, _ = setup_runtime(
            *records, checkpointer=checkpointer, receipts=receipts
        )
        await start(runtime)
        before = await state(runtime)
        rebuilt, second_repo, _ = setup_runtime(
            *records, checkpointer=checkpointer, receipts=receipts
        )
        result = await rebuilt.resume(
            thread_id="price-loop", owner_id="visitor-a", message="第二个"
        )
        assert result["phase"] == "completed"
        after = await state(rebuilt)
        assert after["query_id"] == before["query_id"] and after["tool_call_count"] == 2
        assert len(repo.queries) == 1 and len(second_repo.queries) == 1

    asyncio.run(run())


def test_wrong_owner_does_not_mutate_checkpoint_or_execute():
    async def run():
        runtime, repo, _ = setup_runtime(
            baseline_v2_record("apple_pro_256"), baseline_v2_record("apple_pro_512")
        )
        await start(runtime)
        before = await state(runtime)
        with pytest.raises(ValueError):
            await runtime.resume(
                thread_id="price-loop", owner_id="visitor-b", message="第二个"
            )
        assert await state(runtime) == before and len(repo.queries) == 1

    asyncio.run(run())


def test_sqlite_checkpoint_and_receipts_survive_close_and_reopen(tmp_path):
    from spb_assistant_api.adapters.checkpointer_factory import (
        create_sqlite_checkpointer,
    )
    from spb_assistant_api.adapters.sqlite_persistence import (
        create_sqlite_agent_repositories,
    )

    async def run():
        database = tmp_path / "price-d3-isolated.sqlite3"
        records = (
            baseline_v2_record("apple_pro_256"),
            baseline_v2_record("apple_pro_512"),
        )
        async with (
            create_sqlite_checkpointer(database) as checkpointer,
            create_sqlite_agent_repositories(database) as repositories,
        ):
            runtime, _, _ = setup_runtime(
                *records, checkpointer=checkpointer, receipts=repositories.tool_receipts
            )
            await start(runtime)
            before = await state(runtime)
        async with (
            create_sqlite_checkpointer(database) as checkpointer,
            create_sqlite_agent_repositories(database) as repositories,
        ):
            runtime, repo, _ = setup_runtime(
                *records, checkpointer=checkpointer, receipts=repositories.tool_receipts
            )
            after_restart = await state(runtime)
            assert after_restart["price_candidates"] == before["price_candidates"]
            completed = await runtime.resume(
                thread_id="price-loop", owner_id="visitor-a", message="第一个"
            )
            assert completed["phase"] == "completed" and len(repo.queries) == 1
            after = await state(runtime)
            assert (
                after["tool_call_count"] == 2
                and after["price_clarification_count"] == 1
            )

    asyncio.run(run())


def test_narrowing_conditions_clears_candidates_without_resetting_query_budget():
    async def run():
        runtime, repo, _ = setup_runtime(
            baseline_v2_record("apple_pro_256"), baseline_v2_record("apple_pro_512")
        )
        await start(runtime)
        before = await state(runtime)
        completed = await runtime.resume(
            thread_id="price-loop", owner_id="visitor-a", message="256GB"
        )
        assert completed["phase"] == "completed"
        after = await state(runtime)
        assert (
            after["price_candidates"] is None
            and after["query_id"] == before["query_id"]
        )
        assert after["tool_call_count"] == 2 and len(repo.queries) == 2
        assert (
            completed["result"]["data"]["facts"][0]["record"]["listing"]["identity"][
                "specification"
            ]["capacity"]
            == "256GB"
        )

    asyncio.run(run())


def test_second_discovery_cannot_request_a_third_tool_call():
    async def run():
        runtime, repo, _ = setup_runtime(
            baseline_v2_record("apple_pro_256"),
            baseline_v2_record("apple_pro_512"),
            truncated=True,
        )
        await start(runtime)
        result = await runtime.resume(
            thread_id="price-loop", owner_id="visitor-a", message="256GB"
        )
        assert (
            result["phase"] == "failed"
            and result["failure"]["code"] == "price_query_budget_exceeded"
        )
        assert len(repo.queries) == 2

    asyncio.run(run())


def test_out_of_range_ordinal_is_stable_failure_without_tool_call():
    async def run():
        runtime, repo, _ = setup_runtime(
            baseline_v2_record("apple_pro_256"), baseline_v2_record("apple_pro_512")
        )
        await start(runtime)
        result = await runtime.resume(
            thread_id="price-loop", owner_id="visitor-a", message="第九个"
        )
        assert (
            result["phase"] == "failed"
            and result["failure"]["code"] == "price_selection_invalid"
        )
        assert len(repo.queries) == 1

    asyncio.run(run())


def test_http_uses_allowlist_while_raw_internal_stream_remains_closed():
    from spb_assistant_api.api.agent_contracts import AgentApiDependencies
    from spb_assistant_api.api.agent_schemas import AgentResultResponse

    async def run():
        runtime, repo, _ = setup_runtime(baseline_v2_record("apple_pro_256"))
        assert AgentApiDependencies(
            service=object(), capabilities=runtime.capability_descriptors
        ).product_price_enabled
        with pytest.raises(ValueError):
            async for _ in runtime.stream_events(
                thread_id="price-loop", message="iPhone 16 Pro多少钱"
            ):
                pass
        assert not repo.queries
        result = await ProductPriceTool(ProductPriceQueryService(repo)).execute(
            ProductPriceCommand(
                conditions={"kind": "device", "product_text": "iPhone 16 Pro"}
            )
        )
        public = AgentResultResponse.from_domain(result)
        assert public.data["type"] == "product_price"
        assert "command_fingerprint" not in public.data

    asyncio.run(run())


def test_candidate_expiry_is_clamped_to_session_and_expired_resume_does_not_read():
    async def run():
        time = [NOW]
        runtime, repo, _ = setup_runtime(
            baseline_v2_record("apple_pro_256"),
            baseline_v2_record("apple_pro_512"),
            clock=lambda: time[0],
        )
        await runtime.start(
            thread_id="price-loop",
            message="iPhone 16 Pro多少钱",
            owner_id="visitor-a",
            session_expires_at=NOW + timedelta(minutes=2),
        )
        candidates = (await state(runtime))["price_candidates"]
        assert datetime.fromisoformat(candidates["expires_at"]) == NOW + timedelta(
            minutes=2
        )
        time[0] += timedelta(minutes=3)
        with pytest.raises(AgentOperationError) as error:
            await runtime.resume(
                thread_id="price-loop", owner_id="visitor-a", message="256GB"
            )
        assert (
            error.value.failure.code == "price_session_expired"
            and len(repo.queries) == 1
        )

    asyncio.run(run())


def test_receipt_replay_preserves_facts_and_new_query_rereads():
    async def run():
        record = baseline_v2_record("apple_pro_256")
        repo = Facts(record)
        tool = ProductPriceTool(ProductPriceQueryService(repo))
        executor = ToolExecutor(
            AgentCommandDispatcher(AgentToolRegistry([tool])),
            InMemoryToolExecutionRepository(),
            clock=lambda: NOW,
        )
        command = ProductPriceCommand(
            conditions={"kind": "device", "product_text": "iPhone 16 Pro"}
        )
        action = InvokeToolAction(
            tool_name="product_price",
            command=command,
            argument_fingerprint=argument_fingerprint(command),
            tool_call_id=uuid4(),
            attempt=1,
            deadline_at=NOW + timedelta(seconds=30),
        )
        first = await executor.execute(conversation_id="receipt", action=action)
        repo.records = []
        replay = await executor.execute(conversation_id="receipt", action=action)
        assert (
            replay.reused and replay.result == first.result and len(repo.queries) == 1
        )
        fresh = await executor.execute(
            conversation_id="receipt",
            action=action.model_copy(update={"tool_call_id": uuid4()}),
        )
        assert fresh.result.status.value == "no_match" and len(repo.queries) == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "payload",
    [
        {"candidate_token": "42"},
        {"source_listing_id": 1},
        {"candidate_token": "x" * 43, "sql": "SELECT 1"},
    ],
)
def test_selection_input_is_not_an_arbitrary_id_or_command_dictionary(payload):
    with pytest.raises(ValidationError):
        PriceSelectionInput.model_validate(payload)
    with pytest.raises(ValidationError):
        AgentResumeInput(
            message="改成其他商品", price_selection={"candidate_token": "x" * 43}
        )


def test_selected_reference_cannot_bypass_condition_checks():
    async def run():
        record = baseline_v2_record("apple_pro_max")
        command = ProductPriceCommand(
            conditions={"kind": "device", "product_text": "iPhone 16 Pro"},
            selection=PriceSelectionReference.from_record(record),
        )
        with pytest.raises(AgentOperationError) as error:
            await ProductPriceTool(ProductPriceQueryService(Facts(record))).execute(
                command
            )
        assert error.value.failure.category.value == "contract_violation"

    asyncio.run(run())


def test_old_today_observation_is_partial_and_validator_rejects_false_today_success():
    async def run():
        record = ProductPriceReadRecord.model_validate(
            _record("fresh_retail_500g").model_dump() | {"read_at": NOW}
        )
        command = ProductPriceCommand(
            conditions={
                "kind": "fresh",
                "commodity": record.listing.identity.commodity_name,
                "region_text": "上海",
                "price_nature": "RETAIL_AVERAGE",
            },
            time={"kind": "today", "raw_text": "今天"},
        )
        result = await ProductPriceTool(
            ProductPriceQueryService(Facts(record))
        ).execute(command)
        assert (
            result.status.value == "partial"
            and result.reason_code == "price_today_not_available"
        )
        AgentResultValidator().validate(command=command, result=result)
        assert (
            result.data.facts[0].record.observation.current_price
            == record.observation.current_price
        )
        from spb_assistant_api.domain.results import AgentResultStatus

        with pytest.raises(AgentOperationError):
            AgentResultValidator().validate(
                command=command,
                result=result.model_copy(update={"status": AgentResultStatus.SUCCESS}),
            )

    asyncio.run(run())
