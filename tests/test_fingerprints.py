from datetime import UTC, datetime, timedelta
from decimal import Decimal

from flight_price_lab.models import FlightLeg, FlightOffer


def make_leg(*, flight_number: str = "EX123") -> FlightLeg:
    departure = datetime(2026, 9, 1, 9, tzinfo=UTC)
    return FlightLeg(
        origin="LHR",
        destination="JFK",
        departure=departure,
        arrival=departure + timedelta(hours=8),
        airline="Example Air",
        flight_number=flight_number,
    )


def make_offer(leg: FlightLeg, *, price: str, observed_at: datetime) -> FlightOffer:
    return FlightOffer(
        legs=(leg,),
        total_price=Decimal(price),
        currency="GBP",
        passenger_count=2,
        provider="test-provider",
        provider_offer_id="mutable-provider-id",
        observed_at=observed_at,
    )


def test_leg_fingerprint_is_deterministic_and_schedule_based() -> None:
    assert make_leg().fingerprint == make_leg().fingerprint
    assert make_leg().fingerprint != make_leg(flight_number="EX456").fingerprint


def test_offer_fingerprint_excludes_price_and_observation_timestamp() -> None:
    flight = make_leg()
    first = make_offer(
        flight, price="200", observed_at=datetime(2026, 8, 1, tzinfo=UTC)
    )
    second = make_offer(
        flight, price="350", observed_at=datetime(2026, 8, 2, tzinfo=UTC)
    )

    assert first.fingerprint == second.fingerprint
