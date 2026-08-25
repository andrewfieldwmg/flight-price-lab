"""PostgreSQL-only test database safety and isolation."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

from flight_price_lab.storage.database import normalize_database_url


def _configured_url(name: str) -> str | None:
    value = os.getenv(name) or dotenv_values(".env").get(name)
    return str(value) if value else None


def _database_name(url: str) -> str:
    return urlsplit(normalize_database_url(url)).path.removeprefix("/")


def _database_identity(url: str) -> tuple[str | None, int | None, str]:
    parsed = urlsplit(normalize_database_url(url))
    return parsed.hostname, parsed.port, _database_name(url)


DEV_DATABASE_URL = _configured_url("DATABASE_URL")
TEST_DATABASE_URL = _configured_url("TEST_DATABASE_URL")

if not DEV_DATABASE_URL or not TEST_DATABASE_URL:
    raise pytest.UsageError("DATABASE_URL and TEST_DATABASE_URL are required")
if _database_identity(DEV_DATABASE_URL) == _database_identity(TEST_DATABASE_URL):
    raise pytest.UsageError(
        "Refusing to run tests: TEST_DATABASE_URL targets the development database"
    )

# Application instances created by tests must always use the isolated test database.
os.environ["DATABASE_URL"] = normalize_database_url(TEST_DATABASE_URL)
os.environ["TEST_DATABASE_URL"] = normalize_database_url(TEST_DATABASE_URL)


def pytest_sessionstart(session: pytest.Session) -> None:
    del session
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", normalize_database_url(TEST_DATABASE_URL))
    command.upgrade(config, "head")


@pytest.fixture(autouse=True)
def clean_persistence_tables() -> None:
    engine = create_engine(normalize_database_url(TEST_DATABASE_URL))
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE search_cache, booking_candidates, "
                "market_observation, flight_observation RESTART IDENTITY"
            )
        )
    engine.dispose()
