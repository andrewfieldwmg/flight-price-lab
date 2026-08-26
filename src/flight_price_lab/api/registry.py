"""In-memory V1 search state behind a replaceable registry interface."""

import asyncio
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass, field
from time import perf_counter
from typing import Protocol

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

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
    async def update(
        self,
        snapshot: SearchSnapshot,
        *,
        persist: bool = False,
        operation: str = "persist_partial_snapshot",
    ) -> None: ...
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
    def close_persistence(self, search_id: str) -> None: ...


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
        self._persistence_connections: dict[str, Connection] = {}
        self._persistence_sessions: dict[str, Session] = {}
        self._pending_candidates: dict[
            str, dict[str, tuple[FlightOffer, ...]]
        ] = {}

    def _record_database_operation(
        self,
        snapshot: SearchSnapshot,
        *,
        operation: str,
        table: str,
        connection_acquire_ms: float,
        query_ms: float,
        commit_ms: float,
    ) -> None:
        duration_ms = connection_acquire_ms + query_ms + commit_ms
        search_id = snapshot.search_id
        self._postgres_write_ms[search_id] = (
            self._postgres_write_ms.get(search_id, 0) + duration_ms
        )
        timing: dict[str, object] = {
            "operation": operation,
            "table": table,
            "duration_ms": round(duration_ms, 2),
            "connection_acquire_ms": round(connection_acquire_ms, 2),
            "query_ms": round(query_ms, 2),
            "commit_ms": round(commit_ms, 2),
        }
        snapshot.diagnostics.database_operations.append(timing)
        snapshot.diagnostics.postgres_write_ms += duration_ms
        search_log(
            "POSTGRES_OPERATION_TIMING",
            trip_id=search_id,
            **timing,
        )

    def postgres_write_ms(self, search_id: str) -> float:
        return self._postgres_write_ms.get(search_id, 0)

    async def create(
        self, snapshot: SearchSnapshot, request: TripSearchRequest | None = None
    ) -> None:
        self._entries[snapshot.search_id] = _Entry(snapshot=deepcopy(snapshot))
        if self._search_store is not None and request is not None:
            acquire_clock = perf_counter()
            connection = self._search_store.engine.connect()  # type: ignore[attr-defined]
            acquire_ms = (perf_counter() - acquire_clock) * 1000
            session = Session(bind=connection, expire_on_commit=False)
            self._persistence_connections[snapshot.search_id] = connection
            self._persistence_sessions[snapshot.search_id] = session
            self._pending_candidates[snapshot.search_id] = {}
            self._search_store.create(  # type: ignore[call-arg]
                snapshot, request, session=session, commit=False
            )
            query_clock = perf_counter()
            session.flush()
            query_ms = (perf_counter() - query_clock) * 1000
            commit_clock = perf_counter()
            session.commit()
            commit_ms = (perf_counter() - commit_clock) * 1000
            self._record_database_operation(
                snapshot,
                operation="create_search_session",
                table="search_sessions",
                connection_acquire_ms=acquire_ms,
                query_ms=query_ms,
                commit_ms=commit_ms,
            )
            self._entries[snapshot.search_id].snapshot = deepcopy(snapshot)

    async def get(self, search_id: str) -> SearchSnapshot | None:
        entry = self._entries.get(search_id)
        if entry:
            return deepcopy(entry.snapshot)
        if self._search_store is not None:
            return self._search_store.get(search_id)
        return None

    async def update(
        self,
        snapshot: SearchSnapshot,
        *,
        persist: bool = False,
        operation: str = "persist_partial_snapshot",
    ) -> None:
        self._entries[snapshot.search_id].snapshot = deepcopy(snapshot)
        if self._search_store is not None and persist:
            session = self._persistence_sessions[snapshot.search_id]
            pending = self._pending_candidates.get(snapshot.search_id, {})
            if pending:
                candidate_clock = perf_counter()
                self._candidate_store.put_many(  # type: ignore[attr-defined]
                    snapshot.search_id, pending, session=session
                )
                session.flush()
                candidate_query_ms = (perf_counter() - candidate_clock) * 1000
                self._record_database_operation(
                    snapshot,
                    operation="persist_booking_candidates",
                    table="booking_candidates",
                    connection_acquire_ms=0,
                    query_ms=candidate_query_ms,
                    commit_ms=0,
                )
                self._pending_candidates[snapshot.search_id] = {}
            self._search_store.update(  # type: ignore[call-arg]
                snapshot, session=session, commit=False
            )
            query_clock = perf_counter()
            session.flush()
            query_ms = (perf_counter() - query_clock) * 1000
            commit_clock = perf_counter()
            session.commit()
            commit_ms = (perf_counter() - commit_clock) * 1000
            self._record_database_operation(
                snapshot,
                operation=operation,
                table="search_sessions",
                connection_acquire_ms=0,
                query_ms=query_ms,
                commit_ms=commit_ms,
            )
            self._entries[snapshot.search_id].snapshot = deepcopy(snapshot)

    async def register_booking_candidate(
        self, search_id: str, option_id: str, offers: tuple[FlightOffer, ...]
    ) -> None:
        self._entries[search_id].booking_candidates[option_id] = deepcopy(offers)
        if self._candidate_store is not None:
            session = self._persistence_sessions.get(search_id)
            if session is not None:
                self._pending_candidates.setdefault(search_id, {})[option_id] = offers
            else:
                self._candidate_store.put(search_id, option_id, offers)

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

    def close_persistence(self, search_id: str) -> None:
        session = self._persistence_sessions.pop(search_id, None)
        connection = self._persistence_connections.pop(search_id, None)
        self._pending_candidates.pop(search_id, None)
        if session is not None:
            session.close()
        if connection is not None:
            connection.close()
