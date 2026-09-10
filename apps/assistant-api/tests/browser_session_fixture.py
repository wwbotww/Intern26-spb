"""Isolated 6A-1 demo: synthetic postage only, no environment or live transport."""

import argparse
from pathlib import Path

from spb_assistant_api.offline_postage import create_offline_postage_app
from spb_assistant_api.security.browser_session import BrowserSessionConfig

from .postage_p1_fixture import preflight
from .postage_p3_fixture import API_KEY, OTHER_KEY, SyntheticPostageTransport, p3_config

ORIGIN = "http://127.0.0.1:13006"
SIGNING_KEY = "synthetic-browser-signing-key-6a1-only"


def browser_config(**changes):
    return BrowserSessionConfig(**{
        "public_origin": ORIGIN, "proxy_api_key": API_KEY,
        "signing_key": SIGNING_KEY, "secure": False, **changes,
    })


def app_at(path, transports, *, browser=None):
    def transport_factory():
        transport = SyntheticPostageTransport()
        transports.append(transport)
        return transport

    return create_offline_postage_app(
        database_path=Path(path), api_keys=f"{API_KEY},{OTHER_KEY}",
        config=p3_config(), catalog=preflight().catalog,
        transport_factory=transport_factory, browser_session=browser or browser_config(),
    )


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18086)
    args = parser.parse_args()
    uvicorn.run(app_at(args.database, []), host="127.0.0.1", port=args.port)
