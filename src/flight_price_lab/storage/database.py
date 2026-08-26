"""PostgreSQL persistence for provider cache and durable observations."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any

from dotenv import dotenv_values
from sqlalchemy import (
    Boolean,
    Date,
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
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class SearchSessionEntry(Base):
    """Durable search metadata and latest normalized recovery snapshot."""

    __tablename__ = "search_sessions"

    search_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    search_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    observation_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    offer_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    carrier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    arrival_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    adults: Mapped[int | None] = mapped_column(Integer, nullable=True)
    children: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passenger_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SearchObservationRun(Base):
    __tablename__ = "search_observation_run"
    __table_args__ = (
        Index("ix_search_observation_run_search_key_time", "search_key", "observed_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    search_id: Mapped[str] = mapped_column(String(64), nullable=False)
    search_key: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    adults: Mapped[int] = mapped_column(Integer, nullable=False)
    children: Mapped[int] = mapped_column(Integer, nullable=False)
    passenger_count: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)


class TripOptionObservation(Base):
    __tablename__ = "trip_option_observation"
    __table_args__ = (
        Index(
            "ix_trip_option_observation_context_time",
            "trip_option_fingerprint",
            "direction",
            "passenger_count",
            "currency",
            "observed_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    observation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trip_option_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    base_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    adults: Mapped[int] = mapped_column(Integer, nullable=False)
    children: Mapped[int] = mapped_column(Integer, nullable=False)
    passenger_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_nonstop: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_self_transfer: Mapped[bool] = mapped_column(Boolean, nullable=False)
    constituent_fingerprints_json: Mapped[str] = mapped_column(Text, nullable=False)


class CalendarPriceObservation(Base):
    __tablename__ = "calendar_price_observation"
    __table_args__ = (
        Index(
            "ix_calendar_price_market_date_observed",
            "market_key",
            "travel_date",
            "observed_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_key: Mapped[str] = mapped_column(String(64), nullable=False)
    travel_date: Mapped[date] = mapped_column(Date, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lowest_direct_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    passenger_context: Mapped[str] = mapped_column(Text, nullable=False)
    source_search_key: Mapped[str] = mapped_column(String(64), nullable=False)


class CalendarPriceStore:
    """Immutable direct-fare observations with a 24-hour freshness window."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        engine: Engine | None = None,
        ttl: timedelta = timedelta(hours=24),
    ) -> None:
        self.engine = engine or create_database_engine(database_url)
        self.ttl = ttl

    @staticmethod
    def market_key(
        origins: list[str] | tuple[str, ...],
        destinations: list[str] | tuple[str, ...],
        adults: int,
        children: int,
        currency: str,
        direction: str,
    ) -> str:
        payload = {
            "origins": sorted(set(origins)),
            "destinations": sorted(set(destinations)),
            "adults": adults,
            "children": children,
            "currency": currency.upper(),
            "direction": direction.upper(),
            "stops": "nonstop",
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def get_fresh(
        self,
        *,
        origins: list[str] | tuple[str, ...],
        destinations: list[str] | tuple[str, ...],
        travel_date: date,
        adults: int,
        children: int,
        currency: str,
        direction: str,
        now: datetime | None = None,
    ) -> CalendarPriceObservation | None:
        current = now or datetime.now(UTC)
        key = self.market_key(
            origins, destinations, adults, children, currency, direction
        )
        with Session(self.engine) as session:
            entry = session.scalar(
                select(CalendarPriceObservation)
                .where(
                    CalendarPriceObservation.market_key == key,
                    CalendarPriceObservation.travel_date == travel_date,
                    CalendarPriceObservation.observed_at > current - self.ttl,
                )
                .order_by(CalendarPriceObservation.observed_at.desc())
                .limit(1)
            )
            if entry is not None:
                session.expunge(entry)
            return entry

    def get_fresh_many(
        self,
        *,
        origins: list[str] | tuple[str, ...],
        destinations: list[str] | tuple[str, ...],
        travel_dates: list[date] | tuple[date, ...],
        adults: int,
        children: int,
        currency: str,
        direction: str,
        now: datetime | None = None,
    ) -> dict[date, CalendarPriceObservation]:
        """Load the newest fresh observation for every requested date in one query."""

        if not travel_dates:
            return {}
        current = now or datetime.now(UTC)
        key = self.market_key(
            origins, destinations, adults, children, currency, direction
        )
        with Session(self.engine) as session:
            rows = session.scalars(
                select(CalendarPriceObservation)
                .where(
                    CalendarPriceObservation.market_key == key,
                    CalendarPriceObservation.travel_date.in_(travel_dates),
                    CalendarPriceObservation.observed_at > current - self.ttl,
                )
                .order_by(CalendarPriceObservation.observed_at.desc())
            ).all()
            result: dict[date, CalendarPriceObservation] = {}
            for row in rows:
                if row.travel_date not in result:
                    session.expunge(row)
                    result[row.travel_date] = row
            return result

    def get_latest(
        self,
        *,
        origins: list[str] | tuple[str, ...],
        destinations: list[str] | tuple[str, ...],
        travel_date: date,
        adults: int,
        children: int,
        currency: str,
        direction: str,
    ) -> CalendarPriceObservation | None:
        """Return the newest observation regardless of age for failure fallback."""

        key = self.market_key(
            origins, destinations, adults, children, currency, direction
        )
        with Session(self.engine) as session:
            entry = session.scalar(
                select(CalendarPriceObservation)
                .where(
                    CalendarPriceObservation.market_key == key,
                    CalendarPriceObservation.travel_date == travel_date,
                )
                .order_by(CalendarPriceObservation.observed_at.desc())
                .limit(1)
            )
            if entry is not None:
                session.expunge(entry)
            return entry

    def get_latest_many(
        self,
        *,
        origins: list[str] | tuple[str, ...],
        destinations: list[str] | tuple[str, ...],
        travel_dates: list[date] | tuple[date, ...],
        adults: int,
        children: int,
        currency: str,
        direction: str,
    ) -> dict[date, CalendarPriceObservation]:
        """Load stale fallbacks for requested dates in one query."""

        if not travel_dates:
            return {}
        key = self.market_key(
            origins, destinations, adults, children, currency, direction
        )
        with Session(self.engine) as session:
            rows = session.scalars(
                select(CalendarPriceObservation)
                .where(
                    CalendarPriceObservation.market_key == key,
                    CalendarPriceObservation.travel_date.in_(travel_dates),
                )
                .order_by(CalendarPriceObservation.observed_at.desc())
            ).all()
            result: dict[date, CalendarPriceObservation] = {}
            for row in rows:
                if row.travel_date not in result:
                    session.expunge(row)
                    result[row.travel_date] = row
            return result

    def put_many(
        self, entries: list[dict[str, object]]
    ) -> dict[date, CalendarPriceObservation]:
        """Persist a calendar range in one transaction."""

        if not entries:
            return {}
        rows = [CalendarPriceObservation(**entry) for entry in entries]
        with Session(self.engine) as session:
            session.add_all(rows)
            session.commit()
            for row in rows:
                session.refresh(row)
                session.expunge(row)
        return {row.travel_date: row for row in rows}

    def reuse_search_baselines_many(
        self,
        *,
        origins: list[str] | tuple[str, ...],
        destinations: list[str] | tuple[str, ...],
        travel_dates: list[date] | tuple[date, ...],
        adults: int,
        children: int,
        currency: str,
        direction: str,
        now: datetime | None = None,
    ) -> dict[date, CalendarPriceObservation]:
        """Reuse matching durable search baselines with one session scan/write."""

        if not travel_dates:
            return {}
        current = now or datetime.now(UTC)
        requested = set(travel_dates)
        is_return = direction.upper() == "RETURN"
        matches: dict[date, tuple[Decimal, datetime, str]] = {}
        with Session(self.engine) as session:
            rows = session.scalars(
                select(SearchSessionEntry)
                .where(
                    SearchSessionEntry.status.in_(("completed", "partial_failure")),
                    SearchSessionEntry.updated_at > current - self.ttl,
                )
                .order_by(SearchSessionEntry.updated_at.desc())
            )
            for row in rows:
                request = json.loads(row.request_json)
                expected_date = request.get(
                    "return_date" if is_return else "outbound_date"
                )
                try:
                    candidate_date = date.fromisoformat(expected_date)
                except (TypeError, ValueError):
                    continue
                if candidate_date not in requested or candidate_date in matches:
                    continue
                if (
                    sorted(
                        request.get("destinations" if is_return else "origins") or []
                    )
                    != sorted(origins)
                    or sorted(
                        request.get("origins" if is_return else "destinations") or []
                    )
                    != sorted(destinations)
                    or request.get("adults") != adults
                    or request.get("children") != children
                    or request.get("currency") != currency.upper()
                ):
                    continue
                snapshot = json.loads(row.snapshot_json)
                result = snapshot.get("return" if is_return else "outbound") or {}
                baseline = result.get("baseline")
                if (
                    isinstance(baseline, dict)
                    and baseline.get("base_price") is not None
                ):
                    matches[candidate_date] = (
                        Decimal(str(baseline["base_price"])),
                        row.completed_at or row.updated_at,
                        row.search_key,
                    )
        if not matches:
            return {}
        return self.put_many(
            [
                {
                    "market_key": self.market_key(
                        origins, destinations, adults, children, currency, direction
                    ),
                    "travel_date": travel_date,
                    "direction": direction.upper(),
                    "observed_at": observed_at,
                    "lowest_direct_price": price,
                    "currency": currency.upper(),
                    "passenger_context": json.dumps(
                        {"adults": adults, "children": children}, separators=(",", ":")
                    ),
                    "source_search_key": search_key,
                }
                for travel_date, (price, observed_at, search_key) in matches.items()
            ]
        )

    def put(
        self,
        *,
        origins: list[str] | tuple[str, ...],
        destinations: list[str] | tuple[str, ...],
        travel_date: date,
        direction: str,
        lowest_direct_price: Decimal,
        currency: str,
        adults: int,
        children: int,
        source_search_key: str,
        observed_at: datetime | None = None,
    ) -> CalendarPriceObservation:
        entry = CalendarPriceObservation(
            market_key=self.market_key(
                origins, destinations, adults, children, currency, direction
            ),
            travel_date=travel_date,
            direction=direction.upper(),
            observed_at=observed_at or datetime.now(UTC),
            lowest_direct_price=lowest_direct_price,
            currency=currency.upper(),
            passenger_context=json.dumps(
                {"adults": adults, "children": children}, separators=(",", ":")
            ),
            source_search_key=source_search_key,
        )
        with Session(self.engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            session.expunge(entry)
        return entry

    def reuse_search_baseline(
        self,
        *,
        origins: list[str] | tuple[str, ...],
        destinations: list[str] | tuple[str, ...],
        travel_date: date,
        adults: int,
        children: int,
        currency: str,
        direction: str,
        now: datetime | None = None,
    ) -> CalendarPriceObservation | None:
        """Read through recent durable search snapshots before calling the provider."""

        current = now or datetime.now(UTC)
        with Session(self.engine) as session:
            rows = session.scalars(
                select(SearchSessionEntry)
                .where(
                    SearchSessionEntry.status.in_(("completed", "partial_failure")),
                    SearchSessionEntry.updated_at > current - self.ttl,
                )
                .order_by(SearchSessionEntry.updated_at.desc())
            )
            for row in rows:
                request = json.loads(row.request_json)
                snapshot = json.loads(row.snapshot_json)
                is_return = direction.upper() == "RETURN"
                expected_origins = request.get(
                    "destinations" if is_return else "origins"
                )
                expected_destinations = request.get(
                    "origins" if is_return else "destinations"
                )
                expected_date = request.get(
                    "return_date" if is_return else "outbound_date"
                )
                if (
                    sorted(expected_origins or []) != sorted(origins)
                    or sorted(expected_destinations or []) != sorted(destinations)
                    or expected_date != travel_date.isoformat()
                    or request.get("adults") != adults
                    or request.get("children") != children
                    or request.get("currency") != currency.upper()
                ):
                    continue
                result = snapshot.get("return" if is_return else "outbound") or {}
                baseline = result.get("baseline")
                if not isinstance(baseline, dict) or baseline.get("base_price") is None:
                    continue
                observed_at = row.completed_at or row.updated_at
                return self.put(
                    origins=origins,
                    destinations=destinations,
                    travel_date=travel_date,
                    direction=direction,
                    lowest_direct_price=Decimal(str(baseline["base_price"])),
                    currency=currency,
                    adults=adults,
                    children=children,
                    source_search_key=row.search_key,
                    observed_at=observed_at,
                )
        return None


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
        LOGGER.critical(
            "DATABASE_URL is required; PostgreSQL is the only runtime database"
        )
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
        engine: Engine | None = None,
        raw_root: str | Path = "data/raw/searchapi",
        ttl: timedelta = timedelta(hours=24),
    ) -> None:
        self.engine = engine or create_database_engine(database_url)
        self.raw_root = Path(raw_root)
        self.ttl = ttl
        self._write_timings: dict[str, dict[str, float]] = {}

    def write_timing(self, cache_key: str) -> dict[str, float] | None:
        return self._write_timings.get(cache_key)

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
            age_seconds = (current - created_at).total_seconds()
            if expires_at <= current:
                return CacheLookup("expired", None, created_at, expires_at, age_seconds)
            path = Path(entry.raw_response_path)
            if entry.response_json:
                payload = json.loads(entry.response_json)
            elif path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
            else:
                return CacheLookup("miss", None, created_at, expires_at, age_seconds)
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
        stem = (
            f"{created.strftime('%Y%m%dT%H%M%S%fZ')}_{origins}_{destinations}_"
            f"{travel_date}"
        )
        contents = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        path = Path("unpersisted-searchapi-response.json")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            collision = 0
            while True:
                suffix = "" if collision == 0 else f"_{collision}"
                path = folder / f"{stem}{suffix}.json"
                try:
                    with path.open("x", encoding="utf-8", newline="\n") as capture:
                        capture.write(contents)
                    break
                except FileExistsError:
                    collision += 1
        except OSError:
            LOGGER.warning(
                "Raw provider capture unavailable; PostgreSQL cache retained"
            )
        request_json = canonical_search_json(parameters)
        entry = SearchCacheEntry(
            cache_key=canonical_search_key(parameters),
            provider="SearchAPI",
            request_json=request_json,
            raw_response_path=str(path.resolve()),
            response_json=contents,
            created_at=created,
            expires_at=created + self.ttl,
            result_count=result_count,
        )
        acquire_clock = perf_counter()
        connection = self.engine.connect()
        acquire_ms = (perf_counter() - acquire_clock) * 1000
        session = Session(bind=connection, expire_on_commit=False)
        try:
            query_clock = perf_counter()
            session.merge(entry)
            session.flush()
            query_ms = (perf_counter() - query_clock) * 1000
            commit_clock = perf_counter()
            session.commit()
            commit_ms = (perf_counter() - commit_clock) * 1000
        finally:
            session.close()
            connection.close()
        self._write_timings[entry.cache_key] = {
            "connection_acquire_ms": acquire_ms,
            "query_ms": query_ms,
            "commit_ms": commit_ms,
        }
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
            response_json=json.dumps(payload, ensure_ascii=False),
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

    def __init__(
        self, database_url: str | None = None, *, engine: Engine | None = None
    ) -> None:
        self.engine = engine or create_database_engine(database_url)

    def put(
        self,
        search_id: str,
        option_id: str,
        offers: tuple[FlightOffer, ...],
        *,
        session: Session | None = None,
        commit: bool = True,
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
        if session is not None:
            session.add(entry)
            if commit:
                session.commit()
            return
        with Session(self.engine) as owned_session:
            owned_session.merge(entry)
            owned_session.commit()

    def put_many(
        self,
        search_id: str,
        candidates: dict[str, tuple[FlightOffer, ...]],
        *,
        session: Session,
    ) -> None:
        from flight_price_lab.models.flight import FlightOffer

        entries: list[BookingCandidateEntry] = []
        for option_id, offers in candidates.items():
            serialized = []
            for offer in offers:
                if not isinstance(offer, FlightOffer):
                    raise TypeError("booking constituent must be a FlightOffer")
                serialized.append(offer.model_dump(mode="json"))
            entries.append(
                BookingCandidateEntry(
                    search_id=search_id,
                    option_id=option_id,
                    offers_json=json.dumps(serialized, separators=(",", ":")),
                    created_at=datetime.now(UTC),
                )
            )
        session.add_all(entries)

    def get(self, search_id: str, option_id: str) -> tuple[FlightOffer, ...] | None:
        from flight_price_lab.models.flight import FlightOffer

        with Session(self.engine) as session:
            entry = session.get(BookingCandidateEntry, (search_id, option_id))
            if entry is None:
                return None
            values = json.loads(entry.offers_json)
        return tuple(FlightOffer.model_validate(value) for value in values)


class SearchSessionStore:
    """Persist latest search state for cross-instance recovery and history access."""

    def __init__(
        self, database_url: str | None = None, *, engine: Engine | None = None
    ) -> None:
        self.engine = engine or create_database_engine(database_url)

    def create(
        self,
        snapshot: Any,
        request: Any,
        *,
        session: Session | None = None,
        commit: bool = True,
    ) -> None:
        now = datetime.now(UTC)
        entry = SearchSessionEntry(
            search_id=snapshot.search_id,
            search_key=snapshot.search_key,
            status=snapshot.status.value,
            request_json=request.model_dump_json(by_alias=True),
            snapshot_json=snapshot.model_dump_json(by_alias=True),
            created_at=now,
            updated_at=now,
            completed_at=None,
        )
        if session is not None:
            session.add(entry)
            if commit:
                session.commit()
            return
        with Session(self.engine) as owned_session:
            owned_session.add(entry)
            owned_session.commit()

    def update(
        self,
        snapshot: Any,
        *,
        session: Session | None = None,
        commit: bool = True,
    ) -> None:
        now = datetime.now(UTC)
        terminal = snapshot.status.value in {"completed", "partial_failure", "failed"}

        def apply(target: Session) -> None:
            entry = target.get(SearchSessionEntry, snapshot.search_id)
            if entry is None:
                raise KeyError(f"search session {snapshot.search_id} was not persisted")
            entry.status = snapshot.status.value
            entry.snapshot_json = snapshot.model_dump_json(by_alias=True)
            entry.updated_at = now
            if terminal:
                entry.completed_at = now
            if commit:
                target.commit()

        if session is not None:
            apply(session)
            return
        with Session(self.engine) as owned_session:
            apply(owned_session)

    def get(self, search_id: str) -> Any | None:
        from flight_price_lab.api.models import SearchSnapshot

        with Session(self.engine) as session:
            entry = session.get(SearchSessionEntry, search_id)
            if entry is None:
                return None
            value = entry.snapshot_json
        return SearchSnapshot.model_validate_json(value)
