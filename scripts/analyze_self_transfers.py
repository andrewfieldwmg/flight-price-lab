"""Analyze synthetic connections from two saved SearchAPI JSON responses."""

import argparse
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from flight_price_lab.analytics.self_transfers import (
    SelfTransferAnalysis,
    analyze_offer_pairs,
    connection_duration_distribution,
    price_duration_frontier,
    total_journey_duration,
    write_itineraries_csv,
)
from flight_price_lab.models import ConstructedItinerary, FlightOffer
from flight_price_lab.providers.searchapi_mapper import normalize_searchapi_response
from flight_price_lab.routing import BaggageProfile, SelfTransferProfile


def _load_offers(path: Path) -> list[FlightOffer]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    offers, rejections = normalize_searchapi_response(payload, raw_reference=str(path))
    if rejections:
        details = "; ".join(
            f"{item.source_bucket}[{item.result_index}] "
            f"{item.rejection_code}: {item.message}"
            for item in rejections
        )
        raise ValueError(f"normalization rejected results in {path}: {details}")
    return offers


def analyze_files(
    first_path: Path,
    second_path: Path,
    *,
    profile: SelfTransferProfile,
    baggage: BaggageProfile,
) -> SelfTransferAnalysis:
    """Load, normalize, and synthesize two saved provider responses."""

    return analyze_offer_pairs(
        _load_offers(first_path),
        _load_offers(second_path),
        profile=profile,
        baggage=baggage,
    )


def _minutes(duration: timedelta) -> int:
    return int(duration.total_seconds() / 60)


def _row(rank: int, itinerary: ConstructedItinerary) -> str:
    first_offer, second_offer = itinerary.components
    first_leg, second_leg = first_offer.legs[0], second_offer.legs[0]
    return "\t".join(
        (
            str(rank),
            f"{itinerary.currency} {itinerary.total_price}",
            first_leg.flight_number,
            first_leg.departure.isoformat(),
            first_leg.arrival.isoformat(),
            second_leg.flight_number,
            second_leg.departure.isoformat(),
            second_leg.arrival.isoformat(),
            str(_minutes(itinerary.connection_duration)),
            str(_minutes(total_journey_duration(itinerary))),
            "yes" if itinerary.overnight_connection else "no",
            str(first_offer.total_price),
            str(second_offer.total_price),
        )
    )


def _print_table(
    title: str,
    itineraries: tuple[ConstructedItinerary, ...] | list[ConstructedItinerary],
) -> None:
    print(f"\n{title}")
    print(
        "Rank\tTotal family price\tFirst flight\tLGW departure\tMXP arrival\t"
        "Second flight\tMXP departure\tCAG arrival\tConnection min\t"
        "Journey min\tOvernight\tFirst price\tSecond price"
    )
    for rank, itinerary in enumerate(itineraries, start=1):
        print(_row(rank, itinerary))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first_raw_json", type=Path)
    parser.add_argument("second_raw_json", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--profile",
        type=SelfTransferProfile,
        choices=list(SelfTransferProfile),
        default=SelfTransferProfile.CONSERVATIVE,
    )
    parser.add_argument(
        "--baggage",
        type=BaggageProfile,
        choices=list(BaggageProfile),
        default=BaggageProfile.CABIN_BAG,
    )
    args = parser.parse_args()

    analysis = analyze_files(
        args.first_raw_json,
        args.second_raw_json,
        profile=args.profile,
        baggage=args.baggage,
    )
    print(f"Direct first-leg offers: {analysis.first_direct_count}")
    print(f"Direct second-leg offers: {analysis.second_direct_count}")
    print(f"Theoretical Cartesian product: {analysis.theoretical_combinations}")
    print(
        f"Rejected because second flight departs too early: {analysis.rejected_too_early}"
    )
    print(f"Rejected for other incompatibility: {analysis.rejected_incompatible}")
    print(f"Total chronological combinations: {analysis.chronological_combinations}")
    print(
        f"Rejected by minimum connection time: {analysis.rejected_minimum_connection}"
    )
    print(f"Feasible combinations: {len(analysis.itineraries)}")
    if analysis.itineraries:
        cheapest = analysis.itineraries[0]
        most_expensive = analysis.itineraries[-1]
        print(f"Cheapest feasible: {cheapest.currency} {cheapest.total_price}")
        print(
            f"Most expensive feasible: {most_expensive.currency} "
            f"{most_expensive.total_price}"
        )

    print("\nCONNECTION DURATION DISTRIBUTION")
    for bucket, count in connection_duration_distribution(analysis.itineraries).items():
        print(f"{bucket}: {count}")

    _print_table("FEASIBLE SYNTHETIC ITINERARIES", analysis.itineraries)
    _print_table(
        "FEASIBLE PRICE / DURATION FRONTIER",
        price_duration_frontier(analysis.itineraries),
    )

    if args.output is not None:
        write_itineraries_csv(args.output, analysis.itineraries)
        print(f"\nSaved CSV: {args.output}")


if __name__ == "__main__":
    main()
