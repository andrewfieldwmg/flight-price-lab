"""Direct-only directional calendar pricing."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from statistics import median
from time import perf_counter

from flight_price_lab.api.models import (
    CalendarPrice,
    CalendarRequestTiming,
    CalendarResponse,
)
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
        total_clock = perf_counter()
        postgres_ms = 0.0
        calls = 0
        avoided = 0
        failures = 0
        active = 0
        peak = 0
        request_timings: list[CalendarRequestTiming] = []
        provider_durations: list[float] = []
        common = {
            "origins": tuple(origins),
            "destinations": tuple(destinations),
            "adults": adults,
            "children": children,
            "currency": currency,
            "direction": direction,
        }

        db_clock = perf_counter()
        fresh = await asyncio.to_thread(
            self.store.get_fresh_many, travel_dates=tuple(dates), **common
        )
        postgres_ms += (perf_counter() - db_clock) * 1000
        missing = [item for item in dates if item not in fresh]

        db_clock = perf_counter()
        reused = await asyncio.to_thread(
            self.store.reuse_search_baselines_many,
            travel_dates=tuple(missing),
            **common,
        )
        postgres_ms += (perf_counter() - db_clock) * 1000
        missing = [item for item in missing if item not in reused]

        db_clock = perf_counter()
        stale = await asyncio.to_thread(
            self.store.get_latest_many, travel_dates=tuple(missing), **common
        )
        postgres_ms += (perf_counter() - db_clock) * 1000

        results: dict[date, CalendarPrice] = {}
        now = datetime.now(UTC)
        for travel_date, cached in {**fresh, **reused}.items():
            avoided += 1
            results[travel_date] = CalendarPrice(
                date=travel_date,
                price=cached.lowest_direct_price,
                currency=cached.currency,
                state="CACHED",
                observed_at=cached.observed_at,
            )
            request_timings.append(
                CalendarRequestTiming(
                    date=travel_date,
                    started_at=now,
                    completed_at=now,
                    duration_ms=0,
                    status="CACHED",
                    cache_hit=True,
                )
            )

        pending_observations: list[dict[str, object]] = []
        pending_lock = asyncio.Lock()

        async def load(travel_date: date) -> None:
            nonlocal calls, avoided, failures, active, peak, postgres_ms
            started_at = datetime.now(UTC)
            clock = perf_counter()
            status = "ERROR"
            cache_hit = False
            provider_call = False
            try:
                async with self.semaphore:
                    active += 1
                    peak = max(peak, active)
                    try:
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
                    finally:
                        active -= 1
                offers = (
                    response.offers
                    if isinstance(response, ProviderSearchResult)
                    else response
                )
                if isinstance(response, ProviderSearchResult):
                    calls += response.provider_calls
                    avoided += response.provider_calls_avoided
                    cache_hit = response.provider_calls == 0
                    provider_call = response.provider_calls > 0
                    postgres_ms += response.postgres_write_ms
                    duration = float(
                        (response.request_timing or {}).get("duration_ms", 0)
                    )
                    if provider_call:
                        provider_durations.append(duration)
                else:
                    calls += 1
                    provider_call = True
                if not offers:
                    status = "UNAVAILABLE"
                    results[travel_date] = CalendarPrice(
                        date=travel_date,
                        price=None,
                        currency=currency,
                        state=status,
                    )
                    return
                lowest = min(offer.total_price for offer in offers)
                status = "CACHED" if cache_hit else "LOADED"
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
                observed_at = datetime.now(UTC)
                results[travel_date] = CalendarPrice(
                    date=travel_date,
                    price=lowest,
                    currency=currency,
                    state=status,
                    observed_at=observed_at,
                )
                if not cache_hit:
                    async with pending_lock:
                        pending_observations.append(
                            {
                                "market_key": self.store.market_key(**common),
                                "travel_date": travel_date,
                                "direction": direction.upper(),
                                "observed_at": observed_at,
                                "lowest_direct_price": lowest,
                                "currency": currency.upper(),
                                "passenger_context": f'{{"adults":{adults},"children":{children}}}',
                                "source_search_key": source_key,
                            }
                        )
            except (TimeoutError, SearchAPIError, NotImplementedError):
                calls += int(not provider_call)
                failures += 1
                elapsed = (perf_counter() - clock) * 1000
                provider_durations.append(elapsed)
                fallback = stale.get(travel_date)
                status = "STALE_AVAILABLE" if fallback is not None else "ERROR"
                results[travel_date] = CalendarPrice(
                    date=travel_date,
                    price=fallback.lowest_direct_price if fallback else None,
                    currency=fallback.currency if fallback else currency,
                    state=status,
                    observed_at=fallback.observed_at if fallback else None,
                )
            finally:
                completed_at = datetime.now(UTC)
                request_timings.append(
                    CalendarRequestTiming(
                        date=travel_date,
                        started_at=started_at,
                        completed_at=completed_at,
                        duration_ms=round((perf_counter() - clock) * 1000, 2),
                        status=status,
                        cache_hit=cache_hit,
                    )
                )

        await asyncio.gather(*(load(item) for item in missing))

        if pending_observations:
            db_clock = perf_counter()
            await asyncio.to_thread(self.store.put_many, pending_observations)
            postgres_ms += (perf_counter() - db_clock) * 1000

        ordered_durations = sorted(provider_durations)
        p95 = (
            ordered_durations[max(0, math.ceil(len(ordered_durations) * 0.95) - 1)]
            if ordered_durations
            else 0
        )
        ordered_results = [results[item] for item in dates]
        return CalendarResponse(
            dates=classify_calendar_prices(ordered_results),
            calendar_provider_calls_this_invocation=calls,
            calendar_calls_avoided=avoided,
            failures=failures,
            request_timings=sorted(request_timings, key=lambda item: item.date),
            calendar_calls_total=calls,
            calendar_calls_concurrent_peak=peak,
            calendar_provider_median_ms=round(median(provider_durations), 2)
            if provider_durations
            else 0,
            calendar_provider_p95_ms=round(p95, 2),
            calendar_provider_slowest_ms=round(max(provider_durations, default=0), 2),
            calendar_total_duration_ms=round((perf_counter() - total_clock) * 1000, 2),
            calendar_postgres_total_ms=round(postgres_ms, 2),
        )
