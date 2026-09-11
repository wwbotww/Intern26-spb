"""CI-only public V2 + Mock supplier evaluation. Never reads runtime settings."""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from spb_eval.reporting import write_agent_report

from .test_phase3bp_postage_eval import evaluate
from .test_product_price_public_eval import evaluate as evaluate_price


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="spb-ci-eval-") as temporary:
        report = asyncio.run(evaluate(Path(temporary) / "agent.db"))
        passed = report.summary["quality_gate"]["passed"] is True
        write_agent_report(report, args.output)
        price_report = asyncio.run(evaluate_price(Path(temporary)))
        write_agent_report(price_report, args.output / "catalog-price")
        passed = passed and price_report.summary["quality_gate"]["passed"] is True
        print(json.dumps({
            "quality_gate_passed": passed, "cases": len(report.results),
            "turns": sum(len(case.turns) for case in report.results),
            "fixture": report.service["evaluation_fixture"],
            "catalog_cases": len(price_report.results),
            "catalog_quality_gate_passed": price_report.summary["quality_gate"]["passed"],
        }))
        return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
