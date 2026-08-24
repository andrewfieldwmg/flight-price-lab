from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from flight_price_lab.models import (
    FlightLeg,
    FlightOffer,
    JourneyStructure,
    TicketingType,
)
from flight_price_lab.routing import construct_itinerary


def leg(
    origin: str, destination: str, departure: datetime, hours: int = 2
) -> FlightLeg:
    return FlightLeg(
        origin=origin,
        destination=destination,
        departure=departure,
        arrival=departure + timedelta(hours=hours),
        airline="Example Air",
        flight_number="EX123",
    )


def offer(
    *legs: FlightLeg,
    price: str = "100.00",
    offer_id: str = "offer-1",
    ticketing_type: TicketingType = TicketingType.UNKNOWN,
) -> FlightOffer:
    return FlightOffer(
        legs=legs,
        total_price=Decimal(price),
        currency="gbp",
        passenger_count=1,
        provider="test-provider",
        provider_offer_id=offer_id,
        observed_at=datetime(2026, 8, 1, tzinfo=UTC),
        raw_reference="data/raw/test-provider/offer-1.json",
        ticketing_type=ticketing_type,
    )


def test_valid_direct_itinerary() -> None:
    flight = leg("LHR", "JFK", datetime(2026, 9, 1, 9, tzinfo=UTC), hours=8)

    itinerary = construct_itinerary([offer(flight)])

    assert itinerary.journey_structure is JourneyStructure.DIRECT
    assert itinerary.ticketing_type is TicketingType.UNKNOWN
    assert itinerary.number_of_stops == 0
    assert itinerary.connection_airport is None


def test_two_single_leg_offers_form_valid_self_transfer() -> None:
    first = leg("LHR", "KEF", datetime(2026, 9, 1, 9, tzinfo=UTC), hours=3)
    second = leg("KEF", "JFK", datetime(2026, 9, 1, 14, tzinfo=UTC), hours=6)

    itinerary = construct_itinerary(
        [
            offer(first, price="80", offer_id="a"),
            offer(second, price="120", offer_id="b"),
        ]
    )

    assert itinerary.journey_structure is JourneyStructure.CONNECTION
    assert itinerary.ticketing_type is TicketingType.SEPARATE_TICKETS
    assert itinerary.number_of_stops == 1
    assert itinerary.connection_airport == "KEF"
    assert itinerary.connection_duration == timedelta(hours=2)
    assert itinerary.total_price == Decimal(200)
    assert itinerary.passenger_count == 1


def test_one_two_leg_offer_preserves_evidenced_single_ticket() -> None:
    first = leg("LHR", "KEF", datetime(2026, 9, 1, 9, tzinfo=UTC), hours=3)
    second = leg("KEF", "JFK", datetime(2026, 9, 1, 14, tzinfo=UTC), hours=6)

    itinerary = construct_itinerary(
        [offer(first, second, ticketing_type=TicketingType.SINGLE_TICKET)]
    )

    assert itinerary.journey_structure is JourneyStructure.CONNECTION
    assert itinerary.ticketing_type is TicketingType.SINGLE_TICKET
    assert itinerary.number_of_stops == 1


def test_two_stop_itinerary_rejected() -> None:
    start = datetime(2026, 9, 1, 8, tzinfo=UTC)
    legs = (
        leg("LHR", "CDG", start),
        leg("CDG", "KEF", start + timedelta(hours=3)),
        leg("KEF", "JFK", start + timedelta(hours=6)),
    )

    with pytest.raises(ValueError, match="more than one stop"):
        construct_itinerary([offer(*legs)])


def test_two_leg_offer_plus_onward_offer_rejected() -> None:
    start = datetime(2026, 9, 1, 8, tzinfo=UTC)
    first = leg("LHR", "CDG", start)
    second = leg("CDG", "KEF", start + timedelta(hours=3))
    third = leg("KEF", "JFK", start + timedelta(hours=6))

    with pytest.raises(ValueError, match="more than one stop"):
        construct_itinerary(
            [offer(first, second, offer_id="a"), offer(third, offer_id="b")]
        )


def test_mismatched_connection_airports_rejected() -> None:
    first = leg("LHR", "CDG", datetime(2026, 9, 1, 8, tzinfo=UTC))
    second = leg("AMS", "JFK", datetime(2026, 9, 1, 12, tzinfo=UTC))

    with pytest.raises(ValueError, match="airports do not match"):
        construct_itinerary([offer(first, second)])


def test_second_leg_before_first_arrival_rejected() -> None:
    first = leg("LHR", "CDG", datetime(2026, 9, 1, 8, tzinfo=UTC), hours=3)
    second = leg("CDG", "JFK", datetime(2026, 9, 1, 10, tzinfo=UTC))

    with pytest.raises(ValueError, match="depart after"):
        construct_itinerary([offer(first, second)])


def test_overnight_connection_identified() -> None:
    first = leg("LHR", "KEF", datetime(2026, 9, 1, 20, tzinfo=UTC), hours=3)
    second = leg("KEF", "JFK", datetime(2026, 9, 2, 7, tzinfo=UTC), hours=6)

    itinerary = construct_itinerary(
        [offer(first, second)],
        timezone_resolver=lambda airport: (
            ZoneInfo("Atlantic/Reykjavik") if airport == "KEF" else None
        ),
    )

    assert itinerary.overnight_connection is True


def test_overnight_is_unknown_without_airport_timezone() -> None:
    first = leg("LHR", "KEF", datetime(2026, 9, 1, 20, tzinfo=UTC), hours=3)
    second = leg("KEF", "JFK", datetime(2026, 9, 2, 7, tzinfo=UTC), hours=6)

    itinerary = construct_itinerary([offer(first, second)])

    assert itinerary.overnight_connection is None
