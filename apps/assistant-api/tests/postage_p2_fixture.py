"""Offline test harness. Never imported by a production composition root."""

import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx

from spb_assistant_api.adapters.postal_postage import PostalPostageGateway
from spb_assistant_api.adapters.postal_postage_contract import PostalPostageConfig

from .postage_p1_fixture import NOW, command, preflight


FIXTURES = Path(__file__).parent / "fixtures" / "postal_postage"
PRIVATE = "SYNTHETIC-PRIVATE-MESSAGE"


def fixture(name="success.json"):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def config(**changes):
    return PostalPostageConfig.model_validate({
        "enabled": True, "profile": fixture("profile.json"), "sys_code": "SYNTHP2",
        "password": "synthetic-password-only", "access_key": "synthetic-ak-only",
        "secret_key": "synthetic-sk-only", **changes,
    })


def bound_policy(cfg=None, **catalog_changes):
    return (cfg or config()).profile.bind(preflight(**catalog_changes).catalog)


def prepared(cfg=None):
    return bound_policy(cfg).prepare(command())


def with_quote(payload=None, **changes):
    payload = payload or fixture()
    decoded = json.loads(payload["body"]["retBody"])
    decoded["map"].update(changes)
    payload["body"]["retBody"] = json.dumps(decoded, ensure_ascii=False)
    return payload


def request_form(request):
    return {k: v[0] for k, v in parse_qs(request.content.decode("utf-8"), keep_blank_values=True).items()}


def response_for(request, **quote_changes):
    payload = with_quote(**quote_changes)
    # Only the synthetic responder correlates a generated request serial.
    # It does not calculate a tariff or fabricate a production fallback.
    payload["body"]["serialNo"] = json.loads(request_form(request)["messageHeader"])["serialNo"]
    return httpx.Response(200, json=payload)


def gateway(handler, *, cfg=None, policy=None, **kwargs):
    cfg = cfg or config()
    return PostalPostageGateway(
        cfg, preflight=policy or bound_policy(cfg), transport=httpx.MockTransport(handler),
        clock=kwargs.pop("clock", lambda: NOW),
        serial_number_factory=kwargs.pop("serial_number_factory", lambda: "synth-p2-1"),
        **kwargs,
    )
