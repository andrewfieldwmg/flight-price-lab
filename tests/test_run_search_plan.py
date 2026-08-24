import json
from datetime import date
from pathlib import Path
from typing import Any

from flight_price_lab.providers.searchapi import SearchAPIError
from flight_price_lab.routing.planning import PlannedProviderSearch
from scripts.run_search_plan import find_existing_captures, run_plan


def search(*, arrival: str = "MXP") -> PlannedProviderSearch:
    return PlannedProviderSearch(
        departure_airports=("LGW", "STN"),
        arrival_airports=(arrival,),
        travel_date=date(2026, 12, 18),
        adults=2,
        children=2,
        currency="GBP",
    )


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def search_one_way(self, **parameters: Any) -> dict[str, Any]:
        self.calls += 1
        return {
            "search_parameters": {
                "engine": "google_flights",
                "flight_type": "one_way",
                "departure_id": parameters["departure_id"],
                "arrival_id": parameters["arrival_id"],
                "outbound_date": parameters["outbound_date"].isoformat(),
                "adults": str(parameters["adults"]),
                "children": str(parameters["children"]),
                "currency": parameters["currency"],
                "travel_class": "economy",
                "stops": parameters["stops"],
                "sort_by": "price",
                "show_cheapest_flights": "true",
            },
            "other_flights": [],
        }


class FailingClient(FakeClient):
    def search_one_way(self, **parameters: Any) -> dict[str, Any]:
        if self.calls == 0:
            self.calls += 1
            raise SearchAPIError("SearchAPI request failed: ReadTimeout")
        return super().search_one_way(**parameters)


def test_runner_never_exceeds_manifest_and_saves_response(tmp_path: Path) -> None:
    client = FakeClient()

    result = run_plan([search()], client, raw_root=tmp_path)

    assert result.calls_made == client.calls == 1
    assert result.skipped_existing == 0
    assert len(result.saved_paths) == 1
    assert json.loads(result.saved_paths[0].read_text())["other_flights"] == []


def test_runner_resumes_by_skipping_equivalent_capture(tmp_path: Path) -> None:
    run_plan([search()], FakeClient(), raw_root=tmp_path)
    client = FakeClient()

    result = run_plan([search()], client, raw_root=tmp_path)

    assert result.calls_made == client.calls == 0
    assert result.skipped_existing == 1
    assert result.saved_paths == ()
    assert len(find_existing_captures(tmp_path)) == 1


def test_runner_records_failure_continues_and_does_not_retry(tmp_path: Path) -> None:
    first_result = run_plan(
        [search(), search(arrival="FCO")], FailingClient(), raw_root=tmp_path
    )
    resumed_client = FakeClient()
    resumed_result = run_plan(
        [search(), search(arrival="FCO")], resumed_client, raw_root=tmp_path
    )

    assert first_result.calls_made == 2
    assert len(first_result.failures) == 1
    assert len(first_result.saved_paths) == 1
    assert resumed_result.previously_failed == 1
    assert resumed_result.skipped_existing == 1
    assert resumed_client.calls == 0
