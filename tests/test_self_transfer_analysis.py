import csv
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from flight_price_lab.analytics.self_transfers import (
    analyze_offer_pairs,
    price_duration_frontier,
    total_journey_duration,
    write_itineraries_csv,
)
from flight_price_lab.models import JourneyStructure, TicketingType
from flight_price_lab.providers.searchapi_mapper import normalize_searchapi_response

FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures" / "searchapi"
FIRST_FIXTURE = FIXTURE_DIRECTORY / "lgw_mxp_2026-12-18_2a2c.json"
SECOND_FIXTURE = FIXTURE_DIRECTORY / "mxp_cag_2026-12-18_2a2c.json"


def _offers(path: Path):
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    offers, rejections = normalize_searchapi_response(payload)
    assert rejections == []
    return offers


@pytest.fixture(scope="module")
def analysis():
    return analyze_offer_pairs(_offers(FIRST_FIXTURE), _offers(SECOND_FIXTURE))


def test_normalizes_mxp_cag_real_fixture() -> None:
    offers = _offers(SECOND_FIXTURE)

    assert len(offers) == 7
    assert sum(len(offer.legs) == 1 for offer in offers) == 5
    assert sum(len(offer.legs) > 1 for offer in offers) == 2
    assert min(offer.total_price for offer in offers if len(offer.legs) == 1) == 158
    assert max(offer.total_price for offer in offers if len(offer.legs) == 1) == 253


def test_synthesizes_valid_real_fixture_offers_with_lineage(analysis) -> None:
    assert analysis.first_direct_count == 7
    assert analysis.second_direct_count == 5
    assert analysis.theoretical_combinations == 35
    assert analysis.itineraries

    itinerary = analysis.itineraries[0]
    first_offer, second_offer = itinerary.components
    assert itinerary.journey_structure is JourneyStructure.CONNECTION
    assert itinerary.ticketing_type is TicketingType.SEPARATE_TICKETS
    assert itinerary.connection_airport == "MXP"
    assert itinerary.number_of_stops == 1
    assert itinerary.total_price == first_offer.total_price + second_offer.total_price
    assert (
        itinerary.passenger_count
        == first_offer.passenger_count
        == (second_offer.passenger_count)
    )
    assert itinerary.currency == first_offer.currency == second_offer.currency
    assert itinerary.constituent_offer_fingerprints == (
        first_offer.fingerprint,
        second_offer.fingerprint,
    )


def test_filters_chronologically_invalid_and_multileg_constituents(analysis) -> None:
    assert analysis.rejected_too_early > 0
    assert analysis.first_direct_count == 7
    assert analysis.second_direct_count == 5
    assert all(
        len(offer.legs) == 1
        for itinerary in analysis.itineraries
        for offer in itinerary.components
    )


def test_output_is_sorted_by_total_price(analysis) -> None:
    prices = [itinerary.total_price for itinerary in analysis.itineraries]

    assert prices == sorted(prices)


def test_price_duration_frontier_excludes_dominated_option(analysis) -> None:
    base = analysis.itineraries[0]
    cheap_slow = base.model_copy(
        update={
            "total_price": Decimal(100),
            "final_arrival": base.departure + timedelta(hours=5),
        }
    )
    expensive_fast = base.model_copy(
        update={
            "total_price": Decimal(120),
            "final_arrival": base.departure + timedelta(hours=4),
        }
    )
    dominated = base.model_copy(
        update={
            "total_price": Decimal(130),
            "final_arrival": base.departure + timedelta(hours=6),
        }
    )

    frontier = price_duration_frontier([dominated, expensive_fast, cheap_slow])

    assert frontier == [cheap_slow, expensive_fast]
    assert [total_journey_duration(item) for item in frontier] == [
        timedelta(hours=5),
        timedelta(hours=4),
    ]


def test_optional_csv_contains_price_sorted_itineraries(
    analysis, tmp_path: Path
) -> None:
    output = tmp_path / "synthetic-itineraries.csv"

    write_itineraries_csv(output, analysis.itineraries)

    with output.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == len(analysis.itineraries)
    assert [Decimal(row["total_price"]) for row in rows] == sorted(
        itinerary.total_price for itinerary in analysis.itineraries
    )
