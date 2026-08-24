"""Descriptive analysis of synthetic, separately ticketed connections."""

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from flight_price_lab.models import ConstructedItinerary, FlightOffer
from flight_price_lab.routing import (
    BaggageProfile,
    SelfTransferProfile,
    construct_self_transfer,
    is_feasible_self_transfer,
)

CONNECTION_BUCKETS = (
    "< 60 min",
    "60-119 min",
    "120-179 min",
    "180-239 min",
    "240-359 min",
    "360+ min",
    "overnight",
)


@dataclass(frozen=True)
class SelfTransferAnalysis:
    """Candidate counts and valid itineraries from two offer collections."""

    first_direct_count: int
    second_direct_count: int
    theoretical_combinations: int
    rejected_too_early: int
    rejected_incompatible: int
    chronological_combinations: int
    rejected_minimum_connection: int
    itineraries: tuple[ConstructedItinerary, ...]


def analyze_offer_pairs(
    first_offers: Sequence[FlightOffer],
    second_offers: Sequence[FlightOffer],
    *,
    profile: SelfTransferProfile = SelfTransferProfile.CONSERVATIVE,
    baggage: BaggageProfile = BaggageProfile.CABIN_BAG,
) -> SelfTransferAnalysis:
    """Build every valid direct-offer pairing and retain descriptive counts."""

    first_direct = tuple(offer for offer in first_offers if len(offer.legs) == 1)
    second_direct = tuple(offer for offer in second_offers if len(offer.legs) == 1)
    chronological_itineraries: list[ConstructedItinerary] = []
    rejected_too_early = 0
    rejected_incompatible = 0

    for first_offer in first_direct:
        for second_offer in second_direct:
            if second_offer.legs[0].departure <= first_offer.legs[0].arrival:
                rejected_too_early += 1
                continue
            try:
                chronological_itineraries.append(
                    construct_self_transfer(first_offer, second_offer)
                )
            except ValueError:
                rejected_incompatible += 1

    itineraries = [
        itinerary
        for itinerary in chronological_itineraries
        if is_feasible_self_transfer(itinerary, profile=profile, baggage=baggage)
    ]
    itineraries.sort(key=lambda itinerary: itinerary.total_price)
    return SelfTransferAnalysis(
        first_direct_count=len(first_direct),
        second_direct_count=len(second_direct),
        theoretical_combinations=len(first_direct) * len(second_direct),
        rejected_too_early=rejected_too_early,
        rejected_incompatible=rejected_incompatible,
        chronological_combinations=len(chronological_itineraries),
        rejected_minimum_connection=(len(chronological_itineraries) - len(itineraries)),
        itineraries=tuple(itineraries),
    )


def total_journey_duration(itinerary: ConstructedItinerary) -> timedelta:
    return itinerary.final_arrival - itinerary.departure


def connection_duration_distribution(
    itineraries: Sequence[ConstructedItinerary],
) -> dict[str, int]:
    """Count exclusive connection-time buckets, with overnight taking precedence."""

    distribution = dict.fromkeys(CONNECTION_BUCKETS, 0)
    for itinerary in itineraries:
        if itinerary.overnight_connection is True:
            distribution["overnight"] += 1
            continue
        if itinerary.connection_duration is None:
            continue
        minutes = itinerary.connection_duration.total_seconds() / 60
        if minutes < 60:
            bucket = "< 60 min"
        elif minutes < 120:
            bucket = "60-119 min"
        elif minutes < 180:
            bucket = "120-179 min"
        elif minutes < 240:
            bucket = "180-239 min"
        elif minutes < 360:
            bucket = "240-359 min"
        else:
            bucket = "360+ min"
        distribution[bucket] += 1
    return distribution


def price_duration_frontier(
    itineraries: Sequence[ConstructedItinerary],
) -> list[ConstructedItinerary]:
    """Return options not dominated on both price and total journey duration."""

    frontier = []
    for candidate in itineraries:
        candidate_duration = total_journey_duration(candidate)
        dominated = any(
            other is not candidate
            and other.total_price <= candidate.total_price
            and total_journey_duration(other) <= candidate_duration
            and (
                other.total_price < candidate.total_price
                or total_journey_duration(other) < candidate_duration
            )
            for other in itineraries
        )
        if not dominated:
            frontier.append(candidate)
    return sorted(frontier, key=lambda itinerary: itinerary.total_price)


def write_itineraries_csv(
    path: Path, itineraries: Sequence[ConstructedItinerary]
) -> None:
    """Write valid synthesized itineraries only when explicitly requested."""

    fieldnames = [
        "first_offer_fingerprint",
        "second_offer_fingerprint",
        "first_flight_number",
        "second_flight_number",
        "departure",
        "connection_arrival",
        "connection_departure",
        "arrival",
        "connection_minutes",
        "journey_minutes",
        "first_leg_price",
        "second_leg_price",
        "total_price",
        "currency",
        "overnight",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for itinerary in itineraries:
            first_offer, second_offer = itinerary.components
            first_leg, second_leg = first_offer.legs[0], second_offer.legs[0]
            writer.writerow(
                {
                    "first_offer_fingerprint": itinerary.constituent_offer_fingerprints[
                        0
                    ],
                    "second_offer_fingerprint": itinerary.constituent_offer_fingerprints[
                        1
                    ],
                    "first_flight_number": first_leg.flight_number,
                    "second_flight_number": second_leg.flight_number,
                    "departure": itinerary.departure.isoformat(),
                    "connection_arrival": first_leg.arrival.isoformat(),
                    "connection_departure": second_leg.departure.isoformat(),
                    "arrival": itinerary.final_arrival.isoformat(),
                    "connection_minutes": int(
                        itinerary.connection_duration.total_seconds() / 60
                    ),
                    "journey_minutes": int(
                        total_journey_duration(itinerary).total_seconds() / 60
                    ),
                    "first_leg_price": first_offer.total_price,
                    "second_leg_price": second_offer.total_price,
                    "total_price": itinerary.total_price,
                    "currency": itinerary.currency,
                    "overnight": itinerary.overnight_connection,
                }
            )
