"""Run at most one cached broad SearchAPI experiment and compare it offline."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

from flight_price_lab.config import Settings
from flight_price_lab.models import FlightOffer
from flight_price_lab.providers.searchapi import SearchAPIClient
from flight_price_lab.providers.searchapi_mapper import normalize_searchapi_response
from flight_price_lab.routing import BaggageProfile, SelfTransferProfile
from flight_price_lab.routing.airport_groups import INITIAL_CANDIDATE_HUBS
from flight_price_lab.routing.hub_synthesis import HubItinerary, synthesize_via_hubs
from flight_price_lab.routing.planning import RoutePlan
from flight_price_lab.storage.database import SearchResponseCache

ORIGINS = ("LGW", "STN", "LTN", "LHR", "LCY")
DESTINATIONS = ("CAG", "OLB", "AHO")
HUBS = INITIAL_CANDIDATE_HUBS
TRAVEL_DATE = date(2026, 12, 18)


def canonical_parameters() -> dict[str, Any]:
    return {
        "origins": list(ORIGINS),
        "destinations": list(DESTINATIONS),
        "date": TRAVEL_DATE.isoformat(),
        "adults": 2,
        "children": 2,
        "currency": "GBP",
        "flight_type": "one_way",
        "stops": "one_stop_or_fewer",
        "included_connecting_airports": list(HUBS),
        "layover_duration_min": 120,
        "layover_duration_max": 360,
        "separate_tickets": 0,
    }


def _candidate_count(payload: dict[str, Any]) -> int:
    return sum(
        len(payload.get(bucket, []))
        for bucket in ("best_flights", "other_flights")
        if isinstance(payload.get(bucket), list)
    )


def capture(*, execute: bool) -> tuple[dict[str, Any], Path, int, int, int, float]:
    cache = SearchResponseCache()
    parameters = canonical_parameters()
    cached = cache.get(parameters)
    if cached is not None:
        return cached.payload, cached.raw_response_path, 0, 1, 0, 0
    if not execute:
        raise RuntimeError("broad query is not cached; rerun with --execute")
    print("Broad query cache miss. Make exactly one SearchAPI request? [y/N]")
    if input().strip().lower() not in {"y", "yes"}:
        raise RuntimeError("cancelled; no provider request made")
    request_clock = perf_counter()
    payload = SearchAPIClient(Settings().searchapi_key).search_one_way(  # type: ignore[call-arg]
        departure_id=",".join(ORIGINS),
        arrival_id=",".join(DESTINATIONS),
        outbound_date=TRAVEL_DATE,
        adults=2,
        children=2,
        currency="GBP",
        stops="one_stop_or_fewer",
        included_connecting_airports=",".join(HUBS),
        layover_duration_min=120,
        layover_duration_max=360,
        separate_tickets=0,
    )
    elapsed_ms = (perf_counter() - request_clock) * 1000
    saved = cache.put(parameters, payload, result_count=_candidate_count(payload))
    return payload, saved.raw_response_path, 1, 0, 1, elapsed_ms


def _saved_direct_offers(exclude: Path) -> dict[tuple[str, str], list[FlightOffer]]:
    by_route: dict[tuple[str, str], dict[str, FlightOffer]] = {}
    for path in Path("data/raw/searchapi").rglob("*.json"):
        if (
            path.resolve() == exclude.resolve()
            or path.name == "search-plan-failures.json"
        ):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            search = payload.get("search_parameters", {})
            if (
                str(search.get("outbound_date")) != TRAVEL_DATE.isoformat()
                or int(search.get("adults", 0)) != 2
                or int(search.get("children", 0)) != 2
                or str(search.get("currency", "")).upper() != "GBP"
            ):
                continue
            offers, _ = normalize_searchapi_response(payload, raw_reference=str(path))
        except (OSError, ValueError, TypeError):
            continue
        for offer in offers:
            if len(offer.legs) != 1:
                continue
            leg = offer.legs[0]
            key = (leg.origin, leg.destination)
            existing = by_route.setdefault(key, {}).get(offer.fingerprint)
            if existing is None or offer.total_price < existing.total_price:
                by_route[key][offer.fingerprint] = offer
    return {route: list(offers.values()) for route, offers in by_route.items()}


def _schedule(item: FlightOffer | HubItinerary) -> tuple[str, ...]:
    legs = (
        item.legs
        if isinstance(item, FlightOffer)
        else tuple(leg for offer in item.itinerary.components for leg in offer.legs)
    )
    return tuple(leg.fingerprint for leg in legs)


def _frontier(items: list[HubItinerary]) -> list[HubItinerary]:
    return [
        candidate
        for candidate in items
        if not any(
            other is not candidate
            and other.total_price <= candidate.total_price
            and other.total_duration <= candidate.total_duration
            and (
                other.total_price < candidate.total_price
                or other.total_duration < candidate.total_duration
            )
            for other in items
        )
    ]


def _minutes(value: timedelta) -> int:
    return int(value.total_seconds() // 60)


def _hub_summary(item: HubItinerary) -> dict[str, Any]:
    return {
        "price": str(item.total_price),
        "hub": item.hub,
        "flights": [
            leg.flight_number
            for offer in item.itinerary.components
            for leg in offer.legs
        ],
        "connection_minutes": _minutes(item.itinerary.connection_duration),
        "journey_minutes": _minutes(item.total_duration),
    }


def analyze(payload: dict[str, Any], raw_path: Path) -> dict[str, Any]:
    broad, rejections = normalize_searchapi_response(
        payload, raw_reference=str(raw_path)
    )
    direct = [offer for offer in broad if len(offer.legs) == 1]
    connected = [offer for offer in broad if len(offer.legs) == 2]
    feasible = [
        offer
        for offer in broad
        if len(offer.legs) == 1
        or (
            offer.legs[0].destination in HUBS
            and timedelta(minutes=120)
            <= offer.legs[1].departure - offer.legs[0].arrival
            <= timedelta(minutes=360)
        )
    ]
    plan = RoutePlan(
        origin_airports=ORIGINS,
        destination_airports=DESTINATIONS,
        candidate_hubs=HUBS,
        travel_date=TRAVEL_DATE,
        adults=2,
        children=2,
        currency="GBP",
        connection_profile=SelfTransferProfile.CONSERVATIVE,
        baggage_profile=BaggageProfile.CABIN_BAG,
    )
    synthesis = synthesize_via_hubs(_saved_direct_offers(raw_path), plan)
    synthetic = [
        item
        for item in synthesis.itineraries
        if item.itinerary.connection_duration is not None
        and item.itinerary.connection_duration <= timedelta(minutes=360)
    ]
    synthetic_frontier = _frontier(synthetic)
    broad_schedules = {_schedule(item) for item in connected}
    synthetic_schedules = {_schedule(item) for item in synthetic}
    frontier_schedules = {_schedule(item) for item in synthetic_frontier}
    overlap = broad_schedules & synthetic_schedules
    broad_top_20 = {
        _schedule(item)
        for item in sorted(
            connected,
            key=lambda item: (
                item.total_price,
                item.legs[-1].arrival - item.legs[0].departure,
            ),
        )[:20]
    }
    synthetic_top_20 = {
        _schedule(item)
        for item in sorted(
            synthetic,
            key=lambda item: (item.total_price, item.total_duration),
        )[:20]
    }
    cheapest_synthetic = min(synthetic, key=lambda item: item.total_price, default=None)
    cheapest_broad = min(feasible, key=lambda item: item.total_price, default=None)
    return {
        "raw_result_count": _candidate_count(payload),
        "normalized_count": len(broad),
        "rejected_count": len(rejections),
        "direct_count": len(direct),
        "one_stop_count": len(connected),
        "hubs": sorted({offer.legs[0].destination for offer in connected}),
        "feasible_count": len(feasible),
        "cheapest_feasible": (
            None
            if cheapest_broad is None
            else {
                "price": str(cheapest_broad.total_price),
                "flights": [leg.flight_number for leg in cheapest_broad.legs],
                "journey_minutes": _minutes(
                    cheapest_broad.legs[-1].arrival - cheapest_broad.legs[0].departure
                ),
            }
        ),
        "synthetic_count": len(synthetic_schedules),
        "coverage_percent": (
            100 * len(overlap) / len(synthetic_schedules) if synthetic_schedules else 0
        ),
        "current_cheapest_recovered": (
            cheapest_synthetic is not None
            and _schedule(cheapest_synthetic) in broad_schedules
        ),
        "synthetic_cheapest": (
            None if cheapest_synthetic is None else _hub_summary(cheapest_synthetic)
        ),
        "frontier_count": len(frontier_schedules),
        "frontier_recovered": len(frontier_schedules & broad_schedules),
        "synthetic_frontier": [_hub_summary(item) for item in synthetic_frontier],
        "broad_only_count": len(broad_schedules - synthetic_schedules),
        "synthetic_only_count": len(synthetic_schedules - broad_schedules),
        "top_20_overlap_count": len(broad_top_20 & synthetic_top_20),
        "top_20_overlap_percent": (
            100 * len(broad_top_20 & synthetic_top_20) / len(synthetic_top_20)
            if synthetic_top_20
            else 0
        ),
        "exact_constituent_lineage_recoverable": False,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    payload, path, calls, hits, misses, elapsed_ms = capture(execute=args.execute)
    report = analyze(payload, path)
    report.update(
        provider_calls=calls,
        cache_hits=hits,
        cache_misses=misses,
        raw_response_path=str(path),
        analyzed_at=datetime.now(UTC).isoformat(),
        provider_elapsed_ms=elapsed_ms,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
