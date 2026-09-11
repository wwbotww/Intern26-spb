"""Opt-in, serial component observation producer. No tools, Graph or Gold."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Annotated, Any, Literal, TextIO

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from . import __version__
from .adapters.deepseek_understanding import DeepSeekQueryUnderstandingModel
from .domain.agent_errors import AgentOperationError
from .domain.failures import AgentFailure, FailureCategory
from .domain.intents import Intent
from .domain.ports import StructuredQueryUnderstandingModel
from .domain.understanding import QueryUnderstandingResult
from .query_model import create_query_understander
from .services.query_understanding import (
    HybridQueryUnderstander,
    RuleBasedQueryUnderstander,
    StructuredLlmQueryUnderstander,
)
from .settings import AssistantSettings

Code = Annotated[
    str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,95}$")
]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MissingSlot = Literal[
    "mail_no", "origin", "destination", "weight", "conditions.kind",
    "conditions.product_text", "conditions.brand", "conditions.commodity", "conditions.variety",
    "conditions.region_text", "conditions.market_text", "conditions.price_nature", "conditions.source_scope",
    "conditions.requested_unit", "conditions.specification", "conditions.specification.capacity",
    "conditions.specification.memory", "conditions.specification.color", "time",
]


class ExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)
    active_intent: Intent | None = None
    explicit_intent: Intent | None = None
    expected_slots: list[MissingSlot] = Field(
        default_factory=list, max_length=16
    )

    @model_validator(mode="after")
    def validate_context(self):
        if not self.message.strip() or Intent.UNKNOWN in (
            self.active_intent,
            self.explicit_intent,
        ):
            raise ValueError("invalid_context")
        if len(set(self.expected_slots)) != len(self.expected_slots):
            raise ValueError("duplicate_expected_slots")
        return self


class ExportCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Code
    input: ExportInput


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["qu-requests-v1"]
    dataset_sha256: Digest
    split: Literal["development", "holdout"]
    cases: list[ExportCase] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate_case_id")
        return self


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _code(value: str) -> str:
    return (
        value
        if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,95}", value)
        else "query_model_unclassified"
    )


def _not_called() -> dict:
    return {
        "outcome": "not_called",
        "duration_ms": None,
        "failure_code": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }


class _BudgetedModel:
    def __init__(self, model: StructuredQueryUnderstandingModel, limit: int):
        self.model, self.limit, self.calls = model, limit, 0
        self.current = _not_called()

    async def classify(
        self, *, message: str, prompt: str, prompt_version: str
    ):
        if self.calls >= self.limit:
            self.current.update(
                outcome="budget_exhausted",
                failure_code="query_model_budget_exhausted",
            )
            raise AgentOperationError(
                AgentFailure(
                    category=FailureCategory.LOOP_BUDGET_EXCEEDED,
                    code="query_model_budget_exhausted",
                    message="本地评测已达到显式模型调用预算",
                )
            )
        self.calls += 1
        started = perf_counter()
        self.current.update(
            outcome="failure", failure_code="query_model_unclassified"
        )
        try:
            result = await self.model.classify(
                message=message, prompt=prompt, prompt_version=prompt_version
            )
            self.current.update(outcome="success", failure_code=None)
            return result
        except AgentOperationError as exc:
            self.current["failure_code"] = _code(exc.failure.code)
            raise
        finally:
            self.current["duration_ms"] = round(
                (perf_counter() - started) * 1000, 3
            )

    def observe(self, measurement: Mapping[str, Any]) -> None:
        usage = {
            name: measurement.get(name)
            for name in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        usage = {
            name: value
            if type(value) is int and 0 <= value <= 1_000_000
            else None
            for name, value in usage.items()
        }
        if (
            all(value is not None for value in usage.values())
            and usage["total_tokens"]
            != usage["prompt_tokens"] + usage["completion_tokens"]
        ):
            return
        self.current.update(usage)


def _project(result: QueryUnderstandingResult, *, needs_model: bool) -> dict:
    # Only allowlisted semantic values are fingerprinted. No raw question,
    # source IDs, token, connection information or free-form provenance.
    payload = result.slots.model_dump(mode="json") if result.slots else {}
    values = {}
    if payload.get("mail_no"):
        values["mail_no"] = payload["mail_no"].strip().upper()
    for name in ("origin", "destination"):
        region = payload.get(name)
        if region and region["resolution"] == "resolved":
            values[name] = region["canonical_name"].strip()
    weight = payload.get("weight")
    if weight and weight["value"] is not None:
        number = Decimal(str(weight["value"])) * (
            Decimal("0.001") if weight["unit"] == "g" else 1
        )
        if (
            not number.is_finite()
            or number <= 0
            or abs(number.adjusted()) > 32
        ):
            raise ValueError("invalid_weight_projection")
        values["weight_kg"] = format(number.normalize(), "f")
    if payload.get("intent") == "product_price":
        conditions = payload["conditions"]
        values["price_category"] = conditions["kind"]
        for source, target in {
            "product_text": "product_text", "brand": "brand", "commodity": "commodity",
            "variety": "variety", "region_text": "price_region", "market_text": "market",
            "price_nature": "price_nature", "source_scope": "source_scope",
        }.items():
            if conditions.get(source):
                values[target] = conditions[source]
        for name in ("capacity", "memory", "color"):
            if value := conditions.get("specification", {}).get(name):
                values[name] = value
        if unit := conditions.get("requested_unit"):
            values["price_unit"] = unit["unit"]
            values["price_quantity"] = format(Decimal(str(unit["quantity"])).normalize(), "f")
        if constraint := payload.get("time"):
            values["price_time"] = constraint["kind"]
    return {
        "intent": result.selected_intent.value,
        "candidate_intents": [item.intent.value for item in result.candidates],
        "multi_intent": result.multi_intent,
        "control": result.control.value,
        "slot_fingerprints": {
            name: _digest({"slot": name, "value": value})
            for name, value in values.items()
        },
        "missing_slots": result.missing_slots,
        "source": result.source,
        "parser_version": result.parser_version,
        "prompt_version": result.prompt_version,
        "needs_model_fallback": needs_model,
    }


def _implementation_digest() -> str:
    root = Path(__file__).parent
    files = [
        "__init__.py",
        "settings.py",
        "understanding_export.py",
        "query_model.py",
        "adapters/deepseek_understanding.py",
        "adapters/agent_http.py",
        "services/query_understanding.py",
        "services/region_resolver.py",
        "services/slot_merger.py",
        "services/product_price_understanding.py",
        "services/fresh_price_scope.py",
        "services/product_price_slot_merger.py",
        "domain/product_price_slots.py",
        "domain/product_price.py",
        "domain/device_price_quote.py",
        "domain/device_query.py",
        "domain/slot_merge.py",
        "domain/understanding.py",
        "domain/slots.py",
        "domain/intents.py",
        "domain/primitives.py",
    ]
    return _digest(
        {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in files
        }
    )


async def export_understanding(
    request: ExportRequest,
    output: TextIO,
    *,
    mode: Literal["rules", "hybrid"] = "rules",
    allow_live_model: bool = False,
    max_model_calls: int = 0,
    settings: AssistantSettings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    product_price_enabled: bool = False,
) -> dict:
    if (
        mode not in {"rules", "hybrid"}
        or type(max_model_calls) is not int
        or not 0 <= max_model_calls <= 1000
    ):
        raise ValueError("invalid_export_options")
    if mode == "rules" and (allow_live_model or max_model_calls):
        raise ValueError("rules_mode_has_no_model_budget")
    if mode == "hybrid" and (not allow_live_model or max_model_calls < 1):
        raise ValueError("hybrid_requires_explicit_authorization_and_budget")
    if not product_price_enabled and any(Intent.PRODUCT_PRICE in {case.input.active_intent, case.input.explicit_intent} for case in request.cases):
        raise ValueError("product_price_context_requires_component_opt_in")
    # Rules never even construct settings or load any local credentials.
    resolved = (settings or AssistantSettings()) if mode == "hybrid" else None
    if resolved and (
        not resolved.query_model_enabled
        or _code(resolved.query_model_name) != resolved.query_model_name
    ):
        raise ValueError("hybrid_model_not_enabled_or_invalid_name")
    budget: _BudgetedModel | None = None

    def decorate(model):
        nonlocal budget
        budget = _BudgetedModel(model, max_model_calls)
        return budget

    def observe(measurement):
        if budget is not None:
            budget.observe(measurement)

    @asynccontextmanager
    async def understander_context():
        if resolved is None:
            yield HybridQueryUnderstander(rules=RuleBasedQueryUnderstander(product_price_enabled=product_price_enabled))
        else:
            async with create_query_understander(
                resolved,
                transport=transport,
                decorate_model=decorate,
                call_observer=observe,
                product_price_enabled=product_price_enabled,
            ) as understander:
                yield understander

    def emit(row):
        output.write(
            json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
        )
        output.flush()  # Keep completed observations when later work is interrupted.

    prompt_text, prompt_version = StructuredLlmQueryUnderstander.prompt_profile(product_price_enabled=product_price_enabled)
    prompt = DeepSeekQueryUnderstandingModel._system_prompt(prompt_text, prompt_version, product_price_enabled=product_price_enabled)
    emit(
        {
            "type": "manifest",
            "schema_version": "qu-observations-v1",
            "request_sha256": _digest(request.model_dump(mode="json")),
            "dataset_sha256": request.dataset_sha256,
            "split": request.split,
            "mode": mode,
            "transport": "none"
            if resolved is None
            else "test_transport"
            if transport is not None
            else "live",
            "created_at": datetime.now(UTC).isoformat(),
            "producer_version": __version__,
            "rules_version": RuleBasedQueryUnderstander(product_price_enabled=product_price_enabled).parser_version,
            "parser_version": HybridQueryUnderstander.parser_version,
            "prompt_version": prompt_version
            if resolved
            else None,
            "implementation_sha256": _implementation_digest(),
            "configuration_sha256": _digest(
                {
                    "mode": mode,
                    "product_price_enabled": product_price_enabled,
                    "max_model_calls": max_model_calls,
                    "serial": True,
                    "model_settings": {
                        name: getattr(resolved, name)
                        for name in (
                            "query_model_base_url",
                            "query_model_name",
                            "query_model_timeout_seconds",
                            "query_model_max_tokens",
                            "query_model_max_response_bytes",
                            "query_model_max_concurrency",
                        )
                    }
                    if resolved
                    else None,
                }
            ),
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "model": resolved.query_model_name if resolved else None,
            "timeout_seconds": resolved.query_model_timeout_seconds
            if resolved
            else None,
            "max_tokens": resolved.query_model_max_tokens
            if resolved
            else None,
            "max_model_calls": max_model_calls,
        }
    )
    errors = skipped = 0
    async with understander_context() as understander:
        for case in request.cases:
            started = perf_counter()
            if budget:
                budget.current = _not_called()
            status, error_code, prediction = "ok", None, None
            try:
                result = await understander.understand(
                    message=case.input.message,
                    active_intent=case.input.active_intent,
                    explicit_intent=case.input.explicit_intent,
                    expected_slots=tuple(case.input.expected_slots),
                )
                if budget and budget.current["outcome"] == "budget_exhausted":
                    status, error_code = (
                        "skipped",
                        "query_model_budget_exhausted",
                    )
                    skipped += 1
                else:
                    needs_model = (
                        budget.current["outcome"] != "not_called"
                        if budget
                        else HybridQueryUnderstander._needs_fallback(result)
                    )
                    prediction = _project(result, needs_model=needs_model)
            except Exception:
                status, error_code = "error", "understanding_export_failed"
                errors += 1
            emit(
                {
                    "type": "observation",
                    "id": case.id,
                    "input_sha256": _digest(
                        case.input.model_dump(mode="json")
                    ),
                    "status": status,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                    "error_code": error_code,
                    "prediction": prediction,
                    "model_call": dict(budget.current)
                    if budget
                    else _not_called(),
                }
            )
    return {
        "cases": len(request.cases),
        "model_calls": budget.calls if budget else 0,
        "errors": errors,
        "skipped": skipped,
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="有界本地 Understanding 观测导出；不执行业务工具"
    )
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("rules", "hybrid"), default="rules")
    parser.add_argument("--allow-live-model", action="store_true")
    parser.add_argument("--max-model-calls", type=int, default=0)
    parser.add_argument("--product-price", action="store_true", help="启用 D1/D2 商品价格组件验收；不开放 HTTP 或调用业务工具")
    args = parser.parse_args(argv)
    try:
        if args.requests.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("requests_too_large")
        request = ExportRequest.model_validate(
            json.loads(
                args.requests.read_text(encoding="utf-8"),
                object_pairs_hook=_unique_object,
            )
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            result = asyncio.run(
                export_understanding(
                    request,
                    output,
                    mode=args.mode,
                    allow_live_model=args.allow_live_model,
                    max_model_calls=args.max_model_calls,
                    product_price_enabled=args.product_price,
                )
            )
        print(json.dumps(result))
        return 3 if result["errors"] or result["skipped"] else 0
    except Exception:
        # No exception strings: validation errors can contain input or credentials.
        print(
            "understanding_export_failed: check contract, output path, configuration and explicit model budget"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
