import copy
import json
from pathlib import Path
from typing import Any

from scripts.compare_baggage_pricing import compare_baggage_fares
from scripts.compare_passenger_pricing import compare_snapshots
from scripts.probe_booking_options import _booking_request
from scripts.probe_searchapi import parse_args

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "searchapi" / "lgw_mxp_2026-12-18_2a2c.json"
)


def test_probe_accepts_explicit_passenger_arguments() -> None:
    args = parse_args(
        [
            "--departure",
            "mxp",
            "--arrival",
            "cag",
            "--date",
            "2026-12-18",
            "--adults",
            "2",
            "--children",
            "2",
            "--currency",
            "gbp",
        ]
    )

    assert args.departure == "MXP"
    assert args.arrival == "CAG"
    assert args.travel_date.isoformat() == "2026-12-18"
    assert args.adults == 2
    assert args.children == 2
    assert args.currency == "GBP"


def test_probe_accepts_multiple_departures_and_nonstop() -> None:
    args = parse_args(
        [
            "--departure",
            "LGW,STN,LTN,LHR,LCY",
            "--arrival",
            "CAG",
            "--stops",
            "nonstop",
        ]
    )

    assert args.departure == "LGW,STN,LTN,LHR,LCY"
    assert args.arrival == "CAG"
    assert args.stops == "nonstop"


def test_probe_accepts_party_total_baggage_counts() -> None:
    args = parse_args(["--carry-on-bags", "4", "--checked-bags", "0"])

    assert args.carry_on_bags == 4
    assert args.checked_bags == 0


def test_price_comparison_matches_canonical_fingerprints(tmp_path: Path) -> None:
    party_payload: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    adult_payload = copy.deepcopy(party_payload)
    adult_payload["search_parameters"]["adults"] = "1"
    adult_payload["search_parameters"]["children"] = "0"
    for group in adult_payload["other_flights"]:
        group["price"] = group["price"] / 2

    party_path = tmp_path / "party.json"
    adult_path = tmp_path / "adult.json"
    party_path.write_text(json.dumps(party_payload), encoding="utf-8")
    adult_path.write_text(json.dumps(adult_payload), encoding="utf-8")

    comparisons = compare_snapshots(party_path, adult_path)

    assert len(comparisons) == 11
    assert all(item.price_ratio == 2 for item in comparisons)


def test_booking_probe_couples_token_to_capture_search_context() -> None:
    payload: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    request = _booking_request(payload, "opaque-token")

    assert request.booking_token.get_secret_value() == "opaque-token"
    assert request.departure_id == "LGW"
    assert request.arrival_id == "MXP"
    assert request.outbound_date.isoformat() == "2026-12-18"
    assert request.adults == 2
    assert request.children == 2
    assert request.currency == "GBP"


def test_baggage_comparison_matches_fingerprints_and_reports_unmatched(
    tmp_path: Path,
) -> None:
    baseline: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    baggage = copy.deepcopy(baseline)
    baggage["search_parameters"]["carry_on_bags"] = "4"
    baggage["other_flights"][0]["price"] += 80
    baggage["other_flights"].pop()
    baseline_path = tmp_path / "baseline.json"
    baggage_path = tmp_path / "baggage.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    baggage_path.write_text(json.dumps(baggage), encoding="utf-8")

    result = compare_baggage_fares(baseline_path, baggage_path)

    assert len(result.matches) == 10
    assert result.unmatched_baseline == 1
    assert result.unmatched_baggage_search == 0
    changed = next(item for item in result.matches if item.flight_numbers == "U2 8305")
    assert changed.delta == 80
