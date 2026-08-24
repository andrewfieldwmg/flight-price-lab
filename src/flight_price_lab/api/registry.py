"""In-memory V1 search state behind a replaceable registry interface."""

import asyncio
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Protocol

from flight_price_lab.api.models import SearchSnapshot


@dataclass(frozen=True)
class SearchEvent:
    sequence: int
    event: str
    data: dict[str, object]


class SearchRegistry(Protocol):
    async def create(self, snapshot: SearchSnapshot) -> None: ...
    async def get(self, search_id: str) -> SearchSnapshot | None: ...
    async def update(self, snapshot: SearchSnapshot) -> None: ...
    async def publish(
        self, search_id: str, event: str, data: dict[str, object]
    ) -> None: ...
    def events(self, search_id: str) -> AsyncIterator[SearchEvent]: ...


@dataclass
class _Entry:
    snapshot: SearchSnapshot
    events: list[SearchEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)


class InMemorySearchRegistry:
    """Single-process development registry; unsuitable for horizontal scaling."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    async def create(self, snapshot: SearchSnapshot) -> None:
        self._entries[snapshot.search_id] = _Entry(snapshot=deepcopy(snapshot))

    async def get(self, search_id: str) -> SearchSnapshot | None:
        entry = self._entries.get(search_id)
        return deepcopy(entry.snapshot) if entry else None

    async def update(self, snapshot: SearchSnapshot) -> None:
        self._entries[snapshot.search_id].snapshot = deepcopy(snapshot)

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
