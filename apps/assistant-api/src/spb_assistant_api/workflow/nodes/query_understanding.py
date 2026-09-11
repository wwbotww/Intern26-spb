from __future__ import annotations

from pydantic import TypeAdapter

from ...domain.agent_events import AgentEventType
from ...domain.intents import Intent
from ...domain.ports import QueryUnderstander
from ...domain.slots import PostageSlots, SlotPayload, SlotProvenance
from ...domain.understanding import ControlDirective
from ...services.slot_merger import SlotMerger, required_missing_slots
from ...services.postage_preflight import PostagePreflight
from ..node_utils import agent_event
from ..state import AgentState


_SLOTS_ADAPTER = TypeAdapter(SlotPayload)


def create_understand_node(
    understander: QueryUnderstander,
    slot_merger: SlotMerger | None = None,
    *,
    postage_preflight: PostagePreflight | None = None,
):
    merger = slot_merger or SlotMerger()

    async def understand(state: AgentState) -> dict[str, object]:
        if state.get("active_intent") == Intent.PRODUCT_PRICE.value and (state.get("price_selected_token") or state.get("last_error")):
            # A separately typed selection is resolved by server policy, never QU/LLM.
            return {"phase": "ready", "latest_message": "", "ambiguities": [], "missing_slots": []}
        active_raw = state.get("active_intent")
        explicit_raw = state.get("explicit_intent")
        active = Intent(active_raw) if active_raw else None
        explicit = Intent(explicit_raw) if explicit_raw else None
        result = await understander.understand(
            message=state.get("latest_message", ""),
            active_intent=active,
            explicit_intent=explicit,
            expected_slots=tuple(state.get("missing_slots", [])),
        )
        if state.get("intent_choice_confirmed", False):
            result = result.model_copy(
                update={
                    "multi_intent": False,
                    "ambiguities": [
                        item
                        for item in result.ambiguities
                        if item
                        not in {
                            "multiple_intents",
                            "intent_selection_required",
                            "intent_switch_confirmation",
                        }
                    ],
                }
            )

        if result.control is not ControlDirective.NONE:
            return {
                "price_candidates": None,
                "price_selected_token": None,
                "price_result_slots": [],
                "latest_message": "",
                "explicit_intent": None,
                "active_intent": None,
                "candidate_intents": [],
                "multi_intent": False,
                "control": result.control.value,
                "slots": None,
                "postage_policy_snapshot": None,
                "postage_review_fingerprint": None,
                "postage_confirmed_fingerprint": None,
                "postage_requirements": [],
                "slot_provenance": [],
                "intent_choice_confirmed": False,
                "missing_slots": [],
                "ambiguities": [],
                "pending_query": "",
                "phase": "ready",
                "understanding_parser_version": result.parser_version,
                "understanding_prompt_version": result.prompt_version,
                "audit_events": [
                    agent_event(
                        AgentEventType.QUERY_UNDERSTOOD,
                        node="understand",
                        phase="ready",
                        intent=Intent.UNKNOWN.value,
                        source=result.source,
                        control=result.control.value,
                        missing_slot_count=0,
                        parser_version=result.parser_version,
                    )
                ],
            }

        selected = result.selected_intent
        ambiguities = list(result.ambiguities)
        missing = list(result.missing_slots)
        incoming = result.slots
        existing = state.get("slots")
        postage_update: dict[str, object] = {}
        price_update: dict[str, object] = {}
        if postage_preflight is not None and isinstance(incoming, PostageSlots):
            # Re-extract product locally; a model cannot invent an executable code.
            message = state.get("latest_message", "")
            evidence = message
            if state.get("intent_choice_confirmed"):
                evidence = state.get("pending_query", "") + " " + message
            product, product_conflict = postage_preflight.select_product(
                evidence, model_product=incoming.product_code,
            )
            incoming = incoming.model_copy(update={"product_code": product})
            if product_conflict:
                ambiguities.append("slot_conflict:product_code")
            if product is not None:
                result = result.model_copy(update={"slot_provenance": [
                    item for item in result.slot_provenance if item.slot != "product_code"
                ] + [SlotProvenance(slot="product_code", source="rule_extractor")]})
            snapshot = state.get("postage_policy_snapshot")
            if snapshot is None:
                # An old paused postage query may have already lost unsupported
                # original conditions. Never certify it using only the resume text.
                snapshot = (
                    "legacy-unverified"
                    if active is Intent.POSTAGE and existing is not None
                    else postage_preflight.fingerprint
                )
            postage_update = {
                "postage_policy_snapshot": snapshot,
                "postage_requirements": sorted(
                    set(state.get("postage_requirements", []))
                    | set(postage_preflight.unsupported_requirements(evidence))
                ),
            }
        existing_provenance = [
            SlotProvenance.model_validate(item)
            for item in state.get("slot_provenance", [])
        ]
        preserved_switch = (
            "intent_switch_confirmation" in ambiguities
            and active is not None
            and explicit is None
        )

        if preserved_switch:
            active_value = active.value
            resolved_slots = existing
            resolved_provenance = existing_provenance
        else:
            confirmed_switch = (
                explicit is not None
                and active is not None
                and explicit is not active
            )
            if confirmed_switch:
                existing = None
                existing_provenance = []
                price_update = {"price_candidates": None, "price_selected_token": None, "price_result_slots": []}
            active_value = (
                None if selected is Intent.UNKNOWN else selected.value
            )
            resolved_slots = (
                incoming.model_dump(mode="json")
                if incoming is not None
                else existing
            )
            resolved_provenance = list(result.slot_provenance)
            if incoming is not None and existing is not None:
                existing_slots = _SLOTS_ADAPTER.validate_python(existing)
                if existing_slots.intent == incoming.intent:
                    merged = merger.merge(
                        existing=existing_slots,
                        incoming=incoming,
                        existing_provenance=existing_provenance,
                        incoming_provenance=result.slot_provenance,
                        confirm_overwrite=state.get(
                            "confirm_slot_overwrite",
                            False,
                        ),
                    )
                    resolved_slots = merged.slots.model_dump(mode="json")
                    resolved_provenance = merged.provenance
                    if merged.invalidate_price_candidates:
                        price_update = {"price_candidates": None, "price_selected_token": None, "price_result_slots": []}
                    for conflict in merged.conflicts:
                        ambiguities.append(f"slot_conflict:{conflict.slot}")
                        missing.append(conflict.slot)

        conflict_slots = [
            item.split(":", 1)[1]
            for item in ambiguities
            if item.startswith("slot_conflict:")
        ]
        if resolved_slots is not None:
            missing = [
                *required_missing_slots(resolved_slots),
                *conflict_slots,
            ]
            if postage_preflight is not None and resolved_slots.get("intent") == "postage":
                missing = [*postage_preflight.missing_slots(PostageSlots.model_validate(resolved_slots)), *conflict_slots]

        intent_ambiguity = result.multi_intent or any(
            item
            in {
                "multiple_intents",
                "intent_selection_required",
                "intent_switch_confirmation",
            }
            for item in ambiguities
        )
        phase = "clarifying" if intent_ambiguity else "ready"
        if not intent_ambiguity and missing:
            phase = "collecting"
        if selected is Intent.UNKNOWN and not intent_ambiguity:
            phase = "clarifying"

        conflict_events = [
            agent_event(
                AgentEventType.SLOT_CONFLICT_DETECTED,
                node="understand",
                phase=phase,
                slot=item.split(":", 1)[1],
            )
            for item in ambiguities
            if item.startswith("slot_conflict:")
        ]
        return {
            **postage_update,
            **price_update,
            "latest_message": "",
            "explicit_intent": None,
            "active_intent": active_value,
            "candidate_intents": [
                candidate.intent.value for candidate in result.candidates
            ],
            "multi_intent": result.multi_intent,
            "control": ControlDirective.NONE.value,
            "slots": resolved_slots,
            "slot_provenance": [
                item.model_dump(mode="json")
                for item in resolved_provenance
            ],
            "confirm_slot_overwrite": False,
            "intent_choice_confirmed": False,
            "missing_slots": list(dict.fromkeys(missing)),
            "ambiguities": list(dict.fromkeys(ambiguities)),
            "pending_query": (
                result.normalized_query if intent_ambiguity else ""
            ),
            "phase": phase,
            "understanding_parser_version": result.parser_version,
            "understanding_prompt_version": result.prompt_version,
            "audit_events": [
                agent_event(
                    AgentEventType.QUERY_UNDERSTOOD,
                    node="understand",
                    phase=phase,
                    intent=selected.value,
                    source=result.source,
                    missing_slot_count=len(set(missing)),
                    ambiguity_count=len(set(ambiguities)),
                    multi_intent=result.multi_intent,
                    parser_version=result.parser_version,
                    prompt_version=result.prompt_version,
                ),
                *conflict_events,
            ],
        }

    return understand
