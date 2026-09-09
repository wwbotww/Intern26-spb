from __future__ import annotations

from ...domain.agent_actions import ControlAction, HandoffAction, InvokeToolAction
from ...domain.agent_errors import AgentOperationError
from ...domain.agent_events import AgentEventType
from ...domain.failures import AgentFailure, FailureCategory
from ...domain.results import AgentResult
from ...services.result_validator import AgentResultValidator
from ..node_utils import agent_event
from ..state import AgentState


def compose_agent_response(state: AgentState) -> dict[str, object]:
    raw_error = state.get("last_error")
    if raw_error is None and state.get("last_result") is not None:
        # A historical checkpoint can resume after validate_result. Apply the
        # current invariant gate again before publishing restored facts.
        try:
            action = InvokeToolAction.model_validate(state.get("pending_action"))
            result = AgentResult.model_validate(state["last_result"])
            if result.tool != action.tool_name:
                raise ValueError("response tool identity mismatch")
            AgentResultValidator().validate(command=action.command, result=result)
        except AgentOperationError as error:
            raw_error = error.failure.model_dump(mode="json")
        except ValueError:
            raw_error = AgentFailure(
                category=FailureCategory.CONTRACT_VIOLATION,
                code="response_execution_context_invalid",
                message="回复缺少可校验的执行上下文",
            ).model_dump(mode="json")
    if raw_error is not None:
        failure = AgentFailure.model_validate(raw_error)
        return {
            "phase": "failed",
            "last_result": None,
            "last_error": failure.model_dump(mode="json"),
            "reply": _failure_reply(failure),
            "required_inputs": [],
            "result": None,
            "failure": failure.model_dump(mode="json"),
            "warnings": [],
            "finish_reason": "failed",
            "audit_events": [
                agent_event(
                    AgentEventType.RESPONSE_PREPARED,
                    node="compose_response",
                    phase="failed",
                    finish_reason="failed",
                    failure_category=failure.category.value,
                )
            ],
        }

    raw_action = state.get("pending_action")
    if raw_action is not None and raw_action.get("type") == "control":
        action = ControlAction.model_validate(raw_action)
        reply = (
            "已取消当前查询。"
            if action.directive.value == "cancel"
            else "已清空当前查询，可以重新选择能力。"
        )
        return {
            "active_intent": None,
            "candidate_intents": [],
            "multi_intent": False,
            "control": "none",
            "slots": None,
            "slot_provenance": [],
            "missing_slots": [],
            "ambiguities": [],
            "pending_query": "",
            "phase": "completed",
            "reply": reply,
            "required_inputs": [],
            "result": None,
            "failure": None,
            "warnings": [],
            "finish_reason": "stop",
            "audit_events": [
                agent_event(
                    AgentEventType.CONVERSATION_RESET,
                    node="compose_response",
                    phase="completed",
                    directive=action.directive.value,
                ),
                agent_event(
                    AgentEventType.RESPONSE_PREPARED,
                    node="compose_response",
                    phase="completed",
                    finish_reason="stop",
                ),
            ],
        }
    if raw_action is not None and raw_action.get("type") == "handoff":
        action = HandoffAction.model_validate(raw_action)
        return {
            "phase": "handoff",
            "reply": _handoff_reply(action.reason_code),
            "required_inputs": [],
            "result": None,
            "failure": None,
            "warnings": [],
            "finish_reason": "handoff",
            "audit_events": [
                agent_event(
                    AgentEventType.RESPONSE_PREPARED,
                    node="compose_response",
                    phase="handoff",
                    finish_reason="handoff",
                )
            ],
        }

    result = AgentResult.model_validate(state.get("last_result"))
    return {
        "phase": "completed",
        "reply": result.answer,
        "required_inputs": [],
        "result": result.model_dump(mode="json"),
        "failure": None,
        "warnings": result.warnings,
        "finish_reason": "stop",
        "audit_events": [
            agent_event(
                AgentEventType.RESPONSE_PREPARED,
                node="compose_response",
                phase="completed",
                finish_reason="stop",
                result_status=result.status.value,
            )
        ],
    }


def _failure_reply(failure: AgentFailure) -> str:
    if failure.category in {
        FailureCategory.UPSTREAM_TIMEOUT,
        FailureCategory.UPSTREAM_RATE_LIMITED,
        FailureCategory.UPSTREAM_UNAVAILABLE,
    }:
        return "查询服务暂时不可用，请稍后重试。"
    if failure.category is FailureCategory.CONTRACT_VIOLATION:
        return "查询结果未通过校验，已停止展示。"
    if failure.category is FailureCategory.LOOP_BUDGET_EXCEEDED:
        return "本轮查询达到安全执行上限，已停止。"
    return "本轮查询未能安全完成，请稍后重试。"


def _handoff_reply(reason_code: str) -> str:
    if reason_code == "capability_not_available":
        return "该能力尚未在 Agent 工作流中开放。"
    return "暂时无法确定查询类型，请选择一个支持的查询能力。"
