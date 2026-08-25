import asyncio
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from flight_price_lab.api.app import create_app
from flight_price_lab.api.models import (
    Direction,
    PriceCompleteness,
    SearchSnapshot,
    SearchStatus,
    SelfTransferPolicy,
    TripSearchRequest,
)
from flight_price_lab.api.registry import InMemorySearchRegistry
from flight_price_lab.api.service import TripSearchService
from flight_price_lab.models import FlightLeg, FlightOffer


class MockProvider:
    def __init__(self, *, failing_hub: str | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.failing_hub = failing_hub

    async def search_direct(self, **arguments: object) -> list[FlightOffer]:
        self.calls.append(arguments)
        origins = tuple(arguments["origins"])
        destinations = tuple(arguments["destinations"])
        if self.failing_hub and self.failing_hub in (*origins, *destinations):
            raise TimeoutError
        travel_date = arguments["travel_date"]
        assert isinstance(travel_date, date)
        passenger_count = int(arguments["adults"]) + int(arguments["children"])
        if destinations == ("MXP",):
            return [
                make_offer(
                    origins[0],
                    "MXP",
                    travel_date,
                    8,
                    "U2 100",
                    "100",
                    passenger_count,
                )
            ]
        if origins == ("MXP",):
            return [
                make_offer(
                    "MXP",
                    destinations[0],
                    travel_date,
                    13,
                    "W4 200",
                    "100",
                    passenger_count,
                )
            ]
        return [
            make_offer(
                origins[0],
                destinations[0],
                travel_date,
                8,
                "FR 300",
                "300",
                passenger_count,
                duration=3,
            )
        ]

    async def calendar(self, **arguments: object) -> list[object]:
        del arguments
        return []


def make_offer(
    origin: str,
    destination: str,
    travel_date: date,
    hour: int,
    flight_number: str,
    price: str,
    passenger_count: int,
    *,
    duration: int = 2,
) -> FlightOffer:
    departure = datetime.combine(travel_date, datetime.min.time(), UTC) + timedelta(
        hours=hour
    )
    return FlightOffer(
        legs=(
            FlightLeg(
                origin=origin,
                destination=destination,
                departure=departure,
                arrival=departure + timedelta(hours=duration),
                airline=flight_number.split()[0],
                flight_number=flight_number,
            ),
        ),
        total_price=Decimal(price),
        currency="GBP",
        passenger_count=passenger_count,
        provider="mock",
        provider_offer_id=f"{origin}-{destination}",
        observed_at=datetime(2026, 8, 24, tzinfo=UTC),
    )


def request(
    policy: SelfTransferPolicy,
    *,
    baggage: bool = False,
    checked_bags: int = 0,
    outbound_window: dict[str, str | int] | None = None,
    return_window: dict[str, str | int] | None = None,
    outbound_max_connection: int = 360,
    return_max_connection: int = 360,
    max_extra_journey_minutes: int | None = None,
) -> TripSearchRequest:
    return TripSearchRequest.model_validate(
        {
            "origins": ["LGW"],
            "destinations": ["CAG"],
            "outbound_date": "2026-12-18",
            "return_date": "2026-12-28",
            "adults": 2,
            "children": 2,
            "baggage": {
                "cabin_bags": 4 if baggage else 0,
                "checked_bags": checked_bags,
            },
            "self_transfer_policy": policy,
            "connection_profile": "CONSERVATIVE",
            "outbound_time_window": {
                **(outbound_window or {}),
                "max_connection_minutes": outbound_max_connection,
            },
            "return_time_window": {
                **(return_window or {}),
                "max_connection_minutes": return_max_connection,
            },
            "max_extra_journey_minutes": max_extra_journey_minutes,
        }
    )


async def run_search(
    policy: SelfTransferPolicy,
    *,
    provider: MockProvider | None = None,
    baggage: bool = False,
    checked_bags: int = 0,
    outbound_window: dict[str, str | int] | None = None,
    return_window: dict[str, str | int] | None = None,
    outbound_max_connection: int = 360,
    return_max_connection: int = 360,
    max_extra_journey_minutes: int | None = None,
):
    provider = provider or MockProvider()
    registry = InMemorySearchRegistry()
    service = TripSearchService(provider, registry, hubs=("MXP",))
    search_id = await service.start(
        request(
            policy,
            baggage=baggage,
            checked_bags=checked_bags,
            outbound_window=outbound_window,
            return_window=return_window,
            outbound_max_connection=outbound_max_connection,
            return_max_connection=return_max_connection,
            max_extra_journey_minutes=max_extra_journey_minutes,
        )
    )
    for _ in range(100):
        snapshot = await registry.get(search_id)
        assert snapshot is not None
        if snapshot.status in (
            SearchStatus.COMPLETED,
            SearchStatus.PARTIAL_FAILURE,
            SearchStatus.FAILED,
        ):
            return snapshot, registry, provider
        await asyncio.sleep(0.001)
    raise AssertionError("search did not complete")


def test_synthetic_option_keeps_two_booking_constituents() -> None:
    snapshot, registry, _ = asyncio.run(run_search(SelfTransferPolicy.OUTBOUND_ONLY))
    option = next(
        item
        for item in snapshot.outbound.feasible_options
        if item.flight_numbers == ["U2 100", "W4 200"]
    )

    constituents = asyncio.run(
        registry.get_booking_candidate(snapshot.search_id, option.id)
    )

    assert constituents is not None
    assert len(constituents) == 2
    assert [offer.legs[0].flight_number for offer in constituents] == [
        "U2 100",
        "W4 200",
    ]


def test_request_validation_and_structured_http_error() -> None:
    client = TestClient(create_app(MockProvider()))
    response = client.post(
        "/api/search",
        json={
            "origins": ["LONDON"],
            "destinations": ["CAG"],
            "outbound_date": "2026-12-18",
            "adults": 0,
            "children": 0,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_return_policy_requires_return_date() -> None:
    with pytest.raises(ValidationError):
        TripSearchRequest.model_validate(
            {
                "origins": ["LGW"],
                "destinations": ["CAG"],
                "outbound_date": "2026-12-18",
                "adults": 1,
                "self_transfer_policy": "RETURN_ONLY",
            }
        )


@pytest.mark.parametrize(
    ("policy", "outbound", "return_"),
    [
        (SelfTransferPolicy.NONE, False, False),
        (SelfTransferPolicy.OUTBOUND_ONLY, True, False),
        (SelfTransferPolicy.RETURN_ONLY, False, True),
        (SelfTransferPolicy.BOTH, True, True),
    ],
)
def test_directional_self_transfer_policies(
    policy: SelfTransferPolicy, outbound: bool, return_: bool
) -> None:
    snapshot, _, _ = asyncio.run(run_search(policy))

    assert (snapshot.outbound.fastest_feasible is not None) is outbound
    assert snapshot.return_ is not None
    assert (snapshot.return_.fastest_feasible is not None) is return_


def test_nonstop_only_does_not_trigger_hub_searches() -> None:
    snapshot, _, provider = asyncio.run(run_search(SelfTransferPolicy.NONE))

    assert len(provider.calls) == 2
    assert len(snapshot.outbound.nonstop_options) == 1
    assert snapshot.return_ is not None
    assert len(snapshot.return_.nonstop_options) == 1


def test_identical_provider_searches_are_deduplicated_within_run() -> None:
    provider = MockProvider()
    service = TripSearchService(provider, InMemorySearchRegistry(), hubs=("MXP",))
    search_request = request(SelfTransferPolicy.NONE)

    async def duplicate() -> None:
        await asyncio.gather(
            service._provider_search(
                search_request, ("LGW",), ("CAG",), date(2026, 12, 18)
            ),
            service._provider_search(
                search_request, ("LGW",), ("CAG",), date(2026, 12, 18)
            ),
        )

    asyncio.run(duplicate())
    assert len(provider.calls) == 1


def test_baseline_event_precedes_synthetic_alternative() -> None:
    snapshot, registry, _ = asyncio.run(run_search(SelfTransferPolicy.OUTBOUND_ONLY))

    async def names() -> list[str]:
        return [event.event async for event in registry.events(snapshot.search_id)]

    events = asyncio.run(names())
    assert events.index("baseline_found") < events.index("hub_started")
    assert events.index("hub_started") < events.index("alternative_found")
    assert events[-1] == "search_completed"


def test_hub_failure_is_partial_not_total_failure() -> None:
    snapshot, _, _ = asyncio.run(
        run_search(
            SelfTransferPolicy.OUTBOUND_ONLY,
            provider=MockProvider(failing_hub="MXP"),
        )
    )

    assert snapshot.outbound.baseline is not None
    assert snapshot.status is SearchStatus.PARTIAL_FAILURE
    assert any(error.code == "provider_timeout" for error in snapshot.errors)


def test_savings_and_duration_are_calculated_against_nonstop() -> None:
    snapshot, _, _ = asyncio.run(run_search(SelfTransferPolicy.OUTBOUND_ONLY))
    option = snapshot.outbound.cheapest_feasible

    assert option is not None
    assert option.saving_vs_nonstop_amount == Decimal(100)
    assert option.saving_vs_nonstop_percent == Decimal("33.33")
    assert option.extra_minutes_vs_nonstop == 240


def test_unknown_baggage_price_propagates_without_zero() -> None:
    snapshot, _, _ = asyncio.run(
        run_search(SelfTransferPolicy.OUTBOUND_ONLY, baggage=True)
    )
    option = snapshot.outbound.fastest_feasible

    assert option is not None
    assert option.price_completeness is PriceCompleteness.UNKNOWN
    assert option.ancillary_price_low is None
    assert option.effective_price_low is None
    assert option.saving_vs_nonstop_amount == Decimal(100)
    assert option.saving_vs_nonstop_percent == Decimal("33.33")


def test_ryanair_cabin_bag_range_reaches_trip_option() -> None:
    provider = MockProvider()
    service = TripSearchService(provider, InMemorySearchRegistry(), hubs=("MXP",))
    flight = make_offer("STN", "CAG", date(2026, 12, 18), 19, "FR 2687", "741", 4)

    option = service._direct_option(
        flight, Direction.OUTBOUND, request(SelfTransferPolicy.NONE, baggage=True)
    )

    assert option.ancillary_price_low == Decimal(24)
    assert option.ancillary_price_high == Decimal(144)
    assert option.price_completeness is PriceCompleteness.PARTIAL
    assert option.baggage_estimates[0].carrier_codes == ["FR"]
    assert option.baggage_estimates[0].price_low == Decimal(24)
    assert option.baggage_estimates[0].price_high == Decimal(144)


def test_passenger_composition_and_checked_bags_are_preserved() -> None:
    _, _, provider = asyncio.run(
        run_search(SelfTransferPolicy.BOTH, baggage=True, checked_bags=3)
    )

    assert provider.calls
    assert all(call["adults"] == 2 for call in provider.calls)
    assert all(call["children"] == 2 for call in provider.calls)
    assert all(call["checked_bags"] == 3 for call in provider.calls)
    assert all(call["cabin_bags"] == 4 for call in provider.calls)


def test_api_routes_are_registered() -> None:
    paths = {route.path for route in create_app(MockProvider()).routes}
    assert {
        "/api/search",
        "/api/search/key",
        "/api/search/{search_id}",
        "/api/search/{search_id}/events",
        "/api/calendar",
        "/api/provider-usage",
        "/api/health",
    } <= paths


def test_health_endpoint_needs_no_provider_call() -> None:
    provider = MockProvider()
    response = TestClient(create_app(provider)).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert provider.calls == []


def test_search_key_endpoint_does_not_call_provider() -> None:
    provider = MockProvider()
    client = TestClient(create_app(provider))
    payload = request(SelfTransferPolicy.NONE).model_dump(mode="json")

    first = client.post("/api/search/key", json=payload)
    payload["origins"] = list(reversed(payload["origins"]))
    second = client.post("/api/search/key", json=payload)

    assert first.status_code == 200
    assert first.json()["search_key"] == second.json()["search_key"]
    assert provider.calls == []


def test_search_diagnostics_are_isolated_per_trip() -> None:
    first = SearchSnapshot(search_id="first", status=SearchStatus.STARTED)
    second = SearchSnapshot(search_id="second", status=SearchStatus.STARTED)

    first.diagnostics.backend_cache_hits += 1
    first.diagnostics.provider_calls_avoided_this_invocation += 1

    assert second.diagnostics.backend_cache_hits == 0
    assert second.diagnostics.provider_calls_avoided_this_invocation == 0


def test_nonstop_option_uses_leg_timestamps() -> None:
    provider = MockProvider()
    service = TripSearchService(provider, InMemorySearchRegistry(), hubs=("MXP",))
    flight = make_offer("LGW", "CAG", date(2026, 12, 18), 8, "FR 300", "300", 4)

    option = service._direct_option(
        flight, Direction.OUTBOUND, request(SelfTransferPolicy.NONE)
    )

    assert option.departure_at == flight.legs[0].departure
    assert option.arrival_at == flight.legs[0].arrival


def test_synthetic_option_uses_first_departure_and_final_arrival() -> None:
    snapshot, _, _ = asyncio.run(run_search(SelfTransferPolicy.OUTBOUND_ONLY))
    option = snapshot.outbound.fastest_feasible

    assert option is not None
    assert option.departure_at.hour == 8
    assert option.arrival_at.hour == 15


def test_trip_option_json_preserves_timezone_offsets() -> None:
    provider = MockProvider()
    service = TripSearchService(provider, InMemorySearchRegistry(), hubs=("MXP",))
    flight = make_offer("LGW", "CAG", date(2026, 12, 18), 8, "FR 300", "300", 4)
    offset = timezone(timedelta(hours=2))
    shifted_leg = flight.legs[0].model_copy(
        update={
            "departure": flight.legs[0].departure.astimezone(offset),
            "arrival": flight.legs[0].arrival.astimezone(offset),
        }
    )
    shifted = flight.model_copy(update={"legs": (shifted_leg,)})

    option = service._direct_option(
        shifted, Direction.OUTBOUND, request(SelfTransferPolicy.NONE)
    )
    payload = option.model_dump(mode="json")

    assert payload["departure_at"].endswith("+02:00")
    assert payload["arrival_at"].endswith("+02:00")


@pytest.mark.parametrize(
    ("window", "accepted"),
    [
        ({"earliest_departure_time": "09:00"}, False),
        ({"latest_arrival_time": "14:59"}, False),
        (
            {
                "earliest_departure_time": "08:00",
                "latest_arrival_time": "15:00",
            },
            True,
        ),
    ],
)
def test_synthetic_time_window_uses_first_departure_and_final_arrival(
    window: dict[str, str], accepted: bool
) -> None:
    snapshot, _, _ = asyncio.run(
        run_search(
            SelfTransferPolicy.OUTBOUND_ONLY,
            outbound_window=window,
        )
    )

    assert (snapshot.outbound.fastest_feasible is not None) is accepted


def test_outbound_and_return_time_windows_are_independent() -> None:
    snapshot, _, _ = asyncio.run(
        run_search(
            SelfTransferPolicy.BOTH,
            outbound_window={"earliest_departure_time": "09:00"},
            return_window={"earliest_departure_time": "08:00"},
        )
    )

    assert snapshot.outbound.fastest_feasible is None
    assert snapshot.return_ is not None
    assert snapshot.return_.fastest_feasible is not None


@pytest.mark.parametrize(("maximum", "accepted"), [(179, False), (180, True)])
def test_maximum_connection_boundary(maximum: int, accepted: bool) -> None:
    snapshot, _, _ = asyncio.run(
        run_search(
            SelfTransferPolicy.OUTBOUND_ONLY,
            outbound_max_connection=maximum,
        )
    )

    assert (snapshot.outbound.fastest_feasible is not None) is accepted


def test_outbound_and_return_maximum_connections_are_independent() -> None:
    snapshot, _, _ = asyncio.run(
        run_search(
            SelfTransferPolicy.BOTH,
            outbound_max_connection=179,
            return_max_connection=180,
        )
    )

    assert snapshot.outbound.feasible_options == []
    assert snapshot.return_ is not None
    assert len(snapshot.return_.feasible_options) == 1


@pytest.mark.parametrize(("maximum", "accepted"), [(239, False), (240, True)])
def test_maximum_extra_journey_filter(maximum: int, accepted: bool) -> None:
    snapshot, _, _ = asyncio.run(
        run_search(
            SelfTransferPolicy.OUTBOUND_ONLY,
            max_extra_journey_minutes=maximum,
        )
    )

    assert (snapshot.outbound.fastest_feasible is not None) is accepted
