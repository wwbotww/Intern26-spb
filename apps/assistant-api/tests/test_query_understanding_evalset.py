from __future__ import annotations

import asyncio
import json
from pathlib import Path

from spb_assistant_api.domain.intents import Intent
from spb_assistant_api.services.query_understanding import (
    RuleBasedQueryUnderstander,
)


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
DATASET = (
    WORKSPACE_ROOT
    / "eval"
    / "datasets"
    / "agent-understanding-v1.jsonl"
)


def test_phase2_public_understanding_contract_dataset() -> None:
    cases = [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    understander = RuleBasedQueryUnderstander()

    async def evaluate() -> None:
        for case in cases:
            active_intent: Intent | None = None
            for turn in case["turns"]:
                explicit = turn.get("explicit_intent")
                result = await understander.understand(
                    message=turn["message"],
                    active_intent=active_intent,
                    explicit_intent=(Intent(explicit) if explicit else None),
                )
                assert result.selected_intent.value == turn[
                    "expected_intent"
                ], case["id"]
                assert result.missing_slots == turn.get(
                    "expected_missing_slots",
                    [],
                ), case["id"]
                assert result.multi_intent is turn.get(
                    "expected_multi_intent",
                    False,
                ), case["id"]
                assert result.control.value == turn.get(
                    "expected_control",
                    "none",
                ), case["id"]
                slot_values = (
                    result.slots.model_dump(mode="json")
                    if result.slots is not None
                    else {}
                )
                for path, expected in turn.get(
                    "expected_slot_values",
                    {},
                ).items():
                    actual = slot_values
                    for part in path.split("."):
                        actual = actual[part]
                    assert actual == expected, (case["id"], path)
                assert result.ambiguities == turn.get(
                    "expected_ambiguities",
                    [],
                ), case["id"]
                if result.selected_intent is not Intent.UNKNOWN:
                    active_intent = result.selected_intent

    asyncio.run(evaluate())
