"""Carrier-published baggage rules and currency-safe price estimates."""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AncillaryPriceType(StrEnum):
    FIXED = "fixed"
    RANGE = "range"
    DYNAMIC = "dynamic"
    FARE_DEPENDENT = "fare_dependent"
    DYNAMIC_OR_FARE_DEPENDENT = "dynamic_or_fare_dependent"
    UNKNOWN = "unknown"


class CarrierRuleConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AncillaryEstimateStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class PersonalItemRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    included: bool | None = None
    dimensions_cm: str | None = None
    weight_kg: Decimal | None = Field(default=None, gt=0)


class BagPriceRule(BaseModel):
    """One bag product or one weight-specific pricing option.

    Price maps retain each researched native currency. A market-dependent key
    such as ``GBP/EUR`` is compatible with an offer in either named currency;
    its numeric amount is used without currency conversion.
    """

    model_config = ConfigDict(frozen=True)

    included: bool | None = None
    price_type: AncillaryPriceType = AncillaryPriceType.UNKNOWN
    price_min: dict[str, Decimal] = Field(default_factory=dict)
    price_max: dict[str, Decimal] = Field(default_factory=dict)
    dimensions_cm: str | None = None
    weight_kg: Decimal | None = Field(default=None, gt=0)
    product: str | None = None
    currency_basis: str | None = None
    supported_weights_kg: tuple[Decimal, ...] = ()
    options: tuple["BagPriceRule", ...] = ()
    notes: str | None = None

    @field_validator("price_min", "price_max")
    @classmethod
    def normalize_price_currencies(
        cls, value: dict[str, Decimal]
    ) -> dict[str, Decimal]:
        return {currency.upper(): amount for currency, amount in value.items()}

    @model_validator(mode="after")
    def validate_prices(self) -> "BagPriceRule":
        for currency in self.price_min.keys() & self.price_max.keys():
            if self.price_max[currency] < self.price_min[currency]:
                raise ValueError(f"{currency} price_max must be at least price_min")
        if self.price_type is AncillaryPriceType.FIXED and (
            not self.price_min or self.price_min != self.price_max
        ):
            raise ValueError("fixed pricing requires matching minimum and maximum")
        if self.price_type is AncillaryPriceType.RANGE and (
            not self.price_min or self.price_min.keys() != self.price_max.keys()
        ):
            raise ValueError("range pricing requires matching currency bounds")
        return self


class CarrierBaggageRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    carrier_code: str
    carrier_name: str
    effective_from: date | None = None
    source_retrieved_at: date
    personal_item: PersonalItemRule
    cabin_bag: BagPriceRule
    checked_bag: BagPriceRule
    source_url: str | None = None
    confidence: CarrierRuleConfidence

    @field_validator("carrier_code")
    @classmethod
    def uppercase_code(cls, value: str) -> str:
        return value.strip().upper()


class CarrierBaggageCatalog(BaseModel):
    model_config = ConfigDict(frozen=True)

    carriers: tuple[CarrierBaggageRule, ...]


class NativeAncillaryRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    component: str
    currency: str
    low: Decimal | None = Field(default=None, ge=0)
    high: Decimal | None = Field(default=None, ge=0)


class CarrierAncillaryEstimate(BaseModel):
    """Party-level bounds expressed only in the offer currency."""

    model_config = ConfigDict(frozen=True)

    estimated_ancillary_total: Decimal | None = Field(default=None, ge=0)
    lower_bound: Decimal | None = Field(default=None, ge=0)
    upper_bound: Decimal | None = Field(default=None, ge=0)
    currency: str
    native_ranges: tuple[NativeAncillaryRange, ...] = ()
    confidence: CarrierRuleConfidence
    completeness_status: AncillaryEstimateStatus
    unknown_components: tuple[str, ...] = ()


class EffectiveItineraryPrice(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_price: Decimal = Field(ge=0)
    currency: str
    ancillary_low: Decimal | None = Field(default=None, ge=0)
    ancillary_high: Decimal | None = Field(default=None, ge=0)
    effective_price_low: Decimal | None = Field(default=None, ge=0)
    effective_price_high: Decimal | None = Field(default=None, ge=0)
    native_ancillary_ranges: tuple[NativeAncillaryRange, ...] = ()
    ancillary_confidence: CarrierRuleConfidence
    completeness_status: AncillaryEstimateStatus
