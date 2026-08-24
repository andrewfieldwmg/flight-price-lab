"""Compare matching offers in baseline and baggage-inclusive SearchAPI captures."""

import argparse
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from flight_price_lab.models import FlightOffer
from flight_price_lab.providers.searchapi_mapper import normalize_searchapi_response


@dataclass(frozen=True)
class BaggageFareComparison:
    fingerprint: str
    flight_numbers: str
    baseline_total: Decimal
    baggage_search_total: Decimal

    @property
    def delta(self) -> Decimal:
        return self.baggage_search_total - self.baseline_total

    @property
    def ratio(self) -> Decimal:
        return self.baggage_search_total / self.baseline_total


@dataclass(frozen=True)
class BaggageComparisonResult:
    matches: tuple[BaggageFareComparison, ...]
    unmatched_baseline: int
    unmatched_baggage_search: int


def _offers(path: Path) -> list[FlightOffer]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    offers, rejections = normalize_searchapi_response(payload, raw_reference=str(path))
    if rejections:
        raise ValueError(f"{path} contains {len(rejections)} rejected results")
    return offers


def compare_baggage_fares(
    baseline_path: Path, baggage_path: Path
) -> BaggageComparisonResult:
    baseline = {offer.fingerprint: offer for offer in _offers(baseline_path)}
    baggage = {offer.fingerprint: offer for offer in _offers(baggage_path)}
    shared = sorted(baseline.keys() & baggage.keys())
    matches = tuple(
        BaggageFareComparison(
            fingerprint=fingerprint,
            flight_numbers=" + ".join(
                leg.flight_number for leg in baseline[fingerprint].legs
            ),
            baseline_total=baseline[fingerprint].total_price,
            baggage_search_total=baggage[fingerprint].total_price,
        )
        for fingerprint in shared
    )
    return BaggageComparisonResult(
        matches=matches,
        unmatched_baseline=len(baseline.keys() - baggage.keys()),
        unmatched_baggage_search=len(baggage.keys() - baseline.keys()),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_json", type=Path)
    parser.add_argument("baggage_json", type=Path)
    args = parser.parse_args()
    result = compare_baggage_fares(args.baseline_json, args.baggage_json)

    print("flight_numbers\tbaseline total\tbaggage-search total\tdelta\tratio")
    for item in sorted(result.matches, key=lambda match: match.baseline_total):
        print(
            f"{item.flight_numbers}\t{item.baseline_total}\t"
            f"{item.baggage_search_total}\t{item.delta}\t{item.ratio:.4f}"
        )
    print(f"Matched offers: {len(result.matches)}")
    print(f"Unmatched baseline offers: {result.unmatched_baseline}")
    print(f"Unmatched baggage-search offers: {result.unmatched_baggage_search}")
    print(
        "Inference confidence: AMBIGUOUS — matching schedules do not prove that "
        "the fare bucket and all non-baggage conditions remained identical."
    )


if __name__ == "__main__":
    main()
