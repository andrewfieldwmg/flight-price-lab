"""Party-level baggage requirements and ancillary price assessments."""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BaggagePricingState(StrEnum):
    INCLUDED = "included"
    KNOWN_EXTRA_COST = "known_extra_cost"
    UNKNOWN = "unknown"


class AncillaryConfidence(StrEnum):
    CONFIRMED = "confirmed"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class BaggageRequirement(BaseModel):
    """Required item counts for the complete search party."""

    model_config = ConfigDict(frozen=True)

    personal_items: int = Field(default=0, ge=0)
    carry_on_bags: int = Field(default=0, ge=0)
    checked_bags: int = Field(default=0, ge=0)
    checked_bag_weight_kg: Decimal | None = Field(default=None, gt=0)


class AncillaryCosts(BaseModel):
    """Ancillary totals for one independently purchased offer/ticket."""

    model_config = ConfigDict(frozen=True)

    personal_item_state: BaggagePricingState = BaggagePricingState.UNKNOWN
    carry_on_state: BaggagePricingState = BaggagePricingState.UNKNOWN
    checked_bag_state: BaggagePricingState = BaggagePricingState.UNKNOWN
    carry_on_total: Decimal | None = Field(default=None, ge=0)
    checked_bag_total: Decimal | None = Field(default=None, ge=0)
    seat_total: Decimal | None = Field(default=None, ge=0)
    other_total: Decimal = Field(default=Decimal(0), ge=0)
    source: str
    confidence: AncillaryConfidence = AncillaryConfidence.UNKNOWN

    @model_validator(mode="after")
    def validate_known_costs(self) -> "AncillaryCosts":
        pairs = (
            (self.carry_on_state, self.carry_on_total, "carry_on_total"),
            (self.checked_bag_state, self.checked_bag_total, "checked_bag_total"),
        )
        for state, total, field in pairs:
            if state is BaggagePricingState.KNOWN_EXTRA_COST and total is None:
                raise ValueError(f"{field} is required for a known extra cost")
            if state is BaggagePricingState.INCLUDED and total not in (
                None,
                Decimal(0),
            ):
                raise ValueError(f"{field} must be zero or absent when included")
        return self


class EffectivePrice(BaseModel):
    """Known price components plus explicit completeness information."""

    model_config = ConfigDict(frozen=True)

    base_total_price: Decimal = Field(ge=0)
    known_ancillary_total: Decimal = Field(ge=0)
    effective_price: Decimal | None = Field(default=None, ge=0)
    complete: bool
    unknown_components: tuple[str, ...] = ()
