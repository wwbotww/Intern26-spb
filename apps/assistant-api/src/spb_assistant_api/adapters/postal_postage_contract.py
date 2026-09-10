"""One synthetic-only, provisional contract. Not approved for provider access.

Every ambiguous choice is named and hashed; see the P2 gap ledger. No dotenv,
SDK auto-retry, signature probing, or recursive response decoding belongs here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator

from ..domain.postage import PostageIdentifier, QuoteAmountKind
from ..services.postage_preflight import PostageCatalog, PostagePreflight


POSTAL_POSTAGE_PROFILE = "postal-postage-synthetic-v1"
POSTAL_POSTAGE_URL = "https://postage.invalid"
POSTAL_POSTAGE_PATH = "CSB"
WireText = Annotated[str, StringConstraints(strict=True, max_length=128)]
WireCode = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9]{3}$")]


class PostalPostageProfile(BaseModel):
    """Explicit assumptions, not a supplier contract approval switch."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    name: Literal["postal-postage-synthetic-v1"] = POSTAL_POSTAGE_PROFILE
    evidence: Literal["synthetic"] = "synthetic"
    api_name: Literal["getAllFeeForInterface"] = "getAllFeeForInterface"
    api_version: PostageIdentifier
    timezone: str
    serialization: Literal["sorted-compact-json-utf8"] = "sorted-compact-json-utf8"
    business_signature: Literal["password-map-md5-raw-md5-raw-base64"] = "password-map-md5-raw-md5-raw-base64"
    csb_signature: Literal["raw-sorted-hmac-sha1-headers-ms"] = "raw-sorted-hmac-sha1-headers-ms"
    response_shape: Literal["body-retBody-json-string-map-object"] = "body-retBody-json-string-map-object"
    currency: Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Z]{3}$")]
    amount_kind: QuoteAmountKind
    amount_unit: Literal["major"] = "major"
    response_weight_unit: Literal["g"] = "g"
    # Non-secret, explicitly assigned pricing principal/channel revision.
    # Credential rotation alone does not change pricing semantics.
    pricing_scope_ref: PostageIdentifier | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("timezone must be an explicit IANA zone") from None
        return value

    @property
    def fingerprint(self) -> str:
        return "sha256:" + hashlib.sha256(self.model_dump_json(exclude_none=True).encode("utf-8")).hexdigest()

    def bind(self, catalog: PostageCatalog, *, require_confirmation: bool = False) -> PostagePreflight:
        # Revalidate even model_copy/model_construct input. No inferred currency
        # or amount field; a profile change invalidates suspended pricing state.
        profile = PostalPostageProfile.model_validate(self.model_dump())
        catalog = PostageCatalog.model_validate(catalog.model_dump())
        if catalog.evidence != "synthetic":
            raise ValueError("P2 only accepts a synthetic catalog")
        if (catalog.currency, catalog.amount_kind) != (profile.currency, profile.amount_kind):
            raise ValueError("catalog and postage profile pricing semantics differ")
        return PostagePreflight(
            catalog, execution_profile_fingerprint=profile.fingerprint,
            require_confirmation=require_confirmation,
        )


class PostalPostageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    enabled: bool = False
    profile: PostalPostageProfile
    # No configurable network destination in P2. These are synthetic credentials
    # passed explicitly by the offline harness, never read from environment.
    sys_code: Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9]{1,8}$")] = Field(repr=False)
    password: SecretStr = Field(repr=False)
    access_key: SecretStr = Field(repr=False)
    secret_key: SecretStr = Field(repr=False)
    timeout_seconds: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)
    max_connections: int = Field(default=4, strict=True, ge=1, le=32)
    max_response_bytes: int = Field(default=1_048_576, strict=True, ge=1, le=1_048_576)

    @field_validator("password", "access_key", "secret_key")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw.strip() or len(raw) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in raw):
            raise ValueError("explicit, nonblank signing material is required")
        raw.encode("utf-8")
        return value

    @field_validator("access_key")
    @classmethod
    def validate_header_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().isascii():
            raise ValueError("CSB access key must be ASCII for header transport")
        return value


def postage_json(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class PostalPostageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    product_code: PostageIdentifier = Field(alias="productCode", repr=False)
    weight: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{0,6}$")] = Field(repr=False)
    sender_city_no: PostageIdentifier = Field(alias="senderCityNo", repr=False)
    destination_code: PostageIdentifier = Field(alias="aACode", repr=False)

    def message_map(self) -> str:
        return postage_json(self.model_dump(by_alias=True))


@dataclass(frozen=True, slots=True)
class PostalPostageSigner:
    """Base64(MD5(raw MD5(UTF8(password + exact map JSON)))).

    Provisional legacy compatibility, NOT encryption or modern HMAC. Do not
    bypass a runtime that prohibits MD5. No alternative algorithm is attempted.
    """

    password: SecretStr = field(repr=False)

    def sign(self, message_map: str) -> str:
        raw = self.password.get_secret_value()
        if not raw.strip():
            raise ValueError("business password is required")
        inner = hashlib.md5((raw + message_map).encode("utf-8")).digest()
        return base64.b64encode(hashlib.md5(inner).digest()).decode("ascii")


@dataclass(frozen=True, slots=True)
class CsbPostageSigner:
    """Narrow scalar form variant of the public Aliyun CSB signing example.

    Headers participate in signing together with UNENCODED business form
    values. The transport encodes only the form, exactly once, afterwards.
    """

    access_key: SecretStr = field(repr=False)
    secret_key: SecretStr = field(repr=False)

    def headers(
        self, form: Mapping[str, str], *, api_name: str, api_version: str, timestamp_ms: int
    ) -> dict[str, str]:
        if any(not isinstance(k, str) or not isinstance(v, str) or k.startswith("_api_") for k, v in form.items()):
            raise ValueError("CSB business form must contain scalar, non-reserved fields")
        if type(timestamp_ms) is not int or timestamp_ms < 0:
            raise ValueError("CSB timestamp must be nonnegative integer milliseconds")
        headers = {
            "_api_name": api_name, "_api_version": api_version,
            "_api_access_key": self.access_key.get_secret_value(),
            "_api_timestamp": str(timestamp_ms),
        }
        values = {**form, **headers}
        canonical = "&".join(f"{key}={values[key]}" for key in sorted(values))
        digest = hmac.new(self.secret_key.get_secret_value().encode("utf-8"), canonical.encode("utf-8"), hashlib.sha1).digest()
        headers["_api_signature"] = base64.b64encode(digest).decode("ascii")
        return headers


class CsbPostageEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)
    code: WireCode = Field(alias="Code")
    # Later layers are validated only when earlier gates have succeeded.
    body: Any = Field(default=None, repr=False)


class PostalPostageEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)
    serial_no: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,36}$")] = Field(alias="serialNo", repr=False)
    ret_code: WireCode = Field(alias="retCode")
    ret_date: Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$")] = Field(alias="retDate", repr=False)
    ret_body: Any = Field(default=None, alias="retBody", repr=False)


class PostalPostageReply(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)
    error_code: Annotated[str, StringConstraints(pattern=r"^[0-9]{4}$")] | None = Field(alias="errorcode")
    error_name: Annotated[str, StringConstraints(max_length=2000)] | None = Field(default=None, alias="errorname", repr=False)
    weight: WireText | None = Field(default=None, repr=False)
    fee_weight: WireText | None = Field(default=None, alias="feeWeight", repr=False)
    vol_weight: WireText | None = Field(default=None, alias="volWeight", repr=False)
    vol_ratio: WireText | None = Field(default=None, alias="volRatio", repr=False)
    total_fee: WireText | None = Field(default=None, alias="totalFee", repr=False)
    standard_fee: WireText | None = Field(default=None, alias="standardFee", repr=False)
    real_fee: WireText | None = Field(default=None, alias="realFee", repr=False)
    reg_fee: WireText | None = Field(default=None, alias="regFee", repr=False)
    insurance_fee: WireText | None = Field(default=None, alias="BXF", repr=False)
    declared_value_fee: WireText | None = Field(default=None, alias="BJF", repr=False)
    inspection_fee: WireText | None = Field(default=None, alias="YGF", repr=False)
    customs_fee: WireText | None = Field(default=None, alias="BGF", repr=False)
    fuel_fee: WireText | None = Field(default=None, alias="FJF", repr=False)
    return_receipt_fee: WireText | None = Field(default=None, alias="HZF", repr=False)
    password_delivery_fee: WireText | None = Field(default=None, alias="MMTDF", repr=False)
    printing_fee: WireText | None = Field(default=None, alias="DYFWF", repr=False)
    handling_fee: WireText | None = Field(default=None, alias="BJSXZF", repr=False)


class PostalPostagePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)
    quote: PostalPostageReply = Field(alias="map", repr=False)
