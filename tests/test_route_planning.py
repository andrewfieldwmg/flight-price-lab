from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from flight_price_lab.models import FlightLeg, FlightOffer
from flight_price_lab.routing.airport_groups import (
    INITIAL_CANDIDATE_HUBS,
    LONDON,
    SARDINIA,
    prioritize_candidate_hubs,
)
from flight_price_lab.routing.availability import (
    RouteAvailabilityIndex,
    observe_searchapi_response,
)
from flight_price_lab.routing.hub_synthesis import synthesize_via_hubs
from flight_price_lab.routing.planning import (
    RoutePlan,
    plan_provider_searches,
    plan_route_discovery_searches,
)


def route_plan(*hubs: str) -> RoutePlan:
    return RoutePlan(
        origin_airports=LONDON,
        destination_airports=SARDINIA,
        candidate_hubs=hubs or INITIAL_CANDIDATE_HUBS,
        travel_date=date(2026, 12, 18),
        adults=2,
        children=2,
        currency="GBP",
    )


def test_hub_priority_promotes_evidence_without_dropping_coverage() -> None:
    hubs = ("BGY", "LIN", "FCO", "MXP", "NAP")

    prioritized = prioritize_candidate_hubs(hubs)

    assert prioritized == ("MXP", "LIN", "BGY", "FCO", "NAP")
    assert set(prioritized) == set(hubs)


def offer(
    origin: str,
    destination: str,
    departure: datetime,
    *,
    duration: timedelta,
    price: str,
    offer_id: str,
    extra_leg: FlightLeg | None = None,
) -> FlightOffer:
    legs = (
        FlightLeg(
            origin=origin,
            destination=destination,
            departure=departure,
            arrival=departure + duration,
            airline="Example Air",
            flight_number=f"EX {offer_id}",
        ),
    )
    if extra_leg is not None:
        legs += (extra_leg,)
    return FlightOffer(
        legs=legs,
        total_price=Decimal(price),
        currency="GBP",
        passenger_count=4,
        provider="test",
        provider_offer_id=offer_id,
        observed_at=datetime(2026, 8, 24, tzinfo=UTC),
    )


def test_sardinia_plan_has_two_unique_searches_per_hub() -> None:
    searches = plan_route_discovery_searches(route_plan())

    assert len(searches) == 16
    assert len(set(searches)) == 16
    assert all(search.stops == "nonstop" for search in searches)


def test_query_planning_deduplicates_hubs_and_preserves_airport_groups() -> None:
    searches = plan_route_discovery_searches(route_plan("MXP", "MXP", "FCO"))

    assert len(searches) == 4
    assert any(
        search.departure_airports == LONDON and search.arrival_airports == ("MXP",)
        for search in searches
    )
    assert any(
        search.departure_airports == ("FCO",) and search.arrival_airports == SARDINIA
        for search in searches
    )
    assert not any(
        search.departure_airports == ("MXP",) and search.arrival_airports == ("FCO",)
        for search in searches
    )


def test_grouped_response_populates_route_pairs_independently() -> None:
    payload = {
        "search_metadata": {"created_at": "2026-08-24T10:00:00Z"},
        "search_parameters": {
            "departure_id": "MXP",
            "arrival_id": "CAG,OLB,AHO",
        },
        "other_flights": [
            {
                "flights": [
                    {
                        "departure_airport": {"id": "MXP"},
                        "arrival_airport": {"id": "CAG"},
                    }
                ]
            },
            {
                "flights": [
                    {
                        "departure_airport": {"id": "MXP"},
                        "arrival_airport": {"id": "AHO"},
                    }
                ]
            },
        ],
    }

    observations = observe_searchapi_response(payload, source="fixture.json")

    assert {
        (item.destination, item.direct_service_observed) for item in observations
    } == {
        ("CAG", True),
        ("OLB", False),
        ("AHO", True),
    }


def test_fare_planner_excludes_unobserved_destination_route() -> None:
    payload = {
        "search_metadata": {"created_at": "2026-08-24T10:00:00Z"},
        "search_parameters": {
            "departure_id": "MXP",
            "arrival_id": "CAG,OLB",
        },
        "other_flights": [
            {
                "flights": [
                    {
                        "departure_airport": {"id": "MXP"},
                        "arrival_airport": {"id": "CAG"},
                    }
                ]
            }
        ],
    }
    availability = RouteAvailabilityIndex()
    for observation in observe_searchapi_response(payload, source="fixture.json"):
        availability.record(observation)
    plan = RoutePlan(
        origin_airports=("MXP",),
        destination_airports=("CAG", "OLB"),
        candidate_hubs=("MXP",),
        travel_date=date(2026, 12, 18),
        adults=2,
        children=2,
        currency="GBP",
    )

    searches = plan_provider_searches(plan, availability)

    assert any(search.arrival_airports == ("CAG",) for search in searches)
    assert not any(search.arrival_airports == ("OLB",) for search in searches)


def test_synthesis_spans_origins_destinations_and_hubs_with_one_stop() -> None:
    start = datetime(2026, 12, 18, 8, tzinfo=UTC)
    offers_by_route = {
        ("LGW", "MXP"): [
            offer(
                "LGW",
                "MXP",
                start,
                duration=timedelta(hours=2),
                price="100",
                offer_id="lgw-mxp",
            )
        ],
        ("MXP", "CAG"): [
            offer(
                "MXP",
                "CAG",
                start + timedelta(hours=5),
                duration=timedelta(hours=1),
                price="80",
                offer_id="mxp-cag",
            )
        ],
        ("LHR", "FCO"): [
            offer(
                "LHR",
                "FCO",
                start,
                duration=timedelta(hours=2),
                price="120",
                offer_id="lhr-fco",
            )
        ],
        ("FCO", "OLB"): [
            offer(
                "FCO",
                "OLB",
                start + timedelta(hours=5),
                duration=timedelta(minutes=45),
                price="70",
                offer_id="fco-olb",
            )
        ],
    }

    result = synthesize_via_hubs(offers_by_route, route_plan("MXP", "FCO"))

    assert {item.hub for item in result.itineraries} == {"MXP", "FCO"}
    assert all(item.itinerary.number_of_stops == 1 for item in result.itineraries)
    assert all(len(item.itinerary.components) == 2 for item in result.itineraries)


def test_multileg_and_cross_airport_hub_transfers_are_excluded() -> None:
    start = datetime(2026, 12, 18, 8, tzinfo=UTC)
    onward_leg = FlightLeg(
        origin="FCO",
        destination="MXP",
        departure=start + timedelta(hours=3),
        arrival=start + timedelta(hours=4),
        airline="Example Air",
        flight_number="EX onward",
    )
    offers_by_route = {
        ("LGW", "MXP"): [
            offer(
                "LGW",
                "FCO",
                start,
                duration=timedelta(hours=2),
                price="100",
                offer_id="multi",
                extra_leg=onward_leg,
            ),
            offer(
                "LGW",
                "MXP",
                start,
                duration=timedelta(hours=2),
                price="110",
                offer_id="direct",
            ),
        ],
        # Deliberately mislabeled route data must not enable MXP -> LIN transfer.
        ("MXP", "CAG"): [
            offer(
                "LIN",
                "CAG",
                start + timedelta(hours=5),
                duration=timedelta(hours=1),
                price="80",
                offer_id="lin-cag",
            )
        ],
    }

    result = synthesize_via_hubs(offers_by_route, route_plan("MXP"))

    assert result.itineraries == ()


def test_pareto_frontier_across_hubs_and_direct_saving() -> None:
    start = datetime(2026, 12, 18, 8, tzinfo=UTC)
    offers_by_route = {}
    specifications = {
        "MXP": ("100", "80", timedelta(hours=6)),
        "FCO": ("120", "90", timedelta(hours=5, minutes=30)),
        "BGY": ("150", "100", timedelta(hours=7)),
    }
    for hub, (first_price, second_price, final_offset) in specifications.items():
        offers_by_route[("LGW", hub)] = [
            offer(
                "LGW",
                hub,
                start,
                duration=timedelta(hours=2),
                price=first_price,
                offer_id=f"to-{hub}",
            )
        ]
        offers_by_route[(hub, "CAG")] = [
            offer(
                hub,
                "CAG",
                start + timedelta(hours=5),
                duration=final_offset - timedelta(hours=5),
                price=second_price,
                offer_id=f"from-{hub}",
            )
        ]

    result = synthesize_via_hubs(
        offers_by_route,
        route_plan("MXP", "FCO", "BGY"),
        direct_benchmark_price=Decimal(300),
    )

    assert result.cheapest is not None and result.cheapest.hub == "MXP"
    assert result.fastest is not None and result.fastest.hub == "FCO"
    assert [item.hub for item in result.frontier] == ["MXP", "FCO"]
    assert result.cheapest_saving == Decimal(120)
