"""Run a resumable SearchAPI query plan after one explicit confirmation."""

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from flight_price_lab.config import Settings
from flight_price_lab.providers.searchapi import SearchAPIClient, SearchAPIError
from flight_price_lab.routing.planning import (
    PlannedProviderSearch,
    RoutePlan,
    plan_route_discovery_searches,
)

RAW_ROOT = Path("data/raw/searchapi")
FAILURE_LEDGER_NAME = "search-plan-failures.json"
_REQUIRED_SIGNATURE_FIELDS = (
    "engine",
    "flight_type",
    "departure_id",
    "arrival_id",
    "outbound_date",
    "adults",
    "children",
    "currency",
    "travel_class",
    "stops",
    "sort_by",
    "show_cheapest_flights",
)
_OPTIONAL_SIGNATURE_FIELDS = ("carry_on_bags", "checked_bags")


@dataclass(frozen=True)
class BatchRunResult:
    calls_made: int
    skipped_existing: int
    previously_failed: int
    failures: tuple[str, ...]
    saved_paths: tuple[Path, ...]


def _airports(value: str) -> tuple[str, ...]:
    return tuple(part.strip().upper() for part in value.split(",") if part.strip())


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from None


def _signature_from_search(search: PlannedProviderSearch) -> tuple[str, ...]:
    parameters = search.as_searchapi_arguments()
    return tuple(
        str(parameters.get(key, 0)).lower()
        for key in _REQUIRED_SIGNATURE_FIELDS + _OPTIONAL_SIGNATURE_FIELDS
    )


def _signature_from_payload(payload: dict[str, Any]) -> tuple[str, ...] | None:
    parameters = payload.get("search_parameters")
    if not isinstance(parameters, dict) or any(
        key not in parameters for key in _REQUIRED_SIGNATURE_FIELDS
    ):
        return None
    return tuple(
        str(parameters.get(key, 0)).lower()
        for key in _REQUIRED_SIGNATURE_FIELDS + _OPTIONAL_SIGNATURE_FIELDS
    )


def find_existing_captures(raw_root: Path) -> dict[tuple[str, ...], Path]:
    captures: dict[tuple[str, ...], Path] = {}
    if not raw_root.exists():
        return captures
    for path in raw_root.rglob("*.json"):
        if path.name == FAILURE_LEDGER_NAME:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        signature = _signature_from_payload(payload)
        if signature is not None:
            captures[signature] = path
    return captures


def load_failure_ledger(raw_root: Path) -> dict[tuple[str, ...], str]:
    path = raw_root / FAILURE_LEDGER_NAME
    if not path.exists():
        return {}
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    failures: dict[tuple[str, ...], str] = {}
    if not isinstance(records, list):
        return failures
    for record in records:
        if not isinstance(record, dict):
            continue
        signature, message = record.get("signature"), record.get("message")
        if (
            isinstance(signature, list)
            and all(isinstance(value, str) for value in signature)
            and isinstance(message, str)
        ):
            failures[tuple(signature)] = message
    return failures


def _save_failure_ledger(raw_root: Path, failures: dict[tuple[str, ...], str]) -> None:
    raw_root.mkdir(parents=True, exist_ok=True)
    records = [
        {"signature": list(signature), "message": message}
        for signature, message in sorted(failures.items())
    ]
    (raw_root / FAILURE_LEDGER_NAME).write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )


def _description(search: PlannedProviderSearch) -> str:
    return (
        f"{','.join(search.departure_airports)} -> "
        f"{','.join(search.arrival_airports)} | {search.travel_date} | nonstop | "
        f"{search.adults}A+{search.children}C | {search.currency}"
    )


def _save_payload(
    raw_root: Path, search: PlannedProviderSearch, payload: dict[str, Any]
) -> Path:
    captured_at = datetime.now(UTC)
    directory = raw_root / captured_at.date().isoformat()
    directory.mkdir(parents=True, exist_ok=True)
    departure = "-".join(search.departure_airports)
    arrival = "-".join(search.arrival_airports)
    path = directory / (
        f"{captured_at:%Y%m%dT%H%M%SZ}_{departure}_{arrival}_{search.travel_date}.json"
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def run_plan(
    searches: Sequence[PlannedProviderSearch],
    client: SearchAPIClient,
    *,
    raw_root: Path = RAW_ROOT,
) -> BatchRunResult:
    """Run each uncaptured, unfailed query once and continue after failures."""

    existing = find_existing_captures(raw_root)
    failure_ledger = load_failure_ledger(raw_root)
    made = skipped = previously_failed = 0
    current_failures: list[str] = []
    saved: list[Path] = []
    total = len(searches)
    for index, search in enumerate(searches, start=1):
        signature = _signature_from_search(search)
        if signature in existing:
            skipped += 1
            print(f"{index}/{total} skipped: {_description(search)}")
            continue
        if signature in failure_ledger:
            previously_failed += 1
            print(f"{index}/{total} previously failed: {_description(search)}")
            continue
        parameters = search.as_searchapi_arguments()
        made += 1
        try:
            payload = client.search_one_way(
                departure_id=str(parameters["departure_id"]),
                arrival_id=str(parameters["arrival_id"]),
                outbound_date=search.travel_date,
                adults=search.adults,
                children=search.children,
                currency=search.currency,
                stops=search.stops,
                carry_on_bags=search.carry_on_bags,
                checked_bags=search.checked_bags,
            )
        except SearchAPIError as error:
            message = str(error)
            failure_ledger[signature] = message
            _save_failure_ledger(raw_root, failure_ledger)
            current_failures.append(f"{_description(search)}: {message}")
            print(f"{index}/{total} failed: {_description(search)}: {message}")
            continue
        path = _save_payload(raw_root, search, payload)
        saved.append(path)
        existing[signature] = path
        print(f"{index}/{total} saved: {path}")
    return BatchRunResult(
        calls_made=made,
        skipped_existing=skipped,
        previously_failed=previously_failed,
        failures=tuple(current_failures),
        saved_paths=tuple(saved),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origins", type=_airports, required=True)
    parser.add_argument("--destinations", type=_airports, required=True)
    parser.add_argument("--hubs", type=_airports, required=True)
    parser.add_argument("--date", type=_date, required=True)
    parser.add_argument("--adults", type=int, required=True)
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument("--currency", default="GBP")
    args = parser.parse_args()
    searches = plan_route_discovery_searches(
        RoutePlan(
            origin_airports=args.origins,
            destination_airports=args.destinations,
            candidate_hubs=args.hubs,
            travel_date=args.date,
            adults=args.adults,
            children=args.children,
            currency=args.currency,
        )
    )
    existing = find_existing_captures(RAW_ROOT)
    previous_failures = load_failure_ledger(RAW_ROOT)
    print(f"FULL QUERY MANIFEST ({len(searches)} entries)")
    for index, search in enumerate(searches, start=1):
        signature = _signature_from_search(search)
        status = (
            "SKIP"
            if signature in existing
            else "PREVIOUSLY_FAILED"
            if signature in previous_failures
            else "RUN"
        )
        print(f"{index}. [{status}] {_description(search)}")
    run_count = sum(
        _signature_from_search(search) not in existing
        and _signature_from_search(search) not in previous_failures
        for search in searches
    )
    print(f"\nAbout to execute up to {run_count} SearchAPI requests. Continue? [y/N]")
    if input().strip().lower() not in {"y", "yes"}:
        print("Cancelled; no requests made.")
        return

    settings = Settings()  # type: ignore[call-arg]
    result = run_plan(searches, SearchAPIClient(settings.searchapi_key))
    print(
        f"Complete: {result.calls_made} API calls made; "
        f"{result.skipped_existing} existing captures skipped; "
        f"{result.previously_failed} previous failures not retried."
    )
    if result.failures:
        print("FAILURES")
        for failure in result.failures:
            print(f"- {failure}")
    if result.failures or result.previously_failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
