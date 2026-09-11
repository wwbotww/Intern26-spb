import asyncio
import io
import json
from pathlib import Path
from typing import get_args

import httpx
import pytest

from spb_assistant_api.understanding_export import (
    ExportRequest,
    MissingSlot,
    export_understanding,
)
from spb_eval.understanding_dataset import (
    build_requests,
    load_observations,
    load_understanding_cases,
)
from spb_eval.understanding_metrics import calculate_understanding_metrics
from spb_eval.understanding_schema import MissingSlot as EvalMissingSlot


ROOT = Path(__file__).resolve().parents[3]
DATASET = ROOT / "eval/datasets/query-understanding-product-price-development.jsonl"


def test_price_component_export_is_independently_scorable_and_never_uses_credentials(
    tmp_path, monkeypatch
):
    def forbidden_settings():
        raise AssertionError(
            "offline price understanding must not load settings or credentials"
        )

    monkeypatch.setattr(
        "spb_assistant_api.understanding_export.AssistantSettings", forbidden_settings
    )
    dataset_hash, cases = load_understanding_cases(DATASET, "development")
    request = ExportRequest.model_validate(build_requests(dataset_hash, cases))
    stream = io.StringIO()
    result = asyncio.run(
        export_understanding(request, stream, product_price_enabled=True)
    )
    assert result == {"cases": 24, "model_calls": 0, "errors": 0, "skipped": 0}
    path = tmp_path / "price-observations.jsonl"
    path.write_text(stream.getvalue(), encoding="utf-8")
    manifest, observations = load_observations(path, request.model_dump(mode="json"))
    assert manifest.rules_version == "agent-rules-product-price-v1"
    summary, rows = calculate_understanding_metrics(cases, observations)
    failures = [row for row in rows if row["reasons"]]
    assert failures == []
    assert "product_price" in summary["intent"]["labels"]
    assert "device_price" not in summary["intent"]["labels"]
    assert summary["intent"]["macro_f1"] == 1
    # This is a development component regression, not a holdout/generalization claim.
    assert all(
        case.provenance == "synthetic" and case.split == "development" for case in cases
    )
    for raw in ("苹果", "上海", "iphone 16", "current_price", "candidate_token"):
        assert raw not in stream.getvalue()


def test_price_slot_names_are_independently_mirrored_in_eval():
    assert set(get_args(MissingSlot)) == set(get_args(EvalMissingSlot))


def test_generated_model_schema_contains_discriminated_constraints_without_identifiers():
    from spb_assistant_api.domain.understanding import StructuredModelUnderstanding

    schema = StructuredModelUnderstanding.model_json_schema()
    price = schema["$defs"]["ProductPriceSlots"]
    assert price["properties"]["conditions"]["discriminator"]["propertyName"] == "kind"
    for condition in (
        "DevicePriceConditions",
        "FreshPriceConditions",
        "UnknownPriceConditions",
    ):
        assert schema["$defs"][condition]["additionalProperties"] is False
        assert not {
            "catalog_item_id",
            "item_variant_id",
            "candidate_token",
            "sql",
            "current_price",
        } & set(schema["$defs"][condition]["properties"])


@pytest.mark.parametrize("unified", [False, True])
def test_provider_profile_and_export_failure_accounting_are_consistent(
    tmp_path, unified
):
    from spb_assistant_api.settings import AssistantSettings

    observed = []
    wrong_intent = "device_price" if unified else "product_price"

    def completion(request):
        body = json.loads(request.content)
        schema = json.loads(body["messages"][0]["content"].split("JSON Schema:\n")[1])
        observed.append(schema["$defs"]["Intent"]["enum"])
        payload = {
            "selected_intent": wrong_intent,
            "candidates": [{"intent": wrong_intent, "score": 0.9}],
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(payload),
                        },
                    }
                ]
            },
        )

    request = ExportRequest.model_validate(
        {
            "schema_version": "qu-requests-v1",
            "dataset_sha256": "a" * 64,
            "split": "development",
            "cases": [{"id": "profile-check", "input": {"message": "你好"}}],
        }
    )
    output = io.StringIO()
    settings = AssistantSettings(
        _env_file=None,
        query_model_enabled=True,
        query_model_api_key="synthetic-key",
        query_model_base_url="https://model.example.test",
    )
    summary = asyncio.run(
        export_understanding(
            request,
            output,
            mode="hybrid",
            product_price_enabled=unified,
            allow_live_model=True,
            max_model_calls=1,
            settings=settings,
            transport=httpx.MockTransport(completion),
        )
    )
    path = tmp_path / "profile-observations.jsonl"
    path.write_text(output.getvalue(), encoding="utf-8")
    _, observations = load_observations(path, request.model_dump(mode="json"))
    assert summary["model_calls"] == 1
    assert wrong_intent not in observed[0]
    assert observations["profile-check"].model_call.outcome == "failure"
    assert observations["profile-check"].prediction.intent == "unknown"
