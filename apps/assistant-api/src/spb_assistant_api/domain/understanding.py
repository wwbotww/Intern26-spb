from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .intents import Intent
from .primitives import MessageText
from .slots import SlotPayload, SlotProvenance


class IntentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Intent
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    signals: list[str] = Field(default_factory=list)


class QueryUnderstandingResult(BaseModel):
    """Versioned, framework-independent output of query understanding."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    original_query: MessageText
    normalized_query: MessageText
    selected_intent: Intent
    candidates: list[IntentCandidate] = Field(default_factory=list)
    slots: SlotPayload | None = None
    slot_provenance: list[SlotProvenance] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    multi_intent: bool = False
    source: Literal[
        "explicit_ui",
        "active_workflow",
        "rules",
        "model",
    ]
    parser_version: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_semantics(self) -> "QueryUnderstandingResult":
        candidate_intents = [candidate.intent for candidate in self.candidates]
        if len(candidate_intents) != len(set(candidate_intents)):
            raise ValueError("候选意图不能重复")
        if self.slots is not None:
            if self.selected_intent is Intent.UNKNOWN:
                raise ValueError("unknown 意图不能携带已选择的槽位")
            if self.slots.intent != self.selected_intent.value:
                raise ValueError("槽位类型必须与 selected_intent 一致")
        return self
