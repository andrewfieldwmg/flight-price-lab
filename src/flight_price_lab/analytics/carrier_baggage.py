"""Estimate party-level baggage costs from file-backed carrier rules."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from flight_price_lab.models.ancillary import BaggageRequirement
from flight_price_lab.models.carrier_baggage import (
    AncillaryEstimateStatus,
    AncillaryPriceType,
    BagPriceRule,
    CarrierAncillaryEstimate,
    CarrierBaggageCatalog,
    CarrierBaggageRule,
    CarrierRuleConfidence,
    EffectiveItineraryPrice,
    NativeAncillaryRange,
)
from flight_price_lab.models.flight import FlightOffer
from flight_price_lab.models.itinerary import ConstructedItinerary

_CARRIER_CODE = re.compile(r"^([A-Z0-9]{2})\s*\d", re.IGNORECASE)
_CONFIDENCE_ORDER = {
    CarrierRuleConfidence.HIGH: 2,
    CarrierRuleConfidence.MEDIUM: 1,
    CarrierRuleConfidence.LOW: 0,
}


@dataclass(frozen=True)
class _Bounds:
    low: Decimal | None
    high: Decimal | None
    status: AncillaryEstimateStatus
    native: tuple[NativeAncillaryRange, ...]


def load_carrier_baggage_rules(
    path: str | Path = "config/carrier_baggage.yaml",
) -> dict[str, CarrierBaggageRule]:
    with Path(path).open(encoding="utf-8") as source:
        catalog = CarrierBaggageCatalog.model_validate(yaml.safe_load(source))
    rules = {rule.carrier_code: rule for rule in catalog.carriers}
    if len(rules) != len(catalog.carriers):
        raise ValueError("carrier baggage catalog contains duplicate carrier codes")
    return rules


def carrier_codes(offer: FlightOffer) -> tuple[str, ...]:
    codes: list[str] = []
    for leg in offer.legs:
        match = _CARRIER_CODE.match(leg.flight_number.strip())
        code = match.group(1).upper() if match else ""
        if code and code not in codes:
            codes.append(code)
    return tuple(codes)


def _select_checked_rule(
    rule: BagPriceRule, weight_kg: Decimal | None
) -> BagPriceRule | None:
    if not rule.options:
        return rule
    if weight_kg is None:
        return rule.options[0] if len(rule.options) == 1 else None
    return next(
        (option for option in rule.options if option.weight_kg == weight_kg), None
    )


def _bag_bounds(
    rule: BagPriceRule, count: int, currency: str, component: str
) -> _Bounds:
    if count == 0 or rule.included is True:
        return _Bounds(Decimal(0), Decimal(0), AncillaryEstimateStatus.COMPLETE, ())
    native = tuple(
        NativeAncillaryRange(
            component=component,
            currency=native_currency,
            low=amount * count,
            high=(
                rule.price_max.get(native_currency) * count
                if native_currency in rule.price_max
                else None
            ),
        )
        for native_currency, amount in rule.price_min.items()
    )
    if rule.price_type in (
        AncillaryPriceType.FARE_DEPENDENT,
        AncillaryPriceType.DYNAMIC_OR_FARE_DEPENDENT,
        AncillaryPriceType.UNKNOWN,
    ):
        return _Bounds(None, None, AncillaryEstimateStatus.UNKNOWN, native)

    def compatible(prices: Mapping[str, Decimal]) -> Decimal | None:
        exact = prices.get(currency)
        if exact is not None:
            return exact
        return next(
            (
                amount
                for basis, amount in prices.items()
                if currency in basis.split("/")
            ),
            None,
        )

    low = compatible(rule.price_min)
    high = compatible(rule.price_max)
    low = low * count if low is not None else None
    high = high * count if high is not None else None
    if low is None:
        return _Bounds(None, None, AncillaryEstimateStatus.UNKNOWN, native)
    if rule.price_type is AncillaryPriceType.FIXED:
        return _Bounds(low, high, AncillaryEstimateStatus.COMPLETE, native)
    if rule.price_type is AncillaryPriceType.RANGE and high is not None:
        return _Bounds(low, high, AncillaryEstimateStatus.PARTIAL, native)
    return _Bounds(low, high, AncillaryEstimateStatus.PARTIAL, native)


def estimate_offer_baggage(
    offer: FlightOffer,
    requirement: BaggageRequirement,
    rules: Mapping[str, CarrierBaggageRule],
) -> CarrierAncillaryEstimate:
    """Estimate each party-total requirement once per carrier on one ticket."""

    lows: list[Decimal] = []
    highs: list[Decimal] = []
    native: list[NativeAncillaryRange] = []
    unknown: list[str] = []
    statuses: list[AncillaryEstimateStatus] = []
    confidences: list[CarrierRuleConfidence] = []
    codes = carrier_codes(offer)
    if not codes and any(
        (
            requirement.personal_items,
            requirement.carry_on_bags,
            requirement.checked_bags,
        )
    ):
        unknown.append("carrier")

    for code in codes:
        carrier_rule = rules.get(code)
        if carrier_rule is None:
            unknown.append(f"{code}.carrier_rule")
            continue
        confidences.append(carrier_rule.confidence)
        if requirement.personal_items:
            if carrier_rule.personal_item.included is True:
                lows.append(Decimal(0))
                highs.append(Decimal(0))
            else:
                unknown.append(f"{code}.personal_items")
        components = [
            ("carry_on_bags", requirement.carry_on_bags, carrier_rule.cabin_bag)
        ]
        if requirement.checked_bags:
            checked_rule = _select_checked_rule(
                carrier_rule.checked_bag, requirement.checked_bag_weight_kg
            )
            if checked_rule is None:
                unknown.append(f"{code}.checked_bag_weight")
            else:
                components.append(
                    ("checked_bags", requirement.checked_bags, checked_rule)
                )
        for name, count, bag_rule in components:
            if count == 0:
                continue
            bounds = _bag_bounds(bag_rule, count, offer.currency, f"{code}.{name}")
            native.extend(bounds.native)
            statuses.append(bounds.status)
            if bounds.low is None:
                unknown.append(f"{code}.{name}")
            else:
                lows.append(bounds.low)
            if bounds.high is not None:
                highs.append(bounds.high)

    fully_bounded = not unknown and len(lows) == len(highs)
    low = sum(lows, Decimal(0)) if not unknown else None
    high = sum(highs, Decimal(0)) if fully_bounded else None
    if unknown or AncillaryEstimateStatus.UNKNOWN in statuses:
        status = AncillaryEstimateStatus.UNKNOWN
    elif AncillaryEstimateStatus.PARTIAL in statuses:
        status = AncillaryEstimateStatus.PARTIAL
    else:
        status = AncillaryEstimateStatus.COMPLETE
    confidence = (
        min(confidences, key=_CONFIDENCE_ORDER.get)
        if confidences
        else CarrierRuleConfidence.LOW
    )
    exact = low if status is AncillaryEstimateStatus.COMPLETE and low == high else None
    return CarrierAncillaryEstimate(
        estimated_ancillary_total=exact,
        lower_bound=low,
        upper_bound=high,
        currency=offer.currency,
        native_ranges=tuple(native),
        confidence=confidence,
        completeness_status=status,
        unknown_components=tuple(unknown),
    )


def price_itinerary_from_carrier_rules(
    itinerary: ConstructedItinerary,
    requirement: BaggageRequirement,
    rules: Mapping[str, CarrierBaggageRule],
) -> EffectiveItineraryPrice:
    """Apply baggage requirements independently to every constituent ticket."""

    estimates = tuple(
        estimate_offer_baggage(offer, requirement, rules)
        for offer in itinerary.components
    )
    low_known = all(item.lower_bound is not None for item in estimates)
    high_known = all(item.upper_bound is not None for item in estimates)
    ancillary_low = (
        sum(
            (item.lower_bound for item in estimates if item.lower_bound is not None),
            Decimal(0),
        )
        if low_known
        else None
    )
    ancillary_high = (
        sum(
            (item.upper_bound for item in estimates if item.upper_bound is not None),
            Decimal(0),
        )
        if high_known
        else None
    )
    statuses = {item.completeness_status for item in estimates}
    status = (
        AncillaryEstimateStatus.UNKNOWN
        if AncillaryEstimateStatus.UNKNOWN in statuses
        else AncillaryEstimateStatus.PARTIAL
        if AncillaryEstimateStatus.PARTIAL in statuses
        else AncillaryEstimateStatus.COMPLETE
    )
    return EffectiveItineraryPrice(
        base_price=itinerary.total_price,
        currency=itinerary.currency,
        ancillary_low=ancillary_low,
        ancillary_high=ancillary_high,
        effective_price_low=(
            itinerary.total_price + ancillary_low if ancillary_low is not None else None
        ),
        effective_price_high=(
            itinerary.total_price + ancillary_high
            if ancillary_high is not None
            else None
        ),
        native_ancillary_ranges=tuple(
            item for estimate in estimates for item in estimate.native_ranges
        ),
        ancillary_confidence=min(
            (item.confidence for item in estimates), key=_CONFIDENCE_ORDER.get
        ),
        completeness_status=status,
    )
