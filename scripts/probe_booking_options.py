"""Make one confirmed booking-options request for one selected captured offer."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flight_price_lab.config import Settings
from flight_price_lab.providers.searchapi import SearchAPIClient
from flight_price_lab.providers.searchapi_booking import (
    BookingOptionsRequest,
    parse_booking_options,
)
from flight_price_lab.providers.searchapi_mapper import normalize_searchapi_response


def _select_offer(payload: dict[str, Any], flight_number: str):
    offers, rejections = normalize_searchapi_response(payload)
    if rejections:
        raise ValueError("capture contains normalization rejections")
    matches = [
        offer
        for offer in offers
        if any(leg.flight_number == flight_number for leg in offer.legs)
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one offer for flight {flight_number!r}")
    return matches[0]


def _booking_request(payload: dict[str, Any], token: str) -> BookingOptionsRequest:
    parameters = payload.get("search_parameters")
    if not isinstance(parameters, dict):
        raise TypeError("capture has no search_parameters object")
    try:
        return BookingOptionsRequest(
            booking_token=token,
            departure_id=parameters["departure_id"],
            arrival_id=parameters["arrival_id"],
            outbound_date=parameters["outbound_date"],
            flight_type=parameters["flight_type"],
            adults=parameters.get("adults"),
            children=parameters.get("children"),
            currency=parameters.get("currency"),
        )
    except KeyError as error:
        raise ValueError(
            f"capture is missing search parameter {error.args[0]}"
        ) from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_json", type=Path)
    parser.add_argument("--flight-number", required=True)
    args = parser.parse_args()

    payload: dict[str, Any] = json.loads(args.raw_json.read_text(encoding="utf-8"))
    offer = _select_offer(payload, args.flight_number)
    action_metadata = offer.raw_metadata.get("provider_action_metadata")
    token = (
        action_metadata.get("booking_token")
        if isinstance(action_metadata, dict)
        else None
    )
    if not isinstance(token, str) or not token:
        raise ValueError("selected offer has no booking_token")

    flights = " + ".join(leg.flight_number for leg in offer.legs)
    print(f"Selected fare: {offer.currency} {offer.total_price}; flights: {flights}")
    print("About to make exactly 1 SearchAPI booking-options request. Continue? [y/N]")
    if input().strip().lower() not in {"y", "yes"}:
        print("Cancelled; no request made.")
        return

    settings = Settings()  # type: ignore[call-arg]
    request = _booking_request(payload, token)
    response = SearchAPIClient(settings.searchapi_key).booking_options(request)
    captured_at = datetime.now(UTC)
    directory = (
        Path("data/raw/searchapi/booking_options") / captured_at.date().isoformat()
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{captured_at:%Y%m%dT%H%M%SZ}_{offer.fingerprint}.json"
    path.write_text(
        json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    options = parse_booking_options(response)
    print(f"Saved raw booking options: {path}")
    print(f"Booking options parsed: {len(options)}")
    for index, option in enumerate(options, start=1):
        print(
            f"{index}. provider={option.booking_provider or 'unknown'}; "
            f"fare_type={option.fare_type or 'unknown'}; price={option.price}; "
            f"split={option.is_split_booking}; baggage={option.baggage_prices!r}"
        )


if __name__ == "__main__":
    main()
