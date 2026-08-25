import asyncio
import json
import logging
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from flight_price_lab.api.models import TripSearchRequest
from flight_price_lab.api.provider import SearchAPIProviderGateway
from flight_price_lab.api.service import trip_search_key
from flight_price_lab.storage.database import (
    SearchResponseCache,
    canonical_search_key,
)


def postgres_test_url() -> str:
    return os.environ["TEST_DATABASE_URL"]


class FakeSearchClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0

    def search_one_way(self, **arguments: object) -> dict[str, object]:
        del arguments
        self.calls += 1
        return self.payload


def fixture_payload() -> dict[str, object]:
    path = Path("tests/fixtures/searchapi/lgw_cag_fr2687.json")
    return json.loads(path.read_text(encoding="utf-8"))


def arguments() -> dict[str, object]:
    return {
        "origins": ("LCY", "LGW", "STN", "LTN", "LHR"),
        "destinations": ("CAG",),
        "travel_date": date(2026, 12, 18),
        "adults": 2,
        "children": 2,
        "currency": "GBP",
        "cabin_bags": 0,
        "checked_bags": 0,
        "bypass_cache": False,
    }


def test_identical_search_uses_persistent_cache_and_preserves_raw_json(
    tmp_path: Path,
) -> None:
    client = FakeSearchClient(fixture_payload())
    cache = SearchResponseCache(
        postgres_test_url(),
        raw_root=tmp_path / "raw",
    )
    gateway = SearchAPIProviderGateway(client, cache)  # type: ignore[arg-type]

    first = asyncio.run(gateway.search_direct(**arguments()))
    second = asyncio.run(gateway.search_direct(**arguments()))

    assert client.calls == 1
    assert first.provider_calls == 1
    assert first.backend_cache_hits == 0
    assert first.backend_cache_misses == 1
    assert second.provider_calls == 0
    assert second.backend_cache_hits == 1
    assert second.provider_calls_avoided == 1
    assert Path(first.offers[0].raw_reference or "").is_file()


def test_cache_key_sorts_airports_and_excludes_ui_preferences() -> None:
    base = {
        "origins": ["STN", "LGW"],
        "destinations": ["CAG"],
        "date": "2026-12-18",
        "adults": 2,
        "children": 2,
        "currency": "GBP",
        "flight_type": "one_way",
        "stops": "nonstop",
    }
    reordered = {**base, "origins": ["LGW", "STN"]}
    assert canonical_search_key(base) == canonical_search_key(reordered)


def test_refresh_bypasses_cache_without_deleting_previous_raw_capture(
    tmp_path: Path,
) -> None:
    client = FakeSearchClient(fixture_payload())
    cache = SearchResponseCache(
        postgres_test_url(),
        raw_root=tmp_path / "raw",
    )
    gateway = SearchAPIProviderGateway(client, cache)  # type: ignore[arg-type]
    asyncio.run(gateway.search_direct(**arguments()))
    refresh = {**arguments(), "bypass_cache": True}
    asyncio.run(gateway.search_direct(**refresh))

    assert client.calls == 2
    assert len(list((tmp_path / "raw").rglob("*.json"))) == 2


def test_safe_existing_capture_can_seed_cache(tmp_path: Path) -> None:
    capture = tmp_path / "capture.json"
    capture.write_text(json.dumps(fixture_payload()), encoding="utf-8")
    cache = SearchResponseCache(
        postgres_test_url(),
        raw_root=tmp_path / "raw",
    )
    assert cache.seed_capture(capture) is True
    assert len(cache.entries()) == 1


def test_default_cache_ttl_is_sixty_minutes(tmp_path: Path) -> None:
    cache = SearchResponseCache(
        postgres_test_url(),
        raw_root=tmp_path / "raw",
    )
    parameters = {
        "origins": ["LGW"],
        "destinations": ["CAG"],
        "date": "2026-12-18",
        "adults": 2,
        "children": 2,
        "currency": "GBP",
        "flight_type": "one_way",
        "stops": "nonstop",
    }
    created = datetime(2026, 8, 24, 12, tzinfo=UTC)
    cache.put(parameters, fixture_payload(), result_count=1, now=created)

    assert cache.get(parameters, now=created + timedelta(minutes=59)) is not None
    assert cache.get(parameters, now=created + timedelta(minutes=60)) is None


def trip_request(**updates: object) -> TripSearchRequest:
    values: dict[str, object] = {
        "origins": ["LGW", "STN"],
        "destinations": ["CAG", "OLB"],
        "outbound_date": "2026-12-18",
        "adults": 2,
        "children": 2,
        "self_transfer_policy": "BOTH",
        "return_date": "2026-12-28",
    }
    values.update(updates)
    return TripSearchRequest.model_validate(values)


def test_trip_search_key_is_stable_for_airport_order_and_ui_filters() -> None:
    original = trip_request()
    reordered = trip_request(
        origins=["STN", "LGW"],
        destinations=["OLB", "CAG"],
        max_extra_journey_minutes=120,
        outbound_time_window={
            "earliest_departure_time": "08:00",
            "latest_arrival_time": "22:00",
            "max_connection_minutes": 360,
        },
    )
    assert trip_search_key(original) == trip_search_key(reordered)


def test_trip_search_key_changes_for_provider_affecting_inputs() -> None:
    original = trip_request()
    assert trip_search_key(original) != trip_search_key(trip_request(adults=1))
    assert trip_search_key(original) != trip_search_key(
        trip_request(outbound_date="2026-12-19")
    )


def test_refresh_flag_does_not_change_search_identity() -> None:
    assert trip_search_key(trip_request()) == trip_search_key(
        trip_request(refresh_prices=True)
    )


def test_a_b_a_calls_provider_once_per_distinct_key_then_hits_a_cache(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="flight_price_lab.search")
    client = FakeSearchClient(fixture_payload())
    cache = SearchResponseCache(
        postgres_test_url(),
        raw_root=tmp_path / "raw",
    )
    gateway = SearchAPIProviderGateway(client, cache)  # type: ignore[arg-type]
    search_a = arguments()
    search_b = {**arguments(), "children": 1}

    first_a = asyncio.run(gateway.search_direct(**search_a))
    first_b = asyncio.run(gateway.search_direct(**search_b))
    final_a = asyncio.run(gateway.search_direct(**search_a))

    assert [first_a.provider_calls, first_b.provider_calls, final_a.provider_calls] == [
        1,
        1,
        0,
    ]
    assert final_a.backend_cache_hits == 1
    assert final_a.provider_calls_avoided == 1
    events = [json.loads(record.message)["event"] for record in caplog.records]
    assert events.count("PROVIDER_CALL_STARTED") == 2
    assert events[-3:] == [
        "BACKEND_CACHE_HIT",
        "PROVIDER_CALL_SKIPPED_CACHE",
        "RESULT_RESTORED_FROM_CACHE",
    ]


def test_expired_entry_causes_provider_call(tmp_path: Path) -> None:
    client = FakeSearchClient(fixture_payload())
    cache = SearchResponseCache(
        postgres_test_url(),
        raw_root=tmp_path / "raw",
    )
    args = arguments()
    parameters = {
        "origins": list(args["origins"]),
        "destinations": list(args["destinations"]),
        "date": args["travel_date"].isoformat(),  # type: ignore[union-attr]
        "adults": 2,
        "children": 2,
        "currency": "GBP",
        "flight_type": "one_way",
        "stops": "nonstop",
    }
    cache.put(
        parameters,
        fixture_payload(),
        result_count=1,
        now=datetime.now(UTC) - timedelta(minutes=61),
    )
    gateway = SearchAPIProviderGateway(client, cache)  # type: ignore[arg-type]

    result = asyncio.run(gateway.search_direct(**args))

    assert result.provider_calls == 1
    assert result.backend_cache_misses == 1
