"""In-memory V1 search state behind a replaceable registry interface."""

import asyncio
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass, field
from time import perf_counter
from typing import Protocol

from flight_price_lab.api.models import SearchSnapshot, TripSearchRequest
from flight_price_lab.api.search_logging import search_log
from flight_price_lab.models.flight import FlightOffer


@dataclass(frozen=True)
class SearchEvent:
    sequence: int
    event: str
    data: dict[str, object]


class SearchRegistry(Protocol):
    async def create(
        self, snapshot: SearchSnapshot, request: TripSearchRequest | None = None
    ) -> None: ...
    async def get(self, search_id: str) -> SearchSnapshot | None: ...
    async def update(self, snapshot: SearchSnapshot) -> None: ...
    async def register_booking_candidate(
        self, search_id: str, option_id: str, offers: tuple[FlightOffer, ...]
    ) -> None: ...
    async def get_booking_candidate(
        self, search_id: str, option_id: str
    ) -> tuple[FlightOffer, ...] | None: ...
    async def publish(
        self, search_id: str, event: str, data: dict[str, object]
    ) -> None: ...
    def events(self, search_id: str) -> AsyncIterator[SearchEvent]: ...


class BookingCandidatePersistence(Protocol):
    def put(
        self, search_id: str, option_id: str, offers: tuple[FlightOffer, ...]
    ) -> None: ...

    def get(self, search_id: str, option_id: str) -> tuple[FlightOffer, ...] | None: ...


class SearchSessionPersistence(Protocol):
    def create(self, snapshot: SearchSnapshot, request: TripSearchRequest) -> None: ...
    def update(self, snapshot: SearchSnapshot) -> None: ...
    def get(self, search_id: str) -> SearchSnapshot | None: ...


@dataclass
class _Entry:
    snapshot: SearchSnapshot
    events: list[SearchEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    booking_candidates: dict[str, tuple[FlightOffer, ...]] = field(default_factory=dict)


class InMemorySearchRegistry:
    """Single-process development registry; unsuitable for horizontal scaling."""

    def __init__(
        self,
        candidate_store: BookingCandidatePersistence | None = None,
        search_store: SearchSessionPersistence | None = None,
    ) -> None:
        self._entries: dict[str, _Entry] = {}
        self._candidate_store = candidate_store
        self._search_store = search_store
        self._postgres_write_ms: dict[str, float] = {}
        self._reported_postgres_write_ms: dict[str, float] = {}

    def _record_postgres_write(
        self, search_id: str, table: str, started: float
    ) -> None:
        duration_ms = (perf_counter() - started) * 1000
        self._postgres_write_ms[search_id] = (
            self._postgres_write_ms.get(search_id, 0) + duration_ms
        )
        search_log(
            "POSTGRES_WRITE_TIMING",
            trip_id=search_id,
            table=table,
            duration_ms=round(duration_ms, 2),
        )

    def postgres_write_ms(self, search_id: str) -> float:
        return self._postgres_write_ms.get(search_id, 0)

    async def create(
        self, snapshot: SearchSnapshot, request: TripSearchRequest | None = None
    ) -> None:
        self._entries[snapshot.search_id] = _Entry(snapshot=deepcopy(snapshot))
        if self._search_store is not None and request is not None:
            started = perf_counter()
            self._search_store.create(snapshot, request)
            self._record_postgres_write(snapshot.search_id, "search_sessions", started)

    async def get(self, search_id: str) -> SearchSnapshot | None:
        entry = self._entries.get(search_id)
        if entry:
            return deepcopy(entry.snapshot)
        if self._search_store is not None:
            return self._search_store.get(search_id)
        return None

    async def update(self, snapshot: SearchSnapshot) -> None:
        self._entries[snapshot.search_id].snapshot = deepcopy(snapshot)
        if self._search_store is not None:
            started = perf_counter()
            self._search_store.update(snapshot)
            self._record_postgres_write(snapshot.search_id, "search_sessions", started)
            total = self.postgres_write_ms(snapshot.search_id)
            previous = self._reported_postgres_write_ms.get(snapshot.search_id, 0)
            snapshot.diagnostics.postgres_write_ms += total - previous
            self._reported_postgres_write_ms[snapshot.search_id] = total
            self._entries[snapshot.search_id].snapshot = deepcopy(snapshot)

    async def register_booking_candidate(
        self, search_id: str, option_id: str, offers: tuple[FlightOffer, ...]
    ) -> None:
        self._entries[search_id].booking_candidates[option_id] = deepcopy(offers)
        if self._candidate_store is not None:
            started = perf_counter()
            self._candidate_store.put(search_id, option_id, offers)
            self._record_postgres_write(search_id, "booking_candidates", started)

    async def get_booking_candidate(
        self, search_id: str, option_id: str
    ) -> tuple[FlightOffer, ...] | None:
        entry = self._entries.get(search_id)
        offers = entry.booking_candidates.get(option_id) if entry else None
        if offers:
            return deepcopy(offers)
        if self._candidate_store is not None:
            persisted = self._candidate_store.get(search_id, option_id)
            return deepcopy(persisted) if persisted else None
        return None

    async def publish(
        self, search_id: str, event: str, data: dict[str, object]
    ) -> None:
        entry = self._entries[search_id]
        async with entry.condition:
            entry.events.append(SearchEvent(len(entry.events) + 1, event, data))
            entry.condition.notify_all()

    async def events(self, search_id: str) -> AsyncIterator[SearchEvent]:
        entry = self._entries[search_id]
        position = 0
        while True:
            async with entry.condition:
                while position >= len(entry.events):
                    await entry.condition.wait()
                event = entry.events[position]
                position += 1
            yield event
            if event.event in ("search_completed", "search_failed"):
                return
