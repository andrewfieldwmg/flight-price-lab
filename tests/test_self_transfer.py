from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from flight_price_lab.models import (
    FlightLeg,
    FlightOffer,
    JourneyStructure,
    TicketingType,
)
from flight_price_lab.routing import construct_self_transfer, synthesize_connections


def leg(
    origin: str,
    destination: str,
    departure: datetime,
    *,
    duration: timedelta = timedelta(hours=2),
) -> FlightLeg:
    return FlightLeg(
        origin=origin,
        destination=destination,
        departure=departure,
        arrival=departure + duration,
        airline="Example Air",
        flight_number=f"EX {origin}{destination}",
    )


def offer(
    *flight_legs: FlightLeg,
    price: str = "100",
    passengers: int = 4,
    currency: str = "GBP",
    offer_id: str = "offer",
) -> FlightOffer:
    return FlightOffer(
        legs=flight_legs,
        total_price=Decimal(price),
        passenger_count=passengers,
        currency=currency,
        provider="test-provider",
        provider_offer_id=offer_id,
        observed_at=datetime(2026, 8, 24, tzinfo=UTC),
    )


def compatible_offers() -> tuple[FlightOffer, FlightOffer]:
    first = offer(
        leg("LGW", "MXP", datetime(2026, 12, 18, 8, tzinfo=UTC)),
        price="258",
        offer_id="first",
    )
    second = offer(
        leg("MXP", "CAG", datetime(2026, 12, 18, 12, tzinfo=UTC)),
        price="142",
        offer_id="second",
    )
    return first, second


def test_constructs_valid_separate_ticket_connection_with_lineage() -> None:
    first, second = compatible_offers()

    itinerary = construct_self_transfer(first, second)

    assert itinerary.journey_structure is JourneyStructure.CONNECTION
    assert itinerary.ticketing_type is TicketingType.SEPARATE_TICKETS
    assert itinerary.number_of_stops == 1
    assert itinerary.connection_airport == "MXP"
    assert itinerary.connection_duration == timedelta(hours=2)
    assert itinerary.overnight_connection is False
    assert itinerary.constituent_offer_fingerprints == (
        first.fingerprint,
        second.fingerprint,
    )


def test_adds_total_search_party_prices() -> None:
    first, second = compatible_offers()

    itinerary = construct_self_transfer(first, second)

    assert itinerary.total_price == Decimal(400)
    assert itinerary.passenger_count == 4
    assert itinerary.currency == "GBP"


def test_rejects_mismatched_connection_airports() -> None:
    first, second = compatible_offers()
    wrong_second = offer(
        leg("FCO", "CAG", second.legs[0].departure), offer_id="wrong-airport"
    )

    with pytest.raises(ValueError, match="connection airports do not match"):
        construct_self_transfer(first, wrong_second)


def test_rejects_non_chronological_connection() -> None:
    first, _ = compatible_offers()
    early_second = offer(
        leg("MXP", "CAG", datetime(2026, 12, 18, 9, tzinfo=UTC)),
        offer_id="too-early",
    )

    with pytest.raises(ValueError, match="depart after"):
        construct_self_transfer(first, early_second)


def test_rejects_constituent_offer_with_two_legs() -> None:
    first, second = compatible_offers()
    connected_first = offer(
        leg("LGW", "FRA", datetime(2026, 12, 18, 6, tzinfo=UTC)),
        leg("FRA", "MXP", datetime(2026, 12, 18, 9, tzinfo=UTC)),
        offer_id="already-connected",
    )

    with pytest.raises(ValueError, match="first offer must contain exactly one"):
        construct_self_transfer(connected_first, second)

    assert first.legs[0].destination == "MXP"


def test_rejects_different_passenger_counts() -> None:
    first, second = compatible_offers()
    different_party = second.model_copy(update={"passenger_count": 1})

    with pytest.raises(ValueError, match="same passenger count"):
        construct_self_transfer(first, different_party)


def test_rejects_different_currencies() -> None:
    first, second = compatible_offers()
    different_currency = second.model_copy(update={"currency": "EUR"})

    with pytest.raises(ValueError, match="same currency"):
        construct_self_transfer(first, different_currency)


def test_batch_returns_only_valid_combinations_sorted_by_price() -> None:
    first, second = compatible_offers()
    cheaper_first = first.model_copy(
        update={"total_price": Decimal(100), "provider_offer_id": "cheaper"}
    )
    incompatible = second.model_copy(update={"currency": "EUR"})

    itineraries = synthesize_connections([first, cheaper_first], [second, incompatible])

    assert [item.total_price for item in itineraries] == [Decimal(242), Decimal(400)]
    assert all(
        item.ticketing_type is TicketingType.SEPARATE_TICKETS for item in itineraries
    )
