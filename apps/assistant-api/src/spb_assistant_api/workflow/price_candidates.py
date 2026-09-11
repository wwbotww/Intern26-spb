"""Server-only candidate issuance and resolution, scoped to an owned query."""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from ..domain.agent_errors import AgentOperationError
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.product_price_execution import (
    PriceCandidateChoice,
    PriceCandidateSet,
    PriceSelectionReference,
    ProductPriceData,
)
from ..domain.product_price_slots import ProductPriceCommand
from ..domain.tooling import argument_fingerprint


def selection_error(code: str = "price_selection_invalid") -> AgentOperationError:
    return AgentOperationError(
        AgentFailure(
            category=FailureCategory.INVALID_INPUT,
            code=code,
            message="该价格候选已失效或不属于本次查询，请重新查询。",
        )
    )


def price_command(state: Mapping[str, Any]) -> ProductPriceCommand:
    slots = state.get("slots") or {}
    values = {"conditions": slots.get("conditions")}
    if slots.get("time") is not None:
        values["time"] = slots["time"]
    return ProductPriceCommand(**values)


def issue_candidates(
    state: Mapping[str, Any],
    command: ProductPriceCommand,
    data: ProductPriceData,
    now: datetime,
) -> PriceCandidateSet:
    try:
        session_end = datetime.fromisoformat(state["price_session_expires_at"])
        owner = state["price_owner_id"]
        if (
            now.utcoffset() is None
            or session_end.utcoffset() is None
            or session_end <= now
        ):
            raise ValueError("expired price scope")
        choices = []
        for index, fact in enumerate(data.facts[:20], 1):
            record = fact.record
            identity = record.listing.identity
            if identity.kind == "device":
                fields = identity.specification.model_dump(
                    exclude_none=True, exclude={"attributes"}
                )
                label = (
                    identity.product_name
                    + " "
                    + " / ".join(str(value) for value in fields.values())
                )
            else:
                label = " / ".join(
                    filter(
                        None,
                        (
                            identity.commodity_name,
                            identity.source_specification,
                            identity.source_market_name,
                            record.pointer.region.code,
                            "零售均价"
                            if record.observation.price_nature == "RETAIL_AVERAGE"
                            else "批发均价",
                        ),
                    )
                )
            label = f"{index}. {label}"[:255]
            choices.append(
                PriceCandidateChoice(
                    token=secrets.token_urlsafe(32),
                    label=label,
                    reference=PriceSelectionReference.from_record(record),
                )
            )
        return PriceCandidateSet(
            owner_id=owner,
            conversation_id=state["conversation_id"],
            query_id=state["query_id"],
            constraint_fingerprint=argument_fingerprint(command),
            issued_at=now,
            expires_at=min(now + timedelta(minutes=10), session_end),
            choices=tuple(choices),
        )
    except (KeyError, TypeError, ValueError):
        raise selection_error("price_candidate_scope_invalid") from None


def validate_candidates(state: Mapping[str, Any], now: datetime) -> PriceCandidateSet:
    try:
        candidates = PriceCandidateSet.model_validate(state.get("price_candidates"))
        if (
            candidates.owner_id != state.get("price_owner_id")
            or candidates.conversation_id != state.get("conversation_id")
            or candidates.query_id != state.get("query_id")
            or candidates.constraint_fingerprint
            != argument_fingerprint(price_command(state))
            or not candidates.issued_at <= now < candidates.expires_at
            or now >= datetime.fromisoformat(state["price_session_expires_at"])
        ):
            raise ValueError("candidate scope mismatch")
        return candidates
    except (KeyError, TypeError, ValueError):
        raise selection_error() from None


def resolve_selection(
    state: Mapping[str, Any], token: str, now: datetime
) -> PriceSelectionReference:
    candidates = validate_candidates(state, now)
    for choice in candidates.choices:
        if secrets.compare_digest(choice.token, token):
            return choice.reference
    raise selection_error()


def ordinal_token(state: Mapping[str, Any], message: str) -> str | None:
    """Only a complete ordinal reply maps to the current set. Never parse arbitrary IDs."""
    match = re.fullmatch(
        r"\s*(?:选(?:择)?\s*)?第?([1-9]|1[0-9]|20|[一二三四五六七八九十])(?:个|项)?[。！!]?\s*",
        message,
    )
    if not match:
        return None
    number = match[1]
    index = (
        int(number) if number.isascii() else "一二三四五六七八九十".index(number) + 1
    )
    try:
        candidates = PriceCandidateSet.model_validate(state.get("price_candidates"))
    except ValueError:
        raise selection_error() from None
    if index > len(candidates.choices):
        raise selection_error()
    return candidates.choices[index - 1].token
