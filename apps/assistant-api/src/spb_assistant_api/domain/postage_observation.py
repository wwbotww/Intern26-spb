from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator

from .postage import PostageSource
from .results import PostageData


class PostageQuoteObservation(BaseModel):
    """A successful observed quote. Business refusals are not empty quotes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data: PostageData
    source: PostageSource
    queried_at: datetime

    @model_validator(mode="after")
    def validate_observation(self) -> "PostageQuoteObservation":
        if self.queried_at.utcoffset() is None:
            raise ValueError("报价观察时间必须包含时区")
        if self.data.queried_at != self.queried_at:
            raise ValueError("报价数据与观察时间必须一致")
        if self.data.quote_basis is None or self.data.billable_weight is None:
            raise ValueError("报价观察必须有明确依据和计费重量")
        return self
