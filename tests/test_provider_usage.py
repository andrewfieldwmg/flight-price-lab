import asyncio
from datetime import datetime

import httpx
from pydantic import SecretStr

from flight_price_lab.api.models import ProviderUsage
from flight_price_lab.api.provider_usage import (
    CachedProviderUsage,
    SearchAPIUsageGateway,
    map_searchapi_usage,
)
from flight_price_lab.providers.searchapi import SearchAPIClient

PAYLOAD = {
    "account": {
        "current_month_usage": 327,
        "monthly_allowance": 10_000,
        "remaining_credits": 9_673,
    },
    "subscription": {
        "period_start": "2026-08-01T00:00:00Z",
        "period_end": "2026-09-01T00:00:00Z",
    },
}


def test_provider_usage_mapping() -> None:
    usage = map_searchapi_usage(PAYLOAD)

    assert usage.current_month_usage == 327
    assert usage.monthly_allowance == 10_000
    assert usage.remaining_credits == 9_673
    assert usage.period_start == datetime.fromisoformat("2026-08-01T00:00:00+00:00")


def test_account_request_uses_bearer_header_and_normalizes() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json=PAYLOAD)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = SearchAPIClient(SecretStr("secret"), http_client=http_client)
    usage = asyncio.run(SearchAPIUsageGateway(client).get_usage())

    assert seen["url"] == "https://www.searchapi.io/api/v1/me"
    assert seen["authorization"] == "Bearer secret"
    assert usage.remaining_credits == 9_673


def test_provider_usage_is_cached_until_ttl_expires() -> None:
    class Gateway:
        calls = 0

        async def get_usage(self) -> ProviderUsage:
            self.calls += 1
            return map_searchapi_usage(PAYLOAD)

    clock = [100.0]
    gateway = Gateway()
    cache = CachedProviderUsage(gateway, ttl_seconds=45, clock=lambda: clock[0])

    async def exercise() -> None:
        await cache.get()
        await cache.get()
        await cache.get(force_refresh=True)
        clock[0] += 46
        await cache.get()

    asyncio.run(exercise())
    assert gateway.calls == 3
