from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    DELETED = "deleted"


class ConversationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID
    owner_id: str = Field(min_length=1, max_length=255)
    status: ConversationStatus = ConversationStatus.ACTIVE
    state_schema_version: str = Field(default="2", min_length=1, max_length=16)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> "ConversationMetadata":
        values = (self.created_at, self.updated_at, self.expires_at)
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in values
        ):
            raise ValueError("会话时间必须包含时区")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 不能早于 created_at")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at 必须晚于 created_at")
        return self


class IdempotencyStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class IdempotencyReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID
    key: str = Field(min_length=1, max_length=255)
    request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: IdempotencyStatus
    response: dict[str, Any] | None = None
    created_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_completion(self) -> "IdempotencyReceipt":
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at 必须包含时区")
        if self.status is IdempotencyStatus.COMPLETED:
            if self.response is None or self.completed_at is None:
                raise ValueError(
                    "已完成幂等收据必须包含响应和完成时间"
                )
        elif self.response is not None or self.completed_at is not None:
            raise ValueError("处理中幂等收据不能包含完成结果")
        if (
            self.completed_at is not None
            and (
                self.completed_at.tzinfo is None
                or self.completed_at.utcoffset() is None
            )
        ):
            raise ValueError("completed_at 必须包含时区")
        return self


class IdempotencyClaimStatus(StrEnum):
    CLAIMED = "claimed"
    REPLAY = "replay"
    IN_PROGRESS = "in_progress"
    CONFLICT = "conflict"


class IdempotencyClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: IdempotencyClaimStatus
    receipt: IdempotencyReceipt
