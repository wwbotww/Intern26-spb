"""Exercise only the public V2 boundary; the Eval package stays independent."""

import argparse
import asyncio
from pathlib import Path

import httpx

from spb_eval.client import AgentApiClient
from spb_eval.dataset import dataset_sha256, load_agent_dataset
from spb_eval.metrics import agent_case_checks, agent_turn_checks
from spb_eval.reporting import write_agent_report
from spb_eval.runner import run_agent_evaluation
from spb_eval.schemas import AgentRunConfig

from .postage_p3_fixture import API_KEY, app_at

DATASET = Path(__file__).resolve().parents[3] / "eval/datasets/agent-postage-workflow-development-v1.jsonl"


async def evaluate(database):
    transports = []
    app = app_at(database, transports)
    async with app.router.lifespan_context(app), AgentApiClient(
        base_url="http://test", api_key=API_KEY, timeout_seconds=5,
        transport=httpx.ASGITransport(app=app),
    ) as client:
        report = await run_agent_evaluation(
            client=client, cases=load_agent_dataset(DATASET),
            config=AgentRunConfig(
                label="postage-p3-synthetic-development", base_url="http://test",
                dataset=DATASET.name, dataset_sha256=dataset_sha256(DATASET),
                concurrency=1, timeout_seconds=5,
            ),
        )
    report.service["evaluation_fixture"] = {
        "kind": "synthetic_postage_public_v2", "live_requests": 0, "model_requests": 0,
        "mock_requests": sum(len(transport.calls) for transport in transports),
        "supplier_confirmed": False,
    }
    assert all(transport.closed for transport in transports)
    return report


def test_postage_development_cases_through_v2(tmp_path):
    report = asyncio.run(evaluate(tmp_path / "agent.db"))
    assert len(report.results) == 13
    failures = {
        case.case.id: [agent_turn_checks(turn) for turn in case.turns]
        for case in report.results if not agent_case_checks(case)["passed"]
    }
    assert not failures, failures
    assert report.service["evaluation_fixture"]["mock_requests"] == 8
    # The new basis check must actually fail for wrong Gold, not just be stored.
    successful = next(turn for case in report.results for turn in case.turns if turn.expected.expected_result_status == "success")
    tampered = successful.model_copy(update={"expected": successful.expected.model_copy(update={"expected_quote_basis_values": {"source.source_type": "external_api"}})})
    assert not agent_turn_checks(tampered)["quote_basis_values"]
    assert not agent_turn_checks(tampered)["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.database))
    paths = write_agent_report(report, args.output)
    print({"cases": report.summary["cases"], "turns": report.summary["turns"], "fixture": report.service["evaluation_fixture"], "reports": str(paths)})
