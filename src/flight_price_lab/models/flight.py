"""Normalized flight data supplied by providers."""

import json
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flight_price_lab.models.classification import TicketingType


class FlightLeg(BaseModel):
    """A single, continuously operated flight segment."""

    model_config = ConfigDict(frozen=True)

    origin: str
    destination: str
    departure: datetime
    arrival: datetime
    airline: str
    flight_number: str

    @field_validator("origin", "destination")
    @classmethod
    def normalize_airport(cls, value: str) -> str:
        airport = value.strip().upper()
        if len(airport) != 3 or not airport.isalpha():
            raise ValueError("airport must be a three-letter IATA code")
        return airport

    @field_validator("airline", "flight_number")
    @classmethod
    def require_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value

    @model_validator(mode="after")
    def validate_times(self) -> "FlightLeg":
        if self.departure.tzinfo is None or self.arrival.tzinfo is None:
            raise ValueError("flight datetimes must be timezone-aware")
        if self.arrival <= self.departure:
            raise ValueError("arrival must be after departure")
        return self

    @property
    def fingerprint(self) -> str:
        """Return an identity derived only from stable scheduled-flight attributes."""

        attributes = (
            self.origin,
            self.destination,
            self.departure.isoformat(),
            self.arrival.isoformat(),
            self.airline,
            self.flight_number,
        )
        encoded = json.dumps(attributes, separators=(",", ":")).encode()
        return sha256(encoded).hexdigest()


class FlightOffer(BaseModel):
    """A provider offer retaining its reported price and passenger context."""

    model_config = ConfigDict(frozen=True)

    legs: tuple[FlightLeg, ...] = Field(min_length=1)
    total_price: Decimal = Field(
        ge=0, description="Total price for the passenger_count search party"
    )
    currency: str = Field(min_length=3, max_length=3)
    passenger_count: int | None = Field(default=None, ge=1)
    provider: str = Field(min_length=1)
    provider_offer_id: str = Field(min_length=1)
    observed_at: datetime
    raw_reference: str | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
    ticketing_type: TicketingType = TicketingType.UNKNOWN

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("observed_at")
    @classmethod
    def require_observation_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return value

    @property
    def fingerprint(self) -> str:
        """Return identity from ordered legs, excluding price and observation time."""

        encoded = json.dumps(
            tuple(leg.fingerprint for leg in self.legs), separators=(",", ":")
        ).encode()
        return sha256(encoded).hexdigest()
