"""Manually capture one raw SearchAPI response for schema discovery."""

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from flight_price_lab.config import Settings
from flight_price_lab.providers.searchapi import SearchAPIClient

DEFAULT_DEPARTURE = "LGW"
DEFAULT_ARRIVAL = "MXP"
DEFAULT_TRAVEL_DATE = date(2026, 12, 18)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be at least 0")
    return parsed


def _airport_ids(value: str) -> str:
    codes = tuple(code.strip().upper() for code in value.split(","))
    if not codes or any(len(code) != 3 or not code.isalpha() for code in codes):
        raise argparse.ArgumentTypeError(
            "must be one or more comma-separated three-letter IATA airport codes"
        )
    return ",".join(codes)


def _travel_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD format") from None


def _currency(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise argparse.ArgumentTypeError("must be a three-letter currency code")
    return code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--departure", type=_airport_ids, default=DEFAULT_DEPARTURE)
    parser.add_argument("--arrival", type=_airport_ids, default=DEFAULT_ARRIVAL)
    parser.add_argument(
        "--date", dest="travel_date", type=_travel_date, default=DEFAULT_TRAVEL_DATE
    )
    parser.add_argument("--adults", type=_positive_int, default=2)
    parser.add_argument("--children", type=_nonnegative_int, default=2)
    parser.add_argument("--currency", type=_currency, default="GBP")
    parser.add_argument("--carry-on-bags", type=_nonnegative_int)
    parser.add_argument("--checked-bags", type=_nonnegative_int)
    parser.add_argument(
        "--stops",
        choices=("nonstop", "one_stop_or_fewer"),
        default="one_stop_or_fewer",
    )
    return parser.parse_args(argv)


def _result_count(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    return len(value) if isinstance(value, list) else 0


def _print_summary(payload: dict[str, Any], saved_path: Path) -> None:
    print(f"Saved raw response: {saved_path}")
    print(f"Top-level JSON keys: {sorted(payload)}")
    print(f"best_flights count: {_result_count(payload, 'best_flights')}")
    print(f"other_flights count: {_result_count(payload, 'other_flights')}")
    print(f"price_insights present: {'price_insights' in payload}")
    print(f"airports present: {'airports' in payload}")

    results = payload.get("best_flights")
    if not isinstance(results, list):
        return
    for index, result in enumerate(results[:5], start=1):
        if not isinstance(result, dict):
            continue
        flights = result.get("flights")
        segment_count = len(flights) if isinstance(flights, list) else "unknown"
        print(
            f"Result {index}: price={result.get('price', 'unknown')}, "
            f"segments={segment_count}"
        )


def main() -> None:
    args = parse_args()
    print(
        "About to make 1 SearchAPI request:\n"
        f" {args.departure} -> {args.arrival}\n"
        f" {args.travel_date.isoformat()}\n"
        f" {args.adults} adults + {args.children} children\n"
        f" {args.currency}\n"
        f" {args.carry_on_bags or 0} carry-on bags + "
        f"{args.checked_bags or 0} checked bags\n"
        " Continue? [y/N]"
    )
    if input().strip().lower() not in {"y", "yes"}:
        print("Cancelled; no request made.")
        return

    settings = Settings()  # type: ignore[call-arg]
    payload = SearchAPIClient(settings.searchapi_key).search_one_way(
        departure_id=args.departure,
        arrival_id=args.arrival,
        outbound_date=args.travel_date,
        adults=args.adults,
        children=args.children,
        currency=args.currency,
        stops=args.stops,
        carry_on_bags=args.carry_on_bags,
        checked_bags=args.checked_bags,
    )

    captured_at = datetime.now(UTC)
    timestamp = captured_at.strftime("%Y%m%dT%H%M%SZ")
    output_directory = Path("data/raw/searchapi") / captured_at.date().isoformat()
    output_directory.mkdir(parents=True, exist_ok=True)
    departure_slug = args.departure.replace(",", "-")
    arrival_slug = args.arrival.replace(",", "-")
    output_path = output_directory / (
        f"{timestamp}_{departure_slug}_{arrival_slug}_"
        f"{args.travel_date.isoformat()}_co{args.carry_on_bags or 0}_"
        f"cb{args.checked_bags or 0}.json"
    )
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _print_summary(payload, output_path)


if __name__ == "__main__":
    main()
