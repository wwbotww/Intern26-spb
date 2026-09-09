from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from .results import TrackingData


SourceIdentifier = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_.-]{1,128}$")
]


class TrackingSource(BaseModel):
    """Trusted adapter metadata, never populated from an upstream payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Literal["fake_gateway", "external_api"]
    source_name: SourceIdentifier
    profile: SourceIdentifier


class TrackingQueryResult(BaseModel):
    """Internal Port envelope; empty queries also have source and observation time.

    Not part of the V2 wire schema. Tools project this into domain provenance
    and warnings, never provider URLs, credentials or raw responses. Public
    source fields require a separate API projection change.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    data: TrackingData | None
    source: TrackingSource
    queried_at: datetime
    history_completeness: Literal["complete", "partial", "unknown"] = "unknown"

    @model_validator(mode="after")
    def validate_observation_time(self) -> "TrackingQueryResult":
        if self.queried_at.tzinfo is None or self.queried_at.utcoffset() is None:
            raise ValueError("轨迹查询观察时间必须包含时区")
        if self.data is not None and self.data.queried_at != self.queried_at:
            raise ValueError("轨迹数据和观察时间必须一致")
        return self
