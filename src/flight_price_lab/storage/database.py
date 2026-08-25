"""PostgreSQL persistence for provider cache and durable observations."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import dotenv_values
from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from flight_price_lab.models.flight import FlightOffer

LOGGER = logging.getLogger("flight_price_lab.database")


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


class BookingCandidateEntry(Base):
    """Internal booking lineage; provider action metadata never reaches the client."""

    __tablename__ = "booking_candidates"

    search_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    option_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    offers_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MarketObservation(Base):
    __tablename__ = "market_observation"
    __table_args__ = (
        Index("ix_market_observation_market_time", "market_key", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_key: Mapped[str] = mapped_column(String(255), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    cheapest_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)


class FlightObservation(Base):
    __tablename__ = "flight_observation"
    __table_args__ = (
        Index(
            "ix_flight_observation_fingerprint_time",
            "flight_fingerprint",
            "observed_at",
        ),
        Index("ix_flight_observation_departure", "departure_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flight_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    flight_number: Mapped[str] = mapped_column(String(20), nullable=False)
    origin: Mapped[str] = mapped_column(String(3), nullable=False)
    destination: Mapped[str] = mapped_column(String(3), nullable=False)
    departure_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    search_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


def normalize_database_url(url: str) -> str:
    """Use psycopg 3 for standard managed-Postgres URL forms."""

    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def configured_database_url() -> str:
    configured = os.getenv("DATABASE_URL") or dotenv_values(".env").get("DATABASE_URL")
    if not configured:
        LOGGER.critical("DATABASE_URL is required; PostgreSQL is the only runtime database")
        raise RuntimeError("DATABASE_URL is required")
    return normalize_database_url(configured)


def create_database_engine(database_url: str | None = None) -> Engine:
    url = normalize_database_url(database_url or configured_database_url())
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("PostgreSQL with psycopg 3 is required")
    options: dict[str, Any] = {
        "pool_pre_ping": True,
        "connect_args": {"prepare_threshold": None},
    }
    if os.getenv("VERCEL"):
        options["poolclass"] = NullPool
    return create_engine(url, **options)


def database_health(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - health must report rather than crash
        return False


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
    """PostgreSQL cache index whose raw JSON files remain after entries expire."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        raw_root: str | Path = "data/raw/searchapi",
        ttl: timedelta = timedelta(minutes=60),
    ) -> None:
        self.engine = create_database_engine(database_url)
        self.raw_root = Path(raw_root)
        self.ttl = ttl

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


class BookingCandidateStore:
    """Persist selected-option provenance independently from public snapshots."""

    def __init__(self, database_url: str | None = None) -> None:
        self.engine = create_database_engine(database_url)

    def put(
        self, search_id: str, option_id: str, offers: tuple[FlightOffer, ...]
    ) -> None:
        from flight_price_lab.models.flight import FlightOffer

        serialized = []
        for offer in offers:
            if not isinstance(offer, FlightOffer):
                raise TypeError("booking constituent must be a FlightOffer")
            serialized.append(offer.model_dump(mode="json"))
        entry = BookingCandidateEntry(
            search_id=search_id,
            option_id=option_id,
            offers_json=json.dumps(serialized, separators=(",", ":")),
            created_at=datetime.now(UTC),
        )
        with Session(self.engine) as session:
            session.merge(entry)
            session.commit()

    def get(self, search_id: str, option_id: str) -> tuple[FlightOffer, ...] | None:
        from flight_price_lab.models.flight import FlightOffer

        with Session(self.engine) as session:
            entry = session.get(BookingCandidateEntry, (search_id, option_id))
            if entry is None:
                return None
            values = json.loads(entry.offers_json)
        return tuple(FlightOffer.model_validate(value) for value in values)
