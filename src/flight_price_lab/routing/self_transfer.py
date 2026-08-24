"""Synthesize one-stop journeys from independently purchased direct offers."""

from collections.abc import Sequence

from flight_price_lab.airports import airport_timezone
from flight_price_lab.models import ConstructedItinerary, FlightOffer, TicketingType
from flight_price_lab.routing.connections import construct_itinerary


def construct_self_transfer(
    first_offer: FlightOffer, second_offer: FlightOffer
) -> ConstructedItinerary:
    """Construct exactly one stop from two compatible single-leg offers."""

    if len(first_offer.legs) != 1:
        raise ValueError("first offer must contain exactly one flight leg")
    if len(second_offer.legs) != 1:
        raise ValueError("second offer must contain exactly one flight leg")
    if first_offer.passenger_count != second_offer.passenger_count:
        raise ValueError("offers must have the same passenger count")
    if first_offer.currency != second_offer.currency:
        raise ValueError("offers must use the same currency")

    itinerary = construct_itinerary(
        [first_offer, second_offer], timezone_resolver=airport_timezone
    )
    if itinerary.ticketing_type is not TicketingType.SEPARATE_TICKETS:
        raise AssertionError("independently purchased offers must be separate tickets")
    return itinerary


def synthesize_connections(
    first_leg_offers: Sequence[FlightOffer],
    second_leg_offers: Sequence[FlightOffer],
) -> list[ConstructedItinerary]:
    """Return all compatible one-stop combinations ordered by total price."""

    itineraries: list[ConstructedItinerary] = []
    direct_first = (offer for offer in first_leg_offers if len(offer.legs) == 1)
    direct_second = tuple(offer for offer in second_leg_offers if len(offer.legs) == 1)
    for first_offer in direct_first:
        for second_offer in direct_second:
            try:
                itineraries.append(construct_self_transfer(first_offer, second_offer))
            except ValueError:
                continue
    return sorted(itineraries, key=lambda itinerary: itinerary.total_price)
