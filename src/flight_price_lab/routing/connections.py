"""Validate and synthesize direct and one-stop itineraries."""

from collections.abc import Sequence
from datetime import tzinfo
from typing import Protocol

from flight_price_lab.models.classification import JourneyStructure, TicketingType
from flight_price_lab.models.flight import FlightLeg, FlightOffer
from flight_price_lab.models.itinerary import ConstructedItinerary

MAX_STOPS_PER_DIRECTION = 1


class AirportTimezoneResolver(Protocol):
    """Resolve an IATA airport code to its local timezone."""

    def __call__(self, airport: str) -> tzinfo | None: ...


def _flatten_legs(offers: Sequence[FlightOffer]) -> tuple[FlightLeg, ...]:
    return tuple(leg for offer in offers for leg in offer.legs)


def validate_connection(first_leg: FlightLeg, second_leg: FlightLeg) -> None:
    """Validate continuity and chronology for a connection."""

    if first_leg.destination != second_leg.origin:
        raise ValueError("connection airports do not match")
    if second_leg.departure <= first_leg.arrival:
        raise ValueError("second leg must depart after first leg arrives")


def is_overnight_connection(
    first_leg: FlightLeg,
    second_leg: FlightLeg,
    timezone_resolver: AirportTimezoneResolver | None,
) -> bool | None:
    """Compare calendar dates in the connection airport's local timezone.

    None means the airport timezone is unavailable, so the result is unknown.
    """

    if timezone_resolver is None:
        return None
    connection_timezone = timezone_resolver(first_leg.destination)
    if connection_timezone is None:
        return None
    local_arrival = first_leg.arrival.astimezone(connection_timezone)
    local_departure = second_leg.departure.astimezone(connection_timezone)
    return local_departure.date() > local_arrival.date()


def construct_itinerary(
    offers: Sequence[FlightOffer],
    *,
    timezone_resolver: AirportTimezoneResolver | None = None,
) -> ConstructedItinerary:
    """Build a direct or one-stop itinerary from one or two priced offers."""

    components = tuple(offers)
    if not 1 <= len(components) <= 2:
        raise ValueError("an itinerary must contain one or two flight offers")

    legs = _flatten_legs(components)
    number_of_stops = len(legs) - 1
    if not legs or number_of_stops > MAX_STOPS_PER_DIRECTION:
        raise ValueError("more than one stop per direction is not supported")

    connection_airport = None
    connection_duration = None
    overnight_connection = None

    if number_of_stops == 1:
        validate_connection(legs[0], legs[1])
        connection_airport = legs[0].destination
        connection_duration = legs[1].departure - legs[0].arrival
        overnight_connection = is_overnight_connection(
            legs[0], legs[1], timezone_resolver
        )

    component_passenger_counts = tuple(offer.passenger_count for offer in components)
    known_passenger_counts = set(component_passenger_counts)
    passenger_count = (
        known_passenger_counts.pop()
        if None not in known_passenger_counts and len(known_passenger_counts) == 1
        else None
    )
    currencies = {offer.currency for offer in components}
    if len(currencies) != 1:
        raise ValueError("itinerary components must use the same currency")

    return ConstructedItinerary(
        components=components,
        journey_structure=(
            JourneyStructure.DIRECT
            if number_of_stops == 0
            else JourneyStructure.CONNECTION
        ),
        ticketing_type=(
            TicketingType.SEPARATE_TICKETS
            if len(components) > 1
            else components[0].ticketing_type
        ),
        total_price=sum((offer.total_price for offer in components), start=0),
        currency=currencies.pop(),
        passenger_count=passenger_count,
        constituent_offer_fingerprints=tuple(offer.fingerprint for offer in components),
        departure=legs[0].departure,
        final_arrival=legs[-1].arrival,
        connection_airport=connection_airport,
        connection_duration=connection_duration,
        overnight_connection=overnight_connection,
        number_of_stops=number_of_stops,
    )
