from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from ...domain.exceptions import (
    ChatProviderError,
    QueryTooLongError,
    RelevanceJudgeError,
    RetrievalError,
    RetrievalNotReadyError,
)
from ...domain.ports import ChatProvider, RelevanceJudge, Retriever
from ...services.chat import (
    NO_CONTEXT_ANSWER,
    GroundedChatService,
    PreparedChat,
    collect_answer,
)
from ...settings import ApiSettings
from ..schemas import ChatCitation, ChatRequest, ChatResponse


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["chat"])


def encode_sse(event: str, data: dict) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )


def _get_dependencies(
    request: Request,
) -> tuple[Retriever, ChatProvider, ApiSettings]:
    retriever: Retriever | None = getattr(
        request.app.state,
        "retriever",
        None,
    )
    provider: ChatProvider | None = getattr(
        request.app.state,
        "chat_provider",
        None,
    )
    if retriever is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "retrieval_unavailable",
                "message": "检索服务暂不可用",
            },
        )
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "chat_provider_unavailable",
                "message": "问答模型尚未配置",
            },
        )
    settings: ApiSettings = request.app.state.settings
    if (
        settings.relevance_judge_enabled
        and getattr(request.app.state, "relevance_judge", None) is None
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "relevance_gate_unavailable",
                "message": "证据相关性判定服务尚未配置",
            },
        )
    return retriever, provider, settings


def _build_service(
    *,
    retriever: Retriever,
    provider: ChatProvider,
    relevance_judge: RelevanceJudge | None,
    settings: ApiSettings,
) -> GroundedChatService:
    return GroundedChatService(
        retriever=retriever,
        provider=provider,
        relevance_judge=relevance_judge,
        max_context_chars=settings.chat_context_max_chars,
    )


async def _prepare(
    service: GroundedChatService,
    payload: ChatRequest,
    settings: ApiSettings,
) -> PreparedChat:
    try:
        query = payload.to_search_query(settings)
        return await service.prepare(
            question=payload.question,
            search_query=query,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_chat_request", "message": str(exc)},
        ) from exc
    except QueryTooLongError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "query_too_long", "message": str(exc)},
        ) from exc
    except RelevanceJudgeError as exc:
        logger.exception("relevance judge failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "relevance_gate_failed",
                "message": "证据相关性判定失败",
            },
        ) from exc
    except RetrievalNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "retrieval_unavailable",
                "message": str(exc),
            },
        ) from exc
    except RetrievalError as exc:
        logger.exception("chat retrieval failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "retrieval_failed",
                "message": "知识库检索失败",
            },
        ) from exc
    except Exception as exc:
        logger.exception("unexpected chat retrieval failure")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "retrieval_failed",
                "message": "知识库检索失败",
            },
        ) from exc


async def _stream_events(
    *,
    request: Request,
    request_id: str,
    service: GroundedChatService,
    payload: ChatRequest,
    settings: ApiSettings,
) -> AsyncIterator[str]:
    try:
        async with request.app.state.capacity:
            prepared = await _prepare(service, payload, settings)
            if prepared.judge_usage is not None:
                request.app.state.metrics.observe_relevance_judge(
                    accepted=(
                        prepared.rejection_reason != "llm_rejected"
                    ),
                    usage=prepared.judge_usage,
                    duration_seconds=prepared.judge_elapsed_seconds,
                )
            citations = [
                ChatCitation.from_domain(item).model_dump()
                for item in prepared.citations
            ]
            yield encode_sse(
                "metadata",
                {
                    "request_id": request_id,
                    "model": service.model,
                    "citations": citations,
                },
            )
            if not prepared.citations:
                yield encode_sse(
                    "delta",
                    {"content": NO_CONTEXT_ANSWER},
                )
                yield encode_sse(
                    "done",
                    {
                        "request_id": request_id,
                        "finish_reason": (
                            prepared.rejection_reason or "no_context"
                        ),
                    },
                )
                return

            saw_done = False
            async for event in service.stream(prepared):
                if event.event == "keepalive":
                    yield ": keep-alive\n\n"
                    continue
                data = dict(event.data)
                if event.event == "usage":
                    request.app.state.metrics.observe_tokens(data)
                if event.event == "done":
                    data["request_id"] = request_id
                    saw_done = True
                yield encode_sse(event.event, data)
            if not saw_done:
                yield encode_sse(
                    "done",
                    {
                        "request_id": request_id,
                        "finish_reason": "stop",
                    },
                )
    except asyncio.CancelledError:
        raise
    except HTTPException as exc:
        yield encode_sse(
            "error",
            {
                "request_id": request_id,
                "code": (
                    exc.detail.get("code", "chat_failed")
                    if isinstance(exc.detail, dict)
                    else "chat_failed"
                ),
                "message": (
                    exc.detail.get("message", "问答失败")
                    if isinstance(exc.detail, dict)
                    else "问答失败"
                ),
            },
        )
    except ChatProviderError:
        logger.exception("streaming chat provider failed")
        yield encode_sse(
            "error",
            {
                "request_id": request_id,
                "code": "chat_provider_failed",
                "message": "问答模型调用失败",
            },
        )
    except Exception:
        logger.exception("unexpected streaming chat failure")
        yield encode_sse(
            "error",
            {
                "request_id": request_id,
                "code": "chat_failed",
                "message": "问答服务异常",
            },
        )


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def chat(
    payload: ChatRequest,
    request: Request,
) -> ChatResponse | StreamingResponse:
    retriever, provider, settings = _get_dependencies(request)
    service = _build_service(
        retriever=retriever,
        provider=provider,
        relevance_judge=getattr(
            request.app.state,
            "relevance_judge",
            None,
        ),
        settings=settings,
    )
    request_id = request.state.request_id
    if payload.stream:
        return StreamingResponse(
            _stream_events(
                request=request,
                request_id=request_id,
                service=service,
                payload=payload,
                settings=settings,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    async with request.app.state.capacity:
        prepared = await _prepare(service, payload, settings)
        if prepared.judge_usage is not None:
            request.app.state.metrics.observe_relevance_judge(
                accepted=prepared.rejection_reason != "llm_rejected",
                usage=prepared.judge_usage,
                duration_seconds=prepared.judge_elapsed_seconds,
            )
        if prepared.citations:
            try:
                answer, usage, finish_reason = await collect_answer(
                    service,
                    prepared,
                )
                request.app.state.metrics.observe_tokens(usage)
            except ChatProviderError as exc:
                logger.exception("chat provider failed")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "code": "chat_provider_failed",
                        "message": "问答模型调用失败",
                    },
                ) from exc
        else:
            answer = NO_CONTEXT_ANSWER
            usage = {}
            finish_reason = prepared.rejection_reason or "no_context"
    return ChatResponse(
        request_id=request_id,
        model=service.model,
        answer=answer,
        citations=[
            ChatCitation.from_domain(item)
            for item in prepared.citations
        ],
        usage=usage,
        finish_reason=finish_reason,
    )
