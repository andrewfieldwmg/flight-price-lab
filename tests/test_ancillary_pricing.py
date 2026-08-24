from datetime import UTC, datetime, timedelta
from decimal import Decimal

from flight_price_lab.analytics.ancillary_pricing import (
    price_offer_with_ancillaries,
    price_separate_ticket_itinerary,
)
from flight_price_lab.models import (
    AncillaryConfidence,
    AncillaryCosts,
    BaggagePricingState,
    BaggageRequirement,
    FlightLeg,
    FlightOffer,
)
from flight_price_lab.routing import construct_self_transfer


def offer(origin: str, destination: str, hour: int, price: str, offer_id: str):
    departure = datetime(2026, 12, 18, hour, tzinfo=UTC)
    return FlightOffer(
        legs=(
            FlightLeg(
                origin=origin,
                destination=destination,
                departure=departure,
                arrival=departure + timedelta(hours=2),
                airline="Example Air",
                flight_number=f"EX {offer_id}",
            ),
        ),
        total_price=Decimal(price),
        currency="GBP",
        passenger_count=4,
        provider="test",
        provider_offer_id=offer_id,
        observed_at=datetime(2026, 8, 24, tzinfo=UTC),
    )


def costs(**updates):
    values = {
        "personal_item_state": BaggagePricingState.INCLUDED,
        "carry_on_state": BaggagePricingState.INCLUDED,
        "checked_bag_state": BaggagePricingState.INCLUDED,
        "source": "booking-options fixture",
        "confidence": AncillaryConfidence.CONFIRMED,
    }
    values.update(updates)
    return AncillaryCosts(**values)


def test_included_baggage_has_complete_base_effective_price() -> None:
    result = price_offer_with_ancillaries(
        offer("LGW", "MXP", 8, "100", "one"),
        BaggageRequirement(personal_items=4, carry_on_bags=2, checked_bags=1),
        costs(),
    )

    assert result.complete
    assert result.known_ancillary_total == 0
    assert result.effective_price == Decimal(100)


def test_known_checked_bag_charge_is_added() -> None:
    result = price_offer_with_ancillaries(
        offer("LGW", "MXP", 8, "100", "one"),
        BaggageRequirement(checked_bags=2),
        costs(
            checked_bag_state=BaggagePricingState.KNOWN_EXTRA_COST,
            checked_bag_total=Decimal(70),
        ),
    )

    assert result.effective_price == Decimal(170)


def test_known_carry_on_charge_is_added_with_other_costs() -> None:
    result = price_offer_with_ancillaries(
        offer("LGW", "MXP", 8, "100", "one"),
        BaggageRequirement(carry_on_bags=2),
        costs(
            carry_on_state=BaggagePricingState.KNOWN_EXTRA_COST,
            carry_on_total=Decimal(40),
            other_total=Decimal(5),
        ),
    )

    assert result.known_ancillary_total == Decimal(45)
    assert result.effective_price == Decimal(145)


def test_unknown_baggage_cost_keeps_effective_price_incomplete() -> None:
    result = price_offer_with_ancillaries(
        offer("LGW", "MXP", 8, "100", "one"),
        BaggageRequirement(checked_bags=1),
        costs(checked_bag_state=BaggagePricingState.UNKNOWN),
    )

    assert not result.complete
    assert result.effective_price is None
    assert result.unknown_components == ("checked_bags",)


def test_no_ancillary_data_does_not_assume_required_bags_cost_zero() -> None:
    result = price_offer_with_ancillaries(
        offer("LGW", "MXP", 8, "100", "one"),
        BaggageRequirement(carry_on_bags=1),
        None,
    )

    assert result.base_total_price == Decimal(100)
    assert result.effective_price is None
    assert not result.complete


def test_separate_tickets_charge_ancillaries_independently() -> None:
    first = offer("LGW", "MXP", 8, "250", "first")
    second = offer("MXP", "CAG", 13, "150", "second")
    itinerary = construct_self_transfer(first, second)
    per_ticket_cost = costs(
        carry_on_state=BaggagePricingState.KNOWN_EXTRA_COST,
        carry_on_total=Decimal(30),
    )

    result = price_separate_ticket_itinerary(
        itinerary,
        BaggageRequirement(carry_on_bags=1),
        {
            first.fingerprint: per_ticket_cost,
            second.fingerprint: per_ticket_cost,
        },
    )

    assert result.known_ancillary_total == Decimal(60)
    assert result.effective_price == Decimal(460)


def test_one_unknown_ticket_makes_separate_ticket_total_incomplete() -> None:
    first = offer("LGW", "MXP", 8, "250", "first")
    second = offer("MXP", "CAG", 13, "150", "second")
    itinerary = construct_self_transfer(first, second)

    result = price_separate_ticket_itinerary(
        itinerary,
        BaggageRequirement(checked_bags=1),
        {first.fingerprint: costs()},
    )

    assert result.effective_price is None
    assert result.unknown_components == ("ticket_2.checked_bags",)
