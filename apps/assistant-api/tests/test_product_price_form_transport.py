import asyncio
import httpx

from .test_product_price_public_workflow import public_app, PublicRepository, public_fresh_record, send
from .test_product_price_composition import AUTH


def test_web_price_scope_reply_does_not_inject_an_opposite_price_nature(tmp_path):
    async def run():
        repository = PublicRepository(public_fresh_record())
        app = public_app(tmp_path, repository)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test", headers=AUTH,
        ) as client:
            initial = (await send(client, message="黄瓜多少钱")).json()
            response = await send(client, conversation=initial["conversation_id"], message="报价地区：上海；零售")
            assert response.json()["phase"] == "completed", response.text
            assert response.json()["result"]["data"]["items"][0]["quote"]["price_nature"] == "RETAIL_AVERAGE"
            assert len(repository.queries) == 1
    asyncio.run(run())
