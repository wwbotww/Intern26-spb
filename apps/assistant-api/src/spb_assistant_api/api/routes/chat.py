from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from ...domain.exceptions import (
    ToolContractError,
    ToolUnavailableError,
)
from ...domain.models import QueryMode, ToolResult, ToolStatus
from ...services.dispatcher import QueryDispatcher
from ..schemas import ChatRequest, ChatResponse, evidence_from_domain


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["assistant"])

FINISH_REASONS: dict[ToolStatus, str] = {
    ToolStatus.SUCCESS: "stop",
    ToolStatus.PARTIAL: "partial",
    ToolStatus.NEED_MORE_INFO: "insufficient_information",
    ToolStatus.NO_MATCH: "no_match",
    ToolStatus.ERROR: "tool_error",
}
STATUS_MESSAGES: dict[QueryMode, str] = {
    QueryMode.POLICY: "正在查询政策资料",
    QueryMode.DEVICE_PRICE: "正在查询设备参考价格",
}


def encode_sse(event: str, data: dict) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )


async def _dispatch(
    request: Request,
    payload: ChatRequest,
) -> ToolResult:
    dispatcher: QueryDispatcher = request.app.state.dispatcher
    async with request.app.state.capacity:
        return await dispatcher.dispatch(
            mode=payload.mode,
            question=payload.question,
        )


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _raise_non_stream_error(exc: Exception) -> None:
    if isinstance(exc, ToolUnavailableError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "tool_unavailable",
                "message": "所选查询能力暂不可用",
            },
        ) from exc
    if isinstance(exc, ToolContractError):
        logger.exception("tool contract violation")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "tool_contract_failed",
                "message": "查询工具返回了无效结果",
            },
        ) from exc
    logger.exception("tool execution failed")
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "code": "tool_failed",
            "message": "查询工具执行失败",
        },
    ) from exc


async def _stream_events(
    request: Request,
    payload: ChatRequest,
) -> AsyncIterator[str]:
    request_id = _request_id(request)
    yield encode_sse(
        "status",
        {
            "stage": "retrieving",
            "mode": payload.mode.value,
            "message": STATUS_MESSAGES[payload.mode],
        },
    )
    try:
        result = await _dispatch(request, payload)
        if result.status is ToolStatus.ERROR:
            yield encode_sse(
                "error",
                {
                    "request_id": request_id,
                    "code": "tool_failed",
                    "message": result.answer or "查询工具执行失败",
                },
            )
            return
        evidence = [
            evidence_from_domain(item).model_dump(mode="json")
            for item in result.evidence
        ]
        yield encode_sse("evidence", {"items": evidence})
        if result.answer:
            yield encode_sse("delta", {"content": result.answer})
        if result.usage:
            yield encode_sse("usage", dict(result.usage))
        yield encode_sse(
            "done",
            {
                "request_id": request_id,
                "mode": payload.mode.value,
                "used_tool": result.tool,
                "finish_reason": FINISH_REASONS[result.status],
                "reason_code": result.reason_code,
                "warnings": list(result.warnings),
                "missing_fields": list(result.missing_fields),
            },
        )
    except asyncio.CancelledError:
        raise
    except ToolUnavailableError:
        yield encode_sse(
            "error",
            {
                "request_id": request_id,
                "code": "tool_unavailable",
                "message": "所选查询能力暂不可用",
            },
        )
    except ToolContractError:
        logger.exception("streaming tool contract violation")
        yield encode_sse(
            "error",
            {
                "request_id": request_id,
                "code": "tool_contract_failed",
                "message": "查询工具返回了无效结果",
            },
        )
    except Exception:
        logger.exception("streaming tool execution failed")
        yield encode_sse(
            "error",
            {
                "request_id": request_id,
                "code": "tool_failed",
                "message": "查询工具执行失败",
            },
        )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    payload: ChatRequest,
) -> ChatResponse | StreamingResponse:
    if payload.stream:
        return StreamingResponse(
            _stream_events(request, payload),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        result = await _dispatch(request, payload)
    except Exception as exc:
        _raise_non_stream_error(exc)
        raise AssertionError("unreachable")
    if result.status is ToolStatus.ERROR:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "tool_failed",
                "message": result.answer or "查询工具执行失败",
            },
        )
    return ChatResponse.from_domain(
        request_id=_request_id(request),
        mode=payload.mode,
        result=result,
        finish_reason=FINISH_REASONS[result.status],
    )
