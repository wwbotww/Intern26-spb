from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from ..domain.agent_errors import AgentOperationError
from ..domain.commands import TrackingCommand
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.results import TrackingData, TrackingEvent
from ..domain.tracking import TrackingQueryResult, TrackingSource
from ..services.circuit_breaker import CapabilityCircuitBreaker, CircuitState
from .agent_http import AgentJsonHttpClient
from .postal_tracking_contract import (
    PostalLegacySigner,
    PostalTrackingConfig,
    PostalTrackingRequest,
    PostalTrackingResponse,
)


_SERIAL_NUMBER = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_MOBILE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _failure(
    code: str,
    message: str,
    category: FailureCategory = FailureCategory.CONTRACT_VIOLATION,
) -> AgentOperationError:
    return AgentOperationError(
        AgentFailure(category=category, code=code, message=message)
    )


class PostalTrackingGateway:
    """Opt-in adapter for one read-only tracking query.

    The gateway owns its client; the composition root must call close(). No
    model, dotenv loading, protocol fallback, retry or fixture fallback is included.
    """

    def __init__(
        self,
        config: PostalTrackingConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        serial_number_factory: Callable[[], str] | None = None,
        circuit_breaker: CapabilityCircuitBreaker | None = None,
    ) -> None:
        self._config = config
        self._source = TrackingSource(
            source_type="external_api",
            source_name="postal-tracking",
            profile=config.profile,
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._serial_number = serial_number_factory or (lambda: uuid4().hex)
        self._timezone = ZoneInfo(config.timezone)
        self._signer = PostalLegacySigner(config.signing_system_id)
        self._breaker = circuit_breaker or CapabilityCircuitBreaker()
        self._closed = False
        self._client = AgentJsonHttpClient(
            base_url=config.base_url,
            timeout_seconds=config.timeout_seconds,
            max_connections=config.max_connections,
            verify_tls=True,
            max_response_bytes=config.max_response_bytes,
            manage_circuit_breaker=False,
            transport=transport,
        )

    async def query(self, command: TrackingCommand) -> TrackingQueryResult:
        if self._closed:
            raise _failure(
                "postal_tracking_closed", "邮政轨迹客户端已关闭",
                FailureCategory.UPSTREAM_UNAVAILABLE,
            )
        if not self._config.enabled:
            raise _failure(
                "postal_tracking_disabled",
                "邮政轨迹接口尚未启用",
                FailureCategory.UPSTREAM_UNAVAILABLE,
            )
        if not isinstance(command, TrackingCommand):
            raise _failure(
                "postal_tracking_command_invalid", "轨迹命令类型不正确"
            )
        try:
            request = PostalTrackingRequest(traceNo=command.mail_no)
        except ValidationError:
            raise _failure(
                "postal_tracking_mail_no_unsupported",
                "邮件号不满足当前适配器的输入约束",
                FailureCategory.INVALID_INPUT,
            ) from None

        # Local configuration/signing failures are not upstream observations.
        form = self._request_form(request)
        await self._breaker.before_call("tracking")
        try:
            response = await self._client.request_json(
                capability="tracking", method="POST", path=self._config.path,
                form_body=form,
            )
            observation = self._parse_response(command, response.payload)
        except AgentOperationError as error:
            if error.failure.category in {
                FailureCategory.CONTRACT_VIOLATION,
                FailureCategory.UPSTREAM_UNAVAILABLE,
                FailureCategory.UPSTREAM_TIMEOUT,
                FailureCategory.UPSTREAM_RATE_LIMITED,
            }:
                await asyncio.shield(self._breaker.record_failure("tracking"))
            else:
                await asyncio.shield(self._breaker.record_aborted("tracking"))
            raise
        except BaseException:
            # Cancellation/local faults must not strand a half-open probe.
            await asyncio.shield(self._breaker.record_aborted("tracking"))
            raise
        await asyncio.shield(self._breaker.record_success("tracking"))
        return observation

    def _parse_response(
        self, command: TrackingCommand, payload: object,
    ) -> TrackingQueryResult:
        try:
            wire = PostalTrackingResponse.model_validate(payload)
        except ValidationError:
            raise _failure(
                "postal_tracking_response_invalid", "轨迹接口响应不符合字段契约"
            ) from None
        if wire.receive_id != self._config.expected_response_receive_id:
            raise _failure(
                "postal_tracking_recipient_mismatch", "轨迹响应接收方不匹配"
            )
        if not wire.response_state:
            # Text such as "not found" is not a stable business error code.
            raise _failure(
                "postal_tracking_business_rejected",
                "轨迹接口未能完成查询，请联系服务方确认",
                FailureCategory.UPSTREAM_UNAVAILABLE,
            )
        if wire.response_items is None:
            raise _failure(
                "postal_tracking_items_missing", "成功响应缺少明确的轨迹列表"
            )
        if not wire.response_items:
            return TrackingQueryResult(
                data=None, source=self._source, queried_at=self._now()
            )
        if any(item.trace_no != command.mail_no for item in wire.response_items):
            raise _failure(
                "postal_tracking_mail_no_mismatch", "轨迹列表包含非本次查询的邮件"
            )

        # Project an allowlist before data can reach a tool receipt/checkpoint.
        staff_values = {
            value.strip()
            for item in wire.response_items
            for value in (item.operator_no, item.operator_name)
            if value and value.strip()
        }
        projected: list[tuple[TrackingEvent, str]] = []
        try:
            for item in wire.response_items:
                event = TrackingEvent(
                    event_code=item.op_code,
                    description=_public_text(item.op_desc, staff_values),
                    occurred_at=_local_time(item.op_time, self._timezone),
                    location=_public_text(item.op_org_name, staff_values),
                )
                projected.append((event, _public_text(item.op_name, staff_values)))
        except ValueError:
            raise _failure(
                "postal_tracking_event_invalid", "轨迹时间或节点内容无法安全解析"
            ) from None
        # Stable sort retains all equal-time events, including repeated codes.
        projected.sort(key=lambda pair: pair[0].occurred_at)
        latest_time = projected[-1][0].occurred_at
        latest_names = {
            name for event, name in projected if event.occurred_at == latest_time
        }
        latest_label = (
            next(iter(latest_names))
            if len(latest_names) == 1
            else "同一时刻有多条轨迹，请查看明细"
        )
        data = TrackingData(
            mail_no=command.mail_no,
            current_status=latest_label,
            events=[event for event, _ in projected],
            queried_at=self._now(),
        )
        return TrackingQueryResult(
            data=data, source=self._source, queried_at=data.queried_at
        )

    def _request_form(self, request: PostalTrackingRequest) -> dict[str, str]:
        now = self._now()
        serial_number = self._serial_number()
        if not isinstance(serial_number, str) or not _SERIAL_NUMBER.fullmatch(
            serial_number
        ):
            raise _failure(
                "postal_tracking_serial_invalid",
                "轨迹请求流水号生成失败",
                FailureCategory.INTERNAL_ERROR,
            )
        message_body = request.message_body()
        try:
            digest = self._signer.sign(message_body)
        except (ValueError, UnicodeError):
            raise _failure(
                "postal_tracking_signing_unavailable",
                "无法使用已配置的轨迹签名方案",
                FailureCategory.UPSTREAM_UNAVAILABLE,
            ) from None
        form = {
            "sendID": self._config.send_id,
            "proviceNo": self._config.province_no,
            "msgKind": self._config.msg_kind,
            "serialNo": serial_number,
            "sendDate": now.astimezone(self._timezone).strftime("%Y%m%d%H%M%S"),
            "receiveID": self._config.receive_id,
            "dataType": "1",
            "dataDigest": digest,
            "msgBody": message_body,
        }
        if self._config.batch_no is not None:
            form["batchNo"] = self._config.batch_no
        return form

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise _failure(
                "postal_tracking_clock_invalid",
                "轨迹查询时钟必须包含时区",
                FailureCategory.INTERNAL_ERROR,
            )
        return value.astimezone(UTC)

    async def close(self) -> None:
        self._closed = True
        await self._client.close()

    async def readiness(self) -> str:
        """Local configuration/circuit state only; never probes the provider."""
        if self._closed:
            return "not_ready"
        if not self._config.enabled:
            return "disabled"
        snapshot = await self._breaker.snapshot("tracking")
        return "ready" if snapshot.state is CircuitState.CLOSED else "degraded"


def _local_time(value: str, timezone: ZoneInfo) -> datetime:
    naive = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    candidates: set[datetime] = set()
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=timezone, fold=fold).astimezone(UTC)
        if candidate.astimezone(timezone).replace(tzinfo=None) == naive:
            candidates.add(candidate)
    if len(candidates) != 1:
        raise ValueError("ambiguous or nonexistent local time")
    return candidates.pop()


def _public_text(value: str, staff_values: set[str]) -> str:
    # This is a bounded projection, not a claim of exhaustive PII detection.
    for staff_value in sorted(staff_values, key=lambda item: (-len(item), item)):
        value = value.replace(staff_value, "[已隐藏]")
    value = _MOBILE.sub("[电话已隐藏]", value)
    value = _EMAIL.sub("[邮箱已隐藏]", value)
    return " ".join(value.split())
