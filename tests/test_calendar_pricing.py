import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from flight_price_lab.api.app import create_app
from flight_price_lab.api.calendar import (
    DirectionalCalendarService,
    classify_calendar_prices,
)
from flight_price_lab.api.models import CalendarPrice
from flight_price_lab.models.flight import FlightLeg, FlightOffer
from flight_price_lab.providers.searchapi import SearchAPIError
from flight_price_lab.storage.database import CalendarPriceStore


class CalendarProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search_direct(self, **arguments: object) -> list[FlightOffer]:
        self.calls.append(arguments)
        travel_date = arguments["travel_date"]
        assert isinstance(travel_date, date)
        price = Decimal(100 + travel_date.day)
        departure = datetime.combine(travel_date, datetime.min.time(), tzinfo=UTC)
        origin = str(next(iter(arguments["origins"])))  # type: ignore[call-overload]
        destination = str(
            next(iter(arguments["destinations"]))  # type: ignore[call-overload]
        )
        return [
            self._offer(origin, destination, departure, price + 50, "AZ 1"),
            self._offer(origin, destination, departure, price, "FR 1"),
        ]

    @staticmethod
    def _offer(
        origin: str,
        destination: str,
        departure: datetime,
        price: Decimal,
        number: str,
    ) -> FlightOffer:
        return FlightOffer(
            legs=(
                FlightLeg(
                    origin=origin,
                    destination=destination,
                    departure=departure,
                    arrival=departure + timedelta(hours=2),
                    airline=number.split()[0],
                    flight_number=number,
                ),
            ),
            total_price=price,
            currency="GBP",
            passenger_count=4,
            provider="mock",
            provider_offer_id=number,
            observed_at=datetime.now(UTC),
        )

    async def calendar(self, **arguments: object) -> list[CalendarPrice]:
        raise AssertionError(arguments)


class PartiallyFailingCalendarProvider(CalendarProvider):
    def __init__(self, failed_date: date) -> None:
        super().__init__()
        self.failed_date = failed_date

    async def search_direct(self, **arguments: object) -> list[FlightOffer]:
        if arguments["travel_date"] == self.failed_date:
            self.calls.append(arguments)
            raise SearchAPIError("controlled provider failure")
        return await super().search_direct(**arguments)


class ConcurrentCalendarProvider(CalendarProvider):
    def __init__(self, failed_date: date | None = None) -> None:
        super().__init__()
        self.failed_date = failed_date
        self.active = 0
        self.peak = 0

    async def search_direct(self, **arguments: object) -> list[FlightOffer]:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.02)
            if arguments["travel_date"] == self.failed_date:
                self.calls.append(arguments)
                raise SearchAPIError("controlled provider failure")
            return await super().search_direct(**arguments)
        finally:
            self.active -= 1


class CountingCalendarStore(CalendarPriceStore):
    def __init__(self) -> None:
        super().__init__()
        self.batch_writes = 0

    def put_many(self, entries: list[dict[str, object]]):  # type: ignore[no-untyped-def]
        self.batch_writes += 1
        return super().put_many(entries)


def test_calendar_uses_cheapest_direct_and_24_hour_observation_cache() -> None:
    provider = CalendarProvider()
    store = CalendarPriceStore()
    service = DirectionalCalendarService(provider, store)
    dates = [date(2026, 12, 15) + timedelta(days=index) for index in range(7)]

    first = asyncio.run(
        service.prices(
            origins=["LGW", "STN"],
            destinations=["CAG"],
            dates=dates,
            adults=2,
            children=2,
            currency="GBP",
            direction="OUTBOUND",
        )
    )
    second = asyncio.run(
        service.prices(
            origins=["STN", "LGW"],
            destinations=["CAG"],
            dates=dates,
            adults=2,
            children=2,
            currency="GBP",
            direction="OUTBOUND",
        )
    )

    assert first.prices[0].price == Decimal(115)
    assert first.calendar_provider_calls_this_invocation == 7
    assert second.calendar_provider_calls_this_invocation == 0
    assert second.calendar_calls_avoided == 7
    assert len(provider.calls) == 7


def test_seven_missing_dates_run_four_at_a_time_and_persist_once() -> None:
    provider = ConcurrentCalendarProvider()
    store = CountingCalendarStore()
    dates = [date(2027, 1, 1) + timedelta(days=index) for index in range(7)]

    result = asyncio.run(
        DirectionalCalendarService(provider, store, max_concurrency=4).prices(
            origins=["LGW"],
            destinations=["CAG"],
            dates=dates,
            adults=2,
            children=2,
            currency="GBP",
            direction="OUTBOUND",
        )
    )

    assert len(provider.calls) == 7
    assert provider.peak == 4
    assert result.calendar_calls_concurrent_peak == 4
    assert store.batch_writes == 1


def test_four_cached_dates_are_excluded_before_three_missing_are_scheduled() -> None:
    provider = ConcurrentCalendarProvider()
    store = CountingCalendarStore()
    dates = [date(2027, 2, 1) + timedelta(days=index) for index in range(7)]
    for travel_date in dates[:4]:
        store.put(
            origins=("LGW",),
            destinations=("CAG",),
            travel_date=travel_date,
            direction="OUTBOUND",
            lowest_direct_price=Decimal(200),
            currency="GBP",
            adults=2,
            children=2,
            source_search_key=f"cached-{travel_date}",
        )

    result = asyncio.run(
        DirectionalCalendarService(provider, store, max_concurrency=4).prices(
            origins=["LGW"],
            destinations=["CAG"],
            dates=dates,
            adults=2,
            children=2,
            currency="GBP",
            direction="OUTBOUND",
        )
    )

    assert len(provider.calls) == 3
    assert provider.peak == 3
    assert result.calendar_calls_avoided == 4
    assert result.calendar_calls_concurrent_peak == 3
    assert store.batch_writes == 1


def test_one_failed_date_isolated_and_response_remains_complete() -> None:
    failed_date = date(2026, 12, 18)
    provider = ConcurrentCalendarProvider(failed_date)
    client = TestClient(create_app(provider))

    response = client.get(
        "/api/calendar",
        params=[
            ("origins", "LGW"),
            ("destinations", "CAG"),
            ("date_from", "2026-12-15"),
            ("date_to", "2026-12-21"),
            ("adults", "2"),
            ("children", "2"),
            ("currency", "GBP"),
            ("direction", "OUTBOUND"),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["dates"]) == 7
    assert payload["failures"] == 1
    failed = next(item for item in payload["dates"] if item["date"] == "2026-12-18")
    assert failed["state"] == "ERROR"
    assert failed["price"] is None
    assert failed["classification"] is None
    assert sum(item["state"] == "LOADED" for item in payload["dates"]) == 6
    assert payload["calendar_calls_concurrent_peak"] == 4

    reopened = client.get(response.request.url)
    assert reopened.status_code == 200
    assert reopened.json()["calendar_provider_calls_this_invocation"] == 1
    assert reopened.json()["calendar_calls_avoided"] == 6
    assert len(provider.calls) == 8


def test_stale_observation_survives_live_provider_failure() -> None:
    travel_date = date(2026, 12, 18)
    store = CalendarPriceStore(ttl=timedelta(hours=1))
    store.put(
        origins=("LGW",),
        destinations=("CAG",),
        travel_date=travel_date,
        direction="OUTBOUND",
        lowest_direct_price=Decimal(849),
        currency="GBP",
        adults=2,
        children=2,
        source_search_key="stale-source",
        observed_at=datetime.now(UTC) - timedelta(days=2),
    )
    provider = PartiallyFailingCalendarProvider(travel_date)

    result = asyncio.run(
        DirectionalCalendarService(provider, store).prices(
            origins=["LGW"],
            destinations=["CAG"],
            dates=[travel_date],
            adults=2,
            children=2,
            currency="GBP",
            direction="OUTBOUND",
        )
    )

    assert result.failures == 1
    assert result.dates[0].state == "STALE_AVAILABLE"
    assert result.dates[0].price == Decimal(849)


def test_lazy_range_fetches_only_new_dates_and_directions_are_independent() -> None:
    provider = CalendarProvider()
    service = DirectionalCalendarService(provider, CalendarPriceStore())
    common = {
        "adults": 2,
        "children": 2,
        "currency": "GBP",
    }
    asyncio.run(
        service.prices(
            origins=["LGW"],
            destinations=["CAG"],
            dates=[date(2026, 12, 18)],
            direction="OUTBOUND",
            **common,
        )
    )
    outbound = asyncio.run(
        service.prices(
            origins=["LGW"],
            destinations=["CAG"],
            dates=[date(2026, 12, 18), date(2026, 12, 19)],
            direction="OUTBOUND",
            **common,
        )
    )
    inbound = asyncio.run(
        service.prices(
            origins=["CAG"],
            destinations=["LGW"],
            dates=[date(2026, 12, 18)],
            direction="RETURN",
            **common,
        )
    )

    assert outbound.calendar_provider_calls_this_invocation == 1
    assert outbound.calendar_calls_avoided == 1
    assert inbound.calendar_provider_calls_this_invocation == 1


def test_calendar_classification_is_relative() -> None:
    values = [100, 110, 120, 130, 140, 150, 160]
    classified = classify_calendar_prices(
        [
            CalendarPrice(date=date(2026, 12, index + 1), price=value, currency="GBP")
            for index, value in enumerate(values)
        ]
    )
    assert [item.classification for item in classified] == [
        "LOW",
        "LOW",
        "TYPICAL",
        "TYPICAL",
        "TYPICAL",
        "HIGH",
        "HIGH",
    ]


def test_normal_search_direct_observation_is_reused_by_calendar() -> None:
    provider = CalendarProvider()
    client = TestClient(create_app(provider))
    request = {
        "origins": ["LGW"],
        "destinations": ["CAG"],
        "outbound_date": "2026-12-18",
        "return_date": None,
        "adults": 2,
        "children": 2,
        "baggage": {"cabin_bags": 1, "checked_bags": 0},
        "self_transfer_policy": "NONE",
        "currency": "GBP",
    }
    searched = client.post("/api/search/stream", json=request)
    assert searched.status_code == 200
    calls_after_search = len(provider.calls)
    with CalendarPriceStore().engine.begin() as connection:
        connection.execute(text("DELETE FROM calendar_price_observation"))

    calendar = client.get(
        "/api/calendar",
        params=[
            ("origins", "LGW"),
            ("destinations", "CAG"),
            ("date_from", "2026-12-18"),
            ("date_to", "2026-12-18"),
            ("adults", "2"),
            ("children", "2"),
            ("currency", "GBP"),
            ("direction", "OUTBOUND"),
        ],
    )
    assert calendar.status_code == 200
    assert calendar.json()["calendar_provider_calls_this_invocation"] == 0
    assert len(provider.calls) == calls_after_search
