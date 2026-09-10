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
            "postage_policy_snapshot": None,
            "postage_review_fingerprint": None,
            "postage_confirmed_fingerprint": None,
            "postage_requirements": [],
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
    postage_replies = {
        "postage_scope_not_supported": "当前仅支持国内指定产品、按实重且无增值服务的基础询价。此次条件涉及未支持的范围，未生成报价；请确认条件后重新查询。",
        "postage_weight_not_supported": "重量须为本地上限内、可精确换算成整数克的正数；不会自动取整。请修正后重新查询。",
        "postage_context_changed_restart": "资费目录或报价口径已变化，或旧查询尚未核验条件。为避免错价，请重新发起查询。",
        "postage_quote_manual_required": "该产品需要人工询价，本次未生成报价；请联系寄递服务方确认。",
        "postage_quote_weight_rejected": "计费服务未接受此次重量，本次未生成报价；请核对条件后重新查询。",
        "postage_quote_product_rejected": "计费服务未接受所选产品，本次未生成报价；请确认可用产品。",
        "postage_quote_customer_eligibility": "当前询价身份不满足该产品的客户资格要求，未生成报价；请联系服务方确认。",
        "postage_quote_billing_zone_unavailable": "计费服务未提供本次条件的计费区，未生成报价；这不代表该地区一定不可寄递。",
        "postage_quote_rate_unavailable": "计费服务未提供本次条件的标准资费，未生成报价；请联系服务方确认。",
        "postage_quote_customer_history_unavailable": "本次询价所需客户统计不可用，未生成报价；请联系服务方确认。",
        "postage_quote_discount_rejected": "本次计价条件未通过服务方的折扣校验，未生成报价；请联系服务方确认。",
        "postage_quote_billing_mode_unsupported": "计费服务不支持本次计费方式，未生成报价；请联系服务方确认。",
        "postage_quote_collection_context_incomplete": "本次询价所需收寄上下文不完整，未生成报价；请联系服务方确认。",
        "postage_response_scope_unsupported": "返回结果涉及当前未支持的计泡或额外服务费用，已停止展示报价；请联系服务方核实。",
    }
    if failure.code in postage_replies:
        return postage_replies[failure.code]
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
        return "该查询服务暂不可用，请稍后再试。"
    return "暂时无法确定查询类型，请选择一个支持的查询能力。"
