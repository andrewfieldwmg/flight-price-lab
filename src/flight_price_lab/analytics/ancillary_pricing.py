"""Calculate effective prices without treating unknown ancillary costs as zero."""

from collections.abc import Mapping
from decimal import Decimal

from flight_price_lab.models.ancillary import (
    AncillaryCosts,
    BaggagePricingState,
    BaggageRequirement,
    EffectivePrice,
)
from flight_price_lab.models.flight import FlightOffer
from flight_price_lab.models.itinerary import ConstructedItinerary


def price_offer_with_ancillaries(
    offer: FlightOffer,
    requirement: BaggageRequirement,
    costs: AncillaryCosts | None,
) -> EffectivePrice:
    """Price one ticket, retaining incompleteness for required unknown items."""

    if costs is None:
        unknown = tuple(
            name
            for count, name in (
                (requirement.personal_items, "personal_items"),
                (requirement.carry_on_bags, "carry_on_bags"),
                (requirement.checked_bags, "checked_bags"),
            )
            if count > 0
        )
        return EffectivePrice(
            base_total_price=offer.total_price,
            known_ancillary_total=Decimal(0),
            effective_price=None if unknown else offer.total_price,
            complete=not unknown,
            unknown_components=unknown,
        )

    known_total = costs.other_total
    unknown: list[str] = []
    requirements = (
        (
            requirement.personal_items,
            costs.personal_item_state,
            None,
            "personal_items",
        ),
        (
            requirement.carry_on_bags,
            costs.carry_on_state,
            costs.carry_on_total,
            "carry_on_bags",
        ),
        (
            requirement.checked_bags,
            costs.checked_bag_state,
            costs.checked_bag_total,
            "checked_bags",
        ),
    )
    for count, state, total, name in requirements:
        if count == 0:
            continue
        if state is BaggagePricingState.UNKNOWN:
            unknown.append(name)
        elif state is BaggagePricingState.KNOWN_EXTRA_COST and total is not None:
            known_total += total
    if costs.seat_total is not None:
        known_total += costs.seat_total
    complete = not unknown
    return EffectivePrice(
        base_total_price=offer.total_price,
        known_ancillary_total=known_total,
        effective_price=offer.total_price + known_total if complete else None,
        complete=complete,
        unknown_components=tuple(unknown),
    )


def price_separate_ticket_itinerary(
    itinerary: ConstructedItinerary,
    requirement: BaggageRequirement,
    costs_by_offer_fingerprint: Mapping[str, AncillaryCosts],
) -> EffectivePrice:
    """Apply baggage requirements independently to every constituent ticket."""

    priced = tuple(
        price_offer_with_ancillaries(
            offer, requirement, costs_by_offer_fingerprint.get(offer.fingerprint)
        )
        for offer in itinerary.components
    )
    known_total = sum((item.known_ancillary_total for item in priced), start=Decimal(0))
    unknown = tuple(
        f"ticket_{index}.{component}"
        for index, item in enumerate(priced, start=1)
        for component in item.unknown_components
    )
    complete = all(item.complete for item in priced)
    return EffectivePrice(
        base_total_price=itinerary.total_price,
        known_ancillary_total=known_total,
        effective_price=itinerary.total_price + known_total if complete else None,
        complete=complete,
        unknown_components=unknown,
    )
