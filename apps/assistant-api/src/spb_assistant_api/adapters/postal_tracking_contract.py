"""Provisional wire contract for the supplied 2019 tracking document.

These models do not establish provider interoperability. In particular, the
signing input, response recipient and timezone need confirmation before live use.
No environment files or credentials are loaded by this module.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
)

from .agent_http import _validate_base_url, _validate_relative_path


POSTAL_TRACKING_PROFILE = "postal-tracking-doc-2019-v1"


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


NonBlankText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1),
    AfterValidator(_not_blank),
]


# Identifier syntax is the existing domain boundary, not a claimed postal rule.
PostalMailNumber = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=1, max_length=30, pattern=r"^[A-Za-z0-9]+$"
    ),
]
ConfigIdentifier = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.-]+$",
    ),
]


class PostalTrackingConfig(BaseModel):
    """Explicit local configuration; limits on header IDs are local policy.

    Enabling this object is not a security approval or a provider contract approval.
    T3's configured composition requires a separate explicit Agent enable flag.
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True
    )

    enabled: bool = False
    base_url: str = Field(repr=False)
    path: str = "interface"
    send_id: ConfigIdentifier = Field(repr=False)
    receive_id: ConfigIdentifier = Field(default="JDPT", repr=False)
    msg_kind: ConfigIdentifier = Field(repr=False)
    # Not inferred from send_id and not described as a verified shared secret.
    signing_system_id: SecretStr = Field(repr=False)
    # The generic description and query example disagree on this value.
    expected_response_receive_id: NonBlankText = Field(max_length=20, repr=False)
    timezone: str
    province_no: Annotated[
        str, StringConstraints(strict=True, pattern=r"^[0-9]{2}$")
    ] = "99"
    batch_no: ConfigIdentifier | None = None
    profile: Literal["postal-tracking-doc-2019-v1"] = POSTAL_TRACKING_PROFILE
    timeout_seconds: float = Field(default=5, gt=0, le=30)
    max_connections: int = Field(default=4, ge=1, le=32)
    max_response_bytes: int = Field(default=1_048_576, ge=1, le=1_048_576)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = _validate_base_url(value)
        if not normalized.lower().startswith("https://"):
            raise ValueError("postal tracking requires an HTTPS base URL")
        return normalized

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_relative_path(value)

    @field_validator("signing_system_id")
    @classmethod
    def validate_signing_input(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("signing system ID must be explicitly configured")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("timezone must be an explicit IANA zone") from None
        return value


class PostalTrackingRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True
    )

    trace_no: PostalMailNumber = Field(alias="traceNo", repr=False)

    def message_body(self) -> str:
        # Serialize once. Sign and transmit exactly the same JSON string.
        return json.dumps(
            self.model_dump(by_alias=True),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )


@dataclass(frozen=True, slots=True)
class PostalLegacySigner:
    """Compatibility only: Base64(raw MD5(UTF8(body + signing_system_id))).

    This is NOT HMAC or encryption. The raw-digest variant is provisional, not a
    provider-supplied test vector. Do not bypass a platform that prohibits MD5.
    """

    system_id: SecretStr = field(repr=False)

    def sign(self, message_body: str) -> str:
        secret = self.system_id.get_secret_value()
        if not secret.strip():
            raise ValueError("signing system ID must not be empty")
        digest = hashlib.md5((message_body + secret).encode("utf-8")).digest()
        return base64.b64encode(digest).decode("ascii")


class PostalTrackingWireEvent(BaseModel):
    model_config = ConfigDict(
        extra="ignore", strict=True, frozen=True, hide_input_in_errors=True
    )

    trace_no: PostalMailNumber = Field(alias="traceNo", repr=False)
    op_time: Annotated[
        str,
        StringConstraints(
            pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$"
        ),
    ] = Field(alias="opTime")
    op_code: NonBlankText = Field(alias="opCode", max_length=10)
    op_name: NonBlankText = Field(alias="opName", max_length=50, repr=False)
    op_desc: NonBlankText = Field(alias="opDesc", max_length=1000, repr=False)
    op_org_prov_name: Annotated[str, Field(max_length=300)] | None = Field(
        default=None, alias="opOrgProvName", repr=False
    )
    op_org_city: NonBlankText = Field(alias="opOrgCity", max_length=300, repr=False)
    op_org_code: NonBlankText = Field(alias="opOrgCode", max_length=20, repr=False)
    op_org_name: NonBlankText = Field(alias="opOrgName", max_length=300, repr=False)
    operator_no: Annotated[str, Field(max_length=50)] | None = Field(
        default=None, alias="operatorNo", repr=False
    )
    operator_name: Annotated[str, Field(max_length=300)] | None = Field(
        default=None, alias="operatorName", repr=False
    )


class PostalTrackingResponse(BaseModel):
    model_config = ConfigDict(
        extra="ignore", strict=True, frozen=True, hide_input_in_errors=True
    )

    receive_id: NonBlankText = Field(alias="receiveID", max_length=20, repr=False)
    response_state: bool = Field(alias="responseState")
    error_desc: Annotated[str, Field(max_length=200)] | None = Field(
        default=None, alias="errorDesc", repr=False
    )
    response_items: Annotated[
        list[PostalTrackingWireEvent], Field(max_length=30)
    ] | None = Field(default=None, alias="responseItems", repr=False)
