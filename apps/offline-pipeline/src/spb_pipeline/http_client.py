from __future__ import annotations

import hashlib
import os
import random
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .config import Settings


RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class DownloadError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class HttpClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._last_request: dict[str, float] = defaultdict(float)
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(settings.timeout_seconds),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; SPBPolicyArchive/0.1; "
                    "+internal-research)"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _rate_limit(self, url: str) -> None:
        host = (urlsplit(url).hostname or "").lower()
        elapsed = time.monotonic() - self._last_request[host]
        remaining = self.settings.request_delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request[host] = time.monotonic()

    def get_json(
        self, url: str, params: dict[str, str | int]
    ) -> tuple[dict, httpx.Response]:
        response = self._request("GET", url, params=params)
        try:
            return response.json(), response
        except ValueError as exc:
            raise DownloadError(
                f"{url} 未返回合法 JSON", response.status_code
            ) from exc

    def _request(
        self, method: str, url: str, **kwargs: object
    ) -> httpx.Response:
        last_error: BaseException | None = None
        for attempt in range(self.settings.max_retries + 1):
            self._rate_limit(url)
            try:
                response = self.client.request(method, url, **kwargs)
                if response.status_code in RETRYABLE_STATUS:
                    raise DownloadError(
                        f"HTTP {response.status_code}", response.status_code
                    )
                response.raise_for_status()
                return response
            except (httpx.HTTPError, DownloadError) as exc:
                last_error = exc
                if attempt >= self.settings.max_retries:
                    break
                time.sleep((2**attempt) + random.uniform(0.0, 0.4))
        status_code = getattr(last_error, "status_code", None)
        raise DownloadError(f"{url}: {last_error}", status_code) from last_error

    def download(self, url: str, destination: Path) -> dict[str, object]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        response = self._request("GET", url)
        fd, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".part",
        )
        digest = hashlib.sha256()
        length = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    handle.write(chunk)
                    digest.update(chunk)
                    length += len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if length == 0:
                raise DownloadError(f"{url}: 返回了零字节文件", response.status_code)
            os.replace(temporary_name, destination)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return {
            "http_status": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "content_length": length,
            "etag": response.headers.get("etag", ""),
            "last_modified": response.headers.get("last-modified", ""),
            "sha256": digest.hexdigest(),
            "final_url": str(response.url),
        }
