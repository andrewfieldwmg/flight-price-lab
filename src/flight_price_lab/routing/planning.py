"""Plan direct provider searches needed for one-stop routing via candidate hubs."""

from dataclasses import dataclass, field
from datetime import date

from flight_price_lab.models.ancillary import BaggageRequirement
from flight_price_lab.routing.availability import RouteAvailabilityIndex
from flight_price_lab.routing.feasibility import BaggageProfile, SelfTransferProfile


def _normalized_airports(airports: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(airport.strip().upper() for airport in airports))
    if not normalized or any(
        len(airport) != 3 or not airport.isalpha() for airport in normalized
    ):
        raise ValueError("airport groups require valid three-letter IATA codes")
    return normalized


@dataclass(frozen=True)
class RoutePlan:
    origin_airports: tuple[str, ...]
    destination_airports: tuple[str, ...]
    candidate_hubs: tuple[str, ...]
    travel_date: date
    adults: int
    children: int
    currency: str
    connection_profile: SelfTransferProfile = SelfTransferProfile.CONSERVATIVE
    baggage_profile: BaggageProfile = BaggageProfile.CABIN_BAG
    baggage_requirement: BaggageRequirement = field(default_factory=BaggageRequirement)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "origin_airports", _normalized_airports(self.origin_airports)
        )
        object.__setattr__(
            self,
            "destination_airports",
            _normalized_airports(self.destination_airports),
        )
        object.__setattr__(
            self, "candidate_hubs", _normalized_airports(self.candidate_hubs)
        )
        object.__setattr__(self, "currency", self.currency.strip().upper())
        if self.adults < 1 or self.children < 0:
            raise ValueError("passenger counts are invalid")
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise ValueError("currency must be a three-letter code")

    @property
    def passenger_count(self) -> int:
        return self.adults + self.children


@dataclass(frozen=True, order=True)
class PlannedProviderSearch:
    departure_airports: tuple[str, ...]
    arrival_airports: tuple[str, ...]
    travel_date: date
    adults: int
    children: int
    currency: str
    stops: str = "nonstop"
    carry_on_bags: int | None = None
    checked_bags: int | None = None

    def as_searchapi_arguments(self) -> dict[str, str | int | bool]:
        arguments: dict[str, str | int | bool] = {
            "engine": "google_flights",
            "flight_type": "one_way",
            "departure_id": ",".join(self.departure_airports),
            "arrival_id": ",".join(self.arrival_airports),
            "outbound_date": self.travel_date.isoformat(),
            "adults": self.adults,
            "children": self.children,
            "currency": self.currency,
            "travel_class": "economy",
            "stops": self.stops,
            "sort_by": "price",
            "show_cheapest_flights": True,
        }
        if self.carry_on_bags is not None:
            arguments["carry_on_bags"] = self.carry_on_bags
        if self.checked_bags is not None:
            arguments["checked_bags"] = self.checked_bags
        return arguments


def _search(
    route_plan: RoutePlan,
    departures: tuple[str, ...],
    arrivals: tuple[str, ...],
) -> PlannedProviderSearch:
    return PlannedProviderSearch(
        departure_airports=departures,
        arrival_airports=arrivals,
        travel_date=route_plan.travel_date,
        adults=route_plan.adults,
        children=route_plan.children,
        currency=route_plan.currency,
        carry_on_bags=(
            route_plan.baggage_requirement.carry_on_bags
            if route_plan.baggage_requirement.carry_on_bags > 0
            else None
        ),
        checked_bags=(
            route_plan.baggage_requirement.checked_bags
            if route_plan.baggage_requirement.checked_bags > 0
            else None
        ),
    )


def plan_route_discovery_searches(
    route_plan: RoutePlan,
) -> tuple[PlannedProviderSearch, ...]:
    """Return grouped searches used only to discover possible direct route pairs."""

    searches: set[PlannedProviderSearch] = set()
    for hub in route_plan.candidate_hubs:
        searches.add(_search(route_plan, route_plan.origin_airports, (hub,)))
        searches.add(_search(route_plan, (hub,), route_plan.destination_airports))
    return tuple(sorted(searches))


def plan_provider_searches(
    route_plan: RoutePlan, availability: RouteAvailabilityIndex
) -> tuple[PlannedProviderSearch, ...]:
    """Return singleton fare searches only for positively observed direct pairs."""

    searches = {
        _search(route_plan, (origin,), (hub,))
        for hub in route_plan.candidate_hubs
        for origin in route_plan.origin_airports
        if availability.has_observed_direct_service(origin, hub)
    }
    searches.update(
        _search(route_plan, (hub,), (destination,))
        for hub in route_plan.candidate_hubs
        for destination in route_plan.destination_airports
        if availability.has_observed_direct_service(hub, destination)
    )
    return tuple(sorted(searches))
