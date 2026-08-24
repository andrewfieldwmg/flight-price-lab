"""Minimal parsing of SearchAPI booking-option fields observed or documented."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, SecretStr


class BookingOptionsRequest(BaseModel):
    """A booking token inseparably coupled to its originating search context."""

    model_config = ConfigDict(frozen=True)

    booking_token: SecretStr
    departure_id: str
    arrival_id: str
    outbound_date: date
    flight_type: Literal["one_way"] = "one_way"
    adults: int | None = None
    children: int | None = None
    currency: str | None = None


@dataclass(frozen=True)
class BookingOption:
    fare_type: str | None
    price: Decimal | None
    baggage_prices: Any
    is_split_booking: bool | None
    booking_provider: str | None


def parse_booking_options(payload: dict[str, Any]) -> tuple[BookingOption, ...]:
    """Parse known fields while leaving the complete response in raw storage."""

    raw_options = payload.get("booking_options", [])
    if not isinstance(raw_options, list):
        return ()
    parsed = []
    for raw in raw_options:
        if not isinstance(raw, dict):
            continue
        raw_price = raw.get("price")
        try:
            price = Decimal(str(raw_price)) if raw_price is not None else None
        except InvalidOperation:
            price = None
        provider = raw.get("booking_provider", raw.get("provider"))
        parsed.append(
            BookingOption(
                fare_type=raw.get("fare_type")
                if isinstance(raw.get("fare_type"), str)
                else None,
                price=price,
                baggage_prices=raw.get("baggage_prices"),
                is_split_booking=raw.get("is_split_booking")
                if isinstance(raw.get("is_split_booking"), bool)
                else None,
                booking_provider=provider if isinstance(provider, str) else None,
            )
        )
    return tuple(parsed)
