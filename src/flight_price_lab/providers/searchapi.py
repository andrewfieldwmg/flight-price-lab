"""Minimal client for capturing raw SearchAPI Google Flights responses."""

from datetime import date
from typing import Any

import httpx
from pydantic import SecretStr

from flight_price_lab.providers.searchapi_booking import BookingOptionsRequest

SEARCHAPI_ENDPOINT = "https://www.searchapi.io/api/v1/search"
SEARCHAPI_ACCOUNT_ENDPOINT = "https://www.searchapi.io/api/v1/me"


class SearchAPIError(RuntimeError):
    """A safe, concise SearchAPI request failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SearchAPIClient:
    """SearchAPI client with bearer authentication and no automatic retries."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        http_client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._http_client = http_client
        self._timeout = timeout

    def search_one_way(
        self,
        *,
        departure_id: str,
        arrival_id: str,
        outbound_date: date,
        adults: int = 1,
        children: int = 0,
        currency: str = "GBP",
        travel_class: str = "economy",
        stops: str = "one_stop_or_fewer",
        sort_by: str = "price",
        show_cheapest_flights: bool = True,
        carry_on_bags: int | None = None,
        checked_bags: int | None = None,
        included_connecting_airports: str | None = None,
        layover_duration_min: int | None = None,
        layover_duration_max: int | None = None,
        separate_tickets: int | None = None,
    ) -> dict[str, Any]:
        """Make one request and return its decoded JSON object unchanged."""

        params: dict[str, str | int] = {
            "engine": "google_flights",
            "flight_type": "one_way",
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "outbound_date": outbound_date.isoformat(),
            "adults": adults,
            "children": children,
            "currency": currency,
            "travel_class": travel_class,
            "stops": stops,
            "sort_by": sort_by,
            "show_cheapest_flights": str(show_cheapest_flights).lower(),
        }
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        if carry_on_bags is not None:
            params["carry_on_bags"] = carry_on_bags
        if checked_bags is not None:
            params["checked_bags"] = checked_bags
        if included_connecting_airports is not None:
            params["included_connecting_airports"] = included_connecting_airports
        if layover_duration_min is not None:
            params["layover_duration_min"] = layover_duration_min
        if layover_duration_max is not None:
            params["layover_duration_max"] = layover_duration_max
        if separate_tickets is not None:
            params["separate_tickets"] = separate_tickets

        return self._get(params, headers)

    def booking_options(self, request: BookingOptionsRequest) -> dict[str, Any]:
        """Request booking options with the token's originating search context."""

        token = request.booking_token.get_secret_value()
        params: dict[str, str | int] = {
            "engine": "google_flights",
            "flight_type": request.flight_type,
            "departure_id": request.departure_id,
            "arrival_id": request.arrival_id,
            "outbound_date": request.outbound_date.isoformat(),
            "booking_token": token,
        }
        if request.adults is not None:
            params["adults"] = request.adults
        if request.children is not None:
            params["children"] = request.children
        if request.currency is not None:
            params["currency"] = request.currency
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        return self._get(params, headers, sensitive_values=(token,))

    def account_usage(self) -> dict[str, Any]:
        """Return account usage without placing credentials in the URL."""

        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        return self._get({}, headers, endpoint=SEARCHAPI_ACCOUNT_ENDPOINT)

    def _get(
        self,
        params: dict[str, str | int],
        headers: dict[str, str],
        sensitive_values: tuple[str, ...] = (),
        endpoint: str = SEARCHAPI_ENDPOINT,
    ) -> dict[str, Any]:

        try:
            if self._http_client is not None:
                response = self._http_client.get(
                    endpoint,
                    params=params,
                    headers=headers,
                    timeout=self._timeout,
                )
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.get(endpoint, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as error:
            provider_message = _safe_provider_message(
                error.response,
                sensitive_values=(
                    self._api_key.get_secret_value(),
                    *sensitive_values,
                ),
            )
            suffix = f": {provider_message}" if provider_message else ""
            raise SearchAPIError(
                f"SearchAPI returned HTTP {error.response.status_code}{suffix}",
                status_code=error.response.status_code,
            ) from None
        except (httpx.HTTPError, ValueError) as error:
            raise SearchAPIError(
                f"SearchAPI request failed: {type(error).__name__}"
            ) from None

        if not isinstance(payload, dict):
            raise SearchAPIError("SearchAPI returned a non-object JSON response")
        return payload


def _safe_provider_message(
    response: httpx.Response, *, sensitive_values: tuple[str, ...]
) -> str | None:
    """Extract a bounded provider error without exposing request secrets."""

    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    candidate = payload.get("message", payload.get("error"))
    if isinstance(candidate, dict):
        candidate = candidate.get("message")
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    message = candidate.strip()[:300]
    for secret in sensitive_values:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message
