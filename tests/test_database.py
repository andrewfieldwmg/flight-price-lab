from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from flight_price_lab.storage.database import (
    FlightObservation,
    MarketObservation,
    create_database_engine,
    normalize_database_url,
)


def test_managed_postgres_urls_use_psycopg_three() -> None:
    assert normalize_database_url("postgres://user:pass@host/db") == (
        "postgresql+psycopg://user:pass@host/db"
    )
    assert normalize_database_url("postgresql://user:pass@host/db") == (
        "postgresql+psycopg://user:pass@host/db"
    )


def test_database_url_is_required_everywhere(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        create_database_engine()


def test_non_postgres_database_is_rejected() -> None:
    with pytest.raises(ValueError, match="PostgreSQL with psycopg 3 is required"):
        create_database_engine("sqlite:///:memory:")


def test_initial_migration_creates_current_and_history_schema() -> None:
    engine = create_database_engine()
    inspector = inspect(engine)
    assert {
        "search_cache",
        "booking_candidates",
        "market_observation",
        "flight_observation",
        "alembic_version",
    } <= set(inspector.get_table_names())
    assert {item["name"] for item in inspector.get_indexes("market_observation")} == {
        "ix_market_observation_market_time"
    }
    assert {item["name"] for item in inspector.get_indexes("flight_observation")} == {
        "ix_flight_observation_departure",
        "ix_flight_observation_fingerprint_time",
    }

    observed = datetime(2026, 8, 25, tzinfo=UTC)
    with Session(engine) as session:
        session.add(
            MarketObservation(
                market_key="LON-CAG:2026-12-18",
                observed_at=observed,
                cheapest_price=Decimal("486.00"),
                currency="GBP",
                source="SearchAPI",
            )
        )
        session.add(
            FlightObservation(
                flight_fingerprint="a" * 64,
                observed_at=observed,
                flight_number="U2 8309",
                origin="LGW",
                destination="MXP",
                departure_at=datetime(2026, 12, 18, 14, 25, tzinfo=UTC),
                price=Decimal("328.00"),
                currency="GBP",
                search_id=None,
            )
        )
        session.commit()
        assert session.scalar(select(MarketObservation.cheapest_price)) == Decimal(
            "486.00"
        )
        assert session.scalar(select(FlightObservation.flight_number)) == "U2 8309"

        session.add(
            FlightObservation(
                id=1,
                flight_fingerprint="b" * 64,
                observed_at=observed,
                flight_number="BA 534",
                origin="LHR",
                destination="NAP",
                departure_at=observed,
                price=Decimal("1391.00"),
                currency="GBP",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_postgres_connection_migrated_indexes_and_transactional_insert() -> None:
    engine = create_database_engine()
    with engine.connect() as connection:
        assert connection.dialect.name == "postgresql"
        tables = set(inspect(connection).get_table_names())
        assert {
            "search_cache",
            "booking_candidates",
            "market_observation",
            "flight_observation",
        } <= tables
        indexes = {
            item["name"]
            for item in inspect(connection).get_indexes("flight_observation")
        }
        assert {
            "ix_flight_observation_departure",
            "ix_flight_observation_fingerprint_time",
        } <= indexes
        connection.rollback()
        transaction = connection.begin()
        connection.execute(
            MarketObservation.__table__.insert().values(
                market_key="integration-test",
                observed_at=datetime.now(UTC),
                cheapest_price=Decimal("1.00"),
                currency="GBP",
                source="pytest",
            )
        )
        assert (
            connection.scalar(
                select(MarketObservation.market_key).where(
                    MarketObservation.market_key == "integration-test"
                )
            )
            == "integration-test"
        )
        transaction.rollback()
