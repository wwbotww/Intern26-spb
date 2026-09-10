"""Short-lived anonymous browser identity, separate from proxy service auth.

The opaque HttpOnly cookie is a bearer credential, not a login or consent proof.
Only a separately authenticated gateway may use it. No caller-provided owner is
trusted; cookies are origin-bound, versioned, bounded and HMAC authenticated.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from ipaddress import IPv4Address, IPv4Network
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit


SESSION_PATH = "/v2/agent/browser-session"
SESSION_REF_HEADER = "x-agent-session"
_TOKEN = re.compile(r"1\.([0-9]{1,12})\.([0-9]{1,12})\.([A-Za-z0-9_-]{43})\.([A-Za-z0-9_-]{43})", re.ASCII)


def private_http_origin(origin: str) -> bool:
    """Explicit IPv4 intranet/loopback allowlist; no DNS or public HTTP."""
    try:
        parsed = urlsplit(origin)
        address = IPv4Address(parsed.hostname or "")
        port = parsed.port
        return (
            parsed.scheme == "http" and not any((parsed.username, parsed.password, parsed.path, parsed.query, parsed.fragment))
            and origin == f"http://{address}" + (f":{port}" if port is not None else "")
            and (port is None or 1 <= port <= 65535)
            and any(address in IPv4Network(network) for network in (
                "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
            ))
        )
    except ValueError:
        return False


class BrowserSessionError(ValueError):
    def __init__(self, code: str, message: str, status: int = 401):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class BrowserSessionConfig:
    public_origin: str
    proxy_api_key: str = field(repr=False)
    signing_key: str = field(repr=False)
    previous_signing_key: str = field(default="", repr=False)
    secure: bool = True
    private_http_enabled: bool = False
    ttl_seconds: int = 1800

    def __post_init__(self):
        parsed = urlsplit(self.public_origin)
        if (
            parsed.scheme not in {"https", "http"} or not parsed.hostname
            or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in self.public_origin)
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
            or self.public_origin != f"{parsed.scheme}://{parsed.netloc}"
        ):
            raise ValueError("浏览器公开 origin 必须是无路径、凭据或查询参数的精确 HTTP(S) origin")
        # Intranet HTTP is a separate explicit exception, never an inferred downgrade.
        if self.private_http_enabled:
            if self.secure or not private_http_origin(self.public_origin):
                raise ValueError("内网 HTTP 模式只接受明确的私有 IPv4 origin 和非 Secure cookie")
        elif not self.secure:
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("非 Secure cookie 仅允许明确的本机 HTTP origin")
        elif parsed.scheme != "https":
            raise ValueError("Secure 浏览器会话必须配置 HTTPS origin")
        try:
            parsed.port
        except ValueError:
            raise ValueError("浏览器 origin 端口无效") from None
        if not self.proxy_api_key.strip():
            raise ValueError("必须配置浏览器代理服务 Key")
        for key in (self.signing_key,) + ((self.previous_signing_key,) if self.previous_signing_key else ()):
            if not 32 <= len(key.encode("utf-8")) <= 512 or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in key):
                raise ValueError("浏览器签名 Key 须为 32～512 字节无空白密钥")
        if not self.signing_key or self.signing_key == self.proxy_api_key:
            raise ValueError("浏览器签名 Key 必须独立于代理 Key")
        if self.previous_signing_key in {self.proxy_api_key, self.signing_key}:
            raise ValueError("轮换旧 Key 必须与当前签名及代理 Key 不同")
        if type(self.ttl_seconds) is not int or not 60 <= self.ttl_seconds <= 86400:
            raise ValueError("浏览器会话 TTL 必须为 60～86400 秒")

    @property
    def cookie_name(self) -> str:
        if self.private_http_enabled:
            return "spb-agent-intranet"
        return "__Host-spb-agent" if self.secure else "spb-agent-local"


@dataclass(frozen=True)
class BrowserIdentity:
    session_ref: str
    issued_at: int
    expires_at: int
    token: str = field(repr=False)

    @property
    def owner_id(self) -> str:
        return "browser:" + self.session_ref


class BrowserSessionManager:
    def __init__(self, config: BrowserSessionConfig, *, clock: Callable[[], float] = time.time):
        self.config = config
        self._clock = clock

    def check_origin(self, *, origin: str | None, fetch_site: str | None, method: str) -> None:
        if fetch_site is not None and fetch_site != "same-origin":
            raise BrowserSessionError("browser_origin_rejected", "请从配置的同源 Web 入口访问", 403)
        if origin is not None and origin != self.config.public_origin:
            raise BrowserSessionError("browser_origin_rejected", "请从配置的同源 Web 入口访问", 403)
        if method not in {"GET", "HEAD"} and origin != self.config.public_origin:
            raise BrowserSessionError("browser_origin_required", "缺少有效的同源请求标识", 403)

    def _signature(self, payload: str, key: str) -> str:
        signed = (self.config.public_origin + "\n" + payload).encode("utf-8")
        return base64.urlsafe_b64encode(hmac.digest(key.encode("utf-8"), signed, "sha256")).decode("ascii").rstrip("=")

    def _identity(self, payload: str, issued: int, expires: int, sid: str, *, token: str | None = None) -> BrowserIdentity:
        ref = hashlib.sha256(("browser-v1\n" + self.config.public_origin + "\n" + sid).encode()).hexdigest()
        return BrowserIdentity(ref, issued, expires, token or payload + "." + self._signature(payload, self.config.signing_key))

    def mint(self) -> BrowserIdentity:
        now = int(self._clock())
        expires = now + self.config.ttl_seconds
        sid = secrets.token_urlsafe(32)
        return self._identity(f"1.{now}.{expires}.{sid}", now, expires, sid)

    def verify_cookie(self, cookie_header: str | None) -> BrowserIdentity:
        if not cookie_header:
            raise BrowserSessionError("browser_session_required", "请先建立浏览器访客会话")
        if len(cookie_header) > 8192:
            raise BrowserSessionError("browser_session_invalid", "浏览器访客会话无效，请重建会话")
        values = []
        for part in cookie_header.split(";"):
            name, separator, value = part.strip().partition("=")
            if name == self.config.cookie_name and separator:
                values.append(value)
        if not values:
            raise BrowserSessionError("browser_session_required", "请先建立浏览器访客会话")
        if len(values) != 1 or not (match := _TOKEN.fullmatch(values[0])):
            raise BrowserSessionError("browser_session_invalid", "浏览器访客会话无效，请重建会话")
        issued_text, expires_text, sid, signature = match.groups()
        issued, expires = int(issued_text), int(expires_text)
        payload = values[0].rsplit(".", 1)[0]
        valid = False
        for key in (self.config.signing_key, self.config.previous_signing_key):
            if key:
                valid |= hmac.compare_digest(signature, self._signature(payload, key))
        if not valid or str(issued) != issued_text or str(expires) != expires_text or not 0 < expires - issued <= self.config.ttl_seconds:
            raise BrowserSessionError("browser_session_invalid", "浏览器访客会话无效，请重建会话")
        now = int(self._clock())
        if issued > now + 30:
            raise BrowserSessionError("browser_session_invalid", "浏览器访客会话时间无效")
        if expires <= now:
            raise BrowserSessionError("browser_session_expired", "浏览器访客会话已过期，请重建会话")
        return self._identity(payload, issued, expires, sid, token=values[0])

    def establish(self, cookie_header: str | None, *, reset: bool = False) -> tuple[BrowserIdentity, bool]:
        """Identity plus whether to issue a cookie. Verification never rewrites it.

        A delayed old-tab bootstrap must not overwrite a newer reset cookie.
        Previous signing keys accept old credentials only until absolute expiry.
        """
        if reset:
            return self.mint(), True
        try:
            return self.verify_cookie(cookie_header), False
        except BrowserSessionError as error:
            if error.code == "browser_session_required":
                return self.mint(), True
            raise

    @staticmethod
    def check_reference(identity: BrowserIdentity, reference: str | None) -> None:
        if reference is None or not re.fullmatch(r"[a-f0-9]{64}", reference):
            raise BrowserSessionError("browser_session_reference_required", "请先核验当前浏览器访客身份", 409)
        if not hmac.compare_digest(reference, identity.session_ref):
            raise BrowserSessionError("browser_session_changed", "浏览器访客身份已变化，请重新核验后开始新会话", 409)
