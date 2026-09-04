from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from ..domain.slot_merge import SlotConflict, SlotMergeResult
from ..domain.slots import (
    DeliveryTimeSlots,
    PostageSlots,
    RegionResolution,
    SlotPayload,
    SlotProvenance,
    TrackingSlots,
)


_SLOTS_ADAPTER = TypeAdapter(SlotPayload)
_SOURCE_PRIORITY = {
    "workflow_state": 0,
    "model_extractor": 1,
    "rule_extractor": 2,
    "current_turn": 3,
    "explicit_ui": 4,
}


class SlotMerger:
    """Pure merge policy for one active intent.

    Ambiguous or empty values may be refined directly. Conflicting confirmed
    values remain unchanged unless the caller records an explicit overwrite
    confirmation. Conflict objects contain field names only, so they are safe
    to project into logs and metrics.
    """

    def merge(
        self,
        *,
        existing: SlotPayload | dict[str, Any] | None,
        incoming: SlotPayload | dict[str, Any],
        existing_provenance: list[SlotProvenance] | None = None,
        incoming_provenance: list[SlotProvenance] | None = None,
        confirm_overwrite: bool = False,
    ) -> SlotMergeResult:
        incoming_slots = _SLOTS_ADAPTER.validate_python(incoming)
        if existing is None:
            return SlotMergeResult(
                slots=incoming_slots,
                provenance=self._deduplicate(incoming_provenance or []),
                changed_slots=[
                    name
                    for name, value in incoming_slots.model_dump().items()
                    if name != "intent" and value is not None
                ],
            )

        existing_slots = _SLOTS_ADAPTER.validate_python(existing)
        if existing_slots.intent != incoming_slots.intent:
            return SlotMergeResult(
                slots=existing_slots,
                provenance=self._deduplicate(existing_provenance or []),
                conflicts=[
                    SlotConflict(slot="intent", reason="intent_switch")
                ],
            )

        current = existing_slots.model_dump(mode="python")
        new_values = incoming_slots.model_dump(mode="python")
        provenance = list(existing_provenance or [])
        incoming_by_slot = {
            item.slot: item for item in incoming_provenance or []
        }
        conflicts: list[SlotConflict] = []
        changed: list[str] = []

        for name, incoming_value in new_values.items():
            if name == "intent" or incoming_value is None:
                continue
            existing_value = current.get(name)
            if existing_value is None or self._is_unconfirmed_value(
                name,
                existing_value,
            ):
                current[name] = incoming_value
                changed.append(name)
                provenance = self._replace_provenance(
                    provenance,
                    incoming_by_slot.get(name),
                )
                continue
            if existing_value == incoming_value:
                provenance = self._prefer_provenance(
                    provenance,
                    incoming_by_slot.get(name),
                )
                continue
            if confirm_overwrite:
                current[name] = incoming_value
                changed.append(name)
                provenance = self._replace_provenance(
                    provenance,
                    incoming_by_slot.get(name),
                )
                continue
            conflicts.append(SlotConflict(slot=name))

        return SlotMergeResult(
            slots=_SLOTS_ADAPTER.validate_python(current),
            provenance=self._deduplicate(provenance),
            conflicts=conflicts,
            changed_slots=changed,
        )

    @staticmethod
    def _replace_provenance(
        items: list[SlotProvenance],
        incoming: SlotProvenance | None,
    ) -> list[SlotProvenance]:
        if incoming is None:
            return items
        return [item for item in items if item.slot != incoming.slot] + [
            incoming
        ]

    @staticmethod
    def _prefer_provenance(
        items: list[SlotProvenance],
        incoming: SlotProvenance | None,
    ) -> list[SlotProvenance]:
        if incoming is None:
            return items
        current = next(
            (item for item in items if item.slot == incoming.slot),
            None,
        )
        if current is None or _SOURCE_PRIORITY[incoming.source] > _SOURCE_PRIORITY[
            current.source
        ]:
            return SlotMerger._replace_provenance(items, incoming)
        return items

    @staticmethod
    def _deduplicate(
        items: list[SlotProvenance],
    ) -> list[SlotProvenance]:
        by_slot: dict[str, SlotProvenance] = {}
        for item in items:
            current = by_slot.get(item.slot)
            if current is None or _SOURCE_PRIORITY[item.source] > _SOURCE_PRIORITY[
                current.source
            ]:
                by_slot[item.slot] = item
        return [by_slot[name] for name in sorted(by_slot)]

    @staticmethod
    def _is_unconfirmed_value(name: str, value: object) -> bool:
        if not isinstance(value, dict):
            return False
        if name in {"origin", "destination"}:
            return value.get("resolution") != RegionResolution.RESOLVED.value
        if name == "weight":
            return value.get("value") is None
        return False


def required_missing_slots(
    slots: SlotPayload | dict[str, Any],
) -> list[str]:
    payload = _SLOTS_ADAPTER.validate_python(slots)
    if isinstance(payload, TrackingSlots):
        return [] if payload.mail_no is not None else ["mail_no"]
    if isinstance(payload, (DeliveryTimeSlots, PostageSlots)):
        missing = [
            name
            for name, region in (
                ("origin", payload.origin),
                ("destination", payload.destination),
            )
            if region is None
            or region.resolution is not RegionResolution.RESOLVED
        ]
        if (
            isinstance(payload, PostageSlots)
            and (
                payload.weight is None
                or payload.weight.value is None
            )
        ):
            missing.append("weight")
        return missing
    return []
