"""Public black-box catalog development evaluation, not a holdout."""

import asyncio
from pathlib import Path
import httpx

from spb_eval.client import AgentApiClient
from spb_eval.dataset import dataset_sha256, load_agent_dataset
from spb_eval.metrics import agent_case_checks, agent_turn_checks
from spb_eval.runner import run_agent_evaluation
from spb_eval.schemas import AgentRunConfig

from .test_product_price_public_workflow import (
    PublicRepository,
    public_app,
    public_fresh_record,
    KEY,
)
from .product_price_device_fixture import baseline_v2_record

DATASET = (
    Path(__file__).resolve().parents[3]
    / "eval/datasets/agent-product-price-workflow-development-v1.jsonl"
)


async def evaluate(directory):
    repository = PublicRepository(
        baseline_v2_record("apple_pro_256"),
        baseline_v2_record("apple_pro_512"),
        public_fresh_record(),
    )
    app = public_app(directory, repository)
    async with (
        app.router.lifespan_context(app),
        AgentApiClient(
            base_url="http://test",
            api_key=KEY,
            timeout_seconds=5,
            transport=httpx.ASGITransport(app=app),
        ) as client,
    ):
        report = await run_agent_evaluation(
            client=client,
            cases=load_agent_dataset(DATASET),
            config=AgentRunConfig(
                label="catalog-public-synthetic-development",
                base_url="http://test",
                dataset=DATASET.name,
                dataset_sha256=dataset_sha256(DATASET),
                concurrency=1,
                timeout_seconds=5,
            ),
        )
    report.service["evaluation_fixture"] = {
        "kind": "synthetic_catalog_public_v2",
        "live_requests": 0,
        "model_requests": 0,
        "repository_reads": len(repository.queries),
        "supplier_confirmed": False,
    }
    return report


def test_public_catalog_development_dataset(tmp_path):
    report = asyncio.run(evaluate(tmp_path))
    failures = {
        case.case.id: [agent_turn_checks(turn) for turn in case.turns]
        for case in report.results
        if not agent_case_checks(case)["passed"]
    }
    assert not failures, failures
    assert len(report.results) == 7
    assert report.summary["quality_gate"]["passed"]
    successful = next(
        turn
        for case in report.results
        for turn in case.turns
        if turn.expected.expected_result_status == "success"
    )
    wrong = successful.model_copy(
        update={
            "expected": successful.expected.model_copy(
                update={
                    "expected_result_values": {"items.0.quote.quoted_unit": "INVENTED"}
                }
            )
        }
    )
    assert not agent_turn_checks(wrong)["passed"]
