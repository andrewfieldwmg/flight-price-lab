"""Direct-only directional calendar pricing."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from flight_price_lab.api.models import CalendarPrice, CalendarResponse
from flight_price_lab.api.provider import ProviderGateway, ProviderSearchResult
from flight_price_lab.providers.searchapi import SearchAPIError
from flight_price_lab.storage.database import CalendarPriceStore, canonical_search_key


def classify_calendar_prices(prices: list[CalendarPrice]) -> list[CalendarPrice]:
    available = [item for item in prices if item.price is not None]
    if len(available) < 3 or len({item.price for item in available}) == 1:
        return [
            item.model_copy(update={"classification": "TYPICAL"})
            if item.price is not None
            else item
            for item in prices
        ]
    ordered = sorted(available, key=lambda item: item.price or Decimal())
    edge = max(1, round(len(ordered) * 0.286))
    low_cutoff = ordered[edge - 1].price
    high_cutoff = ordered[-edge].price
    result = []
    for item in prices:
        classification = None
        if item.price is not None:
            if item.price <= low_cutoff and item.price < high_cutoff:
                classification = "LOW"
            elif item.price >= high_cutoff and item.price > low_cutoff:
                classification = "HIGH"
            else:
                classification = "TYPICAL"
        result.append(item.model_copy(update={"classification": classification}))
    return result


class DirectionalCalendarService:
    def __init__(
        self,
        provider: ProviderGateway,
        store: CalendarPriceStore,
        *,
        max_concurrency: int = 4,
    ) -> None:
        self.provider = provider
        self.store = store
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def prices(
        self,
        *,
        origins: Sequence[str],
        destinations: Sequence[str],
        dates: Sequence[date],
        adults: int,
        children: int,
        currency: str,
        direction: str,
    ) -> CalendarResponse:
        calls = 0
        avoided = 0
        failures = 0

        async def load(travel_date: date) -> CalendarPrice:
            nonlocal calls, avoided, failures
            cached = await asyncio.to_thread(
                self.store.get_fresh,
                origins=tuple(origins),
                destinations=tuple(destinations),
                travel_date=travel_date,
                adults=adults,
                children=children,
                currency=currency,
                direction=direction,
            )
            if cached is not None:
                avoided += 1
                return CalendarPrice(
                    date=travel_date,
                    price=cached.lowest_direct_price,
                    currency=cached.currency,
                    state="CACHED",
                    observed_at=cached.observed_at,
                )
            reused = await asyncio.to_thread(
                self.store.reuse_search_baseline,
                origins=tuple(origins),
                destinations=tuple(destinations),
                travel_date=travel_date,
                adults=adults,
                children=children,
                currency=currency,
                direction=direction,
            )
            if reused is not None:
                avoided += 1
                return CalendarPrice(
                    date=travel_date,
                    price=reused.lowest_direct_price,
                    currency=reused.currency,
                    state="CACHED",
                    observed_at=reused.observed_at,
                )
            stale = await asyncio.to_thread(
                self.store.get_latest,
                origins=tuple(origins),
                destinations=tuple(destinations),
                travel_date=travel_date,
                adults=adults,
                children=children,
                currency=currency,
                direction=direction,
            )
            try:
                async with self.semaphore:
                    response = await self.provider.search_direct(
                        origins=tuple(origins),
                        destinations=tuple(destinations),
                        travel_date=travel_date,
                        adults=adults,
                        children=children,
                        currency=currency,
                        cabin_bags=0,
                        checked_bags=0,
                        bypass_cache=False,
                        trip_id="calendar",
                        trip_search_key="calendar",
                        direction=direction,
                        query_type="calendar_direct",
                        hub=None,
                    )
            except (TimeoutError, SearchAPIError, NotImplementedError):
                calls += 1
                failures += 1
                if stale is not None:
                    return CalendarPrice(
                        date=travel_date,
                        price=stale.lowest_direct_price,
                        currency=stale.currency,
                        state="STALE_AVAILABLE",
                        observed_at=stale.observed_at,
                    )
                return CalendarPrice(
                    date=travel_date,
                    price=None,
                    currency=currency,
                    state="ERROR",
                )
            offers = (
                response.offers
                if isinstance(response, ProviderSearchResult)
                else response
            )
            calls += (
                response.provider_calls
                if isinstance(response, ProviderSearchResult)
                else 1
            )
            if isinstance(response, ProviderSearchResult):
                avoided += response.provider_calls_avoided
            if not offers:
                return CalendarPrice(
                    date=travel_date,
                    price=None,
                    currency=currency,
                    state="UNAVAILABLE",
                )
            lowest = min(offer.total_price for offer in offers)
            source_key = canonical_search_key(
                {
                    "origins": list(origins),
                    "destinations": list(destinations),
                    "date": travel_date.isoformat(),
                    "adults": adults,
                    "children": children,
                    "currency": currency,
                    "flight_type": "one_way",
                    "stops": "nonstop",
                }
            )
            observation = await asyncio.to_thread(
                self.store.put,
                origins=tuple(origins),
                destinations=tuple(destinations),
                travel_date=travel_date,
                direction=direction,
                lowest_direct_price=lowest,
                currency=currency,
                adults=adults,
                children=children,
                source_search_key=source_key,
            )
            return CalendarPrice(
                date=travel_date,
                price=lowest,
                currency=currency,
                observed_at=observation.observed_at,
            )

        results = await asyncio.gather(*(load(item) for item in dates))
        return CalendarResponse(
            dates=classify_calendar_prices(list(results)),
            calendar_provider_calls_this_invocation=calls,
            calendar_calls_avoided=avoided,
            failures=failures,
        )
