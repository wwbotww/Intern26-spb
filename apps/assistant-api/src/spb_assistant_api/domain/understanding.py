from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .intents import Intent
from .primitives import MessageText
from .slots import SlotPayload, SlotProvenance


class ControlDirective(StrEnum):
    """Deterministic conversation controls that never reach an LLM/tool."""

    NONE = "none"
    CANCEL = "cancel"
    RESTART = "restart"


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
    control: ControlDirective = ControlDirective.NONE
    source: Literal[
        "explicit_ui",
        "active_workflow",
        "rules",
        "model",
    ]
    parser_version: str = Field(min_length=1, max_length=64)
    prompt_version: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_semantics(self) -> "QueryUnderstandingResult":
        candidate_intents = [candidate.intent for candidate in self.candidates]
        if len(candidate_intents) != len(set(candidate_intents)):
            raise ValueError("候选意图不能重复")
        if (
            self.selected_intent is not Intent.UNKNOWN
            and self.selected_intent not in candidate_intents
        ):
            raise ValueError("候选意图必须包含 selected_intent")
        if self.multi_intent and len(
            {
                intent
                for intent in candidate_intents
                if intent is not Intent.UNKNOWN
            }
        ) < 2:
            raise ValueError("多意图结果必须包含至少两个候选")
        if len(self.missing_slots) != len(set(self.missing_slots)):
            raise ValueError("missing_slots 不能重复")
        if len(self.ambiguities) != len(set(self.ambiguities)):
            raise ValueError("ambiguities 不能重复")
        if self.slots is not None:
            if self.selected_intent is Intent.UNKNOWN:
                raise ValueError("unknown 意图不能携带已选择的槽位")
            if self.slots.intent != self.selected_intent.value:
                raise ValueError("槽位类型必须与 selected_intent 一致")
        if self.control is not ControlDirective.NONE:
            if self.selected_intent is not Intent.UNKNOWN:
                raise ValueError("控制命令不能同时选择业务意图")
            if self.slots is not None:
                raise ValueError("控制命令不能携带业务槽位")
        if self.source == "model" and self.prompt_version is None:
            raise ValueError("模型理解结果必须记录 prompt_version")
        return self


class StructuredModelUnderstanding(BaseModel):
    """Untrusted structured output accepted from an LLM adapter.

    The model chooses only public intent/slot concepts. It cannot name tools,
    commands, nodes, deadlines, or executable functions.
    """

    model_config = ConfigDict(extra="forbid")

    selected_intent: Intent
    candidates: list[IntentCandidate] = Field(default_factory=list)
    slots: SlotPayload | None = None
    missing_slots: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    multi_intent: bool = False

    @model_validator(mode="after")
    def validate_semantics(self) -> "StructuredModelUnderstanding":
        candidate_intents = [candidate.intent for candidate in self.candidates]
        if len(candidate_intents) != len(set(candidate_intents)):
            raise ValueError("候选意图不能重复")
        if (
            self.selected_intent is not Intent.UNKNOWN
            and self.selected_intent not in candidate_intents
        ):
            raise ValueError("候选意图必须包含 selected_intent")
        if self.multi_intent and len(
            {
                intent
                for intent in candidate_intents
                if intent is not Intent.UNKNOWN
            }
        ) < 2:
            raise ValueError("多意图结果必须包含至少两个候选")
        if len(self.missing_slots) != len(set(self.missing_slots)):
            raise ValueError("missing_slots 不能重复")
        if len(self.ambiguities) != len(set(self.ambiguities)):
            raise ValueError("ambiguities 不能重复")
        if self.slots is not None:
            if self.selected_intent is Intent.UNKNOWN:
                raise ValueError("unknown 意图不能携带槽位")
            if self.slots.intent != self.selected_intent.value:
                raise ValueError("模型槽位类型必须与 selected_intent 一致")
        return self
