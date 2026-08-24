from datetime import date

import httpx
import pytest
from pydantic import SecretStr

from flight_price_lab.providers.searchapi import (
    SEARCHAPI_ENDPOINT,
    SearchAPIClient,
    SearchAPIError,
)
from flight_price_lab.providers.searchapi_booking import BookingOptionsRequest


def test_search_one_way_sends_expected_request_and_returns_json() -> None:
    expected_payload = {"best_flights": [{"price": 123}], "airports": []}

    def handle_request(request: httpx.Request) -> httpx.Response:
        assert f"{request.url.scheme}://{request.url.host}{request.url.path}" == (
            SEARCHAPI_ENDPOINT
        )
        assert dict(request.url.params) == {
            "engine": "google_flights",
            "flight_type": "one_way",
            "departure_id": "LGW",
            "arrival_id": "MXP",
            "outbound_date": "2026-12-18",
            "adults": "2",
            "children": "2",
            "currency": "GBP",
            "travel_class": "economy",
            "stops": "one_stop_or_fewer",
            "sort_by": "price",
            "show_cheapest_flights": "true",
        }
        assert request.headers["Authorization"] == "Bearer test-secret-key"
        assert "test-secret-key" not in str(request.url)
        return httpx.Response(200, json=expected_payload)

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as http_client:
        result = SearchAPIClient(
            SecretStr("test-secret-key"), http_client=http_client
        ).search_one_way(
            departure_id="LGW",
            arrival_id="MXP",
            outbound_date=date(2026, 12, 18),
            adults=2,
            children=2,
        )

    assert result == expected_payload


def test_search_one_way_raises_safe_error_for_non_success() -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="large or sensitive response", request=request)

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as http_client:
        client = SearchAPIClient(SecretStr("test-secret-key"), http_client=http_client)
        with pytest.raises(SearchAPIError) as caught:
            client.search_one_way(
                departure_id="LGW",
                arrival_id="MXP",
                outbound_date=date(2026, 12, 18),
            )

    message = str(caught.value)
    assert message == "SearchAPI returned HTTP 429"
    assert "test-secret-key" not in message
    assert "large or sensitive response" not in message


def test_search_supports_party_bag_counts_and_booking_options() -> None:
    requests: list[httpx.Request] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"booking_options": []})

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as http_client:
        client = SearchAPIClient(SecretStr("test-secret-key"), http_client=http_client)
        client.search_one_way(
            departure_id="LGW",
            arrival_id="MXP",
            outbound_date=date(2026, 12, 18),
            carry_on_bags=2,
            checked_bags=1,
        )
        client.booking_options(
            BookingOptionsRequest(
                booking_token="opaque-booking-token",
                departure_id="LGW",
                arrival_id="MXP",
                outbound_date=date(2026, 12, 18),
                adults=2,
                children=2,
                currency="GBP",
            )
        )

    assert requests[0].url.params["carry_on_bags"] == "2"
    assert requests[0].url.params["checked_bags"] == "1"
    assert dict(requests[1].url.params) == {
        "engine": "google_flights",
        "flight_type": "one_way",
        "departure_id": "LGW",
        "arrival_id": "MXP",
        "outbound_date": "2026-12-18",
        "booking_token": "opaque-booking-token",
        "adults": "2",
        "children": "2",
        "currency": "GBP",
    }
    assert all(
        request.headers["Authorization"] == "Bearer test-secret-key"
        for request in requests
    )


def test_broad_search_filters_use_documented_parameter_names() -> None:
    captured: list[httpx.Request] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"best_flights": []})

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as http_client:
        SearchAPIClient(
            SecretStr("test-secret-key"), http_client=http_client
        ).search_one_way(
            departure_id="LGW,STN,LTN,LHR,LCY",
            arrival_id="CAG,OLB,AHO",
            outbound_date=date(2026, 12, 18),
            adults=2,
            children=2,
            included_connecting_airports="MXP,BGY,LIN,FCO,CIA,BLQ,PSA,NAP",
            layover_duration_min=180,
            layover_duration_max=360,
            separate_tickets=0,
        )

    params = captured[0].url.params
    assert params["included_connecting_airports"] == ("MXP,BGY,LIN,FCO,CIA,BLQ,PSA,NAP")
    assert params["layover_duration_min"] == "180"
    assert params["layover_duration_max"] == "360"
    assert params["separate_tickets"] == "0"


def test_booking_options_400_surfaces_safe_provider_message() -> None:
    token = "opaque-booking-token"

    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": f"Invalid booking context for {token}"}},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as http_client:
        client = SearchAPIClient(SecretStr("test-secret-key"), http_client=http_client)
        with pytest.raises(SearchAPIError) as caught:
            client.booking_options(
                BookingOptionsRequest(
                    booking_token=token,
                    departure_id="LGW",
                    arrival_id="MXP",
                    outbound_date=date(2026, 12, 18),
                    adults=2,
                    children=2,
                    currency="GBP",
                )
            )

    message = str(caught.value)
    assert message == (
        "SearchAPI returned HTTP 400: Invalid booking context for [REDACTED]"
    )
    assert token not in message
    assert "test-secret-key" not in message
