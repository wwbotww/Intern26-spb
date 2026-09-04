from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .slots import SlotPayload, SlotProvenance


class SlotConflict(BaseModel):
    """A conflict marker that intentionally excludes the sensitive values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: str
    reason: str = "confirmed_value_changed"


class SlotMergeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slots: SlotPayload
    provenance: list[SlotProvenance] = Field(default_factory=list)
    conflicts: list[SlotConflict] = Field(default_factory=list)
    changed_slots: list[str] = Field(default_factory=list)
