"""Normalized SearchAPI account usage with a short-lived process cache."""

import asyncio
from collections.abc import Callable, Mapping
from time import monotonic
from typing import Any, Protocol

from flight_price_lab.api.models import ProviderUsage
from flight_price_lab.providers.searchapi import SearchAPIClient


def map_searchapi_usage(payload: Mapping[str, Any]) -> ProviderUsage:
    """Map the documented account and subscription objects only."""

    account = payload.get("account")
    subscription = payload.get("subscription")
    if not isinstance(account, Mapping) or not isinstance(subscription, Mapping):
        raise TypeError("SearchAPI account response is missing usage data")
    try:
        return ProviderUsage(
            current_month_usage=account["current_month_usage"],
            monthly_allowance=account["monthly_allowance"],
            remaining_credits=account["remaining_credits"],
            period_start=subscription["period_start"],
            period_end=subscription["period_end"],
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            "SearchAPI account response contains invalid usage data"
        ) from None


class ProviderUsageGateway(Protocol):
    async def get_usage(self) -> ProviderUsage: ...


class SearchAPIUsageGateway:
    def __init__(self, client: SearchAPIClient) -> None:
        self._client = client

    async def get_usage(self) -> ProviderUsage:
        payload = await asyncio.to_thread(self._client.account_usage)
        return map_searchapi_usage(payload)


class CachedProviderUsage:
    def __init__(
        self,
        gateway: ProviderUsageGateway,
        *,
        ttl_seconds: float = 45,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._gateway = gateway
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._cached: ProviderUsage | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self, *, force_refresh: bool = False) -> ProviderUsage:
        now = self._clock()
        if not force_refresh and self._cached is not None and now < self._expires_at:
            return self._cached
        async with self._lock:
            now = self._clock()
            if force_refresh or self._cached is None or now >= self._expires_at:
                self._cached = await self._gateway.get_usage()
                self._expires_at = now + self._ttl_seconds
            return self._cached
