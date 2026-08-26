"""Stable provider-independent API contracts."""

from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flight_price_lab.models.classification import TicketingType


class SelfTransferPolicy(StrEnum):
    NONE = "NONE"
    OUTBOUND_ONLY = "OUTBOUND_ONLY"
    RETURN_ONLY = "RETURN_ONLY"
    BOTH = "BOTH"


class Direction(StrEnum):
    OUTBOUND = "OUTBOUND"
    RETURN = "RETURN"


class ConnectionProfile(StrEnum):
    CONSERVATIVE = "CONSERVATIVE"
    STANDARD = "STANDARD"
    AGGRESSIVE = "AGGRESSIVE"


class PriceCompleteness(StrEnum):
    COMPLETE = "COMPLETE"
    ESTIMATED = "ESTIMATED"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class SearchStatus(StrEnum):
    STARTED = "started"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"


class BaggageRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    cabin_bags: int = Field(default=0, ge=0)
    checked_bags: int = Field(default=0, ge=0)


class DirectionTimeWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    earliest_departure_time: time | None = None
    latest_arrival_time: time | None = None
    max_connection_minutes: int = Field(default=360, ge=0)


class TripSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    origins: list[str] = Field(min_length=1)
    destinations: list[str] = Field(min_length=1)
    outbound_date: date
    return_date: date | None = None
    adults: int = Field(ge=1)
    children: int = Field(default=0, ge=0)
    baggage: BaggageRequest = Field(default_factory=BaggageRequest)
    outbound_time_window: DirectionTimeWindow = Field(
        default_factory=DirectionTimeWindow
    )
    return_time_window: DirectionTimeWindow = Field(default_factory=DirectionTimeWindow)
    max_extra_journey_minutes: int | None = Field(default=None, ge=0)
    self_transfer_policy: SelfTransferPolicy = SelfTransferPolicy.NONE
    connection_profile: ConnectionProfile = ConnectionProfile.CONSERVATIVE
    currency: str = "GBP"
    refresh_prices: bool = False

    @field_validator("origins", "destinations")
    @classmethod
    def validate_airports(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip().upper() for value in values))
        if any(len(value) != 3 or not value.isalpha() for value in normalized):
            raise ValueError("airports must be three-letter IATA codes")
        return normalized

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("currency must be a three-letter code")
        return value

    @model_validator(mode="after")
    def validate_dates_and_policy(self) -> "TripSearchRequest":
        if self.return_date is not None and self.return_date < self.outbound_date:
            raise ValueError("return_date must not precede outbound_date")
        if self.return_date is None and self.self_transfer_policy in (
            SelfTransferPolicy.RETURN_ONLY,
            SelfTransferPolicy.BOTH,
        ):
            raise ValueError("return self-transfer policy requires return_date")
        if self.return_date is None and (
            self.return_time_window.earliest_departure_time is not None
            or self.return_time_window.latest_arrival_time is not None
        ):
            raise ValueError("return time constraints require return_date")
        return self


class TripLegSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    origin: str
    destination: str
    departure_at: datetime
    arrival_at: datetime
    airline: str
    flight_number: str

    @field_validator("departure_at", "arrival_at")
    @classmethod
    def require_leg_timestamp_offset(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("leg timestamps must include a timezone offset")
        return value


class BaggageEstimate(BaseModel):
    """Party-total baggage estimate for one independently purchased ticket."""

    model_config = ConfigDict(frozen=True)

    ticket_index: int = Field(ge=1)
    carrier_codes: list[str]
    flight_numbers: list[str]
    price_low: Decimal | None = None
    price_high: Decimal | None = None
    completeness: PriceCompleteness
    confidence: str


class TripOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    direction: Direction
    route: list[str]
    flight_numbers: list[str]
    airlines: list[str]
    legs: list[TripLegSummary]
    base_price: Decimal
    ancillary_price_low: Decimal | None
    ancillary_price_high: Decimal | None
    baggage_estimates: list[BaggageEstimate] = Field(default_factory=list)
    cabin_bags: int = Field(default=0, ge=0)
    checked_bags: int = Field(default=0, ge=0)
    effective_price_low: Decimal | None
    effective_price_high: Decimal | None
    currency: str
    price_completeness: PriceCompleteness
    is_nonstop: bool
    is_self_transfer: bool
    connection_airport: str | None = None
    connection_minutes: int | None = None
    departure_at: datetime
    arrival_at: datetime
    total_journey_minutes: int
    saving_vs_nonstop_amount: Decimal | None = None
    saving_vs_nonstop_percent: Decimal | None = None
    saving_vs_nonstop_low: Decimal | None = None
    saving_vs_nonstop_high: Decimal | None = None
    extra_minutes_vs_nonstop: int | None = None
    ticketing_type: TicketingType
    baggage_confidence: str

    @field_validator("departure_at", "arrival_at")
    @classmethod
    def require_timestamp_offset(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trip timestamps must include a timezone offset")
        return value


class SearchError(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    direction: Direction | None = None
    hub: str | None = None


class DirectionResults(BaseModel):
    baseline: TripOption | None = None
    nonstop_options: list[TripOption] = Field(default_factory=list)
    cheapest_feasible: TripOption | None = None
    fastest_feasible: TripOption | None = None
    pareto_frontier: list[TripOption] = Field(default_factory=list)
    feasible_options: list[TripOption] = Field(default_factory=list)


class SearchDiagnostics(BaseModel):
    trip_id: str = ""
    search_key: str = ""
    local_cache_hit: bool = False
    backend_cache_hits: int = 0
    backend_cache_misses: int = 0
    provider_calls_this_invocation: int = 0
    provider_calls_avoided_this_invocation: int = 0
    original_provider_calls: int | None = None
    original_search_completed_at: datetime | None = None
    search_started_at: datetime | None = None
    search_completed_at: datetime | None = None
    total_duration_ms: float | None = None
    direct_outbound_ms: float = 0
    direct_return_ms: float = 0
    hub_search_total_ms: float = 0
    normalization_ms: float = 0
    itinerary_synthesis_ms: float = 0
    ranking_filtering_ms: float = 0
    postgres_write_ms: float = 0
    final_serialization_ms: float = 0
    provider_calls_total: int = 0
    provider_calls_concurrent_peak: int = 0
    slowest_provider_call_ms: float = 0
    median_provider_call_ms: float = 0
    p95_provider_call_ms: float = 0
    provider_requests: list[dict[str, object]] = Field(default_factory=list)
    database_operations: list[dict[str, object]] = Field(default_factory=list)


class SearchSnapshot(BaseModel):
    search_id: str
    trip_id: str = ""
    search_key: str = ""
    status: SearchStatus
    outbound: DirectionResults = Field(default_factory=DirectionResults)
    return_: DirectionResults | None = Field(default=None, alias="return")
    errors: list[SearchError] = Field(default_factory=list)
    diagnostics: SearchDiagnostics = Field(default_factory=SearchDiagnostics)


class SearchStartedResponse(BaseModel):
    search_id: str
    trip_id: str
    search_key: str
    status: str = "started"


class SearchKeyResponse(BaseModel):
    search_key: str


class CalendarPrice(BaseModel):
    date: date
    price: Decimal
    currency: str


class CalendarResponse(BaseModel):
    prices: list[CalendarPrice]


class ProviderUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    current_month_usage: int = Field(ge=0)
    monthly_allowance: int = Field(ge=0)
    remaining_credits: int = Field(ge=0)
    period_start: datetime
    period_end: datetime


class ErrorResponse(BaseModel):
    error: SearchError
