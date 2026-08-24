from datetime import UTC, datetime, timedelta

import pytest

from flight_price_lab.airports import (
    AmbiguousLocalTimeError,
    NonexistentLocalTimeError,
    resolve_airport_local_datetime,
)


def test_resolves_ordinary_airport_local_timestamp() -> None:
    wall_time = datetime(2026, 1, 15, 12, 0, tzinfo=UTC).replace(tzinfo=None)
    resolved = resolve_airport_local_datetime("JFK", wall_time)

    assert resolved.tzinfo is not None
    assert resolved.utcoffset() == -timedelta(hours=5)


def test_rejects_nonexistent_spring_forward_timestamp() -> None:
    wall_time = datetime(2026, 3, 8, 2, 30, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(NonexistentLocalTimeError, match="does not exist"):
        resolve_airport_local_datetime("JFK", wall_time)


def test_rejects_ambiguous_fall_back_timestamp() -> None:
    wall_time = datetime(2026, 11, 1, 1, 30, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(AmbiguousLocalTimeError, match="ambiguous"):
        resolve_airport_local_datetime("JFK", wall_time)
