"""Observed direct-route availability derived from provider responses."""

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RouteAvailability:
    origin: str
    destination: str
    direct_service_observed: bool
    source: str
    observed_at: datetime


class RouteAvailabilityIndex:
    """Latest observation for each directed airport pair."""

    def __init__(self) -> None:
        self._observations: dict[tuple[str, str], RouteAvailability] = {}

    def record(self, observation: RouteAvailability) -> None:
        key = (observation.origin, observation.destination)
        current = self._observations.get(key)
        if current is None or observation.observed_at >= current.observed_at:
            self._observations[key] = observation

    def get(self, origin: str, destination: str) -> RouteAvailability | None:
        return self._observations.get((origin.upper(), destination.upper()))

    def has_observed_direct_service(self, origin: str, destination: str) -> bool:
        observation = self.get(origin, destination)
        return observation is not None and observation.direct_service_observed

    def observations(self) -> tuple[RouteAvailability, ...]:
        return tuple(self._observations[key] for key in sorted(self._observations))


def observe_searchapi_response(
    payload: dict[str, Any], *, source: str
) -> tuple[RouteAvailability, ...]:
    """Record each requested pair independently from direct legs actually returned."""

    parameters = payload.get("search_parameters")
    metadata = payload.get("search_metadata")
    if not isinstance(parameters, dict) or not isinstance(metadata, dict):
        return ()
    departures = tuple(
        item.strip().upper()
        for item in str(parameters.get("departure_id", "")).split(",")
        if item.strip()
    )
    arrivals = tuple(
        item.strip().upper()
        for item in str(parameters.get("arrival_id", "")).split(",")
        if item.strip()
    )
    try:
        observed_at = datetime.fromisoformat(str(metadata["created_at"]))
    except (KeyError, ValueError):
        return ()

    observed_pairs: set[tuple[str, str]] = set()
    for bucket in ("best_flights", "other_flights"):
        groups = payload.get(bucket, [])
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            flights = group.get("flights")
            if not isinstance(flights, list) or len(flights) != 1:
                continue
            flight = flights[0]
            if not isinstance(flight, dict):
                continue
            departure = flight.get("departure_airport")
            arrival = flight.get("arrival_airport")
            if isinstance(departure, dict) and isinstance(arrival, dict):
                origin, destination = departure.get("id"), arrival.get("id")
                if isinstance(origin, str) and isinstance(destination, str):
                    observed_pairs.add((origin.upper(), destination.upper()))

    return tuple(
        RouteAvailability(
            origin=origin,
            destination=destination,
            direct_service_observed=(origin, destination) in observed_pairs,
            source=source,
            observed_at=observed_at,
        )
        for origin in departures
        for destination in arrivals
    )


def load_route_availability(
    raw_root: Path, *, travel_date: date
) -> RouteAvailabilityIndex:
    """Build availability for one date from saved SearchAPI captures."""

    index = RouteAvailabilityIndex()
    if not raw_root.exists():
        return index
    for path in raw_root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        parameters = payload.get("search_parameters")
        if (
            not isinstance(parameters, dict)
            or parameters.get("outbound_date") != travel_date.isoformat()
        ):
            continue
        for observation in observe_searchapi_response(payload, source=str(path)):
            index.record(observation)
    return index
