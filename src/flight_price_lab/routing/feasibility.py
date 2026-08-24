"""Product-policy feasibility rules for synthetic self-transfers."""

from datetime import timedelta
from enum import StrEnum

from flight_price_lab.models import (
    ConstructedItinerary,
    JourneyStructure,
    TicketingType,
)


class SelfTransferProfile(StrEnum):
    CONSERVATIVE = "conservative"
    STANDARD = "standard"
    AGGRESSIVE = "aggressive"


class BaggageProfile(StrEnum):
    PERSONAL_ITEM_ONLY = "personal_item_only"
    CABIN_BAG = "cabin_bag"
    CHECKED_BAG = "checked_bag"


_MINIMUM_CONNECTION_MINUTES = {
    SelfTransferProfile.CONSERVATIVE: {"cabin": 180, "checked": 240},
    SelfTransferProfile.STANDARD: {"cabin": 150, "checked": 210},
    SelfTransferProfile.AGGRESSIVE: {"cabin": 120, "checked": 180},
}


def minimum_connection_duration(
    profile: SelfTransferProfile, baggage: BaggageProfile
) -> timedelta:
    """Return a product-policy threshold, not an airport-published MCT."""

    baggage_class = "checked" if baggage is BaggageProfile.CHECKED_BAG else "cabin"
    return timedelta(minutes=_MINIMUM_CONNECTION_MINUTES[profile][baggage_class])


def is_feasible_self_transfer(
    itinerary: ConstructedItinerary,
    *,
    profile: SelfTransferProfile,
    baggage: BaggageProfile,
) -> bool:
    """Apply V1 topology, chronology, airport, and policy-duration rules."""

    if itinerary.journey_structure is not JourneyStructure.CONNECTION:
        return False
    if itinerary.ticketing_type is not TicketingType.SEPARATE_TICKETS:
        return False
    if len(itinerary.components) != 2 or any(
        len(offer.legs) != 1 for offer in itinerary.components
    ):
        return False
    first_leg = itinerary.components[0].legs[0]
    second_leg = itinerary.components[1].legs[0]
    if first_leg.destination != second_leg.origin:
        return False
    if second_leg.departure <= first_leg.arrival:
        return False
    if itinerary.connection_duration is None:
        return False
    return itinerary.connection_duration >= minimum_connection_duration(
        profile, baggage
    )
