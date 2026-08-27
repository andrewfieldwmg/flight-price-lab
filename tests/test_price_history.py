import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from flight_price_lab.api.models import (
    Direction,
    HistoryStatus,
    PriceCompleteness,
    PriceHistoryComparison,
    SearchSnapshot,
    SearchStatus,
    SelfTransferPolicy,
    TrendStatus,
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
    SearchSessionEntry,
    SearchSessionStore,
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


def test_same_day_observations_never_replace_previous_day_baseline() -> None:
    engine = create_database_engine()
    store = PriceHistoryStore(engine)
    departure = datetime(2026, 12, 18, 10, tzinfo=UTC)

    def priced(value: str) -> tuple[FlightOffer, TripOption]:
        selected = offer("FR 2687", "STN", "CAG", departure, value)
        return selected, option(Direction.OUTBOUND, (selected,), value)

    old_offer, old_option = priced("623")
    morning_offer, morning_option = priced("549")
    noon_offer, noon_option = priced("549")
    store.capture_and_enrich(
        snapshot("aug-26", old_option),
        request(),
        (old_offer,),
        write_observation=True,
        observed_at=datetime(2026, 8, 26, 14, 41, tzinfo=UTC),
    )
    store.capture_and_enrich(
        snapshot("aug-27-am", morning_option),
        request(),
        (morning_offer,),
        write_observation=True,
        observed_at=datetime(2026, 8, 27, 9, 50, tzinfo=UTC),
    )
    current = store.capture_and_enrich(
        snapshot("aug-27-noon", noon_option),
        request(),
        (noon_offer,),
        write_observation=True,
        observed_at=datetime(2026, 8, 27, 12, 30, tzinfo=UTC),
    )
    replay = store.capture_and_enrich(
        snapshot("fresh-session", noon_option),
        request(),
        (),
        write_observation=False,
        observed_at=datetime(2026, 8, 27, 12, 50, tzinfo=UTC),
    )

    for result in (current, replay):
        history = result.outbound.baseline.history
        assert result.outbound.baseline.base_price == Decimal(549)
        assert history.previous_price == Decimal(623)
        assert history.price_change_amount == Decimal(-74)
        assert history.price_change_percent == Decimal("-11.88")
        assert history.day_difference == 1
        assert result.outbound.baseline.legs[0].history.previous_price == Decimal(623)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SearchObservationRun)) == 3


def test_legacy_cached_zero_percent_history_is_rehydrated_from_prior_day() -> None:
    engine = create_database_engine()
    history_store = PriceHistoryStore(engine)
    session_store = SearchSessionStore(engine=engine)
    departure = datetime(2026, 12, 18, 10, tzinfo=UTC)
    earliest_offer = offer("FR 2687", "STN", "CAG", departure, "700")
    earliest_option = option(Direction.OUTBOUND, (earliest_offer,), "700")
    history_store.capture_and_enrich(
        snapshot("aug-25", earliest_option), request(), (earliest_offer,),
        write_observation=True, observed_at=datetime(2026, 8, 25, 14, 41, tzinfo=UTC),
    )
    old_offer = offer("FR 2687", "STN", "CAG", departure, "623")
    old_option = option(Direction.OUTBOUND, (old_offer,), "623")
    history_store.capture_and_enrich(
        snapshot("aug-26", old_option), request(), (old_offer,),
        write_observation=True, observed_at=datetime(2026, 8, 26, 14, 41, tzinfo=UTC),
    )
    current_offer = old_offer.model_copy(update={"total_price": Decimal(549)})
    current_option = option(Direction.OUTBOUND, (current_offer,), "549")
    history_store.capture_and_enrich(
        snapshot("aug-27-am", current_option), request(), (current_offer,),
        write_observation=True, observed_at=datetime(2026, 8, 27, 9, 50, tzinfo=UTC),
    )
    stale_history = PriceHistoryComparison(
        history_status=HistoryStatus.PREVIOUS_FOUND,
        previous_price=Decimal(549),
        price_change_amount=Decimal(0),
        price_change_percent=Decimal(0),
        previous_observed_at=datetime(2026, 8, 27, 9, 50, tzinfo=UTC),
        elapsed_seconds=2 * 60 * 60 + 40 * 60,
        day_difference=0,
        previous_observation_run_id="same-day-run",
    )
    stale_option = current_option.model_copy(
        update={
            "history": stale_history,
            "legs": [
                current_option.legs[0].model_copy(update={"history": stale_history})
            ],
        }
    )
    stale_snapshot = snapshot("legacy-cache", stale_option)
    stale_snapshot.status = SearchStatus.COMPLETED
    stale_snapshot.diagnostics.original_search_completed_at = datetime(
        2026, 8, 27, 12, 30, tzinfo=UTC
    )
    session_store.create(stale_snapshot, request())
    # Emulate an existing cache row written before history was stripped on writes.
    with Session(engine) as session:
        entry = session.get(SearchSessionEntry, "legacy-cache")
        assert entry is not None
        entry.snapshot_json = stale_snapshot.model_dump_json(by_alias=True)
        session.commit()

    cached = session_store.get("legacy-cache", now=datetime(2026, 8, 27, 12, 50, tzinfo=UTC))
    assert cached.outbound.baseline.history.price_change_percent == Decimal(0)
    rehydrated = history_store.capture_and_enrich(
        cached,
        session_store.get_request("legacy-cache"),
        (),
        write_observation=False,
        observed_at=datetime(2026, 8, 27, 12, 30, tzinfo=UTC),
    )

    history = rehydrated.outbound.baseline.history
    assert history.previous_price == Decimal(623)
    assert history.price_change_amount == Decimal(-74)
    assert history.price_change_percent == Decimal("-11.88")
    assert history.day_difference == 1
    assert history.trend_status is TrendStatus.FALLING
    assert history.observed_day_count == 3
    assert rehydrated.diagnostics.provider_calls_this_invocation == 0
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SearchObservationRun)) == 3


def test_future_backend_cache_payload_omits_derived_history() -> None:
    engine = create_database_engine()
    session_store = SearchSessionStore(engine=engine)
    selected = offer(
        "FR 2687", "STN", "CAG", datetime(2026, 12, 18, 10, tzinfo=UTC), "549"
    )
    selected_option = option(Direction.OUTBOUND, (selected,), "549")
    stale = PriceHistoryComparison(
        history_status=HistoryStatus.PREVIOUS_FOUND,
        previous_price=Decimal(549),
        price_change_amount=Decimal(0),
        price_change_percent=Decimal(0),
    )
    selected_option = selected_option.model_copy(update={"history": stale})
    cached_snapshot = snapshot("stripped-cache", selected_option)
    session_store.create(cached_snapshot, request())

    with Session(engine) as session:
        payload = session.get(SearchSessionEntry, "stripped-cache").snapshot_json
    assert '"history"' not in payload
    assert session_store.get("stripped-cache").outbound.baseline.history is None


@pytest.mark.parametrize(
    ("prices", "expected_status", "expected_percent"),
    [
        (["500", "550", "623", "623"], TrendStatus.RISING, Decimal("24.60")),
        (["650", "600", "550", "550"], TrendStatus.FALLING, Decimal("-15.38")),
        (["500", "501", "499", "500"], TrendStatus.FLAT, Decimal("0.00")),
        (["500", "550"], TrendStatus.INSUFFICIENT_HISTORY, None),
    ],
)
def test_multi_day_trend_classification(
    prices: list[str],
    expected_status: TrendStatus,
    expected_percent: Decimal | None,
) -> None:
    engine = create_database_engine()
    store = PriceHistoryStore(engine)
    departure = datetime(2026, 12, 18, 10, tzinfo=UTC)
    result = None
    for index, price in enumerate(prices):
        selected = offer("FR 2687", "STN", "CAG", departure, price)
        selected_option = option(Direction.OUTBOUND, (selected,), price)
        result = store.capture_and_enrich(
            snapshot(f"day-{index}", selected_option), request(), (selected,),
            write_observation=True,
            observed_at=datetime(2026, 8, 24 + index, 12, tzinfo=UTC),
        )

    assert result is not None
    history = result.outbound.baseline.history
    assert history.trend_status is expected_status
    assert history.observed_day_count == len(prices)
    assert history.trend_change_percent == expected_percent
    if len(prices) >= 3:
        assert history.trend_current_price == Decimal(prices[-1])
        assert history.trend_span_days == len(prices) - 1
    if prices[-1] == prices[-2]:
        assert history.price_change_percent == Decimal("0.00")
    if len(prices) == 4:
        assert result.outbound.baseline.legs[0].history.trend_status is expected_status


def test_trend_uses_latest_observation_once_per_london_day() -> None:
    engine = create_database_engine()
    store = PriceHistoryStore(engine)
    departure = datetime(2026, 12, 18, 10, tzinfo=UTC)
    observations = [
        (datetime(2026, 8, 24, 9, tzinfo=UTC), "500"),
        (datetime(2026, 8, 24, 15, tzinfo=UTC), "550"),
        (datetime(2026, 8, 25, 12, tzinfo=UTC), "600"),
        (datetime(2026, 8, 26, 12, tzinfo=UTC), "650"),
    ]
    result = None
    for observed_at, price in observations:
        selected = offer("FR 2687", "STN", "CAG", departure, price)
        result = store.capture_and_enrich(
            snapshot(observed_at.isoformat(), option(Direction.OUTBOUND, (selected,), price)),
            request(), (selected,), write_observation=True, observed_at=observed_at,
        )

    series = result.outbound.baseline.history.daily_series
    assert [(point.date.isoformat(), point.price) for point in series] == [
        ("2026-08-24", Decimal(550)),
        ("2026-08-25", Decimal(600)),
        ("2026-08-26", Decimal(650)),
    ]


def test_irregular_trend_dates_drive_actual_calendar_day_slope() -> None:
    engine = create_database_engine()
    store = PriceHistoryStore(engine)
    departure = datetime(2026, 12, 18, 10, tzinfo=UTC)
    result = None
    for day, price in [(1, "500"), (3, "550"), (7, "600")]:
        selected = offer("FR 2687", "STN", "CAG", departure, price)
        result = store.capture_and_enrich(
            snapshot(f"aug-{day}", option(Direction.OUTBOUND, (selected,), price)),
            request(), (selected,), write_observation=True,
            observed_at=datetime(2026, 8, day, 12, tzinfo=UTC),
        )

    history = result.outbound.baseline.history
    assert history.trend_span_days == 6
    assert history.price_slope_per_day == Decimal("3.2143")


def test_prior_day_uses_london_date_and_skips_missing_dates() -> None:
    engine = create_database_engine()
    store = PriceHistoryStore(engine)
    departure = datetime(2026, 12, 18, 10, tzinfo=UTC)
    old_offer = offer("FR 2687", "STN", "CAG", departure, "600")
    old_option = option(Direction.OUTBOUND, (old_offer,), "600")
    store.capture_and_enrich(
        snapshot("aug-24", old_option), request(), (old_offer,),
        write_observation=True, observed_at=datetime(2026, 8, 24, 23, 30, tzinfo=UTC),
    )
    current_offer = old_offer.model_copy(update={"total_price": Decimal(549)})
    current_option = option(Direction.OUTBOUND, (current_offer,), "549")
    result = store.capture_and_enrich(
        snapshot("aug-27", current_option), request(), (current_offer,),
        write_observation=True, observed_at=datetime(2026, 8, 27, 0, 30, tzinfo=UTC),
    )

    history = result.outbound.baseline.history
    assert history.previous_price == Decimal(600)
    # Both UTC instants are after midnight on 25 and 27 August in London.
    assert history.day_difference == 2


def test_same_london_day_only_is_first_seen_across_utc_date_boundary() -> None:
    engine = create_database_engine()
    store = PriceHistoryStore(engine)
    departure = datetime(2026, 12, 18, 10, tzinfo=UTC)
    old_offer = offer("FR 2687", "STN", "CAG", departure, "600")
    old_option = option(Direction.OUTBOUND, (old_offer,), "600")
    store.capture_and_enrich(
        snapshot("before-midnight-utc", old_option), request(), (old_offer,),
        write_observation=True, observed_at=datetime(2026, 8, 26, 23, 30, tzinfo=UTC),
    )
    current_offer = old_offer.model_copy(update={"total_price": Decimal(549)})
    current_option = option(Direction.OUTBOUND, (current_offer,), "549")
    result = store.capture_and_enrich(
        snapshot("after-midnight-utc", current_option), request(), (current_offer,),
        write_observation=True, observed_at=datetime(2026, 8, 27, 0, 30, tzinfo=UTC),
    )

    assert result.outbound.baseline.history.history_status is HistoryStatus.FIRST_SEEN
    assert result.outbound.baseline.legs[0].history.history_status is HistoryStatus.FIRST_SEEN


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
