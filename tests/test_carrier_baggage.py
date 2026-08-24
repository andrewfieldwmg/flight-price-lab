import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from flight_price_lab.analytics.carrier_baggage import (
    estimate_offer_baggage,
    load_carrier_baggage_rules,
    price_itinerary_from_carrier_rules,
)
from flight_price_lab.models import (
    AncillaryEstimateStatus,
    AncillaryPriceType,
    BaggageRequirement,
    BagPriceRule,
    CarrierBaggageRule,
    CarrierRuleConfidence,
    FlightLeg,
    FlightOffer,
    PersonalItemRule,
)
from flight_price_lab.providers.searchapi_mapper import normalize_searchapi_response
from flight_price_lab.routing import construct_self_transfer


def offer(
    origin: str,
    destination: str,
    hour: int,
    flight_number: str,
    price: str = "100",
    currency: str = "GBP",
) -> FlightOffer:
    departure = datetime(2026, 12, 18, hour, tzinfo=UTC)
    return FlightOffer(
        legs=(
            FlightLeg(
                origin=origin,
                destination=destination,
                departure=departure,
                arrival=departure + timedelta(hours=2),
                airline=flight_number.split()[0],
                flight_number=flight_number,
            ),
        ),
        total_price=Decimal(price),
        currency=currency,
        passenger_count=4,
        provider="test",
        provider_offer_id=flight_number,
        observed_at=datetime(2026, 8, 24, tzinfo=UTC),
    )


def synthetic_rule(code: str, cabin: BagPriceRule) -> CarrierBaggageRule:
    return CarrierBaggageRule(
        carrier_code=code,
        carrier_name=code,
        source_retrieved_at="2026-08-24",
        personal_item=PersonalItemRule(included=True),
        cabin_bag=cabin,
        checked_bag=BagPriceRule(),
        confidence=CarrierRuleConfidence.HIGH,
    )


def test_included_cabin_bag_has_zero_complete_cost() -> None:
    included = BagPriceRule(included=True)
    result = estimate_offer_baggage(
        offer("LGW", "MXP", 8, "U2 8309"),
        BaggageRequirement(carry_on_bags=4),
        {"U2": synthetic_rule("U2", included)},
    )

    assert result.estimated_ancillary_total == 0
    assert result.lower_bound == result.upper_bound == 0
    assert result.completeness_status is AncillaryEstimateStatus.COMPLETE


def test_fixed_charge_is_multiplied_by_party_bag_count() -> None:
    fixed = BagPriceRule(
        price_type=AncillaryPriceType.FIXED,
        price_min={"GBP": Decimal(20)},
        price_max={"GBP": Decimal(20)},
    )
    result = estimate_offer_baggage(
        offer("LGW", "MXP", 8, "U2 8309"),
        BaggageRequirement(carry_on_bags=4),
        {"U2": synthetic_rule("U2", fixed)},
    )

    assert result.estimated_ancillary_total == Decimal(80)


def test_ryanair_range_resolves_for_named_market_currency() -> None:
    rules = load_carrier_baggage_rules()
    result = estimate_offer_baggage(
        offer("STN", "CAG", 8, "FR 2687"),
        BaggageRequirement(carry_on_bags=1),
        rules,
    )

    assert rules["FR"].cabin_bag.price_type is AncillaryPriceType.RANGE
    assert result.native_ranges[0].currency == "GBP/EUR"
    assert result.native_ranges[0].low == Decimal(6)
    assert result.native_ranges[0].high == Decimal(36)
    assert result.lower_bound == Decimal(6)
    assert result.upper_bound == Decimal(36)
    assert result.completeness_status is AncillaryEstimateStatus.PARTIAL


def test_easyjet_minimum_only_dynamic_pricing_is_partial() -> None:
    result = estimate_offer_baggage(
        offer("LGW", "MXP", 8, "U2 8309"),
        BaggageRequirement(carry_on_bags=4),
        load_carrier_baggage_rules(),
    )

    assert result.lower_bound == Decimal("23.96")
    assert result.upper_bound is None
    assert result.completeness_status is AncillaryEstimateStatus.PARTIAL


def test_wizz_dynamic_without_price_is_unknown() -> None:
    result = estimate_offer_baggage(
        offer("MXP", "CAG", 13, "W4 6997"),
        BaggageRequirement(carry_on_bags=1),
        load_carrier_baggage_rules(),
    )

    assert result.lower_bound is None
    assert result.upper_bound is None
    assert result.completeness_status is AncillaryEstimateStatus.UNKNOWN


def test_vueling_multiple_checked_bag_weights_select_requested_weight() -> None:
    rules = load_carrier_baggage_rules()
    assert [option.weight_kg for option in rules["VY"].checked_bag.options] == [
        15,
        20,
        25,
        30,
    ]
    result = estimate_offer_baggage(
        offer("BCN", "CAG", 8, "VY 1234", currency="EUR"),
        BaggageRequirement(checked_bags=1, checked_bag_weight_kg=25),
        rules,
    )

    assert result.lower_bound == Decimal(18)
    assert result.upper_bound == Decimal(99)
    assert result.completeness_status is AncillaryEstimateStatus.PARTIAL


def test_swiss_fare_dependent_price_is_unknown() -> None:
    result = estimate_offer_baggage(
        offer("LHR", "ZRH", 8, "LX 317"),
        BaggageRequirement(carry_on_bags=1),
        load_carrier_baggage_rules(),
    )

    assert result.lower_bound is None
    assert result.completeness_status is AncillaryEstimateStatus.UNKNOWN


def test_condor_known_gbp_minimum_is_partial() -> None:
    result = estimate_offer_baggage(
        offer("LGW", "FRA", 8, "DE 1234"),
        BaggageRequirement(carry_on_bags=2),
        load_carrier_baggage_rules(),
    )

    assert result.lower_bound == Decimal(20)
    assert result.upper_bound is None
    assert result.completeness_status is AncillaryEstimateStatus.PARTIAL


def test_mixed_ancillary_currency_is_retained_but_not_summed() -> None:
    result = estimate_offer_baggage(
        offer("LGW", "FRA", 8, "DE 1234", currency="GBP"),
        BaggageRequirement(checked_bags=1),
        load_carrier_baggage_rules(),
    )

    assert result.native_ranges[0].currency == "EUR"
    assert result.native_ranges[0].low == Decimal("24.99")
    assert result.lower_bound is None
    assert result.completeness_status is AncillaryEstimateStatus.UNKNOWN


def test_separate_tickets_apply_baggage_independently() -> None:
    first = offer("LGW", "MXP", 8, "U2 8309", price="300")
    second = offer("MXP", "CAG", 13, "U2 1234", price="186")
    itinerary = construct_self_transfer(first, second)

    result = price_itinerary_from_carrier_rules(
        itinerary,
        BaggageRequirement(carry_on_bags=1),
        load_carrier_baggage_rules(),
    )

    assert result.base_price == Decimal(486)
    assert result.ancillary_low == Decimal("11.98")
    assert result.ancillary_high is None
    assert result.effective_price_low == Decimal("497.98")
    assert result.effective_price_high is None
    assert len(result.native_ancillary_ranges) == 2


def test_easyjet_and_ryanair_ticket_bounds_are_combined_independently() -> None:
    first = offer("LGW", "MXP", 8, "U2 8309", price="250")
    second = offer("MXP", "CAG", 13, "FR 4578", price="199.99")
    result = price_itinerary_from_carrier_rules(
        construct_self_transfer(first, second),
        BaggageRequirement(carry_on_bags=1),
        load_carrier_baggage_rules(),
    )

    assert result.base_price == Decimal("449.99")
    assert result.ancillary_low == Decimal("11.99")
    assert result.ancillary_high is None
    assert result.effective_price_low == Decimal("461.98")
    assert result.effective_price_high is None


def test_seed_catalog_has_only_observed_carriers_and_retrieval_date() -> None:
    rules = load_carrier_baggage_rules()

    assert set(rules) == {"U2", "FR", "W4", "VY", "LX", "DE"}
    assert {str(rule.source_retrieved_at) for rule in rules.values()} == {"2026-08-24"}


def test_fr_2687_fixture_resolves_dynamic_cabin_bag_range() -> None:
    payload = json.loads(
        Path("tests/fixtures/searchapi/lgw_cag_fr2687.json").read_text(encoding="utf-8")
    )
    offers, rejections = normalize_searchapi_response(payload)

    assert rejections == []
    assert offers[0].legs[0].flight_number == "FR 2687"
    estimate = estimate_offer_baggage(
        offers[0],
        BaggageRequirement(carry_on_bags=4),
        load_carrier_baggage_rules(),
    )
    assert estimate.lower_bound == Decimal(24)
    assert estimate.upper_bound == Decimal(144)
    assert estimate.completeness_status is AncillaryEstimateStatus.PARTIAL
