"""Deterministic quotation policy, injected into explicit P1–P3 paths only."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from decimal import Decimal, localcontext
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from ..domain.agent_errors import AgentOperationError
from ..domain.commands import PostageCommand
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.postage import (
    PostageIdentifier,
    PostagePricingContext,
    PricingFingerprint,
    QuoteAmountKind,
    exact_weight_grams,
)
from ..domain.slots import PostageSlots, RegionRef, RegionResolution


ProductLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]

POSTAGE_CONFIRMATION = "确认基础询价"


class PostageProduct(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: PostageIdentifier
    name: ProductLabel
    aliases: tuple[ProductLabel, ...] = ()


class PostageRegionBinding(BaseModel):
    """Reviewed catalog binding; a resolved name alone never establishes coverage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    province_code: PostageIdentifier
    city_code: PostageIdentifier
    county_code: PostageIdentifier | None = None
    billing_id: PostageIdentifier

    def matches(self, region: RegionRef, *, origin: bool) -> bool:
        return (
            region.resolution is RegionResolution.RESOLVED
            and region.province_code == self.province_code
            and region.city_code == self.city_code
            and (
                region.county_code == self.county_code
                or (origin and self.county_code is None)
            )
        )


class PostageCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: PostageIdentifier
    evidence: Literal["synthetic", "reviewed_contract"]
    products: tuple[PostageProduct, ...] = Field(min_length=1)
    origins: tuple[PostageRegionBinding, ...] = Field(min_length=1)
    destinations: tuple[PostageRegionBinding, ...] = Field(min_length=1)
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    amount_kind: QuoteAmountKind
    max_weight_grams: int = Field(strict=True, gt=0, le=1_000_000)


def _failure(code: str, message: str, *, state: bool = False) -> AgentOperationError:
    return AgentOperationError(
        AgentFailure(
            category=(
                FailureCategory.STATE_SCHEMA_INCOMPATIBLE
                if state else FailureCategory.INVALID_INPUT
            ),
            code=code,
            message=message,
        )
    )


class PostagePreflight:
    def __init__(
        self, catalog: PostageCatalog, *, execution_profile_fingerprint: str | None = None,
        require_confirmation: bool = False,
    ) -> None:
        self._catalog = PostageCatalog.model_validate(catalog.model_dump())
        self._require_confirmation = require_confirmation
        self._products = {item.code: item for item in self.catalog.products}
        if len({code.casefold() for code in self._products}) != len(self.catalog.products):
            raise ValueError("资费产品代码不能重复")
        for rows in (self.catalog.origins, self.catalog.destinations):
            keys = [(r.province_code, r.city_code, r.county_code) for r in rows]
            if len(set(keys)) != len(keys):
                raise ValueError("资费地域绑定不能重复")
        if any(row.county_code is not None for row in self.catalog.origins):
            raise ValueError("当前寄件地绑定必须为地市级")
        payload = self.catalog.model_dump_json().encode("utf-8")
        if execution_profile_fingerprint is not None:
            # Opaque adapter contract identity; no provider wire fields enter
            # services/domain. Preserve the P1 hash exactly when unbound.
            fingerprint = TypeAdapter(PricingFingerprint).validate_python(
                execution_profile_fingerprint
            )
            payload += b"\nexecution-profile:" + fingerprint.encode("ascii")
        if require_confirmation:
            payload += b"\nconfirmation:explicit-command-v1"
        self._fingerprint = "sha256:" + hashlib.sha256(payload).hexdigest()

    @property
    def require_confirmation(self) -> bool:
        return self._require_confirmation

    @property
    def catalog(self) -> PostageCatalog:
        return self._catalog

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    @property
    def product_choices(self) -> list[str]:
        return list(self._products)

    def product_mentions(self, message: str) -> list[str]:
        matches = []
        for item in self._products.values():
            for token in (item.code, item.name, *item.aliases):
                pattern = re.escape(token)
                if token.isascii():
                    pattern = rf"(?<![A-Za-z0-9_.-]){pattern}(?![A-Za-z0-9_.-])"
                if re.search(pattern, message, re.IGNORECASE):
                    matches.append(item.code)
                    break
        return matches

    def select_product(
        self, message: str, *, model_product: str | None
    ) -> tuple[str | None, bool]:
        matches = self.product_mentions(message)
        explicit_codes = re.findall(
            r"产品\s*[:：=]?\s*([A-Za-z0-9_.-]+)", message
        )
        known = {code.casefold() for code in self._products}
        unknown_explicit = any(code.casefold() not in known for code in explicit_codes)
        unknown_request = not matches and (
            model_product is not None
            or any(word in message for word in ("产品", "EMS", "ems", "特快", "经济快递"))
        )
        conflict = len(matches) > 1 or unknown_explicit or unknown_request
        return (matches[0] if len(matches) == 1 and not conflict else None), conflict

    def unsupported_requirements(self, message: str) -> list[str]:
        # Conservative signals, not a claim of complete natural-language parsing.
        groups = {
            "dimensional": (
                r"体积|计泡|泡重|大箱|大件|尺寸|长宽高|厘米|"
                r"\d\s*(?:cm|CM)|\d\s*[xX×*]\s*\d"
            ),
            "extra_service": r"保价|保险|保额|回执|增值|代收|到付",
            "customer_channel": r"客户价|协议价|大客户|优惠|快递100|到站",
            "international": r"国际|国外|境外|海外|美国|日本|英国|法国|德国|加拿大|澳大利亚|香港|澳门|台湾",
            "comparison": r"比价|最便宜|比较.*产品|对比.*产品",
        }
        flags = [
            name for name, pattern in groups.items()
            if re.search(pattern, message)
        ]
        # The existing hard-entity extractor accepts plain decimal kg/g/斤.
        # Reject unsupported numeric spellings before it can retain an old
        # weight or accidentally read only the suffix (e.g. 1,500 g -> 500 g).
        unparsed_weights = (
            r"\d(?:\.\d+)?[eE][+-]?\d+\s*(?:kg|g|公斤|千克|克|斤)",
            r"\d[,，]\d+(?:\.\d+)?\s*(?:kg|g|公斤|千克|克|斤)",
            r"[一二三四五六七八九十百千万两几半]+\s*(?:kg|g|公斤|千克|克|斤)",
            r"\d(?:\.\d+)?\s*(?:吨|磅|lb|lbs|oz|t)(?![A-Za-z])",
            r"[−-]\s+\d|−\d",
        )
        if any(re.search(pattern, message, re.IGNORECASE) for pattern in unparsed_weights):
            flags.append("invalid_weight")
        if re.search(
            r"(?:nan|[+-]?inf(?:inity)?)\s*(?:kg|g|公斤|千克|克)",
            message, re.IGNORECASE,
        ):
            flags.append("invalid_weight")
        for match in re.finditer(
            r"(?<![\d.])([+-]?\d+(?:\.\d+)?)\s*"
            r"(千克|公斤|kg|斤|克|g)(?![A-Za-z])",
            message, re.IGNORECASE,
        ):
            value = Decimal(match[1])
            unit = match[2].lower()
            if unit == "斤":
                with localcontext() as context:
                    context.prec = max(28, len(value.as_tuple().digits) + 3)
                    value = value * Decimal("500")
                unit = "g"
            try:
                exact_weight_grams(
                    value, "kg" if unit in {"千克", "公斤", "kg"} else "g",
                    maximum=self.catalog.max_weight_grams,
                )
            except ValueError:
                flags.append("invalid_weight")
        return sorted(set(flags))

    def validate_state(self, state: Mapping[str, object]) -> None:
        if state.get("postage_policy_snapshot") != self.fingerprint:
            raise _failure(
                "postage_context_changed_restart",
                "资费上下文已变化或旧查询未经检查，请重新发起查询", state=True,
            )
        if "invalid_weight" in (state.get("postage_requirements") or []):
            raise _failure(
                "postage_weight_not_supported",
                "重量必须是可精确换算为整数克的正数，请重新查询",
            )
        if state.get("postage_requirements"):
            raise _failure(
                "postage_scope_not_supported",
                "当前只支持国内按实重、无增值服务的指定产品询价；请重新确认查询范围",
            )

    def _binding(self, region: RegionRef, *, origin: bool) -> PostageRegionBinding | None:
        rows = self.catalog.origins if origin else self.catalog.destinations
        return next((row for row in rows if row.matches(region, origin=origin)), None)

    def missing_slots(self, slots: PostageSlots) -> list[str]:
        missing = [
            name for name, value, origin in (
                ("origin", slots.origin, True),
                ("destination", slots.destination, False),
            )
            if value is None or self._binding(value, origin=origin) is None
        ]
        if slots.weight is None or slots.weight.value is None:
            missing.append("weight")
        if slots.product_code not in self._products:
            missing.append("product_code")
        return missing

    def prepare(self, command: PostageCommand) -> PostageCommand:
        if command.declared_value is not None:
            raise _failure("postage_extra_service_not_supported", "基础询价不支持保额")
        if command.product_code not in self._products:
            raise _failure("postage_product_not_available", "请选择已配置的产品")
        origin = self._binding(command.origin, origin=True)
        destination = self._binding(command.destination, origin=False)
        if origin is None or destination is None:
            raise _failure("postage_region_not_available", "当前目录没有可用的计费地区绑定")
        try:
            grams = exact_weight_grams(
                command.weight.value, command.weight.unit,
                maximum=self.catalog.max_weight_grams,
            )
        except (TypeError, ValueError) as error:
            raise _failure(
                "postage_weight_not_supported",
                "重量必须为本地上限内的整数克；不能自动取整",
            ) from error
        context = PostagePricingContext(
            catalog_version=self.catalog.version, policy_fingerprint=self.fingerprint,
            product_code=command.product_code, origin_billing_id=origin.billing_id,
            destination_billing_id=destination.billing_id, weight_grams=grams,
            currency=self.catalog.currency, amount_kind=self.catalog.amount_kind,
        )
        return PostageCommand.model_validate({
            **command.model_dump(), "pricing_context": context,
        })

    def validate_command(self, command: PostageCommand) -> None:
        expected = self.prepare(command)
        if command.pricing_context != expected.pricing_context:
            raise _failure(
                "postage_context_changed_restart",
                "询价缺少有效的已冻结上下文，请重新查询", state=True,
            )
