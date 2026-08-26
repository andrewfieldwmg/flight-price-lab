import copy
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from flight_price_lab.airports import airport_timezone
from flight_price_lab.models import JourneyStructure, TicketingType
from flight_price_lab.providers.searchapi_mapper import (
    RejectionCode,
    SearchAPIMapperError,
    iter_candidate_groups,
    map_flight_group,
    normalize_searchapi_response,
)

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "searchapi" / "lgw_mxp_2026-12-18_2a2c.json"
)
RAW_REFERENCE = "tests/fixtures/searchapi/lgw_mxp_2026-12-18_2a2c.json"


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_real_fixture_has_expected_candidate_groups(payload: dict[str, Any]) -> None:
    candidates = list(iter_candidate_groups(payload))

    assert len(candidates) == 11
    assert {bucket for bucket, _, _ in candidates} == {"other_flights"}


def test_candidate_helper_combines_ranking_buckets(payload: dict[str, Any]) -> None:
    same_group = payload["other_flights"][0]
    ranked_payload = {
        **payload,
        "best_flights": [same_group],
        "other_flights": [same_group],
    }

    assert [bucket for bucket, _, _ in iter_candidate_groups(ranked_payload)] == [
        "best_flights",
        "other_flights",
    ]

    offers, rejections = normalize_searchapi_response(ranked_payload)
    assert rejections == []
    assert offers[0].fingerprint == offers[1].fingerprint
    assert offers[0].model_copy(update={"raw_metadata": {}}) == offers[1].model_copy(
        update={"raw_metadata": {}}
    )
    assert [offer.raw_metadata["source_bucket"] for offer in offers] == [
        "best_flights",
        "other_flights",
    ]


def test_normalizes_all_real_fixture_results(payload: dict[str, Any]) -> None:
    offers, rejections = normalize_searchapi_response(
        payload, raw_reference=RAW_REFERENCE
    )

    assert len(offers) == 11
    assert rejections == []
    assert sum(len(offer.legs) == 1 for offer in offers) == 7
    assert sum(len(offer.legs) == 2 for offer in offers) == 4
    assert all(offer.currency == "GBP" for offer in offers)
    assert all(offer.passenger_count == 4 for offer in offers)
    assert all(offer.provider == "SearchAPI" for offer in offers)


def test_direct_offer_preserves_schedule_price_and_timezone(
    payload: dict[str, Any],
) -> None:
    offers, _ = normalize_searchapi_response(payload)
    offer = offers[0]
    flight = offer.legs[0]

    assert offer.total_price == Decimal(258)
    assert offer.passenger_count == 4
    assert offer.raw_metadata["journey_structure"] == JourneyStructure.DIRECT.value
    assert offer.ticketing_type is TicketingType.UNKNOWN
    assert offer.raw_metadata["price_semantics"] == "search_party_total"
    assert flight.origin == "LGW"
    assert flight.destination == "MXP"
    assert flight.airline == "easyJet"
    assert flight.flight_number == "U2 8305"
    assert flight.departure.utcoffset() == airport_timezone("LGW").utcoffset(
        flight.departure
    )
    assert flight.arrival.utcoffset() == airport_timezone("MXP").utcoffset(
        flight.arrival
    )
    assert flight.departure.tzinfo is not None
    assert flight.arrival.tzinfo is not None


def test_searchapi_price_is_not_multiplied_by_passenger_count(
    payload: dict[str, Any],
) -> None:
    raw_price = payload["other_flights"][0]["price"]

    offer = normalize_searchapi_response(payload)[0][0]

    assert offer.passenger_count == 4
    assert offer.total_price == Decimal(raw_price)
    assert offer.total_price != Decimal(raw_price) * offer.passenger_count


def test_one_stop_searchapi_offer_has_unknown_ticketing(
    payload: dict[str, Any],
) -> None:
    offers, _ = normalize_searchapi_response(payload)
    offer = offers[7]

    assert offer.total_price == Decimal(374)
    assert offer.raw_metadata["journey_structure"] == JourneyStructure.CONNECTION.value
    assert offer.ticketing_type is TicketingType.UNKNOWN
    assert offer.raw_metadata["ticketing_type"] == TicketingType.UNKNOWN.value
    assert [leg.flight_number for leg in offer.legs] == ["VY 7607", "VY 6054"]
    assert offer.legs[0].destination == offer.legs[1].origin == "BCN"


def test_fingerprints_are_deterministic_for_fixture(payload: dict[str, Any]) -> None:
    first_run, _ = normalize_searchapi_response(payload)
    second_run, _ = normalize_searchapi_response(payload)

    assert first_run[0].legs[0].fingerprint == second_run[0].legs[0].fingerprint
    assert first_run[0].fingerprint == second_run[0].fingerprint


def test_provider_action_tokens_do_not_affect_identity(payload: dict[str, Any]) -> None:
    changed = copy.deepcopy(payload)
    changed["other_flights"][0]["booking_token"] = "entirely-different-token"

    original = normalize_searchapi_response(payload)[0][0]
    modified = normalize_searchapi_response(changed)[0][0]

    assert original.fingerprint == modified.fingerprint
    assert original.provider_offer_id == modified.provider_offer_id
    assert (
        original.raw_metadata["provider_action_metadata"]
        != (modified.raw_metadata["provider_action_metadata"])
    )


def test_mapper_retains_private_provider_search_context(
    payload: dict[str, Any],
) -> None:
    offer = normalize_searchapi_response(payload)[0][0]

    context = offer.raw_metadata["provider_search_context"]
    assert context["departure_id"] == "LGW"
    assert context["arrival_id"] == "MXP"
    assert context["adults"] == "2"
    assert context["children"] == "2"


def _map_modified_group(payload: dict[str, Any], group: dict[str, Any]):
    return map_flight_group(
        group,
        search_parameters=payload["search_parameters"],
        observed_at=datetime(2026, 8, 24, 10, 36, 50, tzinfo=UTC),
        source_bucket="test",
        result_index=0,
    )


def test_three_leg_result_is_rejected(payload: dict[str, Any]) -> None:
    group = copy.deepcopy(payload["other_flights"][7])
    group["flights"].append(copy.deepcopy(group["flights"][1]))

    with pytest.raises(SearchAPIMapperError, match="more than one stop"):
        _map_modified_group(payload, group)


def test_mismatched_connection_airports_are_rejected(payload: dict[str, Any]) -> None:
    group = copy.deepcopy(payload["other_flights"][7])
    group["flights"][1]["departure_airport"]["id"] = "ZRH"

    with pytest.raises(ValueError, match="connection airports do not match"):
        _map_modified_group(payload, group)


def test_unknown_airport_timezone_fails_explicitly(payload: dict[str, Any]) -> None:
    group = copy.deepcopy(payload["other_flights"][0])
    group["flights"][0]["departure_airport"]["id"] = "XXX"

    with pytest.raises(SearchAPIMapperError, match="no timezone found") as caught:
        _map_modified_group(payload, group)

    assert caught.value.code is RejectionCode.UNKNOWN_AIRPORT


def test_missing_required_provider_field_fails_clearly(
    payload: dict[str, Any],
) -> None:
    group = copy.deepcopy(payload["other_flights"][0])
    del group["flights"][0]["flight_number"]

    with pytest.raises(SearchAPIMapperError, match="flight_number"):
        _map_modified_group(payload, group)


def test_batch_returns_structured_rejection(payload: dict[str, Any]) -> None:
    changed = copy.deepcopy(payload)
    del changed["other_flights"][0]["flights"][0]["flight_number"]

    offers, rejections = normalize_searchapi_response(changed)

    assert len(offers) == 10
    assert len(rejections) == 1
    rejection = rejections[0]
    assert rejection.provider == "SearchAPI"
    assert rejection.source_bucket == "other_flights"
    assert rejection.result_index == 0
    assert rejection.rejection_code is RejectionCode.MISSING_REQUIRED_FIELD
    assert rejection.field_path == "flight_number"
    assert "flight_number" in rejection.message
