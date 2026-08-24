"""Compare matching flight prices in two saved SearchAPI snapshots; never calls APIs."""

import argparse
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any

from flight_price_lab.models import FlightOffer
from flight_price_lab.providers.searchapi_mapper import normalize_searchapi_response


@dataclass(frozen=True)
class PriceComparison:
    fingerprint: str
    flight_numbers: str
    one_adult_price: Decimal
    party_price: Decimal

    @property
    def price_ratio(self) -> Decimal:
        return self.party_price / self.one_adult_price


def _load_offers(path: Path) -> list[FlightOffer]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    offers, rejections = normalize_searchapi_response(payload, raw_reference=str(path))
    if rejections:
        details = "; ".join(
            f"{item.source_bucket}[{item.result_index}] {item.rejection_code}"
            for item in rejections
        )
        raise ValueError(f"snapshot contains rejected results: {details}")
    return offers


def compare_snapshots(
    party_snapshot: Path, one_adult_snapshot: Path
) -> list[PriceComparison]:
    """Match canonical offer fingerprints and return price comparisons."""

    party_offers = {offer.fingerprint: offer for offer in _load_offers(party_snapshot)}
    adult_offers = {
        offer.fingerprint: offer for offer in _load_offers(one_adult_snapshot)
    }
    comparisons = []
    for fingerprint in sorted(party_offers.keys() & adult_offers.keys()):
        party_offer = party_offers[fingerprint]
        adult_offer = adult_offers[fingerprint]
        if adult_offer.total_price == 0:
            continue
        comparisons.append(
            PriceComparison(
                fingerprint=fingerprint,
                flight_numbers=" + ".join(
                    leg.flight_number for leg in adult_offer.legs
                ),
                one_adult_price=adult_offer.total_price,
                party_price=party_offer.total_price,
            )
        )
    return comparisons


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("party_snapshot", type=Path, help="2-adult + 2-child JSON")
    parser.add_argument("one_adult_snapshot", type=Path, help="1-adult JSON")
    args = parser.parse_args()

    comparisons = compare_snapshots(args.party_snapshot, args.one_adult_snapshot)
    print("fingerprint\tflight_numbers\t1-adult price\t2A+2C price\tprice ratio")
    for item in comparisons:
        print(
            f"{item.fingerprint}\t{item.flight_numbers}\t"
            f"{item.one_adult_price}\t{item.party_price}\t{item.price_ratio:.4f}"
        )
    if comparisons:
        print(f"median ratio\t{median(item.price_ratio for item in comparisons):.4f}")
    else:
        print("median ratio\tno matching offers")
    print("No automatic price-semantics conclusion is made.")


if __name__ == "__main__":
    main()
