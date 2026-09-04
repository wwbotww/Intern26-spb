from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from collections.abc import AsyncIterator, Mapping
from time import perf_counter
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from ... import __version__
from ...domain.agent_actions import RequiredInput
from ...domain.agent_errors import AgentOperationError
from ...domain.failures import FailureCategory
from ...domain.intents import Intent
from ...observability.agent_trace import AgentRunTrace
from ..agent_contracts import AgentApiDependencies, PUBLIC_AGENT_SLOTS
from ..agent_schemas import (
    AgentCapability,
    AgentErrorEnvelope,
    AgentHealthResponse,
    AgentMessageRequest,
    AgentResponse,
    AgentStreamDeltaEvent,
    AgentStreamDoneEvent,
    AgentStreamErrorEvent,
    AgentStreamInputRequiredEvent,
    AgentStreamResultEvent,
    AgentStreamStateEvent,
    AgentStreamStatusEvent,
    RequiredInputResponse,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v2/agent", tags=["assistant-agent-v2"])

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        pattern=r"^[\x21-\x7E]+$",
    ),
]

_DISPLAY_NAMES: dict[Intent, str] = {
    Intent.POLICY: "政策查询",
    Intent.DEVICE_PRICE: "设备价格查询",
    Intent.TRACKING: "邮件轨迹查询",
    Intent.DELIVERY_TIME: "寄递时限查询",
    Intent.POSTAGE: "邮费试算",
}
_REQUIRED_INPUTS: dict[str, RequiredInput] = {
    "question": RequiredInput(
        name="question",
        label="查询问题",
        type="string",
        validation_hint="请描述需要查询的内容",
    ),
    "mail_no": RequiredInput(
        name="mail_no",
        label="邮件号",
        type="string",
        validation_hint="当前支持 13 位数字邮件号",
    ),
    "origin": RequiredInput(
        name="origin",
        label="寄件地区",
        type="region",
        validation_hint="请提供省/市名称",
    ),
    "destination": RequiredInput(
        name="destination",
        label="收件地区",
        type="region",
        validation_hint="请提供省/市名称",
    ),
    "weight": RequiredInput(
        name="weight",
        label="重量",
        type="number",
        validation_hint="请提供大于 0 的克或千克重量",
    ),
}
_DECLARED_SLOTS: dict[Intent, tuple[str, ...]] = {
    Intent.POLICY: ("question",),
    Intent.DEVICE_PRICE: ("question",),
    Intent.TRACKING: ("mail_no",),
    Intent.DELIVERY_TIME: ("origin", "destination"),
    Intent.POSTAGE: ("origin", "destination", "weight"),
}
_MESSAGE_ERROR_RESPONSES = {
    404: {
        "model": AgentErrorEnvelope,
        "description": "Conversation unavailable",
    },
    409: {
        "model": AgentErrorEnvelope,
        "description": "Workflow conflict",
    },
    422: {
        "model": AgentErrorEnvelope,
        "description": "Invalid Agent request",
    },
    502: {
        "model": AgentErrorEnvelope,
        "description": "Output contract failure",
    },
    503: {
        "model": AgentErrorEnvelope,
        "description": "Dependency unavailable",
    },
    504: {
        "model": AgentErrorEnvelope,
        "description": "Workflow timeout",
    },
}

_SSE_RESPONSE = {
    "description": "Workflow completed, paused, or streamed as V2 events",
    "content": {
        "application/json": {
            "schema": {"$ref": "#/components/schemas/AgentResponse"}
        },
        "text/event-stream": {
            "schema": {
                "type": "string",
                "description": (
                    "Versioned events: status, state, input_required or result, "
                    "optional delta, then done; error is terminal."
                ),
            }
        },
    },
}
_DELETE_ERROR_RESPONSES = {
    404: {
        "model": AgentErrorEnvelope,
        "description": "Conversation unavailable",
    },
    409: {
        "model": AgentErrorEnvelope,
        "description": "Workflow conflict",
    },
    503: {
        "model": AgentErrorEnvelope,
        "description": "Persistence unavailable",
    },
    504: {
        "model": AgentErrorEnvelope,
        "description": "Deletion timeout",
    },
}


def _dependencies(request: Request) -> AgentApiDependencies:
    dependencies = getattr(request.app.state, "agent_api", None)
    if not isinstance(dependencies, AgentApiDependencies):
        raise RuntimeError("V2 Agent API 未装配")
    return dependencies


def _readiness_state(value: object) -> str:
    return (
        value
        if isinstance(value, str)
        and value in {"ready", "not_ready", "degraded"}
        else "not_ready"
    )


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _owner_id(request: Request) -> str:
    return str(getattr(request.state, "client_id", "unknown"))


def _creation_request_hash(payload: AgentMessageRequest) -> str:
    encoded = json.dumps(
        {
            "message": payload.message,
            "explicit_intent": (
                payload.explicit_intent.value
                if payload.explicit_intent is not None
                else None
            ),
            "confirm_overwrite": payload.confirm_overwrite,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _public_message(category: FailureCategory, code: str) -> str:
    if code == "conversation_not_available":
        return "会话不存在或不可访问"
    if code == "conversation_expired":
        return "会话已过期，请重新开始"
    if category is FailureCategory.STATE_CONFLICT:
        return "会话状态已变化，请刷新后重试"
    if category in {
        FailureCategory.INVALID_INPUT,
        FailureCategory.MISSING_INPUT,
        FailureCategory.AMBIGUOUS_INTENT,
    }:
        return "请求内容无法用于当前会话状态"
    if category in {
        FailureCategory.UPSTREAM_TIMEOUT,
        FailureCategory.UPSTREAM_RATE_LIMITED,
        FailureCategory.UPSTREAM_UNAVAILABLE,
    }:
        return "查询依赖暂时不可用，请稍后重试"
    if category in {
        FailureCategory.PERSISTENCE_UNAVAILABLE,
        FailureCategory.STATE_SCHEMA_INCOMPATIBLE,
    }:
        return "会话状态服务暂时不可用"
    if category is FailureCategory.CONTRACT_VIOLATION:
        return "查询结果未通过服务契约校验"
    if category is FailureCategory.LOOP_BUDGET_EXCEEDED:
        return "本轮查询达到安全执行上限"
    return "Agent 请求未能安全完成"


def _status_code(category: FailureCategory, code: str) -> int:
    if code == "conversation_not_available":
        return status.HTTP_404_NOT_FOUND
    if category is FailureCategory.STATE_CONFLICT:
        return status.HTTP_409_CONFLICT
    if category in {
        FailureCategory.INVALID_INPUT,
        FailureCategory.MISSING_INPUT,
        FailureCategory.AMBIGUOUS_INTENT,
    }:
        return status.HTTP_422_UNPROCESSABLE_CONTENT
    if category in {
        FailureCategory.UPSTREAM_TIMEOUT,
        FailureCategory.UPSTREAM_RATE_LIMITED,
        FailureCategory.UPSTREAM_UNAVAILABLE,
        FailureCategory.PERSISTENCE_UNAVAILABLE,
        FailureCategory.STATE_SCHEMA_INCOMPATIBLE,
    }:
        return status.HTTP_503_SERVICE_UNAVAILABLE
    if category is FailureCategory.CONTRACT_VIOLATION:
        return status.HTTP_502_BAD_GATEWAY
    if category is FailureCategory.LOOP_BUDGET_EXCEEDED:
        return status.HTTP_409_CONFLICT
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def _raise_agent_error(request: Request, error: AgentOperationError) -> None:
    status_code, detail, headers = _agent_error_detail(request, error)
    raise HTTPException(
        status_code=status_code,
        detail=detail,
        headers=headers,
    ) from error


def _agent_error_detail(
    request: Request,
    error: AgentOperationError,
) -> tuple[int, dict[str, Any], dict[str, str] | None]:
    failure = error.failure
    headers = None
    if failure.retry_after_seconds is not None:
        headers = {
            "Retry-After": str(max(1, math.ceil(failure.retry_after_seconds)))
        }
    return (
        _status_code(failure.category, failure.code),
        {
            "code": failure.code,
            "message": _public_message(failure.category, failure.code),
            "request_id": _request_id(request),
            "category": failure.category.value,
            "retryable": failure.retryable,
            "retry_after_seconds": failure.retry_after_seconds,
        },
        headers,
    )


def _raise_internal_error(
    request: Request,
    *,
    code: str,
    message: str,
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
            "request_id": _request_id(request),
            "retryable": False,
        },
    )


async def _execute_agent_message(
    *,
    request: Request,
    payload: AgentMessageRequest,
    idempotency_key: str,
    dependencies: AgentApiDependencies,
) -> AgentResponse:
    owner_id = _owner_id(request)
    conversation_id = payload.conversation_id
    async with asyncio.timeout(dependencies.run_timeout_seconds):
        if conversation_id is None:
            metadata = (
                await dependencies.service.create_conversation_idempotently(
                    owner_id=owner_id,
                    idempotency_key=idempotency_key,
                    request_hash=_creation_request_hash(payload),
                )
            )
            conversation_id = metadata.conversation_id
        async with request.app.state.capacity:
            output = await dependencies.service.send_message(
                conversation_id=conversation_id,
                owner_id=owner_id,
                idempotency_key=idempotency_key,
                message=payload.message,
                explicit_intent=payload.explicit_intent,
                confirm_overwrite=payload.confirm_overwrite,
            )
    return AgentResponse.from_runtime(
        request_id=_request_id(request),
        output=output,
    )


def _record_agent_response(
    request: Request,
    *,
    response: AgentResponse,
    transport: str,
    started: float,
) -> None:
    duration = perf_counter() - started
    intent = response.intent.value if response.intent else "unknown"
    interrupt_reason = (
        response.next_action.value
        if response.phase.value == "waiting_user"
        else None
    )
    failure_category = (
        response.failure.category.value if response.failure else None
    )
    request.app.state.metrics.observe_agent_run(
        transport=transport,
        outcome=response.phase.value,
        intent=intent,
        duration_seconds=duration,
        interrupt_reason=interrupt_reason,
        failure_category=failure_category,
    )
    result = response.result
    failure = response.failure
    AgentRunTrace(
        transport=transport,
        outcome=response.phase.value,
        duration_seconds=duration,
        conversation_id=response.conversation_id,
        turn_id=response.turn_id,
        phase=response.phase.value,
        intent=intent,
        next_action=response.next_action.value,
        required_input_names=tuple(
            item.name
            for item in response.required_inputs
            if item.name in PUBLIC_AGENT_SLOTS
        ),
        result_type=result.type if result else None,
        result_status=result.status.value if result else None,
        reason_code=result.reason_code if result else None,
        failure_category=(failure.category.value if failure else None),
        failure_code=failure.code if failure else None,
        retryable=failure.retryable if failure else None,
        warning_count=len(response.warnings),
    ).log()


def _record_agent_failure(
    request: Request,
    *,
    payload: AgentMessageRequest,
    transport: str,
    started: float,
    category: str,
    code: str,
    retryable: bool,
    outcome: str = "failed",
) -> None:
    duration = perf_counter() - started
    intent = (
        payload.explicit_intent.value
        if payload.explicit_intent is not None
        else "unknown"
    )
    request.app.state.metrics.observe_agent_run(
        transport=transport,
        outcome=outcome,
        intent=intent,
        duration_seconds=duration,
        failure_category=category,
    )
    AgentRunTrace(
        transport=transport,
        outcome=outcome,
        duration_seconds=duration,
        conversation_id=payload.conversation_id,
        intent=intent,
        failure_category=category,
        failure_code=code,
        retryable=retryable,
    ).log()


def _encode_sse(event: str, payload: BaseModel) -> str:
    data = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"event: {event}\ndata: {data}\n\n"


def _stream_error(
    request: Request,
    *,
    code: str,
    message: str,
    http_status: int,
    category: FailureCategory | None = None,
    retryable: bool = False,
    retry_after_seconds: float | None = None,
) -> str:
    return _encode_sse(
        "error",
        AgentStreamErrorEvent(
            request_id=_request_id(request),
            code=code,
            message=message,
            http_status=http_status,
            category=category,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
        ),
    )


async def _stream_agent_events(
    *,
    request: Request,
    payload: AgentMessageRequest,
    idempotency_key: str,
    dependencies: AgentApiDependencies,
) -> AsyncIterator[str]:
    request_id = _request_id(request)
    started = perf_counter()
    recorded = False
    request.app.state.metrics.agent_runs_in_flight.inc()
    try:
        yield _encode_sse(
            "status",
            AgentStreamStatusEvent(
                request_id=request_id,
                message="Agent 已接收请求，正在推进工作流",
            ),
        )
        response = await _execute_agent_message(
            request=request,
            payload=payload,
            idempotency_key=idempotency_key,
            dependencies=dependencies,
        )
        _record_agent_response(
            request,
            response=response,
            transport="sse",
            started=started,
        )
        recorded = True
        common = {
            "request_id": request_id,
            "conversation_id": response.conversation_id,
            "turn_id": response.turn_id,
        }
        yield _encode_sse(
            "state",
            AgentStreamStateEvent(
                **common,
                phase=response.phase,
                intent=response.intent,
                next_action=response.next_action,
            ),
        )
        if response.next_action.value in {
            "collect_slots",
            "clarify_intent",
        }:
            yield _encode_sse(
                "input_required",
                AgentStreamInputRequiredEvent(
                    **common,
                    required_inputs=response.required_inputs,
                ),
            )
        else:
            yield _encode_sse(
                "result",
                AgentStreamResultEvent(
                    **common,
                    result=response.result,
                    failure=response.failure,
                    warnings=response.warnings,
                ),
            )
        if response.reply:
            yield _encode_sse(
                "delta",
                AgentStreamDeltaEvent(
                    **common,
                    content=response.reply,
                ),
            )
        yield _encode_sse(
            "done",
            AgentStreamDoneEvent(response=response),
        )
    except AgentOperationError as error:
        _record_agent_failure(
            request,
            payload=payload,
            transport="sse",
            started=started,
            category=error.failure.category.value,
            code=error.failure.code,
            retryable=error.failure.retryable,
        )
        recorded = True
        http_status, detail, _ = _agent_error_detail(request, error)
        yield _stream_error(
            request,
            code=str(detail["code"]),
            message=str(detail["message"]),
            http_status=http_status,
            category=error.failure.category,
            retryable=error.failure.retryable,
            retry_after_seconds=error.failure.retry_after_seconds,
        )
    except TimeoutError:
        _record_agent_failure(
            request,
            payload=payload,
            transport="sse",
            started=started,
            category="timeout",
            code="agent_request_timeout",
            retryable=True,
        )
        recorded = True
        logger.warning("agent workflow stream timed out")
        yield _stream_error(
            request,
            code="agent_request_timeout",
            message="Agent 请求超过服务端时间预算",
            http_status=status.HTTP_504_GATEWAY_TIMEOUT,
            retryable=True,
        )
    except asyncio.CancelledError:
        if not recorded:
            _record_agent_failure(
                request,
                payload=payload,
                transport="sse",
                started=started,
                category="cancelled",
                code="agent_request_cancelled",
                retryable=True,
                outcome="cancelled",
            )
        raise
    except (ValidationError, ValueError) as error:
        if not recorded:
            _record_agent_failure(
                request,
                payload=payload,
                transport="sse",
                started=started,
                category=FailureCategory.CONTRACT_VIOLATION.value,
                code="agent_response_contract_violation",
                retryable=False,
            )
        logger.error(
            "agent stream response contract violation",
            extra={"exception_type": type(error).__name__},
        )
        yield _stream_error(
            request,
            code="agent_response_contract_violation",
            message="Agent 返回内容未通过 API 契约校验",
            http_status=status.HTTP_502_BAD_GATEWAY,
        )
    except Exception as error:
        if not recorded:
            _record_agent_failure(
                request,
                payload=payload,
                transport="sse",
                started=started,
                category="internal",
                code="agent_internal_error",
                retryable=False,
            )
        logger.error(
            "unclassified agent stream failure",
            extra={"exception_type": type(error).__name__},
        )
        yield _stream_error(
            request,
            code="agent_internal_error",
            message="Agent 请求未能安全完成",
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    finally:
        request.app.state.metrics.agent_runs_in_flight.dec()


@router.get("/capabilities", response_model=list[AgentCapability])
async def list_agent_capabilities(request: Request) -> list[AgentCapability]:
    dependencies = _dependencies(request)
    values: list[AgentCapability] = []
    for intent, display_name in _DISPLAY_NAMES.items():
        descriptor = dependencies.capabilities.get(intent)
        slot_names = (
            descriptor.required_slots
            if descriptor is not None
            else _DECLARED_SLOTS[intent]
        )
        values.append(
            AgentCapability(
                intent=intent.value,
                display_name=display_name,
                available=descriptor is not None,
                capability_version=(
                    descriptor.capability_version
                    if descriptor is not None
                    else None
                ),
                required_inputs=[
                    RequiredInputResponse.from_domain(
                        _REQUIRED_INPUTS[slot_name]
                    )
                    for slot_name in slot_names
                ],
            )
        )
    return values


@router.get(
    "/health/ready",
    response_model=AgentHealthResponse,
    responses={503: {"model": AgentHealthResponse}},
)
async def agent_ready(request: Request) -> AgentHealthResponse | Response:
    dependencies = _dependencies(request)
    checks = {"agent_api": "ready"}
    probe = dependencies.readiness_probe
    if probe is None:
        checks.update(
            {"persistence": "not_ready", "checkpoint": "not_ready"}
        )
    else:
        try:
            async with asyncio.timeout(
                min(5.0, dependencies.run_timeout_seconds)
            ):
                probed = await probe.check()
        except TimeoutError:
            logger.warning("agent readiness probe timed out")
            probed = {}
        except Exception as error:
            logger.error(
                "agent readiness probe failed",
                extra={"exception_type": type(error).__name__},
            )
            probed = {}
        if not isinstance(probed, Mapping):
            probed = {}
        checks.update(
            {
                "persistence": _readiness_state(
                    probed.get("persistence")
                ),
                "checkpoint": _readiness_state(
                    probed.get("checkpoint")
                ),
            }
        )

    scheduler = getattr(
        request.app.state,
        "agent_janitor_scheduler",
        None,
    )
    if dependencies.janitor is None:
        checks["janitor"] = "disabled"
    elif scheduler is None:
        checks["janitor"] = "starting"
    else:
        checks["janitor"] = scheduler.readiness

    for intent in _DISPLAY_NAMES:
        checks[f"capability.{intent.value}"] = (
            "ready" if intent in dependencies.capabilities else "disabled"
        )

    critical_ready = (
        checks["agent_api"] == "ready"
        and checks["persistence"] == "ready"
        and checks["checkpoint"] == "ready"
        and any(
            checks[f"capability.{intent.value}"] == "ready"
            for intent in _DISPLAY_NAMES
        )
    )
    metrics = request.app.state.metrics
    for component in ("agent_api", "persistence", "checkpoint", "janitor"):
        metrics.set_agent_readiness(
            component=component,
            ready=checks[component] == "ready",
        )
    for intent in _DISPLAY_NAMES:
        metrics.set_agent_readiness(
            component=f"capability_{intent.value}",
            ready=checks[f"capability.{intent.value}"] == "ready",
        )

    degraded = checks["janitor"] in {"degraded", "starting"}
    response = AgentHealthResponse(
        status=(
            "not_ready"
            if not critical_ready
            else "degraded" if degraded else "ok"
        ),
        service="spb-assistant-agent-v2",
        version=__version__,
        phase=4,
        checks=checks,
    )
    if not critical_ready:
        return Response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response.model_dump_json(),
            media_type="application/json",
        )
    return response


@router.post(
    "/messages",
    response_model=AgentResponse,
    responses={200: _SSE_RESPONSE, **_MESSAGE_ERROR_RESPONSES},
)
async def send_agent_message(
    request: Request,
    payload: AgentMessageRequest,
    idempotency_key: IdempotencyKey,
) -> AgentResponse | StreamingResponse:
    dependencies = _dependencies(request)
    if payload.conversation_id is None:
        if payload.message is None:
            _raise_internal_error(
                request,
                code="message_required_for_new_conversation",
                message="创建会话时必须提供 message",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

    if payload.stream:
        return StreamingResponse(
            _stream_agent_events(
                request=request,
                payload=payload,
                idempotency_key=idempotency_key,
                dependencies=dependencies,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    started = perf_counter()
    request.app.state.metrics.agent_runs_in_flight.inc()
    try:
        response = await _execute_agent_message(
            request=request,
            payload=payload,
            idempotency_key=idempotency_key,
            dependencies=dependencies,
        )
        _record_agent_response(
            request,
            response=response,
            transport="json",
            started=started,
        )
        return response
    except AgentOperationError as error:
        _record_agent_failure(
            request,
            payload=payload,
            transport="json",
            started=started,
            category=error.failure.category.value,
            code=error.failure.code,
            retryable=error.failure.retryable,
        )
        _raise_agent_error(request, error)
    except TimeoutError as error:
        _record_agent_failure(
            request,
            payload=payload,
            transport="json",
            started=started,
            category="timeout",
            code="agent_request_timeout",
            retryable=True,
        )
        logger.warning("agent workflow request timed out")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "code": "agent_request_timeout",
                "message": "Agent 请求超过服务端时间预算",
                "request_id": _request_id(request),
                "retryable": True,
            },
        ) from error
    except asyncio.CancelledError:
        _record_agent_failure(
            request,
            payload=payload,
            transport="json",
            started=started,
            category="cancelled",
            code="agent_request_cancelled",
            retryable=True,
            outcome="cancelled",
        )
        raise
    except (ValidationError, ValueError) as error:
        _record_agent_failure(
            request,
            payload=payload,
            transport="json",
            started=started,
            category=FailureCategory.CONTRACT_VIOLATION.value,
            code="agent_response_contract_violation",
            retryable=False,
        )
        logger.error(
            "agent response contract violation",
            extra={"exception_type": type(error).__name__},
        )
        _raise_internal_error(
            request,
            code="agent_response_contract_violation",
            message="Agent 返回内容未通过 API 契约校验",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
        raise AssertionError("unreachable") from error
    except Exception as error:
        _record_agent_failure(
            request,
            payload=payload,
            transport="json",
            started=started,
            category="internal",
            code="agent_internal_error",
            retryable=False,
        )
        logger.error(
            "unclassified agent API failure",
            extra={"exception_type": type(error).__name__},
        )
        _raise_internal_error(
            request,
            code="agent_internal_error",
            message="Agent 请求未能安全完成",
        )
        raise AssertionError("unreachable") from error
    finally:
        request.app.state.metrics.agent_runs_in_flight.dec()


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_DELETE_ERROR_RESPONSES,
)
async def delete_agent_conversation(
    request: Request,
    conversation_id: UUID,
) -> Response:
    dependencies = _dependencies(request)
    try:
        async with asyncio.timeout(dependencies.run_timeout_seconds):
            await dependencies.service.delete_conversation(
                conversation_id=conversation_id,
                owner_id=_owner_id(request),
            )
    except AgentOperationError as error:
        _raise_agent_error(request, error)
    except TimeoutError as error:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "code": "agent_delete_timeout",
                "message": "会话清理超过服务端时间预算",
                "request_id": _request_id(request),
                "retryable": True,
            },
        ) from error
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.exception("unclassified conversation deletion failure")
        _raise_internal_error(
            request,
            code="agent_delete_failed",
            message="会话暂时无法清理",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        raise AssertionError("unreachable") from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
