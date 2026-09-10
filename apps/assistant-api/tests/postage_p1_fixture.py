"""Synthetic-only helpers; not imported by the application or a production factory."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from spb_assistant_api.domain.commands import PostageCommand
from spb_assistant_api.domain.postage import PostageQuoteBasis
from spb_assistant_api.domain.postage_observation import PostageQuoteObservation
from spb_assistant_api.domain.results import PostageData
from spb_assistant_api.domain.slots import WeightValue
from spb_assistant_api.services.postage_preflight import PostageCatalog, PostagePreflight
from spb_assistant_api.services.region_resolver import create_demo_region_resolver


FIXTURES = Path(__file__).parent / "fixtures" / "postage_p1"
NOW = datetime(2026, 9, 9, 8, tzinfo=UTC)
REGIONS = create_demo_region_resolver()


def preflight(**changes):
    raw = json.loads((FIXTURES / "catalog.json").read_text())
    return PostagePreflight(PostageCatalog.model_validate({**raw, **changes}))


def command(**changes):
    return PostageCommand.model_validate({
        "origin": REGIONS.resolve("北京"), "destination": REGIONS.resolve("上海"),
        "weight": WeightValue(value="1.25", unit="kg"), "product_code": "SYN-A", **changes,
    })


def observation(cmd, *, at=NOW, **changes):
    raw = json.loads((FIXTURES / "quote.json").read_text())
    data = PostageData.model_validate({
        "origin": cmd.origin, "destination": cmd.destination,
        "input_weight": cmd.weight, "billable_weight": raw["billable_weight"],
        "amount": raw["amount"], "currency": cmd.pricing_context.currency,
        "product_code": cmd.product_code, "queried_at": at,
        "quote_basis": PostageQuoteBasis(context=cmd.pricing_context), **changes,
    })
    return PostageQuoteObservation(data=data, source=raw["source"], queried_at=at)


class ObservedFakePostageGateway:
    def __init__(self):
        self.commands = []

    async def quote(self, cmd):
        self.commands.append(cmd)
        return observation(cmd, at=NOW + timedelta(seconds=len(self.commands)))
