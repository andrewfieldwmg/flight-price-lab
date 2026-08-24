"""Persistent SQLite cache for raw provider search responses."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class SearchCacheEntry(Base):
    __tablename__ = "search_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    raw_response_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    result_count: Mapped[int] = mapped_column(Integer, nullable=False)


@dataclass(frozen=True)
class CachedSearch:
    payload: dict[str, Any]
    raw_response_path: Path
    result_count: int


@dataclass(frozen=True)
class CacheLookup:
    status: str
    cached: CachedSearch | None
    created_at: datetime | None
    expires_at: datetime | None
    age_seconds: float | None

    @property
    def fresh(self) -> bool:
        return self.status == "hit"


def canonical_search_json(parameters: dict[str, Any]) -> str:
    """Serialize only provider-affecting search fields deterministically."""

    canonical = dict(parameters)
    canonical["origins"] = sorted(set(canonical["origins"]))
    canonical["destinations"] = sorted(set(canonical["destinations"]))
    if "included_connecting_airports" in canonical:
        canonical["included_connecting_airports"] = sorted(
            set(canonical["included_connecting_airports"])
        )
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"))


def canonical_search_key(parameters: dict[str, Any]) -> str:
    return sha256(canonical_search_json(parameters).encode()).hexdigest()


class SearchResponseCache:
    """SQLite cache index whose raw JSON files remain after entries expire."""

    def __init__(
        self,
        database_url: str = "sqlite:///data/search_cache.sqlite3",
        *,
        raw_root: str | Path = "data/raw/searchapi",
        ttl: timedelta = timedelta(minutes=60),
    ) -> None:
        self.engine = create_engine(database_url)
        self.raw_root = Path(raw_root)
        self.ttl = ttl
        Base.metadata.create_all(self.engine)

    def get(
        self, parameters: dict[str, Any], *, now: datetime | None = None
    ) -> CachedSearch | None:
        return self.lookup(parameters, now=now).cached

    def lookup(
        self, parameters: dict[str, Any], *, now: datetime | None = None
    ) -> CacheLookup:
        current = now or datetime.now(UTC)
        with Session(self.engine) as session:
            entry = session.get(SearchCacheEntry, canonical_search_key(parameters))
            if entry is None:
                return CacheLookup("miss", None, None, None, None)
            created_at = entry.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            expires_at = entry.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            path = Path(entry.raw_response_path)
            age_seconds = (current - created_at).total_seconds()
            if expires_at <= current:
                return CacheLookup("expired", None, created_at, expires_at, age_seconds)
            if not path.is_file():
                return CacheLookup("miss", None, created_at, expires_at, age_seconds)
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return CacheLookup("miss", None, created_at, expires_at, age_seconds)
            return CacheLookup(
                "hit",
                CachedSearch(payload, path, entry.result_count),
                created_at,
                expires_at,
                age_seconds,
            )

    def put(
        self,
        parameters: dict[str, Any],
        payload: dict[str, Any],
        *,
        result_count: int,
        now: datetime | None = None,
    ) -> CachedSearch:
        created = now or datetime.now(UTC)
        origins = "-".join(sorted(set(parameters["origins"])))
        destinations = "-".join(sorted(set(parameters["destinations"])))
        travel_date = parameters["date"]
        folder = self.raw_root / created.date().isoformat()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (
            f"{created.strftime('%Y%m%dT%H%M%S%fZ')}_{origins}_{destinations}_"
            f"{travel_date}.json"
        )
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        request_json = canonical_search_json(parameters)
        entry = SearchCacheEntry(
            cache_key=canonical_search_key(parameters),
            provider="SearchAPI",
            request_json=request_json,
            raw_response_path=str(path.resolve()),
            created_at=created,
            expires_at=created + self.ttl,
            result_count=result_count,
        )
        with Session(self.engine) as session:
            session.merge(entry)
            session.commit()
        return CachedSearch(payload, path.resolve(), result_count)

    def entries(self) -> list[SearchCacheEntry]:
        with Session(self.engine) as session:
            return list(session.scalars(select(SearchCacheEntry)))

    def seed_capture(self, path: str | Path) -> bool:
        """Index a capture only when every canonical parameter is reconstructable."""

        capture = Path(path)
        payload = json.loads(capture.read_text(encoding="utf-8"))
        search = payload.get("search_parameters") if isinstance(payload, dict) else None
        if not isinstance(search, dict):
            return False
        bag_match = re.search(r"_co(\d+)_cb(\d+)\.json$", capture.name)
        carry_on = search.get("carry_on_bags")
        checked = search.get("checked_bags")
        if bag_match is not None:
            carry_on = carry_on if carry_on is not None else bag_match.group(1)
            checked = checked if checked is not None else bag_match.group(2)
        required = {
            "departure_id": search.get("departure_id"),
            "arrival_id": search.get("arrival_id"),
            "outbound_date": search.get("outbound_date"),
            "adults": search.get("adults"),
            "children": search.get("children"),
            "currency": search.get("currency"),
            "flight_type": search.get("flight_type"),
            "stops": search.get("stops"),
        }
        if any(value is None for value in required.values()):
            return False
        parameters = {
            "origins": str(required["departure_id"]).split(","),
            "destinations": str(required["arrival_id"]).split(","),
            "date": str(required["outbound_date"]),
            "adults": int(required["adults"]),
            "children": int(required["children"]),
            "currency": str(required["currency"]).upper(),
            "flight_type": str(required["flight_type"]),
            "stops": str(required["stops"]),
        }
        if carry_on is not None and int(carry_on):
            parameters["carry_on_bags"] = int(carry_on)
        if checked is not None and int(checked):
            parameters["checked_bags"] = int(checked)
        result_count = sum(
            len(payload.get(bucket, []))
            for bucket in ("best_flights", "other_flights")
            if isinstance(payload.get(bucket), list)
        )
        created = datetime.now(UTC)
        entry = SearchCacheEntry(
            cache_key=canonical_search_key(parameters),
            provider="SearchAPI",
            request_json=canonical_search_json(parameters),
            raw_response_path=str(capture.resolve()),
            created_at=created,
            expires_at=created + self.ttl,
            result_count=result_count,
        )
        with Session(self.engine) as session:
            session.merge(entry)
            session.commit()
        return True
