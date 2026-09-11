from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid5

from pydantic import TypeAdapter

from ..domain.agent_actions import (
    ClarifyIntentAction,
    CollectSlotsAction,
    ControlAction,
    HandoffAction,
    InvokeToolAction,
    NextAction,
    RequiredInput,
    RespondAction,
)
from ..domain.agent_events import ToolCallRecord
from ..domain.commands import (
    DeliveryTimeCommand,
    DevicePriceCommand,
    PolicyCommand,
    PostageCommand,
    ProductPriceCommand,
    TrackingCommand,
)
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.intents import Intent
from ..domain.results import AgentResult
from ..domain.slots import (
    DeliveryTimeSlots,
    DevicePriceSlots,
    PolicySlots,
    PostageSlots,
    ProductPriceSlots,
    SlotPayload,
    TrackingSlots,
)
from ..domain.tooling import (
    CommandModel,
    LegacyToolCallReference,
    ToolDescriptor,
    argument_fingerprint,
)
from ..domain.understanding import ControlDirective
from ..domain.agent_errors import AgentOperationError
from ..services.postage_preflight import POSTAGE_CONFIRMATION, PostagePreflight
from ..services.product_price_preflight import PRICE_AMBIGUITY_SLOTS, PRICE_SLOT_LABELS, price_required_input
from .price_candidates import resolve_selection, validate_candidates


_SLOTS_ADAPTER = TypeAdapter(SlotPayload)
_RETRYABLE_TOOL_FAILURES = {
    FailureCategory.UPSTREAM_TIMEOUT,
    FailureCategory.UPSTREAM_RATE_LIMITED,
    FailureCategory.UPSTREAM_UNAVAILABLE,
}


@dataclass(frozen=True, slots=True)
class WorkflowDecision:
    action: NextAction
    failure: AgentFailure | None = None
    postage_review_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    retry: bool


def is_price_clarification(action: NextAction, state: Mapping[str, Any]) -> bool:
    return (
        isinstance(action, CollectSlotsAction) and action.intent is Intent.PRODUCT_PRICE
        or isinstance(action, ClarifyIntentAction) and state.get("active_intent") == Intent.PRODUCT_PRICE.value
    )


class WorkflowPolicy:
    """Pure, deterministic policy for the bounded agent workflow."""

    def __init__(
        self,
        descriptors: Mapping[Intent, ToolDescriptor],
        *,
        postage_preflight: PostagePreflight | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._descriptors = dict(descriptors)
        self._postage_preflight = postage_preflight
        self._clock = clock or (lambda: datetime.now(UTC))

    def decide(self, state: Mapping[str, Any]) -> WorkflowDecision:
        decision = self._decide(state)
        if is_price_clarification(decision.action, state) and (
            int(state.get("price_clarification_count", 0)) >= 3 or int(state.get("tool_call_count", 0)) >= 2
        ):
            return self._failure(FailureCategory.LOOP_BUDGET_EXCEEDED,
                                 "price_query_budget_exceeded", "本次价格查询已达到澄清或查询次数上限，请重新提问。")
        return decision

    def _decide(self, state: Mapping[str, Any]) -> WorkflowDecision:
        if state.get("last_result") is not None:
            AgentResult.model_validate(state["last_result"])
            return WorkflowDecision(action=RespondAction())

        if state.get("last_error") is not None:
            failure = AgentFailure.model_validate(state["last_error"])
            return WorkflowDecision(
                action=RespondAction(),
                failure=failure,
            )

        step_count = int(state.get("step_count", 0))
        max_steps = int(state.get("max_steps", 8))
        if step_count > max_steps:
            return self._failure(
                FailureCategory.LOOP_BUDGET_EXCEEDED,
                "loop_step_budget_exceeded",
                "Agent 已达到本轮最大决策步数",
            )

        control = ControlDirective(
            str(state.get("control", ControlDirective.NONE.value))
        )
        if control is not ControlDirective.NONE:
            return WorkflowDecision(action=ControlAction(directive=control))

        ambiguities = list(state.get("ambiguities", []))
        if bool(state.get("multi_intent")) or any(
            item
            in {
                "multiple_intents",
                "intent_selection_required",
                "intent_switch_confirmation",
            }
            for item in ambiguities
        ):
            candidates = [
                Intent(value)
                for value in dict.fromkeys(
                    state.get("candidate_intents", [])
                )
                if value != Intent.UNKNOWN.value
            ]
            active_raw = state.get("active_intent")
            if active_raw and Intent(active_raw) not in candidates:
                candidates.insert(0, Intent(active_raw))
            if not candidates:
                return WorkflowDecision(
                    action=HandoffAction(reason_code="unknown_intent")
                )
            return WorkflowDecision(
                action=ClarifyIntentAction(
                    candidates=candidates,
                    prompt=(
                        "检测到多个可能的查询目标，"
                        "请选择本轮要处理的一项。"
                    ),
                )
            )

        raw_intent = state.get("active_intent")
        if raw_intent is None or raw_intent == Intent.UNKNOWN.value:
            return WorkflowDecision(
                action=HandoffAction(reason_code="unknown_intent")
            )
        intent = Intent(raw_intent)

        # Do not collect addresses/weight for a capability that cannot execute.
        descriptor = self._descriptors.get(intent)
        if descriptor is None:
            return WorkflowDecision(
                action=HandoffAction(reason_code="capability_not_available")
            )

        missing_slots = list(state.get("missing_slots", []))
        if intent is Intent.PRODUCT_PRICE:
            if any(item in ambiguities for item in ("price_unsupported_time", "price_unsupported_unit")):
                return self._failure(
                    FailureCategory.INVALID_INPUT, "price_scope_unsupported",
                    "当前支持最新可用商品报价；无法按所述历史时间、趋势或包装单位查询，请调整条件。",
                )
            missing_slots = list(dict.fromkeys([
                *missing_slots, *(PRICE_AMBIGUITY_SLOTS[item] for item in ambiguities if item in PRICE_AMBIGUITY_SLOTS),
            ]))
            missing_slots = list(dict.fromkeys([*missing_slots, *state.get("price_result_slots", [])]))
            if state.get("price_candidates") and not missing_slots and not state.get("price_selected_token"):
                try:
                    candidates = validate_candidates(state, self._clock())
                except AgentOperationError as error:
                    return WorkflowDecision(action=RespondAction(), failure=error.failure)
                return WorkflowDecision(action=CollectSlotsAction(
                    intent=intent, prompt="请选择当前候选，或补充更精确的条件；未列出的候选不代表不存在。",
                    required_inputs=[RequiredInput(name="price_selection", label="商品候选", type="choice",
                        choices=[item.label for item in candidates.choices],
                        price_candidates=[{"candidate_token": item.token, "label": item.label,
                            "expires_at": candidates.expires_at.isoformat()} for item in candidates.choices],
                        validation_hint="可以回复序号；候选仅对本次查询短期有效。")],
                ))
        if intent is Intent.POSTAGE and self._postage_preflight is not None:
            try:
                self._postage_preflight.validate_state(state)
                slots = PostageSlots.model_validate(state.get("slots"))
                missing_slots = list(dict.fromkeys([*missing_slots, *self._postage_preflight.missing_slots(slots)]))
            except AgentOperationError as error:
                return WorkflowDecision(action=RespondAction(), failure=error.failure)
            except ValueError:
                return self._failure(FailureCategory.INVALID_INPUT, "postage_slots_invalid", "资费输入不符合契约")
        if missing_slots:
            conflict_slots = {
                item.split(":", 1)[1]
                for item in ambiguities
                if item.startswith("slot_conflict:")
            }
            return WorkflowDecision(
                action=CollectSlotsAction(
                    intent=intent,
                    required_inputs=[
                        self._required_input(
                            name,
                            confirmation_required=name in conflict_slots,
                        )
                        for name in missing_slots
                    ],
                    prompt=self._clarification_prompt(
                        missing_slots,
                        conflict_slots=conflict_slots,
                    ),
                )
            )

        try:
            command = self._build_command(intent, state.get("slots"))
            if isinstance(command, ProductPriceCommand) and state.get("price_selected_token"):
                command = ProductPriceCommand(**command.model_dump(exclude={"selection"}),
                    selection=resolve_selection(state, state["price_selected_token"], self._clock()))
            if isinstance(command, PostageCommand) and self._postage_preflight is not None:
                command = self._postage_preflight.prepare(command)
        except AgentOperationError as error:
            return WorkflowDecision(action=RespondAction(), failure=error.failure)
        except (TypeError, ValueError):
            return self._failure(
                FailureCategory.INVALID_INPUT,
                "validated_slots_invalid",
                "已收集参数不能生成有效命令",
            )
        fingerprint = argument_fingerprint(command)
        if (
            isinstance(command, PostageCommand)
            and self._postage_preflight is not None
            and self._postage_preflight.require_confirmation
            and state.get("postage_confirmed_fingerprint") != fingerprint
        ):
            # This is a reviewed command, not blanket permission for later edits.
            context = command.pricing_context
            assert context is not None
            origin = command.origin.canonical_name
            destination = command.destination.canonical_name
            prompt = (
                f"请确认本次基础询价：{origin} → {destination}，产品 {command.product_code}，"
                f"实重 {context.weight_grams} 克；仅限国内实重、无增值服务。"
                "结果仅为估算，不是最终收费。"
                "如条件有误，请先修改；确认仅适用于本次条件。"
            )
            if self._postage_preflight.catalog.evidence == "synthetic":
                prompt += "当前使用合成测试数据。"
            return WorkflowDecision(
                action=CollectSlotsAction(
                    intent=intent, prompt=prompt,
                    required_inputs=[RequiredInput(
                        name="postage_confirmation", label="报价范围确认", type="choice",
                        choices=[POSTAGE_CONFIRMATION], validation_hint=prompt,
                    )],
                ),
                postage_review_fingerprint=fingerprint,
            )
        try:
            query_id = UUID(str(state.get("query_id", "")))
            call_id = uuid5(
                query_id,
                f"{state.get('conversation_id', '')}:{descriptor.tool_name}:{fingerprint}",
            )
            legacy_raw = state.get("legacy_tool_call")
            if legacy_raw is not None:
                legacy = LegacyToolCallReference.model_validate(legacy_raw)
                if (
                    legacy.tool_name == descriptor.tool_name
                    and legacy.argument_fingerprint == fingerprint
                ):
                    call_id = legacy.tool_call_id
        except (TypeError, ValueError):
            return self._failure(
                FailureCategory.STATE_SCHEMA_INCOMPATIBLE,
                "query_execution_scope_invalid",
                "Workflow 缺少有效的逻辑查询身份",
            )
        prior_calls = [
            ToolCallRecord.model_validate(item)
            for item in state.get("tool_calls", [])
        ]
        same_logical_call = any(
            item.tool_call_id == call_id
            for item in prior_calls
        )
        if (
            not same_logical_call
            and int(state.get("tool_call_count", 0))
            >= (2 if intent is Intent.PRODUCT_PRICE else int(state.get("max_tool_calls", 1)))
        ):
            return self._failure(
                FailureCategory.LOOP_BUDGET_EXCEEDED,
                "tool_call_budget_exceeded",
                "Agent 已达到本轮最大工具调用数",
            )

        attempt = int(state.get("retry_count", 0)) + 1
        if intent is Intent.PRODUCT_PRICE:
            # Query-wide retry allowance is distinct from per-call attempt numbers.
            attempt = max((item.attempt + 1 for item in prior_calls
                           if item.tool_call_id == call_id and item.status.value == "failed"), default=1)
        if attempt > descriptor.max_attempts:
            return self._failure(
                FailureCategory.LOOP_BUDGET_EXCEEDED,
                "tool_attempt_budget_exceeded",
                "工具已达到最大尝试次数",
            )

        conversation_id = str(state.get("conversation_id", "")).strip()
        deadline_at = str(state.get("deadline_at", "")).strip()
        if not conversation_id or not deadline_at:
            return self._failure(
                FailureCategory.INTERNAL_ERROR,
                "workflow_identity_missing",
                "Workflow 缺少执行身份或 deadline",
            )
        try:
            action = InvokeToolAction(
                tool_name=descriptor.tool_name,
                command=command,
                tool_call_id=call_id,
                argument_fingerprint=fingerprint,
                attempt=attempt,
                deadline_at=datetime.fromisoformat(deadline_at),
            )
        except (TypeError, ValueError):
            return self._failure(
                FailureCategory.STATE_SCHEMA_INCOMPATIBLE,
                "invoke_action_invalid",
                "Workflow 执行动作状态不兼容",
            )
        return WorkflowDecision(
            action=action
        )

    @staticmethod
    def recover(
        failure: AgentFailure,
        *,
        retry_count: int,
        max_retries: int,
    ) -> RecoveryDecision:
        return RecoveryDecision(
            retry=(
                failure.retryable
                and failure.category in _RETRYABLE_TOOL_FAILURES
                and retry_count < max_retries
            )
        )

    @staticmethod
    def _build_command(
        intent: Intent,
        raw_slots: object,
    ) -> CommandModel:
        if raw_slots is None:
            raise ValueError("执行工具前必须存在槽位")
        slots = _SLOTS_ADAPTER.validate_python(raw_slots)
        if intent is Intent.POLICY and isinstance(slots, PolicySlots):
            return PolicyCommand(question=slots.question)
        if intent is Intent.PRODUCT_PRICE and isinstance(slots, ProductPriceSlots):
            values = {"conditions": slots.conditions}
            if slots.time is not None:
                values["time"] = slots.time
            return ProductPriceCommand(**values)
        if (
            intent is Intent.DEVICE_PRICE
            and isinstance(slots, DevicePriceSlots)
        ):
            return DevicePriceCommand(question=slots.question)
        if intent is Intent.TRACKING and isinstance(slots, TrackingSlots):
            if slots.mail_no is None:
                raise ValueError("轨迹命令缺少 mail_no")
            return TrackingCommand(mail_no=slots.mail_no)
        if (
            intent is Intent.DELIVERY_TIME
            and isinstance(slots, DeliveryTimeSlots)
        ):
            if slots.origin is None or slots.destination is None:
                raise ValueError("时限命令缺少起止地区")
            return DeliveryTimeCommand(
                origin=slots.origin,
                destination=slots.destination,
            )
        if intent is Intent.POSTAGE and isinstance(slots, PostageSlots):
            if (
                slots.origin is None
                or slots.destination is None
                or slots.weight is None
                or slots.weight.value is None
            ):
                raise ValueError("资费命令缺少起止地区或重量")
            return PostageCommand(
                origin=slots.origin,
                destination=slots.destination,
                weight=slots.weight,
                product_code=slots.product_code,
            )
        raise ValueError(f"尚未实现意图命令: {intent.value}")

    def _required_input(
        self,
        name: str,
        *,
        confirmation_required: bool = False,
    ) -> RequiredInput:
        confirmation_hint = (
            "；确认覆盖时请提交 confirm_overwrite=true"
            if confirmation_required
            else ""
        )
        if name in PRICE_SLOT_LABELS:
            return price_required_input(name, confirmation_hint=confirmation_hint)
        if name == "product_code" and self._postage_preflight is not None:
            return RequiredInput(
                name=name, label="询价产品", type="choice",
                choices=self._postage_preflight.product_choices,
                validation_hint="请选择目录中的产品；产品变更需要确认" + confirmation_hint,
            )
        if name == "mail_no":
            return RequiredInput(
                name="mail_no",
                label="邮件号",
                validation_hint=(
                    "格式以轨迹接口最终契约为准"
                    f"{confirmation_hint}"
                ),
            )
        if name in {"origin", "destination"}:
            return RequiredInput(
                name=name,
                label="寄件地区" if name == "origin" else "收件地区",
                type="region",
                validation_hint=(
                    "请输入省市县，歧义地名需补充上级地区"
                    f"{confirmation_hint}"
                ),
            )
        if name == "weight":
            return RequiredInput(
                name="weight",
                label="重量",
                type="number",
                validation_hint=(
                    "请包含单位，例如 2.5 公斤或 500 克"
                    f"{confirmation_hint}"
                ),
            )
        return RequiredInput(
            name=name,
            label=name,
            validation_hint=confirmation_hint.lstrip("；"),
        )

    @staticmethod
    def _clarification_prompt(
        missing_slots: list[str],
        *,
        conflict_slots: set[str] | None = None,
    ) -> str:
        labels = {
            **PRICE_SLOT_LABELS,
            "origin": "寄件地区",
            "destination": "收件地区",
            "weight": "重量",
            "mail_no": "邮件号",
            "product_code": "询价产品",
        }
        conflicts = conflict_slots or set()
        if conflicts:
            changed = "、".join(
                labels.get(name, name)
                for name in missing_slots
                if name in conflicts
            )
            return f"检测到已确认的{changed}发生变化，请确认是否覆盖。"
        if missing_slots == ["mail_no"]:
            return "请提供邮件号。"
        if all(name in labels for name in missing_slots):
            return "请补充" + "、".join(
                labels[name] for name in missing_slots
            ) + "。"
        return "请补充查询所需信息。"

    @staticmethod
    def _failure(
        category: FailureCategory,
        code: str,
        message: str,
    ) -> WorkflowDecision:
        failure = AgentFailure(
            category=category,
            code=code,
            message=message,
        )
        return WorkflowDecision(
            action=RespondAction(),
            failure=failure,
        )
