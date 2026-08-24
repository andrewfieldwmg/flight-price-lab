"""Synthesized itinerary models."""

from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from flight_price_lab.models.classification import JourneyStructure, TicketingType
from flight_price_lab.models.flight import FlightOffer


class ConstructedItinerary(BaseModel):
    """A validated one-direction itinerary assembled from provider offers."""

    model_config = ConfigDict(frozen=True)

    components: tuple[FlightOffer, ...] = Field(min_length=1, max_length=2)
    journey_structure: JourneyStructure
    ticketing_type: TicketingType
    total_price: Decimal = Field(
        ge=0, description="Sum of provider-reported component prices"
    )
    currency: str = Field(min_length=3, max_length=3)
    passenger_count: int | None = Field(default=None, ge=1)
    constituent_offer_fingerprints: tuple[str, ...] = Field(min_length=1)
    departure: datetime
    final_arrival: datetime
    connection_airport: str | None = None
    connection_duration: timedelta | None = None
    overnight_connection: bool | None = None
    number_of_stops: int = Field(ge=0, le=1)
