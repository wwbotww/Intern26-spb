from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FailureCategory(StrEnum):
    MISSING_INPUT = "missing_input"
    AMBIGUOUS_INTENT = "ambiguous_intent"
    INVALID_INPUT = "invalid_input"
    NO_MATCH = "no_match"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_RATE_LIMITED = "upstream_rate_limited"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    CONTRACT_VIOLATION = "contract_violation"
    STATE_CONFLICT = "state_conflict"
    PERSISTENCE_UNAVAILABLE = "persistence_unavailable"
    STATE_SCHEMA_INCOMPATIBLE = "state_schema_incompatible"
    LOOP_BUDGET_EXCEEDED = "loop_budget_exceeded"
    INTERNAL_ERROR = "internal_error"


class AgentFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: FailureCategory
    code: str
    message: str
    retryable: bool = False
    retry_after_seconds: float | None = Field(default=None, ge=0)
