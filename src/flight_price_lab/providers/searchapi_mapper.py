"""Normalize captured SearchAPI Google Flights result groups."""

from collections.abc import Iterator, Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict

from flight_price_lab.airports import (
    AmbiguousLocalTimeError,
    NonexistentLocalTimeError,
    UnknownAirportError,
    resolve_airport_local_datetime,
)
from flight_price_lab.models import (
    FlightLeg,
    FlightOffer,
    JourneyStructure,
    TicketingType,
)
from flight_price_lab.routing.connections import validate_connection

LAYOVER_DURATION_TOLERANCE = timedelta(minutes=1)


class RejectionCode(StrEnum):
    """Stable categories for normalization failures."""

    INVALID_PROVIDER_SHAPE = "invalid_provider_shape"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_LOCAL_TIME = "invalid_local_time"
    AMBIGUOUS_LOCAL_TIME = "ambiguous_local_time"
    NONEXISTENT_LOCAL_TIME = "nonexistent_local_time"
    UNKNOWN_AIRPORT = "unknown_airport"
    TOO_MANY_STOPS = "too_many_stops"
    INVALID_CONNECTION = "invalid_connection"
    LAYOVER_MISMATCH = "layover_mismatch"
    DOMAIN_VALIDATION = "domain_validation"


class NormalizationRejection(BaseModel):
    """Structured context for one rejected provider result group."""

    model_config = ConfigDict(frozen=True)

    provider: str
    source_bucket: str
    result_index: int
    rejection_code: RejectionCode
    message: str
    field_path: str | None = None


class SearchAPIMapperError(ValueError):
    """A provider result cannot be represented safely in the V1 domain."""

    def __init__(
        self,
        message: str,
        *,
        code: RejectionCode = RejectionCode.INVALID_PROVIDER_SHAPE,
        field_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field_path = field_path


def iter_candidate_groups(
    payload: Mapping[str, Any],
) -> Iterator[tuple[str, int, Mapping[str, Any]]]:
    """Yield both SearchAPI ranking buckets as equivalent candidate groups."""

    for bucket in ("best_flights", "other_flights"):
        groups = payload.get(bucket, [])
        if groups is None:
            continue
        if not isinstance(groups, list):
            raise SearchAPIMapperError(f"{bucket} must be a list")
        for index, group in enumerate(groups):
            if not isinstance(group, Mapping):
                raise SearchAPIMapperError(f"{bucket}[{index}] must be an object")
            yield bucket, index, group


def _required_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SearchAPIMapperError(
            f"missing or invalid required field: {field}",
            code=RejectionCode.MISSING_REQUIRED_FIELD,
            field_path=field,
        )
    return value


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SearchAPIMapperError(
            f"missing or invalid required field: {field}",
            code=RejectionCode.MISSING_REQUIRED_FIELD,
            field_path=field,
        )
    return value.strip()


def _local_datetime(airport: Mapping[str, Any], field: str) -> datetime:
    airport_id = _required_text(airport.get("id"), f"{field}.id").upper()
    date_value = _required_text(airport.get("date"), f"{field}.date")
    time_value = _required_text(airport.get("time"), f"{field}.time")
    try:
        wall_time = datetime.combine(
            date.fromisoformat(date_value), time.fromisoformat(time_value)
        )
    except ValueError:
        raise SearchAPIMapperError(
            f"invalid local datetime in {field}",
            code=RejectionCode.INVALID_LOCAL_TIME,
            field_path=field,
        ) from None
    try:
        return resolve_airport_local_datetime(airport_id, wall_time)
    except UnknownAirportError as error:
        raise SearchAPIMapperError(
            str(error), code=RejectionCode.UNKNOWN_AIRPORT, field_path=f"{field}.id"
        ) from None
    except AmbiguousLocalTimeError as error:
        raise SearchAPIMapperError(
            str(error), code=RejectionCode.AMBIGUOUS_LOCAL_TIME, field_path=field
        ) from None
    except NonexistentLocalTimeError as error:
        raise SearchAPIMapperError(
            str(error), code=RejectionCode.NONEXISTENT_LOCAL_TIME, field_path=field
        ) from None


def _map_leg(raw_leg: Mapping[str, Any]) -> FlightLeg:
    departure = _required_mapping(raw_leg.get("departure_airport"), "departure_airport")
    arrival = _required_mapping(raw_leg.get("arrival_airport"), "arrival_airport")
    return FlightLeg(
        origin=_required_text(departure.get("id"), "departure_airport.id"),
        destination=_required_text(arrival.get("id"), "arrival_airport.id"),
        departure=_local_datetime(departure, "departure_airport"),
        arrival=_local_datetime(arrival, "arrival_airport"),
        airline=_required_text(raw_leg.get("airline"), "airline"),
        flight_number=_required_text(raw_leg.get("flight_number"), "flight_number"),
    )


def _passenger_count(search_parameters: Mapping[str, Any]) -> int:
    fields = ("adults", "children", "infants_in_seat", "infants_on_lap")
    try:
        count = sum(int(search_parameters.get(field, 0)) for field in fields)
    except (TypeError, ValueError):
        raise SearchAPIMapperError("search passenger counts must be integers") from None
    if count < 1:
        raise SearchAPIMapperError("search passenger count must be positive")
    return count


def _validate_layover(
    raw_group: Mapping[str, Any], first_leg: FlightLeg, second_leg: FlightLeg
) -> None:
    try:
        validate_connection(first_leg, second_leg)
    except ValueError as error:
        raise SearchAPIMapperError(
            str(error), code=RejectionCode.INVALID_CONNECTION, field_path="flights"
        ) from None
    layovers = raw_group.get("layovers")
    if layovers is None:
        return
    if not isinstance(layovers, list) or len(layovers) != 1:
        raise SearchAPIMapperError(
            "a one-stop result must have exactly one layover",
            code=RejectionCode.LAYOVER_MISMATCH,
            field_path="layovers",
        )
    layover = _required_mapping(layovers[0], "layovers[0]")
    airport = _required_text(layover.get("id"), "layovers[0].id").upper()
    if airport != first_leg.destination:
        raise SearchAPIMapperError(
            "layover airport does not match connecting legs",
            code=RejectionCode.LAYOVER_MISMATCH,
            field_path="layovers[0].id",
        )
    try:
        provider_duration = timedelta(minutes=int(layover["duration"]))
    except (KeyError, TypeError, ValueError):
        raise SearchAPIMapperError(
            "layover duration is missing or invalid",
            code=RejectionCode.LAYOVER_MISMATCH,
            field_path="layovers[0].duration",
        ) from None
    computed_duration = second_leg.departure - first_leg.arrival
    if abs(computed_duration - provider_duration) > LAYOVER_DURATION_TOLERANCE:
        raise SearchAPIMapperError(
            "layover duration disagrees with flight timestamps",
            code=RejectionCode.LAYOVER_MISMATCH,
            field_path="layovers[0].duration",
        )


def map_flight_group(
    raw_group: Mapping[str, Any],
    *,
    search_parameters: Mapping[str, Any],
    observed_at: datetime,
    source_bucket: str,
    result_index: int,
    raw_reference: str | None = None,
) -> FlightOffer:
    """Map one provider-ranked group to a normalized offer."""

    raw_flights = raw_group.get("flights")
    if not isinstance(raw_flights, list) or not raw_flights:
        raise SearchAPIMapperError("missing or invalid required field: flights")
    if len(raw_flights) > 2:
        raise SearchAPIMapperError(
            "more than one stop per direction is not supported",
            code=RejectionCode.TOO_MANY_STOPS,
            field_path="flights",
        )
    if not all(isinstance(raw_leg, Mapping) for raw_leg in raw_flights):
        raise SearchAPIMapperError("each flights entry must be an object")
    legs = tuple(_map_leg(raw_leg) for raw_leg in raw_flights)

    journey_structure = JourneyStructure.DIRECT
    if len(legs) == 2:
        _validate_layover(raw_group, legs[0], legs[1])
        journey_structure = JourneyStructure.CONNECTION

    # The capture has no explicit evidence of single or separate ticketing.
    ticketing_type = TicketingType.UNKNOWN

    currency = _required_text(search_parameters.get("currency"), "currency").upper()
    try:
        price = Decimal(str(raw_group["price"]))
    except (KeyError, TypeError, ValueError):
        raise SearchAPIMapperError("missing or invalid required field: price") from None

    fingerprint_seed = "|".join(leg.fingerprint for leg in legs)
    schedule_identifier = sha256(fingerprint_seed.encode()).hexdigest()
    provider_action_metadata = {
        key: raw_group[key]
        for key in ("booking_token", "departure_token")
        if key in raw_group
    }
    return FlightOffer(
        legs=legs,
        total_price=price,
        currency=currency,
        passenger_count=_passenger_count(search_parameters),
        provider="SearchAPI",
        provider_offer_id=f"searchapi:schedule:{schedule_identifier}",
        observed_at=observed_at,
        raw_reference=raw_reference,
        raw_metadata={
            "source_bucket": source_bucket,
            "result_index": result_index,
            "journey_structure": journey_structure.value,
            "ticketing_type": ticketing_type.value,
            "price_semantics": "search_party_total",
            "provider_type": raw_group.get("type"),
            "total_duration": raw_group.get("total_duration"),
            "layovers": raw_group.get("layovers"),
            "provider_action_metadata": provider_action_metadata,
        },
        ticketing_type=ticketing_type,
    )


def normalize_searchapi_response(
    payload: Mapping[str, Any],
    *,
    observed_at: datetime | None = None,
    raw_reference: str | None = None,
) -> tuple[list[FlightOffer], list[NormalizationRejection]]:
    """Normalize candidates, collecting structured rejection records."""

    search_parameters = _required_mapping(
        payload.get("search_parameters"), "search_parameters"
    )
    if observed_at is None:
        metadata = _required_mapping(payload.get("search_metadata"), "search_metadata")
        created_at = _required_text(
            metadata.get("created_at"), "search_metadata.created_at"
        )
        try:
            observed_at = datetime.fromisoformat(created_at)
        except ValueError:
            raise SearchAPIMapperError("invalid search_metadata.created_at") from None

    offers: list[FlightOffer] = []
    rejections: list[NormalizationRejection] = []
    for bucket, index, group in iter_candidate_groups(payload):
        try:
            offers.append(
                map_flight_group(
                    group,
                    search_parameters=search_parameters,
                    observed_at=observed_at,
                    source_bucket=bucket,
                    result_index=index,
                    raw_reference=raw_reference,
                )
            )
        except SearchAPIMapperError as error:
            rejections.append(
                NormalizationRejection(
                    provider="SearchAPI",
                    source_bucket=bucket,
                    result_index=index,
                    rejection_code=error.code,
                    message=str(error),
                    field_path=error.field_path,
                )
            )
        except ValueError as error:
            rejections.append(
                NormalizationRejection(
                    provider="SearchAPI",
                    source_bucket=bucket,
                    result_index=index,
                    rejection_code=RejectionCode.DOMAIN_VALIDATION,
                    message=str(error),
                )
            )
    return offers, rejections
