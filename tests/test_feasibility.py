from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from flight_price_lab.analytics.self_transfers import (
    analyze_offer_pairs,
    price_duration_frontier,
)
from flight_price_lab.models import FlightLeg, FlightOffer
from flight_price_lab.routing import (
    BaggageProfile,
    SelfTransferProfile,
    construct_self_transfer,
    is_feasible_self_transfer,
)


def _offer(
    origin: str,
    destination: str,
    departure: datetime,
    *,
    price: str = "100",
    offer_id: str,
) -> FlightOffer:
    return FlightOffer(
        legs=(
            FlightLeg(
                origin=origin,
                destination=destination,
                departure=departure,
                arrival=departure + timedelta(hours=1),
                airline="Example Air",
                flight_number=f"EX {offer_id}",
            ),
        ),
        total_price=Decimal(price),
        passenger_count=4,
        currency="GBP",
        provider="test",
        provider_offer_id=offer_id,
        observed_at=datetime(2026, 8, 24, tzinfo=UTC),
    )


def _itinerary(connection_minutes: int):
    first = _offer(
        "LGW",
        "MXP",
        datetime(2026, 12, 18, 8, tzinfo=UTC),
        offer_id="first",
    )
    second_departure = first.legs[0].arrival + timedelta(minutes=connection_minutes)
    second = _offer(
        "MXP", "CAG", second_departure, offer_id=f"second-{connection_minutes}"
    )
    return construct_self_transfer(first, second)


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(119, False), (120, True)],
)
def test_conservative_cabin_threshold(minutes: int, expected: bool) -> None:
    assert (
        is_feasible_self_transfer(
            _itinerary(minutes),
            profile=SelfTransferProfile.CONSERVATIVE,
            baggage=BaggageProfile.CABIN_BAG,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(119, False), (120, True)],
)
def test_conservative_checked_bag_threshold(minutes: int, expected: bool) -> None:
    assert (
        is_feasible_self_transfer(
            _itinerary(minutes),
            profile=SelfTransferProfile.CONSERVATIVE,
            baggage=BaggageProfile.CHECKED_BAG,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("profile", "baggage", "threshold"),
    [
        (SelfTransferProfile.STANDARD, BaggageProfile.PERSONAL_ITEM_ONLY, 120),
        (SelfTransferProfile.STANDARD, BaggageProfile.CHECKED_BAG, 120),
        (SelfTransferProfile.AGGRESSIVE, BaggageProfile.CABIN_BAG, 120),
        (SelfTransferProfile.AGGRESSIVE, BaggageProfile.CHECKED_BAG, 120),
    ],
)
def test_standard_and_aggressive_thresholds(
    profile: SelfTransferProfile,
    baggage: BaggageProfile,
    threshold: int,
) -> None:
    assert not is_feasible_self_transfer(
        _itinerary(threshold - 1), profile=profile, baggage=baggage
    )
    assert is_feasible_self_transfer(
        _itinerary(threshold), profile=profile, baggage=baggage
    )


def test_feasibility_is_applied_before_frontier() -> None:
    first = _offer(
        "LGW",
        "MXP",
        datetime(2026, 12, 18, 8, tzinfo=UTC),
        price="100",
        offer_id="first",
    )
    cheap_short = _offer(
        "MXP",
        "CAG",
        first.legs[0].arrival + timedelta(minutes=119),
        price="50",
        offer_id="cheap-short",
    )
    feasible = _offer(
        "MXP",
        "CAG",
        first.legs[0].arrival + timedelta(minutes=180),
        price="80",
        offer_id="feasible",
    )

    analysis = analyze_offer_pairs(
        [first],
        [cheap_short, feasible],
        profile=SelfTransferProfile.CONSERVATIVE,
        baggage=BaggageProfile.CABIN_BAG,
    )
    frontier = price_duration_frontier(analysis.itineraries)

    assert analysis.chronological_combinations == 2
    assert analysis.rejected_minimum_connection == 1
    assert len(analysis.itineraries) == 1
    assert frontier == [analysis.itineraries[0]]
    assert frontier[0].components[1].provider_offer_id == "feasible"
