"""Airport metadata used to interpret provider-local timestamps."""

from datetime import UTC, datetime
from functools import cache, lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import airportsdata


class UnknownAirportError(ValueError):
    """Raised when an airport has no usable IANA timezone."""


class AmbiguousLocalTimeError(ValueError):
    """Raised when a wall time maps to two offsets and no offset was supplied."""


class NonexistentLocalTimeError(ValueError):
    """Raised when a wall time falls inside a spring-forward gap."""


@lru_cache(maxsize=1)
def _iata_airports() -> dict[str, dict[str, object]]:
    return airportsdata.load("IATA")


@cache
def airport_timezone(iata_code: str) -> ZoneInfo:
    """Resolve an IATA airport code to its IANA timezone."""

    code = iata_code.strip().upper()
    airport = _iata_airports().get(code)
    timezone_name = airport.get("tz") if airport else None
    if not isinstance(timezone_name, str) or not timezone_name:
        raise UnknownAirportError(f"no timezone found for airport {code!r}")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise UnknownAirportError(
            f"timezone {timezone_name!r} for airport {code!r} is unavailable"
        ) from None


def resolve_airport_local_datetime(iata_code: str, wall_time: datetime) -> datetime:
    """Attach an airport timezone only when the local wall time is unambiguous."""

    if wall_time.tzinfo is not None:
        raise ValueError("wall_time must be naive")
    timezone = airport_timezone(iata_code)
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = wall_time.replace(tzinfo=timezone, fold=fold)
        round_trip = candidate.astimezone(UTC).astimezone(timezone)
        if round_trip.replace(tzinfo=None) == wall_time and round_trip.fold == fold:
            candidates.append(candidate)

    if not candidates:
        raise NonexistentLocalTimeError(
            f"local time {wall_time.isoformat()} does not exist at {iata_code.upper()}"
        )
    if len(candidates) > 1 and candidates[0].utcoffset() != candidates[1].utcoffset():
        raise AmbiguousLocalTimeError(
            f"local time {wall_time.isoformat()} is ambiguous at {iata_code.upper()}"
        )
    return candidates[0]
