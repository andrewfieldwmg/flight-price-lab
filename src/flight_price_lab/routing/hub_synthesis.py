"""Offline synthesis of feasible one-stop itineraries across candidate hubs."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from flight_price_lab.analytics.self_transfers import (
    analyze_offer_pairs,
    total_journey_duration,
)
from flight_price_lab.models import ConstructedItinerary, FlightOffer
from flight_price_lab.routing.planning import RoutePlan

RouteKey = tuple[str, str]


@dataclass(frozen=True)
class HubItinerary:
    hub: str
    itinerary: ConstructedItinerary

    @property
    def total_price(self) -> Decimal:
        return self.itinerary.total_price

    @property
    def total_duration(self) -> timedelta:
        return total_journey_duration(self.itinerary)


@dataclass(frozen=True)
class HubSynthesisResult:
    itineraries: tuple[HubItinerary, ...]
    frontier: tuple[HubItinerary, ...]
    direct_benchmark_price: Decimal | None = None

    @property
    def cheapest(self) -> HubItinerary | None:
        return self.itineraries[0] if self.itineraries else None

    @property
    def fastest(self) -> HubItinerary | None:
        return min(self.itineraries, key=lambda item: item.total_duration, default=None)

    @property
    def cheapest_saving(self) -> Decimal | None:
        if self.direct_benchmark_price is None or self.cheapest is None:
            return None
        return self.direct_benchmark_price - self.cheapest.total_price


def _frontier(candidates: Sequence[HubItinerary]) -> tuple[HubItinerary, ...]:
    efficient = []
    for candidate in candidates:
        dominated = any(
            other is not candidate
            and other.total_price <= candidate.total_price
            and other.total_duration <= candidate.total_duration
            and (
                other.total_price < candidate.total_price
                or other.total_duration < candidate.total_duration
            )
            for other in candidates
        )
        if not dominated:
            efficient.append(candidate)
    return tuple(sorted(efficient, key=lambda item: item.total_price))


def synthesize_via_hubs(
    offers_by_route: Mapping[RouteKey, Sequence[FlightOffer]],
    route_plan: RoutePlan,
    *,
    direct_benchmark_price: Decimal | None = None,
) -> HubSynthesisResult:
    """Synthesize feasible one-stop journeys without cross-airport hub transfers."""

    candidates: list[HubItinerary] = []
    for hub in route_plan.candidate_hubs:
        first_offers = tuple(
            offer
            for origin in route_plan.origin_airports
            for offer in offers_by_route.get((origin, hub), ())
        )
        second_offers = tuple(
            offer
            for destination in route_plan.destination_airports
            for offer in offers_by_route.get((hub, destination), ())
        )
        analysis = analyze_offer_pairs(
            first_offers,
            second_offers,
            profile=route_plan.connection_profile,
            baggage=route_plan.baggage_profile,
        )
        candidates.extend(
            HubItinerary(hub=hub, itinerary=itinerary)
            for itinerary in analysis.itineraries
        )

    candidates.sort(key=lambda item: item.total_price)
    return HubSynthesisResult(
        itineraries=tuple(candidates),
        frontier=_frontier(candidates),
        direct_benchmark_price=direct_benchmark_price,
    )
