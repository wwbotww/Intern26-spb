from __future__ import annotations

import logging
from time import perf_counter

from fastapi import APIRouter, HTTPException, Request, status

from ...domain.exceptions import (
    QueryTooLongError,
    RetrievalError,
    RetrievalNotReadyError,
)
from ...domain.ports import Retriever
from ...settings import ApiSettings
from ..schemas import SearchRequest, SearchResponse, SearchResult


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["retrieval"])


def _unavailable_detail(reason: str) -> dict[str, str]:
    return {
        "code": "retrieval_unavailable",
        "message": "检索服务暂不可用",
        "reason": reason,
    }


@router.post("/retrieve", response_model=SearchResponse)
async def retrieve(
    payload: SearchRequest,
    request: Request,
) -> SearchResponse:
    retriever: Retriever | None = getattr(
        request.app.state,
        "retriever",
        None,
    )
    if retriever is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_unavailable_detail(
                getattr(
                    request.app.state,
                    "initialization_error",
                    "not_initialized",
                )
            ),
        )
    settings: ApiSettings = request.app.state.settings
    try:
        query = payload.to_domain(settings)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_search_request", "message": str(exc)},
        ) from exc

    started = perf_counter()
    try:
        async with request.app.state.capacity:
            hits = await retriever.search(query)
    except QueryTooLongError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "query_too_long", "message": str(exc)},
        ) from exc
    except RetrievalNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_unavailable_detail(str(exc)),
        ) from exc
    except RetrievalError as exc:
        logger.exception("retrieval failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "retrieval_failed",
                "message": "上游检索失败",
            },
        ) from exc
    except Exception as exc:
        logger.exception("unexpected retrieval failure")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "retrieval_failed",
                "message": "上游检索失败",
            },
        ) from exc

    elapsed_ms = (perf_counter() - started) * 1000
    results = [
        SearchResult.from_domain(hit, rank)
        for rank, hit in enumerate(hits, start=1)
    ]
    return SearchResponse(
        query=query.text,
        count=len(results),
        elapsed_ms=round(elapsed_ms, 3),
        results=results,
    )
