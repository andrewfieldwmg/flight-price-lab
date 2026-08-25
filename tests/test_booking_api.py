import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from pydantic import SecretStr

from flight_price_lab.api.app import create_app
from flight_price_lab.api.booking import (
    AeroitaliaHandoffAdapter,
    BritishAirwaysHandoffAdapter,
    EasyJetHandoffAdapter,
    HandoffCapability,
    PriceChangeStatus,
    ResolvedHandoff,
    WizzAirHandoffAdapter,
    classify_price_change,
)
from flight_price_lab.api.models import SearchSnapshot
from flight_price_lab.api.registry import InMemorySearchRegistry
from flight_price_lab.api.service import TripSearchService
from flight_price_lab.models.flight import FlightLeg, FlightOffer
from flight_price_lab.storage.database import BookingCandidateStore


def offer(flight_number: str, price: str = "100") -> FlightOffer:
    departure = datetime(2026, 12, 18, 10, tzinfo=UTC)
    return FlightOffer(
        legs=(
            FlightLeg(
                origin="STN",
                destination="CAG",
                departure=departure,
                arrival=departure + timedelta(hours=3),
                airline="Ryanair",
                flight_number=flight_number,
            ),
        ),
        total_price=Decimal(price),
        currency="GBP",
        passenger_count=4,
        provider="SearchAPI",
        provider_offer_id=flight_number,
        observed_at=datetime.now(UTC),
    )


class Resolver:
    def __init__(self, prices: dict[str, str | None]) -> None:
        self.prices = prices

    async def resolve(self, selected: FlightOffer) -> ResolvedHandoff:
        value = self.prices[selected.legs[0].flight_number]
        if value is None:
            raise ValueError("unavailable")
        return ResolvedHandoff(
            current_price=Decimal(value),
            booking_url="https://www.google.com/travel/clk/f",
            booking_post_data=SecretStr("u=opaque-provider-post"),
            capability=HandoffCapability.EXACT_FLIGHT_HANDOFF,
            adults=2,
            children=2,
            exact_flight_verified=True,
            passenger_composition_verified=True,
            carrier="FR",
            flight_number=selected.legs[0].flight_number,
            origin=selected.legs[0].origin,
            destination=selected.legs[-1].destination,
            travel_date=selected.legs[0].departure.date().isoformat(),
        )


class Launcher:
    async def launch(self, handoff: ResolvedHandoff) -> str:
        assert handoff.booking_post_data.get_secret_value() == "u=opaque-provider-post"
        return "https://www.ryanair.com/gb/en/trip/flights/select"


class MixedCapabilityResolver:
    async def resolve(self, selected: FlightOffer) -> ResolvedHandoff:
        carrier = selected.legs[0].flight_number.split()[0]
        prefilled = carrier == "W4"
        return ResolvedHandoff(
            current_price=None if prefilled else Decimal(328),
            booking_url="https://www.google.com/travel/clk/f",
            booking_post_data=SecretStr("u=opaque-provider-post"),
            capability=(
                HandoffCapability.PREFILLED_SEARCH
                if prefilled
                else HandoffCapability.EXACT_FLIGHT_HANDOFF
            ),
            adults=2,
            children=2,
            exact_flight_verified=not prefilled,
            passenger_composition_verified=True,
            carrier=carrier,
            flight_number=selected.legs[0].flight_number,
            origin=selected.legs[0].origin,
            destination=selected.legs[-1].destination,
            travel_date=selected.legs[0].departure.date().isoformat(),
        )


class SyntheticSearchProvider:
    async def search_direct(self, **arguments: object) -> list[FlightOffer]:
        origins = tuple(arguments["origins"])
        destinations = tuple(arguments["destinations"])
        travel_date = arguments["travel_date"]
        assert isinstance(travel_date, date)
        if destinations == ("MXP",):
            return [route_offer("LGW", "MXP", travel_date, 10, "U2 8309", "328")]
        if origins == ("MXP",):
            return [route_offer("MXP", "CAG", travel_date, 15, "W4 6997", "158")]
        if origins == ("LGW",) and destinations == ("CAG",):
            return [route_offer("LGW", "CAG", travel_date, 8, "FR 2687", "741")]
        return []


def route_offer(
    origin: str,
    destination: str,
    travel_date: date,
    hour: int,
    flight_number: str,
    price: str,
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
                arrival=departure + timedelta(hours=2),
                airline=flight_number.split()[0],
                flight_number=flight_number,
            ),
        ),
        total_price=Decimal(price),
        currency="GBP",
        passenger_count=4,
        provider="SearchAPI",
        provider_offer_id=f"provider-{flight_number}",
        observed_at=datetime(2026, 8, 25, tzinfo=UTC),
        raw_metadata={
            "provider_action_metadata": {"booking_token": "opaque-test-token"}
        },
    )


def prepare_client(prices: dict[str, str | None]) -> tuple[TestClient, object]:
    app = create_app(booking_resolver=Resolver(prices), handoff_launcher=Launcher())
    return TestClient(app), app


def test_fresh_price_and_opaque_handoff_are_returned_for_exact_option() -> None:
    client, app = prepare_client({"FR 2687": "113"})
    search_id = "search-1"
    selected = offer("FR 2687")
    asyncio.run(
        app.state.registry.create(
            SearchSnapshot(search_id=search_id, trip_id=search_id, status="completed")
        )
    )
    asyncio.run(
        app.state.registry.register_booking_candidate(
            search_id, "selected", (selected,)
        )
    )
    response = client.post(
        "/api/booking/prepare",
        json={"search_id": search_id, "selected_option_ids": ["selected"]},
    )
    body = response.json()
    assert body["tickets"][0]["flight_number"] == "FR 2687"
    assert body["tickets"][0]["current_price"] == "113"
    assert body["tickets"][0]["capability"] == "EXACT_FLIGHT_HANDOFF"
    assert body["tickets"][0]["fare_selected"] is False
    assert body["tickets"][0]["adults"] == 2
    assert body["tickets"][0]["children"] == 2
    assert body["tickets"][0]["departure_at"].endswith(("Z", "+00:00"))
    assert body["tickets"][0]["arrival_at"].endswith(("Z", "+00:00"))
    assert body["tickets"][0]["exact_flight_verified"] is True
    assert "opaque-provider-post" not in response.text
    expired = client.post(
        "/api/booking/prepare",
        json={"search_id": search_id, "selected_option_ids": ["other"]},
    )
    assert expired.status_code == 409
    assert expired.json()["detail"]["code"] == "booking_context_expired"


def test_material_change_requires_acknowledgement() -> None:
    client, app = prepare_client({"FR 2687": "150"})
    asyncio.run(
        app.state.registry.create(SearchSnapshot(search_id="s", status="completed"))
    )
    asyncio.run(
        app.state.registry.register_booking_candidate("s", "o", (offer("FR 2687"),))
    )
    prepared = client.post(
        "/api/booking/prepare", json={"search_id": "s", "selected_option_ids": ["o"]}
    ).json()
    ticket = prepared["tickets"][0]
    assert ticket["price_change_status"] == "MATERIAL_INCREASE"
    path = (
        f"/api/booking/{prepared['booking_session_id']}/handoff/{ticket['ticket_id']}"
    )
    assert client.post(path).status_code == 409
    assert (
        client.post(
            f"{path}?acknowledge_material_change=true", follow_redirects=False
        ).status_code
        == 303
    )


def test_price_decrease_never_requires_acknowledgement() -> None:
    assert (
        classify_price_change(Decimal(849), Decimal(813))
        is PriceChangeStatus.PRICE_DECREASED
    )
    client, app = prepare_client({"FR 2687": "64"})
    asyncio.run(
        app.state.registry.create(SearchSnapshot(search_id="down", status="completed"))
    )
    asyncio.run(
        app.state.registry.register_booking_candidate("down", "o", (offer("FR 2687"),))
    )
    prepared = client.post(
        "/api/booking/prepare",
        json={"search_id": "down", "selected_option_ids": ["o"]},
    ).json()
    ticket = prepared["tickets"][0]
    assert prepared["state"] == "READY"
    assert ticket["price_change_status"] == "PRICE_DECREASED"
    assert ticket["material_change_acknowledgement_required"] is False


def test_multi_ticket_waits_for_all_and_failure_blocks_ready() -> None:
    client, app = prepare_client({"FR 1": "101", "FR 2": None})
    asyncio.run(
        app.state.registry.create(SearchSnapshot(search_id="s", status="completed"))
    )
    asyncio.run(
        app.state.registry.register_booking_candidate(
            "s", "connection", (offer("FR 1"), offer("FR 2"))
        )
    )
    body = client.post(
        "/api/booking/prepare",
        json={"search_id": "s", "selected_option_ids": ["connection"]},
    ).json()
    assert len(body["tickets"]) == 2
    assert body["state"] == "FAILED"
    assert {ticket["status"] for ticket in body["tickets"]} == {"READY", "FAILED"}


def test_easyjet_adapter_validates_exact_flight_party_route_and_date() -> None:
    handoff = ResolvedHandoff(
        current_price=Decimal(328),
        booking_url="https://www.google.com/travel/clk/f",
        booking_post_data=SecretStr("u=opaque"),
        capability=HandoffCapability.EXACT_FLIGHT_HANDOFF,
        adults=2,
        children=2,
        exact_flight_verified=True,
        passenger_composition_verified=True,
        carrier="U2",
        flight_number="U2 8309",
        origin="LGW",
        destination="MXP",
        travel_date="2026-12-18",
    )
    url = (
        "http://www.easyjet.com/deeplink?dep=LGW&dest=MXP&dd=2026-12-18"
        "&apax=2&cpax=2&ipax=0&xdfn=8309"
    )

    validated = EasyJetHandoffAdapter().validate_redirect(url, handoff)

    assert validated.startswith("https://www.easyjet.com/deeplink?")
    assert EasyJetHandoffAdapter.capability is HandoffCapability.EXACT_FLIGHT_HANDOFF
    assert handoff.fare_selected is False


def test_wizz_adapter_validates_prefilled_search_without_claiming_flight() -> None:
    handoff = ResolvedHandoff(
        current_price=None,
        booking_url="https://www.google.com/travel/clk/f",
        booking_post_data=SecretStr("u=opaque"),
        capability=HandoffCapability.PREFILLED_SEARCH,
        adults=2,
        children=2,
        exact_flight_verified=False,
        passenger_composition_verified=True,
        carrier="W4",
        flight_number="W4 6997",
        origin="MXP",
        destination="CAG",
        travel_date="2026-12-18",
    )
    url = (
        "https://www.wizzair.com/en-gb/booking/select-flight/"
        "MXP/CAG/2026-12-18/null/2/2/0?ref=gfs"
    )

    validated = WizzAirHandoffAdapter().validate_redirect(url, handoff)

    assert validated == url
    assert WizzAirHandoffAdapter.capability is HandoffCapability.PREFILLED_SEARCH
    assert handoff.exact_flight_verified is False
    assert handoff.passenger_composition_verified is True
    assert handoff.current_price is None
    assert handoff.fare_selected is False


def test_prefilled_wizz_ticket_does_not_block_combined_assisted_handoff() -> None:
    app = create_app(
        booking_resolver=MixedCapabilityResolver(), handoff_launcher=Launcher()
    )
    client = TestClient(app)
    asyncio.run(
        app.state.registry.create(SearchSnapshot(search_id="s", status="completed"))
    )
    asyncio.run(
        app.state.registry.register_booking_candidate(
            "s", "connection", (offer("U2 8309", "328"), offer("W4 6997", "158"))
        )
    )

    body = client.post(
        "/api/booking/prepare",
        json={"search_id": "s", "selected_option_ids": ["connection"]},
    ).json()

    assert body["state"] == "READY"
    assert [ticket["status"] for ticket in body["tickets"]] == [
        "READY",
        "VERIFY_ON_AIRLINE",
    ]
    easyjet = body["tickets"][0]
    assert easyjet["capability"] == "EXACT_FLIGHT_HANDOFF"
    assert easyjet["exact_flight_verified"] is True
    assert easyjet["passenger_composition_verified"] is True
    assert easyjet["current_price"] == "328"
    wizz = body["tickets"][1]
    assert wizz["capability"] == "PREFILLED_SEARCH"
    assert wizz["current_price"] is None
    assert wizz["exact_flight_verified"] is False
    assert wizz["passenger_composition_verified"] is True


def test_synthetic_constituent_lineage_survives_registry_recreation() -> None:
    first_registry = InMemorySearchRegistry(BookingCandidateStore())
    selected = (offer("U2 8309", "328"), offer("W4 6997", "158"))
    asyncio.run(
        first_registry.create(SearchSnapshot(search_id="cached", status="completed"))
    )
    asyncio.run(
        first_registry.register_booking_candidate("cached", "synthetic", selected)
    )

    restored_registry = InMemorySearchRegistry(BookingCandidateStore())
    restored = asyncio.run(
        restored_registry.get_booking_candidate("cached", "synthetic")
    )

    assert restored is not None
    assert len(restored) == 2
    assert [item.legs[0].flight_number for item in restored] == [
        "U2 8309",
        "W4 6997",
    ]
    assert [item.total_price for item in restored] == [Decimal(328), Decimal(158)]


def test_real_synthetic_option_id_prepares_two_mixed_tickets() -> None:
    app = create_app(
        provider=SyntheticSearchProvider(),
        booking_resolver=MixedCapabilityResolver(),
        handoff_launcher=Launcher(),
    )
    service = TripSearchService(
        SyntheticSearchProvider(), app.state.registry, hubs=("MXP",)
    )

    async def search() -> tuple[str, str]:
        request = {
            "origins": ["LGW"],
            "destinations": ["CAG"],
            "outbound_date": "2026-12-18",
            "adults": 2,
            "children": 2,
            "baggage": {"cabin_bags": 1, "checked_bags": 0},
            "self_transfer_policy": "OUTBOUND_ONLY",
            "connection_profile": "CONSERVATIVE",
            "currency": "GBP",
        }
        from flight_price_lab.api.models import TripSearchRequest

        search_id = await service.start(TripSearchRequest.model_validate(request))
        for _ in range(100):
            snapshot = await app.state.registry.get(search_id)
            assert snapshot is not None
            if snapshot.status.value in {"completed", "partial_failure"}:
                option = next(
                    item
                    for item in snapshot.outbound.feasible_options
                    if item.flight_numbers == ["U2 8309", "W4 6997"]
                )
                return search_id, option.id
            await asyncio.sleep(0.001)
        raise AssertionError("synthetic search did not complete")

    search_id, option_id = asyncio.run(search())
    response = TestClient(app).post(
        "/api/booking/prepare",
        json={"search_id": search_id, "selected_option_ids": [option_id]},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["tickets"]) == 2
    assert [ticket["flight_number"] for ticket in body["tickets"]] == [
        "U2 8309",
        "W4 6997",
    ]
    assert [ticket["capability"] for ticket in body["tickets"]] == [
        "EXACT_FLIGHT_HANDOFF",
        "PREFILLED_SEARCH",
    ]
    assert body["tickets"][1]["current_price"] is None
    assert body["current_total"] is None


def test_aeroitalia_adapter_validates_prefilled_route_date_and_party() -> None:
    handoff = ResolvedHandoff(
        current_price=Decimal(411),
        booking_url="https://www.google.com/travel/clk/f",
        booking_post_data=SecretStr("u=opaque"),
        capability=HandoffCapability.PREFILLED_SEARCH,
        adults=2,
        children=2,
        carrier="XZ",
        flight_number="XZ 2331",
        origin="FCO",
        destination="CAG",
        travel_date="2026-12-18",
    )
    url = (
        "https://book.aeroitalia.com/deeplink/search?ADT=2&CHD=2&INL=0"
        "&o1=FCO&d1=CAG&dd1=2026-12-18&r=false"
    )

    assert AeroitaliaHandoffAdapter().validate_redirect(url, handoff) == url
    assert AeroitaliaHandoffAdapter.capability is HandoffCapability.PREFILLED_SEARCH
    assert handoff.exact_flight_verified is False


def test_british_airways_adapter_validates_encoded_flight_context() -> None:
    handoff = ResolvedHandoff(
        current_price=Decimal("1390.20"),
        booking_url="https://www.google.com/travel/clk/f",
        booking_post_data=SecretStr("u=opaque"),
        capability=HandoffCapability.PREFILLED_SEARCH,
        adults=2,
        children=2,
        carrier="BA",
        flight_number="BA 534",
        origin="LHR",
        destination="NAP",
        travel_date="2026-12-18",
    )
    url = (
        "https://www.britishairways.com/nx/b/airselect/en/gb/book/metasearch"
        "?ad=2&ch=2&inf=0&ond1=LHR-NAP_2026-12-18T11%3A45%3A00_BA0534_M"
    )

    assert BritishAirwaysHandoffAdapter().validate_redirect(url, handoff) == url
    assert BritishAirwaysHandoffAdapter.capability is HandoffCapability.PREFILLED_SEARCH
    assert handoff.exact_flight_verified is False
