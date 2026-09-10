"""Proxy-only anonymous identity bootstrap; never accepts caller-supplied owners."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ...security.browser_session import BrowserSessionError, BrowserSessionManager, SESSION_PATH


class BrowserSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reset: bool = False


class BrowserSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    expires_at: datetime


router = APIRouter(tags=["assistant-agent-v2"])


@router.post(SESSION_PATH, response_model=BrowserSessionResponse)
async def establish_browser_session(
    payload: BrowserSessionRequest, request: Request, response: Response,
) -> BrowserSessionResponse:
    manager = getattr(request.state, "browser_session_manager", None)
    if not isinstance(manager, BrowserSessionManager):
        raise HTTPException(403, detail={"code": "browser_proxy_required", "message": "此入口仅用于浏览器代理会话"})
    try:
        identity, issue_cookie = manager.establish(request.state.browser_cookie_header, reset=payload.reset)
    except BrowserSessionError as error:
        raise HTTPException(error.status, detail={"code": error.code, "message": str(error)}) from None
    # Absolute expiry: bootstrap / signing-key rotation must not extend the session.
    expires_at = datetime.fromtimestamp(identity.expires_at, UTC)
    if issue_cookie:
        response.set_cookie(
            key=manager.config.cookie_name, value=identity.token, expires=expires_at,
            path="/", secure=manager.config.secure, httponly=True, samesite="strict",
        )
    response.headers["Cache-Control"] = "no-store"
    return BrowserSessionResponse(session_ref=identity.session_ref, expires_at=expires_at)
