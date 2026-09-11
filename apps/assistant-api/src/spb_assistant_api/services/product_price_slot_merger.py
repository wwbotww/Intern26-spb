"""Atomic price-condition merge; dependencies are invalidated before reuse."""

from __future__ import annotations

from typing import Any

from ..domain.product_price_slots import (
    DevicePriceConditions,
    FreshPriceConditions,
    ProductPriceSlots,
    UnknownPriceConditions,
)
from ..domain.slot_merge import SlotConflict, SlotMergeResult
from ..domain.slots import SlotProvenance


def price_slot_values(slots: ProductPriceSlots) -> dict[str, Any]:
    values: dict[str, Any] = {}

    def visit(data: dict, prefix: str = "") -> None:
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            if value is None or key == "intent" or value == {}:
                continue
            if isinstance(value, dict) and path not in {
                "time",
                "conditions.requested_unit",
            }:
                visit(value, path)
            else:
                values[path] = value

    visit(slots.model_dump(mode="python"))
    return values


def _set(data: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        data = data.setdefault(part, {})
    data[parts[-1]] = value


def _clear(data: dict, path: str) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        data = data.get(part, {})
    data.pop(parts[-1], None)


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return {k: v for k, v in left.items() if k != "raw_text"} == {
            k: v for k, v in right.items() if k != "raw_text"
        }
    return left == right


def merge_product_price_slots(
    *,
    existing: ProductPriceSlots,
    incoming: ProductPriceSlots,
    existing_provenance: list[SlotProvenance],
    incoming_provenance: list[SlotProvenance],
    confirm_overwrite: bool,
) -> SlotMergeResult:
    old, new = price_slot_values(existing), price_slot_values(incoming)
    previous_kind, next_kind = existing.conditions.kind, incoming.conditions.kind
    sources = {p.slot: p for p in incoming_provenance}
    conflicts: list[SlotConflict] = []
    expanded_scope = [
        key
        for key in (
            "conditions.region_text",
            "conditions.market_text",
            "conditions.price_nature",
        )
        if next_kind == previous_kind == "fresh"
        and new.get("conditions.source_scope") == "separate_sources"
        and key in old
        and key not in new
    ]
    if next_kind == "unknown" and previous_kind != "unknown":
        # A time-only follow-up cannot erase an already selected category.
        new = {
            key: value
            for key, value in new.items()
            if not key.startswith("conditions.")
        }
    category_changed = next_kind != "unknown" and previous_kind != next_kind
    if category_changed and previous_kind != "unknown":
        conflicts.append(
            SlotConflict(slot="conditions.kind", reason="price_category_changed")
        )
    else:
        for key, value in new.items():
            if (
                key in old
                and not _same(old[key], value)
                and key not in {"conditions.kind", "conditions.subject_text"}
            ):
                conflicts.append(SlotConflict(slot=key))
    if expanded_scope and not any(
        item.slot == "conditions.source_scope" for item in conflicts
    ):
        conflicts.append(
            SlotConflict(slot="conditions.source_scope", reason="price_scope_expanded")
        )
    # A blanket confirmation is not authority for model-invented replacement.
    untrusted = [
        item
        for item in conflicts
        if any(
            source.source == "model_extractor"
            and (item.slot == path or item.slot.startswith(path + "."))
            for path, source in sources.items()
        )
    ]
    if untrusted or (conflicts and not confirm_overwrite):
        return SlotMergeResult(
            slots=existing, provenance=existing_provenance, conflicts=conflicts
        )

    invalidated: list[str] = []
    if category_changed:
        merged = incoming.model_dump(mode="python")
        invalidated = [key for key in old if key.startswith("conditions.")]
        if (
            isinstance(existing.conditions, UnknownPriceConditions)
            and existing.conditions.subject_text == "苹果"
        ):
            if (
                isinstance(incoming.conditions, FreshPriceConditions)
                and incoming.conditions.commodity is None
            ):
                merged["conditions"]["commodity"] = "苹果"
            elif (
                isinstance(incoming.conditions, DevicePriceConditions)
                and incoming.conditions.brand is None
            ):
                merged["conditions"]["brand"] = "APPLE"
        # Time is independent of category refinement, but not a confirmed switch.
        if previous_kind == "unknown" and incoming.time is None:
            merged["time"] = existing.time.model_dump() if existing.time else None
    else:
        merged = existing.model_dump(mode="python")
        changed = {
            key
            for key, value in new.items()
            if key in old and not _same(value, old[key])
        }
        reset_prefixes: list[str] = []
        reset_prefixes.extend(expanded_scope)
        if changed & {"conditions.brand", "conditions.product_text"}:
            reset_prefixes.append("conditions.specification.")
        if "conditions.commodity" in changed:
            reset_prefixes.extend(("conditions.variety", "conditions.requested_unit"))
        if "conditions.region_text" in changed:
            reset_prefixes.append("conditions.market_text")
        for key in old:
            if any(key.startswith(prefix) for prefix in reset_prefixes):
                _clear(merged, key)
                invalidated.append(key)
        # A brand/model pair is a single identity. New model text without a
        # detected brand must not inherit a stale explicit brand.
        if "conditions.product_text" in changed and "conditions.brand" not in new:
            merged["conditions"]["brand"] = None
            invalidated.append("conditions.brand")
        if "conditions.brand" in changed and "conditions.product_text" not in new:
            merged["conditions"]["product_text"] = None
            invalidated.append("conditions.product_text")
        for key, value in new.items():
            if key not in old or not _same(value, old[key]) or key in invalidated:
                _set(merged, key, value)
    slots = ProductPriceSlots.model_validate(merged)
    final = price_slot_values(slots)
    changed_keys = [key for key, value in final.items() if old.get(key) != value]
    provenance = {
        p.slot: p
        for p in existing_provenance
        if p.slot in final and p.slot not in invalidated
    }
    priority = {
        "workflow_state": 0,
        "model_extractor": 1,
        "rule_extractor": 2,
        "current_turn": 3,
        "explicit_ui": 4,
    }
    for item in incoming_provenance:
        prior = provenance.get(item.slot)
        if item.slot in final and (
            prior is None
            or item.slot in changed_keys
            or priority[item.source] > priority[prior.source]
        ):
            provenance[item.slot] = item
    return SlotMergeResult(
        slots=slots,
        provenance=[provenance[key] for key in sorted(provenance)],
        changed_slots=changed_keys,
        invalidated_slots=sorted(set(invalidated)),
        invalidate_price_candidates=(old != final),
    )
