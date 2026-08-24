"""Provider gateway used by API orchestration."""

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from sqlalchemy.exc import SQLAlchemyError

from flight_price_lab.api.models import CalendarPrice
from flight_price_lab.api.search_logging import (
    development_diagnostics_enabled,
    search_log,
)
from flight_price_lab.models.flight import FlightOffer
from flight_price_lab.providers.searchapi import SearchAPIClient
from flight_price_lab.providers.searchapi_mapper import normalize_searchapi_response
from flight_price_lab.storage.database import (
    CachedSearch,
    CacheLookup,
    SearchResponseCache,
    canonical_search_json,
    canonical_search_key,
)


class _VolatileResponseCache:
    """Process-local fallback when Vercel's filesystem cannot host SQLite/raw JSON."""

    def __init__(self, ttl: timedelta = timedelta(minutes=60)) -> None:
        self.ttl = ttl
        self.entries: dict[str, tuple[CachedSearch, datetime, datetime]] = {}

    def lookup(self, parameters: dict[str, object]) -> CacheLookup:
        entry = self.entries.get(canonical_search_key(parameters))
        if entry is None:
            return CacheLookup("miss", None, None, None, None)
        cached, created, expires = entry
        now = datetime.now(UTC)
        age = (now - created).total_seconds()
        if expires <= now:
            return CacheLookup("expired", None, created, expires, age)
        return CacheLookup("hit", cached, created, expires, age)

    def put(
        self,
        parameters: dict[str, object],
        payload: dict[str, object],
        *,
        result_count: int,
    ) -> CachedSearch:
        created = datetime.now(UTC)
        cached = CachedSearch(payload, Path("volatile-response.json"), result_count)
        self.entries[canonical_search_key(parameters)] = (
            cached,
            created,
            created + self.ttl,
        )
        return cached


@dataclass(frozen=True)
class ProviderSearchResult:
    offers: list[FlightOffer]
    backend_cache_hits: int
    backend_cache_misses: int
    provider_calls: int
    provider_calls_avoided: int


class ProviderGateway(Protocol):
    async def search_direct(
        self,
        *,
        origins: Sequence[str],
        destinations: Sequence[str],
        travel_date: date,
        adults: int,
        children: int,
        currency: str,
        cabin_bags: int,
        checked_bags: int,
        bypass_cache: bool,
        trip_id: str,
        trip_search_key: str,
    ) -> list[FlightOffer] | ProviderSearchResult: ...

    async def calendar(
        self,
        *,
        origins: Sequence[str],
        destinations: Sequence[str],
        date_from: date,
        date_to: date,
        adults: int,
        children: int,
        currency: str,
    ) -> list[CalendarPrice]: ...


class SearchAPIProviderGateway:
    def __init__(
        self, client: SearchAPIClient, cache: SearchResponseCache | None = None
    ) -> None:
        self._client = client
        if cache is not None:
            self._cache = cache
        else:
            try:
                self._cache = SearchResponseCache()
            except (OSError, SQLAlchemyError):
                self._cache = _VolatileResponseCache()
        self._cache_locks: dict[str, asyncio.Lock] = {}

    async def search_direct(self, **arguments: object) -> ProviderSearchResult:
        origins = tuple(str(item) for item in arguments.pop("origins"))  # type: ignore[union-attr]
        destinations = tuple(str(item) for item in arguments.pop("destinations"))  # type: ignore[union-attr]
        travel_date = arguments.pop("travel_date")
        assert isinstance(travel_date, date)
        bypass_cache = bool(arguments.pop("bypass_cache", False))
        trip_id = str(arguments.pop("trip_id", ""))
        trip_search_key = str(arguments.pop("trip_search_key", ""))
        cabin_bags = int(arguments.pop("cabin_bags"))
        checked_bags = int(arguments.pop("checked_bags"))
        parameters = {
            "origins": list(origins),
            "destinations": list(destinations),
            "date": travel_date.isoformat(),
            "adults": arguments["adults"],
            "children": arguments["children"],
            "currency": arguments["currency"],
            "flight_type": "one_way",
            "stops": "nonstop",
        }
        if cabin_bags:
            parameters["carry_on_bags"] = cabin_bags
        if checked_bags:
            parameters["checked_bags"] = checked_bags
        cache_key = canonical_search_key(parameters)
        common = {
            "trip_id": trip_id,
            "search_key": trip_search_key,
            "provider_search_key": cache_key,
            "provider_search_key_short": cache_key[:12],
            "cache_bypass": bypass_cache,
        }
        lock = self._cache_locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            if bypass_cache:
                lookup = CacheLookup("miss", None, None, None, None)
            else:
                try:
                    lookup = await asyncio.to_thread(self._cache.lookup, parameters)
                except (OSError, SQLAlchemyError):
                    fallback = _VolatileResponseCache()
                    self._cache = fallback
                    lookup = fallback.lookup(parameters)
            lookup_fields = {
                **common,
                "created_at": lookup.created_at,
                "expires_at": lookup.expires_at,
                "age_seconds": lookup.age_seconds,
                "fresh": lookup.fresh,
            }
            if development_diagnostics_enabled():
                lookup_fields["canonical_request"] = json.loads(
                    canonical_search_json(parameters)
                )
            search_log("BACKEND_CACHE_LOOKUP", **lookup_fields)
            search_log(
                f"BACKEND_CACHE_{lookup.status.upper()}",
                **common,
                created_at=lookup.created_at,
                expires_at=lookup.expires_at,
                age_seconds=lookup.age_seconds,
                fresh=lookup.fresh,
                reason="explicit_bypass" if bypass_cache else None,
            )
            cached = lookup.cached
            provider_calls = 0
            if cached is None:
                search_log("PROVIDER_CALL_PLANNED", **common)
                search_log("PROVIDER_CALL_STARTED", **common)
                try:
                    payload = await asyncio.to_thread(
                        self._client.search_one_way,
                        departure_id=",".join(origins),
                        arrival_id=",".join(destinations),
                        outbound_date=travel_date,
                        carry_on_bags=cabin_bags or None,
                        checked_bags=checked_bags or None,
                        stops="nonstop",
                        **arguments,
                    )
                except Exception as error:
                    search_log(
                        "PROVIDER_CALL_FAILED",
                        **common,
                        error_type=type(error).__name__,
                    )
                    raise
                result_count = sum(
                    len(payload.get(bucket, []))
                    for bucket in ("best_flights", "other_flights")
                    if isinstance(payload.get(bucket), list)
                )
                try:
                    cached = await asyncio.to_thread(
                        self._cache.put,
                        parameters,
                        payload,
                        result_count=result_count,
                    )
                except (OSError, SQLAlchemyError):
                    fallback = _VolatileResponseCache()
                    self._cache = fallback
                    cached = fallback.put(
                        parameters, payload, result_count=result_count
                    )
                provider_calls = 1
                search_log(
                    "PROVIDER_CALL_SUCCEEDED", **common, result_count=result_count
                )
            else:
                search_log("PROVIDER_CALL_SKIPPED_CACHE", **common)
                search_log(
                    "RESULT_RESTORED_FROM_CACHE",
                    **common,
                    result_count=cached.result_count,
                )
        payload = cached.payload
        offers, _ = normalize_searchapi_response(
            payload, raw_reference=str(cached.raw_response_path)
        )
        return ProviderSearchResult(
            offers=[offer for offer in offers if len(offer.legs) == 1],
            backend_cache_hits=int(provider_calls == 0),
            backend_cache_misses=int(provider_calls == 1),
            provider_calls=provider_calls,
            provider_calls_avoided=int(provider_calls == 0),
        )

    async def calendar(self, **arguments: object) -> list[CalendarPrice]:
        del arguments
        raise NotImplementedError(
            "SearchAPI calendar data is not available through the current client"
        )
