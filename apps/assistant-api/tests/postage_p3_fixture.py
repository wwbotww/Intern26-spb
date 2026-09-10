"""Explicit synthetic V2 harness; responses are a tiny fixture table, not tariffs."""

import argparse
import json
from pathlib import Path

import httpx

from spb_assistant_api.offline_postage import create_offline_postage_app

from .postage_p1_fixture import preflight
from .postage_p2_fixture import config, request_form, response_for

API_KEY = "postage-p3-synthetic-key"
OTHER_KEY = "postage-p3-other-owner"
QUERY = "从北京寄到上海，1.25公斤，产品 SYN-A，查邮费"
CONFIRM = "确认基础询价"


def p3_config(**changes):
    original = config()
    profile = original.profile.model_copy(update={"pricing_scope_ref": "synthetic-public-channel-v1"})
    return config(profile=profile, **changes)


class SyntheticPostageTransport(httpx.MockTransport):
    def __init__(self, scenario="success"):
        self.calls = []
        self.closed = False
        self.scenario = scenario
        super().__init__(self.respond)

    def respond(self, request):
        assert request.url.host == "postage.invalid"
        fields = json.loads(request_form(request)["map"])
        self.calls.append(fields)
        if self.scenario == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if self.scenario == "malformed":
            return response_for(request, totalFee="NaN")
        if self.scenario == "business-refusal":
            return response_for(request, errorcode="0001", errorname="SYNTHETIC-PRIVATE-MESSAGE")
        prices = {("SYN-A", "1250"): "12.30", ("SYN-A", "2000"): "16.00", ("SYN-B", "1250"): "18.40"}
        amount = prices.get((fields["productCode"], fields["weight"]))
        if amount is None:
            return response_for(request, errorcode="0001")
        return response_for(request, totalFee=amount, weight=fields["weight"], feeWeight=str(max(1500, int(fields["weight"]))))

    async def aclose(self):
        self.closed = True
        await super().aclose()


def app_at(path, transports, *, scenario="success", cfg=None):
    def transport_factory():
        transport = SyntheticPostageTransport(scenario)
        transports.append(transport)
        return transport

    return create_offline_postage_app(
        database_path=Path(path), api_keys=f"{API_KEY},{OTHER_KEY}",
        config=cfg or p3_config(), catalog=preflight().catalog,
        transport_factory=transport_factory,
    )


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18083)
    parser.add_argument("--scenario", choices=["success", "business-refusal", "timeout", "malformed"], default="success")
    args = parser.parse_args()
    uvicorn.run(app_at(args.database, [], scenario=args.scenario), host="127.0.0.1", port=args.port)
