"""Loopback-only catalog browser QA; never reads dotenv or company data."""

import argparse
from pathlib import Path

import uvicorn

from spb_assistant_api.api.app import create_app
from .test_product_price_public_workflow import PublicRepository, public_fresh_record
from .test_product_price_composition import settings
from .product_price_device_fixture import baseline_v2_record


def app_at(path):
    key = "postage-p3-synthetic-key"  # Matches the existing isolated browser proxy.
    return create_app(
        settings=settings(
            path.parent,
            agent_database_path=str(path),
            api_keys=key,
            agent_browser_session_enabled=True,
            agent_browser_proxy_api_key=key,
            agent_browser_signing_key="synthetic-catalog-browser-signing-key-only",
            agent_browser_public_origin="http://127.0.0.1:13006",
            agent_browser_cookie_secure=False,
        ),
        product_price_repository=PublicRepository(
            baseline_v2_record("apple_pro_256"),
            baseline_v2_record("apple_pro_512"),
            public_fresh_record(),
            public_fresh_record("fresh_wholesale_kg"),
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    uvicorn.run(app_at(args.database), host="127.0.0.1", port=18086)
