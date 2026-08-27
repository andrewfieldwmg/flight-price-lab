import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from flight_price_lab.api.models import (
    Direction,
    HistoryStatus,
    PriceCompleteness,
    SearchSnapshot,
    SearchStatus,
    SelfTransferPolicy,
    TripLegSummary,
    TripOption,
    TripSearchRequest,
)
from flight_price_lab.api.provider import ProviderSearchResult
from flight_price_lab.api.registry import InMemorySearchRegistry
from flight_price_lab.api.service import TripSearchService
from flight_price_lab.models import FlightLeg, FlightOffer, TicketingType
from flight_price_lab.storage.database import (
    FlightObservation,
    SearchObservationRun,
    TripOptionObservation,
    create_database_engine,
)
from flight_price_lab.storage.price_history import PriceHistoryStore


def request(*, adults: int = 2, children: int = 2, currency: str = "GBP"):
    return TripSearchRequest.model_validate(
        {
            "origins": ["LGW"],
            "destinations": ["CAG"],
            "outbound_date": "2026-12-18",
            "return_date": "2026-12-28",
            "adults": adults,
            "children": children,
            "baggage": {"cabin_bags": 1, "checked_bags": 0},
            "self_transfer_policy": "BOTH",
            "connection_profile": "CONSERVATIVE",
            "currency": currency,
        }
    )


def offer(
    flight_number: str,
    origin: str,
    destination: str,
    departure: datetime,
    price: str,
) -> FlightOffer:
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
        observed_at=datetime(2026, 8, 26, tzinfo=UTC),
    )


def option(
    direction: Direction, offers: tuple[FlightOffer, ...], price: str
) -> TripOption:
    legs = [item.legs[0] for item in offers]
    fingerprint = sha256(
        "|".join(item.fingerprint for item in offers).encode()
    ).hexdigest()
    return TripOption(
        id=fingerprint,
        direction=direction,
        route=[legs[0].origin, *(leg.destination for leg in legs)],
        flight_numbers=[leg.flight_number for leg in legs],
        airlines=[leg.airline for leg in legs],
        legs=[
            TripLegSummary(
                origin=leg.origin,
                destination=leg.destination,
                departure_at=leg.departure,
                arrival_at=leg.arrival,
                airline=leg.airline,
                flight_number=leg.flight_number,
                constituent_fingerprint=item.fingerprint,
                constituent_price=item.total_price,
            )
            for item, leg in zip(offers, legs, strict=True)
        ],
        base_price=Decimal(price),
        ancillary_price_low=None,
        ancillary_price_high=None,
        effective_price_low=None,
        effective_price_high=None,
        currency="GBP",
        price_completeness=PriceCompleteness.UNKNOWN,
        is_nonstop=len(offers) == 1,
        is_self_transfer=len(offers) == 2,
        connection_airport=legs[0].destination if len(offers) == 2 else None,
        connection_minutes=180 if len(offers) == 2 else None,
        departure_at=legs[0].departure,
        arrival_at=legs[-1].arrival,
        total_journey_minutes=300 if len(offers) == 2 else 120,
        ticketing_type=(
            TicketingType.SEPARATE_TICKETS
            if len(offers) == 2
            else TicketingType.UNKNOWN
        ),
        baggage_confidence="unknown",
        constituent_fingerprints=[item.fingerprint for item in offers],
    )


def snapshot(
    search_id: str, outbound: TripOption, inbound: TripOption | None = None
) -> SearchSnapshot:
    value = SearchSnapshot(
        search_id=search_id,
        trip_id=search_id,
        search_key="stable-search",
        status=SearchStatus.COMPLETED,
    )
    value.outbound.nonstop_options = [outbound]
    value.outbound.baseline = outbound
    if inbound is not None:
        from flight_price_lab.api.models import DirectionResults

        value.return_ = DirectionResults(baseline=inbound, nonstop_options=[inbound])
    return value


def test_live_capture_persists_run_constituents_and_generated_options() -> None:
    engine = create_database_engine()
    store = PriceHistoryStore(engine)
    first = offer(
        "U2 8309", "LGW", "MXP", datetime(2026, 12, 18, 10, tzinfo=UTC), "258"
    )
    second = offer(
        "W4 6997", "MXP", "CAG", datetime(2026, 12, 18, 15, tzinfo=UTC), "144"
    )
    trip = option(Direction.OUTBOUND, (first, second), "402")

    enriched = store.capture_and_enrich(
        snapshot("live", trip),
        request(),
        (first, second),
        write_observation=True,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )

    assert enriched.outbound.baseline.history.history_status is HistoryStatus.FIRST_SEEN
    with Session(engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(SearchObservationRun)) == 1
        )
        assert session.scalar(select(func.count()).select_from(FlightObservation)) == 2
        assert (
            session.scalar(select(func.count()).select_from(TripOptionObservation)) == 1
        )


def test_cached_replay_writes_no_observation_and_refresh_compares_composite() -> None:
    engine = create_database_engine()
    store = PriceHistoryStore(engine)
    departure = datetime(2026, 12, 18, 10, tzinfo=UTC)
    old_a = offer("U2 8309", "LGW", "MXP", departure, "250")
    old_b = offer("W4 6997", "MXP", "CAG", departure + timedelta(hours=5), "128")
    old_trip = option(Direction.OUTBOUND, (old_a, old_b), "378")
    first_at = datetime(2026, 8, 20, tzinfo=UTC)
    store.capture_and_enrich(
        snapshot("first", old_trip),
        request(),
        (old_a, old_b),
        write_observation=True,
        observed_at=first_at,
    )
    cached = store.capture_and_enrich(
        snapshot("cached", old_trip),
        request(),
        (),
        write_observation=False,
        observed_at=first_at + timedelta(days=2),
    )
    new_a = old_a.model_copy(update={"total_price": Decimal(258)})
    new_b = old_b.model_copy(update={"total_price": Decimal(144)})
    assert new_a.fingerprint == old_a.fingerprint
    current_trip = option(Direction.OUTBOUND, (new_a, new_b), "402")
    refreshed = store.capture_and_enrich(
        snapshot("refresh", current_trip),
        request(),
        (new_a, new_b),
        write_observation=True,
        observed_at=first_at + timedelta(days=3),
    )

    assert cached.outbound.baseline.history.previous_price == Decimal(378)
    history = refreshed.outbound.baseline.history
    assert history.previous_price == Decimal(378)
    assert history.price_change_amount == Decimal(24)
    assert history.price_change_percent == Decimal("6.35")
    assert history.elapsed_seconds == 3 * 24 * 60 * 60
    assert refreshed.outbound.baseline.legs[1].history.price_change_percent == Decimal(
        "12.50"
    )
    with Session(engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(SearchObservationRun)) == 2
        )


def test_cached_history_is_resolved_with_two_batched_queries() -> None:
    engine = create_database_engine()
    store = PriceHistoryStore(engine)
    departure = datetime(2026, 12, 18, 10, tzinfo=UTC)
    offers = tuple(
        offer(
            f"U2 {8300 + index}",
            "LGW",
            "MXP",
            departure + timedelta(hours=index),
            str(200 + index),
        )
        for index in range(6)
    )
    options = [
        option(Direction.OUTBOUND, (item,), str(200 + index))
        for index, item in enumerate(offers)
    ]
    first = snapshot("batch-live", options[0])
    first.outbound.nonstop_options = options
    store.capture_and_enrich(first, request(), offers, write_observation=True)

    statements: list[str] = []

    def count_history_selects(_conn, _cursor, statement, _parameters, _context, _many):
        lowered = statement.lower()
        if statement.lstrip().lower().startswith("select") and (
            "trip_option_observation" in lowered or "flight_observation" in lowered
        ):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", count_history_selects)
    try:
        replay = snapshot("batch-cached", options[0])
        replay.outbound.nonstop_options = options
        store.capture_and_enrich(replay, request(), (), write_observation=False)
    finally:
        event.remove(engine, "before_cursor_execute", count_history_selects)

    assert len(statements) == 2
    with Session(engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(SearchObservationRun)) == 1
        )


def test_direction_and_passenger_currency_contexts_are_independent() -> None:
    engine = create_database_engine()
    store = PriceHistoryStore(engine)
    departure = datetime(2026, 12, 18, 10, tzinfo=UTC)
    selected = offer("FR 2687", "STN", "CAG", departure, "741")
    outbound = option(Direction.OUTBOUND, (selected,), "741")
    store.capture_and_enrich(
        snapshot("first", outbound), request(), (selected,), write_observation=True
    )
    inbound_same_id = outbound.model_copy(update={"direction": Direction.RETURN})
    different_party = store.capture_and_enrich(
        snapshot("party", outbound),
        request(adults=1, children=0),
        (),
        write_observation=False,
    )
    different_composition = store.capture_and_enrich(
        snapshot("composition", outbound),
        request(adults=4, children=0),
        (),
        write_observation=False,
    )
    different_currency = store.capture_and_enrich(
        snapshot("currency", outbound),
        request(currency="EUR"),
        (),
        write_observation=False,
    )
    independent_return = store.capture_and_enrich(
        snapshot("return", inbound_same_id), request(), (), write_observation=False
    )

    assert (
        different_party.outbound.baseline.history.history_status
        is HistoryStatus.FIRST_SEEN
    )
    assert (
        different_currency.outbound.baseline.history.history_status
        is HistoryStatus.FIRST_SEEN
    )
    assert (
        different_composition.outbound.baseline.history.history_status
        is HistoryStatus.FIRST_SEEN
    )
    assert (
        independent_return.outbound.baseline.history.history_status
        is HistoryStatus.FIRST_SEEN
    )


class ObservationProvider:
    def __init__(self, *, live: bool) -> None:
        self.live = live

    async def search_direct(self, **arguments: object) -> ProviderSearchResult:
        origins = tuple(arguments["origins"])
        destinations = tuple(arguments["destinations"])
        travel_date = arguments["travel_date"]
        assert isinstance(travel_date, date)
        selected = offer(
            "FR 2687",
            origins[0],
            destinations[0],
            datetime.combine(travel_date, datetime.min.time(), UTC)
            + timedelta(hours=8),
            "741",
        )
        return ProviderSearchResult(
            offers=[selected],
            backend_cache_hits=int(not self.live),
            backend_cache_misses=int(self.live),
            provider_calls=int(self.live),
            provider_calls_avoided=int(not self.live),
        )

    async def calendar(self, **arguments: object) -> list[object]:
        return []


def test_search_orchestration_writes_live_and_refresh_but_not_cached() -> None:
    engine = create_database_engine()
    store = PriceHistoryStore(engine)
    search_request = request().model_copy(
        update={"self_transfer_policy": SelfTransferPolicy.NONE}
    )

    async def run(provider: ObservationProvider, refresh: bool = False) -> None:
        service = TripSearchService(
            provider,
            InMemorySearchRegistry(),
            price_history_store=store,
        )
        current = search_request.model_copy(update={"refresh_prices": refresh})
        search_id = await service.start(current)
        for _ in range(200):
            result = await service.registry.get(search_id)
            if result and result.status in {
                SearchStatus.COMPLETED,
                SearchStatus.PARTIAL_FAILURE,
            }:
                return
            await asyncio.sleep(0.001)
        raise AssertionError("search did not complete")

    asyncio.run(run(ObservationProvider(live=True)))
    asyncio.run(run(ObservationProvider(live=False)))
    with Session(engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(SearchObservationRun)) == 1
        )
    asyncio.run(run(ObservationProvider(live=True), refresh=True))
    with Session(engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(SearchObservationRun)) == 2
        )
