from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

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
from ..domain.commands import TrackingCommand
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.intents import Intent
from ..domain.results import AgentResult
from ..domain.slots import SlotPayload, TrackingSlots
from ..domain.tooling import CommandModel, ToolDescriptor
from ..domain.understanding import ControlDirective


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


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    retry: bool


class WorkflowPolicy:
    """Pure, deterministic policy for the bounded agent workflow."""

    def __init__(
        self,
        descriptors: Mapping[Intent, ToolDescriptor],
    ) -> None:
        self._descriptors = dict(descriptors)

    def decide(self, state: Mapping[str, Any]) -> WorkflowDecision:
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

        missing_slots = list(state.get("missing_slots", []))
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

        descriptor = self._descriptors.get(intent)
        if descriptor is None:
            return WorkflowDecision(
                action=HandoffAction(reason_code="capability_not_available")
            )

        try:
            command = self._build_command(intent, state.get("slots"))
        except (TypeError, ValueError):
            return self._failure(
                FailureCategory.INVALID_INPUT,
                "validated_slots_invalid",
                "已收集参数不能生成有效命令",
            )
        fingerprint = argument_fingerprint(command)
        prior_calls = [
            ToolCallRecord.model_validate(item)
            for item in state.get("tool_calls", [])
        ]
        same_logical_call = any(
            item.argument_fingerprint == fingerprint
            for item in prior_calls
        )
        if (
            not same_logical_call
            and int(state.get("tool_call_count", 0))
            >= int(state.get("max_tool_calls", 1))
        ):
            return self._failure(
                FailureCategory.LOOP_BUDGET_EXCEEDED,
                "tool_call_budget_exceeded",
                "Agent 已达到本轮最大工具调用数",
            )

        attempt = int(state.get("retry_count", 0)) + 1
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
                tool_call_id=uuid5(
                    NAMESPACE_URL,
                    f"{conversation_id}:{fingerprint}",
                ),
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
        if intent is Intent.TRACKING and isinstance(slots, TrackingSlots):
            if slots.mail_no is None:
                raise ValueError("轨迹命令缺少 mail_no")
            return TrackingCommand(mail_no=slots.mail_no)
        raise ValueError(f"阶段 1 尚未实现意图命令: {intent.value}")

    @staticmethod
    def _required_input(
        name: str,
        *,
        confirmation_required: bool = False,
    ) -> RequiredInput:
        confirmation_hint = (
            "；确认覆盖时请提交 confirm_overwrite=true"
            if confirmation_required
            else ""
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
            "origin": "寄件地区",
            "destination": "收件地区",
            "weight": "重量",
            "mail_no": "邮件号",
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


def argument_fingerprint(command: CommandModel) -> str:
    payload = json.dumps(
        command.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
